"""Application service for deploying Center artifacts to Node filesystems."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.application.schemas import ExecuteToolResult
from yequ.models.artifact import ArtifactDeployPreflight
from yequ.services.artifact_service import artifact_to_dict, get_artifact

ARTIFACT_DEPLOY_PREFLIGHT_DEFAULT_TTL_SEC = 120
ARTIFACT_DEPLOY_PREFLIGHT_MIN_TTL_SEC = 30
ARTIFACT_DEPLOY_PREFLIGHT_MAX_TTL_SEC = 300


@dataclass(slots=True)
class ArtifactDeployPreflightCommand:
    artifact_id: str
    target_node_id: str
    output_path: str
    mode: str = "fail_if_exists"
    timeout_sec: int = 20
    ttl_sec: int = ARTIFACT_DEPLOY_PREFLIGHT_DEFAULT_TTL_SEC
    actor_type: str = "agent"
    actor_id: str = "agent"
    session_id: str | None = None
    execution_mode: str = "auto"


@dataclass(slots=True)
class ArtifactDeployCommand:
    artifact_id: str
    target_node_id: str
    output_path: str
    mode: str = "fail_if_exists"
    preflight_id: str | None = None
    skip_preflight: bool = False
    skip_reason: str | None = None


class ArtifactDeployApplicationService:
    """Preflight and validate Center artifact deployment intents."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def preflight(self, command: ArtifactDeployPreflightCommand) -> dict[str, object]:
        now = datetime.now(UTC)
        ttl_sec = _preflight_ttl(command.ttl_sec)
        expires_at = now + timedelta(seconds=ttl_sec)
        missing = []
        if not command.artifact_id:
            missing.append("artifact_id")
        if not command.target_node_id:
            missing.append("target_node_id")
        if not command.output_path:
            missing.append("output_path")
        if command.mode not in {"fail_if_exists", "overwrite"}:
            missing.append("mode")
        if missing:
            return {
                "allowed": False,
                "decision": "needs_input",
                "missing_slots": missing,
                "failed_preconditions": [],
                "artifact": None,
                "target": None,
                "observed_at": now.isoformat(),
                "ttl_sec": ttl_sec,
                "expires_at": expires_at.isoformat(),
            }

        artifact_fact: dict[str, object]
        artifact_error: dict[str, object] | None = None
        try:
            artifact = await get_artifact(self.db, command.artifact_id)
            artifact_fact = artifact_to_dict(artifact)
        except ValueError as exc:
            artifact_fact = {
                "artifact_id": command.artifact_id,
                "status": "not_found",
            }
            artifact_error = {
                "fact": "artifact.exists",
                "code": "artifact_not_found",
                "message": str(exc),
            }

        target_result = await self._invoke_target_stat(command)
        target = _stat_payload(target_result)
        target["observed_at"] = now.isoformat()
        artifact_fact["observed_at"] = now.isoformat()

        failed = _artifact_deploy_preflight_failures(
            artifact_error=artifact_error,
            target_result=target_result,
            artifact=artifact_fact,
            target=target,
            mode=command.mode,
        )

        preflight = ArtifactDeployPreflight(
            preflight_id=f"apf_{secrets.token_hex(8)}",
            status="allow" if not failed else "preflight_failed",
            allowed=not failed,
            intent_hash=_artifact_deploy_intent_hash(
                artifact_id=command.artifact_id,
                target_node_id=command.target_node_id,
                output_path=command.output_path,
                mode=command.mode,
            ),
            artifact_id=command.artifact_id,
            target_node_id=command.target_node_id,
            output_path=command.output_path,
            mode=command.mode,
            artifact_fact=artifact_fact,
            target_fact=target,
            failed_preconditions=failed,
            actor_id=command.actor_id,
            session_id=command.session_id,
            expires_at=expires_at,
        )
        self.db.add(preflight)
        await self.db.commit()
        return {
            "preflight_id": preflight.preflight_id,
            "allowed": not failed,
            "decision": "allow" if not failed else "preflight_failed",
            "missing_slots": [],
            "failed_preconditions": failed,
            "artifact": artifact_fact,
            "target": target,
            "mode": command.mode,
            "observed_at": now.isoformat(),
            "ttl_sec": ttl_sec,
            "expires_at": preflight.expires_at.isoformat(),
        }

    async def validate_preflight(
        self,
        command: ArtifactDeployCommand,
    ) -> ArtifactDeployPreflight | None:
        if command.skip_preflight:
            if not command.skip_reason:
                raise ValueError("skip_preflight requires explicit skip_reason")
            return None
        if not command.preflight_id:
            raise ValueError(
                "preflight_required: artifact.deploy.preflight must pass before artifact.deploy"
            )
        result = await self.db.execute(
            select(ArtifactDeployPreflight).where(
                ArtifactDeployPreflight.preflight_id == command.preflight_id
            )
        )
        preflight = result.scalar_one_or_none()
        if preflight is None:
            raise ValueError(f"preflight_not_found: {command.preflight_id}")
        now = datetime.now(UTC)
        expires_at = _ensure_aware(preflight.expires_at)
        if expires_at <= now:
            raise ValueError("preflight_expired: rerun artifact.deploy.preflight")
        expected_hash = _artifact_deploy_intent_hash(
            artifact_id=command.artifact_id,
            target_node_id=command.target_node_id,
            output_path=command.output_path,
            mode=command.mode,
        )
        if preflight.intent_hash != expected_hash:
            raise ValueError("preflight_intent_mismatch: rerun artifact.deploy.preflight")
        if not preflight.allowed:
            raise ValueError(
                "preflight_failed: rerun artifact.deploy.preflight and inspect failures"
            )
        return preflight

    async def _invoke_target_stat(
        self,
        command: ArtifactDeployPreflightCommand,
    ) -> ExecuteToolResult:
        from yequ.db import async_session_factory
        from yequ.runtime import CenterExecutionRuntime, RuntimeCommand

        async with async_session_factory() as db:
            return await CenterExecutionRuntime(db).execute(
                RuntimeCommand(
                    function_name="capability.invoke",
                    input_data={
                        "capability_ref": "transfer.local.stat",
                        "node_id": command.target_node_id,
                        "input": {"path": command.output_path, "sha256": False},
                    },
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    session_id=command.session_id,
                    execution_mode=command.execution_mode,
                    wait_for_result=True,
                    deadline=datetime.now(UTC) + timedelta(seconds=command.timeout_sec),
                    timeout_sec=command.timeout_sec,
                    suppress_operation=True,
                )
            )


def _artifact_deploy_preflight_failures(
    *,
    artifact_error: dict[str, object] | None,
    target_result: ExecuteToolResult,
    artifact: dict[str, object],
    target: dict[str, object],
    mode: str,
) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    if artifact_error is not None:
        failures.append(artifact_error)
    if target_result.status != "succeeded":
        failures.append(
            {
                "fact": "target.stat",
                "code": target_result.error_code or target_result.status,
                "message": target_result.error_message,
            }
        )
    if failures:
        return failures

    if artifact.get("status") != "available":
        failures.append(
            {
                "fact": "artifact.available",
                "code": "artifact_unavailable",
                "status": artifact.get("status"),
            }
        )

    if target.get("parent_exists") is False:
        failures.append(
            {"fact": "target.parent_exists", "code": "target_parent_not_found"}
        )
    if target.get("writable") is not True:
        failures.append({"fact": "target.writable", "code": "target_not_writable"})
    if mode == "fail_if_exists" and target.get("found") is True:
        failures.append({"fact": "target.not_exists", "code": "target_exists"})

    artifact_size = _first_int(artifact.get("size_bytes"))
    free_bytes = _first_int(target.get("free_bytes"))
    if artifact_size is not None and free_bytes is not None and free_bytes < artifact_size:
        failures.append(
            {
                "fact": "target.free_space",
                "code": "insufficient_space",
                "required_bytes": artifact_size,
                "available_bytes": free_bytes,
            }
        )

    return failures


def _stat_payload(result: ExecuteToolResult) -> dict[str, object]:
    if result.status != "succeeded":
        return {
            "status": result.status,
            "error_code": result.error_code,
            "error_message": result.error_message,
        }
    return dict(result.output_data or {})


def _artifact_deploy_intent_hash(
    *,
    artifact_id: str,
    target_node_id: str,
    output_path: str,
    mode: str,
) -> str:
    payload = {
        "artifact_id": artifact_id,
        "target_node_id": target_node_id,
        "output_path": output_path,
        "mode": mode,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _preflight_ttl(ttl_sec: int) -> int:
    return max(
        ARTIFACT_DEPLOY_PREFLIGHT_MIN_TTL_SEC,
        min(int(ttl_sec), ARTIFACT_DEPLOY_PREFLIGHT_MAX_TTL_SEC),
    )


def _first_int(*values: object) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                continue
    return None


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
