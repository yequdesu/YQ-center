"""Typed YCR entity metadata helpers."""

from __future__ import annotations

from typing import Any

JsonDict = dict[str, object]

WORKING_SET_LIMIT = 12


def capability_entities(capabilities: list[dict[str, object]]) -> JsonDict:
    items = [
        entity
        for capability in capabilities
        if (entity := capability_entity(capability)) is not None
    ]
    return {"capabilities": items}


def capability_entity(capability: dict[str, object]) -> JsonDict | None:
    canonical_name = _string_or_none(capability.get("canonical_name"))
    if not canonical_name:
        return None
    invoke = capability.get("invoke") if isinstance(capability.get("invoke"), dict) else {}
    sources = capability.get("sources") if isinstance(capability.get("sources"), list) else []
    source = next((item for item in sources if isinstance(item, dict)), {})
    source_id = _string_or_none(invoke.get("source_id") or source.get("source_id"))
    node_id = _string_or_none(invoke.get("node_id") or source.get("node_id"))
    return {
        "entity_type": "capability",
        "capability_ref": _string_or_none(invoke.get("capability_ref")) or canonical_name,
        "canonical_name": canonical_name,
        "source_id": source_id,
        "node_id": node_id,
        "registered_name": _string_or_none(
            invoke.get("registered_name") or source.get("registered_name")
        ),
        "dispatchable": bool(
            invoke.get("dispatchable_source_count", 0)
            or source.get("dispatchable")
            or capability.get("dispatchable")
        ),
        "risk": capability.get("risk"),
        "effect": capability.get("effect"),
        "description": _truncate(_string_or_none(capability.get("description")) or "", 220),
    }


def attach_ycr_entities(
    result: dict[str, object],
    *,
    capabilities: list[dict[str, object]],
) -> dict[str, object]:
    output = dict(result)
    output["ycr_entities"] = capability_entities(capabilities)
    return output


def metadata_from_result(result: object) -> JsonDict:
    if not isinstance(result, dict):
        return {}
    entities = result.get("ycr_entities")
    if not isinstance(entities, dict):
        return {}
    normalized = normalize_entities(entities)
    return {"ycr_entities": normalized} if normalized else {}


def observation_entities_from_result(result: object) -> JsonDict:
    if not isinstance(result, dict):
        return {}
    entities: JsonDict = {}
    normalized = normalize_entities(result.get("ycr_entities"))
    if normalized:
        entities.update(normalized)
    artifacts = _artifact_entities_from_result(result)
    if artifacts:
        entities["artifacts"] = artifacts
    operation = result.get("operation")
    if isinstance(operation, dict):
        operation_id = _string_or_none(operation.get("operation_id"))
        if operation_id:
            entities["operations"] = [
                {
                    "entity_type": "operation",
                    "operation_id": operation_id,
                    "kind": _string_or_none(operation.get("kind")),
                    "status": _string_or_none(operation.get("status")),
                    "title": _truncate(_string_or_none(operation.get("title")) or "", 220),
                }
            ]
    return entities


def strip_ycr_entities(result: object) -> object:
    if not isinstance(result, dict) or "ycr_entities" not in result:
        return result
    output = dict(result)
    output.pop("ycr_entities", None)
    return output


def working_set_from_metadata(metadata: object) -> list[JsonDict]:
    if not isinstance(metadata, dict):
        return []
    entities = normalize_entities(metadata.get("ycr_entities"))
    capabilities = entities.get("capabilities") if isinstance(entities, dict) else []
    if not isinstance(capabilities, list):
        return []
    return [
        item
        for item in capabilities
        if isinstance(item, dict) and item.get("dispatchable")
    ][:WORKING_SET_LIMIT]


def normalize_entities(value: object) -> JsonDict:
    if not isinstance(value, dict):
        return {}
    capabilities = value.get("capabilities")
    if not isinstance(capabilities, list):
        return {}
    normalized: list[JsonDict] = []
    seen: set[str] = set()
    for item in capabilities:
        if not isinstance(item, dict):
            continue
        entity = _normalize_capability_entity(item)
        if entity is None:
            continue
        key = str(entity.get("source_id") or "") or (
            f"{entity.get('canonical_name')}@{entity.get('node_id')}"
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(entity)
    return {"capabilities": normalized}


def _normalize_capability_entity(value: dict[str, Any]) -> JsonDict | None:
    canonical_name = _string_or_none(value.get("canonical_name"))
    if not canonical_name:
        return None
    return {
        "entity_type": "capability",
        "capability_ref": _string_or_none(value.get("capability_ref")) or canonical_name,
        "canonical_name": canonical_name,
        "source_id": _string_or_none(value.get("source_id")),
        "node_id": _string_or_none(value.get("node_id")),
        "registered_name": _string_or_none(value.get("registered_name")),
        "dispatchable": bool(value.get("dispatchable")),
        "risk": value.get("risk"),
        "effect": value.get("effect"),
        "description": _truncate(_string_or_none(value.get("description")) or "", 220),
    }


def _artifact_entities_from_result(result: dict[str, object]) -> list[JsonDict]:
    artifacts: list[JsonDict] = []
    for item in _artifact_candidates(result):
        artifact_id = _string_or_none(item.get("artifact_id"))
        if not artifact_id:
            continue
        artifacts.append(
            {
                "entity_type": "artifact",
                "artifact_id": artifact_id,
                "artifact_type": _string_or_none(item.get("artifact_type") or item.get("kind")),
                "title": _truncate(_string_or_none(item.get("title")) or "", 220),
                "content_type": _string_or_none(item.get("content_type")),
                "node_id": _string_or_none(item.get("node_id")),
                "status": _string_or_none(item.get("status")),
                "size_bytes": (
                    item.get("size_bytes") if isinstance(item.get("size_bytes"), int) else None
                ),
                "summary": item.get("summary") if isinstance(item.get("summary"), dict) else {},
            }
        )
    return artifacts[:WORKING_SET_LIMIT]


def _artifact_candidates(result: dict[str, object]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    artifacts = result.get("artifacts")
    if isinstance(artifacts, list):
        candidates.extend(item for item in artifacts if isinstance(item, dict))
    artifact = result.get("artifact")
    if isinstance(artifact, dict):
        candidates.append(artifact)
    return candidates


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."
