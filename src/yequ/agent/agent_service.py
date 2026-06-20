"""Agent Service -- orchestrates Agent interactions through Center.

Flow:
  prompt -> Provider.invoke -> raw tool_calls
  -> validate -> resolve node -> policy -> create Invocation
  -> wait for completion -> collect result -> final response
"""

import asyncio
import logging
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
from yequ.services.approval_service import consume_approval, create_approval, verify_approval

log = logging.getLogger(__name__)


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

    # SQLite strips timezone; if naive, assume UTC
    started = session.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    deadline = datetime.fromtimestamp(
        started.timestamp() + max_total_duration_sec, tz=UTC
    )

    # L1 readonly policy: build a set of known function names from available_functions
    known_functions = {f.name for f in available_functions}

    # -- Step 1: Write agent.prompt.received + COMMIT before provider call --
    await _write_timeline(
        db, "agent.prompt.received",
        session_id=session_id, actor=provider.provider_name(),
        prompt=prompt, step=step_count + 1,
    )
    await db.commit()  # commit so event is visible even if provider hangs

    # -- Step 2: Call Provider with hard asyncio timeout --
    log.info("agent provider request started: provider=%s session_id=%s",
             provider.provider_name(), session_id)

    # Diagnostic events: only write when debug_timeline is enabled
    from yequ.config import get_settings as _gs
    if _gs().debug_timeline:
        await _write_timeline(db, "agent.provider.request.started",
                              session_id=session_id, actor=provider.provider_name())
        await db.commit()

    import time as _time
    _t0 = _time.monotonic()
    try:
        provider_result = await asyncio.wait_for(
            provider.invoke(
                prompt,
                available_functions=available_functions,
                context={"call_path": list(call_path), "session_id": session_id},
            ),
            timeout=45.0,
        )
        _elapsed = _time.monotonic() - _t0
        log.info("agent provider request returned: provider=%s elapsed=%.1fs",
                 provider.provider_name(), _elapsed)

        if _gs().debug_timeline:
            await _write_timeline(db, "agent.provider.request.returned",
                                  session_id=session_id, actor=provider.provider_name(),
                                  tool_call_count=len(provider_result.tool_calls))
            await db.commit()

        # Parse raw tool_calls into AgentToolCall list
        if _gs().debug_timeline:
            await _write_timeline(db, "agent.provider.parse.started",
                                  session_id=session_id, actor=provider.provider_name(),
                                  tool_call_count=len(provider_result.tool_calls))
            await db.commit()

        raw_tool_calls_parsed: list[dict[str, object]] = []
        for raw_tc in provider_result.tool_calls:
            raw_tool_calls_parsed.append({
                "call_id": str(raw_tc.get("call_id", "")),
                "name": str(raw_tc.get("name", "")),
                "sanitized_name": str(raw_tc.get("sanitized_name", "")),
            })

        if _gs().debug_timeline:
            await _write_timeline(db, "agent.provider.parse.completed",
                                  session_id=session_id, actor=provider.provider_name(),
                                  tool_call_count=len(raw_tool_calls_parsed))
            await db.commit()
    except TimeoutError:
        log.error("agent provider timed out: provider=%s session_id=%s",
                  provider.provider_name(), session_id)
        await _write_timeline(db, "agent.provider.failed", session_id=session_id,
                              actor=provider.provider_name(), success=False,
                              error="Provider timed out after 45s",
                              error_code="provider_timeout")
        await db.commit()
        return AgentInvokeResponse(
            success=False, status="failed", provider_name=provider.provider_name(),
            session_id=session_id,
            error=AgentInvokeError(code="provider_timeout",
                                   message="Provider timed out after 45s",
                                   retryable=True),
            usage=AgentInvokeUsage(tool_calls=0),
            trace=AgentInvokeTrace(
                trace_id=trace_id, call_path=list(call_path),
                step_count=step_count + 1, max_depth=max_depth,
                max_steps=max_steps, max_total_duration_sec=max_total_duration_sec,
            ),
        )

    # ── Post-provider: wrap everything in try/except for safety ──
    try:
        # Handle provider failure
        if not provider_result.success:
            log.error("agent provider request failed: provider=%s error=%s",
                      provider.provider_name(), provider_result.error_message)
            await _write_timeline(db, "agent.provider.failed", session_id=session_id,
                                  actor=provider.provider_name(), success=False,
                                  error=provider_result.error_message,
                                  error_code=provider_result.error_code or "provider_error")
            await db.commit()
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

        log.info("agent provider request completed: provider=%s tool_call_count=%d",
                 provider.provider_name(), len(provider_result.tool_calls))

        await _write_timeline(db, "agent.provider.completed", session_id=session_id,
                              actor=provider.provider_name(), success=True,
                              tool_call_count=len(provider_result.tool_calls))
    except Exception as _post_exc:
        log.exception("agent post-provider exception: session_id=%s", session_id)
        await _write_timeline(db, "agent.provider.failed", session_id=session_id,
                              actor=provider.provider_name(), success=False,
                              error=str(_post_exc)[:500],
                              error_code="internal_error")
        await db.commit()
        return AgentInvokeResponse(
            success=False, status="failed", provider_name=provider.provider_name(),
            session_id=session_id,
            error=AgentInvokeError(code="internal_error",
                                   message=str(_post_exc)[:500],
                                   retryable=False),
            usage=AgentInvokeUsage(tool_calls=0),
            trace=AgentInvokeTrace(
                trace_id=trace_id, call_path=list(call_path),
                step_count=step_count + 1, max_depth=max_depth,
                max_steps=max_steps,
                max_total_duration_sec=max_total_duration_sec,
            ),
        )

    # -- Step 3: Validate tool calls + execute --
    execution_results: list[AgentToolCall] = []
    for raw_tc in provider_result.tool_calls:
        tc_name = str(raw_tc.get("name", ""))

        # L1 readonly: reject unknown tool calls (not in available_functions at all)
        if tc_name not in known_functions:
            tc = AgentToolCall(
                call_id=raw_tc.get("call_id", _make_call_id()),
                name=tc_name,
                sanitized_name=str(raw_tc.get("sanitized_name", tc_name)),
                input=raw_tc.get("input", {}),
                status="failed",
                error={"code": "function_not_available",
                       "message": f"Function {tc_name!r} is not available or not allowed"},
                finished_at=_iso(datetime.now(UTC)),
            )
            execution_results.append(tc)
            await _write_timeline(db, "agent.tool.denied", session_id=session_id,
                                  actor=provider.provider_name(), call_id=tc.call_id,
                                  function_name=tc_name,
                                  error_code="function_not_available")
            continue

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
    all_waiting = all(tc.status == "waiting_approval" for tc in execution_results)

    if not execution_results or all_succeeded:
        final_status = "succeeded"
    elif all_waiting:
        final_status = "waiting_approval"
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


async def agent_plan(
    db: AsyncSession,
    provider: AgentProvider,
    *,
    session_id: str,
    prompt: str,
    target_node_id: str,
    available_functions: list[AgentFunction],
    execution_mode: str = "auto",
    max_total_duration_sec: int = 300,
) -> dict:
    """Generate a MaintenancePlan from a maintenance prompt.

    The Agent analyzes the prompt and produces a structured plan
    with ordered steps. Returns the plan dict for the caller to
    create via the MaintenancePlan API.
    """
    from yequ.services.maintenance_service import create_plan

    # Debug: log prompt encoding
    log.info("agent plan prompt: len=%d has_utf8=%s preview=%s",
             len(prompt), any(ord(c) > 127 for c in prompt), repr(prompt[:100]))

    # Call provider to analyze the prompt — use concise tool-only output
    plan_prompt = (
        f"Task: {prompt}. "
        "Design an ordered maintenance plan. If the task says 'check and fix if needed', "
        "include BOTH a diagnostic/check step AND a repair step. "
        "The check step goes first. The repair step should depend on the check step. "
        "Repair steps that write/change state require approval. "
        "Return ONLY tool calls. One tool call = one step. "
        "Do NOT write explanations — just return the tool calls."
    )
    provider_result = await asyncio.wait_for(
        provider.invoke(
            plan_prompt,
            available_functions=available_functions,
            context={"session_id": session_id},
        ),
        timeout=45.0,
    )

    if not provider_result.success:
        return {
            "status": "failed",
            "error": {
                "code": provider_result.error_code or "provider_error",
                "message": provider_result.error_message or "Provider failed",
            },
        }

    # Parse tool calls into plan steps
    steps = []
    for tc in provider_result.tool_calls:
        func_name = tc.get("name", "")
        func_input = tc.get("input", {})
        func_meta = next((f for f in available_functions if f.name == func_name), None)

        step = {
            "function_name": func_name,
            "input": func_input,
            "continue_on_failure": False,
            "timeout_sec": func_meta.timeout_sec if func_meta else 30,
            "resource_keys": [],
        }

        # If write operation, compute resource keys
        if func_meta and func_meta.effect in ("write", "destructive"):
            from yequ.services.resource_lock_service import compute_resource_keys
            step["resource_keys"] = compute_resource_keys(
                func_name, target_node_id, func_input,
            )

        steps.append(step)

    # Create the plan
    plan = await create_plan(
        db,
        goal=prompt,
        actor_id=provider.provider_name(),
        target_node_id=target_node_id,
        steps=steps,
        session_id=session_id,
        risk="maintenance",
        max_total_duration_sec=max_total_duration_sec,
        execution_mode=execution_mode,
    )

    has_write = any(
        f for f in available_functions
        if f.name in {s["function_name"] for s in steps} and f.effect in ("write", "destructive")
    )
    return {
        "status": "waiting_approval" if has_write else "ready",
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "step_count": len(steps),
        "steps": [
            {"seq": i + 1, "function_name": s["function_name"], "input": s.get("input", {})}
            for i, s in enumerate(steps)
        ],
    }


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

    # -- L2 write operations without approval: create approval, return waiting_approval --
    if resolved.effect in ("write", "destructive") and not tc.input.get("approval_id"):
        # Extract resource_key_template from capability's resource_keys (first item if present)
        resource_key_template = resolved.resource_keys[0] if (resolved.resource_keys and len(resolved.resource_keys) > 0) else None
        approval = await create_approval(
            db, actor_id=actor_id, session_id=session_id,
            function_name=tc.name, target_node_id=resolved.node_id,
            input_data=tc.input, risk=resolved.risk, effect=resolved.effect,
            resource_keys=resolved.resource_keys if hasattr(resolved, 'resource_keys') else None,
            resource_key_template=resource_key_template,
        )
        await db.commit()
        tc.status = "waiting_approval"
        tc.target_node_id = resolved.node_id
        tc.policy = AgentToolPolicySnapshot(
            decision="ask", risk=resolved.risk, effect=resolved.effect,
            execution_mode=execution_mode,
        )
        tc.error = {"code": "approval_required", "message": "Write operation requires approval",
                    "details": {"approval_id": approval.approval_id}}
        tc.finished_at = _iso(datetime.now(UTC))
        await _write_timeline(db, "approval.requested", session_id=session_id,
                              actor=provider_name, call_id=tc.call_id,
                              function_name=tc.name, target_node_id=resolved.node_id,
                              invocation_id=approval.approval_id)
        return tc

    # -- If approval_id is provided in input, verify it --
    if resolved.effect in ("write", "destructive") and tc.input.get("approval_id"):
        try:
            approval = await verify_approval(
                db, tc.input["approval_id"],
                actor_id=actor_id, session_id=session_id,
                function_name=tc.name, target_node_id=resolved.node_id,
                input_data=tc.input,
            )
            await consume_approval(db, approval)
            await db.commit()
            await _write_timeline(db, "approval.consumed", session_id=session_id,
                                  actor=provider_name, call_id=tc.call_id,
                                  function_name=tc.name,
                                  invocation_id=approval.approval_id)
        except ValueError as e:
            tc.status = "failed"
            tc.error = {"code": "policy_denied", "message": str(e)}
            tc.finished_at = _iso(datetime.now(UTC))
            await _write_timeline(db, "policy.denied", session_id=session_id,
                                  actor=provider_name, call_id=tc.call_id,
                                  function_name=tc.name, error=str(e)[:500],
                                  error_code="policy_denied")
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
    await db.commit()  # MUST commit before waiting — otherwise poll sessions see nothing

    tc.invocation_id = inv.invocation_id
    tc.job_ids = [job.job_id]

    # L2 action started timeline event
    if resolved.effect in ("write", "destructive"):
        await _write_timeline(db, "l2.action.started", session_id=session_id,
                              actor=provider_name, call_id=tc.call_id,
                              function_name=tc.name, invocation_id=inv.invocation_id,
                              job_id=job.job_id, target_node_id=resolved.node_id)

    # Verify persistence with a fresh session
    from yequ.db import async_session_factory as _asf
    async with _asf() as _vdb:
        from yequ.models.job import Job as _Job
        _jr = await _vdb.execute(select(_Job).where(_Job.job_id == job.job_id))
        _persisted = _jr.scalar_one_or_none() is not None
    await _write_timeline(db, "agent.tool.job.persisted", session_id=session_id,
                          actor=provider_name, call_id=tc.call_id,
                          function_name=tc.name, invocation_id=inv.invocation_id,
                          job_id=job.job_id, target_node_id=resolved.node_id,
                          success=_persisted)
    if not _persisted:
        tc.status = "failed"
        tc.error = {"code": "tool_failed", "message": "Job was not persisted"}
        tc.finished_at = _iso(datetime.now(UTC))
        return tc

    await _write_timeline(db, "agent.tool.invocation_created", session_id=session_id,
                          actor=provider_name, call_id=tc.call_id,
                          function_name=tc.name, invocation_id=inv.invocation_id,
                          job_id=job.job_id, target_node_id=resolved.node_id)

    # -- Wait for Invocation terminal --
    final_status = await _wait_invocation_terminal(inv.invocation_id, deadline)

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
        if resolved.effect in ("write", "destructive"):
            await _write_timeline(db, "l2.action.completed", session_id=session_id,
                                  actor=provider_name, call_id=tc.call_id,
                                  function_name=tc.name, status="succeeded",
                                  invocation_id=inv.invocation_id, job_id=job.job_id,
                                  target_node_id=resolved.node_id)
    elif final_status == "timeout":
        tc.status = "timeout"
        tc.error = {"code": "tool_timeout", "message": f"Tool {tc.name} timed out"}
        await _write_timeline(db, "agent.tool.failed", session_id=session_id,
                              actor=provider_name, call_id=tc.call_id,
                              function_name=tc.name, status="timeout",
                              invocation_id=inv.invocation_id, error_code="tool_timeout")
        if resolved.effect in ("write", "destructive"):
            await _write_timeline(db, "l2.action.failed", session_id=session_id,
                                  actor=provider_name, call_id=tc.call_id,
                                  function_name=tc.name, status="timeout",
                                  invocation_id=inv.invocation_id, job_id=job.job_id,
                                  target_node_id=resolved.node_id, error="tool_timeout")
    else:
        tc.status = "failed"
        tc.error = {"code": "tool_failed",
                     "message": f"Tool {tc.name} ended with {final_status}"}
        await _write_timeline(db, "agent.tool.failed", session_id=session_id,
                              actor=provider_name, call_id=tc.call_id,
                              function_name=tc.name, status=final_status,
                              invocation_id=inv.invocation_id, error_code="tool_failed")
        if resolved.effect in ("write", "destructive"):
            await _write_timeline(db, "l2.action.failed", session_id=session_id,
                                  actor=provider_name, call_id=tc.call_id,
                                  function_name=tc.name, status=final_status,
                                  invocation_id=inv.invocation_id, job_id=job.job_id,
                                  target_node_id=resolved.node_id, error=final_status)

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
            highlights.append(f"System metrics: CPU {r.get('cpu', '?')}%, Memory {r.get('memory', '?')}%")

        elif tc.name == "system.service.status" and tc.result:
            name = tc.result.get("name", tc.input.get("name", "?"))
            status = tc.result.get("status", "?")
            start_type = tc.result.get("start_type", "?")
            parts.append(f"Service '{name}': {status} (startup: {start_type})")
            highlights.append(f"Service {name}: {status}")

        elif tc.name == "system.processes.list" and tc.result:
            count = tc.result.get("count", 0)
            top = tc.result.get("processes", [])[:5]
            parts.append(f"{count} processes running")
            if top:
                names = [p.get("name", "?") for p in top]
                parts.append(f"Top: {', '.join(names)}")
                highlights.append(f"{count} processes, top: {', '.join(names[:3])}")

        elif tc.name == "system.eventlog.query" and tc.result:
            count = tc.result.get("total", 0)
            highest = tc.result.get("highest_level", "?")
            parts.append(f"{count} recent events (highest: {highest})")
            highlights.append(f"EventLog: {count} events, highest level: {highest}")

        elif tc.name == "system.disk.detail" and tc.result:
            drives = tc.result if isinstance(tc.result, list) else [tc.result]
            for d in drives:
                # Support multiple field naming conventions from Windows Node
                total = d.get("total_gb") or d.get("total_bytes") or d.get("size") or d.get("capacity") or "?"
                free = d.get("free_gb") or d.get("free_bytes") or d.get("free") or d.get("available") or "?"
                pct = d.get("used_percent") or d.get("usage_percent") or d.get("percent") or "?"
                drive = d.get("drive") or d.get("drive_letter") or d.get("mount") or d.get("device") or "?"
                if isinstance(total, (int, float)) and isinstance(free, (int, float)):
                    if isinstance(total, int) and total > 1024**3:
                        total = f"{total/1024**3:.1f}GB"
                        free = f"{free/1024**3:.1f}GB"
                    parts.append(f"Drive {drive}: {free}/{total} free ({pct}% used)")
                else:
                    parts.append(f"Drive {drive}: free={free} total={total}")
                highlights.append(f"Disk {drive}: {free} free of {total}")

        elif tc.name == "system.network.routes" and tc.result:
            routes = tc.result if isinstance(tc.result, list) else tc.result.get("routes", [tc.result])
            count = len(routes)
            default_route = None
            interfaces: set[str] = set()
            for r in routes:
                dest = str(r.get("destination") or r.get("network") or r.get("dest") or "")
                gw = str(r.get("gateway") or r.get("nexthop") or r.get("next_hop") or "")
                iface = str(r.get("interface") or r.get("iface") or r.get("interface_ip") or r.get("adapter") or "")
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
) -> None:
    """Write an agent timeline event using a fresh short-lived session.

    Uses its own DB session to avoid any lock contention with the
    request session or background writer. Commits immediately.
    """
    from sqlalchemy import func

    from yequ.db import async_session_factory
    from yequ.models.timeline import TimelineEvent

    async with async_session_factory() as _db:
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
        await _db.commit()
