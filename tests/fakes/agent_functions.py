"""Test-only AgentFunction fixtures.

Production Agent functions must come from Center meta tools and the capability
registry.  These static functions exist only for legacy planner/provider tests
that do not provision a node.
"""

from __future__ import annotations

from yequ.agent.provider import AgentFunction


def default_agent_functions() -> list[AgentFunction]:
    return [
        AgentFunction(
            name="system.metrics.snapshot",
            description="Get current CPU, memory, and disk usage.",
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.info",
            description="Get basic system information.",
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.service.status",
            description="Get the current status of a named service.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.processes.list",
            description="List running processes.",
            input_schema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 50}},
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.disk.detail",
            description="Get detailed disk information for all drives.",
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.network.routes",
            description="Get the node network route table.",
            input_schema={"type": "object", "properties": {}},
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
        AgentFunction(
            name="system.eventlog.query",
            description="Query recent node event log entries.",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": ["Application", "System"]},
                    "limit": {"type": "integer", "default": 50},
                },
            },
            risk="safe",
            effect="read",
            timeout_sec=10,
        ),
        AgentFunction(
            name="system.service.ensure_running",
            description="Ensure a named service is running.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            risk="maintenance",
            effect="write",
            timeout_sec=30,
        ),
        AgentFunction(
            name="system.service.restart",
            description="Restart a named service.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            risk="maintenance",
            effect="write",
            timeout_sec=30,
        ),
        AgentFunction(
            name="test.maintenance.repair_fail",
            description="TEST ONLY: Simulates a failed repair step.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
            risk="maintenance",
            effect="write",
            timeout_sec=5,
        ),
        AgentFunction(
            name="test.maintenance.verify_fail",
            description="TEST ONLY: Simulates a failed verify step.",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
            risk="safe",
            effect="read",
            timeout_sec=5,
        ),
    ]
