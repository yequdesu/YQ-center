# YeQu Center Stage 4: Agent Basic Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the first version of YeQu Agent integration — Agent as Actor through Center's Invocation path, Policy enforcement, call graph constraints (depth/steps/duration), Agent Provider abstraction with FakeAgentProvider for testing, and comprehensive tests.

**Architecture:** Agent → AgentService → Policy → Invocation/Job creation → Node execution. Agent never calls Node directly. A Policy engine checks execution mode vs risk before every call. Call graph constraints (call_path, max_depth, max_steps, max_total_duration) are enforced at the AgentService layer. AgentProvider is an ABC with FakeAgentProvider for testing.

**Tech Stack:** Python 3.11, Pydantic v2, SQLAlchemy 2 (async), FastAPI, pytest

---

## File Map

| File | Responsibility |
|---|---|
| `src/yequ/services/policy.py` | Policy engine — risk/mode matrix, allow/deny/ask decisions |
| `src/yequ/agent/__init__.py` | Agent package init |
| `src/yequ/agent/provider.py` | AgentProvider abstract base class |
| `src/yequ/agent/fake_provider.py` | FakeAgentProvider — canned responses for testing |
| `src/yequ/agent/agent_service.py` | Agent orchestration — session, invoke, constraints |
| `src/yequ/api/routes/agent.py` | Agent API — POST /agent/invoke |
| `src/yequ/api/app.py` | **Modify:** include agent router |
| `src/yequ/api/deps.py` | **Modify:** add get_api_token dependency (if needed) |
| `tests/test_agent.py` | Agent tests — call chain, loop detection, timeout |

---

### Task 1: Policy Engine

**Files:**
- Create: `src/yequ/services/policy.py`

Stage 4 requirement: "Agent 调用必须经过 Policy，不允许直接访问 Node 或 Plugin"

The Policy engine checks whether a given (execution_mode, risk_level, function_name) combination is allowed.

Risk/mode matrix from YeQu-Architecture-Design.md:

| mode \ risk | safe | maintenance | destructive | catastrophic |
|---|---:|---:|---:|---:|
| auto | allow | allow | ask | deny |
| assist | allow | conditional | ask | deny |
| readonly | allow | deny | deny | deny |
| manual | allow | ask | ask | deny |

```python
# src/yequ/services/policy.py
"""Policy engine — controls what actions an Actor can perform.

Agent calls MUST pass through Policy before becoming Invocations.
Policy checks execution mode vs function risk level.
"""

from dataclasses import dataclass

from yequ.protocol import ExecutionMode, RiskLevel


class PolicyDecision:
    """Result of a policy check."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"  # requires human confirmation
    CONDITIONAL = "conditional"  # allowed if in whitelist


# (mode, risk) -> decision
POLICY_MATRIX: dict[tuple[ExecutionMode, RiskLevel], str] = {
    # auto mode
    (ExecutionMode.AUTO, RiskLevel.SAFE): PolicyDecision.ALLOW,
    (ExecutionMode.AUTO, RiskLevel.MAINTENANCE): PolicyDecision.ALLOW,
    (ExecutionMode.AUTO, RiskLevel.DESTRUCTIVE): PolicyDecision.ASK,
    (ExecutionMode.AUTO, RiskLevel.CATASTROPHIC): PolicyDecision.DENY,
    # assist mode
    (ExecutionMode.ASSIST, RiskLevel.SAFE): PolicyDecision.ALLOW,
    (ExecutionMode.ASSIST, RiskLevel.MAINTENANCE): PolicyDecision.CONDITIONAL,
    (ExecutionMode.ASSIST, RiskLevel.DESTRUCTIVE): PolicyDecision.ASK,
    (ExecutionMode.ASSIST, RiskLevel.CATASTROPHIC): PolicyDecision.DENY,
    # readonly mode
    (ExecutionMode.READONLY, RiskLevel.SAFE): PolicyDecision.ALLOW,
    (ExecutionMode.READONLY, RiskLevel.MAINTENANCE): PolicyDecision.DENY,
    (ExecutionMode.READONLY, RiskLevel.DESTRUCTIVE): PolicyDecision.DENY,
    (ExecutionMode.READONLY, RiskLevel.CATASTROPHIC): PolicyDecision.DENY,
    # manual mode
    (ExecutionMode.MANUAL, RiskLevel.SAFE): PolicyDecision.ALLOW,
    (ExecutionMode.MANUAL, RiskLevel.MAINTENANCE): PolicyDecision.ASK,
    (ExecutionMode.MANUAL, RiskLevel.DESTRUCTIVE): PolicyDecision.ASK,
    (ExecutionMode.MANUAL, RiskLevel.CATASTROPHIC): PolicyDecision.DENY,
}


@dataclass
class PolicyResult:
    allowed: bool
    decision: str
    reason: str | None = None


def check_policy(
    execution_mode: str,
    risk_level: str = RiskLevel.SAFE,
    function_name: str = "",
    whitelist: set[str] | None = None,
) -> PolicyResult:
    """Check if an action is allowed under the given execution mode and risk.

    Args:
        execution_mode: auto, assist, readonly, manual
        risk_level: safe, maintenance, destructive, catastrophic
        function_name: The function being called (for conditional checks)
        whitelist: Set of function names allowed in conditional mode

    Returns:
        PolicyResult with allowed=True/False and decision string
    """
    mode = ExecutionMode(execution_mode)
    risk = RiskLevel(risk_level)

    decision = POLICY_MATRIX.get((mode, risk), PolicyDecision.DENY)

    if decision == PolicyDecision.ALLOW:
        return PolicyResult(allowed=True, decision=decision)
    elif decision == PolicyDecision.DENY:
        return PolicyResult(
            allowed=False, decision=decision,
            reason=f"{risk} action denied in {execution_mode} mode",
        )
    elif decision == PolicyDecision.CONDITIONAL:
        # Only allowed if function is in the whitelist
        if whitelist and function_name in whitelist:
            return PolicyResult(allowed=True, decision=decision)
        return PolicyResult(
            allowed=False, decision=PolicyDecision.DENY,
            reason=f"{function_name} not in conditional whitelist",
        )
    elif decision == PolicyDecision.ASK:
        # Requires human confirmation — for now, deny in automated context
        return PolicyResult(
            allowed=False, decision=decision,
            reason=f"{risk} action requires human confirmation",
        )

    return PolicyResult(allowed=False, decision=decision, reason="unknown")
```

---

### Task 2: Agent Provider Interface + FakeAgentProvider

**Files:**
- Create: `src/yequ/agent/__init__.py`
- Create: `src/yequ/agent/provider.py`
- Create: `src/yequ/agent/fake_provider.py`

```python
# src/yequ/agent/__init__.py
"""YeQu Agent — LLM-driven actor integration."""
```

```python
# src/yequ/agent/provider.py
"""Agent Provider abstract interface.

An Agent Provider wraps an LLM backend and exposes a standard
invocation interface to the Center. Agent Providers never call
Nodes or Plugins directly — all execution goes through Center.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AgentFunction:
    """Metadata about a function the Agent can call."""
    name: str
    description: str = ""
    input_schema: dict | None = None
    output_schema: dict | None = None
    risk: str = "safe"
    effect: str = "read"
    timeout_sec: int = 30


@dataclass
class AgentResult:
    """Result of an Agent Provider invocation."""
    success: bool
    output: dict | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    function_calls: list[dict] = field(default_factory=list)
    # function_calls: list of {"name": str, "input": dict} the agent wants to make


class AgentProvider(ABC):
    """Abstract base for all Agent Providers (LLM backends).

    Each Provider exposes:
    - A list of functions it can reason about
    - An invoke() method that takes a user prompt and returns
      structured output including any function calls to make
    """

    @abstractmethod
    async def invoke(
        self,
        prompt: str,
        *,
        available_functions: list[AgentFunction],
        context: dict | None = None,
    ) -> AgentResult:
        """Invoke the agent with a prompt and available functions.

        The provider reasons about the prompt, decides which functions
        to call, and returns structured output with function_calls.
        """
        ...

    @abstractmethod
    def list_functions(self) -> list[AgentFunction]:
        """Return the functions this provider can reason about."""
        ...

    @abstractmethod
    def provider_name(self) -> str:
        """Unique provider identifier."""
        ...
```

```python
# src/yequ/agent/fake_provider.py
"""FakeAgentProvider — returns canned responses for testing.

Does NOT connect to a real LLM. Used for integration testing
the Agent->Center pipeline without external dependencies.
"""

from yequ.agent.provider import AgentFunction, AgentProvider, AgentResult


class FakeAgentProvider(AgentProvider):
    """Test provider that returns pre-configured responses.

    Configure with add_response() to set up expected behavior.
    Unmatched prompts return a default success with no function calls.
    """

    def __init__(self, provider_name: str = "fake") -> None:
        self._name = provider_name
        self._responses: dict[str, AgentResult] = {}
        self._functions: list[AgentFunction] = []
        self._invoke_count = 0
        self._default_result = AgentResult(
            success=True,
            output={"message": "default fake response"},
            function_calls=[],
        )

    @property
    def invoke_count(self) -> int:
        return self._invoke_count

    def add_response(self, prompt_contains: str, result: AgentResult) -> None:
        """Register a response for prompts containing the given string."""
        self._responses[prompt_contains] = result

    def set_default_result(self, result: AgentResult) -> None:
        """Set the default result for unmatched prompts."""
        self._default_result = result

    def add_function(self, func: AgentFunction) -> None:
        """Register a function this provider can reason about."""
        self._functions.append(func)

    def add_functions(self, funcs: list[AgentFunction]) -> None:
        """Register multiple functions."""
        self._functions.extend(funcs)

    async def invoke(
        self,
        prompt: str,
        *,
        available_functions: list[AgentFunction],
        context: dict | None = None,
    ) -> AgentResult:
        """Return a canned response based on the prompt.

        Matches prompt against registered response patterns.
        Falls back to default_result if no match.
        """
        self._invoke_count += 1

        for pattern, result in self._responses.items():
            if pattern in prompt:
                return result

        return self._default_result

    def list_functions(self) -> list[AgentFunction]:
        return list(self._functions)

    def provider_name(self) -> str:
        return self._name
```

---

### Task 3: Agent Service

**Files:**
- Create: `src/yequ/agent/agent_service.py`

Agent 作为 Actor，通过 Center 发起 Invocation。每一步调用都写 TimelineEvent。约束 call_path, max_depth, max_steps, max_total_duration。循环检测。Policy 检查。

```python
# src/yequ/agent/agent_service.py
"""Agent Service — orchestrates Agent interactions through Center.

The Agent calls Center's standard Invocation path. Every agent step
is recorded as a TimelineEvent. Call graph constraints are enforced.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.provider import AgentFunction, AgentProvider, AgentResult
from yequ.protocol import ErrorCode, InvocationStatus, JobStatus, RiskLevel
from yequ.protocol.errors import YqpError
from yequ.services.job_state_machine import is_terminal
from yequ.services.policy import check_policy


def _make_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:16]}"


async def create_agent_session(
    db: AsyncSession,
    *,
    actor_id: str,
    execution_mode: str = "auto",
    max_depth: int = 5,
    max_steps: int = 20,
    max_total_duration_sec: int = 300,
) -> dict:
    """Create an Agent Session.

    Returns session metadata for the agent to use in subsequent calls.
    """
    from yequ.models.session import Session

    session_id = _make_session_id()
    sess = Session(
        session_id=session_id,
        actor_type="agent",
        actor_id=actor_id,
        status="active",
        execution_mode=execution_mode,
        started_at=datetime.now(timezone.utc),
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
    call_path: list[str],
    max_depth: int = 5,
    max_steps: int = 20,
    max_total_duration_sec: int = 300,
    step_count: int = 0,
    started_at: datetime | None = None,
    execution_mode: str = "auto",
) -> AgentResult:
    """Invoke an Agent Provider through the Center's standard path.

    Enforces:
    - max_depth: current call_path depth must not exceed max_depth
    - max_steps: total agent steps must not exceed max_steps
    - max_total_duration: total elapsed time must not exceed limit
    - call_path loop detection: function must not already be in call_path
    - Policy: every function call checks policy before proceeding

    Each agent step writes a TimelineEvent.
    """
    from yequ.models.timeline import TimelineEvent

    now = datetime.now(timezone.utc)

    # Duration check
    if started_at is None:
        started_at = now
    elapsed = (now - started_at).total_seconds()
    if elapsed > max_total_duration_sec:
        return AgentResult(
            success=False,
            error_code=ErrorCode.MAX_DURATION_EXCEEDED,
            error_message=f"Exceeded max total duration {max_total_duration_sec}s (elapsed: {elapsed:.1f}s)",
            retryable=False,
        )

    # Step count check
    if step_count >= max_steps:
        return AgentResult(
            success=False,
            error_code=ErrorCode.MAX_STEPS_EXCEEDED,
            error_message=f"Exceeded max steps {max_steps}",
            retryable=False,
        )

    # Depth check
    if len(call_path) >= max_depth:
        return AgentResult(
            success=False,
            error_code=ErrorCode.CALL_DEPTH_EXCEEDED,
            error_message=f"Call depth {len(call_path)} exceeds max {max_depth}",
            retryable=False,
        )

    # Write step start event
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

    # Invoke the provider
    result = await provider.invoke(
        prompt,
        available_functions=available_functions,
        context={"call_path": call_path, "session_id": session_id},
    )

    # Validate function_calls against call_path for loop detection
    validated_calls = []
    for fc in result.function_calls:
        func_name = fc.get("name", "")
        func_input = fc.get("input", {})

        # Loop detection: function already in call_path
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
                error_message=f"Circular call detected: {func_name} already in call_path {call_path}",
                retryable=False,
            )

        # Find function metadata for policy check
        func_meta = next(
            (f for f in available_functions if f.name == func_name),
            None,
        )
        risk = func_meta.risk if func_meta else RiskLevel.SAFE

        # Policy check
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

    # Write step finished event
    event = TimelineEvent(
        event_type="agent.step.finished",
        actor_type="agent",
        actor_id=provider.provider_name(),
        session_id=session_id,
        data={
            "step": step_count + 1,
            "function_calls": [
                {"name": fc["name"], "input_summary": str(fc.get("input", {}))[:200]}
                for fc in validated_calls
            ],
            "output_summary": (
                str(result.output)[:500] if result.output else None
            ),
        },
        timestamp=now,
    )
    db.add(event)
    await db.flush()

    result.function_calls = validated_calls
    return result
```

---

### Task 4: Agent API Routes

**Files:**
- Create: `src/yequ/api/routes/agent.py`
- Modify: `src/yequ/api/app.py` — include agent router

```python
# src/yequ/api/routes/agent.py
"""Agent API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.agent_service import agent_invoke, create_agent_session
from yequ.agent.fake_provider import FakeAgentProvider
from yequ.agent.provider import AgentFunction, AgentProvider
from yequ.api.deps import get_db
from yequ.protocol import ErrorCode

router = APIRouter(prefix="/agent", tags=["agent"])

# In-memory provider registry for testing
_provider_registry: dict[str, AgentProvider] = {}


def register_provider(provider: AgentProvider) -> None:
    """Register an Agent Provider (for testing/setup)."""
    _provider_registry[provider.provider_name()] = provider


def get_provider(name: str) -> AgentProvider | None:
    """Get a registered provider by name."""
    return _provider_registry.get(name)


class CreateSessionRequest(BaseModel):
    actor_id: str = Field(default="agent")
    execution_mode: str = Field(default="auto")
    max_depth: int = Field(default=5, ge=1, le=20)
    max_steps: int = Field(default=20, ge=1, le=100)
    max_total_duration_sec: int = Field(default=300, ge=1, le=3600)


class InvokeAgentRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    provider_name: str = Field(default="fake")
    prompt: str = Field(..., min_length=1)
    call_path: list[str] = Field(default_factory=list)
    step_count: int = Field(default=0, ge=0)
    execution_mode: str = Field(default="auto")


class InvokeAgentResponse(BaseModel):
    success: bool
    output: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    function_calls: list[dict[str, object]] = Field(default_factory=list)


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create an Agent Session."""
    return await create_agent_session(
        db,
        actor_id=body.actor_id,
        execution_mode=body.execution_mode,
        max_depth=body.max_depth,
        max_steps=body.max_steps,
        max_total_duration_sec=body.max_total_duration_sec,
    )


@router.post("/invoke", response_model=InvokeAgentResponse)
async def invoke_agent(
    body: InvokeAgentRequest,
    db: AsyncSession = Depends(get_db),
) -> InvokeAgentResponse:
    """Invoke an Agent Provider with a prompt.

    The Agent reasons about the prompt and returns function_calls.
    Each call is checked against Policy and call graph constraints.
    """
    # Get or create the provider
    provider = get_provider(body.provider_name)
    if provider is None:
        # Auto-create a FakeAgentProvider for testing
        if body.provider_name == "fake":
            provider = FakeAgentProvider()
            register_provider(provider)
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider {body.provider_name!r} not found",
            )

    # Default available functions for testing
    test_functions = [
        AgentFunction(
            name="system.metrics.snapshot",
            description="Get system metrics",
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.diagnostic.run",
            description="Run system diagnostic",
            risk="maintenance",
            effect="read",
            timeout_sec=30,
        ),
    ]

    result = await agent_invoke(
        db,
        provider,
        session_id=body.session_id,
        prompt=body.prompt,
        available_functions=test_functions,
        call_path=body.call_path,
        max_depth=10,
        max_steps=body.step_count + 10,
        step_count=body.step_count,
        execution_mode=body.execution_mode,
    )

    return InvokeAgentResponse(
        success=result.success,
        output=result.output or {},
        error_code=result.error_code,
        error_message=result.error_message,
        retryable=result.retryable,
        function_calls=[dict(fc) for fc in result.function_calls],
    )
```

Also update app.py to include `agent_router`:

```python
from yequ.api.routes.agent import router as agent_router
# in create_app():
app.include_router(agent_router)
```

---

### Task 5: Agent Tests + Final Verification

**Files:**
- Create: `tests/test_agent.py`

Tests covering:
1. Policy engine — allow/deny/ask/conditional matrix
2. FakeAgentProvider — canned responses, function listing
3. Agent session creation
4. Agent invoke with function call
5. Call depth exceeded
6. Step count exceeded
7. Duration exceeded
8. Loop detection (circular call_path)
9. Policy denied (destructive in readonly mode)
10. TimelineEvent written for each step
```

---

## Stage 4 Exit Criteria

- [ ] Policy engine — risk/mode matrix, allow/deny/ask decisions
- [ ] AgentProvider abstract interface (ABC)
- [ ] FakeAgentProvider with canned responses
- [ ] Agent session creation
- [ ] Agent invoke through Center path (never directly to Node)
- [ ] call_path tracking, max_depth enforcement
- [ ] max_steps enforcement  
- [ ] max_total_duration enforcement
- [ ] Circular dependency (loop) detection
- [ ] Policy check before every function call
- [ ] TimelineEvent written for every agent step
- [ ] Agent API — POST /agent/sessions, POST /agent/invoke
- [ ] Provider failure returns standard YqpError structure
- [ ] No auto-retry of non-idempotent calls
- [ ] 10+ agent tests passing
