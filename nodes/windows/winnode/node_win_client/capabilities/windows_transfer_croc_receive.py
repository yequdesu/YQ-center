from __future__ import annotations

from typing import Any

from node_win_client.models import FunctionManifest, JobEventType

from .base import CapabilityContext, NodeCapability


async def execute(input_data: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
    from node_win_client.transfer_yq_croc import yq_croc_receive

    def progress(_event_type: str, payload: dict[str, Any]) -> Any:
        if context.job_event_callback is not None:
            return context.job_event_callback(JobEventType.PROGRESS, payload)
        return None

    return await yq_croc_receive(input_data, context.transfer_yq_croc, progress)


CAPABILITY = NodeCapability(
    manifest=FunctionManifest(
        name="windows.transfer.croc.receive",
        description="Receive one file or directory through yq-croc.",
        agent_description="Transfer receiver endpoint. Center supplies transfer id, code, target/output path, relay, expected facts, and timeout.",
        user_visible_name="yq-croc receive",
        input_schema={"type": "object", "properties": {"transfer_id": {"type": "string"}, "attempt": {"type": "integer"}, "code": {"type": "string"}, "output_dir": {"type": ["string", "null"]}, "target_path": {"type": ["string", "null"]}, "relay_url": {"type": ["string", "null"]}, "route_policy": {"type": ["string", "null"]}, "timeout_sec": {"type": "integer"}, "resume_mode": {"type": "string"}, "expected_sha256": {"type": "string"}, "expected_size_bytes": {"type": "integer"}}, "required": ["transfer_id", "attempt", "code"], "additionalProperties": True},
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
        required_intent_slots=["transfer_id", "attempt", "code", "target_output_dir_or_target_path"],
    ),
    handler=execute,
    run_in_thread=False,
)
