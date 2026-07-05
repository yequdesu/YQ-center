"""Capability discovery boundary for registry filters and Tool RAG."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.ycr import YcrCapabilityIndex
from yequ.services.capability_registry import capability_describe, capability_search
from yequ.ycr.embedding import EmbeddingError, embed_text_full
from yequ.ycr.retrieval import TOKEN_RE, cosine_similarity

TOOL_RAG_CANDIDATE_LIMIT = 500
CAPABILITY_INDEX_VERSION = 2
RETRIEVAL_TOP_K = 50
RRF_K = 60
RRF_RELATIVE_SCORE_FLOOR = 0.70
DENSE_ONLY_MIN_SCORE = 0.50
MAX_INDEX_BUILDS_PER_SEARCH = 12


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
    requested_projection = str(filters.get("projection") or "summary")
    index_filters = {**filters, "projection": "schema"}
    index_candidates = await capability_search(
        db,
        query=None,
        **index_filters,
        limit=TOOL_RAG_CANDIDATE_LIMIT,
        max_limit=TOOL_RAG_CANDIDATE_LIMIT,
    )
    return_candidates = await capability_search(
        db,
        query=None,
        **filters,
        limit=TOOL_RAG_CANDIDATE_LIMIT,
        max_limit=TOOL_RAG_CANDIDATE_LIMIT,
    )
    if not index_candidates:
        return {
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
        }

    try:
        index_result = await _ensure_capability_indexes(db, index_candidates)
        indexed = index_result.indexed
        query_embedding = await embed_text_full(query)
    except EmbeddingError as exc:
        raise ValueError(f"capability_rag_unavailable: {exc}") from exc

    return_by_name = {str(item.get("canonical_name") or ""): item for item in return_candidates}
    dense_rows: list[tuple[str, float]] = []
    sparse_rows: list[tuple[str, float]] = []
    trace_by_name: dict[str, dict[str, Any]] = {}
    for index in indexed:
        if not isinstance(index.embedding_json, list):
            continue
        dense_score = cosine_similarity(
            query_embedding.dense,
            [float(value) for value in index.embedding_json],
        )
        sparse_score = _sparse_dot(query_embedding.sparse, index.sparse_json or {})
        field_matches = _field_evidence(query, index.document_json or {})
        trace_by_name[index.canonical_name] = {
            "dense_score": dense_score,
            "sparse_score": sparse_score,
            "field_matches": field_matches,
            "index": index,
        }
        dense_rows.append((index.canonical_name, dense_score))
        if sparse_score > 0:
            sparse_rows.append((index.canonical_name, sparse_score))
    ranked_names = _rrf_fusion(
        dense_rows,
        sparse_rows,
        top_k=RETRIEVAL_TOP_K,
    )

    matches: list[dict[str, object]] = []
    for name, rrf_score, ranks in ranked_names[: _bounded_limit(limit)]:
        trace = trace_by_name.get(name)
        if trace is None:
            continue
        index = trace["index"]
        candidate = return_by_name.get(name)
        if candidate is None:
            candidate = next(
                item for item in index_candidates if str(item.get("canonical_name") or "") == name
            )
        output = dict(candidate)
        output["retrieval"] = {
            "strategy": "tool_rag_bge_m3_rrf_v1",
            "score": rrf_score,
            "rrf_score": rrf_score,
            "dense_rank": ranks.get("dense_rank"),
            "dense_score": trace["dense_score"],
            "sparse_rank": ranks.get("sparse_rank"),
            "sparse_score": trace["sparse_score"],
            "field_matches": trace["field_matches"],
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
            "strategy": "tool_rag_bge_m3_rrf_v1",
            "candidate_count": len(index_candidates),
            "candidate_limit": TOOL_RAG_CANDIDATE_LIMIT,
            "requested_projection": requested_projection,
            "indexed_count": len(indexed),
            "index_build": {
                "built_count": index_result.built_count,
                "stale_or_missing_count": index_result.stale_or_missing_count,
                "skipped_locked_count": index_result.skipped_locked_count,
                "deferred_count": index_result.deferred_count,
            },
            "semantic": {
                "enabled": True,
                "provider": query_embedding.provider,
                "model": query_embedding.model,
                "dense_result_count": len(dense_rows),
                "sparse_result_count": len(sparse_rows),
                "match_count": len(matches),
            },
        },
    }


def _rrf_fusion(
    dense_rows: list[tuple[str, float]],
    sparse_rows: list[tuple[str, float]],
    *,
    top_k: int,
) -> list[tuple[str, float, dict[str, int]]]:
    if not sparse_rows:
        dense_ranked = sorted(dense_rows, key=lambda item: item[1], reverse=True)[:top_k]
        if not dense_ranked or dense_ranked[0][1] < DENSE_ONLY_MIN_SCORE:
            return []
        return [(dense_ranked[0][0], dense_ranked[0][1], {"dense_rank": 1})]

    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for channel, rows in (
        ("dense", sorted(dense_rows, key=lambda item: item[1], reverse=True)[:top_k]),
        ("sparse", sorted(sparse_rows, key=lambda item: item[1], reverse=True)[:top_k]),
    ):
        for rank, (name, _score) in enumerate(rows, start=1):
            scores[name] = scores.get(name, 0.0) + 1.0 / (RRF_K + rank)
            ranks.setdefault(name, {})[f"{channel}_rank"] = rank
    return [
        (name, score, ranks.get(name, {}))
        for name, score in _apply_relative_score_floor(
            sorted(scores.items(), key=lambda item: item[1], reverse=True)
        )
    ]


def _sparse_dot(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(float(value) * float(right.get(key, 0.0)) for key, value in left.items())


def _apply_relative_score_floor(rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    if not rows:
        return []
    floor = rows[0][1] * RRF_RELATIVE_SCORE_FLOOR
    return [row for row in rows if row[1] >= floor]


class CapabilityIndexBuildResult:
    def __init__(
        self,
        *,
        indexed: list[YcrCapabilityIndex],
        built_count: int,
        stale_or_missing_count: int,
        skipped_locked_count: int,
        deferred_count: int,
    ) -> None:
        self.indexed = indexed
        self.built_count = built_count
        self.stale_or_missing_count = stale_or_missing_count
        self.skipped_locked_count = skipped_locked_count
        self.deferred_count = deferred_count


async def _ensure_capability_indexes(
    db: AsyncSession,
    candidates: list[dict[str, object]],
) -> CapabilityIndexBuildResult:
    indexed: list[YcrCapabilityIndex] = []
    build_count = 0
    skipped_locked_count = 0
    deferred_count = 0
    candidate_docs = []
    for candidate in candidates:
        document = _capability_index_document(candidate)
        index_id = _index_id(candidate)
        candidate_docs.append(
            (
                candidate,
                index_id,
                document,
                _capability_index_text(document),
                _stable_hash(document),
            )
        )

    records_by_index_id = await _load_capability_index_records(
        db,
        [item[1] for item in candidate_docs],
    )
    stale_or_missing_count = 0
    for candidate, index_id, document, index_text, document_hash in candidate_docs:
        record = records_by_index_id.get(index_id)
        if (
            record is not None
            and record.index_version == CAPABILITY_INDEX_VERSION
            and record.document_hash == document_hash
            and record.embedding_json
            and record.sparse_json
        ):
            indexed.append(record)
            continue
        stale_or_missing_count += 1
        if build_count >= MAX_INDEX_BUILDS_PER_SEARCH:
            deferred_count += 1
            continue
        if not await _try_capability_index_lock(db, index_id):
            skipped_locked_count += 1
            continue
        embedding = await embed_text_full(index_text)
        if record is None:
            record = YcrCapabilityIndex(index_id=index_id)
            db.add(record)
        record.capability_id = str(candidate.get("capability_id") or "")
        record.canonical_name = str(candidate.get("canonical_name") or "")
        record.capability_type = str(candidate.get("capability_type") or "function")
        record.index_version = CAPABILITY_INDEX_VERSION
        record.document_hash = document_hash
        record.document_json = document
        record.index_text = index_text
        record.embedding_json = embedding.dense
        record.embedding_provider = embedding.provider
        record.embedding_model = embedding.model
        record.sparse_json = embedding.sparse
        await db.flush()
        if db.bind and db.bind.dialect.name == "postgresql":
            await db.execute(
                text(
                    "UPDATE ycr_capability_index "
                    "SET embedding_vector = CAST(:embedding AS vector) "
                    "WHERE index_id = :index_id"
                ),
                {"embedding": _vector_literal(embedding.dense), "index_id": index_id},
            )
        indexed.append(record)
        build_count += 1
    return CapabilityIndexBuildResult(
        indexed=indexed,
        built_count=build_count,
        stale_or_missing_count=stale_or_missing_count,
        skipped_locked_count=skipped_locked_count,
        deferred_count=deferred_count,
    )


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


async def _try_capability_index_lock(db: AsyncSession, index_id: str) -> bool:
    if not db.bind or db.bind.dialect.name != "postgresql":
        return True
    result = await db.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"ycr_capability_index:{index_id}"},
    )
    return bool(result.scalar_one())


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
    return {
        "identity": " ".join(
            str(value)
            for value in [
                candidate.get("canonical_name"),
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
        "constraints_text": " ".join(
            str(value)
            for value in [
                candidate.get("capability_type"),
                candidate.get("risk"),
                candidate.get("effect"),
                _compact_json_text(candidate.get("artifact_inputs") or []),
                _compact_json_text(candidate.get("artifact_outputs") or []),
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
        ("schema", document.get("schema_text")),
        ("examples", document.get("examples_text")),
        ("constraints", document.get("constraints_text")),
    ]
    return "\n".join(f"{name}: {text}" for name, text in sections if text)


def _stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _index_id(candidate: dict[str, object]) -> str:
    seed = f"{candidate.get('capability_id')}:{candidate.get('canonical_name')}"
    return f"capidx_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _field_evidence(query: str, document: dict[str, Any]) -> list[str]:
    query_terms = _query_terms(query)
    if not query_terms:
        return []
    fields = [
        "identity",
        "intent_text",
        "schema_text",
        "examples_text",
        "constraints_text",
    ]
    matches: list[str] = []
    for term in query_terms:
        for field in fields:
            tokens = _field_tokens(document.get(field))
            if term in tokens:
                matches.append(f"{field}:{term}")
                break
    return matches[:8]


def _query_terms(query: str) -> list[str]:
    return [
        token
        for token in TOKEN_RE.findall(query.lower())
        if len(token) > 1 and not token.isdigit()
    ]


def _field_tokens(value: object) -> set[str]:
    text_value = _stringify_for_lexical(value).lower()
    raw_tokens = TOKEN_RE.findall(text_value)
    tokens: set[str] = set()
    for token in raw_tokens:
        if len(token) > 1:
            tokens.add(token)
        for part in re.split(r"[_\-.\\/]+", token):
            if len(part) > 1:
                tokens.add(part)
    return tokens


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
