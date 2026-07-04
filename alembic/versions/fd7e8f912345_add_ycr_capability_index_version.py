"""add ycr capability index version

Revision ID: fd7e8f912345
Revises: fc6d7e8f9123
Create Date: 2026-07-05 12:05:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "fd7e8f912345"
down_revision = "fc6d7e8f9123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ycr_capability_index",
        sa.Column("index_version", sa.Integer(), nullable=False, server_default="2"),
    )
    op.alter_column("ycr_capability_index", "index_version", server_default=None)


def downgrade() -> None:
    op.drop_column("ycr_capability_index", "index_version")
