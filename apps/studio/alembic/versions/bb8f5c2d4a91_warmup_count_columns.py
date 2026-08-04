"""replace warmup duration columns with count columns

Revision ID: bb8f5c2d4a91
Revises: c3a6b2d1e4f7
Create Date: 2026-06-05 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "bb8f5c2d4a91"
down_revision: Union[str, Sequence[str], None] = "c3a6b2d1e4f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("warmup_count", sa.Integer(), nullable=False,
                  server_default="0"),
    )
    op.add_column(
        "accounts",
        sa.Column("target_warmup_count", sa.Integer(), nullable=False,
                  server_default="10"),
    )
    op.execute(
        "UPDATE accounts "
        "SET warmup_count = target_warmup_count "
        "WHERE warmed_up = 1"
    )
    op.drop_column("accounts", "time_spent_warming")
    op.drop_column("accounts", "max_warm_time")
    op.execute("DELETE FROM settings WHERE key = 'default_max_warm_time'")
    op.execute(
        "INSERT INTO settings (key, value) "
        "VALUES ('default_target_warmup_count', '10') "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("time_spent_warming", sa.Integer(), nullable=False,
                  server_default="0"),
    )
    op.add_column(
        "accounts",
        sa.Column("max_warm_time", sa.Integer(), nullable=False,
                  server_default="500"),
    )
    op.execute(
        "UPDATE accounts "
        "SET time_spent_warming = warmup_count * 60, "
        "    max_warm_time = GREATEST(target_warmup_count * 60, 60)"
    )
    op.drop_column("accounts", "target_warmup_count")
    op.drop_column("accounts", "warmup_count")
    op.execute("DELETE FROM settings WHERE key = 'default_target_warmup_count'")
    op.execute(
        "INSERT INTO settings (key, value) "
        "VALUES ('default_max_warm_time', '500') "
        "ON CONFLICT (key) DO NOTHING"
    )
