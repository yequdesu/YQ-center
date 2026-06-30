"""add_job_progress_detail

Revision ID: 6f708192a3b4
Revises: 5e6f708192a3
Create Date: 2026-06-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "6f708192a3b4"
down_revision: Union[str, Sequence[str], None] = "5e6f708192a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    from alembic import context
    from sqlalchemy import inspect

    conn = context.get_context().connection
    return column in {item["name"] for item in inspect(conn).get_columns(table)}


def upgrade() -> None:
    if not _has_column("jobs", "progress_detail"):
        op.add_column("jobs", sa.Column("progress_detail", sa.JSON(), nullable=True))


def downgrade() -> None:
    if _has_column("jobs", "progress_detail"):
        op.drop_column("jobs", "progress_detail")
