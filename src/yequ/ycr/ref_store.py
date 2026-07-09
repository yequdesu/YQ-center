"""Persistent YCR ContextRef store."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.config import get_settings
from yequ.models.ycr import YcrContextChunk, YcrContextRef
from yequ.ycr.budget import json_size_bytes
from yequ.ycr.embedding import EmbeddingError, YcrEmbedding
from yequ.ycr.ledger import write_ledger
from yequ.ycr.rag_cache import cached_query_embedding
from yequ.ycr.retrieval import cosine_similarity
from yequ.ycr.scheduler import (
    PRIORITY_BACKGROUND_CONTEXT_INDEX,
    PRIORITY_FOREGROUND_CONTEXT_EMBEDDING,
    scheduled_embed_text,
    scheduled_embed_text_full,
)


def stable_ref_id(*, source_type: str, source_id: str, path: str) -> str:
    seed = f"{source_type}:{source_id}:{path}"
    return f"ctxref_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


async def upsert_ref(
    db: AsyncSession,
    *,
    ref_type: str,
    source_type: str,
    source_id: str,
    path: str,
    value: object,
    summary: str,
    actor_id: str | None = None,
    session_id: str | None = None,
    trust_level: str = "node_reported_fact",
    projection_policy: str = "context_ref_v1",
    projection_version: int = 1,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    size = json_size_bytes(value)
    max_size = get_settings().ycr_max_raw_ref_bytes
    if size > max_size:
        raise ValueError(f"raw_result_too_large: raw_size_bytes={size} max={max_size}")

    ref_id = stable_ref_id(source_type=source_type, source_id=source_id, path=path)
    existing = await db.execute(select(YcrContextRef).where(YcrContextRef.ref_id == ref_id))
    record = existing.scalar_one_or_none()
    if record is None:
        record = YcrContextRef(ref_id=ref_id)
        db.add(record)
    record.ref_type = ref_type
    record.source_type = source_type
    record.source_id = source_id
    record.source_path = path
    record.source_version = _hash_value(value)
    record.actor_id = actor_id
    record.session_id = session_id
    record.projection_policy = projection_policy
    record.projection_version = projection_version
    record.trust_level = trust_level
    record.summary = summary
    record.value_json = _jsonable(value)
    record.metadata_json = {
        "source_hash": record.source_version,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        **(metadata or {}),
    }
    await db.flush()

    await _replace_chunks_without_embeddings(db, record)
    ref = ref_to_dict(record)
    await write_ledger(
        db,
        event_type="ref_upsert",
        source_type=source_type,
        source_id=source_id,
        ref_id=ref_id,
        raw_value=value,
        projected_value=ref,
        projection_policy=projection_policy,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        session_id=session_id,
        metadata={"chunk_count": len(_chunks(record.value_json))},
    )
    return ref


async def index_ref_chunks(db: AsyncSession, ref_id: str) -> dict[str, object]:
    record = await _require_record(db, ref_id)
    result = await db.execute(select(YcrContextChunk).where(YcrContextChunk.ref_id == ref_id))
    chunks = result.scalars().all()
    indexed = 0
    provider = None
    model = None
    try:
        for chunk in chunks:
            if chunk.embedding_json:
                continue
            embedding, provider, model = await scheduled_embed_text(
                chunk.text,
                priority=PRIORITY_BACKGROUND_CONTEXT_INDEX,
                purpose="context.ref.index",
                cache_key=chunk.chunk_id,
            )
            chunk.embedding_json = embedding
            chunk.embedding_model = model
            await db.flush()
            if db.bind and db.bind.dialect.name == "postgresql":
                await db.execute(
                    text(
                        "UPDATE ycr_context_chunks "
                        "SET embedding_vector = CAST(:embedding AS vector) "
                        "WHERE chunk_id = :chunk_id"
                    ),
                    {"embedding": _vector_literal(embedding), "chunk_id": chunk.chunk_id},
                )
            indexed += 1
    except EmbeddingError as exc:
        raise ValueError(f"context_search_unavailable: {exc}") from exc
    record.metadata_json = {
        **(record.metadata_json or {}),
        "embedding_provider": provider,
        "embedding_model": model,
        "indexed_at": datetime.now(UTC).isoformat(),
    }
    return {"ref_id": ref_id, "indexed_chunks": indexed, "chunk_count": len(chunks)}


async def get_ref(db: AsyncSession, ref_id: str) -> dict[str, object]:
    return ref_to_dict(await _require_record(db, ref_id))


async def inspect_ref(db: AsyncSession, ref_id: str) -> dict[str, object]:
    record = await _require_record(db, ref_id)
    return {
        "ref": ref_to_dict(record),
        "schema": _schema_summary(record.value_json),
        "preview": _preview_value(record.value_json),
    }


async def expand_ref(
    db: AsyncSession,
    ref_id: str,
    *,
    path: str = "$",
    limit: int = 20,
) -> dict[str, object]:
    record = await _require_record(db, ref_id)
    selected_path = path or "$"
    value = _select_path(record.value_json, selected_path)
    if selected_path == "$" and json_size_bytes(value) > get_settings().ycr_projection_inline_bytes:
        return {
            "ref_id": ref_id,
            "path": selected_path,
            "status": "path_required",
            "message": (
                "Root value is too large to expand inline; request a specific path, "
                "tail, schema, or search."
            ),
            "schema": _schema_summary(value),
            "preview": _preview_shape(value, limit=limit),
            "available_paths": _available_child_paths(value, limit=limit),
        }
    if isinstance(value, list):
        value = value[: max(1, min(limit, 100))]
    if isinstance(value, dict):
        keys = list(value.keys())[: max(1, min(limit, 100))]
        value = {str(key): value[key] for key in keys}
    return {"ref_id": ref_id, "path": selected_path, "value": value}


async def tail_ref(
    db: AsyncSession,
    ref_id: str,
    *,
    path: str = "$",
    lines: int = 40,
) -> dict[str, object]:
    record = await _require_record(db, ref_id)
    value = _select_path(record.value_json, path or "$")
    line_count = max(1, min(int(lines or 40), 200))
    if isinstance(value, str):
        tail = value.splitlines()[-line_count:]
    elif isinstance(value, list):
        tail = value[-line_count:]
    else:
        tail = json.dumps(value, ensure_ascii=False, indent=2).splitlines()[-line_count:]
    return {"ref_id": ref_id, "path": path or "$", "tail": tail, "line_count": len(tail)}


async def schema_ref(db: AsyncSession, ref_id: str, *, path: str = "$") -> dict[str, object]:
    record = await _require_record(db, ref_id)
    value = _select_path(record.value_json, path or "$")
    return {"ref_id": ref_id, "path": path or "$", "schema": _schema_summary(value)}


async def search_context(
    db: AsyncSession,
    *,
    query: str,
    ref_id: str | None = None,
    session_id: str | None = None,
    limit: int = 10,
) -> dict[str, object]:
    if not query.strip():
        raise ValueError("context_search_unavailable: query is required")
    if not ref_id and not session_id:
        raise ValueError("context_search_unavailable: ref_id or session_id is required")
    bounded_limit = max(1, min(limit, 50))
    ref = await get_ref(db, ref_id) if ref_id else None
    index_status = await _context_index_status(db, ref_id=ref_id, session_id=session_id)
    if index_status["status"] in {"no_refs", "not_indexed"}:
        return _search_output(
            query=query,
            matches=[],
            ref=ref,
            embedding_provider="not_used",
            embedding_model="not_used",
            limit=limit,
            index_status=index_status,
            result_status=str(index_status["status"]),
        )

    query_embedding, embedding_provider, embedding_model = await _embed_query(db, query)

    if db.bind and db.bind.dialect.name == "postgresql":
        where = ["embedding_vector IS NOT NULL"]
        params: dict[str, object] = {
            "embedding": _vector_literal(query_embedding),
            "limit": bounded_limit,
        }
        if ref_id:
            where.append("ref_id = :ref_id")
            params["ref_id"] = ref_id
        elif session_id:
            where.append(
                "ref_id IN (SELECT ref_id FROM ycr_context_refs WHERE session_id = :session_id)"
            )
            params["session_id"] = session_id
        rows = await db.execute(
            text(
                "SELECT chunk_id, ref_id, path, text, trust_level, "
                "1 - (embedding_vector <=> CAST(:embedding AS vector)) AS score "
                "FROM ycr_context_chunks "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY embedding_vector <=> CAST(:embedding AS vector) "
                "LIMIT :limit"
            ),
            params,
        )
        matches = [
            _match_dict(
                chunk_id=row.chunk_id,
                ref_id=row.ref_id,
                path=row.path,
                score=float(row.score or 0),
                text=row.text,
                query=query,
                trust_level=row.trust_level,
            )
            for row in rows
        ]
    else:
        stmt = select(YcrContextChunk)
        if ref_id:
            stmt = stmt.where(YcrContextChunk.ref_id == ref_id)
        elif session_id:
            refs = await db.execute(
                select(YcrContextRef.ref_id).where(YcrContextRef.session_id == session_id)
            )
            ref_ids = [str(item) for item in refs.scalars().all()]
            if not ref_ids:
                return _search_output(
                    query=query,
                    matches=[],
                    ref=ref,
                    embedding_provider=embedding_provider,
                    embedding_model=embedding_model,
                    limit=limit,
                    index_status=index_status,
                    result_status="no_refs",
                )
            stmt = stmt.where(YcrContextChunk.ref_id.in_(ref_ids))
        result = await db.execute(stmt)
        matches = []
        for chunk in result.scalars().all():
            if not isinstance(chunk.embedding_json, list):
                continue
            score = cosine_similarity(
                query_embedding,
                [float(value) for value in chunk.embedding_json],
            )
            if score <= 0:
                continue
            matches.append(
                _match_dict(
                    chunk_id=chunk.chunk_id,
                    ref_id=chunk.ref_id,
                    path=chunk.path,
                    score=score,
                    text=chunk.text,
                    query=query,
                    trust_level=chunk.trust_level,
                )
            )
        matches.sort(key=lambda item: float(item["score"]), reverse=True)
        matches = matches[:bounded_limit]

    result_status = "hit" if matches else "miss"
    output = _search_output(
        query=query,
        matches=matches,
        ref=ref,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        limit=limit,
        index_status=index_status,
        result_status=result_status,
    )
    await write_ledger(
        db,
        event_type="ref_search",
        source_type=str(ref["source_anchor"]["type"]) if ref else "session",
        source_id=str(ref["source_anchor"]["id"]) if ref else str(session_id or ""),
        ref_id=ref_id,
        raw_value={"query": query},
        projected_value=output,
        projection_policy="context_search_vector_v2",
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        session_id=session_id or (str(ref.get("session_id")) if ref else None),
        metadata={"limit": limit, "index_status": index_status, "result_status": result_status},
    )
    return output


async def search_ref(
    db: AsyncSession,
    ref_id: str,
    *,
    query: str,
    limit: int = 10,
) -> dict[str, object]:
    return await search_context(db, ref_id=ref_id, query=query, limit=limit)


async def _replace_chunks_without_embeddings(db: AsyncSession, record: YcrContextRef) -> None:
    await db.execute(delete(YcrContextChunk).where(YcrContextChunk.ref_id == record.ref_id))
    for index, chunk in enumerate(_chunks(record.value_json)):
        chunk_seed = f"{record.ref_id}:{index}:{chunk[0]}:{_hash_value(chunk[1])}"
        chunk_id = f"ctxchk_{hashlib.sha256(chunk_seed.encode()).hexdigest()[:16]}"
        db.add(
            YcrContextChunk(
                chunk_id=chunk_id,
                ref_id=record.ref_id,
                path=chunk[0],
                text=chunk[1],
                token_estimate=max(1, len(chunk[1]) // 4),
                trust_level=record.trust_level,
                embedding_json=None,
                embedding_model=None,
            )
        )


async def _embed_query(db: AsyncSession, query: str) -> tuple[list[float], str, str]:
    try:
        cached = await cached_query_embedding(
            db,
            query,
            compute=_compute_context_query_embedding,
        )
        return cached.embedding.dense, cached.embedding.provider, cached.embedding.model
    except EmbeddingError as exc:
        raise ValueError(f"context_search_unavailable: {exc}") from exc


async def _compute_context_query_embedding(query: str) -> YcrEmbedding:
    return await scheduled_embed_text_full(
        query,
        priority=PRIORITY_FOREGROUND_CONTEXT_EMBEDDING,
        purpose="context.search.query_embedding",
        cache_key=query,
    )


async def _context_index_status(
    db: AsyncSession,
    *,
    ref_id: str | None,
    session_id: str | None,
) -> dict[str, object]:
    if db.bind and db.bind.dialect.name == "postgresql":
        where: list[str] = []
        params: dict[str, object] = {}
        if ref_id:
            where.append("ref_id = :ref_id")
            params["ref_id"] = ref_id
        elif session_id:
            where.append(
                "ref_id IN (SELECT ref_id FROM ycr_context_refs WHERE session_id = :session_id)"
            )
            params["session_id"] = session_id
        else:
            return {"status": "no_scope", "total_chunks": 0, "indexed_chunks": 0}
        rows = await db.execute(
            text(
                "SELECT COUNT(*) AS total_chunks, "
                "COUNT(*) FILTER (WHERE embedding_vector IS NOT NULL) AS indexed_chunks "
                "FROM ycr_context_chunks "
                f"WHERE {' AND '.join(where)}"
            ),
            params,
        )
        row = rows.one()
        total = int(row.total_chunks or 0)
        indexed = int(row.indexed_chunks or 0)
    else:
        total_stmt = select(
            func.count(YcrContextChunk.id),
            func.count(YcrContextChunk.embedding_model),
        )
        if ref_id:
            total_stmt = total_stmt.where(YcrContextChunk.ref_id == ref_id)
        elif session_id:
            refs = await db.execute(
                select(YcrContextRef.ref_id).where(YcrContextRef.session_id == session_id)
            )
            ref_ids = [str(item) for item in refs.scalars().all()]
            if not ref_ids:
                return {"status": "no_refs", "total_chunks": 0, "indexed_chunks": 0}
            total_stmt = total_stmt.where(YcrContextChunk.ref_id.in_(ref_ids))
        else:
            return {"status": "no_scope", "total_chunks": 0, "indexed_chunks": 0}
        row = (await db.execute(total_stmt)).one()
        total = int(row[0] or 0)
        indexed = int(row[1] or 0)

    if total <= 0:
        status = "no_refs"
    elif indexed <= 0:
        status = "not_indexed"
    elif indexed < total:
        status = "partial"
    else:
        status = "ready"
    return {"status": status, "total_chunks": total, "indexed_chunks": indexed}


async def _require_record(db: AsyncSession, ref_id: str) -> YcrContextRef:
    result = await db.execute(select(YcrContextRef).where(YcrContextRef.ref_id == ref_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise ValueError(f"Context ref not found: {ref_id}")
    return record


def ref_to_dict(record: YcrContextRef) -> dict[str, object]:
    return {
        "ref_id": record.ref_id,
        "ref_type": record.ref_type,
        "source_anchor": {"type": record.source_type, "id": record.source_id},
        "source_type": record.source_type,
        "source_id": record.source_id,
        "session_id": record.session_id,
        "path": record.source_path,
        "summary": record.summary,
        "trust_level": record.trust_level,
        "projection_policy": record.projection_policy,
        "projection_version": record.projection_version,
        "available_ops": ["inspect", "expand", "tail", "schema", "search"],
    }


def _chunks(value: object, *, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        chunks: list[tuple[str, str]] = []
        for key, item in value.items():
            chunks.extend(_chunks(item, path=_child_path(path, str(key))))
        return chunks
    if isinstance(value, list):
        chunks = []
        for index, item in enumerate(value[:1000]):
            chunks.extend(_chunks(item, path=f"{path}[{index}]"))
        return chunks
    text = str(value)
    if not text.strip():
        return []
    return [(path, text)]


def _jsonable(value: object) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _hash_value(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _match_dict(
    *,
    chunk_id: str,
    ref_id: str,
    path: str,
    score: float,
    text: str,
    query: str,
    trust_level: str,
) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "ref_id": ref_id,
        "path": path,
        "score": score,
        "snippet": _snippet(text, query),
        "trust_level": trust_level,
    }


def _search_output(
    *,
    query: str,
    matches: list[dict[str, object]],
    ref: dict[str, object] | None,
    embedding_provider: str,
    embedding_model: str,
    limit: int,
    index_status: dict[str, object],
    result_status: str,
) -> dict[str, object]:
    output: dict[str, object] = {
        "query": query,
        "matches": matches[: max(1, min(limit, 50))],
        "match_count": len(matches),
        "index_status": index_status,
        "result_rag": {
            "status": result_status,
            "index": index_status,
            "match_count": len(matches),
        },
        "retrieval": {
            "strategy": "context_vector_search_v2",
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            "index": index_status,
            "result_rag": {
                "status": result_status,
                "match_count": len(matches),
            },
        },
    }
    if ref is not None:
        output["ref_id"] = ref["ref_id"]
        output["ref"] = ref
    return output


def _snippet(text: str, query: str) -> str:
    position = text.lower().find(query.lower())
    if position < 0:
        return text[:360]
    start = max(0, position - 160)
    end = min(len(text), position + len(query) + 240)
    return text[start:end]


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values[:1024]) + "]"


def _preview_value(value: object) -> object:
    if isinstance(value, str):
        return value[:1200]
    if isinstance(value, list):
        return value[:8]
    if isinstance(value, dict):
        keys = list(value.keys())[:32]
        return {str(key): value[key] for key in keys}
    return value


def _preview_shape(value: object, *, limit: int) -> object:
    bounded = max(1, min(limit, 32))
    if isinstance(value, dict):
        return {
            str(key): _schema_summary(item)
            for key, item in list(value.items())[:bounded]
        }
    if isinstance(value, list):
        return [_schema_summary(item) for item in value[:bounded]]
    if isinstance(value, str):
        return value[:1200]
    return value


def _available_child_paths(value: object, *, limit: int) -> list[str]:
    bounded = max(1, min(limit, 100))
    if isinstance(value, dict):
        return [f"$.{key}" for key in list(value.keys())[:bounded]]
    if isinstance(value, list):
        return [f"$[{index}]" for index in range(min(len(value), bounded))]
    return []


def _schema_summary(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {
            "type": "object",
            "key_count": len(value),
            "keys": [
                {"name": str(key), "schema": _schema_summary(item)}
                for key, item in list(value.items())[:32]
            ],
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "count": len(value),
            "item_schema": _schema_summary(value[0]) if value else {"type": "unknown"},
        }
    if isinstance(value, str):
        return {"type": "string", "chars": len(value)}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int | float):
        return {"type": "number"}
    if value is None:
        return {"type": "null"}
    return {"type": type(value).__name__}


def _select_path(value: object, path: str) -> object:
    if path in {"", "$"}:
        return value
    current: Any = value
    parts = path[2:].split(".") if path.startswith("$.") else path.split(".")
    for part in parts:
        if not part:
            continue
        if "[" in part and part.endswith("]"):
            key, raw_index = part[:-1].split("[", 1)
            if key:
                if not isinstance(current, dict):
                    raise ValueError(f"Cannot select {path}: {key} is not an object")
                current = current[key]
            if not isinstance(current, list):
                raise ValueError(f"Cannot select {path}: {part} is not a list")
            current = current[int(raw_index)]
            continue
        if not isinstance(current, dict):
            raise ValueError(f"Cannot select {path}: {part} is not an object")
        current = current[part]
    return current


def _child_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path != "$" else f"$.{key}"
