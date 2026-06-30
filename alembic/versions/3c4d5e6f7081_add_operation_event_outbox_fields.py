"""add_operation_event_outbox_fields

Revision ID: 3c4d5e6f7081
Revises: 2b3c4d5e6f70
Create Date: 2026-06-30 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3c4d5e6f7081"
down_revision: str | Sequence[str] | None = "2b3c4d5e6f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operation_events",
        sa.Column(
            "dispatch_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "operation_events",
        sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "operation_events",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "operation_events",
        sa.Column("last_dispatch_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_operation_events_dispatch_status",
        "operation_events",
        ["dispatch_status"],
    )
    op.alter_column("operation_events", "dispatch_status", server_default=None)
    op.alter_column("operation_events", "dispatch_attempts", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_operation_events_dispatch_status", table_name="operation_events")
    op.drop_column("operation_events", "last_dispatch_error")
    op.drop_column("operation_events", "dispatched_at")
    op.drop_column("operation_events", "dispatch_attempts")
    op.drop_column("operation_events", "dispatch_status")
