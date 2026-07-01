"""Application service for Center-managed transfer sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.application.schemas import ExecuteToolResult
from yequ.models.job import Job
from yequ.models.timeline import TimelineEvent
from yequ.models.transfer import TransferPreflight, TransferSession
from yequ.services.job_service import cancel_job
from yequ.services.timeline_writer import add_timeline_event

TRANSFER_PREFLIGHT_DEFAULT_TTL_SEC = 120
TRANSFER_PREFLIGHT_MIN_TTL_SEC = 30
TRANSFER_PREFLIGHT_MAX_TTL_SEC = 300
TRANSFER_JOB_START_WAIT_SEC = 8.0
TRANSFER_RECEIVER_READY_MIN_WAIT_SEC = 180.0
TRANSFER_RECEIVER_READY_MAX_WAIT_SEC = 600.0
TRANSFER_JOB_MIN_LEASE_SEC = 180
TRANSFER_JOB_MAX_LEASE_SEC = 600


@dataclass(slots=True)
class TransferCreateCommand:
    source_node_id: str
    target_node_id: str
    source_path: str
    target_output_dir: str | None = None
    target_path: str | None = None
    conflict_mode: str | None = None
    timeout_sec: int = 3600
    expected_sha256: str | None = None
    cleanup_on_failure: bool = False
    preflight_id: str | None = None
    skip_preflight: bool = False
    skip_reason: str | None = None
    actor_type: str = "agent"
    actor_id: str = "agent"
    session_id: str | None = None
    execution_mode: str = "auto"


@dataclass(slots=True)
class TransferPreflightCommand:
    source_node_id: str
    target_node_id: str
    source_path: str
    target_output_dir: str | None = None
    target_path: str | None = None
    conflict_mode: str | None = None
    include_sha256: bool = False
    timeout_sec: int = 20
    ttl_sec: int = TRANSFER_PREFLIGHT_DEFAULT_TTL_SEC
    actor_type: str = "agent"
    actor_id: str = "agent"
    session_id: str | None = None
    execution_mode: str = "auto"


@dataclass(frozen=True, slots=True)
class NormalizedTransferTarget:
    output_dir: str
    target_path: str | None


class TransferApplicationService:
    """Create and inspect transfer sessions without exposing node transport sequencing to Agent."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def preflight(self, command: TransferPreflightCommand) -> dict[str, object]:
        now = datetime.now(UTC)
        ttl_sec = _preflight_ttl(command.ttl_sec)
        expires_at = now + timedelta(seconds=ttl_sec)
        missing = []
        if not command.source_node_id:
            missing.append("source_node_id")
        if not command.target_node_id:
            missing.append("target_node_id")
        if not command.source_path:
            missing.append("source_path")
        if not command.target_output_dir and not command.target_path:
            missing.append("target_output_dir_or_target_path")
        conflict_mode = command.conflict_mode
        if conflict_mode not in {"fail_if_exists", "overwrite", "reuse_complete"}:
            missing.append("conflict_mode")
        if missing:
            return {
                "allowed": False,
                "decision": "needs_input",
                "missing_slots": missing,
                "failed_preconditions": [],
                "source": None,
                "target": None,
                "observed_at": now.isoformat(),
                "ttl_sec": ttl_sec,
                "expires_at": expires_at.isoformat(),
            }
        try:
            target_intent = _normalize_transfer_target(
                source_path=command.source_path,
                target_output_dir=command.target_output_dir,
                target_path=command.target_path,
            )
        except ValueError as exc:
            return {
                "allowed": False,
                "decision": "needs_input",
                "missing_slots": [],
                "failed_preconditions": [
                    {
                        "fact": "target_path",
                        "code": "unsupported_target_path",
                        "message": str(exc),
                    }
                ],
                "source": None,
                "target": None,
                "observed_at": now.isoformat(),
                "ttl_sec": ttl_sec,
                "expires_at": expires_at.isoformat(),
            }

        source_result = await self._invoke_stat_capability(
            command,
            node_id=command.source_node_id,
            path=command.source_path,
            include_sha256=command.include_sha256,
        )
        target_probe_path = target_intent.output_dir
        target_result = await self._invoke_stat_capability(
            command,
            node_id=command.target_node_id,
            path=target_probe_path,
            include_sha256=False,
        )
        source_status_result = await self._invoke_status_capability(
            command,
            node_id=command.source_node_id,
        )
        target_status_result = await self._invoke_status_capability(
            command,
            node_id=command.target_node_id,
        )

        source = _stat_payload(source_result)
        target = _stat_payload(target_result)
        source_status = _status_payload(source_status_result)
        target_status = _status_payload(target_status_result)
        source["observed_at"] = now.isoformat()
        source["runtime"] = source_status
        source_status["observed_at"] = now.isoformat()
        target["observed_at"] = now.isoformat()
        target["runtime"] = target_status
        target_status["observed_at"] = now.isoformat()
        failed = _transfer_preflight_failures(
            source_result=source_result,
            target_result=target_result,
            source_status_result=source_status_result,
            target_status_result=target_status_result,
            source=source,
            target=target,
            source_status=source_status,
            target_status=target_status,
            target_path=command.target_path,
            conflict_mode=conflict_mode,
        )
        preflight = TransferPreflight(
            preflight_id=f"tpf_{secrets.token_hex(8)}",
            status="allow" if not failed else "preflight_failed",
            allowed=not failed,
            intent_hash=_transfer_intent_hash(
                source_node_id=command.source_node_id,
                target_node_id=command.target_node_id,
                source_path=command.source_path,
                target_output_dir=target_intent.output_dir,
                target_path=target_intent.target_path,
                conflict_mode=conflict_mode,
            ),
            source_node_id=command.source_node_id,
            target_node_id=command.target_node_id,
            source_path=command.source_path,
            target_output_dir=target_intent.output_dir,
            target_path=target_intent.target_path,
            conflict_mode=conflict_mode,
            source_fact=source,
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
            "source": source,
            "target": target,
            "source_runtime": source_status,
            "target_runtime": target_status,
            "conflict_mode": conflict_mode,
            "observed_at": now.isoformat(),
            "ttl_sec": ttl_sec,
            "expires_at": preflight.expires_at.isoformat(),
        }

    async def create(self, command: TransferCreateCommand) -> dict[str, object]:
        if not command.source_node_id:
            raise ValueError("source_node_id is required")
        if not command.target_node_id:
            raise ValueError("target_node_id is required")
        if command.source_node_id == command.target_node_id:
            raise ValueError("source_node_id and target_node_id must differ")
        if not command.source_path:
            raise ValueError("source_path is required")
        if not command.target_output_dir and not command.target_path:
            raise ValueError("target_output_dir or target_path is required")
        target_intent = _normalize_transfer_target(
            source_path=command.source_path,
            target_output_dir=command.target_output_dir,
            target_path=command.target_path,
        )

        conflict_mode = command.conflict_mode
        if conflict_mode not in {"fail_if_exists", "overwrite", "reuse_complete"}:
            raise ValueError("conflict_mode must be fail_if_exists, overwrite, or reuse_complete")
        preflight = await self._verify_preflight(command, conflict_mode=conflict_mode)

        session = TransferSession(
            transfer_id=f"trf_{secrets.token_hex(8)}",
            transport="rclone_sftp",
            mode="node_to_node",
            status="created",
            source_node_id=command.source_node_id,
            target_node_id=command.target_node_id,
            source_path=command.source_path,
            target_path=target_intent.target_path,
            target_output_dir=target_intent.output_dir,
            conflict_mode=conflict_mode,
            attempt=1,
            created_by=command.actor_type,
            actor_id=command.actor_id,
            session_id=command.session_id,
            started_at=datetime.now(UTC),
            metadata_json={
                "phase": "transfer_session_v2_rclone_sftp",
                "preflight_id": preflight.preflight_id if preflight else None,
                "skip_preflight": command.skip_preflight,
                "skip_reason": command.skip_reason,
                "cleanup_on_failure": command.cleanup_on_failure,
            },
        )
        self.db.add(session)
        await self.db.flush()
        await self.db.commit()

        expected_size_bytes = (
            _first_int(preflight.source_fact.get("size_bytes"))
            if preflight and isinstance(preflight.source_fact, dict)
            else None
        )

        endpoint_username = f"yequ_{session.transfer_id}"
        endpoint_password = secrets.token_urlsafe(32)
        receive_input: dict[str, object] = {
            "transfer_id": session.transfer_id,
            "output_dir": target_intent.output_dir,
            "target_path": target_intent.target_path,
            "username": endpoint_username,
            "password": endpoint_password,
            "conflict_mode": conflict_mode,
            "timeout_sec": command.timeout_sec,
            "expected_sha256": command.expected_sha256,
            "cleanup_on_failure": command.cleanup_on_failure,
        }
        if expected_size_bytes is not None:
            receive_input["expected_size_bytes"] = expected_size_bytes

        receive_result = await self._invoke_capability(
            command,
            node_id=command.target_node_id,
            capability_ref="transfer.rclone.receive",
            tool_input=receive_input,
        )
        if receive_result.status not in {"created", "running"}:
            session.status = "failed"
            session.error_code = _classify_transfer_error(
                receive_result.error_code or "receiver_job_failed",
                receive_result.error_message,
            )
            session.error_message = receive_result.error_message
            await self.db.commit()
            return self._session_dict(session, receive_result=receive_result)

        session.target_invocation_id = receive_result.invocation_id
        session.target_job_id = receive_result.job_id
        session.status = "receiving"
        await self.db.commit()

        try:
            receiver_start = await self._wait_for_job_start(session.target_job_id)
            if receiver_start and receiver_start.status in {
                "succeeded",
                "failed",
                "timeout",
                "cancelled",
            }:
                session.status = "failed"
                session.error_code = _classify_transfer_error(
                    receiver_start.error_code or "receiver_job_finished_before_sender",
                    receiver_start.error_message,
                )
                session.error_message = receiver_start.error_message or (
                    "receiver job reached terminal state before sender was started"
                )
                await self.db.commit()
                return self._session_dict(
                    session,
                    target_job=receiver_start,
                    receive_result=receive_result,
                )
            receiver_ready = await self._wait_for_receiver_ready(
                session.target_job_id,
                wait_sec=_receiver_ready_wait_sec(command.timeout_sec),
            )
        except asyncio.CancelledError:
            await self._cancel_transfer_create_inflight(session)
            raise

        if receiver_ready is None:
            await self._cancel_job_id(session.target_job_id, reason="receiver_ready_timeout")
            session.status = "failed"
            session.error_code = "receiver_ready_timeout"
            session.error_message = "receiver job disappeared before reporting rclone endpoint"
            await self.db.commit()
            return self._session_dict(session, receive_result=receive_result)
        if receiver_ready.status in {
            "succeeded",
            "failed",
            "timeout",
            "cancelled",
        }:
            session.status = "failed"
            session.error_code = _classify_transfer_error(
                receiver_ready.error_code or "receiver_job_finished_before_sender",
                receiver_ready.error_message,
            )
            session.error_message = receiver_ready.error_message or (
                "receiver job reached terminal state before sender was started"
            )
            await self.db.commit()
            return self._session_dict(
                session,
                target_job=receiver_ready,
                receive_result=receive_result,
            )
        if not _job_has_receiver_ready(receiver_ready):
            await self._cancel_job_id(session.target_job_id, reason="receiver_ready_timeout")
            session.status = "failed"
            session.error_code = "receiver_ready_timeout"
            session.error_message = "receiver did not report rclone endpoint before sender startup"
            await self.db.commit()
            return self._session_dict(
                session,
                target_job=receiver_ready,
                receive_result=receive_result,
            )

        endpoint = _receiver_endpoint(receiver_ready)
        if endpoint is None:
            await self._cancel_job_id(session.target_job_id, reason="receiver_endpoint_missing")
            session.status = "failed"
            session.error_code = "receiver_endpoint_missing"
            session.error_message = "receiver reported ready without a usable rclone endpoint"
            await self.db.commit()
            return self._session_dict(
                session,
                target_job=receiver_ready,
                receive_result=receive_result,
            )
        endpoint["password"] = endpoint_password

        send_input: dict[str, object] = {
            "transfer_id": session.transfer_id,
            "source_path": command.source_path,
            "target_endpoint": endpoint,
            "target_payload_path": _target_payload_path(command.source_path),
            "timeout_sec": command.timeout_sec,
            "expected_sha256": command.expected_sha256,
        }
        if expected_size_bytes is not None:
            send_input["expected_size_bytes"] = expected_size_bytes

        send_result = await self._invoke_capability(
            command,
            node_id=command.source_node_id,
            capability_ref="transfer.rclone.send",
            tool_input=send_input,
        )
        if send_result.status not in {"created", "running"}:
            await self._cancel_job_id(session.target_job_id, reason="sender_job_failed")
            session.status = "failed"
            session.error_code = _classify_transfer_error(
                send_result.error_code or "sender_job_failed",
                send_result.error_message,
            )
            session.error_message = send_result.error_message
            await self.db.commit()
            return self._session_dict(
                session,
                receive_result=receive_result,
                send_result=send_result,
            )

        session.source_invocation_id = send_result.invocation_id
        session.source_job_id = send_result.job_id
        session.status = "running"
        await add_timeline_event(
            self.db,
            TimelineEvent(
                event_type="transfer.session.created",
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                session_id=command.session_id,
                data={
                    "transfer_id": session.transfer_id,
                    "source_node_id": command.source_node_id,
                    "target_node_id": command.target_node_id,
                    "source_job_id": session.source_job_id,
                    "target_job_id": session.target_job_id,
                },
            ),
        )
        await self.db.commit()
        return self._session_dict(session, receive_result=receive_result, send_result=send_result)

    async def status(self, transfer_id: str) -> dict[str, object]:
        session = await self._get_session(transfer_id)
        source_job = await self._get_job(session.source_job_id)
        target_job = await self._get_job(session.target_job_id)
        await self._refresh_status_from_jobs(session, source_job, target_job)
        await self.db.commit()
        return self._session_dict(session, source_job=source_job, target_job=target_job)

    async def cancel(
        self,
        transfer_id: str,
        *,
        reason: str = "transfer_cancelled",
    ) -> dict[str, object]:
        session = await self._get_session(transfer_id)
        for job_id in (session.source_job_id, session.target_job_id):
            job = await self._get_job(job_id)
            if job is None or job.status in {"succeeded", "failed", "timeout", "cancelled"}:
                continue
            try:
                await cancel_job(self.db, job, reason=reason, node_id=job.node_id)
            except ValueError:
                continue
        session.status = "cancelled"
        session.completed_at = datetime.now(UTC)
        await add_timeline_event(
            self.db,
            TimelineEvent(
                event_type="transfer.session.cancelled",
                actor_type=session.created_by,
                actor_id=session.actor_id,
                session_id=session.session_id,
                data={"transfer_id": session.transfer_id, "reason": reason},
            ),
        )
        await self.db.commit()
        return await self.status(transfer_id)

    async def _cancel_job_id(self, job_id: str | None, *, reason: str) -> None:
        job = await self._get_job(job_id)
        if job is None or job.status in {"succeeded", "failed", "timeout", "cancelled"}:
            return
        try:
            await cancel_job(self.db, job, reason=reason, node_id=job.node_id)
        except ValueError:
            return

    async def _cancel_transfer_create_inflight(self, session: TransferSession) -> None:
        await self._cancel_job_id(session.source_job_id, reason="transfer_create_request_cancelled")
        await self._cancel_job_id(session.target_job_id, reason="transfer_create_request_cancelled")
        session.status = "cancelled"
        session.completed_at = datetime.now(UTC)
        session.error_code = "transfer_create_request_cancelled"
        session.error_message = "transfer.create request was cancelled before operation creation"
        await self.db.commit()

    async def _wait_for_job_start(self, job_id: str | None) -> Job | None:
        if not job_id:
            return None
        from yequ.config import get_settings
        from yequ.db import async_session_factory

        if get_settings().test_mode:
            return None

        deadline = datetime.now(UTC) + timedelta(seconds=TRANSFER_JOB_START_WAIT_SEC)
        last_job: Job | None = None
        while datetime.now(UTC) < deadline:
            async with async_session_factory() as db:
                result = await db.execute(select(Job).where(Job.job_id == job_id))
                last_job = result.scalar_one_or_none()
                if last_job is None:
                    return None
                if last_job.status not in {"created", "queued"}:
                    return last_job
            await asyncio.sleep(0.5)
        return last_job

    async def _wait_for_receiver_ready(self, job_id: str | None, *, wait_sec: float) -> Job | None:
        if not job_id:
            return None
        from yequ.config import get_settings
        from yequ.db import async_session_factory

        if get_settings().test_mode:
            return None

        deadline = datetime.now(UTC) + timedelta(seconds=wait_sec)
        last_job: Job | None = None
        terminal = {"succeeded", "failed", "timeout", "cancelled"}
        while datetime.now(UTC) < deadline:
            async with async_session_factory() as db:
                result = await db.execute(select(Job).where(Job.job_id == job_id))
                last_job = result.scalar_one_or_none()
                if last_job is None:
                    return None
                if _job_has_receiver_ready(last_job) or last_job.status in terminal:
                    return last_job
            await asyncio.sleep(0.5)
        return last_job

    async def _verify_preflight(
        self,
        command: TransferCreateCommand,
        *,
        conflict_mode: str,
    ) -> TransferPreflight | None:
        if command.skip_preflight:
            if not command.skip_reason:
                raise ValueError("skip_preflight requires skip_reason")
            return None
        if not command.preflight_id:
            raise ValueError(
                "preflight_required: transfer.preflight must pass before transfer.create"
            )
        target_intent = _normalize_transfer_target(
            source_path=command.source_path,
            target_output_dir=command.target_output_dir,
            target_path=command.target_path,
        )

        result = await self.db.execute(
            select(TransferPreflight).where(
                TransferPreflight.preflight_id == command.preflight_id
            )
        )
        preflight = result.scalar_one_or_none()
        if preflight is None:
            raise ValueError(f"preflight_not_found: {command.preflight_id}")
        expires_at = preflight.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < datetime.now(UTC):
            raise ValueError("preflight_expired: rerun transfer.preflight")
        expected_hash = _transfer_intent_hash(
            source_node_id=command.source_node_id,
            target_node_id=command.target_node_id,
            source_path=command.source_path,
            target_output_dir=target_intent.output_dir,
            target_path=target_intent.target_path,
            conflict_mode=conflict_mode,
        )
        if preflight.intent_hash != expected_hash:
            raise ValueError("preflight_intent_mismatch: rerun transfer.preflight")
        if not preflight.allowed:
            raise ValueError("preflight_failed: rerun transfer.preflight and inspect failures")
        return preflight

    async def _invoke_capability(
        self,
        command: TransferCreateCommand,
        *,
        node_id: str,
        capability_ref: str,
        tool_input: dict[str, object],
    ) -> ExecuteToolResult:
        from yequ.db import async_session_factory
        from yequ.runtime import CenterExecutionRuntime, RuntimeCommand

        clean_input = {key: value for key, value in tool_input.items() if value is not None}
        async with async_session_factory() as db:
            return await CenterExecutionRuntime(db).execute(
                RuntimeCommand(
                    function_name="capability.invoke",
                    input_data={
                        "capability_ref": capability_ref,
                        "node_id": node_id,
                        "input": clean_input,
                    },
                    actor_type=command.actor_type,
                    actor_id=command.actor_id,
                    session_id=command.session_id,
                    execution_mode=command.execution_mode,
                    wait_for_result=False,
                    timeout_sec=command.timeout_sec,
                    lease_sec=_transfer_job_lease_sec(command.timeout_sec),
                    resource_keys=[f"node:{node_id}:transfer"],
                    suppress_operation=True,
                )
            )

    async def _invoke_stat_capability(
        self,
        command: TransferPreflightCommand,
        *,
        node_id: str,
        path: str,
        include_sha256: bool,
    ) -> ExecuteToolResult:
        from yequ.db import async_session_factory
        from yequ.runtime import CenterExecutionRuntime, RuntimeCommand

        async with async_session_factory() as db:
            return await CenterExecutionRuntime(db).execute(
                RuntimeCommand(
                    function_name="capability.invoke",
                    input_data={
                        "capability_ref": "transfer.local.stat",
                        "node_id": node_id,
                        "input": {"path": path, "sha256": include_sha256},
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

    async def _invoke_status_capability(
        self,
        command: TransferPreflightCommand,
        *,
        node_id: str,
    ) -> ExecuteToolResult:
        from yequ.db import async_session_factory
        from yequ.runtime import CenterExecutionRuntime, RuntimeCommand

        async with async_session_factory() as db:
            return await CenterExecutionRuntime(db).execute(
                RuntimeCommand(
                    function_name="capability.invoke",
                    input_data={
                        "capability_ref": "transfer.rclone.status",
                        "node_id": node_id,
                        "input": {},
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

    async def _get_session(self, transfer_id: str) -> TransferSession:
        result = await self.db.execute(
            select(TransferSession).where(TransferSession.transfer_id == transfer_id)
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise ValueError(f"TransferSession {transfer_id!r} not found")
        return session

    async def _get_job(self, job_id: str | None) -> Job | None:
        if not job_id:
            return None
        result = await self.db.execute(select(Job).where(Job.job_id == job_id))
        return result.scalar_one_or_none()

    async def _refresh_status_from_jobs(
        self,
        session: TransferSession,
        source_job: Job | None,
        target_job: Job | None,
    ) -> None:
        jobs = [job for job in (source_job, target_job) if job is not None]
        if len(jobs) < 2:
            return
        statuses = {job.status for job in jobs}
        if "failed" in statuses:
            session.status = "failed"
            await self._cancel_non_terminal_peer(
                jobs,
                reason="transfer_peer_failed",
            )
        elif "timeout" in statuses:
            session.status = "timeout"
            await self._cancel_non_terminal_peer(
                jobs,
                reason="transfer_peer_timeout",
            )
        elif "cancelled" in statuses:
            session.status = "cancelled"
            await self._cancel_non_terminal_peer(
                jobs,
                reason="transfer_peer_cancelled",
            )
        elif statuses == {"succeeded"}:
            session.status = "succeeded"
            session.completed_at = session.completed_at or datetime.now(UTC)
            output = source_job.output if source_job else None
            if isinstance(output, dict):
                if isinstance(output.get("size_bytes"), int):
                    session.size_bytes = output.get("size_bytes")
                if isinstance(output.get("sha256"), str):
                    session.sha256 = output.get("sha256")
        elif "running" in statuses or "claimed" in statuses:
            session.status = "running"
        elif "queued" in statuses or "created" in statuses:
            session.status = "queued"

        failed_job = _select_transfer_failure_job(jobs)
        if failed_job is not None:
            error_message = _transfer_failure_message(failed_job)
            session.error_code = _classify_transfer_error(
                failed_job.error_code,
                error_message,
            )
            session.error_message = error_message
            session.completed_at = session.completed_at or datetime.now(UTC)

    async def _cancel_non_terminal_peer(
        self,
        jobs: list[Job],
        *,
        reason: str,
    ) -> None:
        terminal_statuses = {"succeeded", "failed", "timeout", "cancelled"}
        for job in jobs:
            if job.status in terminal_statuses:
                continue
            try:
                await cancel_job(self.db, job, reason=reason, node_id=job.node_id)
            except ValueError:
                continue

    def _session_dict(
        self,
        session: TransferSession,
        *,
        receive_result: ExecuteToolResult | None = None,
        send_result: ExecuteToolResult | None = None,
        source_job: Job | None = None,
        target_job: Job | None = None,
    ) -> dict[str, object]:
        data: dict[str, object] = {
            "transfer_id": session.transfer_id,
            "transport": session.transport,
            "mode": session.mode,
            "status": session.status,
            "source_node_id": session.source_node_id,
            "target_node_id": session.target_node_id,
            "source_path": session.source_path,
            "target_path": session.target_path,
            "target_output_dir": session.target_output_dir,
            "source_invocation_id": session.source_invocation_id,
            "target_invocation_id": session.target_invocation_id,
            "source_job_id": session.source_job_id,
            "target_job_id": session.target_job_id,
            "conflict_mode": session.conflict_mode,
            "attempt": session.attempt,
            "size_bytes": session.size_bytes,
            "sha256": session.sha256,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "completed_at": session.completed_at.isoformat() if session.completed_at else None,
            "error_code": session.error_code,
            "error_message": session.error_message,
        }
        if receive_result is not None:
            data["receive_result"] = _tool_result_dict(receive_result)
        if send_result is not None:
            data["send_result"] = _tool_result_dict(send_result)
        if source_job is not None:
            data["source_job"] = _job_dict(source_job)
        if target_job is not None:
            data["target_job"] = _job_dict(target_job)
        data["summary"] = _transfer_summary(data)
        return data


def _tool_result_dict(result: ExecuteToolResult) -> dict[str, object]:
    return {
        "status": result.status,
        "function_name": result.function_name,
        "target_node_id": result.target_node_id,
        "invocation_id": result.invocation_id,
        "job_id": result.job_id,
        "error_code": result.error_code,
        "error_message": result.error_message,
    }


def _job_dict(job: Job) -> dict[str, object]:
    return {
        "job_id": job.job_id,
        "node_id": job.node_id,
        "function_name": job.function_name,
        "status": job.status,
        "progress_pct": job.progress_pct,
        "progress_message": job.progress_message,
        "progress_detail": job.progress_detail,
        "output": job.output,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "error_details": job.error_details,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _select_transfer_failure_job(jobs: list[Job]) -> Job | None:
    candidates = [job for job in jobs if job.status in {"failed", "timeout"}]
    if not candidates:
        return None
    return max(candidates, key=_transfer_failure_priority)


def _transfer_failure_priority(job: Job) -> int:
    text = " ".join(
        str(value or "")
        for value in (
            job.error_code,
            job.error_message,
            job.error_details,
        )
    ).lower()
    score = 0
    if job.status == "failed":
        score += 30
    if job.status == "timeout":
        score += 20
    if job.error_code == "function_execution_failed":
        score += 40
    if any(
        marker in text
        for marker in (
            "could not secure channel",
            "secure channel",
            "peer disconnected",
            "output_dir is required",
            "permission denied",
            "rclone transfer failed",
            "rclone receive failed",
            "rclone send failed",
            "connection refused",
            "checksum",
        )
    ):
        score += 80
    if "409 conflict" in text or "already in terminal" in text:
        score -= 100
    return score


def _transfer_failure_message(job: Job) -> str | None:
    base = job.error_message
    detail_message = _transfer_failure_detail_message(job.error_details)
    if detail_message and base and detail_message not in base:
        return f"{base}: {detail_message}"
    return base or detail_message


def _transfer_failure_detail_message(details: object) -> str | None:
    if not isinstance(details, dict):
        return None
    for key in ("stderr", "stdout", "message", "error"):
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _stat_payload(result: ExecuteToolResult) -> dict[str, object]:
    if result.status != "succeeded":
        return {
            "status": result.status,
            "error_code": result.error_code,
            "error_message": result.error_message,
        }
    return dict(result.output_data or {})


def _status_payload(result: ExecuteToolResult) -> dict[str, object]:
    if result.status != "succeeded":
        return {
            "status": result.status,
            "error_code": result.error_code,
            "error_message": result.error_message,
        }
    return dict(result.output_data or {})


def _transfer_preflight_failures(
    *,
    source_result: ExecuteToolResult,
    target_result: ExecuteToolResult,
    source_status_result: ExecuteToolResult,
    target_status_result: ExecuteToolResult,
    source: dict[str, object],
    target: dict[str, object],
    source_status: dict[str, object],
    target_status: dict[str, object],
    target_path: str | None,
    conflict_mode: str,
) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    if source_result.status != "succeeded":
        failures.append(
            {
                "fact": "source.stat",
                "code": source_result.error_code or source_result.status,
                "message": source_result.error_message,
            }
        )
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

    if source_status_result.status != "succeeded":
        failures.append(
            {
                "fact": "source.runtime.status",
                "code": source_status_result.error_code or source_status_result.status,
                "message": source_status_result.error_message,
            }
        )
    if target_status_result.status != "succeeded":
        failures.append(
            {
                "fact": "target.runtime.status",
                "code": target_status_result.error_code or target_status_result.status,
                "message": target_status_result.error_message,
            }
        )
    if failures:
        return failures

    if source.get("found") is not True:
        failures.append({"fact": "source.exists", "code": "source_not_found"})
    if source.get("readable") is not True:
        failures.append({"fact": "source.readable", "code": "source_not_readable"})

    if target.get("parent_exists") is False:
        failures.append(
            {"fact": "target.parent_exists", "code": "target_parent_not_found"}
        )
    if target.get("writable") is not True:
        failures.append({"fact": "target.writable", "code": "target_not_writable"})
    if target_path and conflict_mode == "fail_if_exists" and target.get("found") is True:
        failures.append({"fact": "target.not_exists", "code": "target_exists"})

    source_size = _first_int(source.get("size_bytes"), source.get("size"))
    free_bytes = _first_int(target.get("free_bytes"))
    if source_size is not None and free_bytes is not None and free_bytes < source_size:
        failures.append(
            {
                "fact": "target.free_space",
                "code": "insufficient_space",
                "required_bytes": source_size,
                "available_bytes": free_bytes,
            }
        )

    if source_status.get("installed") is not True:
        failures.append({"fact": "source.runtime.rclone_installed", "code": "rclone_not_installed"})
    if source_status.get("executable") is False:
        failures.append({"fact": "source.runtime.rclone_executable", "code": "rclone_not_executable"})
    if source_status.get("allow_send") is not True:
        failures.append({"fact": "source.runtime.allow_send", "code": "send_not_allowed"})
    if target_status.get("installed") is not True:
        failures.append({"fact": "target.runtime.rclone_installed", "code": "rclone_not_installed"})
    if target_status.get("executable") is False:
        failures.append({"fact": "target.runtime.rclone_executable", "code": "rclone_not_executable"})
    if target_status.get("allow_receive") is not True:
        failures.append({"fact": "target.runtime.allow_receive", "code": "receive_not_allowed"})
    if not _first_str(target_status.get("advertise_host"), target_status.get("host")):
        failures.append({"fact": "target.runtime.advertise_host", "code": "advertise_host_missing"})
    if _first_int(target_status.get("listen_port"), target_status.get("port")) is None:
        failures.append({"fact": "target.runtime.listen_port", "code": "listen_port_missing"})

    return failures


def _transfer_summary(data: dict[str, object]) -> dict[str, object]:
    source_job = data.get("source_job")
    target_job = data.get("target_job")
    source_output = (
        source_job.get("output") if isinstance(source_job, dict) else None
    )
    target_output = (
        target_job.get("output") if isinstance(target_job, dict) else None
    )
    source_output_dict = source_output if isinstance(source_output, dict) else {}
    target_output_dict = target_output if isinstance(target_output, dict) else {}
    source_size = _first_int(
        data.get("size_bytes"),
        source_output_dict.get("size_bytes"),
        source_output_dict.get("size"),
    )
    target_size = _first_int(
        target_output_dict.get("size_bytes"),
        target_output_dict.get("size"),
    )
    source_sha256 = _first_str(
        data.get("sha256"),
        source_output_dict.get("sha256"),
        source_output_dict.get("hash_sha256"),
    )
    target_sha256 = _first_str(
        target_output_dict.get("sha256"),
        target_output_dict.get("hash_sha256"),
    )
    target_path = _first_str(
        data.get("target_path"),
        target_output_dict.get("path"),
        target_output_dict.get("target_path"),
        target_output_dict.get("output_path"),
    )
    if not target_path:
        target_output_dir = _first_str(data.get("target_output_dir"))
        source_path = _first_str(data.get("source_path"))
        if target_output_dir and source_path:
            source_name = source_path.replace("\\", "/").rstrip("/").split("/")[-1]
            target_path = (
                f"{target_output_dir.rstrip('/')}/{source_name}"
                if source_name
                else target_output_dir
            )

    return {
        "source": {
            "node_id": data.get("source_node_id"),
            "path": data.get("source_path"),
            "job_id": data.get("source_job_id"),
            "status": source_job.get("status") if isinstance(source_job, dict) else None,
            "size_bytes": source_size,
            "sha256": source_sha256,
        },
        "target": {
            "node_id": data.get("target_node_id"),
            "path": target_path,
            "output_dir": data.get("target_output_dir"),
            "job_id": data.get("target_job_id"),
            "status": target_job.get("status") if isinstance(target_job, dict) else None,
            "size_bytes": target_size,
            "sha256": target_sha256,
        },
        "verification": {
            "size_match": (
                source_size == target_size
                if source_size is not None and target_size is not None
                else None
            ),
            "sha256_match": (
                source_sha256 == target_sha256
                if source_sha256 and target_sha256
                else None
            ),
        },
        "progress": _transfer_progress(data),
    }


def _transfer_progress(data: dict[str, object]) -> dict[str, object]:
    status = str(data.get("status") or "created")
    source_job = data.get("source_job")
    target_job = data.get("target_job")
    source_progress = _job_progress(source_job if isinstance(source_job, dict) else None)
    target_progress = _job_progress(target_job if isinstance(target_job, dict) else None)
    known_pcts = [
        progress["progress_pct"]
        for progress in (source_progress, target_progress)
        if isinstance(progress.get("progress_pct"), int | float)
    ]
    pct: int | None = None
    if status == "succeeded":
        pct = 100
    elif known_pcts:
        pct = max(0, min(100, round(sum(float(value) for value in known_pcts) / len(known_pcts))))

    bytes_transferred = _max_int(
        source_progress.get("bytes_transferred"),
        target_progress.get("bytes_transferred"),
    )
    total_bytes = _first_int(
        source_progress.get("total_bytes"),
        target_progress.get("total_bytes"),
        data.get("size_bytes"),
    )
    rate_bytes_per_sec = _max_int(
        source_progress.get("rate_bytes_per_sec"),
        target_progress.get("rate_bytes_per_sec"),
    )
    eta_sec = _min_int(source_progress.get("eta_sec"), target_progress.get("eta_sec"))
    last_progress_at = _max_str(
        source_progress.get("last_progress_at"),
        target_progress.get("last_progress_at"),
    )
    phase = _transfer_phase(status, source_progress, target_progress)

    return {
        "phase": phase,
        "pct": pct,
        "message": _transfer_progress_message(status, phase, source_progress, target_progress),
        "source": source_progress,
        "target": target_progress,
        "size_bytes": total_bytes,
        "bytes_transferred": bytes_transferred,
        "rate_bytes_per_sec": rate_bytes_per_sec,
        "eta_sec": eta_sec,
        "last_progress_at": last_progress_at,
    }


def _job_progress(job: dict[str, object] | None) -> dict[str, object]:
    if job is None:
        return {
            "job_id": None,
            "status": None,
            "progress_pct": None,
            "progress_message": None,
        }
    pct = job.get("progress_pct")
    detail = job.get("progress_detail")
    detail_dict = detail if isinstance(detail, dict) else {}
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "progress_pct": pct if isinstance(pct, int | float) else None,
        "progress_message": _first_str(job.get("progress_message")),
        "bytes_transferred": _first_int(detail_dict.get("bytes_transferred")),
        "total_bytes": _first_int(detail_dict.get("total_bytes")),
        "rate_bytes_per_sec": _first_int(detail_dict.get("rate_bytes_per_sec")),
        "eta_sec": _first_int(detail_dict.get("eta_sec")),
        "last_progress_at": _first_str(detail_dict.get("last_progress_at")),
        "progress_source": _first_str(detail_dict.get("progress_source")),
        "phase": _first_str(detail_dict.get("phase")),
    }


def _job_has_receiver_ready(job: Job | None) -> bool:
    if job is None:
        return False
    detail = job.progress_detail
    if not isinstance(detail, dict):
        return False
    if detail.get("receiver_ready") is True:
        return True
    return (
        detail.get("progress_source") == "rclone_receiver_ready"
        or detail.get("phase") == "receiver_ready"
    )


def _receiver_ready_wait_sec(timeout_sec: int | None) -> float:
    timeout = max(float(timeout_sec or 0), 1.0)
    return min(
        TRANSFER_RECEIVER_READY_MAX_WAIT_SEC,
        max(TRANSFER_RECEIVER_READY_MIN_WAIT_SEC, timeout * 0.1),
    )


def _transfer_job_lease_sec(timeout_sec: int | None) -> int:
    timeout = max(int(timeout_sec or 0), 1)
    return min(TRANSFER_JOB_MAX_LEASE_SEC, max(TRANSFER_JOB_MIN_LEASE_SEC, timeout))


def _transfer_progress_message(
    status: str,
    phase: str,
    source_progress: dict[str, object],
    target_progress: dict[str, object],
) -> str:
    if status == "succeeded":
        return "Transfer completed"
    if status == "failed":
        return "Transfer failed"
    if status == "cancelled":
        return "Transfer cancelled"
    if status == "timeout":
        return "Transfer timed out"
    phase_message = {
        "preflighting": "Checking transfer prerequisites",
        "starting_receiver": "Starting receiver",
        "starting_sender": "Starting sender",
        "transferring": "Transferring",
        "verifying": "Verifying transfer",
    }.get(phase)
    source_message = _first_str(source_progress.get("progress_message"))
    target_message = _first_str(target_progress.get("progress_message"))
    if source_message and target_message and source_message != target_message:
        return f"{source_message}; {target_message}"
    if source_message:
        return source_message
    if target_message:
        return target_message
    if phase_message:
        return phase_message
    source_status = _first_str(source_progress.get("status"))
    target_status = _first_str(target_progress.get("status"))
    if source_status == "running" and target_status == "running":
        return "Transferring"
    if source_status == "queued" or target_status == "queued":
        return "Waiting for transfer jobs"
    return "Transfer is running"


def _transfer_phase(
    status: str,
    source_progress: dict[str, object],
    target_progress: dict[str, object],
) -> str:
    if status in {"succeeded", "failed", "cancelled", "timeout"}:
        return status
    source_status = _first_str(source_progress.get("status"))
    target_status = _first_str(target_progress.get("status"))
    source_phase = _first_str(source_progress.get("phase"))
    target_phase = _first_str(target_progress.get("phase"))
    if _phase_indicates_transfer(source_phase) or _phase_indicates_transfer(target_phase):
        return "transferring"
    if source_status == "succeeded" and target_status == "succeeded":
        return "verifying"
    if source_status in {"running", "claimed"} and target_status in {"running", "claimed"}:
        return "transferring"
    if target_status in {"running", "claimed"} and source_status in {None, "created", "queued"}:
        return "starting_sender"
    if target_status in {None, "created", "queued"}:
        return "starting_receiver"
    if source_status in {None, "created", "queued"}:
        return "starting_sender"
    if status in {"created", "queued"}:
        return "starting_receiver"
    return "transferring"


def _phase_indicates_transfer(value: str | None) -> bool:
    return value in {"transferring", "sending", "receiving"}


def _classify_transfer_error(error_code: str | None, error_message: str | None) -> str | None:
    message = (error_message or "").lower()
    if "checksum" in message or "sha256" in message:
        return "integrity_mismatch"
    if "connection refused" in message or "no route to host" in message:
        return "rclone_endpoint_unreachable"
    if "permission denied" in message or "authentication failed" in message:
        return "rclone_auth_failed"
    if "receiver" in message and "ready" in message and "timeout" in message:
        return "receiver_ready_timeout"
    if "timeout" in message:
        return "transfer_timeout"
    return error_code


def _first_int(*values: object) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            return int(value)
    return None


def _max_int(*values: object) -> int | None:
    numbers = [_first_int(value) for value in values]
    present = [value for value in numbers if value is not None]
    return max(present) if present else None


def _min_int(*values: object) -> int | None:
    numbers = [_first_int(value) for value in values]
    present = [value for value in numbers if value is not None]
    return min(present) if present else None


def _first_str(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _max_str(*values: object) -> str | None:
    strings = [value for value in values if isinstance(value, str) and value]
    return max(strings) if strings else None


def _preflight_ttl(value: object) -> int:
    if isinstance(value, bool):
        return TRANSFER_PREFLIGHT_DEFAULT_TTL_SEC
    if isinstance(value, int | float):
        return max(
            TRANSFER_PREFLIGHT_MIN_TTL_SEC,
            min(TRANSFER_PREFLIGHT_MAX_TTL_SEC, int(value)),
        )
    return TRANSFER_PREFLIGHT_DEFAULT_TTL_SEC


def _normalize_transfer_target(
    *,
    source_path: str,
    target_output_dir: str | None,
    target_path: str | None,
) -> NormalizedTransferTarget:
    if target_output_dir:
        return NormalizedTransferTarget(output_dir=target_output_dir, target_path=target_path)

    if not target_path:
        raise ValueError("target_output_dir or target_path is required")

    target_parent = _path_parent(target_path)
    if not target_parent:
        raise ValueError("target_path must include a parent directory")

    return NormalizedTransferTarget(output_dir=target_parent, target_path=target_path)


def _path_name(path: str) -> str:
    return _pure_path(path).name


def _path_parent(path: str) -> str:
    pure = _pure_path(path)
    parent = str(pure.parent)
    if parent in {"", "."}:
        return ""
    return parent


def _pure_path(path: str) -> PurePosixPath | PureWindowsPath:
    if "\\" in path or _looks_like_windows_drive(path):
        return PureWindowsPath(path)
    return PurePosixPath(path)


def _looks_like_windows_drive(path: str) -> bool:
    return len(path) >= 2 and path[1] == ":"


def _transfer_intent_hash(
    *,
    source_node_id: str,
    target_node_id: str,
    source_path: str,
    target_output_dir: str | None,
    target_path: str | None,
    conflict_mode: str,
) -> str:
    payload = {
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "source_path": source_path,
        "target_output_dir": target_output_dir,
        "target_path": target_path,
        "conflict_mode": conflict_mode,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _target_payload_path(source_path: str) -> str:
    source_name = _path_name(source_path) or "source"
    return f"payload/{source_name}"


def _receiver_endpoint(job: Job | None) -> dict[str, object] | None:
    if job is None or not isinstance(job.progress_detail, dict):
        return None
    detail = job.progress_detail
    endpoint = detail.get("endpoint")
    if isinstance(endpoint, dict):
        host = _first_str(endpoint.get("host"), endpoint.get("advertise_host"))
        port = _first_int(endpoint.get("port"), endpoint.get("listen_port"))
        username = _first_str(endpoint.get("username"))
    else:
        host = _first_str(detail.get("host"), detail.get("advertise_host"))
        port = _first_int(detail.get("port"), detail.get("listen_port"))
        username = _first_str(detail.get("username"))
    if not host or port is None or not username:
        return None
    return {
        "host": host,
        "port": port,
        "username": username,
        "remote_dir": _first_str(
            endpoint.get("remote_dir") if isinstance(endpoint, dict) else None,
            detail.get("remote_dir"),
            "payload",
        ),
    }
