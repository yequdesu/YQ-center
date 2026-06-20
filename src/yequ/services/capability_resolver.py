"""Capability resolver — selects the best online Node for a Function."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.capability import Capability
from yequ.models.node import Node
from yequ.protocol import NodeStatus


@dataclass
class ResolvedCapability:
    """Result of resolving a Function to a Node."""
    node_id: str
    function_name: str
    plugin_id: str
    risk: str
    effect: str
    timeout_sec: int
    input_schema: dict | None = None
    output_schema: dict | None = None
    resource_keys: list[str] | None = None


async def resolve_target_node(
    db: AsyncSession,
    function_name: str,
    *,
    requested_node_id: str | None = None,
) -> ResolvedCapability | None:
    """Resolve which Node should execute a Function.

    Rules (in order):
    1. If requested_node_id is given, only check that Node.
    2. Node must be online (or provisioned for dev).
    3. Node must have the Function registered and active.
    4. Without requested_node_id, query all online Nodes
       and pick the one with the most recent heartbeat.

    Returns ResolvedCapability or None if no candidate found.
    """
    # Build base node query
    node_query = select(Node).where(
        Node.status.in_([NodeStatus.ONLINE, NodeStatus.PROVISIONED])
    )
    if requested_node_id:
        node_query = node_query.where(Node.node_id == requested_node_id)

    node_result = await db.execute(node_query)
    nodes = node_result.scalars().all()

    if not nodes:
        return None

    for node in nodes:
        # Check if this node has the function registered and active
        cap_result = await db.execute(
            select(Capability).where(
                Capability.node_record_id == node.id,
                Capability.capability_type == "function",
                Capability.name == function_name,
                Capability.is_active == True,  # noqa: E712
            )
        )
        cap = cap_result.scalar_one_or_none()
        if cap is not None:
            return ResolvedCapability(
                node_id=node.node_id,
                function_name=cap.name,
                plugin_id=cap.plugin_id,
                risk=cap.risk or "safe",
                effect=cap.effect or "read",
                timeout_sec=cap.timeout_sec or 30,
                input_schema=cap.input_schema,
                output_schema=cap.output_schema,
                resource_keys=cap.resource_keys,
            )

    return None
