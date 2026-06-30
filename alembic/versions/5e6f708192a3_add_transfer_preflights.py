"""add_transfer_preflights

Revision ID: 5e6f708192a3
Revises: 4d5e6f708192
Create Date: 2026-06-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "5e6f708192a3"
down_revision: Union[str, Sequence[str], None] = "4d5e6f708192"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transfer_preflights",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("preflight_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("intent_hash", sa.String(length=64), nullable=False),
        sa.Column("source_node_id", sa.String(length=128), nullable=False),
        sa.Column("target_node_id", sa.String(length=128), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=True),
        sa.Column("target_output_dir", sa.Text(), nullable=True),
        sa.Column("resume_mode", sa.String(length=32), nullable=False),
        sa.Column("source_fact", sa.JSON(), nullable=True),
        sa.Column("target_fact", sa.JSON(), nullable=True),
        sa.Column("failed_preconditions", sa.JSON(), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("session_id", sa.String(length=32), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("preflight_id"),
    )
    op.create_index("ix_transfer_preflights_preflight_id", "transfer_preflights", ["preflight_id"])
    op.create_index("ix_transfer_preflights_status", "transfer_preflights", ["status"])
    op.create_index("ix_transfer_preflights_intent_hash", "transfer_preflights", ["intent_hash"])
    op.create_index("ix_transfer_preflights_source_node_id", "transfer_preflights", ["source_node_id"])
    op.create_index("ix_transfer_preflights_target_node_id", "transfer_preflights", ["target_node_id"])
    op.create_index("ix_transfer_preflights_actor_id", "transfer_preflights", ["actor_id"])
    op.create_index("ix_transfer_preflights_session_id", "transfer_preflights", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_transfer_preflights_session_id", table_name="transfer_preflights")
    op.drop_index("ix_transfer_preflights_actor_id", table_name="transfer_preflights")
    op.drop_index("ix_transfer_preflights_target_node_id", table_name="transfer_preflights")
    op.drop_index("ix_transfer_preflights_source_node_id", table_name="transfer_preflights")
    op.drop_index("ix_transfer_preflights_intent_hash", table_name="transfer_preflights")
    op.drop_index("ix_transfer_preflights_status", table_name="transfer_preflights")
    op.drop_index("ix_transfer_preflights_preflight_id", table_name="transfer_preflights")
    op.drop_table("transfer_preflights")
