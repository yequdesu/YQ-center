"""add ycr capability context snapshots

Revision ID: ff9234567890
Revises: ff9123456789
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "ff9234567890"
down_revision = "ff9123456789"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ycr_capability_context_snapshots",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("snapshot_key", sa.String(length=128), nullable=False),
        sa.Column("target_node_id", sa.String(length=128), nullable=True),
        sa.Column("function_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("registry_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index(
        "ix_ycr_capability_context_snapshots_snapshot_key",
        "ycr_capability_context_snapshots",
        ["snapshot_key"],
        unique=True,
    )
    for column in ["target_node_id", "function_fingerprint", "registry_fingerprint"]:
        op.create_index(
            f"ix_ycr_capability_context_snapshots_{column}",
            "ycr_capability_context_snapshots",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in ["registry_fingerprint", "function_fingerprint", "target_node_id"]:
        op.drop_index(
            f"ix_ycr_capability_context_snapshots_{column}",
            table_name="ycr_capability_context_snapshots",
        )
    op.drop_index(
        "ix_ycr_capability_context_snapshots_snapshot_key",
        table_name="ycr_capability_context_snapshots",
    )
    op.drop_table("ycr_capability_context_snapshots")
