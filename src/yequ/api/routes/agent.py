"""Agent API endpoints — session management and provider invocation."""

import json
from collections.abc import AsyncIterator
from typing import Any

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
from yequ.models.node import Node
from yequ.shared_types import JsonObject

router = APIRouter(prefix="/agent", tags=["agent"])

# -- In-memory provider registry --
_provider_registry: dict[str, AgentProvider] = {}


def register_provider(provider: AgentProvider) -> None:
    """Register an Agent Provider (for testing/setup)."""
    _provider_registry[provider.provider_name()] = provider


def get_provider(name: str) -> AgentProvider | None:
    """Get a registered provider by name."""
    return _provider_registry.get(name)


# -- Default available functions for testing only --
def _default_functions() -> list[AgentFunction]:
    """L1 + L2 functions available to the Agent.

    L2 write functions are included for planning/approval workflow.
    They will require approval before execution.
    """
    return [
        AgentFunction(
            name="system.metrics.snapshot",
            description=(
                "Get current CPU usage (%), memory usage (%), and disk usage (%) "
                "for the main drive. Use this when asked about system performance, "
                "load, or resource usage."
            ),
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.info",
            description=(
                "Get basic system information: OS name and version, hostname, "
                "uptime in seconds, and current user. Use this when asked about "
                "what machine this is, its OS, or how long it has been running."
            ),
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.service.status",
            description=(
                "Get the current status, startup mode, and display name of a named "
                "service on a node. Requires the exact service identifier in 'name'. "
                "Use this when asked about a specific service."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Service identifier as registered on the target node",
                    }
                },
                "required": ["name"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.processes.list",
            description=(
                "List running processes with name, PID, memory usage, and CPU time. "
                "Returns up to 50 processes sorted by memory. Use this when asked "
                "about running programs, what processes are active, or checking for "
                "specific processes."
            ),
            input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 50, "maximum": 100}},
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.disk.detail",
            description=(
                "Get detailed disk information for all drives: total capacity (GB), "
                "used space (GB), free space (GB), usage percentage, and filesystem "
                "type. Use this when asked about disk space, storage capacity, or "
                "drive details."
            ),
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.network.routes",
            description=(
                "Get the node network route table: destination network, netmask, "
                "gateway, interface IP, metric, and route type for each entry. Use "
                "this when asked about routing table, network routes, next hop, "
                "interface routes, or how network traffic is routed."
            ),
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.eventlog.query",
            description=(
                "Query recent node event log entries. Returns event count, severity "
                "levels, and recent event summaries. Accepts optional source and "
                "limit parameters. Use this when asked about system errors, recent "
                "warnings, or what happened on the machine."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": ["Application", "System"]},
                    "limit": {"type": "integer", "default": 50, "maximum": 100},
                },
            },
            risk="safe",
            effect="read",
            timeout_sec=10,
        ),
        # L2 maintenance write functions — require approval
        AgentFunction(
            name="system.service.ensure_running",
            description=(
                "Ensure a named service is running. If stopped, start it. Requires "
                "'name' parameter. Requires approval for write operations."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Service identifier as registered on the target node",
                    }
                },
                "required": ["name"],
            },
            risk="maintenance",
            effect="write",
            timeout_sec=30,
        ),
        AgentFunction(
            name="system.service.restart",
            description="Restart a named service. Requires 'name' parameter. Requires approval.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Service identifier as registered on the target node",
                    }
                },
                "required": ["name"],
            },
            risk="maintenance",
            effect="write",
            timeout_sec=30,
        ),
        # Test failure injection functions — for stable L2-C remote verification
        AgentFunction(
            name="test.maintenance.repair_fail",
            description=(
                "TEST ONLY: Simulates a failed repair step. Always fails with "
                "error_code=TEST_REPAIR_FAILED. Use to verify rollback_recommended "
                "flow."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Target service name for context",
                    }
                },
            },
            risk="maintenance",
            effect="write",
            timeout_sec=5,
        ),
        AgentFunction(
            name="test.maintenance.verify_fail",
            description=(
                "TEST ONLY: Simulates a failed verify step after a repair. Always "
                "fails with error_code=TEST_VERIFY_FAILED. Use to verify "
                "rollback_recommended flow."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Target service name for context",
                    }
                },
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
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
    suppress_user_message: bool = Field(default=False)
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
    target_node_id: str | None = Field(default=None)
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
    """Build the agent function list from DB capabilities on schedulable nodes.

    In production, Agent tools must reflect actual Node registrations. Test
    mode keeps the built-in defaults so isolated provider tests can exercise
    planning/streaming without provisioning a node fixture.
    """
    from sqlalchemy.orm import joinedload

    from yequ.config import get_settings
    from yequ.services.node_liveness_service import is_node_schedulable

    settings = get_settings()
    available = _default_functions() if settings.test_mode else []
    existing = {f.name for f in available}

    cap_result = await db.execute(
        select(Capability)
        .where(
            Capability.capability_type == "function",
            Capability.is_active == True,  # noqa: E712
        )
        .options(joinedload(Capability.node))
    )
    for cap in cap_result.unique().scalars().all():
        if cap.name in existing:
            continue
        # Skip capabilities on non-schedulable nodes
        if cap.node is None:
            continue
        schedulable, _ = is_node_schedulable(cap.node, settings)
        if not schedulable:
            continue
        available.append(_agent_function_from_capability(cap))
        existing.add(cap.name)
    return available


async def _default_target_node_id(db: AsyncSession) -> str:
    """Pick a schedulable target node without embedding a platform default."""
    from yequ.config import get_settings
    from yequ.services.node_liveness_service import is_node_schedulable

    settings = get_settings()
    result = await db.execute(
        select(Node).order_by(
            Node.last_heartbeat_at.desc().nullslast(),
            Node.node_id.asc(),
        )
    )
    for node in result.scalars().all():
        schedulable, _ = is_node_schedulable(node, settings)
        if schedulable:
            return node.node_id
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "no_schedulable_node",
            "message": "No schedulable node is available for this plan.",
        },
    )


INTERNAL_TOOL_INPUT_FIELDS = {"approval_id", "dry_run"}


def _agent_function_from_capability(cap: Capability) -> AgentFunction:
    """Convert a registered Node capability into the LLM-visible tool contract.

    Node manifests may carry operational fields that are required by Center or
    the Node daemon but should not be chosen or narrated by the LLM. The Agent
    sees a stable operator-facing contract; Center injects internal fields when
    it executes approvals/preflight.
    """
    hidden_fields = set(cap.hidden_input_fields or []) | INTERNAL_TOOL_INPUT_FIELDS
    return AgentFunction(
        name=cap.name,
        description=_capability_agent_description(cap),
        input_schema=_strip_internal_input_fields(cap.input_schema or {}, hidden_fields),
        risk=cap.risk or "safe",
        effect=cap.effect or "read",
        timeout_sec=cap.timeout_sec or 30,
        output_schema=cap.output_schema,
    )


def _capability_agent_description(cap: Capability) -> str:
    description = (cap.agent_description or cap.description or "").strip()
    if description:
        return description

    effect = cap.effect or "read"
    risk = cap.risk or "safe"
    context = cap.execution_context or "node"
    approval_note = (
        " This operation changes system state and Center will ask the user for "
        "approval before execution."
        if effect in ("write", "destructive")
        or risk in ("maintenance", "destructive", "catastrophic")
        else ""
    )
    return (
        f"{cap.name} provided by node plugin {cap.plugin_id}. "
        f"Execution context: {context}. Effect: {effect}. Risk: {risk}."
        f"{approval_note}"
    )


def _strip_internal_input_fields(
    schema: dict[str, object],
    hidden_fields: set[str],
) -> dict[str, object]:
    if not schema or not hidden_fields:
        return dict(schema)

    cleaned = dict(schema)
    properties = cleaned.get("properties")
    if isinstance(properties, dict):
        cleaned["properties"] = {
            key: value for key, value in properties.items() if key not in hidden_fields
        }

    required = cleaned.get("required")
    if isinstance(required, list):
        cleaned["required"] = [
            key for key in required if not isinstance(key, str) or key not in hidden_fields
        ]

    return cleaned


def _sse_response(
    event_source: AsyncIterator[JsonObject],
    turn_context: dict[str, Any] | None = None,
) -> StreamingResponse:
    async def event_generator() -> AsyncIterator[str]:
        turn_id: str | None = None
        async for event in event_source:
            if turn_context is not None:
                from yequ.services.agent_turn_service import (
                    create_agent_turn,
                    record_agent_turn_event,
                )

                event = dict(event)
                data = dict(event.get("data") or {})
                if turn_id is None:
                    turn_id = await create_agent_turn(
                        session_id=turn_context["session_id"],
                        prompt=turn_context["prompt"],
                        provider_name=turn_context["provider_name"],
                        target_node_id=turn_context.get("target_node_id"),
                        execution_mode=turn_context["execution_mode"],
                        trace_id=str(event.get("trace_id") or ""),
                        metadata=turn_context.get("metadata"),
                    )
                event["turn_id"] = turn_id
                data["turn_id"] = turn_id
                event["data"] = data
                await record_agent_turn_event(turn_id, event)
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
) -> StreamingResponse:
    provider = await _resolve_provider(body.provider_name)
    available = await _available_functions(db)
    # Release the route-level session before entering the long-lived SSE stream.
    # The stream creates its own short-lived sessions internally so no single
    # connection is held across LLM calls or job polling.
    await db.close()
    return _sse_response(
        agent_invoke_stream(
            provider,
            session_id=body.session_id,
            prompt=body.prompt,
            target_node_id=body.target_node_id,
            suppress_user_message=body.suppress_user_message,
            available_functions=available,
            call_path=body.call_path,
            max_depth=body.max_depth,
            max_steps=body.max_steps,
            max_total_duration_sec=body.max_total_duration_sec,
            step_count=body.step_count,
            execution_mode=body.execution_mode,
        ),
        turn_context={
            "session_id": body.session_id,
            "prompt": body.prompt,
            "provider_name": provider.provider_name(),
            "target_node_id": body.target_node_id,
            "execution_mode": body.execution_mode,
            "metadata": {
                "suppress_user_message": body.suppress_user_message,
                "step_count": body.step_count,
            },
        },
    )


@router.post("/plan")
async def agent_plan_endpoint(
    body: AgentPlanRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_agent_token),
) -> JsonObject:
    provider = await _resolve_provider(body.provider_name)

    target_node_id = body.target_node_id or await _default_target_node_id(db)
    return await agent_plan(
        db,
        provider,
        session_id=body.session_id,
        prompt=body.prompt,
        target_node_id=target_node_id,
        available_functions=await _available_functions(db),
        execution_mode=body.execution_mode,
        max_total_duration_sec=body.max_total_duration_sec,
    )


@router.post("/plan/stream")
async def agent_plan_stream_endpoint(
    body: AgentPlanRequest,
    db: AsyncSession = Depends(get_db),
    _token: dict[str, str] = Depends(get_agent_token),
) -> StreamingResponse:
    provider = await _resolve_provider(body.provider_name)
    target_node_id = body.target_node_id or await _default_target_node_id(db)
    available = await _available_functions(db)
    # Release route-level session before SSE stream (same pattern as invoke/stream)
    await db.close()
    return _sse_response(
        agent_plan_stream(
            provider,
            session_id=body.session_id,
            prompt=body.prompt,
            target_node_id=target_node_id,
            available_functions=available,
            execution_mode=body.execution_mode,
            max_total_duration_sec=body.max_total_duration_sec,
        )
    )
