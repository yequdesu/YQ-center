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
