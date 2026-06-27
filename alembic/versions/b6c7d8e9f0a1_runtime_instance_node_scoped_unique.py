"""make runtime IDs node-scoped

Revision ID: b6c7d8e9f0a1
Revises: a4b5c6d7e8f9
Create Date: 2026-06-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "b6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "runtime_instances"
NODE_RUNTIME_UQ = "uq_runtime_instances_node_runtime"


def _table_exists(table: str) -> bool:
    return table in sa.inspect(context.get_context().connection).get_table_names()


def _unique_constraints(table: str) -> set[str]:
    inspector = sa.inspect(context.get_context().connection)
    return {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table)
        if constraint.get("name")
    }


def _runtime_id_unique_constraint_name() -> str | None:
    inspector = sa.inspect(context.get_context().connection)
    for constraint in inspector.get_unique_constraints(TABLE):
        if constraint.get("column_names") == ["runtime_id"]:
            return constraint.get("name")
    return None


def upgrade() -> None:
    if not _table_exists(TABLE):
        return

    dialect = context.get_context().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_constraint("runtime_instances_runtime_id_key", type_="unique")
            batch.create_unique_constraint(
                NODE_RUNTIME_UQ,
                ["node_record_id", "runtime_id"],
            )
        return

    runtime_unique = _runtime_id_unique_constraint_name()
    if runtime_unique:
        op.drop_constraint(runtime_unique, TABLE, type_="unique")

    if NODE_RUNTIME_UQ not in _unique_constraints(TABLE):
        op.create_unique_constraint(
            NODE_RUNTIME_UQ,
            TABLE,
            ["node_record_id", "runtime_id"],
        )


def downgrade() -> None:
    if not _table_exists(TABLE):
        return

    dialect = context.get_context().dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(TABLE) as batch:
            batch.drop_constraint(NODE_RUNTIME_UQ, type_="unique")
            batch.create_unique_constraint(
                "runtime_instances_runtime_id_key",
                ["runtime_id"],
            )
        return

    if NODE_RUNTIME_UQ in _unique_constraints(TABLE):
        op.drop_constraint(NODE_RUNTIME_UQ, TABLE, type_="unique")

    if not _runtime_id_unique_constraint_name():
        op.create_unique_constraint(
            "runtime_instances_runtime_id_key",
            TABLE,
            ["runtime_id"],
        )
