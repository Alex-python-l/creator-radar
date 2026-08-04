"""Tests for app.writer.understanding.extract_video_facts.

Uses a fake Gemini client that mirrors the upload → poll → generate
flow already used by generate_caption.py. No real Gemini call here.
"""
import json
import pytest
from pydantic import ValidationError

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

    with pytest.raises(ValidationError):
        understanding.extract_video_facts(stub_video)


def test_extract_video_facts_cleans_up_gemini_file(monkeypatch, stub_video):
    """Even when extraction succeeds the temp file uploaded to Gemini
    must be deleted on exit (we pay storage for files we leave behind)."""
    from app.writer import understanding
    client = _FakeGeminiClient(json.dumps(VALID_PAYLOAD))
    monkeypatch.setattr(understanding, "_get_client", lambda: client)

    understanding.extract_video_facts(stub_video)

    assert client.files.deleted == ["files/fake-id"]


def test_extract_video_facts_handles_trailing_text(monkeypatch, stub_video):
    """Gemini sometimes emits trailing tokens or a markdown fence after
    the JSON object. The parser must take the first object and discard
    the rest, not crash with `Extra data`."""
    from app.writer import understanding
    fenced = "```json\n" + json.dumps(VALID_PAYLOAD) + "\n```\n\noh hi"
    monkeypatch.setattr(understanding, "_get_client",
                        lambda: _FakeGeminiClient(fenced))
    facts = understanding.extract_video_facts(stub_video)
    assert facts.climax_moment.timestamp_s == 4.2

    trailing = json.dumps(VALID_PAYLOAD) + "\n\n{\"second\": \"object\"}"
    monkeypatch.setattr(understanding, "_get_client",
                        lambda: _FakeGeminiClient(trailing))
    facts2 = understanding.extract_video_facts(stub_video)
    assert facts2.spoken_verbatim == ["wait whaaat"]


def test_extract_video_facts_cleans_up_on_gemini_failure(monkeypatch, stub_video):
    """Gemini file must be deleted even when generate_content raises."""
    from app.writer import understanding

    class _BoomModelsAPI:
        def generate_content(self, **kwargs):  # noqa: ARG002
            raise RuntimeError("boom")

    class _FailingGeminiClient:
        def __init__(self):
            self.files = _FakeFilesAPI()
            self.models = _BoomModelsAPI()

    client = _FailingGeminiClient()
    monkeypatch.setattr(understanding, "_get_client", lambda: client)

    with pytest.raises(RuntimeError, match="boom"):
        understanding.extract_video_facts(stub_video)

    assert client.files.deleted == ["files/fake-id"]
