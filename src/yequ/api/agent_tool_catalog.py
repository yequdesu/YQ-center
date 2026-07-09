"""Agent tool catalog and prompt/debug metadata helpers."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.agent.provider import AgentFunction, AgentProvider
from yequ.models.capability import Capability
from yequ.models.node import Node
from yequ.runtime.capability_context import JsonDict

CENTER_META_TOOL_GROUPS: dict[str, dict[str, object]] = {
    "nodes": {
        "title": "Nodes",
        "description": "List and inspect connected nodes and platform/runtime status.",
        "tools": ["node.list", "node.status"],
    },
    "capabilities": {
        "title": "Capability Discovery",
        "description": (
            "Search or describe non-opened capabilities when group directory is insufficient."
        ),
        "tools": ["capability.search", "capability.describe"],
    },
    "context": {
        "title": "YCR Context",
        "description": "Inspect, expand, tail, schema, search, and status for YCR context refs.",
        "tools": [
            "context.status",
            "context.inspect",
            "context.expand",
            "context.tail",
            "context.schema",
            "context.search",
        ],
    },
    "artifacts": {
        "title": "Artifacts",
        "description": "List, inspect, read text, present, preflight, and deploy Center artifacts.",
        "tools": [
            "artifact.list",
            "artifact.get",
            "artifact.read_text",
            "artifact.present",
            "artifact.deploy.preflight",
            "artifact.deploy",
        ],
    },
    "operations": {
        "title": "Operations",
        "description": "Inspect or cancel waitable Center operations.",
        "tools": ["operation.status", "operation.cancel"],
    },
    "transfers": {
        "title": "Transfers",
        "description": (
            "Preflight, create, inspect, resume, or cancel Center-managed node transfers."
        ),
        "tools": [
            "transfer.preflight",
            "transfer.create",
            "transfer.status",
            "transfer.resume",
            "transfer.cancel",
        ],
    },
}


def center_meta_tool_groups() -> dict[str, dict[str, object]]:
    return CENTER_META_TOOL_GROUPS


def _center_meta_functions() -> list[AgentFunction]:
    """Stable Center meta tools for capability discovery."""
    return [
        AgentFunction(
            name="capability.groups",
            description=(
                "List stable Center meta-tool groups. Open a relevant group with "
                "capability.group.open before using Center meta capabilities."
            ),
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="capability.group.open",
            description=(
                "Open one Center meta-tool group and return invoke-ready capability "
                "refs and input schemas. Use this instead of capability.search for "
                "Center meta tools."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "group_id": {"type": "string"},
                    "projection": {
                        "type": "string",
                        "enum": ["summary", "invoke_ready"],
                        "default": "invoke_ready",
                    },
                },
                "required": ["group_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="node.list",
            description="List known nodes, current status, platform, and capability counts.",
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="node.status",
            description=(
                "Inspect one node with a compact default projection. Use "
                "capability.search projection=invoke_ready for callable sources."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "projection": {
                        "type": "string",
                        "enum": ["summary", "detail"],
                        "default": "summary",
                    },
                },
                "required": ["node_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="capability.search",
            description=(
                "Search Center capability definitions with structured filters. "
                "Use projection=summary for discovery and projection=invoke_ready "
                "before invoking a concrete source. Returns at most five compact "
                "candidates; use capability.describe for full schema."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "node_id": {"type": "string"},
                    "platform_os": {"type": "string"},
                    "effect": {"type": "string"},
                    "risk": {"type": "string"},
                    "runtime_kind": {"type": "string"},
                    "runtime_labels": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "supports_progress": {"type": "boolean"},
                    "supports_cancel": {"type": "boolean"},
                    "supports_resume": {"type": "boolean"},
                    "preflight_supported": {"type": "boolean"},
                    "artifact_input": {"type": "boolean"},
                    "artifact_output": {"type": "boolean"},
                    "projection": {
                        "type": "string",
                        "enum": ["summary", "invoke_ready", "schema", "diagnostics"],
                        "default": "summary",
                    },
                    "capability_type": {"type": "string", "default": "function"},
                    "limit": {"type": "integer", "default": 5, "maximum": 5},
                },
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="capability.describe",
            description=(
                "Describe one Center capability definition. Defaults to an "
                "invoke-ready projection with canonical_name/source_id; request "
                "schema or diagnostics only when needed."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "capability_ref": {"type": "string"},
                    "node_id": {"type": "string"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "schema",
                                "preconditions",
                                "examples",
                                "diagnostics",
                                "sources",
                                "runtime",
                            ],
                        },
                    },
                    "projection": {
                        "type": "string",
                        "enum": ["detail", "summary", "invoke_ready", "schema", "diagnostics"],
                        "default": "invoke_ready",
                    },
                },
                "required": ["capability_ref"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="context.inspect",
            description="Inspect a YCR context ref without expanding the full value.",
            input_schema={
                "type": "object",
                "properties": {"ref_id": {"type": "string"}},
                "required": ["ref_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="context.expand",
            description=(
                "Expand a bounded, path-specific slice from a YCR context ref. "
                "Use context.inspect or context.schema before expanding broad roots."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ref_id": {"type": "string"},
                    "path": {
                        "type": "string",
                        "default": "$",
                        "description": "Prefer a specific JSON path instead of broad root $.",
                    },
                    "limit": {"type": "integer", "default": 20, "maximum": 100},
                },
                "required": ["ref_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="context.tail",
            description="Read the tail of a log, event list, or large context ref value.",
            input_schema={
                "type": "object",
                "properties": {
                    "ref_id": {"type": "string"},
                    "path": {"type": "string", "default": "$"},
                    "lines": {"type": "integer", "default": 40, "maximum": 200},
                },
                "required": ["ref_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="context.schema",
            description="Return a structural schema summary for a YCR context ref.",
            input_schema={
                "type": "object",
                "properties": {
                    "ref_id": {"type": "string"},
                    "path": {"type": "string", "default": "$"},
                },
                "required": ["ref_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="context.search",
            description=(
                "Search YCR context refs using vector retrieval. If ref_id is omitted, "
                "Center searches refs attached to the current Agent session."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ref_id": {"type": "string"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10, "maximum": 50},
                },
                "required": ["query"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="context.status",
            description="Inspect YCR status and context ref capabilities.",
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="capability.invoke",
            description=(
                "Invoke one concrete Center capability source. Use source_id from "
                "capability.search or capability.describe when more than one source "
                "exists. If the user specified a target node or node-local path, "
                "pass node_id and use only a source_id that belongs to that node. "
                "Put the real capability arguments in input."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "capability_ref": {
                        "type": "string",
                        "description": "Capability ID, canonical name, alias, or registered name",
                    },
                    "source_id": {
                        "type": "string",
                        "description": (
                            "Concrete capability source ID. It must belong to node_id "
                            "when node_id is provided."
                        ),
                    },
                    "node_id": {
                        "type": "string",
                        "description": (
                            "Target node constraint. Required when the user named a "
                            "specific node or destination path."
                        ),
                    },
                    "input": {
                        "type": "object",
                        "description": "Arguments passed to the concrete Node capability",
                    },
                },
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="artifact.list",
            description=(
                "List Center-managed artifacts such as images, screenshots, files, "
                "reports, and exports. Use this to find existing media before "
                "presenting it."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "invocation_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "node_id": {"type": "string"},
                    "artifact_type": {"type": "string"},
                    "limit": {"type": "integer", "default": 20, "maximum": 50},
                    "projection": {
                        "type": "string",
                        "enum": ["summary", "detail"],
                        "default": "summary",
                    },
                },
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="artifact.get",
            description="Inspect one Center-managed artifact by artifact_id.",
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "projection": {
                        "type": "string",
                        "enum": ["summary", "detail"],
                        "default": "summary",
                    },
                },
                "required": ["artifact_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="artifact.read_text",
            description=(
                "Read bounded text from a Center-managed text artifact. Use this "
                "to inspect logs, txt, markdown, csv, json, or other text artifacts "
                "without deploying them to a Node. Supports artifact/title glob matching, "
                "head/tail/range line reads, negative line indexes from file end, and "
                "line_glob filtering. Do not use for images or binary files."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "artifact_pattern": {
                        "type": "string",
                        "description": (
                            "Glob matched against artifact_id and title; latest text match is used."
                        ),
                    },
                    "session_id": {
                        "type": "string",
                        "description": (
                            "Defaults to current Agent session when artifact_pattern is used."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["head", "tail", "range", "full"],
                        "default": "head",
                    },
                    "lines": {
                        "type": "integer",
                        "default": 200,
                        "maximum": 2000,
                    },
                    "line_start": {
                        "type": "integer",
                        "description": (
                            "1-based inclusive line start for mode=range; negative counts from end."
                        ),
                    },
                    "line_end": {
                        "type": "integer",
                        "description": (
                            "1-based inclusive line end for mode=range; negative counts from end."
                        ),
                    },
                    "line_glob": {
                        "type": "string",
                        "description": (
                            "Optional shell-style glob filter applied to selected lines."
                        ),
                    },
                    "max_bytes": {
                        "type": "integer",
                        "default": 65536,
                        "maximum": 131072,
                    },
                    "encoding": {"type": "string", "default": "utf-8"},
                },
                "anyOf": [
                    {"required": ["artifact_id"]},
                    {"required": ["artifact_pattern"]},
                ],
                "additionalProperties": False,
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="artifact.present",
            description=(
                "Present one or more existing Center artifacts in the Console chat UI. "
                "Use this when the user asks to show, display, preview, or open an "
                "image/media/file artifact. This does not visually understand images."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "artifact_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                    },
                    "projection": {
                        "type": "string",
                        "enum": ["summary", "detail"],
                        "default": "summary",
                    },
                },
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="artifact.deploy.preflight",
            description=(
                "Check whether an existing Center artifact can be deployed to a "
                "specific Node output_path. Use this before artifact.deploy; do "
                "not guess missing target paths."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "target_node_id": {"type": "string"},
                    "output_path": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["fail_if_exists", "overwrite"],
                        "default": "fail_if_exists",
                    },
                    "timeout_sec": {"type": "integer", "default": 20},
                    "ttl_sec": {
                        "type": "integer",
                        "default": 120,
                        "description": (
                            "Requested freshness window; Center clamps it to 30-300 seconds."
                        ),
                    },
                },
                "required": ["artifact_id", "target_node_id", "output_path"],
                "additionalProperties": False,
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="artifact.deploy",
            description=(
                "Deploy one existing Center artifact to a specific Node path. "
                "Use only after artifact.deploy.preflight succeeds for the exact "
                "target node and output_path. "
                "This creates a waitable Node Job Operation; do not use it for "
                "large Node-to-Node transfers where transfer.create/croc is better."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string"},
                    "target_node_id": {"type": "string"},
                    "output_path": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["fail_if_exists", "overwrite"],
                        "default": "fail_if_exists",
                    },
                    "source_id": {
                        "type": "string",
                        "description": "Optional concrete artifact download capability source_id",
                    },
                    "preflight_id": {
                        "type": "string",
                        "description": "ID returned by a successful artifact.deploy.preflight call",
                    },
                    "skip_preflight": {"type": "boolean", "default": False},
                    "skip_reason": {"type": "string"},
                },
                "required": ["artifact_id", "target_node_id", "output_path"],
                "additionalProperties": False,
            },
            risk="maintenance",
            effect="write",
            timeout_sec=300,
        ),
        AgentFunction(
            name="operation.status",
            description=(
                "Inspect one Center Operation and its referenced domain state. "
                "Use this for wait handles returned by long-running operations."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "operation_id": {"type": "string"},
                    "projection": {
                        "type": "string",
                        "enum": ["summary", "detail"],
                        "default": "summary",
                    },
                },
                "required": ["operation_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="operation.cancel",
            description="Cancel one waitable Center Operation if it supports cancellation.",
            input_schema={
                "type": "object",
                "properties": {
                    "operation_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["operation_id"],
            },
            risk="maintenance",
            effect="write",
            timeout_sec=5,
        ),
        AgentFunction(
            name="transfer.preflight",
            description=(
                "Check whether a cross-node transfer is ready before creating it. "
                "Use this after the user has specified source, target node, and "
                "target_output_dir or target_path; do not guess missing landing paths."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "source_node_id": {"type": "string"},
                    "target_node_id": {"type": "string"},
                    "source_path": {"type": "string"},
                    "target_output_dir": {"type": "string"},
                    "target_path": {"type": "string"},
                    "relay_url": {
                        "type": "string",
                        "description": "Optional yq-croc relay control host:port.",
                    },
                    "route_policy": {
                        "type": "string",
                        "enum": [
                            "auto",
                            "relay_only",
                            "relay_pool",
                            "local_first",
                            "local_only",
                            "direct_ip",
                        ],
                        "default": "auto",
                        "description": (
                            "Transfer route strategy. Omit or use auto for Center-managed "
                            "automatic routing; explicit values are enforced."
                        ),
                    },
                    "direct_ip": {
                        "type": "string",
                        "description": "Required only when route_policy is direct_ip.",
                    },
                    "multicast_address": {
                        "type": "string",
                        "description": "Optional croc local discovery multicast address.",
                    },
                    "resume_mode": {
                        "type": "string",
                        "enum": ["resume", "overwrite", "fail_if_exists"],
                        "description": (
                            "Required user intent. Do not choose a value by default: "
                            "ask the user when overwrite/resume/conflict behavior is "
                            "not explicit."
                        ),
                    },
                    "include_sha256": {"type": "boolean", "default": False},
                    "timeout_sec": {"type": "integer", "default": 20},
                    "ttl_sec": {
                        "type": "integer",
                        "default": 120,
                        "description": (
                            "Requested freshness window; Center clamps it to 30-300 seconds."
                        ),
                    },
                },
                "required": [
                    "source_node_id",
                    "target_node_id",
                    "source_path",
                    "resume_mode",
                ],
                "anyOf": [
                    {"required": ["target_output_dir"]},
                    {"required": ["target_path"]},
                ],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="transfer.create",
            description=(
                "Create a Center-managed croc TransferSession between two nodes. "
                "Use this instead of directly calling low-level croc send/receive; "
                "Center starts the sender first, waits for yq-croc sender_ready, "
                "then starts the receiver. Prefer "
                "transfer.preflight first when path permissions, free space, or "
                "overwrite behavior are uncertain."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "source_node_id": {"type": "string"},
                    "target_node_id": {"type": "string"},
                    "source_path": {"type": "string"},
                    "target_output_dir": {"type": "string"},
                    "target_path": {"type": "string"},
                    "relay_url": {
                        "type": "string",
                        "description": "Optional yq-croc relay control host:port.",
                    },
                    "route_policy": {
                        "type": "string",
                        "enum": [
                            "auto",
                            "relay_only",
                            "relay_pool",
                            "local_first",
                            "local_only",
                            "direct_ip",
                        ],
                        "default": "auto",
                        "description": (
                            "Transfer route strategy. Omit or use auto for Center-managed "
                            "automatic routing; explicit values are enforced."
                        ),
                    },
                    "direct_ip": {
                        "type": "string",
                        "description": "Required only when route_policy is direct_ip.",
                    },
                    "multicast_address": {
                        "type": "string",
                        "description": "Optional croc local discovery multicast address.",
                    },
                    "resume_mode": {
                        "type": "string",
                        "enum": ["resume", "overwrite", "fail_if_exists"],
                        "description": (
                            "Required user intent. Do not choose a value by default: "
                            "ask the user when overwrite/resume/conflict behavior is "
                            "not explicit."
                        ),
                    },
                    "timeout_sec": {"type": "integer", "default": 3600},
                    "expected_sha256": {"type": "string"},
                    "preflight_id": {
                        "type": "string",
                        "description": "ID returned by a successful transfer.preflight call",
                    },
                    "skip_preflight": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Only true when the user explicitly accepts skipping preflight."
                        ),
                    },
                    "skip_reason": {"type": "string"},
                },
                "required": [
                    "source_node_id",
                    "target_node_id",
                    "source_path",
                    "resume_mode",
                ],
                "anyOf": [
                    {"required": ["target_output_dir"]},
                    {"required": ["target_path"]},
                ],
            },
            risk="maintenance",
            effect="external",
            timeout_sec=5,
        ),
        AgentFunction(
            name="transfer.status",
            description="Inspect one Center-managed TransferSession and its sender/receiver jobs.",
            input_schema={
                "type": "object",
                "properties": {
                    "transfer_id": {"type": "string"},
                    "projection": {
                        "type": "string",
                        "enum": ["summary", "detail"],
                        "default": "summary",
                    },
                },
                "required": ["transfer_id"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="transfer.resume",
            description=(
                "Resume an interrupted Center-managed TransferSession. Use only when "
                "transfer.status reports status=interrupted and resumable=true; Center "
                "creates the next attempt and reuses the existing TransferSession."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "transfer_id": {"type": "string"},
                    "timeout_sec": {"type": "integer", "default": 3600},
                    "relay_url": {"type": "string"},
                    "route_policy": {
                        "type": "string",
                        "enum": [
                            "auto",
                            "relay_only",
                            "relay_pool",
                            "local_first",
                            "local_only",
                            "direct_ip",
                        ],
                    },
                    "direct_ip": {"type": "string"},
                    "multicast_address": {"type": "string"},
                },
                "required": ["transfer_id"],
            },
            risk="maintenance",
            effect="external",
            timeout_sec=5,
        ),
        AgentFunction(
            name="transfer.cancel",
            description="Cancel one Center-managed TransferSession and its non-terminal jobs.",
            input_schema={
                "type": "object",
                "properties": {
                    "transfer_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["transfer_id"],
            },
            risk="maintenance",
            effect="write",
            timeout_sec=5,
        ),
    ]


async def _available_functions(
    db: AsyncSession,
    *,
    target_node_id: str | None = None,
) -> list[AgentFunction]:
    """Build the Agent tool list.

    Production exposes only stable bootstrap protocol tools. Center meta tools
    are reached through grouped progressive expansion. Raw Node/Product
    capabilities remain discoverable through the capability discovery group.

    Tests that need static tools must register a fake provider explicitly.
    """
    del db, target_node_id
    bootstrap = {"capability.groups", "capability.group.open", "capability.invoke"}
    return [function for function in _center_meta_functions() if function.name in bootstrap]


async def _default_target_node_id(db: AsyncSession) -> str:
    """Pick a schedulable target node without embedding a platform default."""
    from yequ.config import get_settings
    from yequ.services.node_liveness_service import is_node_schedulable

    settings = get_settings()
    result = await db.execute(
        select(Node).order_by(
            Node.last_heartbeat_at.desc().nullslast(),
            Node.node_id.asc(),
        )
    )
    for node in result.scalars().all():
        schedulable, _ = is_node_schedulable(node, settings)
        if schedulable:
            return node.node_id
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "no_schedulable_node",
            "message": "No schedulable node is available for this plan.",
        },
    )


INTERNAL_TOOL_INPUT_FIELDS = {"approval_id", "dry_run"}


def _agent_function_from_capability(cap: Capability) -> AgentFunction:
    """Convert a registered Node capability into the LLM-visible tool contract.

    Node manifests may carry operational fields that are required by Center or
    the Node daemon but should not be chosen or narrated by the LLM. The Agent
    sees a stable operator-facing contract; Center injects internal fields when
    it executes approvals/preflight.
    """
    hidden_fields = set(cap.hidden_input_fields or []) | INTERNAL_TOOL_INPUT_FIELDS
    return AgentFunction(
        name=cap.name,
        description=_capability_agent_description(cap),
        input_schema=_strip_internal_input_fields(cap.input_schema or {}, hidden_fields),
        risk=cap.risk or "safe",
        effect=cap.effect or "read",
        timeout_sec=cap.timeout_sec or 30,
        output_schema=cap.output_schema,
        source_nodes=[cap.node.node_id] if cap.node else [],
    )


def _capability_agent_description(cap: Capability) -> str:
    description = (cap.agent_description or cap.description or "").strip()
    if description:
        return description

    effect = cap.effect or "read"
    risk = cap.risk or "safe"
    context = cap.execution_context or "node"
    approval_note = (
        " This operation changes system state and Center will ask the user for "
        "approval before execution."
        if effect in ("write", "destructive")
        or risk in ("maintenance", "destructive", "catastrophic")
        else ""
    )
    return (
        f"{cap.name} provided by node plugin {cap.plugin_id}. "
        f"Execution context: {context}. Effect: {effect}. Risk: {risk}."
        f"{approval_note}"
    )


def _strip_internal_input_fields(
    schema: dict[str, object],
    hidden_fields: set[str],
) -> dict[str, object]:
    if not schema or not hidden_fields:
        return dict(schema)

    cleaned = dict(schema)
    properties = cleaned.get("properties")
    if isinstance(properties, dict):
        cleaned["properties"] = {
            key: value for key, value in properties.items() if key not in hidden_fields
        }

    required = cleaned.get("required")
    if isinstance(required, list):
        cleaned["required"] = [
            key for key in required if not isinstance(key, str) or key not in hidden_fields
        ]

    return cleaned


def _function_debug_summary(func: AgentFunction) -> dict[str, object]:
    return {
        "name": func.name,
        "description": func.description,
        "risk": func.risk,
        "effect": func.effect,
        "timeout_sec": func.timeout_sec,
        "source_nodes": list(func.source_nodes),
        "input_schema": func.input_schema or {},
        "output_schema": func.output_schema or {},
    }


def _provider_system_prompt(
    provider: AgentProvider,
    functions: list[AgentFunction],
    capability_context: JsonDict | None = None,
) -> str:
    prompt_builder = getattr(provider, "debug_system_prompt", None)
    if not callable(prompt_builder):
        return ""
    try:
        value = prompt_builder(
            functions,
            context={"capability_context": capability_context} if capability_context else None,
        )
    except TypeError:
        value = prompt_builder(functions)
    return value if isinstance(value, str) else str(value)


def _agent_debug_metadata(
    provider: AgentProvider,
    *,
    available_functions: list[AgentFunction],
    target_node_id: str | None,
    execution_mode: str,
    capability_context: JsonDict | None = None,
) -> dict[str, object]:
    return {
        "system_prompt": _provider_system_prompt(provider, available_functions, capability_context),
        "target_node_id": target_node_id,
        "execution_mode": execution_mode,
        "routing_mode": (
            str(capability_context.get("routing_mode"))
            if capability_context
            else ("pinned" if target_node_id else "auto")
        ),
        "capability_context": capability_context or {},
        "nodes": capability_context.get("nodes", []) if capability_context else [],
        "tool_count_by_node": (
            capability_context.get("tool_count_by_node", {}) if capability_context else {}
        ),
        "available_functions": [_function_debug_summary(f) for f in available_functions],
    }
