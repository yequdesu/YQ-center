"""Agent API endpoints — session management and provider invocation."""

import json
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

import yequ.db as yequ_db
from yequ.agent.agent_service import agent_plan, create_agent_session
from yequ.agent.agent_stream import agent_invoke_stream, agent_plan_stream
from yequ.agent.provider import AgentProvider
from yequ.api.agent_context import (
    _agent_run_resume_prompt,
    _context_block_summary,
    _load_agent_context_refs,
    _operation_observation_from_run_projection,
    _operation_resume_prompt,
    _prompt_with_context_refs,
    _record_operation_resume_checkpoint,
    _with_context_block_events,
)
from yequ.api.agent_providers import (
    get_provider as get_provider,
)
from yequ.api.agent_providers import (
    register_provider as register_provider,
)
from yequ.api.agent_providers import (
    resolve_provider,
)
from yequ.api.agent_schemas import (
    AgentPlanRequest,
    CreateSessionRequest,
    InvokeAgentRequest,
    MarkOperationNotificationFailedRequest,
    MarkOperationNotificationReportedRequest,
    ResumeAgentRunRequest,
    ResumeLastAgentRunRequest,
    ResumeOperationRequest,
)
from yequ.api.agent_tool_catalog import (
    _agent_debug_metadata,
    _available_functions,
    _default_target_node_id,
)
from yequ.api.agent_tool_catalog import (
    _agent_function_from_capability as _agent_function_from_capability,
)
from yequ.api.agent_tool_catalog import (
    _center_meta_functions as _center_meta_functions,
)
from yequ.api.deps import get_agent_token
from yequ.runtime.capability_context import build_capability_context
from yequ.services.session_audit import record_session_audit_event
from yequ.shared_types import JsonObject

router = APIRouter(prefix="/agent", tags=["agent"])

# -- Endpoints --


def _as_nested_str(value: object, *path: str) -> str | None:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) else None


async def _resolve_provider(provider_name: str) -> AgentProvider:
    return await resolve_provider(provider_name)


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


@router.get("/sessions/{session_id}/plan")
async def get_session_agent_plan_endpoint(
    session_id: str,
    _token: dict[str, str] = Depends(get_agent_token),
) -> dict[str, object]:
    """Return the latest generic Agent Runtime plan for a session."""
    from yequ.runtime.agent_plan_service import get_latest_agent_plan_for_session

    async with yequ_db.async_session_factory() as db:
        plan = await get_latest_agent_plan_for_session(db, session_id=session_id)
    return {"plan": plan}


@router.post("/invoke/stream")
async def invoke_agent_stream_endpoint(
    body: InvokeAgentRequest,
    _token: dict[str, str] = Depends(get_agent_token),
) -> StreamingResponse:
    record_session_audit_event(
        body.session_id,
        "agent.invoke.request_received",
        {
            "provider_name": body.provider_name,
            "target_node_id": body.target_node_id,
            "execution_mode": body.execution_mode,
            "prompt": body.prompt,
            "user_visible_prompt": body.user_visible_prompt,
            "suppress_user_message": body.suppress_user_message,
            "context_refs": [ref.model_dump() for ref in body.context_refs],
            "max_depth": body.max_depth,
            "max_steps": body.max_steps,
            "max_total_duration_sec": body.max_total_duration_sec,
            "step_count": body.step_count,
        },
        source="api.agent",
    )
    provider = await _resolve_provider(body.provider_name)
    record_session_audit_event(
        body.session_id,
        "agent.invoke.provider_resolved",
        {"provider_name": provider.provider_name()},
        source="api.agent",
    )
    context_load_started = perf_counter()
    async with yequ_db.async_session_factory() as db:
        refs_started = perf_counter()
        context_blocks = await _load_agent_context_refs(
            db,
            session_id=body.session_id,
            provider_name=provider.provider_name(),
            target_node_id=body.target_node_id,
            execution_mode=body.execution_mode,
            context_refs=body.context_refs,
        )
        refs_elapsed_ms = round((perf_counter() - refs_started) * 1000, 3)
        functions_started = perf_counter()
        available = await _available_functions(db, target_node_id=body.target_node_id)
        functions_elapsed_ms = round((perf_counter() - functions_started) * 1000, 3)
        capability_context_started = perf_counter()
        capability_context = await build_capability_context(
            db,
            available_functions=available,
            target_node_id=body.target_node_id,
        )
        capability_context_elapsed_ms = round(
            (perf_counter() - capability_context_started) * 1000,
            3,
        )
    context_load_elapsed_ms = round((perf_counter() - context_load_started) * 1000, 3)
    record_session_audit_event(
        body.session_id,
        "agent.invoke.context_loaded",
        {
            "context_block_count": len(context_blocks),
            "available_function_count": len(available),
            "elapsed_ms": context_load_elapsed_ms,
            "spans": {
                "context_refs_load_ms": refs_elapsed_ms,
                "available_functions_ms": functions_elapsed_ms,
                "capability_context_ms": capability_context_elapsed_ms,
            },
            "capability_context_nodes": capability_context.get("nodes", [])
            if isinstance(capability_context, dict)
            else [],
            "capability_context_snapshot": capability_context.get("snapshot", {})
            if isinstance(capability_context, dict)
            else {},
        },
        source="api.agent",
    )
    agent_prompt = await _prompt_with_context_refs(body.prompt, context_blocks)
    record_session_audit_event(
        body.session_id,
        "agent.invoke.prompt_ready",
        {
            "prompt_chars": len(body.prompt),
            "agent_prompt_chars": len(agent_prompt),
            "context_block_count": len(context_blocks),
        },
        source="api.agent",
    )
    return _sse_response(
        _with_context_block_events(
            agent_invoke_stream(
                provider,
                session_id=body.session_id,
                prompt=agent_prompt,
                user_visible_prompt=body.user_visible_prompt or body.prompt,
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
                run_metadata={
                    "context_refs": [ref.model_dump() for ref in body.context_refs],
                    "context_blocks": [_context_block_summary(block) for block in context_blocks],
                },
            ),
            context_blocks,
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
                "context_refs": [ref.model_dump() for ref in body.context_refs],
                "context_blocks": context_blocks,
                "user_visible_prompt": body.user_visible_prompt,
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


@router.get("/sessions/{session_id}/operation-notifications")
async def list_operation_notifications_endpoint(
    session_id: str,
    _token: dict[str, str] = Depends(get_agent_token),
) -> dict[str, object]:
    from yequ.services.agent_operation_notifications import (
        AgentOperationNotificationService,
    )

    async with yequ_db.async_session_factory() as db:
        notifications = await AgentOperationNotificationService(db).list_pending(
            session_id=session_id
        )
    return {"notifications": notifications}


@router.post("/sessions/{session_id}/operation-notifications/claim")
async def claim_operation_notification_endpoint(
    session_id: str,
    _token: dict[str, str] = Depends(get_agent_token),
) -> dict[str, object]:
    from yequ.services.agent_operation_notifications import (
        AgentOperationNotificationService,
    )

    async with yequ_db.async_session_factory() as db:
        notification = await AgentOperationNotificationService(db).claim_next(
            session_id=session_id
        )
        await db.commit()
    return {"notification": notification}


@router.post("/operation-notifications/{notification_id}/reported")
async def mark_operation_notification_reported_endpoint(
    notification_id: str,
    body: MarkOperationNotificationReportedRequest,
    _token: dict[str, str] = Depends(get_agent_token),
) -> dict[str, object]:
    from yequ.services.agent_operation_notifications import (
        AgentOperationNotificationService,
    )

    async with yequ_db.async_session_factory() as db:
        notification = await AgentOperationNotificationService(db).mark_reported(
            notification_id=notification_id,
            turn_id=body.turn_id,
        )
        await db.commit()
    return {"notification": notification}


@router.post("/operation-notifications/{notification_id}/failed")
async def mark_operation_notification_failed_endpoint(
    notification_id: str,
    body: MarkOperationNotificationFailedRequest,
    _token: dict[str, str] = Depends(get_agent_token),
) -> dict[str, object]:
    from yequ.services.agent_operation_notifications import (
        AgentOperationNotificationService,
    )

    async with yequ_db.async_session_factory() as db:
        notification = await AgentOperationNotificationService(db).mark_failed(
            notification_id=notification_id,
            error=body.error,
        )
        await db.commit()
    return {"notification": notification}


@router.post("/resume-operation/stream")
async def resume_operation_stream_endpoint(
    body: ResumeOperationRequest,
    _token: dict[str, str] = Depends(get_agent_token),
) -> StreamingResponse:
    record_session_audit_event(
        body.session_id,
        "agent.resume_operation.request_received",
        {
            "operation_id": body.operation_id,
            "provider_name": body.provider_name,
            "target_node_id": body.target_node_id,
            "execution_mode": body.execution_mode,
            "max_steps": body.max_steps,
            "max_total_duration_sec": body.max_total_duration_sec,
        },
        source="api.agent",
    )
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
    record_session_audit_event(
        body.session_id,
        "agent.resume_operation.context_loaded",
        {
            "operation_id": body.operation_id,
            "operation_status": _as_nested_str(operation_observation, "operation", "status"),
            "available_function_count": len(available),
        },
        source="api.agent",
    )

    user_message = body.user_message.strip() if body.user_message else ""
    prompt = await _operation_resume_prompt(
        db,
        operation_observation,
        user_message=user_message,
    )
    record_session_audit_event(
        body.session_id,
        "agent.resume_operation.prompt_ready",
        {"operation_id": body.operation_id, "prompt_chars": len(prompt)},
        source="api.agent",
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
                "user_message": user_message,
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


@router.post("/resume-run/stream")
async def resume_agent_run_stream_endpoint(
    body: ResumeAgentRunRequest,
    _token: dict[str, str] = Depends(get_agent_token),
) -> StreamingResponse:
    record_session_audit_event(
        body.session_id,
        "agent.resume_run.request_received",
        {
            "run_id": body.run_id,
            "provider_name": body.provider_name,
            "target_node_id": body.target_node_id,
            "execution_mode": body.execution_mode,
        },
        source="api.agent",
    )
    provider = await _resolve_provider(body.provider_name)
    async with yequ_db.async_session_factory() as db:
        from yequ.runtime.agent_run_service import get_agent_run_projection

        run_projection = await get_agent_run_projection(db, body.run_id)
        operation_observation = await _operation_observation_from_run_projection(db, run_projection)
        available = await _available_functions(db, target_node_id=body.target_node_id)
        capability_context = await build_capability_context(
            db,
            available_functions=available,
            target_node_id=body.target_node_id,
        )
    record_session_audit_event(
        body.session_id,
        "agent.resume_run.context_loaded",
        {
            "run_id": body.run_id,
            "available_function_count": len(available),
        },
        source="api.agent",
    )

    prompt = await _agent_run_resume_prompt(
        db,
        run_projection,
        operation_observation=operation_observation,
    )
    record_session_audit_event(
        body.session_id,
        "agent.resume_run.prompt_ready",
        {"run_id": body.run_id, "prompt_chars": len(prompt)},
        source="api.agent",
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
                "resume_run_id": body.run_id,
                "run_checkpoint": run_projection,
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


@router.post("/resume-last-run/stream")
async def resume_last_agent_run_stream_endpoint(
    body: ResumeLastAgentRunRequest,
    _token: dict[str, str] = Depends(get_agent_token),
) -> StreamingResponse:
    record_session_audit_event(
        body.session_id,
        "agent.resume_last_run.request_received",
        {
            "provider_name": body.provider_name,
            "target_node_id": body.target_node_id,
            "execution_mode": body.execution_mode,
        },
        source="api.agent",
    )
    provider = await _resolve_provider(body.provider_name)
    async with yequ_db.async_session_factory() as db:
        from yequ.runtime.agent_run_service import get_last_resumable_agent_run

        run_projection = await get_last_resumable_agent_run(db, session_id=body.session_id)
        operation_observation = await _operation_observation_from_run_projection(db, run_projection)
        available = await _available_functions(db, target_node_id=body.target_node_id)
        capability_context = await build_capability_context(
            db,
            available_functions=available,
            target_node_id=body.target_node_id,
        )
    record_session_audit_event(
        body.session_id,
        "agent.resume_last_run.context_loaded",
        {
            "run_id": run_projection.get("run_id"),
            "available_function_count": len(available),
        },
        source="api.agent",
    )

    prompt = await _agent_run_resume_prompt(
        db,
        run_projection,
        operation_observation=operation_observation,
    )
    record_session_audit_event(
        body.session_id,
        "agent.resume_last_run.prompt_ready",
        {"run_id": run_projection.get("run_id"), "prompt_chars": len(prompt)},
        source="api.agent",
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
                "resume_run_id": run_projection.get("run_id"),
                "run_checkpoint": run_projection,
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
