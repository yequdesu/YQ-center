"""Inline Center meta tool execution for CenterExecutionRuntime."""

from __future__ import annotations

from fnmatch import fnmatch

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.application.schemas import ExecuteToolResult
from yequ.runtime.command import RuntimeCommand
from yequ.runtime.input_utils import (
    dedupe_strings,
    int_or_default,
    int_or_none,
    required_string,
    runtime_error,
    string_list,
    string_or_none,
)

TEXT_ARTIFACT_CONTENT_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/x-ndjson",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
}
MAX_ARTIFACT_READ_BYTES = 128 * 1024


def _score_recommendation(item: dict[str, object], terms: list[str]) -> int:
    text = " ".join(
        str(item.get(key) or "")
        for key in ("canonical_name", "display_name", "description", "agent_description")
    ).lower()
    return sum(
        3 if term in str(item.get("canonical_name", "")).lower() else 1
        for term in terms
        if term in text
    )


def _is_text_artifact(content_type: object, title: object) -> bool:
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    if normalized.startswith("text/") or normalized in TEXT_ARTIFACT_CONTENT_TYPES:
        return True
    suffix = str(title or "").lower().rsplit(".", 1)
    return len(suffix) == 2 and suffix[1] in {
        "txt",
        "log",
        "md",
        "csv",
        "json",
        "jsonl",
        "xml",
        "yaml",
        "yml",
    }


def _decode_text_artifact(data: bytes, encoding: str) -> tuple[str, str]:
    normalized = encoding.strip() or "utf-8"
    try:
        return data.decode(normalized), normalized
    except LookupError as exc:
        raise ValueError(f"unknown encoding {encoding!r}") from exc
    except UnicodeDecodeError:
        if normalized.lower().replace("_", "-") == "utf-8":
            return data.decode("utf-8", errors="replace"), "utf-8-replace"
        raise ValueError(f"artifact content cannot be decoded as {encoding!r}") from None


def _slice_text(
    text: str,
    *,
    mode: str,
    lines: int,
    line_start: int | None,
    line_end: int | None,
    line_glob: str | None,
) -> tuple[str, bool, dict[str, object]]:
    if mode == "full":
        selected = text.splitlines()
        if line_glob:
            selected = [line for line in selected if fnmatch(line, line_glob)]
        return "\n".join(selected), False, _line_selection(mode, selected, line_glob)
    split = text.splitlines()
    if mode == "tail":
        sliced = split[-lines:]
    elif mode == "range":
        start = _resolve_line_index(line_start or 1, len(split), default=1)
        end = _resolve_line_index(line_end, len(split), default=start + lines - 1)
        sliced = [] if end < start else split[start - 1 : end]
    else:
        sliced = split[:lines]
    if line_glob:
        sliced = [line for line in sliced if fnmatch(line, line_glob)]
    return (
        "\n".join(sliced),
        len(split) > len(sliced) if not line_glob else True,
        _line_selection(mode, sliced, line_glob, line_start=line_start, line_end=line_end),
    )


def _resolve_line_index(value: int | None, total: int, *, default: int) -> int:
    if value is None:
        return max(1, min(default, max(total, 1)))
    if value < 0:
        return max(1, total + value + 1)
    return max(1, value)


def _line_selection(
    mode: str,
    selected: list[str],
    line_glob: str | None,
    *,
    line_start: int | None = None,
    line_end: int | None = None,
) -> dict[str, object]:
    return {
        "mode": mode,
        "selected_line_count": len(selected),
        "line_start": line_start,
        "line_end": line_end,
        "line_glob": line_glob,
    }


async def _resolve_read_artifact(
    db: AsyncSession,
    *,
    artifact_id: str | None,
    artifact_pattern: str | None,
    session_id: str | None,
):
    from yequ.services.artifact_service import get_artifact, list_artifacts

    if artifact_id:
        return await get_artifact(db, artifact_id)
    if not artifact_pattern:
        raise ValueError("artifact_id or artifact_pattern is required")
    artifacts = await list_artifacts(db, session_id=session_id, limit=100)
    matches = [
        artifact
        for artifact in artifacts
        if fnmatch(artifact.artifact_id, artifact_pattern)
        or fnmatch(str(artifact.title or ""), artifact_pattern)
    ]
    if not matches:
        raise ValueError(f"No artifact matches pattern {artifact_pattern!r}")
    for artifact in matches:
        if _is_text_artifact(artifact.content_type, artifact.title):
            return artifact
    raise ValueError(f"No text-like artifact matches pattern {artifact_pattern!r}")


def _center_function_by_name() -> dict[str, object]:
    from yequ.api.agent_tool_catalog import _center_meta_functions

    return {function.name: function for function in _center_meta_functions()}


def _center_group_summaries() -> list[dict[str, object]]:
    from yequ.api.agent_tool_catalog import center_meta_tool_groups

    groups = center_meta_tool_groups()
    return [
        {
            "group_id": group_id,
            "title": str(group.get("title") or group_id),
            "description": str(group.get("description") or ""),
            "tool_count": len(group.get("tools") or []),
            "open": {
                "capability_ref": "capability.group.open",
                "input": {"group_id": group_id},
            },
        }
        for group_id, group in groups.items()
    ]


def _open_center_group(group_id: str, *, projection: str) -> dict[str, object]:
    from yequ.api.agent_tool_catalog import center_meta_tool_groups

    groups = center_meta_tool_groups()
    group = groups.get(group_id)
    if group is None:
        raise ValueError(f"Unknown capability group {group_id!r}")
    functions = _center_function_by_name()
    tools = []
    for name in group.get("tools") or []:
        function = functions.get(str(name))
        if function is None:
            continue
        item = {
            "capability_ref": function.name,
            "canonical_name": function.name,
            "description": function.description,
            "risk": function.risk,
            "effect": function.effect,
            "dispatchable": True,
        }
        if projection == "invoke_ready":
            item["input_schema"] = function.input_schema or {}
            item["invoke"] = {
                "capability_ref": function.name,
                "input_field": "input",
            }
        tools.append(item)
    return {
        "group": {
            "group_id": group_id,
            "title": str(group.get("title") or group_id),
            "description": str(group.get("description") or ""),
            "projection": projection,
        },
        "capabilities": tools,
        "capability_count": len(tools),
    }


async def execute_inline_meta_tool(
    db: AsyncSession,
    command: RuntimeCommand,
) -> ExecuteToolResult:
    from yequ.application.transfer import TransferApplicationService, TransferPreflightCommand
    from yequ.services.artifact_service import (
        artifact_to_dict,
        get_artifact,
        list_artifacts,
        resolve_download,
    )
    from yequ.services.capability_registry import node_list, node_status
    from yequ.services.operation_service import OperationService
    from yequ.ycr.client import YcrError, get_ycr_client

    input_data = dict(command.input_data)
    ycr_client = get_ycr_client()
    try:
        if command.function_name == "node.list":
            output = {"nodes": await node_list(db)}
        elif command.function_name == "node.status":
            node_id = string_or_none(input_data.get("node_id")) or command.target_node_id
            if not node_id:
                return runtime_error(command, "invalid_input", "node_id is required")
            output = {
                "node": await node_status(
                    db,
                    node_id,
                    projection=string_or_none(input_data.get("projection")) or "summary",
                )
            }
        elif command.function_name == "capability.groups":
            output = {
                "groups": _center_group_summaries(),
                "strategy": "center_meta_group_directory_v1",
            }
        elif command.function_name == "capability.group.open":
            projection = string_or_none(input_data.get("projection")) or "invoke_ready"
            if projection not in {"summary", "invoke_ready"}:
                return runtime_error(
                    command,
                    "invalid_input",
                    "projection must be summary or invoke_ready",
                )
            output = _open_center_group(
                required_string(input_data.get("group_id"), "group_id"),
                projection=projection,
            )
        elif command.function_name == "capability.search":
            search_result = await ycr_client.tool_search(
                query=string_or_none(input_data.get("query"))
                or string_or_none(input_data.get("q")),
                node_id=string_or_none(input_data.get("node_id")),
                platform_os=string_or_none(input_data.get("platform_os")),
                filters={
                    "effect": string_or_none(input_data.get("effect")),
                    "risk": string_or_none(input_data.get("risk")),
                    "runtime_kind": string_or_none(input_data.get("runtime_kind")),
                    "runtime_labels": string_list(input_data.get("runtime_labels"))
                    or string_list(input_data.get("labels")),
                    "supports_progress": input_data.get("supports_progress"),
                    "supports_cancel": input_data.get("supports_cancel"),
                    "supports_resume": input_data.get("supports_resume"),
                    "preflight_supported": input_data.get("preflight_supported"),
                    "artifact_input": input_data.get("artifact_input"),
                    "artifact_output": input_data.get("artifact_output"),
                    "agent_visible": input_data.get("agent_visible"),
                    "invocation_surface": string_or_none(input_data.get("invocation_surface")),
                    "projection": string_or_none(input_data.get("projection")) or "summary",
                    "capability_type": string_or_none(input_data.get("capability_type"))
                    or "function",
                    "include_inactive": bool(input_data.get("include_inactive", False)),
                },
                limit=min(int_or_default(input_data.get("limit"), 5), 5),
            )
            output = {
                "capabilities": search_result.get("matches", []),
                "query": search_result.get("query", ""),
                "match_count": search_result.get("match_count", 0),
                "retrieval": search_result.get("retrieval", {}),
            }
            if isinstance(search_result.get("ycr_entities"), dict):
                output["ycr_entities"] = search_result["ycr_entities"]
        elif command.function_name == "capability.describe":
            capability_ref = string_or_none(input_data.get("capability_ref")) or string_or_none(
                input_data.get("capability_id"),
            )
            if not capability_ref:
                return runtime_error(command, "invalid_input", "capability_ref is required")
            describe_result = await ycr_client.tool_describe(
                capability_ref=capability_ref,
                node_id=string_or_none(input_data.get("node_id")),
                sections=string_list(input_data.get("sections")),
                projection=string_or_none(input_data.get("projection")) or "invoke_ready",
            )
            output = {"capability": describe_result.get("capability", {})}
            if isinstance(describe_result.get("ycr_entities"), dict):
                output["ycr_entities"] = describe_result["ycr_entities"]
        elif command.function_name == "context.status":
            output = {"ycr": await ycr_client.status()}
        elif command.function_name == "context.inspect":
            output = {
                "context": await ycr_client.inspect(
                    required_string(input_data.get("ref_id"), "ref_id")
                )
            }
        elif command.function_name == "context.expand":
            output = {
                "context": await ycr_client.expand(
                    required_string(input_data.get("ref_id"), "ref_id"),
                    path=string_or_none(input_data.get("path")) or "$",
                    limit=int_or_default(input_data.get("limit"), 20),
                )
            }
        elif command.function_name == "context.tail":
            output = {
                "context": await ycr_client.tail(
                    required_string(input_data.get("ref_id"), "ref_id"),
                    path=string_or_none(input_data.get("path")) or "$",
                    lines=int_or_default(input_data.get("lines"), 40),
                )
            }
        elif command.function_name == "context.schema":
            output = {
                "context": await ycr_client.schema(
                    required_string(input_data.get("ref_id"), "ref_id"),
                    path=string_or_none(input_data.get("path")) or "$",
                )
            }
        elif command.function_name == "context.search":
            output = {
                "context": await ycr_client.search(
                    string_or_none(input_data.get("ref_id")),
                    query=required_string(input_data.get("query"), "query"),
                    limit=int_or_default(input_data.get("limit"), 10),
                    session_id=command.session_id,
                )
            }
        elif command.function_name == "artifact.list":
            projection = string_or_none(input_data.get("projection")) or "summary"
            artifacts = await list_artifacts(
                db,
                session_id=string_or_none(input_data.get("session_id")) or command.session_id,
                invocation_id=string_or_none(input_data.get("invocation_id")),
                job_id=string_or_none(input_data.get("job_id")),
                node_id=string_or_none(input_data.get("node_id")),
                artifact_type=string_or_none(input_data.get("artifact_type")),
                limit=int_or_default(input_data.get("limit"), 20),
            )
            output = {
                "artifacts": [
                    artifact_to_dict(artifact, projection=projection) for artifact in artifacts
                ],
                "projection": projection,
            }
        elif command.function_name == "artifact.get":
            artifact_id = string_or_none(input_data.get("artifact_id"))
            if not artifact_id:
                return runtime_error(command, "invalid_input", "artifact_id is required")
            artifact = await get_artifact(db, artifact_id)
            projection = string_or_none(input_data.get("projection")) or "summary"
            output = {
                "artifact": artifact_to_dict(artifact, projection=projection),
                "projection": projection,
            }
        elif command.function_name == "artifact.read_text":
            artifact_id = string_or_none(input_data.get("artifact_id"))
            artifact_pattern = string_or_none(input_data.get("artifact_pattern"))
            session_id = string_or_none(input_data.get("session_id")) or command.session_id
            mode = string_or_none(input_data.get("mode")) or "head"
            if mode not in {"head", "tail", "range", "full"}:
                return runtime_error(
                    command,
                    "invalid_input",
                    "mode must be head, tail, range, or full",
                )
            max_bytes = min(
                int_or_default(input_data.get("max_bytes"), 64 * 1024),
                MAX_ARTIFACT_READ_BYTES,
            )
            if max_bytes < 1:
                return runtime_error(command, "invalid_input", "max_bytes must be positive")
            lines = max(1, min(int_or_default(input_data.get("lines"), 200), 2000))
            line_start = int_or_none(input_data.get("line_start"))
            line_end = int_or_none(input_data.get("line_end"))
            line_glob = string_or_none(input_data.get("line_glob"))
            encoding = string_or_none(input_data.get("encoding")) or "utf-8"
            artifact_ref = await _resolve_read_artifact(
                db,
                artifact_id=artifact_id,
                artifact_pattern=artifact_pattern,
                session_id=session_id,
            )
            download = await resolve_download(db, artifact_ref.artifact_id)
            artifact = download.artifact
            content_type = artifact.content_type or download.blob.content_type
            if not _is_text_artifact(content_type, artifact.title):
                return runtime_error(
                    command,
                    "unsupported_artifact_type",
                    "artifact.read_text only supports text-like artifacts; "
                    f"artifact_id={artifact.artifact_id}, content_type={content_type}, "
                    f"artifact_type={artifact.artifact_type}",
                )
            raw = download.path.read_bytes()
            read_from_tail = mode == "tail" or (
                mode == "range" and any((value or 0) < 0 for value in (line_start, line_end))
            )
            raw_slice = raw[-max_bytes:] if read_from_tail else raw[:max_bytes]
            try:
                text, used_encoding = _decode_text_artifact(raw_slice, encoding)
            except ValueError as exc:
                return runtime_error(command, "invalid_encoding", str(exc))
            content, line_truncated, line_selection = _slice_text(
                text,
                mode=mode,
                lines=lines,
                line_start=line_start,
                line_end=line_end,
                line_glob=line_glob,
            )
            byte_truncated = len(raw) > len(raw_slice)
            output = {
                "artifact": artifact_to_dict(artifact, projection="summary"),
                "content": content,
                "read": {
                    "mode": mode,
                    "encoding": used_encoding,
                    "bytes_read": len(raw_slice),
                    "total_bytes": len(raw),
                    "max_bytes": max_bytes,
                    "lines": lines,
                    "truncated": byte_truncated or line_truncated,
                    "byte_truncated": byte_truncated,
                    "line_truncated": line_truncated,
                    "line_selection": line_selection,
                    "artifact_pattern": artifact_pattern,
                },
            }
        elif command.function_name == "artifact.present":
            artifact_ids = string_list(input_data.get("artifact_ids"))
            artifact_id = string_or_none(input_data.get("artifact_id"))
            if artifact_id:
                artifact_ids = [artifact_id, *artifact_ids]
            artifact_ids = dedupe_strings(artifact_ids)
            if not artifact_ids:
                return runtime_error(
                    command,
                    "invalid_input",
                    "artifact_id or artifact_ids is required",
                )
            if len(artifact_ids) > 10:
                return runtime_error(
                    command,
                    "invalid_input",
                    "artifact.present can show at most 10 artifacts",
                )
            projection = string_or_none(input_data.get("projection")) or "summary"
            artifacts = [
                artifact_to_dict(await get_artifact(db, artifact_id), projection=projection)
                for artifact_id in artifact_ids
            ]
            output = {
                "artifacts": artifacts,
                "presentation": {
                    "kind": "artifact_gallery",
                    "count": len(artifacts),
                },
                "projection": projection,
            }
        elif command.function_name == "artifact.deploy.preflight":
            from yequ.application.artifact_deploy import (
                ArtifactDeployApplicationService,
                ArtifactDeployPreflightCommand,
            )

            output = {
                "preflight": await ArtifactDeployApplicationService(db).preflight(
                    ArtifactDeployPreflightCommand(
                        artifact_id=required_string(
                            input_data.get("artifact_id"),
                            "artifact_id",
                        ),
                        target_node_id=required_string(
                            input_data.get("target_node_id"),
                            "target_node_id",
                        ),
                        output_path=required_string(
                            input_data.get("output_path"),
                            "output_path",
                        ),
                        mode=string_or_none(input_data.get("mode")) or "fail_if_exists",
                        timeout_sec=int_or_default(input_data.get("timeout_sec"), 20),
                        ttl_sec=int_or_default(input_data.get("ttl_sec"), 120),
                        actor_type=command.actor_type,
                        actor_id=command.actor_id,
                        session_id=command.session_id,
                        execution_mode=command.execution_mode,
                    )
                )
            }
        elif command.function_name == "operation.status":
            operation_id = required_string(input_data.get("operation_id"), "operation_id")
            output = await OperationService(db).status(
                operation_id,
                projection=string_or_none(input_data.get("projection")) or "summary",
            )
        elif command.function_name == "operation.cancel":
            operation_id = required_string(input_data.get("operation_id"), "operation_id")
            output = await OperationService(db).cancel(
                operation_id,
                reason=string_or_none(input_data.get("reason")) or "operation_cancelled",
            )
        elif command.function_name == "transfer.preflight":
            output = {
                "preflight": await TransferApplicationService(db).preflight(
                    TransferPreflightCommand(
                        source_node_id=required_string(
                            input_data.get("source_node_id"),
                            "source_node_id",
                        ),
                        target_node_id=required_string(
                            input_data.get("target_node_id"),
                            "target_node_id",
                        ),
                        source_path=required_string(
                            input_data.get("source_path"),
                            "source_path",
                        ),
                        target_output_dir=string_or_none(input_data.get("target_output_dir")),
                        target_path=string_or_none(input_data.get("target_path")),
                        relay_url=string_or_none(input_data.get("relay_url")),
                        route_policy=string_or_none(input_data.get("route_policy")),
                        direct_ip=string_or_none(input_data.get("direct_ip")),
                        multicast_address=string_or_none(input_data.get("multicast_address")),
                        resume_mode=string_or_none(input_data.get("resume_mode")),
                        include_sha256=bool(input_data.get("include_sha256", False)),
                        timeout_sec=int_or_default(input_data.get("timeout_sec"), 20),
                        ttl_sec=int_or_default(input_data.get("ttl_sec"), 120),
                        actor_type=command.actor_type,
                        actor_id=command.actor_id,
                        session_id=command.session_id,
                        execution_mode=command.execution_mode,
                    )
                )
            }
        elif command.function_name == "transfer.status":
            transfer_id = required_string(input_data.get("transfer_id"), "transfer_id")
            output = {
                "transfer": await TransferApplicationService(db).status(
                    transfer_id,
                    projection=string_or_none(input_data.get("projection")) or "summary",
                )
            }
        elif command.function_name == "transfer.cancel":
            transfer_id = required_string(input_data.get("transfer_id"), "transfer_id")
            output = {
                "transfer": await TransferApplicationService(db).cancel(
                    transfer_id,
                    reason=string_or_none(input_data.get("reason")) or "transfer_cancelled",
                )
            }
        else:
            return runtime_error(command, "unknown_meta_tool", command.function_name)
    except YcrError as exc:
        return runtime_error(command, exc.code, exc.message)
    except ValueError as exc:
        return runtime_error(command, "not_found", str(exc))

    return ExecuteToolResult(
        status="succeeded",
        function_name=command.function_name,
        target_node_id=command.target_node_id,
        risk="safe",
        effect="read",
        output_data=output,
    )
