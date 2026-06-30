"""Agent session, planning, history, and timeline helpers.

The production ReAct execution path lives in ``agent_stream`` and enters Center
through ``CenterExecutionRuntime``. This module intentionally does not expose a
non-streaming Agent loop.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import yequ.db as yequ_db
from yequ.agent.provider import (
    AgentFunction,
    AgentMessage,
    AgentProvider,
)
from yequ.application import (
    MaintenancePlanApplicationService,
)

log = logging.getLogger(__name__)


def _make_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:16]}"


def _as_object_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


async def create_agent_session(
    *,
    actor_id: str,
    execution_mode: str = "auto",
    max_depth: int = 5,
    max_steps: int = 20,
    max_total_duration_sec: int = 300,
) -> dict[str, object]:
    """Create an Agent Session.  Uses a self-managed short-lived DB session."""
    from yequ.models.session import Session

    session_id = _make_session_id()
    now = datetime.now(UTC)

    async with yequ_db.async_session_factory() as db:
        sess = Session(
            session_id=session_id,
            actor_type="agent",
            actor_id=actor_id,
            status="active",
            execution_mode=execution_mode,
            label=session_id[:8],
            started_at=now,
            updated_at=now,
            metadata_={
                "max_depth": max_depth,
                "max_steps": max_steps,
                "max_total_duration_sec": max_total_duration_sec,
            },
        )
        db.add(sess)
        await db.commit()

    return {
        "session_id": session_id,
        "execution_mode": execution_mode,
        "max_depth": max_depth,
        "max_steps": max_steps,
        "max_total_duration_sec": max_total_duration_sec,
    }


async def agent_plan(
    provider: AgentProvider,
    *,
    session_id: str,
    prompt: str,
    target_node_id: str,
    available_functions: list[AgentFunction],
    context: dict[str, object] | None = None,
    execution_mode: str = "auto",
    max_total_duration_sec: int = 300,
) -> dict[str, object]:
    """Generate a structured MaintenancePlan IR from a maintenance prompt.

    Strategy:
    1. Call DeepSeek to classify intent: readonly_check | check_and_fix
    2. Build deterministic check->repair->verify steps based on intent
    3. Validate, store, return plan
    """
    # -- Step 1: Intent classification via provider --
    classification_prompt = (
        f"User request: {prompt}\n"
        "Classify this request as EXACTLY ONE of:\n"
        "- readonly_check: just check status, no repair needed\n"
        "- check_and_fix: check status AND repair/fix if unhealthy\n"
        "Output ONLY the classification word. No punctuation, no quotes, "
        "no explanation."
    )

    log.info(
        "agent plan prompt: len=%d has_utf8=%s preview=%s",
        len(prompt),
        any(ord(c) > 127 for c in prompt),
        repr(prompt[:100]),
    )

    provider_result = await asyncio.wait_for(
        provider.invoke(
            classification_prompt,
            available_functions=[],
            context={"session_id": session_id, **(context or {})},
        ),
        timeout=30.0,
    )
    if not provider_result.success:
        return {
            "status": "failed",
            "error": {
                "code": provider_result.error_code or "provider_error",
                "message": provider_result.error_message or "Provider failed to classify intent",
            },
        }
    msg = (provider_result.message or "").strip().lower()
    if msg == "readonly_check":
        intent = "readonly_check"
    elif msg == "check_and_fix":
        intent = "check_and_fix"
    else:
        return {
            "status": "failed",
            "error": {
                "code": "agent_protocol_error",
                "message": f"Provider returned invalid maintenance intent: {msg!r}",
            },
        }

    # -- Step 2: infer function and input from registered tool contracts --
    seed_calls = await _infer_plan_seed_calls(
        provider,
        prompt=prompt,
        available_functions=available_functions,
        session_id=session_id,
        context=context,
    )
    function_name = _select_check_function(available_functions, seed_calls)
    plan_input = _infer_plan_input(prompt, function_name, available_functions, seed_calls)

    # -- Step 3: Build IR steps based on intent --
    steps_ir: list[dict[str, object]] = []

    if intent == "check_and_fix":
        # Check
        steps_ir.append(
            {
                "seq": 1,
                "kind": "check",
                "function_name": function_name,
                "input": plan_input,
                "condition": "always",
                "depends_on": [],
                "risk": "readonly",
                "requires_approval": False,
            }
        )
        # Repair
        repair_func = _select_repair_function(function_name, available_functions, seed_calls)
        rollback_hint = _build_rollback_hint(
            function_name=function_name,
            repair_function_name=repair_func,
            input_data=plan_input,
            available_functions=available_functions,
        )
        if repair_func:
            steps_ir.append(
                {
                    "seq": 2,
                    "kind": "repair",
                    "function_name": repair_func,
                    "input": _input_for_function(repair_func, plan_input, seed_calls),
                    "condition": "if_previous_unhealthy",
                    "depends_on": [1],
                    "risk": "maintenance_write",
                    "requires_approval": True,
                    "rollback_hint": rollback_hint,
                }
            )
        # Verify
        steps_ir.append(
            {
                "seq": 3,
                "kind": "verify",
                "function_name": function_name,
                "input": plan_input,
                "condition": "after_repair",
                "depends_on": [2],
                "risk": "readonly",
                "requires_approval": False,
            }
        )
    else:
        # Readonly check only
        steps_ir.append(
            {
                "seq": 1,
                "kind": "check",
                "function_name": function_name,
                "input": plan_input,
                "condition": "always",
                "depends_on": [],
                "risk": "readonly",
                "requires_approval": False,
            }
        )

    # -- Step 4: Validate --
    has_write = any(s["requires_approval"] for s in steps_ir)
    for s in steps_ir:
        requires_approval = bool(s.get("requires_approval"))
        kind = str(s.get("kind", ""))
        function_name_value = str(s.get("function_name", ""))
        if requires_approval and kind not in ("repair", "rollback"):
            s["requires_approval"] = False  # fix incorrect metadata
            requires_approval = False
        func_exists = any(f.name == function_name_value for f in available_functions)
        if not func_exists:
            return {
                "status": "failed",
                "error": {
                    "code": "function_not_available",
                    "message": f"Function {function_name_value!r} not registered on node",
                },
            }

    # -- Step 5: Create plan --
    async with yequ_db.async_session_factory() as plan_db:
        plan = await MaintenancePlanApplicationService(plan_db).create(
        goal=prompt,
        actor_id=provider.provider_name(),
        target_node_id=target_node_id,
        steps=[
            {
                "function_name": str(s.get("function_name", "")),
                "input": _as_object_dict(s.get("input", {})),
                "kind": str(s.get("kind", "")),
                "condition": str(s.get("condition", "")),
                "depends_on": [str(d) for d in _as_list(s.get("depends_on", []))],
                "requires_approval": bool(s.get("requires_approval")),
                "risk": str(s.get("risk", "safe")),
                "continue_on_failure": False,
                "rollback_hint": s.get("rollback_hint"),
            }
            for s in steps_ir
        ],
            session_id=session_id,
            risk="maintenance" if has_write else "safe",
            max_total_duration_sec=max_total_duration_sec,
            execution_mode=execution_mode,
        )

        # Set status + approval
        if has_write:
            plan.status = "waiting_approval"
            await plan_db.commit()
        else:
            await plan_db.commit()

    return {
        "status": plan.status,
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "approval_required": has_write,
        "step_count": len(steps_ir),
        "steps": steps_ir,
    }


async def _infer_plan_seed_calls(
    provider: AgentProvider,
    *,
    prompt: str,
    available_functions: list[AgentFunction],
    session_id: str,
    context: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """Ask the provider for tool-shaped planning hints without execution."""
    try:
        result = await asyncio.wait_for(
            provider.invoke(
                (
                    "Return the tool calls you would use to handle this "
                    "maintenance request. If no tool fits, return zero tool "
                    "calls. Do not execute anything, do not explain.\n\n"
                    f"Request: {prompt}"
                ),
                available_functions=available_functions,
                context={
                    "session_id": session_id,
                    "purpose": "maintenance_plan_seed",
                    **(context or {}),
                },
            ),
            timeout=30.0,
        )
        return [c for c in (result.tool_calls or []) if isinstance(c, dict)]
    except Exception:
        log.warning("failed to generate seed tool calls from provider")
        return []


def _build_rollback_hint(
    *,
    function_name: str,
    repair_function_name: str | None,
    input_data: dict[str, object],
    available_functions: list[AgentFunction],
) -> dict[str, object]:
    """Build a platform-neutral rollback hint for a repair/write step."""
    rollback_function = _select_rollback_function(function_name, available_functions)
    if rollback_function:
        return {
            "action": "restore_previous_state",
            "target_type": _tool_family(function_name),
            "target_input": dict(input_data),
            "repair_function": repair_function_name,
            "rollback_function": rollback_function,
            "requires_approval": True,
        }
    return {
        "action": "manual_review",
        "reason": "rollback_function_not_registered",
        "target_type": _tool_family(function_name),
        "target_input": dict(input_data),
        "repair_function": repair_function_name,
        "requires_approval": True,
    }


def _select_check_function(
    available_functions: list[AgentFunction],
    seed_calls: list[dict[str, object]],
) -> str:
    available = {f.name: f for f in available_functions}
    for call in seed_calls:
        name = str(call.get("name") or "")
        func = available.get(name)
        if func and func.effect == "read":
            return name
    preferred = [
        f
        for f in available_functions
        if f.effect == "read"
        and any(
            token in f.name.rsplit(".", 1)[-1]
            for token in ("status", "check", "detail", "snapshot")
        )
    ]
    if preferred:
        return sorted(preferred, key=lambda f: (0 if "status" in f.name else 1, f.name))[0].name
    read_funcs = [f for f in available_functions if f.effect == "read"]
    if read_funcs:
        return sorted(read_funcs, key=lambda f: f.name)[0].name
    return available_functions[0].name if available_functions else ""


def _select_repair_function(
    check_function_name: str,
    available_functions: list[AgentFunction],
    seed_calls: list[dict[str, object]],
) -> str | None:
    available = {f.name: f for f in available_functions}
    family = _tool_family(check_function_name)
    for call in seed_calls:
        name = str(call.get("name") or "")
        func = available.get(name)
        if func and func.effect in ("write", "destructive"):
            return name
    candidates = [
        f
        for f in available_functions
        if f.effect in ("write", "destructive") and _tool_family(f.name) == family
    ]
    if not candidates:
        candidates = [f for f in available_functions if f.effect in ("write", "destructive")]
    if not candidates:
        return None
    priority = ("ensure", "repair", "restart", "set", "start")
    return sorted(
        candidates,
        key=lambda f: (
            next((i for i, token in enumerate(priority) if token in f.name), len(priority)),
            f.name,
        ),
    )[0].name


def _select_rollback_function(
    function_name: str,
    available_functions: list[AgentFunction],
) -> str | None:
    family = _tool_family(function_name)
    candidates = [
        f
        for f in available_functions
        if f.effect in ("write", "destructive")
        and _tool_family(f.name) == family
        and any(token in f.name for token in ("restore", "rollback", "ensure_state", "set_state"))
    ]
    return sorted(candidates, key=lambda f: f.name)[0].name if candidates else None


def _infer_plan_input(
    prompt: str,
    function_name: str,
    available_functions: list[AgentFunction],
    seed_calls: list[dict[str, object]],
) -> dict[str, object]:
    seeded = _input_for_function(function_name, {}, seed_calls)
    if seeded:
        return seeded
    func = next((f for f in available_functions if f.name == function_name), None)
    required: list[str] = []
    if func and isinstance(func.input_schema, dict):
        raw_required = func.input_schema.get("required")
        required = (
            [item for item in raw_required if isinstance(item, str)]
            if isinstance(raw_required, list)
            else []
        )
    if required == ["name"]:
        target = _quoted_or_named_target(prompt)
        return {"name": target} if target else {}
    return {}


def _input_for_function(
    function_name: str | None,
    default_input: dict[str, object],
    seed_calls: list[dict[str, object]],
) -> dict[str, object]:
    if function_name:
        for call in seed_calls:
            if call.get("name") == function_name and isinstance(call.get("input"), dict):
                return _as_object_dict(call["input"])
    return dict(default_input)


def _quoted_or_named_target(prompt: str) -> str | None:
    import re

    quoted = re.search(r"[`\"']([^`\"']{1,96})[`\"']", prompt)
    if quoted:
        return quoted.group(1).strip()
    target_match = re.search(
        r"(?:service|process|daemon|task)\s+([A-Za-z0-9_.:-]{2,96})",
        prompt,
        re.IGNORECASE,
    )
    if target_match:
        return target_match.group(1).strip()
    return None


def _tool_family(function_name: str) -> str:
    parts = function_name.split(".")
    if len(parts) <= 2:
        return function_name
    return ".".join(parts[:-1])


# -- Session history management --


async def _load_session_history(db: AsyncSession, session_id: str) -> list[AgentMessage]:
    """Load persisted conversation history for a session."""
    from yequ.models.agent_message import AgentMessage as AgentMessageModel

    result = await db.execute(
        select(AgentMessageModel)
        .where(AgentMessageModel.session_id == session_id)
        .order_by(AgentMessageModel.created_at.desc())
        .limit(50)
    )
    rows = list(reversed(result.scalars().all()))
    return [
        AgentMessage(
            role=m.role,
            content=m.content,
            tool_call_id=m.tool_call_id,
            tool_calls=cast(list[dict[str, object]] | None, m.tool_calls),
            message_id=m.message_id,
        )
        for m in rows
    ]


async def _save_session_history(
    db: AsyncSession,
    session_id: str,
    messages: list[AgentMessage],
) -> None:
    """Persist new messages to session history.

    Reads existing message_ids, only inserts messages not yet saved.
    """
    from yequ.models.agent_message import AgentMessage as AgentMessageModel

    existing_ids_result = await db.execute(
        select(AgentMessageModel.message_id).where(AgentMessageModel.session_id == session_id)
    )
    existing_ids = {row[0] for row in existing_ids_result.all()}

    now = datetime.now(UTC)
    new_count = 0
    for m in messages:
        if m.role == "system":
            continue  # never persist system prompt
        if m.message_id and m.message_id in existing_ids:
            continue
        mid = m.message_id or f"msg_{uuid.uuid4().hex[:16]}"
        m.message_id = mid
        db.add(
            AgentMessageModel(
                message_id=mid,
                session_id=session_id,
                role=m.role,
                content=m.content,
                tool_call_id=m.tool_call_id,
                tool_calls=m.tool_calls,
                created_at=now,
            )
        )
        new_count += 1
        now = datetime.now(UTC)  # slight offset per message for ordering

    if new_count > 0:
        await db.flush()

    # Update session.updated_at when new messages are persisted
    if new_count > 0:
        from yequ.models.session import Session

        sess_result = await db.execute(select(Session).where(Session.session_id == session_id))
        sess = sess_result.scalar_one_or_none()
        if sess is not None:
            sess.updated_at = datetime.now(UTC)

    # Trim old messages to keep history bounded
    await _trim_history(db, session_id)


async def _trim_history(db: AsyncSession, session_id: str, keep_last: int = 40) -> None:
    """Keep only the most recent N messages for a session.

    Never orphan tool messages — if the cutoff would leave a tool message
    whose parent assistant(tool_calls) was deleted, also delete the orphaned
    tool messages.  This prevents invalid history where a tool role message
    has no preceding assistant with matching tool_calls, which causes
    DeepSeek API 400 errors.
    """
    from yequ.models.agent_message import AgentMessage as AgentMessageModel

    # Fetch all messages ordered by created_at (oldest first)
    result = await db.execute(
        select(AgentMessageModel)
        .where(AgentMessageModel.session_id == session_id)
        .order_by(AgentMessageModel.created_at.asc())
    )
    all_messages = list(result.scalars().all())

    if len(all_messages) <= keep_last:
        return

    # Collect tool_call_ids from the assistant messages that will be deleted
    cutoff_index = len(all_messages) - keep_last
    to_delete = all_messages[:cutoff_index]
    to_keep = all_messages[cutoff_index:]

    orphaned_call_ids: set[str] = set()
    for msg in to_delete:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                call_id = tc.get("call_id")
                if isinstance(call_id, str):
                    orphaned_call_ids.add(call_id)

    # Also delete tool messages in the kept set whose parent was deleted
    for msg in to_keep:
        if (
            msg.role == "tool"
            and msg.tool_call_id
            and msg.tool_call_id in orphaned_call_ids
        ):
            to_delete.append(msg)

    for old in to_delete:
        await db.delete(old)
    if to_delete:
        await db.flush()

async def _write_timeline(
    db: AsyncSession | None,
    event_type: str,
    *,
    session_id: str,
    actor: str,
    call_id: str | None = None,
    function_name: str | None = None,
    invocation_id: str | None = None,
    job_id: str | None = None,
    target_node_id: str | None = None,
    status: str | None = None,
    error_code: str | None = None,
    error: str | None = None,
    reason: str | None = None,
    tool_call_count: int | None = None,
    risk: str | None = None,
    effect: str | None = None,
    prompt: str | None = None,
    step: int | None = None,
    success: bool | None = None,
    final: bool | None = None,
) -> None:
    """Write an agent timeline event in the current transaction.

    Agent request handlers already control commit boundaries. Using the same
    session prevents SQLite write-lock conflicts during tests and keeps the
    event atomic with the state transition it describes. When db is None, a
    short independent session is used for background-only callers.
    """
    from sqlalchemy import func

    from yequ.models.timeline import TimelineEvent

    async def _add_event(_db: AsyncSession) -> None:
        result = await _db.execute(select(func.max(TimelineEvent.global_seq)))
        max_seq = result.scalar() or 0
        next_seq: int = max_seq + 1

        data: dict[str, object] = {}
        if call_id:
            data["call_id"] = call_id
        if function_name:
            data["function_name"] = function_name
        if invocation_id:
            data["invocation_id"] = invocation_id
        if job_id:
            data["job_id"] = job_id
        if target_node_id:
            data["target_node_id"] = target_node_id
        if status:
            data["status"] = status
        if error_code:
            data["error_code"] = error_code
        if error:
            data["error"] = error
        if reason:
            data["reason"] = reason
        if tool_call_count is not None:
            data["tool_call_count"] = tool_call_count
        if risk:
            data["risk"] = risk
        if effect:
            data["effect"] = effect
        if prompt:
            data["prompt"] = prompt[:500]
        if step is not None:
            data["step"] = step
        if success is not None:
            data["success"] = success
        if final is not None:
            data["final"] = final

        event = TimelineEvent(
            global_seq=next_seq,
            event_type=event_type,
            actor_type="agent",
            actor_id=actor,
            session_id=session_id,
            invocation_id=invocation_id,
            job_id=job_id,
            node_id=target_node_id,
            data=data,
            timestamp=datetime.now(UTC),
        )
        _db.add(event)
        await _db.flush()

    if db is not None:
        await _add_event(db)
    else:
        async with yequ_db.async_session_factory() as _db:
            await _add_event(_db)
            await _db.commit()

