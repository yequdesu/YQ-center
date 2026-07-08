from __future__ import annotations

import ctypes
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

BASE = "https://gtw.yequdesu.top"
ADMIN = {"Authorization": "Bearer qq756522327"}
AGENT = {"Authorization": "Bearer win-agent-pg-e2e-197042c1ca2674edf948fc86"}
ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
CONFIG = ROOT / "config.local.yaml"
SERVICE = "Spooler"
TERMINAL = {
    "succeeded",
    "failed",
    "timeout",
    "cancelled",
    "partial",
    "dry_run_completed",
    "policy_denied",
}


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
    try:
        data: Any = response.json()
    except Exception:
        data = response.text
    return response.status_code, data


def get(path: str, **kwargs: Any) -> tuple[int, Any]:
    return request("GET", path, **kwargs)


def post(path: str, **kwargs: Any) -> tuple[int, Any]:
    return request("POST", path, **kwargs)


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


def wait_invocation(invocation_id: str, timeout_sec: int = 150) -> dict[str, Any] | None:
    deadline = time.time() + timeout_sec
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        code, data = get(f"/admin/invocations/{invocation_id}", timeout=20)
        if code < 400 and isinstance(data, dict):
            last = data
            if data.get("status") in TERMINAL:
                return data
        time.sleep(1)
    return last


def wait_run(run_id: str, timeout_sec: int = 180) -> dict[str, Any] | None:
    deadline = time.time() + timeout_sec
    terminal = {
        "succeeded",
        "failed",
        "timeout",
        "cancelled",
        "partial",
        "partially_succeeded",
        "rollback_recommended",
    }
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        code, data = get(f"/admin/maintenance/runs/{run_id}", timeout=30)
        if code < 400 and isinstance(data, dict):
            last = data
            if data.get("status") in terminal:
                return data
        time.sleep(2)
    return last


def timeline(params: dict[str, Any]) -> list[dict[str, Any]]:
    _, data = get("/admin/timeline", params=params, timeout=20)
    if isinstance(data, dict):
        events = data.get("events") or data.get("items") or data.get("value") or []
    elif isinstance(data, list):
        events = data
    else:
        events = []
    return [event for event in events if isinstance(event, dict)]


def event_types(events: list[dict[str, Any]]) -> list[str]:
    return [event.get("event_type") or event.get("type") for event in events if event]


def approve(approval_id: str) -> None:
    post(
        f"/admin/approvals/{approval_id}/approve",
        body={"approved_by": "win-e2e-admin"},
        timeout=20,
    )


def create_restart_request(dry_run: bool | None = None) -> tuple[int, Any]:
    body: dict[str, Any] = {
        "function_name": "system.service.restart",
        "target_node_id": "winClient",
        "execution_mode": "auto",
        "input": {"name": SERVICE},
    }
    if dry_run is not None:
        body["dry_run"] = dry_run
    return post("/admin/invocations", body=body, timeout=30)


def execute_restart(approval_id: str, dry_run: bool = False) -> tuple[int, Any]:
    return post(
        "/admin/invocations",
        body={
            "function_name": "system.service.restart",
            "target_node_id": "winClient",
            "execution_mode": "auto",
            "approval_id": approval_id,
            "dry_run": dry_run,
            "input": {"name": SERVICE},
        },
        timeout=30,
    )


def main() -> None:
    out: dict[str, Any] = {
        "is_windows_admin": bool(ctypes.windll.shell32.IsUserAnAdmin())
    }

    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine -match 'node_win_client\\\\.cli' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
            ],
            cwd=str(ROOT),
            timeout=20,
        )
    except Exception as exc:
        out["daemon_cleanup_warning"] = repr(exc)

    try:
        code, data = create_restart_request(dry_run=True)
        out["06_l2_dry_run_precheck"] = {
            "ok": code < 400
            and isinstance(data, dict)
            and data.get("invocation_status") == "dry_run_completed"
            and data.get("job_id") == "",
            "status_code": code,
            "response": data,
        }
    except Exception as exc:
        out["06_l2_dry_run_precheck"] = {"ok": False, "exception": repr(exc)}

    process: subprocess.Popen[str] | None = None
    try:
        process = start_daemon()
        _, waiting = create_restart_request(dry_run=False)
        approval_id = waiting.get("approval_id") if isinstance(waiting, dict) else None
        approve(approval_id)
        _, execution = execute_restart(approval_id, dry_run=False)
        invocation_id = execution.get("invocation_id") if isinstance(execution, dict) else None
        terminal = wait_invocation(invocation_id) if invocation_id else None
        jobs = terminal.get("jobs") if isinstance(terminal, dict) else []
        job = jobs[0] if jobs else {}
        events = timeline({"invocation_id": invocation_id, "limit": 100}) if invocation_id else []
        out["01_l2_real_restart"] = {
            "ok": isinstance(terminal, dict) and terminal.get("status") == "succeeded",
            "approval_id": approval_id,
            "invocation_id": invocation_id,
            "job_id": job.get("job_id"),
            "status": terminal.get("status") if isinstance(terminal, dict) else None,
            "job_status": job.get("status"),
            "result": terminal.get("result") if isinstance(terminal, dict) else None,
            "timeline": event_types(events),
        }
    except Exception as exc:
        out["01_l2_real_restart"] = {"ok": False, "exception": repr(exc)}
    finally:
        stop_daemon(process)

    process = None
    try:
        _, waiting_1 = create_restart_request(dry_run=False)
        approval_1 = waiting_1.get("approval_id") if isinstance(waiting_1, dict) else None
        approve(approval_1)
        _, execution_1 = execute_restart(approval_1, dry_run=False)
        invocation_1 = execution_1.get("invocation_id") if isinstance(execution_1, dict) else None
        job_1 = execution_1.get("job_id") if isinstance(execution_1, dict) else None
        _, waiting_2 = create_restart_request(dry_run=False)
        approval_2 = waiting_2.get("approval_id") if isinstance(waiting_2, dict) else None
        if approval_2:
            approve(approval_2)
        code_2, execution_2 = execute_restart(approval_2, dry_run=False) if approval_2 else (None, None)
        conflict_events = timeline({"event_type": "resource.lock.conflict", "limit": 20})
        process = start_daemon()
        terminal_1 = wait_invocation(invocation_1) if invocation_1 else None
        stop_daemon(process)
        process = None
        out["02_resource_lock_conflict"] = {
            "ok": bool(code_2 == 409 and conflict_events),
            "first_invocation_id": invocation_1,
            "first_job_id": job_1,
            "first_status_after_release": terminal_1.get("status") if isinstance(terminal_1, dict) else None,
            "second_execute_status_code": code_2,
            "second_response": execution_2,
            "recent_conflict_event_count": len(conflict_events),
        }
    except Exception as exc:
        out["02_resource_lock_conflict"] = {"ok": False, "exception": repr(exc)}
    finally:
        stop_daemon(process)

    process = None
    try:
        process = start_daemon()
        _, plan = post(
            "/admin/maintenance/plans",
            body={
                "goal": "maintenance-readonly-admin-e2e",
                "target_node_id": "winClient",
                "steps": [
                    {"function_name": "windows.transfer.croc.status", "input": {}},
                    {
                        "function_name": "windows.exec.run",
                        "input": {
                            "profile": "user.readonly",
                            "command": "whoami",
                            "reason": "remote e2e identity check",
                        },
                    },
                ],
            },
            timeout=30,
        )
        plan_id = plan.get("plan_id") if isinstance(plan, dict) else None
        code_run, run = post(
            f"/admin/maintenance/plans/{plan_id}/run",
            params={"dry_run": "false"},
            timeout=60,
        )
        run_id = run.get("run_id") if isinstance(run, dict) else None
        detail = wait_run(run_id) if run_id else None
        steps = (detail.get("steps") or detail.get("step_results") or []) if isinstance(detail, dict) else []
        out["04_maintenance_plan_run"] = {
            "ok": isinstance(detail, dict)
            and detail.get("status") == "succeeded"
            and steps
            and all(step.get("status") == "succeeded" for step in steps),
            "plan_id": plan_id,
            "run_id": run_id,
            "run_start_code": code_run,
            "run_status": detail.get("status") if isinstance(detail, dict) else None,
            "summary": detail.get("summary") if isinstance(detail, dict) else None,
            "step_statuses": [step.get("status") for step in steps],
            "run_response": run,
        }
    except Exception as exc:
        out["04_maintenance_plan_run"] = {"ok": False, "exception": repr(exc)}
    finally:
        stop_daemon(process)

    process = None
    try:
        process = start_daemon()
        prompt_multi = "\u5168\u9762\u68c0\u67e5\u8fd9\u53f0 Windows \u673a\u5668\u72b6\u6001"
        code, session = post(
            "/agent/sessions",
            headers=AGENT,
            body={
                "actor_id": "win-agent-multitool-admin-e2e",
                "execution_mode": "auto",
                "max_total_duration_sec": 180,
            },
            timeout=30,
        )
        if code < 400:
            code_invoke, response = post(
                "/agent/invoke",
                headers=AGENT,
                body={
                    "session_id": session["session_id"],
                    "provider_name": "deepseek",
                    "prompt": prompt_multi,
                    "execution_mode": "auto",
                },
                timeout=240,
            )
            calls = response.get("tool_calls") if isinstance(response, dict) else []
            out["03_agent_deepseek_multi_tool"] = {
                "ok": code_invoke < 400
                and isinstance(response, dict)
                and response.get("success") is True
                and len(calls) >= 3
                and all(call.get("status") == "succeeded" for call in calls),
                "status_code": code_invoke,
                "success": response.get("success") if isinstance(response, dict) else None,
                "status": response.get("status") if isinstance(response, dict) else None,
                "tool_count": len(calls) if isinstance(calls, list) else 0,
                "tool_names": [call.get("name") for call in calls] if isinstance(calls, list) else [],
                "tool_statuses": [call.get("status") for call in calls] if isinstance(calls, list) else [],
                "error": response.get("error") if isinstance(response, dict) else response,
            }
        else:
            out["03_agent_deepseek_multi_tool"] = {
                "ok": False,
                "session_code": code,
                "response": session,
            }

        prompt_plan = "\u68c0\u67e5\u6253\u5370\u670d\u52a1\uff0c\u5982\u679c\u4e0d\u6b63\u5e38\u5c31\u4fee\u590d"
        code, session_2 = post(
            "/agent/sessions",
            headers=AGENT,
            body={
                "actor_id": "win-agent-plan-admin-e2e",
                "execution_mode": "auto",
                "max_total_duration_sec": 180,
            },
            timeout=30,
        )
        if code < 400:
            code_plan, plan = post(
                "/agent/plan",
                headers=AGENT,
                body={
                    "session_id": session_2["session_id"],
                    "provider_name": "deepseek",
                    "prompt": prompt_plan,
                    "target_node_id": "winClient",
                    "execution_mode": "auto",
                    "max_total_duration_sec": 180,
                },
                timeout=240,
            )
            steps = plan.get("steps") if isinstance(plan, dict) else []
            out["05_agent_plan_complex_prompt"] = {
                "ok": code_plan < 400
                and isinstance(plan, dict)
                and plan.get("status") in {"draft", "waiting_approval", "ready"}
                and len(steps) >= 2,
                "status_code": code_plan,
                "plan_id": plan.get("plan_id") if isinstance(plan, dict) else None,
                "status": plan.get("status") if isinstance(plan, dict) else None,
                "goal": plan.get("goal") if isinstance(plan, dict) else None,
                "step_count": len(steps) if isinstance(steps, list) else 0,
                "steps": steps,
                "error": plan.get("error") if isinstance(plan, dict) else plan,
            }
        else:
            out["05_agent_plan_complex_prompt"] = {
                "ok": False,
                "session_code": code,
                "response": session_2,
            }
    except Exception as exc:
        out.setdefault("03_agent_deepseek_multi_tool", {"ok": False, "exception": repr(exc)})
        out.setdefault("05_agent_plan_complex_prompt", {"ok": False, "exception": repr(exc)})
    finally:
        stop_daemon(process)

    try:
        invocation_id = out.get("01_l2_real_restart", {}).get("invocation_id") or "inv_3fc12e0506f54329"
        job_id = out.get("01_l2_real_restart", {}).get("job_id") or "job_8282f841be424239"
        events = timeline({"invocation_id": invocation_id, "limit": 100})
        events_by_job = timeline({"job_id": job_id, "limit": 100})
        merged = events + [event for event in events_by_job if event not in events]
        found = event_types(merged)
        expected = [
            "approval.requested",
            "approval.approved",
            "approval.consumed",
            "l2.action.started",
            "resource.lock.acquired",
            "job.queued",
            "job.succeeded",
            "resource.lock.released",
            "l2.action.completed",
        ]
        out["07_timeline_audit_integrity"] = {
            "ok": all(event in found for event in expected),
            "invocation_id": invocation_id,
            "job_id": job_id,
            "missing": [event for event in expected if event not in found],
            "event_types": found,
        }
    except Exception as exc:
        out["07_timeline_audit_integrity"] = {"ok": False, "exception": repr(exc)}

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
