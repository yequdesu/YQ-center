"""add timeline sequence allocator

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-06-24 12:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "timeline_sequences",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.execute(
        "INSERT INTO timeline_sequences (name, value) "
        "SELECT 'global', COALESCE(MAX(global_seq), 0) FROM timeline_events"
    )


def downgrade() -> None:
    op.drop_table("timeline_sequences")
