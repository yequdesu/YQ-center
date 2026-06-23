"""add_runtime_instances

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    from alembic import context
    from sqlalchemy import inspect

    conn = context.get_context().connection
    return table in inspect(conn).get_table_names()


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
    if not _table_exists("runtime_instances"):
        op.create_table(
            "runtime_instances",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("node_record_id", sa.String(length=32), nullable=False),
            sa.Column("runtime_id", sa.String(length=256), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="privileged"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="online"),
            sa.Column("labels", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("owner", sa.String(length=256), nullable=True),
            sa.Column("privilege", sa.String(length=64), nullable=True),
            sa.Column("interactive", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["node_record_id"], ["nodes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("runtime_id"),
        )
        op.create_index("ix_runtime_instances_node_record_id", "runtime_instances", ["node_record_id"])
        op.create_index("ix_runtime_instances_runtime_id", "runtime_instances", ["runtime_id"])

    _add_column_if_missing("capabilities", sa.Column("execution_requirements", sa.JSON(), nullable=True))
    _add_column_if_missing("jobs", sa.Column("runtime_id", sa.String(length=256), nullable=True))
    _add_column_if_missing("jobs", sa.Column("execution_requirements_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    _drop_column_if_present("jobs", "execution_requirements_snapshot")
    _drop_column_if_present("jobs", "runtime_id")
    _drop_column_if_present("capabilities", "execution_requirements")
    if _table_exists("runtime_instances"):
        op.drop_table("runtime_instances")
