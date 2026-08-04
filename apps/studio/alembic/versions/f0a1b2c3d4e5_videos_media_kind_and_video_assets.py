"""videos.media_kind and video_assets

Revision ID: f0a1b2c3d4e5
Revises: d9f2c3a4b5e6
Create Date: 2026-06-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "d9f2c3a4b5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "videos",
        sa.Column(
            "media_kind",
            sa.String(length=16),
            nullable=False,
            server_default="video",
        ),
    )
    op.create_index(
        "idx_videos_media_kind", "videos", ["media_kind"],
    )
    op.create_table(
        "video_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["video_id"], ["videos.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "video_id", "idx", name="uq_video_assets_video_idx",
        ),
    )


def downgrade() -> None:
    op.drop_table("video_assets")
    op.drop_index("idx_videos_media_kind", table_name="videos")
    op.drop_column("videos", "media_kind")
