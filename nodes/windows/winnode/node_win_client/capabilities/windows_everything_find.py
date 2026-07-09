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
            "Find files or directories on this Windows node through Everything/es.exe. "
            "For filename, partial-name, wildcard, regex, exact, or fuzzy search, put "
            "the search text in query. Use root only to restrict results to a directory "
            "or to list a directory when query is empty. Use windows.exec.run for stat, "
            "hash, or file-content inspection after an exact path is known."
        ),
        user_visible_name="Find files",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search text for a file or directory name/path. Put the requested "
                        "filename, partial name, wildcard pattern, or regex here."
                    ),
                },
                "root": {
                    "type": "string",
                    "description": (
                        "Optional absolute Windows directory that limits results, such as "
                        "C:\\Users\\YeQuDesu\\Desktop. May be used alone to list that tree."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "exact", "glob", "substring", "fuzzy", "regex"],
                    "default": "auto",
                    "description": (
                        "How query is matched. Use auto unless the user asks for "
                        "regex, exact, or wildcard matching."
                    ),
                },
                "recursive": {
                    "type": "boolean",
                    "default": True,
                    "description": "When root is set, include descendants under that root.",
                },
                "include_files": {"type": "boolean", "default": True},
                "include_dirs": {"type": "boolean", "default": True},
                "case_sensitive": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 50},
            },
            "anyOf": [{"required": ["query"]}, {"required": ["root"]}],
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
        examples=[
            {"input": {"query": "SillyTavern-1.17.0.zip", "mode": "auto", "limit": 20}},
            {"input": {"root": "F:\\Desktop", "query": "*.zip", "mode": "glob", "recursive": True}},
            {"input": {"root": "C:\\Users\\YeQuDesu\\Desktop", "limit": 50}},
        ],
    ),
    handler=execute,
)
