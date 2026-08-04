from app.repositories import settings as settings_repo
from app.repositories import job_runs as jobs_repo
from app.repositories import accounts as acc_repo
from app.repositories import videos as vid_repo

def test_settings_get_all_and_update(db):
    s = settings_repo.get_all(db)
    assert s["default_target_warmup_count"] == "10"
    settings_repo.update(db, {"default_target_warmup_count": "20"})
    assert settings_repo.get_all(db)["default_target_warmup_count"] == "20"

def test_job_run_lifecycle(db):
    jid = jobs_repo.start(db, "warmup")
    jobs_repo.finish(db, jid, "ok", "8 accounts warmed")
    recent = jobs_repo.recent(db, limit=5)
    assert recent[0]["status"] == "ok"
    assert recent[0]["summary"] == "8 accounts warmed"

def test_account_create_list_update_delete(db):
    aid = acc_repo.create(db, {"profile_name": "p1", "email": "a@b.com",
                               "proxy": "socks5://x",
                               "target_warmup_count": 10})
    row = acc_repo.get(db, aid)
    assert row["profile_name"] == "p1"
    assert row["warmup_count"] == 0
    assert row["target_warmup_count"] == 10
    assert "time_spent_warming" not in row
    assert "max_warm_time" not in row
    acc_repo.update(db, aid, {"email": "c@d.com", "tiktok_logged_in": 1})
    assert acc_repo.get(db, aid)["email"] == "c@d.com"
    assert acc_repo.get(db, aid)["tiktok_logged_in"] == 1
    acc_repo.delete(db, aid)
    assert acc_repo.get(db, aid) is None

def test_upsert_by_phone_id_for_import(db):
    acc_repo.upsert_by_phone_id(db, "ph1", {"profile_name": "imp1",
                                            "group_name": "G", "proxy": ""})
    acc_repo.upsert_by_phone_id(db, "ph1", {"profile_name": "imp1-renamed",
                                            "group_name": "G", "proxy": ""})
    rows = acc_repo.list_all(db)
    assert len(rows) == 1
    assert rows[0]["profile_name"] == "imp1-renamed"

def test_account_create_rejects_empty(db):
    import pytest
    with pytest.raises(ValueError):
        acc_repo.create(db, {"not_a_real_column": "x"})


def test_account_warmup_mode_defaults_to_count(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name": "wm-default"})
    row = acc_repo.get(db, aid)
    assert row["warmup_mode"] == "count"


def test_account_warmup_mode_round_trip(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name": "wm-rt",
                               "warmup_mode": "daily"})
    assert acc_repo.get(db, aid)["warmup_mode"] == "daily"
    acc_repo.update(db, aid, {"warmup_mode": "count"})
    assert acc_repo.get(db, aid)["warmup_mode"] == "count"

def test_video_create_with_drive_file_id(db):
    vid = vid_repo.create(db, "g.mp4", "/x/g.mp4", drive_file_id="DRIVEID123")
    assert vid_repo.get(db, vid)["drive_file_id"] == "DRIVEID123"

def test_video_create_without_drive_file_id_is_null(db):
    vid = vid_repo.create(db, "h.mp4", "/x/h.mp4")
    assert vid_repo.get(db, vid)["drive_file_id"] is None

def test_existing_drive_file_ids(db):
    vid_repo.create(db, "a.mp4", "/x/a.mp4", drive_file_id="ID_A")
    vid_repo.create(db, "b.mp4", "/x/b.mp4", drive_file_id="ID_B")
    vid_repo.create(db, "c.mp4", "/x/c.mp4")  # manual upload, no drive id
    assert vid_repo.existing_drive_file_ids(db) == {"ID_A", "ID_B"}

def test_account_repo_does_not_expose_retired_avatar_fields(db):
    from sqlalchemy import text
    from app.repositories import accounts as ar

    aid = ar.create(db, {"profile_name": "NoAvatar"})
    db.execute(
        text(
            "UPDATE accounts SET profile_pic_url=:url, profile_pic_path=:path, "
            "pic_status=:status, pic_task_id=:task WHERE id=:aid"
        ),
        {
            "aid": aid,
            "url": "https://legacy.example/avatar.jpg",
            "path": "../../.env",
            "status": "initiated",
            "task": "pic-task",
        },
    )
    db.commit()

    row = ar.get(db, aid)
    listed = next(r for r in ar.list_all(db) if r["id"] == aid)
    for key in ("profile_pic_url", "profile_pic_path",
                "pic_status", "pic_task_id", "avatar_src"):
        assert key not in row
        assert key not in listed

def test_accounts_columns_include_new_credential_fields(db):
    from app.repositories import accounts as ar
    aid = ar.create(db, {"profile_name": "M1", "password": "pw",
                          "tiktok_password": "tkpw", "recovery_email": "r@x.io",
                          "mail_inbox_url": "https://inbox.example/me",
                          "raw_credentials": "raw line",
                          "group_id": None})
    row = ar.get(db, aid)
    assert row["password"] == "pw"
    assert row["tiktok_password"] == "tkpw"
    assert row["recovery_email"] == "r@x.io"
    assert row["mail_inbox_url"] == "https://inbox.example/me"
    assert row["raw_credentials"] == "raw line"


def test_account_list_resolves_group_name_from_group_id(db):
    from app.repositories import accounts as ar, groups as gr
    gid = gr.create(db, name="Display Group")
    aid = ar.create(db, {"profile_name": "Grouped", "group_id": gid})

    row = next(r for r in ar.list_all(db) if r["id"] == aid)

    assert row["group_id"] == gid
    assert row["group_name"] == "Display Group"


def test_account_list_exposes_active_phone_session_deadline(db):
    from app.repositories import accounts as ar
    from app.repositories import phone_sessions as ps_repo
    from app.services import phone_runtime

    aid = ar.create(db, {"profile_name": "Held", "geelark_phone_id": "ph-held"})
    sid = phone_runtime.start_session(
        db,
        account_id=aid,
        env_id="ph-held",
        source="manual",
        owner_type="account",
        owner_id=aid,
        budget_seconds=2 * 3600,
        created_by="operator",
    )

    row = next(r for r in ar.list_all(db) if r["id"] == aid)
    active = ps_repo.get(db, sid)

    assert row["phone_busy_until"] == active["budget_deadline_at"]


def test_eligible_commenters_marks_active_phone_session_busy(db, default_group_id):
    from app.repositories import accounts as ar
    from app.repositories import eligible_phones
    from app.services import phone_runtime

    aid = ar.create(db, {
        "profile_name": "Busy Commenter",
        "geelark_phone_id": "ph-busy-commenter",
        "group_id": default_group_id,
        "warmed_up": 1,
        "tiktok_logged_in": 1,
    })
    phone_runtime.start_session(
        db,
        account_id=aid,
        env_id="ph-busy-commenter",
        source="manual",
        owner_type="account",
        owner_id=aid,
        budget_seconds=3600,
        created_by="operator",
    )

    row = next(
        r for r in eligible_phones.list_eligible_commenters(db, platform="tiktok")
        if r["id"] == aid
    )
    assert row["busy"] is True


def test_list_by_group_returns_only_that_group(db):
    from app.repositories import accounts as ar, groups as gr
    g1 = gr.create(db, name="g1")
    g2 = gr.create(db, name="g2")
    a1 = ar.create(db, {"profile_name": "P1", "group_id": g1})
    a2 = ar.create(db, {"profile_name": "P2", "group_id": g1})
    ar.create(db, {"profile_name": "P3", "group_id": g2})
    rows = ar.list_by_group(db, g1)
    assert {r["id"] for r in rows} == {a1, a2}


def test_bulk_set_logged_in_only_updates_named_ids(db):
    from app.repositories import accounts as ar
    a1 = ar.create(db, {"profile_name": "X1"})
    a2 = ar.create(db, {"profile_name": "X2"})
    a3 = ar.create(db, {"profile_name": "X3"})
    n = ar.bulk_set_logged_in(db, [a1, a3])
    assert n == 2
    assert ar.get(db, a1)["tiktok_logged_in"] == 1
    assert ar.get(db, a2)["tiktok_logged_in"] == 0
    assert ar.get(db, a3)["tiktok_logged_in"] == 1


def test_bulk_set_logged_in_empty_list_is_noop(db):
    from app.repositories import accounts as ar
    a1 = ar.create(db, {"profile_name": "Y1"})
    assert ar.bulk_set_logged_in(db, []) == 0
    assert ar.get(db, a1)["tiktok_logged_in"] == 0


def test_count_for_group_aggregates_states(db):
    from app.repositories import accounts as ar, groups as gr
    g1 = gr.create(db, name="g1")
    # P1: tiktok login info + logged in + warmed
    ar.create(db, {"profile_name": "P1", "group_id": g1,
                    "geelark_phone_id": "ph1",
                    "tiktok_login_info": "tiktok blob",
                    "tiktok_logged_in": 1, "warmed_up": 1})
    # P2: youtube login info + tiktok logged in (assigned counts ANY login_info)
    ar.create(db, {"profile_name": "P2", "group_id": g1,
                    "geelark_phone_id": "ph2",
                    "youtube_login_info": "yt blob",
                    "tiktok_logged_in": 1})
    # P3: no login info anywhere → not assigned
    ar.create(db, {"profile_name": "P3", "group_id": g1,
                    "geelark_phone_id": "ph3"})
    c = ar.count_for_group(db, g1)
    assert c == {"phones": 3, "assigned": 2, "logged_in": 2, "warmed": 1}


def test_videos_create_accepts_group_id(db):
    from app.repositories import videos as vr, groups as gr
    g1 = gr.create(db, name="g1")
    vid = vr.create(db, "v.mp4", "/tmp/v.mp4", group_id=g1)
    row = vr.get(db, vid)
    assert row["group_id"] == g1


def test_videos_count_for_group(session):
    from app.repositories import videos as vr
    from app.repositories import publications as pr
    from app.repositories import accounts as ar
    from app.repositories import groups as gr
    ar.create(session, {"profile_name": "C1", "geelark_phone_id": "phc1"})
    gid = gr.create(session, name="g1")
    vid_a = vr.create(session, "a.mp4", "/tmp/a.mp4", group_id=gid)
    vid_b = vr.create(session, "b.mp4", "/tmp/b.mp4", group_id=gid)
    vr.create(session, "c.mp4", "/tmp/c.mp4", group_id=gid)  # no publications
    pa = pr.create(session, video_id=vid_a, platform="tiktok")
    pb = pr.create(session, video_id=vid_b, platform="tiktok")
    pr.set_result(session, pa, "SUCCESS", "ok")
    pr.set_result(session, pb, "FAILURE", "err")
    c = vr.count_for_group(session, gid)
    # vid_a -> SUCCESS, vid_b -> FAILURE, vid_c -> no publications (pending)
    assert c == {"published": 1, "pending": 1, "failed": 1}


def test_count_for_group_mixed_success_and_failure_is_published(session):
    """A video with SUCCESS on TT + FAILURE on YT counts as published
    (SUCCESS wins; bucket is not duplicated)."""
    from app.repositories import videos as vr
    from app.repositories import publications as pr
    from app.repositories import accounts as ar
    from app.repositories import groups as gr
    ar.create(session, {"profile_name": "MX", "geelark_phone_id": "phMX"})
    gid = gr.create(session, name="mixed")
    vid = vr.create(session, "m.mp4", "/tmp/m.mp4", group_id=gid)
    p_tt = pr.create(session, video_id=vid, platform="tiktok")
    p_yt = pr.create(session, video_id=vid, platform="youtube")
    pr.set_result(session, p_tt, "SUCCESS", "ok")
    pr.set_result(session, p_yt, "FAILURE", "rate limit")
    c = vr.count_for_group(session, gid)
    assert c == {"published": 1, "pending": 0, "failed": 0}


def test_count_for_group_video_without_publications_is_pending(session):
    """A video that has no publication rows yet is 'pending'
    (not-yet-attempted), not invisible."""
    from app.repositories import videos as vr
    from app.repositories import groups as gr
    gid = gr.create(session, name="empty")
    vr.create(session, "z.mp4", "/tmp/z.mp4", group_id=gid)
    c = vr.count_for_group(session, gid)
    assert c == {"published": 0, "pending": 1, "failed": 0}


def test_next_serial_index_empty_group_returns_1(db):
    from app.repositories import accounts as acc, groups as gr
    gid = gr.create(db, name="g")
    assert acc.next_serial_index(db, gid, "g") == 1


def test_next_serial_index_skips_existing_numbers(db):
    from app.repositories import accounts as acc, groups as gr
    gid = gr.create(db, name="g")
    acc.create(db, {"profile_name": "g-01", "group_id": gid})
    acc.create(db, {"profile_name": "g-02", "group_id": gid})
    assert acc.next_serial_index(db, gid, "g") == 3


def test_next_serial_index_treats_paren_suffix_as_same_slot(db):
    """`g-01 (2)` is a leftover from unique_profile_name and occupies slot 1."""
    from app.repositories import accounts as acc, groups as gr
    gid = gr.create(db, name="g")
    acc.create(db, {"profile_name": "g-01", "group_id": gid})
    acc.create(db, {"profile_name": "g-01 (2)", "group_id": gid})
    assert acc.next_serial_index(db, gid, "g") == 2


def test_next_serial_index_ignores_other_groups(db):
    from app.repositories import accounts as acc, groups as gr
    g1 = gr.create(db, name="g")
    g2 = gr.create(db, name="h")
    acc.create(db, {"profile_name": "g-05", "group_id": g1})
    acc.create(db, {"profile_name": "h-99", "group_id": g2})
    assert acc.next_serial_index(db, g1, "g") == 6


def test_next_serial_index_ignores_unparseable_suffix(db):
    """A row like `g-foo` doesn't occupy any numeric slot."""
    from app.repositories import accounts as acc, groups as gr
    gid = gr.create(db, name="g")
    acc.create(db, {"profile_name": "g-foo", "group_id": gid})
    assert acc.next_serial_index(db, gid, "g") == 1


def test_next_serial_index_three_digit_numbers(db):
    """Width is just for formatting; the helper handles >99 fine."""
    from app.repositories import accounts as acc, groups as gr
    gid = gr.create(db, name="g")
    acc.create(db, {"profile_name": "g-100", "group_id": gid})
    assert acc.next_serial_index(db, gid, "g") == 101


def test_touch_last_posted_per_platform(session):
    from app.repositories import accounts as ar
    aid = ar.create(session, {"profile_name": "TP-multi"})
    expected_col = {
        "tiktok":    "last_posted_at",
        "youtube":   "last_posted_at_youtube",
        "instagram": "last_posted_at_instagram",
        "facebook":  "last_posted_at_facebook",
    }
    for plat, col in expected_col.items():
        ar.touch_last_posted(session, aid, plat)
        row = ar.get(session, aid)
        assert row[col] is not None, f"touch for {plat} did not set {col}"


def test_touch_last_posted_rejects_unknown_platform(session):
    import pytest
    from app.repositories import accounts as ar
    aid = ar.create(session, {"profile_name": "TPZ"})
    with pytest.raises(ValueError):
        ar.touch_last_posted(session, aid, "linkedin")


def test_accounts_columns_roundtrip_all_platform_fields(session):
    from app.repositories import accounts as ar
    payload = {"profile_name": "MULTI"}
    for plat in ("youtube", "instagram", "facebook"):
        payload[f"{plat}_logged_in"] = 1
        payload[f"{plat}_login_info"] = f"{plat} login blob"
    aid = ar.create(session, payload)
    row = ar.get(session, aid)
    for plat in ("youtube", "instagram", "facebook"):
        assert row[f"{plat}_logged_in"] == 1
        assert row[f"{plat}_login_info"] == f"{plat} login blob"


def test_mark_warmed_up_resets_mode_to_count(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {
        "profile_name": "exit-daily",
        "warmup_mode": "daily",
    })
    acc_repo.mark_warmed_up(db, [aid])
    row = acc_repo.get(db, aid)
    assert row["warmed_up"] == 1
    assert row["warmup_mode"] == "count"


def test_bulk_set_warmup_mode_updates_listed_rows(db):
    from app.repositories import accounts as acc_repo
    a = acc_repo.create(db, {"profile_name": "bulk-mode-a"})
    b = acc_repo.create(db, {"profile_name": "bulk-mode-b"})
    c = acc_repo.create(db, {"profile_name": "bulk-mode-c"})
    n = acc_repo.bulk_set_warmup_mode(db, [a, b], "daily")
    assert n == 2
    assert acc_repo.get(db, a)["warmup_mode"] == "daily"
    assert acc_repo.get(db, b)["warmup_mode"] == "daily"
    # untouched account stays on its default
    assert acc_repo.get(db, c)["warmup_mode"] == "count"


def test_bulk_set_warmup_mode_returns_zero_for_empty_or_missing(db):
    from app.repositories import accounts as acc_repo
    assert acc_repo.bulk_set_warmup_mode(db, [], "daily") == 0
    assert acc_repo.bulk_set_warmup_mode(db, [999], "daily") == 0


def test_bulk_set_warmup_mode_to_count_backfills_warmed_up_when_count_meets_target(db):
    """Switching to count mode must re-evaluate count >= target and set
    warmed_up=1 — otherwise an account that has already accumulated enough
    warmups under daily mode stays warmed_up=0 forever after the switch
    (advance_warmup_one is the only other code path that flips warmed_up,
    and it doesn't fire when no new warmup task runs)."""
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {
        "profile_name": "daily-already-met-target",
        "warmup_mode": "daily",
        "warmup_count": 17,
        "target_warmup_count": 10,
        "warmed_up": 0,
    })
    acc_repo.bulk_set_warmup_mode(db, [aid], "count")
    row = acc_repo.get(db, aid)
    assert row["warmup_mode"] == "count"
    assert row["warmed_up"] == 1


def test_bulk_set_warmup_mode_to_count_leaves_warmed_up_when_below_target(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {
        "profile_name": "daily-below-target",
        "warmup_mode": "daily",
        "warmup_count": 5,
        "target_warmup_count": 10,
        "warmed_up": 0,
    })
    acc_repo.bulk_set_warmup_mode(db, [aid], "count")
    row = acc_repo.get(db, aid)
    assert row["warmup_mode"] == "count"
    assert row["warmed_up"] == 0


def test_bulk_set_warmup_mode_to_daily_does_not_touch_warmed_up(db):
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {
        "profile_name": "switch-to-daily-warm",
        "warmup_mode": "count",
        "warmup_count": 30,
        "target_warmup_count": 10,
        "warmed_up": 1,
    })
    acc_repo.bulk_set_warmup_mode(db, [aid], "daily")
    row = acc_repo.get(db, aid)
    assert row["warmup_mode"] == "daily"
    assert row["warmed_up"] == 1


def test_account_create_with_proxy_sets_expires_at_30d(db):
    from datetime import datetime, timedelta
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {
        "profile_name": "proxy-create",
        "proxy": "socks5://u:p@host.example:1080",
    })
    row = acc_repo.get(db, aid)
    assert row["proxy_expires_at"] is not None
    delta = row["proxy_expires_at"] - datetime.utcnow()
    # Allow a generous window for clock drift / test runner slowness.
    assert timedelta(days=29) < delta < timedelta(days=31)


def test_account_update_proxy_resets_lifecycle(db):
    from datetime import datetime
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {"profile_name": "proxy-update"})
    # Simulate a probe pass that filled cached probe fields and the
    # previous expiry / country, then change the proxy: every cached
    # field has to be cleared so the next probe rewrites them.
    acc_repo.update(db, aid, {
        "proxy": "socks5://old:pw@old.example:1080",
        "proxy_status": "ok",
        "proxy_checked_at": datetime.utcnow(),
        "proxy_exit_ip": "1.2.3.4",
        "proxy_country": "US",
        "proxy_region": "San Jose",
    })
    acc_repo.update(db, aid, {
        "proxy": "socks5://new:pw@new.example:1080",
    })
    row = acc_repo.get(db, aid)
    assert row["proxy"] == "socks5://new:pw@new.example:1080"
    assert row["proxy_status"] is None
    assert row["proxy_checked_at"] is None
    assert row["proxy_exit_ip"] is None
    assert row["proxy_country"] is None
    assert row["proxy_region"] is None
    assert row["proxy_expires_at"] is not None


def test_account_update_without_proxy_change_keeps_lifecycle(db):
    from datetime import datetime
    from app.repositories import accounts as acc_repo
    aid = acc_repo.create(db, {
        "profile_name": "proxy-stable",
        "proxy": "socks5://u:p@host.example:1080",
    })
    # Re-run probe — same proxy, fresh status — must NOT reset expires_at.
    original = acc_repo.get(db, aid)["proxy_expires_at"]
    acc_repo.update(db, aid, {
        "proxy_status": "ok",
        "proxy_checked_at": datetime.utcnow(),
        "proxy_exit_ip": "9.9.9.9",
        "proxy_country": "US",
    })
    row = acc_repo.get(db, aid)
    assert row["proxy_expires_at"] == original
    assert row["proxy_status"] == "ok"
    assert row["proxy_exit_ip"] == "9.9.9.9"
    assert row["proxy_country"] == "US"
