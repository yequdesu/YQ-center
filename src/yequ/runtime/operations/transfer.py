"""Transfer Operation projection handler."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.application.transfer import TransferApplicationService
from yequ.models.operation import Operation

TERMINAL_OPERATION_STATUSES = {"succeeded", "failed", "cancelled", "timeout"}


class TransferOperationHandler:
    """Project transfer domain state into the Operation shell."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def project(self, operation: Operation) -> dict[str, object]:
        transfer = await TransferApplicationService(self.db).status(operation.ref_id)
        self.sync_operation(operation, transfer)
        return {"transfer": transfer}

    async def cancel(self, operation: Operation, *, reason: str) -> dict[str, object]:
        transfer = await TransferApplicationService(self.db).cancel(
            operation.ref_id,
            reason=reason,
        )
        self.sync_operation(operation, transfer)
        return {"transfer": transfer}

    @staticmethod
    def sync_operation(operation: Operation, transfer: dict[str, object]) -> bool:
        next_status = operation_status_from_transfer(transfer.get("status"))
        progress = _operation_transfer_progress(transfer)
        progress_pct = progress.get("pct")
        progress_message = optional_str(progress.get("message"))
        changed = (
            operation.status != next_status
            or operation.progress_pct != progress_pct
            or operation.progress_message != progress_message
        )
        operation.status = next_status
        operation.progress_pct = progress_pct if isinstance(progress_pct, int) else None
        operation.progress_message = progress_message
        operation.output_data = {"transfer": transfer, "progress_detail": progress}
        operation.error_code = optional_str(transfer.get("error_code"))
        operation.error_message = optional_str(transfer.get("error_message"))
        if next_status in TERMINAL_OPERATION_STATUSES:
            operation.completed_at = operation.completed_at or datetime.now(UTC)
        return changed


def operation_status_from_transfer(value: object) -> str:
    status = str(value or "created")
    if status in {"created", "queued"}:
        return "queued"
    if status in {"receiving", "running"}:
        return "running"
    if status in TERMINAL_OPERATION_STATUSES:
        return status
    return "running"


def transfer_title(transfer: dict[str, object] | object) -> str:
    transfer_id = getattr(transfer, "transfer_id", None)
    if transfer_id is not None:
        source = str(getattr(transfer, "source_node_id", "") or "")
        target = str(getattr(transfer, "target_node_id", "") or "")
        return f"Transfer {source} -> {target}".strip()
    if isinstance(transfer, dict):
        source = str(transfer.get("source_node_id") or "")
        target = str(transfer.get("target_node_id") or "")
        return f"Transfer {source} -> {target}".strip()
    return "Transfer"


def optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _operation_transfer_progress(transfer: dict[str, object]) -> dict[str, object]:
    summary = transfer.get("summary")
    if isinstance(summary, dict):
        progress = summary.get("progress")
        if isinstance(progress, dict):
            return progress

    status = str(transfer.get("status") or "created")
    if status == "succeeded":
        return {"pct": 100, "message": "Transfer completed"}
    if status == "failed":
        return {"pct": None, "message": "Transfer failed"}
    if status == "cancelled":
        return {"pct": None, "message": "Transfer cancelled"}
    if status == "timeout":
        return {"pct": None, "message": "Transfer timed out"}
    if status in {"created", "queued"}:
        return {"pct": None, "message": "Waiting for transfer jobs"}
    return {"pct": None, "message": "Transfer is running"}
