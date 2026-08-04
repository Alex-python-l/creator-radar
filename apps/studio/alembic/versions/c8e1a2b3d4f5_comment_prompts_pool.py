"""comment_prompts pool + engagement_tasks.comment_prompt_id

Adds a reusable prompt pool for AI-generated TikTok comments, mirroring
the caption_prompts pool added in b5c4e8f3a712. The existing
engagement_tasks.comment_brief column stays in place as a read-only
fallback for rows persisted before this change.

Revision ID: c8e1a2b3d4f5
Revises: b5c4e8f3a712
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8e1a2b3d4f5"
down_revision: Union[str, Sequence[str], None] = "b5c4e8f3a712"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "comment_prompts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "engagement_tasks",
        sa.Column(
            "comment_prompt_id",
            sa.Integer,
            sa.ForeignKey("comment_prompts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("engagement_tasks") as batch:
        batch.drop_column("comment_prompt_id")
    op.drop_table("comment_prompts")
