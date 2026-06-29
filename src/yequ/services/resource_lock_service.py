"""Resource lock service — prevents concurrent conflicting writes."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.job import Job
from yequ.models.resource_lock import ResourceLock
from yequ.models.timeline import TimelineEvent
from yequ.protocol import JobStatus, LockStatus
from yequ.services.timeline_writer import add_timeline_event
from yequ.shared_types import JsonObject

TERMINAL_JOB_VALUES = {status.value for status in JobStatus if status in {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
    JobStatus.TIMEOUT,
}}


def _make_lock_id() -> str:
    return f"lock_{uuid.uuid4().hex[:16]}"


async def acquire_lock(
    db: AsyncSession,
    resource_key: str,
    job_id: str,
    invocation_id: str,
    node_id: str,
    ttl_minutes: int = 5,
) -> ResourceLock:
    """Acquire a lock on a resource_key.

    Returns the lock if acquired. Raises ValueError if the resource
    is already locked by another active job.
    """
    # Check for existing held locks. Stale locks whose owner job is already
    # terminal are released here so one historical bad state cannot block a
    # serialized resource forever.
    result = await db.execute(
        select(ResourceLock).where(
            ResourceLock.resource_key == resource_key,
            ResourceLock.status == LockStatus.HELD,
        )
    )
    existing_locks = list(result.scalars().all())
    for existing in existing_locks:
        owner_result = await db.execute(select(Job).where(Job.job_id == existing.job_id))
        owner = owner_result.scalar_one_or_none()
        if owner is not None and str(owner.status) in TERMINAL_JOB_VALUES:
            await _release_one_lock(db, existing, reason="stale_owner_terminal")
            continue

        # Write lock conflict timeline event before raising
        conflict_event = TimelineEvent(
            global_seq=0,
            event_type="resource.lock.conflict",
            actor_type="system",
            actor_id="resource_lock",
            node_id=node_id,
            job_id=job_id,
            invocation_id=invocation_id,
            data={
                "resource_key": resource_key,
                "existing_job_id": existing.job_id,
                "existing_lock_id": existing.lock_id,
            },
            timestamp=datetime.now(UTC),
        )
        await add_timeline_event(db, conflict_event)
        raise ValueError(f"Resource {resource_key!r} is locked by job {existing.job_id!r}")

    now = datetime.now(UTC)
    lock = ResourceLock(
        lock_id=_make_lock_id(),
        resource_key=resource_key,
        job_id=job_id,
        invocation_id=invocation_id,
        node_id=node_id,
        status=LockStatus.HELD,
        expires_at=now + timedelta(minutes=ttl_minutes),
        created_at=now,
    )
    db.add(lock)
    await db.flush()

    event = TimelineEvent(
        global_seq=0,
        event_type="resource.lock.acquired",
        actor_type="system",
        actor_id="resource_lock",
        node_id=node_id,
        job_id=job_id,
        invocation_id=invocation_id,
        data={"resource_key": resource_key, "lock_id": lock.lock_id},
        timestamp=datetime.now(UTC),
    )
    await add_timeline_event(db, event)
    return lock


async def _release_one_lock(
    db: AsyncSession,
    lock: ResourceLock,
    *,
    reason: str = "released",
) -> None:
    lock.status = LockStatus.RELEASED
    lock.released_at = datetime.now(UTC)
    event = TimelineEvent(
        global_seq=0,
        event_type="resource.lock.released",
        actor_type="system",
        actor_id="resource_lock",
        node_id=lock.node_id,
        job_id=lock.job_id,
        invocation_id=lock.invocation_id,
        data={"resource_key": lock.resource_key, "lock_id": lock.lock_id, "reason": reason},
        timestamp=lock.released_at,
    )
    await add_timeline_event(db, event)


async def release_lock(
    db: AsyncSession,
    job_id: str,
) -> list[ResourceLock]:
    """Release all locks held by a job."""
    result = await db.execute(
        select(ResourceLock).where(
            ResourceLock.job_id == job_id,
            ResourceLock.status == LockStatus.HELD,
        )
    )
    locks = list(result.scalars().all())
    for lock in locks:
        await _release_one_lock(db, lock)
    return locks


def compute_resource_keys(
    function_name: str,
    node_id: str,
    input_data: JsonObject,
    resource_key_template: str | None = None,
) -> list[str]:
    """Compute resource keys for a function call.

    Uses template if provided, else falls back to rules.

    Template substitutions: {node_id}, {name}, {pid}, {task_name}, any input key.

    Rules:
    - service.* -> node:{node_id}:service:{name}
    - process.* -> node:{node_id}:process:{pid}
    - task.* -> node:{node_id}:task:{task_name}
    - network.dns.* -> node:{node_id}:maintenance:dns
    - temp.cleanup -> node:{node_id}:maintenance:temp:{path_hash}
    """
    if resource_key_template:
        rendered = resource_key_template.replace("{node_id}", node_id)
        for key, val in input_data.items():
            rendered = rendered.replace(f"{{{key}}}", str(val))
        return [rendered]

    keys: list[str] = []
    if "service" in function_name:
        name = input_data.get("name", "")
        if name:
            keys.append(f"node:{node_id}:service:{name}")
    elif "process" in function_name:
        pid = input_data.get("pid", "")
        if pid:
            keys.append(f"node:{node_id}:process:{pid}")
    elif "task" in function_name:
        task_name = input_data.get("task_name", input_data.get("name", ""))
        if task_name:
            keys.append(f"node:{node_id}:task:{task_name}")
    elif "network.dns" in function_name:
        keys.append(f"node:{node_id}:maintenance:dns")
    elif "temp.cleanup" in function_name:
        import hashlib

        path = str(input_data.get("path", ""))
        path_hash = hashlib.md5(path.encode()).hexdigest()[:8]
        keys.append(f"node:{node_id}:maintenance:temp:{path_hash}")
    return keys
