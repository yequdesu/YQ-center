import json

import pytest

from yequ.api.agent_tool_catalog import _center_meta_functions
from yequ.services.result_ingestion import guard_job_output
from yequ.ycr.budget import projection_profile_from_settings
from yequ.ycr.context_packet import build_agent_context_packet
from yequ.ycr.entities import (
    capability_entities,
    observation_entities_from_result,
    strip_ycr_entities,
)
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
    assert result["projection_policy"] == "tool_observation_structured_ref_projection_v2"
    assert result["structured_refs"]["by_path"]["$.stdout"]["$ycr_ref"] == raw_ref["ref_id"]
    assert result["expand_hints"][0]["path"] == "$.stdout"
    assert result["expand_hints"][0]["preferred_ops"] == ["tail", "search", "expand"]


async def test_ycr_tool_projection_refs_large_exec_stdout_preview(db_session) -> None:
    value = {
        "profile": "user.readonly",
        "exit_code": 0,
        "stdout_preview": "line\n" * 5000,
        "stderr_tail": "",
        "truncated": True,
        "duration_ms": 42,
    }
    raw_ref = await upsert_ref(
        db_session,
        ref_type="tool_result",
        source_type="tool_call",
        source_id="call_exec",
        path="$",
        value=value,
        summary="linux.exec.run succeeded",
        session_id="sess_1",
    )
    projected = project_tool_observation_from_ref(
        name="linux.exec.run",
        call_id="call_exec",
        status="succeeded",
        result=value,
        raw_ref=raw_ref,
        target_node_id="linux-node-01",
    )

    result = projected["result"]
    assert result["facts"]["exit_code"] == 0
    assert result["facts"]["stdout_preview"]["$ycr_ref"] == raw_ref["ref_id"]
    assert result["facts"]["stdout_preview"]["path"] == "$.stdout_preview"
    assert result["structured_refs"]["by_path"]["$.stdout_preview"]["value_type"] == "string"
    assert result["expand_hints"][0]["preferred_ops"] == ["tail", "search", "expand"]


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
    assert result["structured_refs"]["by_path"]["$.capabilities"]["value_type"] == "array"
    assert result["expand_hints"][0]["preferred_ops"] == ["schema", "search", "expand"]
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
    assert searched["result_rag"]["status"] == "hit"
    assert searched["index_status"]["status"] == "ready"
    assert status["status"] == "ready"


async def test_ycr_context_search_reports_unindexed_ref(db_session) -> None:
    ref = await upsert_ref(
        db_session,
        ref_type="job_output",
        source_type="job",
        source_id="job_unindexed",
        path="$.stdout",
        value="alpha failure beta",
        summary="Unindexed stdout",
    )
    await db_session.commit()

    searched = await search_ref(db_session, str(ref["ref_id"]), query="failure")

    assert searched["matches"] == []
    assert searched["match_count"] == 0
    assert searched["index_status"]["status"] == "not_indexed"
    assert searched["result_rag"]["status"] == "not_indexed"


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


async def test_ycr_build_turn_compacts_old_history_and_injects_working_set(
    db_session,
    override_settings,
) -> None:
    search_result = {
        "capabilities": [
            {
                "canonical_name": "screen.capture",
                "description": "Capture the current interactive Windows desktop.",
                "risk": "safe",
                "effect": "read",
                "sources": [
                    {
                        "source_id": "src_screen",
                        "node_id": "winClient",
                        "registered_name": "windows.screen.capture",
                        "dispatchable": True,
                    }
                ],
                "invoke": {
                    "capability_ref": "screen.capture",
                    "source_id": "src_screen",
                    "node_id": "winClient",
                    "registered_name": "windows.screen.capture",
                    "dispatchable_source_count": 1,
                },
            }
        ]
    }
    raw_ref = await upsert_ref(
        db_session,
        ref_type="tool_result",
        source_type="tool_call",
        source_id="call_search",
        path="$",
        value=strip_ycr_entities(search_result),
        summary="capability.search succeeded",
        session_id="sess_compact",
        metadata={"ycr_entities": capability_entities(search_result["capabilities"])},
    )
    await db_session.commit()
    shell = tool_observation_shell(
        name="capability.search",
        call_id="call_search",
        status="succeeded",
        raw_ref=raw_ref,
    )
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": (
                f"old user message {index}: "
                "please inspect the windows desktop and file system " * 20
            ),
        }
        for index in range(12)
    ]
    messages.insert(
        2,
        {
            "role": "tool",
            "tool_call_id": "call_search",
            "content": json.dumps(shell),
        },
    )
    messages.append({"role": "user", "content": "capture the win screen"})

    packet = await build_agent_context_packet(
        db_session,
        session_id="sess_compact",
        actor_id="agent",
        provider="test",
        model="test-model",
        messages=messages,
        available_functions=[{"name": "capability.search", "input_schema": {}}],
        capability_context={"nodes": []},
        profile=projection_profile_from_settings(override_settings),
        step=4,
    )

    assert packet["context_estimate"]["history_compaction"]["compacted_message_count"] > 0
    assert packet["context_estimate"]["history_compaction_saved_tokens"] > 0
    assert packet["context_estimate"]["working_set_count"] == 1
    assert packet["provider_context"]["working_set"][0]["canonical_name"] == "screen.capture"
    assert "YCR capability working set" in packet["messages"][0]["content"]
    assert "YCR deterministic conversation summary" in packet["messages"][1]["content"]
    summary = packet["context_estimate"]["history_compaction"]["summary"]
    assert summary["mode"] == "deterministic"
    assert summary["llm"] == "disabled"


async def test_ycr_tool_observation_updates_session_state_for_next_turn(
    db_session,
    override_settings,
) -> None:
    from yequ.ycr.session_state import ingest_tool_observation_state

    result = {
        "artifacts": [
            {
                "artifact_id": "id_screenshot",
                "artifact_type": "screenshot",
                "title": "windows-screen.png",
                "content_type": "image/png",
                "size_bytes": 2048,
                "node_id": "winClient",
            }
        ],
        "operation": {
            "operation_id": "op_screenshot",
            "kind": "job",
            "status": "succeeded",
            "title": "Capture screen",
        },
        "ycr_entities": capability_entities(
            [
                {
                    "canonical_name": "screen.capture",
                    "description": "Capture the current desktop.",
                    "risk": "safe",
                    "effect": "read",
                    "invoke": {
                        "capability_ref": "screen.capture",
                        "source_id": "src_screen",
                        "node_id": "winClient",
                        "registered_name": "windows.screen.capture",
                        "dispatchable_source_count": 1,
                    },
                }
            ]
        ),
    }
    raw_ref = await upsert_ref(
        db_session,
        ref_type="tool_result",
        source_type="tool_call",
        source_id="call_capture",
        path="$",
        value=strip_ycr_entities(result),
        summary="capability.invoke succeeded",
        session_id="sess_state",
    )
    await ingest_tool_observation_state(
        db_session,
        session_id="sess_state",
        name="artifact.present",
        status="succeeded",
        result=result,
        raw_ref=raw_ref,
        target_node_id="winClient",
    )
    await db_session.commit()

    packet = await build_agent_context_packet(
        db_session,
        session_id="sess_state",
        actor_id="agent",
        provider="test",
        model="test-model",
        messages=[{"role": "user", "content": "use the previous screenshot"}],
        available_functions=[{"name": "capability.search", "input_schema": {}}],
        capability_context={"nodes": []},
        profile=projection_profile_from_settings(override_settings),
        step=2,
    )

    session_state = packet["provider_context"]["session_state"]
    candidates = packet["provider_context"]["capability_candidates"]
    assert session_state["counts"]["capability"] == 1
    assert session_state["counts"]["artifact"] == 1
    assert session_state["counts"]["focus"] == 2
    assert session_state["counts"]["operation"] == 1
    focus_items = session_state["items"]["focus"]
    current_artifact = next(
        item for item in focus_items if item["entity_key"] == "current_artifact"
    )
    assert current_artifact["data"]["artifact_id"] == "id_screenshot"
    assert current_artifact["data"]["reason"] == "last_presented_to_user"
    assert candidates[0]["capability_ref"] == "screen.capture"
    assert candidates[0]["source"] == "session_state"
    assert packet["context_estimate"]["session_state_tokens"] > 0
    assert packet["context_estimate"]["capability_candidate_count"] == 1
    assert "YCR Session State" in packet["messages"][0]["content"]


async def test_ycr_session_state_tracks_top_level_artifact_id_result(
    db_session,
) -> None:
    from yequ.ycr.session_state import ingest_tool_observation_state, load_session_state

    result = {
        "artifact_id": "id_log",
        "download_url": "/admin/artifacts/id_log/download",
        "lines": 80,
        "sha256": "a" * 64,
        "size_bytes": 4096,
    }
    raw_ref = await upsert_ref(
        db_session,
        ref_type="tool_result",
        source_type="tool_call",
        source_id="call_upload_log",
        path="$",
        value=result,
        summary="capability.invoke succeeded",
        session_id="sess_top_level_artifact",
    )
    await ingest_tool_observation_state(
        db_session,
        session_id="sess_top_level_artifact",
        name="artifact.upload_log",
        status="succeeded",
        result=result,
        raw_ref=raw_ref,
        target_node_id="linux-node-01",
    )
    await db_session.commit()

    state = await load_session_state(
        db_session,
        session_id="sess_top_level_artifact",
    )

    assert state["counts"]["artifact"] == 1
    assert state["counts"]["focus"] == 1
    artifact = state["items"]["artifact"][0]
    assert artifact["entity_key"] == "id_log"
    assert artifact["data"]["artifact_id"] == "id_log"
    assert artifact["data"]["node_id"] is None


async def test_ycr_task_state_working_set_drives_tool_strategy(
    db_session,
    override_settings,
) -> None:
    task_state = {
        "objective": {"text": "capture the windows screen"},
        "working_set": {
            "capabilities": [
                {
                    "capability_ref": "screen.capture",
                    "canonical_name": "screen.capture",
                    "source_id": "src_screen",
                    "node_id": "winClient",
                    "registered_name": "windows.screen.capture",
                    "status": "succeeded",
                }
            ],
            "nodes": [],
            "artifacts": [],
        },
        "completion": {"status": "in_progress", "criteria": [], "satisfied": [], "missing": []},
        "pending_operations": [],
        "pending_approvals": [],
        "facts": [],
        "blockers": [],
        "artifacts": [],
    }

    packet = await build_agent_context_packet(
        db_session,
        session_id="sess_strategy",
        actor_id="agent",
        provider="test",
        model="test-model",
        messages=[{"role": "user", "content": "capture the win screen"}],
        available_functions=[{"name": "capability.invoke", "input_schema": {}}],
        capability_context={"nodes": []},
        task_state=task_state,
        profile=projection_profile_from_settings(override_settings),
        step=2,
    )

    strategy = packet["provider_context"]["tool_strategy"]
    assert strategy["mode"] == "reuse_working_set"
    assert strategy["preferred_candidates"][0]["capability_ref"] == "screen.capture"
    assert [item["capability_ref"] for item in strategy["preferred_candidates"]][-2:] == [
        "capability.group.open",
        "capability.search",
    ]
    assert packet["context_estimate"]["tool_strategy_tokens"] > 0
    assert "YCR Tool Strategy" in packet["messages"][0]["content"]
    assert "YCR capability working set" in packet["messages"][2]["content"]


def test_ycr_preferred_candidates_match_visible_working_set_contract() -> None:
    from yequ.ycr.context_packet import _preferred_candidates_with_discovery_fallbacks
    from yequ.ycr.entities import WORKING_SET_LIMIT

    candidates = [
        {
            "capability_ref": f"capability.{index}",
            "source_id": f"source_{index}",
            "dispatchable": True,
        }
        for index in range(WORKING_SET_LIMIT)
    ]

    preferred = _preferred_candidates_with_discovery_fallbacks(candidates)
    refs = [str(item.get("capability_ref")) for item in preferred]

    assert refs[:WORKING_SET_LIMIT] == [
        f"capability.{index}" for index in range(WORKING_SET_LIMIT)
    ]
    assert refs[-2:] == ["capability.group.open", "capability.search"]


async def test_ycr_build_turn_bootstraps_empty_working_set_from_intent(
    db_session,
    override_settings,
    monkeypatch,
) -> None:
    async def fake_search_capability_registry(
        db,
        *,
        query=None,
        node_id=None,
        platform_os=None,
        filters=None,
        limit=10,
        rerank=True,
    ):
        assert query == "capture the windows screen"
        assert filters == {"projection": "invoke_ready"}
        assert limit == 6
        assert rerank is False
        return {
            "matches": [
                {
                    "canonical_name": "screen.capture",
                    "description": "Capture the interactive desktop.",
                    "risk": "safe",
                    "effect": "read",
                    "input_schema": {
                        "type": "object",
                        "properties": {"title": {"type": "string"}},
                    },
                    "sources": [
                        {
                            "source_id": "src_screen",
                            "node_id": "winClient",
                            "registered_name": "windows.screen.capture",
                            "dispatchable": True,
                        }
                    ],
                    "invoke": {
                        "capability_ref": "screen.capture",
                        "source_id": "src_screen",
                        "node_id": "winClient",
                        "registered_name": "windows.screen.capture",
                        "dispatchable_source_count": 1,
                    },
                }
            ],
            "retrieval": {"strategy": "fake_semantic_v1"},
        }

    import yequ.ycr.capability_gateway as capability_gateway

    monkeypatch.setattr(
        capability_gateway,
        "search_capability_registry",
        fake_search_capability_registry,
    )
    packet = await build_agent_context_packet(
        db_session,
        session_id="sess_bootstrap",
        actor_id="agent",
        provider="test",
        model="test-model",
        messages=[{"role": "user", "content": "capture the windows screen"}],
        available_functions=[{"name": "capability.invoke", "input_schema": {}}],
        capability_context={"nodes": []},
        task_state={
            "objective": {"text": "capture the windows screen"},
            "completion": {"status": "in_progress"},
            "working_set": {"capabilities": [], "nodes": [], "artifacts": []},
        },
        profile=projection_profile_from_settings(override_settings),
        step=1,
    )

    strategy = packet["provider_context"]["tool_strategy"]
    working_set = packet["provider_context"]["working_set"]
    bootstrap = packet["provider_context"]["working_set_bootstrap"]
    assert bootstrap["status"] == "loaded"
    assert bootstrap["candidate_count"] == 1
    assert strategy["mode"] == "reuse_working_set"
    assert working_set[0]["canonical_name"] == "screen.capture"
    assert working_set[0]["input_schema"]["properties"]["title"]["type"] == "string"
    assert packet["context_estimate"]["working_set_count"] == 1
    assert packet["context_estimate"]["capability_candidate_count"] == 1


async def test_ycr_build_turn_enriches_incomplete_working_set_from_intent(
    db_session,
    override_settings,
    monkeypatch,
) -> None:
    async def fake_search_capability_registry(
        db,
        *,
        query=None,
        node_id=None,
        platform_os=None,
        filters=None,
        limit=10,
        rerank=True,
    ):
        assert query == "list a known windows directory"
        assert filters == {"projection": "invoke_ready"}
        return {
            "matches": [
                {
                    "canonical_name": "exec.run",
                    "agent_description": "Run an approved command on a Node.",
                    "risk": "maintenance",
                    "effect": "write",
                    "input_schema": {
                        "type": "object",
                        "required": ["profile", "command", "reason"],
                        "properties": {
                            "profile": {
                                "type": "string",
                                "enum": ["user.readonly", "user.write"],
                            },
                            "command": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                    "sources": [
                        {
                            "source_id": "src_exec_win",
                            "node_id": "winClient",
                            "registered_name": "windows.exec.run",
                            "dispatchable": True,
                        }
                    ],
                    "invoke": {
                        "capability_ref": "exec.run",
                        "source_id": "src_exec_win",
                        "node_id": "winClient",
                        "registered_name": "windows.exec.run",
                        "dispatchable_source_count": 1,
                    },
                }
            ],
            "retrieval": {"strategy": "fake_semantic_v1"},
        }

    import yequ.ycr.capability_gateway as capability_gateway

    monkeypatch.setattr(
        capability_gateway,
        "search_capability_registry",
        fake_search_capability_registry,
    )
    packet = await build_agent_context_packet(
        db_session,
        session_id="sess_incomplete_working_set",
        actor_id="agent",
        provider="test",
        model="test-model",
        messages=[{"role": "user", "content": "list a known windows directory"}],
        available_functions=[{"name": "capability.invoke", "input_schema": {}}],
        capability_context={"nodes": []},
        task_state={
            "objective": {"text": "list a known windows directory"},
            "completion": {"status": "in_progress"},
            "working_set": {
                "capabilities": [
                    {
                        "capability_ref": "exec.run",
                        "source_id": "src_exec_win",
                        "node_id": "winClient",
                        "status": "requested",
                    }
                ],
                "nodes": [],
                "artifacts": [],
            },
        },
        profile=projection_profile_from_settings(override_settings),
        step=2,
    )

    working_set = packet["provider_context"]["working_set"]
    bootstrap = packet["provider_context"]["working_set_bootstrap"]
    assert bootstrap["status"] == "enriched"
    assert bootstrap["enriched_count"] == 1
    assert working_set[0]["capability_ref"] == "exec.run"
    assert working_set[0]["input_required"] == ["profile", "command", "reason"]
    assert working_set[0]["input_schema"]["properties"]["profile"]["enum"] == [
        "user.readonly",
        "user.write",
    ]


async def test_ycr_bootstrap_prioritizes_target_node_capabilities_over_center_meta(
    db_session,
    override_settings,
    monkeypatch,
) -> None:
    async def fake_search_capability_registry(
        db,
        *,
        query=None,
        node_id=None,
        platform_os=None,
        filters=None,
        limit=10,
        rerank=True,
    ):
        assert node_id == "winClient"
        return {
            "matches": [
                {
                    "canonical_name": "node.list",
                    "description": "List known nodes.",
                    "risk": "safe",
                    "effect": "read",
                    "scope": "center",
                    "plane": "control",
                    "provider": "center",
                    "dispatch_kind": "inline",
                    "input_schema": {"type": "object", "properties": {}},
                    "sources": [],
                    "invoke": {
                        "capability_ref": "node.list",
                        "source_id": "center:node.list",
                        "dispatchable_source_count": 1,
                    },
                },
                {
                    "canonical_name": "exec.run",
                    "description": "Run one command on a Node.",
                    "risk": "maintenance",
                    "effect": "write",
                    "scope": "node",
                    "plane": "node_runtime",
                    "dispatch_kind": "node_job",
                    "input_schema": {
                        "type": "object",
                        "required": ["profile", "command", "reason"],
                        "properties": {
                            "profile": {"type": "string"},
                            "command": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    },
                    "sources": [
                        {
                            "source_id": "src_exec_win",
                            "node_id": "winClient",
                            "registered_name": "windows.exec.run",
                            "dispatchable": True,
                        }
                    ],
                    "invoke": {
                        "capability_ref": "exec.run",
                        "source_id": "src_exec_win",
                        "node_id": "winClient",
                        "dispatchable_source_count": 1,
                    },
                },
            ],
            "retrieval": {"strategy": "fake_semantic_v1"},
        }

    import yequ.ycr.capability_gateway as capability_gateway

    monkeypatch.setattr(
        capability_gateway,
        "search_capability_registry",
        fake_search_capability_registry,
    )
    packet = await build_agent_context_packet(
        db_session,
        session_id="sess_target_node_priority",
        actor_id="agent",
        provider="test",
        model="test-model",
        messages=[{"role": "user", "content": "在 winClient 上列出 F:\\Desktop"}],
        available_functions=[{"name": "capability.invoke", "input_schema": {}}],
        capability_context={
            "nodes": [{"node_id": "winClient", "node_name": "Windows Client"}]
        },
        task_state={
            "objective": {"text": "在 winClient 上列出 F:\\Desktop"},
            "completion": {"status": "in_progress"},
            "working_set": {"capabilities": [], "nodes": [], "artifacts": []},
        },
        profile=projection_profile_from_settings(override_settings),
        step=1,
    )

    working_set = packet["provider_context"]["working_set"]
    assert packet["provider_context"]["working_set_bootstrap"]["target_node_id"] == "winClient"
    assert working_set[0]["capability_ref"] == "exec.run"
    assert working_set[0]["node_id"] == "winClient"
    assert working_set[1]["capability_ref"] == "node.list"


async def test_ycr_build_turn_augments_working_set_from_artifact_entity(
    db_session,
    override_settings,
    monkeypatch,
) -> None:
    from yequ.ycr.session_state import upsert_session_entity

    await upsert_session_entity(
        db_session,
        session_id="sess_artifact_augment",
        entity_type="artifact",
        entity_key="id_artifact",
        status="ready",
        title="windows-screen-capture.png",
        data={"artifact_id": "id_artifact", "content_type": "image/png"},
    )
    await db_session.commit()

    async def fake_search_capability_registry(
        db,
        *,
        query=None,
        node_id=None,
        platform_os=None,
        filters=None,
        limit=10,
        rerank=True,
    ):
        if query is not None:
            assert query == "show the screenshot"
            assert filters == {"projection": "invoke_ready"}
            assert limit == 6
            assert rerank is False
            return {"matches": [], "retrieval": {"strategy": "fake_semantic_v1"}}
        assert filters == {"projection": "invoke_ready", "artifact_input": True}
        assert limit == 12
        assert rerank is True
        return {
            "matches": [
                {
                    "canonical_name": "artifact.present",
                    "description": "Present a Center artifact in the Console.",
                    "risk": "safe",
                    "effect": "read",
                    "input_schema": {
                        "type": "object",
                        "required": ["artifact_ids"],
                        "properties": {"artifact_ids": {"type": "array"}},
                    },
                    "sources": [
                        {
                            "source_id": "src_artifact_present",
                            "node_id": None,
                            "registered_name": "artifact.present",
                            "dispatchable": True,
                        }
                    ],
                    "invoke": {
                        "capability_ref": "artifact.present",
                        "source_id": "src_artifact_present",
                        "registered_name": "artifact.present",
                        "dispatchable_source_count": 1,
                    },
                },
                {
                    "canonical_name": "artifact.read_text",
                    "description": "Read bounded text from a Center artifact.",
                    "risk": "safe",
                    "effect": "read",
                    "input_schema": {
                        "type": "object",
                        "properties": {"artifact_id": {"type": "string"}},
                    },
                    "sources": [
                        {
                            "source_id": "src_artifact_read_text",
                            "node_id": None,
                            "registered_name": "artifact.read_text",
                            "dispatchable": True,
                        }
                    ],
                    "invoke": {
                        "capability_ref": "artifact.read_text",
                        "source_id": "src_artifact_read_text",
                        "registered_name": "artifact.read_text",
                        "dispatchable_source_count": 1,
                    },
                }
            ],
            "retrieval": {"strategy": "registry_filter_v1"},
        }

    import yequ.ycr.capability_gateway as capability_gateway

    monkeypatch.setattr(
        capability_gateway,
        "search_capability_registry",
        fake_search_capability_registry,
    )
    packet = await build_agent_context_packet(
        db_session,
        session_id="sess_artifact_augment",
        actor_id="agent",
        provider="test",
        model="test-model",
        messages=[{"role": "user", "content": "show the screenshot"}],
        available_functions=[{"name": "capability.invoke", "input_schema": {}}],
        capability_context={"nodes": []},
        task_state={
            "objective": {"text": "show the screenshot"},
            "completion": {"status": "in_progress"},
            "working_set": {"capabilities": [], "nodes": [], "artifacts": []},
        },
        profile=projection_profile_from_settings(override_settings),
        step=2,
    )

    candidates = packet["provider_context"]["capability_candidates"]
    bootstrap = packet["provider_context"]["working_set_bootstrap"]
    assert candidates[0]["capability_ref"] == "artifact.present"
    assert candidates[0]["source"] == "working_set"
    assert candidates[0]["bound_input"] == {"artifact_ids": ["id_artifact"]}
    read_text = next(item for item in candidates if item["capability_ref"] == "artifact.read_text")
    assert read_text["bound_input"] == {"artifact_id": "id_artifact"}
    assert bootstrap["entity_augments"][0]["source"] == "artifact_entity"
    assert bootstrap["entity_augments"][0]["candidate_count"] == 2
    assert packet["provider_context"]["tool_strategy"]["mode"] == "reuse_working_set"


def test_ycr_prioritizes_bound_read_candidates_before_incomplete_writes() -> None:
    from yequ.ycr.context_packet import _prioritize_session_candidates

    candidates = [
        {
            "capability_ref": "artifact.download_file",
            "effect": "write",
            "input_required": ["artifact_id", "output_path"],
            "bound_input": {"artifact_id": "id_artifact"},
        },
        {
            "capability_ref": "artifact.read_text",
            "effect": "read",
            "input_required": [],
            "bound_input": {"artifact_id": "id_artifact"},
        },
        {
            "capability_ref": "capability.group.open",
            "effect": "read",
            "input_required": ["group"],
            "bound_input": {},
        },
    ]

    ordered = _prioritize_session_candidates(candidates)

    assert [item["capability_ref"] for item in ordered] == [
        "artifact.read_text",
        "artifact.download_file",
        "capability.group.open",
    ]


def test_result_ingestion_preserves_small_output() -> None:
    output = {"status": "ok", "value": 1}

    assert guard_job_output(output, job_id="job_1") == output


def test_ycr_observation_entities_extract_artifacts() -> None:
    entities = observation_entities_from_result(
        {
            "artifacts": [
                {
                    "artifact_id": "art_1",
                    "artifact_type": "screenshot",
                    "title": "screen.png",
                    "content_type": "image/png",
                    "node_id": "winClient",
                    "status": "available",
                    "size_bytes": 123,
                }
            ]
        }
    )

    assert entities["artifacts"][0]["artifact_id"] == "art_1"
    assert entities["artifacts"][0]["node_id"] == "winClient"


def test_ycr_observation_entities_extract_top_level_artifact_id() -> None:
    entities = observation_entities_from_result(
        {
            "artifact_id": "art_log",
            "size_bytes": 17,
        }
    )

    assert entities["artifacts"][0]["artifact_id"] == "art_log"
    assert entities["artifacts"][0]["size_bytes"] == 17


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
