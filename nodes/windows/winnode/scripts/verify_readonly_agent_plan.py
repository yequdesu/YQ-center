from __future__ import annotations

import json
from typing import Any

import requests

BASE = "https://gtw.yequdesu.top"
AGENT = {"Authorization": "Bearer win-agent-pg-e2e-197042c1ca2674edf948fc86"}


def json_or_text(response: requests.Response) -> Any:
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    return response.text


def main() -> None:
    session = requests.post(
        BASE + "/agent/sessions",
        headers=AGENT,
        json={
            "actor_id": "readonly-agent-plan-verify",
            "execution_mode": "auto",
            "max_total_duration_sec": 180,
        },
        timeout=30,
    )
    out: dict[str, Any] = {
        "session": {"status_code": session.status_code, "body": json_or_text(session)}
    }
    if session.status_code >= 400:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    plan = requests.post(
        BASE + "/agent/plan",
        headers=AGENT,
        json={
            "session_id": session.json()["session_id"],
            "provider_name": "deepseek",
            "prompt": "check print spooler service status",
            "target_node_id": "winClient",
            "execution_mode": "auto",
            "max_total_duration_sec": 180,
        },
        timeout=240,
    )
    body = json_or_text(plan)
    steps = body.get("steps") if isinstance(body, dict) else []
    out["plan"] = {
        "status_code": plan.status_code,
        "body": body,
        "step_count": len(steps) if isinstance(steps, list) else 0,
        "function_names": [step.get("function_name") for step in steps]
        if isinstance(steps, list)
        else [],
        "kinds": [step.get("kind") for step in steps] if isinstance(steps, list) else [],
        "requires_approval": [step.get("requires_approval") for step in steps]
        if isinstance(steps, list)
        else [],
        "expected_ok": plan.status_code == 200
        and isinstance(body, dict)
        and body.get("status") in {"ready", "draft"}
        and body.get("approval_required") is not True
        and len(steps) == 1
        and steps[0].get("kind") == "check"
        and steps[0].get("function_name") == "system.service.status"
        and steps[0].get("requires_approval") is False,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
