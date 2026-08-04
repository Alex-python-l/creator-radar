"""publication failure detail (fail_code, fail_screenshot)

Revision ID: f3a9c1e27b04
Revises: 8deafc73b3fd
Create Date: 2026-05-29 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9c1e27b04'
down_revision: Union[str, Sequence[str], None] = '8deafc73b3fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # On a FAILURE, posting_service stores GeeLark's failCode and the URL of
    # the last RPA screenshot (where the cloud-phone flow got stuck).
    op.add_column(
        "publications",
        sa.Column("fail_code", sa.Integer(), nullable=True),
    )
    op.add_column(
        "publications",
        sa.Column("fail_screenshot", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publications", "fail_screenshot")
    op.drop_column("publications", "fail_code")
