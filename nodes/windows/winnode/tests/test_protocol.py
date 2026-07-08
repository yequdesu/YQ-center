import asyncio
import base64
import time

from node_win_client.config import Settings, load_settings
from node_win_client.daemon import WinNodeDaemon, effective_job_input
from node_win_client.models import (
    JobPayload,
    JobStatus,
    MessageType,
    NodeHelloPayload,
    PlatformInfo,
)
from node_win_client.protocol import envelope
from node_win_client.runtime_context import interactive_runtime_id, runtime_id, system_runtime_id
from node_win_client.state_store import StateStore


class FakeTransport:
    def __init__(self, jobs: list[JobPayload]) -> None:
        self.jobs = list(jobs)
        self.sent: list[dict] = []
        self.finished: list[dict] = []
        self.reconcile_actions: list[dict] = []

    async def send(self, message: object) -> dict:
        data = message.model_dump(mode="json") if hasattr(message, "model_dump") else dict(message)  # type: ignore[arg-type]
        self.sent.append(data)
        message_type = data.get("message_type")
        if message_type == MessageType.JOB_POLL:
            if self.jobs:
                return {
                    "message_type": MessageType.JOB_AVAILABLE,
                    "payload": {"jobs": [self.jobs.pop(0).model_dump(mode="json")]},
                }
            return {"message_type": MessageType.JOB_EMPTY, "payload": {"jobs": []}}
        if message_type == MessageType.JOB_FINISHED:
            self.finished.append(data)
        if message_type == MessageType.ARTIFACT_UPLOAD:
            return {
                "message_type": MessageType.ARTIFACT_ACCEPTED,
                "payload": {
                    "artifact": {
                        "artifact_id": "art_test",
                        "size_bytes": 5,
                        "sha256": "fake",
                        "download_url": "/admin/artifacts/art_test/download",
                    }
                },
            }
        if message_type == MessageType.NODE_RECONCILE_JOBS:
            return {
                "message_type": MessageType.JOB_RECONCILIATION,
                "payload": {"actions": self.reconcile_actions},
            }
        return {"message_type": "ok", "payload": {}}


def test_envelope_contains_required_fields() -> None:
    msg = envelope(
        message_type=MessageType.NODE_HELLO,
        node_id="win-client",
        payload=NodeHelloPayload(
            daemon_version="0.1.0",
            node_name="Windows Client",
            role=["client"],
            locality="lan",
            platform=PlatformInfo(os="windows", arch="x86_64"),
        ),
    )

    assert msg.yqp_version == "0.1"
    assert msg.message_id.startswith("msg_")
    assert msg.trace_id.startswith("tr_")
    assert msg.node_id == "win-client"
    assert msg.payload["daemon_version"] == "0.1.0"


def test_job_payload_l2_fields_are_merged_into_plugin_input() -> None:
    job = JobPayload(
        job_id="job_1",
        function="windows.exec.run",
        input={"command": "whoami", "profile": "user.readonly"},
        approval_id="apv_1",
        dry_run=False,
        resource_keys=["node:winClient:maintenance:dns"],
    )

    merged = effective_job_input(job)

    assert merged["approval_id"] == "apv_1"
    assert merged["dry_run"] is False


def test_job_payload_accepts_runtime_contract() -> None:
    job = JobPayload(
        job_id="job_runtime",
        function="windows.everything.find",
        input={"root": "%USERPROFILE%\\Desktop", "query": "*.zip"},
        runtime_id="winClient/runtime/interactive-user",
        execution_requirements={
            "runtime_kind": "interactive",
            "labels": ["profile", "filesystem"],
            "interactive": True,
        },
    )

    assert job.runtime_id == "winClient/runtime/interactive-user"
    assert job.execution_requirements["runtime_kind"] == "interactive"


def test_job_payload_input_fields_win_over_top_level_l2_fields() -> None:
    job = JobPayload(
        job_id="job_1",
        function="windows.exec.run",
        input={
            "command": "whoami",
            "profile": "user.readonly",
            "approval_id": "apv_input",
            "dry_run": True,
        },
        approval_id="apv_top",
        dry_run=False,
    )

    merged = effective_job_input(job)

    assert merged["approval_id"] == "apv_input"
    assert merged["dry_run"] is True


def test_daemon_reconnect_and_capacity_settings_are_loaded(tmp_path) -> None:
    config = tmp_path / "node.yaml"
    config.write_text(
        """
center:
  base_url: "https://gtw.yequdesu.top"
  yqp_path: "/yqp/"
node:
  node_id: "winClient"
  token: "token"
daemon:
  max_concurrent_jobs: 3
  capability_refresh_interval_sec: 120
  reconnect_initial_delay_sec: 1
  reconnect_max_delay_sec: 9
  reconnect_ready_delay_sec: 4
  reconnect_probe_interval_sec: 2
safety:
  allow_write_actions: true
""",
        encoding="utf-8",
    )

    settings = load_settings(str(config))

    assert settings.max_concurrent_jobs == 3
    assert settings.capability_refresh_interval_sec == 120
    assert settings.reconnect_initial_delay_sec == 1
    assert settings.reconnect_max_delay_sec == 9
    assert settings.reconnect_ready_delay_sec == 4
    assert settings.reconnect_probe_interval_sec == 2
    assert settings.routes["artifact.upload"] == "/yqp/"


def test_artifact_upload_uses_yqp_base64_payload() -> None:
    async def run() -> None:
        settings = Settings(node_id="winClient", send_job_events=False)
        daemon = WinNodeDaemon(settings)
        transport = FakeTransport([])
        daemon.transport = transport  # type: ignore[assignment]

        response = await daemon.upload_artifact_bytes(
            data=b"hello",
            content_type="text/plain",
            title="hello.txt",
            summary={"purpose": "test"},
            metadata={"source": "unit"},
        )

        assert response["message_type"] == MessageType.ARTIFACT_ACCEPTED
        message = transport.sent[-1]
        assert message["message_type"] == MessageType.ARTIFACT_UPLOAD
        assert message["node_id"] == "winClient"
        payload = message["payload"]
        assert payload["title"] == "hello.txt"
        assert payload["content_type"] == "text/plain"
        assert base64.b64decode(payload["data_base64"]) == b"hello"

    asyncio.run(run())


def test_reconcile_reports_and_clears_unreported_cache(tmp_path) -> None:
    async def run() -> None:
        store = StateStore(tmp_path / "node_state.sqlite3")
        store.upsert_job_result(
            job_id="job_cached",
            invocation_id="inv_cached",
            function_name="windows.exec.run",
            status="succeeded",
            result={"ok": True},
        )
        settings = Settings(node_id="winClient", send_job_events=False)
        daemon = WinNodeDaemon(settings)
        daemon.state_store = store
        transport = FakeTransport([])
        transport.reconcile_actions = [
            {"job_id": "job_cached", "action": "accept_result", "reconciled": True}
        ]
        daemon.transport = transport  # type: ignore[assignment]

        try:
            result = await daemon.report_unreported_results()
        finally:
            store.close()

        reconcile = next(
            message
            for message in transport.sent
            if message["message_type"] == MessageType.NODE_RECONCILE_JOBS
        )
        known = reconcile["payload"]["known_jobs"]
        assert known[0]["job_id"] == "job_cached"
        assert known[0]["local_status"] == "succeeded"
        assert known[0]["output"] == {"ok": True}
        assert result["accepted"] == 1
        assert result["remaining_unreported"] == 0

    asyncio.run(run())


def test_job_output_artifact_requests_are_uploaded(tmp_path) -> None:
    async def run() -> None:
        artifact_file = tmp_path / "capture.png"
        artifact_file.write_bytes(b"image")
        settings = Settings(node_id="winClient", send_job_events=False)
        daemon = WinNodeDaemon(settings)
        transport = FakeTransport([])
        daemon.transport = transport  # type: ignore[assignment]

        async def fake_execute(function: str, input_data: dict[str, object]) -> dict[str, object]:
            return {
                "captured_at": "2026-06-28T00:00:00Z",
                "__artifact_uploads": [
                    {
                        "path": str(artifact_file),
                        "artifact_type": "screenshot",
                        "content_type": "image/png",
                        "title": "capture.png",
                        "delete_after_upload": True,
                    }
                ],
            }

        daemon.plugin.execute = fake_execute  # type: ignore[method-assign]
        await daemon.execute_job(
            JobPayload(
                job_id="job_capture",
                function="windows.screen.capture",
                input={},
                timeout_sec=5,
                lease_sec=30,
            )
        )

        upload = next(
            message
            for message in transport.sent
            if message["message_type"] == MessageType.ARTIFACT_UPLOAD
        )
        assert upload["payload"]["artifact_type"] == "screenshot"
        assert base64.b64decode(upload["payload"]["data_base64"]) == b"image"
        assert not artifact_file.exists()

        finished = transport.finished[-1]["payload"]
        assert "__artifact_uploads" not in finished["output"]
        assert finished["output"]["artifacts"][0]["artifact_id"] == "art_test"

    asyncio.run(run())


def test_poll_once_drains_available_jobs_until_capacity_is_full() -> None:
    async def run() -> None:
        settings = Settings(
            node_id="winClient",
            max_concurrent_jobs=3,
            send_job_events=False,
            job_poll_interval_sec=3,
        )
        daemon = WinNodeDaemon(settings)
        transport = FakeTransport(
            [
                JobPayload(
                    job_id=f"job_{idx}",
                    function="windows.transfer.croc.status",
                    input={},
                    timeout_sec=2,
                    lease_sec=30,
                )
                for idx in range(3)
            ]
        )
        daemon.transport = transport  # type: ignore[assignment]

        response = await daemon.poll_once()

        assert response["message_type"] == "job.poll.drained"
        assert response["payload"]["accepted"] == 3
        assert [
            message["message_type"]
            for message in transport.sent
            if message["message_type"] == MessageType.JOB_POLL
        ] == [MessageType.JOB_POLL, MessageType.JOB_POLL, MessageType.JOB_POLL]

        while any(state.status == JobStatus.RUNNING for state in daemon.jobs.values()):
            await asyncio.sleep(0.01)

        assert len(transport.finished) == 3
        assert {state.status for state in daemon.jobs.values()} == {JobStatus.SUCCEEDED}

    asyncio.run(run())


def test_hello_and_register_include_runtime_context(monkeypatch) -> None:
    async def run() -> None:
        async def fake_probe_user_worker(timeout_sec: float = 0.5) -> dict[str, object]:
            return {"ok": True, "user": "tester", "pid": 1234}

        monkeypatch.setattr(
            "node_win_client.runtime_context.probe_user_worker",
            fake_probe_user_worker,
        )
        monkeypatch.setattr("node_win_client.runtime_context.runs_as_system_account", lambda: True)

        settings = Settings(node_id="winClient", send_job_events=False)
        daemon = WinNodeDaemon(settings)
        transport = FakeTransport([])
        daemon.transport = transport  # type: ignore[assignment]

        await daemon.hello()
        await daemon.register_capabilities()

        hello = next(
            message
            for message in transport.sent
            if message["message_type"] == MessageType.NODE_HELLO
        )
        register = next(
            message
            for message in transport.sent
            if message["message_type"] == MessageType.NODE_REGISTER_CAPABILITIES
        )

        hello_runtime_ids = {item["runtime_id"] for item in hello["payload"]["runtimes"]}
        assert hello_runtime_ids == {
            system_runtime_id("winClient"),
            interactive_runtime_id("winClient"),
            runtime_id("winClient", "yq-croc-transfer"),
        }
        yq_croc_runtime = next(
            item
            for item in hello["payload"]["runtimes"]
            if item["runtime_id"] == runtime_id("winClient", "yq-croc-transfer")
        )
        assert yq_croc_runtime["kind"] == "interactive"
        assert yq_croc_runtime["interactive"] is True
        assert yq_croc_runtime["labels"] == [
            "windows",
            "profile",
            "filesystem",
            "transfer",
            "yq-croc",
        ]
        assert register["payload"]["runtimes"]

        functions = register["payload"]["plugins"][0]["functions"]
        by_name = {function["name"]: function for function in functions}
        assert (
            by_name["windows.everything.find"]["execution_requirements"]["runtime_kind"]
            == "interactive"
        )
        assert by_name["windows.exec.run"]["execution_requirements"] == {
            "allowed_runtime_kinds": ["interactive", "privileged"],
            "labels": ["windows", "exec"],
            "execution_profiles": [
                "user.readonly",
                "user.write",
                "admin.readonly",
                "admin.write",
            ],
        }
        assert (
            by_name["windows.transfer.croc.status"]["execution_requirements"]["runtime_kind"]
            == "privileged"
        )
        assert by_name["windows.transfer.croc.status"]["execution_requirements"]["labels"] == [
            "network-control",
            "disk-inspection",
        ]
        assert (
            by_name["windows.transfer.croc.send"]["execution_requirements"]["runtime_kind"]
            == "interactive"
        )
        assert by_name["windows.transfer.croc.send"]["execution_requirements"]["labels"] == [
            "windows",
            "profile",
            "filesystem",
            "transfer",
            "yq-croc",
        ]

    asyncio.run(run())


def test_job_finished_preserves_specific_error_code() -> None:
    async def run() -> None:
        settings = Settings(node_id="winClient", send_job_events=False)
        daemon = WinNodeDaemon(settings)
        transport = FakeTransport([])
        daemon.transport = transport  # type: ignore[assignment]

        await daemon.execute_job(
            JobPayload(
                job_id="job_bad",
                function="windows.unknown",
                input={},
                timeout_sec=2,
                lease_sec=30,
            )
        )

        assert transport.finished
        error = transport.finished[-1]["payload"]["error"]
        assert error["code"] == "function_not_supported"
        assert "windows.unknown" in error["message"]

    asyncio.run(run())


def test_blocking_plugin_work_runs_concurrently(monkeypatch) -> None:
    def slow_status(*args, **kwargs) -> dict[str, object]:
        del args, kwargs
        time.sleep(0.25)
        return {"installed": False, "runtime": "yq-croc"}

    monkeypatch.setattr("node_win_client.transfer_yq_croc.probe_yq_croc_status", slow_status)

    async def run() -> None:
        settings = Settings(node_id="winClient", send_job_events=False)
        daemon = WinNodeDaemon(settings)
        transport = FakeTransport([])
        daemon.transport = transport  # type: ignore[assignment]

        started = time.perf_counter()
        await asyncio.gather(
            daemon.execute_job(
                JobPayload(
                    job_id="job_slow_1",
                    function="windows.transfer.croc.status",
                    input={},
                    timeout_sec=2,
                    lease_sec=30,
                )
            ),
            daemon.execute_job(
                JobPayload(
                    job_id="job_slow_2",
                    function="windows.transfer.croc.status",
                    input={},
                    timeout_sec=2,
                    lease_sec=30,
                )
            ),
        )
        elapsed = time.perf_counter() - started

        assert elapsed < 0.45
        assert len(transport.finished) == 2
        assert {message["payload"]["status"] for message in transport.finished} == {"succeeded"}

    asyncio.run(run())
