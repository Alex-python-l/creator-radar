"""generate_caption_for_local_file passes brief/max_len/thinking_budget
through to the Gemini client correctly."""
import os
import pytest
from unittest.mock import MagicMock, patch

import generate_caption


@pytest.fixture
def stub_local_video(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\x00" * 16)
    return str(p)


def _mock_client():
    """Build a mocked genai client that simulates a successful caption gen."""
    client = MagicMock()
    uploaded = MagicMock(name="hi-file", uri="files/abc", state=MagicMock(value="2"))
    client.files.upload.return_value = uploaded
    client.files.get.return_value = uploaded
    response = MagicMock()
    response.text = "Hello world\n#one #two #three"
    client.models.generate_content.return_value = response
    return client


def test_thinking_budget_defaults_to_zero(monkeypatch, stub_local_video):
    client = _mock_client()
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    generate_caption.generate_caption_for_local_file(stub_local_video)

    call = client.models.generate_content.call_args
    assert call.kwargs["config"]["thinking_config"]["thinking_budget"] == 0


def test_thinking_budget_can_be_overridden(monkeypatch, stub_local_video):
    client = _mock_client()
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    generate_caption.generate_caption_for_local_file(
        stub_local_video, thinking_budget=4096
    )

    call = client.models.generate_content.call_args
    assert call.kwargs["config"]["thinking_config"]["thinking_budget"] == 4096


def test_brief_reaches_prompt(monkeypatch, stub_local_video):
    client = _mock_client()
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    generate_caption.generate_caption_for_local_file(
        stub_local_video, brief="Tokyo travel vlog account."
    )

    call = client.models.generate_content.call_args
    parts = call.kwargs["contents"][0]["parts"]
    text_part = next(p for p in parts if "text" in p)
    assert "Tokyo travel vlog account." in text_part["text"]


def test_max_len_reaches_prompt(monkeypatch, stub_local_video):
    client = _mock_client()
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    generate_caption.generate_caption_for_local_file(
        stub_local_video, max_len=180
    )

    call = client.models.generate_content.call_args
    parts = call.kwargs["contents"][0]["parts"]
    text_part = next(p for p in parts if "text" in p)
    assert "180" in text_part["text"]
