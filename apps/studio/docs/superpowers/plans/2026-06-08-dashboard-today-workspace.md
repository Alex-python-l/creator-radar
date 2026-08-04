# Dashboard Today Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current Dashboard (5 stat cards + Recent Activity job log + Stop-All button) with a Today Workspace: 3 clickable glance-stat cards plus a single Exceptions inbox. Delete the Stop-All phones path end-to-end.

**Architecture:** Backend rewrite of `/api/dashboard` returns five regions — `today`, `exceptions`, `exceptions_total`, `accounts`, `videos` — each wrapped in `try/except` so a partial failure never 500s the whole response. All "today" boundaries are computed Python-side in `Asia/Shanghai` and passed as UTC naïve datetimes to SQL. Frontend `Dashboard.tsx` is rewritten to consume the new shape and use the existing `StatCard` (extended with an optional `onClick`) plus a new inline `ExceptionRow` component.

**Tech Stack:** FastAPI · SQLModel/SQLAlchemy · PostgreSQL 16 · React 19 · Vite · TailwindCSS · react-i18next · pytest

**Spec:** `docs/superpowers/specs/2026-06-08-dashboard-today-workspace-design.md`

---

## File Structure

**Backend**
- Rewrite: `app/api/dashboard.py` — new response shape, region helpers, error isolation
- Delete: `app/api/phones.py` — the only route was `/stop-all`
- Modify: `app/main.py` — drop `phones` from the router imports and `include_router` calls
- Modify: `app/services/geelark_service.py` — delete `stop_all_phones()` function (lines ~95–101)
- Create: `tests/test_api_dashboard.py` — replaces any old dashboard test, covers all regions + error isolation
- Delete: any existing `tests/test_*dashboard*` that asserts the old shape (none found at plan-writing time; verify in Task 12)

**Frontend**
- Modify: `frontend/src/components/ui.tsx` — add optional `onClick` to `StatCard`
- Modify: `frontend/src/api.ts` — delete `stopAll`, update `dashboard()` return type
- Rewrite: `frontend/src/pages/Dashboard.tsx` — new layout, polling preserved
- Modify: `frontend/src/i18n/locales/zh.json` — delete old `dashboard.*` keys, add new ones
- Modify: `frontend/src/i18n/locales/en.json` — mirror zh changes

---

## Task 1: Backend helper — today window in UTC

**Files:**
- Create: `app/api/dashboard.py` (will be expanded across Tasks 1–6; this task scaffolds the file)
- Create: `tests/test_api_dashboard.py`

- [ ] **Step 1: Write the failing test for `_today_window_utc()`**

Append to `tests/test_api_dashboard.py`:

```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.api.dashboard import _today_window_utc


def test_today_window_returns_naive_utc_datetimes():
    start, end = _today_window_utc()
    assert start.tzinfo is None
    assert end.tzinfo is None
    assert end - start == timedelta(days=1)


def test_today_window_aligns_to_shanghai_midnight():
    start, end = _today_window_utc()
    tz = ZoneInfo("Asia/Shanghai")
    start_local = start.replace(tzinfo=timezone.utc).astimezone(tz)
    end_local = end.replace(tzinfo=timezone.utc).astimezone(tz)
    assert start_local.hour == 0 and start_local.minute == 0
    assert end_local.hour == 0 and end_local.minute == 0
    assert end_local.date() == start_local.date() + timedelta(days=1)
```

- [ ] **Step 2: Run the test, expect import error**

Run: `uv run pytest tests/test_api_dashboard.py -v`
Expected: FAIL with `ImportError` because `_today_window_utc` doesn't exist yet.

- [ ] **Step 3: Replace `app/api/dashboard.py` with the new skeleton**

Overwrite `app/api/dashboard.py` (the old `dashboard()` route stays at the bottom for now — Tasks 2-6 build it back up):

```python
"""Today Workspace endpoint.

All "today" boundaries are computed in Asia/Shanghai local time.
Each region (today / exceptions / accounts / videos) is wrapped in
try/except so a partial DB failure returns a default value for that
region instead of 500-ing the entire endpoint.
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db import get_session

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard")

TZ = ZoneInfo("Asia/Shanghai")


def _today_window_utc() -> tuple[datetime, datetime]:
    """Return [start, end) of the local "today" as naive UTC datetimes
    compatible with the project's TIMESTAMP WITHOUT TIME ZONE columns."""
    now_local = datetime.now(TZ)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


@router.get("")
def dashboard(session: Session = Depends(get_session)):
    # Filled in across Tasks 2-6.
    return {
        "today": {"done": 0, "failed": 0, "in_progress": 0},
        "exceptions": [],
        "exceptions_total": 0,
        "accounts": {"total": 0, "ready": 0, "warming": 0, "not_started": 0},
        "videos": {"unscheduled": 0, "total": 0},
    }
```

- [ ] **Step 4: Run the test, expect pass**

Run: `uv run pytest tests/test_api_dashboard.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/api/dashboard.py tests/test_api_dashboard.py
git commit -m "feat(dashboard): scaffold today workspace endpoint with TZ-aware window helper"
```

---

## Task 2: Backend — today counts (done / failed / in_progress)

**Files:**
- Modify: `app/api/dashboard.py`
- Modify: `tests/test_api_dashboard.py`

- [ ] **Step 1: Write failing tests for `_today_counts`**

Append to `tests/test_api_dashboard.py`:

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.api.dashboard import _today_counts, _today_window_utc
from app.models import Account, Group, Publication, Video


def _seed_video(db: Session, group_id: int) -> int:
    v = Video(original_filename="x.mp4", stored_path="x", group_id=group_id)
    db.add(v); db.commit(); db.refresh(v)
    return v.id


def _seed_group(db: Session) -> int:
    g = Group(name="G1"); db.add(g); db.commit(); db.refresh(g)
    return g.id


def _seed_pub(db: Session, video_id: int, *, result: str,
              posted_at: datetime | None = None,
              last_checked_at: datetime | None = None,
              platform: str = "tiktok") -> int:
    p = Publication(video_id=video_id, platform=platform, result=result,
                    posted_at=posted_at, last_checked_at=last_checked_at)
    db.add(p); db.commit(); db.refresh(p)
    return p.id


def test_today_done_counts_only_local_today(db):
    g = _seed_group(db)
    v1 = _seed_video(db, g); v2 = _seed_video(db, g); v3 = _seed_video(db, g)
    start, end = _today_window_utc()
    one_hour = timedelta(hours=1)
    _seed_pub(db, v1, result="SUCCESS", posted_at=start - one_hour, platform="tiktok")
    _seed_pub(db, v2, result="SUCCESS", posted_at=start + one_hour, platform="tiktok")
    _seed_pub(db, v3, result="SUCCESS", posted_at=end + one_hour, platform="tiktok")
    counts = _today_counts(db)
    assert counts == {"done": 1, "failed": 0, "in_progress": 0}


def test_today_failed_counts_uses_last_checked_at(db):
    g = _seed_group(db)
    v1 = _seed_video(db, g); v2 = _seed_video(db, g)
    start, _ = _today_window_utc()
    _seed_pub(db, v1, result="FAILURE",
              last_checked_at=start + timedelta(hours=2), platform="tiktok")
    _seed_pub(db, v2, result="FAILURE",
              last_checked_at=start - timedelta(hours=2), platform="tiktok")
    counts = _today_counts(db)
    assert counts == {"done": 0, "failed": 1, "in_progress": 0}


def test_today_in_progress_has_no_date_filter(db):
    g = _seed_group(db)
    v1 = _seed_video(db, g); v2 = _seed_video(db, g); v3 = _seed_video(db, g)
    _seed_pub(db, v1, result="QUEUED", platform="tiktok")
    _seed_pub(db, v2, result="INITIATED", platform="tiktok")
    _seed_pub(db, v3, result="PENDING", platform="tiktok")
    counts = _today_counts(db)
    assert counts == {"done": 0, "failed": 0, "in_progress": 2}
```

- [ ] **Step 2: Run the new tests, expect failure**

Run: `uv run pytest tests/test_api_dashboard.py -v`
Expected: `ImportError` on `_today_counts`.

- [ ] **Step 3: Implement `_today_counts`**

Add to `app/api/dashboard.py` above the `@router.get("")` line:

```python
import sqlalchemy as sa


def _today_counts(session: Session) -> dict:
    start, end = _today_window_utc()
    done = session.execute(sa.text("""
        SELECT COUNT(*) FROM publications
        WHERE result = 'SUCCESS'
          AND posted_at >= :start AND posted_at < :end
    """), {"start": start, "end": end}).scalar() or 0
    failed = session.execute(sa.text("""
        SELECT COUNT(*) FROM publications
        WHERE result = 'FAILURE'
          AND last_checked_at >= :start AND last_checked_at < :end
    """), {"start": start, "end": end}).scalar() or 0
    in_progress = session.execute(sa.text("""
        SELECT COUNT(*) FROM publications
        WHERE result IN ('QUEUED', 'INITIATED')
    """)).scalar() or 0
    return {"done": int(done), "failed": int(failed),
            "in_progress": int(in_progress)}
```

Wire it into the route by replacing the `today` line in the handler:

```python
@router.get("")
def dashboard(session: Session = Depends(get_session)):
    try:
        today = _today_counts(session)
    except Exception:
        log.exception("dashboard.today_counts failed")
        today = {"done": 0, "failed": 0, "in_progress": 0}
    return {
        "today": today,
        "exceptions": [],
        "exceptions_total": 0,
        "accounts": {"total": 0, "ready": 0, "warming": 0, "not_started": 0},
        "videos": {"unscheduled": 0, "total": 0},
    }
```

- [ ] **Step 4: Run the tests, expect pass**

Run: `uv run pytest tests/test_api_dashboard.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/api/dashboard.py tests/test_api_dashboard.py
git commit -m "feat(dashboard): today counts in Asia/Shanghai window"
```

---

## Task 3: Backend — account counts (total / ready / warming / not_started)

**Files:**
- Modify: `app/api/dashboard.py`
- Modify: `tests/test_api_dashboard.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_dashboard.py`:

```python
from app.api.dashboard import _account_counts


def _seed_account(db: Session, *, name: str, warmed_up: int = 0,
                  warmup_count: int = 0, target: int = 10,
                  phone_id: str | None = None) -> int:
    a = Account(profile_name=name, warmed_up=warmed_up,
                warmup_count=warmup_count, target_warmup_count=target,
                geelark_phone_id=phone_id)
    db.add(a); db.commit(); db.refresh(a)
    return a.id


def test_account_counts_partition(db):
    _seed_account(db, name="ready1", warmed_up=1, warmup_count=10)
    _seed_account(db, name="ready2", warmed_up=1, warmup_count=10)
    _seed_account(db, name="ready3", warmed_up=1, warmup_count=10)
    _seed_account(db, name="warm1", warmed_up=0, warmup_count=3)
    _seed_account(db, name="warm2", warmed_up=0, warmup_count=5)
    _seed_account(db, name="cold1", warmed_up=0, warmup_count=0)
    _seed_account(db, name="cold2", warmed_up=0, warmup_count=0)
    counts = _account_counts(db)
    assert counts == {"total": 7, "ready": 3, "warming": 2, "not_started": 2}
```

- [ ] **Step 2: Run the test, expect failure**

Run: `uv run pytest tests/test_api_dashboard.py::test_account_counts_partition -v`
Expected: `ImportError` on `_account_counts`.

- [ ] **Step 3: Implement `_account_counts` and wire it into the route**

Add the helper:

```python
def _account_counts(session: Session) -> dict:
    row = session.execute(sa.text("""
        SELECT
          COUNT(*) AS total,
          COUNT(*) FILTER (WHERE warmed_up = 1) AS ready,
          COUNT(*) FILTER (WHERE warmed_up = 0 AND warmup_count > 0) AS warming
        FROM accounts
    """)).first()
    total = int(row.total or 0)
    ready = int(row.ready or 0)
    warming = int(row.warming or 0)
    not_started = max(total - ready - warming, 0)
    return {"total": total, "ready": ready, "warming": warming,
            "not_started": not_started}
```

Update the handler:

```python
    try:
        accounts = _account_counts(session)
    except Exception:
        log.exception("dashboard.account_counts failed")
        accounts = {"total": 0, "ready": 0, "warming": 0, "not_started": 0}
```

And replace `"accounts": {...}` with `"accounts": accounts` in the return dict.

- [ ] **Step 4: Run the tests, expect pass**

Run: `uv run pytest tests/test_api_dashboard.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/api/dashboard.py tests/test_api_dashboard.py
git commit -m "feat(dashboard): account readiness partition"
```

---

## Task 4: Backend — video counts (unscheduled / total)

**Files:**
- Modify: `app/api/dashboard.py`
- Modify: `tests/test_api_dashboard.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_dashboard.py`:

```python
from app.api.dashboard import _video_counts


def test_video_counts_unscheduled_excludes_videos_with_any_publication(db):
    g = _seed_group(db)
    v_scheduled = _seed_video(db, g)
    v_unscheduled_1 = _seed_video(db, g)
    v_unscheduled_2 = _seed_video(db, g)
    _seed_pub(db, v_scheduled, result="PENDING", platform="tiktok")
    counts = _video_counts(db)
    assert counts == {"unscheduled": 2, "total": 3}
    _ = (v_unscheduled_1, v_unscheduled_2)  # suppress unused
```

- [ ] **Step 2: Run the test, expect failure**

Run: `uv run pytest tests/test_api_dashboard.py::test_video_counts_unscheduled_excludes_videos_with_any_publication -v`
Expected: `ImportError` on `_video_counts`.

- [ ] **Step 3: Implement `_video_counts` and wire it into the route**

```python
def _video_counts(session: Session) -> dict:
    total = session.execute(sa.text("SELECT COUNT(*) FROM videos")).scalar() or 0
    unscheduled = session.execute(sa.text("""
        SELECT COUNT(*) FROM videos v
        WHERE NOT EXISTS (
          SELECT 1 FROM publications p WHERE p.video_id = v.id
        )
    """)).scalar() or 0
    return {"unscheduled": int(unscheduled), "total": int(total)}
```

Handler update:

```python
    try:
        videos = _video_counts(session)
    except Exception:
        log.exception("dashboard.video_counts failed")
        videos = {"unscheduled": 0, "total": 0}
```

Replace `"videos": {...}` with `"videos": videos`.

- [ ] **Step 4: Run the tests, expect pass**

Run: `uv run pytest tests/test_api_dashboard.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/api/dashboard.py tests/test_api_dashboard.py
git commit -m "feat(dashboard): video library counts"
```

---

## Task 5: Backend — exceptions (4 kinds, severity ordered, capped at 5)

**Files:**
- Modify: `app/api/dashboard.py`
- Modify: `tests/test_api_dashboard.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_dashboard.py`:

```python
from app.api.dashboard import _exceptions


def test_exception_publication_failed_row(db):
    g = _seed_group(db)
    a = _seed_account(db, name="acc1", phone_id="ph1")
    v = _seed_video(db, g)
    pub = Publication(video_id=v, platform="tiktok", result="FAILURE",
                      account_id=a, notes="captcha required",
                      last_checked_at=datetime.utcnow())
    db.add(pub); db.commit(); db.refresh(pub)
    out = _exceptions(db)
    exc = next(e for e in out["items"] if e["kind"] == "publication_failed")
    assert exc["severity"] == "red"
    assert exc["publication_id"] == pub.id
    assert exc["account"] == "acc1"
    assert exc["platform"] == "tiktok"
    assert exc["reason"] == "captcha required"
    assert out["total"] >= 1


def test_exception_proxy_down_only_when_status_fail(db):
    a_fail = Account(profile_name="bad", proxy_status="fail",
                     proxy_checked_at=datetime.utcnow())
    a_ok = Account(profile_name="good", proxy_status="ok",
                   proxy_checked_at=datetime.utcnow())
    a_null = Account(profile_name="never_checked", proxy_status=None)
    db.add_all([a_fail, a_ok, a_null]); db.commit()
    out = _exceptions(db)
    kinds = [e["kind"] for e in out["items"]]
    profiles = [e["account"] for e in out["items"] if e["kind"] == "proxy_down"]
    assert "proxy_down" in kinds
    assert profiles == ["bad"]


def test_exception_not_logged_in_requires_phone_id(db):
    a_phone_no_login = Account(profile_name="needs_login",
                               geelark_phone_id="ph1",
                               tiktok_logged_in=0)
    a_no_phone = Account(profile_name="no_phone",
                         geelark_phone_id=None,
                         tiktok_logged_in=0)
    a_logged = Account(profile_name="ok",
                       geelark_phone_id="ph2",
                       tiktok_logged_in=1)
    db.add_all([a_phone_no_login, a_no_phone, a_logged]); db.commit()
    out = _exceptions(db)
    profiles = [e["account"] for e in out["items"] if e["kind"] == "not_logged_in"]
    assert profiles == ["needs_login"]


def test_exception_warmup_stuck_requires_24h_since_last_warm(db):
    now = datetime.utcnow()
    a_stuck = Account(profile_name="stuck", warmed_up=0, warmup_count=2,
                      target_warmup_count=10,
                      last_warm_at=now - timedelta(hours=25))
    a_recent = Account(profile_name="recent", warmed_up=0, warmup_count=2,
                       target_warmup_count=10,
                       last_warm_at=now - timedelta(hours=10))
    a_done = Account(profile_name="done", warmed_up=1, warmup_count=10,
                     target_warmup_count=10,
                     last_warm_at=now - timedelta(hours=30))
    a_never = Account(profile_name="never", warmed_up=0, warmup_count=0,
                      target_warmup_count=10, last_warm_at=None)
    db.add_all([a_stuck, a_recent, a_done, a_never]); db.commit()
    out = _exceptions(db)
    profiles = [e["account"] for e in out["items"]
                if e["kind"] == "warmup_stuck"]
    assert profiles == ["stuck"]


def test_exception_severity_order_and_cap(db):
    g = _seed_group(db)
    now = datetime.utcnow()
    # 2 reds
    for i in range(2):
        v = _seed_video(db, g)
        db.add(Publication(video_id=v, platform="tiktok", result="FAILURE",
                           notes=f"red{i}", last_checked_at=now))
    # 2 oranges (proxy_down)
    for i in range(2):
        db.add(Account(profile_name=f"orange{i}", proxy_status="fail",
                       proxy_checked_at=now))
    # 2 yellows
    for i in range(2):
        db.add(Account(profile_name=f"yellow{i}", warmed_up=0, warmup_count=1,
                       target_warmup_count=10,
                       last_warm_at=now - timedelta(hours=30)))
    db.commit()
    out = _exceptions(db)
    severities = [e["severity"] for e in out["items"]]
    assert severities == ["red", "red", "orange", "orange", "yellow"]
    assert len(out["items"]) == 5
    assert out["total"] == 6
```

- [ ] **Step 2: Run the tests, expect failure**

Run: `uv run pytest tests/test_api_dashboard.py -v`
Expected: `ImportError` on `_exceptions`.

- [ ] **Step 3: Implement `_exceptions`**

Add to `app/api/dashboard.py`:

```python
_SEVERITY_RANK = {"red": 0, "orange": 1, "yellow": 2}


def _exceptions(session: Session) -> dict:
    """Return {"items": [...], "total": N}.

    Each kind contributes its full COUNT to `total` and at most 5 candidate
    rows to a merged pool. The merged pool is sorted by severity then by
    each row's own ordering timestamp DESC, then sliced to 5.
    """
    items: list[dict] = []
    total = 0

    # publication_failed
    total += int(session.execute(sa.text("""
        SELECT COUNT(*) FROM publications WHERE result = 'FAILURE'
    """)).scalar() or 0)
    for row in session.execute(sa.text("""
        SELECT p.id AS publication_id, p.platform, p.notes,
               p.last_checked_at AS ts,
               a.profile_name AS account, a.id AS account_id,
               v.original_filename AS video
        FROM publications p
        LEFT JOIN accounts a ON a.id = p.account_id
        LEFT JOIN videos v ON v.id = p.video_id
        WHERE p.result = 'FAILURE'
        ORDER BY p.last_checked_at DESC NULLS LAST
        LIMIT 5
    """)).mappings().all():
        items.append({
            "kind": "publication_failed",
            "severity": "red",
            "publication_id": row["publication_id"],
            "account": row["account"] or "—",
            "account_id": row["account_id"],
            "video": row["video"] or "—",
            "platform": row["platform"],
            "reason": row["notes"] or "",
            "_ts": row["ts"],
        })

    # proxy_down
    total += int(session.execute(sa.text("""
        SELECT COUNT(*) FROM accounts
        WHERE proxy_status = 'fail' AND proxy_checked_at IS NOT NULL
    """)).scalar() or 0)
    now = datetime.utcnow()
    for row in session.execute(sa.text("""
        SELECT id AS account_id, profile_name AS account,
               proxy_checked_at AS ts
        FROM accounts
        WHERE proxy_status = 'fail' AND proxy_checked_at IS NOT NULL
        ORDER BY proxy_checked_at DESC
        LIMIT 5
    """)).mappings().all():
        hours = int(max(0, (now - row["ts"]).total_seconds() // 3600))
        items.append({
            "kind": "proxy_down",
            "severity": "orange",
            "account_id": row["account_id"],
            "account": row["account"] or "—",
            "hours_since": hours,
            "_ts": row["ts"],
        })

    # not_logged_in (TikTok-only for v1; see spec Non-Goals)
    total += int(session.execute(sa.text("""
        SELECT COUNT(*) FROM accounts
        WHERE geelark_phone_id IS NOT NULL AND tiktok_logged_in = 0
    """)).scalar() or 0)
    for row in session.execute(sa.text("""
        SELECT id AS account_id, profile_name AS account,
               updated_at AS ts
        FROM accounts
        WHERE geelark_phone_id IS NOT NULL AND tiktok_logged_in = 0
        ORDER BY updated_at DESC
        LIMIT 5
    """)).mappings().all():
        items.append({
            "kind": "not_logged_in",
            "severity": "orange",
            "account_id": row["account_id"],
            "account": row["account"] or "—",
            "platform": "tiktok",
            "_ts": row["ts"],
        })

    # warmup_stuck
    cutoff = now - timedelta(hours=24)
    total += int(session.execute(sa.text("""
        SELECT COUNT(*) FROM accounts
        WHERE warmed_up = 0
          AND warmup_count < target_warmup_count
          AND last_warm_at IS NOT NULL
          AND last_warm_at < :cutoff
    """), {"cutoff": cutoff}).scalar() or 0)
    for row in session.execute(sa.text("""
        SELECT id AS account_id, profile_name AS account,
               last_warm_at AS ts
        FROM accounts
        WHERE warmed_up = 0
          AND warmup_count < target_warmup_count
          AND last_warm_at IS NOT NULL
          AND last_warm_at < :cutoff
        ORDER BY last_warm_at DESC
        LIMIT 5
    """), {"cutoff": cutoff}).mappings().all():
        hours = int(max(0, (now - row["ts"]).total_seconds() // 3600))
        items.append({
            "kind": "warmup_stuck",
            "severity": "yellow",
            "account_id": row["account_id"],
            "account": row["account"] or "—",
            "hours_since": hours,
            "_ts": row["ts"],
        })

    items.sort(key=lambda e: (
        _SEVERITY_RANK[e["severity"]],
        # newer-first within severity; None sinks to the bottom
        -(e["_ts"].timestamp() if e.get("_ts") else 0),
    ))
    sliced = []
    for e in items[:5]:
        e.pop("_ts", None)
        sliced.append(e)

    return {"items": sliced, "total": total}
```

Wire into handler:

```python
    try:
        exc = _exceptions(session)
        exceptions = exc["items"]
        exceptions_total = exc["total"]
    except Exception:
        log.exception("dashboard.exceptions failed")
        exceptions = []
        exceptions_total = 0
```

Replace the corresponding `"exceptions": ..., "exceptions_total": ...` lines in the return dict to use these variables.

- [ ] **Step 4: Run the tests, expect pass**

Run: `uv run pytest tests/test_api_dashboard.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add app/api/dashboard.py tests/test_api_dashboard.py
git commit -m "feat(dashboard): exceptions inbox across 4 kinds"
```

---

## Task 6: Backend — endpoint integration test + partial-failure isolation

**Files:**
- Modify: `tests/test_api_dashboard.py`

- [ ] **Step 1: Write failing integration tests**

Append to `tests/test_api_dashboard.py`:

```python
def test_dashboard_endpoint_returns_full_shape(client):
    client.post("/api/login", json={"password": "pw"})
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "today", "exceptions", "exceptions_total", "accounts", "videos"
    }
    assert set(body["today"].keys()) == {"done", "failed", "in_progress"}
    assert set(body["accounts"].keys()) == {
        "total", "ready", "warming", "not_started"
    }
    assert set(body["videos"].keys()) == {"unscheduled", "total"}


def test_dashboard_partial_failure_isolates_to_one_region(client, monkeypatch):
    client.post("/api/login", json={"password": "pw"})
    from app.api import dashboard as dash_mod

    def boom(_session):
        raise RuntimeError("simulated")

    monkeypatch.setattr(dash_mod, "_exceptions", boom)
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["exceptions"] == []
    assert body["exceptions_total"] == 0
    # Other regions still computed
    assert body["today"] == {"done": 0, "failed": 0, "in_progress": 0}
    assert body["accounts"]["total"] == 0
```

- [ ] **Step 2: Run the tests, expect pass (logic already in place from Tasks 1–5)**

Run: `uv run pytest tests/test_api_dashboard.py -v`
Expected: 14 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_api_dashboard.py
git commit -m "test(dashboard): integration shape + partial-failure isolation"
```

---

## Task 7: Backend — delete stop-all phones path

**Files:**
- Delete: `app/api/phones.py`
- Modify: `app/main.py:25,30`
- Modify: `app/services/geelark_service.py` (delete `stop_all_phones`)
- Modify: `tests/test_api_dashboard.py` (add 404 assertion)

- [ ] **Step 1: Write failing test that proves the endpoint is gone**

Append to `tests/test_api_dashboard.py`:

```python
def test_phones_stop_all_endpoint_is_removed(client):
    client.post("/api/login", json={"password": "pw"})
    r = client.post("/api/phones/stop-all")
    assert r.status_code == 404
```

- [ ] **Step 2: Run the test, expect failure**

Run: `uv run pytest tests/test_api_dashboard.py::test_phones_stop_all_endpoint_is_removed -v`
Expected: FAIL with `assert 200 == 404` (the route still exists).

- [ ] **Step 3: Delete `app/api/phones.py`**

```bash
rm app/api/phones.py
```

- [ ] **Step 4: Update `app/main.py`**

Edit line 25 — remove `phones` from the import:

```python
from app.api import accounts, videos, dashboard, settings, groups, engagement
```

Delete the `include_router` line (was line 30):

```python
# Delete the entire line: app.include_router(phones.router, dependencies=[Depends(require_auth)])
```

- [ ] **Step 5: Delete `stop_all_phones` from `app/services/geelark_service.py`**

Remove the function body (lines ~95–101):

```python
# DELETE this block:
def stop_all_phones(session: Session, client) -> dict:
    ids = [a["geelark_phone_id"] for a in acc_repo.list_all(session)
           if a.get("geelark_phone_id")]
    if not ids:
        return {"stopped": 0}
    client.stop_phones(ids)
    return {"stopped": len(ids)}
```

- [ ] **Step 6: Run the full dashboard test file**

Run: `uv run pytest tests/test_api_dashboard.py -v`
Expected: 15 passed.

- [ ] **Step 7: Run the whole suite to confirm no other callers broke**

Run: `uv run pytest -q`
Expected: ≥ 386 passed (baseline 385 + new dashboard tests minus old dashboard tests if any). No failures.

If a test fails because it references `stop_all_phones`, delete or rewrite it.

- [ ] **Step 8: Commit**

```bash
git add -u app/api/phones.py app/main.py app/services/geelark_service.py tests/test_api_dashboard.py
git commit -m "refactor(dashboard): remove stop-all phones path end-to-end"
```

---

## Task 8: Frontend — `StatCard` gains optional `onClick`

**Files:**
- Modify: `frontend/src/components/ui.tsx`

- [ ] **Step 1: Update the `StatCard` signature and rendering**

In `frontend/src/components/ui.tsx`, find the `StatCard` function (line ~164) and replace it with:

```tsx
export function StatCard({
  label,
  value,
  caption,
  tone,
  onClick,
}: {
  label: string
  value: number | string
  caption?: ReactNode
  tone?: 'blue' | 'green' | 'orange' | 'red' | 'gray'
  onClick?: () => void
}) {
  const valueColor =
    tone === 'blue'
      ? 'text-[var(--color-blue)]'
      : tone === 'green'
        ? 'text-[var(--color-green)]'
        : tone === 'orange'
          ? 'text-[var(--color-orange)]'
          : tone === 'red'
            ? 'text-[var(--color-red)]'
            : 'text-[var(--color-ink)]'
  const interactive = onClick != null
  const Wrapper: 'button' | 'div' = interactive ? 'button' : 'div'
  return (
    <Wrapper
      onClick={onClick}
      type={interactive ? 'button' : undefined}
      className={cn(
        'px-5 py-4 min-w-[170px] flex-1 rounded-[var(--radius-card)]',
        'border border-[var(--color-line)] bg-[var(--color-surface)]',
        'text-left',
        interactive &&
          'cursor-pointer transition-colors hover:bg-[var(--color-surface-hover)]',
      )}
    >
      <div className="text-[12.5px] text-[var(--color-ink-3)] font-medium">{label}</div>
      <div
        className={cn(
          'mt-2.5 text-[34px] leading-none font-semibold tracking-[-0.02em] tabular-nums',
          valueColor,
        )}
      >
        {value}
      </div>
      {caption ? (
        <div className="mt-2 text-[12.5px] text-[var(--color-ink-3)]">{caption}</div>
      ) : null}
    </Wrapper>
  )
}
```

Note: this replaces the original `<Card …>` wrapper. Match the `Card` primitive's classes (`rounded-[var(--radius-card)] border bg-surface`) verbatim — read the `Card` definition in the same file and align. If your repo's `Card` adds box-shadow or other classes, copy those too.

- [ ] **Step 2: Verify no consumer breaks**

Run: `cd frontend && export PATH="/opt/homebrew/bin:$PATH" && npm run build`
Expected: clean build.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ui.tsx
git commit -m "feat(ui): StatCard supports optional onClick"
```

---

## Task 9: Frontend — `api.ts` drops `stopAll`, updates `dashboard()` type

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Drop `stopAll`**

In `frontend/src/api.ts`, delete the line:

```ts
stopAll: () => req('/phones/stop-all', { method: 'POST' }),
```

- [ ] **Step 2: Add the new Dashboard type and update `dashboard()` signature**

If `api.ts` exports typed wrappers, add (near the top of the file or alongside other types):

```ts
export type DashboardException =
  | {
      kind: 'publication_failed'
      severity: 'red'
      publication_id: number
      account: string
      account_id: number | null
      video: string
      platform: string
      reason: string
    }
  | {
      kind: 'proxy_down'
      severity: 'orange'
      account_id: number
      account: string
      hours_since: number
    }
  | {
      kind: 'not_logged_in'
      severity: 'orange'
      account_id: number
      account: string
      platform: string
    }
  | {
      kind: 'warmup_stuck'
      severity: 'yellow'
      account_id: number
      account: string
      hours_since: number
    }

export type DashboardData = {
  today: { done: number; failed: number; in_progress: number }
  exceptions: DashboardException[]
  exceptions_total: number
  accounts: { total: number; ready: number; warming: number; not_started: number }
  videos: { unscheduled: number; total: number }
}
```

Update the wrapper signature:

```ts
  dashboard: (): Promise<DashboardData> => req('/dashboard'),
```

- [ ] **Step 3: Confirm App.tsx / Dashboard.tsx still build (they will be rewritten in Task 11; current build may surface old type errors — that's expected and resolved by Task 11). Skip a full build until Task 11.**

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api.ts
git commit -m "refactor(api): drop stopAll, type dashboard() response"
```

---

## Task 10: Frontend — i18n delete old + add new keys

**Files:**
- Modify: `frontend/src/i18n/locales/zh.json`
- Modify: `frontend/src/i18n/locales/en.json`

- [ ] **Step 1: Replace the `dashboard` block in `zh.json`**

In `frontend/src/i18n/locales/zh.json`, find the `"dashboard": { ... }` block (lines 91–133) and replace it with:

```json
  "dashboard": {
    "title": "今日工作台",
    "subtitle": "{{date}} · Asia/Shanghai",
    "stat": {
      "todayDone": "已发成功",
      "todayCaption": "失败 {{failed}} · 进行中 {{in_progress}}",
      "todayCaptionEmpty": "今日无失败 · 队列空闲",
      "accountsReady": "已准备好",
      "accountsValue": "{{ready}} / {{total}}",
      "accountsCaption": "养号 {{warming}} · 未启 {{not_started}}",
      "videosUnscheduled": "未排期视频",
      "videosCaption": "共 {{total}}"
    },
    "exceptions": {
      "title": "异常需处理",
      "viewAll": "查看全部 →",
      "allQuietTitle": "All quiet",
      "allQuietBody": "系统目前一切正常"
    },
    "exception": {
      "kind": {
        "publicationFailed": "发布失败",
        "proxyDown": "代理挂掉",
        "notLoggedIn": "未登录",
        "warmupStuck": "养号卡住"
      },
      "action": {
        "go": "去查看 →"
      },
      "proxyDownContext": "{{hours}}h 前掉线",
      "warmupStuckContext": "{{hours}}h 没新进度"
    }
  },
```

- [ ] **Step 2: Replace the `dashboard` block in `en.json`**

```json
  "dashboard": {
    "title": "Today",
    "subtitle": "{{date}} · Asia/Shanghai",
    "stat": {
      "todayDone": "Posted today",
      "todayCaption": "Failed {{failed}} · In progress {{in_progress}}",
      "todayCaptionEmpty": "No failures today · Queue idle",
      "accountsReady": "Ready to post",
      "accountsValue": "{{ready}} / {{total}}",
      "accountsCaption": "Warming {{warming}} · Not started {{not_started}}",
      "videosUnscheduled": "Unscheduled videos",
      "videosCaption": "{{total}} total"
    },
    "exceptions": {
      "title": "Needs attention",
      "viewAll": "View all →",
      "allQuietTitle": "All quiet",
      "allQuietBody": "Everything is running smoothly"
    },
    "exception": {
      "kind": {
        "publicationFailed": "Publish failed",
        "proxyDown": "Proxy down",
        "notLoggedIn": "Not logged in",
        "warmupStuck": "Warmup stuck"
      },
      "action": {
        "go": "Open →"
      },
      "proxyDownContext": "{{hours}}h since last check",
      "warmupStuckContext": "{{hours}}h since last progress"
    }
  },
```

- [ ] **Step 3: Sanity-check JSON validity**

Run: `node -e "JSON.parse(require('fs').readFileSync('frontend/src/i18n/locales/zh.json'))" && node -e "JSON.parse(require('fs').readFileSync('frontend/src/i18n/locales/en.json'))"`
Expected: silent (no syntax error).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n/locales/zh.json frontend/src/i18n/locales/en.json
git commit -m "i18n(dashboard): replace stat/activity keys with today workspace keys"
```

---

## Task 11: Frontend — rewrite `Dashboard.tsx`

**Files:**
- Rewrite: `frontend/src/pages/Dashboard.tsx`

- [ ] **Step 1: Overwrite the file**

Replace the entire contents of `frontend/src/pages/Dashboard.tsx` with:

```tsx
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { api, type DashboardData, type DashboardException } from '../api'
import {
  Card,
  EmptyState,
  PageTitle,
  SectionTitle,
  StatCard,
} from '../components/ui'

const SEVERITY_DOT: Record<DashboardException['severity'], string> = {
  red: 'bg-[var(--color-red)]',
  orange: 'bg-[var(--color-orange)]',
  yellow: 'bg-[var(--color-yellow)]',
}

const KIND_LABEL_KEY: Record<DashboardException['kind'], string> = {
  publication_failed: 'dashboard.exception.kind.publicationFailed',
  proxy_down: 'dashboard.exception.kind.proxyDown',
  not_logged_in: 'dashboard.exception.kind.notLoggedIn',
  warmup_stuck: 'dashboard.exception.kind.warmupStuck',
}

const KIND_TARGET: Record<DashboardException['kind'], string> = {
  publication_failed: '/videos',
  proxy_down: '/accounts',
  not_logged_in: '/accounts',
  warmup_stuck: '/accounts',
}

function formatLocalDate(): string {
  // YYYY-MM-DD in Asia/Shanghai
  const now = new Date()
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  return fmt.format(now)
}

export default function Dashboard() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [d, setD] = useState<DashboardData | null>(null)

  const load = () =>
    api
      .dashboard()
      .then(setD)
      .catch(() => {})
  useEffect(() => {
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [])

  if (!d) {
    return <p className="text-[13px] text-[var(--color-ink-3)]">{t('common.loading')}</p>
  }

  const todayCaption =
    d.today.failed === 0 && d.today.in_progress === 0
      ? t('dashboard.stat.todayCaptionEmpty')
      : t('dashboard.stat.todayCaption', {
          failed: d.today.failed,
          in_progress: d.today.in_progress,
        })

  return (
    <div className="flex flex-col gap-9">
      <PageTitle
        title={t('dashboard.title')}
        subtitle={t('dashboard.subtitle', { date: formatLocalDate() })}
      />

      <div className="flex flex-wrap gap-4">
        <StatCard
          label={t('dashboard.stat.todayDone')}
          value={d.today.done}
          caption={todayCaption}
          onClick={() => navigate('/videos')}
        />
        <StatCard
          label={t('dashboard.stat.accountsReady')}
          value={t('dashboard.stat.accountsValue', {
            ready: d.accounts.ready,
            total: d.accounts.total,
          })}
          caption={t('dashboard.stat.accountsCaption', {
            warming: d.accounts.warming,
            not_started: d.accounts.not_started,
          })}
          onClick={() => navigate('/accounts')}
        />
        <StatCard
          label={t('dashboard.stat.videosUnscheduled')}
          value={d.videos.unscheduled}
          caption={t('dashboard.stat.videosCaption', { total: d.videos.total })}
          onClick={() => navigate('/videos')}
        />
      </div>

      <section>
        <div className="flex items-center justify-between mb-3">
          <SectionTitle>{`${t('dashboard.exceptions.title')} (${d.exceptions_total})`}</SectionTitle>
          {d.exceptions_total > d.exceptions.length ? (
            <button
              type="button"
              className="text-[12.5px] text-[var(--color-ink-2)] hover:text-[var(--color-ink)]"
              onClick={() => navigate('/videos')}
            >
              {t('dashboard.exceptions.viewAll')}
            </button>
          ) : null}
        </div>
        <Card>
          {d.exceptions.length === 0 ? (
            <EmptyState
              title={t('dashboard.exceptions.allQuietTitle')}
              children={t('dashboard.exceptions.allQuietBody')}
            />
          ) : (
            <ul className="divide-y divide-[var(--color-line)]">
              {d.exceptions.map((e, i) => (
                <ExceptionRow key={i} e={e} onGo={() => navigate(KIND_TARGET[e.kind])} />
              ))}
            </ul>
          )}
        </Card>
      </section>
    </div>
  )
}

function ExceptionRow({
  e,
  onGo,
}: {
  e: DashboardException
  onGo: () => void
}) {
  const { t } = useTranslation()
  const kindLabel = t(KIND_LABEL_KEY[e.kind])

  let metaTail: string | null = null
  if (e.kind === 'publication_failed') {
    metaTail = `${e.video} · ${e.platform}`
  } else if (e.kind === 'proxy_down') {
    metaTail = t('dashboard.exception.proxyDownContext', { hours: e.hours_since })
  } else if (e.kind === 'not_logged_in') {
    metaTail = e.platform
  } else if (e.kind === 'warmup_stuck') {
    metaTail = t('dashboard.exception.warmupStuckContext', { hours: e.hours_since })
  }

  const reason = e.kind === 'publication_failed' ? e.reason : null

  return (
    <li className="flex items-start gap-4 px-5 py-3 hover:bg-[var(--color-surface-hover)] transition-colors">
      <span
        className={`mt-1.5 inline-block w-2 h-2 rounded-full shrink-0 ${SEVERITY_DOT[e.severity]}`}
      />
      <div className="flex-1 min-w-0">
        <div className="text-[13.5px] text-[var(--color-ink)] truncate">
          <span className="font-medium">{kindLabel}</span>
          <span className="text-[var(--color-ink-3)]"> · {e.account}</span>
          {metaTail ? (
            <span className="text-[var(--color-ink-3)]"> · {metaTail}</span>
          ) : null}
        </div>
        {reason ? (
          <div className="mt-0.5 text-[12.5px] text-[var(--color-ink-2)] truncate">{reason}</div>
        ) : null}
      </div>
      <button
        type="button"
        className="text-[12.5px] text-[var(--color-ink-2)] hover:text-[var(--color-ink)] shrink-0"
        onClick={onGo}
      >
        {t('dashboard.exception.action.go')}
      </button>
    </li>
  )
}
```

- [ ] **Step 2: If `App.tsx` passes `onToast` to `<Dashboard …>`, remove that prop**

Search for usage: `grep -n "Dashboard" frontend/src/App.tsx`

If the call site looks like `<Dashboard onToast={…} />`, change it to `<Dashboard />`. The new component takes no props.

- [ ] **Step 3: Check that `--color-yellow` exists**

Run: `grep -n "color-yellow" frontend/src/index.css frontend/src/styles.css 2>/dev/null`

If no match, either:
- Add a `--color-yellow` token to the project's CSS root (look for where `--color-orange` is defined and add a sibling) — pick a warm amber that contrasts with `--color-orange` (e.g. `#f0b400`), OR
- Change `SEVERITY_DOT.yellow` in `Dashboard.tsx` to reuse `'bg-[var(--color-orange)] opacity-60'` (cheap fallback to ship today; refine in a follow-up).

Pick the path that matches how the codebase already extends tokens. Commit the CSS change in the same step if you take Path A.

- [ ] **Step 4: Build the frontend**

Run: `cd frontend && export PATH="/opt/homebrew/bin:$PATH" && npm run build`
Expected: clean build, no TS errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Dashboard.tsx
# include App.tsx and any CSS change if Step 2 or 3 modified them
git commit -m "feat(dashboard): rewrite as today workspace UI"
```

---

## Task 12: Verify locally + deploy

**Files:** none new — verification + ops.

- [ ] **Step 1: Run the full backend test suite**

Run: `uv run pytest -q`
Expected: ≥ 386 passed. Investigate any failure before continuing.

- [ ] **Step 2: Grep for any leftover stop-all references**

Run: `grep -rn "stop_all_phones\|stop-all\|stopAll" app/ frontend/src/ tests/ 2>/dev/null`
Expected: zero hits.

If any test still references the removed path, delete or rewrite the test, recommit, re-run pytest.

- [ ] **Step 3: Start the dev server**

Run: `./run.sh` (in one terminal) and `uv run python -m procrastinate -a app.tasks.procrastinate_app worker --queues phones,default` (in another).

- [ ] **Step 4: Manual smoke**

Open `http://localhost:8080`, log in, verify on the home page:
- Title `今日工作台` with `YYYY-MM-DD · Asia/Shanghai` subtitle (today's local date).
- Three stat cards render with numbers and the right captions.
- Clicking each card routes to `/videos` / `/accounts` / `/videos`.
- If the DB has no exceptions: "All quiet" empty state. Force one by setting one publication's `result='FAILURE'` (or one account's `proxy_status='fail'`) directly in `mkt_test` or local DB and refresh — the row appears with the correct severity color and meta.

- [ ] **Step 5: Build the frontend for production**

Run: `cd frontend && export PATH="/opt/homebrew/bin:$PATH" && npm run build`
Expected: clean build.

- [ ] **Step 6: Deploy following `docs/production-deploy.md`**

Follow the runbook. After deploy, restart **both** `mkt` and `mkt-worker` (the runbook covers `mkt` only; `mkt-worker` shares the `app/` tree).

No schema migration to run. No PG backup required (this change is read-only at the DB layer).

- [ ] **Step 7: Production smoke**

Open the production URL, log in, verify the Dashboard renders without errors. Open browser devtools and confirm `GET /api/dashboard` returns the new shape.

- [ ] **Step 8: Commit any final cleanup**

If verification surfaced a leftover, commit it. Otherwise this task ends without a commit.

---

## Self-Review

**Spec coverage**

- Overview / Goals: covered by Tasks 1-6 (today workspace endpoint) + 11 (UI) + 7 (delete stop-all).
- Non-Goals: respected — no `OPERATION_LOCK` removal (warmup/engagement/delete still use `client.stop_phones`), no query-param deep links, no platform breakdown, no retention policy, no settings for warmup_stuck threshold, no frontend tests.
- Page Structure (header, stat row, exceptions card, polling): Task 11.
- Data Contract (today / exceptions / accounts / videos shape, today boundaries in Asia/Shanghai, exception kinds and predicates, severity ordering, cap-5): Tasks 1-5.
- Error Handling (per-region try/except, frontend silent fail, missing account fallback, empty data): Tasks 1-6 + 11.
- Testing (backend: 12 unit + 2 integration + 1 router-removed = 15 tests, no frontend tests): Tasks 1-7.
- Implementation Notes (Order of work, StatCard onClick, severity tokens, no SSE, auth already wired): Tasks 8 (StatCard) + 11 (severity tokens + auth pre-wired) + Task 12 (deploy order).
- Deployment: Task 12 covers `pytest`, `npm run build`, follow runbook, restart both services.

No gaps found.

**Placeholder scan**

- No "TBD" / "TODO" / "implement later" in any task.
- All code blocks contain complete, runnable code.
- All commands have expected outputs.
- Severity-yellow fallback path in Task 11 Step 3 has both branches spelled out — not a placeholder.

**Type consistency**

- Backend: `_today_counts → dict`, `_account_counts → dict`, `_video_counts → dict`, `_exceptions → {"items": [...], "total": N}`. The handler unpacks `exc["items"]` and `exc["total"]` consistently across Tasks 5 and 6.
- Frontend: `DashboardData` and `DashboardException` types declared in Task 9 are consumed verbatim in Task 11 (`d.today.done`, `d.exceptions`, `e.kind`, `e.severity`, etc.). i18n keys defined in Task 10 match every `t('dashboard....')` call in Task 11.
- SQL column names referenced from `models.py`: `posted_at`, `last_checked_at`, `notes`, `result`, `account_id`, `video_id`, `platform` (publications); `profile_name`, `geelark_phone_id`, `tiktok_logged_in`, `warmed_up`, `warmup_count`, `target_warmup_count`, `last_warm_at`, `proxy_status`, `proxy_checked_at`, `updated_at` (accounts); `original_filename` (videos). All verified against `app/models.py`.

No inconsistencies found.
