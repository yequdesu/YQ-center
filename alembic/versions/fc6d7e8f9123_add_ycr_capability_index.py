"""add ycr capability index

Revision ID: fc6d7e8f9123
Revises: fb5c6d7e8f91
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fc6d7e8f9123"
down_revision: str | None = "fb5c6d7e8f91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ycr_capability_index",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("index_id", sa.String(length=64), nullable=False),
        sa.Column("capability_id", sa.String(length=64), nullable=False),
        sa.Column("canonical_name", sa.String(length=256), nullable=False),
        sa.Column("capability_type", sa.String(length=16), nullable=False),
        sa.Column("document_hash", sa.String(length=64), nullable=False),
        sa.Column("document_json", sa.JSON(), nullable=False),
        sa.Column("index_text", sa.Text(), nullable=False),
        sa.Column("embedding_json", sa.JSON(), nullable=True),
        sa.Column("embedding_provider", sa.String(length=64), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("sparse_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("index_id"),
    )
    op.create_index("ix_ycr_capability_index_index_id", "ycr_capability_index", ["index_id"])
    op.create_index("ix_ycr_capability_index_capability_id", "ycr_capability_index", ["capability_id"])
    op.create_index("ix_ycr_capability_index_canonical_name", "ycr_capability_index", ["canonical_name"])
    op.create_index("ix_ycr_capability_index_capability_type", "ycr_capability_index", ["capability_type"])
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("DROP INDEX IF EXISTS ix_ycr_context_chunks_embedding_vector")
        op.execute("ALTER TABLE ycr_context_chunks DROP COLUMN IF EXISTS embedding_vector")
        op.execute("ALTER TABLE ycr_context_chunks ADD COLUMN embedding_vector vector(1024)")
        op.execute(
            "CREATE INDEX ix_ycr_context_chunks_embedding_vector "
            "ON ycr_context_chunks USING ivfflat (embedding_vector vector_cosine_ops)"
        )
        op.execute("ALTER TABLE ycr_capability_index ADD COLUMN embedding_vector vector(1024)")
        op.execute(
            "CREATE INDEX ix_ycr_capability_index_embedding_vector "
            "ON ycr_capability_index USING ivfflat (embedding_vector vector_cosine_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_ycr_capability_index_embedding_vector")
        op.execute("DROP INDEX IF EXISTS ix_ycr_context_chunks_embedding_vector")
        op.execute("ALTER TABLE ycr_context_chunks DROP COLUMN IF EXISTS embedding_vector")
        op.execute("ALTER TABLE ycr_context_chunks ADD COLUMN embedding_vector vector(256)")
        op.execute(
            "CREATE INDEX ix_ycr_context_chunks_embedding_vector "
            "ON ycr_context_chunks USING ivfflat (embedding_vector vector_cosine_ops)"
        )
    op.drop_index("ix_ycr_capability_index_capability_type", table_name="ycr_capability_index")
    op.drop_index("ix_ycr_capability_index_canonical_name", table_name="ycr_capability_index")
    op.drop_index("ix_ycr_capability_index_capability_id", table_name="ycr_capability_index")
    op.drop_index("ix_ycr_capability_index_index_id", table_name="ycr_capability_index")
    op.drop_table("ycr_capability_index")
