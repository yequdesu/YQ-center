"""Center-owned capability registry v2 services."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from yequ.models.base import generate_uuid
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

    await _lock_registry_key(db, f"capability-snapshot:{node.id}")
    db.sync_session.autoflush = False

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
    runtime_kind: str | None = None,
    runtime_labels: list[str] | None = None,
    supports_progress: bool | None = None,
    supports_cancel: bool | None = None,
    supports_resume: bool | None = None,
    preflight_supported: bool | None = None,
    artifact_input: bool | None = None,
    artifact_output: bool | None = None,
    projection: str = "summary",
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
        .execution_options(populate_existing=True)
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
        if artifact_input is not None and bool(definition.artifact_inputs) != artifact_input:
            continue
        if artifact_output is not None and bool(definition.artifact_outputs) != artifact_output:
            continue
        sources = [
            source
            for source in definition.sources
            if _source_visible(
                source,
                node_id=node_id,
                platform_os=platform_os,
                runtime_kind=runtime_kind,
                runtime_labels=runtime_labels,
                supports_progress=supports_progress,
                supports_cancel=supports_cancel,
                supports_resume=supports_resume,
                preflight_supported=preflight_supported,
                include_inactive=include_inactive,
            )
        ]
        if not sources:
            continue
        if terms and not _definition_matches(definition, sources, terms):
            continue
        matches.append(
            _definition_search_summary(
                definition,
                sources,
                projection=projection,
                terms=terms,
                filters={
                    "node_id": node_id,
                    "platform_os": platform_os,
                    "effect": effect,
                    "risk": risk,
                    "runtime_kind": runtime_kind,
                    "runtime_labels": runtime_labels,
                    "supports_progress": supports_progress,
                    "supports_cancel": supports_cancel,
                    "supports_resume": supports_resume,
                    "preflight_supported": preflight_supported,
                    "artifact_input": artifact_input,
                    "artifact_output": artifact_output,
                },
            )
        )
        if len(matches) >= _bounded_limit(limit):
            break

    return matches


async def capability_describe(
    db: AsyncSession,
    capability_ref: str,
    *,
    node_id: str | None = None,
    sections: list[str] | None = None,
    projection: str = "detail",
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
        .execution_options(populate_existing=True)
    )
    definition = result.unique().scalar_one_or_none()
    if definition is None:
        alias_result = await db.execute(
            select(CapabilityDefinition)
            .options(joinedload(CapabilityDefinition.sources).joinedload(CapabilitySource.node))
            .execution_options(populate_existing=True)
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
            runtime_kind=None,
            runtime_labels=None,
            supports_progress=None,
            supports_cancel=None,
            supports_resume=None,
            preflight_supported=None,
            include_inactive=include_inactive,
        )
    ]
    return _definition_detail(definition, sources, sections=sections, projection=projection)


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

    source = await _get_or_create_source(
        db,
        node=node,
        definition=definition,
        plugin_id=plugin_id,
        plugin_version=plugin_version,
        registered_name=registered_name,
        now=now,
    )

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
    source.supports_progress = bool(manifest.get("supports_progress"))
    source.supports_cancel = bool(manifest.get("supports_cancel"))
    source.supports_resume = bool(manifest.get("supports_resume"))
    source.progress_contract = _string_or_none(manifest.get("progress_contract"))
    source.preconditions = _list_of_dicts_or_none(manifest.get("preconditions"))
    source.required_intent_slots = _list_of_strings_or_none(
        manifest.get("required_intent_slots")
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
    await _lock_registry_key(
        db,
        f"capability-definition:{capability_type}:{canonical_name}",
    )
    if db.get_bind().dialect.name == "postgresql":
        return await _load_definition_after_upsert(
            db,
            canonical_name=canonical_name,
            capability_type=capability_type,
        )

    result = await db.execute(
        select(CapabilityDefinition).where(
            CapabilityDefinition.canonical_name == canonical_name,
            CapabilityDefinition.capability_type == capability_type,
        )
    )
    definition = result.scalar_one_or_none()
    if definition is not None:
        return definition

    try:
        async with db.begin_nested():
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
    except IntegrityError:
        result = await db.execute(
            select(CapabilityDefinition).where(
                CapabilityDefinition.canonical_name == canonical_name,
                CapabilityDefinition.capability_type == capability_type,
            )
        )
        return result.scalar_one()


async def _get_or_create_source(
    db: AsyncSession,
    *,
    node: Node,
    definition: CapabilityDefinition,
    plugin_id: str,
    plugin_version: str,
    registered_name: str,
    now: datetime,
) -> CapabilitySource:
    await _lock_registry_key(
        db,
        (
            "capability-source:"
            f"{node.id}:{definition.id}:{plugin_id}:{registered_name}"
        ),
    )
    if db.get_bind().dialect.name == "postgresql":
        return await _load_source_after_upsert(
            db,
            node=node,
            definition=definition,
            plugin_id=plugin_id,
            plugin_version=plugin_version,
            registered_name=registered_name,
            now=now,
        )

    result = await db.execute(
        select(CapabilitySource).where(
            CapabilitySource.node_record_id == node.id,
            CapabilitySource.plugin_id == plugin_id,
            CapabilitySource.registered_name == registered_name,
            CapabilitySource.definition_id == definition.id,
        )
    )
    source = result.scalar_one_or_none()
    if source is not None:
        return source

    try:
        async with db.begin_nested():
            source = CapabilitySource(
                definition_id=definition.id,
                node_record_id=node.id,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                registered_name=registered_name,
                registered_at=now,
            )
            db.add(source)
            await db.flush()
            return source
    except IntegrityError:
        result = await db.execute(
            select(CapabilitySource).where(
                CapabilitySource.node_record_id == node.id,
                CapabilitySource.plugin_id == plugin_id,
                CapabilitySource.registered_name == registered_name,
                CapabilitySource.definition_id == definition.id,
            )
        )
        return result.scalar_one()


async def _lock_registry_key(db: AsyncSession, key: str) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})


async def _load_definition_after_upsert(
    db: AsyncSession,
    *,
    canonical_name: str,
    capability_type: str,
) -> CapabilityDefinition:
    for attempt in range(20):
        inserted_id = (
            await db.execute(
            pg_insert(CapabilityDefinition.__table__)
            .values(
                id=generate_uuid(),
                capability_id=generate_uuid(),
                canonical_name=canonical_name,
                capability_type=capability_type,
                aliases=[],
                examples=[],
                tags=[],
                artifact_inputs=[],
                artifact_outputs=[],
                status="active",
            )
            .on_conflict_do_nothing(
                index_elements=["canonical_name", "capability_type"]
            )
            .returning(CapabilityDefinition.__table__.c.id)
        )
        ).scalar_one_or_none()
        if inserted_id:
            definition = await db.get(CapabilityDefinition, inserted_id)
            if definition is not None:
                return definition
        row_id = (
            await db.execute(
                text(
                    """
                    SELECT id FROM capability_definitions
                    WHERE canonical_name = :canonical_name
                      AND capability_type = :capability_type
                    ORDER BY created_at, id
                    LIMIT 1
                    """
                ),
                {
                    "canonical_name": canonical_name,
                    "capability_type": capability_type,
                },
            )
        ).scalar_one_or_none()
        definition = await db.get(CapabilityDefinition, row_id) if row_id else None
        if definition is not None:
            return definition
        if attempt < 19:
            await asyncio.sleep(0.05)
    raise RuntimeError(
        f"capability definition upsert did not return {canonical_name}/{capability_type}"
    )


async def _load_source_after_upsert(
    db: AsyncSession,
    *,
    node: Node,
    definition: CapabilityDefinition,
    plugin_id: str,
    plugin_version: str,
    registered_name: str,
    now: datetime,
) -> CapabilitySource:
    for attempt in range(20):
        inserted_id = (
            await db.execute(
            pg_insert(CapabilitySource.__table__)
            .values(
                id=generate_uuid(),
                source_id=generate_uuid(),
                definition_id=definition.id,
                node_record_id=node.id,
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                registered_name=registered_name,
                registered_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "node_record_id",
                    "plugin_id",
                    "registered_name",
                    "definition_id",
                ]
            )
            .returning(CapabilitySource.__table__.c.id)
        )
        ).scalar_one_or_none()
        if inserted_id:
            source = await db.get(CapabilitySource, inserted_id)
            if source is not None:
                return source
        row_id = (
            await db.execute(
                text(
                    """
                    SELECT id FROM capability_sources
                    WHERE node_record_id = :node_record_id
                      AND plugin_id = :plugin_id
                      AND registered_name = :registered_name
                      AND definition_id = :definition_id
                    ORDER BY registered_at DESC, id
                    LIMIT 1
                    """
                ),
                {
                    "node_record_id": node.id,
                    "plugin_id": plugin_id,
                    "registered_name": registered_name,
                    "definition_id": definition.id,
                },
            )
        ).scalar_one_or_none()
        source = await db.get(CapabilitySource, row_id) if row_id else None
        if source is not None:
            return source

        fallback_id = (
            await db.execute(
                text(
                    """
                    SELECT id FROM capability_sources
                    WHERE node_record_id = :node_record_id
                      AND plugin_id = :plugin_id
                      AND registered_name = :registered_name
                    ORDER BY registered_at DESC, id
                    LIMIT 1
                    """
                ),
                {
                    "node_record_id": node.id,
                    "plugin_id": plugin_id,
                    "registered_name": registered_name,
                },
            )
        ).scalar_one_or_none()
        source = await db.get(CapabilitySource, fallback_id) if fallback_id else None
        if source is not None:
            return source
        if attempt < 19:
            await asyncio.sleep(0.05)
    raise RuntimeError(
        "capability source upsert did not return "
        f"{node.node_id}/{plugin_id}/{registered_name}/{definition.canonical_name}"
    )


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
    *,
    projection: str = "summary",
    terms: list[str] | None = None,
    filters: dict[str, object] | None = None,
) -> JsonObject:
    projection = _normalize_projection(projection, default="summary")
    data: JsonObject = {
        "capability_id": definition.capability_id,
        "canonical_name": definition.canonical_name,
        "capability_type": definition.capability_type,
        "risk": definition.risk,
        "effect": definition.effect,
        "source_count": len(sources),
        "match_reasons": _definition_match_reasons(
            definition,
            sources,
            terms or [],
            filters or {},
        ),
        "sources": [
            _source_projection(source, definition, projection=projection)
            for source in sources
        ],
    }
    if projection in {"summary", "invoke_ready", "schema", "diagnostics"}:
        data.update(
            {
                "display_name": definition.display_name,
                "description": definition.description,
                "tags": list(definition.tags or []),
            }
        )
    if projection in {"invoke_ready", "schema", "diagnostics"}:
        data["aliases"] = list(definition.aliases or [])
    if projection == "schema":
        data.update(
            {
                "agent_description": definition.agent_description,
                "input_schema": definition.input_schema,
                "output_schema": definition.output_schema,
                "value_schema": definition.value_schema,
                "artifact_inputs": list(definition.artifact_inputs or []),
                "artifact_outputs": list(definition.artifact_outputs or []),
            }
        )
    if projection == "diagnostics":
        data.update(
            {
                "status": definition.status,
                "failure_modes": _merge_source_failure_modes(sources),
                "preconditions": _merge_source_preconditions(sources),
            }
        )
    return data


def _definition_detail(
    definition: CapabilityDefinition,
    sources: list[CapabilitySource],
    *,
    sections: list[str] | None = None,
    projection: str = "detail",
) -> JsonObject:
    requested = {section.strip() for section in sections or [] if section.strip()}
    include_all = not requested
    projection = _normalize_projection(projection, default="detail")
    base_projection = "summary" if requested and projection == "detail" else projection
    data = _definition_search_summary(
        definition,
        sources,
        projection="schema" if base_projection in {"detail", "schema"} else base_projection,
    )
    data["status"] = definition.status
    if include_all or "schema" in requested:
        data.update(
            {
                "agent_description": definition.agent_description,
                "input_schema": definition.input_schema,
                "output_schema": definition.output_schema,
                "value_schema": definition.value_schema,
                "artifact_inputs": list(definition.artifact_inputs or []),
                "artifact_outputs": list(definition.artifact_outputs or []),
            }
        )
    if include_all or "examples" in requested:
        data["examples"] = list(definition.examples or [])
    if include_all or "preconditions" in requested:
        data["preconditions"] = _merge_source_preconditions(sources)
        data["required_intent_slots"] = _merge_source_required_slots(sources)
    if include_all or "diagnostics" in requested:
        data["failure_modes"] = _merge_source_failure_modes(sources)
    if include_all or "sources" in requested or "runtime" in requested:
        data["sources"] = [
            _source_summary(source, definition, source.node)
            for source in sorted(
                sources,
                key=lambda item: (
                    item.node.node_id if item.node else "",
                    item.registered_name,
                ),
            )
        ]
    elif requested:
        data.pop("sources", None)
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
        "dispatchable": _source_dispatchable(source),
        "unavailable_reasons": _source_unavailable_reasons(source),
        "is_active": source.is_active,
        "unavailable_reason": source.unavailable_reason,
        "execution_requirements": source.execution_requirements,
        "risk": definition.risk,
        "effect": definition.effect,
        "timeout_sec": source.timeout_sec,
        "resource_keys": list(source.resource_keys or []),
        "conflict_policy": source.conflict_policy,
        "preflight_supported": source.preflight_supported,
        "supports_progress": source.supports_progress,
        "supports_cancel": source.supports_cancel,
        "supports_resume": source.supports_resume,
        "progress_contract": source.progress_contract,
        "preconditions": list(source.preconditions or []),
        "required_intent_slots": list(source.required_intent_slots or []),
        "hidden_input_fields": list(source.hidden_input_fields or []),
        "contract_issues": _capability_contract_issues(definition, source),
    }


def _source_projection(
    source: CapabilitySource,
    definition: CapabilityDefinition,
    *,
    projection: str,
) -> JsonObject:
    data: JsonObject = {
        "source_id": source.source_id,
        "node_id": source.node.node_id if source.node else "",
        "registered_name": source.registered_name,
        "platform_os": source.platform_os,
        "status": source.status,
        "dispatchable": _source_dispatchable(source),
    }
    if projection in {"invoke_ready", "schema", "diagnostics"}:
        data.update(
            {
                "capability_id": definition.capability_id,
                "canonical_name": definition.canonical_name,
                "runtime_id": source.runtime_id,
                "execution_requirements": source.execution_requirements,
                "timeout_sec": source.timeout_sec,
                "resource_keys": list(source.resource_keys or []),
                "conflict_policy": source.conflict_policy,
                "preflight_supported": source.preflight_supported,
                "supports_progress": source.supports_progress,
                "supports_cancel": source.supports_cancel,
                "supports_resume": source.supports_resume,
                "progress_contract": source.progress_contract,
                "required_intent_slots": list(source.required_intent_slots or []),
            }
        )
    if projection in {"schema", "diagnostics"}:
        data["preconditions"] = list(source.preconditions or [])
        data["hidden_input_fields"] = list(source.hidden_input_fields or [])
    if projection == "diagnostics":
        data.update(
            {
                "is_active": source.is_active,
                "unavailable_reason": source.unavailable_reason,
                "unavailable_reasons": _source_unavailable_reasons(source),
                "failure_modes": list(source.failure_modes or []),
                "contract_issues": _capability_contract_issues(definition, source),
            }
        )
    elif projection in {"invoke_ready", "schema"}:
        data["unavailable_reasons"] = _source_unavailable_reasons(source)
    return data


def _source_visible(
    source: CapabilitySource,
    *,
    node_id: str | None,
    platform_os: str | None,
    runtime_kind: str | None,
    runtime_labels: list[str] | None,
    supports_progress: bool | None,
    supports_cancel: bool | None,
    supports_resume: bool | None,
    preflight_supported: bool | None,
    include_inactive: bool,
) -> bool:
    if not include_inactive and not source.is_active:
        return False
    if node_id and (source.node is None or source.node.node_id != node_id):
        return False
    if platform_os and (source.platform_os or "").lower() != platform_os.lower():
        return False
    requirements = source.execution_requirements or {}
    if runtime_kind and not _runtime_kind_matches(requirements, runtime_kind):
        return False
    if runtime_labels and not _runtime_labels_match(requirements, runtime_labels):
        return False
    if supports_progress is not None and source.supports_progress != supports_progress:
        return False
    if supports_cancel is not None and source.supports_cancel != supports_cancel:
        return False
    if supports_resume is not None and source.supports_resume != supports_resume:
        return False
    return not (
        preflight_supported is not None
        and source.preflight_supported != preflight_supported
    )


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


def _definition_match_reasons(
    definition: CapabilityDefinition,
    sources: list[CapabilitySource],
    terms: list[str],
    filters: dict[str, object],
) -> list[str]:
    reasons: list[str] = []
    for term in terms:
        reason = _term_match_reason(definition, sources, term)
        if reason:
            reasons.append(reason)
    for key, value in filters.items():
        if value is None or value == []:
            continue
        reasons.append(f"filter:{key}={value}")
    if not reasons:
        reasons.append("registry:active_capability")
    return reasons[:12]


def _term_match_reason(
    definition: CapabilityDefinition,
    sources: list[CapabilitySource],
    term: str,
) -> str | None:
    fields = {
        "canonical_name": definition.canonical_name,
        "display_name": definition.display_name or "",
        "description": definition.description or "",
        "agent_description": definition.agent_description or "",
        "aliases": " ".join(definition.aliases or []),
        "tags": " ".join(definition.tags or []),
        "registered_name": " ".join(source.registered_name for source in sources),
        "node_id": " ".join(source.node.node_id for source in sources if source.node),
        "platform_os": " ".join(source.platform_os or "" for source in sources),
    }
    for field, value in fields.items():
        if term in value.lower():
            return f"query:{term}:{field}"
    return None


def _source_dispatchable(source: CapabilitySource) -> bool:
    return not _source_unavailable_reasons(source)


def _source_unavailable_reasons(source: CapabilitySource) -> list[JsonObject]:
    reasons: list[JsonObject] = []
    if not source.is_active:
        reasons.append(
            {
                "code": "source_inactive",
                "message": "Capability source is not active.",
            }
        )
    if source.unavailable_reason:
        reasons.append(
            {
                "code": "source_unavailable",
                "message": source.unavailable_reason,
            }
        )
    if source.status not in {"loaded", "active", "online"}:
        reasons.append(
            {
                "code": "source_status_not_loaded",
                "message": f"Capability source status is {source.status}.",
            }
        )
    node = source.node
    if node is None:
        reasons.append(
            {
                "code": "node_missing",
                "message": "Capability source has no node record.",
            }
        )
    elif node.status != NodeStatus.ONLINE:
        reasons.append(
            {
                "code": "node_offline",
                "message": f"Node {node.node_id} is {node.status}.",
                "node_id": node.node_id,
                "node_status": node.status,
            }
        )
    return reasons


def _normalize_projection(value: str | None, *, default: str) -> str:
    projection = (value or default).strip()
    allowed = {"summary", "invoke_ready", "schema", "diagnostics", "detail"}
    return projection if projection in allowed else default


def _bounded_limit(value: int) -> int:
    return max(1, min(int(value), 50))


def _runtime_kind_matches(requirements: JsonObject, runtime_kind: str) -> bool:
    desired = runtime_kind.strip().lower()
    if not desired:
        return True
    actual = str(requirements.get("runtime_kind") or "").lower()
    allowed = [
        str(item).lower()
        for item in requirements.get("allowed_runtime_kinds", [])
        if isinstance(item, str)
    ]
    return actual == desired or desired in allowed


def _runtime_labels_match(requirements: JsonObject, runtime_labels: list[str]) -> bool:
    desired = {label.strip().lower() for label in runtime_labels if label.strip()}
    if not desired:
        return True
    actual = {
        str(label).lower()
        for label in requirements.get("labels", [])
        if isinstance(label, str)
    }
    return desired.issubset(actual)


def _merge_source_preconditions(sources: list[CapabilitySource]) -> list[JsonObject]:
    merged: list[JsonObject] = []
    seen: set[str] = set()
    for source in sources:
        for item in source.preconditions or []:
            key = repr(sorted(item.items()))
            if key not in seen:
                seen.add(key)
                merged.append(dict(item))
    return merged


def _merge_source_required_slots(sources: list[CapabilitySource]) -> list[str]:
    return sorted(
        {
            slot
            for source in sources
            for slot in (source.required_intent_slots or [])
            if slot
        }
    )


def _merge_source_failure_modes(sources: list[CapabilitySource]) -> list[JsonObject]:
    merged: list[JsonObject] = []
    seen: set[str] = set()
    for source in sources:
        for item in source.failure_modes or []:
            key = repr(sorted(item.items()))
            if key not in seen:
                seen.add(key)
                merged.append(dict(item))
    return merged


def _capability_contract_issues(
    definition: CapabilityDefinition,
    source: CapabilitySource,
) -> list[JsonObject]:
    if definition.capability_type != "function":
        return []

    issues: list[JsonObject] = []
    registered_name = source.registered_name
    canonical_name = definition.canonical_name
    platform_os = (source.platform_os or "").lower()
    effect = (definition.effect or "").lower()
    risk = (definition.risk or "").lower()
    requirements = source.execution_requirements or {}
    input_schema = definition.input_schema or {}
    output_schema = definition.output_schema or {}
    resource_keys = list(source.resource_keys or [])
    timeout_sec = int(source.timeout_sec or 0)
    is_transfer_operation = canonical_name in {
        "transfer.croc.send",
        "transfer.croc.receive",
    }
    is_artifact_download = canonical_name == "artifact.download_file"
    is_long_task = timeout_sec > 60 or effect == "external" or is_transfer_operation

    def add(code: str, message: str, *, severity: str = "warning") -> None:
        issues.append({"severity": severity, "code": code, "message": message})

    if platform_os in {"linux", "windows"} and not registered_name.startswith(
        f"{platform_os}."
    ):
        add(
            "platform_prefix_mismatch",
            f"{platform_os} capability should be registered with '{platform_os}.' prefix.",
        )
    if risk not in {"safe", "maintenance", "destructive", "catastrophic"}:
        add("missing_or_invalid_risk", "Function capability must declare a valid risk.")
    if effect not in {"read", "write", "destructive", "external"}:
        add("missing_or_invalid_effect", "Function capability must declare a valid effect.")
    if not definition.description or len(definition.description.strip()) < 24:
        add(
            "description_too_short",
            "Capability description must explain the operational boundary.",
        )
    if not definition.agent_description:
        add(
            "missing_agent_description",
            "Capability should provide a concise agent_description for tool selection.",
        )
    if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
        add(
            "invalid_input_schema",
            "Function input_schema must be a JSON object schema.",
            severity="error",
        )
    elif "additionalProperties" not in input_schema:
        add(
            "input_schema_allows_implicit_fields",
            "Function input_schema should declare additionalProperties explicitly.",
        )
    if not isinstance(output_schema, dict) or not output_schema:
        add(
            "missing_output_schema",
            "Function output_schema must describe stable result fields.",
            severity="error",
        )
    if effect in {"write", "destructive", "external"} and not resource_keys:
        add(
            "missing_resource_keys",
            "Write/destructive/external capability must declare resource_keys.",
            severity="error",
        )
    if source.conflict_policy in {"serialize", "reject_if_running"} and not resource_keys:
        add(
            "conflict_policy_without_resource_keys",
            "Serialized/rejected concurrency policy requires resource_keys.",
            severity="error",
        )
    if not requirements:
        add(
            "missing_execution_requirements",
            "Capability must declare execution_requirements or execution_context.",
        )
    if is_long_task:
        if not source.supports_progress:
            add(
                "long_task_missing_progress",
                "Long/external capability should declare supports_progress.",
            )
        if not source.supports_cancel:
            add(
                "long_task_missing_cancel",
                "Long/external capability should declare supports_cancel.",
            )
        if source.supports_progress and not source.progress_contract:
            add(
                "missing_progress_contract",
                "Progress-capable capability must declare progress_contract.",
            )
    if is_transfer_operation:
        if not source.preflight_supported:
            add(
                "transfer_missing_preflight",
                "Transfer capability must support preflight or be covered by transfer.preflight.",
                severity="error",
            )
        if not source.preconditions:
            add(
                "transfer_missing_preconditions",
                "Transfer capability must declare preconditions.",
            )
        if not source.required_intent_slots:
            add(
                "transfer_missing_intent_slots",
                "Transfer capability must declare required_intent_slots.",
            )
        if source.supports_resume is False:
            add(
                "transfer_missing_resume",
                "croc transfer capability should declare supports_resume.",
            )
    if is_artifact_download:
        if effect != "write":
            add(
                "artifact_download_effect_must_be_write",
                "Artifact download writes Center artifact bytes to a Node path.",
                severity="error",
            )
        required_slots = set(source.required_intent_slots or [])
        missing_slots = sorted({"artifact_id", "output_path"} - required_slots)
        if missing_slots:
            add(
                "artifact_download_missing_intent_slots",
                "Artifact download must declare artifact_id and output_path intent slots.",
                severity="error",
            )
        if not source.preconditions:
            add(
                "artifact_download_missing_preconditions",
                (
                    "Artifact download must declare target path and artifact "
                    "availability preconditions."
                ),
            )
        if source.supports_resume:
            add(
                "artifact_download_resume_not_supported",
                "Artifact download must not declare supports_resume until resumable fetch exists.",
                severity="error",
            )

    return issues


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
