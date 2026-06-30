"""Base protocol for Operation projection handlers."""

from __future__ import annotations

from typing import Protocol

from yequ.models.operation import Operation


class OperationHandler(Protocol):
    """Project and control one operation kind/ref pair."""

    async def project(self, operation: Operation) -> dict[str, object]:
        """Return domain projection and update the operation shell if needed."""

    async def cancel(self, operation: Operation, *, reason: str) -> dict[str, object]:
        """Cancel the operation and return the updated domain projection."""
