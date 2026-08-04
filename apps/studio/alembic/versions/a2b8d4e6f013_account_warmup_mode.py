"""account warmup_mode column + daily_warmup_count setting

Revision ID: a2b8d4e6f013
Revises: a1b2c3d4e5f6
Create Date: 2026-06-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2b8d4e6f013"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "warmup_mode",
            sa.String(length=16),
            nullable=False,
            server_default="count",
        ),
    )
    op.execute(
        "INSERT INTO settings (key, value) "
        "VALUES ('daily_warmup_count', '4') "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_column("accounts", "warmup_mode")
    op.execute("DELETE FROM settings WHERE key='daily_warmup_count'")
