"""Node service — business logic for YQP node protocol messages."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.node import Node
from yequ.protocol import JobDeliveryMode, NodeStatus


async def handle_hello(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process node.hello — accept a Node connection.

    Updates Node status to online, records daemon version and platform info.
    Returns node.accepted payload with protocol negotiation parameters.
    """
    node.status = NodeStatus.ONLINE
    node.daemon_version = payload.get("daemon_version")
    node.last_seen_at = datetime.now(UTC)

    platform = payload.get("platform", {})
    if platform:
        node.platform_os = platform.get("os")
        node.platform_arch = platform.get("arch")

    node.heartbeat_interval_sec = settings.default_heartbeat_interval_sec
    node.job_delivery_mode = JobDeliveryMode.POLL

    await db.commit()

    return {
        "heartbeat_interval_sec": settings.default_heartbeat_interval_sec,
        "heartbeat_timeout_multiplier": settings.default_heartbeat_timeout_multiplier,
        "signal_report_interval_sec": settings.default_signal_report_interval_sec,
        "signal_stale_multiplier": settings.default_signal_stale_multiplier,
        "job_delivery_mode": JobDeliveryMode.POLL,
        "job_poll_interval_sec": settings.default_job_poll_interval_sec,
        "server_time": datetime.now(UTC).isoformat(),
    }


async def handle_heartbeat(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process node.heartbeat — update last_seen and heartbeat times.

    If node was OFFLINE or REJOINING, brings it back to ONLINE.
    """
    now = datetime.now(UTC)
    node.last_seen_at = now
    node.last_heartbeat_at = now

    # If node was offline or rejoining, bring back to online
    if node.status in (NodeStatus.OFFLINE, NodeStatus.REJOINING):
        node.status = NodeStatus.ONLINE

    await db.commit()

    return {}  # No meaningful response payload for heartbeat
