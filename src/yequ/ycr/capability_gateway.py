"""Capability discovery boundary for registry filters and Tool RAG."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import bindparam, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.config import get_settings
from yequ.models.capability_runtime import CapabilityDefinition, CapabilitySource
from yequ.models.node import Node
from yequ.models.ycr import YcrCapabilityIndex
from yequ.services.capability_registry import (
    capability_describe,
    capability_search,
)
from yequ.ycr.embedding import (
    EmbeddingError,
    RerankItem,
    YcrEmbedding,
)
from yequ.ycr.entities import attach_ycr_entities
from yequ.ycr.rag_cache import (
    cached_query_embedding,
    cached_rerank,
    cached_retrieval_candidates,
    hash_rag_object,
)
from yequ.ycr.retrieval import TOKEN_RE, cosine_similarity
from yequ.ycr.scheduler import (
    PRIORITY_FOREGROUND_CAPABILITY_EMBEDDING,
    PRIORITY_FOREGROUND_CAPABILITY_RERANK,
    scheduled_embed_text_full,
    scheduled_rerank_documents,
)

TOOL_RAG_CANDIDATE_LIMIT = 500
CAPABILITY_INDEX_VERSION = 3
RETRIEVAL_TOP_K = 50
RRF_K = 60
DENSE_ONLY_MIN_SCORE = 0.55


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
        "agent_visible": _bool_filter(filters.get("agent_visible"), default=True),
        "invocation_surface": _str_or_none(filters.get("invocation_surface")) or "agent",
    }
    if query and query.strip():
        return await _search_capability_rag(
            db,
            query=query.strip(),
            filters=common_filters,
            limit=limit,
        )
    if not _has_structured_filter(common_filters):
        raise ValueError(
            "invalid_capability_search_request: capability.search requires query or filter"
        )
    capabilities = await capability_search(
        db,
        query=None,
        **common_filters,
        limit=limit,
    )
    return attach_ycr_entities(
        {
            "kind": "capability_registry_search_result",
            "query": "",
            "matches": capabilities,
            "match_count": len(capabilities),
            "retrieval": {
                "strategy": "registry_filter_v1",
                "semantic": {"enabled": False, "reason": "query_not_provided"},
            },
        },
        capabilities=capabilities,
    )


async def describe_capability_registry(
    db: AsyncSession,
    *,
    capability_ref: str,
    node_id: str | None = None,
    sections: list[str] | None = None,
    projection: str = "invoke_ready",
) -> dict[str, object]:
    capability = await capability_describe(
        db,
        capability_ref,
        node_id=node_id,
        sections=sections or [],
        projection=projection,
        include_inactive=False,
    )
    return attach_ycr_entities(
        {
            "kind": "capability_registry_description",
            "capability": capability,
        },
        capabilities=[capability],
    )


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _bool_filter(value: object, *, default: bool | None) -> bool | None:
    return value if isinstance(value, bool) else default


async def _search_capability_rag(
    db: AsyncSession,
    *,
    query: str,
    filters: dict[str, object],
    limit: int,
) -> dict[str, object]:
    requested_projection = str(filters.get("projection") or "summary")
    return_candidates = await capability_search(
        db,
        query=None,
        **filters,
        limit=TOOL_RAG_CANDIDATE_LIMIT,
        max_limit=TOOL_RAG_CANDIDATE_LIMIT,
    )
    if not return_candidates:
        return attach_ycr_entities(
            {
                "kind": "capability_tool_rag_result",
                "query": query,
                "matches": [],
                "match_count": 0,
                "retrieval": {
                    "strategy": "tool_rag_bge_m3_rrf_v1",
                    "candidate_count": 0,
                    "candidate_limit": TOOL_RAG_CANDIDATE_LIMIT,
                    "semantic": {"enabled": True, "match_count": 0},
                },
            },
            capabilities=[],
        )

    indexed = await _load_ready_capability_indexes(db, return_candidates)
    if len(indexed) < len(return_candidates):
        enqueued = await _enqueue_missing_capability_indexes(db, return_candidates, indexed)
        return _index_not_ready_response(
            query=query,
            requested_projection=requested_projection,
            candidate_count=len(return_candidates),
            indexed_count=len(indexed),
            enqueued_count=enqueued,
        )
    try:
        cached_embedding = await cached_query_embedding(
            db,
            query,
            compute=_compute_query_embedding,
        )
        query_embedding = cached_embedding.embedding
    except EmbeddingError as exc:
        raise ValueError(f"capability_rag_unavailable: {exc}") from exc

    return_by_name = {str(item.get("canonical_name") or ""): item for item in return_candidates}
    index_by_name = {index.canonical_name: index for index in indexed}
    registry_version = await _capability_registry_version(db)
    filters_hash = _retrieval_filters_hash(filters)
    query_embedding_hash = hash_rag_object(
        {"dense": query_embedding.dense, "sparse": query_embedding.sparse}
    )
    cached_retrieval = await cached_retrieval_candidates(
        db,
        normalized_query_hash=cached_embedding.normalized_query_hash,
        query_embedding_hash=query_embedding_hash,
        registry_version=registry_version,
        filters_hash=filters_hash,
        top_k=RETRIEVAL_TOP_K,
        compute=lambda: _compute_retrieval_candidate_rows(query_embedding, indexed),
    )

    rerank_documents_text: list[str] = []
    coarse_payloads: list[
        tuple[str, float, dict[str, int], YcrCapabilityIndex, dict[str, Any]]
    ] = []
    for row in cached_retrieval.rows[: max(_bounded_limit(limit), get_settings().ycr_rerank_top_k)]:
        name = str(row.get("canonical_name") or "")
        index = index_by_name.get(name)
        if index is None:
            continue
        ranks = _rank_dict(row.get("ranks"))
        trace = {
            "dense_score": float(row.get("dense_score") or 0.0),
            "sparse_score": float(row.get("sparse_score") or 0.0),
            "index": index,
        }
        rrf_score = float(row.get("rrf_score") or 0.0)
        coarse_payloads.append((name, rrf_score, ranks, index, trace))
        rerank_documents_text.append(index.index_text)

    try:
        cached_reranked = await cached_rerank(
            db,
            query,
            rerank_documents_text,
            document_hashes=[payload[3].document_hash for payload in coarse_payloads],
            top_n=_bounded_limit(limit),
            compute=_compute_rerank,
        )
        reranked = cached_reranked.items
    except EmbeddingError as exc:
        raise ValueError(f"capability_rerank_unavailable: {exc}") from exc

    matches: list[dict[str, object]] = []
    for rerank_item in reranked:
        try:
            name, rrf_score, ranks, index, trace = coarse_payloads[rerank_item.index]
        except IndexError:
            continue
        candidate = return_by_name.get(name)
        if candidate is None:
            candidate = next(
                item for item in return_candidates if str(item.get("canonical_name") or "") == name
            )
        output = dict(candidate)
        output["retrieval"] = {
            "strategy": "tool_rag_bge_m3_rrf_v1",
            "score": rerank_item.score,
            "rrf_score": rrf_score,
            "rerank_score": rerank_item.score,
            "dense_rank": ranks.get("dense_rank"),
            "dense_score": trace["dense_score"],
            "sparse_rank": ranks.get("sparse_rank"),
            "sparse_score": trace["sparse_score"],
            "index_id": index.index_id,
            "document_hash": index.document_hash,
        }
        matches.append(output)

    return attach_ycr_entities(
        {
            "kind": "capability_tool_rag_result",
            "query": query,
            "matches": matches,
            "match_count": len(matches),
            "retrieval": {
                "strategy": "tool_rag_bge_m3_rrf_v1",
                "candidate_count": len(return_candidates),
                "candidate_limit": TOOL_RAG_CANDIDATE_LIMIT,
                "requested_projection": requested_projection,
                "indexed_count": len(indexed),
                "unindexed_count": len(return_candidates) - len(indexed),
                "reranked_count": len(reranked),
                "semantic": {
                    "enabled": True,
                    "provider": query_embedding.provider,
                    "model": query_embedding.model,
                    "coarse_result_count": len(cached_retrieval.rows),
                    "match_count": len(matches),
                },
                "cache": {
                    "query_embedding": {
                        "status": cached_embedding.cache_status,
                        "cache_key": cached_embedding.cache_key,
                    },
                    "retrieval": {
                    "status": cached_retrieval.cache_status,
                    "cache_key": cached_retrieval.cache_key,
                    "query_embedding_hash": cached_retrieval.query_embedding_hash,
                    "registry_version": cached_retrieval.registry_version,
                    "filters_hash": cached_retrieval.filters_hash,
                },
                    "rerank": {
                        "status": cached_reranked.cache_status,
                        "cache_key": cached_reranked.cache_key,
                        "document_hashes_hash": cached_reranked.document_hashes_hash,
                    },
                },
            },
        },
        capabilities=matches,
    )


async def _compute_retrieval_candidate_rows(
    query_embedding: YcrEmbedding,
    indexed: list[YcrCapabilityIndex],
) -> list[dict[str, object]]:
    dense_rows: list[tuple[str, float]] = []
    sparse_rows: list[tuple[str, float]] = []
    trace_by_name: dict[str, dict[str, float]] = {}
    for index in indexed:
        if not isinstance(index.embedding_json, list):
            continue
        dense_score = cosine_similarity(
            query_embedding.dense,
            [float(value) for value in index.embedding_json],
        )
        sparse_score = _sparse_dot(query_embedding.sparse, index.sparse_json or {})
        trace_by_name[index.canonical_name] = {
            "dense_score": dense_score,
            "sparse_score": sparse_score,
        }
        dense_rows.append((index.canonical_name, dense_score))
        if sparse_score > 0:
            sparse_rows.append((index.canonical_name, sparse_score))
    ranked_rows = _rrf_fusion(
        dense_rows,
        sparse_rows,
        top_k=RETRIEVAL_TOP_K,
    )
    return [
        {
            "canonical_name": name,
            "rrf_score": score,
            "ranks": ranks,
            "dense_score": trace_by_name.get(name, {}).get("dense_score", 0.0),
            "sparse_score": trace_by_name.get(name, {}).get("sparse_score", 0.0),
        }
        for name, score, ranks in ranked_rows
    ]


async def _enqueue_missing_capability_indexes(
    db: AsyncSession,
    candidates: list[dict[str, object]],
    indexed: list[YcrCapabilityIndex],
) -> int:
    indexed_capability_ids = {index.capability_id for index in indexed}
    missing_ids = {
        str(candidate.get("capability_id") or "")
        for candidate in candidates
        if str(candidate.get("capability_id") or "")
        and str(candidate.get("capability_id") or "") not in indexed_capability_ids
    }
    if not missing_ids:
        return 0
    from yequ.ycr.capability_index_jobs import enqueue_capability_index_jobs

    return await enqueue_capability_index_jobs(db, capability_ids=missing_ids)


def _index_not_ready_response(
    *,
    query: str,
    requested_projection: str,
    candidate_count: int,
    indexed_count: int,
    enqueued_count: int,
) -> dict[str, object]:
    unindexed_count = max(0, candidate_count - indexed_count)
    return attach_ycr_entities(
        {
            "kind": "capability_tool_rag_result",
            "query": query,
            "matches": [],
            "match_count": 0,
            "retrieval": {
                "strategy": "tool_rag_bge_m3_rrf_v1",
                "candidate_count": candidate_count,
                "candidate_limit": TOOL_RAG_CANDIDATE_LIMIT,
                "requested_projection": requested_projection,
                "indexed_count": indexed_count,
                "unindexed_count": unindexed_count,
                "foreground": {
                    "policy": "read_ready_indexes_only_v1",
                    "computed_missing_indexes": False,
                    "enqueued_missing_indexes": enqueued_count,
                },
                "semantic": {
                    "enabled": True,
                    "status": "not_ready",
                    "match_count": 0,
                },
                "index": {
                    "status": "not_ready",
                    "reason": "matching capability indexes are not ready",
                    "retryable": True,
                    "retry_after_seconds": 5,
                },
            },
        },
        capabilities=[],
    )


async def _compute_query_embedding(query: str) -> YcrEmbedding:
    return await scheduled_embed_text_full(
        query,
        priority=PRIORITY_FOREGROUND_CAPABILITY_EMBEDDING,
        purpose="capability.search.query_embedding",
        cache_key=query,
    )


async def _compute_rerank(query: str, documents: list[str], top_n: int) -> list[RerankItem]:
    return await scheduled_rerank_documents(
        query,
        documents,
        priority=PRIORITY_FOREGROUND_CAPABILITY_RERANK,
        purpose="capability.search.rerank",
        top_n=top_n,
    )


def _rrf_fusion(
    dense_rows: list[tuple[str, float]],
    sparse_rows: list[tuple[str, float]],
    *,
    top_k: int,
) -> list[tuple[str, float, dict[str, int]]]:
    if not sparse_rows:
        dense_ranked = [
            item
            for item in sorted(dense_rows, key=lambda item: item[1], reverse=True)
            if item[1] >= DENSE_ONLY_MIN_SCORE
        ][:top_k]
        return [
            (name, score, {"dense_rank": rank})
            for rank, (name, score) in enumerate(dense_ranked, start=1)
        ]

    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    sparse_names = {name for name, _score in sparse_rows}
    dense_ranked = [
        (name, rank)
        for rank, (name, score) in enumerate(
            sorted(dense_rows, key=lambda item: item[1], reverse=True),
            start=1,
        )
        if name in sparse_names or score >= DENSE_ONLY_MIN_SCORE
    ][:top_k]
    sparse_ranked = [
        (name, rank)
        for rank, (name, _score) in enumerate(
            sorted(sparse_rows, key=lambda item: item[1], reverse=True),
            start=1,
        )
    ][:top_k]
    for channel, rows in (
        ("dense", dense_ranked),
        ("sparse", sparse_ranked),
    ):
        for name, rank in rows:
            scores[name] = scores.get(name, 0.0) + 1.0 / (RRF_K + rank)
            ranks.setdefault(name, {})[f"{channel}_rank"] = rank
    return [
        (name, score, ranks.get(name, {}))
        for name, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
    ]


def _sparse_dot(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(float(value) * float(right.get(key, 0.0)) for key, value in left.items())


def _rank_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    output: dict[str, int] = {}
    for key, item in value.items():
        if item is not None:
            output[str(key)] = int(item)
    return output


async def _capability_registry_version(db: AsyncSession) -> str:
    definition_result = await db.execute(
        select(CapabilityDefinition).order_by(
            CapabilityDefinition.capability_type,
            CapabilityDefinition.canonical_name,
            CapabilityDefinition.capability_id,
        )
    )
    source_result = await db.execute(
        select(CapabilitySource, Node)
        .join(Node, CapabilitySource.node_record_id == Node.id, isouter=True)
        .order_by(CapabilitySource.source_id)
    )
    definitions = [
        {
            "capability_id": item.capability_id,
            "canonical_name": item.canonical_name,
            "capability_type": item.capability_type,
            "scope": item.scope,
            "plane": item.plane,
            "dispatch_kind": item.dispatch_kind,
            "agent_visible": item.agent_visible,
            "invocation_surface": item.invocation_surface,
            "status": item.status,
            "updated_at": item.updated_at,
        }
        for item in definition_result.scalars().all()
    ]
    sources = [
        {
            "source_id": source.source_id,
            "definition_id": source.definition_id,
            "node_id": node.node_id if node else None,
            "node_status": node.status if node else None,
            "registered_name": source.registered_name,
            "runtime_id": source.runtime_id,
            "platform_os": source.platform_os,
            "status": source.status,
            "is_active": source.is_active,
            "unavailable_reason": source.unavailable_reason,
            "updated_at": source.updated_at,
        }
        for source, node in source_result.all()
    ]
    return hash_rag_object(
        {
            "kind": "capability_registry_version",
            "capability_index_version": CAPABILITY_INDEX_VERSION,
            "definitions": definitions,
            "sources": sources,
        }
    )


def _retrieval_filters_hash(filters: dict[str, object]) -> str:
    return hash_rag_object(
        {
            key: value
            for key, value in sorted(filters.items())
            if key != "projection"
        }
    )


async def _load_ready_capability_indexes(
    db: AsyncSession,
    candidates: list[dict[str, object]],
) -> list[YcrCapabilityIndex]:
    index_ids = [_index_id(candidate) for candidate in candidates]
    records_by_index_id = await _load_capability_index_records(
        db,
        index_ids,
    )
    indexed: list[YcrCapabilityIndex] = []
    for candidate, index_id in zip(candidates, index_ids, strict=False):
        record = records_by_index_id.get(index_id)
        if (
            record is not None
            and record.index_version == CAPABILITY_INDEX_VERSION
            and record.capability_id == str(candidate.get("capability_id") or "")
            and record.canonical_name == str(candidate.get("canonical_name") or "")
            and record.embedding_json
            and record.sparse_json
        ):
            indexed.append(record)
    return indexed


async def _load_capability_index_records(
    db: AsyncSession,
    index_ids: list[str],
) -> dict[str, YcrCapabilityIndex]:
    if not index_ids:
        return {}
    result = await db.execute(
        select(YcrCapabilityIndex).where(
            YcrCapabilityIndex.index_id.in_(bindparam("index_ids", expanding=True))
        ),
        {"index_ids": index_ids},
    )
    return {record.index_id: record for record in result.scalars().all()}


def _capability_index_document(candidate: dict[str, object]) -> dict[str, Any]:
    sources = [item for item in candidate.get("sources") or [] if isinstance(item, dict)]
    invoke = candidate.get("invoke") if isinstance(candidate.get("invoke"), dict) else {}
    registered_names = [
        str(source.get("registered_name"))
        for source in sources
        if source.get("registered_name")
    ]
    schema_text = _schema_terms(
        candidate.get("input_schema"),
        candidate.get("output_schema"),
        candidate.get("value_schema"),
    )
    examples = candidate.get("examples") or []
    identity_terms = _capability_name_terms(
        candidate.get("canonical_name"),
        *(candidate.get("aliases") or []),
        *registered_names,
    )
    artifact_text = " ".join(
        item
        for item in [
            _compact_json_text(candidate.get("artifact_inputs") or []),
            _compact_json_text(candidate.get("artifact_outputs") or []),
        ]
        if item
    )
    source_text = _compact_json_text(
        [
            {
                "registered_name": source.get("registered_name"),
                "platform_os": source.get("platform_os"),
                "dispatchable": source.get("dispatchable"),
                "execution_requirements": source.get("execution_requirements"),
                "resource_keys": source.get("resource_keys"),
                "supports_progress": source.get("supports_progress"),
                "supports_cancel": source.get("supports_cancel"),
                "supports_resume": source.get("supports_resume"),
            }
            for source in sources
        ]
    )
    return {
        "identity": " ".join(
            str(value)
            for value in [
                candidate.get("canonical_name"),
                identity_terms,
                *(candidate.get("aliases") or []),
                *registered_names,
            ]
            if value
        ),
        "intent_text": " ".join(
            str(value)
            for value in [
                candidate.get("display_name"),
                candidate.get("agent_description"),
                candidate.get("description"),
                " ".join(str(tag) for tag in candidate.get("tags") or []),
            ]
            if value
        ),
        "schema_text": schema_text,
        "examples_text": _compact_json_text(examples),
        "io_contract_text": " ".join(
            item for item in [schema_text, artifact_text] if item
        ),
        "runtime_contract_text": source_text,
        "constraints_text": " ".join(
            str(value)
            for value in [
                candidate.get("capability_type"),
                candidate.get("risk"),
                candidate.get("effect"),
                artifact_text,
            ]
            if value
        ),
        "metadata": {
            "canonical_name": candidate.get("canonical_name"),
            "capability_type": candidate.get("capability_type"),
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
        },
    }


def _capability_index_text(document: dict[str, Any]) -> str:
    sections = [
        ("identity", document.get("identity")),
        ("intent", document.get("intent_text")),
        ("io", document.get("io_contract_text") or document.get("schema_text")),
        ("examples", document.get("examples_text")),
        ("runtime", document.get("runtime_contract_text")),
        ("constraints", document.get("constraints_text")),
    ]
    return "\n".join(f"{name}: {text}" for name, text in sections if text)


def _capability_name_terms(*values: object) -> str:
    terms: list[str] = []
    for value in values:
        if not value:
            continue
        for token in TOKEN_RE.findall(str(value).replace(".", " ").replace("_", " ").lower()):
            if len(token) > 1:
                terms.append(token)
    return " ".join(dict.fromkeys(terms))


def _stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _index_id(candidate: dict[str, object]) -> str:
    seed = f"{candidate.get('capability_id')}:{candidate.get('canonical_name')}"
    return f"capidx_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _query_terms(query: str) -> list[str]:
    return [
        token
        for token in TOKEN_RE.findall(query.lower())
        if len(token) > 1 and not token.isdigit()
    ]


def _stringify_for_lexical(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _schema_terms(*schemas: object) -> str:
    terms: list[str] = []
    for schema in schemas:
        _collect_schema_terms(schema, terms)
    return " ".join(dict.fromkeys(terms))


def _collect_schema_terms(value: object, terms: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"properties", "$defs", "definitions"} and isinstance(item, dict):
                terms.extend(str(name) for name in item)
            elif key in {"description", "title"} and isinstance(item, str):
                terms.append(item)
            else:
                _collect_schema_terms(item, terms)
    elif isinstance(value, list):
        for item in value:
            _collect_schema_terms(item, terms)


def _compact_json_text(value: object) -> str:
    if value in (None, [], {}):
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in values) + "]"


def _bounded_limit(value: int) -> int:
    return max(1, min(int(value), 50))


def _has_structured_filter(filters: dict[str, object]) -> bool:
    ignored = {"projection", "capability_type", "include_inactive"}
    for key, value in filters.items():
        if key in ignored:
            continue
        if isinstance(value, list) and value:
            return True
        if isinstance(value, bool):
            return True
        if value not in (None, "", [], {}):
            return True
    return False
