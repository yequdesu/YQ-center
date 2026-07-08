import asyncio
import io
import json
from pathlib import Path

import pytest

from node_win_client import l2b
from node_win_client.config import L2Policy, TransferYqCrocConfig, load_settings
from node_win_client.l2b import execute_l2b
from node_win_client.plugins import (
    FakeSystemPlugin,
)

L1_FUNCTIONS = {
    "windows.screen.capture",
    "windows.file.upload_artifact",
    "windows.transfer.croc.status",
    "windows.transfer.local.stat",
    "windows.transfer.croc.reconcile",
}

EXEC_FUNCTIONS = {
    "windows.exec.run",
}

TRANSFER_EXTERNAL_FUNCTIONS = {
    "windows.transfer.croc.send",
    "windows.transfer.croc.receive",
}

ARTIFACT_WRITE_FUNCTIONS = {
    "windows.artifact.download_file",
}

L2_FUNCTIONS: set[str] = set()

L2B_FUNCTIONS = {
    "windows.everything.find",
}


def test_l1_readonly_functions_are_registered() -> None:
    manifest = FakeSystemPlugin().manifest()
    functions = {function.name: function for function in manifest.functions}

    assert L1_FUNCTIONS.issubset(functions)

    for function in manifest.functions:
        if function.name in L1_FUNCTIONS:
            assert function.risk == "safe"
            assert function.effect == "read"
            assert function.approval_required is False


def test_l2_maintenance_functions_are_registered() -> None:
    manifest = FakeSystemPlugin().manifest()
    functions = {function.name: function for function in manifest.functions}

    assert not (L2_FUNCTIONS & set(functions))


def test_exec_functions_are_registered_as_conservative_maintenance() -> None:
    manifest = FakeSystemPlugin().manifest()
    functions = {function.name: function for function in manifest.functions}

    assert EXEC_FUNCTIONS.issubset(functions)
    for name in EXEC_FUNCTIONS:
        function = functions[name]
        assert function.risk == "maintenance"
        assert function.effect == "write"
        assert function.conflict_policy == "serialize"
        assert function.resource_keys == ["node.exec"]


def test_manifest_contains_expected_l1_capacity() -> None:
    manifest = FakeSystemPlugin().manifest()

    assert manifest.plugin_version == "0.6.0"
    assert len(manifest.functions) == 10
    assert len(manifest.signals) == 3


def test_transfer_external_functions_are_registered() -> None:
    manifest = FakeSystemPlugin().manifest()
    functions = {function.name: function for function in manifest.functions}

    assert TRANSFER_EXTERNAL_FUNCTIONS.issubset(functions)
    for name in TRANSFER_EXTERNAL_FUNCTIONS:
        function = functions[name]
        assert function.risk == "maintenance"
        assert function.effect == "external"
        assert function.conflict_policy == "serialize"
        assert function.supports_progress is True
        assert function.supports_cancel is True
        assert function.supports_resume is True
        assert function.progress_contract == "transfer_progress_v1"
        assert function.required_intent_slots
        assert function.required_intent_slots


def test_yq_croc_runtime_requirements_are_role_specific() -> None:
    manifest = FakeSystemPlugin().manifest()
    functions = {function.name: function for function in manifest.functions}

    status_req = functions["windows.transfer.croc.status"].execution_requirements
    assert status_req == {
        "runtime_kind": "privileged",
        "labels": ["network-control", "disk-inspection"],
        "interactive": False,
    }

    reconcile_req = functions["windows.transfer.croc.reconcile"].execution_requirements
    assert reconcile_req == {
        "runtime_kind": "privileged",
        "labels": ["disk-inspection"],
        "interactive": False,
    }

    for name in TRANSFER_EXTERNAL_FUNCTIONS:
        requirements = functions[name].execution_requirements
        assert requirements == {
            "runtime_kind": "interactive",
            "labels": ["windows", "profile", "filesystem", "transfer", "yq-croc"],
            "interactive": True,
            "privilege": "user",
        }


def test_yq_croc_user_worker_allowlist_includes_interactive_transfer_functions() -> None:
    from node_win_client.runtime_context import user_context_required

    assert user_context_required("windows.transfer.local.stat") is True
    assert user_context_required("windows.transfer.croc.send") is True
    assert user_context_required("windows.transfer.croc.receive") is True
    assert user_context_required("windows.transfer.croc.status") is False
    assert user_context_required("windows.transfer.croc.reconcile") is False


def test_artifact_write_functions_are_registered() -> None:
    manifest = FakeSystemPlugin().manifest()
    functions = {function.name: function for function in manifest.functions}

    assert ARTIFACT_WRITE_FUNCTIONS.issubset(functions)
    function = functions["windows.artifact.download_file"]
    assert function.risk == "maintenance"
    assert function.effect == "write"
    assert function.conflict_policy == "serialize"
    assert function.execution_requirements is not None
    assert function.execution_requirements["runtime_kind"] == "interactive"
    assert "artifact" in function.execution_requirements["labels"]
    assert function.required_intent_slots == ["artifact_id", "output_path"]
    assert function.required_intent_slots == ["artifact_id", "output_path"]


def test_l2b_functions_are_registered() -> None:
    manifest = FakeSystemPlugin().manifest()
    functions = {function.name: function for function in manifest.functions}

    assert L2B_FUNCTIONS.issubset(functions)
    for name in L2B_FUNCTIONS:
        function = functions[name]
        assert function.resource_key_template or function.effect == "read"


@pytest.mark.parametrize(
    ("function_name", "input_data", "expected_key"),
    [
        ("windows.transfer.croc.status", {}, "installed"),
    ],
)
def test_l1_readonly_functions_execute(
    function_name: str, input_data: dict[str, object], expected_key: str
) -> None:
    output = asyncio.run(FakeSystemPlugin().execute(function_name, input_data))

    assert expected_key in output


def test_yq_croc_status_reports_missing_configured_binary() -> None:
    plugin = FakeSystemPlugin(
        transfer_yq_croc=TransferYqCrocConfig(binary_path="Z:\\missing\\yq-croc.exe")
    )

    output = asyncio.run(plugin.execute("windows.transfer.croc.status", {}))

    assert output["transport"] == "croc"
    assert output["runtime"] == "yq-croc"
    assert output["installed"] is False
    assert output["error"]["code"] == "yq_croc_binary_missing"


def test_yq_croc_status_reports_firewall_outbound_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from node_win_client import transfer_yq_croc

    binary = tmp_path / "yq-croc.exe"
    binary.write_bytes(b"test")

    def fake_probe(binary_path: str | None, args: list[str]) -> dict[str, object]:
        assert binary_path == str(binary)
        command = args[0]
        if command == "version":
            return {
                "ok": True,
                "payload": {
                    "runtime": "yq-croc",
                    "runtime_version": "test",
                    "upstream_croc_version": "v10.4.6",
                },
            }
        if command == "probe":
            return {"ok": True, "payload": {"runtime": "yq-croc"}}
        if command == "relay-probe":
            return {
                "ok": True,
                "payload": {"runtime": "yq-croc", "relay_reachable": True},
            }
        raise AssertionError(f"unexpected probe args: {args}")

    monkeypatch.setattr(transfer_yq_croc, "_run_json_probe", fake_probe)
    monkeypatch.setattr(
        transfer_yq_croc,
        "_windows_firewall_outbound_facts",
        lambda binary_path: {"platform": "windows", "allows_outbound": False},
    )

    status = transfer_yq_croc.probe_yq_croc_status(TransferYqCrocConfig(binary_path=str(binary)))

    assert status["installed"] is True
    assert status["executable"] is True
    assert status["relay_reachable"] is True
    assert status["firewall_allows_outbound"] is False
    assert status["ready"] is False
    assert status["error"]["code"] == "windows_firewall_outbound_blocked"


def test_yq_croc_status_probes_requested_relay_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from node_win_client import transfer_yq_croc

    binary = tmp_path / "yq-croc.exe"
    binary.write_bytes(b"test")
    observed_args: list[list[str]] = []

    def fake_probe(binary_path: str | None, args: list[str]) -> dict[str, object]:
        assert binary_path == str(binary)
        observed_args.append(args)
        command = args[0]
        if command == "version":
            return {
                "ok": True,
                "payload": {
                    "runtime": "yq-croc",
                    "runtime_version": "test",
                    "upstream_croc_version": "v10.4.6",
                },
            }
        if command == "probe":
            return {"ok": True, "payload": {"runtime": "yq-croc"}}
        if command == "relay-probe":
            return {
                "ok": True,
                "payload": {
                    "runtime": "yq-croc",
                    "relay_url": "127.0.0.1:29009",
                    "relay_reachable": True,
                },
            }
        raise AssertionError(f"unexpected probe args: {args}")

    monkeypatch.setattr(transfer_yq_croc, "_run_json_probe", fake_probe)
    monkeypatch.setattr(
        transfer_yq_croc,
        "_windows_firewall_outbound_facts",
        lambda binary_path: {"platform": "windows", "allows_outbound": True},
    )
    plugin = FakeSystemPlugin(transfer_yq_croc=TransferYqCrocConfig(binary_path=str(binary)))

    status = asyncio.run(
        plugin.execute("windows.transfer.croc.status", {"relay_url": "127.0.0.1:29009"})
    )

    assert status["ready"] is True
    assert status["relay_mode"] == "configured"
    assert status["relay_url"] == "127.0.0.1:29009"
    assert observed_args[-1] == ["relay-probe", "--timeout", "5s", "--relay", "127.0.0.1:29009"]


def test_yq_croc_reconcile_reports_ledger(tmp_path: Path) -> None:
    plugin = FakeSystemPlugin(
        transfer_yq_croc=TransferYqCrocConfig(temp_dir=str(tmp_path)),
    )

    output = asyncio.run(
        plugin.execute("windows.transfer.croc.reconcile", {"transfer_id": "trf_test"})
    )

    assert output["transport"] == "croc"
    assert output["runtime"] == "yq-croc"
    assert output["transfer_id"] == "trf_test"
    assert output["count"] == 0


def test_yq_croc_send_does_not_require_asyncio_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from node_win_client import transfer_yq_croc

    binary = tmp_path / "yq-croc.exe"
    binary.write_bytes(b"test")
    source = tmp_path / "payload.bin"
    source.write_bytes(b"transfer payload")
    observed_process_args: list[list[str]] = []
    observed_requests: list[dict[str, object]] = []

    async def fail_asyncio_subprocess(*args, **kwargs):
        del args, kwargs
        raise NotImplementedError("selector event loop does not support subprocesses")

    def fake_probe(binary_path: str | None, args: list[str]) -> dict[str, object]:
        assert binary_path == str(binary)
        command = args[0]
        if command == "version":
            return {
                "ok": True,
                "payload": {
                    "runtime": "yq-croc",
                    "runtime_version": "test",
                    "upstream_croc_version": "v10.4.6",
                },
            }
        if command == "probe":
            return {"ok": True, "payload": {"runtime": "yq-croc"}}
        if command == "relay-probe":
            return {"ok": True, "payload": {"runtime": "yq-croc", "relay_reachable": True}}
        raise AssertionError(f"unexpected probe args: {args}")

    class FakePopen:
        pid = 4242
        returncode = 0

        def __init__(self, args, **kwargs) -> None:
            del kwargs
            observed_process_args.append([str(item) for item in args])
            observed_requests.append(json.loads(Path(args[3]).read_text(encoding="utf-8")))
            self.stdout = io.StringIO(
                '{"event":"sender_ready","data":{}}\n'
                '{"event":"bytes_progress","data":{"bytes_transferred":16,"total_bytes":16}}\n'
                '{"event":"transfer_done","data":{}}\n'
            )
            self.stderr = io.StringIO("")

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            del timeout
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_asyncio_subprocess)
    monkeypatch.setattr(transfer_yq_croc, "_run_json_probe", fake_probe)
    monkeypatch.setattr(
        transfer_yq_croc,
        "_windows_firewall_outbound_facts",
        lambda binary_path: {"platform": "windows", "allows_outbound": True},
    )
    monkeypatch.setattr(transfer_yq_croc.subprocess, "Popen", FakePopen)
    monkeypatch.setattr("node_win_client.plugins.runs_as_system_account", lambda: False)

    progress_events: list[dict[str, object]] = []
    plugin = FakeSystemPlugin(
        transfer_yq_croc=TransferYqCrocConfig(
            binary_path=str(binary),
            temp_dir=str(tmp_path / "transfers"),
        )
    )
    output = asyncio.run(
        plugin.execute(
            "windows.transfer.croc.send",
            {
                "transfer_id": "trf_test",
                "attempt": 1,
                "source_path": str(source),
                "code": "yequ-test-code",
                "__job_event_callback": lambda _event_type, data: progress_events.append(data),
            },
        )
    )

    assert output["runtime"] == "yq-croc"
    assert output["status"] == "succeeded"
    assert observed_process_args[0][1] == "send"
    assert observed_requests[0]["source_path"] == str(source.resolve())
    assert any(event.get("sender_ready") is True for event in progress_events)


def test_transfer_local_stat_reports_file_hash(tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("transfer payload", encoding="utf-8")

    output = asyncio.run(
        FakeSystemPlugin().execute(
            "windows.transfer.local.stat",
            {"path": str(sample), "sha256": True},
        )
    )

    assert output["found"] is True
    assert output["is_file"] is True
    assert output["size_bytes"] == len("transfer payload")
    assert output["sha256"]


def test_artifact_download_file_streams_and_verifies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"center artifact bytes"
    expected_sha256 = "ad56404f5094b92cbd268639df7536b4dcf4a59284af8a2791a8790768130953"
    output_path = tmp_path / "artifact.bin"

    class FakeResponse:
        status_code = 200
        headers = {
            "X-YeQu-Artifact-Sha256": expected_sha256,
            "content-type": "application/octet-stream",
        }
        text = ""

        def iter_bytes(self):
            yield payload[:7]
            yield payload[7:]

    class FakeStream:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

        def __enter__(self):
            assert self.args[0] == "GET"
            assert self.args[1].endswith("/yqp/artifacts/id_test/download")
            assert self.kwargs["headers"]["Authorization"] == "Bearer node-token"
            return FakeResponse()

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr("node_win_client.plugins.httpx.stream", FakeStream)

    plugin = FakeSystemPlugin(
        center_base_url="https://center.example",
        node_token="node-token",
    )
    output = asyncio.run(
        plugin.execute(
            "windows.artifact.download_file",
            {
                "artifact_id": "id_test",
                "output_path": str(output_path),
                "mode": "fail_if_exists",
            },
        )
    )

    assert output_path.read_bytes() == payload
    assert output["artifact_id"] == "id_test"
    assert output["output_path"] == str(output_path.resolve())
    assert output["size_bytes"] == len(payload)
    assert output["sha256"] == expected_sha256
    assert output["verified"] is True


def test_file_upload_artifact_returns_pending_upload(tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("artifact payload", encoding="utf-8")

    output = asyncio.run(
        FakeSystemPlugin().execute(
            "windows.file.upload_artifact",
            {"path": str(sample), "title": "sample.txt"},
        )
    )

    assert output["artifact_pending"] is True
    assert output["artifact_type"] == "file"
    upload = output["__artifact_uploads"][0]
    assert upload["path"] == str(sample.resolve())
    assert upload["content_type"] == "text/plain"
    assert upload["delete_after_upload"] is False


def test_everything_find_is_not_limited_by_file_root_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "usb-file.txt"
    target.write_text("ok", encoding="utf-8")
    policy = L2Policy(allowed_file_roots=[str(tmp_path / "allowed")])

    monkeypatch.setattr(l2b, "_find_everything_cli", lambda: "C:\\Program Files\\Everything\\es.exe")
    monkeypatch.setattr(l2b, "_everything_candidates", lambda *args, **kwargs: ([target], None))
    output = execute_l2b(
        "windows.everything.find",
        {"root": str(outside), "query": "usb", "mode": "substring"},
        policy,
    )

    assert output["matches"][0]["name"] == "usb-file.txt"


def test_l2b_file_search_uses_everything_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "SillyTavern-1.17.0.zip"
    target.write_text("zip", encoding="utf-8")
    other = root / "notes.txt"
    other.write_text("notes", encoding="utf-8")
    policy = L2Policy(allowed_file_roots=[str(tmp_path / "allowed")])

    monkeypatch.setattr(l2b, "_find_everything_cli", lambda: "C:\\Program Files\\Everything\\es.exe")
    monkeypatch.setattr(
        l2b,
        "_everything_candidates",
        lambda *args, **kwargs: ([target, other], None),
    )

    output = execute_l2b(
        "windows.everything.find",
        {"root": str(root), "query": "silly tavern", "mode": "fuzzy", "limit": 10},
        policy,
    )

    assert output["backend"] == "everything"
    assert output["everything_available"] is True
    assert [item["name"] for item in output["matches"]] == ["SillyTavern-1.17.0.zip"]
    assert output["matches"][0]["match_type"] in {"substring_name", "fuzzy_name"}


def test_l2b_file_search_requires_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(l2b, "_find_everything_cli", lambda: None)

    with pytest.raises(ValueError, match="everything_unavailable"):
        execute_l2b("windows.everything.find", {"query": "archive.zip"}, L2Policy())


def test_l2_policy_loads_from_config(tmp_path: Path) -> None:
    config = tmp_path / "node.yaml"
    config.write_text(
        """
center_base_url: "http://127.0.0.1:9800"
l2:
  allowed_services:
    - Spooler
  allowed_task_prefixes:
    - "\\\\YeQu\\\\"
  allowed_temp_paths:
    - "%TEMP%"
""",
        encoding="utf-8",
    )

    settings = load_settings(str(config))

    assert settings.l2_policy.allowed_services == ["Spooler"]
    assert settings.l2_policy.allowed_task_prefixes == ["\\YeQu\\"]
    assert settings.l2_policy.allowed_temp_paths == ["%TEMP%"]


def test_safety_allowed_services_feed_l2_policy(tmp_path: Path) -> None:
    config = tmp_path / "node.yaml"
    config.write_text(
        """
center:
  base_url: "http://127.0.0.1:9800"
node:
  node_id: winClient
  token: token
safety:
  allow_write_actions: true
  allowed_services:
    - EventLog
""",
        encoding="utf-8",
    )

    settings = load_settings(str(config))

    assert settings.l2_policy.allowed_services == ["EventLog"]
