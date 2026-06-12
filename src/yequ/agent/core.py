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

SYSTEM_PROMPT = """你是 YeQu Gateway 的运维助手。宿主是 yequdesu。你通过调用工具来操作系统，不能凭空编造。

【致命规则】你只能报告工具实际返回的结果。如果你没有调用工具，就不能声称执行了任何操作、不能提供 command_id 或 token。command_id 只能来自 send_command 工具的返回值。违反此规则会导致用户被误导——这是不可接受的。

工具速查：
- 执行命令 → send_command(device_id="..-executor", action="exec", params={"command": "..."})
- 查看设备 → list_devices 或 check_device_online
- 批准设备 → approve_device（先 list_pending 查看待审批列表）
- 撤销设备 → revoke_device（需要确认一次）
- 查数据 → get_device_status / get_events / get_metrics

行为：
- 用户说"做某事"→ 调用工具。不要说"我可以帮你做"——直接调用工具
- send_command 对本地服务（source_type=service/gateway）会自动等待结果，不要追问"要不要查"
- 如果返回 queued，告诉用户结果到了会通知。不要反复轮询 check_command_result
- 记住你调用工具得到的结果（command_id、token 等），对话历史可查阅
- 用中文，简洁直接"""

MAX_TOOL_ROUNDS = 30


@dataclass
class AgentEvent:
    """One event emitted during an agent run."""
    type: str  # "tool_call" | "text" | "done" | "error"
    data: Any = None


# UUID pattern for detecting hallucinated command IDs
import re as _re
_UUID_RE = _re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', _re.I)


def _is_hallucinated_text(text: str, called_send_cmd: bool) -> bool:
    """Check if the LLM's text response claims actions it didn't take."""
    if not text:
        return False
    if called_send_cmd:
        return False
    # Keywords that indicate the LLM is describing (not doing) a send_command
    triggers = ["指令ID", "command_id", "已下发", "已发送指令", "下发指令",
                "指令已发送", "命令已下发"]
    if any(t in text for t in triggers):
        return True
    # UUID pattern — LLM hallucinated a command ID
    if _UUID_RE.search(text):
        return True
    return False


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
        self._load_session()

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
        """Pi-inspired agent loop with text gate and steering."""
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        for h in self._history[-20:]:
            messages.append(h)
        messages.append({"role": "user", "content": question})

        tool_calls_made: list[dict] = []
        final_text = ""
        steering_attempts = 0
        MAX_STEERING = 5

        for _round in range(MAX_TOOL_ROUNDS):
            response = self.stream_fn(messages, self.tools)
            sent_cmd_this_round = any(
                tc.name == "send_command" for tc in (response.tool_calls or [])
            )

            if response.tool_calls:
                steering_attempts = 0  # reset on real action
                for tc in response.tool_calls:
                    yield AgentEvent(type="tool_call", data={
                        "name": tc.name, "arguments": tc.arguments, "id": tc.id,
                    })
                    result = self.handler.execute(tc.name, tc.arguments)
                    tool_calls_made.append({"name": tc.name, "arguments": tc.arguments})

                    # Anthropic-native tool_use format
                    messages.append({
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": tc.id,
                                     "name": tc.name, "input": tc.arguments}],
                    })
                    messages.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tc.id,
                                     "content": result}],
                    })
                continue

            # No tool calls — run text gate
            if _is_hallucinated_text(response.text or "", sent_cmd_this_round):
                steering_attempts += 1
                if steering_attempts > MAX_STEERING:
                    final_text = "Agent 未能正确调用工具，请重新描述你的需求。"
                    yield AgentEvent(type="text", data=final_text)
                    yield AgentEvent(type="done")
                    return
                # Steering: inject correction and continue
                messages.append({
                    "role": "user",
                    "content": ("STOP. You described an action but did not call the tool. "
                                "You MUST call the send_command tool with action='exec' "
                                "and params={'command': '...'} RIGHT NOW. "
                                "Do not describe — execute.")
                })
                continue

            # Text passes gate — accept as final answer
            if response.text:
                final_text = response.text
                yield AgentEvent(type="text", data=final_text)
            yield AgentEvent(type="done")
            self._log_conversation(question, final_text, tool_calls_made)
            self._history.append({"role": "user", "content": question})
            self._history.append({"role": "assistant", "content": final_text})
            self._save_session()
            return

        # Max rounds reached
        final_text = "已达到最大对话轮次，请简化问题。"
        yield AgentEvent(type="text", data=final_text)
        yield AgentEvent(type="done")
        self._log_conversation(question, final_text, tool_calls_made)
        self._history.append({"role": "user", "content": question})
        self._history.append({"role": "assistant", "content": final_text})
        self._save_session()

    # ── Session Persistence ─────────────────────────────────────────

    def _load_session(self):
        """Load conversation history from SQLite."""
        from yequ.storage.database import get_connection
        try:
            with get_connection(self.db_path) as conn:
                row = conn.execute(
                    "SELECT messages_json FROM agent_sessions WHERE id = 'default'"
                ).fetchone()
            if row:
                import json as _json
                self._history = _json.loads(row["messages_json"])[-20:]
        except Exception:
            pass

    def _save_session(self):
        """Persist conversation history to SQLite."""
        import json as _json
        from yequ.storage.database import get_connection
        try:
            msgs = _json.dumps(self._history[-20:], ensure_ascii=False)
            with get_connection(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO agent_sessions (id, messages_json, updated_at) "
                    "VALUES ('default', ?, datetime('now'))",
                    (msgs,),
                )
                conn.commit()
        except Exception:
            pass

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
        # Debug log: write every conversation turn to a file
        try:
            import os as _os, json as _json
            debug_path = _os.path.join(_os.path.dirname(self.db_path), "agent_debug.log")
            entry = {
                "timestamp": __import__('yequ.utils').now_iso(),
                "question": question,
                "answer": answer or "",
                "tool_calls": tool_calls,
                "model": self.config.model,
            }
            with open(debug_path, "a") as f:
                f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass


def create_agent(config: AgentConfig, db_path: str, data_dir: str) -> Agent:
    return Agent(config=config, db_path=db_path, data_dir=data_dir)
