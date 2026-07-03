"""replace timeline row lock allocator with PostgreSQL sequence

Revision ID: 9b0c1d2e3f4a
Revises: 8a9b0c1d2e3f
Create Date: 2026-07-03 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b0c1d2e3f4a"
down_revision: str | Sequence[str] | None = "8a9b0c1d2e3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name

    if dialect_name == "postgresql":
        op.execute("CREATE SEQUENCE IF NOT EXISTS timeline_global_seq")
        op.execute(
            """
DO $$
DECLARE
    event_value bigint := 0;
    table_value bigint := 0;
    next_value bigint;
BEGIN
    SELECT COALESCE(MAX(global_seq), 0) INTO event_value FROM timeline_events;

    IF to_regclass('timeline_sequences') IS NOT NULL THEN
        EXECUTE 'SELECT COALESCE(MAX(value), 0) FROM timeline_sequences WHERE name = ''global'''
        INTO table_value;
    END IF;

    next_value := GREATEST(event_value, table_value) + 1;
    PERFORM setval('timeline_global_seq', next_value, false);
END $$;
"""
        )
        op.execute(
            "ALTER TABLE timeline_events "
            "ALTER COLUMN global_seq SET DEFAULT nextval('timeline_global_seq')"
        )

    op.execute("DROP TABLE IF EXISTS timeline_sequences")


def downgrade() -> None:
    dialect_name = op.get_bind().dialect.name

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
    if dialect_name == "postgresql":
        op.execute("ALTER TABLE timeline_events ALTER COLUMN global_seq DROP DEFAULT")
        op.execute("DROP SEQUENCE IF EXISTS timeline_global_seq")
