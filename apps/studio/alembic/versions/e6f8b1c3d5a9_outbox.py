"""outbox table for transactional event dispatch

API endpoints that create a business row and defer a worker task have a
silent failure window: if the defer call dies after the DB commit (e.g.
Procrastinate App not open, connector hiccup, worker restart), the row
is stuck PENDING with no event to drive it. The outbox closes that
window: the endpoint writes (business row, outbox row) in one
transaction; a periodic dispatcher re-defers any outbox row whose
sent_at is still NULL.

Revision ID: e6f8b1c3d5a9
Revises: d5e7a9c1f3b8
Create Date: 2026-06-19 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e6f8b1c3d5a9"
down_revision: Union[str, Sequence[str], None] = "d5e7a9c1f3b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        # Short intent string, e.g. 'publish_publication',
        # 'submit_engagement_run', 'extract_understanding'. Drives the
        # dispatcher's defer routing.
        sa.Column("intent", sa.String(length=64), nullable=False),
        # Stable reference to the business entity this event is for,
        # e.g. 'publication:1234'. Carries the (intent, business_ref)
        # idempotency key.
        sa.Column("business_ref", sa.String(length=128), nullable=False),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(),
            nullable=False, server_default=sa.func.now(),
        ),
        # Number of dispatch attempts so far (0 at enqueue, incremented
        # on each failed defer).
        sa.Column(
            "attempts", sa.Integer(),
            nullable=False, server_default="0",
        ),
        # Earliest moment the dispatcher should re-try this row. Set to
        # now() at enqueue; bumped on failure for backoff.
        sa.Column(
            "next_attempt_at", sa.DateTime(),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    # Idempotency: at most one in-flight outbox row per business entity
    # per intent. Repeated enqueue while still pending is a no-op so
    # API handlers can be replayed without doubling up.
    op.create_index(
        "uq_outbox_inflight_business_ref",
        "outbox",
        ["intent", "business_ref"],
        unique=True,
        postgresql_where=sa.text("sent_at IS NULL"),
    )
    # Dispatcher hot path: pull rows whose retry time is due, oldest
    # first.
    op.create_index(
        "idx_outbox_pending",
        "outbox",
        ["next_attempt_at"],
        postgresql_where=sa.text("sent_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_outbox_pending", table_name="outbox")
    op.drop_index("uq_outbox_inflight_business_ref", table_name="outbox")
    op.drop_table("outbox")
