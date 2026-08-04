"""geelark callback log table

Revision ID: ca11bac4109e
Revises: f0a1b2c3d4e5
Create Date: 2026-06-13 13:30:00.000000

Phase 0 of the GeeLark task-done callback work (2026-06-09 design,
re-anchored by 2026-06-13 phone-runtime-architecture spec).

This phase is observation-only: every inbound callback hits a log-only
endpoint and lands here raw. Phase 1 derives the real payload schema
from these rows and wires the finalize path; no production code reads
from this table yet.

Retention: a follow-up cron prunes rows older than 7 days. Not
critical for Phase 0 — table volume is bounded by task throughput.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ca11bac4109e"
down_revision: Union[str, Sequence[str], None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "geelark_callback_log",
        sa.Column("id", sa.BigInteger, primary_key=True,
                  autoincrement=True),
        sa.Column("received_at", sa.DateTime, nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("source_ip", sa.String(length=45)),
        sa.Column("headers_json", sa.JSON, nullable=False),
        sa.Column("body_text", sa.Text, nullable=False),
        sa.Column("parsed_task_id", sa.String(length=64)),
        sa.Column("parsed_status", sa.Integer),
        sa.Column("handled", sa.Boolean, nullable=False,
                  server_default=sa.false()),
        sa.Column("handled_at", sa.DateTime),
        sa.Column("handle_outcome", sa.Text),
    )
    op.create_index(
        "idx_gcl_task", "geelark_callback_log", ["parsed_task_id"],
    )
    op.execute(
        "CREATE INDEX idx_gcl_received "
        "ON geelark_callback_log (received_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_gcl_received")
    op.drop_index("idx_gcl_task", table_name="geelark_callback_log")
    op.drop_table("geelark_callback_log")
