"""drop groups.target_phone_count

Revision ID: e8c7a3d91f02
Revises: c6d4e9a2b1f0
Create Date: 2026-05-31 22:00:00.000000

The "target phone count" concept (and the draft-group UI built on top of it)
was removed from the product — a group is just a group, no more "you set a
target of 5 phones, only 3 logged in, so we'll badge you as draft."

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8c7a3d91f02'
down_revision: Union[str, Sequence[str], None] = 'c6d4e9a2b1f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("groups", "target_phone_count")


def downgrade() -> None:
    op.add_column(
        "groups",
        sa.Column("target_phone_count", sa.Integer(), nullable=True),
    )
