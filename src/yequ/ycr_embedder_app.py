"""Local OpenAI-compatible embedding service for YCR.

This service is intentionally separate from Center and YCR.  It loads the
local BGE-M3 model in its own process so Agent/Center failures do not inherit
model runtime state.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

app = FastAPI(title="YeQu Local Embedder", version="1.0.0")

_model: Any | None = None
_model_lock = asyncio.Lock()


class EmbeddingRequest(BaseModel):
    model: str = Field(default="BAAI/bge-m3")
    input: str | list[str]


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
        "device": os.getenv("YEQU_EMBEDDER_DEVICE", "cpu"),
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
    vectors = await asyncio.to_thread(_encode, model, inputs)
    return {
        "object": "list",
        "model": body.model,
        "data": [
            {
                "object": "embedding",
                "index": index,
                "embedding": [float(value) for value in vector],
            }
            for index, vector in enumerate(vectors)
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


def _encode(model: Any, inputs: list[str]) -> list[list[float]]:
    output = model.encode(
        inputs,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    dense = output.get("dense_vecs") if isinstance(output, dict) else output
    return [list(vector) for vector in dense]
