"""Node service — business logic for YQP node protocol messages."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.capability import Capability
from yequ.models.node import Node
from yequ.models.runtime_instance import RuntimeInstance
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
    await _sync_runtime_instances(db, node, payload.get("runtimes") or [], now=node.last_seen_at)

    # Write node.online timeline event
    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0
    db.add(TimelineEvent(
        global_seq=max_seq + 1,
        event_type="node.online",
        actor_type="system",
        actor_id="node_service",
        node_id=node.node_id,
        data={"node_id": node.node_id, "daemon_version": node.daemon_version},
        timestamp=datetime.now(UTC),
    ))

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
    await _sync_runtime_instances(db, node, payload.get("runtimes") or [], now=now)

    # If node was offline or rejoining, bring back to online
    if node.status in (NodeStatus.OFFLINE, NodeStatus.REJOINING):
        node.status = NodeStatus.ONLINE

        # Write node.online timeline event on recovery
        result = await db.execute(select(func.max(TimelineEvent.global_seq)))
        max_seq = result.scalar() or 0
        db.add(TimelineEvent(
            global_seq=max_seq + 1,
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
        ))

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


def _legacy_runtime_id(node: Node) -> str:
    """Default runtime for legacy Nodes that do not yet report runtimes."""
    return f"{node.node_id}/runtime/default"


def _execution_requirements_from_context(context: str | None) -> dict | None:
    """Map deprecated execution_context to platform-neutral requirements."""
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
    runtimes: list[dict],
    *,
    now: datetime,
) -> None:
    """Synchronize the runtime snapshot reported by a Node.

    Center stores only platform-neutral runtime attributes. OS-specific
    worker implementation details stay inside the Node and may appear only as
    opaque metadata.
    """
    if not runtimes:
        runtimes = [{
            "runtime_id": _legacy_runtime_id(node),
            "kind": "privileged",
            "status": "online",
            "labels": ["legacy"],
            "interactive": False,
            "metadata": {"source": "legacy_default"},
        }]

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

    from yequ.services.timeline_writer import get_timeline_writer

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
    registered: dict[str, dict] = {}
    for cap in result.scalars().all():
        if cap.value_schema:
            registered[cap.name] = cap.value_schema

    # Get the async timeline writer for accepted events
    tl_writer = get_timeline_writer()

    for sig in signals:
        name = sig["name"]
        value = sig.get("value")
        value_schema = registered.get(name)

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
                db.add(event)
                rejected += 1
                continue

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
                "ttl_sec": sig.get("ttl_sec"),
            },
            timestamp=now,
        )
        tl_writer.enqueue(event)
        accepted += 1

    # Commit any schema_invalid events that were written synchronously
    if rejected:
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

    Returns up to `capacity` jobs that are queued for this node,
    accounting for currently running jobs on the node.

    available_slots = max(payload.capacity - len(payload.running_jobs), 0)
    Jobs transition from queued to claimed upon poll.
    If no jobs available, returns empty jobs list (job.empty semantics).
    """
    from sqlalchemy import select

    from yequ.models.job import Job
    from yequ.protocol import JobStatus

    raw_capacity = int(payload.get("capacity", 1))
    running_jobs = payload.get("running_jobs") or []
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
            "runtime_id": job.runtime_id,
            "execution_requirements": job.execution_requirements_snapshot or {},
            "timeout_sec": job.timeout_sec,
            "lease_sec": job.lease_sec,
            "approval_id": getattr(job, "approval_id", None),
            "resource_keys": getattr(job, "resource_keys", []),
            "dry_run": getattr(job, "dry_run", False),
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

    # Normalize error payload: node may send flat error_code/error_message
    # OR nested error.code / error.message / error.details.
    raw_error = payload.get("error")
    if isinstance(raw_error, dict):
        job.error_code = raw_error.get("code") or payload.get("error_code")
        job.error_message = raw_error.get("message") or payload.get("error_message")
        job.error_details = raw_error.get("details")
    else:
        job.error_code = payload.get("error_code")
        job.error_message = payload.get("error_message")
        job.error_details = None

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
            "error_code": job.error_code,
            "error_message": job.error_message,
            "finished_at": now.isoformat(),
        },
        timestamp=now,
    )
    db.add(event)
    await db.commit()

    # Release resource locks on job completion
    from yequ.services.resource_lock_service import release_lock
    await release_lock(db, job_id)

    # L2 action timeline: write l2.action.completed or l2.action.failed
    # Skip if this job belongs to a MaintenanceStep (maintenance.step.* covers it)
    from yequ.models.maintenance_plan import MaintenanceStep
    step_result = await db.execute(
        select(MaintenanceStep).where(MaintenanceStep.job_id == job_id)
    )
    is_maintenance_step = step_result.scalar_one_or_none() is not None

    if job.approval_id and not is_maintenance_step:
        l2_status = "completed" if terminal_status == "succeeded" else "failed"
        l2_seq = await db.execute(select(func.max(TimelineEvent.global_seq)))
        l2_max = l2_seq.scalar() or 0
        l2_event = TimelineEvent(
            global_seq=l2_max + 1,
            event_type=f"l2.action.{l2_status}",
            actor_type="system", actor_id=node.node_id,
            node_id=node.node_id, job_id=job_id,
            invocation_id=job.invocation_id,
            data={
                "approval_id": job.approval_id,
                "function_name": job.function_name,
                "status": terminal_status,
                "output": payload.get("output"),
            },
            timestamp=now,
        )
        db.add(l2_event)
        await db.flush()

    # Aggregate Invocation status — update Invocation when all Jobs terminal
    from yequ.models.invocation import Invocation
    from yequ.services.invocation_service import (
        aggregate_invocation_status,
        finish_invocation,
    )

    new_status = await aggregate_invocation_status(db, job.invocation_id)
    if new_status in ("succeeded", "failed", "timeout", "cancelled", "partial"):
        inv_result = await db.execute(
            select(Invocation).where(
                Invocation.invocation_id == job.invocation_id
            )
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
            iev_result = await db.execute(select(func.max(TimelineEvent.global_seq)))
            iev_max = iev_result.scalar() or 0
            inv_event = TimelineEvent(
                global_seq=iev_max + 1,
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
            db.add(inv_event)
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


async def handle_job_event(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
    """Process job.event — Daemon reports a progress/log/cancelling event.

    Standard event types: job.started, job.progress, job.log,
    job.cancelling, job.cancelled, job.timeout.

    These are recorded as TimelineEvents only — no state change
    (state changes happen via job.accepted/job.finished).
    """
    from datetime import UTC, datetime

    from sqlalchemy import func, select

    from yequ.models.timeline import TimelineEvent

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

    # Compute next global_seq
    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0
    next_seq = max_seq + 1

    event = TimelineEvent(
        global_seq=next_seq,
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
    db.add(event)
    await db.commit()

    return {"job_id": job_id, "event_type": event_type, "sequence": sequence}


async def handle_job_cancel(
    db: AsyncSession,
    node: Node,
    payload: dict,
    settings,
) -> dict:
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
    result = await db.execute(
        select(Job).where(Job.job_id == job_id, Job.node_id == node.node_id)
    )
    job = result.scalar_one_or_none()

    if job is None:
        # Center might cancel a job the node hasn't seen yet
        # Look up by job_id only
        result = await db.execute(
            select(Job).where(Job.job_id == job_id)
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
