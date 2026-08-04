"""drop groups.archived_at

Revision ID: d5f1a8c93e26
Revises: 1f4b7c9d2a30
Create Date: 2026-05-31 23:45:00.000000

The group-archive feature was removed from the product — there's no UI
that distinguishes archived from active groups, no endpoint that flips
the bit. The column has been dead since that cleanup.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5f1a8c93e26"
down_revision: Union[str, Sequence[str], None] = "1f4b7c9d2a30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("groups", "archived_at")


def downgrade() -> None:
    op.add_column(
        "groups",
        sa.Column("archived_at", sa.DateTime(), nullable=True),
    )
