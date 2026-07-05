"""Application service for Center-managed transfer sessions."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.application.schemas import ExecuteToolResult
from yequ.application.transfer_helpers import (
    TRANSFER_PREFLIGHT_DEFAULT_TTL_SEC,
    TRANSFER_SENDER_START_WAIT_SEC,
    NormalizedTransferTarget,
    _classify_transfer_error,
    _first_int,
    _generate_croc_code,
    _job_dict,
    _job_has_sender_ready,
    _job_reports_resumable,
    _mask_relay_url,
    _normalize_route_policy,
    _normalize_transfer_target,
    _preflight_ttl,
    _relay_mode,
    _secret_hash,
    _select_transfer_failure_job,
    _sender_ready_wait_sec,
    _session_last_resumable_error,
    _session_resumable,
    _session_resume_hint,
    _stat_payload,
    _status_payload,
    _target_output_dir_from_path,
    _tool_result_dict,
    _transfer_failure_message,
    _transfer_intent_hash,
    _transfer_job_lease_sec,
    _transfer_preflight_failures,
    _transfer_summary,
    _validate_route_policy_fields,
)
from yequ.models.job import Job
from yequ.models.timeline import TimelineEvent
from yequ.models.transfer import TransferAttempt, TransferPreflight, TransferSession
from yequ.services.job_service import cancel_job
from yequ.services.timeline_writer import add_timeline_event


@dataclass(slots=True)
class TransferCreateCommand:
    source_node_id: str
    target_node_id: str
    source_path: str
    target_output_dir: str | None = None
    target_path: str | None = None
    code: str | None = None
    relay_url: str | None = None
    route_policy: str | None = None
    direct_ip: str | None = None
    multicast_address: str | None = None
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
    route_policy: str | None = None
    direct_ip: str | None = None
    multicast_address: str | None = None
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
    route_policy: str | None = None
    direct_ip: str | None = None
    multicast_address: str | None = None
    timeout_sec: int | None = None
    actor_type: str = "agent"
    actor_id: str = "agent"
    session_id: str | None = None
    execution_mode: str = "auto"




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
        route_policy = _normalize_route_policy(command.route_policy)
        if route_policy == "direct_ip" and not command.direct_ip:
            missing.append("direct_ip")
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
            route_policy=route_policy,
            direct_ip=command.direct_ip,
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
                route_policy=route_policy,
                direct_ip=command.direct_ip,
                multicast_address=command.multicast_address,
                resume_mode=resume_mode,
            ),
            source_node_id=command.source_node_id,
            target_node_id=command.target_node_id,
            source_path=command.source_path,
            target_output_dir=target_intent.output_dir,
            target_path=target_intent.target_path,
            resume_mode=resume_mode,
            route_policy=route_policy,
            direct_ip=command.direct_ip,
            multicast_address=command.multicast_address,
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
            "route_policy": route_policy,
            "direct_ip": command.direct_ip,
            "multicast_address": command.multicast_address,
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
        route_policy = _normalize_route_policy(command.route_policy)
        _validate_route_policy_fields(route_policy, direct_ip=command.direct_ip)
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
            route_policy=route_policy,
            direct_ip=command.direct_ip,
            multicast_address=command.multicast_address,
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
            relay_mode=_relay_mode(route_policy, command.relay_url),
            relay_url_masked=_mask_relay_url(command.relay_url),
            route_policy=route_policy,
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
                "route_policy": route_policy,
                "direct_ip": command.direct_ip,
                "multicast_address": command.multicast_address,
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
            "route_policy": route_policy,
            "direct_ip": command.direct_ip,
            "multicast_address": command.multicast_address,
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
                    "relay_mode": _relay_mode(route_policy, command.relay_url),
                    "relay_url_masked": _mask_relay_url(command.relay_url),
                    "route_policy": route_policy,
                    "direct_ip": command.direct_ip,
                    "multicast_address": command.multicast_address,
                },
            ),
        )
        await self.db.commit()
        return self._session_dict(session, receive_result=receive_result, send_result=send_result)

    async def status(self, transfer_id: str, *, projection: str = "detail") -> dict[str, object]:
        session = await self._get_session(transfer_id)
        source_job = await self._get_job(session.source_job_id)
        target_job = await self._get_job(session.target_job_id)
        await self._refresh_status_from_jobs(session, source_job, target_job)
        await self.db.commit()
        detail = self._session_dict(session, source_job=source_job, target_job=target_job)
        if projection == "summary":
            return transfer_session_summary(detail)
        return detail

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
        route_policy = _normalize_route_policy(command.route_policy or session.route_policy)
        direct_ip = command.direct_ip or session.direct_ip
        multicast_address = command.multicast_address or session.multicast_address
        _validate_route_policy_fields(route_policy, direct_ip=direct_ip)
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
        session.route_policy = route_policy
        session.direct_ip = direct_ip
        session.multicast_address = multicast_address
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
            relay_mode=_relay_mode(route_policy, relay_url),
            relay_url_masked=_mask_relay_url(relay_url),
            route_policy=route_policy,
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
            route_policy=route_policy,
            direct_ip=direct_ip,
            multicast_address=multicast_address,
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
                "route_policy": route_policy,
                "direct_ip": direct_ip,
                "multicast_address": multicast_address,
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
            "route_policy": route_policy,
            "direct_ip": direct_ip,
            "multicast_address": multicast_address,
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
                    "relay_mode": _relay_mode(route_policy, relay_url),
                    "relay_url_masked": _mask_relay_url(relay_url),
                    "route_policy": route_policy,
                    "direct_ip": direct_ip,
                    "multicast_address": multicast_address,
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
        route_policy = _normalize_route_policy(command.route_policy)

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
            route_policy=route_policy,
            direct_ip=command.direct_ip,
            multicast_address=command.multicast_address,
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
            "route_policy": session.route_policy,
            "direct_ip": session.direct_ip,
            "multicast_address": session.multicast_address,
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


def transfer_session_summary(transfer: dict[str, object]) -> dict[str, object]:
    source_job = transfer.get("source_job")
    target_job = transfer.get("target_job")
    return {
        "transfer_id": transfer.get("transfer_id"),
        "transport": transfer.get("transport"),
        "mode": transfer.get("mode"),
        "status": transfer.get("status"),
        "source_node_id": transfer.get("source_node_id"),
        "target_node_id": transfer.get("target_node_id"),
        "source_path": transfer.get("source_path"),
        "target_path": transfer.get("target_path"),
        "target_output_dir": transfer.get("target_output_dir"),
        "resume_mode": transfer.get("resume_mode"),
        "attempt": transfer.get("attempt"),
        "size_bytes": transfer.get("size_bytes"),
        "sha256": transfer.get("sha256"),
        "route_policy": transfer.get("route_policy"),
        "resumable": transfer.get("resumable"),
        "last_resumable_error": transfer.get("last_resumable_error"),
        "resume_hint": transfer.get("resume_hint"),
        "error_code": transfer.get("error_code"),
        "error_message": transfer.get("error_message"),
        "created_at": transfer.get("created_at"),
        "started_at": transfer.get("started_at"),
        "completed_at": transfer.get("completed_at"),
        "summary": transfer.get("summary"),
        "source_job": _job_status_summary(source_job),
        "target_job": _job_status_summary(target_job),
    }


def _job_status_summary(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {
        "job_id": value.get("job_id"),
        "node_id": value.get("node_id"),
        "function_name": value.get("function_name"),
        "status": value.get("status"),
        "progress_pct": value.get("progress_pct"),
        "progress_message": value.get("progress_message"),
        "error_code": value.get("error_code"),
        "error_message": value.get("error_message"),
    }
