"""CommentSource strategy class tests.

The three modes (geelark_ai / studio_ai / provided) used to live as
hard-coded branches inside engagement_service. They're now thin
strategy objects in app/services/comment_sources.py; these tests pin
the contract each one exposes so a new mode plugged in tomorrow lands
under the same assertions.
"""
import pytest

from app.services import comment_sources


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_registry_has_three_modes():
    assert sorted(comment_sources.all_modes()) == [
        "geelark_ai", "provided", "studio_ai",
    ]


def test_get_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown comment_mode"):
        comment_sources.get("not-a-real-mode")


def test_capability_flags():
    geelark = comment_sources.get("geelark_ai")
    studio = comment_sources.get("studio_ai")
    provided = comment_sources.get("provided")

    # GeeLark's own AI is the only mode that does NOT supply explicit
    # comment text; the others both feed an explicit string into the
    # GeeLark task.
    assert geelark.supplies_comment_text is False
    assert studio.supplies_comment_text is True
    assert provided.supplies_comment_text is True

    # studio_ai is the only mode that needs a comment_prompt_id at
    # batch creation; the others can run from a freeform brief alone.
    assert geelark.requires_prompt is False
    assert studio.requires_prompt is True
    assert provided.requires_prompt is False


# ---------------------------------------------------------------------------
# GeelarkAISource
# ---------------------------------------------------------------------------
def test_geelark_ai_tiktok_returns_none():
    """GeeLark's own AI composes the comment inline — send None so
    create_tiktok_ai_comment_task picks GeeLark-AI behaviour."""
    source = comment_sources.get("geelark_ai")
    text = source.prepare_tiktok_comment_text(None, {}, {})
    assert text is None


def test_geelark_ai_facebook_raises():
    """GeeLark's AI mode is not supported for Facebook engagement;
    the source must surface this explicitly rather than silently
    sending an empty payload."""
    source = comment_sources.get("geelark_ai")
    with pytest.raises(RuntimeError, match="not supported for Facebook"):
        source.prepare_facebook_candidates(None, {}, {})


# ---------------------------------------------------------------------------
# ProvidedSource
# ---------------------------------------------------------------------------
def test_provided_tiktok_returns_run_comment_text():
    source = comment_sources.get("provided")
    text = source.prepare_tiktok_comment_text(
        None, {"comment_text": "  hello world  "}, {},
    )
    assert text == "hello world"


def test_provided_tiktok_raises_when_text_missing():
    source = comment_sources.get("provided")
    for run in ({}, {"comment_text": ""}, {"comment_text": "   "}):
        with pytest.raises(RuntimeError, match="provided comment text"):
            source.prepare_tiktok_comment_text(None, run, {})


def test_provided_facebook_returns_run_candidates():
    source = comment_sources.get("provided")
    candidates = source.prepare_facebook_candidates(
        None,
        {"id": 1, "comment_candidates": ["a", "b", "c"]},
        {},
    )
    assert candidates == ["a", "b", "c"]


def test_provided_facebook_raises_when_candidates_missing():
    source = comment_sources.get("provided")
    for run in ({"id": 1}, {"id": 1, "comment_candidates": []}):
        with pytest.raises(RuntimeError, match="no comment_candidates"):
            source.prepare_facebook_candidates(None, run, {})


# ---------------------------------------------------------------------------
# StudioAISource — delegates to engagement_service helpers
# ---------------------------------------------------------------------------
def test_studio_ai_tiktok_delegates_to_prepare_studio_comment(monkeypatch):
    """The source class stays thin: it calls the existing
    _prepare_studio_comment helper that knows how to wire Gemini +
    metadata lookup."""
    calls: list[tuple] = []

    def fake(session, run, parent):
        calls.append((session, run["id"], parent.get("id")))
        return "studio comment for #1"

    from app.services import engagement_service as svc
    monkeypatch.setattr(svc, "_prepare_studio_comment", fake)

    source = comment_sources.get("studio_ai")
    text = source.prepare_tiktok_comment_text(
        "fake-session", {"id": 1}, {"id": 9},
    )
    assert text == "studio comment for #1"
    assert calls == [("fake-session", 1, 9)]


def test_studio_ai_facebook_delegates_to_generate_fb_candidates(monkeypatch):
    calls: list[tuple] = []

    def fake(session, run, parent):
        calls.append((session, run["id"], parent.get("id")))
        return ["cand-a", "cand-b"]

    from app.services import engagement_service as svc
    monkeypatch.setattr(svc, "_generate_fb_studio_candidates", fake)

    source = comment_sources.get("studio_ai")
    candidates = source.prepare_facebook_candidates(
        "fake-session", {"id": 7}, {"id": 9},
    )
    assert candidates == ["cand-a", "cand-b"]
    assert calls == [("fake-session", 7, 9)]
