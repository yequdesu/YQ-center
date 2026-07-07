"""YCR session-local working set state."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.models.operation import Operation
from yequ.models.ycr import YcrSessionState
from yequ.ycr.entities import metadata_from_result, normalize_entities

JsonDict = dict[str, object]

SESSION_STATE_LIMIT_PER_TYPE = 8


async def upsert_session_entity(
    db: AsyncSession,
    *,
    session_id: str | None,
    entity_type: str,
    entity_key: str,
    status: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    ref_id: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    trust_level: str = "node_reported_fact",
    weight: int = 0,
    data: JsonDict | None = None,
) -> YcrSessionState | None:
    if not session_id or not entity_key.strip() or not entity_type.strip():
        return None
    now = datetime.now(UTC)
    result = await db.execute(
        select(YcrSessionState).where(
            YcrSessionState.session_id == session_id,
            YcrSessionState.entity_type == entity_type,
            YcrSessionState.entity_key == entity_key,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        record = YcrSessionState(
            session_id=session_id,
            entity_type=entity_type,
            entity_key=entity_key,
            trust_level=trust_level,
        )
        db.add(record)
    record.status = status
    record.title = _bounded_text(title, 220)
    record.summary = _bounded_text(summary, 500)
    record.ref_id = ref_id
    record.source_type = source_type
    record.source_id = source_id
    record.trust_level = trust_level
    record.weight = int(weight)
    record.data_json = _jsonable_dict(data or {})
    record.last_seen_at = now
    await db.flush()
    return record


async def ingest_tool_observation_state(
    db: AsyncSession,
    *,
    session_id: str | None,
    name: str,
    status: str,
    result: object,
    raw_ref: dict[str, object],
    target_node_id: object | None = None,
) -> None:
    if not session_id:
        return
    ref_id = _string(raw_ref.get("ref_id"))
    metadata = metadata_from_result(result)
    entities = normalize_entities(metadata.get("ycr_entities") if metadata else {})
    for capability in _list_of_dicts(entities.get("capabilities")):
        capability_ref = _string(
            capability.get("source_id")
            or capability.get("capability_ref")
            or capability.get("canonical_name")
        )
        if not capability_ref:
            continue
        node_id = _string(capability.get("node_id"))
        await upsert_session_entity(
            db,
            session_id=session_id,
            entity_type="capability",
            entity_key=capability_ref,
            status="ready" if capability.get("dispatchable") else "known",
            title=_string(capability.get("canonical_name")),
            summary=_string(capability.get("description")),
            ref_id=ref_id,
            source_type="tool_call",
            source_id=_string(raw_ref.get("source_anchor", {}).get("id"))
            if isinstance(raw_ref.get("source_anchor"), dict)
            else None,
            data={
                "capability_ref": capability.get("capability_ref"),
                "canonical_name": capability.get("canonical_name"),
                "source_id": capability.get("source_id"),
                "node_id": node_id,
                "registered_name": capability.get("registered_name"),
                "risk": capability.get("risk"),
                "effect": capability.get("effect"),
            },
            weight=70 if capability.get("dispatchable") else 40,
        )
        if node_id:
            await _upsert_node_fact(
                db,
                session_id=session_id,
                node_id=node_id,
                ref_id=ref_id,
                status="known",
                source_name=name,
            )

    if not isinstance(result, dict):
        return
    for artifact in _list_of_dicts(result.get("artifacts")):
        await _upsert_artifact_fact(
            db,
            session_id=session_id,
            artifact=artifact,
            ref_id=ref_id,
            status=status,
            source_name=name,
        )
    artifact = result.get("artifact")
    if isinstance(artifact, dict):
        await _upsert_artifact_fact(
            db,
            session_id=session_id,
            artifact=artifact,
            ref_id=ref_id,
            status=status,
            source_name=name,
        )
    operation = result.get("operation")
    if isinstance(operation, dict):
        await _upsert_operation_fact(
            db,
            session_id=session_id,
            operation=operation,
            ref_id=ref_id,
            source_name=name,
        )
    for node in _list_of_dicts(result.get("nodes")):
        node_id = _string(node.get("node_id") or node.get("id"))
        if node_id:
            await _upsert_node_fact(
                db,
                session_id=session_id,
                node_id=node_id,
                ref_id=ref_id,
                status=_string(node.get("status") or node.get("runtime_status") or "known"),
                source_name=name,
                data=node,
            )
    node_id = _string(result.get("node_id") or target_node_id)
    if node_id:
        await _upsert_node_fact(
            db,
            session_id=session_id,
            node_id=node_id,
            ref_id=ref_id,
            status=_string(result.get("status") or "known"),
            source_name=name,
            data={key: value for key, value in result.items() if key != "ycr_entities"},
        )


async def ingest_operation_event_state(
    db: AsyncSession,
    *,
    operation: Operation,
    event_id: str | None = None,
) -> None:
    if not operation.session_id:
        return
    await upsert_session_entity(
        db,
        session_id=operation.session_id,
        entity_type="operation",
        entity_key=operation.operation_id,
        status=operation.status,
        title=operation.title,
        summary=operation.progress_message or operation.error_message,
        ref_id=None,
        source_type=operation.ref_type,
        source_id=operation.ref_id,
        data={
            "operation_id": operation.operation_id,
            "kind": operation.kind,
            "status": operation.status,
            "ref_type": operation.ref_type,
            "ref_id": operation.ref_id,
            "event_id": event_id,
            "progress_pct": operation.progress_pct,
            "progress_message": operation.progress_message,
            "error_code": operation.error_code,
            "error_message": operation.error_message,
        },
        weight=90 if operation.status in {"succeeded", "failed", "cancelled", "timeout"} else 60,
    )


async def load_session_state(
    db: AsyncSession,
    *,
    session_id: str,
    limit_per_type: int = SESSION_STATE_LIMIT_PER_TYPE,
) -> JsonDict:
    result = await db.execute(
        select(YcrSessionState)
        .where(YcrSessionState.session_id == session_id)
        .order_by(
            YcrSessionState.entity_type.asc(),
            desc(YcrSessionState.weight),
            desc(YcrSessionState.last_seen_at),
            desc(YcrSessionState.updated_at),
        )
    )
    grouped: dict[str, list[JsonDict]] = {}
    for record in result.scalars().all():
        items = grouped.setdefault(record.entity_type, [])
        if len(items) >= limit_per_type:
            continue
        items.append(session_state_item(record))
    return {
        "session_id": session_id,
        "items": grouped,
        "counts": {entity_type: len(items) for entity_type, items in grouped.items()},
    }


async def session_state_stats(db: AsyncSession, *, session_id: str | None = None) -> JsonDict:
    stmt = select(YcrSessionState.entity_type, func.count(YcrSessionState.id)).group_by(
        YcrSessionState.entity_type
    )
    if session_id:
        stmt = stmt.where(YcrSessionState.session_id == session_id)
    result = await db.execute(stmt)
    return {
        "session_id": session_id,
        "counts": {str(row[0]): int(row[1]) for row in result.all()},
    }


def session_state_item(record: YcrSessionState) -> JsonDict:
    return {
        "entity_type": record.entity_type,
        "entity_key": record.entity_key,
        "status": record.status,
        "title": record.title,
        "summary": record.summary,
        "ref_id": record.ref_id,
        "source_type": record.source_type,
        "source_id": record.source_id,
        "trust_level": record.trust_level,
        "weight": record.weight,
        "last_seen_at": record.last_seen_at.isoformat() if record.last_seen_at else None,
        "data": record.data_json or {},
    }


async def _upsert_artifact_fact(
    db: AsyncSession,
    *,
    session_id: str,
    artifact: dict[str, Any],
    ref_id: str | None,
    status: str,
    source_name: str,
) -> None:
    artifact_id = _string(artifact.get("artifact_id") or artifact.get("id"))
    if not artifact_id:
        return
    artifact_data = {
        "artifact_id": artifact_id,
        "artifact_type": artifact.get("artifact_type"),
        "title": artifact.get("title"),
        "content_type": artifact.get("content_type"),
        "size_bytes": artifact.get("size_bytes"),
        "node_id": artifact.get("node_id"),
    }
    await upsert_session_entity(
        db,
        session_id=session_id,
        entity_type="artifact",
        entity_key=artifact_id,
        status=_string(artifact.get("status") or status),
        title=_string(artifact.get("title") or artifact.get("filename")),
        summary=_string(artifact.get("summary") or artifact.get("content_type")),
        ref_id=ref_id,
        source_type="tool_result",
        source_id=source_name,
        data=artifact_data,
        weight=80,
    )
    await _upsert_artifact_focus(
        db,
        session_id=session_id,
        artifact_id=artifact_id,
        artifact_data=artifact_data,
        ref_id=ref_id,
        source_name=source_name,
    )


async def _upsert_artifact_focus(
    db: AsyncSession,
    *,
    session_id: str,
    artifact_id: str,
    artifact_data: JsonDict,
    ref_id: str | None,
    source_name: str,
) -> None:
    focus_data = {
        "focus_kind": "artifact",
        "artifact_id": artifact_id,
        "reason": "last_presented_to_user"
        if source_name == "artifact.present"
        else "last_artifact_result",
        **artifact_data,
    }
    await upsert_session_entity(
        db,
        session_id=session_id,
        entity_type="focus",
        entity_key="last_artifact",
        status="active",
        title=_string(artifact_data.get("title")) or artifact_id,
        summary=_string(artifact_data.get("content_type")),
        ref_id=ref_id,
        source_type="tool_result",
        source_id=source_name,
        data=focus_data,
        weight=100,
    )
    if source_name == "artifact.present":
        await upsert_session_entity(
            db,
            session_id=session_id,
            entity_type="focus",
            entity_key="current_artifact",
            status="active",
            title=_string(artifact_data.get("title")) or artifact_id,
            summary=_string(artifact_data.get("content_type")),
            ref_id=ref_id,
            source_type="tool_result",
            source_id=source_name,
            data=focus_data,
            weight=110,
        )


async def _upsert_operation_fact(
    db: AsyncSession,
    *,
    session_id: str,
    operation: dict[str, Any],
    ref_id: str | None,
    source_name: str,
) -> None:
    operation_id = _string(operation.get("operation_id") or operation.get("id"))
    if not operation_id:
        return
    await upsert_session_entity(
        db,
        session_id=session_id,
        entity_type="operation",
        entity_key=operation_id,
        status=_string(operation.get("status")),
        title=_string(operation.get("title")),
        summary=_string(operation.get("progress_message") or operation.get("error_message")),
        ref_id=ref_id,
        source_type="tool_result",
        source_id=source_name,
        data={
            "operation_id": operation_id,
            "kind": operation.get("kind"),
            "status": operation.get("status"),
            "ref_type": operation.get("ref_type"),
            "ref_id": operation.get("ref_id"),
            "progress_pct": operation.get("progress_pct"),
            "error_code": operation.get("error_code"),
            "error_message": operation.get("error_message"),
        },
        weight=85,
    )


async def _upsert_node_fact(
    db: AsyncSession,
    *,
    session_id: str,
    node_id: str,
    ref_id: str | None,
    status: str | None,
    source_name: str,
    data: dict[str, Any] | None = None,
) -> None:
    await upsert_session_entity(
        db,
        session_id=session_id,
        entity_type="node",
        entity_key=node_id,
        status=status,
        title=node_id,
        summary=_string((data or {}).get("platform") or (data or {}).get("platform_os")),
        ref_id=ref_id,
        source_type="tool_result",
        source_id=source_name,
        data={"node_id": node_id, **_jsonable_dict(data or {})},
        weight=50,
    )


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bounded_text(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    return value if len(value) <= limit else value[: max(0, limit - 3)] + "..."


def _jsonable_dict(value: dict[str, Any]) -> JsonDict:
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}
