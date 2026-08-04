import pytest

from app.services import geelark_service
from app.services import posting_service

class FakeClient:
    def __init__(self):
        self.stopped = False
    def list_phones(self, page=1, page_size=100):
        return {"total": 1, "items": [
            {"id": "ph1", "serialName": "R1", "group": {"name": "G1"}}]}
    def stop_phones(self, ids):
        self.stopped = True
        return {"successDetails": [{"id": i} for i in ids]}

def test_import_from_geelark_upserts(db):
    fake = FakeClient()
    n = geelark_service.import_from_geelark(db, fake)
    from app.repositories import accounts as acc_repo
    rows = acc_repo.list_all(db)
    assert n == 1
    assert rows[0]["geelark_phone_id"] == "ph1"
    assert rows[0]["profile_name"] == "R1"
    assert rows[0]["group_name"] == "G1"

def test_import_skips_records_without_phone_id(db):
    class NoIdClient:
        def list_phones(self, page=1, page_size=100):
            return {"items": [
                {"serialName": "ghost", "group": {"name": "G"}},
                {"id": "ph2", "serialName": "real", "group": {"name": "G"}}]}
    from app.repositories import accounts as acc_repo
    n = geelark_service.import_from_geelark(db, NoIdClient())
    rows = acc_repo.list_all(db)
    assert n == 1
    assert len(rows) == 1
    assert rows[0]["geelark_phone_id"] == "ph2"

def test_import_from_geelark_sets_group_id(db):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as grp_repo

    class FakeListClient:
        def list_phones(self, page=1, page_size=100):
            if page > 1:
                return {"items": []}
            return {"items": [
                {"id": "ph-1", "serialName": "M1",
                 "group": {"name": "imported-batch"}},
            ]}

    n = geelark_service.import_from_geelark(db, FakeListClient())
    assert n == 1
    # Check group was created + account linked via group_id
    from sqlalchemy import text
    row = db.execute(text(
        "SELECT group_id, group_name FROM accounts WHERE geelark_phone_id='ph-1'"
    )).fetchone()
    assert row[1] == "imported-batch"   # group_name
    assert row[0] is not None           # group_id
    g = db.execute(text(
        "SELECT name FROM groups WHERE id=:gid"
    ), {"gid": row[0]}).fetchone()
    assert g[0] == "imported-batch"

class FakeCreateClient:
    def __init__(self):
        self.created = []
    def create_phones(self, entries):
        self.created += entries
        return {"details": [{"id": "newph1", "serialName": entries[0]["serialName"]}]}
    def get_installable_apps(self, env_id, name=""):
        return {"items": [{"appVersionId": "tv1", "appName": "TikTok"}]}
    def install_app(self, env_id, app_version_id):
        return {}

def test_check_initiated_success_clears_file(db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    aid = acc_repo.create(db, {"profile_name": "R1",
                               "geelark_phone_id": "ph1"})
    f = tmp_path / "s.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "s.mp4", str(f))
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(db, pid, "cap", "tk1", "submitted")
    class DoneClient:
        def query_tasks(self, ids): return [{"id": ids[0], "status": 3}]
    summary = posting_service.check_initiated(db, DoneClient())
    p = pub_repo.get(db, pid)
    assert p["result"] == "SUCCESS"
    v = vid_repo.get(db, vid)
    assert v["stored_path"] is None
    assert not f.exists()
    assert summary == "1 success, 0 failure"


def test_check_initiated_failure_and_not_found(db):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    aid = acc_repo.create(db, {"profile_name": "R2",
                               "geelark_phone_id": "ph2"})
    v_fail = vid_repo.create(db, "f.mp4", "/x/f.mp4")
    vid_repo.set_account(db, v_fail, aid)
    p_fail = pub_repo.create(db, video_id=v_fail, platform="tiktok")
    pub_repo.mark_initiated(db, p_fail, "c", "tkF", "s")
    v_missing = vid_repo.create(db, "m.mp4", "/x/m.mp4")
    vid_repo.set_account(db, v_missing, aid)
    p_missing = pub_repo.create(db, video_id=v_missing, platform="tiktok")
    pub_repo.mark_initiated(db, p_missing, "c", "tkM", "s")
    class MixedClient:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 4}] if ids == ["tkF"] else []
    summary = posting_service.check_initiated(db, MixedClient())
    fail_row = pub_repo.get(db, p_fail)
    assert fail_row["result"] == "PENDING"
    assert fail_row["retry_count"] == 1
    assert fail_row["next_retry_at"] is not None
    missing_row = pub_repo.get(db, p_missing)
    assert missing_row["result"] == "PENDING"
    assert missing_row["retry_count"] == 1
    assert missing_row["next_retry_at"] is not None
    fail_attempts = pub_repo.list_attempts(db, p_fail)
    assert len(fail_attempts) == 1
    assert fail_attempts[0]["source"] == "geelark"
    assert fail_attempts[0]["stage"] == "status_check"
    assert fail_attempts[0]["message"] == "task status 4"
    assert fail_attempts[0]["geelark_task_id"] == "tkF"
    missing_attempts = pub_repo.list_attempts(db, p_missing)
    assert len(missing_attempts) == 1
    assert missing_attempts[0]["source"] == "geelark"
    assert missing_attempts[0]["stage"] == "status_check"
    assert missing_attempts[0]["message"] == "task not found"
    assert missing_attempts[0]["geelark_task_id"] == "tkM"
    assert summary == "0 success, 2 failure"

def test_format_proxy_builds_url_from_geelark_object():
    obj = {"type": "socks5", "server": "99.kookeey.info", "port": 20763,
           "username": "984a4549", "password": "6eca3dd4"}
    assert geelark_service._format_proxy(obj) == \
        "socks5://984a4549:6eca3dd4@99.kookeey.info:20763"


def test_format_proxy_handles_none_and_empty():
    assert geelark_service._format_proxy(None) == ""
    assert geelark_service._format_proxy({}) == ""
    # missing server -> unusable -> empty
    assert geelark_service._format_proxy({"type": "socks5", "port": 1080}) == ""
    # missing port -> unusable -> empty
    assert geelark_service._format_proxy(
        {"type": "socks5", "server": "h.example"}) == ""


def test_format_proxy_omits_credentials_when_missing():
    obj = {"type": "http", "server": "h.example", "port": 8080}
    assert geelark_service._format_proxy(obj) == "http://h.example:8080"


def test_format_proxy_defaults_scheme_to_socks5():
    obj = {"server": "h.example", "port": 1234,
           "username": "u", "password": "p"}
    assert geelark_service._format_proxy(obj) == "socks5://u:p@h.example:1234"


def test_import_from_geelark_writes_proxy_url(db):
    class ProxyClient:
        def list_phones(self, page=1, page_size=100):
            return {"items": [{"id": "pP", "serialName": "P1",
                "group": {"name": "G"},
                "proxy": {"type": "socks5", "server": "h.example",
                          "port": 20763, "username": "u9", "password": "p9"}}]}
    from app.repositories import accounts as acc_repo
    geelark_service.import_from_geelark(db, ProxyClient())
    assert acc_repo.list_all(db)[0]["proxy"] == \
        "socks5://u9:p9@h.example:20763"


def test_import_from_geelark_preserves_manually_edited_proxy(db):
    from app.repositories import accounts as acc_repo
    # User imported once, then manually edited the proxy
    aid = acc_repo.create(db, {"profile_name": "EDITED", "geelark_phone_id": "pE",
                               "proxy": "socks5://hand:tuned@my.host:1080"})
    class ReimportClient:
        def list_phones(self, page=1, page_size=100):
            return {"items": [{"id": "pE", "serialName": "EDITED",
                "group": {"name": "G"},
                "proxy": {"type": "socks5", "server": "geelark.host",
                          "port": 9999, "username": "x", "password": "y"}}]}
    geelark_service.import_from_geelark(db, ReimportClient())
    assert acc_repo.get(db, aid)["proxy"] == "socks5://hand:tuned@my.host:1080"


def test_import_from_geelark_fills_empty_proxy(db):
    from app.repositories import accounts as acc_repo
    # Earlier buggy import left proxy as empty string
    aid = acc_repo.create(db, {"profile_name": "EMPTY", "geelark_phone_id": "pX",
                               "proxy": ""})
    class FillClient:
        def list_phones(self, page=1, page_size=100):
            return {"items": [{"id": "pX", "serialName": "EMPTY",
                "group": {"name": "G"},
                "proxy": {"type": "socks5", "server": "h.example",
                          "port": 1234, "username": "u", "password": "p"}}]}
    geelark_service.import_from_geelark(db, FillClient())
    assert acc_repo.get(db, aid)["proxy"] == "socks5://u:p@h.example:1234"


def test_import_from_geelark_disambiguates_duplicate_names(db):
    class DupClient:
        def list_phones(self, page=1, page_size=100):
            return {"total": 3, "items": [
                {"id": "p1", "serialName": "R23", "group": {"name": "G"}},
                {"id": "p2", "serialName": "R23", "group": {"name": "G"}},
                {"id": "p3", "serialName": "R23", "group": {"name": "G"}}]}
    from app.repositories import accounts as acc_repo
    n = geelark_service.import_from_geelark(db, DupClient())
    rows = acc_repo.list_all(db)
    assert n == 3 and len(rows) == 3
    assert sorted(r["profile_name"] for r in rows) == ["R23", "R23 (2)", "R23 (3)"]


def test_check_initiated_keeps_file_until_all_publications_terminal(db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    aid = acc_repo.create(db, {"profile_name": "MX",
                               "geelark_phone_id": "phMX"})
    f = tmp_path / "mx.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "mx.mp4", str(f))
    vid_repo.set_account(db, vid, aid)
    pids = {}
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        pid = pub_repo.create(db, video_id=vid, platform=plat)
        pub_repo.mark_initiated(db, pid, "c", f"t-{plat}", "s")
        pids[plat] = pid

    class R1:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 3 if ids[0] == "t-tiktok" else 2}]
    posting_service.check_initiated(db, R1())
    assert pub_repo.get(db, pids["tiktok"])["result"] == "SUCCESS"
    assert f.exists()
    assert vid_repo.get(db, vid)["stored_path"] is not None

    class R2:
        def query_tasks(self, ids): return [{"id": ids[0], "status": 3}]
    posting_service.check_initiated(db, R2())
    for plat in ("youtube", "instagram", "facebook"):
        assert pub_repo.get(db, pids[plat])["result"] == "SUCCESS"
    assert not f.exists()
    assert vid_repo.get(db, vid)["stored_path"] is None


def test_plan_assignments_marks_queued_and_returns_pids(db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import groups as gr

    gid = gr.create(db, name="g")
    aid = acc_repo.create(db, {"profile_name": "PA",
                               "geelark_phone_id": "phPA",
                               "group_id": gid,
                               "warmed_up": 1,
                               "youtube_logged_in": 1})
    f = tmp_path / "p.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "p.mp4", str(f), group_id=gid)
    pid = pub_repo.create(db, video_id=vid, platform="youtube")
    out = posting_service.plan_assignments(db, platform="youtube")
    assert out == [pid]
    row = pub_repo.get(db, pid)
    assert row["result"] == "QUEUED"
    assert row["account_id"] == aid
    assert vid_repo.get(db, vid)["account_id"] is None


def test_plan_assignments_limit_caps_candidate_batch(db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import groups as gr

    gid = gr.create(db, name="limited-plan")
    for idx in range(3):
        acc_repo.create(db, {
            "profile_name": f"limited-{idx}",
            "geelark_phone_id": f"ph-limited-{idx}",
            "group_id": gid,
            "warmed_up": 1,
            "youtube_logged_in": 1,
        })
        f = tmp_path / f"limited-{idx}.mp4"
        f.write_bytes(b"x")
        vid = vid_repo.create(db, f"limited-{idx}.mp4", str(f),
                              group_id=gid)
        pub_repo.create(db, video_id=vid, platform="youtube")

    out = posting_service.plan_assignments(db, platform="youtube", limit=2)

    assert len(out) == 2
    queued = [
        p for p in db.exec(__import__("sqlalchemy").text(
            "SELECT result FROM publications ORDER BY id")).all()
        if p[0] == "QUEUED"
    ]
    assert len(queued) == 2


def test_plan_assignments_skips_publication_before_retry_time(db, tmp_path):
    from datetime import datetime, timedelta
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import groups as gr

    gid = gr.create(db, name="retry-wait")
    acc_repo.create(db, {"profile_name": "retry-wait-phone",
                         "geelark_phone_id": "ph-retry-wait",
                         "group_id": gid,
                         "warmed_up": 1,
                         "tiktok_logged_in": 1})
    f = tmp_path / "retry-wait.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(db, "retry-wait.mp4", str(f), group_id=gid)
    pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    pub_repo.set_result(db, pid, "FAILURE", "upload failed")
    pub_repo.schedule_retry(
        db,
        pid,
        notes="upload failed",
        next_retry_at=datetime.utcnow() + timedelta(minutes=5),
    )

    assert posting_service.plan_assignments(db, platform="tiktok") == []
    assert pub_repo.get(db, pid)["result"] == "PENDING"


def test_plan_assignments_due_retry_bypasses_post_cooldown(db, tmp_path):
    from datetime import datetime, timedelta
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import groups as gr

    gid = gr.create(db, name="retry-due")
    aid = acc_repo.create(db, {"profile_name": "retry-due-phone",
                               "geelark_phone_id": "ph-retry-due",
                               "group_id": gid,
                               "warmed_up": 1,
                               "tiktok_logged_in": 1,
                               "last_posted_at": datetime.utcnow()})
    f = tmp_path / "retry-due.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(db, "retry-due.mp4", str(f), group_id=gid)
    pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    pub_repo.set_account(db, pid, aid)
    pub_repo.set_result(db, pid, "FAILURE", "upload failed")
    pub_repo.schedule_retry(
        db,
        pid,
        notes="upload failed",
        next_retry_at=datetime.utcnow() - timedelta(seconds=1),
    )

    assert posting_service.plan_assignments(
        db, platform="tiktok", cooldown_hours=2) == [pid]
    assert pub_repo.get(db, pid)["result"] == "QUEUED"


def test_plan_assignments_skips_drafts_and_cross_group_accounts(db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as gr
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    g1 = gr.create(db, name="g1")
    g2 = gr.create(db, name="g2")
    acc_repo.create(db, {"profile_name": "wrong-group",
                         "geelark_phone_id": "ph-wrong",
                         "group_id": g2,
                         "warmed_up": 1,
                         "tiktok_logged_in": 1})
    f1 = tmp_path / "draft.mp4"; f1.write_bytes(b"x")
    draft_vid = vid_repo.create(db, "draft.mp4", str(f1))
    draft_pid = pub_repo.create(db, video_id=draft_vid, platform="tiktok")
    f2 = tmp_path / "g1.mp4"; f2.write_bytes(b"x")
    g1_vid = vid_repo.create(db, "g1.mp4", str(f2), group_id=g1)
    g1_pid = pub_repo.create(db, video_id=g1_vid, platform="tiktok")

    assert posting_service.plan_assignments(db, platform="tiktok") == []
    assert pub_repo.get(db, draft_pid)["result"] == "PENDING"
    assert pub_repo.get(db, g1_pid)["result"] == "PENDING"

    right = acc_repo.create(db, {"profile_name": "right-group",
                                 "geelark_phone_id": "ph-right",
                                 "group_id": g1,
                                 "warmed_up": 1,
                                 "tiktok_logged_in": 1})
    assert posting_service.plan_assignments(db, platform="tiktok") == [g1_pid]
    assert pub_repo.get(db, g1_pid)["result"] == "QUEUED"
    assert pub_repo.get(db, g1_pid)["account_id"] == right
    assert vid_repo.get(db, g1_vid)["account_id"] is None
    assert pub_repo.get(db, draft_pid)["result"] == "PENDING"


def test_submit_publication_rejects_cross_group_assigned_account(db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as gr
    from app.repositories import publications as pub_repo
    from app.repositories import videos as vid_repo

    g1 = gr.create(db, name="submit-g1")
    g2 = gr.create(db, name="submit-g2")
    acc = acc_repo.create(db, {
        "profile_name": "wrong-submit",
        "geelark_phone_id": "ph-wrong-submit",
        "group_id": g2,
        "warmed_up": 1,
        "tiktok_logged_in": 1,
    })
    f = tmp_path / "cross-submit.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(db, "cross-submit.mp4", str(f), group_id=g1)
    pid = pub_repo.find_or_create(db, video_id=vid, platform="tiktok")
    pub_repo.set_account(db, pid, acc)
    pub_repo.mark_queued(db, pid)

    class NoUploadClient:
        def upload_file(self, *_args, **_kwargs):
            raise AssertionError("cross-group publication must not upload")

    with pytest.raises(RuntimeError, match="video's group"):
        posting_service.submit_publication_to_geelark(
            db,
            NoUploadClient(),
            publication_id=pid,
        )
    pub = pub_repo.get(db, pid)
    assert pub["result"] == "FAILURE"
    attempts = pub_repo.list_attempts(db, pid)
    assert attempts[-1]["stage"] == "preflight"
    assert "video's group" in attempts[-1]["message"]


def test_business_finalizers_do_not_close_manual_hold_session(db):
    from app.repositories import accounts as acc_repo
    from app.services import engagement_service, phone_runtime, warmup_service

    aid = acc_repo.create(
        db,
        {"profile_name": "held", "geelark_phone_id": "ph-held"},
    )
    sid = phone_runtime.start_session(
        db,
        account_id=aid,
        env_id="ph-held",
        source="manual",
        owner_type="account",
        owner_id=aid,
        budget_seconds=3600,
        created_by="operator",
    )

    posting_service._finalize_session(
        db, aid, kind="failure", owner_id=123, reason="wrong owner",
    )
    warmup_service._finalize_session(
        db, aid, kind="failure", reason="wrong owner",
    )
    engagement_service._finalize_session(
        db, aid, kind="failure", owner_id=456, reason="wrong owner",
    )

    active = phone_runtime.get_active_for_account(db, aid)
    assert active is not None
    assert active["id"] == sid
    assert active["status"] == "ACTIVE"


def test_plan_assignments_requires_existing_phone_login_for_platform(
        db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as gr
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    gid = gr.create(db, name="g")
    aid = acc_repo.create(db, {"profile_name": "TT-only",
                               "geelark_phone_id": "ph-tt",
                               "group_id": gid,
                               "warmed_up": 1,
                               "tiktok_logged_in": 1,
                               "youtube_logged_in": 0})
    f = tmp_path / "yt.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "yt.mp4", str(f), group_id=gid)
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="youtube")

    assert posting_service.plan_assignments(db, platform="youtube") == []
    assert pub_repo.get(db, pid)["result"] == "PENDING"


def test_plan_assignments_skips_groups_with_no_eligible_account_for_platform(
        db, tmp_path):
    """The 2026-06-16 silent-stall (fb/yt) shape: group A has tons of fb
    PENDINGs but no fb-logged-in account, so it occupies the id-ASC LIMIT
    window forever; group B (newer ids) has fb work AND a fb-logged-in
    account but never gets seen. Fix: plan_assignments computes the set of
    groups with at least one platform-eligible account and pushes that into
    list_pending's SQL via in_groups, so the LIMIT budget lands on
    actually-assignable work."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.repositories import groups as gr

    # Group A: tiktok-only accounts (simulates R/O groups in prod).
    g_a = gr.create(db, name="A")
    for idx in range(2):
        acc_repo.create(db, {"profile_name": f"A-{idx}",
                             "geelark_phone_id": f"ph-A-{idx}",
                             "group_id": g_a,
                             "warmed_up": 1,
                             "tiktok_logged_in": 1,
                             "facebook_logged_in": 0})

    # Group B (created later → larger id) has an fb-logged-in account.
    g_b = gr.create(db, name="B")
    acc_repo.create(db, {"profile_name": "B-fb",
                         "geelark_phone_id": "ph-B-fb",
                         "group_id": g_b,
                         "warmed_up": 1,
                         "tiktok_logged_in": 1,
                         "facebook_logged_in": 1})

    # Group A's fb publications come first in id order.
    blocked_pids = []
    for idx in range(3):
        f = tmp_path / f"a{idx}.mp4"; f.write_bytes(b"x")
        vid = vid_repo.create(db, f"a{idx}.mp4", str(f), group_id=g_a)
        blocked_pids.append(
            pub_repo.create(db, video_id=vid, platform="facebook"))

    # Group B's fb publication is newest (largest id).
    f = tmp_path / "b.mp4"; f.write_bytes(b"x")
    vid_b = vid_repo.create(db, "b.mp4", str(f), group_id=g_b)
    target_pid = pub_repo.create(db, video_id=vid_b, platform="facebook")

    # limit=1 must reach B's pub even though A's 3 fb pubs sit ahead by id.
    out = posting_service.plan_assignments(db, platform="facebook",
                                           limit=1)
    assert out == [target_pid]
    assert pub_repo.get(db, target_pid)["result"] == "QUEUED"
    for pid in blocked_pids:
        assert pub_repo.get(db, pid)["result"] == "PENDING"


def test_plan_assignments_only_requires_login_for_current_platform(
        db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as gr
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    gid = gr.create(db, name="g")
    tiktok_only = acc_repo.create(db, {"profile_name": "TT-only",
                                       "geelark_phone_id": "ph-tt",
                                       "group_id": gid,
                                       "warmed_up": 1,
                                       "tiktok_logged_in": 1,
                                       "youtube_logged_in": 0})
    acc_repo.create(db, {"profile_name": "TT-YT",
                         "geelark_phone_id": "ph-all",
                         "group_id": gid,
                         "warmed_up": 1,
                         "tiktok_logged_in": 1,
                         "youtube_logged_in": 1})
    f = tmp_path / "multi.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "multi.mp4", str(f), group_id=gid)
    tt_pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    yt_pid = pub_repo.create(db, video_id=vid, platform="youtube")

    assert posting_service.plan_assignments(db, platform="tiktok") == [tt_pid]
    assert pub_repo.get(db, tt_pid)["account_id"] == tiktok_only
    assert vid_repo.get(db, vid)["account_id"] is None
    assert pub_repo.get(db, yt_pid)["result"] == "PENDING"


def test_plan_assignments_assigns_each_platform_independently(
        db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as gr
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    gid = gr.create(db, name="g")
    tiktok_phone = acc_repo.create(db, {"profile_name": "TT-only",
                                        "geelark_phone_id": "ph-tt",
                                        "group_id": gid,
                                        "warmed_up": 1,
                                        "tiktok_logged_in": 1,
                                        "youtube_logged_in": 0})
    youtube_phone = acc_repo.create(db, {"profile_name": "YT-only",
                                         "geelark_phone_id": "ph-yt",
                                         "group_id": gid,
                                         "warmed_up": 1,
                                         "tiktok_logged_in": 0,
                                         "youtube_logged_in": 1})
    f = tmp_path / "split.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "split.mp4", str(f), group_id=gid)
    tt_pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    yt_pid = pub_repo.create(db, video_id=vid, platform="youtube")

    assert posting_service.plan_assignments(
        db, platform="tiktok", cooldown_hours=12) == [tt_pid]
    assert pub_repo.get(db, tt_pid)["account_id"] == tiktok_phone
    assert pub_repo.get(db, yt_pid)["result"] == "PENDING"

    assert posting_service.plan_assignments(
        db, platform="youtube", cooldown_hours=12) == [yt_pid]
    assert pub_repo.get(db, yt_pid)["account_id"] == youtube_phone
    assert vid_repo.get(db, vid)["account_id"] is None


def test_plan_assignments_uses_each_platform_pool_capacity(
        db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as gr
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    gid = gr.create(db, name="g")
    for i in range(8):
        acc_repo.create(db, {"profile_name": f"TT-{i}",
                             "geelark_phone_id": f"ph-tt-{i}",
                             "group_id": gid,
                             "warmed_up": 1,
                             "tiktok_logged_in": 1,
                             "youtube_logged_in": 0})
    for i in range(2):
        acc_repo.create(db, {"profile_name": f"YT-{i}",
                             "geelark_phone_id": f"ph-yt-{i}",
                             "group_id": gid,
                             "warmed_up": 1,
                             "tiktok_logged_in": 0,
                             "youtube_logged_in": 1})

    tiktok_pids = []
    youtube_pids = []
    for i in range(8):
        f = tmp_path / f"v{i}.mp4"; f.write_bytes(b"x")
        vid = vid_repo.create(db, f"v{i}.mp4", str(f), group_id=gid)
        tiktok_pids.append(pub_repo.create(db, video_id=vid,
                                           platform="tiktok"))
        youtube_pids.append(pub_repo.create(db, video_id=vid,
                                            platform="youtube"))

    assert posting_service.plan_assignments(
        db, platform="tiktok", cooldown_hours=12) == tiktok_pids
    assert posting_service.plan_assignments(
        db, platform="youtube", cooldown_hours=12) == youtube_pids[:2]
    assert [pub_repo.get(db, pid)["result"] for pid in youtube_pids].count(
        "QUEUED") == 2
    assert [pub_repo.get(db, pid)["result"] for pid in youtube_pids].count(
        "PENDING") == 6


def test_plan_assignments_does_not_queue_same_assigned_phone_twice(
        db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as gr
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    gid = gr.create(db, name="g")
    aid = acc_repo.create(db, {"profile_name": "YT",
                               "geelark_phone_id": "ph-yt",
                               "group_id": gid,
                               "warmed_up": 1,
                               "youtube_logged_in": 1})
    pids = []
    for i in range(2):
        f = tmp_path / f"manual-{i}.mp4"; f.write_bytes(b"x")
        vid = vid_repo.create(db, f"manual-{i}.mp4", str(f), group_id=gid)
        pid = pub_repo.create(db, video_id=vid, platform="youtube")
        pub_repo.set_account(db, pid, aid)
        pids.append(pid)

    assert posting_service.plan_assignments(
        db, platform="youtube", cooldown_hours=12) == [pids[0]]
    assert pub_repo.get(db, pids[0])["result"] == "QUEUED"
    assert pub_repo.get(db, pids[1])["result"] == "PENDING"


def test_plan_assignments_skips_phone_with_initiated_task_across_platforms(
        db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as gr
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    gid = gr.create(db, name="g")
    busy = acc_repo.create(db, {"profile_name": "busy",
                                "geelark_phone_id": "ph-busy",
                                "group_id": gid,
                                "warmed_up": 1,
                                "tiktok_logged_in": 1,
                                "youtube_logged_in": 1})
    free = acc_repo.create(db, {"profile_name": "free-yt",
                                "geelark_phone_id": "ph-free",
                                "group_id": gid,
                                "warmed_up": 1,
                                "youtube_logged_in": 1})
    f1 = tmp_path / "busy.mp4"; f1.write_bytes(b"x")
    busy_vid = vid_repo.create(db, "busy.mp4", str(f1), group_id=gid)
    busy_pid = pub_repo.create(db, video_id=busy_vid, platform="tiktok")
    pub_repo.set_account(db, busy_pid, busy)
    pub_repo.mark_initiated(db, busy_pid, "c", "task-busy", "submitted")

    f2 = tmp_path / "next.mp4"; f2.write_bytes(b"x")
    next_vid = vid_repo.create(db, "next.mp4", str(f2), group_id=gid)
    next_pid = pub_repo.create(db, video_id=next_vid, platform="youtube")

    assert posting_service.plan_assignments(
        db, platform="youtube", cooldown_hours=12) == [next_pid]
    assert pub_repo.get(db, next_pid)["account_id"] == free


def test_plan_assignments_does_not_reuse_already_claimed_phone_in_scan(
        db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as gr
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    gid = gr.create(db, name="g")
    aid = acc_repo.create(db, {"profile_name": "one-phone",
                               "geelark_phone_id": "ph-one",
                               "group_id": gid,
                               "warmed_up": 1,
                               "tiktok_logged_in": 1})
    f1 = tmp_path / "manual.mp4"; f1.write_bytes(b"x")
    manual_vid = vid_repo.create(db, "manual.mp4", str(f1), group_id=gid)
    vid_repo.set_account(db, manual_vid, aid)
    manual_pid = pub_repo.create(db, video_id=manual_vid, platform="tiktok")
    f2 = tmp_path / "auto.mp4"; f2.write_bytes(b"x")
    auto_vid = vid_repo.create(db, "auto.mp4", str(f2), group_id=gid)
    auto_pid = pub_repo.create(db, video_id=auto_vid, platform="tiktok")

    assert posting_service.plan_assignments(db, platform="tiktok") == [
        manual_pid]
    assert pub_repo.get(db, auto_pid)["result"] == "PENDING"
    assert vid_repo.get(db, auto_vid)["account_id"] is None


def test_submit_publication_to_geelark_happy_path(db, tmp_path, monkeypatch,
                                                  default_group_id):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    aid = acc_repo.create(db, {"profile_name": "SP",
                               "geelark_phone_id": "phSP",
                               "warmed_up": 1,
                               "tiktok_logged_in": 1,
                               "group_id": default_group_id})
    f = tmp_path / "s.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "s.mp4", str(f), group_id=default_group_id)
    # submit_publication_to_geelark reads video.account_id, so stamp first
    # then QUEUE — mirrors what /api/videos/{vid}/publish does.
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    pub_repo.mark_queued(db, pid)

    class FakeClient:
        def upload_file(self, p, t): return "https://res/s.mp4"
        def create_video_post_task(self, env, url,
                                   video_desc=None, cover_url=None):
            return "tk-1"
    monkeypatch.setattr(posting_service, "_caption",
                        lambda s, p, a: "cap")
    task_id = posting_service.submit_publication_to_geelark(
        db, FakeClient(), publication_id=pid)
    assert task_id == "tk-1"
    row = pub_repo.get(db, pid)
    assert row["result"] == "INITIATED"


def test_submit_publication_keeps_queued_when_phone_already_initiated(
        db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as gr
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import posting_service as ps

    gid = gr.create(db, name="g")
    aid = acc_repo.create(db, {"profile_name": "busy",
                               "geelark_phone_id": "ph-busy",
                               "group_id": gid,
                               "warmed_up": 1,
                               "tiktok_logged_in": 1,
                               "youtube_logged_in": 1})
    f1 = tmp_path / "active.mp4"; f1.write_bytes(b"x")
    active_vid = vid_repo.create(db, "active.mp4", str(f1), group_id=gid)
    active_pid = pub_repo.create(db, video_id=active_vid, platform="tiktok")
    pub_repo.set_account(db, active_pid, aid)
    pub_repo.mark_initiated(db, active_pid, "c", "task-active", "submitted")

    f2 = tmp_path / "blocked.mp4"; f2.write_bytes(b"x")
    blocked_vid = vid_repo.create(db, "blocked.mp4", str(f2), group_id=gid)
    blocked_pid = pub_repo.create(db, video_id=blocked_vid, platform="youtube")
    pub_repo.set_account(db, blocked_pid, aid)
    pub_repo.mark_queued(db, blocked_pid)

    class ShouldNotSubmit:
        def upload_file(self, path, ext):
            raise AssertionError("busy phone should not upload")

    with pytest.raises(ps.PhoneBusy):
        ps.submit_publication_to_geelark(
            db, ShouldNotSubmit(), publication_id=blocked_pid)

    blocked = pub_repo.get(db, blocked_pid)
    # Stays QUEUED so the task-layer PhoneBusy retry loop re-enters the
    # submit gate; unclaiming to PENDING made every retry a silent no-op.
    assert blocked["result"] == "QUEUED"
    assert blocked["account_id"] == aid
    assert pub_repo.get(db, active_pid)["result"] == "INITIATED"


def test_submit_publication_marks_missing_stored_file(db, tmp_path, monkeypatch,
                                                       default_group_id):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    aid = acc_repo.create(db, {"profile_name": "MISS",
                               "geelark_phone_id": "phMISS",
                               "warmed_up": 1,
                               "tiktok_logged_in": 1,
                               "group_id": default_group_id})
    missing = tmp_path / "gone.mp4"
    vid = vid_repo.create(db, "gone.mp4", str(missing), group_id=default_group_id)
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    pub_repo.mark_queued(db, pid)

    class FailFast:
        def upload_file(self, *a, **k):
            raise AssertionError("must not upload a missing file")

    monkeypatch.setattr(
        posting_service,
        "_caption",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("must not caption a missing file")),
    )
    with pytest.raises(FileNotFoundError):
        posting_service.submit_publication_to_geelark(
            db, FailFast(), publication_id=pid)

    row = pub_repo.get(db, pid)
    assert row["result"] == "FAILURE"
    assert row["notes"] == f"stored file missing on server: {missing}"


def test_submit_publication_caption_failure_records_gemini_attempt(
        db, tmp_path, monkeypatch, default_group_id):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    aid = acc_repo.create(db, {"profile_name": "GeminiFail",
                               "geelark_phone_id": "ph-gemini-fail",
                               "warmed_up": 1,
                               "tiktok_logged_in": 1,
                               "group_id": default_group_id})
    f = tmp_path / "gemini-fail.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(db, "gemini-fail.mp4", str(f), group_id=default_group_id)
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    pub_repo.mark_queued(db, pid)

    class UploadShouldNotRun:
        def upload_file(self, *args, **kwargs):
            raise AssertionError("upload should not run after caption failure")

    monkeypatch.setattr(
        posting_service,
        "_caption",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("Gemini quota exceeded")),
    )

    with pytest.raises(RuntimeError, match="Gemini quota exceeded"):
        posting_service.submit_publication_to_geelark(
            db, UploadShouldNotRun(), publication_id=pid)

    row = pub_repo.get(db, pid)
    assert row["result"] == "FAILURE"
    assert row["notes"] == "caption failed: Gemini quota exceeded"
    attempts = pub_repo.list_attempts(db, pid)
    assert len(attempts) == 1
    assert attempts[0]["source"] == "gemini"
    assert attempts[0]["stage"] == "caption"
    assert attempts[0]["message"] == "Gemini quota exceeded"


def test_submit_publication_upload_failure_records_geelark_attempt(
        db, tmp_path, monkeypatch, default_group_id):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    aid = acc_repo.create(db, {"profile_name": "UploadFail",
                               "geelark_phone_id": "ph-upload-fail",
                               "warmed_up": 1,
                               "tiktok_logged_in": 1,
                               "group_id": default_group_id})
    f = tmp_path / "upload-fail.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(db, "upload-fail.mp4", str(f), group_id=default_group_id)
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    pub_repo.mark_queued(db, pid)

    class BrokenUpload:
        def upload_file(self, path, ext):
            raise RuntimeError("GeeLark upload 502")

    monkeypatch.setattr(posting_service, "_caption",
                        lambda *a, **k: "caption")

    with pytest.raises(RuntimeError, match="GeeLark upload 502"):
        posting_service.submit_publication_to_geelark(
            db, BrokenUpload(), publication_id=pid)

    row = pub_repo.get(db, pid)
    assert row["result"] == "FAILURE"
    assert row["notes"] == "upload failed: GeeLark upload 502"
    attempts = pub_repo.list_attempts(db, pid)
    assert len(attempts) == 1
    assert attempts[0]["source"] == "geelark"
    assert attempts[0]["stage"] == "upload"
    assert attempts[0]["message"] == "GeeLark upload 502"


def test_submit_publication_reuses_caption_for_same_video(
        db, tmp_path, monkeypatch):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as gr
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    gid = gr.create(db, name="g")
    tt_aid = acc_repo.create(db, {"profile_name": "CAP-TT",
                                  "geelark_phone_id": "phCAP-TT",
                                  "group_id": gid,
                                  "warmed_up": 1,
                                  "tiktok_logged_in": 1})
    yt_aid = acc_repo.create(db, {"profile_name": "CAP-YT",
                                  "geelark_phone_id": "phCAP-YT",
                                  "group_id": gid,
                                  "warmed_up": 1,
                                  "youtube_logged_in": 1})
    f = tmp_path / "shared.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "shared.mp4", str(f), group_id=gid)
    tt_pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    yt_pid = pub_repo.create(db, video_id=vid, platform="youtube")
    pub_repo.set_account(db, tt_pid, tt_aid)
    pub_repo.set_account(db, yt_pid, yt_aid)
    pub_repo.mark_queued(db, tt_pid)
    pub_repo.mark_queued(db, yt_pid)

    calls = []

    def fake_caption(session, path, acc):
        calls.append((path, acc["id"]))
        return "one shared caption"

    class FakeClient:
        def upload_file(self, p, t):
            return "https://res/shared.mp4"
        def create_video_post_task(self, env, url,
                                   video_desc=None, cover_url=None):
            assert video_desc == "one shared caption"
            return "tk-shared"
        def create_youtube_post_task(self, env, url, title):
            assert title == "one shared caption"
            return "yt-shared"

    monkeypatch.setattr(posting_service, "_caption", fake_caption)
    posting_service.submit_publication_to_geelark(
        db, FakeClient(), publication_id=tt_pid)
    posting_service.submit_publication_to_geelark(
        db, FakeClient(), publication_id=yt_pid)

    assert calls == [(str(f), tt_aid)]
    assert pub_repo.get(db, tt_pid)["caption"] == "one shared caption"
    assert pub_repo.get(db, yt_pid)["caption"] == "one shared caption"


def test_check_publication_one_transitions(db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    aid = acc_repo.create(db, {"profile_name": "CK",
                               "geelark_phone_id": "phCK"})
    f = tmp_path / "ck.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "ck.mp4", str(f))
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(db, pid, "c", "ck1", "s")

    class Running:
        def query_tasks(self, ids): return [{"id": ids[0], "status": 2}]
    assert posting_service.check_publication_one(db, Running(), pid) \
        == "running"

    class Done:
        def query_tasks(self, ids): return [{"id": ids[0], "status": 3}]
    assert posting_service.check_publication_one(db, Done(), pid) \
        == "success"
    assert pub_repo.get(db, pid)["result"] == "SUCCESS"


def test_check_publication_one_failure_captures_geelark_detail(db, tmp_path):
    """On a GeeLark task failure, the check should pull task detail and
    record failCode, the human-readable failDesc (as notes), and the LAST
    RPA screenshot (the failure point) onto the publication."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    aid = acc_repo.create(db, {"profile_name": "FX",
                               "geelark_phone_id": "phFX"})
    f = tmp_path / "fx.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "fx.mp4", str(f))
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="facebook")
    pub_repo.mark_initiated(db, pid, "c", "fb-task-1", "s")

    class Failed:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 4}]
        def get_task_detail(self, task_id):
            assert task_id == "fb-task-1"
            return {"status": 4, "failCode": 20267,
                    "failDesc": "Reel is empty",
                    "resultImages": ["https://img/step1.jpg",
                                     "https://img/failpoint.jpg"]}

    assert posting_service.check_publication_one(db, Failed(), pid) \
        == "failure"
    row = pub_repo.get(db, pid)
    assert row["result"] == "FAILURE"
    assert row["notes"] == "Reel is empty"
    assert row["fail_code"] == 20267
    # the LAST screenshot is the failure point
    assert row["fail_screenshot"] == "https://img/failpoint.jpg"
    attempts = pub_repo.list_attempts(db, pid)
    assert len(attempts) == 1
    assert attempts[0]["source"] == "geelark"
    assert attempts[0]["stage"] == "status_check"
    assert attempts[0]["message"] == "Reel is empty"
    assert attempts[0]["error_code"] == "20267"
    assert attempts[0]["fail_screenshot"] == "https://img/failpoint.jpg"
    assert attempts[0]["geelark_task_id"] == "fb-task-1"


def test_check_publication_one_failure_keeps_source_file(db, tmp_path):
    """A failed publication must not delete the source video.

    Failed videos need operator inspection and manual/automatic retry. Only
    videos whose publications all succeed are safe to remove from disk.
    """
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    aid = acc_repo.create(db, {"profile_name": "KeepFail",
                               "geelark_phone_id": "ph-keep-fail"})
    f = tmp_path / "keep-fail.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(db, "keep-fail.mp4", str(f))
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(db, pid, "c", "task-keep-fail", "submitted")

    class Failed:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 4}]

        def get_task_detail(self, task_id):
            return {"failCode": 20267, "failDesc": "Reel is empty",
                    "resultImages": []}

    assert posting_service.check_publication_one(db, Failed(), pid) \
        == "failure"

    assert f.exists()
    assert vid_repo.get(db, vid)["stored_path"] == str(f)


def test_check_publication_one_failure_degrades_without_detail(db, tmp_path):
    """If the client can't supply task detail (or it errors), the check
    still resolves to FAILURE with a generic reason and no screenshot —
    reconciliation must never get stuck on a missing detail call."""
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    aid = acc_repo.create(db, {"profile_name": "FY",
                               "geelark_phone_id": "phFY"})
    f = tmp_path / "fy.mp4"; f.write_bytes(b"x")
    vid = vid_repo.create(db, "fy.mp4", str(f))
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="facebook")
    pub_repo.mark_initiated(db, pid, "c", "fb-task-2", "s")

    class FailedNoDetail:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 4}]
        def get_task_detail(self, task_id):
            raise RuntimeError("detail endpoint down")

    assert posting_service.check_publication_one(db, FailedNoDetail(), pid) \
        == "failure"
    row = pub_repo.get(db, pid)
    assert row["result"] == "FAILURE"
    assert row["notes"] == "task status 4"
    assert row["fail_screenshot"] is None


def test_check_publication_one_stores_share_link(db, tmp_path):
    from app.repositories import accounts as acc_repo
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo
    from app.services import posting_service

    aid = acc_repo.create(db, {"profile_name": "SL", "geelark_phone_id": "ph-sl"})
    f = tmp_path / "sl.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(db, "sl.mp4", str(f))
    vid_repo.set_account(db, vid, aid)
    pid = pub_repo.create(db, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(db, pid, "caption", "task-sl", "submitted")

    class DoneWithLink:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 3,
                     "shareLink": "https://www.tiktok.com/@owner/video/1234567890"}]

    outcome = posting_service.check_publication_one(db, DoneWithLink(), pid)

    assert outcome == "success"
    row = pub_repo.get(db, pid)
    assert row["share_link"] == "https://www.tiktok.com/@owner/video/1234567890"
    assert row["share_link_captured_at"] is not None
