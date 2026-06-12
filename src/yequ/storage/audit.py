"""Audit logging and conversation logging (spec 5.4).

Audit log: records all write operations (who, when, what).
Conversation log: records all agent interactions for traceability.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from yequ.storage.database import get_connection
from yequ.utils import now_iso

logger = logging.getLogger(__name__)


# ── Audit Log ─────────────────────────────────────────────────────

def log_action(
    db_path: str,
    action: str,
    target_type: str,
    target_id: str,
    actor: str = "system",
    detail: dict[str, Any] | None = None,
) -> None:
    """Record a write operation in the audit log."""
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO audit_log (timestamp, actor, action, target_type, target_id, detail_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now_iso(), actor, action, target_type, target_id,
             json.dumps(detail or {}, ensure_ascii=False)),
        )
        conn.commit()


def get_audit_log(
    db_path: str,
    action: str | None = None,
    target_type: str | None = None,
    actor: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query audit log entries with optional filters."""
    query = "SELECT * FROM audit_log WHERE 1=1"
    params: list[Any] = []

    if action:
        query += " AND action = ?"
        params.append(action)
    if target_type:
        query += " AND target_type = ?"
        params.append(target_type)
    if actor:
        query += " AND actor = ?"
        params.append(actor)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [{
        "id": r["id"],
        "timestamp": r["timestamp"],
        "actor": r["actor"],
        "action": r["action"],
        "target_type": r["target_type"],
        "target_id": r["target_id"],
        "detail": json.loads(r["detail_json"]),
    } for r in rows]


# ── Conversation Log ──────────────────────────────────────────────

def log_conversation(
    db_path: str,
    question: str,
    answer: str,
    tool_calls: list[dict] | None = None,
    channel: str = "cli",
    model: str = "",
    provider: str = "",
) -> None:
    """Record an agent conversation turn."""
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO conversation_log
               (timestamp, channel, question, answer, tool_calls_json, model, provider)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (now_iso(), channel, question, answer,
             json.dumps(tool_calls or [], ensure_ascii=False),
             model, provider),
        )
        conn.commit()


def get_conversations(
    db_path: str,
    channel: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query conversation log entries."""
    query = "SELECT * FROM conversation_log WHERE 1=1"
    params: list[Any] = []

    if channel:
        query += " AND channel = ?"
        params.append(channel)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [{
        "id": r["id"],
        "timestamp": r["timestamp"],
        "channel": r["channel"],
        "question": r["question"],
        "answer": r["answer"],
        "tool_calls": json.loads(r["tool_calls_json"]),
        "model": r["model"],
        "provider": r["provider"],
    } for r in rows]
