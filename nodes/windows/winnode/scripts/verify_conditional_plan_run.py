from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

BASE = "https://gtw.yequdesu.top"
ADMIN_TOKEN = "qq756522327"
AGENT_TOKEN = "win-agent-pg-e2e-197042c1ca2674edf948fc86"
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
AGENT = {"Authorization": f"Bearer {AGENT_TOKEN}"}
ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
CONFIG = ROOT / "config.local.yaml"


def json_or_text(response: requests.Response) -> Any:
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    return response.text


def request(
    method: str,
    path: str,
    *,
    headers: dict[str, str] = ADMIN,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    response = requests.request(
        method,
        BASE + path,
        headers=headers,
        json=body,
        params=params,
        timeout=timeout,
    )
    return response.status_code, json_or_text(response)


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


def wait_run(run_id: str, timeout_sec: int = 180) -> dict[str, Any] | None:
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


def events_from_timeline(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, dict):
        events = body.get("events") or body.get("items") or body.get("value") or []
    elif isinstance(body, list):
        events = body
    else:
        events = []
    return [event for event in events if isinstance(event, dict)]


def main() -> None:
    out: dict[str, Any] = {}
    daemon: subprocess.Popen[str] | None = None
    try:
        session_code, session = request(
            "POST",
            "/agent/sessions",
            headers=AGENT,
            body={
                "actor_id": "conditional-plan-run-verify",
                "execution_mode": "auto",
                "max_total_duration_sec": 180,
            },
            timeout=30,
        )
        out["session"] = {"status_code": session_code, "body": session}
        if session_code >= 400 or not isinstance(session, dict):
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return

        plan_code, plan = request(
            "POST",
            "/agent/plan",
            headers=AGENT,
            body={
                "session_id": session["session_id"],
                "provider_name": "deepseek",
                "prompt": "检查打印服务，如果不正常就修复",
                "target_node_id": "winClient",
                "execution_mode": "auto",
                "max_total_duration_sec": 180,
            },
            timeout=240,
        )
        steps = plan.get("steps") if isinstance(plan, dict) else []
        out["plan"] = {
            "status_code": plan_code,
            "plan_id": plan.get("plan_id") if isinstance(plan, dict) else None,
            "status": plan.get("status") if isinstance(plan, dict) else None,
            "approval_required": plan.get("approval_required") if isinstance(plan, dict) else None,
            "step_count": len(steps) if isinstance(steps, list) else 0,
            "steps": steps,
            "expected_ok": plan_code == 200
            and isinstance(plan, dict)
            and plan.get("status") == "waiting_approval"
            and len(steps) == 3,
        }
        if plan_code >= 400 or not isinstance(plan, dict):
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return

        plan_id = plan["plan_id"]
        approve_code, approval = request(
            "POST",
            f"/admin/maintenance/plans/{plan_id}/approve",
            timeout=30,
        )
        out["approval"] = {
            "status_code": approve_code,
            "body": approval,
            "approval_id": approval.get("approval_id") if isinstance(approval, dict) else None,
        }

        daemon = start_daemon()
        run_code, run = request(
            "POST",
            f"/admin/maintenance/plans/{plan_id}/run",
            timeout=60,
        )
        run_id = run.get("run_id") if isinstance(run, dict) else None
        detail = wait_run(run_id) if run_id else None
        run_steps = (detail.get("steps") or []) if isinstance(detail, dict) else []
        step_statuses = [step.get("status") for step in run_steps]
        skip_reasons = [step.get("skip_reason") for step in run_steps]
        out["run"] = {
            "status_code": run_code,
            "run_id": run_id,
            "run_start": run,
            "run_status": detail.get("status") if isinstance(detail, dict) else None,
            "summary": detail.get("summary") if isinstance(detail, dict) else None,
            "step_statuses": step_statuses,
            "skip_reasons": skip_reasons,
            "detail": detail,
            "expected_ok": isinstance(detail, dict)
            and detail.get("status") == "succeeded"
            and step_statuses == ["succeeded", "skipped", "succeeded"]
            and skip_reasons[1] == "previous_healthy",
        }

        approval_id = out["approval"].get("approval_id")
        if approval_id:
            timeline_code, timeline = request(
                "GET",
                "/admin/timeline",
                params={"approval_id": approval_id, "limit": 100},
                timeout=30,
            )
            events = events_from_timeline(timeline)
            event_types = [event.get("event_type") or event.get("type") for event in events]
            expected = [
                "approval.requested",
                "approval.approved",
                "approval.consumed",
                "maintenance.step.started",
                "maintenance.step.succeeded",
                "maintenance.step.skipped",
                "maintenance.run.succeeded",
            ]
            out["timeline"] = {
                "status_code": timeline_code,
                "approval_id": approval_id,
                "event_types": event_types,
                "missing": [event for event in expected if event not in event_types],
                "expected_ok": timeline_code == 200
                and all(event in event_types for event in expected),
            }
    finally:
        stop_daemon(daemon)

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
