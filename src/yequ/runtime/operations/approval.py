"""Approval wait Operation projection handler."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.approval import ApprovalRequest
from yequ.models.operation import Operation


class ApprovalOperationHandler:
    """Project an ApprovalRequest into the Operation shell."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def project(self, operation: Operation) -> dict[str, object]:
        approval = await self._get_approval(operation.ref_id)
        self.sync_operation(operation, approval)
        return {"approval": approval_dict(approval)}

    async def cancel(self, operation: Operation, *, reason: str) -> dict[str, object]:
        approval = await self._get_approval(operation.ref_id)
        if approval.status == "pending":
            approval.status = "denied"
            approval.denied_by = "operation"
            approval.decision_reason = reason
            approval.updated_at = datetime.now(UTC)
        self.sync_operation(operation, approval)
        return {"approval": approval_dict(approval)}

    async def _get_approval(self, approval_id: str) -> ApprovalRequest:
        result = await self.db.execute(
            select(ApprovalRequest).where(ApprovalRequest.approval_id == approval_id)
        )
        approval = result.scalar_one_or_none()
        if approval is None:
            raise ValueError(f"ApprovalRequest {approval_id!r} not found")
        return approval

    @staticmethod
    def sync_operation(operation: Operation, approval: ApprovalRequest) -> None:
        operation.status = operation_status_from_approval(approval.status)
        operation.output_data = {"approval": approval_dict(approval)}
        if operation.status in {"succeeded", "failed", "cancelled", "timeout"}:
            operation.completed_at = operation.completed_at or datetime.now(UTC)


def operation_status_from_approval(status: str) -> str:
    if status == "pending":
        return "running"
    if status in {"approved", "consumed"}:
        return "succeeded"
    if status == "expired":
        return "timeout"
    if status == "denied":
        return "cancelled"
    return "failed"


def approval_title(approval: ApprovalRequest) -> str:
    return f"Approval {approval.function_name} @ {approval.target_node_id}"


def approval_dict(approval: ApprovalRequest) -> dict[str, object]:
    return {
        "approval_id": approval.approval_id,
        "status": approval.status,
        "actor_id": approval.actor_id,
        "session_id": approval.session_id,
        "function_name": approval.function_name,
        "target_node_id": approval.target_node_id,
        "risk": approval.risk,
        "effect": approval.effect,
        "resource_keys": approval.resource_keys or [],
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
        "updated_at": approval.updated_at.isoformat() if approval.updated_at else None,
        "consumed_at": approval.consumed_at.isoformat() if approval.consumed_at else None,
        "consumed_invocation_id": approval.consumed_invocation_id,
    }
