"""Capability Resolver — selects the best schedulable Node for a Function.

This is the SINGLE authority for deciding which Node can execute a Function.
No other module should scatter node-selection logic.
"""

from dataclasses import dataclass, field
from datetime import UTC

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.capability import Capability
from yequ.models.job import Job
from yequ.models.node import Node
from yequ.protocol import JobStatus, NodeStatus


@dataclass
class ResolvedCapability:
    """Result of resolving a Function to a Node.

    When available=True, the caller can proceed with invocation.
    When available=False, unavailable_reason is set and the caller
    must return a stable error.
    """

    node_id: str
    function_name: str
    plugin_id: str = ""
    risk: str = "safe"
    effect: str = "read"
    approval_required: bool = False
    timeout_sec: int = 30
    lease_sec: int = 30
    resource_key_template: list[str] = field(default_factory=list)
    conflict_policy: str | None = None
    available: bool = True
    unavailable_reason: str | None = None

    @property
    def resource_keys(self) -> list[str]:
        """Compatibility alias used by Agent L2 approval creation."""
        return self.resource_key_template


async def resolve_function(
    db: AsyncSession,
    function_name: str,
    *,
    target_node_id: str | None = None,
    requested_node_id: str | None = None,
    required_effect: str | None = None,
    required_risk: str | None = None,
    settings=None,
) -> ResolvedCapability:
    """Resolve which Node should execute a Function.

    Rules (fixed order):
    1. If target_node_id given, only check that node.
    2. Node must be online (effective_status == online).
    3. Node must have the Function registered and active.
    4. Without target_node_id, scan all online nodes.
    5. If multiple nodes provide the function, sort by:
       - online first
       - locality: local/lan > wan
       - last_heartbeat_at newest first
       - running_jobs fewest first
       - node_id lexicographic (stable tiebreaker)
    6. Return first match. If none, return available=False.
    """
    # Normalize: accept both parameter names for backward compat
    _node_id = target_node_id or requested_node_id
    from yequ.services.node_liveness_service import is_node_schedulable

    # Build candidate node query
    if _node_id:
        node_query = select(Node).where(Node.node_id == _node_id)
    else:
        node_query = select(Node).where(
            Node.status.in_([NodeStatus.ONLINE, NodeStatus.PROVISIONED])
        )

    node_result = await db.execute(node_query)
    candidates = list(node_result.scalars().all())

    if not candidates:
        return ResolvedCapability(
            node_id=_node_id or "",
            function_name=function_name,
            available=False,
            unavailable_reason=(
                f"Node '{_node_id}' not found"
                if _node_id
                else "No nodes available"
            ),
        )

    # Filter: node must be schedulable and have the active function registered
    viable: list[tuple[Node, Capability]] = []
    offline_reasons: list[str] = []

    for node in candidates:
        # Always verify schedulability. Fall back to stored status when
        # settings (and thus liveness service) is unavailable (e.g. in tests).
        if settings:
            schedulable, reason = is_node_schedulable(node, settings)
        else:
            schedulable = node.status == NodeStatus.ONLINE
            reason = f"node_{node.status}" if not schedulable else None
        if not schedulable:
            if _node_id:
                offline_reasons.append(reason or "node_not_schedulable")
            continue

        cap_result = await db.execute(
            select(Capability)
            .where(
                Capability.node_record_id == node.id,
                Capability.capability_type == "function",
                Capability.name == function_name,
                Capability.is_active == True,  # noqa: E712
            )
        )
        cap = cap_result.scalar_one_or_none()
        if cap is not None:
            if required_effect and cap.effect != required_effect:
                if _node_id:
                    offline_reasons.append(
                        f"Capability '{function_name}' effect is '{cap.effect}', "
                        f"requires '{required_effect}'"
                    )
                continue
            if required_risk and cap.risk != required_risk:
                if _node_id:
                    offline_reasons.append(
                        f"Capability '{function_name}' risk is '{cap.risk}', "
                        f"requires '{required_risk}'"
                    )
                continue
            viable.append((node, cap))
        elif _node_id:
            offline_reasons.append(
                f"Node '{_node_id}' does not have capability '{function_name}'"
            )

    if not viable:
        reason = (
            offline_reasons[0]
            if offline_reasons
            else f"No online node has capability '{function_name}'"
        )
        return ResolvedCapability(
            node_id=_node_id or "",
            function_name=function_name,
            available=False,
            unavailable_reason=reason,
        )

    node_ids = [node.node_id for node, _cap in viable]
    running_counts = dict.fromkeys(node_ids, 0)
    running_result = await db.execute(
        select(Job.node_id, func.count(Job.id))
        .where(
            Job.node_id.in_(node_ids),
            Job.status.in_([
                JobStatus.CLAIMED,
                JobStatus.RUNNING,
                JobStatus.CANCELLING,
            ]),
        )
        .group_by(Job.node_id)
    )
    running_counts.update({node_id: int(count) for node_id, count in running_result.all()})

    def _heartbeat_sort_value(node: Node) -> float:
        if node.last_heartbeat_at is None:
            return float("inf")
        heartbeat = node.last_heartbeat_at
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=UTC)
        return -heartbeat.timestamp()

    def _sort_key(item: tuple[Node, Capability]) -> tuple:
        node, _cap = item
        status_rank = 0 if node.status == NodeStatus.ONLINE else 1
        locality_rank = {"local": 0, "lan": 1}.get((node.locality or "").lower(), 2)
        return (
            status_rank,
            locality_rank,
            running_counts.get(node.node_id, 0),
            _heartbeat_sort_value(node),
            node.node_id,
        )

    # Sort viable candidates by the fixed routing contract.
    viable.sort(key=_sort_key)

    # Pick the best
    best_node, best_cap = viable[0]

    # Determine approval_required
    approval_required = (
        best_cap.effect in ("write", "destructive")
        or best_cap.risk in ("maintenance", "destructive", "catastrophic")
    )

    return ResolvedCapability(
        node_id=best_node.node_id,
        function_name=best_cap.name,
        plugin_id=best_cap.plugin_id,
        risk=best_cap.risk or "safe",
        effect=best_cap.effect or "read",
        approval_required=approval_required,
        timeout_sec=best_cap.timeout_sec or 30,
        lease_sec=30,
        resource_key_template=list(best_cap.resource_keys) if best_cap.resource_keys else [],
        conflict_policy=best_cap.conflict_policy,
        available=True,
        unavailable_reason=None,
    )


# Backward-compatible alias
resolve_target_node = resolve_function
