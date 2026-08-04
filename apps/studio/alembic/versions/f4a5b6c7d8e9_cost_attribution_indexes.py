"""cost attribution: indexes for time-window lateral joins

publications.posted_at and engagement_runs.finished_at had no
composite indexes paired with account_id, so the cost-audit page's
LATERAL join was seq-scanning publications (5k+ rows) for every
transaction row. Adds covering indexes; query drops from ~150ms/50
rows to roughly index-scan time.

Revision ID: f4a5b6c7d8e9
Revises: e3f4c5d6a7b8
Create Date: 2026-06-09 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "e3f4c5d6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "idx_publications_account_posted",
        "publications",
        ["account_id", "posted_at"],
        postgresql_where="posted_at IS NOT NULL",
    )
    op.create_index(
        "idx_engagement_runs_account_finished",
        "engagement_runs",
        ["account_id", "finished_at"],
        postgresql_where="finished_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_engagement_runs_account_finished",
        table_name="engagement_runs",
    )
    op.drop_index(
        "idx_publications_account_posted",
        table_name="publications",
    )
