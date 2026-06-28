"""Application query service for pre-execution tool checks."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.application.schemas import ToolPreflightCommand, ToolPreflightResult
from yequ.application.tool_invocation import CENTER_META_TOOLS
from yequ.config import get_settings
from yequ.models.capability import Capability
from yequ.services.capability_resolver import resolve_function
from yequ.services.policy import check_policy_l2


class ToolPreflightApplicationService:
    """Read-only checks used by Agent scheduling before execution."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def check(self, command: ToolPreflightCommand) -> ToolPreflightResult:
        risk = command.declared_risk or "safe"
        effect = command.declared_effect or "read"

        policy = check_policy_l2(
            execution_mode=command.execution_mode,
            risk_level=risk,
            effect=effect,
        )
        if not policy.allowed and policy.decision != "ask":
            return ToolPreflightResult(
                function_name=command.function_name,
                status="denied",
                target_node_id=command.target_node_id,
                risk=risk,
                effect=effect,
                error_code="policy_denied",
                error_message=policy.reason or "Policy denied",
            )

        if (
            command.function_name in CENTER_META_TOOLS
            or command.function_name == "capability.invoke"
        ):
            return ToolPreflightResult(
                function_name=command.function_name,
                status="ok",
                target_node_id=command.target_node_id,
                risk=risk,
                effect=effect,
            )

        resolved = await resolve_function(
            self.db,
            command.function_name,
            target_node_id=command.target_node_id,
            settings=get_settings(),
        )
        if resolved is None or not resolved.available:
            return ToolPreflightResult(
                function_name=command.function_name,
                status="unavailable",
                target_node_id=command.target_node_id or (resolved.node_id if resolved else None),
                risk=risk,
                effect=effect,
                error_code=(resolved.unavailable_code if resolved else "function_not_available"),
                error_message=(
                    resolved.unavailable_reason
                    if resolved and resolved.unavailable_reason
                    else f"No online node has {command.function_name!r}"
                ),
            )

        capability_result = await self.db.execute(
            select(Capability)
            .where(
                Capability.capability_type == "function",
                Capability.name == command.function_name,
                Capability.is_active == True,  # noqa: E712
            )
            .limit(1)
        )
        capability = capability_result.scalar_one_or_none()

        return ToolPreflightResult(
            function_name=command.function_name,
            status="ok",
            target_node_id=resolved.node_id,
            risk=resolved.risk or risk,
            effect=resolved.effect or effect,
            resource_keys=list(
                capability.resource_keys
                if capability and capability.resource_keys
                else resolved.resource_keys or []
            ),
            conflict_policy=(
                capability.conflict_policy
                if capability and capability.conflict_policy
                else resolved.conflict_policy
            ),
        )
