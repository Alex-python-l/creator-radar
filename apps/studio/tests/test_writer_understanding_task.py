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
