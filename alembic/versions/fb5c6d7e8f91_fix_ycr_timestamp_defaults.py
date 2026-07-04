"""fix ycr timestamp defaults

Revision ID: fb5c6d7e8f91
Revises: fa4b5c6d7e90
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fb5c6d7e8f91"
down_revision: str | None = "fa4b5c6d7e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table_name in ("ycr_context_refs", "ycr_context_chunks", "ycr_context_ledgers"):
        op.alter_column(table_name, "created_at", server_default=sa.text("now()"))
        op.alter_column(table_name, "updated_at", server_default=sa.text("now()"))


def downgrade() -> None:
    for table_name in ("ycr_context_refs", "ycr_context_chunks", "ycr_context_ledgers"):
        op.alter_column(table_name, "created_at", server_default=None)
        op.alter_column(table_name, "updated_at", server_default=None)
