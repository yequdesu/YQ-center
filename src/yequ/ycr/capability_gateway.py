"""Capability discovery boundary for registry filters and Tool RAG."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.ycr import YcrCapabilityIndex
from yequ.services.capability_registry import capability_describe, capability_search
from yequ.ycr.embedding import EmbeddingError, embed_text
from yequ.ycr.retrieval import TOKEN_RE, cosine_similarity

TOOL_RAG_CANDIDATE_LIMIT = 500


async def search_capability_registry(
    db: AsyncSession,
    *,
    query: str | None = None,
    node_id: str | None = None,
    platform_os: str | None = None,
    filters: dict[str, object] | None = None,
    limit: int = 10,
) -> dict[str, object]:
    filters = filters or {}
    common_filters = {
        "node_id": node_id,
        "platform_os": platform_os,
        "effect": _str_or_none(filters.get("effect")),
        "risk": _str_or_none(filters.get("risk")),
        "runtime_kind": _str_or_none(filters.get("runtime_kind")),
        "runtime_labels": [str(item) for item in filters.get("runtime_labels") or []],
        "supports_progress": _bool_or_none(filters.get("supports_progress")),
        "supports_cancel": _bool_or_none(filters.get("supports_cancel")),
        "supports_resume": _bool_or_none(filters.get("supports_resume")),
        "preflight_supported": _bool_or_none(filters.get("preflight_supported")),
        "artifact_input": _bool_or_none(filters.get("artifact_input")),
        "artifact_output": _bool_or_none(filters.get("artifact_output")),
        "projection": _str_or_none(filters.get("projection")) or "summary",
        "capability_type": _str_or_none(filters.get("capability_type")) or "function",
        "include_inactive": bool(filters.get("include_inactive", False)),
    }
    if query and query.strip():
        return await _search_capability_rag(
            db,
            query=query.strip(),
            filters=common_filters,
            limit=limit,
        )
    capabilities = await capability_search(
        db,
        query=None,
        **common_filters,
        limit=limit,
    )
    return {
        "kind": "capability_registry_search_result",
        "query": "",
        "matches": capabilities,
        "match_count": len(capabilities),
        "retrieval": {
            "strategy": "registry_filter_v1",
            "semantic": {"enabled": False, "reason": "query_not_provided"},
        },
    }


async def describe_capability_registry(
    db: AsyncSession,
    *,
    capability_ref: str,
    node_id: str | None = None,
    sections: list[str] | None = None,
    projection: str = "invoke_ready",
) -> dict[str, object]:
    return {
        "kind": "capability_registry_description",
        "capability": await capability_describe(
            db,
            capability_ref,
            node_id=node_id,
            sections=sections or [],
            projection=projection,
            include_inactive=False,
        ),
    }


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


async def _search_capability_rag(
    db: AsyncSession,
    *,
    query: str,
    filters: dict[str, object],
    limit: int,
) -> dict[str, object]:
    candidates = await capability_search(
        db,
        query=None,
        **filters,
        limit=TOOL_RAG_CANDIDATE_LIMIT,
        max_limit=TOOL_RAG_CANDIDATE_LIMIT,
    )
    if not candidates:
        return {
            "kind": "capability_tool_rag_result",
            "query": query,
            "matches": [],
            "match_count": 0,
            "retrieval": {
                "strategy": "tool_rag_hybrid_v1",
                "candidate_count": 0,
                "candidate_limit": TOOL_RAG_CANDIDATE_LIMIT,
                "semantic": {"enabled": True, "match_count": 0},
            },
        }

    try:
        indexed = await _ensure_capability_indexes(db, candidates)
        query_embedding, provider, model = await embed_text(query)
    except EmbeddingError as exc:
        raise ValueError(f"capability_rag_unavailable: {exc}") from exc

    index_by_name = {item.canonical_name: item for item in indexed}
    ranked: list[tuple[float, dict[str, object], YcrCapabilityIndex, dict[str, float]]] = []
    for candidate in candidates:
        canonical_name = str(candidate.get("canonical_name") or "")
        index = index_by_name.get(canonical_name)
        if index is None or not isinstance(index.embedding_json, list):
            continue
        dense_score = cosine_similarity(
            query_embedding,
            [float(value) for value in index.embedding_json],
        )
        sparse_score = _sparse_score(query, index.sparse_json or {})
        score = dense_score * 0.82 + sparse_score * 0.18
        if score <= 0:
            continue
        ranked.append(
            (
                score,
                candidate,
                index,
                {"dense": dense_score, "sparse": sparse_score},
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)

    matches: list[dict[str, object]] = []
    for score, candidate, index, scores in ranked[: _bounded_limit(limit)]:
        output = dict(candidate)
        output["retrieval"] = {
            "strategy": "tool_rag_hybrid_v1",
            "score": score,
            "dense_score": scores["dense"],
            "sparse_score": scores["sparse"],
            "index_id": index.index_id,
            "document_hash": index.document_hash,
        }
        matches.append(output)
    return {
        "kind": "capability_tool_rag_result",
        "query": query,
        "matches": matches,
        "match_count": len(matches),
        "retrieval": {
            "strategy": "tool_rag_hybrid_v1",
            "candidate_count": len(candidates),
            "candidate_limit": TOOL_RAG_CANDIDATE_LIMIT,
            "indexed_count": len(indexed),
            "semantic": {
                "enabled": True,
                "provider": provider,
                "model": model,
                "match_count": len(matches),
            },
        },
    }


async def _ensure_capability_indexes(
    db: AsyncSession,
    candidates: list[dict[str, object]],
) -> list[YcrCapabilityIndex]:
    indexed: list[YcrCapabilityIndex] = []
    for candidate in candidates:
        document = _capability_index_document(candidate)
        index_text = _capability_index_text(document)
        document_hash = _stable_hash(document)
        index_id = _index_id(candidate)
        result = await db.execute(
            select(YcrCapabilityIndex).where(YcrCapabilityIndex.index_id == index_id)
        )
        record = result.scalar_one_or_none()
        if record is not None and record.document_hash == document_hash and record.embedding_json:
            indexed.append(record)
            continue
        embedding, provider, model = await embed_text(index_text)
        if record is None:
            record = YcrCapabilityIndex(index_id=index_id)
            db.add(record)
        record.capability_id = str(candidate.get("capability_id") or "")
        record.canonical_name = str(candidate.get("canonical_name") or "")
        record.capability_type = str(candidate.get("capability_type") or "function")
        record.document_hash = document_hash
        record.document_json = document
        record.index_text = index_text
        record.embedding_json = embedding
        record.embedding_provider = provider
        record.embedding_model = model
        record.sparse_json = _sparse_terms(index_text)
        await db.flush()
        if db.bind and db.bind.dialect.name == "postgresql":
            await db.execute(
                text(
                    "UPDATE ycr_capability_index "
                    "SET embedding_vector = CAST(:embedding AS vector) "
                    "WHERE index_id = :index_id"
                ),
                {"embedding": _vector_literal(embedding), "index_id": index_id},
            )
        indexed.append(record)
    return indexed


def _capability_index_document(candidate: dict[str, object]) -> dict[str, Any]:
    sources = [item for item in candidate.get("sources") or [] if isinstance(item, dict)]
    invoke = candidate.get("invoke") if isinstance(candidate.get("invoke"), dict) else {}
    return {
        "canonical_name": candidate.get("canonical_name"),
        "capability_type": candidate.get("capability_type"),
        "display_name": candidate.get("display_name"),
        "description": candidate.get("description"),
        "tags": candidate.get("tags") or [],
        "risk": candidate.get("risk"),
        "effect": candidate.get("effect"),
        "invoke": invoke,
        "sources": [
            {
                "node_id": source.get("node_id"),
                "registered_name": source.get("registered_name"),
                "platform_os": source.get("platform_os"),
                "status": source.get("status"),
                "dispatchable": source.get("dispatchable"),
            }
            for source in sources
        ],
    }


def _capability_index_text(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True)


def _stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _index_id(candidate: dict[str, object]) -> str:
    seed = f"{candidate.get('capability_id')}:{candidate.get('canonical_name')}"
    return f"capidx_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _sparse_terms(text_value: str) -> dict[str, float]:
    terms: dict[str, float] = {}
    for token in TOKEN_RE.findall(text_value.lower()):
        terms[token] = terms.get(token, 0.0) + 1.0
    total = sum(terms.values()) or 1.0
    return {key: value / total for key, value in terms.items()}


def _sparse_score(query: str, sparse_terms: dict[str, float]) -> float:
    tokens = TOKEN_RE.findall(query.lower())
    if not tokens:
        return 0.0
    return sum(float(sparse_terms.get(token, 0.0)) for token in tokens) / len(tokens)


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


def _bounded_limit(value: int) -> int:
    return max(1, min(int(value), 50))
