from __future__ import annotations

from typing import Any

from node_win_client.models import FunctionManifest

from .base import CapabilityContext, NodeCapability


def execute(input_data: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
    from node_win_client.plugins import file_upload_artifact

    return file_upload_artifact(input_data, context.l2_policy)


CAPABILITY = NodeCapability(
    manifest=FunctionManifest(
        name="windows.file.upload_artifact",
        description="Upload one known Windows file to Center as an artifact.",
        agent_description="Use for small/medium known files that should become Center artifacts. Use transfer workflow for large node-to-node transfer.",
        user_visible_name="Upload file artifact",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "minLength": 1}, "title": {"type": "string"}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 52428800}},
            "required": ["path"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        risk="safe",
        effect="read",
        timeout_sec=20,
        idempotency="non_idempotent",
        resource_keys=["node.file"],
        execution_context="user",
    ),
    handler=execute,
)
