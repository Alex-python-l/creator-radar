# Video Selection Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators pick which Drive videos to import, and assign each pending video to a chosen TikTok account before publishing.

**Architecture:** Part A splits Drive import into a synchronous "list folder" step and a "download the selected files" step. Part B lets a pending video carry a manually-set `account_id`; `posting_service.publish` posts manually-assigned videos to their accounts (warmup + cooldown guarded unless `force`) and auto-assigns the rest as before.

**Tech Stack:** FastAPI, SQLite, gdown, React 19 + Vite.

Design spec: `docs/superpowers/specs/2026-05-22-video-selection-design.md`.

Run Python tests with `./.venv/bin/pytest`. After each backend task run the full suite (`./.venv/bin/pytest -q`) and confirm zero failures.

---

## File Structure

**Modified — Part A (import selection):**
- `app/services/drive_import.py` — add `list_folder()`; `run_import()` and `start_import()` take an explicit selected-files list instead of a folder URL.
- `app/api/videos.py` — add `POST /import/list`; change `POST /import` body to `{files: [...]}`.
- `frontend/src/api.ts` — add `importList()`; `importDrive()` takes a files list.
- `frontend/src/pages/Videos.tsx` — two-step import modal (list → pick → import).

**Modified — Part B (account assignment):**
- `app/repositories/videos.py` — add `set_account()`.
- `app/api/videos.py` — add `PATCH /{vid}`; `force` param on the publish endpoints.
- `app/services/posting_service.py` — `publish()` restructured into manual + auto paths with a `force` flag; extract `_post_one()`.
- `frontend/src/api.ts` — add `setVideoAccount()`.
- `frontend/src/pages/Videos.tsx` — per-row account dropdown; force-publish toggle.

**Tests:** `tests/test_drive_import.py`, `tests/test_api.py`, `tests/test_repositories.py`, `tests/test_services.py`.

**Ops:** Task 8 deploys to Server A (`1.15.155.204`, systemd `mkt`).

---

## Task 1: `drive_import.list_folder()`

**Files:**
- Modify: `app/services/drive_import.py`
- Test: `tests/test_drive_import.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_drive_import.py` (the `FakeFile` namedtuple is already defined in this file):

```python
def test_list_folder_returns_videos_with_imported_flag(db, monkeypatch):
    import app.config as cfg
    from app.repositories import videos as vid_repo
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    vid_repo.create(db, "old.mp4", "/x/old.mp4", drive_file_id="ID1")
    listing = [FakeFile("ID1", "f/old.mp4", "x"),
               FakeFile("ID2", "f/new.mp4", "x"),
               FakeFile("ID3", "f/notes.txt", "x")]
    monkeypatch.setattr(drive_import.gdown, "download_folder", lambda **kw: listing)
    files = drive_import.list_folder("https://drive.google.com/drive/folders/ID")
    assert files == [
        {"id": "ID1", "name": "old.mp4", "already_imported": True},
        {"id": "ID2", "name": "new.mp4", "already_imported": False},
    ]


def test_list_folder_without_proxy_raises(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "")
    with pytest.raises(drive_import.ProxyNotConfigured):
        drive_import.list_folder("https://drive.google.com/drive/folders/ABCDEFGHIJ")


def test_list_folder_bad_url_raises(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    with pytest.raises(ValueError):
        drive_import.list_folder("nonsense")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_drive_import.py -k list_folder -q`
Expected: FAIL — `drive_import` has no attribute `list_folder`.

- [ ] **Step 3: Implement `list_folder`**

In `app/services/drive_import.py`, add this function immediately after `_now()` (before `run_import`):

```python
def list_folder(folder_url: str) -> list[dict]:
    """List the video files in a public Drive folder, without downloading.

    Returns [{"id", "name", "already_imported"}] for video files only.
    Raises ProxyNotConfigured, ValueError (bad URL), or RuntimeError (list failed).
    """
    if not config.DRIVE_IMPORT_PROXY:
        raise ProxyNotConfigured()
    parse_folder_id(folder_url)  # raises ValueError on a bad URL
    listing = gdown.download_folder(
        url=folder_url, skip_download=True, quiet=True,
        proxy=config.DRIVE_IMPORT_PROXY or None)
    if not listing:
        raise RuntimeError("folder is empty or could not be listed")
    conn = get_connection()
    try:
        existing = vid_repo.existing_drive_file_ids(conn)
    finally:
        conn.close()
    files = []
    for f in listing:
        name = Path(f.path).name
        if is_video(name):
            files.append({"id": f.id, "name": name,
                          "already_imported": f.id in existing})
    return files
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_drive_import.py -k list_folder -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: zero failures.

- [ ] **Step 6: Commit**

```bash
git add app/services/drive_import.py tests/test_drive_import.py
git commit -m "feat: drive_import.list_folder — list a folder without downloading"
```

---

## Task 2: Selected-files import — service + API (atomic)

`run_import` / `start_import` change signature AND the `/import` endpoint changes
together — they are coupled, so this task changes both plus all affected tests
in one commit.

**Files:**
- Modify: `app/services/drive_import.py`
- Modify: `app/api/videos.py`
- Test: `tests/test_drive_import.py`, `tests/test_api.py`

- [ ] **Step 1: Update the drive_import tests**

In `tests/test_drive_import.py`, DELETE these test functions in full:
`test_run_import_downloads_new_videos`, `test_run_import_skips_already_imported`,
`test_run_import_isolates_per_file_failure`, `test_run_import_marks_error_when_listing_fails`,
`test_run_import_writes_job_run`, `test_start_import_bad_url_raises`,
`test_start_import_without_proxy_raises`, `test_start_import_rejects_concurrent`,
`test_start_import_launches_background_thread`.

Append these replacements:

```python
def test_run_import_downloads_selected_files(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import videos as vid_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")

    def fake_download(id=None, output=None, **kw):
        Path(output).write_bytes(b"v")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)
    drive_import.run_import([{"id": "ID1", "name": "a.mp4"},
                             {"id": "ID2", "name": "b.mov"}])
    vids = vid_repo.list_all(db)
    assert {v["drive_file_id"] for v in vids} == {"ID1", "ID2"}
    assert all(v["result"] == "PENDING" for v in vids)
    st = drive_import.get_state()
    assert st["state"] == "done" and st["done"] == 2 and st["failed"] == 0


def test_run_import_skips_already_imported(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import videos as vid_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    vid_repo.create(db, "old.mp4", str(tmp_path / "old.mp4"), drive_file_id="ID1")

    def fake_download(id=None, output=None, **kw):
        Path(output).write_bytes(b"v")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)
    drive_import.run_import([{"id": "ID1", "name": "old.mp4"},
                             {"id": "ID2", "name": "new.mp4"}])
    st = drive_import.get_state()
    assert st["done"] == 1 and st["skipped"] == 1


def test_run_import_isolates_per_file_failure(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import videos as vid_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")

    def fake_download(id=None, output=None, **kw):
        if id == "BAD":
            raise RuntimeError("network error")
        Path(output).write_bytes(b"v")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)
    drive_import.run_import([{"id": "OK", "name": "good.mp4"},
                             {"id": "BAD", "name": "bad.mp4"}])
    vids = vid_repo.list_all(db)
    assert len(vids) == 1 and vids[0]["drive_file_id"] == "OK"
    st = drive_import.get_state()
    assert st["done"] == 1 and st["failed"] == 1 and st["state"] == "done"
    assert any("bad.mp4" in e for e in st["errors"])


def test_run_import_writes_job_run(db, tmp_path, monkeypatch):
    import app.config as cfg
    from app.repositories import job_runs as jobs_repo
    monkeypatch.setattr(cfg, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")

    def fake_download(id=None, output=None, **kw):
        Path(output).write_bytes(b"v")
        return output

    monkeypatch.setattr(drive_import.gdown, "download", fake_download)
    drive_import.run_import([{"id": "Z", "name": "z.mp4"}])
    jr = jobs_repo.recent(db, 1)[0]
    assert jr["job_type"] == "drive_import" and jr["status"] == "ok"
    assert "1 imported" in jr["summary"]


def test_start_import_without_proxy_raises(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "")
    with pytest.raises(drive_import.ProxyNotConfigured):
        drive_import.start_import([{"id": "X", "name": "a.mp4"}])


def test_start_import_empty_selection_raises(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    with pytest.raises(ValueError):
        drive_import.start_import([])


def test_start_import_rejects_concurrent(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    monkeypatch.setitem(drive_import._import_state, "state", "running")
    with pytest.raises(drive_import.ImportAlreadyRunning):
        drive_import.start_import([{"id": "X", "name": "a.mp4"}])


def test_start_import_launches_background_thread(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    monkeypatch.setitem(drive_import._import_state, "state", "idle")
    called = {}
    monkeypatch.setattr(drive_import, "run_import",
                        lambda files: called.update(files=files))
    t = drive_import.start_import([{"id": "X", "name": "a.mp4"}])
    t.join(timeout=2)
    assert called.get("files") == [{"id": "X", "name": "a.mp4"}]
```

- [ ] **Step 2: Update the import API tests**

In `tests/test_api.py`, DELETE `test_import_without_proxy_returns_503`,
`test_import_bad_url_returns_400`, `test_import_already_running_returns_409`,
`test_import_valid_request_starts` (keep `test_import_status_returns_state`).

Append these:

```python
def test_import_without_proxy_returns_503(client, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "")
    client.post("/api/login", json={"password": "pw"})
    r = client.post("/api/videos/import",
                    json={"files": [{"id": "X", "name": "a.mp4"}]})
    assert r.status_code == 503


def test_import_empty_selection_returns_400(client, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    client.post("/api/login", json={"password": "pw"})
    r = client.post("/api/videos/import", json={"files": []})
    assert r.status_code == 400


def test_import_already_running_returns_409(client, monkeypatch):
    import app.config as cfg
    from app.services import drive_import
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    client.post("/api/login", json={"password": "pw"})
    monkeypatch.setitem(drive_import._import_state, "state", "running")
    r = client.post("/api/videos/import",
                    json={"files": [{"id": "X", "name": "a.mp4"}]})
    assert r.status_code == 409


def test_import_valid_request_starts(client, monkeypatch):
    import app.config as cfg
    from app.services import drive_import
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    got = {}
    monkeypatch.setattr(drive_import, "start_import",
                        lambda files: got.update(files=files))
    client.post("/api/login", json={"password": "pw"})
    r = client.post("/api/videos/import",
                    json={"files": [{"id": "X", "name": "a.mp4"}]})
    assert r.status_code == 200 and r.json()["started"] is True
    assert got["files"] == [{"id": "X", "name": "a.mp4"}]


def test_import_list_returns_files(client, monkeypatch):
    from app.services import drive_import
    monkeypatch.setattr(drive_import, "list_folder",
                        lambda url: [{"id": "X", "name": "a.mp4",
                                      "already_imported": False}])
    client.post("/api/login", json={"password": "pw"})
    r = client.post("/api/videos/import/list",
                    json={"folder_url": "https://drive.google.com/drive/folders/ID"})
    assert r.status_code == 200
    assert r.json()["files"][0]["name"] == "a.mp4"


def test_import_list_without_proxy_returns_503(client, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "")
    client.post("/api/login", json={"password": "pw"})
    r = client.post("/api/videos/import/list",
                    json={"folder_url": "https://drive.google.com/drive/folders/X1234567890"})
    assert r.status_code == 503
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_drive_import.py tests/test_api.py -k "import or run_import or start_import" -q`
Expected: FAIL — `run_import`/`start_import` still take a folder URL; `/import/list` route missing.

- [ ] **Step 4: Replace `run_import` in `app/services/drive_import.py`**

Replace the entire `run_import` function with:

```python
def run_import(files: list[dict]) -> None:
    """Background worker: download the selected Drive files, record them.

    `files` is a list of {"id", "name"} dicts. Opens its own DB connection
    (runs in a background thread). Updates progress state, writes a job_runs row.
    """
    _update(state="running", total=0, done=0, skipped=0, failed=0,
            current=None, errors=[], started_at=_now(), finished_at=None)
    conn = get_connection()
    jid = jobs_repo.start(conn, "drive_import")
    errors: list[str] = []
    done = skipped = failed = 0
    try:
        proxy = config.DRIVE_IMPORT_PROXY or None
        existing = vid_repo.existing_drive_file_ids(conn)
        wanted = [(f["id"], f["name"]) for f in files
                  if f["id"] not in existing]
        skipped = len(files) - len(wanted)
        _update(total=len(wanted), skipped=skipped)
        for file_id, name in wanted:
            _update(current=name)
            dest = unique_path(config.UPLOAD_DIR, name, file_id)
            try:
                gdown.download(id=file_id, output=str(dest),
                               quiet=True, proxy=proxy)
                vid_repo.create(conn, name, str(dest), drive_file_id=file_id)
                done += 1
            except Exception as e:  # per-file isolation: skip and continue
                if dest.exists():
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                failed += 1
                errors.append(f"{name}: {e}")
            _update(done=done, failed=failed, errors=list(errors))
        summary = f"{done} imported, {skipped} skipped, {failed} failed"
        jobs_repo.finish(conn, jid, "ok" if failed == 0 else "error", summary)
        _update(state="done", current=None, finished_at=_now())
    except Exception as e:  # whole-import failure
        jobs_repo.finish(conn, jid, "error", str(e))
        _update(state="error", current=None,
                errors=list(errors) + [str(e)], finished_at=_now())
    finally:
        conn.close()
```

- [ ] **Step 5: Replace `start_import` in `app/services/drive_import.py`**

Replace the entire `start_import` function with:

```python
def start_import(files: list[dict]) -> threading.Thread:
    """Validate input and launch a background import of the selected files.

    Raises ProxyNotConfigured, ValueError (empty selection), or
    ImportAlreadyRunning. Returns the started daemon Thread.
    """
    if not config.DRIVE_IMPORT_PROXY:
        raise ProxyNotConfigured()
    if not files:
        raise ValueError("no files selected")
    with _state_lock:
        if _import_state["state"] == "running":
            raise ImportAlreadyRunning()
        _import_state["state"] = "running"
    t = threading.Thread(target=run_import, args=(files,), daemon=True)
    t.start()
    return t
```

- [ ] **Step 6: Replace `import_drive` and add `import_list` in `app/api/videos.py`**

Replace the entire `import_drive` function with:

```python
@router.post("/import")
def import_drive(body: dict):
    from app.services import drive_import
    files = (body or {}).get("files") or []
    try:
        drive_import.start_import(files)
    except drive_import.ProxyNotConfigured:
        raise HTTPException(status_code=503,
                            detail="DRIVE_IMPORT_PROXY is not configured")
    except drive_import.ImportAlreadyRunning:
        raise HTTPException(status_code=409,
                            detail="an import is already running")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"started": True}


@router.post("/import/list")
def import_list(body: dict):
    from app.services import drive_import
    url = (body or {}).get("folder_url", "")
    try:
        files = drive_import.list_folder(url)
    except drive_import.ProxyNotConfigured:
        raise HTTPException(status_code=503,
                            detail="DRIVE_IMPORT_PROXY is not configured")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not list folder: {e}")
    return {"files": files}
```

- [ ] **Step 7: Run tests to verify they pass, then the full suite**

Run: `./.venv/bin/pytest tests/test_drive_import.py tests/test_api.py -q`
Expected: PASS.
Run: `./.venv/bin/pytest -q`
Expected: zero failures.

- [ ] **Step 8: Commit**

```bash
git add app/services/drive_import.py app/api/videos.py tests/test_drive_import.py tests/test_api.py
git commit -m "feat: import the selected Drive files; add import/list endpoint"
```

---

## Task 3: Frontend — two-step import modal

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/pages/Videos.tsx`

No frontend test framework; verification is a clean TypeScript+Vite build. Read both files first; the anchors below identify what to replace.

- [ ] **Step 1: Update `frontend/src/api.ts`**

Find:

```typescript
  importDrive: (folderUrl: string) =>
    req('/videos/import', { method: 'POST', body: JSON.stringify({ folder_url: folderUrl }) }),
  importStatus: () => req('/videos/import/status'),
```

Replace with:

```typescript
  importList: (folderUrl: string) =>
    req('/videos/import/list', { method: 'POST', body: JSON.stringify({ folder_url: folderUrl }) }),
  importDrive: (files: { id: string; name: string }[]) =>
    req('/videos/import', { method: 'POST', body: JSON.stringify({ files }) }),
  importStatus: () => req('/videos/import/status'),
```

- [ ] **Step 2: Replace the import-modal state hooks in `Videos.tsx`**

Find:

```typescript
  const [importOpen, setImportOpen] = useState(false)
  const [importUrl, setImportUrl] = useState('')
  const [importing, setImporting] = useState(false)
  const [importProg, setImportProg] = useState<ImportState | null>(null)
```

Replace with:

```typescript
  const [importOpen, setImportOpen] = useState(false)
  const [importUrl, setImportUrl] = useState('')
  const [importStep, setImportStep] = useState<'url' | 'pick'>('url')
  const [importFiles, setImportFiles] = useState<DriveFile[]>([])
  const [importSel, setImportSel] = useState<Set<string>>(new Set())
  const [importBusy, setImportBusy] = useState(false)
  const [importing, setImporting] = useState(false)
  const [importProg, setImportProg] = useState<ImportState | null>(null)
```

- [ ] **Step 3: Add the `DriveFile` type in `Videos.tsx`**

Immediately after the `type ImportState = { ... }` block, add:

```typescript
type DriveFile = { id: string; name: string; already_imported: boolean }
```

- [ ] **Step 4: Replace the `startImport` handler in `Videos.tsx`**

Replace the entire `startImport` function with:

```typescript
  const openImport = () => {
    setImportStep('url')
    setImportUrl('')
    setImportFiles([])
    setImportSel(new Set())
    setImportOpen(true)
  }

  const importSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (importBusy) return
    if (importStep === 'url') {
      const url = importUrl.trim()
      if (!url) return
      setImportBusy(true)
      try {
        const r = await api.importList(url)
        if (Array.isArray(r?.files)) {
          setImportFiles(r.files)
          setImportSel(
            new Set(
              r.files
                .filter((f: DriveFile) => !f.already_imported)
                .map((f: DriveFile) => f.id),
            ),
          )
          setImportStep('pick')
        } else {
          onToast({ msg: r?.detail || 'Could not list folder', tone: 'red' })
        }
      } catch {
        onToast({ msg: 'Could not list folder', tone: 'red' })
      } finally {
        setImportBusy(false)
      }
      return
    }
    const chosen = importFiles.filter((f) => importSel.has(f.id))
    if (chosen.length === 0) return
    setImportBusy(true)
    try {
      const r = await api.importDrive(chosen.map((f) => ({ id: f.id, name: f.name })))
      if (r?.started) {
        setImportOpen(false)
        setImportProg(null)
        setImporting(true)
      } else {
        onToast({ msg: r?.detail || 'Could not start import', tone: 'red' })
      }
    } catch {
      onToast({ msg: 'Could not start import', tone: 'red' })
    } finally {
      setImportBusy(false)
    }
  }

  const toggleImportFile = (id: string) =>
    setImportSel((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
```

- [ ] **Step 5: Point the "Import from Drive" button at `openImport`**

Find:

```tsx
            <Button variant="secondary" onClick={() => setImportOpen(true)}>
              Import from Drive
            </Button>
```

Replace with:

```tsx
            <Button variant="secondary" onClick={openImport}>
              Import from Drive
            </Button>
```

- [ ] **Step 6: Replace the import `<Modal>` block in `Videos.tsx`**

Replace the entire `<Modal open={importOpen} ...> ... </Modal>` block with:

```tsx
      <Modal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        title="Import from Google Drive"
        onSubmit={importSubmit}
        submitLabel={
          importBusy
            ? 'Working…'
            : importStep === 'url'
              ? 'List videos'
              : `Import ${importFiles.filter((f) => importSel.has(f.id)).length}`
        }
      >
        {importStep === 'url' ? (
          <TextField
            label="Public Drive folder link"
            placeholder="https://drive.google.com/drive/folders/…"
            hint="The folder must be shared as 'anyone with the link'."
            value={importUrl}
            onChange={(e) => setImportUrl(e.target.value)}
            autoFocus
          />
        ) : (
          <div className="flex flex-col gap-1.5 max-h-[320px] overflow-auto">
            {importFiles.length === 0 ? (
              <div className="text-[13px] text-[var(--color-ink-3)]">
                No videos found in this folder.
              </div>
            ) : (
              importFiles.map((f) => (
                <label
                  key={f.id}
                  className={cn(
                    'flex items-center gap-2.5 px-2 py-1.5 rounded-[6px] text-[13.5px]',
                    f.already_imported
                      ? 'text-[var(--color-ink-4)]'
                      : 'text-[var(--color-ink)] hover:bg-[var(--color-gray-bg)] cursor-pointer',
                  )}
                >
                  <input
                    type="checkbox"
                    disabled={f.already_imported}
                    checked={importSel.has(f.id)}
                    onChange={() => toggleImportFile(f.id)}
                  />
                  <span className="truncate">{f.name}</span>
                  {f.already_imported ? (
                    <span className="text-[11.5px] text-[var(--color-ink-4)] ml-auto">
                      already imported
                    </span>
                  ) : null}
                </label>
              ))
            )}
          </div>
        )}
      </Modal>
```

- [ ] **Step 7: Build to verify it compiles**

Run: `export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" && npm --prefix frontend run build`
Expected: `tsc -b` reports no type errors; `vite build` completes.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api.ts frontend/src/pages/Videos.tsx
git commit -m "feat: two-step Drive import — list folder, pick videos"
```

---

## Task 4: `vid_repo.set_account()` and `PATCH /api/videos/{vid}`

**Files:**
- Modify: `app/repositories/videos.py`
- Modify: `app/api/videos.py`
- Test: `tests/test_repositories.py`, `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_repositories.py`:

```python
def test_set_account_assigns_and_clears(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name": "A1"})
    vid = vid_repo.create(db, "v.mp4", "/x/v.mp4")
    vid_repo.set_account(db, vid, aid)
    assert vid_repo.get(db, vid)["account_id"] == aid
    vid_repo.set_account(db, vid, None)
    assert vid_repo.get(db, vid)["account_id"] is None
```

Append to `tests/test_api.py`:

```python
def test_patch_video_sets_account(client):
    client.post("/api/login", json={"password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "PV1"}).json()["id"]
    client.post("/api/videos/upload", files={"files": ("p.mp4", b"x", "video/mp4")})
    vid = client.get("/api/videos").json()[0]["id"]
    r = client.patch(f"/api/videos/{vid}", json={"account_id": aid})
    assert r.status_code == 200
    row = next(v for v in client.get("/api/videos").json() if v["id"] == vid)
    assert row["account_id"] == aid


def test_patch_video_unknown_video_returns_404(client):
    client.post("/api/login", json={"password": "pw"})
    r = client.patch("/api/videos/99999", json={"account_id": None})
    assert r.status_code == 404


def test_patch_video_unknown_account_returns_400(client):
    client.post("/api/login", json={"password": "pw"})
    client.post("/api/videos/upload", files={"files": ("q.mp4", b"x", "video/mp4")})
    vid = client.get("/api/videos").json()[0]["id"]
    r = client.patch(f"/api/videos/{vid}", json={"account_id": 99999})
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_repositories.py tests/test_api.py -k "set_account or patch_video" -q`
Expected: FAIL — `set_account` missing; `PATCH /api/videos/{vid}` route missing.

- [ ] **Step 3: Add `set_account` to `app/repositories/videos.py`**

Add this function immediately after `create`:

```python
def set_account(conn: sqlite3.Connection, vid: int, account_id: int | None) -> None:
    conn.execute("UPDATE videos SET account_id=? WHERE id=?", (account_id, vid))
    conn.commit()
```

- [ ] **Step 4: Add `PATCH /{vid}` to `app/api/videos.py`**

Add this function immediately after `delete_video`:

```python
@router.patch("/{vid}")
def update_video(vid: int, body: dict):
    from app.repositories import accounts as acc_repo
    conn = _db()
    if vid_repo.get(conn, vid) is None:
        raise HTTPException(status_code=404, detail="video not found")
    account_id = (body or {}).get("account_id")
    if account_id is not None and acc_repo.get(conn, account_id) is None:
        raise HTTPException(status_code=400, detail="account not found")
    vid_repo.set_account(conn, vid, account_id)
    return {"ok": True}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_repositories.py tests/test_api.py -k "set_account or patch_video" -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: zero failures.

- [ ] **Step 7: Commit**

```bash
git add app/repositories/videos.py app/api/videos.py tests/test_repositories.py tests/test_api.py
git commit -m "feat: assign a pending video to an account (PATCH /api/videos/{id})"
```

---

## Task 5: `posting_service.publish` — manual + auto + force

**Files:**
- Modify: `app/services/posting_service.py`
- Test: `tests/test_services.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_services.py`:

```python
def test_publish_posts_manual_video_to_its_account(db, monkeypatch, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    aid = acc_repo.create(db, {"profile_name": "M1", "geelark_phone_id": "phM1",
                               "warmed_up": 1})
    f = tmp_path / "m.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "m.mp4", str(f))
    vid_repo.set_account(db, vid, aid)

    class FakePost:
        def upload_file(self, p, t): return "https://res/m.mp4"
        def create_video_post_task(self, env_id, video_url,
                                   video_desc=None, cover_url=None): return "pt-m"

    monkeypatch.setattr(posting_service, "_caption", lambda path: "cap #IELTS")
    list(posting_service.publish(db, FakePost()))
    row = vid_repo.get(db, vid)
    assert row["result"] == "INITIATED" and row["account_id"] == aid
    assert row["geelark_task_id"] == "pt-m"


def test_publish_skips_manual_video_on_unwarmed_account(db, monkeypatch, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    aid = acc_repo.create(db, {"profile_name": "M2", "geelark_phone_id": "phM2",
                               "warmed_up": 0})
    f = tmp_path / "m2.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "m2.mp4", str(f))
    vid_repo.set_account(db, vid, aid)

    class FakePost:
        def upload_file(self, p, t): raise AssertionError("must not post")
        def create_video_post_task(self, *a, **k): raise AssertionError("must not post")

    monkeypatch.setattr(posting_service, "_caption", lambda path: "c")
    out = list(posting_service.publish(db, FakePost()))
    assert vid_repo.get(db, vid)["result"] == "PENDING"
    assert any("SKIPPED" in line for line in out)


def test_publish_force_posts_manual_video_on_unwarmed_account(db, monkeypatch, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    aid = acc_repo.create(db, {"profile_name": "M3", "geelark_phone_id": "phM3",
                               "warmed_up": 0})
    f = tmp_path / "m3.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "m3.mp4", str(f))
    vid_repo.set_account(db, vid, aid)

    class FakePost:
        def upload_file(self, p, t): return "https://res/m3.mp4"
        def create_video_post_task(self, env_id, video_url,
                                   video_desc=None, cover_url=None): return "pt-m3"

    monkeypatch.setattr(posting_service, "_caption", lambda path: "c")
    list(posting_service.publish(db, FakePost(), force=True))
    assert vid_repo.get(db, vid)["result"] == "INITIATED"


def test_publish_auto_still_assigns_unassigned_videos(db, monkeypatch, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    acc_repo.create(db, {"profile_name": "AU1", "geelark_phone_id": "phAU1",
                         "warmed_up": 1})
    f = tmp_path / "au.mp4"; f.write_bytes(b"x")
    vid_repo.create(db, "au.mp4", str(f))  # no account_id -> auto

    class FakePost:
        def upload_file(self, p, t): return "https://res/au.mp4"
        def create_video_post_task(self, env_id, video_url,
                                   video_desc=None, cover_url=None): return "pt-au"

    monkeypatch.setattr(posting_service, "_caption", lambda path: "c")
    list(posting_service.publish(db, FakePost()))
    assert vid_repo.list_all(db)[0]["result"] == "INITIATED"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/pytest tests/test_services.py -k publish -q`
Expected: FAIL — `publish` does not yet honor `account_id` or `force`.

- [ ] **Step 3: Replace the `publish` function in `app/services/posting_service.py`**

Replace the entire `publish` function with this `_post_one` helper followed by the new `publish`:

```python
def _post_one(conn, client, v, acc):
    """Post one video to one account. Generator of log lines."""
    try:
        caption = _caption(v["stored_path"])
    except Exception as e:
        vid_repo.mark_failure(conn, v["id"], acc["id"], f"caption failed: {e}")
        yield f"  FAILURE caption: {e}"
        return
    try:
        ext = os.path.splitext(v["original_filename"])[1].lstrip(".") or "mp4"
        res_url = client.upload_file(v["stored_path"], ext)
        task_id = client.create_video_post_task(
            acc["geelark_phone_id"], res_url, video_desc=caption)
    except Exception as e:
        vid_repo.mark_failure(conn, v["id"], acc["id"], f"post failed: {e}")
        yield f"  FAILURE post: {e}"
        return
    vid_repo.mark_initiated(conn, v["id"], acc["id"], caption, task_id,
                            "submitted to GeeLark")
    acc_repo.update(conn, acc["id"],
                    {"last_posted_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    yield f"  INITIATED task {task_id}"


def publish(conn, client, cooldown_hours: float = 2, force: bool = False):
    """Generator of log lines. Videos with a manually-set account_id post to
    that account (warmup + cooldown guarded unless force=True); videos with no
    account_id auto-assign via build_pool/assign as before."""
    pend = vid_repo.list_pending(conn)
    if not pend:
        yield "No videos to post."
        return
    manual = [v for v in pend if v.get("account_id")]
    auto = [v for v in pend if not v.get("account_id")]
    accounts = acc_repo.list_all(conn)
    by_id = {a["id"]: a for a in accounts}
    cutoff = time.time() - cooldown_hours * 3600

    for v in manual:
        acc = by_id.get(v["account_id"])
        if acc is None:
            continue  # account deleted; FK already nulled — defensive
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
        fresh = acc_repo.list_all(conn)  # manual posts updated last_posted_at
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_services.py -k publish -q`
Expected: PASS (the new tests plus the existing `test_posting_assigns_and_marks_initiated` and `test_publish_handles_whitespace_profile_name`, which exercise the auto path).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: zero failures.

- [ ] **Step 6: Commit**

```bash
git add app/services/posting_service.py tests/test_services.py
git commit -m "feat: publish manually-assigned videos to their account; force flag"
```

---

## Task 6: API — `force` on the publish endpoints

**Files:**
- Modify: `app/api/videos.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write a failing test**

Append to `tests/test_api.py`:

```python
def test_publish_stream_accepts_force_param(client):
    client.post("/api/login", json={"password": "pw"})
    with client.stream("POST", "/api/videos/publish-stream?force=true") as r:
        assert r.status_code == 200
        body = "".join(chunk for chunk in r.iter_text())
    assert "No videos to post" in body
```

- [ ] **Step 2: Run test to verify it passes or fails**

Run: `./.venv/bin/pytest tests/test_api.py::test_publish_stream_accepts_force_param -q`
FastAPI ignores unknown query params, so this test may already PASS. Either way, apply Step 3 — it wires `force` through to `posting_service.publish`; the test then guards against regression.

- [ ] **Step 3: Add `force` to both publish endpoints in `app/api/videos.py`**

Replace the `publish` function with:

```python
@router.post("/publish")
def publish(force: bool = False):
    from app.services import posting_service
    from app.services.geelark_service import make_client
    from app.repositories import settings as srepo
    from app.locks import OPERATION_LOCK
    cd = float(srepo.get_all(_db()).get("post_cooldown_hours", 2))
    with OPERATION_LOCK:
        log = list(posting_service.publish(_db(), make_client(),
                                           cooldown_hours=cd, force=force))
    return {"log": log}
```

Replace the `publish_stream` function with:

```python
@router.post("/publish-stream")
def publish_stream(force: bool = False):
    from app.services import posting_service
    from app.services.geelark_service import make_client
    from app.repositories import settings as srepo
    cd = float(srepo.get_all(_db()).get("post_cooldown_hours", 2))
    return sse_from_generator(
        locked_generator(posting_service.publish(
            _db(), make_client(), cooldown_hours=cd, force=force)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/bin/pytest tests/test_api.py::test_publish_stream_accepts_force_param -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/pytest -q`
Expected: zero failures.

- [ ] **Step 6: Commit**

```bash
git add app/api/videos.py tests/test_api.py
git commit -m "feat: force param on the publish endpoints"
```

---

## Task 7: Frontend — per-row account dropdown and force toggle

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/pages/Videos.tsx`

Verification is a clean build.

- [ ] **Step 1: Add `setVideoAccount` to `frontend/src/api.ts`**

Immediately after the `importStatus` line in the `api` object, add:

```typescript
  setVideoAccount: (id: number, accountId: number | null) =>
    req(`/videos/${id}`, { method: 'PATCH', body: JSON.stringify({ account_id: accountId }) }),
```

- [ ] **Step 2: Add the `Account` type and state in `Videos.tsx`**

After the `type DriveFile = ...` line, add:

```typescript
type Account = { id: number; profile_name: string | null; warmed_up: number }
```

After the `const [importProg, ...]` hook, add:

```typescript
  const [accounts, setAccounts] = useState<Account[]>([])
  const [forcePublish, setForcePublish] = useState(false)
```

- [ ] **Step 3: Load accounts and add the `assignAccount` handler in `Videos.tsx`**

Find the first `useEffect`:

```typescript
  useEffect(() => {
    load()
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [])
```

Replace with:

```typescript
  useEffect(() => {
    load()
    api.accounts().then(setAccounts).catch(() => {})
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [])

  const assignAccount = (id: number, accountId: number | null) => {
    setVids((prev) => prev.map((v) => (v.id === id ? { ...v, account_id: accountId } : v)))
    api.setVideoAccount(id, accountId).catch(() => {
      onToast({ msg: 'Could not set account', tone: 'red' })
      load()
    })
  }
```

- [ ] **Step 4: Pass `forcePublish` through the publish call in `Videos.tsx`**

Find:

```typescript
    streamPost('/videos/publish-stream', (l) => setLog((p) => [...p, l]))
```

Replace with:

```typescript
    streamPost(
      `/videos/publish-stream${forcePublish ? '?force=true' : ''}`,
      (l) => setLog((p) => [...p, l]),
    )
```

- [ ] **Step 5: Add the force toggle to the page header in `Videos.tsx`**

Find (inside the `<PageTitle>` `right` prop):

```tsx
            <Button variant="secondary" onClick={openImport}>
              Import from Drive
            </Button>
            <Button
              variant="primary"
              onClick={publish}
              disabled={running || pending.length === 0}
            >
```

Replace with:

```tsx
            <Button variant="secondary" onClick={openImport}>
              Import from Drive
            </Button>
            <label className="flex items-center gap-1.5 text-[12.5px] text-[var(--color-ink-3)] select-none">
              <input
                type="checkbox"
                checked={forcePublish}
                onChange={(e) => setForcePublish(e.target.checked)}
              />
              Force
            </label>
            <Button
              variant="primary"
              onClick={publish}
              disabled={running || pending.length === 0}
            >
```

- [ ] **Step 6: Add the Account column to the pending-queue table in `Videos.tsx`**

Replace the pending-queue `<THead>` block:

```tsx
            <THead>
              <Tr className="hover:bg-transparent">
                <Th>File</Th>
                <Th align="right" className="w-[140px]">
                  Status
                </Th>
                <Th align="right" className="w-[80px]" />
              </Tr>
            </THead>
```

with:

```tsx
            <THead>
              <Tr className="hover:bg-transparent">
                <Th>File</Th>
                <Th className="w-[200px]">Account</Th>
                <Th align="right" className="w-[120px]">
                  Status
                </Th>
                <Th align="right" className="w-[80px]" />
              </Tr>
            </THead>
```

Replace the pending-row `{pending.map((v) => ( ... ))}` block:

```tsx
              {pending.map((v) => (
                <Tr key={v.id}>
                  <Td>
                    <span className="font-medium text-[var(--color-ink)]">
                      {v.original_filename}
                    </span>
                  </Td>
                  <Td align="right">
                    <Badge tone="gray">Pending</Badge>
                  </Td>
                  <Td align="right">
                    <button
                      onClick={() => removeVideo(v.id)}
                      className="text-[12.5px] text-[var(--color-ink-4)] hover:text-[var(--color-red)]"
                    >
                      Delete
                    </button>
                  </Td>
                </Tr>
              ))}
```

with:

```tsx
              {pending.map((v) => (
                <Tr key={v.id}>
                  <Td>
                    <span className="font-medium text-[var(--color-ink)]">
                      {v.original_filename}
                    </span>
                  </Td>
                  <Td>
                    <select
                      value={v.account_id ?? ''}
                      onChange={(e) =>
                        assignAccount(v.id, e.target.value ? Number(e.target.value) : null)
                      }
                      className="w-full bg-[var(--color-surface)] border border-[var(--color-line)] rounded-[6px] px-2 py-1 text-[12.5px]"
                    >
                      <option value="">(auto)</option>
                      {accounts.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.profile_name ?? `#${a.id}`}
                          {a.warmed_up ? '' : ' — not warmed'}
                        </option>
                      ))}
                    </select>
                  </Td>
                  <Td align="right">
                    <Badge tone="gray">Pending</Badge>
                  </Td>
                  <Td align="right">
                    <button
                      onClick={() => removeVideo(v.id)}
                      className="text-[12.5px] text-[var(--color-ink-4)] hover:text-[var(--color-red)]"
                    >
                      Delete
                    </button>
                  </Td>
                </Tr>
              ))}
```

- [ ] **Step 7: Build to verify it compiles**

Run: `export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" && npm --prefix frontend run build`
Expected: `tsc -b` reports no type errors; `vite build` completes.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api.ts frontend/src/pages/Videos.tsx
git commit -m "feat: per-video account dropdown and force-publish toggle"
```

---

## Task 8: Deploy to Server A + verify

Ops task. Server A: `1.15.155.204`, app at `/opt/mkt`, systemd service `mkt`.

- [ ] **Step 1: Confirm the full suite passes**

Run: `./.venv/bin/pytest -q`
Expected: zero failures.

- [ ] **Step 2: Build the frontend**

Run: `export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" && npm --prefix frontend run build`
Expected: `frontend/dist/` rebuilt.

- [ ] **Step 3: Package and upload**

```bash
cd /Users/Zhuanz/Documents/mkt
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-vsel.tar.gz --exclude='__pycache__' --exclude='*.pyc' \
  app geelark_client.py generate_caption.py requirements.txt frontend/dist
```

Copy `/tmp/mkt-vsel.tar.gz` to `/tmp/` on `1.15.155.204`.

- [ ] **Step 4: Unpack and restart**

On server A:

```bash
tar -xzf /tmp/mkt-vsel.tar.gz -C /opt/mkt
systemctl restart mkt
sleep 5
systemctl is-active mkt
rm -f /tmp/mkt-vsel.tar.gz
```

Expected: `active`.

- [ ] **Step 5: Verify the new routes exist**

On server A:

```bash
curl -s -o /dev/null -w "import/list -> %{http_code}\n" -X POST http://127.0.0.1:8080/api/videos/import/list
curl -s -o /dev/null -w "PATCH video -> %{http_code}\n" -X PATCH http://127.0.0.1:8080/api/videos/1
```

Expected: both `401` (routes exist, behind auth). A `404` means the new code did not deploy.

- [ ] **Step 6: Browser check**

Log in to `http://1.15.155.204:8080`, open the Videos page. Confirm: "Import from Drive" opens a two-step modal (URL → checkbox list); each pending video row has an account dropdown; a "Force" checkbox sits by the Publish button.

---

## Self-Review Notes

- **Spec coverage:** Part A — `import/list` + selected-files import (Tasks 1, 2), two-step modal (Task 3). Part B — `PATCH` account assignment (Task 4), manual/auto/force publish (Task 5), `force` endpoints (Task 6), account dropdown + force toggle (Task 7). Deploy (Task 8). Every spec section and error-table row maps to a task.
- **Type consistency:** `list_folder` → `[{id,name,already_imported}]`; `run_import`/`start_import` take `[{id,name}]`; `importDrive` sends `{files}`, `importList` sends `{folder_url}`; `set_account(conn,vid,account_id)`; `publish(conn,client,cooldown_hours,force)`; `_post_one(conn,client,v,acc)`. Frontend `DriveFile` / `Account` types match the API shapes. Consistent across tasks.
- **No red intermediate states:** Task 2 changes the coupled `run_import`/`start_import` signatures and the `/import` endpoint together in one commit. Every other task leaves the suite green.
- **No placeholders:** every step has complete code or an exact command.
