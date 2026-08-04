"""add discovered_videos table

Revision ID: 5c592229e6e1
Revises: 9762066ca2ef
Create Date: 2026-06-09 12:02:18.636126

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c592229e6e1'
down_revision: Union[str, Sequence[str], None] = '9762066ca2ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "discovered_videos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("video_id", sa.String(), nullable=False),
        sa.Column("author_unique_id", sa.String(), nullable=True),
        sa.Column("author_nickname", sa.String(), nullable=True),
        sa.Column("author_followers", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("play_count", sa.Integer(), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=True),
        sa.Column("comment_count", sa.Integer(), nullable=True),
        sa.Column("share_count", sa.Integer(), nullable=True),
        sa.Column("collect_count", sa.Integer(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("create_time", sa.DateTime(), nullable=True),
        sa.Column("search_keyword", sa.String(), nullable=False),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("url", name="uq_discovered_videos_url"),
    )
    op.create_index("idx_discovered_videos_play_count",
                    "discovered_videos", ["play_count"])
    op.create_index("idx_discovered_videos_create_time",
                    "discovered_videos", ["create_time"])
    op.create_index("idx_discovered_videos_last_seen_at",
                    "discovered_videos", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("idx_discovered_videos_last_seen_at",
                  table_name="discovered_videos")
    op.drop_index("idx_discovered_videos_create_time",
                  table_name="discovered_videos")
    op.drop_index("idx_discovered_videos_play_count",
                  table_name="discovered_videos")
    op.drop_table("discovered_videos")
