"""add_session_label_updated_at

Revision ID: c5e1d2f3a4b6
Revises: 5437858701b2
Create Date: 2026-06-22 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5e1d2f3a4b6'
down_revision: Union[str, Sequence[str], None] = '5437858701b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add label and updated_at columns to sessions table (idempotent)."""
    from alembic import context
    from sqlalchemy import inspect

    conn = context.get_context().connection
    inspector = inspect(conn)
    existing_columns = {c['name'] for c in inspector.get_columns('sessions')}

    if 'label' not in existing_columns:
        op.add_column('sessions', sa.Column('label', sa.String(length=128), nullable=True))
        op.execute(
            "UPDATE sessions SET label = substr(session_id, 1, 8) WHERE label IS NULL"
        )

    if 'updated_at' not in existing_columns:
        op.add_column('sessions', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        op.execute(
            "UPDATE sessions SET updated_at = started_at WHERE updated_at IS NULL"
        )


def downgrade() -> None:
    """Remove label and updated_at columns from sessions table."""
    from alembic import context
    from sqlalchemy import inspect

    conn = context.get_context().connection
    inspector = inspect(conn)
    existing_columns = {c['name'] for c in inspector.get_columns('sessions')}

    if 'updated_at' in existing_columns:
        op.drop_column('sessions', 'updated_at')
    if 'label' in existing_columns:
        op.drop_column('sessions', 'label')
