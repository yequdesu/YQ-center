"""replace croc transfer fields with rclone conflict mode

Revision ID: 8f9a0b1c2d3e
Revises: 7f8192a3b4c5
Create Date: 2026-07-01 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "8f9a0b1c2d3e"
down_revision: str | Sequence[str] | None = "7f8192a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transfer_sessions",
        sa.Column(
            "conflict_mode",
            sa.String(length=32),
            nullable=False,
            server_default="fail_if_exists",
        ),
    )
    op.add_column(
        "transfer_preflights",
        sa.Column(
            "conflict_mode",
            sa.String(length=32),
            nullable=False,
            server_default="fail_if_exists",
        ),
    )
    op.execute("UPDATE transfer_sessions SET transport = 'rclone_sftp' WHERE transport = 'croc'")
    op.drop_column("transfer_sessions", "relay_url")
    op.drop_column("transfer_sessions", "code_hash")
    op.drop_column("transfer_sessions", "resume_mode")
    op.drop_column("transfer_preflights", "resume_mode")
    op.alter_column("transfer_sessions", "conflict_mode", server_default=None)
    op.alter_column("transfer_preflights", "conflict_mode", server_default=None)


def downgrade() -> None:
    op.add_column(
        "transfer_preflights",
        sa.Column(
            "resume_mode",
            sa.String(length=32),
            nullable=False,
            server_default="fail_if_exists",
        ),
    )
    op.add_column(
        "transfer_sessions",
        sa.Column(
            "resume_mode",
            sa.String(length=32),
            nullable=False,
            server_default="fail_if_exists",
        ),
    )
    op.add_column("transfer_sessions", sa.Column("code_hash", sa.String(length=64), nullable=True))
    op.add_column("transfer_sessions", sa.Column("relay_url", sa.Text(), nullable=True))
    op.drop_column("transfer_preflights", "conflict_mode")
    op.drop_column("transfer_sessions", "conflict_mode")
