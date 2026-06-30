"""Operation Bus service for waitable Center runtime work."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.operation import Operation, OperationEvent
from yequ.models.transfer import TransferSession

TERMINAL_OPERATION_STATUSES = {"succeeded", "failed", "cancelled", "timeout"}


class OperationService:
    """Manage Operation shells and projections over domain records."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_for_transfer(
        self,
        transfer: dict[str, object] | TransferSession,
        *,
        actor_type: str,
        actor_id: str | None,
        session_id: str | None,
    ) -> dict[str, object]:
        transfer_id = (
            transfer.transfer_id
            if isinstance(transfer, TransferSession)
            else str(transfer.get("transfer_id") or "")
        )
        if not transfer_id:
            raise ValueError("transfer_id is required")

        existing = await self._find_by_ref("transfer_session", transfer_id)
        if existing is not None:
            return self.operation_dict(existing)

        status = _operation_status_from_transfer(
            transfer.status if isinstance(transfer, TransferSession) else transfer.get("status")
        )
        now = datetime.now(UTC)
        operation = Operation(
            operation_id=f"op_{secrets.token_hex(8)}",
            kind="transfer",
            status=status,
            ref_type="transfer_session",
            ref_id=transfer_id,
            actor_type=actor_type,
            actor_id=actor_id,
            session_id=session_id,
            title=_transfer_title(transfer),
            wait_policy="manual",
            resume_policy="manual",
            cancel_supported=True,
            started_at=now,
            completed_at=now if status in TERMINAL_OPERATION_STATUSES else None,
            metadata_json={"created_by": "transfer.create"},
        )
        self.db.add(operation)
        await self.db.flush()
        await self._append_event(
            operation,
            "operation.created",
            {"ref_type": operation.ref_type, "ref_id": operation.ref_id},
        )
        result = self.operation_dict(operation)
        await self.db.commit()
        return result

    async def status(self, operation_id: str) -> dict[str, object]:
        operation = await self._get(operation_id)
        ref: dict[str, object] = {}
        if operation.kind == "transfer" and operation.ref_type == "transfer_session":
            from yequ.application.transfer import TransferApplicationService

            transfer = await TransferApplicationService(self.db).status(operation.ref_id)
            await self.db.refresh(operation)
            await self._sync_from_transfer(operation, transfer)
            ref["transfer"] = transfer
        result = {"operation": self.operation_dict(operation), **ref}
        await self.db.commit()
        return result

    async def cancel(
        self,
        operation_id: str,
        *,
        reason: str = "operation_cancelled",
    ) -> dict[str, object]:
        operation = await self._get(operation_id)
        if not operation.cancel_supported:
            raise ValueError(f"Operation {operation_id!r} does not support cancel")
        ref: dict[str, object] = {}
        if operation.kind == "transfer" and operation.ref_type == "transfer_session":
            from yequ.application.transfer import TransferApplicationService

            transfer = await TransferApplicationService(self.db).cancel(
                operation.ref_id,
                reason=reason,
            )
            await self.db.refresh(operation)
            await self._sync_from_transfer(operation, transfer)
            ref["transfer"] = transfer
        else:
            operation.status = "cancelled"
            operation.completed_at = datetime.now(UTC)
            await self._append_event(operation, "operation.cancelled", {"reason": reason})
        result = {"operation": self.operation_dict(operation), **ref}
        await self.db.commit()
        return result

    async def _sync_from_transfer(
        self,
        operation: Operation,
        transfer: dict[str, object],
    ) -> None:
        next_status = _operation_status_from_transfer(transfer.get("status"))
        changed = operation.status != next_status
        operation.status = next_status
        operation.output_data = {"transfer": transfer}
        operation.error_code = _optional_str(transfer.get("error_code"))
        operation.error_message = _optional_str(transfer.get("error_message"))
        if next_status in TERMINAL_OPERATION_STATUSES:
            operation.completed_at = operation.completed_at or datetime.now(UTC)
        if changed:
            await self._append_event(
                operation,
                f"operation.{next_status}",
                {"transfer_id": operation.ref_id, "status": next_status},
            )

    async def _find_by_ref(self, ref_type: str, ref_id: str) -> Operation | None:
        result = await self.db.execute(
            select(Operation).where(
                Operation.ref_type == ref_type,
                Operation.ref_id == ref_id,
            )
        )
        return result.scalar_one_or_none()

    async def _get(self, operation_id: str) -> Operation:
        result = await self.db.execute(
            select(Operation).where(Operation.operation_id == operation_id)
        )
        operation = result.scalar_one_or_none()
        if operation is None:
            raise ValueError(f"Operation {operation_id!r} not found")
        return operation

    async def _append_event(
        self,
        operation: Operation,
        event_type: str,
        data: dict[str, object] | None = None,
    ) -> None:
        seq_result = await self.db.execute(
            select(func.max(OperationEvent.seq)).where(
                OperationEvent.operation_id == operation.operation_id
            )
        )
        seq = int(seq_result.scalar() or 0) + 1
        self.db.add(
            OperationEvent(
                event_id=f"opevt_{secrets.token_hex(8)}",
                operation_id=operation.operation_id,
                seq=seq,
                event_type=event_type,
                status=operation.status,
                data=data or {},
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def operation_dict(operation: Operation) -> dict[str, object]:
        values = operation.__dict__
        started_at = values.get("started_at")
        completed_at = values.get("completed_at")
        created_at = values.get("created_at")
        updated_at = values.get("updated_at")
        return {
            "operation_id": values.get("operation_id"),
            "kind": values.get("kind"),
            "status": values.get("status"),
            "ref_type": values.get("ref_type"),
            "ref_id": values.get("ref_id"),
            "title": values.get("title"),
            "progress_pct": values.get("progress_pct"),
            "progress_message": values.get("progress_message"),
            "error_code": values.get("error_code"),
            "error_message": values.get("error_message"),
            "wait_policy": values.get("wait_policy"),
            "resume_policy": values.get("resume_policy"),
            "cancel_supported": bool(values.get("cancel_supported", False)),
            "started_at": started_at.isoformat() if started_at else None,
            "completed_at": completed_at.isoformat() if completed_at else None,
            "created_at": created_at.isoformat() if created_at else None,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }


def wait_handle_for_operation(operation: dict[str, object]) -> dict[str, object]:
    return {
        "type": "operation",
        "operation_id": operation["operation_id"],
        "kind": operation["kind"],
        "status": operation["status"],
        "resume_policy": operation.get("resume_policy") or "manual",
        "cancel_supported": bool(operation.get("cancel_supported", False)),
    }


def _operation_status_from_transfer(value: object) -> str:
    status = str(value or "created")
    if status in {"created", "queued"}:
        return "queued"
    if status in {"receiving", "running"}:
        return "running"
    if status in TERMINAL_OPERATION_STATUSES:
        return status
    return "running"


def _transfer_title(transfer: dict[str, object] | TransferSession) -> str:
    if isinstance(transfer, TransferSession):
        return f"Transfer {transfer.source_node_id} -> {transfer.target_node_id}"
    source = str(transfer.get("source_node_id") or "")
    target = str(transfer.get("target_node_id") or "")
    return f"Transfer {source} -> {target}".strip()


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
