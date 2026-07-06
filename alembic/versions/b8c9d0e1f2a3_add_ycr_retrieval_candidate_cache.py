"""add ycr retrieval candidate cache

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "ycr_retrieval_candidate_cache" in tables:
        return

    op.create_table(
        "ycr_retrieval_candidate_cache",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("normalized_query_hash", sa.String(length=64), nullable=False),
        sa.Column("query_embedding_hash", sa.String(length=64), nullable=False),
        sa.Column("corpus_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("filters_hash", sa.String(length=64), nullable=False),
        sa.Column("retrieval_version", sa.Integer(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cache_key"),
    )
    op.create_index(
        "ix_ycr_retrieval_candidate_cache_cache_key",
        "ycr_retrieval_candidate_cache",
        ["cache_key"],
    )
    op.create_index(
        "ix_ycr_retrieval_candidate_cache_normalized_query_hash",
        "ycr_retrieval_candidate_cache",
        ["normalized_query_hash"],
    )
    op.create_index(
        "ix_ycr_retrieval_candidate_cache_query_embedding_hash",
        "ycr_retrieval_candidate_cache",
        ["query_embedding_hash"],
    )
    op.create_index(
        "ix_ycr_retrieval_candidate_cache_corpus_fingerprint",
        "ycr_retrieval_candidate_cache",
        ["corpus_fingerprint"],
    )
    op.create_index(
        "ix_ycr_retrieval_candidate_cache_filters_hash",
        "ycr_retrieval_candidate_cache",
        ["filters_hash"],
    )
    op.alter_column("ycr_retrieval_candidate_cache", "hit_count", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_ycr_retrieval_candidate_cache_filters_hash",
        table_name="ycr_retrieval_candidate_cache",
    )
    op.drop_index(
        "ix_ycr_retrieval_candidate_cache_corpus_fingerprint",
        table_name="ycr_retrieval_candidate_cache",
    )
    op.drop_index(
        "ix_ycr_retrieval_candidate_cache_query_embedding_hash",
        table_name="ycr_retrieval_candidate_cache",
    )
    op.drop_index(
        "ix_ycr_retrieval_candidate_cache_normalized_query_hash",
        table_name="ycr_retrieval_candidate_cache",
    )
    op.drop_index(
        "ix_ycr_retrieval_candidate_cache_cache_key",
        table_name="ycr_retrieval_candidate_cache",
    )
    op.drop_table("ycr_retrieval_candidate_cache")
