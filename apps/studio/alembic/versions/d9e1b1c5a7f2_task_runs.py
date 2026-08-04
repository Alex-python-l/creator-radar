"""task_runs state machine

Revision ID: d9e1b1c5a7f2
Revises: b8e55510a5e7
Create Date: 2026-06-15 12:00:00.000000

Phase 2 of the 2026-06-13 phone-runtime-architecture spec. Adds the
task_runs business-state table that pairs 1:1 with the phone_sessions
row for warmup / publish / engagement work. Separate from phone_sessions
because the two lifecycles diverge in failure: a task_run can land in
TIMEOUT while its phone_session is still STOPPING, a manual hold's
phone_session has no task_run, and future retries will allocate one
task_run per attempt.

Phase 2 only adds the schema + writes — the table is observed but not
yet read by production code paths. Future PRs will read state from it
(callback finalize, audit page, ops view).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9e1b1c5a7f2"
down_revision: Union[str, Sequence[str], None] = "b8e55510a5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_runs",
        sa.Column("id", sa.BigInteger, primary_key=True,
                  autoincrement=True),
        # 'publish' | 'warmup' | 'engagement'.
        sa.Column("kind", sa.String(length=32), nullable=False),
        # business referent (e.g. 'publication' + publications.id, or
        # 'account' + accounts.id for warmup).
        sa.Column("ref_type", sa.String(length=32), nullable=False),
        sa.Column("ref_id", sa.BigInteger, nullable=False),
        sa.Column("account_id", sa.Integer, nullable=False),
        sa.Column("phone_session_id", sa.BigInteger),
        sa.Column("geelark_task_id", sa.String(length=64)),
        # PLANNED / PHONE_STARTING / SUBMITTED_TO_GEELARK / RUNNING /
        # SUCCESS / FAILURE / TIMEOUT / CANCELLED.
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False,
                  server_default="1"),
        sa.Column("planned_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("finished_at", sa.DateTime),
        sa.Column("deadline_at", sa.DateTime),
        sa.Column("failure_reason", sa.Text),
        sa.Column(
            "created_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at", sa.DateTime, nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    # Resolve a phone_session_id back to its business task_run.
    op.create_index(
        "idx_task_runs_phone_session",
        "task_runs",
        ["phone_session_id"],
    )
    # Resolve a GeeLark task id (callback path / poll path) back to
    # the business task_run.
    op.create_index(
        "idx_task_runs_geelark_task",
        "task_runs",
        ["geelark_task_id"],
    )
    # Look up by business referent (e.g. "all task_runs for
    # publication 1234"). Composite so audit queries don't scan the
    # full table.
    op.create_index(
        "idx_task_runs_ref",
        "task_runs",
        ["ref_type", "ref_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_task_runs_ref", table_name="task_runs")
    op.drop_index("idx_task_runs_geelark_task", table_name="task_runs")
    op.drop_index("idx_task_runs_phone_session", table_name="task_runs")
    op.drop_table("task_runs")
