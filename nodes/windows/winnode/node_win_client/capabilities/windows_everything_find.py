from __future__ import annotations

from typing import Any

from node_win_client.models import FunctionManifest

from .base import CapabilityContext, NodeCapability


def execute(input_data: dict[str, Any], context: CapabilityContext) -> dict[str, Any]:
    from node_win_client.l2b import file_search

    return file_search(input_data, context.l2_policy)


CAPABILITY = NodeCapability(
    manifest=FunctionManifest(
        name="windows.everything.find",
        description="Find Windows files and directories through Everything.",
        agent_description=(
            "Use for Windows file discovery by name, partial name, glob, regex, exact, "
            "fuzzy, or root. Requires Everything/es.exe. Use windows.exec.run for "
            "direct directory listing, stat, hash, or file-content inspection after "
            "an exact path is known."
        ),
        user_visible_name="Find files",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "root": {"type": "string"},
                "mode": {"type": "string", "enum": ["auto", "exact", "glob", "substring", "fuzzy", "regex"], "default": "auto"},
                "recursive": {"type": "boolean", "default": True},
                "include_files": {"type": "boolean", "default": True},
                "include_dirs": {"type": "boolean", "default": True},
                "case_sensitive": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        risk="safe",
        effect="read",
        timeout_sec=20,
        idempotency="idempotent",
        resource_keys=["node.file"],
        conflict_policy="allow_parallel",
        execution_context="user",
    ),
    handler=execute,
)
