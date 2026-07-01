"""add_transfer_attempts

Revision ID: 8192a3b4c5d6
Revises: 7f8192a3b4c5
Create Date: 2026-07-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8192a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "7f8192a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transfer_attempts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("transfer_session_id", sa.String(length=32), nullable=False),
        sa.Column("transfer_id", sa.String(length=64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.String(length=32), nullable=True),
        sa.Column("target_job_id", sa.String(length=32), nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=True),
        sa.Column("relay_mode", sa.String(length=32), nullable=True),
        sa.Column("relay_url_masked", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resumable", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["transfer_session_id"], ["transfer_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transfer_session_id", "attempt", name="uq_transfer_attempt_session_attempt"
        ),
    )
    op.create_index(
        "ix_transfer_attempts_transfer_session_id", "transfer_attempts", ["transfer_session_id"]
    )
    op.create_index("ix_transfer_attempts_transfer_id", "transfer_attempts", ["transfer_id"])
    op.create_index("ix_transfer_attempts_source_job_id", "transfer_attempts", ["source_job_id"])
    op.create_index("ix_transfer_attempts_target_job_id", "transfer_attempts", ["target_job_id"])
    op.create_index("ix_transfer_attempts_status", "transfer_attempts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_transfer_attempts_status", table_name="transfer_attempts")
    op.drop_index("ix_transfer_attempts_target_job_id", table_name="transfer_attempts")
    op.drop_index("ix_transfer_attempts_source_job_id", table_name="transfer_attempts")
    op.drop_index("ix_transfer_attempts_transfer_id", table_name="transfer_attempts")
    op.drop_index("ix_transfer_attempts_transfer_session_id", table_name="transfer_attempts")
    op.drop_table("transfer_attempts")
