"""ycr clean rebuild schema

Revision ID: fe8f91234567
Revises: fd7e8f912345
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fe8f91234567"
down_revision: str | None = "fd7e8f912345"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    ref_columns = {column["name"] for column in inspector.get_columns("ycr_context_refs")}
    if "expires_at" in ref_columns:
        op.drop_column("ycr_context_refs", "expires_at")

    ledger_columns = {column["name"] for column in inspector.get_columns("ycr_context_ledgers")}
    if "session_id" not in ledger_columns:
        op.add_column(
            "ycr_context_ledgers",
            sa.Column("session_id", sa.String(length=32), nullable=True),
        )
        op.create_index(
            "ix_ycr_context_ledgers_session_id",
            "ycr_context_ledgers",
            ["session_id"],
        )

    if "ycr_capability_index_jobs" not in inspector.get_table_names():
        op.create_table(
            "ycr_capability_index_jobs",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("job_id", sa.String(length=64), nullable=False),
            sa.Column("index_id", sa.String(length=64), nullable=False),
            sa.Column("capability_id", sa.String(length=64), nullable=False),
            sa.Column("canonical_name", sa.String(length=256), nullable=False),
            sa.Column("capability_type", sa.String(length=16), nullable=False),
            sa.Column("document_hash", sa.String(length=64), nullable=False),
            sa.Column("document_json", sa.JSON(), nullable=False),
            sa.Column("index_text", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error_code", sa.String(length=64), nullable=True),
            sa.Column("last_error_message", sa.Text(), nullable=True),
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
            sa.UniqueConstraint("job_id"),
        )
        op.create_index(
            "ix_ycr_capability_index_jobs_job_id",
            "ycr_capability_index_jobs",
            ["job_id"],
        )
        op.create_index(
            "ix_ycr_capability_index_jobs_index_id",
            "ycr_capability_index_jobs",
            ["index_id"],
        )
        op.create_index(
            "ix_ycr_capability_index_jobs_capability_id",
            "ycr_capability_index_jobs",
            ["capability_id"],
        )
        op.create_index(
            "ix_ycr_capability_index_jobs_canonical_name",
            "ycr_capability_index_jobs",
            ["canonical_name"],
        )
        op.create_index(
            "ix_ycr_capability_index_jobs_capability_type",
            "ycr_capability_index_jobs",
            ["capability_type"],
        )
        op.create_index(
            "ix_ycr_capability_index_jobs_document_hash",
            "ycr_capability_index_jobs",
            ["document_hash"],
        )
        op.create_index(
            "ix_ycr_capability_index_jobs_status",
            "ycr_capability_index_jobs",
            ["status"],
        )
        op.alter_column("ycr_capability_index_jobs", "status", server_default=None)
        op.alter_column("ycr_capability_index_jobs", "attempt", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_ycr_capability_index_jobs_status", table_name="ycr_capability_index_jobs")
    op.drop_index(
        "ix_ycr_capability_index_jobs_document_hash",
        table_name="ycr_capability_index_jobs",
    )
    op.drop_index(
        "ix_ycr_capability_index_jobs_capability_type",
        table_name="ycr_capability_index_jobs",
    )
    op.drop_index(
        "ix_ycr_capability_index_jobs_canonical_name",
        table_name="ycr_capability_index_jobs",
    )
    op.drop_index(
        "ix_ycr_capability_index_jobs_capability_id",
        table_name="ycr_capability_index_jobs",
    )
    op.drop_index("ix_ycr_capability_index_jobs_index_id", table_name="ycr_capability_index_jobs")
    op.drop_index("ix_ycr_capability_index_jobs_job_id", table_name="ycr_capability_index_jobs")
    op.drop_table("ycr_capability_index_jobs")

    op.drop_index("ix_ycr_context_ledgers_session_id", table_name="ycr_context_ledgers")
    op.drop_column("ycr_context_ledgers", "session_id")
    op.add_column(
        "ycr_context_refs",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
