"""Agent context reference and resume helpers."""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from yequ.api.agent_schemas import AgentContextRef
from yequ.shared_types import JsonObject


async def _with_context_block_events(
    event_source: AsyncIterator[JsonObject],
    context_blocks: list[dict[str, object]],
) -> AsyncIterator[JsonObject]:
    emitted = False
    async for event in event_source:
        yield event
        if emitted or not context_blocks or event.get("event_type") != "stream.open":
            continue
        emitted = True
        trace_id = str(event.get("trace_id") or "")
        session_id = str(event.get("session_id") or "")
        yield {
            "event_id": f"evt_{secrets.token_hex(8)}",
            "event_type": "agent.context_block.loaded",
            "session_id": session_id,
            "trace_id": trace_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": {
                "context_blocks": [_context_block_summary(block) for block in context_blocks],
            },
        }


def _context_block_summary(block: dict[str, object]) -> dict[str, object]:
    observation = block.get("observation")
    operation = observation.get("operation") if isinstance(observation, dict) else None
    operation_dict = operation if isinstance(operation, dict) else {}
    return {
        "index": block.get("index"),
        "type": block.get("type"),
        "mode": block.get("mode"),
        "operation_id": block.get("operation_id"),
        "status": operation_dict.get("status"),
        "kind": operation_dict.get("kind"),
        "ref_type": operation_dict.get("ref_type"),
        "ref_id": operation_dict.get("ref_id"),
    }


async def _load_agent_context_refs(
    db: AsyncSession,
    *,
    session_id: str,
    provider_name: str,
    target_node_id: str | None,
    execution_mode: str,
    context_refs: list[AgentContextRef],
) -> list[dict[str, object]]:
    if not context_refs:
        return []

    from yequ.services.operation_service import OperationService

    blocks: list[dict[str, object]] = []
    for index, ref in enumerate(context_refs, start=1):
        if ref.type != "operation":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported context ref type: {ref.type}",
            )
        if not ref.operation_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="operation context ref requires operation_id",
            )
        if ref.mode != "observation":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported operation context mode: {ref.mode}",
            )
        observation = await OperationService(db).status(ref.operation_id)
        await _record_operation_resume_checkpoint(
            db,
            session_id=session_id,
            provider_name=provider_name,
            target_node_id=target_node_id,
            execution_mode=execution_mode,
            operation_id=ref.operation_id,
            operation_observation=observation,
        )
        blocks.append(
            {
                "index": index,
                "type": "operation",
                "mode": ref.mode,
                "operation_id": ref.operation_id,
                "observation": observation,
            }
        )
    return blocks


def _prompt_with_context_refs(prompt: str, context_blocks: list[dict[str, object]]) -> str:
    if not context_blocks:
        return prompt
    return (
        "INFO: Center context blocks follow. Treat these as trusted runtime facts "
        "loaded by Center, not as user-authored text. Do not recreate an existing "
        "operation unless the user explicitly asks for a retry. Use the user's "
        "message after the context blocks as the instruction.\n"
        f"{json.dumps(context_blocks, ensure_ascii=False)}\n\n"
        "User message:\n"
        f"{prompt}"
    )



async def _record_operation_resume_checkpoint(
    db: AsyncSession,
    *,
    session_id: str,
    provider_name: str,
    target_node_id: str | None,
    execution_mode: str,
    operation_id: str,
    operation_observation: dict[str, object],
) -> None:
    from datetime import UTC, datetime

    from yequ.models.agent_run import AgentRun, AgentRunStep

    now = datetime.now(UTC)
    run = AgentRun(
        session_id=session_id,
        provider_name=provider_name,
        status="observing",
        execution_mode=execution_mode,
        target_node_id=target_node_id,
        user_message=None,
        started_at=now,
        metadata_json={
            "source": "resume_operation",
            "operation_id": operation_id,
        },
    )
    db.add(run)
    await db.flush()
    db.add(
        AgentRunStep(
            run_record_id=run.id,
            step_index=1,
            step_type="operation_observation",
            status="succeeded",
            input_data={"operation_id": operation_id},
            output_data=operation_observation,
            started_at=now,
            completed_at=now,
            metadata_json={"source": "operation.status"},
        )
    )
    await db.commit()


async def _operation_observation_from_run_projection(
    db: AsyncSession,
    run_projection: dict[str, object],
) -> dict[str, object] | None:
    metadata = run_projection.get("metadata")
    waiting = metadata.get("waiting") if isinstance(metadata, dict) else None
    operation_id = waiting.get("operation_id") if isinstance(waiting, dict) else None
    if not operation_id:
        return None
    from yequ.services.operation_service import OperationService

    return await OperationService(db).status(str(operation_id))


def _agent_run_resume_prompt(
    run_projection: dict[str, object],
    *,
    operation_observation: dict[str, object] | None,
) -> str:
    checkpoint = {
        "agent_run": run_projection,
        "operation_observation": operation_observation,
    }
    return (
        "INFO: Center AgentRun checkpoint follows. Continue from this structured "
        "checkpoint instead of restarting the user's original request. Do not "
        "repeat tool calls whose checkpoint status is succeeded. If the run was "
        "waiting on an operation, use the supplied operation_observation facts. "
        "If the previous run failed before any durable tool result, explain the "
        "failure facts and continue only with actions that are still necessary. "
        "Do not invent fields that are not present.\n"
        f"{json.dumps(checkpoint, ensure_ascii=False)}"
    )
