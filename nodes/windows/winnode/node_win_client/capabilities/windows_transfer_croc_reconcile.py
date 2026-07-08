from __future__ import annotations

from typing import Any

from node_win_client.models import FunctionManifest

from .base import CapabilityContext, NodeCapability


def execute(input_data: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
    from node_win_client.transfer_yq_croc import yq_croc_reconcile

    return yq_croc_reconcile(input_data, context.transfer_yq_croc)


CAPABILITY = NodeCapability(
    manifest=FunctionManifest(
        name="windows.transfer.croc.reconcile",
        description="Read local yq-croc transfer ledger state.",
        agent_description="Use after restart or failed transfer to inspect local yq-croc state. Does not start transfer.",
        user_visible_name="yq-croc reconcile",
        input_schema={"type": "object", "properties": {"transfer_id": {"type": "string"}, "status": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False},
        output_schema={"type": "object"},
        risk="safe",
        effect="read",
        timeout_sec=5,
        resource_keys=["node.transfer"],
    ),
    handler=execute,
)
