"""Agent API endpoints — session management and provider invocation."""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.agent_service import agent_invoke, agent_plan, create_agent_session
from yequ.agent.agent_stream import agent_invoke_stream, agent_plan_stream
from yequ.agent.fake_provider import FakeAgentProvider
from yequ.agent.provider import AgentFunction, AgentProvider
from yequ.agent.tool_execution import AgentInvokeResponse
from yequ.api.deps import get_agent_token, get_db
from yequ.models.capability import Capability

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
    """L1 + L2 functions available to the Agent.

    L2 write functions are included for planning/approval workflow.
    They will require approval before execution.
    """
    return [
        AgentFunction(
            name="system.metrics.snapshot",
            description="Get current CPU usage (%), memory usage (%), and disk usage (%) for the main drive. Use this when asked about system performance, load, or resource usage.",
            input_schema={"type": "object", "properties": {}},
            risk="safe", effect="read", timeout_sec=5,
        ),
        AgentFunction(
            name="system.info",
            description="Get basic system information: OS name and version, hostname, uptime in seconds, and current user. Use this when asked about what machine this is, its OS, or how long it has been running.",
            input_schema={"type": "object", "properties": {}},
            risk="safe", effect="read", timeout_sec=5,
        ),
        AgentFunction(
            name="system.service.status",
            description="Get the current status (running/stopped), startup type (auto/manual/disabled), and display name of a specific Windows service. Requires 'name' parameter -- the service name like 'Spooler', 'EventLog', 'W32Time', 'lanmanserver'. Use this when asked about a specific service.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Windows service name, e.g. Spooler, EventLog, W32Time"}},
                "required": ["name"],
            },
            risk="safe", effect="read", timeout_sec=5,
        ),
        AgentFunction(
            name="system.processes.list",
            description="List running processes with name, PID, memory usage, and CPU time. Returns up to 50 processes sorted by memory. Use this when asked about running programs, what processes are active, or checking for specific processes.",
            input_schema={"type": "object", "properties": {"limit": {"type": "integer", "default": 50, "maximum": 100}}},
            risk="safe", effect="read", timeout_sec=5,
        ),
        AgentFunction(
            name="system.disk.detail",
            description="Get detailed disk information for all drives: total capacity (GB), used space (GB), free space (GB), usage percentage, and filesystem type. Use this when asked about disk space, storage capacity, or drive details.",
            input_schema={"type": "object", "properties": {}},
            risk="safe", effect="read", timeout_sec=5,
        ),
        AgentFunction(
            name="system.network.routes",
            description="Get the Windows network route table: destination network, netmask, gateway, interface IP, metric, and route type for each entry. Use this when asked about routing table, network routes, next hop, interface routes, or how network traffic is routed.",
            input_schema={"type": "object", "properties": {}},
            risk="safe", effect="read", timeout_sec=5,
        ),
        AgentFunction(
            name="system.eventlog.query",
            description="Query recent Windows Event Log entries. Returns event count, severity levels (Error/Warning/Information), and recent event summaries. Accepts optional 'source' parameter ('Application' or 'System', default: both) and 'limit' (default: 50). Use this when asked about system errors, recent warnings, or what happened on the machine.",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": ["Application", "System"]},
                    "limit": {"type": "integer", "default": 50, "maximum": 100},
                },
            },
            risk="safe", effect="read", timeout_sec=10,
        ),
        # L2 maintenance write functions — require approval
        AgentFunction(
            name="system.service.ensure_running",
            description="Ensure a Windows service is running. If stopped, start it. Requires 'name' parameter. REQUIRES APPROVAL for write operations.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Windows service name"}},
                "required": ["name"],
            },
            risk="maintenance", effect="write", timeout_sec=30,
        ),
        AgentFunction(
            name="system.service.restart",
            description="Restart a Windows service. Requires 'name' parameter. REQUIRES APPROVAL.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Windows service name"}},
                "required": ["name"],
            },
            risk="maintenance", effect="write", timeout_sec=30,
        ),
        # Test failure injection functions — for stable L2-C remote verification
        AgentFunction(
            name="test.maintenance.repair_fail",
            description="TEST ONLY: Simulates a failed repair step. Always fails with error_code=TEST_REPAIR_FAILED. Use to verify rollback_recommended flow.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Target service name for context"}},
            },
            risk="maintenance", effect="write", timeout_sec=5,
        ),
        AgentFunction(
            name="test.maintenance.verify_fail",
            description="TEST ONLY: Simulates a failed verify step after a repair. Always fails with error_code=TEST_VERIFY_FAILED. Use to verify rollback_recommended flow.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Target service name for context"}},
            },
            risk="safe", effect="read", timeout_sec=5,
        ),
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
    target_node_id: str | None = Field(default=None)
    call_path: list[str] = Field(default_factory=list)
    step_count: int = Field(default=0, ge=0)
    execution_mode: str = Field(default="auto")
    max_depth: int = Field(default=5, ge=1, le=20)
    max_steps: int = Field(default=20, ge=1, le=100)
    max_total_duration_sec: int = Field(default=300, ge=1, le=3600)


class AgentPlanRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    provider_name: str = Field(default="deepseek")
    prompt: str = Field(..., min_length=1)
    target_node_id: str = Field(default="winClient")
    execution_mode: str = Field(default="auto")
    max_total_duration_sec: int = Field(default=300, ge=1, le=3600)


# -- Endpoints --

async def _resolve_provider(provider_name: str) -> AgentProvider:
    provider = get_provider(provider_name)
    if provider is not None:
        return provider
    if provider_name == "fake":
        provider = FakeAgentProvider()
        for func in _default_functions():
            provider.add_function(func)
        register_provider(provider)
        return provider
    if provider_name == "deepseek":
        from yequ.agent.deepseek_provider import DeepSeekProvider

        provider = DeepSeekProvider()
        register_provider(provider)
        return provider
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Provider {provider_name!r} not found",
    )


async def _available_functions(db: AsyncSession) -> list[AgentFunction]:
    available = _default_functions()
    existing = {f.name for f in available}
    cap_result = await db.execute(
        select(Capability).where(
            Capability.capability_type == "function",
            Capability.is_active == True,  # noqa: E712
        )
    )
    for cap in cap_result.scalars().all():
        if cap.name in existing:
            continue
        available.append(AgentFunction(
            name=cap.name,
            description=f"Node capability: {cap.name} ({cap.plugin_id})",
            input_schema=cap.input_schema or {},
            risk=cap.risk or "safe",
            effect=cap.effect or "read",
            timeout_sec=cap.timeout_sec or 30,
            output_schema=cap.output_schema,
        ))
        existing.add(cap.name)
    return available


def _sse_response(event_source):
    async def event_generator():
        async for event in event_source:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
    provider = await _resolve_provider(body.provider_name)
    available = await _available_functions(db)

    resp = await agent_invoke(
        db,
        provider,
        session_id=body.session_id,
        prompt=body.prompt,
        available_functions=available,
        call_path=body.call_path,
        max_depth=body.max_depth,
        max_steps=body.max_steps,
        max_total_duration_sec=body.max_total_duration_sec,
        step_count=body.step_count,
        execution_mode=body.execution_mode,
        target_node_id=body.target_node_id,
    )

    return resp


@router.post("/invoke/stream")
async def invoke_agent_stream_endpoint(
    body: InvokeAgentRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_agent_token),
):
    provider = await _resolve_provider(body.provider_name)
    available = await _available_functions(db)
    return _sse_response(agent_invoke_stream(
        db,
        provider,
        session_id=body.session_id,
        prompt=body.prompt,
        target_node_id=body.target_node_id,
        available_functions=available,
        call_path=body.call_path,
        max_depth=body.max_depth,
        max_steps=body.max_steps,
        max_total_duration_sec=body.max_total_duration_sec,
        step_count=body.step_count,
        execution_mode=body.execution_mode,
    ))


@router.post("/plan")
async def agent_plan_endpoint(
    body: AgentPlanRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_agent_token),
) -> dict:
    provider = await _resolve_provider(body.provider_name)

    return await agent_plan(
        db, provider,
        session_id=body.session_id,
        prompt=body.prompt,
        target_node_id=body.target_node_id,
        available_functions=_default_functions(),
        execution_mode=body.execution_mode,
        max_total_duration_sec=body.max_total_duration_sec,
    )


@router.post("/plan/stream")
async def agent_plan_stream_endpoint(
    body: AgentPlanRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_agent_token),
):
    provider = await _resolve_provider(body.provider_name)
    return _sse_response(agent_plan_stream(
        db,
        provider,
        session_id=body.session_id,
        prompt=body.prompt,
        target_node_id=body.target_node_id,
        available_functions=_default_functions(),
        execution_mode=body.execution_mode,
        max_total_duration_sec=body.max_total_duration_sec,
    ))
