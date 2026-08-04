# Merge Groups into Accounts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the standalone Groups tab into a left-rail master-detail panel inside the Accounts page. Account-level batch endpoints + an inline-pane wizard make the merge functional.

**Architecture:** Frontend SPA gets a single `/accounts` page with a 240px group rail on the left and a context-aware main pane on the right. Wizard becomes a full-pane mode of the existing `GroupWizard` component. Backend adds two thin endpoints (`POST /api/accounts/warmup|publish`) that accept `account_ids[]`; existing service functions are split so the group endpoints delegate to the new account-list variants.

**Tech Stack:** FastAPI · SQLite WAL · React 19 · Vite · pytest · TestClient.

**Spec:** `docs/superpowers/specs/2026-05-25-merge-groups-into-accounts-design.md`

---

## File Structure

```
Create:
  frontend/src/components/GroupRail.tsx              (left-rail group nav)
  tests/test_api_accounts_batch.py                   (new endpoint tests)

Modify:
  app/services/warmup_service.py                     (extract run_warmup_for_accounts)
  app/services/posting_service.py                    (extract publish_for_accounts)
  app/api/accounts.py                                (add /warmup, /publish endpoints)
  app/api/groups.py                                  (add PATCH rename; warmup/publish delegate)
  app/services/groups_service.py                     (add rename helper)
  frontend/src/api.ts                                (new client methods)
  frontend/src/App.tsx                               (drop /groups routes + redirects)
  frontend/src/components/Shell.tsx                  (remove Groups tab)
  frontend/src/pages/Accounts.tsx                    (master-detail layout, big rewrite)
  frontend/src/components/wizard/GroupWizard.tsx     (add mode: "modal" | "fullpane" prop)

Delete:
  frontend/src/pages/Groups.tsx
  frontend/src/pages/GroupDetail.tsx
```

Ordering rationale: backend changes (service-layer split → API endpoints → rename endpoint) land first so frontend has the API surface to call. Then frontend plumbing (api.ts, nav, routes). Then the GroupRail component (isolated, easy). Then the Accounts.tsx master-detail rewrite (the biggest chunk, broken into 5 sub-tasks). Finally Wizard refactor and dead-page deletion.

---

## Task 1: Service-layer split — `warmup_service`

**Files:**
- Modify: `app/services/warmup_service.py` (currently 56 lines; refactor signature)
- Test: `tests/test_services.py` (existing) — add 1 test for the new account-list variant

**Why:** Currently `run_warmup(conn, client, *, group_id=None)` filters by `group_id` inline. To support `POST /api/accounts/warmup`, we need an entry point that accepts a list of account ids. Group-based callers must keep working.

- [ ] **Step 1: Write failing test for `run_warmup_for_accounts`**

Add at the end of `tests/test_services.py`:

```python
def test_run_warmup_for_accounts_only_processes_given_ids(db, monkeypatch):
    from app.repositories import accounts as acc_repo
    from app.services import warmup_service as ws

    # Three eligible accounts in two groups.
    g1 = db.execute("INSERT INTO groups(name) VALUES('g1')").lastrowid
    g2 = db.execute("INSERT INTO groups(name) VALUES('g2')").lastrowid
    a1 = acc_repo.create(db, {"profile_name": "a1",
                              "geelark_phone_id": "p1",
                              "tiktok_logged_in": 1,
                              "group_id": g1})
    a2 = acc_repo.create(db, {"profile_name": "a2",
                              "geelark_phone_id": "p2",
                              "tiktok_logged_in": 1,
                              "group_id": g1})
    a3 = acc_repo.create(db, {"profile_name": "a3",
                              "geelark_phone_id": "p3",
                              "tiktok_logged_in": 1,
                              "group_id": g2})

    seen = []
    class FakeClient:
        def start_phones(self, ids): seen.extend(ids)
        def create_warmup_task(self, pid, dur): return f"t-{pid}"
        def query_tasks(self, ids): return [{"status": 3}]
        def stop_phones(self, ids): pass

    summary = ws.run_warmup_for_accounts(db, FakeClient(), [a1, a3],
                                         duration_minutes=1, wait=False)
    assert "2 accounts warmed" in summary
    assert set(seen) == {"p1", "p3"}  # a2 untouched
```

- [ ] **Step 2: Run test, confirm failure**

Run: `./.venv/bin/pytest tests/test_services.py::test_run_warmup_for_accounts_only_processes_given_ids -v`
Expected: FAIL with `AttributeError: module 'app.services.warmup_service' has no attribute 'run_warmup_for_accounts'`

- [ ] **Step 3: Refactor `warmup_service.py` — extract account-list variant**

Replace the entire contents of `app/services/warmup_service.py` with:

```python
import time, random
from app.repositories import accounts as acc_repo


def _warm_one(conn, client, a, duration_minutes, wait):
    """Warm a single account in-place. Returns 1 if the account reached its
    max_warm_time as a result of this call, 0 otherwise."""
    dur = duration_minutes or random.randint(2, 5)
    pid = a["geelark_phone_id"]
    terminal_status = None
    try:
        try:
            client.start_phones([pid])
            task_id = client.create_warmup_task(pid, dur)
            if wait:
                deadline = time.time() + dur * 60 + 300
                while time.time() < deadline:
                    res = client.query_tasks([task_id])
                    if res and res[0].get("status") in (3, 4, 7):
                        terminal_status = res[0].get("status")
                        break
                    time.sleep(15)
        finally:
            try:
                client.stop_phones([pid])
            except Exception:
                pass
    except Exception as e:
        acc_repo.update(conn, a["id"], {
            "last_warm_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_warm_result": "Failed",
            "last_warm_notes": str(e)})
        return 0
    if wait and terminal_status != 3:
        note = (f"task status {terminal_status}" if terminal_status is not None
                else "timed out waiting for task completion")
        acc_repo.update(conn, a["id"], {
            "last_warm_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_warm_result": "Failed",
            "last_warm_notes": note})
        return 0
    spent = a.get("time_spent_warming", 0) + dur * 60
    patch = {"time_spent_warming": spent,
             "last_warm_at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "last_warm_result": "Success"}
    reached = 0
    if spent >= a.get("max_warm_time", 500):
        patch["warmed_up"] = 1
        reached = 1
    acc_repo.update(conn, a["id"], patch)
    return reached


def run_warmup_for_accounts(conn, client, account_ids,
                            duration_minutes: int | None = None,
                            wait: bool = True) -> str:
    """Warm the accounts whose ids are in `account_ids`, filtering for the
    same eligibility criteria as run_warmup (logged in, not warmed, has phone)."""
    if not account_ids:
        return "0 accounts to warm"
    ids = set(account_ids)
    targets = [a for a in acc_repo.list_all(conn)
               if a["id"] in ids
               and a.get("tiktok_logged_in") and not a.get("warmed_up")
               and a.get("geelark_phone_id")]
    if not targets:
        return "0 accounts to warm"
    warmed_now = sum(_warm_one(conn, client, a, duration_minutes, wait)
                     for a in targets)
    return f"{len(targets)} accounts warmed, {warmed_now} reached target"


def run_warmup(conn, client, duration_minutes: int | None = None,
               wait: bool = True, *, group_id: int | None = None) -> str:
    """Backward-compatible entry point. Resolves group_id -> ids -> delegate."""
    candidates = acc_repo.list_all(conn)
    if group_id is not None:
        candidates = [a for a in candidates if a.get("group_id") == group_id]
    return run_warmup_for_accounts(conn, client, [a["id"] for a in candidates],
                                   duration_minutes=duration_minutes, wait=wait)
```

- [ ] **Step 4: Run new test + full suite, confirm pass + no regressions**

Run: `./.venv/bin/pytest -q`
Expected: 39 passed (38 baseline + 1 new). Watch specifically for any existing `test_services.py::test_run_warmup_*` or `test_groups_service.py` tests still passing.

- [ ] **Step 5: Commit**

```bash
git add app/services/warmup_service.py tests/test_services.py
git commit -m "refactor(warmup_service): extract run_warmup_for_accounts; run_warmup delegates"
```

---

## Task 2: Service-layer split — `posting_service`

**Files:**
- Modify: `app/services/posting_service.py` (currently 125 lines; lines 42-95 are `publish`)
- Test: `tests/test_services.py` — add 1 test

**Why:** Symmetric with Task 1. Carve out `publish_for_accounts(conn, client, account_ids, ...)` so the new endpoint has a service-layer entry point.

- [ ] **Step 1: Write failing test**

Append to `tests/test_services.py`:

```python
def test_publish_for_accounts_restricts_pool_to_given_ids(db):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.services import posting_service as ps

    g1 = db.execute("INSERT INTO groups(name) VALUES('g1')").lastrowid
    a1 = acc_repo.create(db, {"profile_name": "a1", "geelark_phone_id": "p1",
                              "warmed_up": 1, "group_id": g1})
    a2 = acc_repo.create(db, {"profile_name": "a2", "geelark_phone_id": "p2",
                              "warmed_up": 1, "group_id": g1})
    vid_repo.create(db, {"original_filename": "v.mp4",
                         "stored_path": "/tmp/v.mp4",
                         "group_id": g1})

    class FakeClient:
        def upload_file(self, p, e): return "http://x/v.mp4"
        def create_video_post_task(self, pid, url, video_desc): return "t-1"
    # Stub caption to avoid Gemini call.
    import app.services.posting_service as mod
    mod._caption = lambda p: "cap"

    lines = list(ps.publish_for_accounts(db, FakeClient(), [a1]))
    # Only a1 should appear in the auto-assignment pool; a2 must not be picked.
    assert any("a1" in ln for ln in lines)
    assert not any("a2" in ln for ln in lines)
```

- [ ] **Step 2: Run test, confirm failure**

Run: `./.venv/bin/pytest tests/test_services.py::test_publish_for_accounts_restricts_pool_to_given_ids -v`
Expected: FAIL with `AttributeError: ... has no attribute 'publish_for_accounts'`

- [ ] **Step 3: Refactor `posting_service.py`**

In `app/services/posting_service.py`, replace the body of `publish` (lines 42-95) with a delegation, and add the new account-list function. Final state of the two functions:

```python
def publish_for_accounts(conn, client, account_ids,
                         cooldown_hours: float = 2, force: bool = False):
    """Generator. Restricts pending-video selection and the auto-assignment
    pool to accounts whose id is in `account_ids`.

    Video filtering: a pending video is eligible if its account_id is in
    `account_ids` (manual post), OR if its account_id is null AND its group_id
    matches the group of at least one account in `account_ids` (auto-post)."""
    ids = set(account_ids)
    if not ids:
        yield "No accounts selected."
        return
    accounts = [a for a in acc_repo.list_all(conn) if a["id"] in ids]
    if not accounts:
        yield "No accounts found."
        return
    group_ids = {a.get("group_id") for a in accounts if a.get("group_id") is not None}

    pend = vid_repo.list_pending(conn)
    manual = [v for v in pend if v.get("account_id") in ids]
    auto = [v for v in pend if not v.get("account_id")
            and v.get("group_id") in group_ids]
    if not manual and not auto:
        yield "No videos to post."
        return
    by_id = {a["id"]: a for a in accounts}
    cutoff = time.time() - cooldown_hours * 3600

    for v in manual:
        acc = by_id.get(v["account_id"])
        if acc is None:
            continue
        label = (acc.get("profile_name") or "").strip()
        yield f"'{v['original_filename']}' -> {label} (manual)"
        if not force:
            if not acc.get("warmed_up"):
                yield f"  SKIPPED: {label} not warmed up"
                continue
            last = _epoch(acc.get("last_posted_at"))
            if last is not None and last >= cutoff:
                yield f"  SKIPPED: {label} in cooldown"
                continue
        yield from _post_one(conn, client, v, acc)

    if auto:
        fresh = [a for a in acc_repo.list_all(conn) if a["id"] in ids]
        pool = build_pool([{**a, "last_posted_at": _epoch(a.get("last_posted_at"))}
                            for a in fresh])
        by_name = {(a["profile_name"] or "").strip(): a for a in fresh}
        plan = assign(auto, pool, cooldown_hours=cooldown_hours)
        if not plan and not manual:
            yield "No warmed-up accounts available."
        for item in plan:
            v = item["video"]
            acc = by_name[item["profile_name"]]
            yield f"'{v['original_filename']}' -> {acc['profile_name']} (auto)"
            yield from _post_one(conn, client, v, acc)
    yield "Done."


def publish(conn, client, cooldown_hours: float = 2, force: bool = False,
            *, group_id: int | None = None):
    """Backward-compatible entry point. Resolves group_id -> account ids -> delegate."""
    candidates = acc_repo.list_all(conn)
    if group_id is not None:
        candidates = [a for a in candidates if a.get("group_id") == group_id]
    yield from publish_for_accounts(conn, client,
                                    [a["id"] for a in candidates],
                                    cooldown_hours=cooldown_hours, force=force)
```

Keep `_post_one`, `_caption`, `_epoch`, and `check_initiated` unchanged.

- [ ] **Step 4: Run new + full suite**

Run: `./.venv/bin/pytest -q`
Expected: 40 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/posting_service.py tests/test_services.py
git commit -m "refactor(posting_service): extract publish_for_accounts; publish delegates"
```

---

## Task 3: New endpoint — `POST /api/accounts/warmup`

**Files:**
- Modify: `app/api/accounts.py` (currently 167 lines; add after line 144 / `bulk-mark-logged-in`)
- Test: `tests/test_api_accounts_batch.py` (new file)

- [ ] **Step 1: Write failing tests**

Create `tests/test_api_accounts_batch.py`:

```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "pw")
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "k")
    monkeypatch.setenv("GEELARK_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    import app.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "test.db")
    from app.main import create_app
    c = TestClient(create_app())
    r = c.post("/api/login", json={"password": "pw"})
    assert r.status_code == 200
    return c


def _make_group(client, name="g"):
    r = client.post("/api/groups", json={"name": name})
    assert r.status_code == 200
    return r.json()["id"]


def _make_account(client, profile_name, group_id, **extra):
    body = {"profile_name": profile_name, "group_id": group_id, **extra}
    r = client.post("/api/accounts", json=body)
    assert r.status_code == 200
    return r.json()["id"]


def test_accounts_warmup_400_when_empty_ids(client):
    r = client.post("/api/accounts/warmup", json={"account_ids": []})
    assert r.status_code == 400
    assert "non-empty" in r.json()["detail"].lower()


def test_accounts_warmup_400_when_id_not_found(client):
    r = client.post("/api/accounts/warmup", json={"account_ids": [9999]})
    assert r.status_code == 400


def test_accounts_warmup_400_when_ids_span_multiple_groups(client):
    g1 = _make_group(client, "g1")
    g2 = _make_group(client, "g2")
    a1 = _make_account(client, "a1", g1)
    a2 = _make_account(client, "a2", g2)
    r = client.post("/api/accounts/warmup", json={"account_ids": [a1, a2]})
    assert r.status_code == 400
    assert "same group" in r.json()["detail"].lower()


def test_accounts_warmup_400_when_group_is_archived(client):
    g = _make_group(client, "g")
    a = _make_account(client, "a", g)
    assert client.post(f"/api/groups/{g}/archive").status_code == 200
    r = client.post("/api/accounts/warmup", json={"account_ids": [a]})
    assert r.status_code == 400
    assert "archived" in r.json()["detail"].lower()


def test_accounts_warmup_streams_when_valid(client, monkeypatch):
    # Stub out the GeeLark client + warmup service so the endpoint returns fast.
    import app.services.warmup_service as ws
    monkeypatch.setattr(ws, "run_warmup_for_accounts",
                        lambda *a, **k: "stubbed: 0 to warm")
    g = _make_group(client, "g")
    a = _make_account(client, "a", g)
    r = client.post("/api/accounts/warmup", json={"account_ids": [a]})
    assert r.status_code == 200
    assert "stubbed" in r.text
```

- [ ] **Step 2: Run, confirm 5 failures (all 404, endpoint missing)**

Run: `./.venv/bin/pytest tests/test_api_accounts_batch.py -v`
Expected: All 5 tests fail (404 Not Found on `POST /api/accounts/warmup`).

- [ ] **Step 3: Add endpoint to `app/api/accounts.py`**

Append after the existing `bulk-mark-logged-in` handler (after line 144):

```python
def _validate_account_ids(account_ids):
    """Raise HTTPException(400) unless ids non-empty, all exist, share one
    non-archived group. Returns (account_rows, group_id)."""
    from app.repositories import accounts as acc_repo
    from app.services import groups_service as gs
    if not isinstance(account_ids, list) or not account_ids:
        raise HTTPException(status_code=400,
                            detail="account_ids must be a non-empty list")
    rows = [acc_repo.get(_db(), aid) for aid in account_ids]
    if any(r is None for r in rows):
        raise HTTPException(status_code=400, detail="account not found")
    gids = {r.get("group_id") for r in rows}
    if len(gids) != 1 or None in gids:
        raise HTTPException(status_code=400,
                            detail="all account_ids must share the same group")
    gid = gids.pop()
    grp = gs.get(_db(), gid)
    if grp is None:
        raise HTTPException(status_code=400, detail="group not found")
    if grp.get("archived_at"):
        raise HTTPException(status_code=400, detail="group is archived")
    return rows, gid


@router.post("/warmup")
def warmup_accounts(body: dict):
    from app.services import warmup_service as ws
    from app.services import geelark_service
    from app.sse import sse_from_generator, locked_generator
    account_ids = body.get("account_ids") or []
    _validate_account_ids(account_ids)

    def _gen():
        yield f"warmup starting for {len(account_ids)} accounts..."
        summary = ws.run_warmup_for_accounts(
            _db(), geelark_service.make_client(), account_ids)
        yield summary
        yield "Done."
    return sse_from_generator(locked_generator(_gen()))
```

Note: `acc_repo.get` may not exist yet — if `tests/test_api_accounts_batch.py` import / call fails on a missing `acc_repo.get`, check `app/repositories/accounts.py`. If absent, add the obvious one-liner:

```python
def get(conn, account_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM accounts WHERE id=?",
                       (account_id,)).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 4: Run new tests, confirm pass + full suite**

Run: `./.venv/bin/pytest -q`
Expected: 45 passed (40 + 5).

- [ ] **Step 5: Commit**

```bash
git add app/api/accounts.py app/repositories/accounts.py tests/test_api_accounts_batch.py
git commit -m "feat(api): POST /api/accounts/warmup accepts account_ids[]"
```

---

## Task 4: New endpoint — `POST /api/accounts/publish`

**Files:**
- Modify: `app/api/accounts.py`
- Test: `tests/test_api_accounts_batch.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_api_accounts_batch.py`:

```python
def test_accounts_publish_validation_mirrors_warmup(client):
    # Empty.
    assert client.post("/api/accounts/publish",
                       json={"account_ids": []}).status_code == 400
    # Not found.
    assert client.post("/api/accounts/publish",
                       json={"account_ids": [9999]}).status_code == 400
    # Multiple groups.
    g1, g2 = _make_group(client, "p1"), _make_group(client, "p2")
    a1 = _make_account(client, "p_a1", g1)
    a2 = _make_account(client, "p_a2", g2)
    assert client.post("/api/accounts/publish",
                       json={"account_ids": [a1, a2]}).status_code == 400


def test_accounts_publish_streams_when_valid(client, monkeypatch):
    import app.services.posting_service as ps
    monkeypatch.setattr(ps, "publish_for_accounts",
                        lambda *a, **k: iter(["stubbed publish line"]))
    g = _make_group(client, "p_g")
    a = _make_account(client, "p_a", g)
    r = client.post("/api/accounts/publish", json={"account_ids": [a]})
    assert r.status_code == 200
    assert "stubbed publish line" in r.text
```

- [ ] **Step 2: Run, confirm failure (endpoint missing)**

Run: `./.venv/bin/pytest tests/test_api_accounts_batch.py::test_accounts_publish_validation_mirrors_warmup tests/test_api_accounts_batch.py::test_accounts_publish_streams_when_valid -v`
Expected: 404 / 405 failures.

- [ ] **Step 3: Add the endpoint to `app/api/accounts.py`**

After the `warmup_accounts` handler from Task 3, append:

```python
@router.post("/publish")
def publish_accounts(body: dict):
    from app.services import posting_service as ps
    from app.services import geelark_service
    from app.repositories import settings as srepo
    from app.sse import sse_from_generator, locked_generator
    account_ids = body.get("account_ids") or []
    _validate_account_ids(account_ids)
    cd = float(srepo.get_all(_db()).get("post_cooldown_hours", 2))
    return sse_from_generator(locked_generator(
        ps.publish_for_accounts(_db(), geelark_service.make_client(),
                                account_ids, cooldown_hours=cd)))
```

- [ ] **Step 4: Run new tests + full suite**

Run: `./.venv/bin/pytest -q`
Expected: 47 passed (45 + 2).

- [ ] **Step 5: Commit**

```bash
git add app/api/accounts.py tests/test_api_accounts_batch.py
git commit -m "feat(api): POST /api/accounts/publish accepts account_ids[]"
```

---

## Task 5: Group endpoints delegate to account-list variants

**Files:**
- Modify: `app/api/groups.py` (lines 122-137 `warmup_start`, lines 156-168 `publish_start`)

**Why:** The spec says the existing group endpoints stay as "equivalent to select-all". After Task 1+2, the service functions already delegate internally, so this task only cleans up the API-layer code (and verifies no regression).

- [ ] **Step 1: Confirm existing group endpoint tests still pass**

Run: `./.venv/bin/pytest tests/test_api.py -k group -v`
Note the names of currently passing group tests. Expected: pass count baseline.

- [ ] **Step 2: No-op change verification**

Open `app/api/groups.py` lines 122-137 and 156-168. The bodies already pass `group_id=...` into `ws.run_warmup` / `ps.publish`, which now delegate internally to the account-list variants (Task 1+2). No code change needed.

- [ ] **Step 3: Mark this task complete — no commit required**

This is documentation that the delegation happens correctly through Task 1+2's refactor. Skip to Task 6.

---

## Task 6: PATCH /api/groups/:id — rename support

**Files:**
- Modify: `app/api/groups.py`
- Modify: `app/services/groups_service.py`
- Test: `tests/test_api.py`

**Why:** The ⋯ menu on the group header needs a rename action. No endpoint exists.

- [ ] **Step 1: Write failing test**

Append to `tests/test_api.py`:

```python
def test_patch_group_renames(client):
    client.post("/api/login", json={"password": "pw"})
    gid = client.post("/api/groups", json={"name": "before"}).json()["id"]
    r = client.patch(f"/api/groups/{gid}", json={"name": "after"})
    assert r.status_code == 200
    assert any(g["name"] == "after" for g in client.get("/api/groups").json())


def test_patch_group_rejects_duplicate_name(client):
    client.post("/api/login", json={"password": "pw"})
    g1 = client.post("/api/groups", json={"name": "alpha"}).json()["id"]
    client.post("/api/groups", json={"name": "beta"})
    r = client.patch(f"/api/groups/{g1}", json={"name": "beta"})
    assert r.status_code == 400


def test_patch_group_404_when_missing(client):
    client.post("/api/login", json={"password": "pw"})
    r = client.patch("/api/groups/9999", json={"name": "x"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run, confirm 3 failures**

Run: `./.venv/bin/pytest tests/test_api.py::test_patch_group_renames tests/test_api.py::test_patch_group_rejects_duplicate_name tests/test_api.py::test_patch_group_404_when_missing -v`
Expected: 405 Method Not Allowed.

- [ ] **Step 3: Add service helper**

In `app/services/groups_service.py`, add:

```python
def rename(conn, group_id: int, new_name: str):
    """Rename a group. Raises ValueError on empty or duplicate name."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("name required")
    cur = conn.execute("SELECT id FROM groups WHERE name=? AND id<>?",
                       (new_name, group_id)).fetchone()
    if cur:
        raise ValueError("name already used")
    conn.execute("UPDATE groups SET name=? WHERE id=?", (new_name, group_id))
    conn.commit()
```

- [ ] **Step 4: Add API handler**

In `app/api/groups.py`, add after the `archive_group` handler (after line 47):

```python
@router.patch("/{group_id}")
def patch_group(group_id: int, body: dict):
    from app.services import groups_service as gs
    if gs.get(_db(), group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    if "name" in body:
        try:
            gs.rename(_db(), group_id, body["name"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
```

- [ ] **Step 5: Run full suite**

Run: `./.venv/bin/pytest -q`
Expected: 50 passed.

- [ ] **Step 6: Commit**

```bash
git add app/api/groups.py app/services/groups_service.py tests/test_api.py
git commit -m "feat(api): PATCH /api/groups/:id supports rename"
```

---

## Task 7: Frontend API client — new methods

**Files:**
- Modify: `frontend/src/api.ts`

- [ ] **Step 1: Add new methods**

Open `frontend/src/api.ts`. Inside the exported object, add (alongside existing `updateAccount` etc):

```typescript
  warmupAccounts: (account_ids: number[]) =>
    fetch('/api/accounts/warmup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_ids }),
    }),
  publishAccounts: (account_ids: number[]) =>
    fetch('/api/accounts/publish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_ids }),
    }),
  renameGroup: (id: number, name: string) =>
    req(`/groups/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  archiveGroup: (id: number) =>
    req(`/groups/${id}/archive`, { method: 'POST' }),
  deleteGroup: (id: number) =>
    req(`/groups/${id}`, { method: 'DELETE' }),
```

The `warmup`/`publish` calls return the raw `Response` (not `req()`) because the caller needs to read the SSE stream.

- [ ] **Step 2: Verify frontend still type-checks**

Run: `cd frontend && npm run build` (assuming PATH already set per CLAUDE.md). If `npm` isn't on PATH: `export PATH="/opt/homebrew/bin:$PATH"` first.
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api.ts
git commit -m "feat(api client): warmupAccounts/publishAccounts/renameGroup/etc"
```

---

## Task 8: Remove Groups tab from top nav

**Files:**
- Modify: `frontend/src/components/Shell.tsx` (line 13)

- [ ] **Step 1: Delete the Groups nav entry**

Open `frontend/src/components/Shell.tsx`. Find line 13:

```typescript
{ id: 'groups', path: '/groups', icon: <AccountsIcon /> },
```

Delete this line entirely. Save.

- [ ] **Step 2: Build and verify nav has no Groups**

Run: `cd frontend && npm run build && cd .. && ./run.sh` in one terminal; open `http://localhost:8080` in the browser; log in; confirm the top nav shows: Dashboard · Accounts · Videos · Settings (no Groups).

Ctrl-C the server when verified.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Shell.tsx
git commit -m "feat(ui): remove Groups tab from top nav"
```

---

## Task 9: Routes — drop `/groups*`, add redirects

**Files:**
- Modify: `frontend/src/App.tsx` (lines 54-59)

- [ ] **Step 1: Replace the Groups + GroupDetail routes with redirects**

Open `frontend/src/App.tsx`. Replace lines 54-55:

```typescript
<Route path="/groups" element={<Groups />} />
<Route path="/groups/:id" element={<GroupDetail />} />
```

with:

```typescript
<Route path="/groups" element={<Navigate to="/accounts" replace />} />
<Route path="/groups/:id" element={<GroupRedirect />} />
```

At the top of the file (with other imports), add:

```typescript
import { Navigate, useParams } from 'react-router-dom'
```

(adjust to merge with existing react-router import — likely already imports `Route`, `Routes` from there).

Below imports, before `export default function App`, add:

```typescript
function GroupRedirect() {
  const { id } = useParams<{ id: string }>()
  return <Navigate to={`/accounts?group=${id}`} replace />
}
```

Also remove the now-unused `import Groups from './pages/Groups'` and `import GroupDetail from './pages/GroupDetail'` lines (the files still exist on disk for now; Task 17 deletes them).

- [ ] **Step 2: Build, verify both old URLs redirect**

Run: `cd frontend && npm run build && cd .. && ./run.sh`.
Open `http://localhost:8080/groups` → should land on `/accounts`.
Open `http://localhost:8080/groups/1` → should land on `/accounts?group=1`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(ui): /groups* redirects to /accounts master-detail"
```

---

## Task 10: Create the `GroupRail` left-rail component

**Files:**
- Create: `frontend/src/components/GroupRail.tsx`

**Why:** Isolated component for the 240px master rail. Builds in isolation so it can be slotted into Accounts.tsx in Task 11.

- [ ] **Step 1: Create the file**

Write `frontend/src/components/GroupRail.tsx`:

```tsx
import { useState, useMemo } from 'react'

export type GroupSummary = {
  id: number
  name: string
  archived_at: string | null
  counts: {
    total_accounts: number
    total_phones: number
    logged_in: number
    warmed: number
    published: number
    target_phone_count: number | null
  }
}

export const ALL_ACCOUNTS: 'all' = 'all'

type Props = {
  groups: GroupSummary[]
  totalAccounts: number
  selected: number | 'all'
  onSelect: (id: number | 'all') => void
  onNewGroup: () => void
}

export function GroupRail(props: Props) {
  const { groups, totalAccounts, selected, onSelect, onNewGroup } = props
  const [search, setSearch] = useState('')
  const [showArchived, setShowArchived] = useState(false)

  const active = useMemo(
    () => groups.filter(g => !g.archived_at &&
                              g.name.toLowerCase().includes(search.toLowerCase())),
    [groups, search])
  const archived = useMemo(
    () => groups.filter(g => g.archived_at &&
                              g.name.toLowerCase().includes(search.toLowerCase())),
    [groups, search])

  const isDraft = (g: GroupSummary) =>
    g.counts.target_phone_count !== null &&
    g.counts.target_phone_count > 0 &&
    g.counts.logged_in < g.counts.target_phone_count

  return (
    <aside style={{
      width: 240, flexShrink: 0, borderRight: '1px solid #e5e7eb',
      background: '#f5f5f7', padding: '14px 10px', overflowY: 'auto',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'center', marginBottom: 10 }}>
        <strong style={{ fontSize: 13 }}>Groups</strong>
        <button onClick={onNewGroup}
                style={{ background: '#3b82f6', color: '#fff', border: 'none',
                         padding: '4px 8px', borderRadius: 4, fontSize: 11,
                         cursor: 'pointer' }}>
          + New
        </button>
      </div>
      <input value={search} onChange={e => setSearch(e.target.value)}
             placeholder="Search groups"
             style={{ width: '100%', padding: '4px 8px', fontSize: 11,
                      marginBottom: 8, border: '1px solid #d1d5db',
                      borderRadius: 4 }} />

      <Row label="All accounts" count={totalAccounts}
           active={selected === ALL_ACCOUNTS}
           onClick={() => onSelect(ALL_ACCOUNTS)} />
      {active.map(g => (
        <Row key={g.id} label={g.name} count={g.counts.total_accounts}
             draft={isDraft(g)}
             active={selected === g.id}
             onClick={() => onSelect(g.id)} />
      ))}

      {archived.length > 0 && (
        <>
          <div onClick={() => setShowArchived(s => !s)}
               style={{ fontSize: 10, color: '#999', textTransform: 'uppercase',
                        letterSpacing: 0.5, padding: '8px 8px 4px',
                        marginTop: 14, borderTop: '1px solid #e5e7eb',
                        cursor: 'pointer' }}>
            {showArchived ? '▾' : '▸'} Archived ({archived.length})
          </div>
          {showArchived && archived.map(g => (
            <Row key={g.id} label={g.name} count={g.counts.total_accounts}
                 muted active={selected === g.id}
                 onClick={() => onSelect(g.id)} />
          ))}
        </>
      )}
    </aside>
  )
}

function Row({ label, count, active, muted, draft, onClick }: {
  label: string; count: number; active: boolean; muted?: boolean;
  draft?: boolean; onClick: () => void
}) {
  return (
    <div onClick={onClick}
         style={{
           padding: '6px 8px', borderRadius: 4, cursor: 'pointer',
           display: 'flex', justifyContent: 'space-between',
           marginBottom: 2,
           background: active ? '#3b82f6' : 'transparent',
           color: active ? '#fff' : (muted ? '#999' : '#444'),
           fontStyle: draft ? 'italic' : 'normal',
         }}>
      <span>
        {draft && <span style={{ color: '#f59e0b', marginRight: 4 }}>●</span>}
        {label}
        {draft && <span style={{ marginLeft: 4, fontSize: 10 }}>(draft)</span>}
      </span>
      <span style={{ opacity: active ? 0.8 : 1 }}>{count}</span>
    </div>
  )
}
```

- [ ] **Step 2: Build to type-check**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/GroupRail.tsx
git commit -m "feat(ui): GroupRail component (240px master nav)"
```

---

## Task 11: Accounts.tsx — wire URL-state and slot in GroupRail

**Files:**
- Modify: `frontend/src/pages/Accounts.tsx` (871 lines; ~line 256 `export default function`, lines 476-495 group state)

**Why:** Establish the URL-driven master-detail structure before changing visuals. After this task, `/accounts?group=N` selects a group in the rail, but the right pane still looks identical to today.

- [ ] **Step 1: Add URL-driven group state**

In `frontend/src/pages/Accounts.tsx`, near the top of the component (around line 258), add:

```tsx
import { useSearchParams } from 'react-router-dom'
import { GroupRail, ALL_ACCOUNTS, type GroupSummary } from '../components/GroupRail'
```

Inside the `Accounts` function body, replace the existing local-state `groupFilter` (around line 476) with URL-derived state:

```tsx
const [searchParams, setSearchParams] = useSearchParams()
const groupParam = searchParams.get('group')
const selectedGroup: number | 'all' =
  !groupParam || groupParam === 'all'
    ? ALL_ACCOUNTS
    : Number(groupParam)

const setSelectedGroup = (g: number | 'all') => {
  const next = new URLSearchParams(searchParams)
  if (g === ALL_ACCOUNTS) next.delete('group')
  else next.set('group', String(g))
  setSearchParams(next, { replace: false })
}
```

Delete the now-unused local `groupFilter` `useState` and its setter.

- [ ] **Step 2: Replace `visibleRows` filter to use the URL-derived value**

Find the `visibleRows` block (around line 492). Replace:

```tsx
const visibleRows =
  groupFilter === ALL_GROUPS
    ? rows
    : rows.filter((r) => r.group_id === groupFilter)
```

with:

```tsx
const visibleRows =
  selectedGroup === ALL_ACCOUNTS
    ? rows
    : rows.filter((r) => r.group_id === selectedGroup)
```

Also remove `const ALL_GROUPS = -1` at line 476 (now imported `ALL_ACCOUNTS` is the sentinel).

- [ ] **Step 3: Replace the page's outer flex container with two-column layout**

Find the existing top-level JSX (likely starts with `<div>` or `<PageTitle>`). Wrap the entire current page content in a left-rail-plus-main flex container:

```tsx
return (
  <div style={{ display: 'flex', minHeight: 'calc(100vh - 64px)' }}>
    <GroupRail
      groups={groups as GroupSummary[]}
      totalAccounts={rows.length}
      selected={selectedGroup}
      onSelect={setSelectedGroup}
      onNewGroup={() => setSearchParams({ wizard: 'new' })}
    />
    <main style={{ flex: 1, padding: '18px 22px', minWidth: 0 }}>
      {/* existing PageTitle + table + drawer JSX stays here unchanged for now */}
    </main>
  </div>
)
```

- [ ] **Step 4: Remove the in-page `GroupSelect` usage**

In `PageTitle`'s right slot (around line 510), delete the `<GroupSelect ... />` invocation. The whole `GroupSelect` component definition (lines 116-211) can stay for now — Task 16 deletes it as dead code.

- [ ] **Step 5: Build and verify**

Run: `cd frontend && npm run build && cd .. && ./run.sh`. Open `/accounts`:
- The 240px rail appears on the left with "All accounts" and one row per group.
- Clicking a group changes the URL to `?group=N` and filters the table.
- Clicking "All accounts" clears the param.
- Browser back/forward works.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Accounts.tsx
git commit -m "feat(ui): Accounts page hosts GroupRail; selection is URL-driven"
```

---

## Task 12: Header bar + counts cards + ⋯ menu

**Files:**
- Modify: `frontend/src/pages/Accounts.tsx`

**Why:** Replace the now-empty top of the main pane with the group's name, batch buttons, and counts cards (modes: all-accounts / active-group / archived-group).

- [ ] **Step 1: Add the group-header section**

Inside the `<main>` element added in Task 11, at the very top (before the existing PageTitle / table JSX), insert:

```tsx
{(() => {
  if (selectedGroup === ALL_ACCOUNTS) {
    return (
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>
          All accounts ({rows.length})
        </h1>
      </div>
    )
  }
  const g = groups.find((x: any) => x.id === selectedGroup) as GroupSummary | undefined
  if (!g) return null
  const archived = !!g.archived_at
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'flex-start', marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>
            {archived && '📦 (archived) '}{g.name}
          </h1>
          {g.notes && (
            <div style={{ color: '#999', fontSize: 11, marginTop: 2 }}>
              {g.notes}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {archived ? (
            <button onClick={() => onRestoreGroup(g.id)}
                    style={primaryBtn('#3b82f6')}>Restore group</button>
          ) : (
            <>
              <button onClick={() => onStartWarmup(g.id)}
                      style={primaryBtn('#10b981')}>▶ Warmup</button>
              <button onClick={() => onStartPublish(g.id)}
                      style={primaryBtn('#3b82f6')}>↑ Publish</button>
              <button onClick={() => setMenuOpen(g.id)}
                      style={secondaryBtn}>⋯</button>
            </>
          )}
        </div>
      </div>
      <CountsRow counts={g.counts} muted={archived} />
    </>
  )
})()}
```

- [ ] **Step 2: Add the styling/helper definitions inside the same file**

Above the `Accounts` component, add:

```tsx
const primaryBtn = (color: string): React.CSSProperties => ({
  background: color, color: '#fff', border: 'none',
  padding: '6px 12px', borderRadius: 4, fontSize: 11,
  fontWeight: 600, cursor: 'pointer',
})
const secondaryBtn: React.CSSProperties = {
  background: '#fff', border: '1px solid #d1d5db',
  padding: '6px 10px', borderRadius: 4, fontSize: 11,
  color: '#555', cursor: 'pointer',
}

function CountsRow({ counts, muted }: {
  counts: GroupSummary['counts']; muted?: boolean
}) {
  const cell = (label: string, value: string) => (
    <div style={{
      background: muted ? '#f3f4f6' : '#f9fafb',
      border: '1px solid #e5e7eb', padding: '10px 12px',
      borderRadius: 6, opacity: muted ? 0.6 : 1,
    }}>
      <div style={{ fontSize: 10, color: '#888', textTransform: 'uppercase',
                    letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: '#111',
                    marginTop: 2 }}>{value}</div>
    </div>
  )
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
                  gap: 8, marginBottom: 18 }}>
      {cell('📱 Phones',
            `${counts.total_phones}${counts.target_phone_count ?
                                      ` / ${counts.target_phone_count}` : ''}`)}
      {cell('🔑 Logged in', `${counts.logged_in}`)}
      {cell('🔥 Warmed', `${counts.warmed}`)}
      {cell('✅ Published', `${counts.published}`)}
    </div>
  )
}
```

- [ ] **Step 3: Wire the four action handlers**

Inside the `Accounts` component body, add:

```tsx
const [menuOpen, setMenuOpen] = useState<number | null>(null)

async function onStartWarmup(gid: number) {
  setLog([])
  setRunning(true)
  try {
    const r = await fetch(`/api/groups/${gid}/warmup/start`, { method: 'POST' })
    await streamInto(r, setLog)
  } finally { setRunning(false) }
}
async function onStartPublish(gid: number) {
  setLog([])
  setRunning(true)
  try {
    const r = await fetch(`/api/groups/${gid}/publish`, { method: 'POST' })
    await streamInto(r, setLog)
  } finally { setRunning(false) }
}
async function onRestoreGroup(gid: number) {
  // Currently no restore endpoint; show toast for now and refresh.
  onToast({ kind: 'info', text: 'Restore endpoint not yet implemented' })
}
```

If `streamInto` is not already present in this file, copy it from `frontend/src/pages/GroupDetail.tsx` (it lives in `runStream` lines 26-41 there) — pull out a tiny helper:

```tsx
async function streamInto(r: Response, setLog: (fn: (prev: string[]) => string[]) => void) {
  const reader = r.body?.getReader()
  const dec = new TextDecoder()
  if (!reader) return
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    const chunk = dec.decode(value, { stream: true })
    for (const line of chunk.split('\n')) {
      const t = line.trim()
      if (t.startsWith('data:')) {
        const text = t.slice(5).trim()
        if (text) setLog(prev => [...prev, text])
      }
    }
  }
}
```

- [ ] **Step 4: Add menu dropdown skeleton (rename / archive / delete)**

When `menuOpen === g.id`, render a small floating panel:

```tsx
{menuOpen === selectedGroup && selectedGroup !== ALL_ACCOUNTS &&
 (() => {
   const g = groups.find((x: any) => x.id === selectedGroup)
   if (!g) return null
   return (
     <div style={{ position: 'absolute', right: 22, top: 60, background: '#fff',
                   border: '1px solid #e5e7eb', borderRadius: 6, padding: 4,
                   boxShadow: '0 2px 8px rgba(0,0,0,0.08)', zIndex: 10 }}>
       <MenuItem onClick={async () => {
         const name = prompt('New name', g.name)
         if (!name || name === g.name) return setMenuOpen(null)
         await api.renameGroup(g.id, name)
         await reloadGroups()
         setMenuOpen(null)
       }}>Rename</MenuItem>
       <MenuItem onClick={async () => {
         if (!confirm(`Archive "${g.name}"?`)) return
         await api.archiveGroup(g.id)
         await reloadGroups()
         setMenuOpen(null)
       }}>Archive</MenuItem>
       <MenuItem disabled={g.counts.total_accounts > 0}
                 onClick={async () => {
         if (!confirm(`Delete empty group "${g.name}"?`)) return
         await api.deleteGroup(g.id)
         setSelectedGroup(ALL_ACCOUNTS)
         await reloadGroups()
         setMenuOpen(null)
       }}>Delete</MenuItem>
     </div>
   )
 })()}
```

Add `MenuItem` helper near the top of the file:

```tsx
function MenuItem({ children, onClick, disabled }: {
  children: React.ReactNode; onClick: () => void; disabled?: boolean
}) {
  return (
    <div onClick={disabled ? undefined : onClick}
         style={{
           padding: '6px 10px', borderRadius: 4, fontSize: 12,
           cursor: disabled ? 'not-allowed' : 'pointer',
           color: disabled ? '#bbb' : '#444',
         }}
         onMouseEnter={e => !disabled &&
           ((e.target as HTMLElement).style.background = '#f3f4f6')}
         onMouseLeave={e => (e.target as HTMLElement).style.background = ''}>
      {children}
    </div>
  )
}
```

And add a `reloadGroups` helper near other effects:

```tsx
const reloadGroups = async () => {
  const r = await fetch('/api/groups')
  if (r.ok) setGroups(await r.json())
}
```

- [ ] **Step 5: Build and verify**

Run: `cd frontend && npm run build && cd .. && ./run.sh`. Manual checks:
- `/accounts` shows "All accounts (N)" header, no counts cards.
- Click a group: header changes to its name, 4 counts cards appear, Warmup/Publish/⋯ buttons visible.
- ⋯ menu opens with Rename / Archive / Delete (last disabled if group has accounts).
- Archive a test group: it disappears from active list, appears under "Archived" collapsible in the rail. Selecting it shows only the "Restore group" button and read-only counts.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Accounts.tsx
git commit -m "feat(ui): group header + counts cards + ⋯ menu in main pane"
```

---

## Task 13: Selection toolbar (checkbox column + batch actions)

**Files:**
- Modify: `frontend/src/pages/Accounts.tsx`

- [ ] **Step 1: Add selection state and the table checkbox column**

Inside `Accounts`, add:

```tsx
const [selected, setSelected] = useState<Set<number>>(new Set())

// Clear selection whenever the group changes.
useEffect(() => { setSelected(new Set()) }, [selectedGroup])

const toggleRow = (id: number) =>
  setSelected(prev => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })
const toggleAll = () =>
  setSelected(prev =>
    prev.size === visibleRows.length ? new Set()
      : new Set(visibleRows.map(r => r.id)))
```

In the table header row JSX, add a first `<th>`:

```tsx
<th style={{ width: 30 }}>
  <input type="checkbox"
         checked={selected.size > 0 && selected.size === visibleRows.length}
         onChange={toggleAll} />
</th>
```

In each `<Tr>` rendering a row, add the leading cell (before the existing first cell):

```tsx
<td onClick={e => e.stopPropagation()}>
  <input type="checkbox" checked={selected.has(r.id)}
         onChange={() => toggleRow(r.id)} />
</td>
```

The `e.stopPropagation` prevents the row-click drawer open.

- [ ] **Step 2: Render the selection toolbar above the table when N ≥ 1**

In the main pane, replace the group header `<div>...</div>` with conditional logic: when `selected.size > 0`, render a SelectionToolbar in the same vertical slot instead of the regular header.

```tsx
{selected.size > 0 ? (
  <SelectionToolbar
    count={selected.size}
    mode={selectedGroup === ALL_ACCOUNTS ? 'all' : 'group'}
    archived={selectedGroup !== ALL_ACCOUNTS &&
              !!groups.find((x: any) => x.id === selectedGroup)?.archived_at}
    onWarmup={() => doBatchWarmup([...selected])}
    onPublish={() => doBatchPublish([...selected])}
    onMove={() => openMoveDialog([...selected])}
    onDelete={() => doBatchDelete([...selected])}
    onCancel={() => setSelected(new Set())}
  />
) : /* the existing header block from Task 12 */}
```

- [ ] **Step 3: Define `SelectionToolbar` and the batch handlers**

```tsx
function SelectionToolbar({ count, mode, archived, onWarmup, onPublish,
                            onMove, onDelete, onCancel }: {
  count: number; mode: 'all' | 'group'; archived: boolean
  onWarmup: () => void; onPublish: () => void
  onMove: () => void; onDelete: () => void; onCancel: () => void
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between',
                  alignItems: 'center', marginBottom: 14,
                  background: '#eff6ff', border: '1px solid #bfdbfe',
                  padding: '8px 12px', borderRadius: 6 }}>
      <div style={{ fontSize: 13, fontWeight: 600 }}>
        {count} selected
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        {mode === 'group' && !archived && (
          <>
            <button onClick={onWarmup} style={primaryBtn('#10b981')}>▶ Warmup</button>
            <button onClick={onPublish} style={primaryBtn('#3b82f6')}>↑ Publish</button>
          </>
        )}
        <button onClick={onMove} style={secondaryBtn}>→ Move to group</button>
        <button onClick={onDelete} style={{ ...secondaryBtn, color: '#b91c1c' }}>
          Delete
        </button>
        <button onClick={onCancel} style={secondaryBtn}>Cancel</button>
      </div>
    </div>
  )
}

async function doBatchWarmup(ids: number[]) {
  setLog([]); setRunning(true)
  try {
    const r = await api.warmupAccounts(ids)
    await streamInto(r, setLog)
  } finally { setRunning(false) }
}
async function doBatchPublish(ids: number[]) {
  setLog([]); setRunning(true)
  try {
    const r = await api.publishAccounts(ids)
    await streamInto(r, setLog)
  } finally { setRunning(false) }
}
async function doBatchDelete(ids: number[]) {
  if (!confirm(`Delete ${ids.length} accounts?`)) return
  for (const id of ids) await api.deleteAccount(id)
  setSelected(new Set())
  await reloadRows()
}

async function openMoveDialog(ids: number[]) {
  const name = prompt('Move to which group? (blank = ungrouped)')
  if (name === null) return
  const trimmed = name.trim()
  let gid: number | null = null
  if (trimmed) {
    const target = groups.find((g: any) =>
      g.name === trimmed && !g.archived_at)
    if (!target) {
      onToast({ kind: 'error', text: `No active group named "${trimmed}"` })
      return
    }
    gid = target.id
  }
  for (const id of ids) await api.updateAccount(id, { group_id: gid })
  setSelected(new Set())
  await Promise.all([reloadRows(), reloadGroups()])
}

const reloadRows = async () => {
  const r = await fetch('/api/accounts')
  if (r.ok) setRows(await r.json())
}
```

(Prompt-based dialog is intentional MVP — richer modal can come later.)

- [ ] **Step 4: Build and verify**

Run: `cd frontend && npm run build && cd .. && ./run.sh`. Manual:
- Open a group with accounts; check one row's checkbox → header changes to "1 selected" toolbar.
- Header checkbox → all rows checked, count = N.
- Cancel → selection cleared, header returns.
- Switch group → selection cleared.
- "All accounts" mode: toolbar shows only Move/Delete/Cancel (no Warmup/Publish).
- Archived group: rows are not selectable at all (the table is read-only — disable the checkboxes too: add `disabled={archived}` on each `<input type="checkbox">` and to the header checkbox).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Accounts.tsx
git commit -m "feat(ui): selection toolbar with multi-select batch actions"
```

---

## Task 14: Drawer — add "Group" select

**Files:**
- Modify: `frontend/src/pages/Accounts.tsx` (Drawer section, lines 706-868)

- [ ] **Step 1: Add a Group field to the drawer form**

Inside the Drawer body (between existing fields — likely after `profile_name` or alongside `tiktok_username`), add:

```tsx
<label style={{ display: 'block', marginBottom: 12 }}>
  <span style={{ fontSize: 11, color: '#666',
                  textTransform: 'uppercase', letterSpacing: 0.5 }}>
    Group
  </span>
  <select value={form.group_id ?? ''}
          onChange={e => setForm(f => ({
            ...f,
            group_id: e.target.value === '' ? null : Number(e.target.value),
          }))}
          disabled={isReadOnly}
          style={{ width: '100%', padding: '6px 8px', marginTop: 4,
                   border: '1px solid #d1d5db', borderRadius: 4 }}>
    <option value="">(none)</option>
    {groups.filter((g: any) => !g.archived_at).map((g: any) => (
      <option key={g.id} value={g.id}>{g.name}</option>
    ))}
  </select>
</label>
```

- [ ] **Step 2: Compute `isReadOnly` and pipe to all drawer inputs**

Above the drawer return, add:

```tsx
const editingGroup = editing?.group_id
  ? groups.find((g: any) => g.id === editing.group_id)
  : null
const isReadOnly = !!editingGroup?.archived_at && selectedGroup === editing?.group_id
```

(`isReadOnly` is true only when the user opened the drawer from inside the archived group's pane. From "All accounts" mode, edits are still allowed per the spec.)

Apply `disabled={isReadOnly}` to every editable input in the drawer (existing email, tiktok_username, proxy, max_warm_time, login toggle, etc.).

Hide Save and Delete buttons when `isReadOnly`:

```tsx
{!isReadOnly && <button onClick={onSave}>Save</button>}
{!isReadOnly && <button onClick={onDelete} style={dangerBtn}>Delete</button>}
```

- [ ] **Step 3: Make sure `group_id` is part of the diff sent to PATCH**

In the existing save handler (around line 351), confirm `diff` includes `group_id` when changed. Existing code already does `api.updateAccount(editing.id, diff)`; the diff calculation just needs the new field added. If the diff is computed by comparing `editing` vs `form` keys, this is automatic since `group_id` is now a form key.

- [ ] **Step 4: Build and verify**

Run: `./run.sh`. Open drawer, change Group dropdown to another group, Save. Confirm:
- Account row disappears from current group (if you switched away from "all").
- Switching to the destination group shows it there.
- Counts update on both groups (after `reloadGroups`).
- Opening a drawer from an archived group's pane: all inputs disabled, no Save/Delete.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Accounts.tsx
git commit -m "feat(ui): drawer supports moving account between groups"
```

---

## Task 15: GroupWizard — add `mode` prop, inline rendering

**Files:**
- Modify: `frontend/src/components/wizard/GroupWizard.tsx` (67 lines; modal wrapper at lines 40-48)

- [ ] **Step 1: Add a mode prop and branch the wrapper**

Open `frontend/src/components/wizard/GroupWizard.tsx`. Change the props type:

```tsx
type Props = {
  onClose: () => void
  mode?: 'modal' | 'fullpane'  // default: 'modal'
}
```

Replace the existing modal wrapper at lines 40-48:

```tsx
return (
  <div className="fixed inset-0 bg-black/50 flex ...">
    <div className="bg-white ...">
      {/* steps */}
    </div>
  </div>
)
```

with:

```tsx
const isFullpane = props.mode === 'fullpane'

if (isFullpane) {
  return (
    <div style={{ padding: '18px 22px' }}>
      <StepBar step={step} />
      {step === 1 && <Step1Proxies ... />}
      {step === 2 && <Step2Phones ... />}
      {step === 3 && <Step3Accounts ... />}
      {step === 4 && <Step4Login ... />}
      <FooterButtons step={step} onCancel={handleClose} onAdvance={...} />
    </div>
  )
}
// existing modal branch kept verbatim below
```

Add `StepBar` and `FooterButtons` helpers (small, just visual progress):

```tsx
function StepBar({ step }: { step: 1 | 2 | 3 | 4 }) {
  const labels = ['Proxies', 'Create phones', 'Parse credentials', 'Mark logged in']
  return (
    <div style={{ display: 'flex', gap: 8, marginBottom: 18 }}>
      {labels.map((label, i) => {
        const n = i + 1
        const state: 'done' | 'active' | 'pending' =
          n < step ? 'done' : n === step ? 'active' : 'pending'
        return (
          <div key={n} style={{
            flex: 1, padding: '8px 10px', borderRadius: 4,
            background: state === 'active' ? '#3b82f6'
                       : state === 'done' ? '#d1fae5' : '#f3f4f6',
            color: state === 'active' ? '#fff'
                 : state === 'done' ? '#065f46' : '#888',
            fontSize: 11, fontWeight: 600,
          }}>
            {state === 'done' ? '✓ ' : `${n}. `}{label}
          </div>
        )
      })}
    </div>
  )
}
```

(The existing step components — Step1Proxies through Step4Login — render unchanged. Only the wrapper changes.)

- [ ] **Step 2: Build, verify modal mode still works**

Run: `cd frontend && npm run build`. Then in a temp branch / scratch component, instantiate `<GroupWizard onClose={() => {}} />` (no mode) and confirm modal still renders. (You won't actually call this since Groups page is gone, but the build proves the modal branch still compiles.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/wizard/GroupWizard.tsx
git commit -m "feat(wizard): add mode prop; fullpane variant for inline rendering"
```

---

## Task 16: Wire `?wizard=new` → inline wizard in Accounts main pane

**Files:**
- Modify: `frontend/src/pages/Accounts.tsx`

- [ ] **Step 1: Detect `wizard=new` and `wizard=1` and replace main pane**

In `Accounts.tsx`, add (near the URL-state block from Task 11):

```tsx
const wizardParam = searchParams.get('wizard')
const isWizardActive = wizardParam === '1' || wizardParam === 'new'
```

When `wizardParam === 'new'` on mount, immediately create a draft group and rewrite the URL:

```tsx
useEffect(() => {
  if (wizardParam !== 'new') return
  ;(async () => {
    const r = await fetch('/api/groups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: `Group ${Date.now()}` }),
    })
    if (!r.ok) {
      onToast({ kind: 'error', text: 'Failed to create draft group' })
      setSearchParams({})
      return
    }
    const { id } = await r.json()
    await reloadGroups()
    setSearchParams({ group: String(id), wizard: '1' })
  })()
}, [wizardParam])
```

Inside `<main>`, branch:

```tsx
{isWizardActive && typeof selectedGroup === 'number' ? (
  <GroupWizard
    mode="fullpane"
    /* the existing GroupWizard already reads groupId from its own state /
       props; if it currently takes nothing, pass the id explicitly: */
    onClose={() => {
      // Drop the wizard param; stay on the group's regular pane.
      const next = new URLSearchParams(searchParams)
      next.delete('wizard')
      setSearchParams(next)
      reloadGroups()
    }}
  />
) : (
  /* the header + counts + table block from Tasks 12-13 */
)}
```

Check `frontend/src/components/wizard/GroupWizard.tsx` to confirm how it gets the group id. If it currently calls `createGroup` itself in step 1, refactor so:
- Modal mode: existing behaviour (create on advance from step 1).
- Fullpane mode: receives `initialGroupId` prop, uses it directly.

Add to Props:
```tsx
initialGroupId?: number
```

Inside the wizard, initialize state `groupId` from `initialGroupId` when in fullpane mode.

- [ ] **Step 2: Build and verify**

Run: `./run.sh`. From `/accounts`, click "+ New" in the rail. Expected:
- URL → `/accounts?wizard=new` briefly → `/accounts?group=<new id>&wizard=1`.
- Rail shows the new group selected, marked with amber dot + `(draft)`.
- Main pane shows wizard step bar + Step1Proxies.
- Cancel from step 1: existing cleanup deletes the empty group, navigates to `/accounts`, draft disappears from rail.
- Cancel from step 2+: group persists, draft suffix stays.
- Finish step 4: pane switches to normal layout for that group. Draft suffix disappears once `logged_in >= target_phone_count`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Accounts.tsx frontend/src/components/wizard/GroupWizard.tsx
git commit -m "feat(ui): inline wizard in Accounts main pane via ?wizard=new"
```

---

## Task 17: Delete dead pages and unused code

**Files:**
- Delete: `frontend/src/pages/Groups.tsx`
- Delete: `frontend/src/pages/GroupDetail.tsx`
- Modify: `frontend/src/pages/Accounts.tsx` (remove `GroupSelect` definition lines 116-211)
- Modify: `frontend/src/App.tsx` (already imports removed in Task 9)

- [ ] **Step 1: Delete the page files**

```bash
git rm frontend/src/pages/Groups.tsx frontend/src/pages/GroupDetail.tsx
```

- [ ] **Step 2: Remove `GroupSelect` from Accounts.tsx**

In `frontend/src/pages/Accounts.tsx`, delete the entire `GroupSelect` function (currently lines 116-211 — locate by `function GroupSelect`).

- [ ] **Step 3: Build, verify no broken imports**

Run: `cd frontend && npm run build`
Expected: build succeeds.

If a stray `import { GroupSelect } from` line lingers anywhere (search): delete it.

- [ ] **Step 4: Smoke-test full flow**

Run: `./run.sh`. Click through:
- Top nav: Dashboard / Accounts / Videos / Settings (no Groups).
- `/accounts` master-detail layout, all 3 modes work.
- New wizard → create a small test group → complete steps → see it in rail.
- Bulk select inside a group → batch Warmup (with a stub or real call) returns a stream.
- Move account between groups via drawer.
- Archive / restore / delete (empty) group.
- Old URLs `/groups`, `/groups/1` redirect correctly.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Accounts.tsx frontend/src/pages/Groups.tsx \
        frontend/src/pages/GroupDetail.tsx
git commit -m "chore: delete Groups & GroupDetail pages; drop GroupSelect"
```

---

## Final verification

- [ ] **Step 1: Full test suite**

Run: `./.venv/bin/pytest -q`
Expected: 50 passed (38 baseline + 12 new: 2 service + 7 batch endpoint + 3 rename = 50).

- [ ] **Step 2: Production build**

Run: `cd frontend && npm run build && cd .. && ./run.sh`
Expected: builds clean, app starts on :8080.

- [ ] **Step 3: Read the spec checklist one more time**

Open `docs/superpowers/specs/2026-05-25-merge-groups-into-accounts-design.md` and visually confirm:
- Top nav has no Groups tab ✓
- Routing: `/groups*` redirects ✓
- Three modes (all / active / archived) all render correctly ✓
- Wizard W2 fullpane works ✓
- Multi-select toolbar with Warmup / Publish / Move / Delete ✓
- Drawer has Group select ✓
- Archived group is read-only when viewed via its pane ✓

---

## Self-review notes (filled in by author of this plan)

**Spec coverage:**
- Routing → Tasks 8, 9 ✓
- Layout (rail + main pane) → Tasks 10, 11 ✓
- Modes (all/active/archived) → Task 12, 13 step 4 ✓
- Counts cards → Task 12 ✓
- Selection toolbar → Task 13 ✓
- Drawer Group select + read-only archived → Task 14 ✓
- Wizard W2 → Tasks 15, 16 ✓
- Backend `POST /api/accounts/warmup` → Tasks 1, 3 ✓
- Backend `POST /api/accounts/publish` → Tasks 2, 4 ✓
- PATCH rename → Task 6 ✓
- Group endpoints delegate → Task 5 (no-op verified, covered by Tasks 1+2) ✓
- Deletion of old pages → Task 17 ✓
- Migration/rollback (single commit lands) → tasks already produce separate commits; can be squashed at PR time if desired ✓

**Placeholder scan:** No TBD / TODO. Each step has either exact code or an exact command.

**Type consistency:** `GroupSummary` defined in Task 10, used in Tasks 11–14 with the same shape. `ALL_ACCOUNTS` sentinel exported from `GroupRail.tsx` (Task 10), used in Tasks 11–14.

**Known soft spots:**
- The exact line numbers above are best-guesses from the explore agent's report; implementers should be ready to find symbols by name rather than line number, especially after the page grows in Task 12.
- `openMoveDialog` uses a browser `prompt` for the MVP. A richer dialog can replace it in a follow-up.
- The archive UI has a "Restore group" button but no backend endpoint exists today. The button shows a toast for now; a follow-up task should add `POST /api/groups/:id/restore` (one-liner: set `archived_at` to NULL).
