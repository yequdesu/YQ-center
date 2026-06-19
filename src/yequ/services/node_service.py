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


async def handle_register_capabilities(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process node.register_capabilities — full snapshot semantics.

    For each plugin in the payload:
    - Deactivate all existing capabilities for this (node, plugin_id)
    - Insert the new set of functions and signals as active
    - Plugins with status "error" are recorded but no functions/signals
    """
    from sqlalchemy import update as sql_update

    from yequ.models.capability import Capability

    plugins = payload.get("plugins", [])
    registered_count = 0
    failed_count = 0
    now = datetime.now(UTC)

    for plugin in plugins:
        plugin_id = plugin["plugin_id"]
        plugin_version = plugin.get("plugin_version", "0.0.0")
        plugin_status = plugin.get("status", "loaded")

        # Deactivate ALL existing capabilities for this (node, plugin_id)
        await db.execute(
            sql_update(Capability)
            .where(
                Capability.node_record_id == node.id,
                Capability.plugin_id == plugin_id,
            )
            .values(is_active=False)
        )

        if plugin_status == "error":
            # Record the failed plugin but don't register its functions/signals
            error_info = plugin.get("error", {})
            cap = Capability(
                node_record_id=node.id,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                capability_type="function",
                name=f"{plugin_id}.error",
                status="error",
                error_code=error_info.get("code"),
                error_message=error_info.get("message"),
                is_active=True,
                registered_at=now,
            )
            db.add(cap)
            failed_count += 1
            continue

        # Register functions
        for func in plugin.get("functions", []):
            cap = Capability(
                node_record_id=node.id,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                capability_type="function",
                name=func["name"],
                status=plugin_status,
                input_schema=func.get("input_schema"),
                output_schema=func.get("output_schema"),
                risk=func.get("risk"),
                effect=func.get("effect"),
                timeout_sec=func.get("timeout_sec"),
                idempotency=func.get("idempotency"),
                resource_keys=func.get("resource_keys"),
                conflict_policy=func.get("conflict_policy"),
                is_active=True,
                registered_at=now,
            )
            db.add(cap)
            registered_count += 1

        # Register signals
        for sig in plugin.get("signals", []):
            cap = Capability(
                node_record_id=node.id,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                capability_type="signal",
                name=sig["name"],
                status=plugin_status,
                scope=sig.get("scope"),
                ttl_sec=sig.get("ttl_sec"),
                value_schema=sig.get("value_schema"),
                is_active=True,
                registered_at=now,
            )
            db.add(cap)
            registered_count += 1

    await db.commit()

    return {
        "registered_count": registered_count,
        "failed_count": failed_count,
        "accepted_at": now.isoformat(),
    }
