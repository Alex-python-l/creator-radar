"""Tests for the Gemini model picker:
- generate_caption.list_models() filtering and caching
- generate_caption_for_local_file(model=...) override
- posting_service._caption reads `gemini_model` from settings
- GET /api/settings/gemini-models endpoint
"""
import pytest


# ── fakes ─────────────────────────────────────────────────────────────────────

class _FakeModel:
    """Mimics google.genai.types.Model — only the fields we read."""
    def __init__(self, name, supported_actions=None):
        self.name = name
        self.display_name = name.split("/")[-1]
        self.supported_actions = (
            supported_actions if supported_actions is not None
            else ["generateContent"]
        )


class _FakeModelsList:
    def __init__(self, models, captured=None):
        self._models = models
        self._captured = captured
    def list(self):
        return iter(self._models)
    def generate_content(self, *, model, contents, config):  # noqa: ARG002
        if self._captured is not None:
            self._captured["model"] = model
        return _FakeResponse("hello world #IELTS #ieltstips")


class _FakeFile:
    def __init__(self):
        self.name = "files/fake-id"
        self.uri = "https://example.test/files/fake-id"
        self.state = "ACTIVE"


class _FakeFilesAPI:
    def upload(self, *, file, config): return _FakeFile()  # noqa: ARG002
    def get(self, *, name, config=None): return _FakeFile()  # noqa: ARG002
    def delete(self, *, name): pass  # noqa: ARG002


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeClient:
    def __init__(self, models, captured=None):
        self.files = _FakeFilesAPI()
        self.models = _FakeModelsList(models, captured=captured)


@pytest.fixture
def stub_local_video(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"\x00")
    return str(path)


# ── list_models() filtering ───────────────────────────────────────────────────

def test_list_models_keeps_flash_and_pro_drops_others(monkeypatch):
    fake = _FakeClient([
        _FakeModel("models/gemini-3.5-flash"),
        _FakeModel("models/gemini-3-flash-preview"),
        _FakeModel("models/gemini-3.1-flash-lite"),
        _FakeModel("models/gemini-3.1-pro-preview"),
        _FakeModel("models/gemini-2.5-pro"),
        _FakeModel("models/gemini-2.5-flash"),
        _FakeModel("models/gemini-2.5-flash-lite"),
        # droppers
        _FakeModel("models/gemini-embedding-001"),                # embed
        _FakeModel("models/gemini-2.5-flash-tts"),                # tts
        _FakeModel("models/gemini-2.5-flash-image-preview"),      # image gen
        _FakeModel("models/gemini-3.1-flash-live-preview"),       # live streaming
        _FakeModel("models/imagen-3"),                            # not gemini
        _FakeModel("models/gemini-foo-bar",                       # no generateContent
                   supported_actions=["embedContent"]),
    ])
    import generate_caption
    monkeypatch.setattr(generate_caption, "_get_client", lambda: fake)

    rows = generate_caption.list_models()
    ids = [r["id"] for r in rows]

    for keeper in ["gemini-3.5-flash", "gemini-3-flash-preview",
                   "gemini-3.1-flash-lite", "gemini-3.1-pro-preview",
                   "gemini-2.5-pro", "gemini-2.5-flash",
                   "gemini-2.5-flash-lite"]:
        assert keeper in ids, f"expected keeper {keeper} to be present"

    for dropper in ["gemini-embedding-001", "gemini-2.5-flash-tts",
                    "gemini-2.5-flash-image-preview",
                    "gemini-3.1-flash-live-preview", "imagen-3",
                    "gemini-foo-bar"]:
        assert dropper not in ids, f"expected {dropper} to be filtered out"


def test_list_models_groups_ga_before_preview(monkeypatch):
    fake = _FakeClient([
        _FakeModel("models/gemini-3-flash-preview"),
        _FakeModel("models/gemini-3.5-flash"),
        _FakeModel("models/gemini-3.1-pro-preview"),
        _FakeModel("models/gemini-2.5-pro"),
    ])
    import generate_caption
    monkeypatch.setattr(generate_caption, "_get_client", lambda: fake)

    rows = generate_caption.list_models()
    statuses = [r["status"] for r in rows]
    # All GA first, all Preview second — never alternating.
    assert statuses == sorted(statuses, key=lambda s: 0 if s == "GA" else 1)


# ── model override in generate_caption_for_local_file ─────────────────────────

def test_model_kwarg_flows_to_generate_content(monkeypatch, stub_local_video):
    captured = {}
    fake = _FakeClient([], captured=captured)
    import generate_caption
    monkeypatch.setattr(generate_caption, "_get_client", lambda: fake)

    generate_caption.generate_caption_for_local_file(
        stub_local_video, model="gemini-x-test")
    assert captured["model"] == "gemini-x-test"


def test_model_defaults_to_constant_when_not_passed(monkeypatch, stub_local_video):
    captured = {}
    fake = _FakeClient([], captured=captured)
    import generate_caption
    monkeypatch.setattr(generate_caption, "_get_client", lambda: fake)

    generate_caption.generate_caption_for_local_file(stub_local_video)
    assert captured["model"] == generate_caption.GEMINI_MODEL


def test_blank_model_arg_falls_back_to_constant(monkeypatch, stub_local_video):
    """An empty string from settings shouldn't blank out the API call."""
    captured = {}
    fake = _FakeClient([], captured=captured)
    import generate_caption
    monkeypatch.setattr(generate_caption, "_get_client", lambda: fake)

    generate_caption.generate_caption_for_local_file(stub_local_video, model="  ")
    assert captured["model"] == generate_caption.GEMINI_MODEL


# ── posting_service._caption reads settings ───────────────────────────────────

def test_caption_service_reads_gemini_model_from_settings(
        db, monkeypatch, default_group_id):
    from app.services import posting_service
    from app.repositories import settings as srepo
    srepo.update(db, {"gemini_model": "gemini-from-db"})

    captured = {}
    def fake_gen(local_path, *, model=None, **_kw):  # noqa: ARG001
        captured["model"] = model
        return "cap #IELTS"
    import generate_caption
    monkeypatch.setattr(generate_caption,
                        "generate_caption_for_local_file", fake_gen)

    out = posting_service._caption(
        db, "/tmp/whatever.mp4", {"group_id": default_group_id})
    assert out == "cap #IELTS"
    assert captured["model"] == "gemini-from-db"


def test_caption_service_passes_none_when_setting_unset(
        db, monkeypatch, default_group_id):
    from app.services import posting_service

    captured = {}
    def fake_gen(local_path, *, model=None, **_kw):  # noqa: ARG001
        captured["model"] = model
        return "cap"
    import generate_caption
    monkeypatch.setattr(generate_caption,
                        "generate_caption_for_local_file", fake_gen)

    posting_service._caption(
        db, "/tmp/whatever.mp4", {"group_id": default_group_id})
    assert captured["model"] is None


def test_caption_service_blank_setting_treated_as_unset(
        db, monkeypatch, default_group_id):
    from app.services import posting_service
    from app.repositories import settings as srepo
    srepo.update(db, {"gemini_model": "   "})

    captured = {}
    def fake_gen(local_path, *, model=None, **_kw):  # noqa: ARG001
        captured["model"] = model
        return "cap"
    import generate_caption
    monkeypatch.setattr(generate_caption,
                        "generate_caption_for_local_file", fake_gen)

    posting_service._caption(
        db, "/tmp/whatever.mp4", {"group_id": default_group_id})
    assert captured["model"] is None


def test_comment_service_reads_gemini_model_from_settings(db, monkeypatch):
    from app.repositories import settings as srepo
    from app.services import engagement_service

    srepo.update(db, {"gemini_model": "gemini-comments"})
    monkeypatch.setattr(
        engagement_service.tiktok_metadata,
        "fetch_tiktok_metadata",
        lambda _url: {"title": "Target title", "description": None},
    )
    monkeypatch.setattr(
        engagement_service.eng_repo,
        "set_target_context",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        engagement_service.eng_repo,
        "set_generated_comment",
        lambda *_args, **_kwargs: None,
    )

    captured = {}

    def fake_generate(*, model=None, **_kwargs):
        captured["model"] = model
        return "comment"

    monkeypatch.setattr(
        engagement_service.comment_generation,
        "generate_comment_for_target",
        fake_generate,
    )

    out = engagement_service._prepare_studio_comment(
        db,
        {"id": 1, "target_link": "https://example.test/video"},
        {"comment_prompt_id": None, "comment_brief": "brief"},
    )
    assert out == "comment"
    assert captured["model"] == "gemini-comments"


def test_writer_understanding_task_reads_gemini_model_from_settings(
        db, monkeypatch, tmp_path):
    from app.repositories import settings as srepo
    from app.repositories import videos as vid_repo
    from app.tasks import writer_understanding

    srepo.update(db, {"gemini_model": "gemini-writer"})
    path = tmp_path / "writer.mp4"
    path.write_bytes(b"x")
    vid = vid_repo.create(db, "writer.mp4", str(path))

    captured = {}

    class FakeFacts:
        def model_dump(self):
            return {"summary": "ok"}

    def fake_extract(local_path, *, model=None):
        captured["path"] = local_path
        captured["model"] = model
        return FakeFacts()

    monkeypatch.setattr(
        writer_understanding.understanding,
        "extract_video_facts",
        fake_extract,
    )

    assert writer_understanding._run_sync(vid) == f"ok: video {vid} understood"
    assert captured == {"path": str(path), "model": "gemini-writer"}


# ── GET /api/settings/gemini-models endpoint ──────────────────────────────────

@pytest.fixture
def api_client(tmp_path, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "api.db")
    up = tmp_path / "uploads"; up.mkdir()
    monkeypatch.setattr(cfg, "UPLOAD_DIR", up)
    from app.main import create_app
    from fastapi.testclient import TestClient
    c = TestClient(create_app())
    c.post("/api/login", json={"username": "tester", "password": "pw"})
    return c


def test_endpoint_returns_filtered_list(api_client, monkeypatch):
    import generate_caption
    monkeypatch.setattr(generate_caption, "list_models", lambda: [
        {"id": "gemini-3.5-flash", "display_name": "Gemini 3.5 Flash", "status": "GA"},
        {"id": "gemini-3-flash-preview", "display_name": "Gemini 3 Flash Preview", "status": "Preview"},
    ])
    r = api_client.get("/api/settings/gemini-models")
    assert r.status_code == 200
    rows = r.json()
    assert [m["id"] for m in rows] == ["gemini-3.5-flash", "gemini-3-flash-preview"]


def test_endpoint_502s_on_upstream_failure(api_client, monkeypatch):
    import generate_caption
    def boom():
        raise RuntimeError("upstream down")
    monkeypatch.setattr(generate_caption, "list_models", boom)
    r = api_client.get("/api/settings/gemini-models")
    assert r.status_code == 502
    assert "upstream down" in r.json()["detail"]
