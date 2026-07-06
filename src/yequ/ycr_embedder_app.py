"""Local OpenAI-compatible embedding service for YCR.

This service is intentionally separate from Center and YCR.  It loads the
local BGE-M3 model in its own process so Agent/Center failures do not inherit
model runtime state.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

_model: Any | None = None
_reranker: Any | None = None
_model_lock = asyncio.Lock()
_reranker_lock = asyncio.Lock()
_embedding_semaphore: asyncio.Semaphore | None = None
_rerank_semaphore: asyncio.Semaphore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    await _load_model()
    await _load_reranker()
    yield


app = FastAPI(title="YeQu Local Embedder", version="2.0.0", lifespan=lifespan)


class EmbeddingRequest(BaseModel):
    model: str = Field(default="BAAI/bge-m3")
    input: str | list[str]


class RerankRequest(BaseModel):
    model: str = Field(default="BAAI/bge-reranker-base")
    query: str
    documents: list[str] = Field(default_factory=list)
    top_n: int = Field(default=10, ge=1, le=100)


def _require_embedder_auth(authorization: str | None) -> None:
    token = os.getenv("YEQU_EMBEDDER_API_KEY", "")
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {
        "status": "ok",
        "component": "ycr-embedder",
        "model": os.getenv("YEQU_EMBEDDER_MODEL", "BAAI/bge-m3"),
        "rerank_model": os.getenv("YEQU_YCR_RERANK_MODEL", "BAAI/bge-reranker-base"),
        "device": os.getenv("YEQU_EMBEDDER_DEVICE", "cpu"),
        "embedding_concurrency": _embedding_concurrency(),
        "rerank_concurrency": _rerank_concurrency(),
    }


@app.post("/v1/embeddings")
async def embeddings(
    body: EmbeddingRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _require_embedder_auth(authorization)
    inputs = [body.input] if isinstance(body.input, str) else list(body.input)
    if not inputs:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="input is required")
    model = await _load_model()
    async with _embedding_limit():
        vectors = await asyncio.to_thread(_encode, model, inputs)
    return {
        "object": "list",
        "model": body.model,
        "data": [
            {
                "object": "embedding",
                "index": index,
                "embedding": [float(value) for value in item["dense"]],
                "sparse_embedding": item["sparse"],
            }
            for index, item in enumerate(vectors)
        ],
    }


@app.post("/v1/rerank")
async def rerank(
    body: RerankRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _require_embedder_auth(authorization)
    if not body.query.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query is required")
    if not body.documents:
        return {"object": "list", "model": body.model, "results": []}
    reranker = await _load_reranker()
    async with _rerank_limit():
        scores = await asyncio.to_thread(_rerank_scores, reranker, body.query, body.documents)
    ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)[: body.top_n]
    return {
        "object": "list",
        "model": body.model,
        "results": [
            {"index": index, "relevance_score": float(score)}
            for index, score in ranked
        ],
    }


async def _load_model() -> Any:
    global _model
    if _model is not None:
        return _model
    async with _model_lock:
        if _model is not None:
            return _model
        try:
            from FlagEmbedding import BGEM3FlagModel
        except ImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="FlagEmbedding is not installed. Install the embedder dependencies.",
            ) from exc
        model_name = os.getenv("YEQU_EMBEDDER_MODEL", "BAAI/bge-m3")
        device = os.getenv("YEQU_EMBEDDER_DEVICE", "cpu")
        use_fp16 = os.getenv("YEQU_EMBEDDER_USE_FP16", "false").lower() in {"1", "true", "yes"}
        _model = await asyncio.to_thread(
            BGEM3FlagModel,
            model_name,
            use_fp16=use_fp16,
            device=device,
        )
        return _model


async def _load_reranker() -> Any:
    global _reranker
    if _reranker is not None:
        return _reranker
    async with _reranker_lock:
        if _reranker is not None:
            return _reranker
        try:
            from FlagEmbedding import FlagReranker
        except ImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="FlagEmbedding is not installed. Install the embedder dependencies.",
            ) from exc
        model_name = os.getenv("YEQU_YCR_RERANK_MODEL", "BAAI/bge-reranker-base")
        use_fp16 = os.getenv("YEQU_EMBEDDER_USE_FP16", "false").lower() in {"1", "true", "yes"}
        _reranker = await asyncio.to_thread(FlagReranker, model_name, use_fp16=use_fp16)
        return _reranker


def _encode(model: Any, inputs: list[str]) -> list[dict[str, object]]:
    output = model.encode(
        inputs,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense_vectors = output.get("dense_vecs") if isinstance(output, dict) else output
    sparse_vectors = output.get("lexical_weights") if isinstance(output, dict) else None
    if sparse_vectors is None:
        sparse_vectors = [{} for _ in inputs]
    return [
        {
            "dense": list(dense),
            "sparse": {str(key): float(value) for key, value in dict(sparse).items()},
        }
        for dense, sparse in zip(dense_vectors, sparse_vectors, strict=True)
    ]


def _rerank_scores(reranker: Any, query: str, documents: list[str]) -> list[float]:
    pairs = [[query, document] for document in documents]
    scores = reranker.compute_score(pairs, normalize=True)
    if isinstance(scores, float):
        return [scores]
    return [float(score) for score in scores]


def _embedding_limit() -> asyncio.Semaphore:
    global _embedding_semaphore
    if _embedding_semaphore is None:
        _embedding_semaphore = asyncio.Semaphore(_embedding_concurrency())
    return _embedding_semaphore


def _rerank_limit() -> asyncio.Semaphore:
    global _rerank_semaphore
    if _rerank_semaphore is None:
        _rerank_semaphore = asyncio.Semaphore(_rerank_concurrency())
    return _rerank_semaphore


def _embedding_concurrency() -> int:
    return _env_int("YEQU_EMBEDDER_EMBEDDING_CONCURRENCY", 1)


def _rerank_concurrency() -> int:
    return _env_int("YEQU_EMBEDDER_RERANK_CONCURRENCY", 1)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default
