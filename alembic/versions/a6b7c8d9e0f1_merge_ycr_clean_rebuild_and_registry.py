"""merge ycr clean rebuild and registry repair heads

Revision ID: a6b7c8d9e0f1
Revises: 1d2e3f4a5b6c, fe8f91234567
Create Date: 2026-07-06

"""

from typing import Sequence


# revision identifiers, used by Alembic.
revision: str = "a6b7c8d9e0f1"
down_revision: str | Sequence[str] | None = ("1d2e3f4a5b6c", "fe8f91234567")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge schema branches."""


def downgrade() -> None:
    """Split schema branches."""
