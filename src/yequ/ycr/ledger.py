"""YCR context ledger writer."""

from __future__ import annotations

import json
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.ycr import YcrContextLedger


async def write_ledger(
    db: AsyncSession,
    *,
    event_type: str,
    source_type: str,
    source_id: str,
    ref_id: str | None = None,
    raw_value: object | None = None,
    projected_value: object | None = None,
    projection_policy: str | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    db.add(
        YcrContextLedger(
            ledger_id=f"ctxled_{secrets.token_hex(8)}",
            event_type=event_type,
            source_type=source_type,
            source_id=source_id,
            ref_id=ref_id,
            raw_size_bytes=_json_size(raw_value) if raw_value is not None else None,
            projected_size_bytes=(
                _json_size(projected_value) if projected_value is not None else None
            ),
            raw_estimated_tokens=_estimate_tokens(raw_value) if raw_value is not None else None,
            projected_estimated_tokens=(
                _estimate_tokens(projected_value) if projected_value is not None else None
            ),
            projection_policy=projection_policy,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            metadata_json=metadata or {},
        )
    )


def _json_size(value: object) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False).encode())
    except TypeError:
        return len(str(value).encode())


def _estimate_tokens(value: object) -> int:
    return max(1, _json_size(value) // 4)
