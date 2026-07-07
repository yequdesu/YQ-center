from __future__ import annotations

import pytest

from yequ.runtime.command import RuntimeCommand
from yequ.runtime.meta_tools import execute_inline_meta_tool
from yequ.ycr.client import YcrError


@pytest.mark.asyncio
async def test_meta_tool_propagates_ycr_error_message(db_session, monkeypatch) -> None:
    class FailingYcrClient:
        async def status(self) -> dict[str, object]:
            raise YcrError("context_router_unavailable", "YCR unavailable")

    monkeypatch.setattr("yequ.ycr.client.get_ycr_client", lambda: FailingYcrClient())

    result = await execute_inline_meta_tool(
        db_session,
        RuntimeCommand(function_name="context.status", input_data={}),
    )

    assert result.status == "failed"
    assert result.error_code == "context_router_unavailable"
    assert result.error_message == "YCR unavailable"


@pytest.mark.asyncio
async def test_capability_meta_tools_preserve_ycr_entities(db_session, monkeypatch) -> None:
    entities = {
        "capabilities": [
            {
                "key": "screen.capture",
                "node_id": "winClient",
                "canonical_name": "screen.capture",
            }
        ]
    }

    class FakeYcrClient:
        async def tool_search(self, **_payload) -> dict[str, object]:
            return {
                "matches": [{"canonical_name": "screen.capture"}],
                "query": "screenshot",
                "match_count": 1,
                "retrieval": {"mode": "test"},
                "ycr_entities": entities,
            }

        async def tool_describe(self, **_payload) -> dict[str, object]:
            return {
                "capability": {"canonical_name": "screen.capture"},
                "ycr_entities": entities,
            }

    monkeypatch.setattr("yequ.ycr.client.get_ycr_client", lambda: FakeYcrClient())

    search_result = await execute_inline_meta_tool(
        db_session,
        RuntimeCommand(function_name="capability.search", input_data={"query": "screenshot"}),
    )
    describe_result = await execute_inline_meta_tool(
        db_session,
        RuntimeCommand(
            function_name="capability.describe",
            input_data={"capability_ref": "screen.capture"},
        ),
    )

    assert search_result.status == "succeeded"
    assert search_result.output_data["ycr_entities"] == entities
    assert describe_result.status == "succeeded"
    assert describe_result.output_data["ycr_entities"] == entities
