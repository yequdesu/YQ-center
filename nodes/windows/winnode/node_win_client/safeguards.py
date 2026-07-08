from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SafetyCheck:
    allowed: bool
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class Safeguards:
    allow_write_actions: bool = True
    allowed_services: list[str] = field(default_factory=list)

    def check_service(self, service_name: str) -> SafetyCheck:
        if service_name in self.allowed_services:
            return SafetyCheck(allowed=True)
        return SafetyCheck(
            allowed=False,
            error_code="SERVICE_NOT_ALLOWED",
            error_message=(
                f"Service {service_name} is not allowed by local safety policy."
            ),
        )

    def check_write_action(self, action_name: str) -> SafetyCheck:
        if self.allow_write_actions:
            return SafetyCheck(allowed=True)
        return SafetyCheck(
            allowed=False,
            error_code="WRITE_ACTION_DENIED",
            error_message=(
                f"Write action {action_name} is denied: "
                "allow_write_actions is disabled."
            ),
        )


def load_safeguards(
    allow_write_actions: bool = True,
    allowed_services: list[str] | None = None,
) -> Safeguards:
    return Safeguards(
        allow_write_actions=allow_write_actions,
        allowed_services=allowed_services or [],
    )
