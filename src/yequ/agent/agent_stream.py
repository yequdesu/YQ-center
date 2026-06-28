"""Agent Stream --SSE async generators for /agent/invoke/stream and /agent/plan/stream.

Each generator yields dicts with keys: event_id, event_type, session_id, trace_id, timestamp, data.
The caller (FastAPI route) formats these as SSE text/event-stream.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.agent_service import _write_timeline
from yequ.agent.provider import AgentFunction, AgentProvider
from yequ.application import (
    ExecuteToolCommand,
    MaintenancePlanApplicationService,
    ToolInvocationApplicationService,
    ToolPreflightApplicationService,
    ToolPreflightCommand,
)
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


def _provider_system_prompt(provider: AgentProvider, functions: list[AgentFunction]) -> str:
    prompt_builder = getattr(provider, "debug_system_prompt", None)
    if not callable(prompt_builder):
        return ""
    value = prompt_builder(functions)
    return value if isinstance(value, str) else str(value)


def _prompt_context_event_data(
    provider: AgentProvider,
    *,
    available_functions: list[AgentFunction],
    target_node_id: str | None,
    execution_mode: str,
) -> dict[str, object]:
    return {
        "provider_name": provider.provider_name(),
        "system_prompt": _provider_system_prompt(provider, available_functions),
        "target_node_id": target_node_id,
        "execution_mode": execution_mode,
        "available_functions": [_function_debug_summary(f) for f in available_functions],
    }


async def agent_invoke_stream(
    provider: AgentProvider,
    *,
    session_id: str,
    prompt: str,
    target_node_id: str | None = None,
    available_functions: list[AgentFunction],
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
        ),
    )

    # Constraint checks
    elapsed = (now - started_at).total_seconds()
    if elapsed > max_total_duration_sec:
        yield _event(
            "agent.failed",
            session_id,
            trace_id,
            {
                "error_code": "max_duration_exceeded",
                "message": f"Duration {elapsed:.1f}s exceeds max",
            },
        )
        _mark_stream_inactive(session_id)
        yield _event("stream.close", session_id, trace_id)
        return
    if step_count >= max_steps:
        yield _event(
            "agent.failed",
            session_id,
            trace_id,
            {"error_code": "max_steps_exceeded", "message": f"Max steps {max_steps} exceeded"},
        )
        _mark_stream_inactive(session_id)
        yield _event("stream.close", session_id, trace_id)
        return
    if len(call_path) >= max_depth:
        yield _event(
            "agent.failed",
            session_id,
            trace_id,
            {
                "error_code": "call_depth_exceeded",
                "message": f"Depth {len(call_path)} exceeds max {max_depth}",
            },
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

    current_step = step_count
    final_message = ""
    loop_state = "running"
    all_tool_results: list[dict[str, object]] = []

    try:
        while current_step < max_steps:
            now = datetime.now(UTC)
            if (now - started_at).total_seconds() > max_total_duration_sec:
                loop_state = "timeout"
                break

            current_step += 1
            yield _event(
                "agent.loop.iteration",
                session_id,
                trace_id,
                {"iteration": current_step, "max_steps": max_steps},
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
                loop_state = "provider_failed"
                yield _event(
                    "agent.provider.failed",
                    session_id,
                    trace_id,
                    {"error_code": "llm_error", "message": provider_error},
                )
                break

            executable_calls = list(provider_tool_calls)

            if not executable_calls:
                if not assistant_text:
                    loop_state = "protocol_error"
                    yield _event(
                        "agent.failed",
                        session_id,
                        trace_id,
                        {
                            "error_code": "agent_protocol_error",
                            "message": "Provider returned neither assistant text nor tool calls.",
                        },
                    )
                    break
                final_message = assistant_text
                loop_state = "completed"
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

            ordered_results: list[dict[str, object]] = []
            # Use a short-lived session for the preflight + execution block so
            # the DB connection is released before the next LLM round-trip.
            async with async_session_factory() as exec_block_db:
                async for ev in _execute_tool_calls_scheduled(
                    exec_block_db,
                    provider,
                    session_actor_id,
                    session_id,
                    trace_id,
                    executable_calls,
                    known_functions,
                    available_functions,
                    call_path,
                    execution_mode,
                    max_depth,
                    max_total_duration_sec,
                    started_at,
                    target_node_id,
                ):
                    yield ev
                    # Collect results from completed/failed/waiting_approval events
                    if ev["event_type"] in (
                        "agent.tool_call.completed",
                        "agent.tool_call.failed",
                        "agent.tool_call.waiting_approval",
                    ):
                        data = _as_object_dict(ev.get("data", {}))
                        call_id = str(data.get("call_id", ""))
                        name = str(data.get("name", ""))
                        if ev["event_type"] == "agent.tool_call.completed":
                            ordered_results.append(
                                {
                                    "name": name,
                                    "call_id": call_id,
                                    "status": "succeeded",
                                    "result": data.get("result"),
                                    "target_node_id": data.get("target_node_id"),
                                }
                            )
                        elif ev["event_type"] == "agent.tool_call.failed":
                            ordered_results.append(
                                {
                                    "name": name,
                                    "call_id": call_id,
                                    "status": "failed",
                                    "error": data.get("message"),
                                    "error_code": data.get("error_code"),
                                    "error_details": data.get("details"),
                                    "target_node_id": data.get("target_node_id"),
                                }
                            )
                        elif ev["event_type"] == "agent.tool_call.waiting_approval":
                            ordered_results.append(
                                {
                                    "name": name,
                                    "call_id": call_id,
                                    "status": "waiting_approval",
                                    "approval_id": data.get("approval_id"),
                                    "target_node_id": data.get("target_node_id"),
                                }
                            )
                            loop_state = "waiting_approval"

            # Sort results by original provider call order
            ordered_results.sort(key=lambda r: provider_call_order.get(str(r["call_id"]), 999))

            for tc_result in ordered_results:
                all_tool_results.append(tc_result)
                history.append(
                    AgentMessage(
                        role="tool",
                        tool_call_id=str(tc_result["call_id"]),
                        content=_json.dumps(tc_result),
                    )
                )
                if tc_result.get("status") == "waiting_approval":
                    loop_state = "waiting_approval"

            yield _event(
                "agent.observing", session_id, trace_id, {"tool_count": len(executable_calls)}
            )
            if loop_state == "waiting_approval":
                break
        if not final_message:
            if loop_state == "waiting_approval":
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
            loop_state = "protocol_error"
            yield _event(
                "agent.failed",
                session_id,
                trace_id,
                {
                    "error_code": "agent_protocol_error",
                    "message": "Agent loop ended without a provider final answer.",
                    "loop_state": loop_state,
                },
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


async def _stream_tool_calls(
    db: AsyncSession,
    provider: AgentProvider,
    actor_id: str,
    session_id: str,
    trace_id: str,
    tool_calls: list[dict[str, object]],
    known_functions: set[str],
    available_functions: list[AgentFunction],
    call_path: list[str],
    execution_mode: str,
    max_depth: int,
    max_total_duration_sec: int,
    started_at: datetime,
    target_node_id: str | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Process tool calls and emit events. Sub-generator consumed by the main stream."""
    for raw_tc in tool_calls:
        tc_name = str(raw_tc.get("name", ""))

        # Unknown function
        if tc_name not in known_functions:
            yield _event(
                "agent.tool_call.created",
                session_id,
                trace_id,
                {
                    "call_id": raw_tc.get("call_id", ""),
                    "name": tc_name,
                    "status": "failed",
                    "target_node_id": target_node_id,
                },
            )
            yield _event(
                "agent.tool_call.failed",
                session_id,
                trace_id,
                {
                    "call_id": raw_tc.get("call_id", ""),
                    "name": tc_name,
                    "error_code": "function_not_available",
                    "message": f"Function {tc_name!r} is not available",
                    "target_node_id": target_node_id,
                },
            )
            continue

        # Loop detection
        if tc_name in call_path:
            yield _event(
                "agent.tool_call.created",
                session_id,
                trace_id,
                {
                    "call_id": raw_tc.get("call_id", ""),
                    "name": tc_name,
                    "status": "failed",
                    "target_node_id": target_node_id,
                },
            )
            yield _event(
                "agent.tool_call.failed",
                session_id,
                trace_id,
                {
                    "call_id": raw_tc.get("call_id", ""),
                    "name": tc_name,
                    "error_code": "circular_dependency",
                    "message": f"Circular: {tc_name!r} in call_path",
                    "target_node_id": target_node_id,
                },
            )
            continue

        call_id = str(raw_tc.get("call_id", _make_event_id()))

        yield _event(
            "agent.tool_call.created",
            session_id,
            trace_id,
            {
                "call_id": call_id,
                "name": tc_name,
                "sanitized_name": str(raw_tc.get("sanitized_name", tc_name)),
                "input": raw_tc.get("input", {}),
                "target_node_id": target_node_id,
            },
        )

        yield _event(
            "agent.tool_call.arguments",
            session_id,
            trace_id,
            {
                "call_id": call_id,
                "name": tc_name,
                "input": raw_tc.get("input", {}),
                "target_node_id": target_node_id,
            },
        )

        # Execute and stream progress
        async for ev in _execute_and_stream(
            db,
            provider,
            actor_id,
            session_id,
            trace_id,
            call_id,
            tc_name,
            _as_object_dict(raw_tc.get("input", {})),
            known_functions,
            available_functions,
            call_path,
            execution_mode,
            max_depth,
            max_total_duration_sec,
            started_at,
            None,
        ):
            yield ev


async def _execute_and_stream(
    db: AsyncSession,
    provider: AgentProvider,
    actor_id: str,
    session_id: str,
    trace_id: str,
    call_id: str,
    tc_name: str,
    tc_input: dict[str, object],
    known_functions: set[str],
    available_functions: list[AgentFunction],
    call_path: list[str],
    execution_mode: str,
    max_depth: int,
    max_total_duration_sec: int,
    started_at: datetime,
    target_node_id: str | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Execute a single tool call and stream its lifecycle events."""
    func_meta = next((f for f in available_functions if f.name == tc_name), None)
    result = await ToolInvocationApplicationService(db).execute(
        ExecuteToolCommand(
            actor_type="agent",
            actor_id=actor_id,
            session_id=session_id,
            function_name=tc_name,
            input_data=tc_input,
            target_node_id=target_node_id,
            execution_mode=execution_mode,
            max_depth=max_depth,
            call_path=list(call_path) + [tc_name],
            wait_for_result=False,
            declared_risk=func_meta.risk if func_meta else None,
            declared_effect=func_meta.effect if func_meta else None,
        )
    )

    if result.status in {"unavailable", "not_found"}:
        yield _event(
            "agent.tool_call.failed",
            session_id,
            trace_id,
            {
                "call_id": call_id,
                "name": tc_name,
                "error_code": result.error_code or "function_not_available",
                "message": result.error_message or f"No online node has {tc_name!r}",
                "target_node_id": result.target_node_id,
            },
        )
        return

    if result.status == "denied":
        yield _event(
            "agent.tool_call.failed",
            session_id,
            trace_id,
            {
                "call_id": call_id,
                "name": tc_name,
                "error_code": result.error_code or "policy_denied",
                "message": result.error_message or "Policy denied",
                "target_node_id": result.target_node_id,
            },
        )
        return

    if result.status == "approval_required":
        yield _event(
            "agent.approval.required",
            session_id,
            trace_id,
            {
                "call_id": call_id,
                "name": tc_name,
                "approval_id": result.approval_id,
                "target_node_id": result.target_node_id,
            },
        )
        yield _event(
            "agent.tool_call.waiting_approval",
            session_id,
            trace_id,
            {
                "call_id": call_id,
                "name": tc_name,
                "approval_id": result.approval_id,
                "status": "waiting_approval",
                "message": result.error_message or "Write operation requires approval",
                "target_node_id": result.target_node_id,
            },
        )
        return

    if not result.invocation_id or not result.job_id:
        yield _event(
            "agent.tool_call.failed",
            session_id,
            trace_id,
            {
                "call_id": call_id,
                "name": tc_name,
                "error_code": result.error_code or "tool_failed",
                "message": result.error_message or "Tool execution did not create a job",
                "target_node_id": result.target_node_id,
            },
        )
        return

    invocation_id = result.invocation_id
    job_id = result.job_id

    yield _event(
        "agent.invocation.created",
        session_id,
        trace_id,
        {
            "call_id": call_id,
            "name": tc_name,
            "invocation_id": invocation_id,
            "target_node_id": result.target_node_id,
        },
    )

    yield _event(
        "agent.job.queued",
        session_id,
        trace_id,
        {
            "call_id": call_id,
            "name": tc_name,
            "invocation_id": invocation_id,
            "job_id": job_id,
            "target_node_id": result.target_node_id,
        },
    )

    # Poll for job completion
    deadline = datetime.fromtimestamp(
        (started_at or datetime.now(UTC)).timestamp() + max_total_duration_sec,
        tz=UTC,
    )

    from yequ.db import async_session_factory
    from yequ.models.invocation import Invocation
    from yequ.models.job import Job as JobModel

    poll_count = 0
    final_status = "running"
    while datetime.now(UTC) < deadline:
        async with async_session_factory() as poll_db:
            j_result = await poll_db.execute(select(JobModel).where(JobModel.job_id == job_id))
            j = j_result.scalar_one_or_none()
            if j:
                if poll_count == 0 and j.status == "running":
                    yield _event(
                        "agent.job.running",
                        session_id,
                        trace_id,
                        {
                            "call_id": call_id,
                            "name": tc_name,
                            "job_id": job_id,
                            "status": "running",
                            "target_node_id": result.target_node_id,
                        },
                    )
                if j.status in ("succeeded", "failed", "timeout", "cancelled"):
                    final_status = j.status
                    break
        poll_count += 1
        await asyncio.sleep(0.5)

    # Collect result --read both Invocation and Job to preserve error details
    async with async_session_factory() as result_db:
        inv_result = await result_db.execute(
            select(Invocation).where(Invocation.invocation_id == invocation_id)
        )
        inv_final = inv_result.scalar_one_or_none()

        job_result = await result_db.execute(select(JobModel).where(JobModel.job_id == job_id))
        job_final = job_result.scalar_one_or_none()

        yield _event(
            "agent.job.finished",
            session_id,
            trace_id,
            {
                "call_id": call_id,
                "name": tc_name,
                "job_id": job_id,
                "status": final_status,
                "target_node_id": result.target_node_id,
            },
        )

        if final_status == "succeeded":
            yield _event(
                "agent.tool_call.completed",
                session_id,
                trace_id,
                {
                    "call_id": call_id,
                    "name": tc_name,
                    "result": inv_final.result if inv_final else {},
                    "target_node_id": result.target_node_id,
                },
            )
        else:
            # Preserve the original error from the Job/Invocation --never
            # overwrite with a generic "tool_failed".
            error_code = (
                (job_final.error_code if job_final else None)
                or (inv_final.error_code if inv_final else None)
                or "tool_failed"
            )
            error_message = (
                (job_final.error_message if job_final else None)
                or (inv_final.error_message if inv_final else None)
                or f"Tool {tc_name} ended with {final_status}"
            )
            error_details = job_final.error_details if job_final else None
            yield _event(
                "agent.tool_call.failed",
                session_id,
                trace_id,
                {
                    "call_id": call_id,
                    "name": tc_name,
                    "status": final_status,
                    "error_code": error_code,
                    "message": error_message,
                    "details": error_details,
                    "target_node_id": result.target_node_id,
                },
            )


# -- Concurrency scheduling for tool calls --

CONCURRENCY_MAX = 4  # Configurable later


async def _execute_tool_calls_scheduled(
    db: AsyncSession,
    provider: AgentProvider,
    actor_id: str,
    session_id: str,
    trace_id: str,
    tool_calls: list[dict[str, object]],
    known_functions: set[str],
    available_functions: list[AgentFunction],
    call_path: list[str],
    execution_mode: str,
    max_depth: int,
    max_total_duration_sec: int,
    started_at: datetime,
    target_node_id: str | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Async generator that executes tool calls with concurrency scheduling.

    Yields SSE events in real-time. All created events are emitted first,
    then concurrent safe/read tools execute in parallel (max 4),
    then serial tools execute one by one.
    """
    from yequ.db import async_session_factory

    # ?? Phase 1: Pre-flight validation + emit all created events ??
    classified: list[dict[str, object]] = []
    preflight_service = ToolPreflightApplicationService(db)

    for raw_tc in tool_calls:
        tc_name = str(raw_tc.get("name", ""))
        tc_call_id = str(raw_tc.get("call_id", _make_event_id()))
        tc_input = raw_tc.get("input", {})

        # Unknown function check
        if tc_name not in known_functions:
            yield _event(
                "agent.tool_call.created",
                session_id,
                trace_id,
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "target_node_id": target_node_id,
                },
            )
            yield _event(
                "agent.tool_call.failed",
                session_id,
                trace_id,
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "error_code": "function_not_available",
                    "message": f"Function {tc_name!r} is not available",
                    "target_node_id": target_node_id,
                },
            )
            classified.append(
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "input": tc_input,
                    "status": "failed",
                    "error": "function_not_available",
                }
            )
            continue

        # Loop detection
        if tc_name in call_path:
            yield _event(
                "agent.tool_call.created",
                session_id,
                trace_id,
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "target_node_id": target_node_id,
                },
            )
            yield _event(
                "agent.tool_call.failed",
                session_id,
                trace_id,
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "error_code": "circular_dependency",
                    "message": f"Circular: {tc_name!r} in call_path",
                    "target_node_id": target_node_id,
                },
            )
            classified.append(
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "input": tc_input,
                    "status": "failed",
                    "error": "circular_dependency",
                }
            )
            continue

        # Get function metadata
        func_meta = next((f for f in available_functions if f.name == tc_name), None)
        risk = func_meta.risk if func_meta else "safe"
        effect = func_meta.effect if func_meta else "read"
        preflight = await preflight_service.check(
            ToolPreflightCommand(
                function_name=tc_name,
                execution_mode=execution_mode,
                target_node_id=target_node_id,
                declared_risk=risk,
                declared_effect=effect,
            )
        )

        # Emit created + arguments events
        yield _event(
            "agent.tool_call.created",
            session_id,
            trace_id,
            {
                "call_id": tc_call_id,
                "name": tc_name,
                "sanitized_name": str(raw_tc.get("sanitized_name", tc_name)),
                "input": tc_input,
                "target_node_id": preflight.target_node_id,
            },
        )
        yield _event(
            "agent.tool_call.arguments",
            session_id,
            trace_id,
            {
                "call_id": tc_call_id,
                "name": tc_name,
                "input": tc_input,
                "target_node_id": preflight.target_node_id,
            },
        )

        if preflight.status == "unavailable":
            yield _event(
                "agent.tool_call.failed",
                session_id,
                trace_id,
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "error_code": preflight.error_code or "function_not_available",
                    "message": preflight.error_message or f"No online node has {tc_name!r}",
                    "target_node_id": preflight.target_node_id,
                },
            )
            classified.append(
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "input": tc_input,
                    "status": "failed",
                    "error": "no_node",
                }
            )
            continue

        if preflight.status == "denied":
            yield _event(
                "agent.tool_call.failed",
                session_id,
                trace_id,
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "error_code": preflight.error_code or "policy_denied",
                    "message": preflight.error_message or "Policy denied",
                    "target_node_id": preflight.target_node_id,
                },
            )
            classified.append(
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "input": tc_input,
                    "status": "failed",
                    "error": "policy_denied",
                }
            )
            continue

        resource_keys = preflight.resource_keys
        is_concurrent_safe = preflight.is_concurrent_safe

        classified.append(
            {
                "call_id": tc_call_id,
                "name": tc_name,
                "input": tc_input,
                "status": "pending",
                "func_meta": func_meta,
                "is_concurrent_safe": is_concurrent_safe,
                "resource_keys": resource_keys,
            }
        )

    # -- Phase 2: Split into concurrent vs serial groups --
    await db.rollback()

    concurrent_candidates = [
        t for t in classified if t.get("is_concurrent_safe") and t["status"] == "pending"
    ]
    serial_tools = [
        t for t in classified if not t.get("is_concurrent_safe") and t["status"] == "pending"
    ]

    # Move tools with shared resource_keys from concurrent to serial
    resource_key_owners: dict[str, str] = {}
    actual_concurrent: list[dict[str, object]] = []
    for t in concurrent_candidates:
        keys = _as_str_list(t.get("resource_keys", []))
        conflicts = [k for k in keys if k in resource_key_owners]
        if conflicts:
            serial_tools.append(t)
        else:
            for k in keys:
                resource_key_owners[k] = str(t["call_id"])
            actual_concurrent.append(t)

    # -- Phase 3: Execute --

    # Execute concurrent tools with semaphore (max CONCURRENCY_MAX)
    if actual_concurrent:
        semaphore = asyncio.Semaphore(CONCURRENCY_MAX)

        async def _execute_concurrent(tool_info: dict[str, object]) -> list[StreamEvent]:
            """Execute a single tool call using a fresh DB session."""
            async with semaphore:
                collected_events: list[StreamEvent] = []
                try:
                    async with async_session_factory() as exec_db:
                        async for ev in _execute_and_stream(
                            exec_db,
                            provider,
                            actor_id,
                            session_id,
                            trace_id,
                            str(tool_info["call_id"]),
                            str(tool_info["name"]),
                            _as_object_dict(tool_info["input"]),
                            known_functions,
                            available_functions,
                            call_path,
                            execution_mode,
                            max_depth,
                            max_total_duration_sec,
                            started_at,
                            target_node_id=target_node_id,
                        ):
                            collected_events.append(ev)
                except Exception as e:
                    log.exception(
                        "concurrent tool execution failed: call_id=%s", tool_info["call_id"]
                    )
                    collected_events.append(
                        _event(
                            "agent.tool_call.failed",
                            session_id,
                            trace_id,
                            {
                                "call_id": tool_info["call_id"],
                                "name": tool_info["name"],
                                "error_code": "internal_error",
                                "message": str(e)[:500],
                                "target_node_id": target_node_id,
                            },
                        )
                    )
                return collected_events

        tasks = [asyncio.create_task(_execute_concurrent(t)) for t in actual_concurrent]
        # Yield events as tasks complete (interleaving is fine --each event has call_id)
        for completed in asyncio.as_completed(tasks):
            tool_events = await completed
            for ev in tool_events:
                yield ev

    # Execute serial tools one by one
    for tool_info in serial_tools:
        try:
            async with async_session_factory() as serial_db:
                async for ev in _execute_and_stream(
                    serial_db,
                    provider,
                    actor_id,
                    session_id,
                    trace_id,
                    str(tool_info["call_id"]),
                    str(tool_info["name"]),
                    _as_object_dict(tool_info["input"]),
                    known_functions,
                    available_functions,
                    call_path,
                    execution_mode,
                    max_depth,
                    max_total_duration_sec,
                    started_at,
                    target_node_id=target_node_id,
                ):
                    yield ev
        except Exception as e:
            log.exception("serial tool execution failed: call_id=%s", tool_info["call_id"])
            yield _event(
                "agent.tool_call.failed",
                session_id,
                trace_id,
                {
                    "call_id": tool_info["call_id"],
                    "name": tool_info["name"],
                    "error_code": "internal_error",
                    "message": str(e)[:500],
                    "target_node_id": target_node_id,
                },
            )


async def agent_plan_stream(
    provider: AgentProvider,
    *,
    session_id: str,
    prompt: str,
    target_node_id: str,
    available_functions: list[AgentFunction],
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


