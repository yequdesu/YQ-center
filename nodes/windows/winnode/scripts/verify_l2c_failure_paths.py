from __future__ import annotations

import json
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests

BASE = "https://gtw.yequdesu.top"
ADMIN_TOKEN = "qq756522327"
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
CONFIG = ROOT / "config.local.yaml"
LOG_DIR = ROOT / "e2e-logs"


def json_or_text(response: requests.Response) -> Any:
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    return response.text


def request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    response = requests.request(
        method,
        BASE + path,
        headers=ADMIN,
        json=body,
        params=params,
        timeout=timeout,
    )
    return response.status_code, json_or_text(response)


def wait_run(run_id: str, timeout_sec: int = 120) -> dict[str, Any] | None:
    terminal = {
        "succeeded",
        "failed",
        "timeout",
        "cancelled",
        "partial",
        "partially_succeeded",
        "rollback_recommended",
    }
    deadline = time.time() + timeout_sec
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        code, body = request("GET", f"/admin/maintenance/runs/{run_id}", timeout=30)
        if code < 400 and isinstance(body, dict):
            last = body
            if body.get("status") in terminal:
                return body
        time.sleep(2)
    return last



def start_daemon() -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [str(PY), "-m", "node_win_client.cli", "run", "-c", str(CONFIG)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    time.sleep(5)
    if process.poll() is not None:
        text = process.stdout.read() if process.stdout else ""
        raise RuntimeError("daemon exited early: " + text[:1000])
    return process


def stop_daemon(process: subprocess.Popen[str] | None) -> None:
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)

def list_items(body: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(body, dict):
        items = body.get(key) or body.get("items") or body.get("value") or []
    elif isinstance(body, list):
        items = body
    else:
        items = []
    return [item for item in items if isinstance(item, dict)]


def event_types(body: Any) -> list[str | None]:
    return [event.get("event_type") or event.get("type") for event in list_items(body, "events")]


def make_plan_body(kind: str) -> dict[str, Any]:
    if kind == "repair":
        failing_step = {
            "function_name": "test.maintenance.repair_fail",
            "input": {"name": "Spooler"},
            "kind": "repair",
            "condition": "always",
            "depends_on": [1],
            "requires_approval": True,
            "rollback_hint": {
                "action": "restore_service_state",
                "target_type": "windows_service",
                "service_name": "Spooler",
                "rollback_function": "system.service.ensure_state",
                "requires_approval": True,
            },
        }
        return {
            "goal": "Test repair failure",
            "target_node_id": "winClient",
            "steps": [
                {
                    "function_name": "system.service.status",
                    "input": {"name": "Spooler"},
                    "kind": "check",
                    "condition": "always",
                },
                failing_step,
            ],
        }

    if kind == "verify":
        return {
            "goal": "Test verify failure",
            "target_node_id": "winClient",
            "steps": [
                {
                    "function_name": "system.service.status",
                    "input": {"name": "Spooler"},
                    "kind": "check",
                    "condition": "always",
                },
                {
                    "function_name": "system.service.ensure_running",
                    "input": {"name": "Spooler", "__test_fail_stage": "verify"},
                    "kind": "repair",
                    "condition": "always",
                    "depends_on": [1],
                    "requires_approval": True,
                    "rollback_hint": {
                        "action": "restore_service_state",
                        "target_type": "windows_service",
                        "service_name": "Spooler",
                        "rollback_function": "system.service.ensure_state",
                        "requires_approval": True,
                    },
                },
                {
                    "function_name": "test.maintenance.verify_fail",
                    "input": {"name": "Spooler"},
                    "kind": "verify",
                    "condition": "after_repair",
                    "depends_on": [2],
                },
            ],
        }

    raise ValueError(kind)


def run_case(kind: str) -> dict[str, Any]:
    out: dict[str, Any] = {"case": kind}
    try:
        return _run_case(kind, out)
    except Exception as exc:
        out["ok"] = False
        out["error"] = repr(exc)
        return out


def _run_case(kind: str, out: dict[str, Any]) -> dict[str, Any]:
    plan_code, plan = request("POST", "/admin/maintenance/plans", body=make_plan_body(kind), timeout=30)
    out["plan"] = {"status_code": plan_code, "body": plan}
    if plan_code >= 400 or not isinstance(plan, dict):
        out["ok"] = False
        out["error"] = "plan_create_failed"
        return out

    plan_id = plan["plan_id"]
    approve_code, approval = request("POST", f"/admin/maintenance/plans/{plan_id}/approve", timeout=30)
    out["approval"] = {"status_code": approve_code, "body": approval}
    if approve_code >= 400 or not isinstance(approval, dict):
        out["ok"] = False
        out["error"] = "plan_approve_failed"
        return out

    approval_id = approval.get("approval_id")
    run_code, run = request(
        "POST",
        f"/admin/maintenance/plans/{plan_id}/run",
        params={"approval_id": approval_id},
        timeout=60,
    )
    out["run_start"] = {"status_code": run_code, "body": run}
    run_id = run.get("run_id") if isinstance(run, dict) else None
    if run_code >= 400 or not run_id:
        out["ok"] = False
        out["error"] = "plan_run_failed"
        return out

    detail = wait_run(run_id)
    artifacts_code, artifacts_body = request(
        "GET",
        f"/admin/maintenance/runs/{run_id}/artifacts",
        timeout=30,
    )
    error_code, error_body = request(
        "GET",
        f"/admin/maintenance/runs/{run_id}/artifacts",
        params={"kind": "error"},
        timeout=30,
    )
    rollback_code, rollback_body = request(
        "GET",
        f"/admin/maintenance/runs/{run_id}/artifacts",
        params={"kind": "rollback_hint"},
        timeout=30,
    )
    timeline_code, timeline = request(
        "GET",
        "/admin/timeline",
        params={"approval_id": approval_id, "limit": 200},
        timeout=30,
    )

    artifacts = list_items(artifacts_body, "artifacts")
    kinds = [artifact.get("kind") for artifact in artifacts]
    errors = list_items(error_body, "artifacts")
    rollback_hints = list_items(rollback_body, "artifacts")
    types = event_types(timeline)
    steps = detail.get("steps") if isinstance(detail, dict) else []
    statuses = [step.get("status") for step in steps] if isinstance(steps, list) else []

    expected_kinds = {"check_result", "before", "error", "rollback_hint"}
    if kind == "verify":
        expected_kinds.add("after")

    out.update(
        {
            "run_detail": detail,
            "artifacts": {
                "status_code": artifacts_code,
                "kinds": kinds,
                "kind_counts": dict(Counter(kinds)),
                "body": artifacts_body,
            },
            "error_artifacts": {
                "status_code": error_code,
                "count": len(errors),
                "body": error_body,
            },
            "rollback_artifacts": {
                "status_code": rollback_code,
                "count": len(rollback_hints),
                "body": rollback_body,
            },
            "timeline": {
                "status_code": timeline_code,
                "event_types": types,
                "event_counts": dict(Counter(types)),
                "body": timeline,
            },
            "ids": {
                "plan_id": plan_id,
                "approval_id": approval_id,
                "run_id": run_id,
            },
            "step_statuses": statuses,
            "missing_artifact_kinds": sorted(expected_kinds - set(kinds)),
        }
    )

    out["ok"] = (
        isinstance(detail, dict)
        and detail.get("status") == "rollback_recommended"
        and detail.get("rollback_recommended") is True
        and expected_kinds.issubset(set(kinds))
        and len(errors) >= 1
        and len(rollback_hints) >= 1
        and "maintenance.rollback.recommended" in types
    )
    return out


def main() -> None:
    daemon: subprocess.Popen[str] | None = None
    try:
        daemon = start_daemon()
        results = {
            "repair_failed": run_case("repair"),
            "verify_failed": run_case("verify"),
        }
    finally:
        stop_daemon(daemon)
    results["ok"] = results["repair_failed"].get("ok") and results["verify_failed"].get("ok")

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"l2c-failure-paths-{time.strftime('%Y%m%d-%H%M%S')}.json"
    log_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"log_path": str(log_path), **results}, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if results["ok"] else 2)


if __name__ == "__main__":
    main()

