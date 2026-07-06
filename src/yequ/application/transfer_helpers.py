"""Pure helper functions for transfer application services."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from yequ.application.schemas import ExecuteToolResult
from yequ.models.job import Job
from yequ.models.transfer import TransferSession

TRANSFER_PREFLIGHT_DEFAULT_TTL_SEC = 120
TRANSFER_PREFLIGHT_MIN_TTL_SEC = 30
TRANSFER_PREFLIGHT_MAX_TTL_SEC = 300
TRANSFER_SENDER_START_WAIT_SEC = 8.0
TRANSFER_SENDER_READY_MIN_WAIT_SEC = 180.0
TRANSFER_SENDER_READY_MAX_WAIT_SEC = 600.0
TRANSFER_JOB_MIN_LEASE_SEC = 180
TRANSFER_JOB_MAX_LEASE_SEC = 600
TRANSFER_ROUTE_DEFAULT = "auto"
TRANSFER_ROUTE_POLICIES = {
    "auto",
    "relay_only",
    "relay_pool",
    "local_first",
    "local_only",
    "direct_ip",
}
TRANSFER_ROUTE_POLICIES_REQUIRING_RELAY = {
    "auto",
    "relay_only",
    "relay_pool",
    "local_first",
}


@dataclass(frozen=True, slots=True)
class NormalizedTransferTarget:
    output_dir: str
    target_path: str | None


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
    route_policy: str,
    direct_ip: str | None,
) -> list[dict[str, object]]:
    failures: list[dict[str, object]] = []
    relay_required = _route_requires_relay(route_policy)
    if route_policy == "direct_ip" and not direct_ip:
        failures.append({"fact": "route.direct_ip", "code": "direct_ip_required"})
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
    if relay_required and source_status.get("relay_reachable") is not True:
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
    if relay_required and target_status.get("relay_reachable") is not True:
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


def _first_sha256(*values: object) -> str | None:
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = value.strip().lower()
        if len(candidate) == 64 and all(ch in "0123456789abcdef" for ch in candidate):
            return candidate
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
    route_policy: str,
    direct_ip: str | None,
    multicast_address: str | None,
    resume_mode: str,
) -> str:
    payload = {
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "source_path": source_path,
        "target_output_dir": target_output_dir,
        "target_path": target_path,
        "relay_url": relay_url,
        "route_policy": route_policy,
        "direct_ip": direct_ip,
        "multicast_address": multicast_address,
        "resume_mode": resume_mode,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_route_policy(value: str | None) -> str:
    route_policy = (value or TRANSFER_ROUTE_DEFAULT).strip()
    if route_policy not in TRANSFER_ROUTE_POLICIES:
        raise ValueError(
            "route_policy must be auto, relay_only, relay_pool, "
            "local_first, local_only, or direct_ip"
        )
    return route_policy


def _validate_route_policy_fields(route_policy: str, *, direct_ip: str | None) -> None:
    if route_policy == "direct_ip" and not direct_ip:
        raise ValueError("direct_ip is required when route_policy is direct_ip")


def _route_requires_relay(route_policy: str) -> bool:
    return route_policy in TRANSFER_ROUTE_POLICIES_REQUIRING_RELAY


def _relay_mode(route_policy: str, relay_url: str | None) -> str:
    if not _route_requires_relay(route_policy):
        return "not_required"
    return "configured" if relay_url else "public_default"


def _generate_croc_code() -> str:
    return f"yequ-{secrets.token_urlsafe(12).replace('_', '').replace('-', '')[:16]}"


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
