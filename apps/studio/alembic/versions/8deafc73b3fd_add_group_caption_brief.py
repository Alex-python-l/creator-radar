"""add group.caption_brief

Revision ID: 8deafc73b3fd
Revises: 69748d477a7f
Create Date: 2026-05-29 11:03:16.138450

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = '8deafc73b3fd'
down_revision: Union[str, Sequence[str], None] = '69748d477a7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "groups",
        sa.Column("caption_brief",
                  sqlmodel.sql.sqltypes.AutoString(),
                  nullable=True),
    )


def downgrade() -> None:
    op.drop_column("groups", "caption_brief")
