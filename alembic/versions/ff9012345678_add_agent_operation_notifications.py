"""add agent runtime plan and operation notifications

Revision ID: ff9012345678
Revises: c9d0e1f2a3b4
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "ff9012345678"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_plans",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("provider_name", sa.String(length=64), nullable=False),
        sa.Column("execution_mode", sa.String(length=16), nullable=False),
        sa.Column("target_node_id", sa.String(length=128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id"),
    )
    for column in ["plan_id", "session_id", "run_id", "turn_id", "status", "target_node_id"]:
        op.create_index(f"ix_agent_plans_{column}", "agent_plans", [column], unique=False)

    op.create_table(
        "agent_plan_steps",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=False),
        sa.Column("plan_record_id", sa.String(length=32), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["plan_record_id"], ["agent_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("step_id"),
    )
    for column in [
        "step_id",
        "plan_record_id",
        "status",
        "operation_id",
        "tool_call_id",
    ]:
        op.create_index(f"ix_agent_plan_steps_{column}", "agent_plan_steps", [column], unique=False)

    op.create_table(
        "agent_operation_notifications",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("notification_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=True),
        sa.Column("operation_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("report_turn_id", sa.String(length=32), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.operation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "operation_id",
            name="uq_agent_operation_notifications_session_operation",
        ),
    )
    op.create_index(
        "ix_agent_operation_notifications_notification_id",
        "agent_operation_notifications",
        ["notification_id"],
        unique=True,
    )
    for column in [
        "session_id",
        "operation_id",
        "event_id",
        "operation_status",
        "status",
        "report_turn_id",
    ]:
        op.create_index(
            f"ix_agent_operation_notifications_{column}",
            "agent_operation_notifications",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in [
        "report_turn_id",
        "status",
        "operation_status",
        "event_id",
        "operation_id",
        "session_id",
        "notification_id",
    ]:
        op.drop_index(
            f"ix_agent_operation_notifications_{column}",
            table_name="agent_operation_notifications",
        )
    op.drop_table("agent_operation_notifications")
    for column in [
        "tool_call_id",
        "operation_id",
        "status",
        "plan_record_id",
        "step_id",
    ]:
        op.drop_index(f"ix_agent_plan_steps_{column}", table_name="agent_plan_steps")
    op.drop_table("agent_plan_steps")
    for column in ["target_node_id", "status", "turn_id", "run_id", "session_id", "plan_id"]:
        op.drop_index(f"ix_agent_plans_{column}", table_name="agent_plans")
    op.drop_table("agent_plans")
