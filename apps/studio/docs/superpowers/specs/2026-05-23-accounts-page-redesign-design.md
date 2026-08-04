# Accounts page redesign — read-only table + right-side edit drawer

**Date:** 2026-05-23
**Scope:** Frontend SPA only (`frontend/src/pages/Accounts.tsx`, `frontend/src/components/ui.tsx`, `frontend/src/i18n/locales/*.json`, possibly a new helper in `frontend/src/lib/`). No backend or schema changes.

## Problem

The current Accounts table crams 9 columns into one row, three of which hold long strings that get visually truncated to uselessness:

- **Email** — addresses like `something.long.address@gmail.com` exceed any reasonable column width.
- **Proxy** — `socks5://user:password@host.example.com:1080` is long *and* leaks the password in plain text.
- **Picture URL** — long signed CDN URLs that nobody reads. The user wants to see the *image*, not the URL.

Every long field is rendered as an `<InlineInput>` stretched to a narrow column, so they're both unreadable and uncomfortable to edit. The page works but feels wrong.

## Goal

Make the Accounts page comfortable to read at a glance and pleasant to edit, without losing the dense tabular "compare many accounts" affordance.

## Non-goals

- Backend, DB, or scheduler changes. The data shape on the wire stays the same.
- Multi-select / batch edit / column reorder / column-width customization. Out of scope.
- Splitting `proxy` into discrete host/port/user/pass DB fields. GeeLark import already stores a single URL string (see commit `5fc1941`); we keep that.
- Touching `Videos`, `Settings`, `Dashboard`, or `Login` pages.

## Design

### Approach

**Read-only main table + right-side editing drawer.** The table becomes a status-only display optimized for scanning. All text editing moves into a drawer that slides in from the right with full-width inputs. One deliberate exception: the **Logged in** toggle stays inline because it's a high-frequency one-tap action.

### Main table — columns

8 columns (down from 9). The "Picture URL" column is removed entirely; its function is absorbed by an avatar thumbnail in the Account column.

| # | Column | Cell content |
|---|---|---|
| 1 | **Account** | 32px round `<Avatar>` (image from `profile_pic_url` or monogram fallback) · `profile_name` (medium weight, ink) · `Phone #{geelark_phone_id}` or `No phone linked` (11.5px ink-4) |
| 2 | **Email** | Plain text; if longer than the cell, truncate with ellipsis and add `title` tooltip; empty → `—` in ink-5 |
| 3 | **Proxy** | Parsed `host:port` only (no scheme, no credentials); `title` tooltip carries the full URL (still useful for debugging on hover); empty → `—` |
| 4 | **Max warm** | Number, right-aligned, tabular-nums |
| 5 | **Logged in** | `<Toggle>` — the one inline-editable cell. `onClick` calls `stopPropagation` so it never opens the drawer. |
| 6 | **Warm progress** | Existing `<ProgressBar>` / "Warmed" `<Badge>` logic, unchanged |
| 7 | **Last warm** | Existing ✓ / ✗ / `—` logic, unchanged |
| 8 | (no header) | Hover-only `→` glyph hinting "click to open". 24px wide. |

The whole `<Tr>` becomes clickable: `cursor-pointer`, hover background. Clicking anywhere except the toggle opens the drawer for that row.

**Delete is no longer in the main row.** It moves into the drawer footer (left-aligned, danger-styled) where the user has just confirmed which account they're looking at.

### Drawer

A new general-purpose `<Drawer>` component in `components/ui.tsx`:

- Mounts a `<dialog>` (same primitive as `<Modal>`) positioned right; 480px wide, full viewport height, 16px outer padding.
- Slide-in animation from the right edge (`translate-x` transition, 180ms ease).
- Semi-transparent backdrop. ESC and backdrop-click close.
- If the form is dirty, closing prompts a `confirm()` "Discard unsaved changes?" (Chinese / English via i18n).

Drawer content for an account:

- **Header** — `Edit account` title + dim subtitle showing `profile_name`. Close button (✕).
- **Body** — vertical sections, each prefixed with a small `<SectionTitle>`-style label:
  1. **Identity**
     - `Profile name` — editable `<TextField>`
     - `Phone` — read-only `geelark_phone_id` (small mono text) or "No phone linked"
  2. **Avatar**
     - `Picture URL` — full-width `<TextField>`
     - Live 80×80 rounded preview thumbnail below the input. Debounced 300ms after typing stops. On image load error, show a small "Cannot load image" caption in ink-4 over a gray placeholder.
  3. **Credentials**
     - `Email` — full-width `<TextField>`
     - `Proxy` — full-width `<TextField>`, font-mono so the URL is readable. Placeholder `socks5://user:pass@host:port`.
  4. **Warm-up**
     - `Max warm time` — numeric `<TextField>` (or `<TextField type="number">`)
- **Footer** — single horizontal bar:
  - Left: `Delete account` (danger ghost button)
  - Right: `Cancel` (secondary) + `Save changes` (primary, disabled when not dirty)

**Save semantics:** transactional. The drawer holds its own form state (one `useState` object initialized from the row when opened). `Save changes` computes the diff and calls `api.updateAccount(id, diff)` exactly once. If the diff is empty the button is disabled. On success, the drawer closes and the table reloads via the existing `load()` path. On failure, a toast fires and the drawer stays open.

This is intentionally different from the current "save on blur" inline pattern — drawers are transactional surfaces and on-blur saves don't fit them.

### `<Avatar>` component (new)

```ts
<Avatar url={r.profile_pic_url} name={r.profile_name} size={32} />
```

Behavior:

- If `url` is non-empty, render `<img src={url}>` in a rounded clipped wrapper.
- On `<img>` `onError`, fall through to the monogram fallback (so a 404'd link doesn't show a broken-image icon).
- Monogram: first letter of `profile_name` uppercased, centered on a soft colored circle. Color is deterministic from a simple hash of `name` so the same account always gets the same color. Palette: 6–8 muted, Apple-style tints (e.g. soft blue, mint, peach, lavender, sand, sage).

### `parseProxyHost` helper (new)

In `frontend/src/lib/proxy.ts`:

```ts
export function parseProxyHost(proxy: string | null): string | null
```

Accepts a string like `socks5://user:pass@host.example.com:1080` and returns `host.example.com:1080`. If the URL has no port, returns just `host.example.com`. If parsing fails (e.g. malformed input), returns the original string trimmed so we still show *something* (and the tooltip carries the full original). Empty / null input → `null`. Uses the built-in `URL` constructor; protocol is rewritten to `http://` before parsing because Safari's `URL` rejects `socks5://`.

### What stays unchanged

- `<PageTitle>` with subtitle stats and the four header buttons (Import / Set Pictures / Create Profiles / Add Account).
- The `LogPanel` shown during `createProfiles` streaming.
- The Add-account `<Modal>`.
- The empty-state copy.
- All backend endpoints, the i18n bundle structure, and the `Account` row type.

### File map

```
frontend/src/
  pages/Accounts.tsx              # rewritten: read-only rows, opens drawer
  components/ui.tsx               # + <Drawer>, + <Avatar>
  lib/proxy.ts                    # NEW — parseProxyHost
  i18n/locales/en.json            # + accounts.drawer.* + accounts.col.* tweaks
  i18n/locales/zh.json            # + accounts.drawer.* + accounts.col.* tweaks
```

### New i18n keys (under `accounts.`)

```
col.account                      # replaces col.profile header
drawer.title
drawer.identity
drawer.identityName
drawer.identityPhone
drawer.identityNoPhone
drawer.avatar
drawer.avatarUrl
drawer.avatarPreviewError
drawer.credentials
drawer.email
drawer.proxy
drawer.proxyPlaceholder
drawer.warmup
drawer.maxWarm
drawer.deleteAccount
drawer.saveChanges
drawer.unsavedConfirm
emptyValue                       # "—" rendered as literal but i18n-keyed for consistency (optional)
```

The "Picture URL" header key (`accounts.col.pictureUrl`) becomes unused — remove from both locale files.

### Interaction details

- **Row click** opens drawer. The login toggle's `onClick` calls `event.stopPropagation()`. So does the toggle's keyboard activation (`onKeyDown` if applicable).
- **Drawer open** populates a fresh form state from the row snapshot at click time. Subsequent reloads of `rows` don't mutate the open drawer's state.
- **Picture URL preview** re-renders 300ms after the URL field stops changing. Implementation: a small `useDebouncedValue(url, 300)` hook local to the drawer.
- **Save** sends only changed fields as the PATCH body. Server already accepts arbitrary subsets via `updateAccount`.
- **Delete inside drawer** keeps the existing confirmation pattern (`confirm()` with `accounts.deleteConfirm`).

## Testing strategy

This is a frontend-only redesign with no backend changes; existing `pytest` suite is unaffected and must still pass (38 tests).

Manual QA checklist (recorded as the verification gate before commit):

1. Open Accounts page. Avatar thumbnails render for accounts with `profile_pic_url`; monogram fallback renders for accounts without.
2. Long email and long proxy show truncated in the cell with full text in `title` tooltip.
3. Proxy cell shows `host:port` only, no password.
4. Click anywhere on a row (except the Login toggle) — drawer slides in from the right.
5. Click the Login toggle — toggle state changes, drawer does **not** open.
6. In the drawer, change one field, click Save — toast fires (existing pattern), drawer closes, row updates.
7. In the drawer, change a field, click Cancel / press ESC / click backdrop — confirm dialog fires; choosing Cancel discards the change.
8. In the drawer, change nothing, click Cancel — closes without confirm.
9. Paste a Picture URL in the drawer — 80×80 preview appears 300ms later. Paste an obviously invalid URL — error caption appears.
10. In the drawer, click Delete — existing delete confirmation flow runs; on confirm the drawer closes and the row disappears.
11. Toggle the sidebar language to 中文 — all new drawer labels translate.
12. `npm run build` succeeds with no new TypeScript errors.

## Out-of-design decisions deliberately taken

- Drawer width is **480px**, not 420 or 560. 480 fits a long proxy URL in a single line at the default font without horizontal scrolling, and still leaves the underlying table partially visible on a 1440px screen.
- Save model is **transactional** (one Save button), not on-blur. Drawers carry the user-expectation of "commit when I'm done." On-blur in a drawer leads to half-saved state when the user clicks Cancel expecting it to revert.
- Avatar fallback is a **monogram with deterministic color**, not a generic person silhouette. Distinguishes accounts at a glance even before pictures are set.
- Proxy stays a **single string field** in the drawer. The DB stores a single string; splitting in the UI then re-joining for save adds complexity for marginal benefit. The drawer's full-width mono input makes a long URL readable.

## Rollout

Single PR. No feature flag. The change is purely client-side and entirely visual/interaction-level; backend stays compatible.
