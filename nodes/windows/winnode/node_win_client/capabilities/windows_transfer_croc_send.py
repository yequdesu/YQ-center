from __future__ import annotations

from typing import Any

from node_win_client.models import FunctionManifest, JobEventType

from .base import CapabilityContext, NodeCapability


async def execute(input_data: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
    from node_win_client.transfer_yq_croc import yq_croc_send

    def progress(_event_type: str, payload: dict[str, Any]) -> Any:
        if context.job_event_callback is not None:
            return context.job_event_callback(JobEventType.PROGRESS, payload)
        return None

    return await yq_croc_send(input_data, context.transfer_yq_croc, progress)


CAPABILITY = NodeCapability(
    manifest=FunctionManifest(
        name="windows.transfer.croc.send",
        description="Send one Windows file or directory through yq-croc.",
        agent_description="Transfer sender endpoint. Center supplies transfer id, code, relay, route policy, and timeout.",
        user_visible_name="yq-croc send",
        input_schema={"type": "object", "properties": {"transfer_id": {"type": "string"}, "attempt": {"type": "integer"}, "source_path": {"type": "string"}, "code": {"type": "string"}, "relay_url": {"type": ["string", "null"]}, "route_policy": {"type": ["string", "null"]}, "timeout_sec": {"type": "integer"}, "resume_mode": {"type": "string"}}, "required": ["transfer_id", "attempt", "source_path", "code"], "additionalProperties": True},
        output_schema={"type": "object"},
        risk="maintenance",
        effect="external",
        timeout_sec=3600,
        lease_sec=30,
        idempotency="non_idempotent",
        resource_keys=["node.transfer"],
        conflict_policy="serialize",
        execution_context="user",
        supports_progress=True,
        supports_cancel=True,
        supports_resume=True,
        progress_contract="transfer_progress_v1",
        required_intent_slots=["transfer_id", "attempt", "source_path", "code"],
    ),
    handler=execute,
    run_in_thread=False,
)
