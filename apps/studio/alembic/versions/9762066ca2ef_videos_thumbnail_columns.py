"""videos thumbnail columns

Revision ID: 9762066ca2ef
Revises: a2b8d4e6f013
Create Date: 2026-06-08 14:30:32.523791

Adds `thumbnail_path` (relative path under data/thumbnails) and
`duration_seconds` to `videos`. Both nullable; backfilled later by
the `backfill_thumbnails` task.
"""
from alembic import op
import sqlalchemy as sa


revision = "9762066ca2ef"
down_revision = "a2b8d4e6f013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos",
                  sa.Column("thumbnail_path", sa.String(length=255),
                            nullable=True))
    op.add_column("videos",
                  sa.Column("duration_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "duration_seconds")
    op.drop_column("videos", "thumbnail_path")
