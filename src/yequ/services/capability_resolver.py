"""Capability resolver — selects the best schedulable Node for a Function."""

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
    available: bool = True
    unavailable_reason: str | None = None


async def resolve_target_node(
    db: AsyncSession,
    function_name: str,
    *,
    requested_node_id: str | None = None,
    settings=None,
) -> ResolvedCapability | None:
    """Resolve which Node should execute a Function.

    Rules (in order):
    1. If requested_node_id is given, only check that Node.
    2. Node must be online (or provisioned for dev).
    3. Node must be liveness-schedulable (effective_status == online).
    4. Node must have the Function registered and active.
    5. Without requested_node_id, scan all candidate Nodes.

    Returns ResolvedCapability or None if no candidate found.
    """
    from yequ.services.node_liveness_service import is_node_schedulable

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
        # Liveness gate: skip nodes that are not schedulable
        if settings:
            schedulable, reason = is_node_schedulable(node, settings)
            if not schedulable:
                continue

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
                available=True,
                unavailable_reason=None,
            )

    # Node exists but is offline/degraded — return unavailable
    if requested_node_id and nodes:
        node = nodes[0]
        if settings:
            schedulable, reason = is_node_schedulable(node, settings)
            if not schedulable:
                return ResolvedCapability(
                    node_id=node.node_id,
                    function_name=function_name,
                    plugin_id="",
                    risk="safe",
                    effect="read",
                    timeout_sec=30,
                    available=False,
                    unavailable_reason=reason,
                )

    return None
