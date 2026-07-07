"""add ycr session state

Revision ID: ff9123456789
Revises: ff9012345678
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "ff9123456789"
down_revision = "ff9012345678"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ycr_session_state",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_key", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("ref_id", sa.String(length=64), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=True),
        sa.Column("source_id", sa.String(length=128), nullable=True),
        sa.Column("trust_level", sa.String(length=64), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("data_json", sa.JSON(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "entity_type",
            "entity_key",
            name="uq_ycr_session_state_session_entity",
        ),
    )
    for column in [
        "session_id",
        "entity_type",
        "entity_key",
        "status",
        "ref_id",
        "source_type",
        "source_id",
    ]:
        op.create_index(
            f"ix_ycr_session_state_{column}",
            "ycr_session_state",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in [
        "source_id",
        "source_type",
        "ref_id",
        "status",
        "entity_key",
        "entity_type",
        "session_id",
    ]:
        op.drop_index(f"ix_ycr_session_state_{column}", table_name="ycr_session_state")
    op.drop_table("ycr_session_state")
