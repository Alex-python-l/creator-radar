"""videos.list_all must not scale queries with the number of videos.

The original implementation called publications.list_for_video inside
a per-video loop, producing 1 + N queries for N videos. On the prod DB
(124 videos / 251 publications) that was 125 round-trips and ~230ms
end-to-end on the API. These tests pin the new behavior: a small,
constant number of SQL statements regardless of how many videos exist,
with the same per-row shape consumers already depend on (publications
ordered by platform, account_profile_name embedded).
"""
from sqlalchemy import event

from app.db import engine
from app.repositories import accounts as ar
from app.repositories import groups as gr
from app.repositories import publications as pr
from app.repositories import videos as vr


def _count_queries(fn):
    count = {"n": 0}

    def _listener(conn, cursor, statement, params, context, executemany):
        # Only SELECT against application tables count toward N+1; ignore
        # transactional BEGIN/COMMIT bookkeeping.
        if statement.lstrip().upper().startswith("SELECT"):
            count["n"] += 1

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", _listener)
    return result, count["n"]


def test_list_all_query_count_does_not_scale_with_row_count(session):
    aid = ar.create(session, {"profile_name": "RPN-acc"})
    gid = gr.create(session, name="rpn")

    def _seed(n_videos: int):
        for i in range(n_videos):
            v = vr.create(session, f"v{i}.mp4", f"/tmp/v{i}.mp4", group_id=gid)
            for plat in ("tiktok", "youtube"):
                pid = pr.find_or_create(session, video_id=v, platform=plat)
                pr.set_account(session, pid, aid)

    _seed(3)
    rows_small, q_small = _count_queries(lambda: vr.list_all(session))
    assert len(rows_small) == 3

    _seed(12)  # 15 videos total
    rows_big, q_big = _count_queries(lambda: vr.list_all(session))
    assert len(rows_big) == 15

    # The smoking gun for N+1 was q_big = 1 + 15 = 16 and q_small = 1 +
    # 3 = 4. A constant-overhead implementation should stay within a
    # small fixed budget no matter how many videos there are. Allow a
    # little slack for framework-internal selects but reject any growth
    # that tracks row count.
    assert q_big <= 5, f"too many queries for 15 videos: {q_big}"
    assert q_big - q_small <= 1, (
        f"queries scaled with row count: {q_small} -> {q_big}")


def test_list_all_preserves_shape_with_publications_and_phone(session):
    """Consumers (frontend Videos page, dashboard) read:
      - publications[] ordered by platform name
      - publication.account_profile_name resolved from account_id
      - video.account_profile_name resolved from video.account_id"""
    aid_v = ar.create(session, {"profile_name": "owner-of-video"})
    aid_p = ar.create(session, {"profile_name": "owner-of-publication"})
    gid = gr.create(session, name="shape")

    v = vr.create(session, "shape.mp4", "/tmp/shape.mp4", group_id=gid)
    vr.set_account(session, v, aid_v)
    # Reset the publication account_id back to a different phone so we
    # can prove list_all resolves it independently of video.account_id.
    pid_yt = pr.find_or_create(session, video_id=v, platform="youtube")
    pid_tt = pr.find_or_create(session, video_id=v, platform="tiktok")
    pr.set_account(session, pid_yt, aid_p)
    pr.set_account(session, pid_tt, aid_p)

    rows = vr.list_all(session)
    assert len(rows) == 1
    row = rows[0]
    assert row["account_profile_name"] == "owner-of-video"
    pubs = row["publications"]
    assert [p["platform"] for p in pubs] == ["tiktok", "youtube"]
    assert all(p["account_profile_name"] == "owner-of-publication"
               for p in pubs)


def test_list_all_video_with_no_publications_still_appears(session):
    """A freshly uploaded video that has no publication rows yet must
    still show up — the Videos page lists pending uploads."""
    gid = gr.create(session, name="np")
    vr.create(session, "lonely.mp4", "/tmp/lonely.mp4", group_id=gid)
    rows = vr.list_all(session)
    assert len(rows) == 1
    assert rows[0]["publications"] == []
