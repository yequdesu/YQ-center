"""Agent Service -- orchestrates Agent interactions through Center.

Flow:
  prompt -> Provider.invoke -> raw tool_calls
  -> validate -> resolve node -> policy -> create Invocation
  -> wait for completion -> collect result -> final response
"""

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.provider import AgentFunction, AgentProvider
from yequ.agent.tool_execution import (
    AgentInvokeError,
    AgentInvokeOutput,
    AgentInvokeResponse,
    AgentInvokeTrace,
    AgentInvokeUsage,
    AgentToolCall,
    AgentToolPolicySnapshot,
)
from yequ.protocol import ErrorCode, RiskLevel


def _make_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:16]}"


def _make_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:16]}"


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


async def create_agent_session(
    db: AsyncSession,
    *,
    actor_id: str,
    execution_mode: str = "auto",
    max_depth: int = 5,
    max_steps: int = 20,
    max_total_duration_sec: int = 300,
) -> dict[str, object]:
    """Create an Agent Session."""
    from yequ.models.session import Session

    session_id = _make_session_id()
    now = datetime.now(UTC)
    sess = Session(
        session_id=session_id,
        actor_type="agent",
        actor_id=actor_id,
        status="active",
        execution_mode=execution_mode,
        started_at=now,
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
    db: AsyncSession,
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
) -> AgentInvokeResponse:
    """Invoke an Agent through the complete execution pipeline.

    1. Load session, validate constraints
    2. Call Provider to get raw tool_calls
    3. For each tool_call: validate -> resolve node -> policy -> execute -> wait
    4. Generate final output message
    5. Return complete AgentInvokeResponse
    """
    from yequ.models.session import Session

    call_path = call_path or []
    now = datetime.now(UTC)
    if started_at is None:
        started_at = now
    trace_id = f"tr_{uuid.uuid4().hex[:16]}"

    # Duration check
    elapsed = (now - started_at).total_seconds()
    if elapsed > max_total_duration_sec:
        return AgentInvokeResponse(
            success=False, status="timeout", session_id=session_id,
            error=AgentInvokeError(code=ErrorCode.MAX_DURATION_EXCEEDED,
                                   message=(
                                       f"Duration {elapsed:.1f}s exceeds max "
                                       f"{max_total_duration_sec}s"
                                   )),
            trace=AgentInvokeTrace(
                trace_id=trace_id, call_path=list(call_path),
                step_count=step_count + 1, max_depth=max_depth,
                max_steps=max_steps,
                max_total_duration_sec=max_total_duration_sec,
            ),
        )

    # Step check
    if step_count >= max_steps:
        return AgentInvokeResponse(
            success=False, status="failed", session_id=session_id,
            error=AgentInvokeError(
                code=ErrorCode.MAX_STEPS_EXCEEDED,
                message=f"Max steps {max_steps} exceeded",
            ),
            trace=AgentInvokeTrace(
                trace_id=trace_id, call_path=list(call_path),
                step_count=step_count + 1, max_depth=max_depth,
                max_steps=max_steps,
                max_total_duration_sec=max_total_duration_sec,
            ),
        )

    # Depth check
    if len(call_path) >= max_depth:
        return AgentInvokeResponse(
            success=False, status="failed", session_id=session_id,
            error=AgentInvokeError(code=ErrorCode.CALL_DEPTH_EXCEEDED,
                                   message=f"Depth {len(call_path)} exceeds max {max_depth}"),
            trace=AgentInvokeTrace(
                trace_id=trace_id, call_path=list(call_path),
                step_count=step_count + 1, max_depth=max_depth,
                max_steps=max_steps,
                max_total_duration_sec=max_total_duration_sec,
            ),
        )

    result = await db.execute(
        select(Session).where(Session.session_id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        return AgentInvokeResponse(
            success=False, status="failed", session_id=session_id,
            error=AgentInvokeError(code=ErrorCode.INTERNAL_ERROR, message="Session not found"),
            trace=AgentInvokeTrace(
                trace_id=trace_id, call_path=list(call_path),
                step_count=step_count + 1, max_depth=max_depth,
                max_steps=max_steps,
                max_total_duration_sec=max_total_duration_sec,
            ),
        )

    deadline = datetime.fromtimestamp(
        session.started_at.timestamp() + max_total_duration_sec, tz=UTC
    )

    # -- Step 1: Write agent.prompt.received --
    await _write_timeline(
        db, "agent.prompt.received",
        session_id=session_id, actor=provider.provider_name(),
        prompt=prompt, step=step_count + 1,
    )

    # -- Step 2: Call Provider --
    provider_result = await provider.invoke(
        prompt,
        available_functions=available_functions,
        context={"call_path": list(call_path), "session_id": session_id},
    )

    # Handle provider failure
    if not provider_result.success:
        await _write_timeline(db, "agent.provider.completed", session_id=session_id,
                              actor=provider.provider_name(), success=False,
                              error=provider_result.error_message)
        return AgentInvokeResponse(
            success=False, status="failed", provider_name=provider.provider_name(),
            session_id=session_id,
            error=AgentInvokeError(code=provider_result.error_code or "provider_error",
                                   message=provider_result.error_message or "Provider failed",
                                   retryable=provider_result.retryable),
            usage=AgentInvokeUsage(tool_calls=0),
            trace=AgentInvokeTrace(
                trace_id=trace_id, call_path=list(call_path),
                step_count=step_count + 1, max_depth=max_depth,
                max_steps=max_steps,
                max_total_duration_sec=max_total_duration_sec,
            ),
        )

    await _write_timeline(db, "agent.provider.completed", session_id=session_id,
                          actor=provider.provider_name(), success=True,
                          tool_call_count=len(provider_result.tool_calls))

    # -- Step 3: Validate tool calls + execute --
    execution_results: list[AgentToolCall] = []
    for raw_tc in provider_result.tool_calls:
        tc_name = str(raw_tc.get("name", ""))

        # Loop detection
        if tc_name in call_path:
            denied_tc = AgentToolCall(
                call_id=raw_tc.get("call_id", _make_call_id()),
                name=tc_name,
                sanitized_name=tc_name,
                input=raw_tc.get("input", {}),
                status="failed",
                error={"code": ErrorCode.CIRCULAR_DEPENDENCY,
                       "message": f"Circular: {tc_name!r} in call_path {call_path}"},
                started_at=_iso(now),
                finished_at=_iso(datetime.now(UTC)),
            )
            execution_results.append(denied_tc)
            await _write_timeline(db, "agent.tool.denied", session_id=session_id,
                                  actor=provider.provider_name(), call_id=denied_tc.call_id,
                                  function_name=tc_name, error_code=ErrorCode.CIRCULAR_DEPENDENCY)
            continue

        # Policy check via available_functions metadata
        func_meta = next(
            (f for f in available_functions if f.name == tc_name),
            None,
        )
        risk = func_meta.risk if func_meta else RiskLevel.SAFE

        from yequ.services.policy import check_policy

        policy_r = check_policy(
            execution_mode=execution_mode,
            risk_level=risk,
            function_name=tc_name,
        )
        if not policy_r.allowed:
            denied_tc = AgentToolCall(
                call_id=raw_tc.get("call_id", _make_call_id()),
                name=tc_name,
                sanitized_name=tc_name,
                input=raw_tc.get("input", {}),
                policy=AgentToolPolicySnapshot(
                    decision=policy_r.decision, risk=risk,
                    effect=func_meta.effect if func_meta else "read",
                    execution_mode=execution_mode,
                ),
                status="failed",
                error={"code": ErrorCode.POLICY_DENIED,
                       "message": policy_r.reason or "Policy denied"},
                started_at=_iso(now),
                finished_at=_iso(datetime.now(UTC)),
            )
            execution_results.append(denied_tc)
            await _write_timeline(db, "agent.tool.denied", session_id=session_id,
                                  actor=provider.provider_name(), call_id=denied_tc.call_id,
                                  function_name=tc_name, error_code=ErrorCode.POLICY_DENIED,
                                  reason=policy_r.reason)
            continue

        # Execute through pipeline
        tc = AgentToolCall(
            call_id=raw_tc.get("call_id", _make_call_id()),
            name=tc_name,
            sanitized_name=str(raw_tc.get("sanitized_name", tc_name)),
            input=raw_tc.get("input", {}),
        )
        executed = await _execute_tool_call(
            db, tc, session_id=session_id, actor_id=session.actor_id,
            execution_mode=execution_mode, call_path=list(call_path),
            max_depth=max_depth, deadline=deadline,
            provider_name=provider.provider_name(),
        )
        execution_results.append(executed)

    # -- Step 4: Aggregate status --
    all_succeeded = all(tc.status == "succeeded" for tc in execution_results)
    any_failed = any(tc.status in ("failed", "denied") for tc in execution_results)

    if not execution_results or all_succeeded:
        final_status = "succeeded"
    elif any_failed:
        final_status = "failed"
    else:
        final_status = "partial"

    # -- Step 5: Generate output message --
    output = _generate_output(provider_result.message, execution_results)

    # -- Step 6: Write agent.final_response --
    await _write_timeline(db, "agent.final_response", session_id=session_id,
                          actor=provider.provider_name(), status=final_status,
                          tool_call_count=len(execution_results))

    await db.commit()

    # -- Step 7: Build response --
    error = None
    if final_status != "succeeded":
        first_failed = next((tc for tc in execution_results if tc.status != "succeeded"), None)
        if first_failed and first_failed.error:
            error = AgentInvokeError(**first_failed.error)
        elif not provider_result.success:
            error = AgentInvokeError(code=provider_result.error_code or "provider_error",
                                     message=provider_result.error_message or "")

    return AgentInvokeResponse(
        success=(final_status == "succeeded"),
        status=final_status,
        provider_name=provider.provider_name(),
        session_id=session_id,
        output=output,
        error=error,
        tool_calls=execution_results,
        usage=AgentInvokeUsage(
            prompt_tokens=(
                provider_result.usage.get("prompt_tokens")
                if provider_result.usage else None
            ),
            completion_tokens=(
                provider_result.usage.get("completion_tokens")
                if provider_result.usage else None
            ),
            total_tokens=(
                provider_result.usage.get("total_tokens")
                if provider_result.usage else None
            ),
            tool_calls=len(execution_results),
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


async def _execute_tool_call(
    db: AsyncSession,
    tc: AgentToolCall,
    *,
    session_id: str,
    actor_id: str,
    execution_mode: str,
    call_path: list[str],
    max_depth: int,
    deadline: datetime,
    provider_name: str,
) -> AgentToolCall:
    """Execute a single tool call through the Center pipeline.

    Flow: validate -> resolve node -> create Invocation -> wait -> collect.
    Returns the AgentToolCall with execution results filled in.
    """
    from yequ.services.capability_resolver import resolve_target_node
    from yequ.services.invocation_service import create_invocation, start_invocation
    from yequ.services.job_service import create_job
    from yequ.services.policy import check_policy

    now = datetime.now(UTC)
    tc.started_at = _iso(now)

    # -- Resolve target node --
    resolved = await resolve_target_node(db, tc.name)
    if resolved is None:
        tc.status = "failed"
        tc.error = {"code": ErrorCode.FUNCTION_NOT_AVAILABLE,
                     "message": f"No online node has {tc.name!r}"}
        tc.finished_at = _iso(datetime.now(UTC))
        await _write_timeline(db, "agent.tool.denied", session_id=session_id,
                              actor=provider_name, call_id=tc.call_id,
                              function_name=tc.name, error_code=ErrorCode.FUNCTION_NOT_AVAILABLE)
        return tc

    tc.target_node_id = resolved.node_id

    # -- Policy check (from resolved capability) --
    policy_r = check_policy(execution_mode=execution_mode, risk_level=resolved.risk,
                             function_name=tc.name)
    tc.policy = AgentToolPolicySnapshot(
        decision=policy_r.decision, risk=resolved.risk,
        effect=resolved.effect, execution_mode=execution_mode,
    )

    if not policy_r.allowed:
        tc.status = "failed"
        tc.error = {"code": ErrorCode.POLICY_DENIED, "message": policy_r.reason or "Policy denied"}
        tc.finished_at = _iso(datetime.now(UTC))
        await _write_timeline(db, "agent.tool.denied", session_id=session_id,
                              actor=provider_name, call_id=tc.call_id,
                              function_name=tc.name, error_code=ErrorCode.POLICY_DENIED,
                              reason=policy_r.reason)
        return tc

    # -- Create Invocation + Job --
    await _write_timeline(db, "agent.tool.selected", session_id=session_id,
                          actor=provider_name, call_id=tc.call_id,
                          function_name=tc.name, target_node_id=resolved.node_id,
                          risk=resolved.risk, effect=resolved.effect)

    inv = await create_invocation(
        db, actor_type="agent", actor_id=actor_id, session_id=session_id,
        function_name=tc.name, input_payload=tc.input,
        target_node_id=resolved.node_id, execution_mode=execution_mode,
        max_depth=max_depth, call_path=list(call_path) + [tc.name],
    )
    start_invocation(inv)

    job = await create_job(
        db, invocation_id=inv.invocation_id, node_id=resolved.node_id,
        function_name=tc.name, input_payload=tc.input,
        timeout_sec=resolved.timeout_sec,
    )
    await db.flush()

    tc.invocation_id = inv.invocation_id
    tc.job_ids = [job.job_id]

    await _write_timeline(db, "agent.tool.invocation_created", session_id=session_id,
                          actor=provider_name, call_id=tc.call_id,
                          function_name=tc.name, invocation_id=inv.invocation_id,
                          job_id=job.job_id, target_node_id=resolved.node_id)

    # -- Wait for Invocation terminal --
    final_status = await _wait_invocation_terminal(db, inv.invocation_id, deadline)

    # -- Collect result --
    from yequ.models.invocation import Invocation
    inv_result = await db.execute(
        select(Invocation).where(Invocation.invocation_id == inv.invocation_id)
    )
    inv_final = inv_result.scalar_one_or_none()

    tc.finished_at = _iso(datetime.now(UTC))

    if final_status == "succeeded":
        tc.status = "succeeded"
        tc.result = inv_final.result if inv_final else {}
        await _write_timeline(db, "agent.tool.completed", session_id=session_id,
                              actor=provider_name, call_id=tc.call_id,
                              function_name=tc.name, status="succeeded",
                              invocation_id=inv.invocation_id, job_id=job.job_id)
    elif final_status == "timeout":
        tc.status = "timeout"
        tc.error = {"code": "tool_timeout", "message": f"Tool {tc.name} timed out"}
        await _write_timeline(db, "agent.tool.failed", session_id=session_id,
                              actor=provider_name, call_id=tc.call_id,
                              function_name=tc.name, status="timeout",
                              invocation_id=inv.invocation_id, error_code="tool_timeout")
    else:
        tc.status = "failed"
        tc.error = {"code": "tool_failed",
                     "message": f"Tool {tc.name} ended with {final_status}"}
        await _write_timeline(db, "agent.tool.failed", session_id=session_id,
                              actor=provider_name, call_id=tc.call_id,
                              function_name=tc.name, status=final_status,
                              invocation_id=inv.invocation_id, error_code="tool_failed")

    return tc


async def _wait_invocation_terminal(
    db: AsyncSession,
    invocation_id: str,
    deadline: datetime,
    poll_interval: float = 0.5,
) -> str:
    """Poll for Invocation terminal status, return final status string.

    Waits until Invocation reaches succeeded/failed/timeout/cancelled/partial,
    or until deadline passes. Returns the final status.
    """
    from yequ.models.invocation import Invocation

    while datetime.now(UTC) < deadline:
        result = await db.execute(
            select(Invocation).where(Invocation.invocation_id == invocation_id)
        )
        inv = result.scalar_one_or_none()
        if inv and inv.status in ("succeeded", "failed", "timeout", "cancelled", "partial"):
            return inv.status
        await asyncio.sleep(poll_interval)

    return "timeout"


def _generate_output(provider_message: str, tool_calls: list[AgentToolCall]) -> AgentInvokeOutput:
    """Generate the final AgentInvokeOutput from provider message + tool results."""
    if provider_message.strip():
        return AgentInvokeOutput(message=provider_message, data={})

    succeeded = [tc for tc in tool_calls if tc.status == "succeeded"]
    failed = [tc for tc in tool_calls if tc.status != "succeeded"]

    if failed:
        first = failed[0]
        return AgentInvokeOutput(
            message=f"Failed to complete tool call: {first.name}",
            data={},
        )

    if succeeded:
        # Check for system.metrics.snapshot
        metrics_tc = next((tc for tc in succeeded if tc.name == "system.metrics.snapshot"), None)
        if metrics_tc and metrics_tc.result:
            r = metrics_tc.result
            msg = (
                f"Windows node {metrics_tc.target_node_id} "
                f"CPU {r.get('cpu', '?')}%, "
                f"memory {r.get('memory', '?')}%, "
                f"disk {r.get('disk', '?')}%."
            )
            return AgentInvokeOutput(message=msg, data=dict(r))
        # Generic
        return AgentInvokeOutput(
            message=f"Completed {len(succeeded)} tool call(s).",
            data={},
        )

    return AgentInvokeOutput(message="No tool calls to execute.", data={})


async def _write_timeline(
    db: AsyncSession,
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
) -> None:
    """Write an agent timeline event."""
    from sqlalchemy import func

    from yequ.models.timeline import TimelineEvent

    result = await db.execute(select(func.max(TimelineEvent.global_seq)))
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
    db.add(event)
    await db.flush()
