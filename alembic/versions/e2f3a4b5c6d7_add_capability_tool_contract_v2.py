"""add_capability_tool_contract_v2

Revision ID: e2f3a4b5c6d7
Revises: d7e8f9a0b1c2
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    from alembic import context
    from sqlalchemy import inspect

    conn = context.get_context().connection
    columns = {c["name"] for c in inspect(conn).get_columns(table)}
    if column.name not in columns:
        op.add_column(table, column)


def _drop_column_if_present(table: str, column_name: str) -> None:
    from alembic import context
    from sqlalchemy import inspect

    conn = context.get_context().connection
    columns = {c["name"] for c in inspect(conn).get_columns(table)}
    if column_name in columns:
        op.drop_column(table, column_name)


def upgrade() -> None:
    _add_column_if_missing("capabilities", sa.Column("description", sa.Text(), nullable=True))
    _add_column_if_missing("capabilities", sa.Column("agent_description", sa.Text(), nullable=True))
    _add_column_if_missing("capabilities", sa.Column("user_visible_name", sa.String(length=256), nullable=True))
    _add_column_if_missing("capabilities", sa.Column("execution_context", sa.String(length=32), nullable=True))
    _add_column_if_missing("capabilities", sa.Column("hidden_input_fields", sa.JSON(), nullable=True))
    _add_column_if_missing("capabilities", sa.Column("examples", sa.JSON(), nullable=True))
    _add_column_if_missing("capabilities", sa.Column("failure_modes", sa.JSON(), nullable=True))
    _add_column_if_missing(
        "capabilities",
        sa.Column("preflight_supported", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    _drop_column_if_present("capabilities", "preflight_supported")
    _drop_column_if_present("capabilities", "failure_modes")
    _drop_column_if_present("capabilities", "examples")
    _drop_column_if_present("capabilities", "hidden_input_fields")
    _drop_column_if_present("capabilities", "execution_context")
    _drop_column_if_present("capabilities", "user_visible_name")
    _drop_column_if_present("capabilities", "agent_description")
    _drop_column_if_present("capabilities", "description")
