"""add ycr context refs

Revision ID: fa4b5c6d7e90
Revises: f3a4b5c6d7e8
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fa4b5c6d7e90"
down_revision: str | None = "9b0c1d2e3f4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ycr_context_refs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("ref_id", sa.String(length=64), nullable=False),
        sa.Column("ref_type", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("session_id", sa.String(length=32), nullable=True),
        sa.Column("projection_policy", sa.String(length=128), nullable=False),
        sa.Column("projection_version", sa.Integer(), nullable=False),
        sa.Column("trust_level", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ref_id"),
    )
    op.create_index("ix_ycr_context_refs_ref_id", "ycr_context_refs", ["ref_id"])
    op.create_index("ix_ycr_context_refs_ref_type", "ycr_context_refs", ["ref_type"])
    op.create_index("ix_ycr_context_refs_source_type", "ycr_context_refs", ["source_type"])
    op.create_index("ix_ycr_context_refs_source_id", "ycr_context_refs", ["source_id"])
    op.create_index("ix_ycr_context_refs_actor_id", "ycr_context_refs", ["actor_id"])
    op.create_index("ix_ycr_context_refs_session_id", "ycr_context_refs", ["session_id"])

    op.create_table(
        "ycr_context_chunks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("chunk_id", sa.String(length=64), nullable=False),
        sa.Column("ref_id", sa.String(length=64), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("trust_level", sa.String(length=64), nullable=False),
        sa.Column("embedding_json", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["ref_id"], ["ycr_context_refs.ref_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id"),
    )
    op.create_index("ix_ycr_context_chunks_chunk_id", "ycr_context_chunks", ["chunk_id"])
    op.create_index("ix_ycr_context_chunks_ref_id", "ycr_context_chunks", ["ref_id"])
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("ALTER TABLE ycr_context_chunks ADD COLUMN embedding_vector vector(256)")
        op.execute(
            "CREATE INDEX ix_ycr_context_chunks_embedding_vector "
            "ON ycr_context_chunks USING ivfflat (embedding_vector vector_cosine_ops)"
        )

    op.create_table(
        "ycr_context_ledgers",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("ledger_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("ref_id", sa.String(length=64), nullable=True),
        sa.Column("raw_size_bytes", sa.Integer(), nullable=True),
        sa.Column("projected_size_bytes", sa.Integer(), nullable=True),
        sa.Column("raw_estimated_tokens", sa.Integer(), nullable=True),
        sa.Column("projected_estimated_tokens", sa.Integer(), nullable=True),
        sa.Column("projection_policy", sa.String(length=128), nullable=True),
        sa.Column("embedding_provider", sa.String(length=64), nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ledger_id"),
    )
    op.create_index("ix_ycr_context_ledgers_ledger_id", "ycr_context_ledgers", ["ledger_id"])
    op.create_index("ix_ycr_context_ledgers_event_type", "ycr_context_ledgers", ["event_type"])
    op.create_index("ix_ycr_context_ledgers_source_type", "ycr_context_ledgers", ["source_type"])
    op.create_index("ix_ycr_context_ledgers_source_id", "ycr_context_ledgers", ["source_id"])
    op.create_index("ix_ycr_context_ledgers_ref_id", "ycr_context_ledgers", ["ref_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_ycr_context_chunks_embedding_vector")
    op.drop_index("ix_ycr_context_ledgers_ref_id", table_name="ycr_context_ledgers")
    op.drop_index("ix_ycr_context_ledgers_source_id", table_name="ycr_context_ledgers")
    op.drop_index("ix_ycr_context_ledgers_source_type", table_name="ycr_context_ledgers")
    op.drop_index("ix_ycr_context_ledgers_event_type", table_name="ycr_context_ledgers")
    op.drop_index("ix_ycr_context_ledgers_ledger_id", table_name="ycr_context_ledgers")
    op.drop_table("ycr_context_ledgers")
    op.drop_index("ix_ycr_context_chunks_ref_id", table_name="ycr_context_chunks")
    op.drop_index("ix_ycr_context_chunks_chunk_id", table_name="ycr_context_chunks")
    op.drop_table("ycr_context_chunks")
    op.drop_index("ix_ycr_context_refs_session_id", table_name="ycr_context_refs")
    op.drop_index("ix_ycr_context_refs_actor_id", table_name="ycr_context_refs")
    op.drop_index("ix_ycr_context_refs_source_id", table_name="ycr_context_refs")
    op.drop_index("ix_ycr_context_refs_source_type", table_name="ycr_context_refs")
    op.drop_index("ix_ycr_context_refs_ref_type", table_name="ycr_context_refs")
    op.drop_index("ix_ycr_context_refs_ref_id", table_name="ycr_context_refs")
    op.drop_table("ycr_context_refs")
