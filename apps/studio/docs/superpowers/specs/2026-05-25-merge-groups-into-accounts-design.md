# Merge Groups tab into Accounts page — master-detail with inline wizard

**Date:** 2026-05-25
**Scope:** Frontend SPA primarily. Two new backend endpoints for multi-select batch ops; one field whitelist confirmation. No schema changes.
**Builds on:** `2026-05-24-groups-wizard-design.md` (entity model unchanged) and `2026-05-23-accounts-page-redesign-design.md` (table + drawer pattern preserved).

## Problem

The Groups tab introduced by the wizard spec works, but daily operation feels like flipping between two halves of the same task:

- To see "what's happening in batch A" → `/groups` → click A → see counts + Warmup/Publish, but cannot see individual accounts.
- To inspect an individual account in batch A → switch to `/accounts` → filter dropdown by A → open drawer.
- The Group **detail page** is essentially a title, a counts row, and two buttons. It does not justify a route.
- Three competing visual styles (Groups list, Group detail, Accounts list) carry redundant chrome.

User feedback verbatim: 导航割裂 · 详情页太空没用 · 整体视觉乱.

## Goal

Collapse Groups into a sidebar inside Accounts. The two become one master-detail view:

- Left rail (240px): the groups themselves — the master.
- Right pane: the selected group's counts, batch actions, and its accounts table — the detail.

A marketer never leaves `/accounts` during day-to-day work.

## Non-goals

- No schema changes. `groups` and `accounts` tables stay as designed in `2026-05-24-groups-wizard-design.md`.
- No change to warmup / publish job logic. New transport only ("operate on a list of account ids" alongside the existing "operate on a whole group").
- No re-design of the 4-step wizard *content*. Same steps, same SSE endpoints — only its presentation moves from a modal to the main pane.
- No Videos / Settings / Dashboard changes.
- No mobile / narrow-viewport layout (dashboard remains desktop-only).
- No group reordering, color tags, or bulk-archive of groups.

## Routing

| Before | After |
|---|---|
| `/groups` | removed → 301 to `/accounts` |
| `/groups/:id` | removed → 301 to `/accounts?group=:id` |
| `/accounts` | unchanged path; new master-detail layout |
| `/accounts?group=:id` | selects that group on mount |
| `/accounts?group=all` (default if omitted) | "All accounts" mode |
| `/accounts?wizard=new` | triggers wizard: creates an empty draft group, then navigates to `/accounts?group=<new id>&wizard=1` |

Frontend deletions: `frontend/src/pages/Groups.tsx`, `frontend/src/pages/GroupDetail.tsx`. Top-nav entry for Groups removed from `frontend/src/components/Shell.tsx` (or wherever the nav is defined — current location to be confirmed during implementation).

## Layout

```
+--------------+--------------------------------------------------+
| 240px        | header: <group name>  ▶ Warmup  ↑ Publish  ⋯    |
| group rail   | counts row: 📱 N  🔑 N  🔥 N  ✅ N              |
|              | toolbar:    🔍 search  status▾  +Profile  Import |
|              | accounts table (rows = accounts in this group)   |
|              |   [☐ header]  [☐] avatar · name · tt · proxy ...|
|              |                                                  |
+--------------+--------------------------------------------------+
```

### Left rail — `frontend/src/components/GroupRail.tsx` (new)

- Top: heading "分组" + `+ 新建` button (navigates to `/accounts?wizard=new`).
- Search input filters list client-side by group name.
- Pinned first row: **全部账号 N** (selected by default if URL has no `group` param). `N` = count of all `accounts` rows regardless of their group's archived state — accounts are not soft-deleted when their group is archived; the rail count for an archived group's row counts the same accounts.
- One row per active group: name + count badge from `counts.total_accounts` already returned by `GET /api/groups`.
- Selected row: blue fill, white text.
- Bottom collapsible "已归档 (N)" section; only rendered if any archived groups exist. Items styled muted.

### Main pane — modes

The pane has three modes, selected by URL:

| Mode | URL | Header | Counts cards | Table extras |
|---|---|---|---|---|
| **All accounts** | `?group=all` or none | Title only ("全部账号 N 个") | hidden | extra column **所属分组**; checkbox toolbar restricted (see below) |
| **Active group** | `?group=<id>` of non-archived | Name · notes · Warmup / Publish / ⋯ | shown | standard |
| **Archived group** | `?group=<id>` of archived | Name prefixed "📦 (归档)" · only `恢复分组` button | shown (muted) | read-only |

#### Header overflow `⋯`
Active group only: `重命名 · 归档 · 删除`. Delete is disabled unless `total_accounts === 0` (server rejects otherwise; UI mirrors).

#### Counts cards
Four cards in a grid: 📱 手机 (`total_phones / target_phone_count`) · 🔑 已登录 · 🔥 已 Warm · ✅ 已发布. Fields already in the `counts` payload from `GET /api/groups/:id`.

#### Selection toolbar (replaces header bar when ≥1 row checked)

Sticky in the same vertical slot as the header:

| Mode | Buttons |
|---|---|
| Active group | `已选 N · ▶ Warmup · ↑ Publish · → 移到分组 · 删除 · 取消` |
| All accounts | `已选 N · → 移到分组 · 删除 · 取消` (no Warmup/Publish — those need a single active group context) |
| Archived group | toolbar never appears (table read-only) |

Esc or 取消 clears selection. Switching the left-rail selection clears the checked set.

#### Account table

- First column: checkbox. Header checkbox toggles all currently visible rows (after search/filter).
- In **All accounts** mode: extra column **所属分组** between **TikTok** and **代理**, value = group name or `—`.
- All other columns and behaviors preserved from `2026-05-23-accounts-page-redesign-design.md` (the read-only-table + edit-drawer pattern).

#### Drawer

- All existing fields preserved.
- New `所属分组` `<Select>`: list of active groups + a "(无)" sentinel. Saves via `PATCH /api/accounts/:id { group_id }`.
- Archived-group mode: every input disabled; Save and Delete buttons hidden.

## Wizard — W2 inline ("main pane takes over")

Trigger: click left-rail `+ 新建` or navigate to `/accounts?wizard=new`.

1. On load with `?wizard=new`, immediately `POST /api/groups` to create an empty draft. The empty-draft cleanup behaviour from commit `9617e3c` already handles cancel-before-step-2.
2. Replace URL with `/accounts?group=<new id>&wizard=1`.
3. Left rail shows the draft selected with a `(草稿)` suffix and amber dot.
4. Main pane renders the wizard component (`frontend/src/components/GroupWizard.tsx`, already exists for modal mode — refactor below).
5. Step bar at top of pane: `① 代理 → ② 创建手机 → ③ 解析凭证 → ④ 标记登录`. Active step blue, completed green check, pending grey.
6. **Cancel** bottom-left of each step:
   - Step 1 (no phones yet): triggers existing cleanup → draft removed from rail → navigate `/accounts`.
   - Step ≥ 2: keep the partially-built group → navigate `/accounts?group=<id>` (drop `wizard=1`); user can resume by clicking the rail entry (it remains marked `(草稿)` until step 4 completes).
7. **Done** on step 4: navigate `/accounts?group=<id>`; pane shows normal layout.

The `(草稿)` suffix on the rail entry is purely a presentational marker — render it whenever the group has not yet satisfied "all 4 steps complete", i.e. when `counts.target_phone_count > 0 && counts.logged_in < counts.target_phone_count`. This is derived from existing `counts` data; no new field.

GroupWizard refactor: add prop `mode: "modal" | "fullpane"`. Footer buttons render inline (fullpane) instead of in a modal footer. Step logic and SSE calls unchanged. Modal mode kept callable for now; the only caller (old Groups page) is deleted in the same commit, so modal mode becomes dead code that a follow-up can remove.

## New backend endpoints

Both live in `app/api/accounts.py`. They wrap existing service code with a different input shape:

```
POST /api/accounts/warmup
  body: { account_ids: number[] }
  returns: SSE stream (same event format as POST /api/groups/:id/warmup/start)

POST /api/accounts/publish
  body: { account_ids: number[] }
  returns: SSE stream (same event format as POST /api/groups/:id/publish)
```

Service-layer refactor:

- Extract `warmup_service.run_warmup_for_accounts(account_ids)` from current `run_warmup(group_id)`. The latter becomes a wrapper: resolve `group_id → account_ids`, delegate.
- Mirror for `posting_service.publish_for_accounts(account_ids)` vs `publish(group_id)`.
- Cooldown / pool-building in `assignment.py::build_pool` continues to apply to the supplied ids.

Validation (returns 400 if any fail):

- `account_ids` non-empty.
- All ids exist.
- All ids share the **same** `group_id` and that group is not archived. (Cross-group warmup makes no sense — settings like cooldown are configured per group; UI also disallows multi-group selection.)

Group endpoints `POST /api/groups/:id/warmup/start` and `POST /api/groups/:id/publish` remain — these are the header button's transport (equivalent to "select all in this group"). They now internally delegate to the account-id variant.

## PATCH /api/accounts/:id

Already accepts arbitrary updatable columns. Verify `group_id` is in the whitelist of mutable fields and that `null` is accepted (moves account to "ungrouped"). If not whitelisted today, add it; add a test either way.

## Error & edge cases

- **Last account moved out of group**: counts drop to 0; header buttons stay clickable but server returns 400 "no eligible accounts" → toast.
- **Group archived while pane is open**: next focus / poll revalidates `GET /api/groups/:id` and switches mode to read-only.
- **Wizard abandoned step 1**: existing draft-cleanup path; rail entry disappears on navigation.
- **Wizard abandoned step ≥ 2**: draft persists with `(草稿)` marker; user can resume from rail.
- **Selection state**: not persisted across page reloads or left-rail switches.
- **Browser back/forward**: URL-driven, so panes restore correctly.
- **Archived-group accounts in "All accounts" mode**: appear in the merged table; the all-accounts drawer is editable (read-only restriction applies only when viewing the archived group's own pane).
- **Renaming a group**: prompt modal from `⋯`; PATCH endpoint to be added if `PATCH /api/groups/:id` not present today — confirm during implementation, add if missing (not covered by wizard spec).

## Testing

Pytest (preserve 38-passed baseline + add):

- `tests/test_api_accounts_batch.py` (new):
  - `POST /api/accounts/warmup` happy path (single group, valid ids) → 200, SSE first event.
  - 400: empty ids, ids from two different groups, ids in archived group, nonexistent id.
  - `POST /api/accounts/publish`: mirror of above.
  - `PATCH /api/accounts/:id { group_id }` to another active group, then to null, then to nonexistent group (400).
- Existing `tests/test_groups_*` must still pass after the group endpoints become wrappers.

Manual / frontend smoke (no automated frontend tests required, consistent with current repo):

- Left rail selection updates `?group=<id>` and reloads pane data.
- Checkbox selection clears on rail switch.
- Wizard cancel from step 1 removes draft from rail.
- 301 redirects from old `/groups*` URLs land on the right pane.

## Migration / rollback

- No DB migration.
- One commit lands: deletes Groups page + GroupDetail page + nav entry; rewrites Accounts page; adds left rail + batch endpoints; refactors wizard to dual-mode.
- Rollback = `git revert` that commit.

## Out of this design

- Multi-group selection / cross-group batch ops.
- Mobile responsive layout.
- Group color tags or custom icons (🔥 emoji in mockups is illustrative only).
- Bulk archive / bulk delete of groups.
- Real-time count updates without page focus poll (covered by existing SSE on warmup/publish; rail counts refresh on focus).
- Removing modal mode of `GroupWizard` (follow-up cleanup after this lands).
