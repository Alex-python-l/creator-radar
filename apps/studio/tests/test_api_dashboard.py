from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.api.dashboard import _today_counts, _today_window_utc
from app.models import Account, Group, Publication, Video


def test_today_window_returns_naive_utc_datetimes():
    start, end = _today_window_utc()
    assert start.tzinfo is None
    assert end.tzinfo is None
    assert end - start == timedelta(days=1)


def test_today_window_aligns_to_shanghai_midnight():
    start, end = _today_window_utc()
    tz = ZoneInfo("Asia/Shanghai")
    start_local = start.replace(tzinfo=timezone.utc).astimezone(tz)
    end_local = end.replace(tzinfo=timezone.utc).astimezone(tz)
    assert start_local.hour == 0 and start_local.minute == 0
    assert end_local.hour == 0 and end_local.minute == 0
    assert end_local.date() == start_local.date() + timedelta(days=1)


def _seed_video(db: Session, group_id: int) -> int:
    v = Video(original_filename="x.mp4", stored_path="x", group_id=group_id)
    db.add(v); db.commit(); db.refresh(v)
    return v.id


def _seed_group(db: Session) -> int:
    g = Group(name="G1"); db.add(g); db.commit(); db.refresh(g)
    return g.id


def _seed_pub(db: Session, video_id: int, *, result: str,
              posted_at: datetime | None = None,
              last_checked_at: datetime | None = None,
              platform: str = "tiktok") -> int:
    p = Publication(video_id=video_id, platform=platform, result=result,
                    posted_at=posted_at, last_checked_at=last_checked_at)
    db.add(p); db.commit(); db.refresh(p)
    return p.id


def test_today_done_counts_only_local_today(db):
    g = _seed_group(db)
    v1 = _seed_video(db, g); v2 = _seed_video(db, g); v3 = _seed_video(db, g)
    start, end = _today_window_utc()
    one_hour = timedelta(hours=1)
    _seed_pub(db, v1, result="SUCCESS", posted_at=start - one_hour, platform="tiktok")
    _seed_pub(db, v2, result="SUCCESS", posted_at=start + one_hour, platform="tiktok")
    _seed_pub(db, v3, result="SUCCESS", posted_at=end + one_hour, platform="tiktok")
    counts = _today_counts(db)
    assert counts == {"done": 1, "failed": 0, "in_progress": 0}


def test_today_done_counts_success_by_completion_time(db):
    g = _seed_group(db)
    v1 = _seed_video(db, g); v2 = _seed_video(db, g)
    start, end = _today_window_utc()
    _seed_pub(
        db,
        v1,
        result="SUCCESS",
        posted_at=start - timedelta(hours=2),
        last_checked_at=start + timedelta(hours=1),
    )
    _seed_pub(
        db,
        v2,
        result="SUCCESS",
        posted_at=start + timedelta(hours=1),
        last_checked_at=end + timedelta(hours=1),
    )
    counts = _today_counts(db)
    assert counts == {"done": 1, "failed": 0, "in_progress": 0}


def test_today_failed_counts_uses_last_checked_at(db):
    g = _seed_group(db)
    v1 = _seed_video(db, g); v2 = _seed_video(db, g)
    start, _ = _today_window_utc()
    _seed_pub(db, v1, result="FAILURE",
              last_checked_at=start + timedelta(hours=2), platform="tiktok")
    _seed_pub(db, v2, result="FAILURE",
              last_checked_at=start - timedelta(hours=2), platform="tiktok")
    counts = _today_counts(db)
    assert counts == {"done": 0, "failed": 1, "in_progress": 0}


def test_today_in_progress_has_no_date_filter(db):
    g = _seed_group(db)
    v1 = _seed_video(db, g); v2 = _seed_video(db, g); v3 = _seed_video(db, g)
    _seed_pub(db, v1, result="QUEUED", platform="tiktok")
    _seed_pub(db, v2, result="INITIATED", platform="tiktok")
    _seed_pub(db, v3, result="PENDING", platform="tiktok")
    counts = _today_counts(db)
    assert counts == {"done": 0, "failed": 0, "in_progress": 2}


from app.api.dashboard import _account_counts


def _seed_account(db: Session, *, name: str, warmed_up: int = 0,
                  warmup_count: int = 0, target: int = 10,
                  phone_id: str | None = None) -> int:
    a = Account(profile_name=name, warmed_up=warmed_up,
                warmup_count=warmup_count, target_warmup_count=target,
                geelark_phone_id=phone_id)
    db.add(a); db.commit(); db.refresh(a)
    return a.id


def test_account_counts_partition(db):
    _seed_account(db, name="ready1", warmed_up=1, warmup_count=10)
    _seed_account(db, name="ready2", warmed_up=1, warmup_count=10)
    _seed_account(db, name="ready3", warmed_up=1, warmup_count=10)
    _seed_account(db, name="warm1", warmed_up=0, warmup_count=3)
    _seed_account(db, name="warm2", warmed_up=0, warmup_count=5)
    _seed_account(db, name="cold1", warmed_up=0, warmup_count=0)
    _seed_account(db, name="cold2", warmed_up=0, warmup_count=0)
    counts = _account_counts(db)
    assert counts == {"total": 7, "ready": 3, "warming": 2, "not_started": 2}


from app.api.dashboard import _video_counts


def test_video_counts_unscheduled_excludes_videos_with_any_publication(db):
    g = _seed_group(db)
    v_scheduled = _seed_video(db, g)
    v_unscheduled_1 = _seed_video(db, g)
    v_unscheduled_2 = _seed_video(db, g)
    _seed_pub(db, v_scheduled, result="PENDING", platform="tiktok")
    counts = _video_counts(db)
    assert counts == {"unscheduled": 2, "total": 3}
    _ = (v_unscheduled_1, v_unscheduled_2)  # suppress unused


from app.api.dashboard import _exceptions


def test_exception_publication_failed_row(db):
    g = _seed_group(db)
    a = _seed_account(db, name="acc1", phone_id="ph1")
    v = _seed_video(db, g)
    pub = Publication(video_id=v, platform="tiktok", result="FAILURE",
                      account_id=a, notes="captcha required",
                      last_checked_at=datetime.utcnow())
    db.add(pub); db.commit(); db.refresh(pub)
    out = _exceptions(db)
    exc = next(e for e in out["items"] if e["kind"] == "publication_failed")
    assert exc["severity"] == "red"
    assert exc["publication_id"] == pub.id
    assert exc["account"] == "acc1"
    assert exc["platform"] == "tiktok"
    assert exc["reason"] == "captcha required"
    assert out["total"] >= 1


def test_exception_proxy_down_only_when_status_fail(db):
    a_fail = Account(profile_name="bad", proxy_status="fail",
                     proxy_checked_at=datetime.utcnow())
    a_ok = Account(profile_name="good", proxy_status="ok",
                   proxy_checked_at=datetime.utcnow())
    a_null = Account(profile_name="never_checked", proxy_status=None)
    db.add_all([a_fail, a_ok, a_null]); db.commit()
    out = _exceptions(db)
    kinds = [e["kind"] for e in out["items"]]
    profiles = [e["account"] for e in out["items"] if e["kind"] == "proxy_down"]
    assert "proxy_down" in kinds
    assert profiles == ["bad"]


def test_exception_not_logged_in_requires_phone_id(db):
    a_phone_no_login = Account(profile_name="needs_login",
                               geelark_phone_id="ph1",
                               tiktok_logged_in=0)
    a_no_phone = Account(profile_name="no_phone",
                         geelark_phone_id=None,
                         tiktok_logged_in=0)
    a_logged = Account(profile_name="ok",
                       geelark_phone_id="ph2",
                       tiktok_logged_in=1)
    db.add_all([a_phone_no_login, a_no_phone, a_logged]); db.commit()
    out = _exceptions(db)
    profiles = [e["account"] for e in out["items"] if e["kind"] == "not_logged_in"]
    assert profiles == ["needs_login"]


def test_exception_proxy_expiring_severity_buckets(db):
    now = datetime.utcnow()
    a_expired = Account(profile_name="exp_already",
                        proxy="socks5://x",
                        proxy_expires_at=now - timedelta(days=1))
    a_soon = Account(profile_name="exp_2d",
                     proxy="socks5://x",
                     proxy_expires_at=now + timedelta(days=2))
    a_week = Account(profile_name="exp_5d",
                     proxy="socks5://x",
                     proxy_expires_at=now + timedelta(days=5))
    a_safe = Account(profile_name="exp_safe",
                     proxy="socks5://x",
                     proxy_expires_at=now + timedelta(days=20))
    a_null = Account(profile_name="legacy_no_expiry",
                     proxy="socks5://x",
                     proxy_expires_at=None)
    db.add_all([a_expired, a_soon, a_week, a_safe, a_null]); db.commit()

    out = _exceptions(db)
    by_profile = {e["account"]: e for e in out["items"]
                  if e["kind"] == "proxy_expiring"}
    # Three rows in the warning window; the far-future row and the
    # NULL-expiry legacy row stay silent.
    assert set(by_profile.keys()) == {"exp_already", "exp_2d", "exp_5d"}
    assert by_profile["exp_already"]["severity"] == "red"
    assert by_profile["exp_2d"]["severity"] == "orange"
    assert by_profile["exp_5d"]["severity"] == "yellow"
    assert by_profile["exp_already"]["days_remaining"] <= 0


def test_exception_warmup_stuck_requires_24h_since_last_warm(db):
    now = datetime.utcnow()
    a_stuck = Account(profile_name="stuck", warmed_up=0, warmup_count=2,
                      target_warmup_count=10,
                      last_warm_at=now - timedelta(hours=25))
    a_recent = Account(profile_name="recent", warmed_up=0, warmup_count=2,
                       target_warmup_count=10,
                       last_warm_at=now - timedelta(hours=10))
    a_done = Account(profile_name="done", warmed_up=1, warmup_count=10,
                     target_warmup_count=10,
                     last_warm_at=now - timedelta(hours=30))
    a_never = Account(profile_name="never", warmed_up=0, warmup_count=0,
                      target_warmup_count=10, last_warm_at=None)
    db.add_all([a_stuck, a_recent, a_done, a_never]); db.commit()
    out = _exceptions(db)
    profiles = [e["account"] for e in out["items"]
                if e["kind"] == "warmup_stuck"]
    assert profiles == ["stuck"]


def test_exception_severity_order_and_cap(db):
    g = _seed_group(db)
    now = datetime.utcnow()
    # 2 reds
    for i in range(2):
        v = _seed_video(db, g)
        db.add(Publication(video_id=v, platform="tiktok", result="FAILURE",
                           notes=f"red{i}", last_checked_at=now))
    # 2 oranges (proxy_down)
    for i in range(2):
        db.add(Account(profile_name=f"orange{i}", proxy_status="fail",
                       proxy_checked_at=now))
    # 2 yellows
    for i in range(2):
        db.add(Account(profile_name=f"yellow{i}", warmed_up=0, warmup_count=1,
                       target_warmup_count=10,
                       last_warm_at=now - timedelta(hours=30)))
    db.commit()
    out = _exceptions(db)
    severities = [e["severity"] for e in out["items"]]
    assert severities == ["red", "red", "orange", "orange", "yellow"]
    assert len(out["items"]) == 5
    assert out["total"] == 6


def test_dashboard_endpoint_returns_full_shape(client, monkeypatch):
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "today", "exceptions", "exceptions_total", "accounts", "videos",
        "database",
    }
    assert set(body["today"].keys()) == {"done", "failed", "in_progress"}
    assert set(body["accounts"].keys()) == {
        "total", "ready", "warming", "not_started"
    }
    assert set(body["videos"].keys()) == {"unscheduled", "total"}
    assert set(body["database"].keys()) == {
        "query_ms", "size_bytes", "connections", "active_queries",
        "locks", "tables",
    }
    assert body["database"]["query_ms"] >= 0
    assert body["database"]["size_bytes"] > 0
    assert body["database"]["tables"] >= 0


def test_dashboard_partial_failure_isolates_to_one_region(client, monkeypatch):
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    from app.api import dashboard as dash_mod

    def boom(_session):
        raise RuntimeError("simulated")

    monkeypatch.setattr(dash_mod, "_exceptions", boom)
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["exceptions"] == []
    assert body["exceptions_total"] == 0
    # Other regions still computed
    assert body["today"] == {"done": 0, "failed": 0, "in_progress": 0}
    assert body["accounts"]["total"] == 0
    assert body["database"]["query_ms"] >= 0


def test_exception_summary_endpoint_uses_latest_snapshot(client, db, monkeypatch):
    from app.api import dashboard as dash_mod

    db.add(Account(
        profile_name="proxy-bad",
        proxy_status="fail",
        proxy_checked_at=datetime.utcnow(),
    ))
    db.commit()
    captured = {}

    def fake_generate(snapshot, **kwargs):
        captured["snapshot"] = snapshot
        captured["kwargs"] = kwargs
        return "当前有 1 个代理异常，请优先更换代理。"

    monkeypatch.setattr(dash_mod, "_generate_exception_summary", fake_generate)
    client.post("/api/login", json={"username": "tester", "password": "pw"})

    response = client.post("/api/dashboard/exceptions/summary")

    assert response.status_code == 200
    assert response.json() == {"summary": "当前有 1 个代理异常，请优先更换代理。"}
    assert captured["snapshot"]["total"] == 1
    assert captured["snapshot"]["counts"] == {
        "publication_failed": 0,
        "proxy_down": 1,
        "not_logged_in": 0,
        "proxy_expiring": 0,
        "warmup_stuck": 0,
    }
    assert captured["kwargs"] == {
        "model": None,
        "thinking_budget": 0,
        "api_key": None,
    }


def test_exception_summary_endpoint_rejects_empty_snapshot(client, monkeypatch):
    from app.api import dashboard as dash_mod

    def should_not_generate(*args, **kwargs):
        raise AssertionError("Gemini should not be called without exceptions")

    monkeypatch.setattr(dash_mod, "_generate_exception_summary", should_not_generate)
    client.post("/api/login", json={"username": "tester", "password": "pw"})

    response = client.post("/api/dashboard/exceptions/summary")

    assert response.status_code == 400
    assert response.json()["detail"] == "no exceptions to summarize"


def test_generate_exception_summary_uses_configured_gemini(monkeypatch):
    import generate_caption
    from app.api.dashboard import _generate_exception_summary

    sentinel_client = object()
    captured = {}

    def fake_client(api_key=None):
        captured["client_args"] = [api_key]
        return sentinel_client

    monkeypatch.setattr(generate_caption, "_get_client", fake_client)

    class Response:
        text = "  共有 2 个异常，请先处理发布失败。  "

    def fake_generate(client, **kwargs):
        captured["client"] = client
        captured["generation"] = kwargs
        return Response()

    monkeypatch.setattr(generate_caption, "_generate_content_with_retry", fake_generate)
    snapshot = {
        "total": 2,
        "counts": {"publication_failed": 2},
        "items": [{"kind": "publication_failed", "reason": "captcha"}],
    }

    result = _generate_exception_summary(
        snapshot,
        model="gemini-test",
        thinking_budget=4096,
        api_key="secret-key",
    )

    assert result == "共有 2 个异常，请先处理发布失败。"
    assert captured["client_args"] == ["secret-key"]
    assert captured["client"] is sentinel_client
    assert captured["generation"]["model"] == "gemini-test"
    assert captured["generation"]["config"]["thinking_config"] == {
        "thinking_budget": 4096,
    }
    assert '"total": 2' in captured["generation"]["contents"][0]["parts"][0]["text"]


def test_phones_stop_all_endpoint_is_removed(client, monkeypatch):
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/phones/stop-all")
    # The route is gone. Depending on whether the SPA fallback (a GET-only
    # `/{full_path:path}` route) is mounted in test, FastAPI returns either
    # 404 (no route at all) or 405 (the fallback matches the path but only
    # for GET). Either is proof that the explicit POST endpoint is gone;
    # what we MUST NOT see is 200.
    assert r.status_code in (404, 405)
