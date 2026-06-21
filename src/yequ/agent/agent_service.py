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

import json as _json

from yequ.agent.provider import AgentFunction, AgentMessage, AgentProvider
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
    target_node_id: str | None = None,
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

    # -- Step 2: Load conversation history --
    history = await _load_session_history(db, session_id)

    # Build the messages array: system prompt is injected by the provider
    # Append the current user message
    history.append(AgentMessage(role="user", content=prompt))

    # -- Step 3: ReAct loop --
    log.info("agent loop started: provider=%s session_id=%s",
             provider.provider_name(), session_id)

    all_tool_calls: list[AgentToolCall] = []
    final_provider_message = ""
    loop_state = "running"
    total_usage: dict[str, object] = {}
    current_step = step_count
    provider_error: AgentInvokeError | None = None

    import time as _time

    while current_step < max_steps:
        # Duration guard
        now = datetime.now(UTC)
        elapsed = (now - started_at).total_seconds()
        if elapsed > max_total_duration_sec:
            loop_state = "timeout"
            break

        current_step += 1

        # -- Call Provider --
        log.info("agent loop iteration: step=%d/%d session_id=%s",
                 current_step, max_steps, session_id)

        await _write_timeline(db, "agent.provider.started", session_id=session_id,
                              actor=provider.provider_name(), step=current_step)

        _t0 = _time.monotonic()
        try:
            provider_result = await asyncio.wait_for(
                provider.invoke(
                    "",
                    available_functions=available_functions,
                    messages=history,
                    context={"call_path": list(call_path),
                             "session_id": session_id,
                             "step": current_step},
                ),
                timeout=45.0,
            )
            _elapsed = _time.monotonic() - _t0
            log.info("agent provider request returned: step=%d elapsed=%.1fs",
                     current_step, _elapsed)
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

        await _write_timeline(db, "agent.provider.completed", session_id=session_id,
                              actor=provider.provider_name(), success=True,
                              tool_call_count=len(provider_result.tool_calls),
                              step=current_step)

        # Accumulate usage
        if provider_result.usage:
            total_usage = provider_result.usage
            total_usage["tool_calls"] = total_usage.get("tool_calls", 0) + len(provider_result.tool_calls)

        # -- No tool calls? This is the final answer --
        if not provider_result.tool_calls:
            final_provider_message = provider_result.message or ""
            loop_state = "completed"
            await _write_timeline(db, "agent.provider.completed", session_id=session_id,
                                  actor=provider.provider_name(), success=True, final=True)
            break

        # -- Append assistant message with tool_calls --
        history.append(AgentMessage(
            role="assistant",
            content=provider_result.message or "",
            tool_calls=provider_result.tool_calls,
        ))

        # -- Validate + execute each tool call --
        iteration_results: list[AgentToolCall] = []
        for raw_tc in provider_result.tool_calls:
            tc_name = str(raw_tc.get("name", ""))
            tc_call_id = str(raw_tc.get("call_id", _make_call_id()))
            tc_input = raw_tc.get("input", {})

            # L1 check + loop detection + policy check (same as before)
            if tc_name not in known_functions:
                tc = AgentToolCall(
                    call_id=tc_call_id, name=tc_name,
                    sanitized_name=str(raw_tc.get("sanitized_name", tc_name)),
                    input=tc_input, status="failed",
                    error={"code": "function_not_available",
                           "message": f"Function {tc_name!r} is not available"},
                    finished_at=_iso(datetime.now(UTC)),
                )
                iteration_results.append(tc)
                history.append(AgentMessage(
                    role="tool", tool_call_id=tc_call_id,
                    content=_json.dumps({"status": "failed", "error": tc.error}),
                ))
                continue

            if tc_name in call_path:
                tc = AgentToolCall(
                    call_id=tc_call_id, name=tc_name, sanitized_name=tc_name,
                    input=tc_input, status="failed",
                    error={"code": ErrorCode.CIRCULAR_DEPENDENCY,
                           "message": f"Circular: {tc_name!r}"},
                    started_at=_iso(now), finished_at=_iso(datetime.now(UTC)),
                )
                iteration_results.append(tc)
                history.append(AgentMessage(
                    role="tool", tool_call_id=tc_call_id,
                    content=_json.dumps({"status": "failed", "error": tc.error}),
                ))
                continue

            func_meta = next((f for f in available_functions if f.name == tc_name), None)
            risk = func_meta.risk if func_meta else RiskLevel.SAFE

            from yequ.services.policy import check_policy
            policy_r = check_policy(execution_mode=execution_mode, risk_level=risk, function_name=tc_name)
            if not policy_r.allowed:
                tc = AgentToolCall(
                    call_id=tc_call_id, name=tc_name, sanitized_name=tc_name,
                    input=tc_input,
                    policy=AgentToolPolicySnapshot(
                        decision=policy_r.decision, risk=risk,
                        effect=func_meta.effect if func_meta else "read",
                        execution_mode=execution_mode,
                    ),
                    status="failed",
                    error={"code": ErrorCode.POLICY_DENIED, "message": policy_r.reason or "Policy denied"},
                    started_at=_iso(now), finished_at=_iso(datetime.now(UTC)),
                )
                iteration_results.append(tc)
                history.append(AgentMessage(
                    role="tool", tool_call_id=tc_call_id,
                    content=_json.dumps({"status": "denied", "error": tc.error}),
                ))
                continue

            # Execute through pipeline
            tc = AgentToolCall(
                call_id=tc_call_id, name=tc_name,
                sanitized_name=str(raw_tc.get("sanitized_name", tc_name)),
                input=tc_input,
            )
            executed = await _execute_tool_call(
                db, tc, session_id=session_id, actor_id=session.actor_id,
                execution_mode=execution_mode, call_path=list(call_path),
                max_depth=max_depth, deadline=deadline,
                provider_name=provider.provider_name(),
                requested_node_id=target_node_id,
            )
            iteration_results.append(executed)

            # Suspend loop if approval is required
            if executed.status == "waiting_approval":
                loop_state = "waiting_approval"
                all_tool_calls.extend(iteration_results)
                await _save_session_history(db, session_id, history)
                await db.commit()
                return _build_loop_response(
                    provider=provider, session_id=session_id,
                    tool_calls=all_tool_calls, trace_id=trace_id,
                    call_path=call_path, step_count=current_step,
                    max_depth=max_depth, max_steps=max_steps,
                    max_total_duration_sec=max_total_duration_sec,
                    status="waiting_approval", usage=total_usage,
                )

            # Append tool observation
            obs_content = _json.dumps({
                "status": executed.status,
                "result": executed.result,
                "error": executed.error,
            })
            history.append(AgentMessage(
                role="tool", tool_call_id=tc_call_id, content=obs_content,
            ))

        all_tool_calls.extend(iteration_results)

        # After observing, guard checks
        now_check = datetime.now(UTC)
        if (now_check - started_at).total_seconds() > max_total_duration_sec:
            loop_state = "timeout"
            break

    # -- Step 4: Fallback synthesis if no final answer --
    if not final_provider_message:
        final_provider_message = _fallback_synthesis(all_tool_calls, loop_state)

    # -- Step 5: Final status --
    any_waiting = any(tc.status == "waiting_approval" for tc in all_tool_calls)
    if any_waiting:
        final_status = "waiting_approval"
    elif loop_state == "timeout":
        final_status = "timeout"
    elif loop_state == "max_steps_reached":
        final_status = "max_steps_reached"
    elif loop_state in ("provider_failed", "provider_timeout"):
        final_status = "failed"
    else:
        all_succeeded = all(tc.status == "succeeded" for tc in all_tool_calls) if all_tool_calls else True
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
    history.append(AgentMessage(role="assistant", content=final_provider_message))
    await _save_session_history(db, session_id, history)

    await _write_timeline(db, "agent.final_response", session_id=session_id,
                          actor=provider.provider_name(), status=final_status,
                          tool_call_count=len(all_tool_calls))
    await db.commit()

    return _build_loop_response(
        provider=provider, session_id=session_id, output=output,
        tool_calls=all_tool_calls, trace_id=trace_id,
        call_path=call_path, step_count=current_step,
        max_depth=max_depth, max_steps=max_steps,
        max_total_duration_sec=max_total_duration_sec,
        status=final_status, usage=total_usage,
        error=provider_error,
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
    """Generate a structured MaintenancePlan IR from a maintenance prompt.

    Strategy:
    1. Call DeepSeek to classify intent: readonly_check | check_and_fix
    2. Build deterministic check->repair->verify steps based on intent
    3. Validate, store, return plan
    """
    from yequ.services.maintenance_service import create_plan

    # ── Step 1: Intent classification via provider ──
    func_names = [f.name for f in available_functions]
    classification_prompt = (
        f"User request: {prompt}\n"
        f"Available functions: {', '.join(func_names)}\n"
        "Classify this request as EXACTLY ONE of:\n"
        "- readonly_check: just check status, no repair needed\n"
        "- check_and_fix: check status AND repair/fix if unhealthy\n"
        "Respond with ONLY the classification word, nothing else."
    )

    log.info("agent plan prompt: len=%d has_utf8=%s preview=%s",
             len(prompt), any(ord(c) > 127 for c in prompt), repr(prompt[:100]))

    intent = "readonly_check"  # default
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
        pass  # provider unavailable -> readonly_check

    # ── Step 2: Parse the function + service name from prompt ──
    service_name = _extract_service_name(prompt, available_functions)
    function_name = _extract_function_for_service(prompt, available_functions)

    # ── Step 3: Build IR steps based on intent ──
    steps_ir = []

    if intent == "check_and_fix":
        # Check
        steps_ir.append({
            "seq": 1, "kind": "check",
            "function_name": function_name,
            "input": {"name": service_name},
            "condition": "always", "depends_on": [],
            "risk": "readonly", "requires_approval": False,
        })
        # Repair
        repair_func = "system.service.ensure_running"
        rollback_hint = _build_rollback_hint(service_name, repair_func, available_functions)
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
        # Verify
        steps_ir.append({
            "seq": 3, "kind": "verify",
            "function_name": function_name,
            "input": {"name": service_name},
            "condition": "after_repair", "depends_on": [2],
            "risk": "readonly", "requires_approval": False,
        })
    else:
        # Readonly check only
        steps_ir.append({
            "seq": 1, "kind": "check",
            "function_name": function_name,
            "input": {"name": service_name},
            "condition": "always", "depends_on": [],
            "risk": "readonly", "requires_approval": False,
        })

    # ── Step 4: Validate ──
    has_write = any(s["requires_approval"] for s in steps_ir)
    for s in steps_ir:
        if s["requires_approval"] and s["kind"] not in ("repair", "rollback"):
            s["requires_approval"] = False  # fix incorrect metadata
        func_exists = any(f.name == s["function_name"] for f in available_functions)
        if not func_exists:
            return {"status": "failed", "error": {"code": "function_not_available",
                     "message": f"Function {s['function_name']!r} not registered on node"}}

    # ── Step 5: Create plan ──
    plan = await create_plan(
        db, goal=prompt, actor_id=provider.provider_name(),
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
        session_id=session_id, risk="maintenance" if has_write else "safe",
        max_total_duration_sec=max_total_duration_sec,
        execution_mode=execution_mode,
    )

    # Set status + approval
    if has_write:
        plan.status = "waiting_approval"
        await db.commit()

    return {
        "status": plan.status,
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "approval_required": has_write,
        "step_count": len(steps_ir),
        "steps": steps_ir,
    }


def _extract_service_name(prompt: str, available_functions: list) -> str:
    """Extract service name from prompt. Maps common names to Windows service names."""
    mapping = {
        "print": "Spooler", "spooler": "Spooler", "打印": "Spooler",
        "eventlog": "EventLog", "event log": "EventLog", "event": "EventLog",
        "time": "W32Time", "w32time": "W32Time",
        "firewall": "MpsSvc", "windows firewall": "MpsSvc",
        "defender": "WinDefend", "windows defender": "WinDefend",
        "update": "wuauserv", "windows update": "wuauserv",
    }
    prompt_lower = prompt.lower()
    for key, svc in mapping.items():
        if key in prompt_lower:
            return svc
    return "Spooler"  # default


def _build_rollback_hint(
    service_name: str,
    function_name: str,
    available_functions: list,
) -> dict:
    """Build a rollback hint for a repair/write step.

    Returns a structured rollback hint dict. If the service name
    can be determined, uses the standard Windows service rollback.
    Otherwise returns a conservative manual_review hint.
    """
    # Known service name → standard Windows service rollback
    if service_name:
        return {
            "action": "restore_service_state",
            "target_type": "windows_service",
            "service_name": service_name,
            "rollback_function": "system.service.ensure_state",
            "requires_approval": True,
        }
    # Unknown target → conservative
    return {
        "action": "manual_review",
        "reason": "rollback_target_unknown",
        "requires_approval": True,
    }


def _extract_function_for_service(prompt: str, available_functions: list) -> str:
    """Determine the appropriate check function."""
    if any("service" in f.name for f in available_functions):
        return "system.service.status"
    return "system.metrics.snapshot"  # fallback


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
    requested_node_id: str | None = None,
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
    from yequ.config import get_settings
    resolved = await resolve_target_node(
        db,
        tc.name,
        requested_node_id=requested_node_id,
        settings=get_settings(),
    )
    if resolved is None:
        tc.status = "failed"
        tc.error = {"code": ErrorCode.FUNCTION_NOT_AVAILABLE,
                     "message": f"No online node has {tc.name!r}"}
        tc.finished_at = _iso(datetime.now(UTC))
        await _write_timeline(db, "agent.tool.denied", session_id=session_id,
                              actor=provider_name, call_id=tc.call_id,
                              function_name=tc.name, error_code=ErrorCode.FUNCTION_NOT_AVAILABLE)
        return tc

    # Check if the resolved capability is actually available (node liveness)
    if not resolved.available:
        tc.status = "failed"
        tc.error = {
            "code": "NODE_UNAVAILABLE",
            "message": f"Node {resolved.node_id} is not schedulable: {resolved.unavailable_reason or 'unknown'}",
        }
        tc.finished_at = _iso(datetime.now(UTC))
        await _write_timeline(db, "agent.tool.denied", session_id=session_id,
                              actor=provider_name, call_id=tc.call_id,
                              function_name=tc.name,
                              error_code="NODE_UNAVAILABLE",
                              target_node_id=resolved.node_id)
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
    await db.commit()

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


# ── Session history management ──


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
            role=m.role, content=m.content,
            tool_call_id=m.tool_call_id, tool_calls=m.tool_calls,
            message_id=m.message_id,
        )
        for m in rows
    ]


async def _save_session_history(
    db: AsyncSession, session_id: str, messages: list[AgentMessage],
) -> None:
    """Persist new messages to session history.

    Reads existing message_ids, only inserts messages not yet saved.
    """
    from yequ.models.agent_message import AgentMessage as AgentMessageModel

    existing_ids_result = await db.execute(
        select(AgentMessageModel.message_id)
        .where(AgentMessageModel.session_id == session_id)
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
        db.add(AgentMessageModel(
            message_id=mid, session_id=session_id,
            role=m.role, content=m.content,
            tool_call_id=m.tool_call_id, tool_calls=m.tool_calls,
            created_at=now,
        ))
        new_count += 1
        now = datetime.now(UTC)  # slight offset per message for ordering

    if new_count > 0:
        await db.flush()

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


# ── Fallback synthesis ──


def _fallback_synthesis(
    tool_calls: list[AgentToolCall], loop_state: str,
) -> str:
    """Produce a human-readable summary from tool results.

    Used when the LLM fails to produce a final answer or max steps reached.
    Always returns a non-empty string.
    """
    if not tool_calls:
        return "No tools were executed."

    parts: list[str] = []
    for tc in tool_calls:
        name = tc.name
        if tc.status == "succeeded":
            parts.append(f"- {name}: succeeded")
            if tc.result:
                # Extract a brief summary
                keys = list(tc.result.keys())[:3]
                summary_parts = []
                for k in keys:
                    v = tc.result.get(k)
                    if isinstance(v, (str, int, float, bool)):
                        summary_parts.append(f"{k}={v}")
                if summary_parts:
                    parts.append(f"  ({', '.join(summary_parts)})")
        elif tc.status in ("failed", "denied"):
            err = tc.error or {}
            parts.append(f"- {name}: failed — {err.get('message', tc.status)}")
        elif tc.status == "waiting_approval":
            parts.append(f"- {name}: requires approval")
        else:
            parts.append(f"- {name}: {tc.status}")

    header = f"Results ({len(tool_calls)} tools, {loop_state}):"
    return header + "\n" + "\n".join(parts)


# ── Loop response builder ──


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
        output = AgentInvokeOutput(message="No output generated.")

    if error is None and status not in ("succeeded", "waiting_approval"):
        first_failed = next((tc for tc in tool_calls if tc.status != "succeeded"), None)
        if first_failed and first_failed.error:
            error = AgentInvokeError(**first_failed.error)

    return AgentInvokeResponse(
        success=(status in ("succeeded", "waiting_approval")),
        status=status,
        provider_name=provider.provider_name(),
        session_id=session_id,
        output=output,
        error=error,
        tool_calls=tool_calls,
        usage=AgentInvokeUsage(
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            tool_calls=len(tool_calls),
        ),
        trace=AgentInvokeTrace(
            trace_id=trace_id, call_path=list(call_path),
            step_count=step_count, max_depth=max_depth,
            max_steps=max_steps, max_total_duration_sec=max_total_duration_sec,
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
    """Collect succeeded tool results into a name→result map."""
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
