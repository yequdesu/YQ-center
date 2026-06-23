"""add_agent_turn_events

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    from alembic import context
    from sqlalchemy import inspect

    conn = context.get_context().connection
    return table in inspect(conn).get_table_names()


def upgrade() -> None:
    if not _has_table("agent_turns"):
        op.create_table(
            "agent_turns",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("turn_id", sa.String(length=32), nullable=False),
            sa.Column("session_id", sa.String(length=32), nullable=False),
            sa.Column("trace_id", sa.String(length=64), nullable=False),
            sa.Column("provider_name", sa.String(length=64), nullable=False),
            sa.Column("target_node_id", sa.String(length=128), nullable=True),
            sa.Column("execution_mode", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("prompt", sa.Text(), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("turn_id"),
        )
        op.create_index("ix_agent_turns_turn_id", "agent_turns", ["turn_id"], unique=False)
        op.create_index("ix_agent_turns_session_id", "agent_turns", ["session_id"], unique=False)
        op.create_index("ix_agent_turns_trace_id", "agent_turns", ["trace_id"], unique=False)
        op.create_index("ix_agent_turns_status", "agent_turns", ["status"], unique=False)

    if not _has_table("agent_turn_events"):
        op.create_table(
            "agent_turn_events",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("event_id", sa.String(length=64), nullable=False),
            sa.Column("turn_id", sa.String(length=32), nullable=False),
            sa.Column("session_id", sa.String(length=32), nullable=False),
            sa.Column("trace_id", sa.String(length=64), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=96), nullable=False),
            sa.Column("data", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id"),
        )
        op.create_index("ix_agent_turn_events_event_id", "agent_turn_events", ["event_id"], unique=False)
        op.create_index("ix_agent_turn_events_turn_id", "agent_turn_events", ["turn_id"], unique=False)
        op.create_index("ix_agent_turn_events_session_id", "agent_turn_events", ["session_id"], unique=False)
        op.create_index("ix_agent_turn_events_trace_id", "agent_turn_events", ["trace_id"], unique=False)
        op.create_index("ix_agent_turn_events_event_type", "agent_turn_events", ["event_type"], unique=False)
        op.create_index("ix_agent_turn_events_created_at", "agent_turn_events", ["created_at"], unique=False)


def downgrade() -> None:
    if _has_table("agent_turn_events"):
        op.drop_index("ix_agent_turn_events_created_at", table_name="agent_turn_events")
        op.drop_index("ix_agent_turn_events_event_type", table_name="agent_turn_events")
        op.drop_index("ix_agent_turn_events_trace_id", table_name="agent_turn_events")
        op.drop_index("ix_agent_turn_events_session_id", table_name="agent_turn_events")
        op.drop_index("ix_agent_turn_events_turn_id", table_name="agent_turn_events")
        op.drop_index("ix_agent_turn_events_event_id", table_name="agent_turn_events")
        op.drop_table("agent_turn_events")
    if _has_table("agent_turns"):
        op.drop_index("ix_agent_turns_status", table_name="agent_turns")
        op.drop_index("ix_agent_turns_trace_id", table_name="agent_turns")
        op.drop_index("ix_agent_turns_session_id", table_name="agent_turns")
        op.drop_index("ix_agent_turns_turn_id", table_name="agent_turns")
        op.drop_table("agent_turns")
