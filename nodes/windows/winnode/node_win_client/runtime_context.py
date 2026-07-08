from __future__ import annotations

import getpass
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import TransferYqCrocConfig
from .models import RuntimeInstancePayload
from .transfer_yq_croc import probe_yq_croc_status

USER_WORKER_PORT = int(os.getenv("YEQU_USER_WORKER_PORT", "9817"))
USER_WORKER_URL = os.getenv("YEQU_USER_WORKER_URL", f"http://127.0.0.1:{USER_WORKER_PORT}")

SYSTEM_RUNTIME_NAME = "system"
INTERACTIVE_RUNTIME_NAME = "interactive-user"

SYSTEM_LABELS = [
    "windows",
    "exec",
    "telemetry",
    "network-control",
    "disk-inspection",
    "maintenance",
]

INTERACTIVE_LABELS = [
    "windows",
    "exec",
    "profile",
    "filesystem",
    "artifact",
    "desktop",
    "startup",
    "user-env",
]

USER_CONTEXT_FUNCTIONS = {
    "windows.exec.run",
    "windows.everything.find",
    "windows.screen.capture",
    "windows.artifact.download_file",
    "windows.file.upload_artifact",
    "windows.transfer.local.stat",
    "windows.transfer.croc.send",
    "windows.transfer.croc.receive",
}
USER_CONTEXT_PREFIXES: tuple[str, ...] = ()


def runtime_id(node_id: str, name: str) -> str:
    return f"{node_id}/runtime/{name}"


def system_runtime_id(node_id: str) -> str:
    return runtime_id(node_id, SYSTEM_RUNTIME_NAME)


def interactive_runtime_id(node_id: str) -> str:
    return runtime_id(node_id, INTERACTIVE_RUNTIME_NAME)


def runs_as_system_account() -> bool:
    user = current_user().lower()
    return user in {"system", "localsystem"} or user.endswith("$")


def current_user() -> str:
    return (os.environ.get("USERNAME") or getpass.getuser() or "unknown").strip()


def user_context_required(function: str) -> bool:
    return function in USER_CONTEXT_FUNCTIONS or function.startswith(USER_CONTEXT_PREFIXES)


def execution_requirements_for_function(
    function: str,
    legacy_context: str = "system",
) -> dict[str, Any]:
    if function == "windows.exec.run":
        return {
            "allowed_runtime_kinds": ["interactive", "privileged"],
            "labels": ["windows", "exec"],
            "execution_profiles": [
                "user.readonly",
                "user.write",
                "admin.readonly",
                "admin.write",
            ],
        }
    if function in {"windows.transfer.croc.send", "windows.transfer.croc.receive"}:
        return {
            "runtime_kind": "interactive",
            "labels": ["windows", "profile", "filesystem", "transfer", "yq-croc"],
            "interactive": True,
            "privilege": "user",
        }
    if function == "windows.transfer.croc.status":
        return {
            "runtime_kind": "privileged",
            "labels": ["network-control", "disk-inspection"],
            "interactive": False,
        }
    if function == "windows.transfer.croc.reconcile":
        return {
            "runtime_kind": "privileged",
            "labels": ["disk-inspection"],
            "interactive": False,
        }
    if user_context_required(function) or legacy_context == "user":
        labels = ["profile", "filesystem"]
        if function.startswith("windows.artifact."):
            labels.append("artifact")
        return {
            "runtime_kind": "interactive",
            "labels": labels,
            "interactive": True,
            "privilege": "user",
        }
    return {
        "runtime_kind": "privileged",
        "labels": _system_labels_for_function(function),
        "interactive": False,
    }


def _system_labels_for_function(function: str) -> list[str]:
    if function == "windows.transfer.croc.status":
        return ["network-control", "disk-inspection"]
    if function == "windows.transfer.croc.reconcile":
        return ["disk-inspection"]
    if function == "windows.screen.capture":
        return ["desktop"]
    return ["telemetry"]


def should_use_user_runtime(
    function: str,
    *,
    runtime_id_value: str | None = None,
    execution_requirements: dict[str, Any] | None = None,
) -> bool:
    if runtime_id_value and runtime_id_value.rsplit("/", 1)[-1] == INTERACTIVE_RUNTIME_NAME:
        return True
    requirements = execution_requirements or {}
    if requirements.get("runtime_kind") == "interactive":
        return True
    if requirements.get("interactive") is True:
        return True
    return not runtime_id_value and user_context_required(function)


async def collect_runtime_instances(
    node_id: str,
    transfer_yq_croc: TransferYqCrocConfig | None = None,
) -> list[RuntimeInstancePayload]:
    now = datetime.now(UTC)
    user_worker = await probe_user_worker()
    interactive_online = not runs_as_system_account() or user_worker.get("ok") is True
    interactive_status = "online" if interactive_online else "offline"
    user_profiles = [
        {
            "profile": "user.readonly",
            "filesystem_intent": "readonly",
            "timeout_sec": 10,
            "max_stdout_bytes": 65536,
            "max_stderr_bytes": 8192,
        },
        {
            "profile": "user.write",
            "filesystem_intent": "write",
            "timeout_sec": 10,
            "max_stdout_bytes": 65536,
            "max_stderr_bytes": 8192,
        },
    ]
    admin_profiles = [
        {
            "profile": "admin.readonly",
            "filesystem_intent": "readonly",
            "timeout_sec": 10,
            "max_stdout_bytes": 65536,
            "max_stderr_bytes": 8192,
        },
        {
            "profile": "admin.write",
            "filesystem_intent": "write",
            "timeout_sec": 10,
            "approval_required": True,
            "max_stdout_bytes": 65536,
            "max_stderr_bytes": 8192,
        },
    ]
    system_profiles = admin_profiles if is_elevated_admin() else []
    runtimes = [
        RuntimeInstancePayload(
            runtime_id=system_runtime_id(node_id),
            kind="privileged",
            status="online",
            labels=SYSTEM_LABELS,
            owner="system",
            privilege="system",
            interactive=False,
            last_seen_at=now,
            metadata={
                "account": current_user(),
                "host_runtime": "service",
                "execution_profiles": system_profiles,
            },
        ),
        RuntimeInstancePayload(
            runtime_id=interactive_runtime_id(node_id),
            kind="interactive",
            status=interactive_status,
            labels=INTERACTIVE_LABELS,
            owner=str(user_worker.get("user") or current_user()),
            privilege="user",
            interactive=True,
            last_seen_at=now if interactive_online else None,
            metadata={
                "worker_url": USER_WORKER_URL,
                "worker_pid": user_worker.get("pid"),
                "host_runtime": "user_session",
                "execution_profiles": user_profiles,
                "reason": user_worker.get("error") if not interactive_online else None,
            },
        ),
    ]
    if transfer_yq_croc is not None:
        status = probe_yq_croc_status(transfer_yq_croc)
        if not interactive_online:
            runtime_status = "offline"
        elif status.get("ready"):
            runtime_status = "online"
        elif status.get("installed"):
            runtime_status = "degraded"
        else:
            runtime_status = "not_installed"
        runtimes.append(
            RuntimeInstancePayload(
                runtime_id=runtime_id(node_id, "yq-croc-transfer"),
                kind="interactive",
                status=runtime_status,
                labels=["windows", "profile", "filesystem", "transfer", "yq-croc"],
                owner=str(user_worker.get("user") or current_user()),
                privilege="user",
                interactive=True,
                last_seen_at=now if runtime_status == "online" else None,
                metadata={
                    "runtime": "yq-croc",
                    "binary_path": status.get("binary_path"),
                    "runtime_version": status.get("runtime_version"),
                    "upstream_croc_version": status.get("upstream_croc_version"),
                    "relay_mode": status.get("relay_mode"),
                    "relay_url": status.get("relay_url"),
                    "relay_reachable": status.get("relay_reachable"),
                    "firewall_allows_outbound": status.get("firewall_allows_outbound"),
                    "allow_send": status.get("allow_send"),
                    "allow_receive": status.get("allow_receive"),
                    "temp_dir": status.get("temp_dir"),
                    "error": status.get("error"),
                    "interactive_worker": user_worker,
                },
            )
        )
    return runtimes


def is_elevated_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


async def probe_user_worker(timeout_sec: float = 0.5) -> dict[str, Any]:
    if not runs_as_system_account():
        return {"ok": True, "user": current_user(), "pid": os.getpid(), "source": "current_process"}
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            response = await client.get(f"{USER_WORKER_URL}/health")
        if response.status_code != 200:
            return {"ok": False, "error": f"http_{response.status_code}"}
        payload = response.json()
        return payload if isinstance(payload, dict) else {"ok": False, "error": "invalid_payload"}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}
