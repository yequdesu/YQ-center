from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tests.conftest import make_yqp_envelope
from yequ.models.capability_runtime import CapabilityDefinition, CapabilitySource


async def _provision_node(db_session, *, node_id: str, token: str) -> None:
    from yequ.models.node import Node
    from yequ.services.node_auth import hash_token

    db_session.add(
        Node(
            node_id=node_id,
            node_name=node_id,
            token_hash=hash_token(token),
            status="provisioned",
        )
    )
    await db_session.commit()


async def _hello_linux_node(client: AsyncClient, node_id: str, token: str) -> None:
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node_id,
            payload={
                "daemon_version": "0.2.0",
                "platform": {"os": "linux", "arch": "x86_64"},
                "runtimes": [
                    {
                        "runtime_id": "linux-root",
                        "kind": "privileged",
                        "status": "online",
                        "privilege": "root",
                    }
                ],
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


async def _hello_windows_node(client: AsyncClient, node_id: str, token: str) -> None:
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node_id,
            payload={
                "daemon_version": "0.5.0",
                "platform": {"os": "windows", "arch": "AMD64"},
                "runtimes": [
                    {
                        "runtime_id": "windows-system",
                        "kind": "privileged",
                        "status": "online",
                        "privilege": "admin",
                    }
                ],
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


async def _register_linux_system_info(client: AsyncClient, node_id: str, token: str) -> None:
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "linux.system",
                        "plugin_version": "1.0",
                        "status": "loaded",
                        "functions": [
                            {
                                "name": "linux.system.info",
                                "description": "Read Linux system information.",
                                "input_schema": {"type": "object", "properties": {}},
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "execution_context": "system",
                            }
                        ],
                        "signals": [],
                    }
                ]
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


async def _register_artifact_output_capability(
    client: AsyncClient,
    node_id: str,
    token: str,
) -> None:
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "windows.artifacts",
                        "plugin_version": "1.0",
                        "status": "loaded",
                        "functions": [
                            {
                                "name": "windows.screen.capture",
                                "description": "Capture screen as Center artifact.",
                                "input_schema": {"type": "object", "properties": {}},
                                "output_schema": {
                                    "type": "object",
                                    "properties": {
                                        "artifacts": {"type": "array"},
                                    },
                                },
                                "risk": "safe",
                                "effect": "read",
                                "execution_context": "user",
                            }
                        ],
                        "signals": [],
                    }
                ]
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


async def _register_windows_croc_status(
    client: AsyncClient,
    node_id: str,
    token: str,
) -> None:
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "windows.transfer",
                        "plugin_version": "1.0",
                        "status": "loaded",
                        "functions": [
                            {
                                "name": "windows.transfer.croc.status",
                                "description": (
                                    "Probe local croc installation and transfer runtime facts."
                                ),
                                "input_schema": {
                                    "type": "object",
                                    "properties": {},
                                    "additionalProperties": False,
                                },
                                "output_schema": {
                                    "type": "object",
                                    "properties": {
                                        "installed": {"type": "boolean"},
                                        "version": {"type": ["string", "null"]},
                                        "allow_send": {"type": "boolean"},
                                        "allow_receive": {"type": "boolean"},
                                    },
                                },
                                "risk": "safe",
                                "effect": "read",
                                "execution_context": "system",
                                "resource_keys": ["node.transfer"],
                            },
                            {
                                "name": "windows.transfer.local.stat",
                                "description": "Inspect local transfer path facts.",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {"path": {"type": "string"}},
                                    "required": ["path"],
                                },
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "execution_context": "user",
                                "resource_keys": ["node.transfer", "node.file"],
                            },
                            {
                                "name": "windows.transfer.croc.reconcile",
                                "description": "Read local croc transfer ledger.",
                                "input_schema": {"type": "object", "properties": {}},
                                "output_schema": {"type": "object"},
                                "risk": "safe",
                                "effect": "read",
                                "execution_context": "system",
                                "resource_keys": ["node.transfer"],
                            },
                            {
                                "name": "windows.transfer.croc.send",
                                "description": "Send a local path through croc.",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string"},
                                        "code": {"type": "string"},
                                    },
                                    "required": ["path", "code"],
                                },
                                "output_schema": {"type": "object"},
                                "risk": "maintenance",
                                "effect": "external",
                                "execution_context": "user",
                                "resource_keys": ["node.transfer"],
                                "hidden_input_fields": ["code"],
                            },
                            {
                                "name": "windows.transfer.croc.receive",
                                "description": "Receive files through croc.",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "code": {"type": "string"},
                                        "output_dir": {"type": "string"},
                                    },
                                    "required": ["code"],
                                },
                                "output_schema": {"type": "object"},
                                "risk": "maintenance",
                                "effect": "external",
                                "execution_context": "user",
                                "resource_keys": ["node.transfer"],
                                "hidden_input_fields": ["code"],
                            }
                        ],
                        "signals": [],
                    }
                ]
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_register_capabilities_writes_definition_and_source(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_linux_system_info(client, node.node_id, token)

    definition_result = await db_session.execute(
        select(CapabilityDefinition).where(
            CapabilityDefinition.canonical_name == "system.info",
            CapabilityDefinition.capability_type == "function",
        )
    )
    definition = definition_result.scalar_one()
    assert definition.status == "active"
    assert "linux.system.info" in definition.aliases
    assert definition.risk == "safe"
    assert definition.effect == "read"

    source_result = await db_session.execute(
        select(CapabilitySource).where(
            CapabilitySource.definition_id == definition.id,
            CapabilitySource.registered_name == "linux.system.info",
        )
    )
    source = source_result.scalar_one()
    assert source.is_active is True
    assert source.platform_os == "linux"
    assert source.execution_requirements == {"runtime_kind": "privileged"}


@pytest.mark.asyncio
async def test_meta_capability_search_and_describe_api(
    client: AsyncClient,
    provisioned_node,
) -> None:
    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_linux_system_info(client, node.node_id, token)

    search_resp = await client.get("/admin/meta/capabilities/search", params={"q": "system"})
    assert search_resp.status_code == 200, search_resp.text
    matches = search_resp.json()
    assert [item["canonical_name"] for item in matches] == ["system.info"]
    assert matches[0]["sources"][0]["registered_name"] == "linux.system.info"

    describe_resp = await client.get("/admin/meta/capabilities/linux.system.info")
    assert describe_resp.status_code == 200, describe_resp.text
    detail = describe_resp.json()
    assert detail["canonical_name"] == "system.info"
    assert detail["sources"][0]["node_id"] == node.node_id
    assert detail["sources"][0]["registered_name"] == "linux.system.info"

    node_resp = await client.get(f"/admin/meta/nodes/{node.node_id}")
    assert node_resp.status_code == 200, node_resp.text
    node_detail = node_resp.json()
    assert node_detail["capability_source_count"] == 1
    assert node_detail["capability_sources"][0]["canonical_name"] == "system.info"


@pytest.mark.asyncio
async def test_croc_status_capability_is_searchable_without_center_hardcoding(
    client: AsyncClient,
    provisioned_node,
) -> None:
    node, token = provisioned_node
    await _hello_windows_node(client, node.node_id, token)
    await _register_windows_croc_status(client, node.node_id, token)

    search_resp = await client.get("/admin/meta/capabilities/search", params={"q": "croc"})
    assert search_resp.status_code == 200, search_resp.text
    matches = search_resp.json()
    names = [item["canonical_name"] for item in matches]
    assert names == [
        "transfer.croc.receive",
        "transfer.croc.reconcile",
        "transfer.croc.send",
        "transfer.croc.status",
    ]

    describe_resp = await client.get("/admin/meta/capabilities/windows.transfer.croc.status")
    assert describe_resp.status_code == 200, describe_resp.text
    detail = describe_resp.json()
    assert detail["canonical_name"] == "transfer.croc.status"
    assert detail["sources"][0]["node_id"] == node.node_id
    assert detail["sources"][0]["resource_keys"] == ["node.transfer"]

    send_resp = await client.get("/admin/meta/capabilities/windows.transfer.croc.send")
    assert send_resp.status_code == 200, send_resp.text
    send_detail = send_resp.json()
    assert send_detail["canonical_name"] == "transfer.croc.send"
    assert send_detail["risk"] == "maintenance"
    assert send_detail["effect"] == "external"
    assert send_detail["sources"][0]["hidden_input_fields"] == ["code"]


@pytest.mark.asyncio
async def test_capability_describe_infers_artifact_outputs_from_schema(
    client: AsyncClient,
    provisioned_node,
) -> None:
    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_artifact_output_capability(client, node.node_id, token)

    describe_resp = await client.get("/admin/meta/capabilities/windows.screen.capture")
    assert describe_resp.status_code == 200, describe_resp.text
    detail = describe_resp.json()
    assert detail["artifact_outputs"] == [
        {
            "field": "artifacts",
            "kind": "center_artifact_reference",
            "description": (
                "Tool output may contain Center artifact references with "
                "artifact_id, content_type, size_bytes, and download_url."
            ),
            "producer": "windows.screen.capture",
        }
    ]


@pytest.mark.asyncio
async def test_center_meta_tool_executes_without_node_job(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.application.tool_invocation import ToolInvocationApplicationService

    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_linux_system_info(client, node.node_id, token)

    result = await ToolInvocationApplicationService(db_session).execute(
        ExecuteToolCommand(
            function_name="capability.search",
            input_data={"query": "linux system"},
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "succeeded"
    assert result.invocation_id is None
    assert result.job_id is None
    assert result.output_data is not None
    assert result.output_data["capabilities"][0]["canonical_name"] == "system.info"


@pytest.mark.asyncio
async def test_center_meta_tool_preflight_does_not_resolve_node_capability(
    db_session,
) -> None:
    from yequ.application.schemas import ToolPreflightCommand
    from yequ.application.tool_preflight import ToolPreflightApplicationService

    result = await ToolPreflightApplicationService(db_session).check(
        ToolPreflightCommand(function_name="node.list", execution_mode="auto")
    )

    assert result.status == "ok"
    assert result.function_name == "node.list"
    assert result.target_node_id is None
    assert result.risk == "safe"
    assert result.effect == "read"


@pytest.mark.asyncio
async def test_capability_invoke_by_source_id_creates_real_node_job(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.application.tool_invocation import ToolInvocationApplicationService

    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_linux_system_info(client, node.node_id, token)

    source_result = await db_session.execute(
        select(CapabilitySource).where(CapabilitySource.registered_name == "linux.system.info")
    )
    source = source_result.scalar_one()

    result = await ToolInvocationApplicationService(db_session).execute(
        ExecuteToolCommand(
            function_name="capability.invoke",
            input_data={"source_id": source.source_id, "input": {}},
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "created"
    assert result.function_name == "linux.system.info"
    assert result.target_node_id == node.node_id
    assert result.job_id is not None
    assert result.invocation_id is not None


@pytest.mark.asyncio
async def test_capability_invoke_requires_disambiguation_for_multiple_sources(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.application.tool_invocation import ToolInvocationApplicationService

    node_a, token_a = provisioned_node
    await _provision_node(db_session, node_id="linux-node-b", token="tok-linux-b")

    await _hello_linux_node(client, node_a.node_id, token_a)
    await _hello_linux_node(client, "linux-node-b", "tok-linux-b")
    await _register_linux_system_info(client, node_a.node_id, token_a)
    await _register_linux_system_info(client, "linux-node-b", "tok-linux-b")

    ambiguous = await ToolInvocationApplicationService(db_session).execute(
        ExecuteToolCommand(
            function_name="capability.invoke",
            input_data={"capability_ref": "system.info", "input": {}},
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert ambiguous.status == "failed"
    assert ambiguous.error_code == "capability_source_unresolved"
    assert "ambiguous" in (ambiguous.error_message or "")

    resolved = await ToolInvocationApplicationService(db_session).execute(
        ExecuteToolCommand(
            function_name="capability.invoke",
            input_data={
                "capability_ref": "system.info",
                "node_id": "linux-node-b",
                "input": {},
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert resolved.status == "created"
    assert resolved.function_name == "linux.system.info"
    assert resolved.target_node_id == "linux-node-b"
    assert resolved.job_id is not None
