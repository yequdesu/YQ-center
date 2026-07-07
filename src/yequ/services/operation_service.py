"""Operation Bus service for waitable Center runtime work."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.approval import ApprovalRequest
from yequ.models.job import Job
from yequ.models.maintenance_plan import MaintenancePlan, MaintenanceRun
from yequ.models.operation import Operation, OperationEvent
from yequ.models.transfer import TransferSession
from yequ.runtime.operations import OperationHandlerRegistry
from yequ.runtime.operations.approval import approval_title, operation_status_from_approval
from yequ.runtime.operations.job import job_title, operation_status_from_job
from yequ.runtime.operations.maintenance import maintenance_title, operation_status_from_maintenance
from yequ.runtime.operations.transfer import operation_status_from_transfer, transfer_title
from yequ.services.session_audit import record_session_audit_event

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

        status = operation_status_from_transfer(
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
            title=transfer_title(transfer),
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

    async def create_for_job(
        self,
        job: Job,
        *,
        actor_type: str,
        actor_id: str | None,
        session_id: str | None,
    ) -> dict[str, object]:
        job_id = str(getattr(job, "job_id", "") or "")
        if not job_id:
            raise ValueError("job_id is required")

        existing = await self._find_by_ref("job", job_id)
        if existing is not None:
            return self.operation_dict(existing)

        status = operation_status_from_job(str(getattr(job, "status", "queued") or "queued"))
        now = datetime.now(UTC)
        operation = Operation(
            operation_id=f"op_{secrets.token_hex(8)}",
            kind="job",
            status=status,
            ref_type="job",
            ref_id=job_id,
            actor_type=actor_type,
            actor_id=actor_id,
            session_id=session_id,
            title=job_title(job),
            wait_policy="manual",
            resume_policy="manual",
            cancel_supported=True,
            started_at=now,
            completed_at=now if status in TERMINAL_OPERATION_STATUSES else None,
            metadata_json={"created_by": "job_execution"},
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

    async def create_for_approval(
        self,
        approval: ApprovalRequest,
        *,
        actor_type: str,
        actor_id: str | None,
        session_id: str | None,
    ) -> dict[str, object]:
        existing = await self._find_by_ref("approval_request", approval.approval_id)
        if existing is not None:
            return self.operation_dict(existing)

        status = operation_status_from_approval(approval.status)
        now = datetime.now(UTC)
        operation = Operation(
            operation_id=f"op_{secrets.token_hex(8)}",
            kind="approval_wait",
            status=status,
            ref_type="approval_request",
            ref_id=approval.approval_id,
            actor_type=actor_type,
            actor_id=actor_id,
            session_id=session_id,
            title=approval_title(approval),
            wait_policy="manual",
            resume_policy="manual",
            cancel_supported=True,
            started_at=now,
            completed_at=now if status in TERMINAL_OPERATION_STATUSES else None,
            metadata_json={"created_by": "approval_gate"},
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

    async def create_for_maintenance(
        self,
        run: MaintenanceRun,
        plan: MaintenancePlan,
        *,
        actor_type: str,
        actor_id: str | None,
        session_id: str | None,
    ) -> dict[str, object]:
        existing = await self._find_by_ref("maintenance_run", run.run_id)
        if existing is not None:
            return self.operation_dict(existing)

        status = operation_status_from_maintenance(run.status)
        now = datetime.now(UTC)
        operation = Operation(
            operation_id=f"op_{secrets.token_hex(8)}",
            kind="maintenance",
            status=status,
            ref_type="maintenance_run",
            ref_id=run.run_id,
            actor_type=actor_type,
            actor_id=actor_id,
            session_id=session_id,
            title=maintenance_title(run, plan),
            wait_policy="manual",
            resume_policy="manual",
            cancel_supported=True,
            started_at=now,
            completed_at=now if status in TERMINAL_OPERATION_STATUSES else None,
            metadata_json={"created_by": "maintenance.run"},
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

    async def status(self, operation_id: str, *, projection: str = "detail") -> dict[str, object]:
        operation = await self._get(operation_id)
        handler = OperationHandlerRegistry(self.db).resolve(
            kind=operation.kind,
            ref_type=operation.ref_type,
        )
        previous_status = operation.status
        ref = await handler.project(operation)
        if operation.status != previous_status:
            await self._append_event(
                operation,
                f"operation.{operation.status}",
                {"ref_type": operation.ref_type, "ref_id": operation.ref_id},
            )
        result = {"operation": self.operation_dict(operation), **ref}
        await self.db.commit()
        if projection == "summary":
            return operation_status_summary(result)
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
        handler = OperationHandlerRegistry(self.db).resolve(
            kind=operation.kind,
            ref_type=operation.ref_type,
        )
        ref = await handler.cancel(operation, reason=reason)
        await self._append_event(operation, "operation.cancelled", {"reason": reason})
        result = {"operation": self.operation_dict(operation), **ref}
        await self.db.commit()
        return result

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
        await self.append_event(operation, event_type, data)

    async def append_event(
        self,
        operation: Operation,
        event_type: str,
        data: dict[str, object] | None = None,
    ) -> None:
        now = datetime.now(UTC)
        seq_result = await self.db.execute(
            select(func.max(OperationEvent.seq)).where(
                OperationEvent.operation_id == operation.operation_id
            )
        )
        seq = int(seq_result.scalar() or 0) + 1
        event = OperationEvent(
            event_id=f"opevt_{secrets.token_hex(8)}",
            operation_id=operation.operation_id,
            seq=seq,
            event_type=event_type,
            status=operation.status,
            data=data or {},
            created_at=now,
        )
        self.db.add(event)
        if operation.session_id:
            from yequ.ycr.session_state import ingest_operation_event_state

            await ingest_operation_event_state(
                self.db,
                operation=operation,
                event_id=event.event_id,
            )
        if operation.status in TERMINAL_OPERATION_STATUSES and operation.session_id:
            from yequ.services.agent_operation_notifications import (
                AgentOperationNotificationService,
            )

            await AgentOperationNotificationService(self.db).enqueue_terminal_event(
                operation=operation,
                event=event,
            )
        record_session_audit_event(
            operation.session_id,
            event_type,
            {
                "operation_id": operation.operation_id,
                "operation_kind": operation.kind,
                "operation_status": operation.status,
                "ref_type": operation.ref_type,
                "ref_id": operation.ref_id,
                "seq": seq,
                "data": data or {},
                "title": operation.title,
                "progress_pct": operation.progress_pct,
                "progress_message": operation.progress_message,
                "error_code": operation.error_code,
                "error_message": operation.error_message,
            },
            source="operation",
            event_time=now,
        )

    @staticmethod
    def operation_dict(operation: Operation) -> dict[str, object]:
        values = operation.__dict__
        started_at = values.get("started_at")
        completed_at = values.get("completed_at")
        created_at = values.get("created_at")
        updated_at = values.get("updated_at")
        output_data = values.get("output_data")
        progress_detail = (
            output_data.get("progress_detail") if isinstance(output_data, dict) else None
        )
        return {
            "operation_id": values.get("operation_id"),
            "kind": values.get("kind"),
            "status": values.get("status"),
            "ref_type": values.get("ref_type"),
            "ref_id": values.get("ref_id"),
            "title": values.get("title"),
            "progress_pct": values.get("progress_pct"),
            "progress_message": values.get("progress_message"),
            "progress_detail": progress_detail if isinstance(progress_detail, dict) else None,
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


def operation_status_summary(value: dict[str, object]) -> dict[str, object]:
    operation = value.get("operation")
    operation_dict = operation if isinstance(operation, dict) else {}
    output: dict[str, object] = {"operation": operation_dict}
    if isinstance(value.get("transfer"), dict):
        from yequ.application.transfer import transfer_session_summary

        output["transfer"] = transfer_session_summary(value["transfer"])
    if isinstance(value.get("job"), dict):
        output["job"] = _job_summary(value["job"])
    if isinstance(value.get("artifacts"), list):
        output["artifacts"] = [
            _artifact_summary(item) for item in value["artifacts"] if isinstance(item, dict)
        ]
    if isinstance(value.get("approval"), dict):
        output["approval"] = value["approval"]
    if isinstance(value.get("maintenance_run"), dict):
        output["maintenance_run"] = _maintenance_summary(value["maintenance_run"])
    return output


def _job_summary(value: dict[str, object]) -> dict[str, object]:
    return {
        "job_id": value.get("job_id"),
        "invocation_id": value.get("invocation_id"),
        "node_id": value.get("node_id"),
        "runtime_id": value.get("runtime_id"),
        "function_name": value.get("function_name"),
        "status": value.get("status"),
        "error_code": value.get("error_code"),
        "error_message": value.get("error_message"),
        "created_at": value.get("created_at"),
        "started_at": value.get("started_at"),
        "finished_at": value.get("finished_at"),
    }


def _artifact_summary(value: dict[str, object]) -> dict[str, object]:
    return {
        "artifact_id": value.get("artifact_id"),
        "artifact_type": value.get("artifact_type"),
        "title": value.get("title"),
        "summary": value.get("summary"),
        "content_type": value.get("content_type"),
        "size_bytes": value.get("size_bytes"),
        "status": value.get("status"),
        "node_id": value.get("node_id"),
        "created_at": value.get("created_at"),
    }


def _maintenance_summary(value: dict[str, object]) -> dict[str, object]:
    return {
        "run_id": value.get("run_id"),
        "plan_id": value.get("plan_id"),
        "status": value.get("status"),
        "progress_pct": value.get("progress_pct"),
        "progress_message": value.get("progress_message"),
        "error_code": value.get("error_code"),
        "error_message": value.get("error_message"),
    }
