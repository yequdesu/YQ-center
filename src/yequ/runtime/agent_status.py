"""Agent runtime status projection shared by Agent and Center services."""

from __future__ import annotations

TERMINAL_AGENT_RUN_STATUSES = {"succeeded", "failed", "cancelled"}
PAUSED_AGENT_RUN_STATUSES = {"waiting_approval", "waiting_operation"}
OPEN_AGENT_RUN_STATUSES = {
    "created",
    "building_context",
    "model_running",
    "validating_tools",
    "preflighting",
    "executing_tools",
    "observing",
    "synthesizing",
}


def is_terminal_agent_status(status: str | None) -> bool:
    return bool(status) and status in TERMINAL_AGENT_RUN_STATUSES


def is_paused_agent_status(status: str | None) -> bool:
    return bool(status) and status in PAUSED_AGENT_RUN_STATUSES


def is_open_agent_status(status: str | None) -> bool:
    return bool(status) and status in OPEN_AGENT_RUN_STATUSES


def status_for_stream_event(event_type: str, error_code: str | None = None) -> str | None:
    if event_type == "stream.open":
        return "created"
    if event_type == "agent.prompt_context":
        return "building_context"
    if event_type == "agent.provider.started":
        return "model_running"
    if event_type == "agent.tool_call.created":
        return "validating_tools"
    if event_type in {"agent.invocation.created", "agent.job.queued", "agent.job.running"}:
        return "executing_tools"
    if event_type in {"agent.approval.required", "agent.tool_call.waiting_approval"}:
        return "waiting_approval"
    if event_type in {
        "agent.operation.created",
        "agent.operation.waiting",
        "agent.run.waiting",
        "agent.tool_call.waiting_operation",
    }:
        return "waiting_operation"
    if event_type == "agent.observing":
        return "observing"
    if event_type in {"agent.output.delta", "agent.synthesizing"}:
        return "synthesizing"
    if event_type == "agent.completed":
        return "succeeded"
    if event_type in {"agent.failed", "agent.provider.failed"}:
        return "failed"
    if error_code == "cancelled":
        return "cancelled"
    return None
