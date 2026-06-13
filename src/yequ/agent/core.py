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
- 查看设备 → list_devices 或 get_device_status（查数据指标，不是查actions）
- 查设备能做什么操作 → list_device_actions（只返回actions，不返回数据capabilities）
- 批准设备 → approve_device（先 list_pending 查看待审批列表）
- 撤销设备 → revoke_device（需要确认一次）
- 查事件/指标 → get_events / get_metrics

注意区分：
- capabilities = 设备能提供什么数据（如CPU、内存、服务列表）
- actions = 设备能执行什么操作（如截图、exec命令）
- list_device_actions 返回空 ≠ 设备没有能力——它可能有很多数据capabilities

行为：
- 用户给了一个任务，你要一口气完成它。不要每步都问"要继续吗？"——直接继续
- 如果当前结果还不足以完整回答用户问题，立刻调下一个工具，不要停下来问
- 只有当任务彻底完成、无需更多操作时，才输出最终答案
- send_command 对本地服务会自动等待结果
- 如果返回 queued，告诉用户结果到了会通知，不要再调 check_command_result
- 用中文，简洁直接

- 当 send_command 返回 status="queued" 时，工具结果里有一个很短的 reply 字段。只输出那个字段的内容，不要加任何额外描述
- 不要对同一个设备重复调用 get_device_status 或 list_device_actions——第一次调用已经拿到全部数据
- check_command_result 只在用户明确要求查结果时调用，不要自己循环去查
- 如果一个命令连续失败或返回异常，停下来反思：是不是设备ID错了？参数格式对不对？然后修正后再试，不要重复同样的错误"""

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
    """Check if the LLM's text should be rejected instead of shown to user."""
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
    # Stopping mid-task: LLM is asking the user instead of continuing
    asking_triggers = ["要我继续", "需要我继续", "要不要我", "需要我帮你",
                       "要不要继续", "要继续吗", "要我帮你", "要让我"]
    if any(t in text for t in asking_triggers) and len(text) < 300:
        # Short response that asks user — LLM should call a tool instead
        return True
    return False


class Agent:
    """LLM + tool-use loop. Provider-agnostic via StreamFn."""

    def __init__(self, config: AgentConfig, db_path: str, data_dir: str, user_id: str = "default"):
        self.db_path = db_path
        self.config = config
        self.user_id = user_id
        self.session_id: int | None = None
        self.stream_fn = create_stream_fn(
            provider=config.provider,
            api_key=config.api_key,
            model=config.model,
        )
        self.tools = TOOLS
        self.handler = ToolHandler(db_path=db_path, config_data_dir=data_dir)
        self._history: list[dict] = []  # conversation context
        self._load_session()

    def set_session(self, session_id: int) -> None:
        """Switch to a different session (or auto-load if None)."""
        self.session_id = session_id
        self._history = []
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

            if response.tool_calls:
                steering_attempts = 0
                assistant_blocks = response.raw_blocks if response.raw_blocks else [
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                    for tc in response.tool_calls
                ]
                messages.append({"role": "assistant", "content": assistant_blocks})
                tool_results_blocks = []
                stop_immediately = False
                prebuilt_reply = None
                for tc in response.tool_calls:
                    yield AgentEvent(type="tool_call", data={
                        "name": tc.name, "arguments": tc.arguments, "id": tc.id,
                    })
                    result_raw = self.handler.execute(tc.name, tc.arguments)
                    tool_calls_made.append({"name": tc.name, "arguments": tc.arguments})
                    # Fast-path: check for STOP_HERE in tool result (parse JSON temporarily)
                    result_obj = None
                    try: result_obj = json.loads(result_raw)
                    except: pass
                    if isinstance(result_obj, dict) and result_obj.get("STOP_HERE"):
                        stop_immediately = True
                        prebuilt_reply = result_obj.get("reply", "Done.")
                    # Pass raw string content to Anthropic API
                    tool_results_blocks.append({
                        "type": "tool_result", "tool_use_id": tc.id, "content": result_raw,
                    })
                messages.append({"role": "user", "content": tool_results_blocks})
                # Fast-path exit: tool told us to stop — use pre-built reply
                if stop_immediately and prebuilt_reply:
                    final_text = prebuilt_reply
                    yield AgentEvent(type="text", data=final_text)
                    yield AgentEvent(type="done")
                    self._log_conversation(question, final_text, tool_calls_made)
                    self._history.append({"role": "user", "content": question})
                    self._history.append({"role": "assistant", "content": final_text})
                    self._save_session()
                    return
                continue

            # No tool calls — run text gate
            sent_cmd_any_round = any(t["name"] == "send_command" for t in tool_calls_made)
            if _is_hallucinated_text(response.text or "", sent_cmd_any_round):
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
        """Load conversation history from the current session (or most recent)."""
        from yequ.storage.database import get_connection
        try:
            with get_connection(self.db_path) as conn:
                if self.session_id:
                    row = conn.execute(
                        "SELECT messages_json FROM agent_sessions WHERE id = ?",
                        (self.session_id,),
                    ).fetchone()
                else:
                    # Auto-load most recent session for this user
                    row = conn.execute(
                        "SELECT id, messages_json FROM agent_sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                        (self.user_id,),
                    ).fetchone()
                    if row:
                        self.session_id = row["id"]
                if row:
                    import json as _json
                    self._history = _json.loads(row["messages_json"])[-20:]
        except Exception:
            pass

    def _save_session(self):
        """Persist conversation history to SQLite."""
        import json as _json
        from yequ.storage.database import get_connection
        from yequ.utils import now_iso
        try:
            if not self.session_id:
                # Auto-create session on first save
                with get_connection(self.db_path) as conn:
                    title = f"Session {now_iso()[:16].replace('T',' ')}"
                    cur = conn.execute(
                        "INSERT INTO agent_sessions (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                        (self.user_id, title, now_iso(), now_iso()),
                    )
                    conn.commit()
                    self.session_id = cur.lastrowid
            msgs = _json.dumps(self._history[-20:], ensure_ascii=False)
            with get_connection(self.db_path) as conn:
                conn.execute(
                    "UPDATE agent_sessions SET messages_json = ?, updated_at = ? WHERE id = ?",
                    (msgs, now_iso(), self.session_id),
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
