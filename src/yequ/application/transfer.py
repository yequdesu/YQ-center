"""Application service for Center-managed transfer sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath
from types import SimpleNamespace
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.application.schemas import ExecuteToolResult
from yequ.models.job import Job
from yequ.models.timeline import TimelineEvent
from yequ.models.transfer import TransferAttempt, TransferPreflight, TransferSession
from yequ.services.job_service import cancel_job
from yequ.services.timeline_writer import add_timeline_event

TRANSFER_PREFLIGHT_DEFAULT_TTL_SEC = 120
TRANSFER_PREFLIGHT_MIN_TTL_SEC = 30
TRANSFER_PREFLIGHT_MAX_TTL_SEC = 300
TRANSFER_SENDER_START_WAIT_SEC = 8.0
TRANSFER_SENDER_READY_MIN_WAIT_SEC = 180.0
TRANSFER_SENDER_READY_MAX_WAIT_SEC = 600.0
TRANSFER_JOB_MIN_LEASE_SEC = 180
TRANSFER_JOB_MAX_LEASE_SEC = 600


@dataclass(slots=True)
class TransferCreateCommand:
    source_node_id: str
    target_node_id: str
    source_path: str
    target_output_dir: str | None = None
    target_path: str | None = None
    code: str | None = None
    relay_url: str | None = None
    resume_mode: str | None = None
    timeout_sec: int = 3600
    expected_sha256: str | None = None
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
    relay_url: str | None = None
    resume_mode: str | None = None
    include_sha256: bool = False
    timeout_sec: int = 20
    ttl_sec: int = TRANSFER_PREFLIGHT_DEFAULT_TTL_SEC
    actor_type: str = "agent"
    actor_id: str = "agent"
    session_id: str | None = None
    execution_mode: str = "auto"


@dataclass(slots=True)
class TransferResumeCommand:
    transfer_id: str
    code: str | None = None
    relay_url: str | None = None
    timeout_sec: int | None = None
    actor_type: str = "agent"
    actor_id: str = "agent"
    session_id: str | None = None
    execution_mode: str = "auto"


@dataclass(frozen=True, slots=True)
class NormalizedTransferTarget:
    output_dir: str
    target_path: str | None


class TransferApplicationService:
    """Create and inspect transfer sessions without exposing croc sequencing to Agent."""

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
        resume_mode = command.resume_mode
        if resume_mode not in {"resume", "overwrite", "fail_if_exists"}:
            missing.append("resume_mode")
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
            resume_mode=resume_mode,
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
                relay_url=command.relay_url,
                resume_mode=resume_mode,
            ),
            source_node_id=command.source_node_id,
            target_node_id=command.target_node_id,
            source_path=command.source_path,
            target_output_dir=target_intent.output_dir,
            target_path=target_intent.target_path,
            resume_mode=resume_mode,
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
            "relay_url": command.relay_url,
            "resume_mode": resume_mode,
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

        code = command.code or _generate_croc_code()
        resume_mode = command.resume_mode
        if resume_mode not in {"resume", "overwrite", "fail_if_exists"}:
            raise ValueError("resume_mode must be resume, overwrite, or fail_if_exists")
        preflight = await self._verify_preflight(command, resume_mode=resume_mode)

        session = TransferSession(
            transfer_id=f"trf_{secrets.token_hex(8)}",
            transport="croc",
            mode="node_to_node",
            status="created",
            source_node_id=command.source_node_id,
            target_node_id=command.target_node_id,
            source_path=command.source_path,
            target_path=target_intent.target_path,
            target_output_dir=target_intent.output_dir,
            relay_url=command.relay_url,
            code_hash=_secret_hash(code),
            resume_mode=resume_mode,
            attempt=1,
            created_by=command.actor_type,
            actor_id=command.actor_id,
            session_id=command.session_id,
            started_at=datetime.now(UTC),
            metadata_json={
                "phase": "transfer_session_v1",
                "preflight_id": preflight.preflight_id if preflight else None,
                "skip_preflight": command.skip_preflight,
                "skip_reason": command.skip_reason,
            },
        )
        self.db.add(session)
        await self.db.flush()
        attempt = TransferAttempt(
            transfer_session_id=session.id,
            transfer_id=session.transfer_id,
            attempt=1,
            code_hash=session.code_hash,
            relay_mode="configured" if command.relay_url else "public_default",
            relay_url_masked=_mask_relay_url(command.relay_url),
            status="created",
            resumable=False,
            started_at=session.started_at,
            metadata_json={
                "runtime": "yq-croc",
                "resume_mode": resume_mode,
            },
        )
        self.db.add(attempt)
        await self.db.commit()

        expected_size_bytes = (
            _first_int(preflight.source_fact.get("size_bytes"))
            if preflight and isinstance(preflight.source_fact, dict)
            else None
        )

        send_result = await self._invoke_capability(
            command,
            node_id=command.source_node_id,
            capability_ref="transfer.croc.send",
            tool_input={
                "transfer_id": session.transfer_id,
                "attempt": session.attempt,
                "code": code,
                "source_path": command.source_path,
                "relay_url": command.relay_url,
                "timeout_sec": command.timeout_sec,
                "resume_mode": resume_mode,
            },
        )
        if send_result.status not in {"created", "running"}:
            session.status = "failed"
            session.error_code = _classify_transfer_error(
                send_result.error_code or "sender_job_failed",
                send_result.error_message,
            )
            session.error_message = send_result.error_message
            await self.db.commit()
            return self._session_dict(session, send_result=send_result)

        session.source_invocation_id = send_result.invocation_id
        session.source_job_id = send_result.job_id
        attempt.source_job_id = send_result.job_id
        attempt.status = "sending"
        session.status = "sending"
        await self.db.commit()

        try:
            sender_start = await self._wait_for_job_start(session.source_job_id)
            if sender_start and sender_start.status in {
                "succeeded",
                "failed",
                "timeout",
                "cancelled",
            }:
                session.status = "failed"
                session.error_code = _classify_transfer_error(
                    sender_start.error_code or "sender_job_finished_before_receiver",
                    sender_start.error_message,
                )
                session.error_message = sender_start.error_message or (
                    "sender job reached terminal state before receiver was started"
                )
                attempt.status = "failed"
                attempt.error_code = session.error_code
                attempt.error_message = session.error_message
                attempt.completed_at = datetime.now(UTC)
                await self.db.commit()
                return self._session_dict(session, source_job=sender_start, send_result=send_result)
            sender_ready = await self._wait_for_sender_ready(
                session.source_job_id,
                wait_sec=_sender_ready_wait_sec(command.timeout_sec),
            )
        except asyncio.CancelledError:
            await self._cancel_transfer_create_inflight(session)
            raise

        if sender_ready is None:
            await self._cancel_job_id(session.source_job_id, reason="sender_room_ready_timeout")
            session.status = "failed"
            session.error_code = "sender_room_ready_timeout"
            session.error_message = "sender job disappeared before reporting yq-croc sender_ready"
            attempt.status = "failed"
            attempt.error_code = session.error_code
            attempt.error_message = session.error_message
            attempt.completed_at = datetime.now(UTC)
            await self.db.commit()
            return self._session_dict(session, send_result=send_result)
        if sender_ready.status in {
            "succeeded",
            "failed",
            "timeout",
            "cancelled",
        }:
            session.status = "failed"
            session.error_code = _classify_transfer_error(
                sender_ready.error_code or "sender_job_finished_before_receiver",
                sender_ready.error_message,
            )
            session.error_message = sender_ready.error_message or (
                "sender job reached terminal state before receiver was started"
            )
            attempt.status = "failed"
            attempt.error_code = session.error_code
            attempt.error_message = session.error_message
            attempt.completed_at = datetime.now(UTC)
            await self.db.commit()
            return self._session_dict(session, source_job=sender_ready, send_result=send_result)
        if not _job_has_sender_ready(sender_ready):
            await self._cancel_job_id(session.source_job_id, reason="sender_room_ready_timeout")
            session.status = "failed"
            session.error_code = "sender_room_ready_timeout"
            session.error_message = (
                "sender did not report yq-croc sender_ready before receiver startup"
            )
            attempt.status = "failed"
            attempt.error_code = session.error_code
            attempt.error_message = session.error_message
            attempt.completed_at = datetime.now(UTC)
            await self.db.commit()
            return self._session_dict(session, source_job=sender_ready, send_result=send_result)

        receive_input: dict[str, object] = {
            "transfer_id": session.transfer_id,
            "attempt": session.attempt,
            "code": code,
            "output_dir": target_intent.output_dir,
            "target_path": target_intent.target_path,
            "relay_url": command.relay_url,
            "timeout_sec": command.timeout_sec,
            "resume_mode": resume_mode,
            "expected_sha256": command.expected_sha256,
        }
        if expected_size_bytes is not None:
            receive_input["expected_size_bytes"] = expected_size_bytes

        receive_result = await self._invoke_capability(
            command,
            node_id=command.target_node_id,
            capability_ref="transfer.croc.receive",
            tool_input=receive_input,
        )
        if receive_result.status not in {"created", "running"}:
            await self._cancel_job_id(session.source_job_id, reason="receiver_job_failed")
            session.status = "failed"
            session.error_code = _classify_transfer_error(
                receive_result.error_code or "receiver_job_failed",
                receive_result.error_message,
            )
            session.error_message = receive_result.error_message
            attempt.status = "failed"
            attempt.target_job_id = receive_result.job_id
            attempt.error_code = session.error_code
            attempt.error_message = session.error_message
            attempt.completed_at = datetime.now(UTC)
            await self.db.commit()
            return self._session_dict(
                session,
                receive_result=receive_result,
                send_result=send_result,
            )

        session.target_invocation_id = receive_result.invocation_id
        session.target_job_id = receive_result.job_id
        attempt.target_job_id = receive_result.job_id
        attempt.status = "running"
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

    async def resume(self, command: TransferResumeCommand) -> dict[str, object]:
        session = await self._get_session(command.transfer_id)
        source_job = await self._get_job(session.source_job_id)
        target_job = await self._get_job(session.target_job_id)
        await self._refresh_status_from_jobs(session, source_job, target_job)
        if not _session_resumable(session, source_job=source_job, target_job=target_job):
            await self.db.commit()
            raise ValueError(
                "transfer_not_resumable: session must be interrupted "
                "and contain resumable node facts"
            )

        old_jobs = [job for job in (source_job, target_job) if job is not None]
        non_terminal = [
            job.job_id
            for job in old_jobs
            if job.status not in {"succeeded", "failed", "timeout", "cancelled"}
        ]
        if non_terminal:
            raise ValueError(
                "transfer_resume_blocked: previous attempt still has non-terminal jobs"
            )

        code = command.code or _generate_croc_code()
        relay_url = command.relay_url or session.relay_url
        timeout_sec = command.timeout_sec or 3600
        next_attempt = int(session.attempt or 1) + 1
        resume_mode = "resume"
        target_intent = NormalizedTransferTarget(
            output_dir=session.target_output_dir
            or _target_output_dir_from_path(session.target_path),
            target_path=session.target_path,
        )

        session.attempt = next_attempt
        session.relay_url = relay_url
        session.code_hash = _secret_hash(code)
        session.resume_mode = resume_mode
        session.status = "created"
        session.error_code = None
        session.error_message = None
        session.completed_at = None
        session.started_at = datetime.now(UTC)

        attempt = TransferAttempt(
            transfer_session_id=session.id,
            transfer_id=session.transfer_id,
            attempt=next_attempt,
            code_hash=session.code_hash,
            relay_mode="configured" if relay_url else "public_default",
            relay_url_masked=_mask_relay_url(relay_url),
            status="created",
            resumable=True,
            started_at=session.started_at,
            metadata_json={
                "runtime": "yq-croc",
                "resume_mode": resume_mode,
                "resumed_from_attempt": next_attempt - 1,
            },
        )
        self.db.add(attempt)
        await self.db.commit()

        command_for_attempt = TransferCreateCommand(
            source_node_id=session.source_node_id,
            target_node_id=session.target_node_id,
            source_path=session.source_path,
            target_output_dir=target_intent.output_dir,
            target_path=target_intent.target_path,
            code=code,
            relay_url=relay_url,
            resume_mode=resume_mode,
            timeout_sec=timeout_sec,
            expected_sha256=session.sha256,
            skip_preflight=True,
            skip_reason="transfer.resume uses existing TransferSession facts",
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            session_id=command.session_id or session.session_id,
            execution_mode=command.execution_mode,
        )

        expected_size_bytes = session.size_bytes
        send_result = await self._invoke_capability(
            command_for_attempt,
            node_id=session.source_node_id,
            capability_ref="transfer.croc.send",
            tool_input={
                "transfer_id": session.transfer_id,
                "attempt": next_attempt,
                "code": code,
                "source_path": session.source_path,
                "relay_url": relay_url,
                "timeout_sec": timeout_sec,
                "resume_mode": resume_mode,
            },
        )
        if send_result.status not in {"created", "running"}:
            session.status = "failed"
            session.error_code = _classify_transfer_error(
                send_result.error_code or "sender_job_failed",
                send_result.error_message,
            )
            session.error_message = send_result.error_message
            attempt.status = "failed"
            attempt.error_code = session.error_code
            attempt.error_message = session.error_message
            attempt.completed_at = datetime.now(UTC)
            await self.db.commit()
            return self._session_dict(session, send_result=send_result)

        session.source_invocation_id = send_result.invocation_id
        session.source_job_id = send_result.job_id
        attempt.source_job_id = send_result.job_id
        attempt.status = "sending"
        session.status = "sending"
        await self.db.commit()

        sender_ready = await self._wait_for_sender_ready(
            session.source_job_id,
            wait_sec=_sender_ready_wait_sec(timeout_sec),
        )
        if sender_ready is None or not _job_has_sender_ready(sender_ready):
            await self._cancel_job_id(session.source_job_id, reason="sender_room_ready_timeout")
            session.status = "failed"
            session.error_code = "sender_room_ready_timeout"
            session.error_message = (
                "sender did not report yq-croc sender_ready before receiver startup"
            )
            attempt.status = "failed"
            attempt.error_code = session.error_code
            attempt.error_message = session.error_message
            attempt.completed_at = datetime.now(UTC)
            await self.db.commit()
            return self._session_dict(session, source_job=sender_ready, send_result=send_result)
        if sender_ready.status in {"succeeded", "failed", "timeout", "cancelled"}:
            session.status = "failed"
            session.error_code = _classify_transfer_error(
                sender_ready.error_code or "sender_job_finished_before_receiver",
                sender_ready.error_message,
            )
            session.error_message = sender_ready.error_message or (
                "sender job reached terminal state before receiver was started"
            )
            attempt.status = "failed"
            attempt.error_code = session.error_code
            attempt.error_message = session.error_message
            attempt.completed_at = datetime.now(UTC)
            await self.db.commit()
            return self._session_dict(session, source_job=sender_ready, send_result=send_result)

        receive_input: dict[str, object] = {
            "transfer_id": session.transfer_id,
            "attempt": next_attempt,
            "code": code,
            "output_dir": target_intent.output_dir,
            "target_path": target_intent.target_path,
            "relay_url": relay_url,
            "timeout_sec": timeout_sec,
            "resume_mode": resume_mode,
            "expected_sha256": session.sha256,
        }
        if expected_size_bytes is not None:
            receive_input["expected_size_bytes"] = expected_size_bytes

        receive_result = await self._invoke_capability(
            command_for_attempt,
            node_id=session.target_node_id,
            capability_ref="transfer.croc.receive",
            tool_input=receive_input,
        )
        if receive_result.status not in {"created", "running"}:
            await self._cancel_job_id(session.source_job_id, reason="receiver_job_failed")
            session.status = "failed"
            session.error_code = _classify_transfer_error(
                receive_result.error_code or "receiver_job_failed",
                receive_result.error_message,
            )
            session.error_message = receive_result.error_message
            attempt.status = "failed"
            attempt.target_job_id = receive_result.job_id
            attempt.error_code = session.error_code
            attempt.error_message = session.error_message
            attempt.completed_at = datetime.now(UTC)
            await self.db.commit()
            return self._session_dict(
                session,
                receive_result=receive_result,
                send_result=send_result,
            )

        session.target_invocation_id = receive_result.invocation_id
        session.target_job_id = receive_result.job_id
        attempt.target_job_id = receive_result.job_id
        attempt.status = "running"
        session.status = "running"
        await add_timeline_event(
            self.db,
            TimelineEvent(
                event_type="transfer.session.resumed",
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                session_id=command.session_id or session.session_id,
                data={
                    "transfer_id": session.transfer_id,
                    "attempt": next_attempt,
                    "source_job_id": session.source_job_id,
                    "target_job_id": session.target_job_id,
                    "relay_mode": "configured" if relay_url else "public_default",
                    "relay_url_masked": _mask_relay_url(relay_url),
                },
            ),
        )
        await self.db.commit()
        return self._session_dict(session, receive_result=receive_result, send_result=send_result)

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

        deadline = datetime.now(UTC) + timedelta(seconds=TRANSFER_SENDER_START_WAIT_SEC)
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

    async def _wait_for_sender_ready(self, job_id: str | None, *, wait_sec: float) -> Job | None:
        if not job_id:
            return None
        from yequ.config import get_settings
        from yequ.db import async_session_factory

        if get_settings().test_mode:
            job = await self._get_job(job_id)
            if job is None:
                return cast(
                    Job,
                    SimpleNamespace(
                        job_id=job_id,
                        status="running",
                        progress_detail={
                            "progress_source": "yq_croc_event",
                            "event": "sender_ready",
                            "role": "sender",
                            "phase": "sender_ready",
                            "sender_ready": True,
                        },
                        error_code=None,
                        error_message=None,
                    ),
                )
            if job is not None:
                detail = dict(job.progress_detail) if isinstance(job.progress_detail, dict) else {}
                detail.update(
                    {
                        "progress_source": "yq_croc_event",
                        "event": "sender_ready",
                        "role": "sender",
                        "phase": "sender_ready",
                        "sender_ready": True,
                    }
                )
                job.progress_detail = detail
            return job

        deadline = datetime.now(UTC) + timedelta(seconds=wait_sec)
        last_job: Job | None = None
        terminal = {"succeeded", "failed", "timeout", "cancelled"}
        while datetime.now(UTC) < deadline:
            async with async_session_factory() as db:
                result = await db.execute(select(Job).where(Job.job_id == job_id))
                last_job = result.scalar_one_or_none()
                if last_job is None:
                    return None
                if _job_has_sender_ready(last_job) or last_job.status in terminal:
                    return last_job
            await asyncio.sleep(0.5)
        return last_job

    async def _verify_preflight(
        self,
        command: TransferCreateCommand,
        *,
        resume_mode: str,
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
            select(TransferPreflight).where(TransferPreflight.preflight_id == command.preflight_id)
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
            relay_url=command.relay_url,
            resume_mode=resume_mode,
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
                        "capability_ref": "transfer.croc.status",
                        "node_id": node_id,
                        "input": (
                            {"relay_url": command.relay_url}
                            if command.relay_url is not None
                            else {}
                        ),
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
            failed_job = _select_transfer_failure_job(jobs)
            session.status = "interrupted" if _job_reports_resumable(failed_job) else "failed"
            await self._cancel_non_terminal_peer(
                jobs,
                reason="transfer_peer_failed",
            )
        elif "timeout" in statuses:
            failed_job = _select_transfer_failure_job(jobs)
            session.status = "interrupted" if _job_reports_resumable(failed_job) else "timeout"
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
            "resume_mode": session.resume_mode,
            "attempt": session.attempt,
            "size_bytes": session.size_bytes,
            "sha256": session.sha256,
            "relay_url": session.relay_url,
            "code_hash": session.code_hash,
            "resumable": _session_resumable(session, source_job=source_job, target_job=target_job),
            "last_resumable_error": _session_last_resumable_error(
                session,
                source_job=source_job,
                target_job=target_job,
            ),
            "resume_hint": _session_resume_hint(session),
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
            "croc transfer failed",
            "croc receive failed",
            "croc send failed",
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
    resume_mode: str,
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
        failures.append({"fact": "target.parent_exists", "code": "target_parent_not_found"})
    if target.get("writable") is not True:
        failures.append({"fact": "target.writable", "code": "target_not_writable"})
    if target_path and resume_mode == "fail_if_exists" and target.get("found") is True:
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

    if source_status.get("runtime") != "yq-croc":
        failures.append({"fact": "source.runtime.yq_croc", "code": "wrong_transfer_runtime"})
    if target_status.get("runtime") != "yq-croc":
        failures.append({"fact": "target.runtime.yq_croc", "code": "wrong_transfer_runtime"})
    if source_status.get("installed") is not True:
        failures.append(
            {"fact": "source.runtime.yq_croc_installed", "code": "yq_croc_not_installed"}
        )
    if source_status.get("executable") is not True:
        failures.append(
            {"fact": "source.runtime.yq_croc_executable", "code": "yq_croc_not_executable"}
        )
    if source_status.get("relay_reachable") is not True:
        failures.append({"fact": "source.runtime.relay_reachable", "code": "relay_unreachable"})
    if source_status.get("firewall_allows_outbound") is False:
        failures.append(
            {
                "fact": "source.runtime.firewall_allows_outbound",
                "code": "runtime_egress_blocked",
            }
        )
    if source_status.get("allow_send") is not True:
        failures.append({"fact": "source.runtime.allow_send", "code": "send_not_allowed"})
    if target_status.get("installed") is not True:
        failures.append(
            {"fact": "target.runtime.yq_croc_installed", "code": "yq_croc_not_installed"}
        )
    if target_status.get("executable") is not True:
        failures.append(
            {"fact": "target.runtime.yq_croc_executable", "code": "yq_croc_not_executable"}
        )
    if target_status.get("relay_reachable") is not True:
        failures.append({"fact": "target.runtime.relay_reachable", "code": "relay_unreachable"})
    if target_status.get("firewall_allows_outbound") is False:
        failures.append(
            {
                "fact": "target.runtime.firewall_allows_outbound",
                "code": "runtime_egress_blocked",
            }
        )
    if target_status.get("allow_receive") is not True:
        failures.append({"fact": "target.runtime.allow_receive", "code": "receive_not_allowed"})

    return failures


def _transfer_summary(data: dict[str, object]) -> dict[str, object]:
    source_job = data.get("source_job")
    target_job = data.get("target_job")
    source_output = source_job.get("output") if isinstance(source_job, dict) else None
    target_output = target_job.get("output") if isinstance(target_job, dict) else None
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
                source_sha256 == target_sha256 if source_sha256 and target_sha256 else None
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


def _job_has_sender_ready(job: Job | None) -> bool:
    if job is None:
        return False
    detail = job.progress_detail
    if not isinstance(detail, dict):
        return False
    if (
        detail.get("role") == "sender"
        and detail.get("progress_source") == "yq_croc_event"
        and detail.get("sender_ready") is True
    ):
        return True
    return (
        detail.get("role") == "sender"
        and detail.get("progress_source") == "yq_croc_event"
        and detail.get("event") == "sender_ready"
    )


def _sender_ready_wait_sec(timeout_sec: int | None) -> float:
    timeout = max(float(timeout_sec or 0), 1.0)
    return min(
        TRANSFER_SENDER_READY_MAX_WAIT_SEC,
        max(TRANSFER_SENDER_READY_MIN_WAIT_SEC, timeout * 0.1),
    )


def _transfer_job_lease_sec(timeout_sec: int | None) -> int:
    timeout = max(int(timeout_sec or 0), 1)
    return min(TRANSFER_JOB_MAX_LEASE_SEC, max(TRANSFER_JOB_MIN_LEASE_SEC, timeout))


def _mask_relay_url(relay_url: str | None) -> str | None:
    if not relay_url:
        return None
    if "@" not in relay_url:
        return relay_url
    return relay_url.rsplit("@", 1)[-1]


def _target_output_dir_from_path(target_path: str | None) -> str:
    if not target_path:
        raise ValueError("transfer session has neither target_output_dir nor target_path")
    if "\\" in target_path or ":" in target_path:
        return str(PureWindowsPath(target_path).parent)
    return str(PurePosixPath(target_path).parent)


def _session_resumable(
    session: TransferSession,
    *,
    source_job: Job | None,
    target_job: Job | None,
) -> bool:
    if session.status != "interrupted":
        return False
    return any(_job_reports_resumable(job) for job in (source_job, target_job))


def _job_reports_resumable(job: Job | None) -> bool:
    if job is None:
        return False
    detail = job.progress_detail
    if isinstance(detail, dict) and detail.get("resumable") is True:
        return True
    output = job.output
    if isinstance(output, dict) and output.get("resumable") is True:
        return True
    error_details = job.error_details
    return isinstance(error_details, dict) and error_details.get("resumable") is True


def _session_last_resumable_error(
    session: TransferSession,
    *,
    source_job: Job | None,
    target_job: Job | None,
) -> str | None:
    if not _session_resumable(session, source_job=source_job, target_job=target_job):
        return None
    for job in (target_job, source_job):
        if job is not None and job.error_code:
            return job.error_code
    return session.error_code


def _session_resume_hint(session: TransferSession) -> str | None:
    if session.status != "interrupted":
        return None
    return "Call transfer.resume for the same TransferSession; Center will create the next attempt."


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
    if "could not secure channel" in message:
        return "croc_secure_channel_failed"
    if "secure channel" in message and "not ready" in message:
        return "croc_secure_channel_not_ready"
    if "peer disconnected" in message or "maybe peer disconnected" in message:
        return "croc_peer_disconnected"
    if "relay" in message and ("unreachable" in message or "connect" in message):
        return "croc_relay_unreachable"
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

    source_name = _path_name(source_path)
    target_name = _path_name(target_path)
    if source_name and target_name and source_name != target_name:
        raise ValueError(
            "target_path filename must match source filename for croc transfer; "
            "use target_output_dir to choose a landing directory"
        )

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
    relay_url: str | None,
    resume_mode: str,
) -> str:
    payload = {
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "source_path": source_path,
        "target_output_dir": target_output_dir,
        "target_path": target_path,
        "relay_url": relay_url,
        "resume_mode": resume_mode,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_croc_code() -> str:
    return f"yequ-{secrets.token_urlsafe(12).replace('_', '').replace('-', '')[:16]}"


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
