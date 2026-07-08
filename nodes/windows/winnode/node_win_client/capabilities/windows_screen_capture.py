from __future__ import annotations

from typing import Any

from node_win_client.models import FunctionManifest

from .base import CapabilityContext, NodeCapability


def execute(input_data: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
    del context
    from node_win_client.plugins import capture_screen

    return capture_screen(input_data)


CAPABILITY = NodeCapability(
    manifest=FunctionManifest(
        name="windows.screen.capture",
        description="Capture the current interactive Windows desktop as a PNG artifact.",
        agent_description="Use when the user asks for a screenshot/screen capture. Output contains artifact metadata, not image bytes.",
        user_visible_name="Capture screen",
        input_schema={"type": "object", "properties": {"title": {"type": "string", "default": "windows-screen-capture.png"}}, "additionalProperties": False},
        output_schema={"type": "object"},
        risk="safe",
        effect="read",
        timeout_sec=15,
        idempotency="non_idempotent",
        resource_keys=["node.screen"],
        conflict_policy="serialize",
        execution_context="user",
    ),
    handler=execute,
)
