"""Application service for MaintenancePlan use cases."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.maintenance_plan import MaintenancePlan
from yequ.services.maintenance_service import create_plan
from yequ.types import JsonObject


class MaintenancePlanApplicationService:
    """Use-case boundary for creating maintenance plans."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        *,
        actor_id: str,
        session_id: str | None,
        goal: str,
        target_node_id: str,
        steps: list[JsonObject],
        risk: str = "maintenance",
        max_total_duration_sec: int | None = None,
        rollback_strategy: str | None = None,
        execution_mode: str = "auto",
    ) -> MaintenancePlan:
        return await create_plan(
            self.db,
            actor_id=actor_id,
            session_id=session_id,
            goal=goal,
            target_node_id=target_node_id,
            steps=steps,
            risk=risk,
            max_total_duration_sec=max_total_duration_sec,
            rollback_strategy=rollback_strategy,
            execution_mode=execution_mode,
        )
