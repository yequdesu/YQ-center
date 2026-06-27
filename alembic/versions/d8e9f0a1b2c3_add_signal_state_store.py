"""add signal state store

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-06-25 18:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signal_states",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("node_id", sa.String(length=128), nullable=False),
        sa.Column("capability_id", sa.String(length=32), nullable=True),
        sa.Column("signal_name", sa.String(length=256), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("value_schema", sa.JSON(), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=True),
        sa.Column("ttl_sec", sa.Integer(), nullable=True),
        sa.Column("freshness_status", sa.String(length=16), nullable=False),
        sa.Column("quality", sa.String(length=16), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "signal_name", name="uq_signal_states_node_signal"),
    )
    op.create_index(op.f("ix_signal_states_node_id"), "signal_states", ["node_id"], unique=False)
    op.create_index(
        op.f("ix_signal_states_signal_name"),
        "signal_states",
        ["signal_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_signal_states_signal_name"), table_name="signal_states")
    op.drop_index(op.f("ix_signal_states_node_id"), table_name="signal_states")
    op.drop_table("signal_states")
