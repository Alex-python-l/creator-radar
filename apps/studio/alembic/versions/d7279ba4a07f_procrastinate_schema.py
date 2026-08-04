"""procrastinate schema

Revision ID: d7279ba4a07f
Revises: 93a8a5f4803c
Create Date: 2026-05-29 00:01:29.027622

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd7279ba4a07f'
down_revision: Union[str, Sequence[str], None] = '93a8a5f4803c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _procrastinate_schema_sql() -> str:
    """Fetch the procrastinate schema DDL from the installed package.

    Uses App(connector=None).schema_manager.get_schema() — tested with
    procrastinate 3.8.1. The connector is not used for get_schema(), so
    passing None is safe and avoids any live DB connection at migration time.
    """
    import procrastinate

    app = procrastinate.App(connector=None)
    return app.schema_manager.get_schema()


def upgrade() -> None:
    """Apply procrastinate's full DDL schema.

    NOTE: procrastinate's DDL is NOT idempotent (no `CREATE OR REPLACE`,
    no `IF NOT EXISTS`). If procrastinate objects already exist in the
    database — e.g. created by `procrastinate schema --apply` run
    manually, or by an earlier `alembic upgrade` that wasn't paired
    with a `downgrade` — this migration will fail with
    `DuplicateFunction` / `DuplicateTable` / `DuplicateObject`.
    Recovery: run `alembic downgrade 93a8a5f4803c` first (which drops
    everything cleanly), then re-run `alembic upgrade head`.

    Creates (procrastinate 3.8.1):
      Tables:  procrastinate_workers, procrastinate_jobs,
               procrastinate_periodic_defers, procrastinate_events
      Types:   procrastinate_job_status, procrastinate_job_event_type,
               procrastinate_job_to_defer_v1
      Functions: procrastinate_defer_jobs_v1, procrastinate_defer_periodic_job_v2,
                 procrastinate_fetch_job_v2, procrastinate_finish_job_v1,
                 procrastinate_cancel_job_v1, procrastinate_retry_job_v1,
                 procrastinate_retry_job_v2, procrastinate_notify_queue_job_inserted_v1,
                 procrastinate_notify_queue_abort_job_v1,
                 procrastinate_trigger_function_status_events_insert_v1,
                 procrastinate_trigger_function_status_events_update_v1,
                 procrastinate_trigger_function_scheduled_events_v1,
                 procrastinate_trigger_abort_requested_events_procedure_v1,
                 procrastinate_unlink_periodic_defers_v1,
                 procrastinate_register_worker_v1, procrastinate_unregister_worker_v1,
                 procrastinate_update_heartbeat_v1, procrastinate_prune_stalled_workers_v1
      Triggers: procrastinate_jobs_notify_queue_job_inserted_v1,
                procrastinate_jobs_notify_queue_job_aborted_v1,
                procrastinate_trigger_status_events_update_v1,
                procrastinate_trigger_status_events_insert_v1,
                procrastinate_trigger_scheduled_events_v1,
                procrastinate_trigger_abort_requested_events_v1,
                procrastinate_trigger_delete_jobs_v1
    """
    sql = _procrastinate_schema_sql()
    op.execute(sql)


def downgrade() -> None:
    """Drop all objects created by procrastinate's schema (procrastinate 3.8.1)."""
    # Triggers are dropped implicitly via CASCADE when tables are dropped,
    # but drop them explicitly first for clarity.
    op.execute("DROP TRIGGER IF EXISTS procrastinate_trigger_delete_jobs_v1 ON procrastinate_events;")
    op.execute("DROP TRIGGER IF EXISTS procrastinate_trigger_abort_requested_events_v1 ON procrastinate_jobs;")
    op.execute("DROP TRIGGER IF EXISTS procrastinate_trigger_scheduled_events_v1 ON procrastinate_jobs;")
    op.execute("DROP TRIGGER IF EXISTS procrastinate_trigger_status_events_insert_v1 ON procrastinate_jobs;")
    op.execute("DROP TRIGGER IF EXISTS procrastinate_trigger_status_events_update_v1 ON procrastinate_jobs;")
    op.execute("DROP TRIGGER IF EXISTS procrastinate_jobs_notify_queue_job_aborted_v1 ON procrastinate_jobs;")
    op.execute("DROP TRIGGER IF EXISTS procrastinate_jobs_notify_queue_job_inserted_v1 ON procrastinate_jobs;")

    # Tables (CASCADE drops dependent foreign keys / triggers automatically)
    op.execute("DROP TABLE IF EXISTS procrastinate_events CASCADE;")
    op.execute("DROP TABLE IF EXISTS procrastinate_periodic_defers CASCADE;")
    op.execute("DROP TABLE IF EXISTS procrastinate_jobs CASCADE;")
    op.execute("DROP TABLE IF EXISTS procrastinate_workers CASCADE;")

    # Functions
    op.execute("DROP FUNCTION IF EXISTS procrastinate_prune_stalled_workers_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_update_heartbeat_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_unregister_worker_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_register_worker_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_unlink_periodic_defers_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_trigger_abort_requested_events_procedure_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_trigger_function_scheduled_events_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_trigger_function_status_events_update_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_trigger_function_status_events_insert_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_notify_queue_abort_job_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_notify_queue_job_inserted_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_retry_job_v2 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_retry_job_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_cancel_job_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_finish_job_v1 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_fetch_job_v2 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_defer_periodic_job_v2 CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS procrastinate_defer_jobs_v1 CASCADE;")

    # Types (after tables/functions that use them are dropped)
    op.execute("DROP TYPE IF EXISTS procrastinate_job_to_defer_v1 CASCADE;")
    op.execute("DROP TYPE IF EXISTS procrastinate_job_event_type CASCADE;")
    op.execute("DROP TYPE IF EXISTS procrastinate_job_status CASCADE;")
