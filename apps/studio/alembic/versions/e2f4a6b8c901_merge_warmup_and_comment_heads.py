"""merge warmup count and comment screenshot heads

Revision ID: e2f4a6b8c901
Revises: bb8f5c2d4a91, d4b8c9e2f301
Create Date: 2026-06-05 10:55:00.000000

"""
from typing import Sequence, Union


revision: str = "e2f4a6b8c901"
down_revision: Union[str, Sequence[str], None] = (
    "bb8f5c2d4a91",
    "d4b8c9e2f301",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
