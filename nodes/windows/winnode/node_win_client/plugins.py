from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import platform
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from shutil import disk_usage
from typing import Any

import httpx

from .capabilities import CapabilityContext, execute_capability, load_capabilities
from .config import L2Policy, TransferYqCrocConfig
from .errors import NodeExecutionError
from .l2b import _validate_file_path, build_l2b_manifests, execute_l2b
from .models import (
    FunctionManifest,
    JobEventType,
    PluginManifest,
    SignalManifest,
    SignalValue,
)
from .runtime_context import (
    USER_WORKER_URL,
    execution_requirements_for_function,
    runs_as_system_account,
    should_use_user_runtime,
)
from .transfer_yq_croc import yq_croc_reconcile


class FakeSystemPlugin:
    plugin_id = "windows.capabilities"
    plugin_version = "0.6.0"

    def __init__(
        self,
        l2_policy: L2Policy | None = None,
        transfer_yq_croc: TransferYqCrocConfig | None = None,
        center_base_url: str | None = None,
        node_token: str | None = None,
        request_timeout_sec: float = 30.0,
    ) -> None:
        self.l2_policy = l2_policy or L2Policy()
        self.transfer_yq_croc = transfer_yq_croc or TransferYqCrocConfig()
        self.center_base_url = (center_base_url or "").rstrip("/")
        self.node_token = node_token or ""
        self.request_timeout_sec = request_timeout_sec
        self._capabilities = load_capabilities()

    def manifest(self) -> PluginManifest:
        functions = [capability.manifest for capability in self._capabilities.values()]
        _apply_execution_requirements(functions)
        return PluginManifest(
            plugin_id=self.plugin_id,
            plugin_version=self.plugin_version,
            functions=functions,
            signals=[
                SignalManifest(
                    name="windows.cpu.usage",
                    scope="node",
                    ttl_sec=15,
                    value_schema={"type": "number", "minimum": 0, "maximum": 100},
                ),
                SignalManifest(
                    name="windows.memory.usage",
                    scope="node",
                    ttl_sec=15,
                    value_schema={"type": "number", "minimum": 0, "maximum": 100},
                ),
                SignalManifest(
                    name="windows.disk.usage",
                    scope="node",
                    ttl_sec=30,
                    value_schema={"type": "number", "minimum": 0, "maximum": 100},
                ),
            ],
        )

    def collect_signals(self) -> list[SignalValue]:
        snapshot = collect_metrics()
        now = datetime.now(UTC)
        return [
            SignalValue(
                name="windows.cpu.usage",
                value=snapshot["cpu"],
                collected_at=now,
                ttl_sec=15,
            ),
            SignalValue(
                name="windows.memory.usage",
                value=snapshot["memory"],
                collected_at=now,
                ttl_sec=15,
            ),
            SignalValue(
                name="windows.disk.usage",
                value=snapshot["disk"],
                collected_at=now,
                ttl_sec=30,
            ),
        ]

    async def execute(self, function: str, input_data: dict[str, Any]) -> dict[str, Any]:
        runtime_id = str(input_data.pop("__runtime_id", "") or "")
        requirements = input_data.pop("__execution_requirements", {})
        if not isinstance(requirements, dict):
            requirements = {}

        if _should_proxy_to_user_worker(function, runtime_id=runtime_id, requirements=requirements):
            proxied = await _try_user_worker_execute(function, input_data)
            if proxied is not None:
                return proxied

        if function not in self._capabilities:
            raise ValueError(f"function not supported: {function}")

        job_event_callback = _pop_job_event_callback(input_data)
        return await execute_capability(
            self._capabilities,
            function,
            input_data,
            CapabilityContext(
                l2_policy=self.l2_policy,
                transfer_yq_croc=self.transfer_yq_croc,
                center_base_url=self.center_base_url,
                node_token=self.node_token,
                request_timeout_sec=self.request_timeout_sec,
                job_event_callback=job_event_callback,
            ),
        )

def _apply_execution_requirements(functions: list[FunctionManifest]) -> None:
    for function in functions:
        function.execution_requirements = execution_requirements_for_function(
            function.name,
            function.execution_context,
        )


JobEventCallback = Callable[[JobEventType, dict[str, Any]], Any]


def _pop_job_event_callback(input_data: dict[str, Any]) -> JobEventCallback | None:
    callback = input_data.pop("__job_event_callback", None)
    if callback is None:
        return None
    if not callable(callback):
        raise TypeError("__job_event_callback must be callable")
    return callback


def _yq_croc_progress_callback(
    callback: JobEventCallback | None,
) -> Callable[[str, dict[str, Any]], Any] | None:
    if callback is None:
        return None

    def emit(_event_type: str, payload: dict[str, Any]) -> Any:
        return callback(JobEventType.PROGRESS, payload)

    return emit


def transfer_local_stat(input_data: dict[str, Any], l2_policy: L2Policy) -> dict[str, Any]:
    path = _validate_file_path(input_data.get("path"), l2_policy)
    include_sha256 = bool(input_data.get("sha256", False))
    parent = path if path.is_dir() else path.parent
    found = path.exists()
    result: dict[str, Any] = {
        "path": str(path),
        "found": found,
        "parent": str(parent),
        "parent_exists": parent.exists(),
        "readable": os.access(path if found else parent, os.R_OK),
        "writable": os.access(parent, os.W_OK),
        "free_bytes": None,
        "sha256": None,
    }
    try:
        if parent.exists():
            result["free_bytes"] = disk_usage(parent).free
    except OSError as exc:
        result["disk_error"] = str(exc)

    if not found:
        return result

    stat = path.stat()
    result.update(
        {
            "name": path.name,
            "is_file": path.is_file(),
            "is_dir": path.is_dir(),
            "size_bytes": stat.st_size if path.is_file() else _directory_size(path),
            "mtime": stat.st_mtime,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        }
    )
    if include_sha256 and path.is_file():
        result["sha256"] = _file_sha256(path)
    return result


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _should_proxy_to_user_worker(
    function: str,
    *,
    runtime_id: str = "",
    requirements: dict[str, Any] | None = None,
) -> bool:
    if os.getenv("YEQU_USER_WORKER") == "1":
        return False
    if os.getenv("YEQU_DISABLE_USER_WORKER_PROXY") == "1":
        return False
    if not runs_as_system_account():
        return False
    return should_use_user_runtime(
        function,
        runtime_id_value=runtime_id or None,
        execution_requirements=requirements,
    )


async def _try_user_worker_execute(
    function: str,
    input_data: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{USER_WORKER_URL}/execute",
                json={"function": function, "input": input_data},
            )
        if response.status_code == 404:
            raise NodeExecutionError(
                "user_worker_route_missing",
                "User Worker is reachable but does not expose the execute route.",
                {"url": USER_WORKER_URL, "function": function},
            )
        if response.status_code == 403:
            raise NodeExecutionError(
                "user_worker_function_denied",
                f"User Worker refused function: {function}",
                {"url": USER_WORKER_URL, "function": function},
            )
        payload: dict[str, Any] | None = None
        try:
            decoded = response.json()
            if isinstance(decoded, dict):
                payload = decoded
        except ValueError:
            payload = None
        if response.status_code >= 400:
            error = payload.get("error") if payload else None
            if isinstance(error, dict):
                raise NodeExecutionError(
                    str(error.get("code") or "user_worker_execution_failed"),
                    str(
                        error.get("message")
                        or f"User Worker returned HTTP {response.status_code}."
                    ),
                    {
                        "origin": "user_worker",
                        "function": function,
                        "worker_error": error,
                        "http_status": response.status_code,
                    },
                )
            response.raise_for_status()
        if payload is None:
            payload = response.json()
        if not payload.get("ok"):
            error = payload.get("error")
            if isinstance(error, dict):
                raise NodeExecutionError(
                    str(error.get("code") or "user_worker_execution_failed"),
                    str(error.get("message") or "User Worker execution failed."),
                    {"origin": "user_worker", "function": function, "worker_error": error},
                )
            raise NodeExecutionError(
                "user_worker_execution_failed",
                str(error or "User Worker execution failed."),
                {"function": function},
            )
        result = payload.get("result")
        if isinstance(result, dict):
            result.setdefault("execution_context", "user")
            return result
        return {"result": result, "execution_context": "user"}
    except NodeExecutionError:
        raise
    except httpx.TimeoutException as exc:
        raise NodeExecutionError(
            "user_worker_timeout",
            "User Worker did not respond before timeout.",
            {"url": USER_WORKER_URL, "function": function},
        ) from exc
    except httpx.ConnectError as exc:
        raise NodeExecutionError(
            "user_worker_unreachable",
            (
                "User Worker is not running or not reachable; install/start Hybrid User "
                "Worker for user-profile tools."
            ),
            {"url": USER_WORKER_URL, "function": function},
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise NodeExecutionError(
            "user_worker_http_error",
            f"User Worker returned HTTP {exc.response.status_code}.",
            {"url": USER_WORKER_URL, "function": function, "body": exc.response.text[:1000]},
        ) from exc
    except Exception as exc:
        raise NodeExecutionError(
            "user_worker_error",
            str(exc) or "User Worker execution failed.",
            {"url": USER_WORKER_URL, "function": function},
        ) from exc


def collect_metrics() -> dict[str, Any]:
    try:
        import psutil

        cpu = float(psutil.cpu_percent(interval=0.1))
        memory = float(psutil.virtual_memory().percent)
    except Exception:
        cpu = _fallback_cpu_percent()
        memory = 0.0

    total, used, _free = disk_usage(os.getcwd())
    disk = round((used / total) * 100, 2) if total else 0.0
    return {
        "cpu": round(cpu, 2),
        "memory": round(memory, 2),
        "disk": disk,
        "hostname": platform.node(),
        "platform": platform.platform(),
    }


def capture_screen(input_data: dict[str, Any]) -> dict[str, Any]:
    title = str(input_data.get("title") or "windows-screen-capture.png")
    if not title.lower().endswith(".png"):
        title += ".png"
    temp_path = Path(tempfile.gettempdir()) / f"yequ-screen-{int(time.time() * 1000)}.png"
    command = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen
$bounds = $screen.Bounds
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $bitmap.Save($env:YEQU_SCREENSHOT_PATH, [System.Drawing.Imaging.ImageFormat]::Png)
    [PSCustomObject]@{
        width = $bounds.Width
        height = $bounds.Height
        primary = $screen.Primary
        device_name = $screen.DeviceName
    } | ConvertTo-Json -Depth 4
}
finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
"""
    details = _run_powershell_json(
        command,
        {"YEQU_SCREENSHOT_PATH": str(temp_path)},
        timeout_sec=10,
    )
    if not temp_path.is_file():
        raise RuntimeError("screen capture did not produce an image file")
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "format": "png",
        "screen": details if isinstance(details, dict) else {},
        "__artifact_uploads": [
            {
                "path": str(temp_path),
                "artifact_type": "screenshot",
                "content_type": "image/png",
                "title": title,
                "summary": {"kind": "screen_capture", "platform": "windows"},
                "metadata": {"producer": "windows.screen.capture"},
                "delete_after_upload": True,
            }
        ],
    }


def file_upload_artifact(input_data: dict[str, Any], l2_policy: L2Policy) -> dict[str, Any]:
    path = _validate_file_path(input_data.get("path"), l2_policy)
    max_bytes = int(input_data.get("max_bytes") or 50 * 1024 * 1024)
    if not path.is_file():
        raise ValueError(f"path is not a file: {path}")
    size_bytes = path.stat().st_size
    if size_bytes > max_bytes:
        raise ValueError(f"file is larger than max_bytes: {size_bytes} > {max_bytes}")
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    title = str(input_data.get("title") or path.name)
    return _artifact_pending_output(
        artifact_type="file",
        path=path,
        content_type=content_type,
        title=title,
        summary={"kind": "file_upload", "size_bytes": size_bytes},
        metadata={"producer": "windows.file.upload_artifact", "source_path": str(path)},
        delete_after_upload=False,
    )


def artifact_download_file(
    input_data: dict[str, Any],
    l2_policy: L2Policy,
    *,
    center_base_url: str,
    node_token: str,
    timeout_sec: float,
) -> dict[str, Any]:
    artifact_id = str(input_data.get("artifact_id") or "").strip()
    if not artifact_id:
        raise NodeExecutionError("invalid_input", "artifact_id is required")
    output_path = _validate_file_path(input_data.get("output_path"), l2_policy)
    mode = str(input_data.get("mode") or "fail_if_exists")
    if mode not in {"fail_if_exists", "overwrite"}:
        raise NodeExecutionError("invalid_input", "mode must be fail_if_exists or overwrite")
    if output_path.exists() and mode == "fail_if_exists":
        raise NodeExecutionError(
            "target_exists",
            f"target already exists: {output_path}",
            {"path": str(output_path)},
        )
    if not center_base_url:
        raise NodeExecutionError(
            "external_service_failed",
            "center_base_url is not configured for artifact download",
            retryable=False,
        )
    if not node_token:
        raise NodeExecutionError(
            "external_service_failed",
            "node token is not configured for artifact download",
            retryable=False,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"{center_base_url.rstrip('/')}/yqp/artifacts/{artifact_id}/download"
    sha256 = hashlib.sha256()
    size_bytes = 0
    headers = {
        "Authorization": f"Bearer {node_token}",
        "User-Agent": "YeQu-Node-winClient/0.1",
    }
    try:
        with httpx.stream(
            "GET",
            url,
            headers=headers,
            timeout=max(1.0, float(timeout_sec)),
            follow_redirects=True,
            trust_env=False,
        ) as response:
            if response.status_code == 404:
                raise NodeExecutionError(
                    "artifact_not_found",
                    f"artifact not found: {artifact_id}",
                    {"artifact_id": artifact_id},
                )
            if response.status_code >= 400:
                response.read()
                raise NodeExecutionError(
                    "external_service_failed",
                    f"artifact download HTTP {response.status_code}",
                    {
                        "artifact_id": artifact_id,
                        "status_code": response.status_code,
                        "body": response.text[:500],
                    },
                    retryable=response.status_code >= 500,
                )
            expected_sha256 = response.headers.get("X-YeQu-Artifact-Sha256")
            content_type = response.headers.get("content-type")
            with output_path.open("wb") as file:
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    file.write(chunk)
                    sha256.update(chunk)
                    size_bytes += len(chunk)
    except NodeExecutionError:
        raise
    except PermissionError as exc:
        raise NodeExecutionError(
            "permission_denied",
            f"cannot write artifact to {output_path}: {exc}",
            {"path": str(output_path)},
            category="permission",
        ) from exc
    except OSError as exc:
        raise NodeExecutionError(
            "permission_denied",
            f"cannot materialize artifact at {output_path}: {exc}",
            {"path": str(output_path)},
            category="permission",
        ) from exc
    except httpx.HTTPError as exc:
        raise NodeExecutionError(
            "external_service_failed",
            f"artifact download failed: {exc}",
            {"artifact_id": artifact_id},
            retryable=True,
        ) from exc

    actual_sha256 = sha256.hexdigest()
    if expected_sha256 and expected_sha256.lower() != actual_sha256.lower():
        raise NodeExecutionError(
            "integrity_mismatch",
            f"artifact sha256 mismatch: expected {expected_sha256}, got {actual_sha256}",
            {
                "artifact_id": artifact_id,
                "path": str(output_path),
                "expected_sha256": expected_sha256,
                "sha256": actual_sha256,
            },
        )

    return {
        "artifact_id": artifact_id,
        "output_path": str(output_path),
        "size_bytes": size_bytes,
        "sha256": actual_sha256,
        "expected_sha256": expected_sha256,
        "content_type": content_type,
        "verified": bool(not expected_sha256 or expected_sha256.lower() == actual_sha256.lower()),
    }


def _artifact_pending_output(
    *,
    artifact_type: str,
    path: Path,
    content_type: str,
    title: str,
    summary: dict[str, Any],
    metadata: dict[str, Any],
    delete_after_upload: bool,
) -> dict[str, Any]:
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "artifact_pending": True,
        "artifact_type": artifact_type,
        "title": title,
        "size_bytes": path.stat().st_size,
        "__artifact_uploads": [
            {
                "path": str(path),
                "artifact_type": artifact_type,
                "content_type": content_type,
                "title": title,
                "summary": summary,
                "metadata": metadata,
                "delete_after_upload": delete_after_upload,
            }
        ],
    }


def _fallback_cpu_percent() -> float:
    start = time.process_time()
    time.sleep(0.05)
    elapsed = max(time.process_time() - start, 0.0)
    return round(min(elapsed / 0.05 * 100, 100), 2)


def _run_powershell_json(command: str, env_vars: dict[str, str], timeout_sec: int) -> Any:
    env = os.environ.copy()
    env.update(env_vars)
    utf8_prefix = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$OutputEncoding = [System.Text.Encoding]::UTF8; "
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", utf8_prefix + command],
        capture_output=True,
        check=False,
        creationflags=_subprocess_creation_flags(),
        encoding="utf-8",
        env=env,
        errors="replace",
        text=True,
        timeout=timeout_sec,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    return _repair_powershell_text(json.loads(completed.stdout))


def _repair_powershell_text(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _repair_powershell_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_repair_powershell_text(item) for item in value]
    if not isinstance(value, str):
        return value
    if not _looks_like_mojibake(value):
        return value
    try:
        repaired = value.encode("latin1").decode("utf-8")
    except UnicodeError:
        return value
    return repaired if repaired else value


def _looks_like_mojibake(value: str) -> bool:
    markers = ("脙", "脗", "芒", "猫", "茅", "氓", "忙", "莽")
    return any(marker in value for marker in markers)


def _subprocess_creation_flags() -> int:
    if platform.system().lower() != "windows":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)




