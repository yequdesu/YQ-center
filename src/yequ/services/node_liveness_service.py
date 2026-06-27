"""Node Liveness Service — effective status computation and stale node detection.

Central authority for:
- Effective status (online / degraded / offline) computed from heartbeat freshness
- Schedulability gating
- Stale node timeout
- Liveness snapshot for API responses
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.config import Settings
from yequ.models.node import Node
from yequ.models.timeline import TimelineEvent
from yequ.protocol.enums import NodeStatus
from yequ.services.timeline_writer import add_timeline_event
from yequ.types import JsonObject

# ── Effective status computation ──


def compute_effective_status(node: Node, settings: Settings) -> str:
    """Return the effective (runtime) status of a Node.

    Rules:
    - Stored status offline → effective offline (already detected)
    - No heartbeat ever + stored status provisioned → online (newly registered)
    - No heartbeat ever + not provisioned → offline
    - Heartbeat age > interval * multiplier → offline (stale)
    - Heartbeat age > interval * (multiplier - 1) → degraded (approaching timeout)
    - Otherwise → online
    """
    now = datetime.now(UTC)
    interval = node.heartbeat_interval_sec or settings.default_heartbeat_interval_sec
    multiplier = settings.default_heartbeat_timeout_multiplier

    # If DB already says offline, respect it
    if node.status == NodeStatus.OFFLINE:
        return NodeStatus.OFFLINE

    # No heartbeat ever — treat as online if freshly connected
    if node.last_heartbeat_at is None:
        if node.status in (NodeStatus.PROVISIONED, NodeStatus.ONLINE):
            return NodeStatus.ONLINE  # not yet sent heartbeat, but OK
        return NodeStatus.OFFLINE

    # Normalize: SQLite may strip timezone, make aware before arithmetic
    last_hb = node.last_heartbeat_at
    if last_hb.tzinfo is None:
        last_hb = last_hb.replace(tzinfo=UTC)

    age_sec = (now - last_hb).total_seconds()

    # Compute thresholds
    offline_threshold = interval * multiplier  # e.g. 10 * 3 = 30s
    degraded_threshold = interval * (multiplier - 1)  # e.g. 10 * 2 = 20s

    if age_sec > offline_threshold:
        return NodeStatus.OFFLINE
    if age_sec > degraded_threshold:
        return NodeStatus.DEGRADED

    # Note: capability/plugin error check requires eager-loaded capabilities.
    # For now, degraded is purely heartbeat-age based.
    return NodeStatus.ONLINE


# ── Schedulability ──


def is_node_schedulable(node: Node, settings: Settings) -> tuple[bool, str | None]:
    """Return (True, None) if the node can accept new jobs, or (False, reason)."""
    effective = compute_effective_status(node, settings)
    if effective == NodeStatus.ONLINE:
        return True, None
    elif effective == NodeStatus.DEGRADED:
        return False, "node_degraded"
    else:
        return False, "node_offline"


# ── Timeline helpers ──


async def _write_node_timeline(
    db: AsyncSession,
    event_type: str,
    node: Node,
    *,
    data: JsonObject | None = None,
) -> None:
    """Write a node lifecycle timeline event synchronously."""
    event_data: JsonObject = {
        "node_id": node.node_id,
        "stored_status": node.status,
        "effective_status": node.status,
    }
    if data:
        event_data.update(data)

    event = TimelineEvent(
        global_seq=0,
        event_type=event_type,
        actor_type="system",
        actor_id="node_liveness",
        node_id=node.node_id,
        data=event_data,
        timestamp=datetime.now(UTC),
    )
    await add_timeline_event(db, event)


# ── Stale node detection ──


async def mark_timed_out_nodes(
    db: AsyncSession,
    settings: Settings,
) -> list[Node]:
    """Scan for nodes with stale heartbeats and transition them to offline.

    Returns the list of nodes that were marked offline in this scan.
    """
    now = datetime.now(UTC)
    interval = settings.default_heartbeat_interval_sec
    multiplier = settings.default_heartbeat_timeout_multiplier
    timeout_sec = interval * multiplier

    # Query nodes that should be online/degraded but might be stale
    result = await db.execute(
        select(Node).where(
            Node.status.in_([NodeStatus.ONLINE, NodeStatus.DEGRADED]),
        )
    )
    candidates = result.scalars().all()

    timed_out: list[Node] = []
    for node in candidates:
        if node.last_heartbeat_at is None:
            continue

        # Idempotent guard: don't write another offline transition
        # if the node is already offline (handles race with heartbeat handler).
        if node.status == NodeStatus.OFFLINE:
            continue

        last_hb = node.last_heartbeat_at
        if last_hb.tzinfo is None:
            last_hb = last_hb.replace(tzinfo=UTC)
        age_sec = (now - last_hb).total_seconds()
        if age_sec > timeout_sec:
            node.status = NodeStatus.OFFLINE
            timed_out.append(node)

            # Write node.offline timeline event
            await _write_node_timeline(
                db,
                "node.offline",
                node,
                data={
                    "reason": "heartbeat_timeout",
                    "last_heartbeat_at": node.last_heartbeat_at.isoformat(),
                    "timeout_sec": timeout_sec,
                    "heartbeat_age_sec": round(age_sec, 1),
                },
            )

            # Cancel stale queued jobs for this node
            await _timeout_queued_jobs_for_node(db, node.node_id, settings)

    if timed_out:
        await db.commit()

    return timed_out


async def _timeout_queued_jobs_for_node(
    db: AsyncSession,
    node_id: str,
    settings: Settings,
) -> int:
    """Cancel queued jobs for a node that just went offline.

    Returns count of jobs cancelled.
    """
    from yequ.models.job import Job as JobModel
    from yequ.services.job_service import cancel_job

    result = await db.execute(
        select(JobModel).where(
            JobModel.node_id == node_id,
            JobModel.status == "queued",
        )
    )
    queued_jobs = result.scalars().all()

    count = 0
    for job in queued_jobs:
        await cancel_job(
            db,
            job,
            reason="node_offline_before_claim",
            node_id=node_id,
        )
        job.error_code = "NODE_OFFLINE_BEFORE_CLAIM"
        job.error_message = f"Node {node_id} went offline before job was claimed"
        count += 1

    if count > 0:
        await db.flush()

    return count


# ── Liveness snapshot for API ──


def get_node_liveness_snapshot(node: Node, settings: Settings) -> JsonObject:
    """Return a dict suitable for JSON API response with liveness details."""
    effective = compute_effective_status(node, settings)
    now = datetime.now(UTC)

    heartbeat_age_sec = None
    heartbeat_stale = False

    if node.last_heartbeat_at is not None:
        last_hb = node.last_heartbeat_at
        if last_hb.tzinfo is None:
            last_hb = last_hb.replace(tzinfo=UTC)
        heartbeat_age_sec = round((now - last_hb).total_seconds(), 1)
        interval = node.heartbeat_interval_sec or settings.default_heartbeat_interval_sec
        heartbeat_stale = heartbeat_age_sec > interval

    schedulable, unavailable_reason = is_node_schedulable(node, settings)

    return {
        "effective_status": effective,
        "heartbeat_age_sec": heartbeat_age_sec,
        "heartbeat_stale": heartbeat_stale,
        "schedulable": schedulable,
        "unavailable_reason": unavailable_reason,
    }
