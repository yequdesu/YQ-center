"""add unified capability fields

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("capability_definitions")}
    additions = [
        ("scope", sa.Column("scope", sa.String(length=16), nullable=False, server_default="node")),
        (
            "plane",
            sa.Column("plane", sa.String(length=32), nullable=False, server_default="node_runtime"),
        ),
        ("provider", sa.Column("provider", sa.String(length=64), nullable=True)),
        (
            "dispatch_kind",
            sa.Column(
                "dispatch_kind",
                sa.String(length=32),
                nullable=False,
                server_default="node_job",
            ),
        ),
        (
            "agent_visible",
            sa.Column("agent_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
        ),
        (
            "invocation_surface",
            sa.Column(
                "invocation_surface",
                sa.String(length=32),
                nullable=False,
                server_default="agent",
            ),
        ),
        ("workflow_kind", sa.Column("workflow_kind", sa.String(length=64), nullable=True)),
        ("artifact_contract", sa.Column("artifact_contract", sa.JSON(), nullable=True)),
        ("operation_contract", sa.Column("operation_contract", sa.JSON(), nullable=True)),
    ]
    for name, column in additions:
        if name not in columns:
            op.add_column("capability_definitions", column)

    for name in ("scope", "plane", "dispatch_kind", "agent_visible", "invocation_surface"):
        if name in columns:
            continue
        op.alter_column("capability_definitions", name, server_default=None)


def downgrade() -> None:
    for name in reversed(
        [
            "scope",
            "plane",
            "provider",
            "dispatch_kind",
            "agent_visible",
            "invocation_surface",
            "workflow_kind",
            "artifact_contract",
            "operation_contract",
        ]
    ):
        op.drop_column("capability_definitions", name)
