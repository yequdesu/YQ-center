"""add_transfer_sessions

Revision ID: 1b2c3d4e5f60
Revises: 0a1b2c3d4e5f
Create Date: 2026-06-29 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1b2c3d4e5f60"
down_revision: Union[str, Sequence[str], None] = "0a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transfer_sessions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("transfer_id", sa.String(length=64), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_node_id", sa.String(length=128), nullable=False),
        sa.Column("target_node_id", sa.String(length=128), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=True),
        sa.Column("target_output_dir", sa.Text(), nullable=True),
        sa.Column("source_invocation_id", sa.String(length=32), nullable=True),
        sa.Column("target_invocation_id", sa.String(length=32), nullable=True),
        sa.Column("source_job_id", sa.String(length=32), nullable=True),
        sa.Column("target_job_id", sa.String(length=32), nullable=True),
        sa.Column("artifact_id", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("relay_url", sa.Text(), nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=True),
        sa.Column("resume_mode", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("session_id", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transfer_id"),
    )
    op.create_index("ix_transfer_sessions_transfer_id", "transfer_sessions", ["transfer_id"])
    op.create_index("ix_transfer_sessions_status", "transfer_sessions", ["status"])
    op.create_index("ix_transfer_sessions_source_node_id", "transfer_sessions", ["source_node_id"])
    op.create_index("ix_transfer_sessions_target_node_id", "transfer_sessions", ["target_node_id"])
    op.create_index(
        "ix_transfer_sessions_source_invocation_id",
        "transfer_sessions",
        ["source_invocation_id"],
    )
    op.create_index(
        "ix_transfer_sessions_target_invocation_id",
        "transfer_sessions",
        ["target_invocation_id"],
    )
    op.create_index("ix_transfer_sessions_source_job_id", "transfer_sessions", ["source_job_id"])
    op.create_index("ix_transfer_sessions_target_job_id", "transfer_sessions", ["target_job_id"])
    op.create_index("ix_transfer_sessions_artifact_id", "transfer_sessions", ["artifact_id"])
    op.create_index("ix_transfer_sessions_actor_id", "transfer_sessions", ["actor_id"])
    op.create_index("ix_transfer_sessions_session_id", "transfer_sessions", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_transfer_sessions_session_id", table_name="transfer_sessions")
    op.drop_index("ix_transfer_sessions_actor_id", table_name="transfer_sessions")
    op.drop_index("ix_transfer_sessions_artifact_id", table_name="transfer_sessions")
    op.drop_index("ix_transfer_sessions_target_job_id", table_name="transfer_sessions")
    op.drop_index("ix_transfer_sessions_source_job_id", table_name="transfer_sessions")
    op.drop_index("ix_transfer_sessions_target_invocation_id", table_name="transfer_sessions")
    op.drop_index("ix_transfer_sessions_source_invocation_id", table_name="transfer_sessions")
    op.drop_index("ix_transfer_sessions_target_node_id", table_name="transfer_sessions")
    op.drop_index("ix_transfer_sessions_source_node_id", table_name="transfer_sessions")
    op.drop_index("ix_transfer_sessions_status", table_name="transfer_sessions")
    op.drop_index("ix_transfer_sessions_transfer_id", table_name="transfer_sessions")
    op.drop_table("transfer_sessions")
