"""publication attempts and retry metadata

Revision ID: aa12f0e9c3b4
Revises: b7c9d2e4f601
Create Date: 2026-06-04 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision: str = "aa12f0e9c3b4"
down_revision: Union[str, Sequence[str], None] = "b7c9d2e4f601"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE publications "
        "ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE publications "
        "ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMP NULL"
    )
    op.create_table(
        "publication_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("publication_id", sa.Integer(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("stage", sqlmodel.sql.sqltypes.AutoString(),
                  nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(),
                  nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("error_code", sqlmodel.sql.sqltypes.AutoString(),
                  nullable=True),
        sa.Column("fail_screenshot", sa.Text(), nullable=True),
        sa.Column("geelark_task_id", sqlmodel.sql.sqltypes.AutoString(),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_publication_attempts_publication",
        "publication_attempts",
        ["publication_id", "attempt_no"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_publication_attempts_publication",
                  table_name="publication_attempts")
    op.drop_table("publication_attempts")
    op.drop_column("publications", "next_retry_at")
    op.drop_column("publications", "retry_count")
