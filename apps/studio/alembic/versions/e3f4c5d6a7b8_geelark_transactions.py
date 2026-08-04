"""geelark_transactions table

Revision ID: e3f4c5d6a7b8
Revises: a87a3b66544a
Create Date: 2026-06-09 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3f4c5d6a7b8'
down_revision: Union[str, Sequence[str], None] = 'a87a3b66544a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "geelark_transactions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("env_id", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("used_time_min", sa.Integer(), nullable=False),
        sa.Column("type", sa.Integer(), nullable=False),
        sa.Column("charge_type", sa.Integer(), nullable=False),
        sa.Column("geelark_created_at", sa.DateTime(), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "idx_geelark_tx_env_time",
        "geelark_transactions",
        ["env_id", sa.text("geelark_created_at DESC")],
    )
    op.create_index(
        "idx_geelark_tx_time",
        "geelark_transactions",
        [sa.text("geelark_created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_geelark_tx_time", table_name="geelark_transactions")
    op.drop_index("idx_geelark_tx_env_time", table_name="geelark_transactions")
    op.drop_table("geelark_transactions")
