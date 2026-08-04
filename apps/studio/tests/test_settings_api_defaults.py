"""GET /api/settings should merge SETTINGS_DEFAULTS so that missing keys
still appear with their default value (lets the UI prefill)."""
import pytest
from sqlalchemy import text

from app import config as cfg
from app.config import SETTINGS_DEFAULTS


@pytest.fixture
def authed_client(client, monkeypatch):
    """TestClient with an authenticated session cookie."""
    r = client.post("/api/login", json={"username": "tester", "password": "pw"})
    assert r.status_code == 200
    return client


def test_get_returns_defaults_for_missing_keys(authed_client, session):
    session.execute(text("TRUNCATE TABLE settings"))
    session.commit()

    r = authed_client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    for k, v in SETTINGS_DEFAULTS.items():
        assert body[k] == v


def test_db_value_wins_over_default(authed_client, session):
    from app.repositories import settings as srepo
    srepo.update(session, {"gemini_high_quality": "1"})

    r = authed_client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["gemini_high_quality"] == "1"


def test_partial_settings_get_merged(authed_client, session):
    session.execute(text("TRUNCATE TABLE settings"))
    session.commit()
    from app.repositories import settings as srepo
    srepo.update(session, {"gemini_high_quality": "1"})  # only one key set

    r = authed_client.get("/api/settings")
    body = r.json()
    assert body["gemini_high_quality"] == "1"                     # from DB
    # Other defaults still surface from SETTINGS_DEFAULTS
    assert body["post_cooldown_hours"] == SETTINGS_DEFAULTS["post_cooldown_hours"]
    # caption_brief was removed when the prompt pool replaced the global brief
    assert "caption_brief" not in body


def test_legacy_warmup_times_setting_is_hidden(authed_client, session):
    from app.repositories import settings as srepo
    srepo.update(session, {"warmup_times": "09:00,14:00,20:00"})

    r = authed_client.get("/api/settings")

    assert r.status_code == 200
    assert "warmup_times" not in r.json()
