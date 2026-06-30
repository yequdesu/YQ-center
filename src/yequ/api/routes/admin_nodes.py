"""Admin endpoints for Nodes, runtimes, and capabilities."""

from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import CursorResult, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.deps import get_admin_token, get_db
from yequ.api.routes.admin_schemas import (
    CapabilitySummary,
    NodeDetail,
    NodeSummary,
    RuntimeSummary,
    _cap_summary,
    _node_detail,
    _node_summary,
    _runtime_summary,
)
from yequ.models.capability import Capability
from yequ.models.node import Node
from yequ.models.runtime_instance import RuntimeInstance
from yequ.models.signal_state import SignalState
from yequ.services.capability_registry import (
    capability_describe,
    capability_search,
    node_list,
    node_status,
)
from yequ.services.signal_state_service import (
    compute_signal_freshness,
    list_signal_states,
    signal_state_to_dict,
)
from yequ.shared_types import JsonObject

router = APIRouter(prefix="/admin", tags=["admin"])


async def _signal_counts_by_node(
    db: AsyncSession,
    *,
    node_id: str | None = None,
) -> dict[str, dict[str, int]]:
    stmt = select(SignalState)
    if node_id:
        stmt = stmt.where(SignalState.node_id == node_id)
    result = await db.execute(stmt)
    counts: dict[str, dict[str, int]] = {}
    now = datetime.now(UTC)
    for state in result.scalars().all():
        by_node = counts.setdefault(state.node_id, {"fresh": 0, "stale": 0})
        if compute_signal_freshness(state, now=now) == "stale":
            by_node["stale"] += 1
        else:
            by_node["fresh"] += 1
    return counts


def _apply_signal_counts(summary: NodeSummary, counts: dict[str, int]) -> None:
    summary.fresh_signal_count = counts.get("fresh", 0)
    summary.stale_signal_count = counts.get("stale", 0)
    summary.signal_stale = summary.stale_signal_count > 0


@router.get("/nodes", response_model=list[NodeSummary])
async def list_nodes(
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[NodeSummary]:
    """List all provisioned nodes."""
    result = await db.execute(select(Node).order_by(Node.node_id))
    nodes = result.scalars().all()
    signal_counts = await _signal_counts_by_node(db)
    summaries = [_node_summary(n) for n in nodes]
    for summary in summaries:
        _apply_signal_counts(summary, signal_counts.get(summary.node_id, {}))
    return summaries


@router.get("/nodes/{node_id}", response_model=NodeDetail)
async def get_node(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> NodeDetail:
    """Get a single node's details."""
    result = await db.execute(select(Node).where(Node.node_id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")
    detail = _node_detail(node)
    signal_counts = await _signal_counts_by_node(db, node_id=node_id)
    _apply_signal_counts(detail, signal_counts.get(node_id, {}))
    return detail


# ── Node management endpoints ──


@router.get("/runtimes", response_model=list[RuntimeSummary])
async def list_runtimes(
    node_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[RuntimeSummary]:
    """List platform-neutral runtime instances reported by Nodes."""
    stmt = select(RuntimeInstance, Node.node_id).join(
        Node, RuntimeInstance.node_record_id == Node.id
    )
    if node_id:
        stmt = stmt.where(Node.node_id == node_id)
    stmt = stmt.order_by(Node.node_id, RuntimeInstance.runtime_id)
    result = await db.execute(stmt)
    return [_runtime_summary(runtime, runtime_node_id) for runtime, runtime_node_id in result.all()]


@router.get("/nodes/{node_id}/runtimes", response_model=list[RuntimeSummary])
async def list_node_runtimes(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[RuntimeSummary]:
    """List runtimes for a single Node."""
    node_result = await db.execute(select(Node).where(Node.node_id == node_id))
    node = node_result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")
    result = await db.execute(
        select(RuntimeInstance)
        .where(RuntimeInstance.node_record_id == node.id)
        .order_by(RuntimeInstance.runtime_id)
    )
    return [_runtime_summary(runtime, node.node_id) for runtime in result.scalars().all()]


@router.get("/signals")
async def list_signals(
    node_id: str | None = None,
    signal_name: str | None = None,
    freshness_status: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[dict[str, object]]:
    """List current SignalState values."""
    states = await list_signal_states(
        db,
        node_id=node_id,
        signal_name=signal_name,
        freshness_status=freshness_status,
        refresh=False,
    )
    return [signal_state_to_dict(state) for state in states]


@router.get("/nodes/{node_id}/signals")
async def list_node_signals(
    node_id: str,
    freshness_status: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[dict[str, object]]:
    """List current SignalState values for a single Node."""
    result = await db.execute(select(Node).where(Node.node_id == node_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Node {node_id!r} not found")
    states = await list_signal_states(
        db,
        node_id=node_id,
        freshness_status=freshness_status,
        refresh=False,
    )
    return [signal_state_to_dict(state) for state in states]


@router.post("/nodes/{node_id}/capabilities/refresh-state")
async def refresh_node_capability_state(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    """Recompute executable state for all capabilities on this node."""
    result = await db.execute(select(Node).where(Node.node_id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(404, f"Node {node_id!r} not found")

    from yequ.config import get_settings
    from yequ.services.node_liveness_service import is_node_schedulable

    settings = get_settings()
    schedulable, reason = is_node_schedulable(node, settings)

    cap_result = await db.execute(
        select(Capability).where(
            Capability.node_record_id == node.id,
            Capability.is_active == True,  # noqa: E712
        )
    )
    caps = cap_result.scalars().all()

    return {
        "node_id": node_id,
        "node_status": node.status,
        "schedulable": schedulable,
        "unavailable_reason": reason if not schedulable else None,
        "active_capability_count": len(caps),
        "executable_capability_count": len(caps) if schedulable else 0,
    }


@router.post("/nodes/{node_id}/mark-offline")
async def mark_node_offline(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    """Manually set a node's status to offline."""
    from yequ.protocol import NodeStatus

    result = await db.execute(select(Node).where(Node.node_id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(404, f"Node {node_id!r} not found")

    node.status = NodeStatus.OFFLINE
    await db.commit()
    return {"node_id": node_id, "status": node.status}


@router.delete("/nodes/{node_id}/stale-capabilities", status_code=200)
async def delete_stale_capabilities(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    """Delete inactive capabilities for a node."""
    result = await db.execute(select(Node).where(Node.node_id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(404, f"Node {node_id!r} not found")

    from sqlalchemy import delete as sa_delete

    del_result = cast(
        CursorResult[object],
        await db.execute(
            sa_delete(Capability).where(
                Capability.node_record_id == node.id,
                Capability.is_active == False,  # noqa: E712
            )
        ),
    )
    await db.commit()
    return {
        "node_id": node_id,
        "deleted_count": del_result.rowcount,
    }


@router.get("/meta/nodes")
async def list_meta_nodes(
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[JsonObject]:
    """List nodes through the Center capability runtime view."""
    return await node_list(db)


@router.get("/meta/nodes/{node_id}")
async def get_meta_node(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    """Get one node through the Center capability runtime view."""
    try:
        return await node_status(db, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/meta/capabilities/search")
async def search_meta_capabilities(
    q: str | None = None,
    node_id: str | None = None,
    platform_os: str | None = None,
    effect: str | None = None,
    risk: str | None = None,
    runtime_kind: str | None = None,
    runtime_labels: list[str] = Query(default_factory=list),
    supports_progress: bool | None = None,
    supports_cancel: bool | None = None,
    supports_resume: bool | None = None,
    preflight_supported: bool | None = None,
    artifact_input: bool | None = None,
    artifact_output: bool | None = None,
    projection: str = "summary",
    capability_type: str = "function",
    include_inactive: bool = False,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[JsonObject]:
    """Search Center capability definitions and concrete sources."""
    return await capability_search(
        db,
        query=q,
        node_id=node_id,
        platform_os=platform_os,
        effect=effect,
        risk=risk,
        runtime_kind=runtime_kind,
        runtime_labels=runtime_labels or None,
        supports_progress=supports_progress,
        supports_cancel=supports_cancel,
        supports_resume=supports_resume,
        preflight_supported=preflight_supported,
        artifact_input=artifact_input,
        artifact_output=artifact_output,
        projection=projection,
        capability_type=capability_type,
        include_inactive=include_inactive,
        limit=limit,
    )


@router.get("/meta/capabilities/{capability_ref}")
async def describe_meta_capability(
    capability_ref: str,
    node_id: str | None = None,
    sections: list[str] = Query(default_factory=list),
    projection: str = "detail",
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> JsonObject:
    """Describe one Center capability definition and its sources."""
    try:
        return await capability_describe(
            db,
            capability_ref,
            node_id=node_id,
            sections=sections or None,
            projection=projection,
            include_inactive=include_inactive,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/capabilities", response_model=list[CapabilitySummary])
async def list_capabilities(
    node_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_admin_token),
) -> list[CapabilitySummary]:
    from sqlalchemy.orm import joinedload

    """List capabilities, optionally filtered by node_id."""
    stmt = select(Capability).where(Capability.is_active).options(joinedload(Capability.node))
    if node_id:
        sub = select(Node.id).where(Node.node_id == node_id).scalar_subquery()
        stmt = stmt.where(Capability.node_record_id == sub)
    stmt = stmt.order_by(Capability.name)
    result = await db.execute(stmt)
    caps = result.scalars().all()
    return [_cap_summary(c) for c in caps]
