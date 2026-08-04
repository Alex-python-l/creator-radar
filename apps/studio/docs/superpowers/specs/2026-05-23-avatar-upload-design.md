# Avatar upload — replace URL paste with direct image upload

**Date:** 2026-05-23
**Scope:** Backend (`app/api/accounts.py`, `app/services/geelark_service.py`, `app/config.py`, `app/db.py`, `app/repositories/accounts.py`) + frontend drawer (`frontend/src/pages/Accounts.tsx`, i18n) + tests. No new dependencies.

## Problem

Today, marketers paste a `profile_pic_url` into the Accounts drawer; the URL is later passed verbatim to `client.edit_tiktok_profile(env_id, avatar=url)` so GeeLark's RPA bot fetches it. Pasting URLs is friction — they want to drop in an image file directly.

The hard constraint stays: GeeLark needs a URL. The redesign has to keep producing one.

## Goal

In the Accounts drawer, replace the "Picture URL" text field with an "Upload image" control. Users pick a file (or click "Remove"); the server stores the file locally and the existing `set_pics` background job re-uploads it to GeeLark's CDN on each dispatch so the URL handed to `edit_tiktok_profile` is always fresh.

## Non-goals

- No drag-drop in the drawer (click-to-pick is enough).
- No image cropping, resizing, or format conversion (no Pillow). GeeLark RPA handles odd aspect ratios.
- No batch / bulk avatar upload.
- No migration tool for accounts that still have a legacy `profile_pic_url`. Old data keeps working via the existing URL pipeline; users who want to change the avatar just upload a new file.
- No avatar library or sharing across accounts.

## Architecture

**Source of truth becomes a local file**, not a URL. The dashboard renders the file from a backend endpoint; `set_pics` uploads it to GeeLark on the fly each time it dispatches. The 3-day GeeLark CDN expiry becomes a non-issue because the URL is single-use per dispatch.

```
   Drawer "Upload"
        │
        ▼
   POST /api/accounts/{id}/avatar  (multipart)
        │
        ▼
   data/avatars/{id}.{ext}  ←─────── source of truth
        │                                  │
        │ GET /api/accounts/{id}/avatar    │ Path read by set_pics
        ▼                                  ▼
   <Avatar> in dashboard          client.upload_file(path, ext)
                                          │
                                          ▼  resourceUrl (3-day)
                                  client.edit_tiktok_profile(env_id, avatar=resourceUrl)
                                          │
                                          ▼
                                    GeeLark RPA sets TikTok avatar
```

## Data model

```sql
ALTER TABLE accounts ADD COLUMN profile_pic_path TEXT;
```

- `profile_pic_path` stores the **filename only** under `data/avatars/` (e.g. `5.jpg`), not a full path — keeps `data/` portable.
- `profile_pic_url` stays in the schema but is **only read for accounts that still have it set** (legacy). New uploads do not touch it.
- `pic_status` and `pic_task_id` semantics unchanged.

## Backend

### Config & paths

- `app/config.py`: add `AVATAR_DIR = DATA_DIR / "avatars"`.
- `app/main.py`: on startup, `AVATAR_DIR.mkdir(parents=True, exist_ok=True)` next to the existing `UPLOAD_DIR` setup.

### New endpoints in `app/api/accounts.py`

| Method & path | Behavior |
|---|---|
| `POST /api/accounts/{id}/avatar` (multipart `file`) | Validate MIME ∈ {image/jpeg, image/png, image/webp} → choose ext (`jpg`/`png`/`webp`). Validate size ≤ 5 MB. Delete any existing `data/avatars/{id}.*` (covers ext change). Stream-copy to `data/avatars/{id}.{ext}`. Update DB: `profile_pic_path={id}.{ext}`, `pic_status=NULL`, `pic_task_id=NULL`. Return the updated account row. |
| `GET /api/accounts/{id}/avatar` | If `profile_pic_path` set and file exists, return it via FastAPI `FileResponse(path, media_type="image/jpeg"\|"image/png"\|"image/webp" picked from the file's extension, headers={"Cache-Control": "private, max-age=60"})`. Else 404. |
| `DELETE /api/accounts/{id}/avatar` | `os.remove` the local file (best-effort), set `profile_pic_path=NULL` in DB. |

Validation errors return HTTP 400 with `detail` matching one of three failure modes the frontend toasts: `too_large`, `bad_format`, or generic.

### `set_pics` change in `app/services/geelark_service.py`

```python
def set_pics(conn, client) -> None:
    for a in acc_repo.list_all(conn):
        if not (a.get("tiktok_logged_in") and a.get("geelark_phone_id")):
            continue
        has_source = bool(a.get("profile_pic_path") or a.get("profile_pic_url"))
        if not has_source:
            continue
        # ... existing pic_task_id resolution logic stays the same ...
        # When dispatching a new task:
        if a.get("profile_pic_path"):
            path = config.AVATAR_DIR / a["profile_pic_path"]
            if not path.exists():
                # File got removed between upload and dispatch — fall through to URL or skip
                if not a.get("profile_pic_url"):
                    continue
                avatar_url = a["profile_pic_url"]
            else:
                ext = path.suffix.lstrip(".") or "jpg"
                avatar_url = client.upload_file(str(path), ext)
        else:
            avatar_url = a["profile_pic_url"]
        task_id = client.edit_tiktok_profile(a["geelark_phone_id"], avatar=avatar_url)
        acc_repo.update(conn, a["id"],
                        {"pic_status": "initiated", "pic_task_id": task_id})
```

The pre-existing branches that mark `pic_status` as `done` / `failed` from status `3` / `4` / `7` are untouched.

### `delete_account` cleanup

In the existing `DELETE /api/accounts/{id}` handler, after the DB row is deleted, glob `data/avatars/{id}.*` and `os.remove` each match (best-effort, swallow `OSError`). Mirrors how `delete_video` cleans `stored_path`.

### `avatar_src` computed field in `/api/accounts` response

The repository layer (`app/repositories/accounts.py`) gains a small helper `with_avatar_src(row)` that the existing `list_all` and `get` mappers call before returning each row:

```python
def with_avatar_src(row: dict) -> dict:
    if row.get("profile_pic_path"):
        path = config.AVATAR_DIR / row["profile_pic_path"]
        try:
            mtime = int(path.stat().st_mtime)
        except OSError:
            mtime = 0
        row["avatar_src"] = f"/api/accounts/{row['id']}/avatar?v={mtime}"
    elif row.get("profile_pic_url"):
        row["avatar_src"] = row["profile_pic_url"]
    else:
        row["avatar_src"] = None
    return row
```

`avatar_src` is recomputed on each request; it is **not** stored.

## Frontend

### `Account` type — add one field

```ts
type Account = {
  // ...existing fields stay...
  profile_pic_path: string | null
  avatar_src: string | null
}
```

The existing `profile_pic_url` field stays in the type (legacy data still returned).

### Drawer rewrites

The **Avatar section** in the drawer is replaced. Before:

```tsx
<TextField label="Picture URL" value={form.profile_pic_url} ... />
<Avatar url={previewUrl || null} ... size={80} />
<span>Paste a URL to preview the image.</span>
```

After:

```tsx
<SectionTitle>{t('accounts.drawer.avatar')}</SectionTitle>
<div className="flex items-center gap-4">
  <Avatar url={editing?.avatar_src} name={form.profile_name || '?'} size={80} />
  <div className="flex flex-col gap-2">
    <div className="flex items-center gap-3">
      <Button variant="secondary" disabled={uploading} onClick={pickFile}>
        {uploading ? t('accounts.drawer.uploading') : t('accounts.drawer.uploadImage')}
      </Button>
      {editing?.avatar_src ? (
        <button onClick={removeAvatar} className="text-[12.5px] text-[var(--color-ink-4)] hover:text-[var(--color-red)]">
          {t('accounts.drawer.removeImage')}
        </button>
      ) : null}
    </div>
    <span className="text-[12px] text-[var(--color-ink-4)]">
      {t('accounts.drawer.avatarHint')}
    </span>
  </div>
</div>
<input ref={fileInputRef} type="file" accept="image/jpeg,image/png,image/webp" className="hidden" onChange={onFileChosen} />
```

**Upload flow** (immediate, not transactional with drawer Save):

1. User clicks "Upload image" → `fileInputRef.current.click()`
2. Browser file picker → user picks a file → `onFileChosen` fires
3. Client-side guards: reject if `file.size > 5*1024*1024` → toast `avatarTooLarge`; reject if `file.type` not in {jpeg, png, webp} → toast `avatarBadFormat`
4. POST FormData to `/api/accounts/{editing.id}/avatar`; set `uploading=true`
5. On success → toast `avatarUpdated`, `load()` (refreshes `rows`); the `editing` snapshot is updated by re-finding the row by id (see next paragraph)
6. On failure → toast `avatarFailed`; `uploading=false`

The drawer's `editing` snapshot needs `avatar_src` to refresh too. After a successful upload/remove call, find the new row in `rows` post-load and `setEditing(newRow)` (only when the user hasn't closed the drawer in the meantime — guard by checking the current `editing?.id`).

### Main table avatar

The existing `<Avatar url={r.profile_pic_url} ...>` call becomes `<Avatar url={r.avatar_src} ...>` (same change in the drawer preview). No other table changes.

### Removed / changed i18n keys

Delete from both en.json and zh.json:
- `accounts.drawer.avatarUrl`
- `accounts.drawer.avatarPreviewEmpty`

Add to both:

```
accounts.drawer.uploadImage          "Upload image"               / "上传图片"
accounts.drawer.removeImage          "Remove"                     / "移除"
accounts.drawer.avatarHint           "Up to 5 MB · JPG, PNG, WebP" / "≤ 5 MB · JPG / PNG / WebP"
accounts.drawer.uploading            "Uploading…"                 / "上传中…"
accounts.toast.avatarUpdated         "Avatar updated"             / "头像已更新"
accounts.toast.avatarFailed          "Avatar upload failed"       / "头像上传失败"
accounts.toast.avatarTooLarge        "Image too large (max 5 MB)" / "图片过大（最多 5 MB）"
accounts.toast.avatarBadFormat       "Unsupported image format"   / "图片格式不支持"
```

### `api.ts` additions

```ts
uploadAvatar: (id: number, file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return fetch('/api/accounts/' + id + '/avatar',
    { method: 'POST', body: fd, credentials: 'include' })
    .then(async r => { if (!r.ok) throw new Error(String(r.status)); return r.json() })
},
deleteAvatar: (id: number) =>
  req(`/accounts/${id}/avatar`, { method: 'DELETE' }),
```

(`uploadAvatar` bypasses the JSON `req()` helper because the body is multipart.)

## File map

```
app/
  config.py                          # + AVATAR_DIR
  main.py                            # mkdir AVATAR_DIR on startup
  db.py                              # +ALTER TABLE accounts ADD COLUMN profile_pic_path (idempotent)
  api/accounts.py                    # +3 endpoints, +cleanup in delete
  repositories/accounts.py           # +with_avatar_src helper; list_all and get apply it
  services/geelark_service.py        # set_pics dispatch branch on profile_pic_path

frontend/src/
  api.ts                             # +uploadAvatar, +deleteAvatar
  pages/Accounts.tsx                 # drawer Avatar section rewrite, use avatar_src
  i18n/locales/en.json               # remove avatarUrl/avatarPreviewEmpty; add new keys
  i18n/locales/zh.json               # ditto

tests/
  api/test_accounts_avatar.py        # NEW — upload happy path, too-large, bad-mime, GET, DELETE, avatar_src field
  services/test_set_pics_with_path.py # NEW — mock client.upload_file is called when path set
```

## Testing strategy

Backend gets the bulk of new automated tests (pytest, fitting the existing `tests/` structure):

1. `POST /api/accounts/{id}/avatar` with a 100-byte JPEG → row's `profile_pic_path` is `{id}.jpg`, file lives under `data/avatars/`, `pic_status` is cleared.
2. Upload then re-upload with a different format → previous file is removed, new file written, `profile_pic_path` reflects new ext.
3. Upload with `Content-Type: text/plain` → 400 with `detail` indicating format.
4. Upload with `>5 MB` body → 400 with `detail` indicating size.
5. `GET /api/accounts/{id}/avatar` for an account with path set → returns the bytes with appropriate Content-Type.
6. `GET /api/accounts/{id}/avatar` for an account with no path → 404.
7. `DELETE /api/accounts/{id}/avatar` → file removed, `profile_pic_path` set to NULL.
8. `GET /api/accounts` response → each row has `avatar_src`. For rows with path: matches `/api/accounts/{id}/avatar?v={mtime}`. For rows with only legacy URL: matches URL. For rows with neither: `null`.
9. `DELETE /api/accounts/{id}` → removes the file too.
10. Service: when `profile_pic_path` set and file exists, `set_pics` calls `client.upload_file(path, ext)` and passes the returned URL to `client.edit_tiktok_profile`. When path set but file missing and legacy URL set, falls back to URL.

Target: existing 100 passed stays green, +6-10 new cases for ~108 total. Frontend changes are verified by the manual QA pass described below.

Manual QA checklist (run after backend + frontend land):

1. Open an account in the drawer. Click "Upload image", pick a JPG ≤ 5 MB → preview replaces within ~1 s; "Avatar updated" toast; main-table row's avatar updates after the table reloads.
2. Click "Upload image", pick a 10 MB file → red toast "Image too large (max 5 MB)"; no network call (or rejected before it lands).
3. Click "Upload image", pick a `.gif` or `.txt` → red toast "Unsupported image format".
4. Upload, then click "Remove" → avatar reverts to monogram fallback in both drawer and main table.
5. Upload, then close drawer without "Save changes" → avatar persists (upload is immediate, not transactional).
6. Delete an account that has an uploaded avatar → file under `data/avatars/` is gone.
7. Language toggle to 中文 → all new strings render in Chinese.

## Edge cases & operational notes

- **Concurrent uploads to the same account**: file write is overwrite-by-name. Last writer wins. Acceptable.
- **Disk full / write fails**: endpoint returns 500. UI toast is generic `avatarFailed`. No DB row mutation.
- **Path traversal**: filename is always derived from `{id}.{ext}` where `id` is the URL int and `ext` is from a fixed allow-list. User-supplied filename is never used.
- **GeeLark file-type API**: `upload_file(path, "jpg")` — for PNG/WebP files we pass `"png"`/`"webp"` per the existing signature. The GeeLark `/open/v1/upload/getUrl` `fileType` param has accepted these in practice; no documented constraint says it won't.
- **Cache busting**: the `?v={mtime}` query on `avatar_src` is enough — the actual `GET /api/accounts/{id}/avatar` endpoint sets `Cache-Control: private, max-age=60` so browsers can briefly cache but pick up `mtime` changes promptly.

## Rollout

Single PR on top of the just-merged Accounts redesign. No feature flag. New column is additive (existing rows get NULL). Old `profile_pic_url`-only accounts continue to work because `set_pics` falls back to the URL when `profile_pic_path` is unset.
