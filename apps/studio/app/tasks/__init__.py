"""Procrastinate App for the mkt worker.

Imported by:
- Worker entry point: `procrastinate -a app.tasks:procrastinate_app worker`
- API/route handlers that need to `defer()` tasks (added in Task 5)
- Test code (which replaces the connector with InMemoryConnector)

Periodic tasks and one-shot task functions are defined in sibling modules
listed in `import_paths`. The modules themselves are created in Tasks 4-5.
"""

import re

from procrastinate import App, PsycopgConnector

from app.config import DATABASE_URL

# Procrastinate uses libpq (via psycopg) directly, not SQLAlchemy, so strip
# the SQLAlchemy dialect prefix from DATABASE_URL.
_libpq_url = re.sub(r"^postgresql\+psycopg(?:\d*)?://", "postgresql://", DATABASE_URL)

# Pool must comfortably exceed worker --concurrency (=16 in prod). Default
# psycopg_pool sizes are min=4/max=4 — a single periodic burst (reclaim +
# auto_publish_scan all firing on the same tick) exhausts it, then finish_job
# can't persist status, jobs stay 'doing' forever, and the worker silently
# stalls (incident 2026-06-10, see
# memory/procrastinate-pool-exhaustion.md).
procrastinate_app = App(
    connector=PsycopgConnector(
        conninfo=_libpq_url,
        min_size=5,
        max_size=25,
    ),
    import_paths=[
        "app.tasks.warmup",
        "app.tasks.proxy_check",
        "app.tasks.publish",  # added in Task 5
        "app.tasks.auto_publish",
        "app.tasks.daily_warmup",
        "app.tasks.engagement",
        "app.tasks.backfill_thumbnails",
        "app.tasks.discover_tiktok",
        "app.tasks.idle_phone_sweeper",
        "app.tasks.overdue_session_watchdog",
        "app.tasks.sync_transactions",
        "app.tasks.writer_understanding",
        "app.tasks.geelark_callback",
        "app.tasks.outbox_dispatcher",
        "app.tasks.saga_stuck_logger",
        "app.tasks.queue_maintenance",
    ],
)
