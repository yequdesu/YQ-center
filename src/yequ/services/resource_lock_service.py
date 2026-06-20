"""Resource lock service — prevents concurrent conflicting writes."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.resource_lock import ResourceLock
from yequ.models.timeline import TimelineEvent
from yequ.protocol import LockStatus


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
    # Check for existing held locks
    result = await db.execute(
        select(ResourceLock).where(
            ResourceLock.resource_key == resource_key,
            ResourceLock.status == LockStatus.HELD,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise ValueError(
            f"Resource {resource_key!r} is locked by job {existing.job_id!r}"
        )

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

    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0
    event = TimelineEvent(
        global_seq=max_seq + 1,
        event_type="resource.lock.acquired",
        actor_type="system", actor_id="resource_lock",
        node_id=node_id, job_id=job_id, invocation_id=invocation_id,
        data={"resource_key": resource_key, "lock_id": lock.lock_id},
        timestamp=datetime.now(UTC),
    )
    db.add(event)
    await db.flush()
    return lock


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
    locks = result.scalars().all()
    now = datetime.now(UTC)
    for lock in locks:
        lock.status = LockStatus.RELEASED
        lock.released_at = now
        result = await db.execute(select(func.max(TimelineEvent.global_seq)))
        max_seq = result.scalar() or 0
        event = TimelineEvent(
            global_seq=max_seq + 1,
            event_type="resource.lock.released",
            actor_type="system", actor_id="resource_lock",
            node_id=lock.node_id, job_id=lock.job_id, invocation_id=lock.invocation_id,
            data={"resource_key": lock.resource_key, "lock_id": lock.lock_id},
            timestamp=datetime.now(UTC),
        )
        db.add(event)
    return locks


def compute_resource_keys(
    function_name: str,
    node_id: str,
    input_data: dict,
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
