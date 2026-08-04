"""proxy expiry and country/region

Adds three nullable columns to accounts. proxy_expires_at is set to
NOW() + 30d by the repository layer whenever an account's proxy is
written or changed; existing rows keep NULL (no warning fires) until the
user replaces the proxy. proxy_country / proxy_region are filled by the
periodic probe scan from the ipinfo.io payload that was previously
discarded.

Revision ID: a30d4cb53cc7
Revises: e6f8b1c3d5a9
Create Date: 2026-06-24 14:32:33.033696

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a30d4cb53cc7'
down_revision: Union[str, Sequence[str], None] = 'e6f8b1c3d5a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("proxy_expires_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("proxy_country", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("proxy_region", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "proxy_region")
    op.drop_column("accounts", "proxy_country")
    op.drop_column("accounts", "proxy_expires_at")
