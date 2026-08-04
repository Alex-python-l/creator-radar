"""switch engagement_tasks.comment_prompt_id FK to ON DELETE SET NULL

The original c8e1a2b3d4f5 migration used ON DELETE RESTRICT as a last-
line defence. In practice the service layer guards inflight runs
explicitly (PENDING / QUEUED / INITIATED) and refuses to delete a prompt
that is still inflight; rows in TERMINAL states (SUCCESS / FAILURE /
SKIPPED) should not block the prompt from being garbage-collected — the
audit trail lives in engagement_runs (comment_text + comment_source),
not in the FK back to comment_prompts.

This brings the comment_prompts FK in line with groups.caption_prompt_id,
which has used SET NULL since b5c4e8f3a712 for the same reason.

Revision ID: d9f2c3a4b5e6
Revises: c8e1a2b3d4f5
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op


revision: str = "d9f2c3a4b5e6"
down_revision: Union[str, Sequence[str], None] = "c8e1a2b3d4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FK_NAME = "engagement_tasks_comment_prompt_id_fkey"


def upgrade() -> None:
    op.drop_constraint(FK_NAME, "engagement_tasks", type_="foreignkey")
    op.create_foreign_key(
        FK_NAME,
        "engagement_tasks",
        "comment_prompts",
        ["comment_prompt_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(FK_NAME, "engagement_tasks", type_="foreignkey")
    op.create_foreign_key(
        FK_NAME,
        "engagement_tasks",
        "comment_prompts",
        ["comment_prompt_id"],
        ["id"],
        ondelete="RESTRICT",
    )
