"""Agent Stream — SSE async generators for /agent/invoke/stream and /agent/plan/stream.

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
from yequ.logconfig import get_logger

log = get_logger(__name__)

# ── Active stream tracking ──
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
    data: dict | None = None,
) -> dict:
    return {
        "event_id": _make_event_id(),
        "event_type": event_type,
        "session_id": session_id,
        "trace_id": trace_id,
        "timestamp": _now_iso(),
        "data": data or {},
    }


async def agent_invoke_stream(
    db: AsyncSession,
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
) -> AsyncGenerator[dict, None]:
    """Async generator yielding SSE event dicts for agent invoke with ReAct loop."""
    from yequ.agent.agent_service import (
        _fallback_synthesis,
        _load_session_history,
        _save_session_history,
    )
    from yequ.agent.provider import AgentMessage
    from yequ.models.session import Session

    call_path = call_path or []
    now = datetime.now(UTC)
    if started_at is None:
        started_at = now
    trace_id = _make_trace_id()

    yield _event("stream.open", session_id, trace_id)
    _mark_stream_active(session_id)

    # Constraint checks
    elapsed = (now - started_at).total_seconds()
    if elapsed > max_total_duration_sec:
        yield _event("agent.failed", session_id, trace_id, {"error_code": "max_duration_exceeded", "message": f"Duration {elapsed:.1f}s exceeds max"})
        _mark_stream_inactive(session_id)
        yield _event("stream.close", session_id, trace_id)
        return
    if step_count >= max_steps:
        yield _event("agent.failed", session_id, trace_id, {"error_code": "max_steps_exceeded", "message": f"Max steps {max_steps} exceeded"})
        _mark_stream_inactive(session_id)
        yield _event("stream.close", session_id, trace_id)
        return
    if len(call_path) >= max_depth:
        yield _event("agent.failed", session_id, trace_id, {"error_code": "call_depth_exceeded", "message": f"Depth {len(call_path)} exceeds max {max_depth}"})
        _mark_stream_inactive(session_id)
        yield _event("stream.close", session_id, trace_id)
        return

    # Session validation
    result = await db.execute(select(Session).where(Session.session_id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        yield _event("agent.failed", session_id, trace_id, {"error_code": "session_not_found", "message": "Session not found"})
        _mark_stream_inactive(session_id)
        yield _event("stream.close", session_id, trace_id)
        return

    yield _event("agent.session.resolved", session_id, trace_id, {"session_status": session.status, "execution_mode": session.execution_mode})
    yield _event(
        "agent.prompt.received",
        session_id,
        trace_id,
        {"prompt": prompt[:500], "step": step_count + 1, "internal": suppress_user_message},
    )

    await _write_timeline(
        db,
        "agent.prompt.received",
        session_id=session_id,
        actor=provider.provider_name(),
        prompt=prompt,
        step=step_count + 1,
    )
    await db.commit()

    known_functions = {f.name for f in available_functions}

    # -- Load history + append user message --
    history = await _load_session_history(db, session_id)
    user_message = AgentMessage(role="user", content=prompt)
    history.append(user_message)
    if not suppress_user_message:
        await _save_session_history(db, session_id, [user_message])
        await db.commit()

    yield _event("agent.loop.started", session_id, trace_id, {"max_steps": max_steps, "max_duration_sec": max_total_duration_sec})

    current_step = step_count
    final_message = ""
    loop_state = "running"
    all_tool_results: list[dict] = []

    try:
        while current_step < max_steps:
            now = datetime.now(UTC)
            if (now - started_at).total_seconds() > max_total_duration_sec:
                loop_state = "timeout"
                break

            current_step += 1
            yield _event("agent.loop.iteration", session_id, trace_id, {"iteration": current_step, "max_steps": max_steps})
            yield _event("agent.provider.started", session_id, trace_id, {"provider_name": provider.provider_name()})

            # Call provider with history
            provider_result = await asyncio.wait_for(
                provider.invoke("", available_functions=available_functions,
                                messages=history,
                                context={"call_path": list(call_path), "session_id": session_id, "step": current_step}),
                timeout=45.0,
            )

            if not provider_result.success:
                loop_state = "provider_failed"
                yield _event("agent.provider.failed", session_id, trace_id, {"error_code": provider_result.error_code, "message": provider_result.error_message or ""})
                break

            # No tool calls = final answer
            if not provider_result.tool_calls:
                final_message = provider_result.message or ""
                loop_state = "completed"
                if final_message:
                    yield _event("agent.output.delta", session_id, trace_id, {"content": final_message})
                yield _event("agent.synthesizing", session_id, trace_id, {"source": "llm"})
                break

            # Stream assistant text immediately when the provider sends text
            # together with tool calls. Without this, intermediate narration is
            # only persisted in history and appears late after a refresh.
            assistant_text = provider_result.message or ""
            if assistant_text:
                yield _event("agent.output.delta", session_id, trace_id, {"content": assistant_text})

            # Append assistant message with tool_calls
            history.append(AgentMessage(role="assistant", content=assistant_text, tool_calls=provider_result.tool_calls))

            # Record original provider call order for history ordering
            provider_call_order: dict[str, int] = {}
            for idx, raw_tc in enumerate(provider_result.tool_calls):
                call_id = str(raw_tc.get("call_id", ""))
                provider_call_order[call_id] = idx

            # Execute tool calls with concurrency scheduling
            import json as _json

            ordered_results: list[dict] = []
            async for ev in _execute_tool_calls_scheduled(
                db, provider, session, session_id, trace_id,
                provider_result.tool_calls, known_functions, available_functions,
                call_path, execution_mode, max_depth,
                max_total_duration_sec, started_at, target_node_id,
            ):
                yield ev
                # Collect results from completed/failed/waiting_approval events
                if ev["event_type"] in (
                    "agent.tool_call.completed",
                    "agent.tool_call.failed",
                    "agent.tool_call.waiting_approval",
                ):
                    data = ev["data"]
                    call_id = str(data.get("call_id", ""))
                    name = str(data.get("name", ""))
                    if ev["event_type"] == "agent.tool_call.completed":
                        ordered_results.append({
                            "name": name, "call_id": call_id,
                            "status": "succeeded",
                            "result": data.get("result"),
                        })
                    elif ev["event_type"] == "agent.tool_call.failed":
                        ordered_results.append({
                            "name": name, "call_id": call_id,
                            "status": "failed",
                            "error": data.get("message"),
                            "error_code": data.get("error_code"),
                            "error_details": data.get("details"),
                        })
                    elif ev["event_type"] == "agent.tool_call.waiting_approval":
                        ordered_results.append({
                            "name": name, "call_id": call_id,
                            "status": "waiting_approval",
                            "approval_id": data.get("approval_id"),
                        })
                        loop_state = "waiting_approval"

            # Sort results by original provider call order
            ordered_results.sort(key=lambda r: provider_call_order.get(r["call_id"], 999))

            for tc_result in ordered_results:
                all_tool_results.append(tc_result)
                history.append(AgentMessage(
                    role="tool",
                    tool_call_id=tc_result["call_id"],
                    content=_json.dumps(tc_result),
                ))
                if tc_result.get("status") == "waiting_approval":
                    loop_state = "waiting_approval"

            yield _event("agent.observing", session_id, trace_id, {"tool_count": len(provider_result.tool_calls)})
            if loop_state == "waiting_approval":
                break

        # -- Fallback synthesis --
        if not final_message:
            final_message = _fallback_synthesis_from_stream(all_tool_results, loop_state)
            yield _event("agent.fallback_synthesis", session_id, trace_id, {"message": final_message[:200]})

        # -- Save history --
        history.append(AgentMessage(role="assistant", content=final_message))
        history_to_persist = (
            [message for message in history if message is not user_message]
            if suppress_user_message
            else history
        )
        await _save_session_history(db, session_id, history_to_persist)

        await _write_timeline(db, "agent.final_response", session_id=session_id, actor=provider.provider_name(), status=loop_state)
        await db.commit()
        yield _event("agent.completed", session_id, trace_id, {"status": loop_state, "message": final_message})

    except TimeoutError:
        yield _event("agent.failed", session_id, trace_id, {"error_code": "provider_timeout", "message": "Provider timed out"})
    except Exception as e:
        log.exception("agent stream error: session_id=%s", session_id)
        yield _event("agent.failed", session_id, trace_id, {"error_code": "internal_error", "message": str(e)[:500]})

    _mark_stream_inactive(session_id)
    yield _event("stream.close", session_id, trace_id)


async def _stream_tool_calls(
    db, provider, session, session_id, trace_id,
    tool_calls, known_functions, available_functions,
    call_path, execution_mode, max_depth,
    max_total_duration_sec, started_at, target_node_id=None,
) -> AsyncGenerator[dict, None]:
    """Process tool calls and emit events. Sub-generator consumed by the main stream."""
    for raw_tc in tool_calls:
        tc_name = str(raw_tc.get("name", ""))

        # Unknown function
        if tc_name not in known_functions:
            yield _event("agent.tool_call.created", session_id, trace_id, {
                "call_id": raw_tc.get("call_id", ""),
                "name": tc_name,
                "status": "failed",
            })
            yield _event("agent.tool_call.failed", session_id, trace_id, {
                "call_id": raw_tc.get("call_id", ""),
                "name": tc_name,
                "error_code": "function_not_available",
                "message": f"Function {tc_name!r} is not available",
            })
            continue

        # Loop detection
        if tc_name in call_path:
            yield _event("agent.tool_call.created", session_id, trace_id, {
                "call_id": raw_tc.get("call_id", ""),
                "name": tc_name,
                "status": "failed",
            })
            yield _event("agent.tool_call.failed", session_id, trace_id, {
                "call_id": raw_tc.get("call_id", ""),
                "name": tc_name,
                "error_code": "circular_dependency",
                "message": f"Circular: {tc_name!r} in call_path",
            })
            continue

        call_id = str(raw_tc.get("call_id", _make_event_id()))

        yield _event("agent.tool_call.created", session_id, trace_id, {
            "call_id": call_id,
            "name": tc_name,
            "sanitized_name": str(raw_tc.get("sanitized_name", tc_name)),
            "input": raw_tc.get("input", {}),
        })

        yield _event("agent.tool_call.arguments", session_id, trace_id, {
            "call_id": call_id,
            "name": tc_name,
            "input": raw_tc.get("input", {}),
        })

        # Execute and stream progress
        async for ev in _execute_and_stream(
            db, provider, session, session_id, trace_id,
            call_id, tc_name, raw_tc.get("input", {}),
            known_functions, available_functions,
            call_path, execution_mode, max_depth,
            max_total_duration_sec, started_at, None,
        ):
            yield ev


async def _execute_and_stream(
    db, provider, session, session_id, trace_id,
    call_id, tc_name, tc_input,
    known_functions, available_functions,
    call_path, execution_mode, max_depth,
    max_total_duration_sec, started_at, target_node_id=None,
) -> AsyncGenerator[dict, None]:
    """Execute a single tool call and stream its lifecycle events."""
    from yequ.services.capability_resolver import resolve_target_node
    from yequ.services.invocation_service import create_invocation, start_invocation
    from yequ.services.job_service import create_job
    from yequ.services.policy import check_policy

    # Resolve target node
    from yequ.config import get_settings
    resolved = await resolve_target_node(
        db,
        tc_name,
        requested_node_id=target_node_id,
        settings=get_settings(),
    )
    if resolved is None or not resolved.available:
        message = (
            resolved.unavailable_reason
            if resolved is not None and resolved.unavailable_reason
            else f"No online node has {tc_name!r}"
        )
        yield _event("agent.tool_call.failed", session_id, trace_id, {
            "call_id": call_id,
            "name": tc_name,
            "error_code": "function_not_available",
            "message": message,
        })
        return

    # Policy check
    func_meta = next((f for f in available_functions if f.name == tc_name), None)
    risk = func_meta.risk if func_meta else "safe"

    policy_r = check_policy(
        execution_mode=execution_mode,
        risk_level=risk,
        function_name=tc_name,
    )
    if not policy_r.allowed:
        yield _event("agent.tool_call.failed", session_id, trace_id, {
            "call_id": call_id,
            "name": tc_name,
            "error_code": "policy_denied",
            "message": policy_r.reason or "Policy denied",
        })
        return

    # Handle L2 write operations — create approval
    if func_meta and func_meta.effect in ("write", "destructive") and not tc_input.get("approval_id"):
        from yequ.services.approval_service import create_approval as svc_create_approval
        approval = await svc_create_approval(
            db,
            actor_id=session.actor_id,
            session_id=session_id,
            function_name=tc_name,
            target_node_id=resolved.node_id,
            input_data=tc_input,
            risk=resolved.risk,
            effect=resolved.effect,
        )
        await db.commit()
        yield _event("agent.approval.required", session_id, trace_id, {
            "call_id": call_id,
            "name": tc_name,
            "approval_id": approval.approval_id,
            "target_node_id": resolved.node_id,
        })
        yield _event("agent.tool_call.waiting_approval", session_id, trace_id, {
            "call_id": call_id,
            "name": tc_name,
            "approval_id": approval.approval_id,
            "status": "waiting_approval",
            "message": "Write operation requires approval",
        })
        return

    # Create invocation
    inv = await create_invocation(
        db,
        actor_type="agent",
        actor_id=session.actor_id,
        session_id=session_id,
        function_name=tc_name,
        input_payload=tc_input,
        target_node_id=resolved.node_id,
        execution_mode=execution_mode,
        max_depth=max_depth,
        call_path=list(call_path) + [tc_name],
    )
    start_invocation(inv)

    yield _event("agent.invocation.created", session_id, trace_id, {
        "call_id": call_id,
        "name": tc_name,
        "invocation_id": inv.invocation_id,
        "target_node_id": resolved.node_id,
    })

    # Create job
    job = await create_job(
        db,
        invocation_id=inv.invocation_id,
        node_id=resolved.node_id,
        function_name=tc_name,
        input_payload=tc_input,
        timeout_sec=resolved.timeout_sec,
    )
    await db.commit()

    yield _event("agent.job.queued", session_id, trace_id, {
        "call_id": call_id,
        "name": tc_name,
        "invocation_id": inv.invocation_id,
        "job_id": job.job_id,
    })

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
            j_result = await poll_db.execute(
                select(JobModel).where(JobModel.job_id == job.job_id)
            )
            j = j_result.scalar_one_or_none()
            if j:
                if poll_count == 0 and j.status == "running":
                    yield _event("agent.job.running", session_id, trace_id, {
                        "call_id": call_id,
                        "name": tc_name,
                        "job_id": job.job_id,
                        "status": "running",
                    })
                if j.status in ("succeeded", "failed", "timeout", "cancelled"):
                    final_status = j.status
                    break
        poll_count += 1
        await asyncio.sleep(0.5)

    # Collect result — read both Invocation and Job to preserve error details
    async with async_session_factory() as result_db:
        inv_result = await result_db.execute(
            select(Invocation).where(Invocation.invocation_id == inv.invocation_id)
        )
        inv_final = inv_result.scalar_one_or_none()

        job_result = await result_db.execute(
            select(JobModel).where(JobModel.job_id == job.job_id)
        )
        job_final = job_result.scalar_one_or_none()

        yield _event("agent.job.finished", session_id, trace_id, {
            "call_id": call_id,
            "name": tc_name,
            "job_id": job.job_id,
            "status": final_status,
        })

        if final_status == "succeeded":
            yield _event("agent.tool_call.completed", session_id, trace_id, {
                "call_id": call_id,
                "name": tc_name,
                "result": inv_final.result if inv_final else {},
            })
        else:
            # Preserve the original error from the Job/Invocation — never
            # overwrite with a generic "tool_failed".
            error_code = (
                job_final.error_code
                or (inv_final.error_code if inv_final else None)
                or "tool_failed"
            )
            error_message = (
                job_final.error_message
                or (inv_final.error_message if inv_final else None)
                or f"Tool {tc_name} ended with {final_status}"
            )
            error_details = (
                job_final.error_details
                if job_final
                else None
            )
            yield _event("agent.tool_call.failed", session_id, trace_id, {
                "call_id": call_id,
                "name": tc_name,
                "status": final_status,
                "error_code": error_code,
                "message": error_message,
                "details": error_details,
            })


# ── Concurrency scheduling for tool calls ──

CONCURRENCY_MAX = 4  # Configurable later


async def _execute_tool_calls_scheduled(
    db: AsyncSession,
    provider,
    session,
    session_id: str,
    trace_id: str,
    tool_calls: list[dict],
    known_functions: set[str],
    available_functions: list,
    call_path: list[str],
    execution_mode: str,
    max_depth: int,
    max_total_duration_sec: int,
    started_at,
    target_node_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Async generator that executes tool calls with concurrency scheduling.

    Yields SSE events in real-time. All created events are emitted first,
    then concurrent safe/read tools execute in parallel (max 4),
    then serial tools execute one by one.
    """
    from yequ.services.capability_resolver import resolve_target_node
    from yequ.services.policy import check_policy
    from yequ.config import get_settings
    from yequ.models.capability import Capability
    from sqlalchemy import select as sa_select
    from yequ.db import async_session_factory

    settings = get_settings()

    # ── Phase 1: Pre-flight validation + emit all created events ──
    classified: list[dict] = []

    for raw_tc in tool_calls:
        tc_name = str(raw_tc.get("name", ""))
        tc_call_id = str(raw_tc.get("call_id", _make_event_id()))
        tc_input = raw_tc.get("input", {})

        # Unknown function check
        if tc_name not in known_functions:
            yield _event("agent.tool_call.created", session_id, trace_id, {
                "call_id": tc_call_id, "name": tc_name,
            })
            yield _event("agent.tool_call.failed", session_id, trace_id, {
                "call_id": tc_call_id, "name": tc_name,
                "error_code": "function_not_available",
                "message": f"Function {tc_name!r} is not available",
            })
            classified.append({
                "call_id": tc_call_id, "name": tc_name, "input": tc_input,
                "status": "failed", "error": "function_not_available",
            })
            continue

        # Loop detection
        if tc_name in call_path:
            yield _event("agent.tool_call.created", session_id, trace_id, {
                "call_id": tc_call_id, "name": tc_name,
            })
            yield _event("agent.tool_call.failed", session_id, trace_id, {
                "call_id": tc_call_id, "name": tc_name,
                "error_code": "circular_dependency",
                "message": f"Circular: {tc_name!r} in call_path",
            })
            classified.append({
                "call_id": tc_call_id, "name": tc_name, "input": tc_input,
                "status": "failed", "error": "circular_dependency",
            })
            continue

        # Resolve target node (pre-flight)
        resolved = await resolve_target_node(
            db, tc_name,
            requested_node_id=target_node_id,
            settings=settings,
        )

        # Get function metadata
        func_meta = next((f for f in available_functions if f.name == tc_name), None)
        risk = func_meta.risk if func_meta else "safe"
        effect = func_meta.effect if func_meta else "read"

        # Emit created + arguments events
        yield _event("agent.tool_call.created", session_id, trace_id, {
            "call_id": tc_call_id,
            "name": tc_name,
            "sanitized_name": str(raw_tc.get("sanitized_name", tc_name)),
            "input": tc_input,
        })
        yield _event("agent.tool_call.arguments", session_id, trace_id, {
            "call_id": tc_call_id,
            "name": tc_name,
            "input": tc_input,
        })

        # Node not available
        if resolved is None or not resolved.available:
            message = (
                resolved.unavailable_reason
                if resolved is not None and resolved.unavailable_reason
                else f"No online node has {tc_name!r}"
            )
            yield _event("agent.tool_call.failed", session_id, trace_id, {
                "call_id": tc_call_id, "name": tc_name,
                "error_code": "function_not_available",
                "message": message,
            })
            classified.append({
                "call_id": tc_call_id, "name": tc_name, "input": tc_input,
                "status": "failed", "error": "no_node",
            })
            continue

        # Policy check
        policy_r = check_policy(
            execution_mode=execution_mode,
            risk_level=risk,
            function_name=tc_name,
        )
        if not policy_r.allowed:
            yield _event("agent.tool_call.failed", session_id, trace_id, {
                "call_id": tc_call_id, "name": tc_name,
                "error_code": "policy_denied",
                "message": policy_r.reason or "Policy denied",
            })
            classified.append({
                "call_id": tc_call_id, "name": tc_name, "input": tc_input,
                "status": "failed", "error": "policy_denied",
            })
            continue

        # Look up resource keys from capability
        cap_result = await db.execute(
            sa_select(Capability).where(
                Capability.capability_type == "function",
                Capability.name == tc_name,
                Capability.is_active == True,  # noqa: E712
            ).limit(1)
        )
        capability = cap_result.scalar_one_or_none()
        resource_keys = list(capability.resource_keys) if (capability and capability.resource_keys) else []
        conflict_policy = capability.conflict_policy if capability else None

        # Determine if concurrent-safe
        is_safe_read = (risk == "safe" and effect == "read")
        has_resource_conflict = bool(resource_keys) and conflict_policy == "serialize"
        is_concurrent_safe = is_safe_read and not has_resource_conflict

        classified.append({
            "call_id": tc_call_id, "name": tc_name, "input": tc_input,
            "status": "pending",
            "resolved": resolved,
            "func_meta": func_meta,
            "is_concurrent_safe": is_concurrent_safe,
            "resource_keys": resource_keys,
        })

    # ── Phase 2: Split into concurrent vs serial groups ──
    concurrent_candidates = [t for t in classified if t.get("is_concurrent_safe") and t["status"] == "pending"]
    serial_tools = [t for t in classified if not t.get("is_concurrent_safe") and t["status"] == "pending"]

    # Move tools with shared resource_keys from concurrent to serial
    resource_key_owners: dict[str, str] = {}
    actual_concurrent: list[dict] = []
    for t in concurrent_candidates:
        keys = t.get("resource_keys", [])
        conflicts = [k for k in keys if k in resource_key_owners]
        if conflicts:
            serial_tools.append(t)
        else:
            for k in keys:
                resource_key_owners[k] = t["call_id"]
            actual_concurrent.append(t)

    # ── Phase 3: Execute ──

    # Execute concurrent tools with semaphore (max CONCURRENCY_MAX)
    if actual_concurrent:
        semaphore = asyncio.Semaphore(CONCURRENCY_MAX)

        async def _execute_concurrent(tool_info: dict) -> list[dict]:
            """Execute a single tool call using a fresh DB session."""
            async with semaphore:
                collected_events: list[dict] = []
                try:
                    async with async_session_factory() as exec_db:
                        async for ev in _execute_and_stream(
                            exec_db, provider, session, session_id, trace_id,
                            tool_info["call_id"], tool_info["name"], tool_info["input"],
                            known_functions, available_functions,
                            call_path, execution_mode, max_depth,
                            max_total_duration_sec, started_at,
                            target_node_id=target_node_id,
                        ):
                            collected_events.append(ev)
                except Exception as e:
                    log.exception("concurrent tool execution failed: call_id=%s", tool_info["call_id"])
                    collected_events.append(_event("agent.tool_call.failed", session_id, trace_id, {
                        "call_id": tool_info["call_id"],
                        "name": tool_info["name"],
                        "error_code": "internal_error",
                        "message": str(e)[:500],
                    }))
                return collected_events

        tasks = [asyncio.create_task(_execute_concurrent(t)) for t in actual_concurrent]
        # Yield events as tasks complete (interleaving is fine — each event has call_id)
        for completed in asyncio.as_completed(tasks):
            tool_events = await completed
            for ev in tool_events:
                yield ev

    # Execute serial tools one by one
    for tool_info in serial_tools:
        try:
            async with async_session_factory() as serial_db:
                async for ev in _execute_and_stream(
                    serial_db, provider, session, session_id, trace_id,
                    tool_info["call_id"], tool_info["name"], tool_info["input"],
                    known_functions, available_functions,
                    call_path, execution_mode, max_depth,
                    max_total_duration_sec, started_at,
                    target_node_id=target_node_id,
                ):
                    yield ev
        except Exception as e:
            log.exception("serial tool execution failed: call_id=%s", tool_info["call_id"])
            yield _event("agent.tool_call.failed", session_id, trace_id, {
                "call_id": tool_info["call_id"],
                "name": tool_info["name"],
                "error_code": "internal_error",
                "message": str(e)[:500],
            })


async def agent_plan_stream(
    db: AsyncSession,
    provider: AgentProvider,
    *,
    session_id: str,
    prompt: str,
    target_node_id: str,
    available_functions: list[AgentFunction],
    execution_mode: str = "auto",
    max_total_duration_sec: int = 300,
) -> AsyncGenerator[dict, None]:
    """Async generator yielding SSE event dicts for agent plan stream."""
    trace_id = _make_trace_id()

    yield _event("stream.open", session_id, trace_id)
    _mark_stream_active(session_id)

    from yequ.models.session import Session

    result = await db.execute(
        select(Session).where(Session.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        yield _event("agent.failed", session_id, trace_id, {
            "error_code": "session_not_found",
            "message": "Session not found",
        })
        _mark_stream_inactive(session_id)
        yield _event("stream.close", session_id, trace_id)
        return

    yield _event("agent.session.resolved", session_id, trace_id, {
        "session_status": session.status,
    })

    yield _event("agent.prompt.received", session_id, trace_id, {
        "prompt": prompt[:500],
    })

    yield _event("agent.provider.started", session_id, trace_id, {
        "provider_name": provider.provider_name(),
    })

    from yequ.agent.agent_service import (
        _build_rollback_hint,
        _extract_function_for_service,
        _extract_service_name,
    )
    from yequ.services.maintenance_service import create_plan

    func_names = [f.name for f in available_functions]
    classification_prompt = (
        f"User request: {prompt}\n"
        f"Available functions: {', '.join(func_names)}\n"
        "Classify this request as EXACTLY ONE of:\n"
        "- readonly_check: just check status, no repair needed\n"
        "- check_and_fix: check status AND repair/fix if unhealthy\n"
        "Respond with ONLY the classification word, nothing else."
    )

    yield _event("agent.planning.summary", session_id, trace_id, {
        "message": "Analyzing request intent...",
    })

    intent = "readonly_check"
    try:
        provider_result = await asyncio.wait_for(
            provider.invoke(
                classification_prompt,
                available_functions=[],
                context={"session_id": session_id},
            ),
            timeout=30.0,
        )
        msg = (provider_result.message or "").strip().lower()
        if "check_and_fix" in msg or "repair" in msg:
            intent = "check_and_fix"
    except Exception:
        pass

    service_name = _extract_service_name(prompt, available_functions)  # type: ignore[arg-type]
    function_name = _extract_function_for_service(prompt, available_functions)  # type: ignore[arg-type]

    steps_ir: list[dict] = []
    if intent == "check_and_fix":
        steps_ir.append({
            "seq": 1, "kind": "check",
            "function_name": function_name,
            "input": {"name": service_name},
            "condition": "always", "depends_on": [],
            "risk": "readonly", "requires_approval": False,
        })
        repair_func = "system.service.ensure_running"
        rollback_hint = _build_rollback_hint(service_name, repair_func, available_functions)  # type: ignore[arg-type]
        if any(f.name == repair_func for f in available_functions):
            steps_ir.append({
                "seq": 2, "kind": "repair",
                "function_name": repair_func,
                "input": {"name": service_name},
                "condition": "if_previous_unhealthy",
                "depends_on": [1],
                "risk": "maintenance_write", "requires_approval": True,
                "rollback_hint": rollback_hint,
            })
        elif any(f.name == "system.service.restart" for f in available_functions):
            steps_ir.append({
                "seq": 2, "kind": "repair",
                "function_name": "system.service.restart",
                "input": {"name": service_name},
                "condition": "if_previous_unhealthy",
                "depends_on": [1],
                "risk": "maintenance_write", "requires_approval": True,
                "rollback_hint": rollback_hint,
            })
        steps_ir.append({
            "seq": 3, "kind": "verify",
            "function_name": function_name,
            "input": {"name": service_name},
            "condition": "after_repair", "depends_on": [2],
            "risk": "readonly", "requires_approval": False,
        })
    else:
        steps_ir.append({
            "seq": 1, "kind": "check",
            "function_name": function_name,
            "input": {"name": service_name},
            "condition": "always", "depends_on": [],
            "risk": "readonly", "requires_approval": False,
        })

    for s in steps_ir:
        yield _event("agent.plan.step.created", session_id, trace_id, {
            "seq": s["seq"],
            "kind": s["kind"],
            "function_name": s["function_name"],
            "requires_approval": s["requires_approval"],
        })

    has_write = any(s["requires_approval"] for s in steps_ir)

    for s in steps_ir:
        func_exists = any(f.name == s["function_name"] for f in available_functions)
        if not func_exists:
            yield _event("agent.failed", session_id, trace_id, {
                "error_code": "function_not_available",
                "message": f"Function {s['function_name']!r} not registered on node",
            })
            _mark_stream_inactive(session_id)
            yield _event("stream.close", session_id, trace_id)
            return

    plan = await create_plan(
        db,
        goal=prompt,
        actor_id=provider.provider_name(),
        target_node_id=target_node_id,
        steps=[{
            "function_name": s["function_name"],
            "input": s["input"],
            "kind": s["kind"],
            "condition": s["condition"],
            "depends_on": [str(d) for d in s.get("depends_on", [])],
            "requires_approval": s["requires_approval"],
            "risk": s["risk"],
            "continue_on_failure": False,
            "rollback_hint": s.get("rollback_hint"),
        } for s in steps_ir],
        session_id=session_id,
        risk="maintenance" if has_write else "safe",
        max_total_duration_sec=max_total_duration_sec,
        execution_mode=execution_mode,
    )

    if has_write:
        plan.status = "waiting_approval"
        await db.commit()

    yield _event("agent.plan.created", session_id, trace_id, {
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "status": plan.status,
        "step_count": len(steps_ir),
    })

    if has_write:
        yield _event("agent.approval.required", session_id, trace_id, {
            "plan_id": plan.plan_id,
            "message": "This plan contains write operations and requires approval",
        })

    yield _event("agent.completed", session_id, trace_id)
    _mark_stream_inactive(session_id)
    yield _event("stream.close", session_id, trace_id)


def _fallback_synthesis_from_stream(tool_results: list[dict], loop_state: str) -> str:
    """Produce a human-readable summary from streamed tool results.

    Quality rules:
    - All failed → list each tool and failure reason
    - Partial success → "已确认 / 未确认 / 下一步"
    - Node offline / no capability → explicit diagnostic
    - General → structured summary
    """
    if not tool_results:
        if loop_state == "provider_failed":
            return (
                "Agent provider did not return a usable tool plan. "
                "No system action was executed. Please retry with a more specific request."
            )
        if loop_state == "timeout":
            return (
                "Agent execution timed out before any tool could run. "
                "No system action was executed."
            )
        return (
            "I did not receive any tool calls or final answer from the model. "
            "No system action was executed. Please retry the request."
        )

    succeeded = [r for r in tool_results if r.get("status") == "succeeded"]
    failed = [r for r in tool_results if r.get("status") == "failed"]
    waiting = [r for r in tool_results if r.get("status") == "waiting_approval"]
    other = [r for r in tool_results if r.get("status") not in ("succeeded", "failed", "waiting_approval")]

    # Check for no-node / capability failures
    node_failures = [
        r for r in failed
        if str(r.get("error", "")).startswith("No online node")
        or "function_not_available" in str(r.get("error", ""))
        or "is not available" in str(r.get("error", ""))
        or "not available" in str(r.get("error", ""))
    ]
    if node_failures and not succeeded:
        names = [r.get("name", "unknown") for r in node_failures]
        return (
            f"无法执行检查：没有在线节点提供所需的能力。\n"
            f"缺失的能力：{', '.join(names)}\n"
            f"建议：请确认目标节点在线并已注册相应功能后重试。"
        )

    # All failed
    if failed and not succeeded:
        lines = ["所有检查均失败："]
        for r in failed:
            name = r.get("name", "unknown")
            error = r.get("error", "未知错误")
            lines.append(f"- **{name}**: {error}")
        return "\n".join(lines)

    # Partial success
    if succeeded and failed:
        lines = []
        lines.append("✅ **已确认**：")
        for r in succeeded:
            name = r.get("name", "unknown")
            lines.append(f"- {name}：正常")
        lines.append("")
        lines.append("❌ **未确认**：")
        for r in failed:
            name = r.get("name", "unknown")
            error = r.get("error", "未知错误")
            lines.append(f"- {name}：{error}")
        lines.append("")
        lines.append("🔜 **下一步**：请根据未确认项决定是否需要进一步排查或修复。")
        if waiting:
            lines.append(f"⏳ 有 {len(waiting)} 个操作等待审批。")
        return "\n".join(lines)

    # Waiting approval
    if waiting and not failed:
        names = [r.get("name", "unknown") for r in waiting]
        return (
            f"⏳ **等待审批**：以下操作需要审批后才能执行：\n"
            + "\n".join(f"- {n}" for n in names)
        )

    # All succeeded
    if succeeded and not failed:
        lines = [f"✅ 所有 {len(succeeded)} 项检查均通过："]
        for r in succeeded:
            name = r.get("name", "unknown")
            lines.append(f"- {name}：正常")
        return "\n".join(lines)

    # Fallback: generic summary
    parts = [f"Results ({len(tool_results)} tools, {loop_state}):"]
    for r in tool_results:
        name = r.get("name", "unknown")
        status = r.get("status", "unknown")
        if status == "succeeded":
            parts.append(f"- {name}: succeeded")
        elif status == "failed":
            parts.append(f"- {name}: failed — {r.get('error', status)}")
        else:
            parts.append(f"- {name}: {status}")
    return "\n".join(parts)
