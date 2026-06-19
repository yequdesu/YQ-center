"""Node service — business logic for YQP node protocol messages."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.capability import Capability
from yequ.models.node import Node
from yequ.models.timeline import TimelineEvent
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
        for fn in plugin.get("functions", []):
            cap = Capability(
                node_record_id=node.id,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                capability_type="function",
                name=fn["name"],
                status=plugin_status,
                input_schema=fn.get("input_schema"),
                output_schema=fn.get("output_schema"),
                risk=fn.get("risk"),
                effect=fn.get("effect"),
                timeout_sec=fn.get("timeout_sec"),
                idempotency=fn.get("idempotency"),
                resource_keys=fn.get("resource_keys"),
                conflict_policy=fn.get("conflict_policy"),
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


async def handle_signal_report(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process signal.report — validate and record signal values.

    Each signal value is validated against its registered value_schema
    (from the Capability table). Invalid values write an audit event
    and are rejected. Valid values write a signal.reported timeline event.
    Unknown signal names are accepted without schema validation.
    """
    import jsonschema

    signals = payload.get("signals", [])
    accepted = 0
    rejected = 0
    now = datetime.now(UTC)

    # Get next global_seq for timeline events
    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0
    next_seq = max_seq + 1

    # Load registered signal schemas for this node
    result = await db.execute(
        select(Capability).where(
            Capability.node_record_id == node.id,
            Capability.capability_type == "signal",
            Capability.is_active,
        )
    )
    registered: dict[str, dict] = {}
    for cap in result.scalars().all():
        if cap.value_schema:
            registered[cap.name] = cap.value_schema

    for sig in signals:
        name = sig["name"]
        value = sig.get("value")
        value_schema = registered.get(name)

        # Validate against value_schema if we have one registered
        if value_schema is not None:
            try:
                jsonschema.validate(value, value_schema)
            except jsonschema.ValidationError as e:
                # Write audit event for schema validation failure
                event = TimelineEvent(
                    global_seq=next_seq,
                    event_type="signal.schema_invalid",
                    actor_type="system",
                    actor_id=node.node_id,
                    node_id=node.node_id,
                    data={
                        "signal_name": name,
                        "value": value,
                        "error": str(e),
                    },
                    timestamp=now,
                )
                next_seq += 1
                db.add(event)
                rejected += 1
                continue

        # Write accepted signal as timeline event
        event = TimelineEvent(
            global_seq=next_seq,
            event_type="signal.reported",
            actor_type="system",
            actor_id=node.node_id,
            node_id=node.node_id,
            data={
                "signal_name": name,
                "value": value,
                "scope": sig.get("scope"),
                "collected_at": sig.get("collected_at"),
                "ttl_sec": sig.get("ttl_sec"),
            },
            timestamp=now,
        )
        next_seq += 1
        db.add(event)
        accepted += 1

    await db.commit()

    return {
        "accepted": accepted,
        "rejected": rejected,
    }
