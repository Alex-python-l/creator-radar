"""move account_id from publications to videos

Revision ID: a8b2d34f7e91
Revises: f3a9c1e27b04
Create Date: 2026-05-30 00:00:00.000000

A video is published to one GeeLark cloud phone — all four platform
publications share that phone. The per-publication account_id added in
Phase 2 was vestigial; collapse it onto videos so the picker becomes a
single dropdown.

Backfill rule: for each video, copy the first non-NULL account_id from
its publications (any of the four). If none, leave NULL — auto-publish
will pick one later.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a8b2d34f7e91'
down_revision: Union[str, Sequence[str], None] = 'f3a9c1e27b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "videos",
        sa.Column("account_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_videos_account_id",
        "videos", "accounts",
        ["account_id"], ["id"],
        ondelete="SET NULL",
    )
    # Backfill: pick any non-NULL publication.account_id per video.
    op.execute("""
        UPDATE videos v
           SET account_id = sub.account_id
          FROM (
            SELECT DISTINCT ON (video_id) video_id, account_id
              FROM publications
             WHERE account_id IS NOT NULL
             ORDER BY video_id, id
          ) sub
         WHERE v.id = sub.video_id
    """)
    # Drop publication-level account_id.
    op.drop_constraint("publications_account_id_fkey",
                       "publications", type_="foreignkey")
    op.drop_column("publications", "account_id")


def downgrade() -> None:
    op.add_column(
        "publications",
        sa.Column("account_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "publications_account_id_fkey",
        "publications", "accounts",
        ["account_id"], ["id"],
        ondelete="SET NULL",
    )
    # Restore per-publication account_id from the video's account_id.
    op.execute("""
        UPDATE publications p
           SET account_id = v.account_id
          FROM videos v
         WHERE p.video_id = v.id
           AND v.account_id IS NOT NULL
    """)
    op.drop_constraint("fk_videos_account_id", "videos", type_="foreignkey")
    op.drop_column("videos", "account_id")
