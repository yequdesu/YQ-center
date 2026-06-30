"""Center-owned capability registry v2 services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from yequ.models.capability_runtime import CapabilityDefinition, CapabilitySource
from yequ.models.node import Node
from yequ.protocol import NodeStatus
from yequ.shared_types import JsonObject

PLATFORM_PREFIXES = {
    "linux",
    "windows",
    "win",
    "darwin",
    "macos",
    "mac",
}


@dataclass(frozen=True, slots=True)
class CapabilityInvocationTarget:
    """Concrete execution target resolved from the v2 registry."""

    capability_id: str
    canonical_name: str
    source_id: str
    registered_name: str
    node_id: str
    risk: str
    effect: str
    timeout_sec: int | None


async def sync_capability_runtime_snapshot(
    db: AsyncSession,
    node: Node,
    plugins: list[JsonObject],
    *,
    now: datetime,
) -> None:
    """Synchronize Node capability snapshot into the v2 registry.

    Snapshot semantics are scoped to the reporting Node. Existing active sources
    for that node are marked inactive first, then the reported functions and
    signals are written as active sources linked to semantic definitions.
    """

    touched_definition_ids: set[str] = set()

    existing_sources = await db.execute(
        select(CapabilitySource).where(
            CapabilitySource.node_record_id == node.id,
            CapabilitySource.is_active == True,  # noqa: E712
        )
    )
    for source in existing_sources.scalars().all():
        source.is_active = False
        source.status = "inactive"
        source.unavailable_reason = "replaced_by_snapshot"
        touched_definition_ids.add(source.definition_id)

    for plugin in plugins:
        plugin_id = str(plugin["plugin_id"])
        plugin_version = str(plugin.get("plugin_version") or "0.0.0")
        plugin_status = str(plugin.get("status") or "loaded")

        if plugin_status == "error":
            continue

        for fn in plugin.get("functions", []):
            definition, source = await _upsert_source(
                db,
                node,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                plugin_status=plugin_status,
                capability_type="function",
                manifest=fn,
                now=now,
            )
            touched_definition_ids.add(definition.id)
            touched_definition_ids.add(source.definition_id)

        for sig in plugin.get("signals", []):
            definition, source = await _upsert_source(
                db,
                node,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                plugin_status=plugin_status,
                capability_type="signal",
                manifest=sig,
                now=now,
            )
            touched_definition_ids.add(definition.id)
            touched_definition_ids.add(source.definition_id)

    await _refresh_definition_statuses(db, touched_definition_ids)


async def node_list(db: AsyncSession) -> list[JsonObject]:
    """Return compact Node status for Center meta tools."""

    result = await db.execute(select(Node).order_by(Node.node_id))
    nodes = list(result.scalars().all())
    output: list[JsonObject] = []
    for node in nodes:
        output.append(await node_status(db, node.node_id, node=node))
    return output


async def node_status(
    db: AsyncSession,
    node_id: str,
    *,
    node: Node | None = None,
) -> JsonObject:
    """Return detailed status for one Node and its v2 capability sources."""

    if node is None:
        result = await db.execute(select(Node).where(Node.node_id == node_id))
        node = result.scalar_one_or_none()
    if node is None:
        raise ValueError(f"Node {node_id!r} not found")

    sources_result = await db.execute(
        select(CapabilitySource, CapabilityDefinition)
        .join(CapabilityDefinition, CapabilitySource.definition_id == CapabilityDefinition.id)
        .where(CapabilitySource.node_record_id == node.id)
        .order_by(CapabilityDefinition.canonical_name, CapabilitySource.registered_name)
    )
    sources = [
        _source_summary(source, definition, node)
        for source, definition in sources_result.all()
        if source.is_active
    ]

    return {
        "node_id": node.node_id,
        "node_name": node.node_name,
        "status": node.status,
        "online": node.status == NodeStatus.ONLINE,
        "platform_os": node.platform_os,
        "platform_arch": node.platform_arch,
        "last_seen_at": node.last_seen_at.isoformat() if node.last_seen_at else None,
        "last_heartbeat_at": (
            node.last_heartbeat_at.isoformat() if node.last_heartbeat_at else None
        ),
        "capability_source_count": len(sources),
        "capability_sources": sources,
    }


async def capability_search(
    db: AsyncSession,
    *,
    query: str | None = None,
    node_id: str | None = None,
    platform_os: str | None = None,
    effect: str | None = None,
    risk: str | None = None,
    capability_type: str = "function",
    include_inactive: bool = False,
    limit: int = 20,
) -> list[JsonObject]:
    """Deterministic registry search used before any vector/embedding layer."""

    stmt = (
        select(CapabilityDefinition)
        .options(joinedload(CapabilityDefinition.sources).joinedload(CapabilitySource.node))
        .where(CapabilityDefinition.capability_type == capability_type)
        .order_by(CapabilityDefinition.canonical_name)
    )
    if effect:
        stmt = stmt.where(CapabilityDefinition.effect == effect)
    if risk:
        stmt = stmt.where(CapabilityDefinition.risk == risk)

    result = await db.execute(stmt)
    definitions = result.unique().scalars().all()
    terms = _terms(query)
    matches: list[JsonObject] = []

    for definition in definitions:
        sources = [
            source
            for source in definition.sources
            if _source_visible(
                source,
                node_id=node_id,
                platform_os=platform_os,
                include_inactive=include_inactive,
            )
        ]
        if not sources:
            continue
        if terms and not _definition_matches(definition, sources, terms):
            continue
        matches.append(_definition_search_summary(definition, sources))
        if len(matches) >= limit:
            break

    return matches


async def capability_describe(
    db: AsyncSession,
    capability_ref: str,
    *,
    node_id: str | None = None,
    include_inactive: bool = False,
) -> JsonObject:
    """Describe one capability definition by ID, canonical name, or alias."""

    result = await db.execute(
        select(CapabilityDefinition)
        .options(joinedload(CapabilityDefinition.sources).joinedload(CapabilitySource.node))
        .where(
            (CapabilityDefinition.capability_id == capability_ref)
            | (CapabilityDefinition.canonical_name == capability_ref)
        )
    )
    definition = result.unique().scalar_one_or_none()
    if definition is None:
        alias_result = await db.execute(
            select(CapabilityDefinition).options(
                joinedload(CapabilityDefinition.sources).joinedload(CapabilitySource.node)
            )
        )
        for candidate in alias_result.unique().scalars().all():
            if capability_ref in (candidate.aliases or []):
                definition = candidate
                break

    if definition is None:
        raise ValueError(f"Capability {capability_ref!r} not found")

    sources = [
        source
        for source in definition.sources
        if _source_visible(
            source,
            node_id=node_id,
            platform_os=None,
            include_inactive=include_inactive,
        )
    ]
    return _definition_detail(definition, sources)


async def resolve_capability_invoke_target(
    db: AsyncSession,
    *,
    capability_ref: str | None = None,
    source_id: str | None = None,
    node_id: str | None = None,
) -> CapabilityInvocationTarget:
    """Resolve capability.invoke input to exactly one active function source."""

    if not capability_ref and not source_id:
        raise ValueError("capability_ref or source_id is required")

    stmt = (
        select(CapabilitySource, CapabilityDefinition, Node)
        .join(CapabilityDefinition, CapabilitySource.definition_id == CapabilityDefinition.id)
        .join(Node, CapabilitySource.node_record_id == Node.id)
        .where(
            CapabilityDefinition.capability_type == "function",
            CapabilitySource.is_active == True,  # noqa: E712
        )
    )
    if source_id:
        stmt = stmt.where(CapabilitySource.source_id == source_id)
    if node_id:
        stmt = stmt.where(Node.node_id == node_id)

    result = await db.execute(stmt)
    rows = list(result.all())
    if capability_ref:
        rows = [
            row
            for row in rows
            if _matches_capability_ref(row[0], row[1], capability_ref)
        ]

    if not rows:
        ref = source_id or capability_ref or ""
        raise ValueError(f"No active capability source matches {ref!r}")

    if len(rows) > 1:
        candidates = [
            {
                "source_id": source.source_id,
                "node_id": node.node_id,
                "registered_name": source.registered_name,
                "canonical_name": definition.canonical_name,
            }
            for source, definition, node in rows
        ]
        raise ValueError(
            "Capability reference is ambiguous; specify source_id or node_id. "
            f"Candidates: {candidates}"
        )

    source, definition, node = rows[0]
    return CapabilityInvocationTarget(
        capability_id=definition.capability_id,
        canonical_name=definition.canonical_name,
        source_id=source.source_id,
        registered_name=source.registered_name,
        node_id=node.node_id,
        risk=definition.risk or "safe",
        effect=definition.effect or "read",
        timeout_sec=source.timeout_sec,
    )


async def _upsert_source(
    db: AsyncSession,
    node: Node,
    *,
    plugin_id: str,
    plugin_version: str,
    plugin_status: str,
    capability_type: str,
    manifest: JsonObject,
    now: datetime,
) -> tuple[CapabilityDefinition, CapabilitySource]:
    registered_name = str(manifest["name"])
    canonical_name = _canonical_name(registered_name, node.platform_os)

    definition = await _get_or_create_definition(
        db,
        canonical_name=canonical_name,
        capability_type=capability_type,
    )
    _merge_definition_manifest(definition, registered_name, manifest, capability_type)

    result = await db.execute(
        select(CapabilitySource).where(
            CapabilitySource.node_record_id == node.id,
            CapabilitySource.plugin_id == plugin_id,
            CapabilitySource.registered_name == registered_name,
            CapabilitySource.definition_id == definition.id,
        )
    )
    source = result.scalar_one_or_none()
    if source is None:
        source = CapabilitySource(
            definition_id=definition.id,
            node_record_id=node.id,
            plugin_id=plugin_id,
            plugin_version=plugin_version,
            registered_name=registered_name,
            registered_at=now,
        )
        db.add(source)

    source.plugin_version = plugin_version
    source.platform_os = node.platform_os
    source.platform_arch = node.platform_arch
    source.status = plugin_status
    source.is_active = True
    source.unavailable_reason = None
    source.runtime_id = _string_or_none(manifest.get("runtime_id"))
    source.execution_requirements = _json_object_or_none(
        manifest.get("execution_requirements")
        or _execution_requirements_from_context(_string_or_none(manifest.get("execution_context")))
    )
    source.timeout_sec = _int_or_none(manifest.get("timeout_sec"))
    source.idempotency = _string_or_none(manifest.get("idempotency"))
    source.resource_keys = _list_of_strings_or_none(manifest.get("resource_keys"))
    source.conflict_policy = _string_or_none(manifest.get("conflict_policy"))
    source.hidden_input_fields = _list_of_strings_or_none(manifest.get("hidden_input_fields"))
    source.failure_modes = _list_of_dicts_or_none(manifest.get("failure_modes"))
    source.preflight_supported = bool(
        manifest.get("preflight_supported")
        or manifest.get("dry_run_supported")
        or "dry_run" in (manifest.get("input_schema") or {}).get("properties", {})
    )
    source.scope = _string_or_none(manifest.get("scope"))
    source.ttl_sec = _int_or_none(manifest.get("ttl_sec"))
    source.registered_at = now
    return definition, source


async def _get_or_create_definition(
    db: AsyncSession,
    *,
    canonical_name: str,
    capability_type: str,
) -> CapabilityDefinition:
    result = await db.execute(
        select(CapabilityDefinition).where(
            CapabilityDefinition.canonical_name == canonical_name,
            CapabilityDefinition.capability_type == capability_type,
        )
    )
    definition = result.scalar_one_or_none()
    if definition is not None:
        return definition
    definition = CapabilityDefinition(
        canonical_name=canonical_name,
        capability_type=capability_type,
        aliases=[],
        examples=[],
        tags=[],
        artifact_inputs=[],
        artifact_outputs=[],
        status="active",
    )
    db.add(definition)
    await db.flush()
    return definition


def _merge_definition_manifest(
    definition: CapabilityDefinition,
    registered_name: str,
    manifest: JsonObject,
    capability_type: str,
) -> None:
    definition.display_name = (
        _string_or_none(manifest.get("user_visible_name")) or definition.display_name
    )
    definition.description = _string_or_none(manifest.get("description")) or definition.description
    definition.agent_description = (
        _string_or_none(manifest.get("agent_description")) or definition.agent_description
    )
    if capability_type == "function":
        definition.input_schema = (
            _json_object_or_none(manifest.get("input_schema")) or definition.input_schema
        )
        definition.output_schema = (
            _json_object_or_none(manifest.get("output_schema")) or definition.output_schema
        )
        definition.risk = _string_or_none(manifest.get("risk")) or definition.risk or "safe"
        definition.effect = _string_or_none(manifest.get("effect")) or definition.effect or "read"
    else:
        definition.value_schema = (
            _json_object_or_none(manifest.get("value_schema")) or definition.value_schema
        )
    definition.artifact_inputs = _list_of_dicts(manifest.get("artifact_inputs"))
    artifact_outputs = _list_of_dicts(manifest.get("artifact_outputs"))
    definition.artifact_outputs = artifact_outputs or _infer_artifact_outputs(
        definition.output_schema,
        registered_name=registered_name,
    )
    definition.examples = _list_of_dicts(manifest.get("examples"))
    definition.tags = sorted(
        {
            *[str(tag) for tag in definition.tags or []],
            *_tags_from_name(definition.canonical_name),
            *[str(tag) for tag in manifest.get("tags", []) if isinstance(tag, str)],
        }
    )
    aliases = {str(alias) for alias in definition.aliases or []}
    aliases.add(registered_name)
    aliases.discard(definition.canonical_name)
    definition.aliases = sorted(aliases)
    definition.status = "active"


async def _refresh_definition_statuses(
    db: AsyncSession,
    definition_ids: set[str],
) -> None:
    for definition_id in definition_ids:
        result = await db.execute(
            select(CapabilitySource).where(
                CapabilitySource.definition_id == definition_id,
                CapabilitySource.is_active == True,  # noqa: E712
            )
        )
        has_active_source = result.scalars().first() is not None
        definition = await db.get(CapabilityDefinition, definition_id)
        if definition is not None:
            definition.status = "active" if has_active_source else "inactive"


def _definition_search_summary(
    definition: CapabilityDefinition,
    sources: list[CapabilitySource],
) -> JsonObject:
    return {
        "capability_id": definition.capability_id,
        "canonical_name": definition.canonical_name,
        "display_name": definition.display_name,
        "capability_type": definition.capability_type,
        "description": definition.description,
        "risk": definition.risk,
        "effect": definition.effect,
        "tags": list(definition.tags or []),
        "aliases": list(definition.aliases or []),
        "source_count": len(sources),
        "sources": [
            {
                "source_id": source.source_id,
                "node_id": source.node.node_id if source.node else "",
                "registered_name": source.registered_name,
                "platform_os": source.platform_os,
                "runtime_id": source.runtime_id,
                "status": source.status,
            }
            for source in sources
        ],
    }


def _definition_detail(
    definition: CapabilityDefinition,
    sources: list[CapabilitySource],
) -> JsonObject:
    data = _definition_search_summary(definition, sources)
    data.update(
        {
            "agent_description": definition.agent_description,
            "input_schema": definition.input_schema,
            "output_schema": definition.output_schema,
            "value_schema": definition.value_schema,
            "artifact_inputs": list(definition.artifact_inputs or []),
            "artifact_outputs": list(definition.artifact_outputs or []),
            "examples": list(definition.examples or []),
            "status": definition.status,
            "sources": [
                _source_summary(source, definition, source.node)
                for source in sorted(
                    sources,
                    key=lambda item: (
                        item.node.node_id if item.node else "",
                        item.registered_name,
                    ),
                )
            ],
        }
    )
    return data


def _infer_artifact_outputs(
    output_schema: JsonObject | None,
    *,
    registered_name: str,
) -> list[JsonObject]:
    if not isinstance(output_schema, dict):
        return []
    properties = output_schema.get("properties")
    if not isinstance(properties, dict) or "artifacts" not in properties:
        return []
    return [
        {
            "field": "artifacts",
            "kind": "center_artifact_reference",
            "description": (
                "Tool output may contain Center artifact references with "
                "artifact_id, content_type, size_bytes, and download_url."
            ),
            "producer": registered_name,
        }
    ]


def _source_summary(
    source: CapabilitySource,
    definition: CapabilityDefinition,
    node: Node | None,
) -> JsonObject:
    return {
        "source_id": source.source_id,
        "capability_id": definition.capability_id,
        "canonical_name": definition.canonical_name,
        "registered_name": source.registered_name,
        "node_id": node.node_id if node else "",
        "plugin_id": source.plugin_id,
        "plugin_version": source.plugin_version,
        "runtime_id": source.runtime_id,
        "platform_os": source.platform_os,
        "platform_arch": source.platform_arch,
        "status": source.status,
        "is_active": source.is_active,
        "unavailable_reason": source.unavailable_reason,
        "execution_requirements": source.execution_requirements,
        "risk": definition.risk,
        "effect": definition.effect,
        "timeout_sec": source.timeout_sec,
        "resource_keys": list(source.resource_keys or []),
        "conflict_policy": source.conflict_policy,
        "preflight_supported": source.preflight_supported,
        "hidden_input_fields": list(source.hidden_input_fields or []),
    }


def _source_visible(
    source: CapabilitySource,
    *,
    node_id: str | None,
    platform_os: str | None,
    include_inactive: bool,
) -> bool:
    if not include_inactive and not source.is_active:
        return False
    if node_id and (source.node is None or source.node.node_id != node_id):
        return False
    return not (platform_os and (source.platform_os or "").lower() != platform_os.lower())


def _definition_matches(
    definition: CapabilityDefinition,
    sources: list[CapabilitySource],
    terms: list[str],
) -> bool:
    haystack_parts = [
        definition.canonical_name,
        definition.display_name or "",
        definition.description or "",
        definition.agent_description or "",
        " ".join(definition.aliases or []),
        " ".join(definition.tags or []),
        " ".join(source.registered_name for source in sources),
        " ".join(source.node.node_id for source in sources if source.node),
        " ".join(source.platform_os or "" for source in sources),
    ]
    haystack = " ".join(haystack_parts).lower()
    return all(term in haystack for term in terms)


def _matches_capability_ref(
    source: CapabilitySource,
    definition: CapabilityDefinition,
    capability_ref: str,
) -> bool:
    return capability_ref in {
        definition.capability_id,
        definition.canonical_name,
        source.source_id,
        source.registered_name,
        *(definition.aliases or []),
    }


def _canonical_name(registered_name: str, platform_os: str | None) -> str:
    parts = registered_name.split(".", 1)
    if len(parts) != 2:
        return registered_name
    prefix = parts[0].lower()
    os_name = (platform_os or "").lower()
    if prefix in PLATFORM_PREFIXES or (os_name and prefix == os_name):
        return parts[1]
    return registered_name


def _execution_requirements_from_context(context: str | None) -> JsonObject | None:
    if context == "system":
        return {"runtime_kind": "privileged"}
    if context == "user":
        return {"runtime_kind": "interactive", "interactive": True}
    if context == "hybrid":
        return {"allowed_runtime_kinds": ["interactive", "privileged"]}
    return None


def _tags_from_name(name: str) -> list[str]:
    return [part for part in name.replace("_", ".").split(".") if part]


def _terms(query: str | None) -> list[str]:
    return [part.lower() for part in (query or "").strip().split() if part.strip()]


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _json_object_or_none(value: object) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def _list_of_strings_or_none(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def _list_of_dicts_or_none(value: object) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    return [dict(item) for item in value if isinstance(item, dict)]


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    return _list_of_dicts_or_none(value) or []
