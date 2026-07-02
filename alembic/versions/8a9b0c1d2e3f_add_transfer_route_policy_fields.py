"""add_transfer_route_policy_fields

Revision ID: 8a9b0c1d2e3f
Revises: 8192a3b4c5d6
Create Date: 2026-07-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8a9b0c1d2e3f"
down_revision: Union[str, Sequence[str], None] = "8192a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    _add_column_if_missing(
        "transfer_sessions",
        sa.Column(
            "route_policy",
            sa.String(length=32),
            nullable=False,
            server_default="auto",
        ),
    )
    _add_column_if_missing("transfer_sessions", sa.Column("direct_ip", sa.Text(), nullable=True))
    _add_column_if_missing(
        "transfer_sessions", sa.Column("multicast_address", sa.Text(), nullable=True)
    )
    _add_column_if_missing(
        "transfer_attempts",
        sa.Column("route_policy", sa.String(length=32), nullable=True),
    )
    _add_column_if_missing(
        "transfer_preflights",
        sa.Column(
            "resume_mode",
            sa.String(length=32),
            nullable=False,
            server_default="resume",
        ),
    )
    _add_column_if_missing(
        "transfer_preflights",
        sa.Column(
            "route_policy",
            sa.String(length=32),
            nullable=False,
            server_default="auto",
        ),
    )
    _add_column_if_missing("transfer_preflights", sa.Column("direct_ip", sa.Text(), nullable=True))
    _add_column_if_missing(
        "transfer_preflights", sa.Column("multicast_address", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("transfer_preflights", "multicast_address")
    op.drop_column("transfer_preflights", "direct_ip")
    op.drop_column("transfer_preflights", "route_policy")
    op.drop_column("transfer_attempts", "route_policy")
    op.drop_column("transfer_sessions", "multicast_address")
    op.drop_column("transfer_sessions", "direct_ip")
    op.drop_column("transfer_sessions", "route_policy")


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    existing = {item["name"] for item in sa.inspect(bind).get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)
