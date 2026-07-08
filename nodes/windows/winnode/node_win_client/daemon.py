from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import platform
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import normalize_execution_error
from .logging_config import get_logger
from .models import (
    HeartbeatPayload,
    JobAcceptedPayload,
    JobAvailablePayload,
    JobEventPayload,
    JobEventType,
    JobFinishedPayload,
    JobLeaseRenewPayload,
    JobPayload,
    JobStatus,
    KnownJob,
    MessageType,
    NodeAcceptedPayload,
    NodeHelloPayload,
    PlatformInfo,
    ReconcileJobsPayload,
    RegisterCapabilitiesPayload,
    SignalReportPayload,
)
from .plugins import FakeSystemPlugin
from .protocol import envelope
from .runtime_context import collect_runtime_instances
from .transport import CenterTransport


@dataclass
class LocalJobState:
    job: JobPayload
    status: JobStatus = JobStatus.RUNNING
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    sequence: int = 0


class WinNodeDaemon:
    def __init__(self, settings: Settings, state_store: Any | None = None) -> None:
        self.settings = settings
        self.state_store = state_store
        self.logger = get_logger("daemon")
        self.transport = CenterTransport(settings)
        self.plugin = FakeSystemPlugin(
            settings.l2_policy,
            settings.transfer.yq_croc,
            center_base_url=settings.center_base_url,
            node_token=settings.node_token,
            request_timeout_sec=settings.request_timeout_sec,
        )
        self.started_at = time.monotonic()
        self.accepted = NodeAcceptedPayload(
            heartbeat_interval_sec=settings.heartbeat_interval_sec,
            signal_report_interval_sec=settings.signal_report_interval_sec,
            job_poll_interval_sec=settings.job_poll_interval_sec,
        )
        self.jobs: dict[str, LocalJobState] = {}
        self._stop = asyncio.Event()
        self._connected = False
        self._reconnect_lock = asyncio.Lock()
        self._last_capability_register_monotonic = 0.0
        self._job_tasks: dict[str, asyncio.Task[None]] = {}

    def _set_daemon_state(self, key: str, value: str) -> None:
        if self.state_store is None:
            return
        try:
            self.state_store.set_daemon_state(key, value)
        except Exception:
            return

    def _set_connected(self, connected: bool) -> None:
        self._connected = connected
        self._set_daemon_state("center_connected", "true" if connected else "false")
        self._set_daemon_state("center_reachable", "true" if connected else "false")

    def _set_last_error(self, exc: BaseException | str | None) -> None:
        if exc is None:
            self._set_daemon_state("last_error", "")
            return
        self._set_daemon_state("last_error", str(exc)[:500])

    async def close(self) -> None:
        await self.transport.close()

    async def hello(self) -> dict[str, Any]:
        self.logger.info("bootstrap.hello preparing node_id=%s", self.settings.node_id)
        runtimes = await collect_runtime_instances(
            self.settings.node_id,
            self.settings.transfer.yq_croc,
        )
        self.logger.info("bootstrap.hello runtimes_collected count=%s", len(runtimes))
        payload = NodeHelloPayload(
            daemon_version="0.1.0",
            node_name=self.settings.node_name,
            role=self.settings.role,
            locality=self.settings.locality,  # type: ignore[arg-type]
            platform=PlatformInfo(
                os="windows",
                arch=platform.machine() or "unknown",
                os_version=platform.version(),
                hostname=platform.node(),
            ),
            runtimes=runtimes,
        )
        response = await self.transport.send(
            envelope(
                message_type=MessageType.NODE_HELLO,
                node_id=self.settings.node_id,
                payload=payload,
            )
        )
        if response.get("message_type") == MessageType.NODE_ACCEPTED:
            self.accepted = NodeAcceptedPayload.model_validate(response.get("payload", {}))
            self.logger.info(
                "bootstrap.hello accepted heartbeat_interval=%s signal_interval=%s "
                "job_poll_interval=%s delivery=%s",
                self.accepted.heartbeat_interval_sec,
                self.accepted.signal_report_interval_sec,
                self.accepted.job_poll_interval_sec,
                self.accepted.job_delivery_mode,
            )
        else:
            self.logger.warning(
                "bootstrap.hello unexpected_response response_type=%s",
                response.get("message_type"),
            )
        return response

    async def register_capabilities(self) -> dict[str, Any]:
        payload = RegisterCapabilitiesPayload(
            plugins=[self.plugin.manifest()],
            runtimes=await collect_runtime_instances(
                self.settings.node_id,
                self.settings.transfer.yq_croc,
            ),
        )
        self.logger.info(
            "bootstrap.register_capabilities sending plugins=%s functions=%s runtimes=%s",
            len(payload.plugins),
            len(payload.plugins[0].functions) if payload.plugins else 0,
            len(payload.runtimes),
        )
        response = await self.transport.send(
            envelope(
                message_type=MessageType.NODE_REGISTER_CAPABILITIES,
                node_id=self.settings.node_id,
                payload=payload,
            )
        )
        self._last_capability_register_monotonic = time.monotonic()
        self._set_daemon_state("last_capability_register", datetime.now(UTC).isoformat())
        self._set_daemon_state("capability_count", str(len(payload.plugins[0].functions)))
        self.logger.info(
            "bootstrap.register_capabilities response response_type=%s",
            response.get("message_type"),
        )
        return response

    async def heartbeat(self) -> dict[str, Any]:
        payload = HeartbeatPayload(
            daemon_uptime_sec=int(time.monotonic() - self.started_at),
            running_jobs=sum(1 for job in self.jobs.values() if job.status == JobStatus.RUNNING),
            plugin_count=1,
            status="online",
            runtimes=await collect_runtime_instances(
                self.settings.node_id,
                self.settings.transfer.yq_croc,
            ),
        )
        response = await self.transport.send(
            envelope(
                message_type=MessageType.NODE_HEARTBEAT,
                node_id=self.settings.node_id,
                payload=payload,
            )
        )
        self.logger.info(
            "heartbeat.sent running_jobs=%s response_type=%s",
            payload.running_jobs,
            response.get("message_type"),
        )
        return response

    async def report_signals(self) -> dict[str, Any]:
        payload = SignalReportPayload(signals=self.plugin.collect_signals())
        response = await self.transport.send(
            envelope(
                message_type=MessageType.SIGNAL_REPORT,
                node_id=self.settings.node_id,
                payload=payload,
            )
        )
        self.logger.info(
            "signals.sent count=%s response_type=%s",
            len(payload.signals),
            response.get("message_type"),
        )
        return response

    async def upload_artifact_bytes(
        self,
        *,
        data: bytes,
        artifact_type: str = "file",
        content_type: str = "application/octet-stream",
        title: str | None = None,
        summary: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
        invocation_id: str | None = None,
        job_id: str | None = None,
        capability_source_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "artifact_type": artifact_type,
            "content_type": content_type,
            "data_base64": base64.b64encode(data).decode("ascii"),
        }
        optional_fields: dict[str, Any | None] = {
            "title": title,
            "summary": summary,
            "metadata": metadata,
            "session_id": session_id,
            "invocation_id": invocation_id,
            "job_id": job_id,
            "capability_source_id": capability_source_id,
        }
        payload.update({key: value for key, value in optional_fields.items() if value is not None})
        response = await self.transport.send(
            envelope(
                message_type=MessageType.ARTIFACT_UPLOAD,
                node_id=self.settings.node_id,
                payload=payload,
                session_id=session_id,
            )
        )
        if response.get("message_type") != MessageType.ARTIFACT_ACCEPTED:
            raise RuntimeError(
                f"artifact.upload unexpected response_type={response.get('message_type')!r}"
            )
        return response

    async def upload_artifact_file(
        self,
        path: str | Path,
        *,
        artifact_type: str = "file",
        content_type: str | None = None,
        title: str | None = None,
        summary: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
        invocation_id: str | None = None,
        job_id: str | None = None,
        capability_source_id: str | None = None,
    ) -> dict[str, Any]:
        artifact_path = Path(path)
        data = artifact_path.read_bytes()
        detected_content_type = content_type or mimetypes.guess_type(artifact_path.name)[0]
        return await self.upload_artifact_bytes(
            data=data,
            artifact_type=artifact_type,
            content_type=detected_content_type or "application/octet-stream",
            title=title or artifact_path.name,
            summary=summary,
            metadata={
                "source_path": str(artifact_path),
                **(metadata or {}),
            },
            session_id=session_id,
            invocation_id=invocation_id,
            job_id=job_id,
            capability_source_id=capability_source_id,
        )

    async def reconcile_jobs(self) -> dict[str, Any]:
        known_by_job_id = {
            state.job.job_id: KnownJob(
                job_id=state.job.job_id,
                local_status=state.status,
                started_at=state.started_at,
                updated_at=state.updated_at,
                output=state.output,
                error=state.error,
            )
            for state in self.jobs.values()
        }

        if self.state_store is not None:
            with suppress(Exception):
                for row in self.state_store.get_unreported():
                    result_json = row.get("result_json")
                    output = json.loads(result_json) if result_json else None
                    error = None
                    if row.get("error_code") or row.get("error_message"):
                        error = {
                            "code": row.get("error_code") or "execution_failed",
                            "message": row.get("error_message") or "execution failed",
                        }
                    known_by_job_id.setdefault(
                        row["job_id"],
                        KnownJob(
                            job_id=row["job_id"],
                            local_status=row["status"],
                            output=output,
                            error=error,
                        ),
                    )

        known = list(known_by_job_id.values())
        response = await self.transport.send(
            envelope(
                message_type=MessageType.NODE_RECONCILE_JOBS,
                node_id=self.settings.node_id,
                payload=ReconcileJobsPayload(known_jobs=known),
            )
        )
        self._apply_reconciliation_actions(response)
        self.logger.info(
            "jobs.reconcile sent known_jobs=%s response_type=%s",
            len(known),
            response.get("message_type"),
        )
        return response

    def _apply_reconciliation_actions(self, response: dict[str, Any]) -> None:
        payload = response.get("payload")
        if not isinstance(payload, dict):
            return
        actions = payload.get("actions")
        if not isinstance(actions, list):
            return
        for action in actions:
            if not isinstance(action, dict):
                continue
            job_id = str(action.get("job_id") or "")
            action_name = str(action.get("action") or "")
            if not job_id:
                continue
            if action_name in {"accept_result", "discard_result", "forget"}:
                if self.state_store is not None:
                    with suppress(Exception):
                        self.state_store.mark_reported(job_id)
                state = self.jobs.get(job_id)
                if state is None or state.status != JobStatus.RUNNING:
                    self.jobs.pop(job_id, None)
                self.logger.info(
                    "jobs.reconcile local_result_closed job_id=%s action=%s",
                    job_id,
                    action_name,
                )
            elif action_name == "cancel":
                task = self._job_tasks.get(job_id)
                if task is not None and not task.done():
                    task.cancel()
                self.logger.info("jobs.reconcile cancel_requested job_id=%s", job_id)
            elif action_name == "continue":
                self.logger.debug("jobs.reconcile continue job_id=%s", job_id)

    async def report_unreported_results(self) -> dict[str, int]:
        response = await self.reconcile_jobs()
        payload = response.get("payload")
        actions = payload.get("actions") if isinstance(payload, dict) else []
        accepted = 0
        discarded = 0
        forgotten = 0
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                if action.get("action") == "accept_result":
                    accepted += 1
                elif action.get("action") == "discard_result":
                    discarded += 1
                elif action.get("action") == "forget":
                    forgotten += 1
        failed = 0
        if self.state_store is not None:
            with suppress(Exception):
                failed = len(self.state_store.get_unreported())
        return {
            "accepted": accepted,
            "discarded": discarded,
            "forgotten": forgotten,
            "remaining_unreported": failed,
        }

    async def poll_once(self) -> dict[str, Any]:
        accepted = 0
        last_response: dict[str, Any] | None = None

        while True:
            running = [
                job_id for job_id, state in self.jobs.items() if state.status == JobStatus.RUNNING
            ]
            capacity = max(0, self.settings.max_concurrent_jobs - len(running))
            if capacity <= 0:
                self.logger.info(
                    "job.poll skipped capacity_full running=%s max_concurrent=%s",
                    len(running),
                    self.settings.max_concurrent_jobs,
                )
                if last_response is not None:
                    return {
                        "message_type": "job.poll.drained",
                        "payload": {"accepted": accepted, "reason": "capacity_full"},
                    }
                return {"message_type": "job.poll.skipped", "payload": {"reason": "capacity_full"}}

            self.logger.info(
                "job.poll sending capacity=%s running=%s max_concurrent=%s",
                capacity,
                len(running),
                self.settings.max_concurrent_jobs,
            )
            response = await self.transport.send(
                envelope(
                    message_type=MessageType.JOB_POLL,
                    node_id=self.settings.node_id,
                    payload={"capacity": capacity, "running_jobs": running},
                )
            )
            last_response = response
            if response.get("message_type") != MessageType.JOB_AVAILABLE:
                self.logger.info(
                    "job.poll response response_type=%s accepted=%s",
                    response.get("message_type"),
                    accepted,
                )
                return (
                    response
                    if accepted == 0
                    else {
                        "message_type": "job.poll.drained",
                        "payload": {
                            "accepted": accepted,
                            "last_message_type": response.get("message_type"),
                        },
                    }
                )

            payload = JobAvailablePayload.model_validate(response.get("payload", {}))
            if not payload.jobs:
                return (
                    response
                    if accepted == 0
                    else {
                        "message_type": "job.poll.drained",
                        "payload": {
                            "accepted": accepted,
                            "last_message_type": response.get("message_type"),
                        },
                    }
                )

            new_jobs = 0
            for job in payload.jobs:
                if job.job_id in self.jobs:
                    self.logger.info("job.poll duplicate job_id=%s", job.job_id)
                    continue
                self.jobs[job.job_id] = LocalJobState(job=job)
                task = asyncio.create_task(self.execute_job(job))
                self._job_tasks[job.job_id] = task
                task.add_done_callback(
                    lambda _, job_id=job.job_id: self._job_tasks.pop(job_id, None)
                )
                accepted += 1
                new_jobs += 1
                self.logger.info(
                    "job.accepted_local job_id=%s function=%s",
                    job.job_id,
                    job.function,
                )

            if new_jobs == 0:
                return {
                    "message_type": "job.poll.drained",
                    "payload": {"accepted": accepted, "reason": "duplicate_jobs"},
                }

    async def execute_job(self, job: JobPayload) -> None:
        state = self.jobs.get(job.job_id) or LocalJobState(job=job)
        self.jobs[job.job_id] = state
        self.logger.info(
            "job.execute start job_id=%s function=%s timeout=%s",
            job.job_id,
            job.function,
            job.timeout_sec,
        )
        await self.transport.send(
            envelope(
                message_type=MessageType.JOB_ACCEPTED,
                node_id=self.settings.node_id,
                payload=JobAcceptedPayload(job_id=job.job_id),
            )
        )
        await self._event(state, JobEventType.STARTED, {"function": job.function})
        renew_task = asyncio.create_task(self._lease_loop(state))
        try:
            job_input = effective_job_input(job)
            if job.runtime_id:
                job_input["__runtime_id"] = job.runtime_id
            if job.execution_requirements:
                job_input["__execution_requirements"] = dict(job.execution_requirements)
            job_input["__job_event_callback"] = (
                lambda event_type, data: self._event(state, event_type, data)
            )
            output = await asyncio.wait_for(
                self.plugin.execute(job.function, job_input),
                timeout=max(float(job.timeout_sec), 0.1),
            )
            output = await self._materialize_output_artifacts(job, output)
            state.status = JobStatus.SUCCEEDED
            state.output = output
            state.updated_at = datetime.now(UTC)
            self.logger.info(
                "job.execute succeeded job_id=%s function=%s",
                job.job_id,
                job.function,
            )
            await self._event(state, JobEventType.RESULT, output)
            await self._finish(state, "succeeded", output=output)
        except TimeoutError:
            state.status = JobStatus.TIMEOUT
            state.error = {"code": "timeout", "message": "local execution timeout"}
            self.logger.warning(
                "job.execute timeout job_id=%s function=%s",
                job.job_id,
                job.function,
            )
            await self._event(state, JobEventType.TIMEOUT, state.error)
            await self._finish(state, "timeout", error=state.error)
        except asyncio.CancelledError:
            state.status = JobStatus.CANCELLED
            state.error = {"code": "cancelled", "message": "local execution cancelled"}
            self.logger.warning(
                "job.execute cancelled job_id=%s function=%s",
                job.job_id,
                job.function,
            )
            await self._event(state, JobEventType.CANCELLED, state.error)
            await self._finish(state, "cancelled", error=state.error)
            raise
        except Exception as exc:
            state.status = JobStatus.FAILED
            state.error = normalize_execution_error(exc)
            self.logger.warning(
                "job.execute failed job_id=%s function=%s error_code=%s error=%s",
                job.job_id,
                job.function,
                state.error.get("code"),
                state.error.get("message"),
            )
            await self._event(state, JobEventType.FAILED, state.error)
            await self._finish(state, "failed", error=state.error)
        finally:
            renew_task.cancel()

    async def _materialize_output_artifacts(
        self,
        job: JobPayload,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        upload_requests = output.pop("__artifact_uploads", None)
        if upload_requests is None:
            return output
        if not isinstance(upload_requests, list):
            raise ValueError("__artifact_uploads must be a list")

        artifacts: list[dict[str, Any]] = []
        for request in upload_requests:
            if not isinstance(request, dict):
                raise ValueError("artifact upload request must be an object")
            path = request.get("path")
            if not path:
                raise ValueError("artifact upload request missing path")
            response = await self.upload_artifact_file(
                Path(str(path)),
                artifact_type=str(request.get("artifact_type") or "file"),
                content_type=(
                    str(request["content_type"]) if request.get("content_type") else None
                ),
                title=str(request["title"]) if request.get("title") else None,
                summary=request.get("summary")
                if isinstance(request.get("summary"), dict)
                else None,
                metadata={
                    "function": job.function,
                    **(
                        request.get("metadata") if isinstance(request.get("metadata"), dict) else {}
                    ),
                },
                invocation_id=job.invocation_id,
                job_id=job.job_id,
            )
            artifact = response.get("payload", {}).get("artifact")
            if not isinstance(artifact, dict):
                raise RuntimeError("artifact.accepted response missing artifact object")
            artifacts.append(artifact)
            if bool(request.get("delete_after_upload")):
                Path(str(path)).unlink(missing_ok=True)

        existing_artifacts = output.get("artifacts")
        if isinstance(existing_artifacts, list):
            output["artifacts"] = [*existing_artifacts, *artifacts]
        else:
            output["artifacts"] = artifacts
        return output

    async def _event(
        self, state: LocalJobState, event_type: JobEventType, data: dict[str, Any]
    ) -> None:
        if not self.settings.send_job_events:
            return
        state.sequence += 1
        state.updated_at = datetime.now(UTC)
        await self.transport.send(
            envelope(
                message_type=MessageType.JOB_EVENT,
                node_id=self.settings.node_id,
                payload=JobEventPayload(
                    job_id=state.job.job_id,
                    event_type=event_type,
                    sequence=state.sequence,
                    data=data,
                ),
            )
        )

    async def _finish(
        self,
        state: LocalJobState,
        status: str,
        output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        if self.state_store is not None:
            with suppress(Exception):
                self.state_store.upsert_job_result(
                    job_id=state.job.job_id,
                    invocation_id=state.job.invocation_id,
                    function_name=state.job.function,
                    status=status,
                    result=output,
                    error_code=error.get("code") if error else None,
                    error_message=error.get("message") if error else None,
                )

        await self.transport.send(
            envelope(
                message_type=MessageType.JOB_FINISHED,
                node_id=self.settings.node_id,
                payload=JobFinishedPayload(
                    job_id=state.job.job_id,
                    status=status,  # type: ignore[arg-type]
                    output=output,
                    error=error,
                ),
            )
        )
        if self.state_store is not None:
            with suppress(Exception):
                self.state_store.mark_reported(state.job.job_id)

    async def _lease_loop(self, state: LocalJobState) -> None:
        interval = max(state.job.lease_sec * 0.5, 1.0)
        try:
            while state.status == JobStatus.RUNNING:
                await asyncio.sleep(interval)
                if state.status != JobStatus.RUNNING:
                    return
                await self.transport.send(
                    envelope(
                        message_type=MessageType.JOB_LEASE_RENEW,
                        node_id=self.settings.node_id,
                        payload=JobLeaseRenewPayload(
                            job_id=state.job.job_id,
                            lease_extend_sec=state.job.lease_sec,
                        ),
                    )
                )
        except asyncio.CancelledError:
            return

    async def bootstrap(self) -> None:
        self._set_daemon_state("daemon_status", "bootstrapping")
        self.logger.info(
            "bootstrap.start node_id=%s center=%s max_concurrent_jobs=%s send_job_events=%s",
            self.settings.node_id,
            self.settings.center_base_url,
            self.settings.max_concurrent_jobs,
            self.settings.send_job_events,
        )
        await self.hello()
        self.logger.info("bootstrap.step_complete step=hello")
        await self.register_capabilities()
        self.logger.info("bootstrap.step_complete step=register_capabilities")
        await self.reconcile_jobs()
        self.logger.info("bootstrap.step_complete step=reconcile_jobs")
        self._set_daemon_state("daemon_status", "running")
        self._set_last_error(None)
        self._set_connected(True)
        self.logger.info("bootstrap.complete")

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(seconds, 0.1))
        except TimeoutError:
            return

    async def _center_health_reachable(self) -> bool:
        try:
            response = await self.transport.client.get(
                "/healthz",
                timeout=min(float(self.settings.request_timeout_sec), 2.0),
            )
            reachable = 200 <= response.status_code < 500
            self._set_daemon_state("center_reachable", "true" if reachable else "false")
            return reachable
        except Exception:
            self._set_daemon_state("center_reachable", "false")
            return False

    async def _sleep_until_retry(self, seconds: float) -> None:
        probe_interval = max(float(self.settings.reconnect_probe_interval_sec), 0.5)
        deadline = time.monotonic() + max(seconds, 0.1)
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await self._sleep_or_stop(min(probe_interval, remaining))
            if self._stop.is_set():
                return
            if await self._center_health_reachable():
                self.logger.info("bootstrap.retry_wakeup reason=center_health_reachable")
                return

    async def _bootstrap_until_connected(self) -> bool:
        async with self._reconnect_lock:
            if self._connected:
                return True

            delay = max(float(self.settings.reconnect_initial_delay_sec), 0.5)
            max_delay = max(float(self.settings.reconnect_max_delay_sec), delay)
            ready_delay = max(float(self.settings.reconnect_ready_delay_sec), 0.5)
            attempts = 0
            while not self._stop.is_set():
                attempts += 1
                self._set_daemon_state("daemon_status", "reconnecting")
                self._set_daemon_state("reconnect_attempts", str(attempts))
                try:
                    await self.bootstrap()
                    self._set_daemon_state("reconnect_attempts", "0")
                    return True
                except Exception as exc:
                    self._set_connected(False)
                    self._set_last_error(exc)
                    health_reachable = await self._center_health_reachable()
                    next_delay = min(delay, ready_delay) if health_reachable else delay
                    self.logger.warning(
                        "bootstrap.failed attempt=%s next_delay=%s health_reachable=%s "
                        "error_type=%s error=%s",
                        attempts,
                        next_delay,
                        health_reachable,
                        type(exc).__name__,
                        exc,
                    )
                    await self._sleep_until_retry(next_delay)
                    delay = min(delay * 2, max_delay)
            return False

    async def _heartbeat_loop(self, seconds: int) -> None:
        while not self._stop.is_set():
            try:
                await self.heartbeat()
                self._set_daemon_state("last_heartbeat", datetime.now(UTC).isoformat())
                self._set_daemon_state("daemon_status", "running")
                self._set_last_error(None)
                self._set_connected(True)

                elapsed_since_register = time.monotonic() - self._last_capability_register_monotonic
                if elapsed_since_register >= self.settings.capability_refresh_interval_sec:
                    await self.register_capabilities()
            except Exception as exc:
                self._set_connected(False)
                self._set_last_error(exc)
                self.logger.warning(
                    "heartbeat.failed error_type=%s error=%s",
                    type(exc).__name__,
                    exc,
                )
                await self._bootstrap_until_connected()
            await self._sleep_or_stop(max(seconds, 1))

    async def run(self) -> None:
        self._set_daemon_state("daemon_status", "starting")
        self._set_connected(False)
        self.logger.info(
            "daemon.starting node_id=%s center=%s request_timeout=%s "
            "poll_interval=%s max_concurrent_jobs=%s",
            self.settings.node_id,
            self.settings.center_base_url,
            self.settings.request_timeout_sec,
            self.settings.job_poll_interval_sec,
            self.settings.max_concurrent_jobs,
        )
        if not await self._bootstrap_until_connected():
            self.logger.warning("daemon.bootstrap_aborted")
            return
        heartbeat_interval = self.accepted.heartbeat_interval_sec
        signal_interval = self.accepted.signal_report_interval_sec
        poll_interval = self.accepted.job_poll_interval_sec or self.settings.job_poll_interval_sec

        async def loop_every(seconds: int, action: Any, require_connected: bool = True) -> None:
            while not self._stop.is_set():
                if require_connected and not self._connected:
                    await self._sleep_or_stop(max(seconds, 1))
                    continue
                try:
                    await action()
                except Exception as exc:
                    self._set_last_error(exc)
                    self.logger.warning(
                        "daemon.loop_action_failed action=%s error_type=%s error=%s",
                        action.__name__,
                        type(exc).__name__,
                        exc,
                    )
                await self._sleep_or_stop(max(seconds, 1))

        tasks = [
            asyncio.create_task(self._heartbeat_loop(heartbeat_interval)),
            asyncio.create_task(loop_every(signal_interval, self.report_signals)),
            asyncio.create_task(loop_every(poll_interval, self.poll_once)),
        ]
        try:
            await self._stop.wait()
        finally:
            for task in tasks:
                task.cancel()
            self._set_daemon_state("daemon_status", "stopped")
            self._set_daemon_state("center_connected", "false")
            self.logger.info("daemon.stopped")

    def stop(self) -> None:
        self._stop.set()


def effective_job_input(job: JobPayload) -> dict[str, Any]:
    job_input = dict(job.input)
    if job.approval_id and "approval_id" not in job_input:
        job_input["approval_id"] = job.approval_id
    if job.dry_run is not None and "dry_run" not in job_input:
        job_input["dry_run"] = job.dry_run
    return job_input
