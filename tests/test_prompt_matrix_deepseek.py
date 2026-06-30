"""DeepSeek Prompt Matrix -- manual integration test.

Tests that DeepSeek correctly selects functions based on natural language prompts.
Run manually: YEQU_DEEPSEEK_API_KEY=sk-... pytest tests/test_prompt_matrix_deepseek.py -v -s

SKIP in CI by default (requires DeepSeek API key).
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("YEQU_DEEPSEEK_API_KEY"),
    reason="YEQU_DEEPSEEK_API_KEY not set",
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt,expected_function,expected_input",
    [
        ("get system metrics", "system.metrics.snapshot", {}),
        ("CPU usage", "system.metrics.snapshot", {}),
        ("memory and disk status", "system.metrics.snapshot", {}),
        ("system information", "system.info", {}),
        ("what machine is this", "system.info", {}),
        ("check Spooler service", "system.service.status", {"name": "Spooler"}),
        ("is EventLog running", "system.service.status", {"name": "EventLog"}),
        ("list processes", "system.processes.list", {}),
        ("show running programs", "system.processes.list", {}),
        ("disk space", "system.disk.detail", {}),
        ("how much free disk", "system.disk.detail", {}),
        ("check system event log", "system.eventlog.query", {}),
        ("recent errors", "system.eventlog.query", {}),
        ("network route table", "system.network.routes", {}),
        ("show routing table", "system.network.routes", {}),
        ("next hop routes", "system.network.routes", {}),
        ("full system check", "system.metrics.snapshot", {}),  # at minimum
    ],
)
async def test_deepseek_tool_selection(prompt, expected_function, expected_input):
    """DeepSeek selects the right function for each prompt."""
    from tests.fakes.agent_functions import default_agent_functions
    from yequ.agent.deepseek_provider import DeepSeekProvider

    provider = DeepSeekProvider()
    funcs = default_agent_functions()

    result = await provider.invoke(prompt, available_functions=funcs)

    assert result.success, f"DeepSeek failed: {result.error_message}"
    assert len(result.tool_calls) >= 1, f"No tool calls for: {prompt}"

    names = [tc["name"] for tc in result.tool_calls]
    assert expected_function in names, f"Expected {expected_function} for '{prompt}', got {names}"

    # Check input if specified
    if expected_input:
        matching = [tc for tc in result.tool_calls if tc["name"] == expected_function]
        for key, val in expected_input.items():
            assert matching[0]["input"].get(key) == val, (
                f"Expected input {key}={val} for '{prompt}', got {matching[0]['input']}"
            )

    print(f"  PASS: '{prompt}' -> {names}")
