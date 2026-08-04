"""drop legacy account lease columns

Removes the two legacy lease/sentinel columns now that phone_sessions
is the single source of truth:

- accounts.phone_busy_until: replaced by phone_sessions.budget_deadline_at.
- accounts.warmup_task_id:   replaced by phone_sessions.geelark_task_id
                             (with NULL = T1↔T3 crash window sentinel).

All production code paths read/write phone_sessions exclusively; the
LEGACY helpers in app/repositories/accounts.py have been removed in
the same change.

Revision ID: c4d6e8f0a3b5
Revises: b3d5e7f9c1a2
Create Date: 2026-06-19 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c4d6e8f0a3b5"
down_revision: Union[str, Sequence[str], None] = "b3d5e7f9c1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("accounts", "phone_busy_until")
    op.drop_column("accounts", "warmup_task_id")


def downgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("warmup_task_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("phone_busy_until", sa.DateTime(), nullable=True),
    )
