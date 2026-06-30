"""Registry for Operation projection handlers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.runtime.operations.approval import ApprovalOperationHandler
from yequ.runtime.operations.base import OperationHandler
from yequ.runtime.operations.job import JobOperationHandler
from yequ.runtime.operations.maintenance import MaintenanceOperationHandler
from yequ.runtime.operations.transfer import TransferOperationHandler


class OperationHandlerRegistry:
    """Resolve operation kind/ref pairs to concrete handlers."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def resolve(self, *, kind: str, ref_type: str) -> OperationHandler:
        if kind == "transfer" and ref_type == "transfer_session":
            return TransferOperationHandler(self.db)
        if kind == "job" and ref_type == "job":
            return JobOperationHandler(self.db)
        if kind == "approval_wait" and ref_type == "approval_request":
            return ApprovalOperationHandler(self.db)
        if kind == "maintenance" and ref_type == "maintenance_run":
            return MaintenanceOperationHandler(self.db)
        raise ValueError(f"Unsupported operation kind/ref: {kind}/{ref_type}")
