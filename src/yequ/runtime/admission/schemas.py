"""Schemas for execution admission decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ExecutionDecision = Literal[
    "inline",
    "sync_wait",
    "waitable_operation",
    "workflow_operation",
    "approval",
    "deny",
]


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Center-side decision for how one call should be executed."""

    function_name: str
    decision: ExecutionDecision
    reason: str
    operation_kind: str | None = None
    waitable: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "function_name": self.function_name,
            "decision": self.decision,
            "reason": self.reason,
            "operation_kind": self.operation_kind,
            "waitable": self.waitable,
            "metadata": dict(self.metadata),
        }

