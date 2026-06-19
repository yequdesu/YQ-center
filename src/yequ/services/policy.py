"""Policy engine — controls what actions an Actor can perform.

Agent calls MUST pass through Policy before becoming Invocations.
Policy checks execution mode vs function risk level.

Risk/Mode matrix (from YeQu-Architecture-Design.md):
           | safe | maintenance | destructive | catastrophic
auto       | allow| allow       | ask         | deny
assist     | allow| conditional | ask         | deny
readonly   | allow| deny        | deny        | deny
manual     | allow| ask         | ask         | deny
"""

from dataclasses import dataclass

from yequ.protocol import ExecutionMode, RiskLevel


class PolicyDecision:
    """Policy decision constants."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    CONDITIONAL = "conditional"


# (execution_mode, risk_level) -> decision
POLICY_MATRIX: dict[tuple[ExecutionMode, RiskLevel], str] = {
    # auto mode
    (ExecutionMode.AUTO, RiskLevel.SAFE): PolicyDecision.ALLOW,
    (ExecutionMode.AUTO, RiskLevel.MAINTENANCE): PolicyDecision.ALLOW,
    (ExecutionMode.AUTO, RiskLevel.DESTRUCTIVE): PolicyDecision.ASK,
    (ExecutionMode.AUTO, RiskLevel.CATASTROPHIC): PolicyDecision.DENY,
    # assist mode
    (ExecutionMode.ASSIST, RiskLevel.SAFE): PolicyDecision.ALLOW,
    (ExecutionMode.ASSIST, RiskLevel.MAINTENANCE): PolicyDecision.CONDITIONAL,
    (ExecutionMode.ASSIST, RiskLevel.DESTRUCTIVE): PolicyDecision.ASK,
    (ExecutionMode.ASSIST, RiskLevel.CATASTROPHIC): PolicyDecision.DENY,
    # readonly mode
    (ExecutionMode.READONLY, RiskLevel.SAFE): PolicyDecision.ALLOW,
    (ExecutionMode.READONLY, RiskLevel.MAINTENANCE): PolicyDecision.DENY,
    (ExecutionMode.READONLY, RiskLevel.DESTRUCTIVE): PolicyDecision.DENY,
    (ExecutionMode.READONLY, RiskLevel.CATASTROPHIC): PolicyDecision.DENY,
    # manual mode
    (ExecutionMode.MANUAL, RiskLevel.SAFE): PolicyDecision.ALLOW,
    (ExecutionMode.MANUAL, RiskLevel.MAINTENANCE): PolicyDecision.ASK,
    (ExecutionMode.MANUAL, RiskLevel.DESTRUCTIVE): PolicyDecision.ASK,
    (ExecutionMode.MANUAL, RiskLevel.CATASTROPHIC): PolicyDecision.DENY,
}


@dataclass
class PolicyResult:
    """Result of a policy check."""

    allowed: bool
    decision: str
    reason: str | None = None


def check_policy(
    execution_mode: str,
    risk_level: str = RiskLevel.SAFE,
    function_name: str = "",
    whitelist: set[str] | None = None,
) -> PolicyResult:
    """Check if an action is allowed under the given execution mode and risk.

    Args:
        execution_mode: auto, assist, readonly, or manual
        risk_level: safe, maintenance, destructive, or catastrophic
        function_name: The function being called (for conditional checks)
        whitelist: Set of function names allowed in conditional mode.
                   If None and mode is conditional, the action is denied.

    Returns:
        PolicyResult with allowed=True/False and decision string.

    Examples:
        >>> r = check_policy("auto", "safe")
        >>> r.allowed
        True

        >>> r = check_policy("readonly", "destructive")
        >>> r.allowed
        False
    """
    mode = ExecutionMode(execution_mode)
    risk = RiskLevel(risk_level)

    decision = POLICY_MATRIX.get((mode, risk), PolicyDecision.DENY)

    if decision == PolicyDecision.ALLOW:
        return PolicyResult(allowed=True, decision=decision)

    if decision == PolicyDecision.DENY:
        return PolicyResult(
            allowed=False,
            decision=decision,
            reason=f"{risk} action denied in {execution_mode} mode",
        )

    if decision == PolicyDecision.CONDITIONAL:
        # Only allowed if function is explicitly whitelisted
        if whitelist and function_name in whitelist:
            return PolicyResult(allowed=True, decision=decision)
        return PolicyResult(
            allowed=False,
            decision=PolicyDecision.DENY,
            reason=(
                f"{function_name!r} not in conditional whitelist "
                f"for {execution_mode} mode"
            ),
        )

    if decision == PolicyDecision.ASK:
        # Requires human confirmation — in automated context, deny
        return PolicyResult(
            allowed=False,
            decision=decision,
            reason=f"{risk} action requires human confirmation",
        )

    return PolicyResult(
        allowed=False, decision=decision, reason="unknown policy decision"
    )
