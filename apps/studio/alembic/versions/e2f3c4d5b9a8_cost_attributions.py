"""cost_attributions ledger

Revision ID: e2f3c4d5b9a8
Revises: d9e1b1c5a7f2
Create Date: 2026-06-15 12:30:00.000000

Phase 3 of the 2026-06-13 phone-runtime-architecture spec.

Persists the result of "which task/session paid for this billing row"
so the Costs page stops re-computing the attribution CTE on every
page-load and so the audit trail survives schema changes in the
underlying business tables. One row per geelark_transactions row,
keyed on transaction_id.

Phase 3 backfills attributions for the existing snapshot and computes
new ones inline as the sync_transactions cron pulls fresh billing
rows. The Costs API reads cost_attributions first, falling back to
the legacy CTE only when no attribution row exists yet (for the
seconds between sync + attribution compute, or for transactions
whose phone_session has already been pruned).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f3c4d5b9a8"
down_revision: Union[str, Sequence[str], None] = "d9e1b1c5a7f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cost_attributions",
        sa.Column("transaction_id", sa.String(length=64), primary_key=True),
        sa.Column("phone_session_id", sa.BigInteger),
        sa.Column("task_run_id", sa.BigInteger),
        # 'publish' | 'warmup' | 'engagement' | 'manual' |
        # 'external_detected' | 'unattributed'.
        sa.Column("kind", sa.String(length=32), nullable=False),
        # 'high' | 'medium' | 'low'.
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    # Group / sum by kind on the Costs overview.
    op.create_index(
        "idx_cost_attributions_kind",
        "cost_attributions",
        ["kind"],
    )


def downgrade() -> None:
    op.drop_index("idx_cost_attributions_kind",
                  table_name="cost_attributions")
    op.drop_table("cost_attributions")
