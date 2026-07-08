from __future__ import annotations

from typing import Any

from node_win_client.models import FunctionManifest

from .base import CapabilityContext, NodeCapability


def execute(input_data: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
    from node_win_client.plugins import transfer_local_stat

    return transfer_local_stat(input_data, context.l2_policy)


CAPABILITY = NodeCapability(
    manifest=FunctionManifest(
        name="windows.transfer.local.stat",
        description="Inspect local Windows path facts for transfer preflight.",
        agent_description="Use for transfer preflight/verification: existence, type, size, optional sha256, writable target facts.",
        user_visible_name="Transfer path stat",
        input_schema={"type": "object", "properties": {"path": {"type": "string", "minLength": 1}, "sha256": {"type": "boolean", "default": False}}, "required": ["path"], "additionalProperties": False},
        output_schema={"type": "object"},
        risk="safe",
        effect="read",
        timeout_sec=30,
        resource_keys=["node.transfer", "node.file"],
        execution_context="user",
    ),
    handler=execute,
)
