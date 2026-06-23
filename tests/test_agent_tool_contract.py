"""Agent-visible tool contract tests.

These tests lock down the structural contract between registered Node
capabilities and the LLM-visible tool schema. Internal Center/Node fields must
not leak into provider tools.
"""

import pytest


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
        agent_description="Terminate a process tree. Use only when the user explicitly asks to close an app or process.",
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

    db_session.add(Capability(
        node_record_id=node.id,
        plugin_id="contract.plugin",
        plugin_version="1.0",
        capability_type="function",
        name="contract.user.desktop_shortcuts",
        status="loaded",
        description="List shortcuts on the active user's desktop.",
        agent_description="List files and shortcuts from the interactive user's Desktop folder.",
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
    ))
    await db_session.commit()

    funcs = await _available_functions(db_session)
    func = next(f for f in funcs if f.name == "contract.user.desktop_shortcuts")

    assert "interactive user's Desktop" in func.description
    assert func.input_schema["properties"] == {"limit": {"type": "integer", "default": 50}}
