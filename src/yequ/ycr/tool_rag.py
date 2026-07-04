"""Tool RAG boundary for capability discovery."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.services.capability_registry import capability_describe, capability_search


async def retrieve_tool_context(
    db: AsyncSession,
    *,
    query: str | None = None,
    node_id: str | None = None,
    platform_os: str | None = None,
    filters: dict[str, object] | None = None,
    limit: int = 10,
) -> dict[str, object]:
    filters = filters or {}
    capabilities = await capability_search(
        db,
        query=query,
        node_id=node_id,
        platform_os=platform_os,
        effect=_str_or_none(filters.get("effect")),
        risk=_str_or_none(filters.get("risk")),
        runtime_kind=_str_or_none(filters.get("runtime_kind")),
        runtime_labels=[str(item) for item in filters.get("runtime_labels") or []],
        supports_progress=_bool_or_none(filters.get("supports_progress")),
        supports_cancel=_bool_or_none(filters.get("supports_cancel")),
        supports_resume=_bool_or_none(filters.get("supports_resume")),
        preflight_supported=_bool_or_none(filters.get("preflight_supported")),
        artifact_input=_bool_or_none(filters.get("artifact_input")),
        artifact_output=_bool_or_none(filters.get("artifact_output")),
        projection=_str_or_none(filters.get("projection")) or "summary",
        capability_type=_str_or_none(filters.get("capability_type")) or "function",
        include_inactive=bool(filters.get("include_inactive", False)),
        limit=limit,
    )
    return {
        "kind": "tool_rag_result",
        "query": query or "",
        "matches": capabilities,
        "match_count": len(capabilities),
    }


async def recommend_tool_context(
    db: AsyncSession,
    *,
    query: str,
    node_id: str | None = None,
    platform_os: str | None = None,
    filters: dict[str, object] | None = None,
    limit: int = 5,
) -> dict[str, object]:
    terms = [term.lower() for term in query.split() if term.strip()]
    result = await retrieve_tool_context(
        db,
        query=query,
        node_id=node_id,
        platform_os=platform_os,
        filters=filters,
        limit=max(limit * 3, 10),
    )
    matches = result["matches"] if isinstance(result.get("matches"), list) else []
    ranked = sorted(
        [item for item in matches if isinstance(item, dict)],
        key=lambda item: _score_tool(item, terms),
        reverse=True,
    )
    return {
        "kind": "tool_rag_recommendation",
        "query": query,
        "recommendations": ranked[:limit],
        "match_count": len(ranked),
    }


async def describe_tool_context(
    db: AsyncSession,
    *,
    capability_ref: str,
    node_id: str | None = None,
    sections: list[str] | None = None,
    projection: str = "invoke_ready",
) -> dict[str, object]:
    return {
        "kind": "tool_rag_description",
        "capability": await capability_describe(
            db,
            capability_ref,
            node_id=node_id,
            sections=sections or [],
            projection=projection,
            include_inactive=False,
        ),
    }


def _score_tool(item: dict[str, object], terms: list[str]) -> int:
    text = " ".join(
        str(item.get(key) or "")
        for key in ("canonical_name", "display_name", "description", "agent_description")
    ).lower()
    return sum(
        3 if term in str(item.get("canonical_name", "")).lower() else 1
        for term in terms
        if term in text
    )


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
