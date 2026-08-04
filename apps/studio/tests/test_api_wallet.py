"""/api/wallet endpoint: in-process 60s cache + graceful upstream failure."""
import pytest

from app.api import wallet as wallet_api


@pytest.fixture(autouse=True)
def _reset_cache():
    wallet_api._cache.clear()
    yield


def test_wallet_requires_auth(client):
    assert client.get("/api/wallet").status_code == 401


def test_wallet_caches_within_ttl(client, monkeypatch):
    calls = {"n": 0}

    class _Fake:
        def get_wallet(self):
            calls["n"] += 1
            return {"balance": 12.34, "giftMoney": 0, "availableTimeAddOn": 5}

    monkeypatch.setattr(wallet_api, "make_client", lambda: _Fake())
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})

    r1 = client.get("/api/wallet")
    r2 = client.get("/api/wallet")
    assert r1.status_code == 200
    assert r1.json() == {"balance": 12.34, "giftMoney": 0, "availableTimeAddOn": 5}
    assert r2.json() == r1.json()
    assert calls["n"] == 1  # second call served from cache


def test_wallet_serves_stale_on_upstream_failure(client, monkeypatch):
    calls = {"n": 0}

    class _Fake:
        def __init__(self, fail):
            self.fail = fail
        def get_wallet(self):
            calls["n"] += 1
            if self.fail:
                raise RuntimeError("upstream 429")
            return {"balance": 99.0, "giftMoney": 0, "availableTimeAddOn": 0}

    state = {"fail": False}
    monkeypatch.setattr(wallet_api, "make_client", lambda: _Fake(state["fail"]))
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})

    fresh = client.get("/api/wallet").json()
    assert fresh["balance"] == 99.0

    # Expire cache, make upstream fail; endpoint must fall back to last good value.
    wallet_api._cache[1]["fetched_at"] = 0.0
    state["fail"] = True
    stale = client.get("/api/wallet").json()
    assert stale == fresh


def test_wallet_returns_error_payload_when_no_cache(client, monkeypatch):
    class _Fake:
        def get_wallet(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(wallet_api, "make_client", lambda: _Fake())
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    body = client.get("/api/wallet").json()
    assert "error" in body
