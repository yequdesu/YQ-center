"""Agent core — orchestrates LLM + tools to answer user queries."""

from __future__ import annotations

import json
import logging
from typing import Any

from yequ.agent.providers import (
    LLMProvider,
    AgentResponse,
    ToolCall,
    create_provider,
)
from yequ.agent.tools import TOOLS, ToolHandler
from yequ.config import AgentConfig

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是 YeQu Gateway 的个人 AI 助手。你的职责是：

1. 回答用户关于设备状态、系统指标、告警事件的问题
2. 通过调用工具获取实时数据，然后基于数据给出分析
3. 如果数据异常（如 CPU 持续高、磁盘接近满、设备离线），主动指出并给出建议

你的工作方式：
- 用户问问题时，先思考需要哪些信息，然后调用对应的工具获取数据
- 获取数据后，用自然语言向用户解释，突出关键信息和异常
- 保持简洁、实用，用中文回复
- 你的宿主是 yequdesu，语气友好但专业

当前环境信息会通过工具调用获取，不要编造数据。"""


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

        # Maximum tool-call rounds to prevent infinite loops
        max_rounds = 5
        for _ in range(max_rounds):
            response = self.provider.chat(messages, tools=self.tools)

            if response.tool_calls:
                # Execute each tool call and append results
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
                continue  # Let LLM process tool results

            # No tool calls — this is the final answer
            return response.text.strip() if response.text else "（Agent 未返回文本回答）"

        return "已达到最大对话轮次，请简化你的问题。"

    def ask_streaming(self, question: str):
        """Process a user question with streaming output (yields text chunks)."""
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

            yield response.text.strip() if response.text else "（Agent 未返回文本回答）"
            return

        yield "已达到最大对话轮次，请简化你的问题。"


def create_agent(config: AgentConfig, db_path: str, data_dir: str) -> Agent:
    """Factory: create an Agent from config."""
    return Agent(config=config, db_path=db_path, data_dir=data_dir)
