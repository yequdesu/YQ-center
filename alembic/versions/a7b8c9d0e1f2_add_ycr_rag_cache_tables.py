"""add ycr rag cache tables

Revision ID: a7b8c9d0e1f2
Revises: a6b7c8d9e0f1
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "a6b7c8d9e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "ycr_query_embedding_cache" not in tables:
        op.create_table(
            "ycr_query_embedding_cache",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("cache_key", sa.String(length=64), nullable=False),
            sa.Column("normalized_query", sa.Text(), nullable=False),
            sa.Column("query_hash", sa.String(length=64), nullable=False),
            sa.Column("normalize_version", sa.Integer(), nullable=False),
            sa.Column("embedding_provider", sa.String(length=64), nullable=False),
            sa.Column("embedding_model", sa.String(length=128), nullable=False),
            sa.Column("embedding_version", sa.Integer(), nullable=False),
            sa.Column("dense_json", sa.JSON(), nullable=False),
            sa.Column("sparse_json", sa.JSON(), nullable=False),
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
            "ix_ycr_query_embedding_cache_cache_key",
            "ycr_query_embedding_cache",
            ["cache_key"],
        )
        op.create_index(
            "ix_ycr_query_embedding_cache_query_hash",
            "ycr_query_embedding_cache",
            ["query_hash"],
        )
        op.create_index(
            "ix_ycr_query_embedding_cache_embedding_provider",
            "ycr_query_embedding_cache",
            ["embedding_provider"],
        )
        op.create_index(
            "ix_ycr_query_embedding_cache_embedding_model",
            "ycr_query_embedding_cache",
            ["embedding_model"],
        )
        op.alter_column("ycr_query_embedding_cache", "hit_count", server_default=None)

    if "ycr_rerank_cache" not in tables:
        op.create_table(
            "ycr_rerank_cache",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("cache_key", sa.String(length=64), nullable=False),
            sa.Column("normalized_query_hash", sa.String(length=64), nullable=False),
            sa.Column("rerank_model", sa.String(length=128), nullable=False),
            sa.Column("rerank_version", sa.Integer(), nullable=False),
            sa.Column("document_hashes_hash", sa.String(length=64), nullable=False),
            sa.Column("top_n", sa.Integer(), nullable=False),
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
        op.create_index("ix_ycr_rerank_cache_cache_key", "ycr_rerank_cache", ["cache_key"])
        op.create_index(
            "ix_ycr_rerank_cache_normalized_query_hash",
            "ycr_rerank_cache",
            ["normalized_query_hash"],
        )
        op.create_index(
            "ix_ycr_rerank_cache_rerank_model",
            "ycr_rerank_cache",
            ["rerank_model"],
        )
        op.create_index(
            "ix_ycr_rerank_cache_document_hashes_hash",
            "ycr_rerank_cache",
            ["document_hashes_hash"],
        )
        op.alter_column("ycr_rerank_cache", "hit_count", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_ycr_rerank_cache_document_hashes_hash", table_name="ycr_rerank_cache")
    op.drop_index("ix_ycr_rerank_cache_rerank_model", table_name="ycr_rerank_cache")
    op.drop_index("ix_ycr_rerank_cache_normalized_query_hash", table_name="ycr_rerank_cache")
    op.drop_index("ix_ycr_rerank_cache_cache_key", table_name="ycr_rerank_cache")
    op.drop_table("ycr_rerank_cache")

    op.drop_index(
        "ix_ycr_query_embedding_cache_embedding_model",
        table_name="ycr_query_embedding_cache",
    )
    op.drop_index(
        "ix_ycr_query_embedding_cache_embedding_provider",
        table_name="ycr_query_embedding_cache",
    )
    op.drop_index("ix_ycr_query_embedding_cache_query_hash", table_name="ycr_query_embedding_cache")
    op.drop_index("ix_ycr_query_embedding_cache_cache_key", table_name="ycr_query_embedding_cache")
    op.drop_table("ycr_query_embedding_cache")
