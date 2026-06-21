"""l2c_maintenance_artifact_rollback

Revision ID: ba40e5beb506
Revises: a7b5f8f32d89
Create Date: 2026-06-21 03:12:19.036105

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = 'ba40e5beb506'
down_revision: Union[str, Sequence[str], None] = 'a7b5f8f32d89'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # -- maintenance_artifacts: add new columns (nullable first, backfill, then constrain) --
    op.add_column('maintenance_artifacts', sa.Column('invocation_id', sa.String(length=32), nullable=True))
    op.add_column('maintenance_artifacts', sa.Column('job_id', sa.String(length=32), nullable=True))
    op.add_column('maintenance_artifacts', sa.Column('kind', sa.String(length=32), nullable=True))
    op.add_column('maintenance_artifacts', sa.Column('data', sqlite.JSON(), nullable=True))
    op.add_column('maintenance_artifacts', sa.Column('summary', sqlite.JSON(), nullable=True))

    # Backfill: set kind from old artifact_type, set content_type default
    op.execute("UPDATE maintenance_artifacts SET kind = artifact_type WHERE kind IS NULL AND artifact_type IS NOT NULL")
    op.execute("UPDATE maintenance_artifacts SET kind = 'check_result' WHERE kind IS NULL")
    op.execute("UPDATE maintenance_artifacts SET content_type = 'application/json' WHERE content_type IS NULL")

    # Now make NOT NULL
    op.alter_column('maintenance_artifacts', 'kind', existing_type=sa.String(length=32), nullable=False)
    op.alter_column('maintenance_artifacts', 'content_type',
                    existing_type=sa.VARCHAR(length=64),
                    nullable=False)

    # Drop old columns
    op.drop_column('maintenance_artifacts', 'artifact_type')
    op.drop_column('maintenance_artifacts', 'sha256')
    op.drop_column('maintenance_artifacts', 'storage_ref')
    op.drop_column('maintenance_artifacts', 'size_bytes')

    # Create indexes
    op.create_index(op.f('ix_maintenance_artifacts_created_at'), 'maintenance_artifacts', ['created_at'], unique=False)
    op.create_index(op.f('ix_maintenance_artifacts_step_id'), 'maintenance_artifacts', ['step_id'], unique=False)

    # -- maintenance_runs: add rollback_recommended with server default --
    op.add_column('maintenance_runs', sa.Column('rollback_recommended', sa.Boolean(), nullable=False, server_default=sa.text('false')))

    # -- maintenance_steps: add rollback_hint (nullable) --
    op.add_column('maintenance_steps', sa.Column('rollback_hint', sqlite.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('maintenance_steps', 'rollback_hint')
    op.drop_column('maintenance_runs', 'rollback_recommended')

    op.add_column('maintenance_artifacts', sa.Column('size_bytes', sa.INTEGER(), autoincrement=False, nullable=True))
    op.add_column('maintenance_artifacts', sa.Column('storage_ref', sa.TEXT(), autoincrement=False, nullable=True))
    op.add_column('maintenance_artifacts', sa.Column('sha256', sa.VARCHAR(length=64), autoincrement=False, nullable=True))
    op.add_column('maintenance_artifacts', sa.Column('artifact_type', sa.VARCHAR(length=32), autoincrement=False, nullable=True))

    # Restore old data
    op.execute("UPDATE maintenance_artifacts SET artifact_type = kind WHERE artifact_type IS NULL")

    op.drop_index(op.f('ix_maintenance_artifacts_step_id'), table_name='maintenance_artifacts')
    op.drop_index(op.f('ix_maintenance_artifacts_created_at'), table_name='maintenance_artifacts')
    op.alter_column('maintenance_artifacts', 'content_type',
                    existing_type=sa.VARCHAR(length=64),
                    nullable=True)
    op.drop_column('maintenance_artifacts', 'summary')
    op.drop_column('maintenance_artifacts', 'data')
    op.drop_column('maintenance_artifacts', 'kind')
    op.drop_column('maintenance_artifacts', 'job_id')
    op.drop_column('maintenance_artifacts', 'invocation_id')
