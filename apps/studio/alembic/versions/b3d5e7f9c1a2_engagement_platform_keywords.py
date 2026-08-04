"""engagement platform / keywords / comment_candidates

Revision ID: b3d5e7f9c1a2
Revises: e2f3c4d5b9a8
Create Date: 2026-06-16 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b3d5e7f9c1a2"
down_revision: Union[str, Sequence[str], None] = "e2f3c4d5b9a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "engagement_tasks",
        sa.Column(
            "platform",
            sa.String(),
            nullable=False,
            server_default="tiktok",
        ),
    )
    op.add_column(
        "engagement_tasks",
        sa.Column(
            "keywords",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.create_index(
        "idx_engagement_tasks_platform_status",
        "engagement_tasks",
        ["platform", "status"],
    )
    op.add_column(
        "engagement_runs",
        sa.Column(
            "comment_candidates",
            sa.JSON(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_engagement_tasks_platform_status",
        table_name="engagement_tasks",
    )
    op.drop_column("engagement_runs", "comment_candidates")
    op.drop_column("engagement_tasks", "keywords")
    op.drop_column("engagement_tasks", "platform")
