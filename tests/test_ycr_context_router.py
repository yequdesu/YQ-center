import json

import pytest

from yequ.api.agent_tool_catalog import _center_meta_functions
from yequ.services.result_ingestion import guard_job_output
from yequ.ycr.budget import projection_profile_from_settings
from yequ.ycr.context_packet import build_agent_context_packet
from yequ.ycr.projection import project_tool_observation_from_ref, tool_observation_shell
from yequ.ycr.ref_store import (
    expand_ref,
    index_ref_chunks,
    inspect_ref,
    search_ref,
    tail_ref,
    upsert_ref,
)


async def test_ycr_tool_projection_refs_large_stdout(db_session) -> None:
    raw_ref = await upsert_ref(
        db_session,
        ref_type="tool_result",
        source_type="tool_call",
        source_id="call_1",
        path="$",
        value={"stdout": "x" * 20000, "status": "ok"},
        summary="linux.logs.tail succeeded",
        session_id="sess_1",
    )
    projected = project_tool_observation_from_ref(
        name="linux.logs.tail",
        call_id="call_1",
        status="succeeded",
        result={"stdout": "x" * 20000, "status": "ok"},
        raw_ref=raw_ref,
        target_node_id="linux-node-01",
    )

    result = projected["result"]
    assert result["refs"]
    assert result["facts"]["status"] == "ok"
    assert result["facts"]["stdout"]["$ycr_ref"] == raw_ref["ref_id"]
    assert result["facts"]["stdout"]["path"] == "$.stdout"


async def test_ycr_tool_projection_refs_medium_meta_tool_output(db_session) -> None:
    value = {
        "capabilities": [
            {
                "canonical_name": f"tool.{index}",
                "description": "diagnostic capability " * 12,
            }
            for index in range(30)
        ]
    }
    raw_ref = await upsert_ref(
        db_session,
        ref_type="tool_result",
        source_type="tool_call",
        source_id="call_meta",
        path="$",
        value=value,
        summary="capability.search succeeded",
        session_id="sess_1",
    )
    projected = project_tool_observation_from_ref(
        name="capability.search",
        call_id="call_meta",
        status="succeeded",
        result=value,
        raw_ref=raw_ref,
    )

    result = projected["result"]
    assert result["refs"]
    assert result["facts"]["capabilities"]["$ycr_ref"] == raw_ref["ref_id"]
    assert result["facts"]["capabilities"]["path"] == "$.capabilities"
    assert result["context_estimate"]["saved_estimated_tokens"] > 0


async def test_ycr_projection_keeps_many_small_decision_fields_inline(db_session) -> None:
    value = {
        "preflight": {
            "allowed": True,
            "decision": "allow",
            "preflight_id": "tpf_test",
            "failed_preconditions": [],
            "source": {"path": "E:\\file.zip", "found": True, "readable": True},
            "target": {"path": "/home/user/", "exists": True, "writable": True},
            "source_runtime": {
                f"fact_{index}": f"value_{index}"
                for index in range(80)
            },
        }
    }
    raw_ref = await upsert_ref(
        db_session,
        ref_type="tool_result",
        source_type="tool_call",
        source_id="call_preflight",
        path="$",
        value=value,
        summary="transfer.preflight succeeded",
        session_id="sess_1",
    )
    projected = project_tool_observation_from_ref(
        name="transfer.preflight",
        call_id="call_preflight",
        status="succeeded",
        result=value,
        raw_ref=raw_ref,
    )

    facts = projected["result"]["facts"]
    assert facts["preflight"]["allowed"] is True
    assert facts["preflight"]["preflight_id"] == "tpf_test"
    assert facts["preflight"]["source"]["path"] == "E:\\file.zip"
    assert "$ycr_ref" not in facts


async def test_ycr_context_tools_expand_and_search_ref(db_session) -> None:
    ref = await upsert_ref(
        db_session,
        ref_type="job_output",
        source_type="job",
        source_id="job_ref",
        path="$.stdout",
        value=("alpha\n" * 300) + "beta failure\nomega",
        summary="Persisted stdout",
    )
    await db_session.commit()
    ref_id = str(ref["ref_id"])
    await index_ref_chunks(db_session, ref_id)
    await db_session.commit()

    inspected = await inspect_ref(db_session, ref_id)
    expanded = await expand_ref(db_session, ref_id)
    tail = await tail_ref(db_session, ref_id, lines=2)
    searched = await search_ref(db_session, ref_id, query="failure")
    status = {"status": "ready"}

    assert inspected["ref"]["ref_id"] == ref_id
    assert "alpha" in expanded["value"]
    assert tail["tail"] == ["beta failure", "omega"]
    assert "failure" in searched["matches"][0]["snippet"]
    assert status["status"] == "ready"


async def test_ycr_expand_large_root_requires_specific_path(db_session, override_settings) -> None:
    override_settings.ycr_projection_inline_bytes = 512
    ref = await upsert_ref(
        db_session,
        ref_type="job_output",
        source_type="job",
        source_id="job_large_root",
        path="$",
        value={"stdout": "alpha" * 300, "status": "succeeded"},
        summary="Large root output",
    )
    await db_session.commit()

    root = await expand_ref(db_session, str(ref["ref_id"]))
    stdout = await expand_ref(db_session, str(ref["ref_id"]), path="$.stdout")

    assert root["status"] == "path_required"
    assert "$.stdout" in root["available_paths"]
    assert "value" not in root
    assert "alpha" in stdout["value"]


def test_center_meta_functions_include_ycr_context_tools() -> None:
    names = {function.name for function in _center_meta_functions()}

    assert "capability.search" in names
    assert "capability.describe" in names
    assert "capability.invoke" in names
    assert "capability.recommend" not in names
    assert "context.inspect" in names
    assert "context.search" in names
    assert "context.status" in names


async def test_ycr_build_turn_rejects_unprojected_tool_message(
    db_session,
    override_settings,
) -> None:
    with pytest.raises(ValueError, match="unprojected_tool_observation"):
        await build_agent_context_packet(
            db_session,
            session_id="sess_1",
            actor_id="agent",
            provider="test",
            model="test-model",
            messages=[
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": '{"status":"succeeded","result":{"raw":true}}',
                }
            ],
            available_functions=[],
            capability_context={},
            profile=projection_profile_from_settings(override_settings),
            step=1,
        )


async def test_ycr_build_turn_returns_provider_packet(db_session, override_settings) -> None:
    raw_ref = await upsert_ref(
        db_session,
        ref_type="tool_result",
        source_type="tool_call",
        source_id="call_1",
        path="$",
        value={"node_id": "node-1", "status": "online"},
        summary="node.status succeeded",
        session_id="sess_1",
    )
    await db_session.commit()
    shell = tool_observation_shell(
        name="node.status",
        call_id="call_1",
        status="succeeded",
        raw_ref=raw_ref,
    )
    packet = await build_agent_context_packet(
        db_session,
        session_id="sess_1",
        actor_id="agent",
        provider="test",
        model="test-model",
        messages=[
            {"role": "user", "content": "status?"},
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": json.dumps(shell),
            },
        ],
        available_functions=[{"name": "node.status", "input_schema": {}}],
        capability_context={"nodes": []},
        profile=projection_profile_from_settings(override_settings),
        step=1,
    )

    assert packet["packet_id"].startswith("ctxpkt_")
    assert packet["ycr"]["projection_policy"] == "agent_context_packet_v2"
    assert packet["context_estimate"]["estimated_input_tokens"] > 0


def test_result_ingestion_preserves_small_output() -> None:
    output = {"status": "ok", "value": 1}

    assert guard_job_output(output, job_id="job_1") == output


def test_result_ingestion_bounds_large_string_field() -> None:
    output = {"stdout": "x" * (70 * 1024), "status": "ok"}

    guarded = guard_job_output(output, job_id="job_1")

    assert guarded["status"] == "ok"
    assert guarded["stdout"]["ycr_ingestion"]["truncated"] is True
    assert guarded["stdout"]["ycr_ingestion"]["reason"] == "string_size_limit"


async def test_ycr_persistent_ref_store_searches_chunks(db_session) -> None:
    ref = await upsert_ref(
        db_session,
        ref_type="job_output",
        source_type="job",
        source_id="job_persistent",
        path="$.stdout",
        value="alpha\nbeta failure\nomega",
        summary="Persisted stdout",
    )
    await db_session.commit()
    await index_ref_chunks(db_session, str(ref["ref_id"]))
    await db_session.commit()

    expanded = await expand_ref(db_session, str(ref["ref_id"]))
    searched = await search_ref(db_session, str(ref["ref_id"]), query="failure")

    assert "beta failure" in expanded["value"]
    assert searched["matches"]
    assert searched["matches"][0]["path"] == "$"


async def test_ycr_http_service_refs_and_search() -> None:
    from httpx import ASGITransport, AsyncClient

    from yequ.ycr_app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://ycr-test") as client:
        status = await client.get("/v1/context/status")
        assert status.status_code == 200

        created = await client.post(
            "/v1/context/refs",
            json={
                "ref_type": "job_output",
                "source_type": "job",
                "source_id": "job_http",
                "path": "$.stdout",
                "value": "alpha beta failure",
                "summary": "stdout",
            },
        )
        assert created.status_code == 200
        ref_id = created.json()["ref_id"]

        indexed = await client.post("/v1/context/index", json={"ref_id": ref_id})
        assert indexed.status_code == 200

        searched = await client.post(
            "/v1/context/search",
            json={"ref_id": ref_id, "query": "failure", "limit": 3},
        )
        assert searched.status_code == 200
        assert searched.json()["matches"]


async def test_ycr_http_tool_observation_stores_raw_ref_shell() -> None:
    from httpx import ASGITransport, AsyncClient

    from yequ.ycr_app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://ycr-test") as client:
        stored = await client.post(
            "/v1/tool-observations",
            json={
                "name": "capability.search",
                "call_id": "call_projection_ref",
                "status": "succeeded",
                "result": {
                    "capabilities": [{"canonical_name": f"tool.{index}"} for index in range(12)]
                },
            },
        )
        assert stored.status_code == 200
        raw_ref = stored.json()["raw_ref"]
        shell = stored.json()["shell"]
        assert shell["ycr"]["kind"] == "tool_observation_shell"

        expanded = await client.post(
            "/v1/context/expand",
            json={"ref_id": raw_ref["ref_id"], "path": "$.capabilities", "limit": 20},
        )
        assert expanded.status_code == 200
        assert expanded.json()["value"][0]["canonical_name"] == "tool.0"


def test_ycr_missing_ref_maps_to_structured_404() -> None:
    from fastapi import HTTPException

    from yequ.ycr_app import _raise_ref_error

    with pytest.raises(HTTPException) as exc_info:
        _raise_ref_error(ValueError("Context ref not found: ctxref_missing"))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error_code"] == "context_ref_not_found"
