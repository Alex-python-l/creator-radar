"""callback_inbox dedup table

GeeLark may retry callbacks; the inbox lets us idempotently turn a
stream of payloads into at-most-one wake per (task_id, status, body)
triple. The endpoint INSERTs ON CONFLICT DO NOTHING and only defers a
process job when the row is newly inserted.

Revision ID: d5e7a9c1f3b8
Revises: c4d6e8f0a3b5
Create Date: 2026-06-19 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d5e7a9c1f3b8"
down_revision: Union[str, Sequence[str], None] = "c4d6e8f0a3b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "callback_inbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("task_id", sa.String(length=64), nullable=False),
        # GeeLark task status from the payload (NULL when we could not
        # parse one). Distinct status values for the same task are
        # treated as distinct events.
        sa.Column("status", sa.Integer(), nullable=True),
        # sha256 of the raw body, hex. Different bodies for the same
        # (task_id, status) — vanishingly rare but possible if GeeLark
        # enriches the payload — are still kept apart.
        sa.Column("body_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        # Short tag describing what happened: 'wake:publish' |
        # 'wake:engagement' | 'wake:warmup' | 'no_session' |
        # 'error:<short>'. NULL while pending.
        sa.Column("process_outcome", sa.String(length=64), nullable=True),
        sa.UniqueConstraint(
            "task_id", "status", "body_hash",
            name="uq_callback_inbox_event",
        ),
    )
    # Lookups by task_id (for the process job + ad-hoc queries) and a
    # partial index for the future reaper that surfaces stuck
    # unprocessed rows.
    op.create_index(
        "idx_callback_inbox_task_id",
        "callback_inbox", ["task_id"],
    )
    op.create_index(
        "idx_callback_inbox_unprocessed",
        "callback_inbox", ["received_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_callback_inbox_unprocessed", table_name="callback_inbox")
    op.drop_index("idx_callback_inbox_task_id", table_name="callback_inbox")
    op.drop_table("callback_inbox")
