"""rename account username columns to login_info

Revision ID: 1f4b7c9d2a30
Revises: e8c7a3d91f02
Create Date: 2026-05-31 23:15:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "1f4b7c9d2a30"
down_revision: Union[str, Sequence[str], None] = "e8c7a3d91f02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RENAMES = (
    ("tiktok_username", "tiktok_login_info"),
    ("youtube_username", "youtube_login_info"),
    ("instagram_username", "instagram_login_info"),
    ("facebook_username", "facebook_login_info"),
)


def upgrade() -> None:
    for old, new in RENAMES:
        op.alter_column("accounts", old, new_column_name=new)


def downgrade() -> None:
    for old, new in reversed(RENAMES):
        op.alter_column("accounts", new, new_column_name=old)
