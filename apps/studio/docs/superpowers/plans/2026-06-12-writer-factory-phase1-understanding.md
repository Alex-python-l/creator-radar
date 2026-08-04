# Writer Factory — Phase 1 (Video Understanding) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Layer 1 of the Writer Factory pipeline — every newly-uploaded video gets a structured `VideoFacts` JSON extracted by Gemini and stored on the video row, ready for Layer 2-6 to consume in later phases. Also create the empty `writer_audit` table that Layer 7 will fill.

**Architecture:** New module `app/writer/` houses the pipeline. Phase 1 ships only `schemas.py`, `understanding.py`, and `prompts/understanding.txt`. A new procrastinate task `extract_understanding_task` is deferred from the video-upload endpoint right after the video row is created. A manual `/api/writer/understanding/{vid}/refresh` endpoint lets operators re-run extraction without re-uploading.

**Tech Stack:** FastAPI + SQLModel + SQLAlchemy (existing), Alembic for schema, Procrastinate for async tasks, Gemini Files API via the existing `google-genai` client (already imported by `generate_caption.py`), Pydantic for structured Gemini output, pytest.

**Spec:** [docs/superpowers/specs/2026-06-12-writer-factory-design.md](../specs/2026-06-12-writer-factory-design.md) — Phase 1 corresponds to spec section 12 row "W1" plus the schema rows in section 4 and the failure-handling row for Layer 1 in section 8.

---

## File Map

| Path | Status | Purpose |
|---|---|---|
| `alembic/versions/aa11bb22cc33_writer_factory_phase1.py` | NEW | Create `writer_audit` table + add `understanding` / `understanding_status` columns to `videos` and `discovered_videos` |
| `app/writer/__init__.py` | NEW | Empty package marker |
| `app/writer/schemas.py` | NEW | `VideoFacts` Pydantic model (strict schema for Gemini output) + `UnderstandingStatus` literals |
| `app/writer/prompts/understanding.txt` | NEW | Versioned prompt template that Gemini consumes |
| `app/writer/understanding.py` | NEW | `extract_video_facts(local_path) -> VideoFacts` — single Gemini call, schema-bound, file-API based |
| `app/repositories/videos.py` | MODIFY | Add `set_understanding(session, vid, facts, status)` helper |
| `app/tasks/writer_understanding.py` | NEW | `extract_understanding_task(video_id)` — procrastinate task that calls `extract_video_facts` and persists the result |
| `app/tasks/__init__.py` | MODIFY | Register `app.tasks.writer_understanding` in `import_paths` |
| `app/api/videos.py:249` | MODIFY | After `vid_repo.create` succeeds in `/upload`, defer `extract_understanding_task` |
| `app/api/writer.py` | NEW | Single `POST /api/writer/understanding/{vid}/refresh` endpoint |
| `app/main.py` | MODIFY | Mount `app.api.writer.router` |
| `tests/test_writer_schemas.py` | NEW | `VideoFacts` validation tests |
| `tests/test_writer_understanding.py` | NEW | `extract_video_facts` with mocked Gemini |
| `tests/test_writer_understanding_task.py` | NEW | Procrastinate task end-to-end with mocked Gemini |
| `tests/test_writer_api.py` | NEW | Refresh endpoint defers the task |
| `tests/test_api_videos_upload.py` (or extend existing `tests/test_api.py`) | MODIFY | Upload now also defers `extract_understanding_task` |

The procrastinate worker process picks up `extract_understanding_task` automatically once `app.tasks.__init__.py` registers the module. The web tier defers via the shared `procrastinate_app`.

---

## Task 1: Alembic migration

**Files:**
- Create: `alembic/versions/aa11bb22cc33_writer_factory_phase1.py`

- [ ] **Step 1: Confirm current head**

Run: `uv run alembic current 2>/dev/null || uv run alembic heads`
Expected output contains `f0a1b2c3d4e5` (set as `down_revision`).

- [ ] **Step 2: Create the migration file**

Path: `alembic/versions/aa11bb22cc33_writer_factory_phase1.py`

```python
"""writer factory phase 1 — understanding columns + writer_audit table

Revision ID: aa11bb22cc33
Revises: f0a1b2c3d4e5
Create Date: 2026-06-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "aa11bb22cc33"
down_revision: Union[str, Sequence[str], None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # videos / discovered_videos — Layer-1 understanding cache.
    for table in ("videos", "discovered_videos"):
        op.add_column(
            table,
            sa.Column("understanding", postgresql.JSONB(), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(
                "understanding_status",
                sa.String(length=20),
                nullable=False,
                server_default="pending",
            ),
        )

    # writer_audit — populated by Layers 6-7 in later phases; created
    # now so the schema is in place when those phases land.
    op.create_table(
        "writer_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("publication_id", sa.Integer(), nullable=True),
        sa.Column("engagement_run_id", sa.Integer(), nullable=True),
        sa.Column("video_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("voice", postgresql.JSONB(), nullable=False),
        sa.Column("angle", sa.String(length=40), nullable=False),
        sa.Column("trend_anchors_used", postgresql.JSONB(), nullable=True),
        sa.Column("candidates", postgresql.JSONB(), nullable=False),
        sa.Column("winner_idx", sa.Integer(), nullable=False),
        sa.Column("winner_fingerprint", postgresql.JSONB(), nullable=False),
        sa.Column("dedup_self_max", sa.Float(), nullable=True),
        sa.Column("dedup_cross_max", sa.Float(), nullable=True),
        sa.Column("total_gemini_calls", sa.Integer(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"], ["publications.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["engagement_run_id"], ["engagement_runs.id"], ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "(publication_id IS NULL) <> (engagement_run_id IS NULL)",
            name="writer_audit_one_target",
        ),
    )
    op.create_index(
        "idx_writer_audit_account_time",
        "writer_audit",
        ["account_id", sa.text("generated_at DESC")],
    )
    op.create_index(
        "idx_writer_audit_kind_time",
        "writer_audit",
        ["kind", sa.text("generated_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_writer_audit_kind_time", table_name="writer_audit")
    op.drop_index("idx_writer_audit_account_time", table_name="writer_audit")
    op.drop_table("writer_audit")
    for table in ("discovered_videos", "videos"):
        op.drop_column(table, "understanding_status")
        op.drop_column(table, "understanding")
```

- [ ] **Step 3: Apply against local DB**

Run: `uv run alembic upgrade head`
Expected output ends with `Running upgrade f0a1b2c3d4e5 -> aa11bb22cc33`.

- [ ] **Step 4: Verify schema landed**

Run:
```bash
uv run python -c "
from sqlalchemy import create_engine, text
from app.config import DATABASE_URL
e = create_engine(DATABASE_URL)
with e.connect() as c:
    cols = c.execute(text(\"\"\"SELECT column_name FROM information_schema.columns
                              WHERE table_name='videos' AND column_name LIKE 'understanding%'\"\"\")).all()
    print('videos cols:', cols)
    tbls = c.execute(text(\"SELECT table_name FROM information_schema.tables WHERE table_name='writer_audit'\")).all()
    print('writer_audit:', tbls)
"
```
Expected: `videos cols: [('understanding',), ('understanding_status',)]` and `writer_audit: [('writer_audit',)]`.

- [ ] **Step 5: Verify downgrade also works**

Run: `uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: no errors. Final head is `aa11bb22cc33`.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/aa11bb22cc33_writer_factory_phase1.py
git commit -m "feat(writer): phase1 schema — videos.understanding + writer_audit table"
```

---

## Task 2: VideoFacts Pydantic schema

**Files:**
- Create: `app/writer/__init__.py`
- Create: `app/writer/schemas.py`
- Test: `tests/test_writer_schemas.py`

- [ ] **Step 1: Create the empty package marker**

Path: `app/writer/__init__.py` — empty file (0 bytes).

- [ ] **Step 2: Write the failing test**

Path: `tests/test_writer_schemas.py`

```python
"""Tests for app.writer.schemas.VideoFacts."""
import pytest
from pydantic import ValidationError

from app.writer.schemas import VideoFacts, ClimaxMoment


def test_video_facts_accepts_full_payload():
    facts = VideoFacts(
        opening_2s="young woman zooms into laptop screen",
        climax_moment=ClimaxMoment(
            timestamp_s=4.2,
            what="AI rewrites her 5.5 sentence into 7.5 inline",
        ),
        on_screen_text=["My band 5.5 → 7.5 in 3 seconds"],
        spoken_verbatim=["wait whaaat"],
        subject_expression_at_climax="wide-eyed disbelief, hand over mouth",
        visible_props=["Cathoven UI", "MacBook"],
        unique_moments=["highlights word 'lowkey' being academically upgraded"],
        vibe="studying-with-me, lo-fi music",
    )
    assert facts.climax_moment.timestamp_s == 4.2
    assert "wait whaaat" in facts.spoken_verbatim


def test_video_facts_allows_empty_lists():
    """Some videos have no on-screen text / no spoken words. That's
    valid — empty list, not missing field."""
    facts = VideoFacts(
        opening_2s="x",
        climax_moment=ClimaxMoment(timestamp_s=0.0, what="y"),
        on_screen_text=[],
        spoken_verbatim=[],
        subject_expression_at_climax="neutral",
        visible_props=[],
        unique_moments=[],
        vibe="ambient",
    )
    assert facts.spoken_verbatim == []


def test_video_facts_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        VideoFacts(opening_2s="x")  # type: ignore[call-arg]


def test_video_facts_rejects_extra_fields():
    """Strict mode: Gemini hallucinating a new key should fail validation,
    not silently drop. This is the whole point of using Pydantic."""
    with pytest.raises(ValidationError):
        VideoFacts.model_validate({
            "opening_2s": "x",
            "climax_moment": {"timestamp_s": 0.0, "what": "y"},
            "on_screen_text": [],
            "spoken_verbatim": [],
            "subject_expression_at_climax": "neutral",
            "visible_props": [],
            "unique_moments": [],
            "vibe": "ambient",
            "bonus_field_gemini_made_up": "oops",
        })


def test_climax_timestamp_must_be_non_negative():
    with pytest.raises(ValidationError):
        ClimaxMoment(timestamp_s=-1.0, what="x")
```

- [ ] **Step 3: Run test — expect failure**

Run: `uv run pytest tests/test_writer_schemas.py -v`
Expected: `ImportError: cannot import name 'VideoFacts' from 'app.writer.schemas'` or `ModuleNotFoundError`.

- [ ] **Step 4: Implement the schema**

Path: `app/writer/schemas.py`

```python
"""Pydantic models for the Writer Factory pipeline.

These models double as JSON Schema definitions sent to Gemini's
structured-output mode — anything the model emits must validate or
the pipeline retries / falls back. `model_config = ConfigDict(extra='forbid')`
is deliberate: Gemini occasionally adds fields like "explanation" or
"confidence_score"; we treat that as a schema violation rather than
silently dropping them, because it usually means the prompt drift is
already underway.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


UnderstandingStatus = Literal["pending", "ok", "failed"]


class ClimaxMoment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp_s: float = Field(
        ge=0.0,
        description="Seconds from video start to the most memorable moment.",
    )
    what: str = Field(
        min_length=1,
        description="Concrete description of what happens at that timestamp.",
    )


class VideoFacts(BaseModel):
    """Layer 1 output. Strict schema — every field is required.

    Empty lists are valid (some videos have no spoken words / no props),
    but the field must be present. Gemini learns to populate everything
    when the schema enforces it.
    """
    model_config = ConfigDict(extra="forbid")

    opening_2s: str = Field(
        min_length=1,
        description="What literally happens in the first 2 seconds.",
    )
    climax_moment: ClimaxMoment
    on_screen_text: list[str] = Field(
        description="Verbatim text overlaid on the video (subtitles, "
                    "captions, on-screen graphics). Empty list if none.",
    )
    spoken_verbatim: list[str] = Field(
        description="Short verbatim quotes from the audio. Empty list "
                    "if the video is silent or non-verbal.",
    )
    subject_expression_at_climax: str = Field(
        min_length=1,
        description="Facial expression / body language at the climax "
                    "moment. 'neutral' is a valid value.",
    )
    visible_props: list[str] = Field(
        description="Concrete objects, brands, locations visible "
                    "on screen. Empty list if none stand out.",
    )
    unique_moments: list[str] = Field(
        description="Any moment that is specifically memorable beyond "
                    "the climax. Empty list if nothing else stands out.",
    )
    vibe: str = Field(
        min_length=1,
        description="One-line aesthetic / mood description.",
    )
```

- [ ] **Step 5: Run test — expect pass**

Run: `uv run pytest tests/test_writer_schemas.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add app/writer/__init__.py app/writer/schemas.py tests/test_writer_schemas.py
git commit -m "feat(writer): VideoFacts pydantic schema for Layer 1"
```

---

## Task 3: Understanding prompt + extraction function

**Files:**
- Create: `app/writer/prompts/understanding.txt`
- Create: `app/writer/understanding.py`
- Test: `tests/test_writer_understanding.py`

- [ ] **Step 1: Write the prompt template**

Path: `app/writer/prompts/understanding.txt`

```
You are a video analyst. Watch the attached video and emit a single
JSON object that strictly matches the provided schema.

CRITICAL RULES:
- Describe ONLY what is actually visible / audible. Do not infer
  what the video might be promoting, who the creator is, or what the
  caption should say. Other layers do that.
- Use concrete nouns and verbs. "person uses app" is too vague —
  write "young woman taps button labeled 'Rewrite' and screen recording
  shows the AI replacing her sentence".
- `opening_2s` is what happens in the first 2 seconds, literally.
- `climax_moment` is the single most memorable / attention-grabbing
  moment. Pick exactly one. Provide its timestamp in seconds.
- `spoken_verbatim` contains short quotes (≤ 8 words each) lifted
  verbatim from the audio. Empty list if silent or non-verbal.
- `on_screen_text` contains text overlaid on the video, transcribed
  verbatim. Empty list if none.
- `visible_props` should focus on items that an outside viewer would
  notice (brand logos, distinctive objects, specific UI). Skip
  generic items like "laptop" unless the laptop is the focus.
- `unique_moments` captures anything memorable beyond the climax that
  a caption could reference. Empty list if nothing else stands out.
- `vibe` is one line on aesthetic / energy ("studying-with-me lo-fi",
  "high-energy sports highlights", "calm voiceover tutorial").

Do not write any captions. Do not interpret the video's purpose. Do
not output anything outside the JSON object.
```

- [ ] **Step 2: Write the failing test**

Path: `tests/test_writer_understanding.py`

```python
"""Tests for app.writer.understanding.extract_video_facts.

Uses a fake Gemini client that mirrors the upload → poll → generate
flow already used by generate_caption.py. No real Gemini call here.
"""
import json
import pytest

from app.writer.schemas import VideoFacts


VALID_PAYLOAD = {
    "opening_2s": "young woman zooms into laptop showing IELTS essay",
    "climax_moment": {
        "timestamp_s": 4.2,
        "what": "AI rewrites her 5.5 sentence into 7.5 inline",
    },
    "on_screen_text": ["My band 5.5 → 7.5"],
    "spoken_verbatim": ["wait whaaat"],
    "subject_expression_at_climax": "wide-eyed disbelief",
    "visible_props": ["Cathoven UI", "MacBook"],
    "unique_moments": ["highlights the word 'lowkey'"],
    "vibe": "studying-with-me, lo-fi",
}


class _FakeFile:
    name = "files/fake-id"
    uri = "https://example.test/files/fake-id"
    state = "ACTIVE"


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeFilesAPI:
    def __init__(self):
        self.deleted: list[str] = []

    def upload(self, *, file, config):  # noqa: ARG002
        return _FakeFile()

    def get(self, *, name, config=None):  # noqa: ARG002
        return _FakeFile()

    def delete(self, *, name):
        self.deleted.append(name)


class _FakeModelsAPI:
    def __init__(self, text: str):
        self._text = text
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _FakeResponse(self._text)


class _FakeGeminiClient:
    def __init__(self, text: str):
        self.files = _FakeFilesAPI()
        self.models = _FakeModelsAPI(text)


@pytest.fixture
def stub_video(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\x00\x00")
    return str(p)


def test_extract_video_facts_returns_validated_model(monkeypatch, stub_video):
    from app.writer import understanding
    monkeypatch.setattr(
        understanding,
        "_get_client",
        lambda: _FakeGeminiClient(json.dumps(VALID_PAYLOAD)),
    )

    facts = understanding.extract_video_facts(stub_video)

    assert isinstance(facts, VideoFacts)
    assert facts.climax_moment.timestamp_s == 4.2
    assert facts.spoken_verbatim == ["wait whaaat"]


def test_extract_video_facts_raises_on_schema_violation(monkeypatch, stub_video):
    """Gemini drift (extra fields, missing fields) should raise so the
    caller can record failed status and move on."""
    from app.writer import understanding
    bad = dict(VALID_PAYLOAD)
    bad["unauthorized_extra_key"] = "drift"
    monkeypatch.setattr(
        understanding,
        "_get_client",
        lambda: _FakeGeminiClient(json.dumps(bad)),
    )

    with pytest.raises(Exception):  # ValidationError or RuntimeError wrapper
        understanding.extract_video_facts(stub_video)


def test_extract_video_facts_cleans_up_gemini_file(monkeypatch, stub_video):
    """Even when extraction succeeds the temp file uploaded to Gemini
    must be deleted on exit (we pay storage for files we leave behind)."""
    from app.writer import understanding
    client = _FakeGeminiClient(json.dumps(VALID_PAYLOAD))
    monkeypatch.setattr(understanding, "_get_client", lambda: client)

    understanding.extract_video_facts(stub_video)

    assert client.files.deleted == ["files/fake-id"]
```

- [ ] **Step 3: Run test — expect failure**

Run: `uv run pytest tests/test_writer_understanding.py -v`
Expected: `ImportError: cannot import name 'extract_video_facts'`.

- [ ] **Step 4: Implement the extraction function**

Path: `app/writer/understanding.py`

```python
"""Layer 1 of the Writer Factory — video understanding.

One Gemini call per video. Strict JSON schema. The result is cached on
videos.understanding so all later layers (and every generation rerun)
read from DB rather than re-paying for understanding.

This module deliberately reuses the lower-level Gemini Files helpers
already proven inside generate_caption.py rather than building parallel
infrastructure. When the writer factory is fully landed (Phase 5)
generate_caption.py can be refactored to share these helpers; for
Phase 1 we just import what's there and move on.
"""
import json
import logging
import time
from pathlib import Path

import httpx

from app.writer.schemas import VideoFacts

log = logging.getLogger(__name__)

# Reuse the timeouts from generate_caption.py — same Gemini Files API
# semantics. Importing constants keeps both modules in lockstep.
from generate_caption import (
    FILE_PROCESSING_POLL,
    FILE_PROCESSING_TIMEOUT,
    GEMINI_FILE_STATUS_TIMEOUT_MS,
    GEMINI_MODEL,
    _get_client,
)


_PROMPT_PATH = Path(__file__).parent / "prompts" / "understanding.txt"


def _read_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _wait_for_active(client, file_name: str) -> None:
    """Poll the Files API until the upload finishes processing.

    Mirrors the polling loop in generate_caption.generate_caption_for_local_file.
    Kept as a private helper so when we refactor to share, the
    refactor diff stays small.
    """
    deadline = time.time() + FILE_PROCESSING_TIMEOUT
    poll_config = {"http_options": {"timeout": GEMINI_FILE_STATUS_TIMEOUT_MS}}
    last_poll_error: Exception | None = None
    while time.time() < deadline:
        try:
            f = client.files.get(name=file_name, config=poll_config)
            last_poll_error = None
        except httpx.TransportError as e:
            last_poll_error = e
            log.warning("  Gemini file status poll failed; retrying: %s", e)
            time.sleep(FILE_PROCESSING_POLL)
            continue
        state = f.state
        state_val = state.value if hasattr(state, "value") else str(state)
        if str(state_val) in ("2", "ACTIVE"):
            return
        if str(state_val) in ("3", "FAILED"):
            raise RuntimeError(f"Gemini file processing failed (state={state_val})")
        time.sleep(FILE_PROCESSING_POLL)
    msg = (
        f"Timed out waiting for Gemini to process file after "
        f"{FILE_PROCESSING_TIMEOUT}s"
    )
    if last_poll_error is not None:
        msg += f"; last status poll failed: {last_poll_error}"
    raise RuntimeError(msg)


def extract_video_facts(local_path: str, *, model: str | None = None) -> VideoFacts:
    """Upload `local_path` to Gemini, run the understanding prompt with
    strict JSON-schema output, and return a validated `VideoFacts`.

    The Gemini file is deleted on exit regardless of success or failure.

    Raises:
        RuntimeError — Gemini failed (timeout, FAILED state, empty output)
        pydantic.ValidationError — Gemini's JSON didn't match VideoFacts schema
    """
    client = _get_client()
    chosen_model = (model or "").strip() or GEMINI_MODEL

    log.info("  writer/understanding: uploading %s", Path(local_path).name)
    with open(local_path, "rb") as f:
        gemini_file = client.files.upload(
            file=f, config={"mime_type": "video/mp4"},
        )
    file_name = gemini_file.name

    try:
        _wait_for_active(client, file_name)

        log.info("  writer/understanding: generating facts (model=%s)", chosen_model)
        response = client.models.generate_content(
            model=chosen_model,
            contents=[
                {"role": "user", "parts": [
                    {"file_data": {"file_uri": gemini_file.uri,
                                    "mime_type": "video/mp4"}},
                    {"text": _read_prompt()},
                ]},
            ],
            config={
                "response_mime_type": "application/json",
                "response_schema": VideoFacts.model_json_schema(),
                # No thinking budget — understanding is mechanical
                # description, not creative work. Cheaper + faster.
                "thinking_config": {"thinking_budget": 0},
            },
        )
        raw = (response.text or "").strip()
    finally:
        try:
            client.files.delete(name=file_name)
        except Exception:
            pass

    if not raw:
        raise RuntimeError("Gemini returned empty understanding response")

    data = json.loads(raw)
    return VideoFacts.model_validate(data)
```

- [ ] **Step 5: Run test — expect pass**

Run: `uv run pytest tests/test_writer_understanding.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run the full suite to confirm nothing regressed**

Run: `uv run pytest -q`
Expected: previous count + 8 new tests, all passing.

- [ ] **Step 7: Commit**

```bash
git add app/writer/prompts/understanding.txt app/writer/understanding.py tests/test_writer_understanding.py
git commit -m "feat(writer): extract_video_facts — Layer 1 Gemini call with strict schema"
```

---

## Task 4: Procrastinate task + repository helper

**Files:**
- Modify: `app/repositories/videos.py`
- Create: `app/tasks/writer_understanding.py`
- Modify: `app/tasks/__init__.py`
- Test: `tests/test_writer_understanding_task.py`

- [ ] **Step 1: Write the failing test (repository helper)**

Open `tests/test_repositories_videos.py` (it already exists). Append:

```python
def test_set_understanding_persists_facts_and_status(db):
    from app.repositories import videos as vid_repo
    vid = vid_repo.create(db, "v.mp4", "/tmp/v.mp4")
    vid_repo.set_understanding(
        db, vid,
        facts={"opening_2s": "x", "vibe": "y"},
        status="ok",
    )
    row = vid_repo.get(db, vid)
    assert row["understanding"]["opening_2s"] == "x"
    assert row["understanding_status"] == "ok"


def test_set_understanding_failed_clears_facts(db):
    from app.repositories import videos as vid_repo
    vid = vid_repo.create(db, "v.mp4", "/tmp/v.mp4")
    vid_repo.set_understanding(db, vid, facts=None, status="failed")
    row = vid_repo.get(db, vid)
    assert row["understanding"] is None
    assert row["understanding_status"] == "failed"
```

- [ ] **Step 2: Run test — expect failure**

Run: `uv run pytest tests/test_repositories_videos.py -v -k set_understanding`
Expected: `AttributeError: module 'app.repositories.videos' has no attribute 'set_understanding'`.

- [ ] **Step 3: Implement the repository helper**

Open `app/repositories/videos.py`. Find any existing `update_media_meta` or similar helper and add this function next to it (so it's near related code):

```python
def set_understanding(session, video_id: int, *,
                      facts: dict | None,
                      status: str) -> None:
    """Persist Layer 1 output on the videos row.

    status: 'pending' | 'ok' | 'failed'. When status='failed', pass
    facts=None — the row keeps the prior facts but the status reflects
    the latest attempt. (Caller may also pass facts=None for 'ok' if
    the schema was relaxed; right now Phase 1 always pairs facts with
    'ok'.)
    """
    from sqlalchemy import text
    session.execute(
        text("""UPDATE videos
                SET understanding        = CAST(:facts AS JSONB),
                    understanding_status = :status
                WHERE id = :vid"""),
        {"facts": _json_or_null(facts), "status": status, "vid": video_id},
    )
    session.commit()


def _json_or_null(facts: dict | None) -> str | None:
    import json
    return None if facts is None else json.dumps(facts)
```

- [ ] **Step 4: Run test — expect pass**

Run: `uv run pytest tests/test_repositories_videos.py -v -k set_understanding`
Expected: 2 passed.

- [ ] **Step 5: Write the failing test (procrastinate task)**

Path: `tests/test_writer_understanding_task.py`

```python
"""End-to-end test for extract_understanding_task using the
InMemoryConnector fixture (no real PG queue)."""
import asyncio
import json
import pytest


VALID_PAYLOAD = {
    "opening_2s": "x",
    "climax_moment": {"timestamp_s": 1.0, "what": "y"},
    "on_screen_text": [],
    "spoken_verbatim": [],
    "subject_expression_at_climax": "neutral",
    "visible_props": [],
    "unique_moments": [],
    "vibe": "ambient",
}


def test_extract_understanding_task_writes_facts_to_video(
        session, procrastinate_app, monkeypatch, tmp_path):
    from app.repositories import videos as vid_repo
    from app.tasks.writer_understanding import extract_understanding_task
    from app.writer import understanding

    f = tmp_path / "v.mp4"
    f.write_bytes(b"\x00")
    vid = vid_repo.create(session, "v.mp4", str(f))

    monkeypatch.setattr(
        understanding, "extract_video_facts",
        lambda path, **kw: understanding.VideoFacts.model_validate(VALID_PAYLOAD),
    )

    result = asyncio.run(extract_understanding_task(video_id=vid))

    assert "ok" in result.lower()
    row = vid_repo.get(session, vid)
    assert row["understanding"]["opening_2s"] == "x"
    assert row["understanding_status"] == "ok"


def test_extract_understanding_task_marks_failed_on_exception(
        session, procrastinate_app, monkeypatch, tmp_path):
    from app.repositories import videos as vid_repo
    from app.tasks.writer_understanding import extract_understanding_task
    from app.writer import understanding

    f = tmp_path / "v.mp4"
    f.write_bytes(b"\x00")
    vid = vid_repo.create(session, "v.mp4", str(f))

    def boom(path, **kw):
        raise RuntimeError("Gemini exploded")
    monkeypatch.setattr(understanding, "extract_video_facts", boom)

    result = asyncio.run(extract_understanding_task(video_id=vid))
    assert "failed" in result.lower()
    row = vid_repo.get(session, vid)
    assert row["understanding_status"] == "failed"
    assert row["understanding"] is None


def test_extract_understanding_task_skips_missing_video(
        session, procrastinate_app, monkeypatch):
    from app.tasks.writer_understanding import extract_understanding_task
    result = asyncio.run(extract_understanding_task(video_id=999999))
    assert "skipped" in result.lower() or "not found" in result.lower()


def test_extract_understanding_task_skips_missing_file(
        session, procrastinate_app, monkeypatch, tmp_path):
    """If the local file is gone (manual deletion / disk corruption),
    mark failed rather than blowing up the worker."""
    from app.repositories import videos as vid_repo
    from app.tasks.writer_understanding import extract_understanding_task

    missing = tmp_path / "gone.mp4"
    vid = vid_repo.create(session, "gone.mp4", str(missing))
    result = asyncio.run(extract_understanding_task(video_id=vid))
    assert "failed" in result.lower() or "missing" in result.lower()
    row = vid_repo.get(session, vid)
    assert row["understanding_status"] == "failed"
```

- [ ] **Step 6: Run test — expect failure**

Run: `uv run pytest tests/test_writer_understanding_task.py -v`
Expected: `ModuleNotFoundError: No module named 'app.tasks.writer_understanding'`.

- [ ] **Step 7: Implement the task**

Path: `app/tasks/writer_understanding.py`

```python
"""Procrastinate task: extract VideoFacts for a single video.

Deferred by the upload endpoint immediately after a video row is
created (Task 5 of this plan) and by the manual refresh endpoint
(Task 6). The task is idempotent — running it again overwrites the
prior understanding with a fresh one.
"""
import logging
import os

from app.db import session_scope
from app.repositories import videos as vid_repo
from app.tasks import procrastinate_app
from app.writer import understanding

log = logging.getLogger(__name__)


@procrastinate_app.task(queue="default", name="extract_understanding")
async def extract_understanding_task(video_id: int) -> str:
    with session_scope() as session:
        row = vid_repo.get(session, video_id)
        if row is None:
            return f"skipped: video {video_id} not found"

        path = row.get("stored_path")
        if not path or not os.path.exists(path):
            log.warning("extract_understanding: file missing for video %s (%s)",
                        video_id, path)
            vid_repo.set_understanding(session, video_id,
                                        facts=None, status="failed")
            return f"failed: file missing for video {video_id}"

        try:
            facts = understanding.extract_video_facts(path)
        except Exception as e:
            log.warning("extract_understanding: Gemini failed for video %s: %s",
                        video_id, e)
            vid_repo.set_understanding(session, video_id,
                                        facts=None, status="failed")
            return f"failed: {e!s}"

        vid_repo.set_understanding(session, video_id,
                                    facts=facts.model_dump(),
                                    status="ok")
        return f"ok: video {video_id} understood"
```

- [ ] **Step 8: Register the task module**

Open `app/tasks/__init__.py`. In the `import_paths` list (currently ends with `"app.tasks.sync_transactions"` around line 45), add `"app.tasks.writer_understanding"`:

```python
    import_paths=[
        "app.tasks.warmup",
        # ... (other entries unchanged) ...
        "app.tasks.sync_transactions",
        "app.tasks.writer_understanding",
    ],
```

- [ ] **Step 9: Run test — expect pass**

Run: `uv run pytest tests/test_writer_understanding_task.py -v`
Expected: 4 passed.

- [ ] **Step 10: Commit**

```bash
git add app/repositories/videos.py app/tasks/writer_understanding.py app/tasks/__init__.py tests/test_writer_understanding_task.py tests/test_repositories_videos.py
git commit -m "feat(writer): extract_understanding task + videos.set_understanding helper"
```

---

## Task 5: Defer understanding from /api/videos/upload

**Files:**
- Modify: `app/api/videos.py:249`
- Test: extend `tests/test_api.py` (or create `tests/test_api_videos_upload.py` if `test_api.py` is unwieldy)

- [ ] **Step 1: Write the failing test**

Choose `tests/test_api.py` if it has other `/api/videos/upload` tests; otherwise create `tests/test_api_videos_upload.py`. Append/insert:

```python
def test_video_upload_defers_understanding_task(
        client, monkeypatch, default_group_id, tmp_path):
    """Successful upload should defer one extract_understanding job
    per uploaded video. We snoop on procrastinate's defer_async."""
    from app.tasks import procrastinate_app

    deferred: list[dict] = []

    async def fake_defer(task_name=None, **kwargs):
        deferred.append({"task_name": task_name, "kwargs": kwargs})
        return 1

    # Monkeypatch the configure_task helper used by the endpoint. We
    # accept whatever indirection the upload route uses; verify by
    # observing that an extract_understanding job is queued exactly
    # once per uploaded file.
    monkeypatch.setattr(
        procrastinate_app, "configure_task",
        lambda name, **kw: type("T", (), {"defer_async": fake_defer})(),
    )

    f = tmp_path / "u.mp4"
    f.write_bytes(b"\x00")
    with open(f, "rb") as fp:
        r = client.post(
            "/api/videos/upload",
            data={"group_id": str(default_group_id), "platforms": "tiktok"},
            files=[("files", ("u.mp4", fp, "video/mp4"))],
        )
    assert r.status_code == 200

    extract_jobs = [d for d in deferred if d["task_name"] == "extract_understanding"]
    assert len(extract_jobs) == 1
    assert extract_jobs[0]["kwargs"].get("video_id")
```

- [ ] **Step 2: Run test — expect failure**

Run: `uv run pytest tests/test_api.py -v -k defers_understanding` (or the file you put it in)
Expected: `assert len(extract_jobs) == 1` fails with `0 == 1`.

- [ ] **Step 3: Modify the upload endpoint**

Open `app/api/videos.py`. After the existing block in `upload()` (around line 269 where `vid_repo.create` and `generate_thumbnail` finish), add a defer call inside the `try` block right after the thumbnail step:

```python
            rel, dur = generate_thumbnail(dest, video_id=vid)
            if rel is not None or dur is not None:
                vid_repo.update_media_meta(session, vid,
                                           thumbnail_path=rel,
                                           duration_seconds=dur)
            # Layer 1 of writer factory — queue understanding extraction.
            # Best-effort: a failure to defer must NOT roll back the upload,
            # because the manual /api/writer/understanding/{vid}/refresh
            # endpoint can recover from a missed defer later.
            try:
                await procrastinate_app.configure_task(
                    name="extract_understanding",
                ).defer_async(video_id=vid)
            except Exception as e:
                log.warning("upload: failed to defer understanding for %s: %s",
                            vid, e)
```

If `procrastinate_app` is not yet imported at the top of `app/api/videos.py`, add:

```python
from app.tasks import procrastinate_app
import logging
log = logging.getLogger(__name__)
```

(Skip the `log` line if a logger is already defined in this file.)

- [ ] **Step 4: Run test — expect pass**

Run: `uv run pytest tests/test_api.py -v -k defers_understanding`
Expected: 1 passed.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: previous count + 1 new test, all passing.

- [ ] **Step 6: Commit**

```bash
git add app/api/videos.py tests/test_api.py
git commit -m "feat(writer): defer extract_understanding from /api/videos/upload"
```

---

## Task 6: Manual refresh endpoint

**Files:**
- Create: `app/api/writer.py`
- Modify: `app/main.py`
- Test: `tests/test_writer_api.py`

- [ ] **Step 1: Write the failing test**

Path: `tests/test_writer_api.py`

```python
"""Smoke tests for POST /api/writer/understanding/{vid}/refresh."""
import pytest


def test_refresh_endpoint_requires_auth(client_no_auth, tmp_path):
    """Without a session cookie the endpoint must 401."""
    r = client_no_auth.post("/api/writer/understanding/1/refresh")
    assert r.status_code == 401


def test_refresh_endpoint_404_for_missing_video(client):
    r = client.post("/api/writer/understanding/99999/refresh")
    assert r.status_code == 404


def test_refresh_endpoint_defers_extract_task(client, monkeypatch,
                                               default_group_id, tmp_path):
    from app.repositories import videos as vid_repo
    from app.db import session_scope
    from app.tasks import procrastinate_app

    f = tmp_path / "v.mp4"; f.write_bytes(b"\x00")
    with session_scope() as s:
        vid = vid_repo.create(s, "v.mp4", str(f), group_id=default_group_id)

    deferred: list[dict] = []

    async def fake_defer(**kwargs):
        deferred.append(kwargs)
        return 1

    monkeypatch.setattr(
        procrastinate_app, "configure_task",
        lambda name, **kw: type("T", (), {"defer_async": fake_defer})(),
    )

    r = client.post(f"/api/writer/understanding/{vid}/refresh")
    assert r.status_code == 200
    assert r.json()["queued"] is True
    assert deferred == [{"video_id": vid}]
```

(If `client_no_auth` fixture doesn't exist in `conftest.py`, this test
can be removed and a comment added noting that auth is enforced by the
existing middleware test elsewhere. Check conftest before deleting.)

- [ ] **Step 2: Run test — expect failure**

Run: `uv run pytest tests/test_writer_api.py -v`
Expected: 404 on the route — the router doesn't exist yet.

- [ ] **Step 3: Implement the router**

Path: `app/api/writer.py`

```python
"""Operator-facing endpoints for the Writer Factory.

Phase 1: one endpoint — manually re-trigger understanding extraction
for a specific video. Phase 5 will add the read-only /writer-lab API
that backs the dashboard UI.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db import get_session
from app.repositories import videos as vid_repo
from app.tasks import procrastinate_app

router = APIRouter(prefix="/api/writer")
log = logging.getLogger(__name__)


@router.post("/understanding/{vid}/refresh")
async def refresh_understanding(vid: int,
                                 session: Session = Depends(get_session)):
    row = vid_repo.get(session, vid)
    if row is None:
        raise HTTPException(status_code=404, detail="video not found")
    try:
        await procrastinate_app.configure_task(
            name="extract_understanding",
        ).defer_async(video_id=vid)
    except Exception as e:
        log.warning("refresh_understanding: defer failed for %s: %s", vid, e)
        raise HTTPException(status_code=502,
                            detail="failed to queue understanding job")
    return {"queued": True, "video_id": vid}
```

- [ ] **Step 4: Mount the router**

Open `app/main.py`. Find the block where other API routers are included (likely calls like `app.include_router(videos.router)`). Add:

```python
from app.api import writer as writer_api
# ...
app.include_router(writer_api.router)
```

- [ ] **Step 5: Run test — expect pass**

Run: `uv run pytest tests/test_writer_api.py -v`
Expected: tests pass.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: previous total + this task's new tests, all passing.

- [ ] **Step 7: Commit**

```bash
git add app/api/writer.py app/main.py tests/test_writer_api.py
git commit -m "feat(writer): POST /api/writer/understanding/{vid}/refresh"
```

---

## Task 7: Local end-to-end smoke + deploy to prod

**Files:** none new — this task verifies the system and ships it.

- [ ] **Step 1: Confirm pytest is green end-to-end**

Run: `uv run pytest -q`
Expected: full suite passes. Note the count (will be the new baseline going forward).

- [ ] **Step 2: Rebuild frontend (no UI changes in Phase 1 but the bundle is shipped fresh as part of every deploy)**

Run:
```bash
export PATH="/opt/homebrew/bin:$PATH"
cd frontend && npm run build && cd ..
```
Expected: `built in <Xms>` and `dist/index.html` mtime updates.

- [ ] **Step 3: Live smoke against the local web tier**

Start the local web tier (`./run.sh`) and the local worker
(`uv run python -m procrastinate -a app.tasks.procrastinate_app worker --queues phones,default`) in two terminals.

Upload a real short video through the dashboard at http://localhost:8080/videos. Watch the worker terminal: it should log `writer/understanding: uploading <name>` followed by `writer/understanding: generating facts`.

Then query the DB:
```bash
docker exec -i <local-pg-container> psql -U mkt -d mkt -c \
  "SELECT id, understanding_status, jsonb_pretty(understanding) FROM videos ORDER BY id DESC LIMIT 1;"
```
(Or whatever local PG access pattern this dev box uses — `psql` directly if PG is on the host.)

Expected: `understanding_status='ok'` and a populated JSON with all `VideoFacts` fields.

If `understanding_status='failed'`, read the worker log to see whether
Gemini schema rejected the output or the Files API timed out, then
fix forward.

- [ ] **Step 4: Package the deploy tarball**

Run:
```bash
COPYFILE_DISABLE=1 tar -czf /tmp/mkt-deploy.tar.gz \
  --exclude='__pycache__' --exclude='*.pyc' \
  app alembic alembic.ini geelark_client.py generate_caption.py generate_comment.py \
  pyproject.toml uv.lock frontend/dist
ls -lh /tmp/mkt-deploy.tar.gz
```
Expected: tarball ~1.5-2 MB.

- [ ] **Step 5: Upload + apply on prod**

Run:
```bash
scp -i ~/.ssh/svm_server -o IdentitiesOnly=yes \
  /tmp/mkt-deploy.tar.gz root@139.196.52.92:/tmp/mkt-deploy.tar.gz

ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 '
  set -e
  export PATH="/root/.local/bin:$PATH"
  cd /opt/mkt
  tar -xzf /tmp/mkt-deploy.tar.gz
  rm -f frontend/dist/favicon.svg
  chown -R root:root app alembic alembic.ini frontend/dist geelark_client.py generate_caption.py generate_comment.py pyproject.toml uv.lock
  uv sync --no-dev
  uv run alembic upgrade head
  systemctl restart mkt mkt-worker
  sleep 5
  systemctl is-active mkt mkt-worker
  rm -f /tmp/mkt-deploy.tar.gz
'
```
Expected output ends with `active active`.

- [ ] **Step 6: Verify prod schema landed**

Run:
```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  "docker exec svm-postgres psql -U mkt -d mkt -c \"
   SELECT column_name FROM information_schema.columns
   WHERE table_name='videos' AND column_name LIKE 'understanding%';
   SELECT table_name FROM information_schema.tables WHERE table_name='writer_audit';\""
```
Expected: shows `understanding` + `understanding_status` columns and `writer_audit` table.

- [ ] **Step 7: Prod smoke**

Upload one test video through the dashboard at http://139.196.52.92:8080.

Wait ~60s, then:
```bash
ssh -i ~/.ssh/svm_server -o IdentitiesOnly=yes root@139.196.52.92 \
  "docker exec svm-postgres psql -U mkt -d mkt -c \"
   SELECT id, understanding_status, left(understanding::text, 200) AS preview
   FROM videos ORDER BY id DESC LIMIT 1;\""
```
Expected: `understanding_status='ok'` and a non-empty preview.

- [ ] **Step 8: No commit step — Task 7 is verification + deploy only.**

The deploy is the deliverable. Phase 1 done.

---

## Phase 1 Acceptance

- [ ] Every video uploaded via `/api/videos/upload` ends up with
      `understanding_status='ok'` and a populated `understanding` JSONB
      column within ~60s.
- [ ] Re-running `/api/writer/understanding/{vid}/refresh` for an
      existing video overwrites the understanding successfully.
- [ ] A video whose local file is missing transitions to
      `understanding_status='failed'` instead of crashing the worker.
- [ ] `writer_audit` table exists but is empty (Phase 4+ fills it).
- [ ] All pre-existing tests still pass; new test count = prior + ~13.
- [ ] Prod `mkt` and `mkt-worker` both `active` after deploy.

## What's NOT in Phase 1

(All deferred to later phases per spec section 12. Listed so reviewers
don't ask "why didn't you do X" — it's by design.)

- Layer 0 trend observatory cron — Phase 4
- Voice composition (Layer 2) — Phase 2
- Angle selection (Layer 3) — Phase 2
- Caption / comment generation (Layers 4-6) — Phases 2-3
- writer_audit population — Phase 3
- Cross-account dedup — Phase 4
- `/writer-lab` UI page — Phase 5
- Back-filling understanding for existing videos in prod DB —
  intentionally not in Phase 1 (spec section 14 OQ#5); refresh
  endpoint handles per-video on demand if needed before later phases.
