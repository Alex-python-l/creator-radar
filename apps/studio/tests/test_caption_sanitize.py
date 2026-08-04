"""Tests for generate_caption.sanitize_caption().

The sanitizer is the safety net for Gemini's output drift: even with a strict
prompt, the model occasionally returns preamble ("Here's a caption:..."),
Markdown formatting (**Title:** / **Caption:**), or multiple options. None of
those render on TikTok — they'd post as literal text. Sanitize fixes what's
fixable; if the model emitted multiple distinct caption options we'd rather
fail loudly than guess which one to publish.

The four real bad captions below are pulled from production (videos 7, 32,
41, 42). Keep them verbatim so regressions surface immediately.
"""
import pytest
import httpx

from generate_caption import sanitize_caption


# ── unit cases ────────────────────────────────────────────────────────────

def test_strips_markdown_bold():
    assert sanitize_caption("**Hello** world") == "Hello world"


def test_strips_title_and_caption_labels():
    raw = "**Title:** My IELTS win 🏆\n\n**Caption:** I did it! #IELTS"
    out = sanitize_caption(raw)
    assert "Title:" not in out
    assert "Caption:" not in out
    assert "My IELTS win 🏆" in out
    assert "I did it! #IELTS" in out


def test_removes_heres_preamble_line():
    raw = "Here's a TikTok title and caption for the video:\n\nMy real caption #IELTS"
    out = sanitize_caption(raw)
    assert out == "My real caption #IELTS"


def test_collapses_blank_lines():
    assert sanitize_caption("A\n\n\n\nB") == "A\nB"


def test_strips_outer_whitespace():
    assert sanitize_caption("   \n\nhello #IELTS   \n  ") == "hello #IELTS"


def test_preserves_already_clean_caption():
    # Production caption #9 — already valid, must not be mangled.
    clean = (
        "POV: My IELTS journey led to this luxury car upgrade! 🚗✨ 31 days of wins!\n\n"
        "#IELTS #IELTSJourney #StudyMotivation #LuxuryLife #Goals"
    )
    out = sanitize_caption(clean)
    # We allow blank-line collapsing, so compare semantically.
    assert "POV: My IELTS journey led to this luxury car upgrade! 🚗✨ 31 days of wins!" in out
    assert out.rstrip().endswith("#IELTS #IELTSJourney #StudyMotivation #LuxuryLife #Goals")


def test_raises_on_multiple_options():
    # If Gemini gives us 2+ distinct options we don't pick one silently.
    raw = (
        "**Option 1:**\n**Caption:** First version #IELTS\n\n"
        "**Option 2:**\n**Caption:** Second version #IELTS"
    )
    with pytest.raises(RuntimeError, match="multiple"):
        sanitize_caption(raw)


def test_caps_length_at_max_preserving_hashtag_tail():
    # A 200-char body + hashtag tail. Trim should keep the trailing hashtags
    # (they're the discoverability layer) and chop the body.
    body = "x" * 300
    raw = f"{body}\n#IELTS #ieltstips"
    out = sanitize_caption(raw)
    assert len(out) <= 220
    assert out.endswith("#IELTS #ieltstips")


# ── real production failures (verbatim) ───────────────────────────────────

def test_real_video_7_strips_markdown_and_fits():
    # Originally 212 chars with literal **Title:** / **Caption:** markers.
    raw = (
        "**Title:** My IELTS motivation! 🌊\n\n"
        "**Caption:** No more 9-5 after I ace my IELTS! This view is my ultimate reward. "
        "Manifesting this for all of us! ✨\n\n"
        "#IELTS #IELTSpiration #IELTSJourney #StudyMotivation #DreamBig"
    )
    out = sanitize_caption(raw)
    assert "**" not in out
    assert "Title:" not in out
    assert "Caption:" not in out
    assert len(out) <= 220
    assert "#IELTS" in out


def test_real_video_41_drops_preamble_and_markdown():
    # Originally 323 chars with "Here's a TikTok title..." preamble.
    raw = (
        "Here's a TikTok title and caption for the video:\n\n"
        "**Title:** POV: My IELTS speaking test results came out. 😭\n\n"
        "**Caption:** My IELTS journey has been a rollercoaster! This is me waiting "
        "for my results, knowing I messed up on one section. Can anyone relate? 😥 "
        "#IELTS #ieltspreparation #ieltstips #studymotivation #studyabroad"
    )
    out = sanitize_caption(raw)
    assert not out.lower().startswith("here")
    assert "**" not in out
    assert "Title:" not in out
    assert "Caption:" not in out
    assert len(out) <= 220


def test_real_video_42_rejects_multi_option_dump():
    # Originally 666 chars with three distinct "Option N" blocks. This is the
    # one case where silent sanitization would publish the wrong thing.
    raw = (
        "Here are a few options for the TikTok title and caption, keeping your requirements in mind:\n\n"
        "**Option 1:**\n\n"
        "**Title:** POV: You bet on yourself for IELTS!\n"
        "**Caption:** That feeling when your hard work for IELTS actually pays off! 🥹 So relieved. "
        "#IELTS #IELTSpayoff #studyabroad #studymotivation\n\n"
        "**Option 2:**\n\n"
        "**Title:** IELTS journey underdog moment!\n"
        "**Caption:** I can't believe I actually crushed my IELTS exam! All those late nights were worth it. 🙏 "
        "#IELTS #ieltstips #ieltsprep #victory\n\n"
        "**Option 3:**\n\n"
        "**Title:** My IELTS success story!\n"
        "**Caption:** This is me after seeing my IELTS results. Never doubt yourself! ✨ "
        "#IELTS #IELTSsuccess #studygram #motivation"
    )
    with pytest.raises(RuntimeError, match="multiple"):
        sanitize_caption(raw)


# ── integration: generate_caption_for_local_file applies the sanitizer ──

class _FakeFile:
    """Mimics the genai File object: name, uri, state."""
    def __init__(self):
        self.name = "files/fake-id"
        self.uri = "https://example.test/files/fake-id"
        self.state = "ACTIVE"


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeFilesAPI:
    def __init__(self):
        self.deleted = []
    def upload(self, *, file, config):  # noqa: ARG002
        return _FakeFile()
    def get(self, *, name, config=None):  # noqa: ARG002
        return _FakeFile()
    def delete(self, *, name):
        self.deleted.append(name)


class _FakeModelsAPI:
    def __init__(self, text):
        self._text = text
    def generate_content(self, *, model, contents, config):  # noqa: ARG002
        return _FakeResponse(self._text)


class _FakeGeminiClient:
    def __init__(self, text):
        self.files = _FakeFilesAPI()
        self.models = _FakeModelsAPI(text)


@pytest.fixture
def stub_local_video(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"\x00\x00")  # contents don't matter, file is uploaded raw
    return str(path)


def test_generate_caption_for_local_file_applies_sanitizer(monkeypatch, stub_local_video):
    """End-to-end: a markdown-laden Gemini response comes back sanitized."""
    raw = (
        "**Title:** My IELTS motivation! 🌊\n\n"
        "**Caption:** Studying so hard right now. #IELTS #ieltstips"
    )
    import generate_caption
    monkeypatch.setattr(generate_caption, "_get_client",
                        lambda: _FakeGeminiClient(raw))

    out = generate_caption.generate_caption_for_local_file(stub_local_video)
    assert "**" not in out
    assert "Title:" not in out
    assert "Caption:" not in out
    assert "My IELTS motivation! 🌊" in out
    assert "#IELTS #ieltstips" in out


def test_generate_caption_for_local_file_raises_on_multi_option(monkeypatch, stub_local_video):
    """If Gemini emits multiple options, the whole call fails — no silent pick."""
    raw = (
        "**Option 1:** First #IELTS\n\n"
        "**Option 2:** Second #IELTS"
    )
    import generate_caption
    monkeypatch.setattr(generate_caption, "_get_client",
                        lambda: _FakeGeminiClient(raw))

    with pytest.raises(RuntimeError, match="multiple"):
        generate_caption.generate_caption_for_local_file(stub_local_video)


def test_generate_caption_for_local_file_retries_transient_file_poll_disconnect(
        monkeypatch, stub_local_video):
    """A transient Gemini File API poll disconnect should not fail posting."""
    import generate_caption

    class _FilesWithOneDisconnect:
        def __init__(self):
            self.get_calls = 0

        def upload(self, *, file, config):  # noqa: ARG002
            return _FakeFile()

        def get(self, *, name, config=None):  # noqa: ARG002
            self.get_calls += 1
            if self.get_calls == 1:
                raise httpx.RemoteProtocolError(
                    "Server disconnected without sending a response.")
            return _FakeFile()

        def delete(self, *, name):  # noqa: ARG002
            pass

    fake = _FakeGeminiClient("Back to study mode #IELTS")
    fake.files = _FilesWithOneDisconnect()
    monkeypatch.setattr(generate_caption, "_get_client", lambda: fake)
    monkeypatch.setattr(generate_caption, "FILE_PROCESSING_POLL", 0)

    out = generate_caption.generate_caption_for_local_file(stub_local_video)

    assert out == "Back to study mode #IELTS"
    assert fake.files.get_calls == 2
