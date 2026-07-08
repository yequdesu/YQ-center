from __future__ import annotations

from typing import Any


class NodeExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        origin: str = "node",
        category: str = "execution",
        retryable: bool = False,
        user_action: str | None = None,
        operator_action: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details or {}
        self.origin = origin
        self.category = category
        self.retryable = retryable
        self.user_action = user_action
        self.operator_action = operator_action

    def to_error(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "origin": self.origin,
            "category": self.category,
            "retryable": self.retryable,
        }
        if self.user_action:
            error["user_action"] = self.user_action
        if self.operator_action:
            error["operator_action"] = self.operator_action
        if self.details:
            error["details"] = self.details
        return error


def normalize_execution_error(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, NodeExecutionError):
        return exc.to_error()

    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    code = "execution_failed"

    category = "execution"
    retryable = False

    if isinstance(exc, TimeoutError):
        code = "execution_timeout"
        category = "timeout"
        retryable = True
    elif isinstance(exc, PermissionError):
        code = "permission_denied"
        category = "permission"
    elif isinstance(exc, FileNotFoundError):
        code = "path_not_found"
        category = "not_found"
    elif "approval_id is required" in lowered:
        code = "approval_required"
    elif "allowlist" in lowered and "path" in lowered:
        code = "file_path_not_allowed"
    elif "path is forbidden" in lowered:
        code = "file_path_forbidden"
    elif "service is forbidden" in lowered:
        code = "service_forbidden"
    elif "service is not in" in lowered:
        code = "service_not_allowed"
    elif "write actions are disabled" in lowered:
        code = "write_action_denied"
    elif "protected process" in lowered or "protected pid" in lowered:
        code = "protected_process"
    elif "process not found" in lowered:
        code = "process_not_found"
    elif "not supported" in lowered:
        code = "function_not_supported"

    return {
        "code": code,
        "message": message,
        "origin": "node",
        "category": category,
        "retryable": retryable,
        "exception_type": exc.__class__.__name__,
    }
