from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .models import Envelope


def dump_payload(payload: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        return payload.model_dump(mode="json", exclude_none=True)
    return payload


def envelope(
    *,
    message_type: str,
    node_id: str | None,
    payload: BaseModel | dict[str, Any],
    trace_id: str | None = None,
    session_id: str | None = None,
) -> Envelope:
    data: dict[str, Any] = {
        "message_type": message_type,
        "node_id": node_id,
        "session_id": session_id,
        "payload": dump_payload(payload),
    }
    if trace_id:
        data["trace_id"] = trace_id
    return Envelope(**data)

