"""Persistent YCR ContextRef store."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.ycr import YcrContextChunk, YcrContextRef
from yequ.ycr.embedding import embed_text
from yequ.ycr.ledger import write_ledger
from yequ.ycr.retrieval import cosine_similarity
from yequ.ycr.source_adapter import load_source_value


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
    ttl_sec: int = 86400,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
) -> dict[str, object]:
    ref_id = stable_ref_id(source_type=source_type, source_id=source_id, path=path)
    now = datetime.now(UTC)
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
    record.metadata_json = {"source_hash": record.source_version}
    record.expires_at = now + timedelta(seconds=max(60, ttl_sec))
    await db.flush()

    await db.execute(delete(YcrContextChunk).where(YcrContextChunk.ref_id == ref_id))
    chunks = _chunks(record.value_json)
    for index, chunk in enumerate(chunks):
        chunk_seed = f"{ref_id}:{index}:{chunk[0]}"
        chunk_id = f"ctxchk_{hashlib.sha256(chunk_seed.encode()).hexdigest()[:16]}"
        embedding, actual_provider, actual_model = await embed_text(chunk[1])
        db.add(
            YcrContextChunk(
                chunk_id=chunk_id,
                ref_id=ref_id,
                path=chunk[0],
                text=chunk[1],
                token_estimate=max(1, len(chunk[1]) // 4),
                trust_level=trust_level,
                embedding_json=embedding,
                embedding_model=actual_model,
            )
        )
        await db.flush()
        if db.bind and db.bind.dialect.name == "postgresql":
            await db.execute(
                text(
                    "UPDATE ycr_context_chunks "
                    "SET embedding_vector = CAST(:embedding AS vector) "
                    "WHERE chunk_id = :chunk_id"
                ),
                {"embedding": _vector_literal(embedding), "chunk_id": chunk_id},
            )
        embedding_provider = actual_provider
        embedding_model = actual_model
    record.metadata_json = {
        "source_hash": record.source_version,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
    }
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
        metadata={"chunk_count": len(chunks)},
    )
    return ref


async def rehydrate_ref(db: AsyncSession, ref_id: str) -> dict[str, object]:
    record = await _require_record(db, ref_id)
    source_value = await load_source_value(
        db,
        source_type=record.source_type,
        source_id=record.source_id,
    )
    value = _select_path(source_value, record.source_path)
    return await upsert_ref(
        db,
        ref_type=record.ref_type,
        source_type=record.source_type,
        source_id=record.source_id,
        path=record.source_path,
        value=value,
        summary=record.summary or "Rehydrated YCR context ref.",
        actor_id=record.actor_id,
        session_id=record.session_id,
        trust_level=record.trust_level,
        projection_policy=record.projection_policy,
        projection_version=record.projection_version,
    )


async def get_ref(db: AsyncSession, ref_id: str) -> dict[str, object]:
    result = await db.execute(select(YcrContextRef).where(YcrContextRef.ref_id == ref_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise ValueError(f"Context ref not found: {ref_id}")
    return ref_to_dict(record)


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
    value = _select_path(record.value_json, path or "$")
    if isinstance(value, list):
        value = value[: max(1, min(limit, 100))]
    if isinstance(value, dict):
        keys = list(value.keys())[: max(1, min(limit, 100))]
        value = {str(key): value[key] for key in keys}
    return {"ref_id": ref_id, "path": path or "$", "value": value}


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


async def search_ref(
    db: AsyncSession,
    ref_id: str,
    *,
    query: str,
    limit: int = 10,
) -> dict[str, object]:
    ref = await get_ref(db, ref_id)
    query_embedding, embedding_provider, embedding_model = await embed_text(query)
    if db.bind and db.bind.dialect.name == "postgresql":
        rows = await db.execute(
            text(
                "SELECT chunk_id, path, text, trust_level, "
                "1 - (embedding_vector <=> CAST(:embedding AS vector)) AS score "
                "FROM ycr_context_chunks "
                "WHERE ref_id = :ref_id AND embedding_vector IS NOT NULL "
                "ORDER BY embedding_vector <=> CAST(:embedding AS vector) "
                "LIMIT :limit"
            ),
            {
                "embedding": _vector_literal(query_embedding),
                "ref_id": ref_id,
                "limit": max(1, min(limit, 50)),
            },
        )
        matches = [
            {
                "chunk_id": row.chunk_id,
                "path": row.path,
                "score": float(row.score or 0),
                "snippet": _snippet(row.text, query),
                "trust_level": row.trust_level,
            }
            for row in rows
        ]
        output = {
            "ref_id": ref_id,
            "query": query,
            "matches": matches,
            "match_count": len(matches),
            "ref": ref,
        }
        await write_ledger(
            db,
            event_type="ref_search",
            source_type=str(ref["source_anchor"]["type"]),
            source_id=str(ref["source_anchor"]["id"]),
            ref_id=ref_id,
            raw_value={"query": query},
            projected_value=output,
            projection_policy="context_search_pgvector_v1",
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            metadata={"limit": limit},
        )
        return output
    result = await db.execute(select(YcrContextChunk).where(YcrContextChunk.ref_id == ref_id))
    matches = []
    for chunk in result.scalars().all():
        if not isinstance(chunk.embedding_json, list):
            continue
        chunk_embedding = chunk.embedding_json
        score = cosine_similarity(query_embedding, [float(value) for value in chunk_embedding])
        if query.lower() in chunk.text.lower():
            score += 1.0
        if score <= 0:
            continue
        matches.append(
            {
                "chunk_id": chunk.chunk_id,
                "path": chunk.path,
                "score": score,
                "snippet": _snippet(chunk.text, query),
                "trust_level": chunk.trust_level,
            }
        )
    matches.sort(key=lambda item: float(item["score"]), reverse=True)
    output = {
        "ref_id": ref_id,
        "query": query,
        "matches": matches[: max(1, min(limit, 50))],
        "match_count": len(matches),
        "ref": ref,
    }
    await write_ledger(
        db,
        event_type="ref_search",
        source_type=str(ref["source_anchor"]["type"]),
        source_id=str(ref["source_anchor"]["id"]),
        ref_id=ref_id,
        raw_value={"query": query},
        projected_value=output,
        projection_policy="context_search_vector_v1",
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        metadata={"limit": limit},
    )
    return output


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
        "path": record.source_path,
        "summary": record.summary,
        "trust_level": record.trust_level,
        "projection_policy": record.projection_policy,
        "projection_version": record.projection_version,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "available_ops": ["inspect", "expand", "tail", "schema", "search"],
    }


def _chunks(value: object, *, path: str = "$") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        chunks: list[tuple[str, str]] = []
        for key, item in value.items():
            chunks.extend(_chunks(item, path=f"{path}.{key}"))
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
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        payload = str(value)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _snippet(text: str, query: str) -> str:
    position = text.lower().find(query.lower())
    if position < 0:
        return text[:360]
    start = max(0, position - 160)
    end = min(len(text), position + len(query) + 240)
    return text[start:end]


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values[:256]) + "]"


def _preview_value(value: object) -> object:
    if isinstance(value, str):
        return value[:1200]
    if isinstance(value, list):
        return value[:8]
    if isinstance(value, dict):
        keys = list(value.keys())[:32]
        return {str(key): value[key] for key in keys}
    return value


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
