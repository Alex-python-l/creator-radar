# Multi-phone-per-proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the create-phones wizard accept a "phones to create" number independent of the proxy count, with proxies round-robined across phones, and generate `{group}-NN` names that skip any number already used inside the group (no `(2)` suffixes).

**Architecture:** Pure additions to three layers — a new `next_serial_index` helper in `app/repositories/accounts.py`; an optional `count` parameter on `create_profiles_for_group` in `app/services/geelark_service.py`; an optional `count` field on `POST /api/groups/{gid}/phones/create`; plus a number input in `Step1Proxies.tsx` carried through `WizardState` to `Step2Phones.tsx`. Old callers that don't pass `count` get the existing 1:1 behavior unchanged.

**Tech Stack:** Python 3.12 / FastAPI / SQLite (WAL) / pytest; React 19 + Vite + TypeScript + react-i18next; tests run via `.venv/bin/pytest -q`.

**Spec:** [docs/superpowers/specs/2026-05-25-multi-phone-per-proxy-design.md](../specs/2026-05-25-multi-phone-per-proxy-design.md)

---

## Task 1: `next_serial_index` repository helper

Backend-only, pure SQL + Python. No GeeLark calls. This is the foundation; later tasks depend on it.

**Files:**
- Modify: `app/repositories/accounts.py` (add new function after `unique_profile_name`, around line 90)
- Test: `tests/test_repositories.py`

- [ ] **Step 1.1: Write the failing tests**

Append to `tests/test_repositories.py`:

```python
def test_next_serial_index_empty_group_returns_1(db):
    from app.repositories import accounts as acc, groups as gr
    gid = gr.create(db, name="g")
    assert acc.next_serial_index(db, gid, "g") == 1


def test_next_serial_index_skips_existing_numbers(db):
    from app.repositories import accounts as acc, groups as gr
    gid = gr.create(db, name="g")
    acc.create(db, {"profile_name": "g-01", "group_id": gid})
    acc.create(db, {"profile_name": "g-02", "group_id": gid})
    assert acc.next_serial_index(db, gid, "g") == 3


def test_next_serial_index_treats_paren_suffix_as_same_slot(db):
    """`g-01 (2)` is a leftover from unique_profile_name and occupies slot 1."""
    from app.repositories import accounts as acc, groups as gr
    gid = gr.create(db, name="g")
    acc.create(db, {"profile_name": "g-01", "group_id": gid})
    acc.create(db, {"profile_name": "g-01 (2)", "group_id": gid})
    assert acc.next_serial_index(db, gid, "g") == 2


def test_next_serial_index_ignores_other_groups(db):
    from app.repositories import accounts as acc, groups as gr
    g1 = gr.create(db, name="g")
    g2 = gr.create(db, name="h")
    acc.create(db, {"profile_name": "g-05", "group_id": g1})
    acc.create(db, {"profile_name": "h-99", "group_id": g2})
    assert acc.next_serial_index(db, g1, "g") == 6


def test_next_serial_index_ignores_unparseable_suffix(db):
    """A row like `g-foo` doesn't occupy any numeric slot."""
    from app.repositories import accounts as acc, groups as gr
    gid = gr.create(db, name="g")
    acc.create(db, {"profile_name": "g-foo", "group_id": gid})
    assert acc.next_serial_index(db, gid, "g") == 1


def test_next_serial_index_three_digit_numbers(db):
    """Width is just for formatting; the helper handles >99 fine."""
    from app.repositories import accounts as acc, groups as gr
    gid = gr.create(db, name="g")
    acc.create(db, {"profile_name": "g-100", "group_id": gid})
    assert acc.next_serial_index(db, gid, "g") == 101
```

- [ ] **Step 1.2: Run and verify it fails**

```bash
.venv/bin/pytest tests/test_repositories.py -k next_serial_index -v
```
Expected: 6 errors, all `AttributeError: module 'app.repositories.accounts' has no attribute 'next_serial_index'`.

- [ ] **Step 1.3: Implement `next_serial_index`**

Append to `app/repositories/accounts.py` (after `unique_profile_name`, before `upsert_by_phone_id` at line 91):

```python
def next_serial_index(conn: sqlite3.Connection, group_id: int,
                      group_name: str) -> int:
    """Lowest integer N such that `{group_name}-NN` is not already used by
    any row in this group.

    Takes the substring of profile_name after the first `-`, stops at the
    first non-digit character, and parses what's left as an integer. So
    `g-01` -> 1, `g-01 (2)` -> 1 (a leftover from unique_profile_name still
    occupies slot 1), `g-100` -> 100. Rows where nothing parses are
    ignored. Returns 1 for an empty group.
    """
    prefix = f"{group_name}-"
    seen: set[int] = set()
    for r in conn.execute(
        "SELECT profile_name FROM accounts "
        "WHERE group_id=? AND profile_name LIKE ?",
        (group_id, prefix + "%")):
        tail = r["profile_name"][len(prefix):]
        digits = ""
        for ch in tail:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            seen.add(int(digits))
    return max(seen) + 1 if seen else 1
```

- [ ] **Step 1.4: Run tests, verify pass**

```bash
.venv/bin/pytest tests/test_repositories.py -k next_serial_index -v
```
Expected: 6 passed.

- [ ] **Step 1.5: Commit**

```bash
git add app/repositories/accounts.py tests/test_repositories.py
git commit -m "feat(accounts): next_serial_index — first free {group}-NN slot"
```

---

## Task 2: `create_profiles_for_group` accepts `count`

Round-robin proxies across `count` phones, use `next_serial_index` for names.

**Files:**
- Modify: `app/services/geelark_service.py:196-259` (signature + body of `create_profiles_for_group`)
- Test: `tests/test_geelark_service_create_for_group.py`

- [ ] **Step 2.1: Write the failing tests**

Append to `tests/test_geelark_service_create_for_group.py`:

```python
def test_create_profiles_count_none_keeps_1_to_1_behavior(db):
    """Regression guard — old callers that don't pass count still get
    one phone per proxy."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg1")
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://a:1080", "socks5://b:1080"]))

    assert len(calls["create_phones"]) == 2


def test_create_profiles_count_more_than_proxies_round_robins(db):
    """One proxy, five phones — proxy is reused for every phone."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg2")
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://only:1080"], count=5))

    assert len(calls["create_phones"]) == 5
    proxies_seen = [c[0]["proxy"] for c in calls["create_phones"]]
    assert proxies_seen == ["socks5://only:1080"] * 5
    serials = [c[0]["serialName"] for c in calls["create_phones"]]
    assert serials == ["rg2-01", "rg2-02", "rg2-03", "rg2-04", "rg2-05"]


def test_create_profiles_count_with_multiple_proxies_cycles(db):
    """Two proxies, five phones — cycles 1,2,1,2,1."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg3")
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://p1:1080", "socks5://p2:1080"], count=5))

    proxies_seen = [c[0]["proxy"] for c in calls["create_phones"]]
    assert proxies_seen == [
        "socks5://p1:1080", "socks5://p2:1080",
        "socks5://p1:1080", "socks5://p2:1080",
        "socks5://p1:1080",
    ]


def test_create_profiles_count_fewer_than_proxies_uses_prefix(db):
    """Three proxies, two phones — the third proxy is unused this round."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg4")
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://p1:1080", "socks5://p2:1080", "socks5://p3:1080"],
        count=2))

    proxies_seen = [c[0]["proxy"] for c in calls["create_phones"]]
    assert proxies_seen == ["socks5://p1:1080", "socks5://p2:1080"]


def test_create_profiles_count_resumes_from_next_serial(db):
    """If the group already has rg5-01 and rg5-01 (2), the new run starts
    at rg5-02 — no (3) suffixes."""
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    from app.repositories import accounts as acc
    gid = gr.create(db, name="rg5")
    acc.create(db, {"profile_name": "rg5-01", "group_id": gid})
    acc.create(db, {"profile_name": "rg5-01 (2)", "group_id": gid})
    client, calls = _fake_client()

    list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://p:1080"], count=3))

    serials = [c[0]["serialName"] for c in calls["create_phones"]]
    assert serials == ["rg5-02", "rg5-03", "rg5-04"]


def test_create_profiles_rejects_zero_count(db):
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg6")
    client, calls = _fake_client()

    log = list(gs.create_profiles_for_group(db, client, gid,
        ["socks5://p:1080"], count=0))

    assert calls["create_phones"] == []
    assert any("FAILED" in line for line in log)


def test_create_profiles_rejects_empty_proxies_with_count(db):
    from app.services import geelark_service as gs
    from app.repositories import groups as gr
    gid = gr.create(db, name="rg7")
    client, calls = _fake_client()

    log = list(gs.create_profiles_for_group(db, client, gid, [], count=2))

    assert calls["create_phones"] == []
    assert any("FAILED" in line for line in log)
```

- [ ] **Step 2.2: Run and verify they fail**

```bash
.venv/bin/pytest tests/test_geelark_service_create_for_group.py -v
```
Expected: 7 failures (new tests). The old 2 tests should still pass.

- [ ] **Step 2.3: Rewrite `create_profiles_for_group`**

Replace `app/services/geelark_service.py:196-259` (the entire `create_profiles_for_group` function) with:

```python
def create_profiles_for_group(conn, client, group_id: int,
                              proxies: list[str],
                              count: int | None = None):
    """Generator yielding log lines. Creates `count` GeeLark phones inside
    the group, round-robining `proxies` across them. When `count` is None
    (the old contract), it defaults to `len(proxies)` so callers that
    relied on 1-phone-per-proxy keep working.

    Names follow `{group_name}-NN` starting at the first NN not already
    used in this group — see `accounts.next_serial_index`. No
    `unique_profile_name` wrap; numbers are picked to be unique.

    On per-phone failure we log the failure and move on — partial success
    is the desired behavior."""
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as grp_repo

    group = grp_repo.get(conn, group_id)
    if group is None:
        yield f"FAILED: group {group_id} not found"
        return
    group_name = group["name"]

    if not proxies:
        yield "FAILED: at least one proxy is required"
        return
    if count is None:
        count = len(proxies)
    if count < 1:
        yield f"FAILED: count must be >= 1 (got {count})"
        return

    start = acc_repo.next_serial_index(conn, group_id, group_name)

    for i in range(count):
        proxy = proxies[i % len(proxies)]
        serial = f"{group_name}-{(start + i):02d}"
        aid = acc_repo.create(conn, {
            "profile_name": serial,
            "group_id": group_id,
            "proxy": proxy,
        })
        yield f"Creating phone for {serial} (proxy {proxy})..."
        try:
            data = client.create_phones([{
                "serialName": serial,
                "proxy": proxy,
                "groupName": group_name,
            }])
        except Exception as e:
            yield f"  FAILED: create_phones raised: {e}"
            continue
        details = data.get("details") or data.get("list") or []
        if not details:
            yield f"  FAILED: no phone returned for {serial}"
            continue
        phone_id = details[0].get("id") or details[0].get("envId")
        if not phone_id:
            yield f"  FAILED: phone created but no id for {serial}"
            continue
        acc_repo.update(conn, aid, {"geelark_phone_id": phone_id})
        yield f"  phone {phone_id} created, installing TikTok..."
        try:
            apps = client.get_installable_apps(phone_id, name="TikTok")
            items = apps.get("items", [])
            tt = next((i for i in items
                       if "tiktok" in (i.get("appName", "").lower())), None)
            if tt:
                client.install_app(phone_id, tt["appVersionId"])
                yield f"  TikTok install requested for {serial}"
            else:
                yield f"  WARNING: TikTok not in installable list for {serial}"
        except Exception as e:
            yield f"  WARNING: TikTok auto-install failed for {serial}: {e}"
    yield "Done."
```

Key changes from the old version:
- Adds `count` parameter with the None-default contract.
- Drops the `from app.repositories.accounts import unique_profile_name` inline import — no longer needed.
- Uses `next_serial_index` once at the start, then `start + i` for each name.
- Round-robins `proxies[i % len(proxies)]`.
- Early-returns on empty `proxies` and `count < 1`.

- [ ] **Step 2.4: Run service tests, verify all pass**

```bash
.venv/bin/pytest tests/test_geelark_service_create_for_group.py -v
```
Expected: 9 passed (2 old + 7 new).

- [ ] **Step 2.5: Commit**

```bash
git add app/services/geelark_service.py tests/test_geelark_service_create_for_group.py
git commit -m "feat(geelark_service): create N phones with round-robin proxies"
```

---

## Task 3: API endpoint accepts `count`

`POST /api/groups/{gid}/phones/create` validates and forwards `count`.

**Files:**
- Modify: `app/api/groups.py:75-88` (the `create_phones` endpoint)
- Test: `tests/test_api.py`

- [ ] **Step 3.1: Look at the existing test setup**

Open `tests/test_api.py` and find where group/phones endpoints are tested. There are likely tests around line 449/517 (saw "patch /api/accounts" tests near there). Find an existing test that creates a group via `POST /api/groups` so the new tests follow the same setup pattern.

- [ ] **Step 3.2: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_phones_create_accepts_count(client, monkeypatch):
    """count gets passed through to the service layer."""
    client.post("/api/login", json={"password": "pw"})
    gid = client.post("/api/groups", json={"name": "wiz-A"}).json()["id"]

    captured: dict = {}

    def fake_create(conn, c, group_id, proxies, count=None):
        captured["count"] = count
        captured["proxies"] = proxies
        yield "Done."

    monkeypatch.setattr(
        "app.services.geelark_service.create_profiles_for_group",
        fake_create)
    monkeypatch.setattr(
        "app.services.geelark_service.make_client",
        lambda: object())

    r = client.post(f"/api/groups/{gid}/phones/create",
                    json={"proxies": ["socks5://p:1080"], "count": 4})
    assert r.status_code == 200, r.text
    # Drain SSE so the generator actually runs
    list(r.iter_lines())
    assert captured["count"] == 4
    assert captured["proxies"] == ["socks5://p:1080"]


def test_phones_create_without_count_passes_none(client, monkeypatch):
    """Backwards compatibility — old front ends omitting count work."""
    client.post("/api/login", json={"password": "pw"})
    gid = client.post("/api/groups", json={"name": "wiz-B"}).json()["id"]

    captured: dict = {}

    def fake_create(conn, c, group_id, proxies, count=None):
        captured["count"] = count
        yield "Done."

    monkeypatch.setattr(
        "app.services.geelark_service.create_profiles_for_group",
        fake_create)
    monkeypatch.setattr(
        "app.services.geelark_service.make_client",
        lambda: object())

    r = client.post(f"/api/groups/{gid}/phones/create",
                    json={"proxies": ["socks5://p:1080"]})
    assert r.status_code == 200
    list(r.iter_lines())
    assert captured["count"] is None


def test_phones_create_rejects_zero_count(client):
    client.post("/api/login", json={"password": "pw"})
    gid = client.post("/api/groups", json={"name": "wiz-C"}).json()["id"]
    r = client.post(f"/api/groups/{gid}/phones/create",
                    json={"proxies": ["socks5://p:1080"], "count": 0})
    assert r.status_code == 400


def test_phones_create_rejects_negative_count(client):
    client.post("/api/login", json={"password": "pw"})
    gid = client.post("/api/groups", json={"name": "wiz-D"}).json()["id"]
    r = client.post(f"/api/groups/{gid}/phones/create",
                    json={"proxies": ["socks5://p:1080"], "count": -1})
    assert r.status_code == 400


def test_phones_create_rejects_non_int_count(client):
    client.post("/api/login", json={"password": "pw"})
    gid = client.post("/api/groups", json={"name": "wiz-E"}).json()["id"]
    r = client.post(f"/api/groups/{gid}/phones/create",
                    json={"proxies": ["socks5://p:1080"], "count": "five"})
    assert r.status_code == 400
```

- [ ] **Step 3.3: Run and verify they fail**

```bash
.venv/bin/pytest tests/test_api.py -k phones_create -v
```
Expected: 5 failures (validation accepts everything currently; tests assert specific behaviors).

- [ ] **Step 3.4: Modify the endpoint**

Replace `app/api/groups.py:75-88` (the `create_phones` function):

```python
@router.post("/{group_id}/phones/create")
def create_phones(group_id: int, body: dict):
    from app.services import groups_service as gs
    from app.services import geelark_service
    from app.sse import sse_from_generator, locked_generator
    if gs.get(_db(), group_id) is None:
        raise HTTPException(status_code=404, detail="group not found")
    proxies = body.get("proxies") or []
    if not isinstance(proxies, list) or not proxies:
        raise HTTPException(status_code=400,
                            detail="proxies must be a non-empty list")
    count = body.get("count")
    if count is not None:
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise HTTPException(status_code=400,
                                detail="count must be a positive integer")
    return sse_from_generator(locked_generator(
        geelark_service.create_profiles_for_group(
            _db(), geelark_service.make_client(), group_id, proxies,
            count=count)))
```

Notes:
- `isinstance(count, bool)` guard is because `bool` is a subclass of `int` in Python — without it `count: true` would pass the type check and reach the service as `count=1`.
- `count=None` is forwarded explicitly so the service's None-default path is preserved.

- [ ] **Step 3.5: Run API tests, verify pass**

```bash
.venv/bin/pytest tests/test_api.py -k phones_create -v
```
Expected: 5 passed.

- [ ] **Step 3.6: Commit**

```bash
git add app/api/groups.py tests/test_api.py
git commit -m "feat(api): /phones/create accepts count, validates positive int"
```

---

## Task 4: Front-end — wire `count` through wizard state

Add the field to shared state, accept it in Step 1, pass it from Step 2.

**Files:**
- Modify: `frontend/src/components/wizard/GroupWizard.tsx` (WizardState type and initial state)
- Modify: `frontend/src/components/wizard/Step1Proxies.tsx` (new number input + defaulting logic)
- Modify: `frontend/src/components/wizard/Step2Phones.tsx` (forward count, summary copy)
- Modify: `frontend/src/i18n/locales/en.json` (new s1 keys, revised s2.summary)
- Modify: `frontend/src/i18n/locales/zh.json` (same)

- [ ] **Step 4.1: Add i18n keys**

In `frontend/src/i18n/locales/en.json`, in the `wizard.s1` object (after `"select_at_least_one"`):
```json
      "select_at_least_one": "Select at least one proxy to continue.",
      "count": "Phones to create",
      "count_hint": "Leave empty to match the number of usable proxies",
      "count_invalid": "Phones to create must be a positive integer"
```

In the same file, change `wizard.s2.summary`:
```json
    "s2": {
      "summary": "Will create {{count}} phones for \"{{name}}\" using {{proxies}} proxies.",
```

In `frontend/src/i18n/locales/zh.json`, mirror the changes:
```json
      "select_at_least_one": "请至少勾选一个代理继续。",
      "count": "手机数量",
      "count_hint": "留空 = 使用可用代理的数量",
      "count_invalid": "手机数量必须是正整数"
```
```json
    "s2": {
      "summary": "将为 \"{{name}}\" 创建 {{count}} 台手机，使用 {{proxies}} 个代理。",
```

- [ ] **Step 4.2: Extend `WizardState`**

Edit `frontend/src/components/wizard/GroupWizard.tsx`:

```typescript
export type WizardState = {
  name: string
  notes: string
  proxies: string[]
  count: string            // kept as string so empty/typing states are first-class
  groupId: number | null
  createdPhones: { id: number; name: string }[]
}
```

And the initial state in `useState`:
```typescript
  const [state, setState] = useState<WizardState>({
    name: '', notes: '', proxies: [], count: '',
    groupId: initialGroupId, createdPhones: [],
  })
```

- [ ] **Step 4.3: Add the count input to Step 1**

Edit `frontend/src/components/wizard/Step1Proxies.tsx`.

Add the input below the existing proxy `<textarea>` block (the one ending with `placeholder="socks5://user:pass@host:port" />`). Right before the `<div className="flex gap-2">` containing the Check button, insert:

```tsx
      <div>
        <label className="block text-sm font-medium mb-1">
          {t('wizard.s1.count')}
        </label>
        <input
          type="number"
          min={1}
          className="border rounded w-32 px-2 py-1"
          value={state.count}
          onChange={e => patch({ count: e.target.value })}
          placeholder=""
        />
        <div className="text-xs text-gray-500 mt-1">
          {t('wizard.s1.count_hint')}
        </div>
      </div>
```

After a successful probe, default the count if the user hasn't typed anything. Find the line `setSelected(new Set(data.filter(r => r.ok).map(r => r.proxy)))` in `runCheck` and immediately after it add:

```typescript
    if (!state.count.trim()) {
      patch({ count: String(data.filter(r => r.ok).length) })
    }
```

Update `proceed()` to validate the count. Replace:
```typescript
  function proceed() {
    const chosen = (results || []).filter(r => selected.has(r.proxy))
                                    .map(r => r.proxy)
    if (!chosen.length) { setErr(t('wizard.s1.select_at_least_one')); return }
    setSubmitting(true)
    patch({ proxies: chosen })
    onNext()
  }
```
with:
```typescript
  function proceed() {
    const chosen = (results || []).filter(r => selected.has(r.proxy))
                                    .map(r => r.proxy)
    if (!chosen.length) { setErr(t('wizard.s1.select_at_least_one')); return }
    const n = parseInt(state.count, 10)
    if (!Number.isFinite(n) || n < 1) {
      setErr(t('wizard.s1.count_invalid')); return
    }
    setSubmitting(true)
    patch({ proxies: chosen })
    onNext()
  }
```

- [ ] **Step 4.4: Forward count from Step 2**

Edit `frontend/src/components/wizard/Step2Phones.tsx`. Replace the `start()` function's fetch body:

```typescript
      body: JSON.stringify({ proxies: state.proxies }),
```

with:
```typescript
      body: JSON.stringify({ proxies: state.proxies,
                              count: parseInt(state.count, 10) }),
```

Update the summary block:
```tsx
      <div className="text-sm">
        {t('wizard.s2.summary', { count: parseInt(state.count, 10) || state.proxies.length,
                                    proxies: state.proxies.length,
                                    name: state.name })}
      </div>
```

- [ ] **Step 4.5: Build the frontend**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
```
Expected: succeeds with no TypeScript errors. If `tsc -b` complains about an unused import or `state.count` type mismatch, fix the obvious thing it points at — likely a missing optional chain or `state.count` defined only on one of the two wizard step props. Re-build until clean.

- [ ] **Step 4.6: Commit**

```bash
git add frontend/src/components/wizard/GroupWizard.tsx \
        frontend/src/components/wizard/Step1Proxies.tsx \
        frontend/src/components/wizard/Step2Phones.tsx \
        frontend/src/i18n/locales/en.json \
        frontend/src/i18n/locales/zh.json \
        frontend/dist
git commit -m "feat(wizard): phones-to-create count input in step 1"
```

---

## Task 5: Full-suite verification

A regression guard before deploy.

- [ ] **Step 5.1: Run the entire backend test suite**

```bash
.venv/bin/pytest -q
```
Expected: all green. Prior runs in this session were at 235 passed; this plan adds 6 (Task 1) + 7 (Task 2) + 5 (Task 3) = 18 new tests, so expect **253 passed**. Any unrelated failure means investigate before deploying.

- [ ] **Step 5.2: Spot-check that the frontend build is shipped in `frontend/dist`**

```bash
ls -lh frontend/dist/assets/
```
Expected: a `.js` and `.css` newer than the last `npm run build`. (The dist files are part of the deploy tarball.)

- [ ] **Step 5.3: Sanity-test the new endpoint locally — OPTIONAL but valuable**

Skip if you're confident; otherwise (the `jq` chain pulls the new group's id out automatically):
```bash
./run.sh &
sleep 3
curl -s -c /tmp/c -X POST localhost:8080/api/login \
  -H 'Content-Type: application/json' -d '{"password":"changeme"}' > /dev/null
GID=$(curl -s -b /tmp/c -X POST localhost:8080/api/groups \
        -H 'Content-Type: application/json' \
        -d '{"name":"local-test"}' | jq -r .id)
curl -s -b /tmp/c -o /dev/null -w 'HTTP %{http_code}\n' \
  -X POST localhost:8080/api/groups/$GID/phones/create \
  -H 'Content-Type: application/json' \
  -d '{"proxies":["socks5://1.2.3.4:1080"],"count":0}'
# Expect: HTTP 400
kill %1
```

---

## Task 6: Deploy to production

Same flow as the previous deploy in this session.

- [ ] **Step 6.1: Build frontend (in case Task 4.5 was on a different commit)**

```bash
export PATH="/opt/homebrew/bin:$PATH"
cd /Users/Zhuanz/Documents/mkt/frontend && npm run build
```

- [ ] **Step 6.2: Package**

```bash
cd /Users/Zhuanz/Documents/mkt
tar -czf /tmp/mkt-deploy.tar.gz app geelark_client.py generate_caption.py \
        requirements.txt frontend/dist
ls -lh /tmp/mkt-deploy.tar.gz
```

- [ ] **Step 6.3: Upload**

```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
    /tmp/mkt-deploy.tar.gz root@1.15.155.204:/tmp/mkt-deploy.tar.gz
```

- [ ] **Step 6.4: Extract + restart**

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@1.15.155.204 '
  set -e
  cd /opt/mkt
  tar -xzf /tmp/mkt-deploy.tar.gz
  md5sum requirements.txt
  systemctl restart mkt
  sleep 2
  systemctl is-active mkt
'
```
Expected: `active`. requirements.txt md5 has not changed since the last deploy (`3fc15617442856f5c904c6c78ffd7982`), so pip install is skipped.

- [ ] **Step 6.5: Smoke test prod**

```bash
curl -sS -o /dev/null -w 'HTTP %{http_code} time %{time_total}s\n' \
     http://1.15.155.204:8080/
curl -sS -o /dev/null -w 'HTTP %{http_code} time %{time_total}s\n' \
     http://1.15.155.204:8080/api/accounts
```
Expected: `/` → 200, `/api/accounts` → 401.

---

## Out of scope (do not start here)

- Cleaning up the orphan `test111-01` / `test111-01 (2)` rows on prod.
- Per-row retry UI when a phone create fails.
- TikTok fingerprinting warnings when one proxy is shared across many phones.

These are noted in the spec's "Out of scope" section and remain followups.
