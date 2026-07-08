"""add agent run events

Revision ID: ff9345678901
Revises: ff9234567890
Create Date: 2026-07-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "ff9345678901"
down_revision = "ff9234567890"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("run_record_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("turn_id", sa.String(length=32), nullable=True),
        sa.Column("step_id", sa.String(length=64), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=True),
        sa.Column("plan_step_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_record_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_record_id", "seq", name="uq_agent_run_events_run_seq"),
    )
    for column in [
        "event_id",
        "run_record_id",
        "run_id",
        "session_id",
        "turn_id",
        "step_id",
        "plan_id",
        "plan_step_id",
        "event_type",
    ]:
        op.create_index(
            f"ix_agent_run_events_{column}",
            "agent_run_events",
            [column],
            unique=column == "event_id",
        )


def downgrade() -> None:
    for column in [
        "event_type",
        "plan_step_id",
        "plan_id",
        "step_id",
        "turn_id",
        "session_id",
        "run_id",
        "run_record_id",
        "event_id",
    ]:
        op.drop_index(f"ix_agent_run_events_{column}", table_name="agent_run_events")
    op.drop_table("agent_run_events")
