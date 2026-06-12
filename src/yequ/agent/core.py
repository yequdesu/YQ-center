"""Agent core — orchestrates LLM + tools to answer user queries."""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from yequ.agent.providers import (
    LLMProvider,
    AgentResponse,
    StreamEvent,
    create_provider,
)
from yequ.agent.tools import TOOLS, ToolHandler
from yequ.config import AgentConfig

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是 YeQu Gateway 的个人 AI 助手。你的职责是：

1. 回答用户关于设备状态、系统指标、告警事件的问题
2. 通过调用工具获取实时数据，然后基于数据给出分析
3. 如果数据异常（如 CPU 持续高、磁盘接近满、设备离线），主动指出并给出建议

工作方式：
- 用户问问题时，先思考需要哪些信息，调用对应工具获取数据
- 获取数据后，用自然语言向用户解释，突出关键信息和异常
- 保持简洁、实用，用中文回复
- 宿主是 yequdesu，语气友好但专业
- 不要编造数据，所有数据必须来自工具调用"""


class Agent:
    """YeQu Gateway AI Agent — LLM + tool use loop."""

    def __init__(self, config: AgentConfig, db_path: str, data_dir: str):
        self.provider = create_provider(
            provider=config.provider,
            api_key=config.api_key,
            model=config.model,
        )
        self.tools = TOOLS
        self.handler = ToolHandler(db_path=db_path, config_data_dir=data_dir)

    def ask(self, question: str) -> str:
        """Process a user question, loop through tool calls, return final answer."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        max_rounds = 5
        for _ in range(max_rounds):
            response = self.provider.chat(messages, tools=self.tools)

            if response.tool_calls:
                for tc in response.tool_calls:
                    result = self.handler.execute(tc.name, tc.arguments)
                    messages.append({
                        "role": "assistant",
                        "content": json.dumps({
                            "tool_use": {"name": tc.name, "id": tc.id, "input": tc.arguments}
                        }, ensure_ascii=False),
                    })
                    messages.append({
                        "role": "user",
                        "content": json.dumps({
                            "tool_result": {
                                "tool_use_id": tc.id,
                                "content": result,
                            }
                        }, ensure_ascii=False),
                    })
                continue

            return response.text.strip() if response.text else "（Agent 未返回文本回答）"

        return "已达到最大对话轮次，请简化你的问题。"

    def ask_stream(self, question: str) -> Iterator[StreamEvent]:
        """Process a question with streaming. Yields StreamEvent for SSE delivery.

        Events:
          {type: "tool_call", data: {name, arguments}}  — LLM wants to call a tool
          {type: "token", data: "文本片段"}              — text chunk from LLM
          {type: "done"}                                 — complete
          {type: "error", data: "错误信息"}              — error
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]

        max_rounds = 5
        for _ in range(max_rounds):
            stream = self.provider.stream(messages, tools=self.tools)

            tool_calls_in_round: list[dict] = []
            had_output = False

            for event in stream:
                if event.type == "error":
                    yield event
                    return
                elif event.type == "tool_call":
                    tool_calls_in_round.append(event.data)
                elif event.type == "token":
                    had_output = True
                    yield event
                elif event.type == "done":
                    pass  # handled after loop

            if tool_calls_in_round:
                # Yield tool call info to the client
                for tc in tool_calls_in_round:
                    yield StreamEvent(type="tool_call", data=tc)
                    result = self.handler.execute(tc["name"], tc.get("arguments", {}))
                    messages.append({
                        "role": "assistant",
                        "content": json.dumps({
                            "tool_use": {"name": tc["name"], "id": tc.get("id", ""), "input": tc.get("arguments", {})}
                        }, ensure_ascii=False),
                    })
                    messages.append({
                        "role": "user",
                        "content": json.dumps({
                            "tool_result": {
                                "tool_use_id": tc.get("id", ""),
                                "content": result,
                            }
                        }, ensure_ascii=False),
                    })
                continue  # Next round with tool results

            if had_output:
                yield StreamEvent(type="done")
                return

            # No tool calls and no text — fallback to non-stream
            response = self.provider.chat(messages, tools=self.tools)
            if response.tool_calls:
                for tc in response.tool_calls:
                    result = self.handler.execute(tc.name, tc.arguments)
                    messages.append({
                        "role": "assistant",
                        "content": json.dumps({
                            "tool_use": {"name": tc.name, "id": tc.id, "input": tc.arguments}
                        }, ensure_ascii=False),
                    })
                    messages.append({
                        "role": "user",
                        "content": json.dumps({
                            "tool_result": {"tool_use_id": tc.id, "content": result}
                        }, ensure_ascii=False),
                    })
                continue

            if response.text:
                # Fake streaming for providers without native streaming
                for i in range(0, len(response.text), 4):
                    yield StreamEvent(type="token", data=response.text[i:i+4])
                yield StreamEvent(type="done")
                return

        yield StreamEvent(type="done")


def create_agent(config: AgentConfig, db_path: str, data_dir: str) -> Agent:
    """Factory: create an Agent from config."""
    return Agent(config=config, db_path=db_path, data_dir=data_dir)
