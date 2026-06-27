"""Node service — business logic for YQP node protocol messages."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.config import Settings
from yequ.logconfig import get_logger
from yequ.models.capability import Capability
from yequ.models.job import Job
from yequ.models.node import Node
from yequ.models.runtime_instance import RuntimeInstance
from yequ.models.signal_state import SignalState
from yequ.models.timeline import TimelineEvent
from yequ.protocol import JobDeliveryMode, NodeStatus
from yequ.types import JsonObject

log = get_logger(__name__)


async def handle_hello(
    db: AsyncSession,
    node: Node,
    payload: JsonObject,
    settings: Settings,
) -> JsonObject:
    """Process node.hello — accept a Node connection.

    Updates Node status to online, records daemon version and platform info.
    Returns node.accepted payload with protocol negotiation parameters.
    """
    from yequ.services.timeline_writer import add_timeline_event

    node.status = NodeStatus.ONLINE
    node.daemon_version = payload.get("daemon_version")
    node.last_seen_at = datetime.now(UTC)

    platform = payload.get("platform", {})
    if platform:
        node.platform_os = platform.get("os")
        node.platform_arch = platform.get("arch")

    node.heartbeat_interval_sec = settings.default_heartbeat_interval_sec
    node.job_delivery_mode = JobDeliveryMode.POLL
    await _sync_runtime_instances(db, node, payload.get("runtimes") or [], now=node.last_seen_at)

    # Write node.online timeline event
    await add_timeline_event(
        db,
        TimelineEvent(
            global_seq=0,
            event_type="node.online",
            actor_type="system",
            actor_id="node_service",
            node_id=node.node_id,
            data={"node_id": node.node_id, "daemon_version": node.daemon_version},
            timestamp=datetime.now(UTC),
        ),
    )

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
    payload: JsonObject,
    settings: Settings,
) -> JsonObject:
    """Process node.heartbeat — update last_seen and heartbeat times.

    If node was OFFLINE or REJOINING, brings it back to ONLINE.
    """
    from yequ.services.timeline_writer import add_timeline_event

    now = datetime.now(UTC)
    node.last_seen_at = now
    node.last_heartbeat_at = now
    await _sync_runtime_instances(db, node, payload.get("runtimes") or [], now=now)

    # If node was offline or rejoining, bring back to online
    if node.status in (NodeStatus.OFFLINE, NodeStatus.REJOINING):
        node.status = NodeStatus.ONLINE

        # Write node.online timeline event on recovery
        await add_timeline_event(
            db,
            TimelineEvent(
                global_seq=0,
                event_type="node.online",
                actor_type="system",
                actor_id="node_service",
                node_id=node.node_id,
                data={
                    "node_id": node.node_id,
                    "previous_status": str(node.status),  # will be "online" since we already set it
                    "recovery": True,
                },
                timestamp=now,
            ),
        )

    await db.commit()

    return {}  # No meaningful response payload for heartbeat


async def handle_register_capabilities(
    db: AsyncSession,
    node: Node,
    payload: JsonObject,
    settings: Settings,
) -> JsonObject:
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
    await _sync_runtime_instances(db, node, payload.get("runtimes") or [], now=now)

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
                description=fn.get("description"),
                agent_description=fn.get("agent_description"),
                user_visible_name=fn.get("user_visible_name"),
                input_schema=fn.get("input_schema"),
                output_schema=fn.get("output_schema"),
                risk=fn.get("risk"),
                effect=fn.get("effect"),
                timeout_sec=fn.get("timeout_sec"),
                idempotency=fn.get("idempotency"),
                resource_keys=fn.get("resource_keys"),
                conflict_policy=fn.get("conflict_policy"),
                execution_context=fn.get("execution_context"),
                execution_requirements=(
                    fn.get("execution_requirements")
                    or _execution_requirements_from_context(fn.get("execution_context"))
                ),
                hidden_input_fields=fn.get("hidden_input_fields"),
                examples=fn.get("examples"),
                failure_modes=fn.get("failure_modes"),
                preflight_supported=bool(
                    fn.get("preflight_supported")
                    or fn.get("dry_run_supported")
                    or "dry_run" in (fn.get("input_schema") or {}).get("properties", {})
                ),
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


def _execution_requirements_from_context(context: str | None) -> JsonObject | None:
    """Map execution_context to platform-neutral requirements."""
    if context == "system":
        return {"runtime_kind": "privileged"}
    if context == "user":
        return {"runtime_kind": "interactive", "interactive": True}
    if context == "hybrid":
        return {
            "runtime_kind": "interactive",
            "fallback_runtime_kind": "privileged",
        }
    return None


async def _sync_runtime_instances(
    db: AsyncSession,
    node: Node,
    runtimes: list[JsonObject],
    *,
    now: datetime,
) -> None:
    """Synchronize the runtime snapshot reported by a Node.

    Center stores only platform-neutral runtime attributes. OS-specific
    worker implementation details stay inside the Node and may appear only as
    opaque metadata.
    """
    if not runtimes:
        log.warning("node %s reported no runtimes — skipping runtime sync", node.node_id)
        return

    seen: set[str] = set()
    for raw in runtimes:
        runtime_id = str(raw.get("runtime_id") or "").strip()
        if not runtime_id:
            continue
        seen.add(runtime_id)

        result = await db.execute(
            select(RuntimeInstance).where(
                RuntimeInstance.node_record_id == node.id,
                RuntimeInstance.runtime_id == runtime_id,
            )
        )
        runtime = result.scalar_one_or_none()
        if runtime is None:
            runtime = RuntimeInstance(
                node_record_id=node.id,
                runtime_id=runtime_id,
            )
            db.add(runtime)

        runtime.kind = str(raw.get("kind") or runtime.kind or "privileged")
        runtime.status = str(raw.get("status") or runtime.status or "online")
        labels = raw.get("labels")
        runtime.labels = [str(label) for label in labels] if isinstance(labels, list) else []
        runtime.owner = raw.get("owner")
        runtime.privilege = raw.get("privilege")
        runtime.interactive = bool(raw.get("interactive", False))
        runtime.last_seen_at = now
        metadata = raw.get("metadata")
        runtime.metadata_json = metadata if isinstance(metadata, dict) else None

    if seen:
        result = await db.execute(
            select(RuntimeInstance).where(RuntimeInstance.node_record_id == node.id)
        )
        for runtime in result.scalars().all():
            if runtime.runtime_id not in seen and runtime.status == "online":
                runtime.status = "offline"


async def handle_signal_report(
    db: AsyncSession,
    node: Node,
    payload: JsonObject,
    settings: Settings,
) -> JsonObject:
    """Process signal.report — validate and record signal values.

    Each signal value is validated against its registered value_schema
    (from the Capability table). Invalid values write an audit event
    and are rejected. Valid values write a signal.reported timeline event.
    Unknown signal names are accepted without schema validation.
    """
    import jsonschema

    from yequ.services.timeline_writer import add_timeline_event, get_timeline_writer

    signals = payload.get("signals", [])
    accepted = 0
    rejected = 0
    now = datetime.now(UTC)

    # Load registered signal schemas for this node
    result = await db.execute(
        select(Capability).where(
            Capability.node_record_id == node.id,
            Capability.capability_type == "signal",
            Capability.is_active,
        )
    )
    registered: dict[str, Capability] = {}
    for cap in result.scalars().all():
        registered[cap.name] = cap

    # Get the async timeline writer for accepted events
    tl_writer = get_timeline_writer()

    for sig in signals:
        name = sig["name"]
        value = sig.get("value")
        signal_cap = registered.get(name)
        value_schema = signal_cap.value_schema if signal_cap else None

        # Validate against value_schema if we have one registered
        if value_schema is not None:
            try:
                jsonschema.validate(value, value_schema)
            except jsonschema.ValidationError as e:
                # Write audit event for schema validation failure (synchronous)
                event = TimelineEvent(
                    global_seq=0,  # assigned by writer batch on flush
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
                await add_timeline_event(db, event)
                rejected += 1
                continue

        ttl_sec = sig.get("ttl_sec")
        if ttl_sec is None and signal_cap is not None:
            ttl_sec = signal_cap.ttl_sec
        ttl_int = int(ttl_sec) if ttl_sec is not None else None
        collected_at = _parse_signal_datetime(sig.get("collected_at"))
        expires_at = now + timedelta(seconds=ttl_int) if ttl_int else None

        state_result = await db.execute(
            select(SignalState).where(
                SignalState.node_id == node.node_id,
                SignalState.signal_name == name,
            )
        )
        state = state_result.scalar_one_or_none()
        if state is None:
            state = SignalState(
                node_id=node.node_id,
                signal_name=name,
                reported_at=now,
            )
            db.add(state)
        state.capability_id = signal_cap.id if signal_cap else None
        state.value = value
        state.value_schema = value_schema
        state.scope = sig.get("scope") or (signal_cap.scope if signal_cap else None)
        state.ttl_sec = ttl_int
        state.freshness_status = "fresh"
        state.quality = "ok"
        state.collected_at = collected_at
        state.reported_at = now
        state.expires_at = expires_at

        # Enqueue accepted signal as timeline event (fire-and-forget)
        event = TimelineEvent(
            global_seq=0,  # assigned by writer batch on flush
            event_type="signal.reported",
            actor_type="system",
            actor_id=node.node_id,
            node_id=node.node_id,
            data={
                "signal_name": name,
                "value": value,
                "scope": sig.get("scope"),
                "collected_at": sig.get("collected_at"),
                "ttl_sec": ttl_int,
            },
            timestamp=now,
        )
        tl_writer.enqueue(event)
        accepted += 1

    # Commit state updates and any schema_invalid events written synchronously.
    if accepted or rejected:
        await db.commit()

    return {
        "accepted": accepted,
        "rejected": rejected,
    }


def _parse_signal_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


async def handle_job_poll(
    db: AsyncSession,
    node: Node,
    payload: JsonObject,
    settings: Settings,
) -> JsonObject:
    """Process job.poll — return available jobs for this node.

    Returns up to `capacity` jobs that are queued for this node,
    accounting for currently running jobs on the node.

    available_slots = max(payload.capacity - len(payload.running_jobs), 0)
    Jobs transition from queued to claimed upon poll.
    If no jobs available, returns empty jobs list (job.empty semantics).
    """
    from sqlalchemy import select

    from yequ.models.job import Job
    from yequ.protocol import JobStatus
    from yequ.services.job_state_machine import transition

    raw_capacity = int(payload.get("capacity", 1))
    running_jobs = payload.get("running_jobs", [])
    available_slots = max(raw_capacity - len(running_jobs), 0)

    if available_slots <= 0:
        return {"jobs": []}

    result = await db.execute(
        select(Job)
        .where(
            Job.node_id == node.node_id,
            Job.status == JobStatus.QUEUED,
        )
        .order_by(Job.created_at.asc())
        .limit(available_slots)
    )
    pending_jobs = result.scalars().all()

    if not pending_jobs:
        return {"jobs": []}

    now = datetime.now(UTC)
    jobs: list[JsonObject] = []
    for job in pending_jobs:
        await transition(
            db,
            job,
            JobStatus.CLAIMED,
            node_id=node.node_id,
            invocation_id=job.invocation_id,
        )
        job.claimed_at = now
        job.lease_expires_at = datetime.fromtimestamp(now.timestamp() + job.lease_sec, tz=UTC)
        jobs.append(
            {
                "job_id": job.job_id,
                "invocation_id": job.invocation_id,
                "function": job.function_name,
                "input": job.input_payload or {},
                "runtime_id": job.runtime_id,
                "execution_requirements": job.execution_requirements_snapshot or {},
                "timeout_sec": job.timeout_sec,
                "lease_sec": job.lease_sec,
                "approval_id": getattr(job, "approval_id", None),
                "resource_keys": getattr(job, "resource_keys", []),
                "dry_run": getattr(job, "dry_run", False),
            }
        )

    await db.commit()
    return {"jobs": jobs}


async def handle_job_accepted(
    db: AsyncSession,
    node: Node,
    payload: JsonObject,
    settings: Settings,
) -> JsonObject:
    """Process job.accepted — Node confirms it will execute the job.

    Job transitions: claimed -> running.
    """
    from fastapi import HTTPException, status
    from sqlalchemy import select

    from yequ.models.job import Job
    from yequ.protocol import JobStatus
    from yequ.protocol.errors import ErrorCode, YqpError
    from yequ.services.job_state_machine import transition

    job_id = payload["job_id"]
    result = await db.execute(select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=YqpError(
                code=ErrorCode.JOB_NOT_FOUND,
                message=f"Job {job_id!r} not found or not assigned to this node",
            ).model_dump(),
        )

    if job.status != JobStatus.CLAIMED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=YqpError(
                code=ErrorCode.INVALID_STATE_TRANSITION,
                message=f"Cannot accept job in status {job.status}",
            ).model_dump(),
        )

    now = datetime.now(UTC)
    await transition(
        db,
        job,
        JobStatus.RUNNING,
        node_id=node.node_id,
        invocation_id=job.invocation_id,
    )
    job.started_at = now
    await db.commit()

    return {"job_id": job_id, "status": "accepted"}


async def handle_job_finished(
    db: AsyncSession,
    node: Node,
    payload: JsonObject,
    settings: Settings,
) -> JsonObject:
    """Process job.finished — Node reports job completion.

    Accepts terminal states: succeeded, failed, cancelled, timeout.
    A job can only enter a terminal state once.
    Writes timeline event for the terminal state.
    """
    from fastapi import HTTPException, status
    from sqlalchemy import select

    from yequ.models.job import Job
    from yequ.models.timeline import TimelineEvent
    from yequ.protocol import JobStatus
    from yequ.protocol.errors import ErrorCode, YqpError
    from yequ.services.job_state_machine import transition

    job_id = payload["job_id"]
    result = await db.execute(select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=YqpError(
                code=ErrorCode.JOB_NOT_FOUND,
                message=f"Job {job_id!r} not found",
            ).model_dump(),
        )

    terminal_status = payload["status"]
    valid_terminals = {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.TIMEOUT,
    }
    if terminal_status not in valid_terminals:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=YqpError(
                code=ErrorCode.SCHEMA_INVALID,
                message=f"Invalid terminal status: {terminal_status}",
            ).model_dump(),
        )

    # Reject if already in terminal state -- terminal state is immutable
    if job.status in valid_terminals:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=YqpError(
                code=ErrorCode.INVALID_STATE_TRANSITION,
                message=f"Job {job_id!r} already in terminal state {job.status}",
            ).model_dump(),
        )

    now = datetime.now(UTC)

    # Normalize error payload: node may send flat error_code/error_message
    # OR nested error.code / error.message / error.details.
    raw_error = payload.get("error")
    if isinstance(raw_error, dict):
        error_code = raw_error.get("code") or payload.get("error_code")
        error_message = raw_error.get("message") or payload.get("error_message")
        error_details = raw_error.get("details")
    else:
        error_code = payload.get("error_code")
        error_message = payload.get("error_message")
        error_details = None

    try:
        await transition(
            db,
            job,
            terminal_status,
            node_id=node.node_id,
            invocation_id=job.invocation_id,
            output=payload.get("output"),
            error_code=error_code,
            error_message=error_message,
            error_details=error_details,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=YqpError(
                code=ErrorCode.INVALID_STATE_TRANSITION,
                message=str(e),
            ).model_dump(),
        ) from None

    job.output = payload.get("output")
    job.error_code = error_code
    job.error_message = error_message
    job.error_details = error_details

    # Release resource locks on job completion
    from yequ.services.resource_lock_service import release_lock

    await release_lock(db, job_id)

    # L2 action timeline: write l2.action.completed or l2.action.failed
    # Skip if this job belongs to a MaintenanceStep (maintenance.step.* covers it)
    from yequ.models.maintenance_plan import MaintenanceStep
    from yequ.services.timeline_writer import add_timeline_event

    step_result = await db.execute(select(MaintenanceStep).where(MaintenanceStep.job_id == job_id))
    is_maintenance_step = step_result.scalar_one_or_none() is not None

    if job.approval_id and not is_maintenance_step:
        l2_status = "completed" if terminal_status == "succeeded" else "failed"
        l2_event = TimelineEvent(
            global_seq=0,
            event_type=f"l2.action.{l2_status}",
            actor_type="system",
            actor_id=node.node_id,
            node_id=node.node_id,
            job_id=job_id,
            invocation_id=job.invocation_id,
            data={
                "approval_id": job.approval_id,
                "function_name": job.function_name,
                "status": terminal_status,
                "output": payload.get("output"),
            },
            timestamp=now,
        )
        await add_timeline_event(db, l2_event)

    # Aggregate Invocation status — update Invocation when all Jobs terminal
    from yequ.models.invocation import Invocation
    from yequ.services.invocation_service import (
        aggregate_invocation_status,
        finish_invocation,
    )

    new_status = await aggregate_invocation_status(db, job.invocation_id)
    if new_status in ("succeeded", "failed", "timeout", "cancelled", "partial"):
        inv_result = await db.execute(
            select(Invocation).where(Invocation.invocation_id == job.invocation_id)
        )
        inv = inv_result.scalar_one_or_none()
        if inv and inv.status not in ("succeeded", "failed", "timeout", "cancelled", "partial"):
            finish_invocation(
                inv,
                new_status,
                error_code=job.error_code,
                error_message=job.error_message,
            )
            if job.output:
                inv.result = job.output
            if job.error_code:
                inv.error_code = job.error_code
            if job.error_message:
                inv.error_message = job.error_message
            if job.error_details:
                inv.error_details = job.error_details
            # Write invocation timeline event
            inv_event = TimelineEvent(
                global_seq=0,
                event_type=f"invocation.{new_status}",
                actor_type="system",
                actor_id=node.node_id,
                node_id=node.node_id,
                job_id=job_id,
                invocation_id=job.invocation_id,
                data={
                    "invocation_id": job.invocation_id,
                    "status": new_status,
                    "job_count": 1,
                },
                timestamp=now,
            )
            await add_timeline_event(db, inv_event)
            await db.commit()

    await db.commit()

    return {"job_id": job_id, "status": terminal_status}


async def handle_job_lease_renew(
    db: AsyncSession,
    node: Node,
    payload: JsonObject,
    settings: Settings,
) -> JsonObject:
    """Process job.lease_renew — extend a running job's lease.

    Only running jobs can renew their lease. Expired leases are denied.
    Returns either lease_accepted or lease_denied semantics.
    """
    from fastapi import HTTPException, status
    from sqlalchemy import select

    from yequ.models.job import Job
    from yequ.protocol import JobStatus
    from yequ.protocol.errors import ErrorCode, YqpError

    job_id = payload["job_id"]
    result = await db.execute(select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=YqpError(
                code=ErrorCode.JOB_NOT_FOUND,
                message=f"Job {job_id!r} not found",
            ).model_dump(),
        )

    if job.status != JobStatus.RUNNING:
        return {
            "job_id": job_id,
            "status": "denied",
            "reason": "job_not_running",
        }

    now = datetime.now(UTC)
    lease_expires = job.lease_expires_at
    if lease_expires is not None and lease_expires.tzinfo is None:
        lease_expires = lease_expires.replace(tzinfo=UTC)
    if lease_expires is not None and lease_expires < now:
        return {
            "job_id": job_id,
            "status": "denied",
            "reason": "lease_expired",
        }

    extend_sec = payload.get("lease_extend_sec", settings.default_lease_sec)
    job.lease_expires_at = datetime.fromtimestamp(now.timestamp() + extend_sec, tz=UTC)
    await db.commit()

    return {
        "job_id": job_id,
        "status": "accepted",
        "lease_expires_at": job.lease_expires_at.isoformat(),
    }


async def _reconcile_to_terminal(
    db: AsyncSession,
    job: Job,
    *,
    terminal_status: str,
    node_id: str,
    output: JsonObject | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Move a daemon-reported terminal reconciliation through legal transitions."""
    from yequ.protocol import JobStatus
    from yequ.services.job_state_machine import transition

    if job.status == JobStatus.CREATED:
        await transition(db, job, JobStatus.QUEUED, node_id=node_id)

    if job.status == JobStatus.QUEUED:
        await transition(db, job, JobStatus.CLAIMED, node_id=node_id)

    if job.status == JobStatus.CLAIMED and terminal_status != JobStatus.TIMEOUT:
        await transition(db, job, JobStatus.RUNNING, node_id=node_id)

    if terminal_status == JobStatus.CANCELLED and job.status == JobStatus.RUNNING:
        await transition(db, job, JobStatus.CANCELLING, node_id=node_id)

    await transition(
        db,
        job,
        terminal_status,
        node_id=node_id,
        output=output,
        error_code=error_code,
        error_message=error_message,
    )


async def handle_reconcile_jobs(
    db: AsyncSession,
    node: Node,
    payload: JsonObject,
    settings: Settings,
) -> JsonObject:
    """Process node.reconcile_jobs — reconcile after reconnection.

    Compares Daemon's local job states with Center's authoritative state
    and returns reconciliation actions per the YQP arbitration rules:

    - Center terminal, Daemon succeeded/cancelled/failed/timeout -> accept/discard result
    - Center terminal, Daemon running -> cancel
    - Both running -> continue with new lease
    - Center unknown -> forget
    - Daemon completed, Center not terminal -> accept_result
    """
    from sqlalchemy import select

    from yequ.models.job import Job
    from yequ.protocol import JobStatus, ReconciliationAction
    from yequ.protocol.errors import ErrorCode, YqpError

    known_jobs = payload.get("known_jobs", [])
    actions: list[JsonObject] = []

    terminal_statuses = {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.TIMEOUT,
    }
    non_terminal_statuses = {
        JobStatus.CREATED,
        JobStatus.QUEUED,
        JobStatus.CLAIMED,
        JobStatus.RUNNING,
    }
    daemon_terminal = {"succeeded", "failed", "cancelled", "timeout"}

    for kj in known_jobs:
        job_id = kj["job_id"]
        local_status = kj.get("local_status", "running")

        # Look up job in Center
        result = await db.execute(
            select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id)
        )
        job = result.scalar_one_or_none()

        if job is None:
            # Center doesn't know this job - Daemon should stop and clean up
            actions.append(
                {
                    "job_id": job_id,
                    "action": ReconciliationAction.FORGET,
                }
            )
            continue

        center_status = job.status

        # Rule 1: Center has terminal state, Daemon completed.
        # Center is authoritative once a Job reaches terminal state; late daemon results are
        # intentionally discarded to preserve terminal immutability and avoid result drift.
        if center_status in terminal_statuses and local_status in daemon_terminal:
            actions.append(
                {
                    "job_id": job_id,
                    "action": ReconciliationAction.DISCARD_RESULT,
                    "reason": f"center_already_{center_status}",
                    "center_status": center_status,
                    "local_status": local_status,
                }
            )
            continue

        # Rule 2: Center has terminal state, Daemon is still running
        if center_status in terminal_statuses:
            actions.append(
                {
                    "job_id": job_id,
                    "action": ReconciliationAction.CANCEL,
                    "reason": f"already_{center_status}",
                }
            )
            continue

        # Rule 3: Center is non-terminal (running/claimed/queued/created),
        # Daemon completed — accept the result and sync Center state
        if center_status in non_terminal_statuses and local_status in daemon_terminal:
            try:
                await _reconcile_to_terminal(
                    db,
                    job,
                    terminal_status=local_status,
                    node_id=node.node_id,
                    output=kj.get("output"),
                    error_code=kj.get("error_code"),
                    error_message=kj.get("error_message"),
                )
            except ValueError as e:
                from fastapi import HTTPException, status

                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=YqpError(
                        code=ErrorCode.INVALID_STATE_TRANSITION,
                        message=str(e),
                    ).model_dump(),
                ) from None
            await db.commit()

            actions.append(
                {
                    "job_id": job_id,
                    "action": ReconciliationAction.ACCEPT_RESULT,
                    "reconciled": True,
                }
            )
            continue

        # Rule 4: Both agree job is running — continue with new lease
        if center_status in non_terminal_statuses and local_status == "running":
            actions.append(
                {
                    "job_id": job_id,
                    "action": ReconciliationAction.CONTINUE,
                    "lease_sec": settings.default_lease_sec,
                }
            )
            continue

        # Default: continue
        actions.append(
            {
                "job_id": job_id,
                "action": ReconciliationAction.CONTINUE,
                "lease_sec": settings.default_lease_sec,
            }
        )

    return {"actions": actions}


async def handle_job_event(
    db: AsyncSession,
    node: Node,
    payload: JsonObject,
    settings: Settings,
) -> JsonObject:
    """Process job.event — Daemon reports a progress/log/cancelling event.

    Standard event types: job.started, job.progress, job.log,
    job.cancelling, job.cancelled, job.timeout.

    These are recorded as TimelineEvents only — no state change
    (state changes happen via job.accepted/job.finished).
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from yequ.models.timeline import TimelineEvent
    from yequ.services.timeline_writer import add_timeline_event

    job_id = payload["job_id"]
    event_type = payload["event_type"]
    sequence = payload.get("sequence", 1)
    data = payload.get("data", {})

    # Look up job to get invocation_id for timeline tracing
    from yequ.models.job import Job

    j_result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = j_result.scalar_one_or_none()
    invocation_id = job.invocation_id if job else None

    now = datetime.now(UTC)

    event = TimelineEvent(
        global_seq=0,
        event_type=event_type,
        actor_type="system",
        actor_id=node.node_id,
        node_id=node.node_id,
        job_id=job_id,
        invocation_id=invocation_id,
        data={
            "event_type": event_type,
            "sequence": sequence,
            **data,
        },
        sequence=sequence,
        timestamp=now,
    )
    await add_timeline_event(db, event)
    await db.commit()

    return {"job_id": job_id, "event_type": event_type, "sequence": sequence}


async def handle_job_cancel(
    db: AsyncSession,
    node: Node,
    payload: JsonObject,
    settings: Settings,
) -> JsonObject:
    """Process job.cancel — Center requests the Node to cancel a running job.

    Center initiates a cancel. The Node should:
    1. Stop the job and report job.cancelling via job.event
    2. Report job.cancelled via job.finished when stopped
    """
    from fastapi import HTTPException, status
    from sqlalchemy import select

    from yequ.models.job import Job
    from yequ.protocol.errors import ErrorCode, YqpError
    from yequ.services.job_service import cancel_job

    job_id = payload["job_id"]
    result = await db.execute(select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id))
    job = result.scalar_one_or_none()

    if job is None:
        # Center might cancel a job the node hasn't seen yet
        # Look up by job_id only
        result = await db.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one_or_none()

        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=YqpError(
                    code=ErrorCode.JOB_NOT_FOUND,
                    message=f"Job {job_id!r} not found",
                ).model_dump(),
            )

    reason = payload.get("reason", "user_requested")

    try:
        await cancel_job(db, job, reason=reason, node_id=node.node_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=YqpError(
                code=ErrorCode.INVALID_STATE_TRANSITION,
                message=str(e),
            ).model_dump(),
        ) from None

    await db.commit()

    return {
        "job_id": job_id,
        "status": job.status,
        "reason": reason,
    }
