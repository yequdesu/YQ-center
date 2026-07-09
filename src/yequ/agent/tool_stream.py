"""Tool execution event stream for Agent runtime.

This module owns tool validation/preflight, concurrency grouping, Center
Invocation/Job execution, and observation events.  The chat stream runner
should orchestrate this executor; it should not contain tool execution policy.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import suppress
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.provider import AgentFunction
from yequ.application.schemas import ExecuteToolCommand, ToolPreflightCommand
from yequ.application.tool_preflight import ToolPreflightApplicationService
from yequ.logconfig import get_logger
from yequ.runtime import CenterExecutionRuntime, RuntimeCommand
from yequ.services.session_audit import record_session_audit_event

StreamEvent = dict[str, object]
EventFactory = Callable[[str, dict[str, object] | None], StreamEvent]

CONCURRENCY_MAX = 4

log = get_logger(__name__)


def _as_object_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


async def execute_tool_calls_scheduled(
    db: AsyncSession,
    *,
    make_event: EventFactory,
    actor_id: str,
    session_id: str,
    tool_calls: list[dict[str, object]],
    known_functions: set[str],
    available_functions: list[AgentFunction],
    call_path: list[str],
    execution_mode: str,
    max_depth: int,
    max_total_duration_sec: int,
    started_at: datetime,
    target_node_id: str | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Execute tool calls with preflight and concurrency scheduling."""
    from yequ.db import async_session_factory

    classified: list[dict[str, object]] = []
    preflight_service = ToolPreflightApplicationService(db)

    for index, raw_tc in enumerate(tool_calls):
        tc_name = str(raw_tc.get("name", ""))
        tc_call_id = str(raw_tc.get("call_id", f"call_missing_{index}"))
        tc_input = raw_tc.get("input", {})

        if tc_name not in known_functions:
            yield make_event(
                "agent.tool_call.created",
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "target_node_id": target_node_id,
                },
            )
            yield make_event(
                "agent.tool_call.failed",
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "error_code": "function_not_available",
                    "message": f"Function {tc_name!r} is not available",
                    "target_node_id": target_node_id,
                },
            )
            classified.append(
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "input": tc_input,
                    "status": "failed",
                    "error": "function_not_available",
                }
            )
            continue

        if tc_name in call_path:
            yield make_event(
                "agent.tool_call.created",
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "target_node_id": target_node_id,
                },
            )
            yield make_event(
                "agent.tool_call.failed",
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "error_code": "circular_dependency",
                    "message": f"Circular: {tc_name!r} in call_path",
                    "target_node_id": target_node_id,
                },
            )
            classified.append(
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "input": tc_input,
                    "status": "failed",
                    "error": "circular_dependency",
                }
            )
            continue

        func_meta = next((f for f in available_functions if f.name == tc_name), None)
        risk = func_meta.risk if func_meta else "safe"
        effect = func_meta.effect if func_meta else "read"
        try:
            preflight = await preflight_service.check(
                ToolPreflightCommand(
                    function_name=tc_name,
                    execution_mode=execution_mode,
                    target_node_id=target_node_id,
                    declared_risk=risk,
                    declared_effect=effect,
                )
            )
        except Exception as exc:
            with suppress(Exception):
                await db.rollback()
            error_code = _record_tool_exception(
                session_id,
                phase="preflight",
                call_id=tc_call_id,
                name=tc_name,
                target_node_id=target_node_id,
                exc=exc,
            )
            yield make_event(
                "agent.tool_call.created",
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "target_node_id": target_node_id,
                },
            )
            yield make_event(
                "agent.tool_call.failed",
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "error_code": error_code,
                    "message": str(exc)[:500],
                    "target_node_id": target_node_id,
                },
            )
            classified.append(
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "input": tc_input,
                    "status": "failed",
                    "error": error_code,
                }
            )
            continue

        yield make_event(
            "agent.tool_call.created",
            {
                "call_id": tc_call_id,
                "name": tc_name,
                "sanitized_name": str(raw_tc.get("sanitized_name", tc_name)),
                "input": tc_input,
                "target_node_id": preflight.target_node_id,
            },
        )
        yield make_event(
            "agent.tool_call.arguments",
            {
                "call_id": tc_call_id,
                "name": tc_name,
                "input": tc_input,
                "target_node_id": preflight.target_node_id,
            },
        )

        if preflight.status == "unavailable":
            yield make_event(
                "agent.tool_call.failed",
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "error_code": preflight.error_code or "function_not_available",
                    "message": preflight.error_message or f"No online node has {tc_name!r}",
                    "target_node_id": preflight.target_node_id,
                },
            )
            classified.append(
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "input": tc_input,
                    "status": "failed",
                    "error": "no_node",
                }
            )
            continue

        if preflight.status == "denied":
            yield make_event(
                "agent.tool_call.failed",
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "error_code": preflight.error_code or "policy_denied",
                    "message": preflight.error_message or "Policy denied",
                    "target_node_id": preflight.target_node_id,
                },
            )
            classified.append(
                {
                    "call_id": tc_call_id,
                    "name": tc_name,
                    "input": tc_input,
                    "status": "failed",
                    "error": "policy_denied",
                }
            )
            continue

        classified.append(
            {
                "call_id": tc_call_id,
                "name": tc_name,
                "input": tc_input,
                "status": "pending",
                "func_meta": func_meta,
                "is_concurrent_safe": preflight.is_concurrent_safe,
                "resource_keys": preflight.resource_keys,
            }
        )

    await db.rollback()

    concurrent_candidates = [
        t for t in classified if t.get("is_concurrent_safe") and t["status"] == "pending"
    ]
    serial_tools = [
        t for t in classified if not t.get("is_concurrent_safe") and t["status"] == "pending"
    ]

    resource_key_owners: dict[str, str] = {}
    actual_concurrent: list[dict[str, object]] = []
    for tool_info in concurrent_candidates:
        keys = _as_str_list(tool_info.get("resource_keys", []))
        conflicts = [key for key in keys if key in resource_key_owners]
        if conflicts:
            serial_tools.append(tool_info)
            continue
        for key in keys:
            resource_key_owners[key] = str(tool_info["call_id"])
        actual_concurrent.append(tool_info)

    if actual_concurrent:
        semaphore = asyncio.Semaphore(CONCURRENCY_MAX)

        async def _execute_concurrent(tool_info: dict[str, object]) -> list[StreamEvent]:
            async with semaphore:
                collected_events: list[StreamEvent] = []
                try:
                    async with async_session_factory() as exec_db:
                        async for event in _execute_and_stream(
                            exec_db,
                            make_event=make_event,
                            actor_id=actor_id,
                            session_id=session_id,
                            call_id=str(tool_info["call_id"]),
                            tc_name=str(tool_info["name"]),
                            tc_input=_as_object_dict(tool_info["input"]),
                            available_functions=available_functions,
                            call_path=call_path,
                            execution_mode=execution_mode,
                            max_depth=max_depth,
                            max_total_duration_sec=max_total_duration_sec,
                            started_at=started_at,
                            target_node_id=target_node_id,
                        ):
                            collected_events.append(event)
                except Exception as exc:
                    log.exception(
                        "concurrent tool execution failed: call_id=%s",
                        tool_info["call_id"],
                    )
                    error_code = _record_tool_exception(
                        session_id,
                        phase="execute_concurrent",
                        call_id=str(tool_info["call_id"]),
                        name=str(tool_info["name"]),
                        target_node_id=target_node_id,
                        exc=exc,
                    )
                    collected_events.append(
                        make_event(
                            "agent.tool_call.failed",
                            {
                                "call_id": tool_info["call_id"],
                                "name": tool_info["name"],
                                "error_code": error_code,
                                "message": str(exc)[:500],
                                "target_node_id": target_node_id,
                            },
                        )
                    )
                return collected_events

        tasks = [asyncio.create_task(_execute_concurrent(tool)) for tool in actual_concurrent]
        for completed in asyncio.as_completed(tasks):
            for event in await completed:
                yield event

    for tool_info in serial_tools:
        try:
            async with async_session_factory() as serial_db:
                async for event in _execute_and_stream(
                    serial_db,
                    make_event=make_event,
                    actor_id=actor_id,
                    session_id=session_id,
                    call_id=str(tool_info["call_id"]),
                    tc_name=str(tool_info["name"]),
                    tc_input=_as_object_dict(tool_info["input"]),
                    available_functions=available_functions,
                    call_path=call_path,
                    execution_mode=execution_mode,
                    max_depth=max_depth,
                    max_total_duration_sec=max_total_duration_sec,
                    started_at=started_at,
                    target_node_id=target_node_id,
                ):
                    yield event
        except Exception as exc:
            log.exception("serial tool execution failed: call_id=%s", tool_info["call_id"])
            error_code = _record_tool_exception(
                session_id,
                phase="execute_serial",
                call_id=str(tool_info["call_id"]),
                name=str(tool_info["name"]),
                target_node_id=target_node_id,
                exc=exc,
            )
            yield make_event(
                "agent.tool_call.failed",
                {
                    "call_id": tool_info["call_id"],
                    "name": tool_info["name"],
                    "error_code": error_code,
                    "message": str(exc)[:500],
                    "target_node_id": target_node_id,
                },
            )


async def _execute_and_stream(
    db: AsyncSession,
    *,
    make_event: EventFactory,
    actor_id: str,
    session_id: str,
    call_id: str,
    tc_name: str,
    tc_input: dict[str, object],
    available_functions: list[AgentFunction],
    call_path: list[str],
    execution_mode: str,
    max_depth: int,
    max_total_duration_sec: int,
    started_at: datetime,
    target_node_id: str | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    func_meta = next((f for f in available_functions if f.name == tc_name), None)
    result = await CenterExecutionRuntime(db).execute(
        RuntimeCommand.from_execute_tool_command(
            ExecuteToolCommand(
            actor_type="agent",
            actor_id=actor_id,
            session_id=session_id,
            function_name=tc_name,
            input_data=tc_input,
            target_node_id=target_node_id,
            execution_mode=execution_mode,
            max_depth=max_depth,
            call_path=list(call_path) + [tc_name],
            wait_for_result=False,
            declared_risk=func_meta.risk if func_meta else None,
            declared_effect=func_meta.effect if func_meta else None,
            )
        )
    )
    event_context = _tool_event_context(tc_name, tc_input)

    if result.status in {"unavailable", "not_found"}:
        yield make_event(
            "agent.tool_call.failed",
            {
                "call_id": call_id,
                "name": tc_name,
                **event_context,
                "error_code": result.error_code or "function_not_available",
                "message": result.error_message or f"No online node has {tc_name!r}",
                "target_node_id": result.target_node_id,
            },
        )
        return

    if result.status == "denied":
        yield make_event(
            "agent.tool_call.failed",
            {
                "call_id": call_id,
                "name": tc_name,
                **event_context,
                "error_code": result.error_code or "policy_denied",
                "message": result.error_message or "Policy denied",
                "target_node_id": result.target_node_id,
            },
        )
        return

    if result.status == "approval_required":
        output = result.output_data or {}
        operation = _as_object_dict(output.get("operation"))
        wait_handle = result.wait_handle or _as_object_dict(output.get("wait_handle"))
        operation_id = result.operation_id or str(operation.get("operation_id") or "")
        if operation_id:
            yield make_event(
                "agent.operation.created",
                {
                    "call_id": call_id,
                    "name": tc_name,
                    **event_context,
                    "operation_id": operation_id,
                    "kind": operation.get("kind"),
                    "status": operation.get("status"),
                    "ref_type": operation.get("ref_type"),
                    "ref_id": operation.get("ref_id"),
                    "title": operation.get("title"),
                    "target_node_id": result.target_node_id,
                },
            )
            yield make_event(
                "agent.operation.waiting",
                {
                    "call_id": call_id,
                    "name": tc_name,
                    **event_context,
                    "operation_id": operation_id,
                    "kind": operation.get("kind"),
                    "status": operation.get("status"),
                    "wait_handle": wait_handle,
                    "resume_policy": wait_handle.get("resume_policy") or "manual",
                    "message": "Approval is waiting in Center runtime.",
                    "target_node_id": result.target_node_id,
                },
            )
        yield make_event(
            "agent.approval.required",
            {
                "call_id": call_id,
                "name": tc_name,
                **event_context,
                "approval_id": result.approval_id,
                "operation_id": operation_id,
                "target_node_id": result.target_node_id,
            },
        )
        yield make_event(
            "agent.tool_call.waiting_approval",
            {
                "call_id": call_id,
                "name": tc_name,
                **event_context,
                "approval_id": result.approval_id,
                "status": "waiting_approval",
                "message": result.error_message or "Write operation requires approval",
                "target_node_id": result.target_node_id,
            },
        )
        return

    if result.status == "waiting_operation":
        output = result.output_data or {}
        operation = _as_object_dict(output.get("operation"))
        wait_handle = result.wait_handle or _as_object_dict(output.get("wait_handle"))
        operation_id = result.operation_id or str(operation.get("operation_id") or "")
        yield make_event(
            "agent.operation.created",
            {
                "call_id": call_id,
                "name": tc_name,
                **event_context,
                "operation_id": operation_id,
                "kind": operation.get("kind"),
                "status": operation.get("status"),
                "ref_type": operation.get("ref_type"),
                "ref_id": operation.get("ref_id"),
                "title": operation.get("title"),
                "target_node_id": result.target_node_id,
            },
        )
        yield make_event(
            "agent.operation.waiting",
            {
                "call_id": call_id,
                "name": tc_name,
                **event_context,
                "operation_id": operation_id,
                "kind": operation.get("kind"),
                "status": operation.get("status"),
                "wait_handle": wait_handle,
                "resume_policy": wait_handle.get("resume_policy") or "manual",
                "message": "Operation is running in Center runtime.",
                "target_node_id": result.target_node_id,
            },
        )
        yield make_event(
            "agent.run.waiting",
            {
                "reason": "waiting_operation",
                "operation_id": operation_id,
                "wait_handle": wait_handle,
            },
        )
        yield make_event(
            "agent.tool_call.waiting_operation",
            {
                "call_id": call_id,
                "name": tc_name,
                **event_context,
                "operation_id": operation_id,
                "wait_handle": wait_handle,
                "result": output,
                "target_node_id": result.target_node_id,
            },
        )
        return

    if result.status == "succeeded" and not result.job_id:
        yield make_event(
            "agent.tool_call.completed",
            {
                "call_id": call_id,
                "name": tc_name,
                **event_context,
                "result": result.output_data or {},
                "target_node_id": result.target_node_id,
            },
        )
        return

    if not result.invocation_id or not result.job_id:
        yield make_event(
            "agent.tool_call.failed",
            {
                "call_id": call_id,
                "name": tc_name,
                **event_context,
                "error_code": result.error_code or "tool_failed",
                "message": result.error_message or "Tool execution did not create a job",
                "target_node_id": result.target_node_id,
            },
        )
        return

    invocation_id = result.invocation_id
    job_id = result.job_id

    yield make_event(
        "agent.invocation.created",
        {
            "call_id": call_id,
            "name": tc_name,
            **event_context,
            "invocation_id": invocation_id,
            "target_node_id": result.target_node_id,
        },
    )
    yield make_event(
        "agent.job.queued",
        {
            "call_id": call_id,
            "name": tc_name,
            **event_context,
            "invocation_id": invocation_id,
            "job_id": job_id,
            "target_node_id": result.target_node_id,
        },
    )

    deadline = datetime.fromtimestamp(
        (started_at or datetime.now(UTC)).timestamp() + max_total_duration_sec,
        tz=UTC,
    )

    from yequ.db import async_session_factory
    from yequ.models.invocation import Invocation
    from yequ.models.job import Job as JobModel

    poll_count = 0
    final_status = "running"
    while datetime.now(UTC) < deadline:
        async with async_session_factory() as poll_db:
            job_result = await poll_db.execute(select(JobModel).where(JobModel.job_id == job_id))
            job = job_result.scalar_one_or_none()
            if job:
                if poll_count == 0 and job.status == "running":
                    yield make_event(
                        "agent.job.running",
                        {
                            "call_id": call_id,
                            "name": tc_name,
                            **event_context,
                            "job_id": job_id,
                            "status": "running",
                            "target_node_id": result.target_node_id,
                        },
                    )
                if job.status in {"succeeded", "failed", "timeout", "cancelled"}:
                    final_status = job.status
                    break
        poll_count += 1
        await asyncio.sleep(0.5)

    async with async_session_factory() as result_db:
        invocation_result = await result_db.execute(
            select(Invocation).where(Invocation.invocation_id == invocation_id)
        )
        invocation = invocation_result.scalar_one_or_none()

        job_result = await result_db.execute(select(JobModel).where(JobModel.job_id == job_id))
        job = job_result.scalar_one_or_none()

        yield make_event(
            "agent.job.finished",
            {
                "call_id": call_id,
                "name": tc_name,
                **event_context,
                "job_id": job_id,
                "status": final_status,
                "target_node_id": result.target_node_id,
            },
        )

        if final_status == "succeeded":
            yield make_event(
                "agent.tool_call.completed",
                {
                    "call_id": call_id,
                    "name": tc_name,
                    **event_context,
                    "result": invocation.result if invocation else {},
                    "target_node_id": result.target_node_id,
                },
            )
            return

        error_code = (
            (job.error_code if job else None)
            or (invocation.error_code if invocation else None)
            or "tool_failed"
        )
        error_message = (
            (job.error_message if job else None)
            or (invocation.error_message if invocation else None)
            or f"Tool {tc_name} ended with {final_status}"
        )
        error_details = job.error_details if job else None
        yield make_event(
            "agent.tool_call.failed",
            {
                "call_id": call_id,
                "name": tc_name,
                **event_context,
                "status": final_status,
                "error_code": error_code,
                "message": error_message,
                "details": error_details,
                "target_node_id": result.target_node_id,
            },
        )


def _tool_event_context(tc_name: str, tc_input: dict[str, object]) -> dict[str, object]:
    if tc_name == "capability.invoke":
        capability_ref = _string_or_none(tc_input.get("capability_ref")) or tc_name
        return {
            "capability_ref": capability_ref,
            "source_id": _string_or_none(tc_input.get("source_id")),
            "node_id": _string_or_none(tc_input.get("node_id")),
            "input": _small_tool_input(tc_input),
        }
    return {
        "capability_ref": tc_name,
        "source_id": _string_or_none(tc_input.get("source_id")),
        "node_id": _string_or_none(tc_input.get("node_id")),
        "input": _small_tool_input(tc_input),
    }


def _small_tool_input(value: dict[str, object]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key in ["capability_ref", "source_id", "node_id", "target_node_id"]:
        item = value.get(key)
        if item is not None:
            output[key] = item
    nested = value.get("input")
    if isinstance(nested, dict):
        output["input_keys"] = sorted(str(key) for key in nested)[:20]
        for key in ["profile", "artifact_id", "output_path", "path", "mode"]:
            item = nested.get(key)
            if item is not None:
                output[key] = item
    return output


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _record_tool_exception(
    session_id: str,
    *,
    phase: str,
    call_id: str,
    name: str,
    target_node_id: str | None,
    exc: Exception,
) -> str:
    error_code = _tool_exception_error_code(exc)
    if error_code in {"db_lock_timeout", "db_statement_timeout", "db_error"}:
        record_session_audit_event(
            session_id,
            "agent.db.error",
            {
                "phase": phase,
                "call_id": call_id,
                "name": name,
                "target_node_id": target_node_id,
                "error_code": error_code,
                "error_type": type(exc).__name__,
                "message": str(exc)[:1000],
            },
            source="agent.tools",
        )
    return error_code


def _tool_exception_error_code(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(
        marker in text
        for marker in (
            "locknotavailable",
            "lock timeout",
            "could not obtain lock",
            "database is locked",
            "deadlock detected",
        )
    ):
        return "db_lock_timeout"
    if any(
        marker in text
        for marker in (
            "statement timeout",
            "querycancelederror",
            "canceling statement due to statement timeout",
        )
    ):
        return "db_statement_timeout"
    if isinstance(exc, DBAPIError | OperationalError):
        return "db_error"
    return "internal_error"
