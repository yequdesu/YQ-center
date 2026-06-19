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


async def handle_job_poll(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process job.poll — return available jobs for this node.

    Returns up to `capacity` jobs that are queued for this node.
    Jobs transition from queued to claimed upon poll.
    If no jobs available, returns empty jobs list (job.empty semantics).
    """
    from sqlalchemy import select

    from yequ.models.job import Job
    from yequ.protocol import JobStatus

    capacity = payload.get("capacity", 1)

    result = await db.execute(
        select(Job)
        .where(
            Job.node_id == node.node_id,
            Job.status == JobStatus.QUEUED,
        )
        .limit(capacity)
    )
    pending_jobs = result.scalars().all()

    if not pending_jobs:
        return {"jobs": []}

    now = datetime.now(UTC)
    jobs = []
    for job in pending_jobs:
        job.status = JobStatus.CLAIMED
        job.claimed_at = now
        job.lease_expires_at = datetime.fromtimestamp(
            now.timestamp() + job.lease_sec, tz=UTC
        )
        jobs.append({
            "job_id": job.job_id,
            "invocation_id": job.invocation_id,
            "function": job.function_name,
            "input": job.input_payload or {},
            "timeout_sec": job.timeout_sec,
            "lease_sec": job.lease_sec,
        })

    await db.commit()
    return {"jobs": jobs}


async def handle_job_accepted(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process job.accepted — Node confirms it will execute the job.

    Job transitions: claimed -> running.
    """
    from fastapi import HTTPException, status
    from sqlalchemy import select

    from yequ.models.job import Job
    from yequ.protocol import JobStatus
    from yequ.protocol.errors import ErrorCode, YqpError

    job_id = payload["job_id"]
    result = await db.execute(
        select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id)
    )
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
    job.status = JobStatus.RUNNING
    job.started_at = now
    await db.commit()

    return {"job_id": job_id, "status": "accepted"}


async def handle_job_finished(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
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

    job_id = payload["job_id"]
    result = await db.execute(
        select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id)
    )
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
        JobStatus.SUCCEEDED, JobStatus.FAILED,
        JobStatus.CANCELLED, JobStatus.TIMEOUT,
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
    job.status = terminal_status
    job.finished_at = now
    job.output = payload.get("output")
    job.error_code = payload.get("error_code")
    job.error_message = payload.get("error_message")

    # Compute global_seq for timeline event
    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0
    next_seq = max_seq + 1

    # Write timeline event
    event = TimelineEvent(
        global_seq=next_seq,
        event_type=f"job.{terminal_status}",
        actor_type="system",
        actor_id=node.node_id,
        node_id=node.node_id,
        job_id=job_id,
        invocation_id=job.invocation_id,
        data={
            "status": terminal_status,
            "output": payload.get("output"),
            "error_code": payload.get("error_code"),
            "finished_at": now.isoformat(),
        },
        timestamp=now,
    )
    db.add(event)
    await db.commit()

    return {"job_id": job_id, "status": terminal_status}


async def handle_job_lease_renew(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
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
    result = await db.execute(
        select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id)
    )
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
    if job.lease_expires_at and job.lease_expires_at < now:
        return {
            "job_id": job_id,
            "status": "denied",
            "reason": "lease_expired",
        }

    extend_sec = payload.get("lease_extend_sec", settings.default_lease_sec)
    job.lease_expires_at = datetime.fromtimestamp(
        now.timestamp() + extend_sec, tz=UTC
    )
    await db.commit()

    return {
        "job_id": job_id,
        "status": "accepted",
        "lease_expires_at": job.lease_expires_at.isoformat(),
    }


async def handle_reconcile_jobs(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
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

    known_jobs = payload.get("known_jobs", [])
    actions: list[dict] = []
    now = datetime.now(UTC)

    terminal_statuses = {
        JobStatus.SUCCEEDED, JobStatus.FAILED,
        JobStatus.CANCELLED, JobStatus.TIMEOUT,
    }
    non_terminal_statuses = {
        JobStatus.CREATED, JobStatus.QUEUED,
        JobStatus.CLAIMED, JobStatus.RUNNING,
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
            actions.append({
                "job_id": job_id,
                "action": ReconciliationAction.FORGET,
            })
            continue

        center_status = job.status

        # Rule 1: Center has terminal state, Daemon completed
        if center_status in terminal_statuses and local_status in daemon_terminal:
            if "output" in kj:
                actions.append({
                    "job_id": job_id,
                    "action": ReconciliationAction.ACCEPT_RESULT,
                    "reconciled": True,
                })
            else:
                actions.append({
                    "job_id": job_id,
                    "action": ReconciliationAction.DISCARD_RESULT,
                })
            continue

        # Rule 2: Center has terminal state, Daemon is still running
        if center_status in terminal_statuses:
            actions.append({
                "job_id": job_id,
                "action": ReconciliationAction.CANCEL,
                "reason": f"already_{center_status}",
            })
            continue

        # Rule 3: Center is non-terminal (running/claimed/queued/created),
        # Daemon completed — accept the result
        if center_status in non_terminal_statuses and local_status in daemon_terminal:
            if local_status == "succeeded" and "output" in kj:
                job.status = JobStatus.SUCCEEDED
                job.finished_at = now
                job.output = kj.get("output")
                await db.commit()

            actions.append({
                "job_id": job_id,
                "action": ReconciliationAction.ACCEPT_RESULT,
                "reconciled": True,
            })
            continue

        # Rule 4: Both agree job is running — continue with new lease
        if center_status in non_terminal_statuses and local_status == "running":
            actions.append({
                "job_id": job_id,
                "action": ReconciliationAction.CONTINUE,
                "lease_sec": settings.default_lease_sec,
            })
            continue

        # Default: continue
        actions.append({
            "job_id": job_id,
            "action": ReconciliationAction.CONTINUE,
            "lease_sec": settings.default_lease_sec,
        })

    return {"actions": actions}
