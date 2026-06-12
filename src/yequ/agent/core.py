"""Agent core — pi-agent-core inspired event-driven loop.

The agent loop is:
  while True:
    response = stream_fn(messages, tools)
    if tool_calls:
      execute tools, append results, continue
    else:
      return text

Clean, debuggable, no streaming compatibility issues.
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

SYSTEM_PROMPT = """你是 YeQu Gateway 的个人 AI 助手。职责：

1. 回答用户关于设备状态、系统指标、告警事件的问题
2. 通过调用工具获取实时数据，基于数据给出分析
3. 数据异常时主动指出并给出建议

规则：
- 先调用工具获取数据，再回答——不要编造数据
- 用中文回复，简洁实用
- 宿主是 yequdesu，语气友好专业"""

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

    # ── Public API ────────────────────────────────────────────────

    def ask(self, question: str) -> str:
        """Blocking call: run the agent loop, return final answer."""
        text, _tool_calls, _events = self._run(question)
        return text or "（Agent 未返回文本回答）"

    def ask_stream(self, question: str) -> Iterator[AgentEvent]:
        """Streaming: run the agent loop, yield events as they happen."""
        _text, _tool_calls, events = self._run(question)
        yield from events

    # ── Core Loop ─────────────────────────────────────────────────

    def _run(self, question: str) -> tuple[str, list[dict], list[AgentEvent]]:
        """The agent loop. Returns (final_text, tool_calls_made, all_events)."""
        events: list[AgentEvent] = []
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        tool_calls_made: list[dict] = []
        final_text = ""

        for _round in range(MAX_TOOL_ROUNDS):
            response = self.stream_fn(messages, self.tools)

            if response.tool_calls:
                # Execute tools, append results to conversation
                for tc in response.tool_calls:
                    events.append(AgentEvent(type="tool_call", data={
                        "name": tc.name, "arguments": tc.arguments, "id": tc.id,
                    }))
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

            # No tool calls — this is the final answer
            if response.text:
                final_text = response.text
                events.append(AgentEvent(type="text", data=final_text))
            events.append(AgentEvent(type="done"))
            self._log_conversation(question, final_text, tool_calls_made)
            return final_text, tool_calls_made, events

        # Max rounds reached
        final_text = "已达到最大对话轮次，请简化问题。"
        events.append(AgentEvent(type="text", data=final_text))
        events.append(AgentEvent(type="done"))
        self._log_conversation(question, final_text, tool_calls_made)
        return final_text, tool_calls_made, events

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
