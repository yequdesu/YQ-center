"""Center-side reducer from AgentRunEvent facts to TaskState and PlanStep."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.agent_plan import AgentPlan, AgentPlanStep
from yequ.models.agent_run import AgentRun, AgentRunEvent
from yequ.runtime.replanner import apply_decision, decide_next_action
from yequ.runtime.task_state import (
    append_unique_item,
    get_task_state,
    remove_item_by_key,
    set_task_state,
)

JsonDict = dict[str, Any]


async def reduce_agent_run_event(
    db: AsyncSession,
    run: AgentRun,
    event: AgentRunEvent,
) -> JsonDict:
    state = get_task_state(run)
    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    event_type = event.event_type

    if event_type == "run.created":
        _record_fact(state, kind="run", key=run.run_id, data={"status": "created"})
    elif event_type in {"llm.tool_call_requested", "tool.started"}:
        _record_tool_request(state, payload)
        await _update_plan_step(
            db,
            run=run,
            event=event,
            status="running",
            payload=payload,
        )
    elif event_type == "tool.completed":
        _record_tool_completion(state, payload)
        await _update_plan_step(
            db,
            run=run,
            event=event,
            status="succeeded",
            payload=payload,
        )
    elif event_type == "tool.failed":
        _record_blocker(state, payload, terminal=False)
        await _update_plan_step(
            db,
            run=run,
            event=event,
            status="failed",
            payload=payload,
        )
    elif event_type == "approval.waiting":
        _record_pending_approval(state, payload, status="pending")
        await _update_plan_step(
            db,
            run=run,
            event=event,
            status="waiting_approval",
            payload=payload,
        )
    elif event_type == "approval.approved":
        _resolve_pending_approval(state, payload, status="approved")
        if _string(payload.get("operation_id")):
            _record_pending_operation(state, payload)
        await _update_plan_step(
            db,
            run=run,
            event=event,
            status="succeeded",
            payload=payload,
        )
    elif event_type == "approval.rejected":
        _resolve_pending_approval(state, payload, status="rejected")
        _record_blocker(state, payload, terminal=True)
        await _update_plan_step(
            db,
            run=run,
            event=event,
            status="failed",
            payload=payload,
        )
    elif event_type == "operation.waiting":
        _record_pending_operation(state, payload)
        await _update_plan_step(
            db,
            run=run,
            event=event,
            status="waiting_operation",
            payload=payload,
        )
    elif event_type in {
        "operation.succeeded",
        "operation.failed",
        "operation.cancelled",
        "operation.timeout",
        "operation.completed",
    }:
        _resolve_pending_operation(state, payload, event_type=event_type)
        if event_type != "operation.succeeded" and event_type != "operation.completed":
            _record_blocker(state, payload, terminal=True)
        await _update_plan_step(
            db,
            run=run,
            event=event,
            status=(
                "succeeded"
                if event_type in {"operation.succeeded", "operation.completed"}
                else "failed"
            ),
            payload=payload,
        )
    elif event_type in {"artifact.created", "artifact.presented", "artifact.read"}:
        _record_artifact(state, payload)
        await _update_plan_step(
            db,
            run=run,
            event=event,
            status="succeeded",
            payload=payload,
        )
    elif event_type == "llm.final_candidate":
        _record_final_candidate(state, payload)
    elif event_type == "run.completed":
        _record_final_candidate(state, payload)
        state["completion"]["status"] = "complete"
    elif event_type == "run.failed":
        _record_blocker(state, payload, terminal=True)
        state["completion"]["status"] = "failed"

    decision = decide_next_action(state)
    apply_decision(state, decision)
    await set_task_state(db, run, state)
    return state


def _record_tool_request(state: JsonDict, payload: JsonDict) -> None:
    capability_ref = _capability_ref(payload)
    if capability_ref:
        _record_working_capability(
            state,
            {
                "capability_ref": capability_ref,
                "source_id": _string(payload.get("source_id")),
                "node_id": _string(payload.get("node_id") or payload.get("target_node_id")),
                "status": "requested",
            },
        )


def _record_tool_completion(state: JsonDict, payload: JsonDict) -> None:
    _resolve_repaired_blockers(state, payload)
    capability_ref = _capability_ref(payload)
    if capability_ref:
        _record_working_capability(
            state,
            {
                "capability_ref": capability_ref,
                "source_id": _string(payload.get("source_id")),
                "node_id": _string(payload.get("node_id") or payload.get("target_node_id")),
                "status": "succeeded",
            },
        )
        _record_fact(
            state,
            kind="tool",
            key=str(payload.get("call_id") or capability_ref),
            data={"capability_ref": capability_ref, "status": "succeeded"},
        )
    _record_tool_result_facts(state, payload)
    _record_transfer_fact(state, payload)
    for artifact in _extract_artifacts(payload):
        _record_artifact(state, artifact)
    operation = payload.get("operation")
    if isinstance(operation, dict):
        _record_pending_operation(state, operation)


def _resolve_repaired_blockers(state: JsonDict, payload: JsonDict) -> None:
    blockers = state.get("blockers")
    if not isinstance(blockers, list) or not blockers:
        return
    capability_ref = _capability_ref(payload)
    function_name = _string(payload.get("function_name") or payload.get("name"))
    target_node_id = _string(payload.get("target_node_id") or payload.get("node_id"))
    blockers[:] = [
        item
        for item in blockers
        if not (
            isinstance(item, dict)
            and item.get("repairable") is True
            and item.get("terminal") is not True
            and _blocker_repaired_by_success(
                item,
                capability_ref=capability_ref,
                function_name=function_name,
                target_node_id=target_node_id,
            )
        )
    ]


def _blocker_repaired_by_success(
    blocker: JsonDict,
    *,
    capability_ref: str,
    function_name: str,
    target_node_id: str,
) -> bool:
    blocker_capability = _string(blocker.get("capability_ref"))
    if blocker_capability and blocker_capability in {capability_ref, function_name}:
        return True
    blocker_node = _string(blocker.get("target_node_id"))
    return bool(blocker_node and target_node_id and blocker_node == target_node_id)


def _record_pending_approval(state: JsonDict, payload: JsonDict, *, status: str) -> None:
    approval_id = _string(payload.get("approval_id"))
    if not approval_id:
        return
    append_unique_item(
        state["pending_approvals"],
        {
            "approval_id": approval_id,
            "status": status,
            "function_name": _string(payload.get("function_name") or payload.get("capability_ref")),
            "target_node_id": _string(payload.get("target_node_id") or payload.get("node_id")),
            "updated_at": _now_iso(),
        },
        key="approval_id",
    )


def _resolve_pending_approval(state: JsonDict, payload: JsonDict, *, status: str) -> None:
    approval_id = _string(payload.get("approval_id"))
    if not approval_id:
        return
    remove_item_by_key(state["pending_approvals"], key="approval_id", value=approval_id)
    _record_fact(
        state,
        kind="approval",
        key=approval_id,
        data={"approval_id": approval_id, "status": status},
    )


def _record_pending_operation(state: JsonDict, payload: JsonDict) -> None:
    operation_id = _string(payload.get("operation_id"))
    if not operation_id:
        return
    status = _string(payload.get("status")) or "waiting"
    append_unique_item(
        state["pending_operations"],
        {
            "operation_id": operation_id,
            "status": status,
            "kind": _string(payload.get("kind") or payload.get("operation_kind")),
            "title": _string(payload.get("title")),
            "updated_at": _now_iso(),
        },
        key="operation_id",
    )


def _resolve_pending_operation(state: JsonDict, payload: JsonDict, *, event_type: str) -> None:
    operation_id = _string(payload.get("operation_id"))
    if not operation_id:
        return
    remove_item_by_key(state["pending_operations"], key="operation_id", value=operation_id)
    status = _string(payload.get("status")) or event_type.removeprefix("operation.")
    _record_fact(
        state,
        kind="operation",
        key=operation_id,
        data={
            "operation_id": operation_id,
            "status": status,
            "kind": _string(payload.get("kind") or payload.get("operation_kind")),
            "error_code": _string(payload.get("error_code")),
            "error_message": _string(payload.get("error_message") or payload.get("message"))[
                :500
            ],
            "artifacts": _small_payload(payload.get("artifacts")),
        },
    )
    _record_transfer_fact(state, payload)


def _record_artifact(state: JsonDict, payload: JsonDict) -> None:
    artifact_id = _string(payload.get("artifact_id"))
    if not artifact_id:
        return
    artifact = {
        "artifact_id": artifact_id,
        "title": _string(payload.get("title")),
        "artifact_type": _string(payload.get("artifact_type") or payload.get("kind")),
        "content_type": _string(payload.get("content_type")),
        "node_id": _string(payload.get("node_id")),
        "line_range": _dict(payload.get("line_range")),
        "matched_lines": _dict(payload.get("matched_lines")),
        "truncated": (
            payload.get("truncated") if isinstance(payload.get("truncated"), bool) else None
        ),
        "read_ref": _string(payload.get("read_ref") or payload.get("ref_id")),
        "updated_at": _now_iso(),
    }
    append_unique_item(state["artifacts"], artifact, key="artifact_id")
    append_unique_item(state["working_set"]["artifacts"], artifact, key="artifact_id")


def _record_final_candidate(state: JsonDict, payload: JsonDict) -> None:
    completion = state.get("completion")
    if not isinstance(completion, dict):
        return
    completion["final_candidate"] = {
        "message_preview": _string(payload.get("message"))[:1000],
        "created_at": _now_iso(),
    }


def _record_working_capability(state: JsonDict, value: JsonDict) -> None:
    key = _string(value.get("source_id")) or _string(value.get("capability_ref"))
    if not key:
        return
    value["key"] = key
    append_unique_item(state["working_set"]["capabilities"], value, key="key")


def _record_fact(state: JsonDict, *, kind: str, key: str, data: JsonDict) -> None:
    append_unique_item(
        state["facts"],
        {"kind": kind, "key": key, "data": data, "updated_at": _now_iso()},
        key="key",
    )


def _record_blocker(state: JsonDict, payload: JsonDict, *, terminal: bool) -> None:
    key = _string(payload.get("error_code")) or _string(payload.get("approval_id"))
    key = key or _string(payload.get("operation_id")) or f"blocker_{len(state['blockers']) + 1}"
    classification = _classify_blocker(payload, terminal=terminal)
    append_unique_item(
        state["blockers"],
        {
            "key": key,
            "terminal": classification["terminal"],
            "repairable": classification["repairable"],
            "classification": classification["classification"],
            "error_code": _string(payload.get("error_code")),
            "message": _string(payload.get("error_message") or payload.get("message")),
            "capability_ref": _capability_ref(payload),
            "target_node_id": _string(payload.get("target_node_id") or payload.get("node_id")),
            "details": _small_payload(
                _dict(payload.get("details") or payload.get("error_details"))
            ),
            "updated_at": _now_iso(),
        },
        key="key",
    )
    _record_tool_result_facts(state, payload)
    _record_transfer_fact(state, payload)


def _record_tool_result_facts(state: JsonDict, payload: JsonDict) -> None:
    capability_ref = _capability_ref(payload)
    if capability_ref != "capability.invoke" and not capability_ref.endswith("exec.run"):
        return
    result = _dict(payload.get("result"))
    output = _dict(payload.get("output") or payload.get("output_data"))
    details = _dict(payload.get("details") or payload.get("error_details"))
    invoked = _dict(payload.get("input"))
    nested_input = _dict(invoked.get("input"))
    command = _string(result.get("command") or output.get("command") or nested_input.get("command"))
    profile = _string(result.get("profile") or output.get("profile") or nested_input.get("profile"))
    exit_code = result.get("exit_code") if "exit_code" in result else output.get("exit_code")
    error_code = _string(payload.get("error_code"))
    message = _string(payload.get("error_message") or payload.get("message"))
    stdout_tail = _string(result.get("stdout_tail") or output.get("stdout_tail"))
    stderr_tail = _string(result.get("stderr_tail") or output.get("stderr_tail"))
    stdout_ref = _string(result.get("stdout_ref") or output.get("stdout_ref"))
    stderr_ref = _string(result.get("stderr_ref") or output.get("stderr_ref"))
    raw_ref_id = _string(payload.get("raw_ref_id"))
    raw_ref = _dict(payload.get("raw_ref"))
    if not raw_ref_id:
        raw_ref_id = _string(raw_ref.get("ref_id"))
    fact: JsonDict = {
        "capability_ref": capability_ref,
        "status": _string(payload.get("status")) or ("failed" if error_code else "succeeded"),
        "target_node_id": _string(payload.get("target_node_id") or payload.get("node_id")),
        "profile": profile,
        "command": command[:500],
        "exit_code": exit_code if isinstance(exit_code, int) else None,
        "error_code": error_code,
        "message": message[:500],
        "available_execution_profiles": details.get("available_execution_profiles"),
        "stdout_ref": stdout_ref or None,
        "stderr_ref": stderr_ref or None,
        "raw_ref_id": raw_ref_id or None,
        "stdout_tail": stdout_tail[:1000],
        "stderr_tail": stderr_tail[:1000],
    }
    if _looks_like_permission_denied(message, error_code):
        fact["permission_denied"] = True
    if _looks_like_file_not_found(message, error_code, stdout_tail, stderr_tail):
        fact["file_not_found"] = True
    if error_code in {"missing_required_slot", "unsupported_enum_value"}:
        fact["schema_error"] = details
    key = _string(payload.get("call_id")) or f"exec_fact_{len(state['facts']) + 1}"
    _record_fact(state, kind="exec.run", key=key, data=fact)


def _record_transfer_fact(state: JsonDict, payload: JsonDict) -> None:
    transfer = _first_mapping(
        payload,
        "transfer",
        "transfer_session",
        "session",
        "result",
        "output",
        "output_data",
    )
    transfer_id = _string(payload.get("transfer_id")) or _string(transfer.get("transfer_id"))
    if not transfer_id:
        return
    data: JsonDict = {
        "transfer_id": transfer_id,
        "status": _string(payload.get("status") or transfer.get("status")),
        "source_node_id": _string(payload.get("source_node_id") or transfer.get("source_node_id")),
        "target_node_id": _string(payload.get("target_node_id") or transfer.get("target_node_id")),
        "source_path": _string(payload.get("source_path") or transfer.get("source_path")),
        "target_path": _string(payload.get("target_path") or transfer.get("target_path")),
        "target_output_dir": _string(
            payload.get("target_output_dir") or transfer.get("target_output_dir")
        ),
        "size_bytes": _int_or_none(payload.get("size_bytes") or transfer.get("size_bytes")),
        "sha256": _string(payload.get("sha256") or transfer.get("sha256")),
        "resumable": _bool_or_none(payload.get("resumable") or transfer.get("resumable")),
        "failed_side": _string(payload.get("failed_side") or transfer.get("failed_side")),
        "error_code": _string(payload.get("error_code") or transfer.get("error_code")),
        "error_message": _string(
            payload.get("error_message") or payload.get("message") or transfer.get("error_message")
        )[:500],
    }
    _record_fact(state, kind="transfer", key=transfer_id, data=data)


def _classify_blocker(payload: JsonDict, *, terminal: bool) -> JsonDict:
    error_code = _string(payload.get("error_code"))
    message = _string(payload.get("error_message") or payload.get("message"))
    details = _dict(payload.get("details") or payload.get("error_details"))
    if error_code in {
        "missing_required_slot",
        "unsupported_enum_value",
        "invalid_input",
        "target_node_mismatch",
        "target_node_required",
    }:
        return {
            "terminal": False,
            "repairable": True,
            "classification": "schema_or_slot_error",
        }
    if _looks_like_permission_denied(message, error_code):
        return {
            "terminal": False,
            "repairable": True,
            "classification": "permission_denied_retry_readonly_admin_if_available",
        }
    if details.get("available_execution_profiles") and (
        "profile" in message.lower() or error_code in {"profile_unavailable"}
    ):
        return {
            "terminal": False,
            "repairable": True,
            "classification": "profile_selection_error",
        }
    return {"terminal": terminal, "repairable": False, "classification": "tool_failure"}


def _looks_like_permission_denied(message: str, error_code: str) -> bool:
    normalized = f"{error_code} {message}".lower()
    return any(
        marker in normalized
        for marker in (
            "permission denied",
            "access denied",
            "operation not permitted",
            "eacces",
            "unauthorized",
        )
    )


def _looks_like_file_not_found(
    message: str,
    error_code: str,
    stdout_tail: str,
    stderr_tail: str,
) -> bool:
    normalized = f"{error_code} {message} {stdout_tail} {stderr_tail}".lower()
    return any(
        marker in normalized
        for marker in (
            "no such file",
            "not found",
            "cannot access",
            "cannot stat",
            "path not found",
            "file not found",
        )
    )


async def _update_plan_step(
    db: AsyncSession,
    *,
    run: AgentRun,
    event: AgentRunEvent,
    status: str,
    payload: JsonDict,
) -> None:
    plan_id = event.plan_id or _string((run.metadata_json or {}).get("plan_id"))
    if not plan_id:
        return
    step = await _resolve_plan_step(
        db,
        plan_id=plan_id,
        event=event,
        payload=payload,
        status=status,
    )
    if step is None:
        return
    step.status = status
    operation_id = _string(payload.get("operation_id"))
    approval_id = _string(payload.get("approval_id"))
    tool_call_id = _string(payload.get("tool_call_id") or payload.get("call_id"))
    if operation_id:
        step.operation_id = operation_id
    if tool_call_id:
        step.tool_call_id = tool_call_id
    metadata = dict(step.metadata_json or {})
    if approval_id:
        metadata["approval_id"] = approval_id
    artifact_id = _string(payload.get("artifact_id"))
    if artifact_id:
        artifact_ids = metadata.get("artifact_ids")
        if not isinstance(artifact_ids, list):
            artifact_ids = []
        if artifact_id not in artifact_ids:
            artifact_ids.append(artifact_id)
        metadata["artifact_ids"] = artifact_ids[:20]
    if payload:
        metadata["last_event"] = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "payload": _small_payload(payload),
        }
    step.metadata_json = metadata
    if status in {"succeeded", "failed", "cancelled"}:
        step.completed_at = step.completed_at or datetime.now(UTC)


async def _resolve_plan_step(
    db: AsyncSession,
    *,
    plan_id: str,
    event: AgentRunEvent,
    payload: JsonDict,
    status: str,
) -> AgentPlanStep | None:
    plan_step_id = event.plan_step_id
    if plan_step_id:
        result = await db.execute(
            select(AgentPlanStep).where(AgentPlanStep.step_id == plan_step_id)
        )
        step = result.scalar_one_or_none()
        if step is not None:
            return step
    result = await db.execute(select(AgentPlan).where(AgentPlan.plan_id == plan_id))
    plan = result.scalar_one_or_none()
    if plan is None:
        return None
    result = await db.execute(
        select(AgentPlanStep)
        .where(AgentPlanStep.plan_record_id == plan.id)
        .order_by(AgentPlanStep.step_index.asc())
    )
    steps = list(result.scalars().all())
    matched = _match_plan_step(steps, payload)
    if matched is not None:
        return matched
    if _should_use_root_step(event.event_type, payload):
        return steps[0] if steps else None
    return await _create_plan_step(
        db,
        plan=plan,
        steps=steps,
        event=event,
        payload=payload,
        status=status,
    )


def _match_plan_step(steps: list[AgentPlanStep], payload: JsonDict) -> AgentPlanStep | None:
    tool_call_id = _string(payload.get("tool_call_id") or payload.get("call_id"))
    operation_id = _string(payload.get("operation_id"))
    approval_id = _string(payload.get("approval_id"))
    if tool_call_id:
        for step in steps:
            if step.tool_call_id == tool_call_id:
                return step
    if operation_id:
        for step in steps:
            if step.operation_id == operation_id:
                return step
    if approval_id:
        for step in steps:
            metadata = step.metadata_json if isinstance(step.metadata_json, dict) else {}
            if metadata.get("approval_id") == approval_id:
                return step
    return None


def _should_use_root_step(event_type: str, payload: JsonDict) -> bool:
    if event_type in {"run.created", "llm.final_candidate", "run.completed", "run.failed"}:
        return True
    return not any(
        _string(payload.get(key))
        for key in ["tool_call_id", "call_id", "operation_id", "approval_id"]
    )


async def _create_plan_step(
    db: AsyncSession,
    *,
    plan: AgentPlan,
    steps: list[AgentPlanStep],
    event: AgentRunEvent,
    payload: JsonDict,
    status: str,
) -> AgentPlanStep:
    next_index = max((step.step_index for step in steps), default=0) + 1
    tool_call_id = _string(payload.get("tool_call_id") or payload.get("call_id")) or None
    operation_id = _string(payload.get("operation_id")) or None
    approval_id = _string(payload.get("approval_id")) or None
    step = AgentPlanStep(
        step_id=f"apstep_{secrets.token_hex(8)}",
        plan_record_id=plan.id,
        step_index=next_index,
        kind=_plan_step_kind(event.event_type),
        title=_plan_step_title(event.event_type, payload),
        status=status,
        operation_id=operation_id,
        tool_call_id=tool_call_id,
        metadata_json={
            "source": "agent_run_event",
            "event_id": event.event_id,
            "event_type": event.event_type,
            "approval_id": approval_id,
            "capability_ref": _capability_ref(payload),
        },
    )
    db.add(step)
    await db.flush()
    return step


def _plan_step_kind(event_type: str) -> str:
    if event_type.startswith("approval."):
        return "approval"
    if event_type.startswith("operation."):
        return "operation"
    if event_type.startswith("tool.") or event_type.startswith("llm.tool_call"):
        return "tool"
    return "agent_task"


def _plan_step_title(event_type: str, payload: JsonDict) -> str:
    capability_ref = _capability_ref(payload)
    if capability_ref:
        return capability_ref[:500]
    operation_id = _string(payload.get("operation_id"))
    if operation_id:
        return f"{event_type} {operation_id}"[:500]
    approval_id = _string(payload.get("approval_id"))
    if approval_id:
        return f"{event_type} {approval_id}"[:500]
    return event_type[:500]


def _extract_artifacts(payload: JsonDict) -> list[JsonDict]:
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        return [item for item in artifacts if isinstance(item, dict)]
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("artifacts"), list):
        return [item for item in result["artifacts"] if isinstance(item, dict)]
    return []


def _capability_ref(payload: JsonDict) -> str | None:
    return _string(
        payload.get("capability_ref")
        or payload.get("function_name")
        or payload.get("name")
        or payload.get("tool_name")
    )


def _small_payload(payload: object) -> JsonDict:
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key, value in payload.items()
        if key
        in {
            "call_id",
            "tool_call_id",
            "capability_ref",
            "source_id",
            "operation_id",
            "approval_id",
            "artifact_id",
            "status",
            "error_code",
            "error_message",
        }
    }


def _dict(value: object) -> JsonDict:
    return dict(value) if isinstance(value, dict) else {}


def _first_mapping(payload: JsonDict, *keys: str) -> JsonDict:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {}


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
