"""Server-side Agent reporter for completed Operations."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

import yequ.db as yequ_db
from yequ.agent.agent_stream import agent_invoke_stream, is_session_running
from yequ.api.agent_providers import resolve_provider
from yequ.api.agent_tool_catalog import _agent_debug_metadata
from yequ.logconfig import get_logger
from yequ.models.agent_message import AgentMessage
from yequ.models.agent_turn import AgentTurn
from yequ.models.session import Session
from yequ.services.agent_operation_notifications import AgentOperationNotificationService
from yequ.services.agent_turn_service import create_agent_turn, record_agent_turn_event
from yequ.services.operation_service import OperationService
from yequ.services.session_audit import record_session_audit_event

log = get_logger(__name__)

REPORT_MAX_STEPS = 1
REPORT_MAX_DEPTH = 1
REPORT_MAX_TOTAL_DURATION_SEC = 120


class AgentOperationReporter:
    """Consume terminal Operation notifications and ask Agent to report once."""

    def __init__(self, interval_sec: int = 3) -> None:
        self._interval_sec = interval_sec
        self._task: asyncio.Task[None] | None = None
        self._active = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="agent-operation-reporter")
        log.info("agent operation reporter started", interval_sec=self._interval_sec)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        log.info("agent operation reporter stopped")

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval_sec)
                await self._scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("agent operation reporter error")

    async def _scan_once(self) -> None:
        if self._active:
            return
        self._active = True
        try:
            async with yequ_db.async_session_factory() as db:
                notification = await AgentOperationNotificationService(db).claim_next_idle(
                    is_session_active=is_session_running
                )
                await db.commit()
            if notification is None:
                return
            await report_operation_notification(notification)
        finally:
            self._active = False


async def report_operation_notification(notification: dict[str, object]) -> None:
    notification_id = str(notification.get("notification_id") or "")
    session_id = str(notification.get("session_id") or "")
    operation_id = str(notification.get("operation_id") or "")
    if not notification_id or not session_id or not operation_id:
        return

    try:
        provider_name = await _resolve_report_provider(session_id)
        provider = await resolve_provider(provider_name)
        async with yequ_db.async_session_factory() as db:
            session = await _load_session(db, session_id)
            operation_observation = await OperationService(db).status(operation_id)
        execution_mode = session.execution_mode
        prompt = _operation_report_prompt(
            operation_observation,
            preferred_language=await _preferred_language(session_id),
        )
        turn_id = await _run_internal_report_turn(
            session_id=session_id,
            provider=provider,
            prompt=prompt,
            operation_id=operation_id,
            notification_id=notification_id,
            execution_mode=execution_mode,
            operation_observation=operation_observation,
        )
        async with yequ_db.async_session_factory() as db:
            await AgentOperationNotificationService(db).mark_reported(
                notification_id=notification_id,
                turn_id=turn_id,
            )
            await db.commit()
    except Exception as exc:
        log.exception(
            "agent operation report failed",
            notification_id=notification_id,
            operation_id=operation_id,
        )
        async with yequ_db.async_session_factory() as db:
            await AgentOperationNotificationService(db).mark_failed(
                notification_id=notification_id,
                error=str(exc),
            )
            await db.commit()


async def _run_internal_report_turn(
    *,
    session_id: str,
    provider: Any,
    prompt: str,
    operation_id: str,
    notification_id: str,
    execution_mode: str,
    operation_observation: dict[str, object],
) -> str:
    turn_id: str | None = None
    completed = False
    trace_id = ""
    event_source = agent_invoke_stream(
        provider,
        session_id=session_id,
        prompt=prompt,
        user_visible_prompt="",
        target_node_id=None,
        suppress_user_message=True,
        available_functions=[],
        capability_context={},
        call_path=[],
        max_depth=REPORT_MAX_DEPTH,
        max_steps=REPORT_MAX_STEPS,
        max_total_duration_sec=REPORT_MAX_TOTAL_DURATION_SEC,
        step_count=0,
        execution_mode=execution_mode,
        run_metadata={
            "source": "agent.operation_reporter",
            "run_kind": "operation_report",
            "internal": True,
            "operation_id": operation_id,
            "notification_id": notification_id,
        },
    )
    async for event in event_source:
        event = dict(event)
        data = dict(event.get("data") or {})
        if turn_id is None:
            trace_id = str(event.get("trace_id") or "")
            turn_id = await create_agent_turn(
                session_id=session_id,
                prompt=prompt,
                provider_name=provider.provider_name(),
                target_node_id=None,
                execution_mode=execution_mode,
                trace_id=trace_id,
                metadata={
                    "suppress_user_message": True,
                    "internal": True,
                    "run_kind": "operation_report",
                    "operation_id": operation_id,
                    "notification_id": notification_id,
                    "operation_observation": operation_observation,
                    "prompt_context": _agent_debug_metadata(
                        provider,
                        available_functions=[],
                        target_node_id=None,
                        execution_mode=execution_mode,
                        capability_context={},
                    ),
                },
            )
        event["turn_id"] = turn_id
        data["turn_id"] = turn_id
        event["data"] = data
        await record_agent_turn_event(turn_id, event)
        if event.get("event_type") == "agent.completed":
            completed = True
        if event.get("event_type") in {"agent.failed", "agent.provider.failed"}:
            message = data.get("message") or data.get("error_message") or "Agent report failed"
            raise RuntimeError(str(message))
    if turn_id is None:
        raise RuntimeError("Agent operation report produced no stream events")
    if not completed:
        raise RuntimeError("Agent operation report did not complete")
    record_session_audit_event(
        session_id,
        "agent.operation_report.completed",
        {
            "operation_id": operation_id,
            "notification_id": notification_id,
            "turn_id": turn_id,
        },
        trace_id=trace_id,
        source="agent.operation_reporter",
        event_time=datetime.now(UTC),
    )
    return turn_id


async def _resolve_report_provider(session_id: str) -> str:
    async with yequ_db.async_session_factory() as db:
        result = await db.execute(
            select(AgentTurn)
            .where(AgentTurn.session_id == session_id)
            .order_by(AgentTurn.started_at.desc(), AgentTurn.id.desc())
            .limit(20)
        )
        for turn in result.scalars().all():
            metadata = turn.metadata_ if isinstance(turn.metadata_, dict) else {}
            if metadata.get("run_kind") == "operation_report":
                continue
            if turn.provider_name:
                return turn.provider_name
    return "deepseek"


async def _preferred_language(session_id: str) -> str:
    async with yequ_db.async_session_factory() as db:
        result = await db.execute(
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id)
            .where(AgentMessage.role == "user")
            .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
            .limit(8)
        )
        for message in result.scalars().all():
            content = message.content or ""
            if any("\u4e00" <= char <= "\u9fff" for char in content):
                return "zh"
    return "en"


async def _load_session(db, session_id: str) -> Session:
    result = await db.execute(select(Session).where(Session.session_id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise ValueError(f"Session {session_id!r} not found")
    return session


def _operation_report_prompt(
    operation_observation: dict[str, object],
    *,
    preferred_language: str,
) -> str:
    language_rule = "用中文汇报。" if preferred_language == "zh" else "Report in English."
    return (
        "系统内部任务：根据下面的 Center Operation 终态生成一次用户可见汇报。"
        f"{language_rule}"
        "只总结最终状态、关键结果、必要下一步；不要调用工具，不要重新执行操作，"
        "不要输出调试字段。Operation observation JSON follows:\n"
        f"{json.dumps(operation_observation, ensure_ascii=False)}"
    )


_reporter: AgentOperationReporter | None = None


def get_agent_operation_reporter(interval_sec: int = 3) -> AgentOperationReporter:
    global _reporter
    if _reporter is None:
        _reporter = AgentOperationReporter(interval_sec=interval_sec)
    return _reporter
