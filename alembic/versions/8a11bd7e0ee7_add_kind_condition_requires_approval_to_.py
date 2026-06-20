"""add_kind_condition_requires_approval_to_step

Revision ID: 8a11bd7e0ee7
Revises: 426a8602aa7e
Create Date: 2026-06-21 01:42:12.373415

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a11bd7e0ee7'
down_revision: Union[str, Sequence[str], None] = '426a8602aa7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add columns as nullable first so existing rows don't fail
    op.add_column('maintenance_steps', sa.Column('kind', sa.String(length=16), nullable=True))
    op.add_column('maintenance_steps', sa.Column('condition', sa.String(length=32), nullable=True))
    op.add_column('maintenance_steps', sa.Column('requires_approval', sa.Boolean(), nullable=True))
    op.add_column('maintenance_steps', sa.Column('skip_reason', sa.Text(), nullable=True))
    op.add_column('maintenance_steps', sa.Column('risk', sa.String(length=16), nullable=True))

    # Set defaults for existing rows
    op.execute("UPDATE maintenance_steps SET kind = 'check' WHERE kind IS NULL")
    op.execute("UPDATE maintenance_steps SET condition = 'always' WHERE condition IS NULL")
    op.execute("UPDATE maintenance_steps SET requires_approval = FALSE WHERE requires_approval IS NULL")
    op.execute("UPDATE maintenance_steps SET risk = 'safe' WHERE risk IS NULL")

    # Now make them NOT NULL
    op.alter_column('maintenance_steps', 'kind', nullable=False)
    op.alter_column('maintenance_steps', 'condition', nullable=False)
    op.alter_column('maintenance_steps', 'requires_approval', nullable=False)
    op.alter_column('maintenance_steps', 'risk', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('maintenance_steps', 'risk')
    op.drop_column('maintenance_steps', 'skip_reason')
    op.drop_column('maintenance_steps', 'requires_approval')
    op.drop_column('maintenance_steps', 'condition')
    op.drop_column('maintenance_steps', 'kind')
