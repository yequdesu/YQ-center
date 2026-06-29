"""Application service for Center-managed transfer sessions."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.application.schemas import ExecuteToolCommand, ExecuteToolResult
from yequ.models.job import Job
from yequ.models.timeline import TimelineEvent
from yequ.models.transfer import TransferSession
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
    resume_mode: str = "resume"
    timeout_sec: int = 3600
    expected_sha256: str | None = None
    actor_type: str = "agent"
    actor_id: str = "agent"
    session_id: str | None = None
    execution_mode: str = "auto"


class TransferApplicationService:
    """Create and inspect transfer sessions without exposing croc sequencing to Agent."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

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

        code = command.code or _generate_croc_code()
        resume_mode = command.resume_mode or "resume"
        if resume_mode not in {"resume", "overwrite", "fail_if_exists"}:
            raise ValueError("resume_mode must be resume, overwrite, or fail_if_exists")

        session = TransferSession(
            transfer_id=f"trf_{secrets.token_hex(8)}",
            transport="croc",
            mode="node_to_node",
            status="created",
            source_node_id=command.source_node_id,
            target_node_id=command.target_node_id,
            source_path=command.source_path,
            target_path=command.target_path,
            target_output_dir=command.target_output_dir,
            relay_url=command.relay_url,
            code_hash=_secret_hash(code),
            resume_mode=resume_mode,
            attempt=1,
            created_by=command.actor_type,
            actor_id=command.actor_id,
            session_id=command.session_id,
            started_at=datetime.now(UTC),
            metadata_json={"phase": "transfer_session_v1"},
        )
        self.db.add(session)
        await self.db.flush()
        await self.db.commit()

        receive_result = await self._invoke_capability(
            command,
            node_id=command.target_node_id,
            capability_ref="transfer.croc.receive",
            tool_input={
                "transfer_id": session.transfer_id,
                "code": code,
                "output_dir": command.target_output_dir,
                "target_path": command.target_path,
                "relay_url": command.relay_url,
                "timeout_sec": command.timeout_sec,
                "resume_mode": resume_mode,
                "expected_sha256": command.expected_sha256,
            },
        )
        if receive_result.status not in {"created", "running"}:
            session.status = "failed"
            session.error_code = receive_result.error_code or "receiver_job_failed"
            session.error_message = receive_result.error_message
            await self.db.commit()
            return self._session_dict(session, receive_result=receive_result)

        session.target_invocation_id = receive_result.invocation_id
        session.target_job_id = receive_result.job_id
        session.status = "receiving"
        await self.db.commit()

        send_result = await self._invoke_capability(
            command,
            node_id=command.source_node_id,
            capability_ref="transfer.croc.send",
            tool_input={
                "transfer_id": session.transfer_id,
                "code": code,
                "path": command.source_path,
                "relay_url": command.relay_url,
                "timeout_sec": command.timeout_sec,
                "expected_receiver_node_id": command.target_node_id,
            },
        )
        if send_result.status not in {"created", "running"}:
            await self._cancel_job_id(session.target_job_id, reason="sender_job_failed")
            session.status = "failed"
            session.error_code = send_result.error_code or "sender_job_failed"
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

    async def _invoke_capability(
        self,
        command: TransferCreateCommand,
        *,
        node_id: str,
        capability_ref: str,
        tool_input: dict[str, object],
    ) -> ExecuteToolResult:
        from yequ.application.tool_invocation import ToolInvocationApplicationService
        from yequ.db import async_session_factory

        clean_input = {key: value for key, value in tool_input.items() if value is not None}
        async with async_session_factory() as db:
            return await ToolInvocationApplicationService(db).execute(
                ExecuteToolCommand(
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
                    lease_sec=30,
                    resource_keys=[f"node:{node_id}:transfer"],
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

        failed_job = next((job for job in jobs if job.status in {"failed", "timeout"}), None)
        if failed_job is not None:
            session.error_code = failed_job.error_code
            session.error_message = failed_job.error_message
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
        "output": job.output,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "error_details": job.error_details,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _generate_croc_code() -> str:
    return f"yequ-{secrets.token_urlsafe(12).replace('_', '').replace('-', '')[:16]}"


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
