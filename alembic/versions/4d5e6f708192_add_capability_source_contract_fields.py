"""add_capability_source_contract_fields

Revision ID: 4d5e6f708192
Revises: 3c4d5e6f7081
Create Date: 2026-06-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "4d5e6f708192"
down_revision: Union[str, Sequence[str], None] = "3c4d5e6f7081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    from alembic import context
    from sqlalchemy import inspect

    conn = context.get_context().connection
    return column in {item["name"] for item in inspect(conn).get_columns(table)}


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    if not _has_column(table, column.name):
        op.add_column(table, column)


def upgrade() -> None:
    _add_column_if_missing(
        "capability_sources",
        sa.Column(
            "supports_progress",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    _add_column_if_missing(
        "capability_sources",
        sa.Column(
            "supports_cancel",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    _add_column_if_missing(
        "capability_sources",
        sa.Column(
            "supports_resume",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    _add_column_if_missing(
        "capability_sources",
        sa.Column("progress_contract", sa.String(length=64), nullable=True),
    )
    _add_column_if_missing(
        "capability_sources",
        sa.Column("preconditions", sa.JSON(), nullable=True),
    )
    _add_column_if_missing(
        "capability_sources",
        sa.Column("required_intent_slots", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    for column_name in (
        "required_intent_slots",
        "preconditions",
        "progress_contract",
        "supports_resume",
        "supports_cancel",
        "supports_progress",
    ):
        if _has_column("capability_sources", column_name):
            op.drop_column("capability_sources", column_name)
