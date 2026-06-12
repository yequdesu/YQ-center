"""Agent core — pi-agent-core inspired event-driven loop.

The agent loop is a generator that yields events AS THEY HAPPEN:
  tool_call → execute → next round → tool_call → ... → text → done

No buffering. No collecting-then-yielding.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

from yequ.agent.providers import StreamFn, LLMResponse, ToolCall, create_stream_fn
from yequ.agent.tools import TOOLS, ToolHandler
from yequ.config import AgentConfig

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是 YeQu Gateway 的运维助手，可以直接控制系统。宿主是 yequdesu。

能力：查看设备状态、审批/撤销设备、修改标签、查询告警和指标、下发指令、控制巡检。

行为准则：
- 用户说"做某事"，你就调用对应的工具去执行——不要再问"要不要做"
- 用户说"批准这台设备"，立刻调用 approve_device；说"标签改成xx"，立刻调用 set_device_labels
- 只有在确实需要确认的敏感操作（如撤销设备）时才问一次，普通操作直接执行
- 如果有设备处于 pending_approval 状态，可以主动提醒并询问是否需要审批
- 先查数据再回答，不编造
- 简洁直接，用中文"""

MAX_TOOL_ROUNDS = 5


@dataclass
class AgentEvent:
    """One event emitted during an agent run."""
    type: str  # "tool_call" | "text" | "done" | "error"
    data: Any = None


class Agent:
    """LLM + tool-use loop. Provider-agnostic via StreamFn."""

    def __init__(self, config: AgentConfig, db_path: str, data_dir: str):
        self.db_path = db_path
        self.config = config
        self.stream_fn = create_stream_fn(
            provider=config.provider,
            api_key=config.api_key,
            model=config.model,
        )
        self.tools = TOOLS
        self.handler = ToolHandler(db_path=db_path, config_data_dir=data_dir)
        self._history: list[dict] = []  # conversation context

    # ── Public API ────────────────────────────────────────────────

    def ask(self, question: str) -> str:
        """Blocking call: collect all events, return final text."""
        text = ""
        for event in self._generate(question):
            if event.type == "text":
                text = event.data
        return text or "（Agent 未返回文本回答）"

    def ask_stream(self, question: str) -> Iterator[AgentEvent]:
        """Streaming: yield events as they happen — tool calls, then text."""
        yield from self._generate(question)

    # ── Core Generator ────────────────────────────────────────────

    def _generate(self, question: str) -> Iterator[AgentEvent]:
        """The agent loop as a generator. Yields events immediately."""
        # Build messages from history + current question
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        # Include recent history (last 10 turns) for context
        for h in self._history[-20:]:
            messages.append(h)
        messages.append({"role": "user", "content": question})

        tool_calls_made: list[dict] = []
        final_text = ""

        for _round in range(MAX_TOOL_ROUNDS):
            response = self.stream_fn(messages, self.tools)

            if response.tool_calls:
                for tc in response.tool_calls:
                    # Yield tool_call event IMMEDIATELY, before executing
                    yield AgentEvent(type="tool_call", data={
                        "name": tc.name, "arguments": tc.arguments, "id": tc.id,
                    })
                    result = self.handler.execute(tc.name, tc.arguments)
                    tool_calls_made.append({"name": tc.name, "arguments": tc.arguments})

                    messages.append({
                        "role": "assistant",
                        "content": json.dumps({
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }, ensure_ascii=False),
                    })
                    messages.append({
                        "role": "user",
                        "content": json.dumps({
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": result,
                        }, ensure_ascii=False),
                    })
                continue  # Next round with tool results

            # No tool calls — final answer
            if response.text:
                final_text = response.text
                yield AgentEvent(type="text", data=final_text)
            yield AgentEvent(type="done")
            self._log_conversation(question, final_text, tool_calls_made)
            # Remember this turn
            self._history.append({"role": "user", "content": question})
            self._history.append({"role": "assistant", "content": final_text})
            return

        # Max rounds reached
        final_text = "已达到最大对话轮次，请简化问题。"
        yield AgentEvent(type="text", data=final_text)
        yield AgentEvent(type="done")
        self._log_conversation(question, final_text, tool_calls_made)
        self._history.append({"role": "user", "content": question})
        self._history.append({"role": "assistant", "content": final_text})

    # ── Conversation Logging ──────────────────────────────────────

    def _log_conversation(self, question, answer, tool_calls):
        try:
            from yequ.storage.audit import log_conversation
            log_conversation(
                self.db_path, question, answer or "",
                tool_calls=tool_calls,
                model=self.config.model,
                provider=self.config.provider,
            )
        except Exception:
            pass


def create_agent(config: AgentConfig, db_path: str, data_dir: str) -> Agent:
    return Agent(config=config, db_path=db_path, data_dir=data_dir)
