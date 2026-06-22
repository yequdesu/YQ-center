"""add_error_details_to_job_invocation

Revision ID: d7e8f9a0b1c2
Revises: c5e1d2f3a4b6
Create Date: 2026-06-22 17:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'c5e1d2f3a4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add error_details JSON columns to jobs and invocations."""
    from alembic import context
    from sqlalchemy import inspect

    conn = context.get_context().connection
    inspector = inspect(conn)

    job_cols = {c['name'] for c in inspector.get_columns('jobs')}
    if 'error_details' not in job_cols:
        op.add_column('jobs', sa.Column('error_details', sa.dialects.postgresql.JSON, nullable=True))

    inv_cols = {c['name'] for c in inspector.get_columns('invocations')}
    if 'error_details' not in inv_cols:
        op.add_column('invocations', sa.Column('error_details', sa.dialects.postgresql.JSON, nullable=True))


def downgrade() -> None:
    """Remove error_details columns."""
    from alembic import context
    from sqlalchemy import inspect

    conn = context.get_context().connection
    inspector = inspect(conn)

    job_cols = {c['name'] for c in inspector.get_columns('jobs')}
    if 'error_details' in job_cols:
        op.drop_column('jobs', 'error_details')

    inv_cols = {c['name'] for c in inspector.get_columns('invocations')}
    if 'error_details' in inv_cols:
        op.drop_column('invocations', 'error_details')
