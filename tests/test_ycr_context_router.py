import json

import pytest

from yequ.api.agent_tool_catalog import _center_meta_functions
from yequ.services.result_ingestion import guard_job_output
from yequ.ycr import project_tool_observation
from yequ.ycr.budget import budget_profile_from_settings
from yequ.ycr.context_packet import build_agent_context_packet
from yequ.ycr.ref_store import expand_ref, inspect_ref, search_ref, tail_ref, upsert_ref


def test_ycr_tool_projection_refs_large_stdout() -> None:
    projected = project_tool_observation(
        name="linux.logs.tail",
        call_id="call_1",
        status="succeeded",
        result={"stdout": "x" * 5000, "status": "ok"},
        target_node_id="linux-node-01",
    )

    result = projected["result"]
    assert result["truncated"] is True
    assert result["refs"]
    assert result["facts"]["stdout"]["chars"] == 5000


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


def test_center_meta_functions_include_ycr_context_tools() -> None:
    names = {function.name for function in _center_meta_functions()}

    assert "capability.search" in names
    assert "capability.describe" in names
    assert "capability.recommend" not in names
    assert "context.inspect" in names
    assert "context.search" in names
    assert "context.status" in names


def test_ycr_build_turn_rejects_unprojected_tool_message(override_settings) -> None:
    with pytest.raises(ValueError, match="unprojected_tool_observation"):
        build_agent_context_packet(
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
            budget=budget_profile_from_settings(override_settings),
            step=1,
        )


def test_ycr_build_turn_returns_provider_packet(override_settings) -> None:
    projected = project_tool_observation(
        name="node.status",
        call_id="call_1",
        status="succeeded",
        result={"node_id": "node-1", "status": "online"},
    )
    packet = build_agent_context_packet(
        session_id="sess_1",
        actor_id="agent",
        provider="test",
        model="test-model",
        messages=[
            {"role": "user", "content": "status?"},
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": json.dumps(projected),
            },
        ],
        available_functions=[{"name": "node.status", "input_schema": {}}],
        capability_context={"nodes": []},
        budget=budget_profile_from_settings(override_settings),
        step=1,
    )

    assert packet["packet_id"].startswith("ctxpkt_")
    assert packet["ycr"]["projection_policy"] == "agent_context_packet_v1"
    assert packet["budget"]["estimated_input_tokens"] > 0


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

        searched = await client.post(
            "/v1/context/search",
            json={"ref_id": ref_id, "query": "failure", "limit": 3},
        )
        assert searched.status_code == 200
        assert searched.json()["matches"]


def test_ycr_missing_ref_maps_to_structured_404() -> None:
    from fastapi import HTTPException

    from yequ.ycr_app import _raise_ref_error

    with pytest.raises(HTTPException) as exc_info:
        _raise_ref_error(ValueError("Context ref not found: ctxref_missing"))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error_code"] == "context_ref_not_found"
