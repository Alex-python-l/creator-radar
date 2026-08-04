import subprocess
from pathlib import Path

import pytest

from app.services import thumbnail_service


FIXTURE = Path(__file__).parent / "fixtures" / "short.mp4"


def test_generate_thumbnail_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbnail_service, "THUMBNAIL_DIR", tmp_path)
    rel, dur = thumbnail_service.generate_thumbnail(FIXTURE, video_id=42)
    assert rel == "thumbnails/42.jpg"
    assert (tmp_path / "42.jpg").exists()
    assert (tmp_path / "42.jpg").stat().st_size > 500  # real JPEG
    assert dur == 3                                    # fixture is 3 seconds


def test_generate_thumbnail_ffmpeg_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbnail_service, "THUMBNAIL_DIR", tmp_path)

    def boom(*a, **kw):
        raise FileNotFoundError("ffmpeg not found")
    monkeypatch.setattr(subprocess, "run", boom)
    assert thumbnail_service.generate_thumbnail(FIXTURE, video_id=1) == (None, None)


def test_generate_thumbnail_ffmpeg_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbnail_service, "THUMBNAIL_DIR", tmp_path)

    def fail(*a, **kw):
        return subprocess.CompletedProcess(a, returncode=1, stdout=b"", stderr=b"boom")
    monkeypatch.setattr(subprocess, "run", fail)
    assert thumbnail_service.generate_thumbnail(FIXTURE, video_id=2) == (None, None)


def test_generate_thumbnail_missing_video(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbnail_service, "THUMBNAIL_DIR", tmp_path)
    # ffmpeg will return nonzero for a nonexistent input — no crash
    assert thumbnail_service.generate_thumbnail(
        tmp_path / "does-not-exist.mp4", video_id=3) == (None, None)
