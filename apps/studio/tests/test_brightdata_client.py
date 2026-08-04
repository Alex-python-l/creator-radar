import json

import pytest
import requests
import responses

from brightdata_client import BrightDataClient


@pytest.fixture
def client():
    return BrightDataClient(token="test-token", dataset_id="gd_test")


@responses.activate
def test_trigger_keyword_search_builds_correct_request(client):
    responses.add(
        responses.POST,
        "https://api.brightdata.com/datasets/v3/trigger",
        json={"snapshot_id": "sd_test123"},
        status=200,
    )
    snap = client.trigger_keyword_search("ielts")
    assert snap == "sd_test123"
    call = responses.calls[0]
    assert call.request.headers["Authorization"] == "Bearer test-token"
    assert call.request.headers["Content-Type"] == "application/json"
    assert call.request.url.startswith(
        "https://api.brightdata.com/datasets/v3/trigger?"
    )
    assert "dataset_id=gd_test" in call.request.url
    body = json.loads(call.request.body)
    assert body == [{
        "url": "https://www.tiktok.com/search?q=ielts",
        "search_keyword": "ielts",
        "country": "",
        "keyword_type": "",
    }]


@responses.activate
def test_trigger_keyword_search_raises_on_error(client):
    responses.add(
        responses.POST,
        "https://api.brightdata.com/datasets/v3/trigger",
        json={"error": "bad input"},
        status=400,
    )
    with pytest.raises(Exception) as exc:
        client.trigger_keyword_search("ielts")
    assert "bad input" in str(exc.value).lower() or "400" in str(exc.value)


@responses.activate
def test_trigger_keyword_search_url_encodes_keyword(client):
    responses.add(
        responses.POST,
        "https://api.brightdata.com/datasets/v3/trigger",
        json={"snapshot_id": "sd_x"},
        status=200,
    )
    client.trigger_keyword_search("ielts speaking")
    body = json.loads(responses.calls[0].request.body)
    # URL must be percent-encoded; search_keyword keeps the raw string
    assert body[0]["url"] == "https://www.tiktok.com/search?q=ielts+speaking"
    assert body[0]["search_keyword"] == "ielts speaking"


@responses.activate
def test_trigger_keyword_searches_batches_inputs(client):
    responses.add(
        responses.POST,
        "https://api.brightdata.com/datasets/v3/trigger",
        json={"snapshot_id": "sd_batch_1"},
        status=200,
    )
    snap = client.trigger_keyword_searches(["ielts", "ieltsspeaking", "toefl"])
    assert snap == "sd_batch_1"
    body = json.loads(responses.calls[0].request.body)
    assert isinstance(body, list)
    assert len(body) == 3
    assert {entry["search_keyword"] for entry in body} == {
        "ielts", "ieltsspeaking", "toefl",
    }
    for entry in body:
        assert entry["url"].startswith("https://www.tiktok.com/search?q=")
        assert entry["country"] == ""
        assert entry["keyword_type"] == ""


def test_trigger_keyword_searches_rejects_empty(client):
    import pytest as _pytest
    with _pytest.raises(Exception):
        client.trigger_keyword_searches([])


@responses.activate
def test_get_progress_returns_status(client):
    responses.add(
        responses.GET,
        "https://api.brightdata.com/datasets/v3/progress/sd_x",
        json={"status": "running", "snapshot_id": "sd_x"},
        status=200,
    )
    assert client.get_progress("sd_x") == "running"


@responses.activate
def test_get_progress_raises_on_failed_lookup(client):
    responses.add(
        responses.GET,
        "https://api.brightdata.com/datasets/v3/progress/sd_x",
        json={"error": "snapshot not found"},
        status=404,
    )
    with pytest.raises(Exception):
        client.get_progress("sd_x")


@responses.activate
def test_fetch_snapshot_returns_records(client):
    # parts flow: part=1 first, then /parts count (1), no further parts
    responses.add(
        responses.GET,
        "https://api.brightdata.com/datasets/v3/snapshot/sd_x",
        body='{"post_id": "1"}\n{"post_id": "2"}',
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.brightdata.com/datasets/v3/snapshot/sd_x/parts",
        json={"parts": 1},
        status=200,
    )
    rows = client.fetch_snapshot("sd_x")
    assert rows == [{"post_id": "1"}, {"post_id": "2"}]


@responses.activate
def test_fetch_snapshot_combines_multiple_parts(client):
    responses.add(
        responses.GET,
        "https://api.brightdata.com/datasets/v3/snapshot/sd_x",
        body='{"post_id": "1"}',
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.brightdata.com/datasets/v3/snapshot/sd_x/parts",
        json={"parts": 2},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.brightdata.com/datasets/v3/snapshot/sd_x",
        body='{"post_id": "2"}\n{"post_id": "3"}',
        status=200,
    )
    rows = client.fetch_snapshot("sd_x")
    assert rows == [{"post_id": "1"}, {"post_id": "2"}, {"post_id": "3"}]


@responses.activate
def test_fetch_snapshot_raises_not_ready_when_still_building(client):
    from brightdata_client import BrightDataNotReady
    responses.add(
        responses.GET,
        "https://api.brightdata.com/datasets/v3/snapshot/sd_x",
        json={"status": "building", "message": "Dataset not ready"},
        status=200,
    )
    import pytest as _pytest
    with _pytest.raises(BrightDataNotReady):
        client.fetch_snapshot("sd_x")


def test_fetch_snapshot_retries_on_chunked_encoding_error(client, monkeypatch):
    """BD's CDN intermittently truncates downloads (urllib3 IncompleteRead
    bubbles up as ChunkedEncodingError). Part fetches must retry rather
    than fail the whole batch."""
    part_calls = []

    class FakeResp:
        status_code = 200

        def __init__(self, body, *, raise_on_content=False):
            self._body = body
            self._raise = raise_on_content

        @property
        def content(self):
            if self._raise:
                raise requests.exceptions.ChunkedEncodingError("broken")
            return self._body

        @property
        def text(self):
            return ""

        def json(self):
            return json.loads(self._body)

    def fake_get(url, **kw):
        if url.endswith("/parts"):
            return FakeResp(b'{"parts": 1}')
        part_calls.append(url)
        if len(part_calls) < 3:
            return FakeResp(b"", raise_on_content=True)
        return FakeResp(b'{"post_id":"1"}')

    monkeypatch.setattr("brightdata_client.requests.get", fake_get)
    monkeypatch.setattr("brightdata_client.time.sleep", lambda s: None)
    rows = client.fetch_snapshot("sd_x")
    assert rows == [{"post_id": "1"}]
    assert len(part_calls) == 3


def test_poll_until_ready_retries_when_snapshot_not_yet_built(client, monkeypatch):
    from brightdata_client import BrightDataNotReady
    monkeypatch.setattr(client, "get_progress", lambda s: "ready")
    fetches = []
    def fake_fetch(snap):
        fetches.append(snap)
        if len(fetches) < 3:
            raise BrightDataNotReady("still building")
        return [{"post_id": "1"}]
    monkeypatch.setattr(client, "fetch_snapshot", fake_fetch)
    sleeps = []
    monkeypatch.setattr("brightdata_client.time.sleep",
                       lambda s: sleeps.append(s))
    rows = client.poll_until_ready("sd_x", interval_seconds=5,
                                   max_wait_seconds=600)
    assert rows == [{"post_id": "1"}]
    assert len(fetches) == 3
    assert sleeps == [5, 5]  # slept twice between the not-ready retries


def test_poll_until_ready_returns_records(client, monkeypatch):
    calls = []
    def fake_progress(snap):
        calls.append(snap)
        return "ready" if len(calls) >= 2 else "running"
    def fake_fetch(snap):
        return [{"post_id": "1"}]
    sleeps = []
    monkeypatch.setattr(client, "get_progress", fake_progress)
    monkeypatch.setattr(client, "fetch_snapshot", fake_fetch)
    monkeypatch.setattr("brightdata_client.time.sleep",
                       lambda s: sleeps.append(s))
    rows = client.poll_until_ready("sd_x", interval_seconds=20,
                                   max_wait_seconds=600)
    assert rows == [{"post_id": "1"}]
    assert sleeps == [20]  # slept once between running -> ready


def test_poll_until_ready_times_out(client, monkeypatch):
    monkeypatch.setattr(client, "get_progress", lambda s: "running")
    monkeypatch.setattr("brightdata_client.time.sleep", lambda s: None)
    monkeypatch.setattr("brightdata_client.time.monotonic",
                       _make_clock([0, 1, 200, 400, 700]))
    with pytest.raises(Exception) as exc:
        client.poll_until_ready("sd_x", interval_seconds=20,
                                max_wait_seconds=600)
    assert "timeout" in str(exc.value).lower()


def test_poll_until_ready_raises_on_failed_status(client, monkeypatch):
    monkeypatch.setattr(client, "get_progress", lambda s: "failed")
    monkeypatch.setattr("brightdata_client.time.sleep", lambda s: None)
    with pytest.raises(Exception) as exc:
        client.poll_until_ready("sd_x")
    assert "failed" in str(exc.value).lower()


def test_parse_concatenated_json_handles_embedded_newlines():
    """BD jsonl rows embed raw newlines inside description strings —
    line-based parsing breaks; the decoder walk must not."""
    from brightdata_client import _parse_concatenated_json
    text = '{"post_id": "1", "description": "line one\nline two"}\n{"post_id": "2"}'
    # Note: the \n inside description above is a REAL newline in the string
    rows = _parse_concatenated_json(text)
    assert [r["post_id"] for r in rows] == ["1", "2"]
    assert "line one" in rows[0]["description"]


def _make_clock(values):
    it = iter(values)
    return lambda: next(it)
