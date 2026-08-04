"""publications table and platform columns

Revision ID: 69748d477a7f
Revises: d7279ba4a07f
Create Date: 2026-05-29 10:45:56.096191

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
_AS = sqlmodel.sql.sqltypes.AutoString


# revision identifiers, used by Alembic.
revision: str = '69748d477a7f'
down_revision: Union[str, Sequence[str], None] = 'd7279ba4a07f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Nine new account columns.
    for col in ("youtube_logged_in", "instagram_logged_in",
                "facebook_logged_in"):
        op.add_column("accounts",
            sa.Column(col, sa.Integer(), nullable=False,
                       server_default="0"))
    for col in ("youtube_username", "instagram_username",
                "facebook_username"):
        op.add_column("accounts", sa.Column(col, _AS(), nullable=True))
    for col in ("last_posted_at_youtube", "last_posted_at_instagram",
                "last_posted_at_facebook"):
        op.add_column("accounts", sa.Column(col, sa.DateTime(),
                                              nullable=True))

    # 2) publications table + UNIQUE + INDEX.
    op.create_table(
        "publications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(),
                   sa.ForeignKey("videos.id", ondelete="CASCADE"),
                   nullable=False),
        sa.Column("platform", _AS(), nullable=False),
        sa.Column("account_id", sa.Integer(),
                   sa.ForeignKey("accounts.id", ondelete="SET NULL"),
                   nullable=True),
        sa.Column("result", _AS(), nullable=False,
                   server_default="PENDING"),
        sa.Column("caption", _AS(), nullable=True),
        sa.Column("geelark_task_id", _AS(), nullable=True),
        sa.Column("notes", _AS(), nullable=True),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                   server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("video_id", "platform",
                              name="uq_publications_video_platform"),
    )
    op.create_index("idx_publications_status", "publications",
                     ["result", "platform"])

    # 3) Backfill TikTok rows from videos into publications.
    op.execute("""
        INSERT INTO publications
          (video_id, platform, account_id, result, caption,
           geelark_task_id, notes, posted_at, last_checked_at, created_at)
        SELECT id, 'tiktok', account_id, COALESCE(result, 'PENDING'),
               caption, geelark_task_id, notes, posted_at, last_checked_at,
               CURRENT_TIMESTAMP
        FROM videos
    """)

    # 4) Drop now-redundant videos columns (reverse order to avoid FK issues).
    for col in ("last_checked_at", "posted_at", "notes",
                "geelark_task_id", "result", "account_id"):
        op.drop_column("videos", col)


def downgrade() -> None:
    op.add_column("videos",
        sa.Column("account_id", sa.Integer(), nullable=True))
    op.add_column("videos",
        sa.Column("result", _AS(), nullable=True,
                   server_default="PENDING"))
    op.add_column("videos",
        sa.Column("geelark_task_id", _AS(), nullable=True))
    op.add_column("videos",
        sa.Column("notes", _AS(), nullable=True))
    op.add_column("videos",
        sa.Column("posted_at", sa.DateTime(), nullable=True))
    op.add_column("videos",
        sa.Column("last_checked_at", sa.DateTime(), nullable=True))
    op.execute("""
        UPDATE videos v SET
          account_id = p.account_id,
          result = p.result,
          geelark_task_id = p.geelark_task_id,
          notes = p.notes,
          posted_at = p.posted_at,
          last_checked_at = p.last_checked_at
        FROM publications p
        WHERE p.video_id = v.id AND p.platform = 'tiktok'
    """)
    op.drop_index("idx_publications_status", table_name="publications")
    op.drop_table("publications")
    for col in ("last_posted_at_facebook", "last_posted_at_instagram",
                "last_posted_at_youtube",
                "facebook_username", "instagram_username", "youtube_username",
                "facebook_logged_in", "instagram_logged_in",
                "youtube_logged_in"):
        op.drop_column("accounts", col)
