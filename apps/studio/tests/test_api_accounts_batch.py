import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GEELARK_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    from app.main import create_app
    with TestClient(create_app()) as c:
        r = c.post("/api/login", json={"username": "tester", "password": "pw"})
        assert r.status_code == 200
        yield c


def _make_group(client, name="g"):
    r = client.post("/api/groups", json={"name": name})
    assert r.status_code == 200
    return r.json()["id"]


def _make_account(client, profile_name, group_id, **extra):
    body = {"profile_name": profile_name, "group_id": group_id, **extra}
    r = client.post("/api/accounts", json=body)
    assert r.status_code == 200
    return r.json()["id"]


def test_accounts_mark_warmed_up_completes_selected_accounts(client):
    g = _make_group(client, "manual-warm")
    a = _make_account(client, "manual-a", g,
                      tiktok_logged_in=1,
                      warmup_count=3,
                      target_warmup_count=10)

    r = client.post("/api/accounts/mark-warmed-up",
                    json={"account_ids": [a]})

    assert r.status_code == 200
    assert r.json()["updated"] == 1
    rows = client.get("/api/accounts").json()
    row = next(x for x in rows if x["id"] == a)
    assert row["warmed_up"] == 1
    assert row["warmup_count"] == 10
    assert row["target_warmup_count"] == 10


def test_set_warmup_mode_updates_listed_accounts(client, db):
    from app.repositories import accounts as acc_repo
    a = acc_repo.create(db, {"profile_name": "mode-a"})
    b = acc_repo.create(db, {"profile_name": "mode-b"})
    resp = client.post("/api/accounts/warmup-mode",
                       json={"account_ids": [a, b], "mode": "daily"})
    assert resp.status_code == 200
    assert resp.json() == {"updated": 2}
    assert acc_repo.get(db, a)["warmup_mode"] == "daily"
    assert acc_repo.get(db, b)["warmup_mode"] == "daily"
    # Now flip back to count — both rows should be 'count' again
    resp2 = client.post("/api/accounts/warmup-mode",
                        json={"account_ids": [a, b], "mode": "count"})
    assert resp2.status_code == 200
    assert resp2.json() == {"updated": 2}
    assert acc_repo.get(db, a)["warmup_mode"] == "count"
    assert acc_repo.get(db, b)["warmup_mode"] == "count"


def test_set_warmup_mode_rejects_unknown_mode(client, db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name": "mode-bad"})
    resp = client.post("/api/accounts/warmup-mode",
                       json={"account_ids": [aid], "mode": "weekly"})
    assert resp.status_code == 400
    assert "mode" in resp.json()["detail"]


def test_set_warmup_mode_rejects_empty_ids(client):
    resp = client.post("/api/accounts/warmup-mode",
                       json={"account_ids": [], "mode": "daily"})
    assert resp.status_code == 400


def test_set_warmup_mode_rejects_missing_account(client):
    resp = client.post("/api/accounts/warmup-mode",
                       json={"account_ids": [999], "mode": "daily"})
    assert resp.status_code == 400


class _FakeGeelarkClient:
    """In-memory stand-in for GeeLarkClient that records every
    proxy_config push so the test can verify routing without dialing
    GeeLark."""
    def __init__(self):
        self.calls: list[dict] = []

    def update_phone_detail(self, env_id, *, name=None, proxy_config=None):
        self.calls.append({
            "env_id": env_id,
            "name": name,
            "proxy_config": proxy_config,
        })
        return {}


def test_replace_proxies_round_robin_and_syncs_geelark(client, db, monkeypatch):
    from app.services import geelark_service
    from app.repositories import accounts as acc_repo
    fake = _FakeGeelarkClient()
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    g = _make_group(client, "replace-rr")
    ids = []
    for i, env in enumerate(("ph-aa", "ph-bb", "ph-cc", "ph-dd")):
        aid = _make_account(client, f"rr-{i}", g,
                            geelark_phone_id=env,
                            proxy="socks5://old:pw@old.example:1080")
        ids.append(aid)

    proxies = [
        "socks5://user:pass@host1.example:11111",
        "socks5://user:pass@host2.example:22222",
    ]
    r = client.post("/api/accounts/replace-proxies",
                    json={"account_ids": ids, "proxies": proxies})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4
    assert body["synced"] == 4
    assert body["failed"] == 0
    # Round-robin pairing 4 accounts × 2 proxies → idx 0,1,0,1
    assert [it["proxy"] for it in body["items"]] == [
        proxies[0], proxies[1], proxies[0], proxies[1],
    ]

    # GeeLark calls landed with the matching proxyConfig dict.
    assert [c["env_id"] for c in fake.calls] == ["ph-aa", "ph-bb", "ph-cc", "ph-dd"]
    assert fake.calls[0]["proxy_config"] == {
        "typeId": 1,
        "server": "host1.example",
        "port": 11111,
        "username": "user",
        "password": "pass",
    }

    # DB rewritten and the lifecycle reset by the repository layer.
    rows = {a["id"]: a for a in client.get("/api/accounts").json()}
    assert rows[ids[0]]["proxy"] == proxies[0]
    assert rows[ids[2]]["proxy"] == proxies[0]
    assert rows[ids[0]]["proxy_status"] is None
    assert rows[ids[0]]["proxy_expires_at"] is not None


def test_replace_proxies_partial_failure_when_geelark_rejects(client, db, monkeypatch):
    from app.services import geelark_service

    class _FlakyClient(_FakeGeelarkClient):
        def update_phone_detail(self, env_id, *, name=None, proxy_config=None):
            if env_id == "ph-bad":
                raise RuntimeError("GeeLark API error [45004]: proxy check failed")
            return super().update_phone_detail(
                env_id, name=name, proxy_config=proxy_config)

    fake = _FlakyClient()
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    g = _make_group(client, "replace-flaky")
    good = _make_account(client, "good-1", g, geelark_phone_id="ph-good")
    bad = _make_account(client, "bad-1", g, geelark_phone_id="ph-bad")

    r = client.post("/api/accounts/replace-proxies",
                    json={"account_ids": [good, bad],
                          "proxies": ["socks5://u:p@host.example:1080"]})
    assert r.status_code == 200
    body = r.json()
    assert body["synced"] == 1
    assert body["failed"] == 1

    rows = {a["id"]: a for a in client.get("/api/accounts").json()}
    # Good account: DB written, GeeLark synced.
    assert rows[good]["proxy"] == "socks5://u:p@host.example:1080"
    # Bad account: GeeLark sync failed → DB was NOT rewritten, so the
    # operator can retry without rebuilding the assignment.
    assert rows[bad]["proxy"] in (None, "")


def test_replace_proxies_rejects_invalid_proxy(client):
    g = _make_group(client, "replace-invalid")
    aid = _make_account(client, "inv-1", g, geelark_phone_id="ph-1")
    r = client.post("/api/accounts/replace-proxies",
                    json={"account_ids": [aid], "proxies": ["totally not a url"]})
    assert r.status_code == 400


def test_replace_proxies_skips_account_with_inflight_session(
        client, db, monkeypatch):
    """Swapping a proxy under a live RPA would change the exit IP
    mid-session (platform risk). The account is skipped with a busy
    error and its GeeLark phone is never touched."""
    from app.services import geelark_service, phone_runtime
    fake = _FakeGeelarkClient()
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    g = _make_group(client, "replace-busy")
    free = _make_account(client, "free", g, geelark_phone_id="ph-free",
                         proxy="socks5://old:pw@old.example:1080")
    busy = _make_account(client, "busy", g, geelark_phone_id="ph-busy",
                         proxy="socks5://old:pw@old.example:1080")
    phone_runtime.start_session(
        db, account_id=busy, env_id="ph-busy", source="warmup",
        owner_type="account", owner_id=busy, budget_seconds=600,
        created_by="test", kind="warmup")

    r = client.post("/api/accounts/replace-proxies",
                    json={"account_ids": [free, busy],
                          "proxies": ["socks5://user:pass@new.example:9999"]})
    assert r.status_code == 200
    items = {it["account_id"]: it for it in r.json()["items"]}
    assert items[free]["geelark_synced"] is True
    assert items[busy]["geelark_synced"] is False
    assert "busy" in (items[busy]["error"] or "")
    # Only the free phone was pushed to GeeLark.
    assert [c["env_id"] for c in fake.calls] == ["ph-free"]


def test_replace_proxies_same_string_still_renews_expiry(
        client, db, monkeypatch):
    """Re-pasting the same endpoints (a monthly subscription renewal)
    must still push proxy_expires_at forward — the lifecycle hook only
    fires on a changed value, so the endpoint passes expiry explicitly."""
    from app.services import geelark_service
    from app.repositories import accounts as acc_repo
    from datetime import datetime, timedelta
    fake = _FakeGeelarkClient()
    monkeypatch.setattr(geelark_service, "make_client", lambda: fake)

    same = "socks5://user:pass@host.example:11111"
    g = _make_group(client, "replace-renew")
    aid = _make_account(client, "renew", g, geelark_phone_id="ph-renew",
                        proxy=same)
    # Force an old expiry to prove it moves.
    acc_repo.update(db, aid,
                    {"proxy_expires_at": datetime.utcnow() - timedelta(days=5)})

    r = client.post("/api/accounts/replace-proxies",
                    json={"account_ids": [aid], "proxies": [same]})
    assert r.status_code == 200
    row = {a["id"]: a for a in client.get("/api/accounts").json()}[aid]
    assert row["proxy_expires_at"] is not None
    expiry = datetime.fromisoformat(
        row["proxy_expires_at"].replace("Z", "")).replace(tzinfo=None)
    assert expiry > datetime.utcnow() + timedelta(days=25)
