"""writer factory phase 1 — understanding columns + writer_audit table

Revision ID: aa11bb22cc33
Revises: ca11bac4109e
Create Date: 2026-06-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "aa11bb22cc33"
down_revision: Union[str, Sequence[str], None] = "ca11bac4109e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # videos / discovered_videos — Layer-1 understanding cache.
    for table in ("videos", "discovered_videos"):
        op.add_column(
            table,
            sa.Column("understanding", postgresql.JSONB(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(
                "understanding_status",
                sa.String(length=20),
                nullable=False,
                server_default="pending",
            ),
        )

    # writer_audit — populated by Layers 6-7 in later phases; created
    # now so the schema is in place when those phases land.
    op.create_table(
        "writer_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("publication_id", sa.Integer(), nullable=True),
        sa.Column("engagement_run_id", sa.Integer(), nullable=True),
        sa.Column("video_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("voice", postgresql.JSONB(), nullable=False),
        sa.Column("angle", sa.String(length=40), nullable=False),
        sa.Column("trend_anchors_used", postgresql.JSONB(), nullable=True),
        sa.Column("candidates", postgresql.JSONB(), nullable=False),
        sa.Column("winner_idx", sa.Integer(), nullable=False),
        sa.Column("winner_fingerprint", postgresql.JSONB(), nullable=False),
        sa.Column("dedup_self_max", sa.Float(), nullable=True),
        sa.Column("dedup_cross_max", sa.Float(), nullable=True),
        sa.Column("total_gemini_calls", sa.Integer(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"], ["publications.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["engagement_run_id"], ["engagement_runs.id"], ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "(publication_id IS NULL) <> (engagement_run_id IS NULL)",
            name="writer_audit_one_target",
        ),
    )
    op.create_index(
        "idx_writer_audit_account_time",
        "writer_audit",
        ["account_id", sa.text("generated_at DESC")],
    )
    op.create_index(
        "idx_writer_audit_kind_time",
        "writer_audit",
        ["kind", sa.text("generated_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_writer_audit_kind_time", table_name="writer_audit")
    op.drop_index("idx_writer_audit_account_time", table_name="writer_audit")
    op.drop_table("writer_audit")
    for table in ("discovered_videos", "videos"):
        op.drop_column(table, "understanding_status")
        op.drop_column(table, "understanding")
