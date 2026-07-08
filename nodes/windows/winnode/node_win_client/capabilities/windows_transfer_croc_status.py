from __future__ import annotations

from typing import Any

from node_win_client.models import FunctionManifest

from .base import CapabilityContext, NodeCapability


def execute(input_data: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
    from node_win_client.transfer_yq_croc import probe_yq_croc_status

    relay_url = input_data.get("relay_url")
    return probe_yq_croc_status(context.transfer_yq_croc, relay_url=str(relay_url).strip() if relay_url else None)


CAPABILITY = NodeCapability(
    manifest=FunctionManifest(
        name="windows.transfer.croc.status",
        description="Probe local yq-croc transfer runtime readiness.",
        agent_description="Use to check whether this Windows node can join yq-croc transfers. Does not start transfer.",
        user_visible_name="yq-croc status",
        input_schema={"type": "object", "properties": {"relay_url": {"type": ["string", "null"]}}, "additionalProperties": False},
        output_schema={"type": "object"},
        risk="safe",
        effect="read",
        timeout_sec=15,
        resource_keys=["node.transfer"],
    ),
    handler=execute,
)
