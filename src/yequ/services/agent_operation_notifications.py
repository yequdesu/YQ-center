"""Agent-facing Operation notification queue."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.operation import AgentOperationNotification, Operation, OperationEvent
from yequ.services.operation_service import TERMINAL_OPERATION_STATUSES

STALE_PROCESSING_AFTER_SEC = 300


class AgentOperationNotificationService:
    """Queue terminal Operation observations for Agent Runtime reporting."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enqueue_terminal_event(
        self,
        *,
        operation: Operation,
        event: OperationEvent,
    ) -> AgentOperationNotification | None:
        if not operation.session_id:
            return None
        if operation.status not in TERMINAL_OPERATION_STATUSES:
            return None
        if operation.kind == "approval_wait":
            return None

        existing = await self._find(operation.session_id, operation.operation_id)
        if existing is not None:
            if not existing.event_id:
                existing.event_id = event.event_id
            existing.operation_status = operation.status
            existing.updated_at = datetime.now(UTC)
            await self.db.flush()
            return existing

        now = datetime.now(UTC)
        notification = AgentOperationNotification(
            notification_id=f"aon_{secrets.token_hex(8)}",
            session_id=operation.session_id,
            operation_id=operation.operation_id,
            event_id=event.event_id,
            operation_status=operation.status,
            status="pending",
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        self.db.add(notification)
        await self.db.flush()
        return notification

    async def claim_next(
        self,
        *,
        session_id: str,
    ) -> dict[str, object] | None:
        return await self._claim_next(session_id=session_id)

    async def claim_next_any(self) -> dict[str, object] | None:
        return await self._claim_next(session_id=None)

    async def claim_next_idle(
        self,
        *,
        is_session_active: Callable[[str], bool],
    ) -> dict[str, object] | None:
        return await self._claim_next(session_id=None, is_session_active=is_session_active)

    async def _claim_next(
        self,
        *,
        session_id: str | None,
        is_session_active: Callable[[str], bool] | None = None,
    ) -> dict[str, object] | None:
        now = datetime.now(UTC)
        stale_cutoff = now - timedelta(seconds=STALE_PROCESSING_AFTER_SEC)
        stmt = (
            select(AgentOperationNotification, Operation)
            .join(Operation, Operation.operation_id == AgentOperationNotification.operation_id)
            .where(
                (
                    AgentOperationNotification.status == "pending"
                )
                | (
                    (AgentOperationNotification.status == "processing")
                    & (AgentOperationNotification.claimed_at < stale_cutoff)
                )
            )
            .order_by(AgentOperationNotification.created_at.asc())
            .limit(20 if is_session_active is not None else 1)
        )
        if session_id is not None:
            stmt = stmt.where(AgentOperationNotification.session_id == session_id)
        result = await self.db.execute(stmt)
        row = None
        for candidate in result.all():
            notification, _operation = candidate
            if is_session_active is not None and is_session_active(notification.session_id):
                continue
            row = candidate
            break
        if row is None:
            return None
        notification, operation = row
        notification.status = "processing"
        notification.attempts = int(notification.attempts or 0) + 1
        notification.claimed_at = now
        notification.updated_at = now
        notification.last_error = None
        await self.db.flush()
        return self.notification_dict(notification, operation)

    async def list_pending(self, *, session_id: str, limit: int = 20) -> list[dict[str, object]]:
        result = await self.db.execute(
            select(AgentOperationNotification, Operation)
            .join(Operation, Operation.operation_id == AgentOperationNotification.operation_id)
            .where(AgentOperationNotification.session_id == session_id)
            .where(AgentOperationNotification.status.in_(["pending", "processing", "failed"]))
            .order_by(AgentOperationNotification.created_at.asc())
            .limit(limit)
        )
        return [
            self.notification_dict(notification, operation)
            for notification, operation in result.all()
        ]

    async def mark_reported(
        self,
        *,
        notification_id: str,
        turn_id: str | None = None,
    ) -> dict[str, object]:
        notification = await self._get(notification_id)
        notification.status = "reported"
        notification.report_turn_id = turn_id or notification.report_turn_id
        notification.reported_at = datetime.now(UTC)
        notification.updated_at = notification.reported_at
        await self.db.flush()
        operation = await self._get_operation(notification.operation_id)
        return self.notification_dict(notification, operation)

    async def mark_failed(
        self,
        *,
        notification_id: str,
        error: str,
    ) -> dict[str, object]:
        notification = await self._get(notification_id)
        notification.status = "failed"
        notification.last_error = error[:1000]
        notification.updated_at = datetime.now(UTC)
        await self.db.flush()
        operation = await self._get_operation(notification.operation_id)
        return self.notification_dict(notification, operation)

    async def _find(
        self,
        session_id: str,
        operation_id: str,
    ) -> AgentOperationNotification | None:
        result = await self.db.execute(
            select(AgentOperationNotification).where(
                AgentOperationNotification.session_id == session_id,
                AgentOperationNotification.operation_id == operation_id,
            )
        )
        return result.scalar_one_or_none()

    async def _get(self, notification_id: str) -> AgentOperationNotification:
        result = await self.db.execute(
            select(AgentOperationNotification).where(
                AgentOperationNotification.notification_id == notification_id
            )
        )
        notification = result.scalar_one_or_none()
        if notification is None:
            raise ValueError(f"AgentOperationNotification {notification_id!r} not found")
        return notification

    async def _get_operation(self, operation_id: str) -> Operation:
        result = await self.db.execute(
            select(Operation).where(Operation.operation_id == operation_id)
        )
        operation = result.scalar_one_or_none()
        if operation is None:
            raise ValueError(f"Operation {operation_id!r} not found")
        return operation

    @staticmethod
    def notification_dict(
        notification: AgentOperationNotification,
        operation: Operation,
    ) -> dict[str, object]:
        return {
            "notification_id": notification.notification_id,
            "session_id": notification.session_id,
            "operation_id": notification.operation_id,
            "event_id": notification.event_id,
            "operation_status": notification.operation_status,
            "status": notification.status,
            "attempts": notification.attempts,
            "last_error": notification.last_error,
            "report_turn_id": notification.report_turn_id,
            "created_at": _iso(notification.created_at),
            "updated_at": _iso(notification.updated_at),
            "claimed_at": _iso(notification.claimed_at),
            "reported_at": _iso(notification.reported_at),
            "operation": {
                "operation_id": operation.operation_id,
                "kind": operation.kind,
                "status": operation.status,
                "ref_type": operation.ref_type,
                "ref_id": operation.ref_id,
                "title": operation.title,
                "progress_pct": operation.progress_pct,
                "progress_message": operation.progress_message,
                "error_code": operation.error_code,
                "error_message": operation.error_message,
            },
        }


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None
