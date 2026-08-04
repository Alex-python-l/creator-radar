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
