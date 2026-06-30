"""add_operation_bus

Revision ID: 2b3c4d5e6f70
Revises: 1b2c3d4e5f60
Create Date: 2026-06-30 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2b3c4d5e6f70"
down_revision: Union[str, Sequence[str], None] = "1b2c3d4e5f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("ref_type", sa.String(length=32), nullable=False),
        sa.Column("ref_id", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("session_id", sa.String(length=32), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("progress_pct", sa.Integer(), nullable=True),
        sa.Column("progress_message", sa.Text(), nullable=True),
        sa.Column("output_data", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("wait_policy", sa.String(length=32), nullable=False),
        sa.Column("resume_policy", sa.String(length=32), nullable=False),
        sa.Column("cancel_supported", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_index("ix_operations_operation_id", "operations", ["operation_id"])
    op.create_index("ix_operations_kind", "operations", ["kind"])
    op.create_index("ix_operations_status", "operations", ["status"])
    op.create_index("ix_operations_ref_type", "operations", ["ref_type"])
    op.create_index("ix_operations_ref_id", "operations", ["ref_id"])
    op.create_index("ix_operations_actor_id", "operations", ["actor_id"])
    op.create_index("ix_operations_session_id", "operations", ["session_id"])

    op.create_table(
        "operation_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.operation_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )
    op.create_index("ix_operation_events_event_id", "operation_events", ["event_id"])
    op.create_index("ix_operation_events_operation_id", "operation_events", ["operation_id"])
    op.create_index("ix_operation_events_event_type", "operation_events", ["event_type"])
    op.create_index("ix_operation_events_status", "operation_events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_operation_events_status", table_name="operation_events")
    op.drop_index("ix_operation_events_event_type", table_name="operation_events")
    op.drop_index("ix_operation_events_operation_id", table_name="operation_events")
    op.drop_index("ix_operation_events_event_id", table_name="operation_events")
    op.drop_table("operation_events")
    op.drop_index("ix_operations_session_id", table_name="operations")
    op.drop_index("ix_operations_actor_id", table_name="operations")
    op.drop_index("ix_operations_ref_id", table_name="operations")
    op.drop_index("ix_operations_ref_type", table_name="operations")
    op.drop_index("ix_operations_status", table_name="operations")
    op.drop_index("ix_operations_kind", table_name="operations")
    op.drop_index("ix_operations_operation_id", table_name="operations")
    op.drop_table("operations")
