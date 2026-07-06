"""Persistent RAG-layer caches for YCR retrieval hot paths."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.config import Settings, get_settings
from yequ.models.ycr import YcrQueryEmbeddingCache, YcrRerankCache
from yequ.ycr.embedding import RerankItem, YcrEmbedding

QUERY_NORMALIZE_VERSION = 1
EMBEDDING_CACHE_VERSION = 1
RERANK_CACHE_VERSION = 1

_WHITESPACE_RE = re.compile(r"\s+")
_query_locks: dict[str, asyncio.Lock] = {}
_rerank_locks: dict[str, asyncio.Lock] = {}
_stats = {
    "query_embedding": {"hit": 0, "miss": 0},
    "rerank": {"hit": 0, "miss": 0},
}


@dataclass(slots=True)
class CachedEmbedding:
    embedding: YcrEmbedding
    cache_key: str
    cache_status: str
    normalized_query_hash: str


@dataclass(slots=True)
class CachedRerank:
    items: list[RerankItem]
    cache_key: str
    cache_status: str
    document_hashes_hash: str


async def cached_query_embedding(
    db: AsyncSession,
    query: str,
    *,
    compute: Callable[[str], Awaitable[YcrEmbedding]],
    settings: Settings | None = None,
) -> CachedEmbedding:
    settings = settings or get_settings()
    normalized = normalize_query(query)
    query_hash = _sha256_text(normalized)
    provider = settings.ycr_embedding_provider
    model = settings.ycr_embedding_model
    cache_key = _hash_object(
        {
            "kind": "query_embedding",
            "normalized_query_hash": query_hash,
            "normalize_version": QUERY_NORMALIZE_VERSION,
            "embedding_provider": provider,
            "embedding_model": model,
            "embedding_version": EMBEDDING_CACHE_VERSION,
        }
    )
    lock = _lock_for(_query_locks, cache_key)
    async with lock:
        record = await _load_query_embedding_cache(db, cache_key)
        if record is not None:
            _record_hit("query_embedding")
            record.hit_count += 1
            record.last_used_at = datetime.now(UTC)
            return CachedEmbedding(
                embedding=YcrEmbedding(
                    dense=[float(value) for value in record.dense_json],
                    sparse={str(key): float(value) for key, value in record.sparse_json.items()},
                    provider=record.embedding_provider,
                    model=record.embedding_model,
                ),
                cache_key=cache_key,
                cache_status="hit",
                normalized_query_hash=query_hash,
            )
        _record_miss("query_embedding")
        embedding = await compute(query)
        db.add(
            YcrQueryEmbeddingCache(
                cache_key=cache_key,
                normalized_query=normalized,
                query_hash=query_hash,
                normalize_version=QUERY_NORMALIZE_VERSION,
                embedding_provider=embedding.provider,
                embedding_model=embedding.model,
                embedding_version=EMBEDDING_CACHE_VERSION,
                dense_json=embedding.dense,
                sparse_json=embedding.sparse,
                last_used_at=datetime.now(UTC),
                hit_count=0,
            )
        )
        await db.flush()
        return CachedEmbedding(
            embedding=embedding,
            cache_key=cache_key,
            cache_status="miss",
            normalized_query_hash=query_hash,
        )


async def cached_rerank(
    db: AsyncSession,
    query: str,
    documents: list[str],
    *,
    document_hashes: list[str],
    top_n: int,
    compute: Callable[[str, list[str], int], Awaitable[list[RerankItem]]],
    settings: Settings | None = None,
) -> CachedRerank:
    settings = settings or get_settings()
    normalized_query_hash = _sha256_text(normalize_query(query))
    document_hashes_hash = _hash_object({"document_hashes": document_hashes})
    cache_key = _hash_object(
        {
            "kind": "rerank",
            "normalized_query_hash": normalized_query_hash,
            "rerank_model": settings.ycr_rerank_model,
            "rerank_version": RERANK_CACHE_VERSION,
            "document_hashes_hash": document_hashes_hash,
            "top_n": top_n,
        }
    )
    lock = _lock_for(_rerank_locks, cache_key)
    async with lock:
        record = await _load_rerank_cache(db, cache_key)
        if record is not None:
            _record_hit("rerank")
            record.hit_count += 1
            record.last_used_at = datetime.now(UTC)
            return CachedRerank(
                items=[
                    RerankItem(index=int(item["index"]), score=float(item["score"]))
                    for item in record.result_json
                ],
                cache_key=cache_key,
                cache_status="hit",
                document_hashes_hash=document_hashes_hash,
            )
        _record_miss("rerank")
        items = await compute(query, documents, top_n)
        db.add(
            YcrRerankCache(
                cache_key=cache_key,
                normalized_query_hash=normalized_query_hash,
                rerank_model=settings.ycr_rerank_model,
                rerank_version=RERANK_CACHE_VERSION,
                document_hashes_hash=document_hashes_hash,
                top_n=top_n,
                result_json=[{"index": item.index, "score": item.score} for item in items],
                last_used_at=datetime.now(UTC),
                hit_count=0,
            )
        )
        await db.flush()
        return CachedRerank(
            items=items,
            cache_key=cache_key,
            cache_status="miss",
            document_hashes_hash=document_hashes_hash,
        )


def normalize_query(query: str) -> str:
    return _WHITESPACE_RE.sub(" ", query.strip()).lower()


def rag_cache_stats() -> dict[str, object]:
    return {
        "query_embedding": dict(_stats["query_embedding"]),
        "rerank": dict(_stats["rerank"]),
        "versions": {
            "query_normalize": QUERY_NORMALIZE_VERSION,
            "embedding_cache": EMBEDDING_CACHE_VERSION,
            "rerank_cache": RERANK_CACHE_VERSION,
        },
    }


async def _load_query_embedding_cache(
    db: AsyncSession,
    cache_key: str,
) -> YcrQueryEmbeddingCache | None:
    result = await db.execute(
        select(YcrQueryEmbeddingCache).where(YcrQueryEmbeddingCache.cache_key == cache_key)
    )
    return result.scalar_one_or_none()


async def _load_rerank_cache(db: AsyncSession, cache_key: str) -> YcrRerankCache | None:
    result = await db.execute(select(YcrRerankCache).where(YcrRerankCache.cache_key == cache_key))
    return result.scalar_one_or_none()


def _lock_for(locks: dict[str, asyncio.Lock], cache_key: str) -> asyncio.Lock:
    lock = locks.get(cache_key)
    if lock is None:
        lock = asyncio.Lock()
        locks[cache_key] = lock
    return lock


def _record_hit(kind: str) -> None:
    _stats[kind]["hit"] += 1


def _record_miss(kind: str) -> None:
    _stats[kind]["miss"] += 1


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_object(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
