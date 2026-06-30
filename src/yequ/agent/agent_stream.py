"""Agent Stream --SSE async generators for /agent/invoke/stream and /agent/plan/stream.

Each generator yields dicts with keys: event_id, event_type, session_id, trace_id, timestamp, data.
The caller (FastAPI route) formats these as SSE text/event-stream.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from sqlalchemy import select

from yequ.agent.agent_service import _write_timeline
from yequ.agent.context_engine import JsonDict
from yequ.agent.provider import AgentFunction, AgentProvider
from yequ.agent.runtime_state import (
    AgentRuntimeController,
    AgentRuntimeFailure,
    AgentRuntimeLimits,
    AgentToolObservationCollector,
)
from yequ.agent.tool_stream import execute_tool_calls_scheduled
from yequ.application import MaintenancePlanApplicationService
from yequ.logconfig import get_logger

log = get_logger(__name__)

StreamEvent = dict[str, object]


def _as_str(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _as_object_dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _as_object_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


# -- Active stream tracking --
_active_stream_sessions: set[str] = set()


def is_session_running(session_id: str) -> bool:
    """Check if a session currently has an active SSE stream."""
    return session_id in _active_stream_sessions


def _mark_stream_active(session_id: str) -> None:
    _active_stream_sessions.add(session_id)


def _mark_stream_inactive(session_id: str) -> None:
    _active_stream_sessions.discard(session_id)


def _make_trace_id() -> str:
    return f"tr_{uuid.uuid4().hex[:16]}"


def _make_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:16]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _event(
    event_type: str,
    session_id: str,
    trace_id: str,
    data: dict[str, object] | None = None,
) -> StreamEvent:
    return {
        "event_id": _make_event_id(),
        "event_type": event_type,
        "session_id": session_id,
        "trace_id": trace_id,
        "timestamp": _now_iso(),
        "data": data or {},
    }


def _function_debug_summary(func: AgentFunction) -> dict[str, object]:
    return {
        "name": func.name,
        "description": func.description,
        "risk": func.risk,
        "effect": func.effect,
        "timeout_sec": func.timeout_sec,
        "source_nodes": list(func.source_nodes),
        "input_schema": func.input_schema or {},
        "output_schema": func.output_schema or {},
    }


def _provider_system_prompt(
    provider: AgentProvider,
    functions: list[AgentFunction],
    capability_context: JsonDict | None = None,
) -> str:
    prompt_builder = getattr(provider, "debug_system_prompt", None)
    if not callable(prompt_builder):
        return ""
    try:
        value = prompt_builder(
            functions,
            context={"capability_context": capability_context} if capability_context else None,
        )
    except TypeError:
        value = prompt_builder(functions)
    return value if isinstance(value, str) else str(value)


def _prompt_context_event_data(
    provider: AgentProvider,
    *,
    available_functions: list[AgentFunction],
    target_node_id: str | None,
    execution_mode: str,
    capability_context: JsonDict | None = None,
) -> dict[str, object]:
    return {
        "provider_name": provider.provider_name(),
        "system_prompt": _provider_system_prompt(
            provider, available_functions, capability_context
        ),
        "target_node_id": target_node_id,
        "execution_mode": execution_mode,
        "routing_mode": (
            str(capability_context.get("routing_mode"))
            if capability_context
            else ("pinned" if target_node_id else "auto")
        ),
        "capability_context": capability_context or {},
        "nodes": capability_context.get("nodes", []) if capability_context else [],
        "tool_count_by_node": (
            capability_context.get("tool_count_by_node", {}) if capability_context else {}
        ),
        "available_functions": [_function_debug_summary(f) for f in available_functions],
    }


async def agent_invoke_stream(
    provider: AgentProvider,
    *,
    session_id: str,
    prompt: str,
    target_node_id: str | None = None,
    available_functions: list[AgentFunction],
    capability_context: JsonDict | None = None,
    suppress_user_message: bool = False,
    call_path: list[str] | None = None,
    max_depth: int = 5,
    max_steps: int = 20,
    max_total_duration_sec: int = 300,
    step_count: int = 0,
    started_at: datetime | None = None,
    execution_mode: str = "auto",
) -> AsyncGenerator[StreamEvent, None]:
    """Async generator yielding SSE event dicts for agent invoke with ReAct loop.

    DB sessions are created internally and held only for the duration of each
    logical operation block --never across long async waits (LLM calls, job
    polling).  This keeps PostgreSQL connections from accumulating as idle-
    in-transaction when the client disconnects or uvicorn reloads.
    """
    from yequ.agent.agent_service import (
        _load_session_history,
        _save_session_history,
    )
    from yequ.agent.provider import AgentMessage
    from yequ.db import async_session_factory
    from yequ.models.session import Session

    call_path = call_path or []
    now = datetime.now(UTC)
    if started_at is None:
        started_at = now
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
    trace_id = _make_trace_id()

    yield _event("stream.open", session_id, trace_id)
    _mark_stream_active(session_id)
    yield _event(
        "agent.prompt_context",
        session_id,
        trace_id,
        _prompt_context_event_data(
            provider,
            available_functions=available_functions,
            target_node_id=target_node_id,
            execution_mode=execution_mode,
            capability_context=capability_context,
        ),
    )

    initial_failure = runtime.check_initial_constraints(now)
    if initial_failure:
        yield _event(
            "agent.failed",
            session_id,
            trace_id,
            initial_failure.as_event_data(),
        )
        _mark_stream_inactive(session_id)
        yield _event("stream.close", session_id, trace_id)
        return

    # -- Block 1: session validation + timeline (short-lived session) --
    async with async_session_factory() as db:
        result = await db.execute(select(Session).where(Session.session_id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            yield _event(
                "agent.failed",
                session_id,
                trace_id,
                {"error_code": "session_not_found", "message": "Session not found"},
            )
            _mark_stream_inactive(session_id)
            yield _event("stream.close", session_id, trace_id)
            return

        session_actor_id = session.actor_id
        session_status = session.status
        session_execution_mode = session.execution_mode

        await _write_timeline(
            db,
            "agent.prompt.received",
            session_id=session_id,
            actor=provider.provider_name(),
            prompt=prompt,
            step=step_count + 1,
        )
        await db.commit()

    yield _event(
        "agent.session.resolved",
        session_id,
        trace_id,
        {"session_status": session_status, "execution_mode": session_execution_mode},
    )
    yield _event(
        "agent.prompt.received",
        session_id,
        trace_id,
        {"prompt": prompt[:500], "step": step_count + 1, "internal": suppress_user_message},
    )

    known_functions = {f.name for f in available_functions}

    # -- Block 2: load history + save user message (short-lived session) --
    async with async_session_factory() as db:
        history = await _load_session_history(db, session_id)
        user_message = AgentMessage(role="user", content=prompt)
        history.append(user_message)
        if not suppress_user_message:
            await _save_session_history(db, session_id, [user_message])
            await db.commit()

    yield _event(
        "agent.loop.started",
        session_id,
        trace_id,
        {"max_steps": max_steps, "max_duration_sec": max_total_duration_sec},
    )

    final_message = ""
    loop_state = "running"
    all_tool_results: list[dict[str, object]] = []

    try:
        while True:
            iteration = runtime.begin_iteration(datetime.now(UTC))
            if isinstance(iteration, AgentRuntimeFailure):
                yield _event(
                    "agent.failed",
                    session_id,
                    trace_id,
                    iteration.as_event_data(),
                )
                _mark_stream_inactive(session_id)
                yield _event("stream.close", session_id, trace_id)
                return
            current_step = iteration.iteration
            yield _event(
                "agent.loop.iteration",
                session_id,
                trace_id,
                iteration.as_event_data(),
            )
            yield _event(
                "agent.provider.started",
                session_id,
                trace_id,
                {"provider_name": provider.provider_name()},
            )

            # Call provider with streaming --text deltas are yielded in real-time
            assistant_text = ""
            provider_tool_calls: list[dict[str, object]] = []
            provider_error: str | None = None
            try:
                async for chunk in provider.invoke_stream(
                    "",
                    available_functions=available_functions,
                    messages=history,
                    context={
                        "call_path": list(call_path),
                        "session_id": session_id,
                        "step": current_step,
                        "capability_context": capability_context or {},
                    },
                ):
                    chunk_type = chunk.get("type")
                    if chunk_type == "delta":
                        content = _as_str(chunk.get("content", ""))
                        assistant_text += content
                        yield _event(
                            "agent.output.delta",
                            session_id,
                            trace_id,
                            {"content": content},
                        )
                    elif chunk_type == "done":
                        provider_tool_calls = _as_object_dict_list(chunk.get("tool_calls", []))
                    elif chunk_type == "error":
                        provider_error = _as_str(
                            chunk.get("message", chunk.get("error_message", "Provider error"))
                        )
            except TimeoutError:
                provider_error = "provider_timeout"
            except Exception as e:
                log.exception("provider stream error: session_id=%s", session_id)
                provider_error = str(e)[:500]

            if provider_error:
                failure = runtime.provider_failed(provider_error)
                loop_state = failure.status
                yield _event(
                    "agent.provider.failed",
                    session_id,
                    trace_id,
                    failure.as_event_data(),
                )
                break

            executable_calls = list(provider_tool_calls)
            provider_decision = runtime.decide_provider_output(
                assistant_text=assistant_text,
                tool_calls=executable_calls,
            )

            if provider_decision.kind == "failure":
                if provider_decision.failure:
                    yield _event(
                        "agent.failed",
                        session_id,
                        trace_id,
                        provider_decision.failure.as_event_data(),
                    )
                break
            if provider_decision.kind == "final":
                final_message = provider_decision.final_message
                loop_state = provider_decision.status
                yield _event("agent.synthesizing", session_id, trace_id, {"source": "llm"})
                break

            # Append assistant message with tool_calls
            history.append(
                AgentMessage(
                    role="assistant", content=assistant_text, tool_calls=provider_tool_calls
                )
            )

            # Record original provider call order for history ordering
            provider_call_order: dict[str, int] = {}
            for idx, raw_tc in enumerate(provider_tool_calls):
                call_id = str(raw_tc.get("call_id", ""))
                provider_call_order[call_id] = idx

            # Execute tool calls with concurrency scheduling
            import json as _json

            observation_collector = AgentToolObservationCollector(provider_call_order)
            # Use a short-lived session for the preflight + execution block so
            # the DB connection is released before the next LLM round-trip.
            async with async_session_factory() as exec_block_db:
                async for ev in execute_tool_calls_scheduled(
                    exec_block_db,
                    make_event=lambda event_type, data=None: _event(
                        event_type, session_id, trace_id, data
                    ),
                    actor_id=session_actor_id,
                    session_id=session_id,
                    tool_calls=executable_calls,
                    known_functions=known_functions,
                    available_functions=available_functions,
                    call_path=call_path,
                    execution_mode=execution_mode,
                    max_depth=max_depth,
                    max_total_duration_sec=max_total_duration_sec,
                    started_at=started_at,
                    target_node_id=target_node_id,
                ):
                    yield ev
                    # Collect results from completed/failed/waiting_approval events
                    if (
                        observation_collector.record_event(
                            str(ev["event_type"]),
                            _as_object_dict(ev.get("data", {})),
                        )
                        and observation_collector.has_waiting_approval
                    ):
                        loop_state = runtime.waiting_approval().status
                    if observation_collector.has_waiting_operation:
                        loop_state = runtime.waiting_operation().status

            for tc_result in observation_collector.ordered_results():
                all_tool_results.append(tc_result)
                history.append(
                    AgentMessage(
                        role="tool",
                        tool_call_id=str(tc_result["call_id"]),
                        content=_json.dumps(tc_result),
                    )
                )
                if tc_result.get("status") == "waiting_approval":
                    loop_state = runtime.waiting_approval().status
                if tc_result.get("status") == "waiting_operation":
                    loop_state = runtime.waiting_operation().status

            yield _event(
                "agent.observing", session_id, trace_id, {"tool_count": len(executable_calls)}
            )
            if loop_state in {"waiting_approval", "waiting_operation"}:
                break
        if not final_message:
            if loop_state in {"waiting_approval", "waiting_operation"}:
                history_to_persist = (
                    [message for message in history if message is not user_message]
                    if suppress_user_message
                    else history
                )
                async with async_session_factory() as waiting_db:
                    await _save_session_history(waiting_db, session_id, history_to_persist)
                    await waiting_db.commit()
                _mark_stream_inactive(session_id)
                yield _event("stream.close", session_id, trace_id)
                return
            failure = runtime.missing_final_answer()
            loop_state = failure.status
            data = failure.as_event_data()
            data["loop_state"] = loop_state
            yield _event(
                "agent.failed",
                session_id,
                trace_id,
                data,
            )
            _mark_stream_inactive(session_id)
            yield _event("stream.close", session_id, trace_id)
            return

        # -- Save history (short-lived session) --
        history.append(AgentMessage(role="assistant", content=final_message))
        history_to_persist = (
            [message for message in history if message is not user_message]
            if suppress_user_message
            else history
        )
        async with async_session_factory() as final_db:
            await _save_session_history(final_db, session_id, history_to_persist)
            await _write_timeline(
                final_db,
                "agent.final_response",
                session_id=session_id,
                actor=provider.provider_name(),
                status=loop_state,
            )
            await final_db.commit()
        yield _event(
            "agent.completed",
            session_id,
            trace_id,
            {"status": loop_state, "message": final_message},
        )

    except TimeoutError:
        yield _event(
            "agent.failed",
            session_id,
            trace_id,
            {"error_code": "provider_timeout", "message": "Provider timed out"},
        )
    except Exception as e:
        log.exception("agent stream error: session_id=%s", session_id)
        yield _event(
            "agent.failed",
            session_id,
            trace_id,
            {"error_code": "internal_error", "message": str(e)[:500]},
        )

    _mark_stream_inactive(session_id)
    yield _event("stream.close", session_id, trace_id)


async def agent_plan_stream(
    provider: AgentProvider,
    *,
    session_id: str,
    prompt: str,
    target_node_id: str,
    available_functions: list[AgentFunction],
    capability_context: JsonDict | None = None,
    execution_mode: str = "auto",
    max_total_duration_sec: int = 300,
) -> AsyncGenerator[StreamEvent, None]:
    """Async generator yielding SSE event dicts for agent plan stream."""
    from yequ.db import async_session_factory

    trace_id = _make_trace_id()

    yield _event("stream.open", session_id, trace_id)
    _mark_stream_active(session_id)
    yield _event(
        "agent.prompt_context",
        session_id,
        trace_id,
        _prompt_context_event_data(
            provider,
            available_functions=available_functions,
            target_node_id=target_node_id,
            execution_mode=execution_mode,
            capability_context=capability_context,
        ),
    )

    from yequ.models.session import Session

    # -- Session lookup (short-lived session) --
    async with async_session_factory() as db:
        result = await db.execute(select(Session).where(Session.session_id == session_id))
        session = result.scalar_one_or_none()
        if session is None:
            yield _event(
                "agent.failed",
                session_id,
                trace_id,
                {
                    "error_code": "session_not_found",
                    "message": "Session not found",
                },
            )
            _mark_stream_inactive(session_id)
            yield _event("stream.close", session_id, trace_id)
            return

    yield _event(
        "agent.session.resolved",
        session_id,
        trace_id,
        {
            "session_status": session.status,
        },
    )

    yield _event(
        "agent.prompt.received",
        session_id,
        trace_id,
        {
            "prompt": prompt[:500],
        },
    )

    yield _event(
        "agent.provider.started",
        session_id,
        trace_id,
        {
            "provider_name": provider.provider_name(),
        },
    )

    from yequ.agent.agent_service import (
        _build_rollback_hint,
        _infer_plan_input,
        _infer_plan_seed_calls,
        _input_for_function,
        _select_check_function,
        _select_repair_function,
    )

    classification_prompt = (
        f"User request: {prompt}\n"
        "Classify this request as EXACTLY ONE of:\n"
        "- readonly_check: just check status, no repair needed\n"
        "- check_and_fix: check status AND repair/fix if unhealthy\n"
        "Output ONLY the classification word. No punctuation, no quotes, "
        "no explanation."
    )

    yield _event(
        "agent.planning.summary",
        session_id,
        trace_id,
        {
            "message": "Analyzing request intent...",
        },
    )

    provider_result = await asyncio.wait_for(
        provider.invoke(
            classification_prompt,
            available_functions=[],
            context={"session_id": session_id},
        ),
        timeout=30.0,
    )
    if not provider_result.success:
        yield _event(
            "agent.failed",
            session_id,
            trace_id,
            {
                "error_code": provider_result.error_code or "provider_error",
                "message": provider_result.error_message or "Provider failed to classify intent",
            },
        )
        _mark_stream_inactive(session_id)
        yield _event("stream.close", session_id, trace_id)
        return
    msg = (provider_result.message or "").strip().lower()
    if msg == "readonly_check":
        intent = "readonly_check"
    elif msg == "check_and_fix":
        intent = "check_and_fix"
    else:
        yield _event(
            "agent.failed",
            session_id,
            trace_id,
            {
                "error_code": "agent_protocol_error",
                "message": f"Provider returned invalid maintenance intent: {msg!r}",
            },
        )
        _mark_stream_inactive(session_id)
        yield _event("stream.close", session_id, trace_id)
        return

    seed_calls = await _infer_plan_seed_calls(
        provider,
        prompt=prompt,
        available_functions=available_functions,
        session_id=session_id,
    )
    function_name = _select_check_function(available_functions, seed_calls)
    plan_input = _infer_plan_input(prompt, function_name, available_functions, seed_calls)

    steps_ir: list[dict[str, object]] = []
    if intent == "check_and_fix":
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

    for s in steps_ir:
        yield _event(
            "agent.plan.step.created",
            session_id,
            trace_id,
            {
                "seq": s["seq"],
                "kind": s["kind"],
                "function_name": s["function_name"],
                "requires_approval": s["requires_approval"],
            },
        )

    has_write = any(s["requires_approval"] for s in steps_ir)

    for s in steps_ir:
        func_exists = any(f.name == s["function_name"] for f in available_functions)
        if not func_exists:
            yield _event(
                "agent.failed",
                session_id,
                trace_id,
                {
                    "error_code": "function_not_available",
                    "message": f"Function {s['function_name']!r} not registered on node",
                },
            )
            _mark_stream_inactive(session_id)
            yield _event("stream.close", session_id, trace_id)
            return

    # -- Create plan (short-lived session) --
    async with async_session_factory() as plan_db:
        plan = await MaintenancePlanApplicationService(plan_db).create(
            goal=prompt,
            actor_id=provider.provider_name(),
            target_node_id=target_node_id,
            steps=[
                {
                    "function_name": s["function_name"],
                    "input": s["input"],
                    "kind": s["kind"],
                    "condition": s["condition"],
                    "depends_on": [str(d) for d in _as_str_list(s.get("depends_on", []))],
                    "requires_approval": s["requires_approval"],
                    "risk": s["risk"],
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

        if has_write:
            plan.status = "waiting_approval"
            await plan_db.commit()

    yield _event(
        "agent.plan.created",
        session_id,
        trace_id,
        {
            "plan_id": plan.plan_id,
            "goal": plan.goal,
            "status": plan.status,
            "step_count": len(steps_ir),
        },
    )

    if has_write:
        yield _event(
            "agent.approval.required",
            session_id,
            trace_id,
            {
                "plan_id": plan.plan_id,
                "message": "This plan contains write operations and requires approval",
            },
        )

    yield _event("agent.completed", session_id, trace_id)
    _mark_stream_inactive(session_id)
    yield _event("stream.close", session_id, trace_id)


