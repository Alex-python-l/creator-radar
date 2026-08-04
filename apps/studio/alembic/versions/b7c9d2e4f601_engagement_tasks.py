"""engagement tasks

Revision ID: b7c9d2e4f601
Revises: 4576004906dc
Create Date: 2026-06-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision: str = "b7c9d2e4f601"
down_revision: Union[str, Sequence[str], None] = "4576004906dc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "publications",
        sa.Column("share_link", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        "publications",
        sa.Column("share_link_captured_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "engagement_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("action", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "target_links",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column(
            "target_publication_ids",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column("comment_probability", sa.Integer(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_engagement_tasks_group_status",
        "engagement_tasks",
        ["group_id", "status"],
        unique=False,
    )
    op.create_table(
        "engagement_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("engagement_task_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("target_publication_id", sa.Integer(), nullable=True),
        sa.Column("target_account_id", sa.Integer(), nullable=True),
        sa.Column("target_link", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("action", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("result", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            "geelark_task_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True
        ),
        sa.Column("schedule_at", sa.Integer(), nullable=True),
        sa.Column("notes", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("fail_code", sa.Integer(), nullable=True),
        sa.Column("fail_screenshot", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("cost", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["engagement_task_id"],
            ["engagement_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_account_id"], ["accounts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["target_publication_id"], ["publications.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_engagement_runs_task",
        "engagement_runs",
        ["engagement_task_id"],
        unique=False,
    )
    op.create_index(
        "idx_engagement_runs_account_result",
        "engagement_runs",
        ["account_id", "result"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_engagement_runs_account_result", table_name="engagement_runs"
    )
    op.drop_index("idx_engagement_runs_task", table_name="engagement_runs")
    op.drop_table("engagement_runs")
    op.drop_index(
        "idx_engagement_tasks_group_status", table_name="engagement_tasks"
    )
    op.drop_table("engagement_tasks")
    op.drop_column("publications", "share_link_captured_at")
    op.drop_column("publications", "share_link")
