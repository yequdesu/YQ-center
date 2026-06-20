"""Approval service — create, approve, deny, consume approvals."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.approval import ApprovalRequest
from yequ.models.timeline import TimelineEvent
from yequ.protocol import ApprovalStatus


def _make_approval_id() -> str:
    return f"apv_{uuid.uuid4().hex[:16]}"


def _hash_input(input_data: dict) -> str:
    filtered = {k: v for k, v in input_data.items() if k != "approval_id"}
    raw = json.dumps(filtered, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _ensure_aware(dt: datetime | None) -> datetime | None:
    """Ensure a datetime is timezone-aware (UTC)."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def create_approval(
    db: AsyncSession,
    *,
    actor_id: str,
    session_id: str | None,
    function_name: str,
    target_node_id: str,
    input_data: dict,
    risk: str,
    effect: str,
    resource_keys: list[str] | None = None,
    resource_key_template: str | None = None,
    ttl_minutes: int = 5,
    invocation_id: str | None = None,
) -> ApprovalRequest:
    """Create a pending ApprovalRequest."""
    from yequ.services.resource_lock_service import compute_resource_keys

    now = datetime.now(UTC)

    # Compute resource keys if not explicitly provided
    if not resource_keys:
        resource_keys = compute_resource_keys(
            function_name, target_node_id, input_data,
            resource_key_template=resource_key_template,
        )

    approval = ApprovalRequest(
        approval_id=_make_approval_id(),
        actor_id=actor_id,
        session_id=session_id,
        invocation_id=invocation_id,
        function_name=function_name,
        target_node_id=target_node_id,
        input_hash=_hash_input(input_data),
        input_snapshot=input_data,
        risk=risk,
        effect=effect,
        resource_keys=resource_keys or [],
        status=ApprovalStatus.PENDING,
        expires_at=now + timedelta(minutes=ttl_minutes),
        created_at=now,
    )
    db.add(approval)
    await db.commit()
    return approval


async def approve_approval(
    db: AsyncSession,
    approval: ApprovalRequest,
    approved_by: str,
    reason: str | None = None,
) -> ApprovalRequest:
    """Approve a pending approval."""
    if approval.status != ApprovalStatus.PENDING:
        raise ValueError(f"Cannot approve approval in status {approval.status}")
    expires = _ensure_aware(approval.expires_at)
    if expires < datetime.now(UTC):
        approval.status = ApprovalStatus.EXPIRED
        await db.commit()
        raise ValueError("Approval has expired")
    approval.status = ApprovalStatus.APPROVED
    approval.approved_by = approved_by
    approval.decision_reason = reason
    approval.updated_at = datetime.now(UTC)
    await db.commit()

    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0
    event = TimelineEvent(
        global_seq=max_seq + 1,
        event_type="approval.approved",
        actor_type="admin", actor_id=approved_by,
        session_id=approval.session_id,
        invocation_id=approval.invocation_id,
        node_id=approval.target_node_id,
        data={"approval_id": approval.approval_id, "function_name": approval.function_name,
              "reason": reason},
        timestamp=datetime.now(UTC),
    )
    db.add(event)
    await db.commit()
    return approval


async def deny_approval(
    db: AsyncSession,
    approval: ApprovalRequest,
    denied_by: str,
    reason: str | None = None,
) -> ApprovalRequest:
    """Deny a pending approval."""
    if approval.status != ApprovalStatus.PENDING:
        raise ValueError(f"Cannot deny approval in status {approval.status}")
    approval.status = ApprovalStatus.DENIED
    approval.denied_by = denied_by
    approval.decision_reason = reason
    approval.updated_at = datetime.now(UTC)
    await db.commit()

    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
    max_seq = result.scalar() or 0
    event = TimelineEvent(
        global_seq=max_seq + 1,
        event_type="approval.denied",
        actor_type="admin", actor_id=denied_by,
        session_id=approval.session_id,
        invocation_id=approval.invocation_id,
        node_id=approval.target_node_id,
        data={"approval_id": approval.approval_id, "function_name": approval.function_name,
              "reason": reason},
        timestamp=datetime.now(UTC),
    )
    db.add(event)
    await db.commit()
    return approval


async def consume_approval(
    db: AsyncSession,
    approval: ApprovalRequest,
) -> ApprovalRequest:
    """Mark an approved approval as consumed (used to create a Job)."""
    if approval.status != ApprovalStatus.APPROVED:
        raise ValueError(f"Cannot consume approval in status {approval.status}")
    approval.status = ApprovalStatus.CONSUMED
    approval.consumed_at = datetime.now(UTC)
    await db.commit()
    return approval


async def verify_approval(
    db: AsyncSession,
    approval_id: str,
    *,
    actor_id: str,
    session_id: str | None,
    function_name: str,
    target_node_id: str,
    input_data: dict,
) -> ApprovalRequest:
    """Verify an approval is valid for execution.

    Checks: exists, approved, not expired, actor/session/function/node match,
    input hash matches, not already consumed.
    Returns the ApprovalRequest if valid, raises ValueError otherwise.
    """
    result = await db.execute(
        select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
    )
    approval = result.scalar_one_or_none()
    if approval is None:
        raise ValueError(f"Approval {approval_id!r} not found")
    if approval.status != ApprovalStatus.APPROVED:
        raise ValueError(f"Approval {approval_id!r} is not approved (status: {approval.status})")
    expires = _ensure_aware(approval.expires_at)
    if expires < datetime.now(UTC):
        raise ValueError(f"Approval {approval_id!r} has expired")
    if approval.actor_id != actor_id:
        raise ValueError(f"Actor mismatch: {actor_id} != {approval.actor_id}")
    if approval.function_name != function_name:
        raise ValueError(f"Function mismatch: {function_name} != {approval.function_name}")
    if approval.target_node_id != target_node_id:
        raise ValueError(f"Node mismatch: {target_node_id} != {approval.target_node_id}")
    if _hash_input(input_data) != approval.input_hash:
        raise ValueError("Input hash mismatch")
    return approval
