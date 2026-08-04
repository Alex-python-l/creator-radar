# Design: Multiple phones per proxy in the create-phones wizard

- **Date:** 2026-05-25
- **Status:** Approved (design); implementation plan pending
- **Scope:** changes the Step 1 input shape, the `phones/create` API, and the
  `create_profiles_for_group` service in the existing groups wizard

## 1. Problem & context

The current wizard binds exactly one cloud phone to each pasted proxy line —
`create_profiles_for_group` iterates `for proxy in proxies` and creates one
phone per iteration. Operators routinely have a small number of proxies (often
just one residential SOCKS5) and want N phones behind them. Today they would
have to paste the same line N times, which is friction and also creates a
visual mismatch with the proxy-list checkbox UI in Step 1.

A second pain point is name collisions. Names are generated as
`{group_name}-{idx:02d}` where `idx` always restarts at 1, then
`unique_profile_name` appends `(2)`, `(3)`... on conflict. The result on
production is rows like `test111-01` and `test111-01 (2)` left behind from
failed earlier attempts, and a fresh creation in the same group restarts
from `-01` again and immediately collides. The user wants names generated so
this never happens.

## 2. Goals / non-goals

**Goals**
- Operator specifies how many phones to create, independent of how many
  proxies were pasted. The proxies are round-robined across the phones.
- Names continue to follow `{group_name}-NN` and are guaranteed unique inside
  the group without ever appending ` (2)` suffixes — by skipping any `NN` that
  is already used in that group.
- Existing 1:1 wizard behavior is preserved when the user doesn't fill in the
  new field.
- Failed accounts rows from earlier attempts (those with
  `geelark_phone_id IS NULL`) are not touched by this change but are skipped
  naturally by the new naming logic.

**Non-goals**
- Cleaning up the existing `test111-01` / `test111-01 (2)` orphan rows. Those
  may or may not have corresponding phones on GeeLark's side; sorting that out
  belongs in a separate task.
- Per-row retry, parallel phone creation, or any change to the underlying
  GeeLark `create_phones` call shape.
- Removing the `unique_profile_name` helper — `import_from_geelark` and other
  paths still need it.
- Warnings about TikTok fingerprinting risk from sharing one proxy across many
  accounts. The operator has explicitly asked for this capability.

## 3. Architecture

No new components. Three existing layers change:

```
Step1Proxies.tsx ── proxies + count ──▶ POST /api/groups/{gid}/phones/create
                                           │
                                           ▼
                                  create_profiles_for_group(
                                    conn, client, group_id,
                                    proxies, count)
                                           │
                            ┌──────────────┴───────────────┐
                            ▼                              ▼
                    next_serial_index(conn,         GeeLark create_phones,
                    group_id, group_name)           one per phone, with the
                    -> start_idx                    round-robin proxy
                            │
                            ▼
                    serialName = f"{group_name}-{(start_idx+i):02d}"
                                  for i in range(count)
```

## 4. Component details

### 4.1 `next_serial_index(conn, group_id, group_name) -> int`

New helper in `app/repositories/accounts.py` next to `unique_profile_name`.

- Scans `accounts.profile_name` for the given `group_id` where the name starts
  with `{group_name}-`.
- For each matching row, takes the substring after the first `-`, stops at the
  first non-digit character, and parses what's left as an integer. So
  `test111-01` → `1`, `test111-01 (2)` → `1` (the ` (2)` suffix is discarded),
  `test111-100` → `100`. Rows where nothing parses (`test111-foo`) are
  ignored.
- Returns `max(seen) + 1`, or `1` if no row matches. Width of the formatted
  output is still `02d` — once we cross 99 the names just grow to three
  digits.

Pure SQL + Python; no GeeLark call.

### 4.2 `create_profiles_for_group(conn, client, group_id, proxies, count=None)`

Existing function in `app/services/geelark_service.py:196`. Signature gains an
optional `count`:

```python
def create_profiles_for_group(conn, client, group_id: int,
                              proxies: list[str],
                              count: int | None = None):
```

Behavior:

1. `count = count if count is not None else len(proxies)`. Preserves the old
   1:1 contract for any caller that doesn't pass `count` (including tests).
2. Reject `count < 1` and `not proxies` with an early `yield "FAILED: ..."`
   line and `return`. Mirrors the existing top-of-function error style.
3. `start = next_serial_index(conn, group_id, group_name)`.
4. Loop `for i in range(count)`:
   - `proxy = proxies[i % len(proxies)]`
   - `serial = f"{group_name}-{(start + i):02d}"`
   - **No** `unique_profile_name` wrap. `next_serial_index` already guarantees
     uniqueness within the group; cross-group collisions are still impossible
     because `profile_name` is globally unique only by accident — but the
     pre-existing UNIQUE constraint on `profile_name` will still raise if
     somehow the name is taken, which surfaces correctly via the generator's
     exception handling.
5. The rest of the loop (insert row, call GeeLark `create_phones`, install
   TikTok) is unchanged.

### 4.3 `POST /api/groups/{gid}/phones/create`

Existing endpoint in `app/api/groups.py:75`. Body extended:

```json
{ "proxies": ["socks5://..."], "count": 3 }
```

- `count` is optional. If omitted, the service defaults it to `len(proxies)`.
- Validation: if `count` is present, it must be an `int` ≥ 1; otherwise return
  400. `proxies` non-empty stays as-is.
- No upper bound on `count`. GeeLark API errors (rate limit, quota) surface
  via the existing per-phone failure path.

### 4.4 Step 1 wizard UI (`Step1Proxies.tsx`)

Below the proxy `<textarea>` and above the Check button:

```
Phones to create  [   ]    (leave empty = same as usable proxies)
```

- Bound to a new `count` field on `WizardState`. Stored as `string` (text
  input) so empty is meaningful; coerced to integer at submit time.
- After a successful probe, if `count` is empty, fill it with the number of
  proxies the user has checked (preserves the "I just want one phone per
  proxy" muscle memory).
- "Next →" enables when the user has selected ≥1 proxy **and** the resolved
  count is ≥1.
- The Step 2 summary text changes from "create N phones from N proxies" to
  "create N phones using X proxies" — the strings already live in the i18n
  files, this is a small wording update.
- Step2 reads `state.count` (falling back to `state.proxies.length`) and
  passes it through to the API call.

## 5. Data flow examples

| Pasted proxies (ok) | count | Phones created | Names assigned (group "g", no priors) |
|---|---|---|---|
| 1 | 1 (default) | 1 | `g-01` |
| 1 | 3 | 3 (all on the one proxy) | `g-01`, `g-02`, `g-03` |
| 2 | 2 (default) | 2 (one each) | `g-01`, `g-02` |
| 2 | 5 | 5 (proxies 1,2,1,2,1) | `g-01`..`g-05` |
| 3 | 2 | 2 (proxies 1,2 — proxy 3 unused this round) | `g-01`, `g-02` |

If group "g" already had rows `g-01` and `g-01 (2)`, `next_serial_index`
returns 2, so the second row above produces `g-02`, `g-03`, `g-04`.

## 6. Error handling

| Situation | Behavior |
|---|---|
| `count` missing or 0/negative | API 400. Front end disables Next while count < 1. |
| `proxies` empty | API 400 (existing behavior). |
| `count` larger than GeeLark's per-call limit | Each phone is its own `create_phones` call already (loop, not batch), so this never triggers. Per-phone failures bubble up via the existing yielded log lines. |
| A specific phone fails to create | Account row is kept (with `geelark_phone_id IS NULL`), the loop continues. Identical to today. |
| Name collision against a non-group row (shouldn't happen) | The UNIQUE constraint raises; the generator's `try/except` around `create_phones` doesn't catch it. We accept this for now — `next_serial_index` makes it unreachable for any valid input. |

## 7. Testing

All in pytest, no GeeLark hits:

- `tests/test_repositories.py` (or a new file alongside) — `next_serial_index`:
  - empty group → 1
  - group with `{g}-01` only → 2
  - group with `{g}-01`, `{g}-01 (2)`, `{g}-02` → 3 (the `(2)` row is
    treated as occupying slot 1)
  - other-group names are ignored
- `tests/test_geelark_service_create_for_group.py` — extend with:
  - `count=None` still produces 1:1 (regression guard)
  - `(proxies=[p1], count=3)` → fake client called 3 times, each with `p1`,
    serialNames `g-01`, `g-02`, `g-03`
  - `(proxies=[p1,p2], count=5)` → proxies cycle p1,p2,p1,p2,p1
  - `count=0` yields the failure line and returns without calling the client
  - start_idx respects pre-existing rows
- `tests/test_api.py` — `POST /api/groups/{gid}/phones/create`:
  - body with `count` is accepted and passed through
  - body without `count` keeps old behavior
  - `count <= 0` returns 400

## 8. Migration / compatibility

- No DB schema change.
- No env var change.
- Old clients that POST `{proxies: [...]}` without `count` keep their
  exact current behavior.
- Existing tests that call `create_profiles_for_group(conn, c, gid, proxies)`
  with no `count` argument continue to pass unchanged.

## 9. Out of scope / followups

- Clean up `test111-01` and `test111-01 (2)` orphan rows. Needs a separate
  decision about whether their GeeLark-side phones (if any) should be
  reconciled or just deleted.
- A "verify on GeeLark" button for stale accounts rows whose
  `geelark_phone_id IS NULL`.
