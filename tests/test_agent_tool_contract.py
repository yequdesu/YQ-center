"""Agent-visible tool contract tests.

These tests lock down the structural contract between registered Node
capabilities and the LLM-visible tool schema. Internal Center/Node fields must
not leak into provider tools.
"""

import pytest


def test_provider_tool_surface_keeps_bootstrap_when_working_set_empty():
    from yequ.agent.agent_stream import _provider_functions_for_tool_strategy
    from yequ.agent.provider import AgentFunction

    functions = [
        AgentFunction(name="capability.groups", description="", input_schema={}),
        AgentFunction(name="capability.group.open", description="", input_schema={}),
        AgentFunction(
            name="capability.invoke",
            description="",
            input_schema={
                "type": "object",
                "properties": {
                    "capability_ref": {"type": "string"},
                    "source_id": {"type": "string"},
                    "input": {"type": "object"},
                },
            },
        ),
    ]

    selected = _provider_functions_for_tool_strategy(
        functions,
        {"mode": "bootstrap_discovery", "candidate_count": 0},
    )

    assert [function.name for function in selected] == [
        "capability.groups",
        "capability.group.open",
        "capability.invoke",
    ]


def test_provider_tool_surface_uses_invoke_only_for_loaded_working_set():
    from yequ.agent.agent_stream import _provider_functions_for_tool_strategy
    from yequ.agent.provider import AgentFunction

    functions = [
        AgentFunction(name="capability.groups", description="", input_schema={}),
        AgentFunction(name="capability.group.open", description="", input_schema={}),
        AgentFunction(
            name="capability.invoke",
            description="",
            input_schema={
                "type": "object",
                "properties": {
                    "capability_ref": {"type": "string"},
                    "source_id": {"type": "string"},
                    "input": {"type": "object"},
                },
            },
        ),
    ]

    selected = _provider_functions_for_tool_strategy(
        functions,
        {
            "mode": "reuse_working_set",
            "candidate_count": 2,
            "preferred_candidates": [
                {
                    "capability_ref": "artifact.read_text",
                    "source_id": "center:artifact.read_text",
                    "bound_input": {"artifact_id": "id_art"},
                }
            ],
        },
    )

    assert [function.name for function in selected] == ["capability.invoke"]
    schema = selected[0].input_schema or {}
    properties = schema["properties"]
    assert properties["capability_ref"]["enum"] == ["artifact.read_text"]
    assert properties["source_id"]["enum"] == ["center:artifact.read_text"]
    assert "bound_input" in selected[0].description


async def test_capability_invoke_preflight_uses_inner_resource_keys(db_session) -> None:
    from datetime import UTC, datetime

    from yequ.agent.tool_stream import _capability_invoke_preflight_override
    from yequ.application.schemas import ToolPreflightResult
    from yequ.models.capability_runtime import CapabilityDefinition, CapabilitySource
    from yequ.models.node import Node
    from yequ.services.node_auth import hash_token

    node = Node(
        node_id="linux-node-test",
        node_name="linux-node-test",
        token_hash=hash_token("linux-node-test-token"),
        status="online",
        last_heartbeat_at=datetime.now(UTC),
    )
    db_session.add(node)
    await db_session.flush()
    definition = CapabilityDefinition(
        canonical_name="exec.run",
        capability_type="function",
        risk="maintenance",
        effect="external",
        status="active",
    )
    db_session.add(definition)
    await db_session.flush()
    db_session.add(
        CapabilitySource(
            source_id="src_exec",
            definition_id=definition.id,
            node_record_id=node.id,
            plugin_id="test.exec",
            plugin_version="1.0",
            registered_name="linux.exec.run",
            status="loaded",
            is_active=True,
            resource_keys=["node.exec"],
            conflict_policy="serialize",
        )
    )
    await db_session.flush()

    preflight = await _capability_invoke_preflight_override(
        db_session,
        function_name="capability.invoke",
        tool_input={
            "source_id": "src_exec",
            "node_id": "linux-node-test",
            "input": {"profile": "user.readonly", "command": "whoami"},
        },
        execution_mode="auto",
        fallback=ToolPreflightResult(
            function_name="capability.invoke",
            status="ok",
            risk="safe",
            effect="read",
        ),
    )

    assert preflight.target_node_id == "linux-node-test"
    assert preflight.risk == "maintenance"
    assert preflight.effect == "external"
    assert preflight.resource_keys == ["node.exec"]
    assert preflight.conflict_policy == "serialize"
    assert not preflight.is_concurrent_safe


async def test_capability_invoke_preflight_handles_center_capability(db_session) -> None:
    from yequ.agent.tool_stream import _capability_invoke_preflight_override
    from yequ.application.schemas import ToolPreflightResult
    from yequ.models.capability_runtime import CapabilityDefinition

    db_session.add(
        CapabilityDefinition(
            capability_id="center_artifact_read_text",
            canonical_name="artifact.read_text",
            capability_type="function",
            risk="safe",
            effect="read",
            scope="center",
            dispatch_kind="center_tool",
            status="active",
        )
    )
    await db_session.flush()

    preflight = await _capability_invoke_preflight_override(
        db_session,
        function_name="capability.invoke",
        tool_input={
            "capability_ref": "artifact.read_text",
            "input": {"artifact_id": "id_art", "head": 50},
        },
        execution_mode="auto",
        fallback=ToolPreflightResult(
            function_name="capability.invoke",
            status="ok",
            risk="safe",
            effect="read",
        ),
    )

    assert preflight.risk == "safe"
    assert preflight.effect == "read"
    assert preflight.resource_keys == []
    assert preflight.conflict_policy is None


def test_capability_invoke_allowlist_rejects_non_candidate() -> None:
    from yequ.agent.tool_stream import _capability_invoke_is_allowed

    allowlist = {
        "capability_refs": {"artifact.read_text"},
        "source_ids": {"center:artifact.read_text"},
    }

    assert _capability_invoke_is_allowed(
        {"capability_ref": "artifact.read_text", "input": {}},
        allowlist,
    )
    assert _capability_invoke_is_allowed(
        {"source_id": "center:artifact.read_text", "input": {}},
        allowlist,
    )
    assert not _capability_invoke_is_allowed(
        {"capability_ref": "context.search", "input": {}},
        allowlist,
    )


def test_agent_function_uses_capability_description_and_hides_internal_fields():
    from yequ.api.routes.agent import _agent_function_from_capability
    from yequ.models.capability import Capability

    cap = Capability(
        node_record_id="node-record",
        plugin_id="system.metrics",
        plugin_version="1.0",
        capability_type="function",
        name="system.process.terminate_tree",
        status="loaded",
        description="Terminate a process tree by PID after user approval.",
        agent_description=(
            "Terminate a process tree. Use only when the user explicitly asks to "
            "close an app or process."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pid": {"type": "integer"},
                "dry_run": {"type": "boolean", "default": True},
                "approval_id": {"type": "string"},
            },
            "required": ["pid", "dry_run"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        risk="maintenance",
        effect="write",
        hidden_input_fields=["approval_id", "dry_run"],
        preflight_supported=True,
        is_active=True,
    )

    func = _agent_function_from_capability(cap)

    assert func.description == cap.agent_description
    properties = func.input_schema["properties"]
    assert "pid" in properties
    assert "dry_run" not in properties
    assert "approval_id" not in properties
    assert func.input_schema["required"] == ["pid"]


@pytest.mark.asyncio
async def test_available_functions_preserve_registered_tool_contract(db_session):
    from datetime import UTC, datetime

    from yequ.api.routes.agent import _available_functions
    from yequ.models.capability import Capability
    from yequ.models.node import Node
    from yequ.services.node_auth import hash_token

    node = Node(
        node_id="tool-contract-node",
        node_name="Tool Contract Node",
        token_hash=hash_token("tool-contract-token"),
        status="online",
        last_heartbeat_at=datetime.now(UTC),
    )
    db_session.add(node)
    await db_session.flush()

    db_session.add(
        Capability(
            node_record_id=node.id,
            plugin_id="contract.plugin",
            plugin_version="1.0",
            capability_type="function",
            name="contract.user.desktop_shortcuts",
            status="loaded",
            description="List shortcuts on the active user's desktop.",
            agent_description=(
                "List files and shortcuts from the interactive user's Desktop folder."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 50},
                    "dry_run": {"type": "boolean"},
                },
            },
            output_schema={"type": "object"},
            risk="safe",
            effect="read",
            execution_context="user",
            hidden_input_fields=["dry_run"],
            is_active=True,
        )
    )
    await db_session.commit()

    funcs = await _available_functions(db_session)
    names = [func.name for func in funcs]

    assert names == ["capability.groups", "capability.group.open", "capability.invoke"]
    assert "contract.user.desktop_shortcuts" not in names
