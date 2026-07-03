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


async def _hello_platform_node(
    client: AsyncClient,
    node_id: str,
    token: str,
    *,
    platform_os: str,
) -> None:
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.hello",
            node_id,
            payload={
                "daemon_version": "0.2.0",
                "platform": {"os": platform_os, "arch": "x86_64"},
                "runtimes": [
                    {
                        "runtime_id": f"{platform_os}-system",
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


async def _register_platform_system_info(
    client: AsyncClient,
    node_id: str,
    token: str,
    *,
    platform_os: str,
) -> None:
    resp = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": f"{platform_os}.system",
                        "plugin_version": "1.0",
                        "status": "loaded",
                        "functions": [
                            {
                                "name": f"{platform_os}.system.info",
                                "description": f"Read {platform_os} system information.",
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


async def _register_linux_transfer_capability(
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
                        "plugin_id": "linux.transfer",
                        "plugin_version": "1.0",
                        "status": "loaded",
                        "functions": [
                            {
                                "name": "linux.transfer.croc.receive",
                                "description": "Receive files with croc.",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "code": {"type": "string"},
                                        "output_dir": {"type": "string"},
                                    },
                                    "required": ["code", "output_dir"],
                                },
                                "output_schema": {"type": "object"},
                                "risk": "maintenance",
                                "effect": "external",
                                "timeout_sec": 3600,
                                "execution_requirements": {
                                    "runtime_kind": "privileged",
                                    "labels": ["linux", "transfer"],
                                },
                                "resource_keys": ["node.transfer"],
                                "conflict_policy": "serialize",
                                "preflight_supported": True,
                                "supports_progress": True,
                                "supports_cancel": True,
                                "supports_resume": True,
                                "progress_contract": "transfer_progress_v1",
                                "preconditions": [
                                    {
                                        "fact": "target.output_dir_writable",
                                        "source": "preflight",
                                    }
                                ],
                                "required_intent_slots": [
                                    "source_node",
                                    "target_node",
                                    "target_output_dir",
                                ],
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
async def test_platform_prefix_uses_reported_platform_os_without_center_enum(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    node, token = provisioned_node
    await _hello_platform_node(client, node.node_id, token, platform_os="freebsd")
    await _register_platform_system_info(client, node.node_id, token, platform_os="freebsd")

    definition_result = await db_session.execute(
        select(CapabilityDefinition).where(
            CapabilityDefinition.canonical_name == "system.info",
            CapabilityDefinition.capability_type == "function",
        )
    )
    definition = definition_result.scalar_one()
    assert "freebsd.system.info" in definition.aliases

    source_result = await db_session.execute(
        select(CapabilitySource).where(
            CapabilitySource.definition_id == definition.id,
            CapabilitySource.registered_name == "freebsd.system.info",
        )
    )
    source = source_result.scalar_one()
    assert source.platform_os == "freebsd"

    search_resp = await client.get(
        "/admin/meta/capabilities/search",
        params={"q": "system", "platform_os": "freebsd"},
    )
    assert search_resp.status_code == 200, search_resp.text
    matches = search_resp.json()
    assert [item["canonical_name"] for item in matches] == ["system.info"]
    assert matches[0]["sources"][0]["registered_name"] == "freebsd.system.info"


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
async def test_capability_search_filters_by_artifact_contract(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.runtime import CenterExecutionRuntime

    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_artifact_output_capability(client, node.node_id, token)

    output_result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="capability.search",
            input_data={
                "query": "screen capture",
                "artifact_output": True,
                "projection": "summary",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )
    assert output_result.status == "succeeded"
    assert output_result.output_data is not None
    assert [
        item["canonical_name"] for item in output_result.output_data["capabilities"]
    ] == ["screen.capture"]

    input_result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="capability.search",
            input_data={
                "query": "screen capture",
                "artifact_input": True,
                "projection": "summary",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )
    assert input_result.status == "succeeded"
    assert input_result.output_data is not None
    assert input_result.output_data["capabilities"] == []


@pytest.mark.asyncio
async def test_center_meta_tool_executes_without_node_job(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.runtime import CenterExecutionRuntime

    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_linux_system_info(client, node.node_id, token)

    result = await CenterExecutionRuntime(db_session).execute(
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
async def test_capability_search_supports_structured_filters_and_projection(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.runtime import CenterExecutionRuntime

    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_linux_system_info(client, node.node_id, token)
    await _register_linux_transfer_capability(client, node.node_id, token)

    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="capability.search",
            input_data={
                "query": "transfer receive",
                "platform_os": "linux",
                "effect": "external",
                "runtime_kind": "privileged",
                "runtime_labels": ["transfer"],
                "supports_progress": True,
                "preflight_supported": True,
                "projection": "invoke_ready",
                "limit": 5,
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "succeeded"
    assert result.output_data is not None
    capabilities = result.output_data["capabilities"]
    assert len(capabilities) == 1
    capability = capabilities[0]
    assert capability["canonical_name"] == "transfer.croc.receive"
    assert "query:transfer" in " ".join(capability["match_reasons"])
    assert "filter:platform_os=linux" in capability["match_reasons"]
    assert "input_schema" not in capability
    source = capability["sources"][0]
    assert source["node_id"] == node.node_id
    assert source["dispatchable"] is True
    assert source["unavailable_reasons"] == []
    assert source["execution_requirements"]["labels"] == ["linux", "transfer"]
    assert source["supports_progress"] is True
    assert source["supports_cancel"] is True
    assert source["supports_resume"] is True
    assert source["required_intent_slots"] == [
        "source_node",
        "target_node",
        "target_output_dir",
    ]


@pytest.mark.asyncio
async def test_capability_search_reports_unavailable_reasons_for_offline_node(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.models.node import Node
    from yequ.runtime import CenterExecutionRuntime

    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_linux_transfer_capability(client, node.node_id, token)

    node_result = await db_session.execute(select(Node).where(Node.node_id == node.node_id))
    node_model = node_result.scalar_one()
    node_model.status = "offline"
    await db_session.commit()

    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="capability.search",
            input_data={
                "query": "transfer receive",
                "projection": "diagnostics",
                "include_inactive": True,
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "succeeded"
    assert result.output_data is not None
    source = result.output_data["capabilities"][0]["sources"][0]
    assert source["dispatchable"] is False
    assert source["unavailable_reasons"] == [
        {
            "code": "node_offline",
            "message": f"Node {node.node_id} is offline.",
            "node_id": node.node_id,
            "node_status": "offline",
        }
    ]


@pytest.mark.asyncio
async def test_capability_describe_sections_return_only_requested_detail(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.runtime import CenterExecutionRuntime

    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_linux_transfer_capability(client, node.node_id, token)

    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="capability.describe",
            input_data={
                "capability_ref": "transfer.croc.receive",
                "node_id": node.node_id,
                "sections": ["preconditions"],
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "succeeded"
    assert result.output_data is not None
    capability = result.output_data["capability"]
    assert capability["canonical_name"] == "transfer.croc.receive"
    assert capability["preconditions"] == [
        {"fact": "target.output_dir_writable", "source": "preflight"}
    ]
    assert capability["required_intent_slots"] == [
        "source_node",
        "target_node",
        "target_output_dir",
    ]
    assert "input_schema" not in capability
    assert "sources" not in capability


@pytest.mark.asyncio
async def test_capability_describe_diagnostics_reports_contract_issues(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.runtime import CenterExecutionRuntime

    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_linux_system_info(client, node.node_id, token)

    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="capability.describe",
            input_data={
                "capability_ref": "system.info",
                "node_id": node.node_id,
                "projection": "diagnostics",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "succeeded"
    assert result.output_data is not None
    source = result.output_data["capability"]["sources"][0]
    issue_codes = {item["code"] for item in source["contract_issues"]}
    assert "missing_agent_description" in issue_codes
    assert "input_schema_allows_implicit_fields" in issue_codes


@pytest.mark.asyncio
async def test_capability_diagnostics_reports_artifact_download_contract_issues(
    client: AsyncClient,
    db_session,
    provisioned_node,
) -> None:
    from yequ.application.schemas import ExecuteToolCommand
    from yequ.runtime import CenterExecutionRuntime

    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    registered = await client.post(
        "/yqp/",
        json=make_yqp_envelope(
            "node.register_capabilities",
            node.node_id,
            payload={
                "plugins": [
                    {
                        "plugin_id": "linux.artifact",
                        "plugin_version": "1.0",
                        "status": "loaded",
                        "functions": [
                            {
                                "name": "linux.artifact.download_file",
                                "description": "Download a Center artifact to a Linux file path.",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {
                                        "artifact_id": {"type": "string"},
                                        "output_path": {"type": "string"},
                                    },
                                    "required": ["artifact_id", "output_path"],
                                    "additionalProperties": False,
                                },
                                "output_schema": {"type": "object"},
                                "risk": "maintenance",
                                "effect": "write",
                                "timeout_sec": 300,
                                "execution_requirements": {
                                    "runtime_kind": "privileged",
                                    "labels": ["linux", "artifact"],
                                },
                                "resource_keys": ["node.filesystem", "center.artifact"],
                                "conflict_policy": "serialize",
                                "supports_resume": True,
                            }
                        ],
                        "signals": [],
                    }
                ]
            },
        ),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert registered.status_code == 200, registered.text

    result = await CenterExecutionRuntime(db_session).execute(
        ExecuteToolCommand(
            function_name="capability.describe",
            input_data={
                "capability_ref": "artifact.download_file",
                "node_id": node.node_id,
                "projection": "diagnostics",
            },
            actor_type="agent",
            actor_id="test-agent",
        )
    )

    assert result.status == "succeeded"
    assert result.output_data is not None
    source = result.output_data["capability"]["sources"][0]
    issue_codes = {item["code"] for item in source["contract_issues"]}
    assert "artifact_download_missing_intent_slots" in issue_codes
    assert "artifact_download_missing_preconditions" in issue_codes
    assert "artifact_download_resume_not_supported" in issue_codes


@pytest.mark.asyncio
async def test_admin_meta_capability_diagnostics_exposes_contract_issues(
    client: AsyncClient,
    provisioned_node,
) -> None:
    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_linux_system_info(client, node.node_id, token)

    response = await client.get(
        "/admin/meta/capabilities/system.info",
        params={
            "node_id": node.node_id,
            "projection": "diagnostics",
        },
    )

    assert response.status_code == 200, response.text
    source = response.json()["sources"][0]
    issue_codes = {item["code"] for item in source["contract_issues"]}
    assert "missing_agent_description" in issue_codes


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
    from yequ.runtime import CenterExecutionRuntime

    node, token = provisioned_node
    await _hello_linux_node(client, node.node_id, token)
    await _register_linux_system_info(client, node.node_id, token)

    source_result = await db_session.execute(
        select(CapabilitySource).where(CapabilitySource.registered_name == "linux.system.info")
    )
    source = source_result.scalar_one()

    result = await CenterExecutionRuntime(db_session).execute(
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
    from yequ.runtime import CenterExecutionRuntime

    node_a, token_a = provisioned_node
    await _provision_node(db_session, node_id="linux-node-b", token="tok-linux-b")

    await _hello_linux_node(client, node_a.node_id, token_a)
    await _hello_linux_node(client, "linux-node-b", "tok-linux-b")
    await _register_linux_system_info(client, node_a.node_id, token_a)
    await _register_linux_system_info(client, "linux-node-b", "tok-linux-b")

    ambiguous = await CenterExecutionRuntime(db_session).execute(
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

    resolved = await CenterExecutionRuntime(db_session).execute(
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
