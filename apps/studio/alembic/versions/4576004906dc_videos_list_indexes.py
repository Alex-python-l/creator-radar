"""videos list indexes

Revision ID: 4576004906dc
Revises: d5f1a8c93e26
Create Date: 2026-06-03 14:55:07.382226

"""
from typing import Sequence, Union

from alembic import op


revision: str = "4576004906dc"
down_revision: Union[str, Sequence[str], None] = "d5f1a8c93e26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL does not auto-index foreign keys (unlike MySQL), so
    # publications.video_id and videos.group_id need explicit indexes.
    # Cursor-paginated list reads filter on group_id and join on video_id.
    op.create_index(
        "ix_videos_group_id", "videos", ["group_id"], if_not_exists=True
    )
    op.create_index(
        "ix_publications_video_id", "publications", ["video_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_publications_video_id", table_name="publications",
                  if_exists=True)
    op.drop_index("ix_videos_group_id", table_name="videos", if_exists=True)
