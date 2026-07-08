from __future__ import annotations

import asyncio
import getpass
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import TransferYqCrocConfig
from .errors import NodeExecutionError

ProgressCallback = Callable[[str, dict[str, Any]], Any]

MAX_OUTPUT_TAIL = 4000
TAIL_LINE_LIMIT = 256
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "interrupted"}


def probe_yq_croc_status(
    config: TransferYqCrocConfig,
    *,
    relay_url: str | None = None,
) -> dict[str, Any]:
    binary_path, binary_error = _resolve_yq_croc_binary(config.binary_path)
    effective_relay_url = relay_url or config.relay_url
    installed = binary_path is not None
    version_probe = _run_json_probe(binary_path, ["version"]) if binary_path else None
    local_probe = _run_json_probe(binary_path, ["probe", "--json"]) if binary_path else None
    relay_args = ["relay-probe", "--timeout", "5s"]
    if effective_relay_url:
        relay_args.extend(["--relay", effective_relay_url])
    if config.relay_password_env:
        relay_args.extend(["--pass-env", config.relay_password_env])
    relay_probe = _run_json_probe(binary_path, relay_args) if binary_path else None
    version_payload = version_probe.get("payload") if version_probe else None
    local_payload = local_probe.get("payload") if local_probe else None
    relay_payload = relay_probe.get("payload") if relay_probe else None
    relay_reachable = bool((relay_payload or {}).get("relay_reachable"))

    probe_error = (
        binary_error
        or _probe_error(version_probe, "yq_croc_version_failed")
        or _probe_error(local_probe, "yq_croc_probe_failed")
        or _probe_error(relay_probe, "yq_croc_relay_unreachable")
    )
    if not relay_reachable and probe_error is None:
        probe_error = {
            "code": "yq_croc_relay_unreachable",
            "message": "yq-croc relay probe returned relay_reachable=false.",
        }
    firewall = _windows_firewall_outbound_facts(binary_path)
    firewall_allows_outbound = firewall.get("allows_outbound")
    if firewall_allows_outbound is False and probe_error is None:
        probe_error = {
            "code": "windows_firewall_outbound_blocked",
            "message": (
                "Windows Firewall outbound policy can block yq-croc. "
                "Add an outbound allow rule for transfer.yq_croc.binary_path."
            ),
        }

    return {
        "transport": "croc",
        "runtime": "yq-croc",
        "enabled": config.enabled,
        "installed": installed,
        "executable": bool(installed and version_probe and version_probe.get("ok")),
        "ready": bool(
            installed
            and config.enabled
            and relay_reachable
            and firewall_allows_outbound is not False
        ),
        "binary_path": binary_path,
        "runtime_version": _first_value(version_payload, local_payload, key="runtime_version"),
        "upstream_croc_version": _first_value(
            version_payload,
            local_payload,
            key="upstream_croc_version",
        ),
        "relay_mode": "configured" if effective_relay_url else "public_default",
        "relay_url": (relay_payload or {}).get("relay_url") or effective_relay_url,
        "relay_reachable": relay_reachable,
        "temp_dir": str(Path(config.temp_dir).resolve()),
        "temp_dir_exists": Path(config.temp_dir).exists(),
        "daemon_user": os.environ.get("USERNAME") or getpass.getuser(),
        "allow_send": config.allow_send,
        "allow_receive": config.allow_receive,
        "limits": {"max_concurrent": config.max_concurrent_transfers},
        "probe": local_payload,
        "relay_probe": relay_payload,
        "firewall_allows_outbound": firewall_allows_outbound,
        "firewall": firewall,
        "error": probe_error,
    }


def yq_croc_reconcile(
    input_data: dict[str, Any],
    config: TransferYqCrocConfig,
) -> dict[str, Any]:
    transfer_id = str(input_data.get("transfer_id") or "").strip() or None
    status_filter = str(input_data.get("status") or "").strip() or None
    limit = max(1, min(int(input_data.get("limit") or 200), 200))
    entries = _read_ledger_entries(config)
    if transfer_id:
        entries = [entry for entry in entries if entry.get("transfer_id") == transfer_id]
    if status_filter:
        entries = [entry for entry in entries if entry.get("status") == status_filter]
    entries = sorted(entries, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[
        :limit
    ]
    return {
        "runtime": "yq-croc",
        "transport": "croc",
        "transfer_id": transfer_id,
        "count": len(entries),
        "entries": entries,
    }


async def yq_croc_send(
    input_data: dict[str, Any],
    config: TransferYqCrocConfig,
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    _require_enabled(config, mode="send")
    transfer_id = _required_str(input_data, "transfer_id")
    attempt = _required_int(input_data, "attempt", minimum=1)
    source_path = Path(_required_str(input_data, "source_path")).resolve()
    code = _required_str(input_data, "code")
    relay_url = _optional_str(input_data.get("relay_url"))
    route_policy = _optional_str(input_data.get("route_policy"))
    direct_ip = _optional_str(input_data.get("direct_ip"))
    multicast_address = _optional_str(input_data.get("multicast_address"))
    timeout_sec = int(input_data.get("timeout_sec") or 3600)
    resume_mode = str(input_data.get("resume_mode") or "resume")
    _validate_resume_mode(resume_mode)
    if not source_path.exists():
        raise NodeExecutionError(
            "source_not_found",
            f"source path does not exist: {source_path}",
            {"path": str(source_path)},
            category="not_found",
        )

    source_size = source_path.stat().st_size if source_path.is_file() else None
    source_sha256 = _sha256_file(source_path) if source_path.is_file() else None
    _prepare_ledger(
        config,
        {
            "transfer_id": transfer_id,
            "attempt": attempt,
            "role": "sender",
            "status": "created",
            "code_hash": _code_hash(code),
            "relay_url": relay_url or config.relay_url,
            "route_policy": route_policy,
            "direct_ip": direct_ip,
            "source_path": str(source_path),
            "target_path": None,
            "output_dir": None,
            "source_size_bytes": source_size,
            "source_sha256": source_sha256,
            "resume_mode": resume_mode,
        },
    )
    _update_ledger_status(config, transfer_id, "running", attempt=attempt)

    try:
        result = await _run_yq_croc(
            config,
            role="sender",
            request={
                "transfer_id": transfer_id,
                "attempt": attempt,
                "role": "sender",
                "code": code,
                "relay_url": relay_url or config.relay_url,
                "relay_password": _relay_password(config),
                "route_policy": route_policy,
                "direct_ip": direct_ip,
                "multicast_address": multicast_address,
                "source_path": str(source_path),
                "resume_mode": resume_mode,
                "expected_size_bytes": source_size,
                "expected_sha256": source_sha256,
                "timeout_sec": timeout_sec,
                "cleanup_on_failure": False,
            },
            timeout_sec=timeout_sec,
            progress_callback=progress_callback,
        )
    except asyncio.CancelledError:
        _update_ledger_status(config, transfer_id, "cancelled", attempt=attempt)
        raise
    except Exception as exc:
        error = _node_error(exc)
        _update_ledger_status(
            config,
            transfer_id,
            "failed",
            attempt=attempt,
            error_code=error["code"],
            error_message=error["message"],
        )
        raise

    _update_ledger_status(config, transfer_id, "succeeded", attempt=attempt)
    return {
        "runtime": "yq-croc",
        "transfer_id": transfer_id,
        "attempt": attempt,
        "status": "succeeded",
        "role": "sender",
        "size_bytes": source_size,
        "sha256": source_sha256,
        "started_at": result["started_at"],
        "completed_at": result["completed_at"],
        "exit_code": result["exit_code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


async def yq_croc_receive(
    input_data: dict[str, Any],
    config: TransferYqCrocConfig,
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    _require_enabled(config, mode="receive")
    transfer_id = _required_str(input_data, "transfer_id")
    attempt = _required_int(input_data, "attempt", minimum=1)
    code = _required_str(input_data, "code")
    target_path_text = _optional_str(input_data.get("target_path"))
    output_dir_text = _optional_str(input_data.get("output_dir"))
    if not output_dir_text and target_path_text:
        output_dir_text = str(Path(target_path_text).resolve().parent)
    if not output_dir_text:
        raise NodeExecutionError("invalid_input", "output_dir or target_path is required")
    output_dir = Path(output_dir_text).resolve()
    target_path = Path(target_path_text).resolve() if target_path_text else None
    relay_url = _optional_str(input_data.get("relay_url"))
    route_policy = _optional_str(input_data.get("route_policy"))
    direct_ip = _optional_str(input_data.get("direct_ip"))
    multicast_address = _optional_str(input_data.get("multicast_address"))
    timeout_sec = int(input_data.get("timeout_sec") or 3600)
    resume_mode = str(input_data.get("resume_mode") or "resume")
    _validate_resume_mode(resume_mode)
    expected_sha256 = _optional_str(input_data.get("expected_sha256"))
    expected_size_bytes = input_data.get("expected_size_bytes")
    cleanup_on_failure = bool(input_data.get("cleanup_on_failure", False))

    _validate_receive_target(output_dir, target_path, resume_mode)
    _prepare_ledger(
        config,
        {
            "transfer_id": transfer_id,
            "attempt": attempt,
            "role": "receiver",
            "status": "created",
            "code_hash": _code_hash(code),
            "relay_url": relay_url or config.relay_url,
            "route_policy": route_policy,
            "direct_ip": direct_ip,
            "source_path": None,
            "target_path": str(target_path) if target_path else None,
            "output_dir": str(output_dir),
            "source_size_bytes": expected_size_bytes,
            "source_sha256": expected_sha256,
            "resume_mode": resume_mode,
            "partial_path": _find_partial_file(output_dir),
        },
    )
    _update_ledger_status(config, transfer_id, "running", attempt=attempt)

    try:
        result = await _run_yq_croc(
            config,
            role="receiver",
            request={
                "transfer_id": transfer_id,
                "attempt": attempt,
                "role": "receiver",
                "code": code,
                "relay_url": relay_url or config.relay_url,
                "relay_password": _relay_password(config),
                "route_policy": route_policy,
                "direct_ip": direct_ip,
                "multicast_address": multicast_address,
                "output_dir": str(output_dir),
                "target_path": str(target_path) if target_path else None,
                "resume_mode": resume_mode,
                "expected_size_bytes": expected_size_bytes,
                "expected_sha256": expected_sha256,
                "timeout_sec": timeout_sec,
                "cleanup_on_failure": cleanup_on_failure,
            },
            timeout_sec=timeout_sec,
            progress_callback=progress_callback,
        )
        received_path = _finalize_received_path(output_dir, target_path)
        received_meta = _received_metadata(received_path)
        if expected_sha256 and received_meta.get("sha256") != expected_sha256:
            raise NodeExecutionError(
                "integrity_mismatch",
                (
                    "received sha256 mismatch: "
                    f"expected {expected_sha256}, got {received_meta.get('sha256')}"
                ),
                {
                    "path": str(received_path),
                    "expected_sha256": expected_sha256,
                    "sha256": received_meta.get("sha256"),
                },
            )
    except asyncio.CancelledError:
        _update_ledger_status(config, transfer_id, "cancelled", attempt=attempt)
        raise
    except Exception as exc:
        error = _node_error(exc)
        _update_ledger_status(
            config,
            transfer_id,
            "failed",
            attempt=attempt,
            error_code=error["code"],
            error_message=error["message"],
        )
        raise

    _update_ledger_status(config, transfer_id, "succeeded", attempt=attempt)
    return {
        "runtime": "yq-croc",
        "transfer_id": transfer_id,
        "attempt": attempt,
        "status": "succeeded",
        "role": "receiver",
        "received_path": str(received_path),
        "received_kind": received_meta["kind"],
        "size_bytes": received_meta["size_bytes"],
        "sha256": received_meta["sha256"],
        "started_at": result["started_at"],
        "completed_at": result["completed_at"],
        "exit_code": result["exit_code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


async def _run_yq_croc(
    config: TransferYqCrocConfig,
    *,
    role: str,
    request: dict[str, Any],
    timeout_sec: int,
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    binary_path, binary_error = _resolve_yq_croc_binary(config.binary_path)
    if not binary_path:
        raise NodeExecutionError(
            str((binary_error or {}).get("code") or "yq_croc_not_found"),
            str((binary_error or {}).get("message") or "yq-croc binary was not found"),
            binary_error or {},
            category="configuration",
            operator_action="Install yq-croc or configure transfer.yq_croc.binary_path.",
        )

    loop = asyncio.get_running_loop()
    process_handle = _ProcessHandle()
    cancel_event = threading.Event()
    runner = asyncio.create_task(
        asyncio.to_thread(
            _run_yq_croc_blocking,
            binary_path,
            config,
            role,
            request,
            timeout_sec,
            progress_callback,
            loop,
            process_handle,
            cancel_event,
        )
    )
    try:
        return await asyncio.shield(runner)
    except asyncio.CancelledError:
        cancel_event.set()
        process_handle.terminate()
        with suppress(Exception):
            await asyncio.wait_for(runner, timeout=10)
        raise


def _run_yq_croc_blocking(
    binary_path: str,
    config: TransferYqCrocConfig,
    role: str,
    request: dict[str, Any],
    timeout_sec: int,
    progress_callback: ProgressCallback | None,
    loop: asyncio.AbstractEventLoop,
    process_handle: _ProcessHandle,
    cancel_event: threading.Event,
) -> dict[str, Any]:
    request_path = _write_request(config, request)
    started_at = datetime.now(UTC).isoformat()
    stdout_tail: deque[str] = deque(maxlen=TAIL_LINE_LIMIT)
    stderr_tail: deque[str] = deque(maxlen=TAIL_LINE_LIMIT)
    events: list[dict[str, Any]] = []
    process: subprocess.Popen[str] | None = None

    try:
        process = subprocess.Popen(
            [binary_path, role_to_command(role), "--request", str(request_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        process_handle.set(process)
        base = {
            "transfer_id": request["transfer_id"],
            "role": role,
            "attempt": request["attempt"],
            "pid": process.pid,
            "total_bytes": request.get("expected_size_bytes"),
        }
        stdout_thread = threading.Thread(
            target=_read_stdout_events_blocking,
            args=(process.stdout, stdout_tail, events, progress_callback, base, loop),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_read_tail_blocking,
            args=(process.stderr, stderr_tail),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        deadline = time.monotonic() + max(1, timeout_sec)

        while True:
            exit_code = process.poll()
            if exit_code is not None:
                break
            if cancel_event.is_set():
                process_handle.terminate()
                raise NodeExecutionError(
                    "cancelled",
                    f"yq-croc {role} was cancelled",
                    {
                        "stdout": _truncate_tail("\n".join(stdout_tail)),
                        "stderr": _truncate_tail("\n".join(stderr_tail)),
                        "binary_path": binary_path,
                        "role": role,
                    },
                    retryable=True,
                )
            if time.monotonic() >= deadline:
                process_handle.terminate()
                raise NodeExecutionError(
                    "timeout",
                    f"yq-croc {role} timed out after {timeout_sec}s",
                    {
                        "stdout": _truncate_tail("\n".join(stdout_tail)),
                        "stderr": _truncate_tail("\n".join(stderr_tail)),
                        "binary_path": binary_path,
                        "role": role,
                    },
                    category="timeout",
                    retryable=True,
                )
            time.sleep(0.2)

        exit_code = process.wait()
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
    finally:
        request_path.unlink(missing_ok=True)
        process_handle.clear(process)

    completed_at = datetime.now(UTC).isoformat()
    stdout_text = _truncate_tail("\n".join(stdout_tail))
    stderr_text = _truncate_tail("\n".join(stderr_tail))
    if exit_code != 0:
        raise NodeExecutionError(
            "yq_croc_failed",
            f"yq-croc {role} failed with exit code {exit_code}",
            {
                "returncode": exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "binary_path": binary_path,
                "role": role,
            },
            retryable=False,
        )
    return {
        "exit_code": exit_code,
        "started_at": started_at,
        "completed_at": completed_at,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }


class _ProcessHandle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def set(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._process = process

    def clear(self, process: subprocess.Popen[str] | None) -> None:
        with self._lock:
            if self._process is process:
                self._process = None

    def terminate(self) -> None:
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        except Exception:
            return


def _read_stdout_events_blocking(
    stream: Any,
    tail: deque[str],
    events: list[dict[str, Any]],
    progress_callback: ProgressCallback | None,
    base: dict[str, Any],
    loop: asyncio.AbstractEventLoop,
) -> None:
    if stream is None:
        return
    for raw_line in stream:
        line = str(raw_line).strip()
        tail.append(_redact(line))
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or not event.get("event"):
            continue
        events.append(event)
        _emit_progress_blocking(progress_callback, _progress_payload(base, event), loop)


def _read_tail_blocking(stream: Any, tail: deque[str]) -> None:
    if stream is None:
        return
    for raw_line in stream:
        tail.append(_redact(str(raw_line).strip()))


def _emit_progress_blocking(
    callback: ProgressCallback | None,
    payload: dict[str, Any],
    loop: asyncio.AbstractEventLoop,
) -> None:
    if callback is None:
        return
    result = callback("transfer_progress", payload)
    if inspect.isawaitable(result):
        asyncio.run_coroutine_threadsafe(result, loop).result()


async def _collect_stdout_events(
    stream: asyncio.StreamReader | None,
    progress_callback: ProgressCallback | None,
    base: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    tail: deque[str] = deque(maxlen=TAIL_LINE_LIMIT)
    events: list[dict[str, Any]] = []
    if stream is None:
        return "", events
    while line_bytes := await stream.readline():
        line = line_bytes.decode("utf-8", errors="replace").strip()
        tail.append(_redact(line))
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or not event.get("event"):
            continue
        events.append(event)
        await _emit_progress(progress_callback, _progress_payload(base, event))
    return _truncate_tail("\n".join(tail)), events


async def _collect_tail(stream: asyncio.StreamReader | None) -> str:
    tail: deque[str] = deque(maxlen=TAIL_LINE_LIMIT)
    if stream is None:
        return ""
    while line_bytes := await stream.readline():
        tail.append(_redact(line_bytes.decode("utf-8", errors="replace").strip()))
    return _truncate_tail("\n".join(tail))


async def _emit_progress(callback: ProgressCallback | None, payload: dict[str, Any]) -> None:
    if callback is None:
        return
    result = callback("transfer_progress", payload)
    if inspect.isawaitable(result):
        await result


def _progress_payload(base: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    event_name = str(event.get("event") or "")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    payload: dict[str, Any] = {
        "transfer_id": base["transfer_id"],
        "role": base["role"],
        "attempt": base["attempt"],
        "pid": base.get("pid"),
        "status": "running",
        "phase": _phase_for_event(event_name),
        "progress_source": "yq_croc_event",
        "runtime": "yq-croc",
        "event": event_name,
        "event_data": data,
    }
    total = data.get("total_bytes") or base.get("total_bytes")
    if total is not None:
        payload["total_bytes"] = total
    transferred = data.get("bytes_transferred")
    if transferred is not None:
        payload["bytes_transferred"] = transferred
        if total:
            payload["progress_pct"] = (float(transferred) / float(total)) * 100.0
    if event_name == "sender_ready":
        payload["sender_ready"] = True
    if event_name == "transfer_error":
        if data.get("error_code"):
            payload["error_code"] = data.get("error_code")
        if data.get("error_message"):
            payload["error_message"] = data.get("error_message")
    return payload


def _phase_for_event(event_name: str) -> str:
    return {
        "runtime_ready": "preparing",
        "source_scanned": "scanning",
        "file_info": "scanning",
        "sender_ready": "ready",
        "receiver_connected": "ready",
        "resume_plan": "resuming",
        "channel_secured": "handshake",
        "bytes_progress": "transferring",
        "integrity_verified": "verifying",
        "transfer_done": "completed",
        "transfer_error": "failed",
        "transfer_cancelled": "cancelled",
    }.get(event_name, "running")


def role_to_command(role: str) -> str:
    if role == "sender":
        return "send"
    if role == "receiver":
        return "receive"
    raise ValueError(f"unknown yq-croc role: {role}")


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


def _require_enabled(config: TransferYqCrocConfig, *, mode: str) -> None:
    status = probe_yq_croc_status(config)
    if not config.enabled:
        raise NodeExecutionError(
            "transfer_yq_croc_disabled",
            "yq-croc transfer is disabled in node configuration.",
            status,
            category="configuration",
        )
    if not status["installed"]:
        raise NodeExecutionError(
            "yq_croc_not_found",
            "yq-croc binary was not found. Configure transfer.yq_croc.binary_path.",
            status,
            category="configuration",
        )
    if mode == "send" and not config.allow_send:
        raise NodeExecutionError("send_not_allowed", "yq-croc send is disabled.", status)
    if mode == "receive" and not config.allow_receive:
        raise NodeExecutionError("receive_not_allowed", "yq-croc receive is disabled.", status)


def _resolve_yq_croc_binary(
    configured_path: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    if configured_path:
        path = Path(configured_path).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            return None, {
                "code": "yq_croc_binary_missing",
                "message": "Configured yq-croc binary path does not exist.",
                "path": str(path),
            }
        if path.is_dir():
            return None, {
                "code": "yq_croc_binary_is_directory",
                "message": "Configured yq-croc binary path is a directory.",
                "path": str(path),
            }
        return str(path), None
    discovered = shutil.which("yq-croc") or shutil.which("yq-croc.exe")
    return (discovered, None) if discovered else (None, {"code": "yq_croc_not_found"})


def _run_json_probe(binary_path: str | None, args: list[str]) -> dict[str, Any]:
    if not binary_path:
        return {"ok": False, "error": {"code": "yq_croc_not_found"}}
    try:
        completed = subprocess.run(
            [binary_path, *args],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return {"ok": False, "error": {"code": "yq_croc_execute_failed", "message": str(exc)}}
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": {
                "code": "yq_croc_probe_failed",
                "message": completed.stderr.strip() or completed.stdout.strip(),
                "returncode": completed.returncode,
            },
        }
    try:
        return {"ok": True, "payload": json.loads(completed.stdout)}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": {"code": "yq_croc_invalid_json", "message": str(exc)}}


def _windows_firewall_outbound_facts(binary_path: str | None) -> dict[str, Any]:
    if os.name != "nt":
        return {"platform": os.name, "allows_outbound": True}
    if not binary_path:
        return {"platform": "windows", "allows_outbound": None, "reason": "binary_missing"}
    command = (
        "$program = $env:YEQU_YQ_CROC_PROGRAM; "
        "$profiles = Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultOutboundAction; "
        "$rules = @(); "
        "if ($program) { "
        "$rules = Get-NetFirewallApplicationFilter -Program $program -ErrorAction SilentlyContinue "
        "| Get-NetFirewallRule -ErrorAction SilentlyContinue "
        "| Select-Object DisplayName,Direction,Action,Enabled,Profile "
        "}; "
        "[pscustomobject]@{profiles=$profiles; rules=$rules} | ConvertTo-Json -Compress -Depth 5"
    )
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "YEQU_YQ_CROC_PROGRAM": str(Path(binary_path).resolve())},
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return {"platform": "windows", "allows_outbound": None, "query_error": str(exc)}
    if completed.returncode != 0:
        return {
            "platform": "windows",
            "allows_outbound": None,
            "query_error": completed.stderr.strip() or completed.stdout.strip(),
        }
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {"platform": "windows", "allows_outbound": None, "query_error": str(exc)}
    profiles = _listify(payload.get("profiles"))
    rules = _listify(payload.get("rules"))
    enabled_block_profiles = [
        str(item.get("Name"))
        for item in profiles
        if _truthy(item.get("Enabled")) and str(item.get("DefaultOutboundAction")) == "Block"
    ]
    outbound_allow_rules = [
        _firewall_rule_summary(rule)
        for rule in rules
        if _truthy(rule.get("Enabled"))
        and str(rule.get("Direction")) == "Outbound"
        and str(rule.get("Action")) == "Allow"
    ]
    outbound_block_rules = [
        _firewall_rule_summary(rule)
        for rule in rules
        if _truthy(rule.get("Enabled"))
        and str(rule.get("Direction")) == "Outbound"
        and str(rule.get("Action")) == "Block"
    ]
    allows_outbound = not outbound_block_rules and (
        not enabled_block_profiles or bool(outbound_allow_rules)
    )
    return {
        "platform": "windows",
        "required": "outbound_tcp_to_relay",
        "allows_outbound": allows_outbound,
        "enabled_blocking_profiles": enabled_block_profiles,
        "program_outbound_allow_rules": outbound_allow_rules,
        "program_outbound_block_rules": outbound_block_rules,
        "rule_hint": (
            "Add an elevated outbound allow rule for yq-croc.exe to relay TCP port 9009."
            if not allows_outbound
            else None
        ),
    }


def _listify(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def _firewall_rule_summary(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "display_name": rule.get("DisplayName"),
        "direction": rule.get("Direction"),
        "action": rule.get("Action"),
        "enabled": _truthy(rule.get("Enabled")),
        "profile": rule.get("Profile"),
    }


def _probe_error(probe: dict[str, Any] | None, default_code: str) -> dict[str, Any] | None:
    if probe and probe.get("ok"):
        return None
    error = dict((probe or {}).get("error") or {})
    error.setdefault("code", default_code)
    error.setdefault("message", default_code)
    return error


def _first_value(*payloads: dict[str, Any] | None, key: str) -> Any:
    for payload in payloads:
        if isinstance(payload, dict) and payload.get(key) is not None:
            return payload[key]
    return None


def _write_request(config: TransferYqCrocConfig, request: dict[str, Any]) -> Path:
    transfer_id = _safe_path_part(str(request["transfer_id"]))
    attempt = int(request["attempt"])
    request_dir = Path(config.temp_dir).resolve() / transfer_id / f"attempt-{attempt}"
    request_dir.mkdir(parents=True, exist_ok=True)
    request_path = request_dir / "request.json"
    clean_request = {key: value for key, value in request.items() if value is not None}
    request_path.write_text(
        json.dumps(clean_request, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return request_path


def _safe_path_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "transfer"


def _relay_password(config: TransferYqCrocConfig) -> str | None:
    if not config.relay_password_env:
        return None
    try:
        return os.environ[config.relay_password_env]
    except KeyError as exc:
        raise NodeExecutionError(
            "relay_password_env_missing",
            f"configured relay password env {config.relay_password_env} is not set",
            {"relay_password_env": config.relay_password_env},
            category="configuration",
        ) from exc


def _prepare_ledger(config: TransferYqCrocConfig, entry: dict[str, Any]) -> None:
    existing = _read_ledger_entry(config, str(entry["transfer_id"]))
    now = datetime.now(UTC).isoformat()
    entry = {**entry, "created_at": now, "updated_at": now}
    if existing:
        existing_attempt = int(existing.get("attempt") or 0)
        new_attempt = int(entry["attempt"])
        if existing_attempt == new_attempt:
            raise NodeExecutionError(
                "transfer_attempt_exists",
                (
                    f"transfer {entry['transfer_id']} attempt {new_attempt} "
                    f"already exists in state {existing.get('status')}"
                ),
                existing,
            )
        if existing_attempt > new_attempt:
            raise NodeExecutionError(
                "stale_transfer_attempt",
                f"stale attempt {new_attempt}; latest local attempt is {existing_attempt}",
                existing,
            )
        if str(existing.get("status")) not in TERMINAL_STATUSES:
            raise NodeExecutionError(
                "transfer_attempt_running",
                f"transfer {entry['transfer_id']} has non-terminal attempt {existing_attempt}",
                existing,
            )
    _write_ledger_entry(config, entry)


def _update_ledger_status(
    config: TransferYqCrocConfig,
    transfer_id: str,
    status: str,
    *,
    attempt: int,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    entry = _read_ledger_entry(config, transfer_id) or {
        "transfer_id": transfer_id,
        "attempt": attempt,
    }
    entry["status"] = status
    entry["attempt"] = attempt
    entry["updated_at"] = datetime.now(UTC).isoformat()
    if status in TERMINAL_STATUSES:
        entry["completed_at"] = entry["updated_at"]
    if error_code:
        entry["last_error_code"] = error_code
    if error_message:
        entry["last_error_message"] = error_message
    _write_ledger_entry(config, entry)


def _ledger_dir(config: TransferYqCrocConfig) -> Path:
    path = Path(config.temp_dir).resolve() / "ledger"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ledger_path(config: TransferYqCrocConfig, transfer_id: str) -> Path:
    return _ledger_dir(config) / f"{_safe_path_part(transfer_id)}.json"


def _read_ledger_entry(config: TransferYqCrocConfig, transfer_id: str) -> dict[str, Any] | None:
    path = _ledger_path(config, transfer_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_ledger_entry(config: TransferYqCrocConfig, entry: dict[str, Any]) -> None:
    path = _ledger_path(config, str(entry["transfer_id"]))
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_ledger_entries(config: TransferYqCrocConfig) -> list[dict[str, Any]]:
    entries = []
    for path in _ledger_dir(config).glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _validate_receive_target(output_dir: Path, target_path: Path | None, resume_mode: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if resume_mode == "fail_if_exists":
        if target_path and target_path.exists():
            raise NodeExecutionError(
                "target_exists",
                f"target already exists: {target_path}",
                {"target_path": str(target_path)},
            )
        if target_path is None and any(output_dir.iterdir()):
            raise NodeExecutionError(
                "target_exists",
                f"output directory is not empty: {output_dir}",
                {"output_dir": str(output_dir)},
            )
    if resume_mode == "overwrite" and target_path and target_path.exists():
        _remove_existing(target_path)


def _finalize_received_path(output_dir: Path, target_path: Path | None) -> Path:
    if target_path and target_path.exists():
        return target_path
    received = _find_newest_entry(output_dir)
    if received is None:
        raise NodeExecutionError(
            "received_file_missing",
            "yq-croc returned success but no received file was found",
            {
                "output_dir": str(output_dir),
                "target_path": str(target_path) if target_path else None,
            },
        )
    if target_path:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if received.resolve() != target_path.resolve():
            if target_path.exists():
                _remove_existing(target_path)
            received.rename(target_path)
        return target_path
    return received


def _find_newest_entry(directory: Path) -> Path | None:
    newest: tuple[float, Path] | None = None
    if not directory.exists():
        return None
    for entry in directory.iterdir():
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest[0]:
            newest = (mtime, entry)
    return newest[1] if newest else None


def _find_partial_file(directory: Path) -> str | None:
    if not directory.exists():
        return None
    for entry in directory.iterdir():
        if entry.name.endswith((".partial", ".croc")):
            return str(entry)
    return None


def _remove_existing(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _received_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise NodeExecutionError("path_not_found", f"received path not found: {path}")
    is_file = path.is_file()
    return {
        "kind": "file" if is_file else "directory",
        "size_bytes": path.stat().st_size if is_file else None,
        "sha256": _sha256_file(path) if is_file else None,
    }


def _required_str(input_data: dict[str, Any], field: str) -> str:
    value = str(input_data.get(field) or "").strip()
    if not value:
        raise NodeExecutionError("invalid_input", f"{field} is required", {"field": field})
    return value


def _required_int(input_data: dict[str, Any], field: str, *, minimum: int) -> int:
    try:
        value = int(input_data.get(field))
    except (TypeError, ValueError) as exc:
        raise NodeExecutionError("invalid_input", f"{field} is required", {"field": field}) from exc
    if value < minimum:
        raise NodeExecutionError(
            "invalid_input",
            f"{field} must be >= {minimum}",
            {"field": field, "value": value},
        )
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validate_resume_mode(value: str) -> None:
    if value not in {"resume", "overwrite", "fail_if_exists"}:
        raise NodeExecutionError(
            "invalid_input",
            f"unknown resume_mode: {value}",
            {"resume_mode": value},
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _node_error(exc: BaseException) -> dict[str, str]:
    if isinstance(exc, NodeExecutionError):
        return {"code": exc.code, "message": exc.message}
    return {"code": "execution_failed", "message": str(exc) or exc.__class__.__name__}


def _redact(line: str) -> str:
    lowered = line.lower()
    if "code" in lowered or "password" in lowered or "passphrase" in lowered:
        return "[REDACTED]"
    return line


def _truncate_tail(text: str) -> str:
    if len(text) <= MAX_OUTPUT_TAIL:
        return text
    return "..." + text[-MAX_OUTPUT_TAIL:]
