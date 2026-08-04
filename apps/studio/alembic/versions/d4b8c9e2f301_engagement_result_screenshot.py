"""engagement result screenshot

Revision ID: d4b8c9e2f301
Revises: c3a6b2d1e4f7
Create Date: 2026-06-05 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


revision: str = "d4b8c9e2f301"
down_revision: Union[str, Sequence[str], None] = "c3a6b2d1e4f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "engagement_runs",
        sa.Column(
            "result_screenshot",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("engagement_runs", "result_screenshot")
