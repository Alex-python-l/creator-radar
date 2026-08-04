#!/usr/bin/env python3
"""One-time data migration from SQLite to PostgreSQL.

Reads SQLite (default /opt/mkt/data/app.db), writes via SQLModel into
the configured DATABASE_URL (PG). Run on the production server
between `alembic upgrade head` and `systemctl restart mkt`:

  uv run python scripts/migrate_sqlite_to_pg.py

For local testing against a copy:
  DATABASE_URL=postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_dev \\
    uv run python scripts/migrate_sqlite_to_pg.py --sqlite=/tmp/prod-app.db

Tables migrated in FK order:
  groups -> accounts -> videos -> settings -> job_runs

Aborts on row-count mismatch.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

# Ensure the project root is on sys.path so `app` is importable
# whether the script is run as `uv run python scripts/...` or directly.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import text
from sqlmodel import Session

from app.db import engine
from app.models import Account, Group, JobRun, Setting, Video

DEFAULT_SQLITE_PATH = Path("/opt/mkt/data/app.db")

# (table_name, model_class) in FK order — parents before children
MODELS_IN_FK_ORDER: list[tuple[str, type]] = [
    ("groups", Group),
    ("accounts", Account),
    ("videos", Video),
    ("settings", Setting),
    ("job_runs", JobRun),
]


def _get_model_columns(model_cls: type) -> set[str]:
    """Return the set of column names defined on a SQLModel class."""
    return {c.name for c in model_cls.__table__.columns}


def _migrate_table(
    sconn: sqlite3.Connection,
    session: Session,
    table: str,
    model_cls: type,
) -> int:
    """Copy all rows from one SQLite table into the corresponding PG table.

    Only columns that exist in both source and target are copied; this
    keeps the migration safe against drift (e.g., a column that was
    dropped from the model but is still in SQLite).
    """
    rows = sconn.execute(f"SELECT * FROM {table}").fetchall()
    model_cols = _get_model_columns(model_cls)
    count = 0
    for row in rows:
        data: dict[str, Any] = {
            k: row[k] for k in row.keys() if k in model_cols
        }
        obj = model_cls(**data)
        session.add(obj)
        count += 1
    return count


def _verify_counts(
    sconn: sqlite3.Connection,
    session: Session,
) -> bool:
    """Verify that each table has the same row count in SQLite and PG.

    Returns True if all counts match, False otherwise.
    """
    print("\nVerification (SQLite count vs PG count):")
    all_match = True
    for table, _model in MODELS_IN_FK_ORDER:
        sqlite_n = sconn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        pg_n = session.exec(text(f"SELECT COUNT(*) FROM {table}")).first()[0]
        marker = "OK" if sqlite_n == pg_n else "MISMATCH"
        if sqlite_n != pg_n:
            all_match = False
        print(f"  {table}: sqlite={sqlite_n}  pg={pg_n}  [{marker}]")
    return all_match


def _reset_sequences(session: Session) -> None:
    """After copying rows with their original ids, advance PG sequences
    so future INSERTs don't collide with migrated ids.
    """
    print("\nResetting PG sequences...")
    for table, model_cls in MODELS_IN_FK_ORDER:
        if "id" not in _get_model_columns(model_cls):
            continue  # settings.key is primary key, no id sequence
        session.exec(text(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
        ))
    session.commit()


def migrate(sqlite_path: Path) -> int:
    """Run the migration. Returns 0 on success, non-zero on failure."""
    if not sqlite_path.exists():
        print(f"ERROR: SQLite file not found at {sqlite_path}")
        return 1

    print(f"Reading from SQLite: {sqlite_path}")
    print(f"Writing to PG: {engine.url}")
    print()

    sconn = sqlite3.connect(str(sqlite_path))
    sconn.row_factory = sqlite3.Row

    try:
        # Copy data — one Session per table so FK parents are committed
        # before children; mixing them in one session lets SQLAlchemy
        # reorder the flush and hit FK violations.
        for table, model_cls in MODELS_IN_FK_ORDER:
            with Session(engine) as session:
                count = _migrate_table(sconn, session, table, model_cls)
                session.commit()
            print(f"  {table}: {count} rows migrated")

        # Verify
        with Session(engine) as session:
            if not _verify_counts(sconn, session):
                print("\nABORT: row count mismatch")
                return 2

        # Reset sequences so future INSERTs don't collide
        with Session(engine) as session:
            _reset_sequences(session)

        print("\nMigration complete.")
        return 0
    finally:
        sconn.close()


def main() -> int:
    path = DEFAULT_SQLITE_PATH
    for arg in sys.argv[1:]:
        if arg.startswith("--sqlite="):
            path = Path(arg.split("=", 1)[1])
        elif arg in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print(f"unknown argument: {arg}")
            print(__doc__)
            return 1
    return migrate(path)


if __name__ == "__main__":
    sys.exit(main())
