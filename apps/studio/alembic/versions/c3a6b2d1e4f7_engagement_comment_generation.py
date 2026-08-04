"""engagement comment generation metadata

Revision ID: c3a6b2d1e4f7
Revises: aa12f0e9c3b4
Create Date: 2026-06-04 17:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision: str = "c3a6b2d1e4f7"
down_revision: Union[str, Sequence[str], None] = "aa12f0e9c3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "engagement_tasks",
        sa.Column(
            "comment_mode",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="geelark_ai",
        ),
    )
    op.add_column(
        "engagement_tasks",
        sa.Column("product_info", sa.Text(), nullable=True),
    )
    op.add_column(
        "engagement_tasks",
        sa.Column("comment_brief", sa.Text(), nullable=True),
    )
    op.add_column(
        "engagement_runs",
        sa.Column("target_title", sa.Text(), nullable=True),
    )
    op.add_column(
        "engagement_runs",
        sa.Column("target_description", sa.Text(), nullable=True),
    )
    op.add_column(
        "engagement_runs",
        sa.Column("comment_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "engagement_runs",
        sa.Column("comment_source", sqlmodel.sql.sqltypes.AutoString(),
                  nullable=True),
    )
    op.add_column(
        "engagement_runs",
        sa.Column("comment_generated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("engagement_runs", "comment_generated_at")
    op.drop_column("engagement_runs", "comment_source")
    op.drop_column("engagement_runs", "comment_text")
    op.drop_column("engagement_runs", "target_description")
    op.drop_column("engagement_runs", "target_title")
    op.drop_column("engagement_tasks", "comment_brief")
    op.drop_column("engagement_tasks", "product_info")
    op.drop_column("engagement_tasks", "comment_mode")
