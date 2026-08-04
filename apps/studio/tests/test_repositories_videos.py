"""Unit tests for the paginated/filtered video repository functions."""
import pytest
from sqlalchemy import text

from app.repositories import accounts as ar
from app.repositories import groups as gr
from app.repositories import publications as pr
from app.repositories import videos as vr


def _seed_pub(session, vid, plat, account_id=None, result="PENDING"):
    """Create or fetch a publication and force its result via raw UPDATE.
    Doing the result mutation in SQL avoids coupling these unit tests to
    the publications repo's evolving public mark_* API surface."""
    pid = pr.find_or_create(session, video_id=vid, platform=plat)
    if account_id is not None:
        pr.set_account(session, pid, account_id)
    if result != "PENDING":
        session.execute(
            text("UPDATE publications SET result = :r WHERE id = :pid"),
            {"r": result, "pid": pid})
        session.commit()
    return pid


def test_count_filtered_empty_db_returns_zero(session):
    assert vr.count_filtered(session, group_id="all", status="all") == 0


def test_count_filtered_all_status_counts_every_video(session):
    gid = gr.create(session, name="g")
    for i in range(3):
        vr.create(session, f"v{i}.mp4", f"/tmp/v{i}", group_id=gid)
    assert vr.count_filtered(session, group_id="all", status="all") == 3


def test_count_filtered_pending_excludes_fully_terminal(session):
    gid = gr.create(session, name="g")
    v1 = vr.create(session, "a.mp4", "/tmp/a", group_id=gid)
    _seed_pub(session, v1, "tiktok", result="SUCCESS")
    _seed_pub(session, v1, "youtube", result="FAILURE")
    v2 = vr.create(session, "b.mp4", "/tmp/b", group_id=gid)
    _seed_pub(session, v2, "tiktok", result="PENDING")
    v3 = vr.create(session, "c.mp4", "/tmp/c", group_id=gid)  # no pubs

    # v2 has all PENDING; v3 has no pubs — both fall into the pending bucket.
    # v1 is mix of SUCCESS+FAILURE with no in-flight, so it lands in handled.
    assert vr.count_filtered(session, group_id="all", status="pending") == 2
    assert vr.count_filtered(
        session, group_id="all", status="handled") == 1


def test_count_filtered_group_filter(session):
    g1 = gr.create(session, name="g1")
    g2 = gr.create(session, name="g2")
    vr.create(session, "a.mp4", "/tmp/a", group_id=g1)
    vr.create(session, "b.mp4", "/tmp/b", group_id=g1)
    vr.create(session, "c.mp4", "/tmp/c", group_id=g2)
    vr.create(session, "d.mp4", "/tmp/d", group_id=None)

    assert vr.count_filtered(session, group_id=g1, status="all") == 2
    assert vr.count_filtered(session, group_id="ungrouped", status="all") == 1
    assert vr.count_filtered(session, group_id="all", status="all") == 4


def test_list_page_empty_db(session):
    items, next_cursor = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert items == []
    assert next_cursor is None


def test_list_page_returns_dict_shape(session):
    aid = ar.create(session, {"profile_name": "phone-a"})
    gid = gr.create(session, name="g")
    v = vr.create(session, "x.mp4", "/tmp/x", group_id=gid)
    _seed_pub(session, v, "tiktok", account_id=aid, result="SUCCESS")
    _seed_pub(session, v, "youtube", result="PENDING")

    items, next_cursor = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert next_cursor is None
    assert len(items) == 1
    row = items[0]
    assert row["id"] == v
    assert row["original_filename"] == "x.mp4"
    assert row["group_id"] == gid
    assert row["summary"] == "PENDING"
    assert row["platforms"]["tiktok"] == {
        "result": "SUCCESS", "has_phone": True,
        "phone_label": "phone-a"}
    assert row["platforms"]["youtube"] == {
        "result": "PENDING", "has_phone": False,
        "phone_label": None}
    assert "instagram" not in row["platforms"]


def test_list_page_summary_terminal_when_all_done(session):
    v = vr.create(session, "x.mp4", "/tmp/x")
    _seed_pub(session, v, "tiktok", result="SUCCESS")
    _seed_pub(session, v, "youtube", result="FAILURE")

    items, _ = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert items[0]["summary"] == "TERMINAL"


def test_list_page_orders_by_id_desc(session):
    v1 = vr.create(session, "a.mp4", "/tmp/a")
    v2 = vr.create(session, "b.mp4", "/tmp/b")
    v3 = vr.create(session, "c.mp4", "/tmp/c")
    items, _ = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert [r["id"] for r in items] == [v3, v2, v1]
    # Zero-publication videos must be summarized as PENDING.
    assert all(r["summary"] == "PENDING" for r in items)
    assert all(r["platforms"] == {} for r in items)


def test_list_page_cursor_pagination_no_overlap(session):
    ids = [vr.create(session, f"v{i}.mp4", f"/tmp/v{i}") for i in range(5)]
    page1, c1 = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=2)
    assert [r["id"] for r in page1] == [ids[4], ids[3]]
    assert c1 == ids[3]

    page2, c2 = vr.list_page(
        session, group_id="all", status="all", cursor=c1, limit=2)
    assert [r["id"] for r in page2] == [ids[2], ids[1]]
    assert c2 == ids[1]

    page3, c3 = vr.list_page(
        session, group_id="all", status="all", cursor=c2, limit=2)
    assert [r["id"] for r in page3] == [ids[0]]
    assert c3 is None


def test_list_page_caption_preview_truncates_to_80(session):
    aid = ar.create(session, {"profile_name": "p"})
    v = vr.create(session, "x.mp4", "/tmp/x")
    pid = _seed_pub(session, v, "tiktok", account_id=aid)
    session.execute(
        text("UPDATE publications SET caption = :c WHERE id = :pid"),
        {"c": "x" * 200, "pid": pid})
    session.commit()

    items, _ = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert items[0]["caption_preview"] == "x" * 80


def test_list_page_phone_label_uses_account_profile_name(session):
    aid = ar.create(session, {"profile_name": "phone-007"})
    v = vr.create(session, "x.mp4", "/tmp/x")
    vr.set_account(session, v, aid)
    items, _ = vr.list_page(
        session, group_id="all", status="all", cursor=None, limit=50)
    assert items[0]["phone_label"] == "phone-007"


def _set_terminal_ts(session, pub_id, *, last_checked, posted=None):
    session.execute(
        text("UPDATE publications "
             "SET last_checked_at = :lc, posted_at = COALESCE(:pa, posted_at) "
             "WHERE id = :pid"),
        {"lc": last_checked, "pa": posted, "pid": pub_id})
    session.commit()


def test_list_page_handled_orders_by_recency_not_video_id(session):
    """Handled tab: oldest-uploaded video that finished MOST RECENTLY
    should be on top, not the newest-uploaded video."""
    from datetime import datetime
    v_old = vr.create(session, "old.mp4", "/tmp/old")     # smaller id
    v_new = vr.create(session, "new.mp4", "/tmp/new")     # larger id
    p_old = _seed_pub(session, v_old, "tiktok", result="SUCCESS")
    p_new = _seed_pub(session, v_new, "tiktok", result="SUCCESS")
    # v_new finished EARLIER; v_old finished LATER.
    _set_terminal_ts(session, p_new, last_checked=datetime(2026, 1, 1, 0, 0))
    _set_terminal_ts(session, p_old, last_checked=datetime(2026, 6, 1, 0, 0))

    items, _ = vr.list_page(
        session, group_id="all", status="handled", cursor=None, limit=50)
    assert [r["id"] for r in items] == [v_old, v_new]


def test_list_page_handled_cursor_pagination(session):
    from datetime import datetime
    vids = [vr.create(session, f"v{i}.mp4", f"/tmp/v{i}") for i in range(4)]
    pubs = [_seed_pub(session, v, "tiktok", result="SUCCESS") for v in vids]
    # Recency order (newest first): v0, v1, v2, v3 — opposite of id order.
    for i, pid in enumerate(pubs):
        _set_terminal_ts(
            session, pid, last_checked=datetime(2026, 6, 10 - i, 0, 0))

    page1, c1 = vr.list_page(
        session, group_id="all", status="handled", cursor=None, limit=2)
    assert [r["id"] for r in page1] == [vids[0], vids[1]]
    assert isinstance(c1, str) and "|" in c1

    page2, c2 = vr.list_page(
        session, group_id="all", status="handled", cursor=c1, limit=2)
    assert [r["id"] for r in page2] == [vids[2], vids[3]]
    assert c2 is None


def test_list_page_handled_falls_back_to_posted_at(session):
    """A publication that went straight to FAILURE via mark_failure has
    posted_at set but last_checked_at NULL — COALESCE must pick up posted_at."""
    from datetime import datetime
    v_a = vr.create(session, "a.mp4", "/tmp/a")
    v_b = vr.create(session, "b.mp4", "/tmp/b")
    p_a = _seed_pub(session, v_a, "tiktok", result="FAILURE")
    p_b = _seed_pub(session, v_b, "tiktok", result="FAILURE")
    # v_a finished EARLIER (older posted_at); v_b finished LATER.
    session.execute(
        text("UPDATE publications SET last_checked_at = NULL, "
             "posted_at = :pa WHERE id = :pid"),
        {"pa": datetime(2026, 5, 1, 0, 0), "pid": p_a})
    session.execute(
        text("UPDATE publications SET last_checked_at = NULL, "
             "posted_at = :pa WHERE id = :pid"),
        {"pa": datetime(2026, 6, 1, 0, 0), "pid": p_b})
    session.commit()

    items, _ = vr.list_page(
        session, group_id="all", status="handled", cursor=None, limit=50)
    assert [r["id"] for r in items] == [v_b, v_a]


def test_list_page_non_handled_still_orders_by_id_desc(session):
    """Sanity check: changing handled sort must NOT affect other tabs."""
    ids = [vr.create(session, f"v{i}.mp4", f"/tmp/v{i}") for i in range(3)]
    items, c = vr.list_page(
        session, group_id="all", status="pending", cursor=None, limit=50)
    assert [r["id"] for r in items] == [ids[2], ids[1], ids[0]]
    assert c is None


def test_list_page_status_pending_excludes_terminal(session):
    v1 = vr.create(session, "a.mp4", "/tmp/a")
    _seed_pub(session, v1, "tiktok", result="SUCCESS")
    _seed_pub(session, v1, "youtube", result="FAILURE")
    v2 = vr.create(session, "b.mp4", "/tmp/b")
    _seed_pub(session, v2, "tiktok", result="PENDING")

    items, _ = vr.list_page(
        session, group_id="all", status="pending", cursor=None, limit=50)
    assert [r["id"] for r in items] == [v2]


def test_list_page_group_ungrouped(session):
    g = gr.create(session, name="g")
    v1 = vr.create(session, "a.mp4", "/tmp/a", group_id=g)
    v2 = vr.create(session, "b.mp4", "/tmp/b", group_id=None)
    items, _ = vr.list_page(
        session, group_id="ungrouped", status="all",
        cursor=None, limit=50)
    assert [r["id"] for r in items] == [v2]


def test_list_page_group_id_int_filter(session):
    g1 = gr.create(session, name="g1")
    g2 = gr.create(session, name="g2")
    v1 = vr.create(session, "a.mp4", "/tmp/a", group_id=g1)
    v2 = vr.create(session, "b.mp4", "/tmp/b", group_id=g1)
    vr.create(session, "c.mp4", "/tmp/c", group_id=g2)
    vr.create(session, "d.mp4", "/tmp/d", group_id=None)

    items, _ = vr.list_page(
        session, group_id=g1, status="all", cursor=None, limit=50)
    assert {r["id"] for r in items} == {v1, v2}
    for r in items:
        assert r["group_id"] == g1


def test_get_detail_returns_none_for_missing(session):
    assert vr.get_detail(session, 99999) is None


def test_get_detail_returns_full_publications_array(session):
    aid = ar.create(session, {"profile_name": "phone-x"})
    v = vr.create(session, "x.mp4", "/tmp/x")
    _seed_pub(session, v, "tiktok", account_id=aid, result="SUCCESS")
    _seed_pub(session, v, "youtube", result="FAILURE")

    d = vr.get_detail(session, v)
    assert d is not None
    assert d["id"] == v
    assert d["original_filename"] == "x.mp4"
    assert d["account_profile_name"] is None  # video has no owner assigned
    assert len(d["publications"]) == 2
    by_plat = {p["platform"]: p for p in d["publications"]}
    assert by_plat["tiktok"]["result"] == "SUCCESS"
    assert by_plat["tiktok"]["account_profile_name"] == "phone-x"
    assert by_plat["youtube"]["result"] == "FAILURE"
    assert by_plat["youtube"]["account_profile_name"] is None


def test_set_understanding_persists_facts_and_status(db):
    from app.repositories import videos as vid_repo
    vid = vid_repo.create(db, "v.mp4", "/tmp/v.mp4")
    vid_repo.set_understanding(
        db, vid,
        facts={"opening_2s": "x", "vibe": "y"},
        status="ok",
    )
    row = vid_repo.get(db, vid)
    assert row["understanding"]["opening_2s"] == "x"
    assert row["understanding_status"] == "ok"


def test_set_understanding_failed_clears_facts(db):
    from app.repositories import videos as vid_repo
    vid = vid_repo.create(db, "v.mp4", "/tmp/v.mp4")
    vid_repo.set_understanding(db, vid, facts=None, status="failed")
    row = vid_repo.get(db, vid)
    assert row["understanding"] is None
    assert row["understanding_status"] == "failed"
