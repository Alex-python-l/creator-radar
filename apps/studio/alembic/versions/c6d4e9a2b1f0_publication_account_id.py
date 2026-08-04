"""restore per-publication account assignment

Revision ID: c6d4e9a2b1f0
Revises: a8b2d34f7e91
Create Date: 2026-05-30 16:30:00.000000

Each platform publication needs its own GeeLark phone assignment. A
TikTok publication can use a TikTok-logged-in phone while the sibling
YouTube publication for the same video uses a different YouTube phone.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d4e9a2b1f0"
down_revision: Union[str, Sequence[str], None] = "a8b2d34f7e91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "publications",
        sa.Column("account_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_publications_account_id",
        "publications", "accounts",
        ["account_id"], ["id"],
        ondelete="SET NULL",
    )
    op.execute("""
        UPDATE publications p
           SET account_id = v.account_id
          FROM videos v
         WHERE p.video_id = v.id
           AND v.account_id IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_constraint("fk_publications_account_id",
                       "publications", type_="foreignkey")
    op.drop_column("publications", "account_id")
