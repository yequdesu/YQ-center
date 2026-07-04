"""Small helpers for runtime command input parsing and errors."""

from __future__ import annotations

from yequ.application.schemas import ExecuteToolResult
from yequ.runtime.command import RuntimeCommand


def string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def required_string(value: object, field_name: str) -> str:
    text = string_or_none(value)
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def int_or_default(value: object, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def bool_or_none(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def runtime_error(
    command: RuntimeCommand,
    error_code: str,
    error_message: str,
) -> ExecuteToolResult:
    return ExecuteToolResult(
        status="failed",
        function_name=command.function_name,
        target_node_id=command.target_node_id,
        risk="safe",
        effect="read",
        error_code=error_code,
        error_message=error_message,
    )
