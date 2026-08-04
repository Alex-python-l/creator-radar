"""phone lease columns

Adds accounts.phone_busy_until (lease expiration timestamp) and
accounts.warmup_task_id (in-flight GeeLark task id, with 'pending'
sentinel between lease grab and GeeLark submit). Partial index
supports the reclaim_in_flight_warmup cron scan.
"""
from alembic import op
import sqlalchemy as sa


revision = "d6e4ca33b3f9"
down_revision = "9762066ca2ef"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("phone_busy_until", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("warmup_task_id", sa.String(length=64), nullable=True),
    )
    op.execute(
        "CREATE INDEX idx_accounts_warmup_inflight "
        "ON accounts (phone_busy_until) "
        "WHERE warmup_task_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_accounts_warmup_inflight")
    op.drop_column("accounts", "warmup_task_id")
    op.drop_column("accounts", "phone_busy_until")
