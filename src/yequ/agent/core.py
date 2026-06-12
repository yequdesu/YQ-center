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
- 用户说"做某事"，直接调用工具执行，不要反复确认
- 用户说"有没有待审批""批准加入请求"→ 先用 list_pending 查看待审批列表，再用 approve_device 批准
- 用户说"批准这台设备"→ approve_device；"标签改成xx"→ set_device_labels
- 只有撤销设备时才需要确认一次
- 发送指令后，如果设备是本地的（source_type=service/gateway），send_command 会自动等待结果返回
- 如果 send_command 返回 status="queued"，说明设备暂时不可达或远程设备。告诉用户指令ID，不要反复调用 check_command_result 去轮询——结果到了会通过 Dashboard 通知
- 不要问用户"要不要我帮你查""要不要等一下"——直接做或直接告知状态
- 先查数据再回答，不编造。简洁直接，用中文

关键：记住你自己做过什么。
- 调用 send_command 后会得到 command_id，务必记住它
- 调用 approve_device 后会得到 token，记住它
- 用户问"刚才的指令ID是什么""刚才的token是什么"时，从你之前的工具调用结果中查找，不要说记不住
- 对话历史中包含你所有工具调用的结果，你随时可以查阅

关于 executor 服务：
- 本机有一个 executor service（device_id 以 -executor 结尾），它可以执行任意脚本
- 用户让你"在本机执行某命令"时，先用 list_devices 找到 executor 设备，再用 list_device_actions 查看它的可用操作
- executor 的 exec action 可以运行 scripts/ 目录下的脚本，先用 list_scripts 查看有哪些可用脚本
- 不要对非 executor 设备调用 list_device_actions 去查找执行能力

即时检测设备状态：
- 用户说"检查某设备状态""检测""是否在线"→ 用 check_device_online，秒级返回
- 不要用 send_command 去检测活跃性——send_command 走指令队列，要等心跳
- check_device_online 直接读心跳时间戳，3秒内出结果
- 只有真正需要设备做事时才用 send_command"""

MAX_TOOL_ROUNDS = 20


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


def create_agent(config: AgentConfig, db_path: str, data_dir: str) -> Agent:
    return Agent(config=config, db_path=db_path, data_dir=data_dir)
