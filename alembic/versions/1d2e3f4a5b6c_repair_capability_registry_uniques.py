"""repair capability registry unique indexes

Revision ID: 1d2e3f4a5b6c
Revises: 0c1d2e3f4a5b
Create Date: 2026-07-04 00:00:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "1d2e3f4a5b6c"
down_revision = "0c1d2e3f4a5b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        """
        DELETE FROM capability_sources source
        USING (
            SELECT id
            FROM (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY node_record_id, plugin_id, registered_name, definition_id
                        ORDER BY registered_at, id
                    ) AS rn
                FROM capability_sources
            ) ranked
            WHERE rn > 1
        ) duplicate
        WHERE source.id = duplicate.id
        """
    )
    op.execute(
        """
        CREATE TEMP TABLE capability_definition_dedup AS
        WITH ranked AS (
            SELECT
                id,
                first_value(id) OVER (
                    PARTITION BY canonical_name, capability_type
                    ORDER BY created_at, id
                ) AS keep_id,
                row_number() OVER (
                    PARTITION BY canonical_name, capability_type
                    ORDER BY created_at, id
                ) AS rn
            FROM capability_definitions
        )
        SELECT id AS duplicate_id, keep_id
        FROM ranked
        WHERE rn > 1
        """
    )
    op.execute(
        """
        DELETE FROM capability_sources duplicate_source
        USING capability_definition_dedup d
        WHERE duplicate_source.definition_id = d.duplicate_id
          AND EXISTS (
              SELECT 1
              FROM capability_sources kept_source
              WHERE kept_source.definition_id = d.keep_id
                AND kept_source.node_record_id = duplicate_source.node_record_id
                AND kept_source.plugin_id = duplicate_source.plugin_id
                AND kept_source.registered_name = duplicate_source.registered_name
          )
        """
    )
    op.execute(
        """
        UPDATE capability_sources source
        SET definition_id = d.keep_id
        FROM capability_definition_dedup d
        WHERE source.definition_id = d.duplicate_id
        """
    )
    op.execute(
        """
        DELETE FROM capability_definitions definition
        USING capability_definition_dedup d
        WHERE definition.id = d.duplicate_id
        """
    )
    op.execute("DROP TABLE capability_definition_dedup")
    op.execute("REINDEX INDEX uq_capability_definitions_canonical_type")
    op.execute("REINDEX INDEX uq_capability_sources_node_plugin_name_definition")


def downgrade() -> None:
    pass
