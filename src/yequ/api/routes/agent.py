"""Agent API endpoints — session management and provider invocation."""

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import yequ.db as yequ_db
from yequ.agent.agent_service import agent_invoke, agent_plan, create_agent_session
from yequ.agent.agent_stream import agent_invoke_stream, agent_plan_stream
from yequ.agent.context_engine import JsonDict, build_capability_context
from yequ.agent.fake_provider import FakeAgentProvider
from yequ.agent.provider import AgentFunction, AgentProvider
from yequ.agent.tool_execution import AgentInvokeResponse
from yequ.api.deps import get_agent_token
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
def _center_meta_functions() -> list[AgentFunction]:
    """Stable Center meta tools for capability discovery."""
    return [
        AgentFunction(
            name="node.list",
            description="List known nodes, current status, platform, and capability counts.",
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="node.status",
            description="Inspect one node and its registered capability sources.",
            input_schema={
                "type": "object",
                "properties": {"node_id": {"type": "string"}},
                "required": ["node_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="capability.search",
            description=(
                "Search Center capability definitions by task, platform, node, risk, "
                "or effect. Returns compact candidates and source IDs."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "node_id": {"type": "string"},
                    "platform_os": {"type": "string"},
                    "effect": {"type": "string"},
                    "risk": {"type": "string"},
                    "capability_type": {"type": "string", "default": "function"},
                    "limit": {"type": "integer", "default": 20, "maximum": 50},
                },
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="capability.describe",
            description=(
                "Describe one Center capability definition, including schemas, "
                "constraints, and concrete node sources."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "capability_ref": {"type": "string"},
                    "node_id": {"type": "string"},
                },
                "required": ["capability_ref"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="capability.invoke",
            description=(
                "Invoke one concrete Center capability source. Use source_id from "
                "capability.search or capability.describe when more than one source "
                "exists. Put the real capability arguments in input."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "capability_ref": {
                        "type": "string",
                        "description": "Capability ID, canonical name, alias, or registered name",
                    },
                    "source_id": {
                        "type": "string",
                        "description": "Concrete capability source ID",
                    },
                    "node_id": {
                        "type": "string",
                        "description": "Optional node filter when using capability_ref",
                    },
                    "input": {
                        "type": "object",
                        "description": "Arguments passed to the concrete Node capability",
                    },
                },
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="artifact.list",
            description=(
                "List Center-managed artifacts such as images, screenshots, files, "
                "reports, and exports. Use this to find existing media before "
                "presenting it."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "invocation_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "node_id": {"type": "string"},
                    "artifact_type": {"type": "string"},
                    "limit": {"type": "integer", "default": 20, "maximum": 50},
                },
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="artifact.get",
            description="Inspect one Center-managed artifact by artifact_id.",
            input_schema={
                "type": "object",
                "properties": {"artifact_id": {"type": "string"}},
                "required": ["artifact_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="artifact.present",
            description=(
                "Present one or more existing Center artifacts in the Console chat UI. "
                "Use this when the user asks to show, display, preview, or open an "
                "image/media/file artifact. This does not visually understand images."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "artifact_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                    },
                },
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="operation.status",
            description=(
                "Inspect one Center Operation and its referenced domain state. "
                "Use this for wait handles returned by long-running operations."
            ),
            input_schema={
                "type": "object",
                "properties": {"operation_id": {"type": "string"}},
                "required": ["operation_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="operation.cancel",
            description="Cancel one waitable Center Operation if it supports cancellation.",
            input_schema={
                "type": "object",
                "properties": {
                    "operation_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["operation_id"],
            },
            risk="maintenance",
            effect="write",
            timeout_sec=5,
        ),
        AgentFunction(
            name="transfer.create",
            description=(
                "Create a Center-managed croc TransferSession between two nodes. "
                "Use this instead of directly calling low-level croc send/receive; "
                "Center will start receiver and sender jobs concurrently."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "source_node_id": {"type": "string"},
                    "target_node_id": {"type": "string"},
                    "source_path": {"type": "string"},
                    "target_output_dir": {"type": "string"},
                    "target_path": {"type": "string"},
                    "resume_mode": {
                        "type": "string",
                        "enum": ["resume", "overwrite", "fail_if_exists"],
                        "default": "resume",
                    },
                    "timeout_sec": {"type": "integer", "default": 3600},
                    "expected_sha256": {"type": "string"},
                },
                "required": ["source_node_id", "target_node_id", "source_path"],
            },
            risk="maintenance",
            effect="external",
            timeout_sec=5,
        ),
        AgentFunction(
            name="transfer.status",
            description="Inspect one Center-managed TransferSession and its sender/receiver jobs.",
            input_schema={
                "type": "object",
                "properties": {"transfer_id": {"type": "string"}},
                "required": ["transfer_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="transfer.cancel",
            description="Cancel one Center-managed TransferSession and its non-terminal jobs.",
            input_schema={
                "type": "object",
                "properties": {
                    "transfer_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["transfer_id"],
            },
            risk="maintenance",
            effect="write",
            timeout_sec=5,
        ),
    ]


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


class ResumeOperationRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    provider_name: str = Field(default="deepseek")
    operation_id: str = Field(..., min_length=1)
    target_node_id: str | None = Field(default=None)
    execution_mode: str = Field(default="auto")
    max_depth: int = Field(default=5, ge=1, le=20)
    max_steps: int = Field(default=20, ge=1, le=100)
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


async def _available_functions(
    db: AsyncSession,
    *,
    target_node_id: str | None = None,
) -> list[AgentFunction]:
    """Build the Agent tool list.

    Production exposes only stable Center meta tools. Raw Node capabilities
    remain in Center's registry and are reached through capability.search,
    capability.describe, and capability.invoke.

    Test mode keeps legacy/default tools so older isolated provider tests can
    exercise planning/streaming without provisioning a Node fixture.
    """
    from sqlalchemy.orm import joinedload

    from yequ.config import get_settings
    from yequ.services.node_liveness_service import is_node_schedulable

    settings = get_settings()
    available = _center_meta_functions()
    if not settings.test_mode:
        return available

    if settings.test_mode:
        available.extend(_default_functions())
    existing = {f.name: f for f in available}

    cap_result = await db.execute(
        select(Capability)
        .where(
            Capability.capability_type == "function",
            Capability.is_active == True,  # noqa: E712
        )
        .options(joinedload(Capability.node))
    )
    for cap in cap_result.unique().scalars().all():
        # Skip capabilities on non-schedulable nodes
        if cap.node is None:
            continue
        if target_node_id and not settings.test_mode and cap.node.node_id != target_node_id:
            continue
        schedulable, _ = is_node_schedulable(cap.node, settings)
        if not schedulable:
            continue
        if cap.name in existing:
            if cap.node.node_id not in existing[cap.name].source_nodes:
                existing[cap.name].source_nodes.append(cap.node.node_id)
            continue
        available.append(_agent_function_from_capability(cap))
        existing[cap.name] = available[-1]
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
        source_nodes=[cap.node.node_id] if cap.node else [],
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


def _provider_system_prompt(
    provider: AgentProvider,
    functions: list[AgentFunction],
    capability_context: JsonDict | None = None,
) -> str:
    prompt_builder = getattr(provider, "debug_system_prompt", None)
    if not callable(prompt_builder):
        return ""
    try:
        value = prompt_builder(
            functions,
            context={"capability_context": capability_context} if capability_context else None,
        )
    except TypeError:
        value = prompt_builder(functions)
    return value if isinstance(value, str) else str(value)


def _agent_debug_metadata(
    provider: AgentProvider,
    *,
    available_functions: list[AgentFunction],
    target_node_id: str | None,
    execution_mode: str,
    capability_context: JsonDict | None = None,
) -> dict[str, object]:
    return {
        "system_prompt": _provider_system_prompt(
            provider, available_functions, capability_context
        ),
        "target_node_id": target_node_id,
        "execution_mode": execution_mode,
        "routing_mode": (
            str(capability_context.get("routing_mode"))
            if capability_context
            else ("pinned" if target_node_id else "auto")
        ),
        "capability_context": capability_context or {},
        "nodes": capability_context.get("nodes", []) if capability_context else [],
        "tool_count_by_node": (
            capability_context.get("tool_count_by_node", {}) if capability_context else {}
        ),
        "available_functions": [_function_debug_summary(f) for f in available_functions],
    }


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
    _token: dict[str, str] = Depends(get_agent_token),
) -> dict[str, object]:
    """Create an Agent Session.

    Returns session metadata including constraint parameters
    that will be enforced during agent invocation.
    """
    return await create_agent_session(
        actor_id=body.actor_id,
        execution_mode=body.execution_mode,
        max_depth=body.max_depth,
        max_steps=body.max_steps,
        max_total_duration_sec=body.max_total_duration_sec,
    )


@router.post("/invoke", response_model=AgentInvokeResponse)
async def invoke_agent_endpoint(
    body: InvokeAgentRequest,
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
    async with yequ_db.async_session_factory() as db:
        available = await _available_functions(db, target_node_id=body.target_node_id)
        capability_context = await build_capability_context(
            db,
            available_functions=available,
            target_node_id=body.target_node_id,
        )

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
            context={"capability_context": capability_context},
        )

    resp.metadata["prompt_context"] = _agent_debug_metadata(
        provider,
        available_functions=available,
        target_node_id=body.target_node_id,
        execution_mode=body.execution_mode,
        capability_context=capability_context,
    )

    return resp


@router.post("/invoke/stream")
async def invoke_agent_stream_endpoint(
    body: InvokeAgentRequest,
    _token: dict[str, str] = Depends(get_agent_token),
) -> StreamingResponse:
    provider = await _resolve_provider(body.provider_name)
    async with yequ_db.async_session_factory() as db:
        available = await _available_functions(db, target_node_id=body.target_node_id)
        capability_context = await build_capability_context(
            db,
            available_functions=available,
            target_node_id=body.target_node_id,
        )
    return _sse_response(
        agent_invoke_stream(
            provider,
            session_id=body.session_id,
            prompt=body.prompt,
            target_node_id=body.target_node_id,
            suppress_user_message=body.suppress_user_message,
            available_functions=available,
            capability_context=capability_context,
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
                "prompt_context": _agent_debug_metadata(
                    provider,
                    available_functions=available,
                    target_node_id=body.target_node_id,
                    execution_mode=body.execution_mode,
                    capability_context=capability_context,
                ),
            },
        },
    )


@router.post("/resume-operation/stream")
async def resume_operation_stream_endpoint(
    body: ResumeOperationRequest,
    _token: dict[str, str] = Depends(get_agent_token),
) -> StreamingResponse:
    provider = await _resolve_provider(body.provider_name)
    async with yequ_db.async_session_factory() as db:
        from yequ.services.operation_service import OperationService

        operation_observation = await OperationService(db).status(body.operation_id)
        await _record_operation_resume_checkpoint(
            db,
            session_id=body.session_id,
            provider_name=provider.provider_name(),
            target_node_id=body.target_node_id,
            execution_mode=body.execution_mode,
            operation_id=body.operation_id,
            operation_observation=operation_observation,
        )
        available = await _available_functions(db, target_node_id=body.target_node_id)
        capability_context = await build_capability_context(
            db,
            available_functions=available,
            target_node_id=body.target_node_id,
        )

    prompt = (
        "INFO: Center operation observation follows. Use these facts to continue "
        "the previous task. Do not invent fields that are not present.\n"
        f"{json.dumps(operation_observation, ensure_ascii=False)}"
    )
    return _sse_response(
        agent_invoke_stream(
            provider,
            session_id=body.session_id,
            prompt=prompt,
            target_node_id=body.target_node_id,
            suppress_user_message=True,
            available_functions=available,
            capability_context=capability_context,
            call_path=[],
            max_depth=body.max_depth,
            max_steps=body.max_steps,
            max_total_duration_sec=body.max_total_duration_sec,
            step_count=0,
            execution_mode=body.execution_mode,
        ),
        turn_context={
            "session_id": body.session_id,
            "prompt": prompt,
            "provider_name": provider.provider_name(),
            "target_node_id": body.target_node_id,
            "execution_mode": body.execution_mode,
            "metadata": {
                "suppress_user_message": True,
                "operation_id": body.operation_id,
                "operation_observation": operation_observation,
                "prompt_context": _agent_debug_metadata(
                    provider,
                    available_functions=available,
                    target_node_id=body.target_node_id,
                    execution_mode=body.execution_mode,
                    capability_context=capability_context,
                ),
            },
        },
    )


async def _record_operation_resume_checkpoint(
    db: AsyncSession,
    *,
    session_id: str,
    provider_name: str,
    target_node_id: str | None,
    execution_mode: str,
    operation_id: str,
    operation_observation: dict[str, object],
) -> None:
    from datetime import UTC, datetime

    from yequ.models.agent_run import AgentRun, AgentRunStep

    now = datetime.now(UTC)
    run = AgentRun(
        session_id=session_id,
        provider_name=provider_name,
        status="observing",
        execution_mode=execution_mode,
        target_node_id=target_node_id,
        user_message=None,
        started_at=now,
        metadata_json={
            "source": "resume_operation",
            "operation_id": operation_id,
        },
    )
    db.add(run)
    await db.flush()
    db.add(
        AgentRunStep(
            run_record_id=run.id,
            step_index=1,
            step_type="operation_observation",
            status="succeeded",
            input_data={"operation_id": operation_id},
            output_data=operation_observation,
            started_at=now,
            completed_at=now,
            metadata_json={"source": "operation.status"},
        )
    )
    await db.commit()


@router.post("/plan")
async def agent_plan_endpoint(
    body: AgentPlanRequest,
    _token: dict[str, str] = Depends(get_agent_token),
) -> JsonObject:
    provider = await _resolve_provider(body.provider_name)

    async with yequ_db.async_session_factory() as db:
        target_node_id = body.target_node_id or await _default_target_node_id(db)
        available = await _available_functions(db, target_node_id=target_node_id)
        capability_context = await build_capability_context(
            db,
            available_functions=available,
            target_node_id=target_node_id,
        )
        plan = await agent_plan(
            provider,
            session_id=body.session_id,
            prompt=body.prompt,
            target_node_id=target_node_id,
            available_functions=available,
            context={"capability_context": capability_context},
            execution_mode=body.execution_mode,
            max_total_duration_sec=body.max_total_duration_sec,
        )
        return plan


@router.post("/plan/stream")
async def agent_plan_stream_endpoint(
    body: AgentPlanRequest,
    _token: dict[str, str] = Depends(get_agent_token),
) -> StreamingResponse:
    provider = await _resolve_provider(body.provider_name)
    async with yequ_db.async_session_factory() as db:
        target_node_id = body.target_node_id or await _default_target_node_id(db)
        available = await _available_functions(db, target_node_id=target_node_id)
        capability_context = await build_capability_context(
            db,
            available_functions=available,
            target_node_id=target_node_id,
        )
    return _sse_response(
        agent_plan_stream(
            provider,
            session_id=body.session_id,
            prompt=body.prompt,
            target_node_id=target_node_id,
            available_functions=available,
            capability_context=capability_context,
            execution_mode=body.execution_mode,
            max_total_duration_sec=body.max_total_duration_sec,
        ),
        turn_context={
            "session_id": body.session_id,
            "prompt": body.prompt,
            "provider_name": provider.provider_name(),
            "target_node_id": target_node_id,
            "execution_mode": body.execution_mode,
            "metadata": {
                "prompt_context": _agent_debug_metadata(
                    provider,
                    available_functions=available,
                    target_node_id=target_node_id,
                    execution_mode=body.execution_mode,
                    capability_context=capability_context,
                ),
            },
        },
    )
