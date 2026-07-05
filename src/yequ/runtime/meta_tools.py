"""Inline Center meta tool execution for CenterExecutionRuntime."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from yequ.application.schemas import ExecuteToolResult
from yequ.runtime.command import RuntimeCommand
from yequ.runtime.input_utils import (
    dedupe_strings,
    int_or_default,
    required_string,
    runtime_error,
    string_list,
    string_or_none,
)


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


async def execute_inline_meta_tool(
    db: AsyncSession,
    command: RuntimeCommand,
) -> ExecuteToolResult:
    from yequ.application.transfer import TransferApplicationService, TransferPreflightCommand
    from yequ.services.artifact_service import (
        artifact_to_dict,
        get_artifact,
        list_artifacts,
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
        elif command.function_name == "capability.search":
            output = {
                "capabilities": (
                    await ycr_client.tool_search(
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
                            "projection": string_or_none(input_data.get("projection")) or "summary",
                            "capability_type": string_or_none(input_data.get("capability_type"))
                            or "function",
                            "include_inactive": bool(input_data.get("include_inactive", False)),
                        },
                        limit=min(int_or_default(input_data.get("limit"), 5), 5),
                    )
                ).get("matches", [])
            }
        elif command.function_name == "capability.describe":
            capability_ref = string_or_none(input_data.get("capability_ref")) or string_or_none(
                input_data.get("capability_id"),
            )
            if not capability_ref:
                return runtime_error(command, "invalid_input", "capability_ref is required")
            output = {
                "capability": (
                    await ycr_client.tool_describe(
                        capability_ref=capability_ref,
                        node_id=string_or_none(input_data.get("node_id")),
                        sections=string_list(input_data.get("sections")),
                        projection=string_or_none(input_data.get("projection")) or "invoke_ready",
                    )
                ).get("capability", {})
            }
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
            artifacts = await list_artifacts(
                db,
                session_id=string_or_none(input_data.get("session_id")) or command.session_id,
                invocation_id=string_or_none(input_data.get("invocation_id")),
                job_id=string_or_none(input_data.get("job_id")),
                node_id=string_or_none(input_data.get("node_id")),
                artifact_type=string_or_none(input_data.get("artifact_type")),
                limit=int_or_default(input_data.get("limit"), 20),
            )
            output = {"artifacts": [artifact_to_dict(artifact) for artifact in artifacts]}
        elif command.function_name == "artifact.get":
            artifact_id = string_or_none(input_data.get("artifact_id"))
            if not artifact_id:
                return runtime_error(command, "invalid_input", "artifact_id is required")
            artifact = await get_artifact(db, artifact_id)
            output = {"artifact": artifact_to_dict(artifact)}
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
            artifacts = [
                artifact_to_dict(await get_artifact(db, artifact_id))
                for artifact_id in artifact_ids
            ]
            output = {
                "artifacts": artifacts,
                "presentation": {
                    "kind": "artifact_gallery",
                    "count": len(artifacts),
                },
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
            output = await OperationService(db).status(operation_id)
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
            output = {"transfer": await TransferApplicationService(db).status(transfer_id)}
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
