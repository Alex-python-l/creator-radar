"""merge_main_and_phone_lease

Revision ID: a87a3b66544a
Revises: 5c592229e6e1, d6e4ca33b3f9
Create Date: 2026-06-09 12:12:32.732952

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a87a3b66544a'
down_revision: Union[str, Sequence[str], None] = ('5c592229e6e1', 'd6e4ca33b3f9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
