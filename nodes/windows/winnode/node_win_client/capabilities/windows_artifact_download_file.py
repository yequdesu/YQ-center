from __future__ import annotations

from typing import Any

from node_win_client.models import FunctionManifest

from .base import CapabilityContext, NodeCapability


def execute(input_data: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
    from node_win_client.plugins import artifact_download_file

    return artifact_download_file(
        input_data,
        context.l2_policy,
        center_base_url=context.center_base_url,
        node_token=context.node_token,
        timeout_sec=context.request_timeout_sec,
    )


CAPABILITY = NodeCapability(
    manifest=FunctionManifest(
        name="windows.artifact.download_file",
        description="Download one Center artifact to a Windows file path.",
        agent_description="Use only when artifact_id and output_path are known. Use transfer workflow for large node-to-node transfer.",
        user_visible_name="Download artifact",
        input_schema={
            "type": "object",
            "properties": {"artifact_id": {"type": "string", "minLength": 1}, "output_path": {"type": "string", "minLength": 1}, "mode": {"type": "string", "enum": ["fail_if_exists", "overwrite"], "default": "fail_if_exists"}},
            "required": ["artifact_id", "output_path"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        risk="maintenance",
        effect="write",
        timeout_sec=300,
        idempotency="idempotent",
        resource_keys=["node.filesystem", "center.artifact"],
        conflict_policy="serialize",
        execution_context="user",
        required_intent_slots=["artifact_id", "output_path"],
    ),
    handler=execute,
)
