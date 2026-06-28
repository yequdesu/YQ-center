"""Agent Service -- orchestrates Agent interactions through Center.

Flow:
  prompt -> Provider.invoke -> raw tool_calls
  -> validate -> resolve node -> policy -> create Invocation
  -> wait for completion -> collect result -> final response
"""

import asyncio
import json as _json
import logging
import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.provider import (
    AgentFunction,
    AgentMessage,
    AgentProvider,
    sanitize_tool_payload_for_agent,
)
from yequ.agent.runtime_state import (
    AgentRuntimeController,
    AgentRuntimeFailure,
    AgentRuntimeLimits,
)
from yequ.agent.tool_execution import (
    AgentInvokeError,
    AgentInvokeOutput,
    AgentInvokeResponse,
    AgentInvokeTrace,
    AgentInvokeUsage,
    AgentToolCall,
    AgentToolPolicySnapshot,
)
from yequ.application import (
    ExecuteToolCommand,
    MaintenancePlanApplicationService,
    ToolInvocationApplicationService,
)
from yequ.db import async_session_factory
from yequ.protocol import ErrorCode

log = logging.getLogger(__name__)


def _runtime_error_response(
    *,
    provider: AgentProvider,
    session_id: str,
    failure: AgentRuntimeFailure,
    trace_id: str,
    call_path: list[str],
    step_count: int,
    max_depth: int,
    max_steps: int,
    max_total_duration_sec: int,
) -> AgentInvokeResponse:
    status = "timeout" if failure.error_code == ErrorCode.MAX_DURATION_EXCEEDED else "failed"
    return AgentInvokeResponse(
        success=False,
        status=status,
        provider_name=provider.provider_name(),
        session_id=session_id,
        error=AgentInvokeError(
            code=failure.error_code,
            message=failure.message,
        ),
        trace=AgentInvokeTrace(
            trace_id=trace_id,
            call_path=list(call_path),
            step_count=step_count + 1,
            max_depth=max_depth,
            max_steps=max_steps,
            max_total_duration_sec=max_total_duration_sec,
        ),
    )


def _make_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:16]}"


def _make_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:16]}"


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _as_object_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _as_str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


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

    async with async_session_factory() as db:
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


async def agent_invoke(
    provider: AgentProvider,
    *,
    session_id: str,
    prompt: str,
    available_functions: list[AgentFunction],
    call_path: list[str] | None = None,
    max_depth: int = 5,
    max_steps: int = 20,
    max_total_duration_sec: int = 300,
    step_count: int = 0,
    started_at: datetime | None = None,
    execution_mode: str = "auto",
    target_node_id: str | None = None,
    context: dict[str, object] | None = None,
) -> AgentInvokeResponse:
    """Invoke an Agent through the complete execution pipeline.

    1. Load session, validate constraints
    2. Call Provider to get raw tool_calls
    3. For each tool_call: validate -> resolve node -> policy -> execute -> wait
    4. Generate final output message
    5. Return complete AgentInvokeResponse

    All DB operations use short-lived sessions created from
    async_session_factory.  No session is held across await boundaries
    (LLM calls, job polling, approval waits).
    """
    from yequ.models.session import Session

    call_path = call_path or []
    now = datetime.now(UTC)
    if started_at is None:
        started_at = now
    trace_id = f"tr_{uuid.uuid4().hex[:16]}"
    runtime = AgentRuntimeController(
        limits=AgentRuntimeLimits(
            max_depth=max_depth,
            max_steps=max_steps,
            max_total_duration_sec=max_total_duration_sec,
        ),
        started_at=started_at,
        current_step=step_count,
        call_path=list(call_path),
    )

    initial_failure = runtime.check_initial_constraints(now)
    if initial_failure:
        return _runtime_error_response(
            provider=provider,
            session_id=session_id,
            failure=initial_failure,
            trace_id=trace_id,
            call_path=call_path,
            step_count=step_count,
            max_depth=max_depth,
            max_steps=max_steps,
            max_total_duration_sec=max_total_duration_sec,
        )

    # -- Session validation (short-lived session) --
    async with async_session_factory() as db:
        result = await db.execute(select(Session).where(Session.session_id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            return AgentInvokeResponse(
                success=False,
                status="failed",
                session_id=session_id,
                error=AgentInvokeError(code=ErrorCode.INTERNAL_ERROR, message="Session not found"),
                trace=AgentInvokeTrace(
                    trace_id=trace_id,
                    call_path=list(call_path),
                    step_count=step_count + 1,
                    max_depth=max_depth,
                    max_steps=max_steps,
                    max_total_duration_sec=max_total_duration_sec,
                ),
            )

        # SQLite strips timezone; if naive, assume UTC
        started = session.started_at
        session_actor_id = session.actor_id
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        deadline = datetime.fromtimestamp(started.timestamp() + max_total_duration_sec, tz=UTC)
        await db.rollback()

    # L1 readonly policy: build a set of known function names from available_functions.
    known_functions = {f.name for f in available_functions}

    # -- Step 1: Write agent.prompt.received (self-committing short session) --
    await _write_timeline(
        None,
        "agent.prompt.received",
        session_id=session_id,
        actor=provider.provider_name(),
        prompt=prompt,
        step=step_count + 1,
    )

    # -- Step 2: Load conversation history (short-lived session) --
    async with async_session_factory() as db:
        history = await _load_session_history(db, session_id)
        await db.rollback()

    # Build the messages array: system prompt is injected by the provider
    # Append the current user message
    history.append(AgentMessage(role="user", content=prompt))

    # -- Step 3: ReAct loop --
    log.info("agent loop started: provider=%s session_id=%s", provider.provider_name(), session_id)

    all_tool_calls: list[AgentToolCall] = []
    final_provider_message = ""
    loop_state = "running"
    total_usage: dict[str, object] = {}
    current_step = step_count
    provider_error: AgentInvokeError | None = None

    import time as _time

    while True:
        iteration = runtime.begin_iteration(datetime.now(UTC))
        if isinstance(iteration, AgentRuntimeFailure):
            loop_state = (
                "timeout" if iteration.error_code == ErrorCode.MAX_DURATION_EXCEEDED else "failed"
            )
            provider_error = AgentInvokeError(
                code=iteration.error_code,
                message=iteration.message,
                retryable=False,
            )
            break
        current_step = iteration.iteration

        # -- Call Provider --
        log.info(
            "agent loop iteration: step=%d/%d session_id=%s", current_step, max_steps, session_id
        )

        await _write_timeline(
            None,
            "agent.provider.started",
            session_id=session_id,
            actor=provider.provider_name(),
            step=current_step,
        )

        _t0 = _time.monotonic()
        try:
            provider_result = await asyncio.wait_for(
                provider.invoke(
                    "",
                    available_functions=available_functions,
                    messages=history,
                    context={
                        "call_path": list(call_path),
                        "session_id": session_id,
                        "step": current_step,
                        **(context or {}),
                    },
                ),
                timeout=45.0,
            )
            _elapsed = _time.monotonic() - _t0
            log.info(
                "agent provider request returned: step=%d elapsed=%.1fs", current_step, _elapsed
            )
        except TimeoutError:
            log.error("agent provider timed out: provider=%s", provider.provider_name())
            loop_state = "provider_timeout"
            provider_error = AgentInvokeError(
                code="provider_timeout",
                message="Agent provider timed out",
                retryable=True,
            )
            break

        # Handle provider failure
        if not provider_result.success:
            log.error("agent provider failed: %s", provider_result.error_message)
            loop_state = "provider_failed"
            provider_error = AgentInvokeError(
                code=provider_result.error_code or "provider_error",
                message=provider_result.error_message or "Agent provider failed",
                retryable=provider_result.retryable,
            )
            break

        await _write_timeline(
            None,
            "agent.provider.completed",
            session_id=session_id,
            actor=provider.provider_name(),
            success=True,
            tool_call_count=len(provider_result.tool_calls),
            step=current_step,
        )

        # Accumulate usage
        if provider_result.usage:
            total_usage = dict(provider_result.usage)
            existing_tool_calls = total_usage.get("tool_calls", 0)
            total_usage["tool_calls"] = (
                existing_tool_calls if isinstance(existing_tool_calls, int) else 0
            ) + len(provider_result.tool_calls)

        executable_calls = list(provider_result.tool_calls)
        provider_decision = runtime.decide_provider_output(
            assistant_text=provider_result.message,
            tool_calls=executable_calls,
        )

        if provider_decision.kind == "failure":
            loop_state = "protocol_error"
            failure = provider_decision.failure
            provider_error = AgentInvokeError(
                code=failure.error_code if failure else "agent_protocol_error",
                message=(
                    failure.message
                    if failure
                    else "Provider returned neither assistant text nor tool calls."
                ),
                retryable=False,
                details={
                    "finish_reason": provider_result.finish_reason,
                    "step": current_step,
                },
            )
            break
        if provider_decision.kind == "final":
            final_provider_message = provider_decision.final_message
            loop_state = provider_decision.status
            await _write_timeline(
                None,
                "agent.provider.completed",
                session_id=session_id,
                actor=provider.provider_name(),
                success=True,
                final=True,
            )
            break

        # -- Append assistant message with tool_calls --
        history.append(
            AgentMessage(
                role="assistant",
                content=provider_result.message or "",
                tool_calls=provider_result.tool_calls,
            )
        )

        # -- Validate + execute each tool call --
        iteration_results: list[AgentToolCall] = []
        for raw_tc in executable_calls:
            tc_name = str(raw_tc.get("name", ""))
            tc_call_id = str(raw_tc.get("call_id", _make_call_id()))
            tc_input = _as_object_dict(raw_tc.get("input", {}))

            # L1 check + loop detection + policy check (same as before)
            if tc_name not in known_functions:
                tc = AgentToolCall(
                    call_id=tc_call_id,
                    name=tc_name,
                    sanitized_name=str(raw_tc.get("sanitized_name", tc_name)),
                    input=tc_input,
                    status="failed",
                    error={
                        "code": "function_not_available",
                        "message": f"Function {tc_name!r} is not available",
                    },
                    finished_at=_iso(datetime.now(UTC)),
                )
                iteration_results.append(tc)
                history.append(
                    AgentMessage(
                        role="tool",
                        tool_call_id=tc_call_id,
                        content=_json.dumps({"status": "failed", "error": tc.error}),
                    )
                )
                continue

            if tc_name in call_path:
                tc = AgentToolCall(
                    call_id=tc_call_id,
                    name=tc_name,
                    sanitized_name=tc_name,
                    input=tc_input,
                    status="failed",
                    error={
                        "code": ErrorCode.CIRCULAR_DEPENDENCY,
                        "message": f"Circular: {tc_name!r}",
                    },
                    started_at=_iso(now),
                    finished_at=_iso(datetime.now(UTC)),
                )
                iteration_results.append(tc)
                history.append(
                    AgentMessage(
                        role="tool",
                        tool_call_id=tc_call_id,
                        content=_json.dumps({"status": "failed", "error": tc.error}),
                    )
                )
                continue

            # Execute through pipeline
            tc = AgentToolCall(
                call_id=tc_call_id,
                name=tc_name,
                sanitized_name=str(raw_tc.get("sanitized_name", tc_name)),
                input=tc_input,
            )
            func_meta = next((f for f in available_functions if f.name == tc_name), None)
            executed = await _execute_tool_call_v2(
                tc,
                session_id=session_id,
                actor_id=session_actor_id,
                execution_mode=execution_mode,
                call_path=list(call_path),
                max_depth=max_depth,
                deadline=deadline,
                provider_name=provider.provider_name(),
                target_node_id=target_node_id,
                declared_risk=func_meta.risk if func_meta else None,
                declared_effect=func_meta.effect if func_meta else None,
            )
            iteration_results.append(executed)

            # Suspend loop if approval is required
            if executed.status == "waiting_approval":
                loop_state = "waiting_approval"
                all_tool_calls.extend(iteration_results)
                async with async_session_factory() as save_db:
                    await _save_session_history(save_db, session_id, history)
                    await save_db.commit()
                return _build_loop_response(
                    provider=provider,
                    session_id=session_id,
                    tool_calls=all_tool_calls,
                    trace_id=trace_id,
                    call_path=call_path,
                    step_count=current_step,
                    max_depth=max_depth,
                    max_steps=max_steps,
                    max_total_duration_sec=max_total_duration_sec,
                    status="waiting_approval",
                    usage=total_usage,
                )

            # Append tool observation
            obs_content = _json.dumps(
                {
                    "status": executed.status,
                    "result": sanitize_tool_payload_for_agent(executed.result),
                    "error": sanitize_tool_payload_for_agent(executed.error),
                }
            )
            history.append(
                AgentMessage(
                    role="tool",
                    tool_call_id=tc_call_id,
                    content=obs_content,
                )
            )

        all_tool_calls.extend(iteration_results)

        # After observing, guard checks
        runtime.status = "observing"

    # -- Step 4: Enforce explicit provider final answer --
    if not final_provider_message and provider_error is None:
        provider_error = AgentInvokeError(
            code="agent_protocol_error",
            message="Agent loop ended without a provider final answer.",
            retryable=False,
            details={"loop_state": loop_state, "step": current_step},
        )
        loop_state = "protocol_error"

    # -- Step 5: Final status --
    any_waiting = any(tc.status == "waiting_approval" for tc in all_tool_calls)
    if any_waiting:
        final_status = "waiting_approval"
    elif loop_state == "timeout":
        final_status = "timeout"
    elif loop_state == "max_steps_reached":
        final_status = "max_steps_reached"
    elif loop_state in ("provider_failed", "provider_timeout", "protocol_error"):
        final_status = "failed"
    else:
        any_succeeded = any(tc.status == "succeeded" for tc in all_tool_calls)
        any_failed = any(tc.status in ("failed", "denied") for tc in all_tool_calls)
        if any_failed and any_succeeded:
            final_status = "partial"
        elif any_failed:
            final_status = "failed"
        else:
            final_status = "succeeded"

    # -- Step 6: Build output --
    output = AgentInvokeOutput(
        message=final_provider_message,
        summary=final_provider_message,
        highlights=_extract_highlights(all_tool_calls),
        tool_results=_collect_tool_results(all_tool_calls),
        data=_collect_tool_results(all_tool_calls),
    )

    # -- Step 7: Save history --
    if final_provider_message:
        history.append(AgentMessage(role="assistant", content=final_provider_message))
    async with async_session_factory() as save_db:
        await _save_session_history(save_db, session_id, history)
        await save_db.commit()

    await _write_timeline(
        None,
        "agent.final_response",
        session_id=session_id,
        actor=provider.provider_name(),
        status=final_status,
        tool_call_count=len(all_tool_calls),
    )

    return _build_loop_response(
        provider=provider,
        session_id=session_id,
        output=output,
        tool_calls=all_tool_calls,
        trace_id=trace_id,
        call_path=call_path,
        step_count=current_step,
        max_depth=max_depth,
        max_steps=max_steps,
        max_total_duration_sec=max_total_duration_sec,
        status=final_status,
        usage=total_usage,
        error=provider_error,
    )


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
    async with async_session_factory() as plan_db:
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


async def _execute_tool_call_v2(
    tc: AgentToolCall,
    *,
    session_id: str,
    actor_id: str,
    execution_mode: str,
    call_path: list[str],
    max_depth: int,
    deadline: datetime,
    provider_name: str,
    target_node_id: str | None = None,
    declared_risk: str | None = None,
    declared_effect: str | None = None,
) -> AgentToolCall:
    """Execute a tool call through the application-layer Center boundary.

    Uses a fresh DB session that is committed before returning, so the
    FOR UPDATE lock on timeline_sequences is never held across await
    boundaries.
    """
    tc.started_at = _iso(datetime.now(UTC))

    async with async_session_factory() as app_db:
        result = await ToolInvocationApplicationService(app_db).execute(
        ExecuteToolCommand(
            actor_type="agent",
            actor_id=actor_id,
            session_id=session_id,
            function_name=tc.name,
            input_data=tc.input,
            target_node_id=target_node_id,
            execution_mode=execution_mode,
            max_depth=max_depth,
            call_path=list(call_path) + [tc.name],
            wait_for_result=False,
            deadline=deadline,
            declared_risk=declared_risk,
            declared_effect=declared_effect,
        )
        )
        await app_db.commit()

    # If the job was created, poll for terminal status using short sessions.
    if result.status in {"created", "running"} and result.job_id:
        final_status = await _wait_invocation_terminal(
            result.invocation_id or "", deadline
        )
        result.status = final_status
        if final_status == "succeeded" and result.invocation_id:
            result.output_data = await _load_invocation_result(result.invocation_id)

    tc.target_node_id = result.target_node_id or ""
    tc.invocation_id = result.invocation_id or ""
    tc.job_ids = [result.job_id] if result.job_id else []
    tc.policy = AgentToolPolicySnapshot(
        decision="ask"
        if result.status == "approval_required"
        else ("deny" if result.status in {"denied", "unavailable", "not_found"} else "allow"),
        risk=result.risk,
        effect=result.effect,
        execution_mode=execution_mode,
    )
    tc.finished_at = _iso(datetime.now(UTC))

    if result.job_id:
        await _write_timeline(
            None,
            "agent.tool.selected",
            session_id=session_id,
            actor=provider_name,
            call_id=tc.call_id,
            function_name=tc.name,
            target_node_id=result.target_node_id,
            risk=result.risk,
            effect=result.effect,
        )
        await _write_timeline(
            None,
            "agent.tool.job.persisted",
            session_id=session_id,
            actor=provider_name,
            call_id=tc.call_id,
            function_name=tc.name,
            invocation_id=result.invocation_id,
            job_id=result.job_id,
            target_node_id=result.target_node_id,
            success=True,
        )
        await _write_timeline(
            None,
            "agent.tool.invocation_created",
            session_id=session_id,
            actor=provider_name,
            call_id=tc.call_id,
            function_name=tc.name,
            invocation_id=result.invocation_id,
            job_id=result.job_id,
            target_node_id=result.target_node_id,
        )

    if result.status == "approval_required":
        tc.status = "waiting_approval"
        tc.error = {
            "code": "approval_required",
            "message": result.error_message or "Write operation requires approval",
            "details": {"approval_id": result.approval_id},
        }
        await _write_timeline(
            None,
            "agent.tool.waiting_approval",
            session_id=session_id,
            actor=provider_name,
            call_id=tc.call_id,
            function_name=tc.name,
            invocation_id=result.invocation_id,
            target_node_id=result.target_node_id,
        )
        return tc

    if result.status in {"unavailable", "not_found"}:
        tc.status = "failed"
        tc.error = {
            "code": result.error_code or ErrorCode.FUNCTION_NOT_AVAILABLE,
            "message": result.error_message or f"No online node has {tc.name!r}",
        }
        await _write_timeline(
            None,
            "agent.tool.denied",
            session_id=session_id,
            actor=provider_name,
            call_id=tc.call_id,
            function_name=tc.name,
            error_code=_as_str(tc.error["code"]),
            target_node_id=result.target_node_id,
        )
        return tc

    if result.status == "denied":
        tc.status = "failed"
        tc.error = {
            "code": result.error_code or ErrorCode.POLICY_DENIED,
            "message": result.error_message or "Policy denied",
        }
        await _write_timeline(
            None,
            "agent.tool.denied",
            session_id=session_id,
            actor=provider_name,
            call_id=tc.call_id,
            function_name=tc.name,
            error_code=_as_str(tc.error["code"]),
            reason=_as_str(tc.error["message"]),
        )
        return tc

    if result.status == "succeeded":
        tc.status = "succeeded"
        tc.result = result.output_data or {}
        await _write_timeline(
            None,
            "agent.tool.completed",
            session_id=session_id,
            actor=provider_name,
            call_id=tc.call_id,
            function_name=tc.name,
            status="succeeded",
            invocation_id=result.invocation_id,
            job_id=result.job_id,
        )
        if result.effect in ("write", "destructive"):
            await _write_timeline(
                None,
                "l2.action.completed",
                session_id=session_id,
                actor=provider_name,
                call_id=tc.call_id,
                function_name=tc.name,
                status="succeeded",
                invocation_id=result.invocation_id,
                job_id=result.job_id,
                target_node_id=result.target_node_id,
            )
        return tc

    if result.status == "timeout":
        tc.status = "timeout"
        tc.error = {
            "code": result.error_code or "tool_timeout",
            "message": result.error_message or f"Tool {tc.name} timed out",
        }
    else:
        tc.status = "failed"
        tc.error = {
            "code": result.error_code or "tool_failed",
            "message": result.error_message or f"Tool {tc.name} ended with {result.status}",
        }
        if result.error_details:
            tc.error["details"] = result.error_details

    await _write_timeline(
        None,
        "agent.tool.failed",
        session_id=session_id,
        actor=provider_name,
        call_id=tc.call_id,
        function_name=tc.name,
        status=result.status,
        invocation_id=result.invocation_id,
        error_code=_as_str(tc.error["code"]),
        error=_as_str(tc.error["message"]),
    )
    if result.effect in ("write", "destructive"):
        await _write_timeline(
            None,
            "l2.action.failed",
            session_id=session_id,
            actor=provider_name,
            call_id=tc.call_id,
            function_name=tc.name,
            status=result.status,
            invocation_id=result.invocation_id,
            job_id=result.job_id,
            target_node_id=result.target_node_id,
            error=_as_str(tc.error["code"]),
        )
    return tc


async def _wait_invocation_terminal(
    invocation_id: str,
    deadline: datetime,
    poll_interval: float = 0.5,
) -> str:
    """Poll for Invocation terminal status using short-lived sessions.

    Creates a fresh DB session for each poll to avoid holding
    a connection open during the wait. Returns the final status.
    """
    from yequ.db import async_session_factory
    from yequ.models.invocation import Invocation

    while datetime.now(UTC) < deadline:
        async with async_session_factory() as db:
            result = await db.execute(
                select(Invocation).where(Invocation.invocation_id == invocation_id)
            )
            inv = result.scalar_one_or_none()
            if inv and inv.status in ("succeeded", "failed", "timeout", "cancelled", "partial"):
                return inv.status
        await asyncio.sleep(poll_interval)

    return "timeout"


async def _load_invocation_result(invocation_id: str) -> dict[str, object]:
    from yequ.db import async_session_factory
    from yequ.models.invocation import Invocation

    async with async_session_factory() as db:
        result = await db.execute(
            select(Invocation).where(Invocation.invocation_id == invocation_id)
        )
        inv = result.scalar_one_or_none()
        return dict(inv.result) if inv and isinstance(inv.result, dict) else {}


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
    """Keep only the most recent N messages for a session."""
    from yequ.models.agent_message import AgentMessage as AgentMessageModel

    result = await db.execute(
        select(AgentMessageModel)
        .where(AgentMessageModel.session_id == session_id)
        .order_by(AgentMessageModel.created_at.desc())
        .offset(keep_last)
    )
    old_messages = result.scalars().all()
    for old in old_messages:
        await db.delete(old)
    if old_messages:
        await db.flush()

# -- Loop response builder --


def _build_loop_response(
    *,
    provider: AgentProvider,
    session_id: str,
    output: AgentInvokeOutput | None = None,
    tool_calls: list[AgentToolCall],
    trace_id: str,
    call_path: list[str],
    step_count: int,
    max_depth: int,
    max_steps: int,
    max_total_duration_sec: int,
    status: str,
    usage: dict[str, object],
    error: AgentInvokeError | None = None,
) -> AgentInvokeResponse:
    """Build the final AgentInvokeResponse."""
    if output is None:
        output = AgentInvokeOutput()

    if error is None and status not in ("succeeded", "waiting_approval"):
        first_failed = next((tc for tc in tool_calls if tc.status != "succeeded"), None)
        if first_failed and first_failed.error:
            error = AgentInvokeError(
                code=_as_str(first_failed.error.get("code", "")),
                message=_as_str(first_failed.error.get("message", "")),
                retryable=bool(first_failed.error.get("retryable", False)),
                details=_as_object_dict(first_failed.error.get("details", {})),
            )

    return AgentInvokeResponse(
        success=(status in ("succeeded", "waiting_approval")),
        status=status,
        provider_name=provider.provider_name(),
        session_id=session_id,
        output=output,
        error=error,
        tool_calls=tool_calls,
        usage=AgentInvokeUsage(
            prompt_tokens=_as_int_or_none(usage.get("prompt_tokens")),
            completion_tokens=_as_int_or_none(usage.get("completion_tokens")),
            total_tokens=_as_int_or_none(usage.get("total_tokens")),
            tool_calls=len(tool_calls),
        ),
        trace=AgentInvokeTrace(
            trace_id=trace_id,
            call_path=list(call_path),
            step_count=step_count,
            max_depth=max_depth,
            max_steps=max_steps,
            max_total_duration_sec=max_total_duration_sec,
        ),
    )


def _extract_highlights(tool_calls: list[AgentToolCall]) -> list[str]:
    """Extract key findings from succeeded tool calls."""
    highlights: list[str] = []
    for tc in tool_calls:
        if tc.status == "succeeded" and tc.result:
            highlights.append(f"{tc.name}: completed")
        elif tc.status == "failed" and tc.error:
            highlights.append(f"FAILED: {tc.name} - {tc.error.get('message', '')}")
    return highlights


def _collect_tool_results(tool_calls: list[AgentToolCall]) -> dict[str, object]:
    """Collect succeeded tool results into a name鈫抮esult map."""
    results: dict[str, object] = {}
    for tc in tool_calls:
        if tc.status == "succeeded" and tc.result:
            results[tc.name] = tc.result
    return results


def _generate_output(provider_message: str, tool_calls: list[AgentToolCall]) -> AgentInvokeOutput:
    """Generate structured AgentInvokeOutput from tool results.

    Produces: summary text, highlights, tool_results map, and data dict.
    Each tool type gets a specific summary format.
    """
    if provider_message.strip() and not tool_calls:
        return AgentInvokeOutput(message=provider_message, data={"raw_message": provider_message})

    succeeded = [tc for tc in tool_calls if tc.status == "succeeded"]
    failed = [tc for tc in tool_calls if tc.status != "succeeded"]

    highlights: list[str] = []
    tool_results: dict[str, object] = {}
    parts: list[str] = []

    for tc in succeeded:
        if tc.result:
            tool_results[tc.name] = tc.result

        if tc.name == "system.metrics.snapshot" and tc.result:
            r = tc.result
            parts.append(
                f"CPU {r.get('cpu', '?')}%, "
                f"Memory {r.get('memory', '?')}%, "
                f"Disk {r.get('disk', '?')}%"
            )
            highlights.append(
                f"System metrics: CPU {r.get('cpu', '?')}%, Memory {r.get('memory', '?')}%"
            )

        elif tc.name == "system.service.status" and tc.result:
            name = tc.result.get("name", tc.input.get("name", "?"))
            status = tc.result.get("status", "?")
            start_type = tc.result.get("start_type", "?")
            parts.append(f"Service '{name}': {status} (startup: {start_type})")
            highlights.append(f"Service {name}: {status}")

        elif tc.name == "system.processes.list" and tc.result:
            count = tc.result.get("count", 0)
            top = [
                item for item in _as_list(tc.result.get("processes", [])) if isinstance(item, dict)
            ][:5]
            parts.append(f"{count} processes running")
            if top:
                names = [p.get("name", "?") for p in top]
                parts.append(f"Top: {', '.join(names)}")
                highlights.append(f"{count} processes, top: {', '.join(names[:3])}")

        elif tc.name == "system.eventlog.query" and tc.result:
            count = tc.result.get("total", 0)
            highest = tc.result.get("highest_level", "?")
            parts.append(f"{count} recent events (highest: {highest})")
            highlights.append(f"Event log: {count} events, highest level: {highest}")

        elif tc.name == "system.disk.detail" and tc.result:
            drives = [tc.result]
            for d in drives:
                # Support multiple field naming conventions from Windows Node
                total = (
                    d.get("total_gb")
                    or d.get("total_bytes")
                    or d.get("size")
                    or d.get("capacity")
                    or "?"
                )
                free = (
                    d.get("free_gb")
                    or d.get("free_bytes")
                    or d.get("free")
                    or d.get("available")
                    or "?"
                )
                pct = d.get("used_percent") or d.get("usage_percent") or d.get("percent") or "?"
                drive = (
                    d.get("drive")
                    or d.get("drive_letter")
                    or d.get("mount")
                    or d.get("device")
                    or "?"
                )
                if isinstance(total, (int, float)) and isinstance(free, (int, float)):
                    if isinstance(total, int) and total > 1024**3:
                        total = f"{total / 1024**3:.1f}GB"
                        free = f"{free / 1024**3:.1f}GB"
                    parts.append(f"Drive {drive}: {free}/{total} free ({pct}% used)")
                else:
                    parts.append(f"Drive {drive}: free={free} total={total}")
                highlights.append(f"Disk {drive}: {free} free of {total}")

        elif tc.name == "system.network.routes" and tc.result:
            routes_value = tc.result.get("routes", [tc.result])
            routes = [item for item in _as_list(routes_value) if isinstance(item, dict)] or [
                tc.result
            ]
            count = len(routes)
            default_route = None
            interfaces: set[str] = set()
            for r in routes:
                dest = str(r.get("destination") or r.get("network") or r.get("dest") or "")
                gw = str(r.get("gateway") or r.get("nexthop") or r.get("next_hop") or "")
                iface = str(
                    r.get("interface")
                    or r.get("iface")
                    or r.get("interface_ip")
                    or r.get("adapter")
                    or ""
                )
                if dest in ("0.0.0.0", "::", "0.0.0.0/0", "::/0"):
                    default_route = gw or iface or "present"
                if iface:
                    interfaces.add(iface)
            summary_parts = [f"{count} routes"]
            if default_route:
                summary_parts.append(f"default via {default_route}")
            if interfaces:
                summary_parts.append(f"iface: {', '.join(sorted(interfaces)[:3])}")
            parts.append(", ".join(summary_parts))
            highlights.append(f"Network: {', '.join(summary_parts)}")

        elif tc.name == "system.info" and tc.result:
            hostname = tc.result.get("hostname", "?")
            os_name = tc.result.get("os", "?")
            uptime = tc.result.get("uptime_sec", 0)
            parts.append(f"Host: {hostname}, OS: {os_name}, Uptime: {uptime}s")
            highlights.append(f"System: {hostname} ({os_name})")

        else:
            parts.append(f"{tc.name}: completed")

    for tc in failed:
        err = tc.error.get("message", "unknown error") if tc.error else "unknown error"
        highlights.append(f"FAILED: {tc.name} - {err}")

    message = "; ".join(parts) if parts else provider_message or "No tool calls executed."
    summary = "; ".join(parts) if parts else ""

    return AgentInvokeOutput(
        message=message,
        summary=summary,
        highlights=highlights,
        tool_results=tool_results,
        data=tool_results,  # put structured results in data too
    )


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

    from yequ.db import async_session_factory
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
        async with async_session_factory() as _db:
            await _add_event(_db)
            await _db.commit()
