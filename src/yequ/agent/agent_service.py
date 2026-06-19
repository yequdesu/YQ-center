"""Agent Service — orchestrates Agent interactions through Center.

The Agent calls Center's standard Invocation path. Every agent step
is recorded as a TimelineEvent. Call graph constraints are enforced
(call_path, max_depth, max_steps, max_total_duration, loop detection).
Policy is checked before every function call.

Agent NEVER calls Nodes directly — all execution goes through Center.
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.provider import AgentFunction, AgentProvider, AgentResult
from yequ.protocol import ErrorCode, RiskLevel


def _make_session_id() -> str:
    """Generate a unique session ID."""
    return f"sess_{uuid.uuid4().hex[:16]}"


async def create_agent_session(
    db: AsyncSession,
    *,
    actor_id: str,
    execution_mode: str = "auto",
    max_depth: int = 5,
    max_steps: int = 20,
    max_total_duration_sec: int = 300,
) -> dict[str, object]:
    """Create an Agent Session.

    Returns session metadata including the constraint parameters
    that will be enforced during agent_invoke().
    """
    from yequ.models.session import Session

    session_id = _make_session_id()
    now = datetime.now(datetime.UTC)
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
) -> AgentResult:
    """Invoke an Agent Provider through the Center's standard path.

    Enforces:
    - max_depth: call_path length must not exceed max_depth
    - max_steps: step_count must not exceed max_steps
    - max_total_duration: elapsed time since started_at must not exceed limit
    - Loop detection: function must not already appear in call_path
    - Policy: every function call checked against execution mode + risk

    Each agent step writes a TimelineEvent (start + finish).
    Returns AgentResult with success/error and function_calls.
    If a constraint is violated, returns a standard error result.
    """
    from yequ.models.timeline import TimelineEvent
    from yequ.services.policy import check_policy

    if call_path is None:
        call_path = []

    now = datetime.now(datetime.UTC)
    if started_at is None:
        started_at = now

    # ── Duration check ──
    elapsed = (now - started_at).total_seconds()
    if elapsed > max_total_duration_sec:
        return AgentResult(
            success=False,
            error_code=ErrorCode.MAX_DURATION_EXCEEDED,
            error_message=(
                f"Exceeded max total duration {max_total_duration_sec}s "
                f"(elapsed: {elapsed:.1f}s)"
            ),
            retryable=False,
        )

    # ── Step count check ──
    if step_count >= max_steps:
        return AgentResult(
            success=False,
            error_code=ErrorCode.MAX_STEPS_EXCEEDED,
            error_message=f"Exceeded max steps {max_steps}",
            retryable=False,
        )

    # ── Depth check ──
    if len(call_path) >= max_depth:
        return AgentResult(
            success=False,
            error_code=ErrorCode.CALL_DEPTH_EXCEEDED,
            error_message=(
                f"Call depth {len(call_path)} exceeds max {max_depth}"
            ),
            retryable=False,
        )

    # ── Write step start event ──
    event = TimelineEvent(
        event_type="agent.step.started",
        actor_type="agent",
        actor_id=provider.provider_name(),
        session_id=session_id,
        data={
            "step": step_count + 1,
            "call_depth": len(call_path),
            "call_path": list(call_path),
            "prompt": prompt[:500],
        },
        timestamp=now,
    )
    db.add(event)
    await db.flush()

    # ── Invoke the provider ──
    result = await provider.invoke(
        prompt,
        available_functions=available_functions,
        context={
            "call_path": list(call_path),
            "session_id": session_id,
        },
    )

    # ── Validate function_calls ──
    validated_calls: list[dict[str, object]] = []
    for fc in result.function_calls:
        func_name = str(fc.get("name", ""))

        # Loop detection
        if func_name in call_path:
            event = TimelineEvent(
                event_type="agent.loop_detected",
                actor_type="agent",
                actor_id=provider.provider_name(),
                session_id=session_id,
                data={
                    "function": func_name,
                    "call_path": list(call_path),
                    "step": step_count + 1,
                },
                timestamp=now,
            )
            db.add(event)
            await db.flush()
            return AgentResult(
                success=False,
                error_code=ErrorCode.CIRCULAR_DEPENDENCY,
                error_message=(
                    f"Circular call: {func_name!r} already in "
                    f"call_path {call_path}"
                ),
                retryable=False,
            )

        # Policy check
        func_meta = next(
            (f for f in available_functions if f.name == func_name),
            None,
        )
        risk = func_meta.risk if func_meta else RiskLevel.SAFE

        policy_result = check_policy(
            execution_mode=execution_mode,
            risk_level=risk,
            function_name=func_name,
        )
        if not policy_result.allowed:
            event = TimelineEvent(
                event_type="agent.policy_denied",
                actor_type="agent",
                actor_id=provider.provider_name(),
                session_id=session_id,
                data={
                    "function": func_name,
                    "risk": risk,
                    "execution_mode": execution_mode,
                    "reason": policy_result.reason,
                },
                timestamp=now,
            )
            db.add(event)
            await db.flush()
            return AgentResult(
                success=False,
                error_code=ErrorCode.POLICY_DENIED,
                error_message=policy_result.reason or "Policy denied",
                retryable=False,
            )

        validated_calls.append(fc)

    # ── Write step finished event ──
    event = TimelineEvent(
        event_type="agent.step.finished",
        actor_type="agent",
        actor_id=provider.provider_name(),
        session_id=session_id,
        data={
            "step": step_count + 1,
            "function_calls": [
                {
                    "name": fc["name"],
                    "input_summary": str(fc.get("input", {}))[:200],
                }
                for fc in validated_calls
            ],
            "output_summary": (
                str(result.output)[:500] if result.output else None
            ),
            "success": result.success,
        },
        timestamp=now,
    )
    db.add(event)
    await db.flush()

    result.function_calls = validated_calls
    return result
