"""add_approval_resource_lock_to_job

Revision ID: 557e73d411cf
Revises: 7c708b5743e4
Create Date: 2026-06-20 13:06:52.303174

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '557e73d411cf'
down_revision: Union[str, Sequence[str], None] = '7c708b5743e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('jobs', sa.Column('approval_id', sa.String(length=32), nullable=True))
    # Add with server_default first, then alter to NOT NULL
    op.add_column('jobs', sa.Column('dry_run', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.alter_column('jobs', 'dry_run', server_default=None)
    op.add_column('jobs', sa.Column('resource_keys', postgresql.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('jobs', 'resource_keys')
    op.drop_column('jobs', 'dry_run')
    op.drop_column('jobs', 'approval_id')
