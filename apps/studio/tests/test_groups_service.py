def test_counts_for_combines_accounts_and_videos(db):
    from app.services import groups_service as gs
    from app.repositories import accounts as ar, videos as vr, groups as gr
    from app.repositories import publications as pr

    g1 = gr.create(db, name="g1")
    ar.create(db, {"profile_name": "P1", "group_id": g1,
                    "tiktok_login_info": "tiktok blob",
                    "tiktok_logged_in": 1, "warmed_up": 1})
    ar.create(db, {"profile_name": "P2", "group_id": g1,
                    "youtube_login_info": "yt blob",
                    "tiktok_logged_in": 1})
    ar.create(db, {"profile_name": "P3", "group_id": g1})
    vid = vr.create(db, "v.mp4", "/tmp/v.mp4", group_id=g1)
    pid = pr.create(db, video_id=vid, platform="tiktok")
    pr.set_result(db, pid, "SUCCESS", "ok")
    vr.create(db, "p.mp4", "/tmp/p.mp4", group_id=g1)

    c = gs.counts_for(db, g1)
    assert c == {"phones": 3, "assigned": 2, "logged_in": 2, "warmed": 1,
                  "published": 1, "pending": 1, "failed": 0}


def test_list_all_attaches_counts(db):
    from app.services import groups_service as gs
    from app.repositories import accounts as ar
    gid = gs.create(db, name="g1")
    ar.create(db, {"profile_name": "p", "group_id": gid})
    rows = gs.list_all(db)
    assert len(rows) == 1
    assert rows[0]["name"] == "g1"
    assert rows[0]["counts"]["phones"] == 1


def test_get_includes_accounts_and_videos(db):
    from app.services import groups_service as gs
    from app.repositories import accounts as ar
    from app.repositories import videos as vr

    gid = gs.create(db, name="g1")
    ar.create(db, {"profile_name": "P1", "group_id": gid})
    vr.create(db, "v.mp4", "/tmp/v.mp4", group_id=gid)

    row = gs.get(db, gid)
    assert len(row["accounts"]) == 1
    assert row["accounts"][0]["profile_name"] == "P1"
    assert len(row["videos"]) == 1
    assert row["videos"][0]["original_filename"] == "v.mp4"


def test_create_passes_through_to_repo(db):
    from app.services import groups_service as gs
    from app.repositories import groups as gr
    gid = gs.create(db, name="x", notes="note")
    row = gr.get(db, gid)
    assert row["notes"] == "note"
