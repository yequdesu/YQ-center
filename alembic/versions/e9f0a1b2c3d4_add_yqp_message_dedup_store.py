"""add yqp message dedup store

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-06-25 22:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | Sequence[str] | None = "d8e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "yqp_messages",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("node_id", sa.String(length=128), nullable=False),
        sa.Column("message_type", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(op.f("ix_yqp_messages_expires_at"), "yqp_messages", ["expires_at"])
    op.create_index(op.f("ix_yqp_messages_message_id"), "yqp_messages", ["message_id"])
    op.create_index(op.f("ix_yqp_messages_message_type"), "yqp_messages", ["message_type"])
    op.create_index(op.f("ix_yqp_messages_node_id"), "yqp_messages", ["node_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_yqp_messages_node_id"), table_name="yqp_messages")
    op.drop_index(op.f("ix_yqp_messages_message_type"), table_name="yqp_messages")
    op.drop_index(op.f("ix_yqp_messages_message_id"), table_name="yqp_messages")
    op.drop_index(op.f("ix_yqp_messages_expires_at"), table_name="yqp_messages")
    op.drop_table("yqp_messages")
