"""phone_sessions table

Revision ID: b8e55510a5e7
Revises: aa11bb22cc33
Create Date: 2026-06-13 13:37:00.000000

Phase 1 Step 1 of the 2026-06-13 phone-runtime-architecture spec.

Adds phone_sessions as the v1 cloud-phone-runtime ledger, evolved from
the current accounts.phone_busy_until / accounts.warmup_task_id pair.

Step 1 only creates the table and its indexes — production code does
not yet read or write phone_sessions. Step 2 wires the Phone Runtime
Manager into publish/warmup/engagement with dual-write to the existing
account-level lease; Step 3 then switches the idle sweeper to dual-read.
Until those land, this table stays empty, so the migration is
non-destructive and runs in microseconds on production data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e55510a5e7"
down_revision: Union[str, Sequence[str], None] = "aa11bb22cc33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "phone_sessions",
        sa.Column("id", sa.BigInteger, primary_key=True,
                  autoincrement=True),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("env_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("owner_type", sa.String(length=32)),
        sa.Column("owner_id", sa.BigInteger),
        sa.Column("geelark_task_id", sa.String(length=64)),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("budget_deadline_at", sa.DateTime, nullable=False),
        sa.Column("stop_requested_at", sa.DateTime),
        sa.Column("stopped_at", sa.DateTime),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("final_status", sa.String(length=32)),
        sa.Column("stop_reason", sa.Text),
        sa.Column("last_observed_status", sa.String(length=32)),
        sa.Column("cost_transaction_id", sa.String(length=64)),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Same account_id can only have one in-flight (stopped_at IS NULL)
    # session at any time. Partial unique index keyed on stopped_at —
    # a physical fact that doesn't shift if we rename status values
    # later.
    op.execute(
        "CREATE UNIQUE INDEX idx_phone_sessions_one_active "
        "ON phone_sessions (account_id) "
        "WHERE stopped_at IS NULL"
    )

    # Phase 2 finalize path resolves geelark_task_id -> phone_session.
    op.create_index(
        "idx_phone_sessions_geelark_task",
        "phone_sessions",
        ["geelark_task_id"],
    )

    # Watchdog scan: in-flight sessions past their budget deadline.
    op.execute(
        "CREATE INDEX idx_phone_sessions_overdue "
        "ON phone_sessions (budget_deadline_at) "
        "WHERE stopped_at IS NULL"
    )

    # Cost attribution: join geelark_transactions by env_id + time
    # overlap to find the owning session.
    op.create_index(
        "idx_phone_sessions_env_started",
        "phone_sessions",
        ["env_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_phone_sessions_env_started")
    op.execute("DROP INDEX IF EXISTS idx_phone_sessions_overdue")
    op.execute("DROP INDEX IF EXISTS idx_phone_sessions_geelark_task")
    op.execute("DROP INDEX IF EXISTS idx_phone_sessions_one_active")
    op.drop_table("phone_sessions")
