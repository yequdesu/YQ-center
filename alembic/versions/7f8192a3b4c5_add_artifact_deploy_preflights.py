"""add_artifact_deploy_preflights

Revision ID: 7f8192a3b4c5
Revises: 6f708192a3b4
Create Date: 2026-06-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "7f8192a3b4c5"
down_revision: Union[str, Sequence[str], None] = "6f708192a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "artifact_deploy_preflights",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("preflight_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("intent_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("target_node_id", sa.String(length=128), nullable=False),
        sa.Column("output_path", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("artifact_fact", sa.JSON(), nullable=True),
        sa.Column("target_fact", sa.JSON(), nullable=True),
        sa.Column("failed_preconditions", sa.JSON(), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("session_id", sa.String(length=32), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.UniqueConstraint("preflight_id"),
    )
    op.create_index(
        "ix_artifact_deploy_preflights_preflight_id",
        "artifact_deploy_preflights",
        ["preflight_id"],
    )
    op.create_index(
        "ix_artifact_deploy_preflights_status",
        "artifact_deploy_preflights",
        ["status"],
    )
    op.create_index(
        "ix_artifact_deploy_preflights_intent_hash",
        "artifact_deploy_preflights",
        ["intent_hash"],
    )
    op.create_index(
        "ix_artifact_deploy_preflights_artifact_id",
        "artifact_deploy_preflights",
        ["artifact_id"],
    )
    op.create_index(
        "ix_artifact_deploy_preflights_target_node_id",
        "artifact_deploy_preflights",
        ["target_node_id"],
    )
    op.create_index(
        "ix_artifact_deploy_preflights_actor_id",
        "artifact_deploy_preflights",
        ["actor_id"],
    )
    op.create_index(
        "ix_artifact_deploy_preflights_session_id",
        "artifact_deploy_preflights",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_artifact_deploy_preflights_session_id",
        table_name="artifact_deploy_preflights",
    )
    op.drop_index(
        "ix_artifact_deploy_preflights_actor_id",
        table_name="artifact_deploy_preflights",
    )
    op.drop_index(
        "ix_artifact_deploy_preflights_target_node_id",
        table_name="artifact_deploy_preflights",
    )
    op.drop_index(
        "ix_artifact_deploy_preflights_artifact_id",
        table_name="artifact_deploy_preflights",
    )
    op.drop_index(
        "ix_artifact_deploy_preflights_intent_hash",
        table_name="artifact_deploy_preflights",
    )
    op.drop_index(
        "ix_artifact_deploy_preflights_status",
        table_name="artifact_deploy_preflights",
    )
    op.drop_index(
        "ix_artifact_deploy_preflights_preflight_id",
        table_name="artifact_deploy_preflights",
    )
    op.drop_table("artifact_deploy_preflights")
