"""Capability discovery boundary backed by Center's registry.

This module is intentionally not Tool RAG.  It exposes deterministic registry
search/describe through the YCR service boundary while a real capability vector
index is not implemented.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.services.capability_registry import capability_describe, capability_search


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
        "kind": "capability_registry_search_result",
        "query": query or "",
        "matches": capabilities,
        "match_count": len(capabilities),
        "retrieval": {
            "strategy": "registry_filter_v1",
            "semantic": {"enabled": False, "reason": "capability_vector_index_not_implemented"},
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
