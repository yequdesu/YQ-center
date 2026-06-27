"""Application-layer errors."""

from __future__ import annotations


class ApplicationError(Exception):
    """Base exception for application-layer failures."""

    error_code = "application_error"


class ToolInvocationError(ApplicationError):
    """Raised when a tool invocation command cannot be executed."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "tool_invocation_error",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}


class ToolExecutionTimeoutError(ToolInvocationError):
    """Raised when waiting for a tool execution exceeds its deadline."""

    def __init__(self, message: str = "Tool execution timed out") -> None:
        super().__init__(message, error_code="tool_timeout")
