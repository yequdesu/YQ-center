"""Agent API endpoints — session management and provider invocation."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.agent_service import agent_invoke, create_agent_session
from yequ.agent.fake_provider import FakeAgentProvider
from yequ.agent.provider import AgentFunction, AgentProvider
from yequ.agent.tool_execution import AgentInvokeResponse
from yequ.api.deps import get_agent_token, get_db

router = APIRouter(prefix="/agent", tags=["agent"])

# -- In-memory provider registry --
_provider_registry: dict[str, AgentProvider] = {}


def register_provider(provider: AgentProvider) -> None:
    """Register an Agent Provider (for testing/setup)."""
    _provider_registry[provider.provider_name()] = provider


def get_provider(name: str) -> AgentProvider | None:
    """Get a registered provider by name."""
    return _provider_registry.get(name)


# -- Default available functions for testing --
def _default_functions() -> list[AgentFunction]:
    """L1 read-only functions available to the Agent."""
    return [
        AgentFunction(name="system.metrics.snapshot",
                      description="Get CPU/memory/disk usage percentages", risk="safe", effect="read", timeout_sec=5),
        AgentFunction(name="system.info",
                      description="Get OS, hostname, uptime, current user", risk="safe", effect="read", timeout_sec=5),
        AgentFunction(name="system.service.status",
                      description="Get a Windows service status by name (e.g. Spooler, EventLog)", risk="safe", effect="read", timeout_sec=5),
        AgentFunction(name="system.processes.list",
                      description="List running processes (limit 50)", risk="safe", effect="read", timeout_sec=5),
        AgentFunction(name="system.disk.detail",
                      description="Get disk capacity, used, free, filesystem per drive", risk="safe", effect="read", timeout_sec=5),
        AgentFunction(name="system.eventlog.query",
                      description="Query recent Windows Event Log entries (Application/System)", risk="safe", effect="read", timeout_sec=10),
    ]


# -- Request/Response models --

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
    max_depth: int = Field(default=5, ge=1, le=20)
    max_steps: int = Field(default=20, ge=1, le=100)
    max_total_duration_sec: int = Field(default=300, ge=1, le=3600)


# -- Endpoints --

@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session_endpoint(
    body: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_agent_token),
) -> dict[str, object]:
    """Create an Agent Session.

    Returns session metadata including constraint parameters
    that will be enforced during agent invocation.
    """
    return await create_agent_session(
        db,
        actor_id=body.actor_id,
        execution_mode=body.execution_mode,
        max_depth=body.max_depth,
        max_steps=body.max_steps,
        max_total_duration_sec=body.max_total_duration_sec,
    )


@router.post("/invoke", response_model=AgentInvokeResponse)
async def invoke_agent_endpoint(
    body: InvokeAgentRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_agent_token),
) -> AgentInvokeResponse:
    """Invoke an Agent Provider with a prompt.

    The Agent reasons about the prompt and returns function_calls.
    Each call is checked against:
    - Policy (execution mode + risk level)
    - Call graph constraints (depth, steps, duration, loops)

    Provider "fake" is auto-created if not registered.
    """
    provider = get_provider(body.provider_name)
    if provider is None:
        if body.provider_name == "fake":
            provider = FakeAgentProvider()
            # Pre-configure fake provider with useful defaults
            for func in _default_functions():
                provider.add_function(func)
            register_provider(provider)
        elif body.provider_name == "deepseek":
            from yequ.agent.deepseek_provider import DeepSeekProvider

            provider = DeepSeekProvider()
            register_provider(provider)
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider {body.provider_name!r} not found",
            )

    resp = await agent_invoke(
        db,
        provider,
        session_id=body.session_id,
        prompt=body.prompt,
        available_functions=_default_functions(),
        call_path=body.call_path,
        max_depth=body.max_depth,
        max_steps=body.max_steps,
        max_total_duration_sec=body.max_total_duration_sec,
        step_count=body.step_count,
        execution_mode=body.execution_mode,
    )

    return resp
