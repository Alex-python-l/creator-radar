"""SQLAlchemy engine + session factory for the mkt persistence layer.

Schema is owned by Alembic (see alembic/). This module exposes:
- `engine` — SQLAlchemy engine (process-wide singleton)
- `get_session()` — FastAPI dependency yielding a Session
- `session_scope()` — context manager for non-request callers
  (scheduler jobs, scripts).

Imported by API routes (via Depends), services (taking Session params),
and the scheduler (using session_scope as a contextmanager).
"""

from contextlib import contextmanager
from typing import Iterator

from fastapi import HTTPException, Request
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.config import DATABASE_URL

engine: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=5,
    # Set echo=True during debugging to log all SQL
)


_WORKSPACE_ID_INFO_KEY = "studio_workspace_id"
_WORKSPACE_BYPASS_INFO_KEY = "studio_workspace_bypass"


def _apply_workspace_context(session: Session, connection) -> None:
    """Apply the session's tenant scope to the current DB transaction."""
    workspace_id = session.info.get(_WORKSPACE_ID_INFO_KEY)
    if workspace_id is not None:
        connection.execute(
            text("SELECT set_config('app.workspace_bypass', 'off', true)")
        )
        connection.execute(
            text("SELECT set_config('app.workspace_id', :value, true)"),
            {"value": str(workspace_id)},
        )
    elif session.info.get(_WORKSPACE_BYPASS_INFO_KEY):
        connection.execute(
            text("SELECT set_config('app.workspace_id', '', true)")
        )
        connection.execute(
            text("SELECT set_config('app.workspace_bypass', 'on', true)")
        )


@event.listens_for(Session, "after_begin")
def _restore_workspace_context(session, transaction, connection) -> None:
    """Reapply transaction-local RLS settings after commit or rollback."""
    _apply_workspace_context(session, connection)


def set_workspace_context(session: Session, workspace_id: int) -> None:
    """Narrow a session to one workspace, including future transactions."""
    if workspace_id <= 0:
        raise ValueError("workspace_id must be positive")
    session.info[_WORKSPACE_ID_INFO_KEY] = int(workspace_id)
    session.info.pop(_WORKSPACE_BYPASS_INFO_KEY, None)
    if session.in_transaction():
        _apply_workspace_context(session, session.connection())


def set_workspace_bypass(session: Session) -> None:
    """Allow a system session to scan tenants across transaction boundaries."""
    session.info[_WORKSPACE_BYPASS_INFO_KEY] = True
    session.info.pop(_WORKSPACE_ID_INFO_KEY, None)
    if session.in_transaction():
        _apply_workspace_context(session, session.connection())


def get_session(request: Request) -> Iterator[Session]:
    """Request session locked to the workspace resolved by require_auth."""
    workspace = getattr(request.state, "workspace", None)
    workspace_id = workspace.get("id") if isinstance(workspace, dict) else None
    if not isinstance(workspace_id, int) or workspace_id <= 0:
        raise HTTPException(status_code=503, detail="workspace context missing")
    with Session(engine) as session:
        set_workspace_context(session, workspace_id)
        yield session


def get_system_session() -> Iterator[Session]:
    """Internal unauthenticated endpoints such as signed callbacks only."""
    with Session(engine) as session:
        set_workspace_bypass(session)
        yield session


@contextmanager
def session_scope(workspace_id: int | None = None) -> Iterator[Session]:
    """For non-request callers (scheduler jobs, scripts).

    Same semantics as get_session but usable in a `with` block
    instead of `Depends`.
    """
    with Session(engine) as session:
        if workspace_id is not None:
            set_workspace_context(session, workspace_id)
        else:
            # Existing schedulers scan all tenants, then derived-row triggers
            # preserve the parent workspace. New jobs should pass workspace_id.
            set_workspace_bypass(session)
        yield session
