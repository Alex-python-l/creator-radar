import pytest
from fastapi.testclient import TestClient
from app.db import engine
from app.repositories import videos as vr
from sqlmodel import Session

@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as cfg
    up = tmp_path / "uploads"
    up.mkdir()
    monkeypatch.setattr(cfg, "UPLOAD_DIR", up)
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c

def test_login_required_then_accounts_crud(client):
    assert client.get("/api/accounts").status_code == 401
    r = client.post("/api/login", json={"username": "tester", "password": "pw"})
    assert r.status_code == 200
    r = client.post("/api/accounts", json={"profile_name": "A1",
                                           "email": "a@b.com"})
    assert r.status_code == 200
    aid = r.json()["id"]
    assert any(a["id"] == aid for a in client.get("/api/accounts").json())

def test_create_account_bad_body_returns_400(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/accounts", json={"nope": "x"})
    assert r.status_code == 400

def test_login_toggle_missing_returns_404(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.patch("/api/accounts/9999/login-toggle")
    assert r.status_code == 404

def test_login_toggle_flips_value(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "T1"}).json()["id"]
    client.patch(f"/api/accounts/{aid}/login-toggle")
    rows = client.get("/api/accounts").json()
    acc = next(a for a in rows if a["id"] == aid)
    assert acc["tiktok_logged_in"] == 1


def test_upload_rejects_path_traversal(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    files = {"files": ("../../evil.mp4", b"x", "video/mp4")}
    r = client.post("/api/videos/upload", files=files)
    assert r.status_code == 200
    name = client.get("/api/videos").json()["items"][0]["original_filename"]
    assert name == "evil.mp4"  # directory components stripped


def test_upload_duplicate_filenames_get_distinct_stored_paths(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post(
        "/api/videos/upload",
        files=[
            ("files", ("same.mp4", b"one", "video/mp4")),
            ("files", ("same.mp4", b"two", "video/mp4")),
        ],
    )
    assert r.status_code == 200
    assert r.json()["uploaded"] == 2

    rows = sorted(client.get("/api/videos").json()["items"], key=lambda v: v["id"])
    assert [v["original_filename"] for v in rows] == ["same.mp4", "same.mp4"]
    # stored_path is not in the list envelope — query DB directly
    with Session(engine) as sess:
        db_rows = sorted(vr.list_all(sess), key=lambda v: v["id"])
    stored_paths = [v["stored_path"] for v in db_rows]
    assert len(set(stored_paths)) == 2

    from pathlib import Path
    uploads_dir = Path(stored_paths[0]).parent
    assert [Path(p).read_bytes() for p in stored_paths] == [b"one", b"two"]
    assert not list(uploads_dir.glob("*.part"))


def test_delete_video_removes_file(client, tmp_path):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    files = {"files": ("d.mp4", b"x", "video/mp4")}
    client.post("/api/videos/upload", files=files)
    # stored_path not in list envelope — query DB for it
    with Session(engine) as sess:
        db_rows = vr.list_all(sess)
    v = db_rows[0]
    import os
    assert os.path.exists(v["stored_path"])
    client.delete(f"/api/videos/{v['id']}")
    assert not os.path.exists(v["stored_path"])
    assert client.get("/api/videos").json()["items"] == []

def test_delete_account_with_videos_returns_200_and_nulls_video(client, tmp_path):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "DV"}).json()["id"]
    files = {"files": ("k.mp4", b"x", "video/mp4")}
    client.post("/api/videos/upload", files=files)
    # Stamp the phone and mark the publication INITIATED so we have
    # history to preserve.
    from app.repositories import publications as pub_repo
    from app.repositories import videos as vid_repo
    # publications not in list envelope — query DB directly
    with Session(engine) as sess:
        db_rows = vid_repo.list_all(sess)
    v = db_rows[0]
    pid = v["publications"][0]["id"]
    with Session(engine) as sess:
        vid_repo.set_account(sess, v["id"], aid)
        pub_repo.mark_initiated(sess, pid, "c", "tk", "s")
    r = client.delete(f"/api/accounts/{aid}")
    assert r.status_code == 200
    assert not any(a["id"] == aid for a in client.get("/api/accounts").json())
    # Check via DB since account_id and publications are not in the list envelope
    with Session(engine) as sess:
        row = vid_repo.list_all(sess)[0]
    # FK ON DELETE SET NULL preserved history — the publication row
    # survives; phone stamps are cleared.
    assert row["account_id"] is None
    assert row["publications"][0]["result"] == "INITIATED"
    assert row["publications"][0]["account_id"] is None


def test_account_publications_returns_completed_posts_only(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts",
                      json={"profile_name": "History"}).json()["id"]
    other_aid = client.post("/api/accounts",
                            json={"profile_name": "Other"}).json()["id"]
    from app.db import engine
    from app.repositories import publications as pub_repo
    from app.repositories import videos as vid_repo
    from sqlmodel import Session

    with Session(engine) as sess:
        vid = vid_repo.create(sess, "posted.mp4", "/x/posted.mp4")
        other_vid = vid_repo.create(sess, "other.mp4", "/x/other.mp4")
        vid_repo.set_account(sess, vid, aid)
        vid_repo.set_account(sess, other_vid, other_aid)
        ok = pub_repo.create(sess, video_id=vid, platform="tiktok")
        failed = pub_repo.create(sess, video_id=vid, platform="youtube")
        queued = pub_repo.create(sess, video_id=vid, platform="instagram")
        other = pub_repo.create(sess, video_id=other_vid, platform="tiktok")
        pub_repo.mark_initiated(sess, ok, "cap-ok", "tk-ok", "submitted")
        pub_repo.set_result(sess, ok, "SUCCESS", "done")
        pub_repo.mark_initiated(sess, failed, "cap-fail", "yt-fail", "submitted")
        pub_repo.set_result(sess, failed, "FAILURE", "blocked")
        pub_repo.mark_queued(sess, queued)
        pub_repo.mark_initiated(sess, other, "cap-other", "tk-other", "submitted")
        pub_repo.set_result(sess, other, "SUCCESS", "done")

    r = client.get(f"/api/accounts/{aid}/publications")

    assert r.status_code == 200, r.text
    rows = r.json()
    assert [row["result"] for row in rows] == ["FAILURE", "SUCCESS"]
    assert {row["platform"] for row in rows} == {"tiktok", "youtube"}
    assert all(row["account_id"] == aid for row in rows)
    assert all(row["original_filename"] == "posted.mp4" for row in rows)
    assert all(row["result"] in {"SUCCESS", "FAILURE"} for row in rows)


def test_import_endpoint_holds_operation_lock(client, monkeypatch):
    from app.locks import OPERATION_LOCK
    from app.services import geelark_service
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    held = {"import": None}
    def fake_import(conn, _c): held["import"] = OPERATION_LOCK.locked(); return 0
    monkeypatch.setattr(geelark_service, "import_from_geelark", fake_import)
    monkeypatch.setattr(geelark_service, "make_client", lambda: object())
    r = client.post("/api/accounts/import-from-geelark")
    assert r.status_code == 200
    assert held["import"] is True


def test_import_without_proxy_returns_503(client, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "")
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/videos/import",
                    json={"files": [{"id": "X", "name": "a.mp4"}]})
    assert r.status_code == 503


def test_import_empty_selection_returns_400(client, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/videos/import", json={"files": []})
    assert r.status_code == 400


def test_import_already_running_returns_409(client, monkeypatch):
    import app.config as cfg
    from app.services import drive_import
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    monkeypatch.setitem(drive_import._import_state, "state", "running")
    r = client.post("/api/videos/import",
                    json={"files": [{"id": "X", "name": "a.mp4"}]})
    assert r.status_code == 409


def test_import_valid_request_starts(client, monkeypatch):
    import app.config as cfg
    from app.services import drive_import
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "http://p")
    got = {}
    monkeypatch.setattr(
        drive_import, "start_import",
        lambda files, group_id=None, platforms=None: got.update(files=files))
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/videos/import",
                    json={"files": [{"id": "X", "name": "a.mp4"}]})
    assert r.status_code == 200 and r.json()["started"] is True
    assert got["files"] == [{"id": "X", "name": "a.mp4"}]


def test_import_list_returns_files(client, monkeypatch):
    from app.services import drive_import
    monkeypatch.setattr(drive_import, "list_folder",
                        lambda url: [{"id": "X", "name": "a.mp4",
                                      "already_imported": False}])
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/videos/import/list",
                    json={"folder_url": "https://drive.google.com/drive/folders/ID"})
    assert r.status_code == 200
    assert r.json()["files"][0]["name"] == "a.mp4"


def test_import_list_without_proxy_returns_503(client, monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "DRIVE_IMPORT_PROXY", "")
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/videos/import/list",
                    json={"folder_url": "https://drive.google.com/drive/folders/X1234567890"})
    assert r.status_code == 503


def test_import_status_returns_state(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.get("/api/videos/import/status")
    assert r.status_code == 200
    assert "state" in r.json()


def test_patch_video_sets_account(client, default_group_id):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts", json={
        "profile_name": "PV1",
        "group_id": default_group_id,
    }).json()["id"]
    client.post(
        "/api/videos/upload",
        data={"group_id": str(default_group_id)},
        files={"files": ("p.mp4", b"x", "video/mp4")},
    )
    vid = client.get("/api/videos").json()["items"][0]["id"]
    # PATCH takes a single account_id (no platform): legacy bulk picker
    # applies the phone to all pending publications, but does not queue.
    r = client.patch(f"/api/videos/{vid}", json={"account_id": aid})
    assert r.status_code == 200
    # account_id and publications not in list envelope — query DB directly
    with Session(engine) as sess:
        row = vr.list_all(sess)[0]
    assert row["account_id"] == aid
    tiktok = next(p for p in row["publications"] if p["platform"] == "tiktok")
    assert tiktok["account_id"] == aid
    assert tiktok["result"] == "PENDING"


def test_patch_video_unknown_video_returns_404(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.patch("/api/videos/99999", json={"account_id": None})
    assert r.status_code == 404


def test_patch_video_unknown_account_returns_400(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    client.post("/api/videos/upload", files={"files": ("q.mp4", b"x", "video/mp4")})
    vid = client.get("/api/videos").json()["items"][0]["id"]
    r = client.patch(f"/api/videos/{vid}", json={"account_id": 99999})
    assert r.status_code == 400


@pytest.fixture
def secrets_client(client):
    """A leader in workspace 1 with one encrypted credential."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    response = client.patch(
        "/api/settings/secrets", json={"GEELARK_TOKEN": "oldtoken12345"},
    )
    assert response.status_code == 200
    return client


def test_secrets_get_returns_masked_status(secrets_client):
    client = secrets_client
    r = client.get("/api/settings/secrets")
    assert r.status_code == 200
    body = r.json()
    # REGISTER_CODE / DASHBOARD_SECRET_KEY 随 2026-07-17 登录翻转退役。
    assert set(body.keys()) == {"GEELARK_TOKEN", "GEMINI_API_KEY"}
    assert body["GEELARK_TOKEN"]["set"] is True
    # masked: first 4 + 6 bullets + last 4
    assert body["GEELARK_TOKEN"]["masked"] == "oldt••••••2345"
    assert body["GEMINI_API_KEY"]["set"] is False
    assert body["GEMINI_API_KEY"]["masked"] == ""


def test_secrets_patch_encrypts_and_returns_masks(secrets_client):
    client = secrets_client
    r = client.patch("/api/settings/secrets", json={
        "GEELARK_TOKEN": "newtoken99",
        "GEMINI_API_KEY": "gem-abc",
    })
    assert r.status_code == 200
    assert set(r.json()["updated"]) == {"GEELARK_TOKEN", "GEMINI_API_KEY"}

    body = client.get("/api/settings/secrets").json()
    assert body["GEELARK_TOKEN"]["masked"] == "newt••••••en99"
    assert body["GEMINI_API_KEY"]["set"] is True


def test_secrets_patch_skips_blank_and_unknown_fields(secrets_client):
    client = secrets_client
    r = client.patch("/api/settings/secrets", json={
        "GEELARK_TOKEN": "",            # blank — ignore, keep old
        "GEMINI_API_KEY": "   ",        # whitespace — ignore
        "DRIVE_IMPORT_PROXY": "evil",   # not a secret field — ignore
    })
    assert r.status_code == 200
    assert r.json()["updated"] == []
    assert client.get("/api/settings/secrets").json()["GEELARK_TOKEN"]["set"] is True


def test_secrets_patch_adds_missing_key(secrets_client):
    client = secrets_client
    r = client.patch("/api/settings/secrets", json={"GEMINI_API_KEY": "k1"})
    assert r.status_code == 200
    assert client.get("/api/settings/secrets").json()["GEMINI_API_KEY"]["set"] is True


def test_secrets_endpoints_require_auth(client):
    # No login → both endpoints must 401
    assert client.get("/api/settings/secrets").status_code == 401
    assert client.patch("/api/settings/secrets", json={}).status_code == 401


def test_create_account_normalizes_proxy(client):
    """Same normalize-on-write contract as PATCH — applies to direct API
    creates too so any path into the proxy column converges on the form
    httpx can dial."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/accounts",
                    json={"profile_name": "PXC1",
                          "proxy": "984a4549:6eca3dd4@ph353.kookeey.info:22795"})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    row = next(a for a in client.get("/api/accounts").json() if a["id"] == aid)
    assert row["proxy"] \
        == "socks5://984a4549:6eca3dd4@ph353.kookeey.info:22795"


def test_create_account_rejects_unrecognizable_proxy(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/accounts",
                    json={"profile_name": "PXC2", "proxy": "garbage"})
    assert r.status_code == 400
    assert "proxy" in r.text.lower()


def test_patch_account_normalizes_proxy(client):
    """Pasting `user:pass@host:port` into the account editor must get
    stored as `socks5://user:pass@host:port` so the scheduler's daily
    probe doesn't trip over httpx's 'Unknown scheme' rejection."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "PX1"}).json()["id"]
    r = client.patch(f"/api/accounts/{aid}",
                     json={"proxy": "984a4549:6eca3dd4@ph353.kookeey.info:22795"})
    assert r.status_code == 200, r.text
    row = next(a for a in client.get("/api/accounts").json() if a["id"] == aid)
    assert row["proxy"] \
        == "socks5://984a4549:6eca3dd4@ph353.kookeey.info:22795"


def test_patch_account_profile_name_syncs_to_geelark(client, monkeypatch):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts", json={
        "profile_name": "Old",
        "geelark_phone_id": "ph-1",
    }).json()["id"]
    calls = []

    class FakeGeeLark:
        def update_phone_detail(self, phone_id, *, name=None):
            calls.append((phone_id, name))
            return {}

    from app.services import geelark_service
    monkeypatch.setattr(geelark_service, "make_client", lambda: FakeGeeLark())

    r = client.patch(f"/api/accounts/{aid}",
                     json={"profile_name": "  New Name  "})

    assert r.status_code == 200, r.text
    assert calls == [("ph-1", "New Name")]
    row = next(a for a in client.get("/api/accounts").json() if a["id"] == aid)
    assert row["profile_name"] == "New Name"


def test_patch_account_profile_name_keeps_local_on_geelark_failure(
        client, monkeypatch):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts", json={
        "profile_name": "Still Local",
        "geelark_phone_id": "ph-2",
    }).json()["id"]

    class FailingGeeLark:
        def update_phone_detail(self, phone_id, *, name=None):
            raise RuntimeError("upstream rejected rename")

    from app.services import geelark_service
    monkeypatch.setattr(geelark_service, "make_client",
                        lambda: FailingGeeLark())

    r = client.patch(f"/api/accounts/{aid}",
                     json={"profile_name": "Remote Failed"})

    assert r.status_code == 502
    assert "upstream rejected rename" in r.text
    row = next(a for a in client.get("/api/accounts").json() if a["id"] == aid)
    assert row["profile_name"] == "Still Local"


def test_patch_account_profile_name_duplicate_stays_local_only(
        client, monkeypatch):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    client.post("/api/accounts", json={"profile_name": "Taken"})
    aid = client.post("/api/accounts", json={
        "profile_name": "Original",
        "geelark_phone_id": "ph-3",
    }).json()["id"]
    calls = []

    class FakeGeeLark:
        def update_phone_detail(self, phone_id, *, name=None):
            calls.append((phone_id, name))
            return {}

    from app.services import geelark_service
    monkeypatch.setattr(geelark_service, "make_client", lambda: FakeGeeLark())

    r = client.patch(f"/api/accounts/{aid}", json={"profile_name": "Taken"})

    assert r.status_code == 400
    assert "already used" in r.text
    assert calls == []
    row = next(a for a in client.get("/api/accounts").json() if a["id"] == aid)
    assert row["profile_name"] == "Original"


def test_patch_account_rejects_unrecognizable_proxy(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "PX2"}).json()["id"]
    r = client.patch(f"/api/accounts/{aid}", json={"proxy": "not-a-proxy"})
    assert r.status_code == 400
    assert "proxy" in r.text.lower()


def test_patch_account_allows_clearing_proxy(client):
    """Empty string means 'unset' — the wizard and the cleanup flows both
    rely on being able to blank a proxy without tripping validation."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "PX3"}).json()["id"]
    client.patch(f"/api/accounts/{aid}",
                 json={"proxy": "socks5://1.2.3.4:1080"})
    r = client.patch(f"/api/accounts/{aid}", json={"proxy": ""})
    assert r.status_code == 200
    row = next(a for a in client.get("/api/accounts").json() if a["id"] == aid)
    assert (row["proxy"] or "") == ""


def test_patch_account_missing_returns_404(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.patch("/api/accounts/9999", json={"proxy": ""})
    assert r.status_code == 404


def test_avatar_endpoints_are_removed(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "NOAV"}).json()["id"]

    r = client.get(f"/api/accounts/{aid}/avatar")
    assert r.status_code == 404

    r = client.post(
        f"/api/accounts/{aid}/avatar",
        files={"file": ("a.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 404

    r = client.delete(f"/api/accounts/{aid}/avatar")
    assert r.status_code == 404


def test_patch_account_ignores_retired_avatar_fields(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts", json={"profile_name": "NOAV2"}).json()["id"]

    r = client.patch(
        f"/api/accounts/{aid}",
        json={
            "profile_pic_path": "/etc/passwd",
            "profile_pic_url": "https://legacy.example/a.jpg",
            "pic_status": "initiated",
            "pic_task_id": "task-1",
        },
    )
    assert r.status_code == 200
    row = next(a for a in client.get("/api/accounts").json() if a["id"] == aid)
    for key in ("profile_pic_path", "profile_pic_url",
                "pic_status", "pic_task_id", "avatar_src"):
        assert key not in row


def test_create_group_and_list(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/groups",
                     json={"name": "jp-1", "notes": "first batch"})
    assert r.status_code == 200
    gid = r.json()["id"]

    r = client.get("/api/groups")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "jp-1"
    assert rows[0]["counts"]["phones"] == 0


def test_get_group_includes_counts(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/groups", json={"name": "g1"})
    gid = r.json()["id"]
    r = client.get(f"/api/groups/{gid}")
    assert r.status_code == 200
    assert "counts" in r.json()


def test_get_unknown_group_404(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.get("/api/groups/9999")
    assert r.status_code == 404


def test_proxies_check_returns_per_proxy_results(client, monkeypatch):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    from app.services import proxy_intake as pi

    def fake_check_batch(proxies, check_one=None):
        return [{"proxy": p, "ok": True, "exit_ip": "1.1.1.1",
                  "latency_ms": 100, "error": None} for p in proxies]

    monkeypatch.setattr(pi, "check_batch", fake_check_batch)

    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    r = client.post(f"/api/groups/{gid}/proxies/check",
                     json={"proxies": ["socks5://a:1080", "socks5://b:1080"]})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert all(row["ok"] for row in rows)


def test_phones_create_stream_invokes_geelark_service(client, monkeypatch):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    from app.services import geelark_service as gs

    captured = {}
    def fake_make_client(): return object()
    def fake_create_for_group(conn, client, group_id, proxies, count=None):
        captured["group_id"] = group_id
        captured["proxies"] = list(proxies)
        yield "log line A"
        yield "log line B"
    monkeypatch.setattr(gs, "make_client", fake_make_client)
    monkeypatch.setattr(gs, "create_profiles_for_group",
                          fake_create_for_group)

    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    r = client.post(f"/api/groups/{gid}/phones/create",
                     json={"proxies": ["socks5://a:1080"]})
    assert r.status_code == 200
    body = r.text
    assert "log line A" in body
    assert "log line B" in body
    assert captured["group_id"] == gid
    assert captured["proxies"] == ["socks5://a:1080"]


# Task 12 Boundary Condition Tests
def test_proxies_check_404_on_missing_group(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/groups/9999/proxies/check", json={"proxies": ["p1"]})
    assert r.status_code == 404

def test_proxies_check_400_on_non_list(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "gp"}).json()["id"]
    r = client.post(f"/api/groups/{gid}/proxies/check", json={"proxies": "not-list"})
    assert r.status_code == 400

def test_phones_create_404_on_missing_group(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/groups/9999/phones/create", json={"proxies": ["p1"]})
    assert r.status_code == 404

def test_phones_create_400_on_non_list(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "gp"}).json()["id"]
    r = client.post(f"/api/groups/{gid}/phones/create", json={"proxies": "not-list"})
    assert r.status_code == 400

def test_phones_create_400_on_empty_list(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "gp"}).json()["id"]
    r = client.post(f"/api/groups/{gid}/phones/create", json={"proxies": []})
    assert r.status_code == 400


def test_accounts_assign_404_on_missing_group(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/groups/9999/accounts/assign", json={"accounts": []})
    assert r.status_code == 404


def test_accounts_assign_writes_login_info_to_unassigned_rows(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    from app.repositories import accounts as ar
    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    with Session(engine) as conn:
        a1 = ar.create(conn, {"profile_name": "P1", "group_id": gid,
                                "geelark_phone_id": "ph1"})
        a2 = ar.create(conn, {"profile_name": "P2", "group_id": gid,
                                "geelark_phone_id": "ph2"})
        a3 = ar.create(conn, {"profile_name": "P3", "group_id": gid,
                                "geelark_phone_id": "ph3"})

    r = client.post(f"/api/groups/{gid}/accounts/assign", json={"accounts": [
        {"tiktok_login_info": "tt blob 1"},
        {"tiktok_login_info": "tt blob 2", "youtube_login_info": "yt blob 2"},
        # All-empty row gets skipped
        {"tiktok_login_info": "", "youtube_login_info": None},
    ]})
    assert r.status_code == 200
    assert r.json() == {"assigned": 2, "skipped": 1}
    with Session(engine) as conn:
        assert ar.get(conn, a1)["tiktok_login_info"] == "tt blob 1"
        assert ar.get(conn, a2)["tiktok_login_info"] == "tt blob 2"
        assert ar.get(conn, a2)["youtube_login_info"] == "yt blob 2"
        assert ar.get(conn, a3)["tiktok_login_info"] is None  # untouched


def test_accounts_assign_splits_raw_login_info_lines(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    from app.repositories import accounts as ar
    from app.db import engine
    from sqlmodel import Session
    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    raw = "\n\n".join([
        "kogolume\tk9@#EO$!bR&R&g\tnikolaev-nk3hf@rambler.ru\tJsCGOKxys3OJR",
        "sofobesa\tN5*uLf!n3KAmu\tivanova.03xs9@rambler.ru\t5ru4gFSk2UiB",
        "sebafira\tk3$yb4F56lG#J\tnikitin-e97wc@rambler.ru\t884qlnoIeQrwP",
        "helepeyo\tX8#tROIAj&jER3\tnikitin-8itju@rambler.ru\tSTrcTzmF5Wqm",
        "numediro\tB7&Yai^&dXEa\tkondrashov.x38qa@rambler.ru\tP91v0NadRIsRt2",
        "higafuyo\tv6!6qeXtZS&CE\tkazantsev7pnyz@rambler.ru\t3IHgln3VTNtK",
        "chileluke\tS4@tg7@2MemW$H\tpoliakov4zagi@rambler.ru\tRgPBNeyYXS3y",
        "hafumika\tK4&VDDREhRuDOm\tkhabarova_1dts0@rambler.ru\tq8yPojBInQ0bLe",
        "segobena\tg8!oFaL%ruFBJ\tkharitonov35wll@rambler.ru\t2SS0tvkCzuIetq",
        "kobudono\tX0$3OjT*WTdPZP2\tvolkova_2yrqq@rambler.ru\t7AFTNXvqAEmCEM",
    ])
    with Session(engine) as conn:
        ids = [
            ar.create(conn, {"profile_name": f"P{i}", "group_id": gid,
                             "geelark_phone_id": f"ph{i}"})
            for i in range(1, 11)
        ]

    r = client.post(f"/api/groups/{gid}/accounts/assign",
                    json={"raw_login_info": raw})

    assert r.status_code == 200
    assert r.json() == {"assigned": 10, "skipped": 0}
    with Session(engine) as conn:
        assert ar.get(conn, ids[0])["tiktok_login_info"] == (
            "kogolume\tk9@#EO$!bR&R&g\t"
            "nikolaev-nk3hf@rambler.ru\tJsCGOKxys3OJR"
        )
        assert ar.get(conn, ids[9])["tiktok_login_info"] == (
            "kobudono\tX0$3OjT*WTdPZP2\t"
            "volkova_2yrqq@rambler.ru\t7AFTNXvqAEmCEM"
        )


def test_accounts_assign_splits_raw_login_info_blank_line_blocks(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    from app.repositories import accounts as ar
    from app.db import engine
    from sqlmodel import Session
    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    raw = """user1
pass1
mail1@example.com
mailpass1

user2
pass2
mail2@example.com
mailpass2
"""
    with Session(engine) as conn:
        ids = [
            ar.create(conn, {"profile_name": f"P{i}", "group_id": gid,
                             "geelark_phone_id": f"ph{i}"})
            for i in range(1, 9)
        ]

    r = client.post(f"/api/groups/{gid}/accounts/assign",
                    json={"raw_login_info": raw})

    assert r.status_code == 200
    assert r.json() == {"assigned": 2, "skipped": 0}
    with Session(engine) as conn:
        assert ar.get(conn, ids[0])["tiktok_login_info"] == (
            "user1\npass1\nmail1@example.com\nmailpass1"
        )
        assert ar.get(conn, ids[1])["tiktok_login_info"] == (
            "user2\npass2\nmail2@example.com\nmailpass2"
        )
        assert ar.get(conn, ids[2])["tiktok_login_info"] is None


def test_bulk_mark_logged_in(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    from app.repositories import accounts as ar
    with Session(engine) as conn:
        a1 = ar.create(conn, {"profile_name": "Q1"})
        a2 = ar.create(conn, {"profile_name": "Q2"})
    r = client.post("/api/accounts/bulk-mark-logged-in",
                     json={"ids": [a1, a2]})
    assert r.status_code == 200
    assert r.json()["updated"] == 2
    with Session(engine) as conn:
        assert ar.get(conn, a1)["tiktok_logged_in"] == 1
        assert ar.get(conn, a2)["tiktok_logged_in"] == 1


def test_video_upload_persists_group_id(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    file_bytes = b"fake-mp4-bytes"
    r = client.post(
        "/api/videos/upload",
        data={"group_id": str(gid)},
        files=[("files", ("clip.mp4", file_bytes, "video/mp4"))],
    )
    assert r.status_code == 200
    assert r.json()["uploaded"] == 1
    with Session(engine) as sess:
        rows = vr.list_all(sess)
    assert rows[0]["original_filename"] == "clip.mp4"
    assert rows[0]["group_id"] == gid


def test_drive_import_passes_group_id(client, monkeypatch):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    from app.services import drive_import as di

    captured = {}
    def fake_start_import(files, group_id=None, platforms=None):
        captured["files"] = files
        captured["group_id"] = group_id
        captured["platforms"] = platforms
        import threading
        return threading.Thread(target=lambda: None)
    monkeypatch.setattr(di, "start_import", fake_start_import)

    r = client.post("/api/groups", json={"name": "gp"})
    gid = r.json()["id"]
    r = client.post("/api/videos/import",
                     json={"files": [{"id": "f1", "name": "a.mp4"}],
                            "group_id": gid})
    assert r.status_code == 200
    assert captured["group_id"] == gid


def test_delete_empty_group_succeeds(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "tmp"}).json()["id"]
    r = client.delete(f"/api/groups/{gid}")
    assert r.status_code == 200

def test_delete_group_with_accounts_rejected(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "occupied"}).json()["id"]
    from app.repositories import accounts as ar
    with Session(engine) as sess:
        ar.create(sess, {"profile_name": "P1", "group_id": gid})
    r = client.delete(f"/api/groups/{gid}")
    assert r.status_code == 400


def test_delete_group_with_videos_rejected(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "has-videos"}).json()["id"]
    client.post(
        "/api/videos/upload",
        files={"files": ("group-video.mp4", b"x", "video/mp4")},
        data={"group_id": str(gid)},
    )
    r = client.delete(f"/api/groups/{gid}")
    assert r.status_code == 400
    assert "videos" in r.json()["detail"]


def test_patch_group_renames(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "before"}).json()["id"]
    r = client.patch(f"/api/groups/{gid}", json={"name": "after"})
    assert r.status_code == 200
    assert any(g["name"] == "after" for g in client.get("/api/groups").json())


def test_patch_group_rejects_duplicate_name(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    g1 = client.post("/api/groups", json={"name": "alpha"}).json()["id"]
    client.post("/api/groups", json={"name": "beta"})
    r = client.patch(f"/api/groups/{g1}", json={"name": "beta"})
    assert r.status_code == 400


def test_patch_group_404_when_missing(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.patch("/api/groups/9999", json={"name": "x"})
    assert r.status_code == 404


def test_phones_create_accepts_count(client, monkeypatch):
    """count gets passed through to the service layer."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "wiz-A"}).json()["id"]

    captured: dict = {}

    def fake_create(conn, c, group_id, proxies, count=None):
        captured["count"] = count
        captured["proxies"] = proxies
        yield "Done."

    monkeypatch.setattr(
        "app.services.geelark_service.create_profiles_for_group",
        fake_create)
    monkeypatch.setattr(
        "app.services.geelark_service.make_client",
        lambda: object())

    r = client.post(f"/api/groups/{gid}/phones/create",
                    json={"proxies": ["socks5://p:1080"], "count": 4})
    assert r.status_code == 200, r.text
    # Drain SSE so the generator actually runs
    list(r.iter_lines())
    assert captured["count"] == 4
    assert captured["proxies"] == ["socks5://p:1080"]


def test_phones_create_without_count_passes_none(client, monkeypatch):
    """Backwards compatibility — old front ends omitting count work."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "wiz-B"}).json()["id"]

    captured: dict = {}

    def fake_create(conn, c, group_id, proxies, count=None):
        captured["count"] = count
        yield "Done."

    monkeypatch.setattr(
        "app.services.geelark_service.create_profiles_for_group",
        fake_create)
    monkeypatch.setattr(
        "app.services.geelark_service.make_client",
        lambda: object())

    r = client.post(f"/api/groups/{gid}/phones/create",
                    json={"proxies": ["socks5://p:1080"]})
    assert r.status_code == 200
    list(r.iter_lines())
    assert captured["count"] is None


def test_phones_create_rejects_zero_count(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "wiz-C"}).json()["id"]
    r = client.post(f"/api/groups/{gid}/phones/create",
                    json={"proxies": ["socks5://p:1080"], "count": 0})
    assert r.status_code == 400


def test_phones_create_rejects_negative_count(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "wiz-D"}).json()["id"]
    r = client.post(f"/api/groups/{gid}/phones/create",
                    json={"proxies": ["socks5://p:1080"], "count": -1})
    assert r.status_code == 400


def test_phones_create_rejects_non_int_count(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "wiz-E"}).json()["id"]
    r = client.post(f"/api/groups/{gid}/phones/create",
                    json={"proxies": ["socks5://p:1080"], "count": "five"})
    assert r.status_code == 400


# --- Static asset caching: HTML entry must revalidate, hashed bundles
# get cached aggressively. Without this, deployments don't reach users
# until they hard-refresh (browsers heuristic-cache HTML with no
# Cache-Control set).

def test_unknown_api_paths_never_get_a_200_html_shell(client):
    """Removed or typo'd /api/* paths must fail with a JSON error, never a
    200 HTML shell.

    History: the pre-2026-07-18 SPA fallback served a 200 index.html for
    unknown GET paths, which a production smoke mistook (via the legacy
    publish-stream path) for an unauthenticated endpoint. The SPA hosting
    is retired (frontend lives in apps/web) — this pins plain-API behavior.
    Note publish-stream specifically collides with the /{vid} path params
    (vid="publish-stream", DELETE/PATCH only) so it is a 405, not a 404."""
    r = client.get("/api/videos/publish-stream")
    assert r.status_code == 405
    assert "text/html" not in r.headers.get("content-type", "")

    r = client.get("/api/nonexistent/never-a-route")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


def test_assign_videos_sets_group_id(client, monkeypatch):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "GA"}).json()["id"]
    client.post("/api/videos/upload",
                files={"files": ("a.mp4", b"x", "video/mp4")})
    client.post("/api/videos/upload",
                files={"files": ("b.mp4", b"x", "video/mp4")})
    vids = [v["id"] for v in client.get("/api/videos").json()["items"]]
    assert all(v["group_id"] is None for v in client.get("/api/videos").json()["items"])

    async def fake_defer_async(**kwargs):
        return type("J", (), {"id": "scan-job"})()

    from app.tasks.auto_publish import auto_publish_scan_task
    monkeypatch.setattr(auto_publish_scan_task, "defer_async",
                        fake_defer_async)

    r = client.post("/api/videos/assign",
                    json={"video_ids": vids, "group_id": gid})
    assert r.status_code == 200
    assert r.json()["assigned"] == 2
    after = client.get("/api/videos").json()["items"]
    assert all(v["group_id"] == gid for v in after)


def test_assign_videos_wakes_auto_publish_scan(client, monkeypatch):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "Wake"}).json()["id"]
    client.post("/api/videos/upload",
                files={"files": ("wake.mp4", b"x", "video/mp4")})
    vid = client.get("/api/videos").json()["items"][0]["id"]
    deferred = []

    async def fake_defer_async(**kwargs):
        deferred.append(kwargs)
        return type("J", (), {"id": "scan-job"})()

    from app.tasks.auto_publish import auto_publish_scan_task
    monkeypatch.setattr(auto_publish_scan_task, "defer_async",
                        fake_defer_async)

    r = client.post("/api/videos/assign",
                    json={"video_ids": [vid], "group_id": gid})

    assert r.status_code == 200, r.text
    assert r.json()["assigned"] == 1
    assert r.json()["publish_scan_queued"] is True
    assert len(deferred) == 1
    assert isinstance(deferred[0]["timestamp"], int)


def test_assign_videos_unknown_group_returns_400(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    client.post("/api/videos/upload",
                files={"files": ("z.mp4", b"x", "video/mp4")})
    vid = client.get("/api/videos").json()["items"][0]["id"]
    r = client.post("/api/videos/assign",
                    json={"video_ids": [vid], "group_id": 99999})
    assert r.status_code == 400


def test_assign_videos_empty_returns_400(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "GE"}).json()["id"]
    r = client.post("/api/videos/assign",
                    json={"video_ids": [], "group_id": gid})
    assert r.status_code == 400


def test_publication_attempts_endpoint_returns_failure_history(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    from app.repositories import accounts as acc_repo
    from app.repositories import publications as pub_repo

    with Session(engine) as sess:
        aid = acc_repo.create(sess, {"profile_name": "Attempts",
                                     "geelark_phone_id": "ph-attempts"})
        vid = vr.create(sess, "attempts.mp4", "/tmp/attempts.mp4")
        vr.set_account(sess, vid, aid)
        pid = pub_repo.create(sess, video_id=vid, platform="tiktok")
        pub_repo.record_attempt(
            sess,
            pid,
            stage="caption",
            source="gemini",
            message="Gemini timed out",
            error_code="timeout",
        )

    r = client.get(f"/api/videos/publications/{pid}/attempts")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body[0]["attempt_no"] == 1
    assert body[0]["source"] == "gemini"
    assert body[0]["stage"] == "caption"
    assert body[0]["message"] == "Gemini timed out"
    assert body[0]["error_code"] == "timeout"


def test_retry_failed_publication_resets_to_pending_and_wakes_scan(
        client, monkeypatch, default_group_id):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    from app.repositories import accounts as acc_repo
    from app.repositories import publications as pub_repo

    with Session(engine) as sess:
        aid = acc_repo.create(sess, {"profile_name": "RetryPub",
                                     "geelark_phone_id": "ph-retry-pub",
                                     "group_id": default_group_id})
        vid = vr.create(sess, "retry-pub.mp4", "/tmp/retry-pub.mp4",
                        group_id=default_group_id)
        vr.set_account(sess, vid, aid)
        pid = pub_repo.create(sess, video_id=vid, platform="tiktok")
        pub_repo.set_result(sess, pid, "FAILURE", "blocked",
                            fail_code=20267,
                            fail_screenshot="https://img/fail.jpg")

    deferred = []

    async def fake_defer_async(**kwargs):
        deferred.append(kwargs)
        return type("J", (), {"id": "scan-job"})()

    from app.tasks.auto_publish import auto_publish_scan_task
    monkeypatch.setattr(auto_publish_scan_task, "defer_async",
                        fake_defer_async)

    r = client.post(f"/api/videos/publications/{pid}/retry")

    assert r.status_code == 200, r.text
    assert r.json()["publish_scan_queued"] is True
    assert len(deferred) == 1
    with Session(engine) as sess:
        row = pub_repo.get(sess, pid)
        assert row["result"] == "PENDING"
        assert row["retry_count"] == 0
        assert row["next_retry_at"] is None
        assert row["fail_code"] is None
        assert row["fail_screenshot"] is None


def test_upload_with_group_wakes_auto_publish_scan(client, monkeypatch):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    gid = client.post("/api/groups", json={"name": "UploadWake"}).json()["id"]
    deferred = []

    async def fake_defer_async(**kwargs):
        deferred.append(kwargs)
        return type("J", (), {"id": "scan-job"})()

    from app.tasks.auto_publish import auto_publish_scan_task
    monkeypatch.setattr(auto_publish_scan_task, "defer_async",
                        fake_defer_async)

    r = client.post(
        "/api/videos/upload",
        data={"group_id": str(gid), "platforms": "tiktok"},
        files={"files": ("ready.mp4", b"x", "video/mp4")},
    )

    assert r.status_code == 200, r.text
    assert r.json()["uploaded"] == 1
    assert r.json()["publish_scan_queued"] is True
    assert len(deferred) == 1


# --- Multi-platform publish (Task 9) ----------------------------------

def test_upload_creates_default_tiktok_publication(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/videos/upload",
                    files={"files": ("u1.mp4", b"x", "video/mp4")})
    assert r.status_code == 200
    # publications not in list envelope — query DB directly
    with Session(engine) as sess:
        v = vr.list_all(sess)[0]
    assert "publications" in v
    assert [p["platform"] for p in v["publications"]] == ["tiktok"]
    assert v["publications"][0]["result"] == "PENDING"


def test_upload_with_all_four_platforms(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post(
        "/api/videos/upload",
        data={"platforms": "tiktok,youtube,instagram,facebook"},
        files={"files": ("u2.mp4", b"x", "video/mp4")})
    assert r.status_code == 200
    # publications not in list envelope — query DB directly
    with Session(engine) as sess:
        v = vr.list_all(sess)[0]
    platforms = sorted(p["platform"] for p in v["publications"])
    assert platforms == ["facebook", "instagram", "tiktok", "youtube"]


def test_upload_with_unknown_platform_returns_400(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post(
        "/api/videos/upload",
        data={"platforms": "tiktok,linkedin"},
        files={"files": ("u3.mp4", b"x", "video/mp4")})
    assert r.status_code == 400


def test_delete_video_cascades_publications(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    client.post("/api/videos/upload",
                data={"platforms": "tiktok,instagram,facebook"},
                files={"files": ("u4.mp4", b"x", "video/mp4")})
    # publications not in list envelope — query DB directly
    with Session(engine) as sess:
        v = vr.list_all(sess)[0]
    assert len(v["publications"]) == 3
    assert client.delete(f"/api/videos/{v['id']}").status_code == 200
    from app.db import session_scope
    import sqlalchemy as sa
    with session_scope() as s:
        n = s.execute(sa.text(
            "SELECT COUNT(*) FROM publications WHERE video_id=:v"),
            {"v": v["id"]}).first()[0]
    assert n == 0


def test_accounts_endpoint_exposes_all_platform_columns(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    payload = {"profile_name": "MULTI-X"}
    for plat in ("youtube", "instagram", "facebook"):
        payload[f"{plat}_logged_in"] = 1
        payload[f"{plat}_login_info"] = f"{plat} login blob"
    aid = client.post("/api/accounts", json=payload).json()["id"]
    row = next(a for a in client.get("/api/accounts").json()
               if a["id"] == aid)
    for plat in ("youtube", "instagram", "facebook"):
        assert row[f"{plat}_logged_in"] == 1
        assert row[f"{plat}_login_info"] == f"{plat} login blob"
        assert f"last_posted_at_{plat}" in row


def test_per_video_publish_returns_queued(client, monkeypatch, default_group_id):
    """POST /api/videos/{vid}/publish?platform=... enqueues an outbox
    row + defers and returns {status: QUEUED, publication_id}. The
    outbox row is marked sent on the happy path so the dispatcher
    doesn't re-defer."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts",
                      json={"profile_name": "PVP",
                            "geelark_phone_id": "phPVP",
                            "warmed_up": 1,
                            "tiktok_logged_in": 1}).json()["id"]
    from app.db import engine as _eng
    from sqlalchemy import text as _t
    with _eng.begin() as _c:
        _c.execute(_t("UPDATE accounts SET group_id = :gid WHERE id = :aid"),
                   {"gid": default_group_id, "aid": aid})
    client.post("/api/videos/upload",
                files={"files": ("pvp.mp4", b"x", "video/mp4")},
                data={"group_id": str(default_group_id)})
    vid = client.get("/api/videos").json()["items"][0]["id"]

    # Stub publish_publication_task.defer_async so we don't need a real
    # Procrastinate connector. The endpoint goes through
    # try_prompt_dispatch → _dispatch_one → publish_publication_task.defer_async.
    deferred = []

    async def fake_defer_async(**kw):
        deferred.append(kw)
        return type("J", (), {"id": "fake-job-id"})()

    from app.tasks.publish import publish_publication_task
    monkeypatch.setattr(publish_publication_task, "defer_async",
                        fake_defer_async)

    r = client.post(f"/api/videos/{vid}/publish?platform=tiktok",
                    json={"account_id": aid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "QUEUED"
    assert "publication_id" in body
    pid = body["publication_id"]
    assert deferred and deferred[0]["publication_id"] == pid

    # Outbox row exists and was marked sent on the prompt dispatch.
    with _eng.begin() as _c:
        row = _c.execute(_t(
            "SELECT intent, business_ref, sent_at, attempts "
            "FROM outbox WHERE business_ref = :ref"
        ), {"ref": f"publication:{pid}"}).first()
    assert row is not None
    assert row.intent == "publish_publication"
    assert row.sent_at is not None
    assert row.attempts == 0


def test_manual_video_account_and_publish_reject_cross_group_account(
        client, default_group_id):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    other_gid = client.post("/api/groups", json={"name": "other"}).json()["id"]
    aid = client.post(
        "/api/accounts",
        json={
            "profile_name": "WRONG-GROUP",
            "geelark_phone_id": "ph-wrong",
            "warmed_up": 1,
            "tiktok_logged_in": 1,
            "group_id": other_gid,
        },
    ).json()["id"]
    client.post(
        "/api/videos/upload",
        files={"files": ("wrong-group.mp4", b"x", "video/mp4")},
        data={"group_id": str(default_group_id)},
    )
    vid = client.get("/api/videos").json()["items"][0]["id"]

    r = client.patch(f"/api/videos/{vid}", json={"account_id": aid})
    assert r.status_code == 400
    assert "group" in r.json()["detail"]

    r = client.post(f"/api/videos/{vid}/publish?platform=tiktok",
                    json={"account_id": aid})
    assert r.status_code == 400
    assert "group" in r.json()["detail"]


def test_per_video_publish_leaves_outbox_pending_when_defer_fails(
        client, tmp_path, monkeypatch, default_group_id):
    """When the prompt defer raises, the endpoint MUST still return 200
    and leave a pending outbox row so the dispatcher can retry. The
    publication stays QUEUED (not rolled back) because the outbox
    guarantees forward progress within ~2 minutes."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts",
                      json={"profile_name": "RB",
                            "geelark_phone_id": "phRB",
                            "warmed_up": 1,
                            "tiktok_logged_in": 1}).json()["id"]
    from app.db import engine as _eng
    from sqlalchemy import text as _t
    with _eng.begin() as _c:
        _c.execute(_t("UPDATE accounts SET group_id = :gid WHERE id = :aid"),
                   {"gid": default_group_id, "aid": aid})
    client.post("/api/videos/upload",
                files={"files": ("rb.mp4", b"x", "video/mp4")},
                data={"group_id": str(default_group_id)})
    vid = client.get("/api/videos").json()["items"][0]["id"]

    async def boom_defer(**kw):
        raise RuntimeError("queue down")

    from app.tasks.publish import publish_publication_task
    monkeypatch.setattr(publish_publication_task, "defer_async", boom_defer)

    r = client.post(f"/api/videos/{vid}/publish?platform=tiktok",
                    json={"account_id": aid})
    assert r.status_code == 200, r.text
    pid = r.json()["publication_id"]

    # Outbox row exists, NOT marked sent — dispatcher will retry.
    with _eng.begin() as _c:
        row = _c.execute(_t(
            "SELECT sent_at FROM outbox WHERE business_ref = :ref"
        ), {"ref": f"publication:{pid}"}).first()
    assert row is not None
    assert row.sent_at is None

    # Publication remains QUEUED — the outbox owns the retry semantics.
    with Session(engine) as sess:
        v = next(v for v in vr.list_all(sess) if v["id"] == vid)
    tt = next(p for p in v["publications"] if p["platform"] == "tiktok")
    assert tt["result"] == "QUEUED"
    assert tt["account_id"] == aid


def test_patch_video_account_stamps_phone_without_queueing(client, default_group_id):
    """PATCH /api/videos/{vid} bulk-edits pending publication assignment.

    QUEUED means a worker task has been deferred; PATCH does not defer
    anything, so it must leave PENDING publications PENDING for the next
    auto_publish_scan to claim.
    """
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    aid = client.post("/api/accounts", json={
        "profile_name": "UA",
        "group_id": default_group_id,
    }).json()["id"]
    client.post(
        "/api/videos/upload",
        data={"group_id": str(default_group_id)},
        files={"files": ("ua.mp4", b"x", "video/mp4")},
    )
    vid = client.get("/api/videos").json()["items"][0]["id"]
    # First assign — stamps the phone but does not QUEUE.
    r = client.patch(f"/api/videos/{vid}", json={"account_id": aid})
    assert r.status_code == 200
    # account_id and publications not in list envelope — query DB directly
    with Session(engine) as sess:
        row = next(v for v in vr.list_all(sess) if v["id"] == vid)
    assert row["account_id"] == aid
    tt = next(p for p in row["publications"] if p["platform"] == "tiktok")
    assert tt["account_id"] == aid
    assert tt["result"] == "PENDING"
    # Now un-assign with null — clears the phone and stays PENDING.
    r = client.patch(f"/api/videos/{vid}", json={"account_id": None})
    assert r.status_code == 200
    with Session(engine) as sess:
        row = next(v for v in vr.list_all(sess) if v["id"] == vid)
    assert row["account_id"] is None
    tt = next(p for p in row["publications"] if p["platform"] == "tiktok")
    assert tt["account_id"] is None
    assert tt["result"] == "PENDING"


def test_patch_video_with_null_account_no_existing_publication_is_noop(client):
    """PATCH with account_id=null must NOT spawn empty publication rows
    for platforms the video doesn't already have."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    client.post("/api/videos/upload",
                data={"platforms": "tiktok"},  # only tiktok publication created
                files={"files": ("np.mp4", b"x", "video/mp4")})
    vid = client.get("/api/videos").json()["items"][0]["id"]
    r = client.patch(f"/api/videos/{vid}", json={"account_id": None})
    assert r.status_code == 200
    # publications not in list envelope — query DB directly
    with Session(engine) as sess:
        row = next(v for v in vr.list_all(sess) if v["id"] == vid)
    platforms = sorted(p["platform"] for p in row["publications"])
    assert platforms == ["tiktok"]  # no YT/IG/FB publication was created


def test_list_videos_paginated_returns_envelope(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    # Upload 3 dummy videos
    for i in range(3):
        client.post("/api/videos/upload",
                    files={"files": (f"v{i}.mp4", b"x", "video/mp4")})
    r = client.get("/api/videos")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "next_cursor" in body
    assert "total" in body
    assert body["total"] == 3
    assert len(body["items"]) == 3
    # Items should NOT have full publications array — that lives on /detail.
    assert "publications" not in body["items"][0]
    assert "platforms" in body["items"][0]
    assert "summary" in body["items"][0]


def test_list_videos_cursor_pagination(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    for i in range(5):
        client.post("/api/videos/upload",
                    files={"files": (f"v{i}.mp4", b"x", "video/mp4")})
    r1 = client.get("/api/videos?limit=2")
    body1 = r1.json()
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None
    assert body1["total"] == 5

    r2 = client.get(f"/api/videos?limit=2&cursor={body1['next_cursor']}")
    body2 = r2.json()
    assert len(body2["items"]) == 2
    assert body2.get("total") is None  # total only on first page
    # No overlap with page 1
    ids1 = {r["id"] for r in body1["items"]}
    ids2 = {r["id"] for r in body2["items"]}
    assert ids1.isdisjoint(ids2)


def test_list_videos_group_ungrouped_filter(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    for i in range(2):
        client.post("/api/videos/upload",
                    files={"files": (f"v{i}.mp4", b"x", "video/mp4")})
    r = client.get("/api/videos?group_id=ungrouped")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2


def test_list_videos_status_filter_validates(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.get("/api/videos?status=garbage")
    assert r.status_code == 422


def test_list_videos_group_filter_validates(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.get("/api/videos?group_id=garbage")
    assert r.status_code == 422


def test_video_detail_returns_full_publications(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/videos/upload",
                    files={"files": ("v.mp4", b"x", "video/mp4")})
    assert r.status_code == 200
    list_body = client.get("/api/videos").json()
    vid = list_body["items"][0]["id"]

    r = client.get(f"/api/videos/{vid}/detail")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == vid
    assert "publications" in body
    # Default upload creates 1 tiktok publication.
    assert len(body["publications"]) >= 1
    assert "platform" in body["publications"][0]
    assert "caption" in body["publications"][0]


def test_video_detail_404_for_missing(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.get("/api/videos/99999/detail")
    assert r.status_code == 404


def test_video_detail_includes_fail_hint_for_known_code(client):
    """Publications with a known GeeLark fail_code expose a human-readable
    `fail_hint` in the detail response."""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    from app.repositories import publications as pub_repo

    r = client.post("/api/videos/upload",
                    files={"files": ("hint.mp4", b"x", "video/mp4")})
    assert r.status_code == 200
    list_body = client.get("/api/videos").json()
    vid = list_body["items"][0]["id"]

    with Session(engine) as sess:
        pid = pub_repo.find_or_create(sess, video_id=vid, platform="instagram")
        pub_repo.set_result(sess, pid, "FAILURE", "Reel is empty",
                            fail_code=20267)

    body = client.get(f"/api/videos/{vid}/detail").json()
    ig_pub = next(p for p in body["publications"]
                  if p["platform"] == "instagram")
    assert ig_pub["fail_code"] == 20267
    assert ig_pub["fail_hint"] is not None
    assert isinstance(ig_pub["fail_hint"], str)
    assert "Instagram" in ig_pub["fail_hint"]


def test_videos_list_includes_thumbnail_url_and_duration(client, sample_video):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.get("/api/videos?group=all&status=all")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    item = next(i for i in items if i["id"] == sample_video.id)
    assert "thumbnail_url" in item
    assert "duration_seconds" in item


def test_videos_list_includes_per_platform_phone(
        client, sample_video_with_publication):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.get("/api/videos?group=all&status=all")
    item = next(i for i in r.json()["items"]
                if i["id"] == sample_video_with_publication.id)
    plats = item["platforms"]
    assert any(p.get("phone_label") for p in plats.values()), plats


def test_videos_list_returns_summary_counts(client, sample_video):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.get("/api/videos?group=all&status=all")
    body = r.json()
    assert "summary_counts" in body
    sc = body["summary_counts"]
    assert sc.keys() >= {"total", "pending", "in_progress", "handled"}
    assert all(isinstance(v, int) for v in sc.values())


# ---------------------------------------------------------------------------
# 5-bucket derived-status coverage
# ---------------------------------------------------------------------------

def _seed_video_with_pubs(db, filename, pub_results):
    """Insert one Video plus one Publication per entry in pub_results
    (a list of (platform, result) tuples). Bypasses repo helpers so the
    forced ``result`` value isn't filtered through PENDING-only writes."""
    import sqlalchemy as sa
    from app.models import Publication, Video
    v = Video(original_filename=filename,
              stored_path=f"/tmp/{filename}")
    db.add(v)
    db.flush()
    for plat, res in pub_results:
        p = Publication(video_id=v.id, platform=plat, result="PENDING")
        db.add(p)
        db.flush()
        if res != "PENDING":
            db.execute(
                sa.text("UPDATE publications SET result = :r WHERE id = :pid"),
                {"r": res, "pid": p.id})
    db.commit()
    db.refresh(v)
    return v


def test_summary_counts_buckets_are_exhaustive(client, db):
    """Seed one video per bucket, then assert every bucket count is
    correct and that the three buckets sum to total. Mutually exclusive
    + exhaustive is the whole point of the new shape."""
    # handled: all SUCCESS
    _seed_video_with_pubs(db, "all-ok.mp4",
                          [("tiktok", "SUCCESS"), ("youtube", "SUCCESS")])
    # in_progress: any INITIATED/QUEUED
    _seed_video_with_pubs(db, "running.mp4",
                          [("tiktok", "INITIATED"), ("youtube", "SUCCESS")])
    # handled: SUCCESS + FAILURE mix
    _seed_video_with_pubs(db, "mixed.mp4",
                          [("tiktok", "SUCCESS"), ("youtube", "FAILURE")])
    # handled: all FAILURE
    _seed_video_with_pubs(db, "all-fail.mp4",
                          [("tiktok", "FAILURE"), ("youtube", "FAILURE")])
    # handled: FAILURE + PENDING (user's screenshot case)
    _seed_video_with_pubs(db, "fail-plus-pending.mp4",
                          [("tiktok", "FAILURE"), ("youtube", "PENDING")])
    # pending: only PENDING
    _seed_video_with_pubs(db, "pending.mp4",
                          [("tiktok", "PENDING")])
    # pending: no publications at all
    from app.models import Video
    v_empty = Video(original_filename="no-pubs.mp4",
                    stored_path="/tmp/no-pubs.mp4")
    db.add(v_empty)
    db.commit()

    client.post("/api/login", json={"username": "tester", "password": "pw"})
    body = client.get("/api/videos?group_id=all&status=all").json()
    sc = body["summary_counts"]
    assert sc["total"] == 7
    assert sc["pending"] == 2
    assert sc["in_progress"] == 1
    assert sc["handled"] == 4  # all-ok + mixed + all-fail + fail-plus-pending
    assert sc["pending"] + sc["in_progress"] + sc["handled"] == sc["total"]


def test_filter_by_in_progress_bucket(client, db):
    """The in_progress filter returns videos with ≥1 INITIATED or
    QUEUED publication — i.e., a phone is actively running a GeeLark
    task right now. PENDING is NOT in_progress; it's waiting to be
    picked up by the scheduler."""
    v_run = _seed_video_with_pubs(db, "running.mp4",
                                  [("tiktok", "INITIATED"),
                                   ("youtube", "SUCCESS")])
    v_qd = _seed_video_with_pubs(db, "queued.mp4",
                                 [("tiktok", "QUEUED")])
    # Not in_progress under 3-bucket model:
    _seed_video_with_pubs(db, "done.mp4", [("tiktok", "SUCCESS")])
    _seed_video_with_pubs(db, "failed.mp4", [("tiktok", "FAILURE")])
    _seed_video_with_pubs(db, "pending.mp4", [("tiktok", "PENDING")])
    _seed_video_with_pubs(db, "fail-plus-pending.mp4",
                          [("tiktok", "FAILURE"), ("youtube", "PENDING")])

    client.post("/api/login", json={"username": "tester", "password": "pw"})
    body = client.get("/api/videos?group_id=all&status=in_progress").json()
    ids = {it["id"] for it in body["items"]}
    assert ids == {v_run.id, v_qd.id}
    for it in body["items"]:
        results = {p["result"] for p in it["platforms"].values()}
        assert results & {"INITIATED", "QUEUED"}, it


def test_filter_by_handled_bucket(client, db):
    """The handled filter returns videos with no in-flight publications
    AND at least one SUCCESS or FAILURE. This covers:
      - all SUCCESS
      - all FAILURE
      - SUCCESS + FAILURE mix
      - SUCCESS + PENDING mix
      - FAILURE + PENDING mix (the user's "2 fail + 2 待发布" case)
    Anything with ≥1 INITIATED/QUEUED belongs to in_progress, not handled."""
    v_all_ok = _seed_video_with_pubs(db, "all-ok.mp4",
                                     [("tiktok", "SUCCESS")])
    v_all_fail = _seed_video_with_pubs(db, "all-fail.mp4",
                                       [("tiktok", "FAILURE")])
    v_mix = _seed_video_with_pubs(db, "mixed.mp4",
                                  [("tiktok", "SUCCESS"),
                                   ("youtube", "FAILURE")])
    v_succ_pend = _seed_video_with_pubs(db, "succ-plus-pend.mp4",
                                        [("tiktok", "SUCCESS"),
                                         ("youtube", "PENDING")])
    v_fail_pend = _seed_video_with_pubs(db, "fail-plus-pend.mp4",
                                        [("tiktok", "FAILURE"),
                                         ("youtube", "PENDING")])
    # Should NOT appear in handled:
    _seed_video_with_pubs(db, "running.mp4",
                          [("tiktok", "INITIATED"), ("youtube", "SUCCESS")])
    _seed_video_with_pubs(db, "all-pending.mp4",
                          [("tiktok", "PENDING")])

    client.post("/api/login", json={"username": "tester", "password": "pw"})
    body = client.get("/api/videos?group_id=all&status=handled").json()
    ids = {it["id"] for it in body["items"]}
    assert ids == {v_all_ok.id, v_all_fail.id, v_mix.id,
                   v_succ_pend.id, v_fail_pend.id}


# ---------------------------------------------------------------------------
# Task 5 — defer extract_understanding from /api/videos/upload
# ---------------------------------------------------------------------------

def test_video_upload_defers_understanding_task(
        client, monkeypatch, default_group_id, tmp_path):
    """Successful upload should defer one extract_understanding job per
    uploaded video. Patch the task object directly — same pattern used
    by tests/test_api_engagement.py for submit_engagement_run_task."""
    from app.tasks.writer_understanding import extract_understanding_task

    deferred: list[dict] = []

    async def fake_defer_async(**kwargs):
        deferred.append(kwargs)
        return type("Job", (), {"id": "job-x"})()

    monkeypatch.setattr(extract_understanding_task, "defer_async",
                        fake_defer_async)
    # Endpoint uses `procrastinate_app.configure_task(name=...).defer_async(...)`,
    # which resolves to the same task instance. Intercept that chain too.
    fake_deferrer = type("FakeDeferrer", (),
                        {"defer_async": staticmethod(fake_defer_async)})()
    from app.tasks import procrastinate_app
    monkeypatch.setattr(procrastinate_app, "configure_task",
                        lambda name, **kw: fake_deferrer)

    client.post("/api/login", json={"username": "tester", "password": "pw"})
    f = tmp_path / "u.mp4"
    f.write_bytes(b"\x00")
    with open(f, "rb") as fp:
        r = client.post(
            "/api/videos/upload",
            data={"group_id": str(default_group_id), "platforms": "tiktok"},
            files=[("files", ("u.mp4", fp, "video/mp4"))],
        )
    assert r.status_code == 200, r.text

    assert len(deferred) == 1
    assert deferred[0].get("video_id")
