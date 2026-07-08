from __future__ import annotations

import json
from typing import Any

import requests

BASE = "https://gtw.yequdesu.top"
ADMIN = {"Authorization": "Bearer qq756522327"}
AGENT = {"Authorization": "Bearer win-agent-pg-e2e-197042c1ca2674edf948fc86"}


def json_or_text(response: requests.Response) -> Any:
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    return response.text


def timeline_events(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, dict):
        events = body.get("events") or body.get("items") or body.get("value") or []
    elif isinstance(body, list):
        events = body
    else:
        events = []
    return [event for event in events if isinstance(event, dict)]


def main() -> None:
    out: dict[str, Any] = {}

    prompt = "检查打印服务，如果不正常就修复"
    session = requests.post(
        BASE + "/agent/sessions",
        headers=AGENT,
        json={
            "actor_id": "agent-plan-fix-verify",
            "execution_mode": "auto",
            "max_total_duration_sec": 180,
        },
        timeout=30,
    )
    out["session"] = {"status": session.status_code, "body": json_or_text(session)}

    if session.status_code < 400:
        session_id = session.json()["session_id"]
        response = requests.post(
            BASE + "/agent/plan",
            headers=AGENT,
            json={
                "session_id": session_id,
                "provider_name": "deepseek",
                "prompt": prompt,
                "target_node_id": "winClient",
                "execution_mode": "auto",
                "max_total_duration_sec": 180,
            },
            timeout=240,
        )
        body = json_or_text(response)
        steps = body.get("steps") if isinstance(body, dict) else []
        names = [step.get("function_name") for step in steps] if isinstance(steps, list) else []
        out["agent_plan"] = {
            "status_code": response.status_code,
            "body": body,
            "goal_repr": repr(body.get("goal")) if isinstance(body, dict) else None,
            "step_count": len(steps) if isinstance(steps, list) else 0,
            "function_names": names,
            "has_l2_fix_step": any(
                name in {"system.service.ensure_running", "system.service.restart"}
                for name in names
            ),
            "expected_ok": response.status_code == 200
            and isinstance(body, dict)
            and body.get("status") == "waiting_approval"
            and len(steps) >= 2
            and any(
                name in {"system.service.ensure_running", "system.service.restart"}
                for name in names
            ),
        }

    approval_id = "apv_ac0dff836fe5466f"
    timeline = requests.get(
        BASE + "/admin/timeline",
        headers=ADMIN,
        params={"approval_id": approval_id, "limit": 100},
        timeout=30,
    )
    timeline_body = json_or_text(timeline)
    events = timeline_events(timeline_body)
    event_types = [event.get("event_type") or event.get("type") for event in events]
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
    out["timeline_approval_id"] = {
        "status_code": timeline.status_code,
        "approval_id": approval_id,
        "event_types": event_types,
        "missing": [event for event in expected if event not in event_types],
        "expected_ok": timeline.status_code == 200
        and all(event in event_types for event in expected),
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
