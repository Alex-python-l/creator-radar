# YouTube Short False Success Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop YouTube publications from being marked successful when YouTube rejected the title, then safely reprocess affected posts.

**Architecture:** Fix the root cause before submission by generating a YouTube-safe title using UTF-16 code-unit counting with a conservative buffer. Keep GeeLark status polling intact, but add YouTube success audit evidence so future `status=3` rows have the final GeeLark screenshot attached for diagnosis. Treat production recovery as a separate operator step: first pause YouTube, deploy the title fix, then requeue only verified false-success rows whose source files still exist.

**Tech Stack:** Python 3.11, FastAPI service modules, SQLModel/PostgreSQL, Procrastinate worker, GeeLark OpenAPI/RPA tasks, pytest.

---

## File Map

- Modify `geelark_client.py`
  - Add UTF-16 title length helpers.
  - Use a conservative YouTube title cap before calling `/open/v1/rpa/task/youtubePubShort`.
- Modify `tests/test_geelark_client.py`
  - Replace Python `[:100]` expectation with UTF-16-safe title tests.
  - Add emoji/regional-indicator regression coverage.
- Modify `app/services/posting_service.py`
  - On YouTube `status=3`, fetch task detail and record the final screenshot/log evidence in `publication_attempts`.
  - Do not use this audit call to block success in the first fix; the root failure is title rejection, and older real successes also had empty `shareLink`.
- Modify `tests/test_services.py`
  - Add coverage for YouTube status-3 audit recording.
  - Keep TikTok/Instagram/Facebook success behavior unchanged.
- Optional create `scripts/audit_youtube_false_success.py`
  - Read-only script to list candidate false-success YouTube rows and source-file availability.

---

## Task 1: Production Stop-Gap

**Files:**
- Modify: none
- Test: production SQL output

- [ ] **Step 1: Snapshot current YouTube-ready account flags**

Run:

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 <<'REMOTE'
set -e
docker exec -i svm-postgres psql -U mkt -d mkt -X -q <<'SQL'
\pset pager off
SELECT id, profile_name, youtube_logged_in, updated_at
FROM accounts
WHERE youtube_logged_in = 1
ORDER BY id;
SQL
REMOTE
```

Expected: rows for M-group YouTube accounts, currently `youtube_logged_in = 1`.

- [ ] **Step 2: Pause automatic YouTube publishing**

Run only after operator approval:

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 <<'REMOTE'
set -e
docker exec -i svm-postgres psql -U mkt -d mkt -X -q <<'SQL'
UPDATE accounts
SET youtube_logged_in = 0, updated_at = now()
WHERE youtube_logged_in = 1;

SELECT id, profile_name, youtube_logged_in
FROM accounts
WHERE youtube_logged_in = 1
ORDER BY id;
SQL
REMOTE
```

Expected: final `SELECT` returns zero rows. This prevents `auto_publish_scan` from queueing more YouTube false-success tasks while leaving TikTok/Instagram/Facebook unaffected.

- [ ] **Step 3: Verify pending YouTube rows remain pending**

Run:

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 <<'REMOTE'
set -e
docker exec -i svm-postgres psql -U mkt -d mkt -X -q <<'SQL'
\pset pager off
SELECT result, COUNT(*)
FROM publications
WHERE platform = 'youtube'
GROUP BY result
ORDER BY result;
SQL
REMOTE
```

Expected: `PENDING` count remains nonzero; no new YouTube rows move to `QUEUED`/`INITIATED` after the pause.

---

## Task 2: YouTube Title Sanitizer

**Files:**
- Modify: `tests/test_geelark_client.py`
- Modify: `geelark_client.py`

- [ ] **Step 1: Write failing UTF-16 title tests**

Add these tests near `test_create_youtube_post_task_truncates_title` in `tests/test_geelark_client.py`:

```python
def _utf16_units_for_test(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def test_create_youtube_post_task_truncates_title_to_safe_utf16_units(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        geelark_client.GeeLarkClient,
        "_post",
        lambda self, path, payload: captured.update(payload=payload)
        or {"taskId": "yt-safe"},
    )
    c = geelark_client.GeeLarkClient("token")

    c.create_youtube_post_task(
        "env-yt",
        "https://res/v.mp4",
        title="Yung kaba tuwing World Cup match, iba talaga. ⚽️🇵🇹 "
              "Portugal is looking strong, pero can DR Congo pull this off?",
    )

    title = captured["payload"]["title"]
    assert _utf16_units_for_test(title) <= 90
    assert title.endswith(" ")
    assert captured["payload"]["title"] == title.strip()


def test_create_youtube_post_task_uses_fallback_for_blank_title(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        geelark_client.GeeLarkClient,
        "_post",
        lambda self, path, payload: captured.update(payload=payload)
        or {"taskId": "yt-blank"},
    )
    c = geelark_client.GeeLarkClient("token")

    c.create_youtube_post_task("env-yt", "https://res/v.mp4", title="   ")

    assert captured["payload"]["title"] == "Short"
```

Update the existing `test_create_youtube_post_task_truncates_title` expectation:

```python
def test_create_youtube_post_task_truncates_title(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        geelark_client.GeeLarkClient,
        "_post",
        lambda self, path, payload: captured.update(payload=payload)
        or {"taskId": "yt-2"},
    )
    c = geelark_client.GeeLarkClient("token")
    c.create_youtube_post_task("env-2", "https://res/v.mp4", title="x" * 250)
    assert captured["payload"]["title"] == "x" * 90
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
uv run pytest tests/test_geelark_client.py::test_create_youtube_post_task_truncates_title tests/test_geelark_client.py::test_create_youtube_post_task_truncates_title_to_safe_utf16_units tests/test_geelark_client.py::test_create_youtube_post_task_uses_fallback_for_blank_title -q
```

Expected: at least the new UTF-16/buffer expectations fail because production code still uses `title[:100]`.

- [ ] **Step 3: Implement UTF-16-safe helper**

In `geelark_client.py`, add near the top-level constants:

```python
YOUTUBE_TITLE_SAFE_UNITS = 90


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _truncate_utf16_units(value: str, limit: int) -> str:
    out: list[str] = []
    used = 0
    for ch in (value or "").strip():
        units = _utf16_units(ch)
        if used + units > limit:
            break
        out.append(ch)
        used += units
    return "".join(out).strip()
```

Then change `create_youtube_post_task`:

```python
    def create_youtube_post_task(
        self,
        env_id: str,
        video_url: str,
        title: str,
    ) -> str:
        """Schedule a YouTube Shorts post. Returns the task ID.

        YouTube's mobile UI counts title length by UTF-16-ish units and
        GeeLark input can add one visible unit. Keep a 10-unit buffer so
        emoji/flags never push the UI to 101/100 and disable Upload Short.
        """
        import time as _time
        safe_title = _truncate_utf16_units(
            title or "", YOUTUBE_TITLE_SAFE_UNITS,
        ) or "Short"
        payload = {
            "scheduleAt": int(_time.time()),
            "id": env_id,
            "title": safe_title,
            "video": video_url,
            "sameStyleVoice": 0,
            "originalVoice": 100,
            "isDisclosureMandatory": False,
        }
        data = self._post("/open/v1/rpa/task/youtubePubShort", payload)
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(
                f"youtube task created but no taskId: {data}")
        return task_id
```

- [ ] **Step 4: Run title tests**

Run:

```bash
uv run pytest tests/test_geelark_client.py::test_create_youtube_post_task_payload_and_endpoint tests/test_geelark_client.py::test_create_youtube_post_task_truncates_title tests/test_geelark_client.py::test_create_youtube_post_task_truncates_title_to_safe_utf16_units tests/test_geelark_client.py::test_create_youtube_post_task_uses_fallback_for_blank_title tests/test_geelark_client.py::test_create_youtube_post_task_raises_when_taskid_missing -q
```

Expected: all selected tests pass.

---

## Task 3: YouTube Success Audit Evidence

**Files:**
- Modify: `tests/test_services.py`
- Modify: `app/services/posting_service.py`

- [ ] **Step 1: Write failing audit test**

Add this test after `test_check_publication_one_stores_share_link` in `tests/test_services.py`:

```python
def test_check_publication_one_records_youtube_success_detail(
        db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import posting_service

    aid = acc_repo.create(
        db,
        {"profile_name": "YT-Audit", "geelark_phone_id": "ph-yt-audit"},
    )
    f = tmp_path / "yt-audit.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(db, "yt-audit.mp4", str(f))
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="youtube")
    pub_repo.mark_initiated(
        db, pid, "caption", "yt-audit-task", "submitted",
    )

    class DoneNoLink:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 3, "shareLink": ""}]

        def get_task_detail(self, task_id):
            assert task_id == "yt-audit-task"
            return {
                "status": 3,
                "resultImages": [
                    "https://img/intermediate.jpg",
                    "https://img/final.jpg",
                ],
                "logs": [
                    "[2026-06-18] Wait for element to appear: Uploaded to Your Videos",
                    "[2026-06-18] Page screenshot: result:https://img/final.jpg",
                ],
            }

    outcome = posting_service.check_publication_one(db, DoneNoLink(), pid)

    assert outcome == "success"
    row = pub_repo.get(db, pid)
    assert row["result"] == "SUCCESS"
    attempts = pub_repo.list_attempts(db, pid)
    assert attempts[-1]["stage"] == "status_check"
    assert attempts[-1]["source"] == "geelark"
    assert attempts[-1]["message"] == "youtube task reported status=3"
    assert attempts[-1]["fail_screenshot"] == "https://img/final.jpg"
    assert attempts[-1]["geelark_task_id"] == "yt-audit-task"
```

- [ ] **Step 2: Run failing audit test**

Run:

```bash
uv run pytest tests/test_services.py::test_check_publication_one_records_youtube_success_detail -q
```

Expected: fail because status-3 YouTube detail is not recorded yet.

- [ ] **Step 3: Add success-detail helper**

In `app/services/posting_service.py`, add below `_geelark_failure_detail`:

```python
def _record_youtube_success_detail(session, client, publication_id: int,
                                   task_id: str) -> None:
    try:
        detail = client.get_task_detail(task_id)
    except Exception:
        return
    images = detail.get("resultImages") or []
    screenshot = images[-1] if images else None
    pub_repo.record_attempt(
        session,
        publication_id,
        stage="status_check",
        source="geelark",
        message="youtube task reported status=3",
        fail_screenshot=screenshot,
        geelark_task_id=task_id,
    )
```

Then change the `status == 3` branch inside `check_publication_one`:

```python
    if status == 3:
        share_link = (res[0].get("shareLink") or "").strip()
        if share_link:
            pub_repo.set_share_link(session, publication_id, share_link)
        if pub["platform"] == "youtube":
            _record_youtube_success_detail(
                session, client, publication_id, pub["geelark_task_id"],
            )
        pub_repo.set_result(session, publication_id, "SUCCESS", "completed")
        _stop_phone_best_effort(client, phone_id)
        _finalize_session(
            session, pub["account_id"], kind="success",
            owner_id=publication_id,
            observed_status=str(status),
        )
        _maybe_cleanup_video(session, pub["video_id"])
        return "success"
```

- [ ] **Step 4: Run status-check tests**

Run:

```bash
uv run pytest tests/test_services.py::test_check_publication_one_transitions tests/test_services.py::test_check_publication_one_stores_share_link tests/test_services.py::test_check_publication_one_records_youtube_success_detail -q
```

Expected: all selected tests pass.

---

## Task 4: Read-Only False-Success Audit Script

**Files:**
- Create: `scripts/audit_youtube_false_success.py`

- [ ] **Step 1: Create script**

Create `scripts/audit_youtube_false_success.py`:

```python
"""Audit YouTube publications that GeeLark marked successful.

Read-only. Emits CSV with source-file availability and GeeLark final
screenshot URL so operators can decide which rows need manual reset.
Run on production from /opt/mkt:
  uv run python scripts/audit_youtube_false_success.py --since 2026-06-16
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

from sqlalchemy import text

from app.db import session_scope
from app.services import geelark_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True,
                        help="UTC date/time lower bound, e.g. 2026-06-16")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = geelark_service.make_client()
    with session_scope() as session:
        rows = session.execute(
            text(
                """
                SELECT p.id AS publication_id,
                       p.video_id,
                       p.account_id,
                       a.profile_name,
                       p.geelark_task_id,
                       p.result,
                       p.created_at,
                       p.posted_at,
                       p.last_checked_at,
                       p.share_link,
                       v.original_filename,
                       v.stored_path
                FROM publications p
                JOIN videos v ON v.id = p.video_id
                LEFT JOIN accounts a ON a.id = p.account_id
                WHERE p.platform = 'youtube'
                  AND p.result = 'SUCCESS'
                  AND COALESCE(p.last_checked_at, p.posted_at, p.created_at)
                        >= :since
                ORDER BY COALESCE(p.last_checked_at, p.posted_at, p.created_at),
                         p.id
                """
            ),
            {"since": args.since},
        ).mappings().all()

    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=[
            "publication_id", "video_id", "account_id", "profile_name",
            "task_id", "created_at", "last_checked_at", "filename",
            "stored_path", "source_exists", "share_link", "geelark_status",
            "final_screenshot",
        ],
    )
    writer.writeheader()
    for row in rows:
        final_screenshot = ""
        geelark_status = ""
        if row["geelark_task_id"]:
            try:
                detail = client.get_task_detail(row["geelark_task_id"])
                geelark_status = str(detail.get("status"))
                images = detail.get("resultImages") or []
                final_screenshot = images[-1] if images else ""
            except Exception as exc:
                geelark_status = f"detail_error:{exc.__class__.__name__}"
        stored_path = row["stored_path"] or ""
        writer.writerow({
            "publication_id": row["publication_id"],
            "video_id": row["video_id"],
            "account_id": row["account_id"],
            "profile_name": row["profile_name"],
            "task_id": row["geelark_task_id"],
            "created_at": row["created_at"],
            "last_checked_at": row["last_checked_at"],
            "filename": row["original_filename"],
            "stored_path": stored_path,
            "source_exists": bool(stored_path and os.path.exists(stored_path)),
            "share_link": row["share_link"] or "",
            "geelark_status": geelark_status,
            "final_screenshot": final_screenshot,
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run script locally against test imports**

Run:

```bash
uv run python scripts/audit_youtube_false_success.py --since 2026-06-16 >/tmp/youtube-audit.csv
```

Expected locally: either a CSV header only, or rows from the local database. No writes occur.

---

## Task 5: Full Verification

**Files:**
- Modify: none
- Test: pytest suite subset first, then full suite if time permits

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
uv run pytest tests/test_geelark_client.py tests/test_services.py::test_check_publication_one_records_youtube_success_detail tests/test_services.py::test_check_publication_one_transitions tests/test_services.py::test_check_publication_one_stores_share_link -q
```

Expected: all pass.

- [ ] **Step 2: Run publish task tests**

Run:

```bash
uv run pytest tests/test_tasks_publish.py -q
```

Expected: all pass.

- [ ] **Step 3: Run full backend suite**

Run:

```bash
uv run pytest -q
```

Expected: all pass. If the full suite is too slow, record the focused test results and the reason full suite was skipped.

---

## Task 6: Deploy

**Files:**
- Use: `docs/production-deploy.md`

- [ ] **Step 1: Build and test per production deploy doc**

Run from repo root:

```bash
uv run pytest -q
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build
cd ..
```

Expected: tests pass and frontend build completes.

- [ ] **Step 2: Package, upload, extract, restart**

Follow `docs/production-deploy.md` exactly. The package must include:

```bash
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-deploy.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' \
  app alembic alembic.ini geelark_client.py generate_caption.py generate_comment.py \
  brightdata_client.py pyproject.toml uv.lock frontend/dist scripts
```

Note: include `scripts` for `audit_youtube_false_success.py`.

- [ ] **Step 3: Smoke test**

Run:

```bash
curl -sS -o /dev/null -w "/ -> %{http_code}\n" \
  http://139.196.52.92:8080/
curl -sS -o /dev/null -w "/api/accounts -> %{http_code}\n" \
  http://139.196.52.92:8080/api/accounts
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  'systemctl is-active mkt mkt-worker nginx'
```

Expected: `/ -> 200`, `/api/accounts -> 401`, services active.

---

## Task 7: Controlled YouTube Re-Enable and One-Post Verification

**Files:**
- Modify: production database only after approval

- [ ] **Step 1: Re-enable one test YouTube account**

Run after deployment and operator approval:

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 <<'REMOTE'
set -e
docker exec -i svm-postgres psql -U mkt -d mkt -X -q <<'SQL'
UPDATE accounts
SET youtube_logged_in = 1, updated_at = now()
WHERE profile_name = 'M7';

SELECT id, profile_name, youtube_logged_in
FROM accounts
WHERE profile_name = 'M7';
SQL
REMOTE
```

Expected: one row, `M7`, `youtube_logged_in = 1`.

- [ ] **Step 2: Queue one known-safe YouTube publication**

Use the dashboard or API to publish exactly one pending YouTube video for M7. Do not bulk re-enable all accounts yet.

- [ ] **Step 3: Verify GeeLark detail and YouTube account**

Run:

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 <<'REMOTE'
set -e
docker exec -i svm-postgres psql -U mkt -d mkt -X -q <<'SQL'
\pset pager off
SELECT p.id, p.result, p.notes, p.geelark_task_id,
       p.created_at, p.posted_at, p.last_checked_at,
       p.share_link, a.profile_name, v.original_filename
FROM publications p
JOIN videos v ON v.id = p.video_id
LEFT JOIN accounts a ON a.id = p.account_id
WHERE p.platform = 'youtube'
ORDER BY COALESCE(p.last_checked_at, p.posted_at, p.created_at) DESC, p.id DESC
LIMIT 5;
SQL
REMOTE
```

Expected: the one controlled row reaches terminal state. Open its GeeLark final screenshot from `publication_attempts`; it should not show `101/100` or a disabled `Upload Short` button.

- [ ] **Step 4: Re-enable remaining YouTube accounts**

Run only after the controlled post is visible on the YouTube account:

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 <<'REMOTE'
set -e
docker exec -i svm-postgres psql -U mkt -d mkt -X -q <<'SQL'
UPDATE accounts
SET youtube_logged_in = 1, updated_at = now()
WHERE profile_name IN ('M1','M2','M3','M4','M5','M6','M7','M8');

SELECT id, profile_name, youtube_logged_in
FROM accounts
WHERE profile_name IN ('M1','M2','M3','M4','M5','M6','M7','M8')
ORDER BY id;
SQL
REMOTE
```

Expected: eight rows, all `youtube_logged_in = 1`.

---

## Task 8: Historical False-Success Recovery

**Files:**
- Use: `scripts/audit_youtube_false_success.py`
- Modify: production database only after manual review

- [ ] **Step 1: Export suspect rows**

Run on production:

```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 <<'REMOTE'
set -e
cd /opt/mkt
/usr/local/bin/uv run python scripts/audit_youtube_false_success.py \
  --since 2026-06-16 > /tmp/youtube-false-success-audit.csv
wc -l /tmp/youtube-false-success-audit.csv
head -5 /tmp/youtube-false-success-audit.csv
REMOTE
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  root@139.196.52.92:/tmp/youtube-false-success-audit.csv \
  /tmp/youtube-false-success-audit.csv
```

Expected: CSV includes rows since 2026-06-16 with final screenshot URLs and `source_exists`.

- [ ] **Step 2: Manually classify rows**

Open final screenshots for candidates. Rows whose final screenshot shows `Add details`, `Write a shorter caption`, `101/100`, or a disabled `Upload Short` are false successes. Rows whose final screenshot shows the YouTube channel page with the new Short visible are real successes.

- [ ] **Step 3: Reset only verified false successes with available source files**

Prepare a comma-separated id list such as `6744,6748,6752`. Run only after manual classification:

```bash
IDS="6744,6748,6752"
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 <<REMOTE
set -e
docker exec -i svm-postgres psql -U mkt -d mkt -X -q <<SQL
WITH target AS (
  SELECT unnest(string_to_array('${IDS}', ',')::int[]) AS id
)
SELECT p.id, p.result, v.stored_path
FROM publications p
JOIN videos v ON v.id = p.video_id
JOIN target t ON t.id = p.id
WHERE v.stored_path IS NOT NULL;

UPDATE publications p
SET result = 'PENDING',
    geelark_task_id = NULL,
    posted_at = NULL,
    last_checked_at = now(),
    notes = 'reset after YouTube title over-limit false success',
    retry_count = 0,
    next_retry_at = NULL,
    fail_code = NULL,
    fail_screenshot = NULL
FROM target t
WHERE p.id = t.id;

SELECT id, result, notes
FROM publications
WHERE id IN (SELECT id FROM target)
ORDER BY id;
SQL
REMOTE
```

Expected: selected rows become `PENDING`. Do not reset rows whose source file is missing; those need re-import/re-upload.

- [ ] **Step 4: Wake publish scan**

Use the dashboard to assign/publish, or run the existing API path that wakes `auto_publish_scan`. Verify only a small batch first.

---

## Self-Review

- Spec coverage: This plan addresses immediate stop-gap, root title-length failure, false-success observability, deployment, controlled verification, and historical recovery.
- Placeholder scan: No TBD/TODO placeholders remain. Every code task has concrete snippets and commands.
- Type consistency: Helper names are `_utf16_units`, `_truncate_utf16_units`, and `YOUTUBE_TITLE_SAFE_UNITS`; tests reference only public payload effects except for local test helper `_utf16_units_for_test`.
