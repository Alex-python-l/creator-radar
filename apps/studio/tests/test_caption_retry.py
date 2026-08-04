"""_generate_content_with_retry backs off on 429/5xx and gives up on
other errors. Covers the failure mode that wedged 19+ publications in
the 2026-06-10 Gemini overload window."""
from unittest.mock import MagicMock

import pytest
from google.genai import errors as genai_errors

import generate_caption


@pytest.fixture
def stub_local_video(tmp_path):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\x00" * 16)
    return str(p)


def _api_error(code: int, status: str) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"code": code, "status": status}})


def _client_with_caption_response(text="Hello world\n#one #two"):
    client = MagicMock()
    uploaded = MagicMock(name="hi-file", uri="files/abc", state=MagicMock(value="2"))
    client.files.upload.return_value = uploaded
    client.files.get.return_value = uploaded
    response = MagicMock()
    response.text = text
    client.models.generate_content.return_value = response
    return client


def test_retries_503_then_succeeds(monkeypatch, stub_local_video):
    monkeypatch.setattr(generate_caption.time, "sleep", lambda _s: None)
    client = _client_with_caption_response()
    final_response = client.models.generate_content.return_value
    client.models.generate_content.side_effect = [
        _api_error(503, "UNAVAILABLE"),
        _api_error(503, "UNAVAILABLE"),
        final_response,
    ]
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    caption = generate_caption.generate_caption_for_local_file(stub_local_video)

    assert caption.startswith("Hello world")
    assert client.models.generate_content.call_count == 3


def test_gives_up_after_max_retries(monkeypatch, stub_local_video):
    monkeypatch.setattr(generate_caption.time, "sleep", lambda _s: None)
    client = _client_with_caption_response()
    client.models.generate_content.side_effect = _api_error(503, "UNAVAILABLE")
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    with pytest.raises(genai_errors.APIError) as exc:
        generate_caption.generate_caption_for_local_file(stub_local_video)

    assert exc.value.code == 503
    expected_attempts = len(generate_caption.GENERATE_CONTENT_RETRY_DELAYS_S) + 1
    assert client.models.generate_content.call_count == expected_attempts


def test_does_not_retry_client_errors(monkeypatch, stub_local_video):
    monkeypatch.setattr(generate_caption.time, "sleep", lambda _s: None)
    client = _client_with_caption_response()
    client.models.generate_content.side_effect = _api_error(400, "INVALID_ARGUMENT")
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    with pytest.raises(genai_errors.APIError) as exc:
        generate_caption.generate_caption_for_local_file(stub_local_video)

    assert exc.value.code == 400
    assert client.models.generate_content.call_count == 1


def test_retries_504_deadline(monkeypatch, stub_local_video):
    monkeypatch.setattr(generate_caption.time, "sleep", lambda _s: None)
    client = _client_with_caption_response()
    final_response = client.models.generate_content.return_value
    client.models.generate_content.side_effect = [
        _api_error(504, "DEADLINE_EXCEEDED"),
        final_response,
    ]
    monkeypatch.setattr(generate_caption, "_get_client", lambda: client)

    caption = generate_caption.generate_caption_for_local_file(stub_local_video)

    assert caption.startswith("Hello world")
    assert client.models.generate_content.call_count == 2
