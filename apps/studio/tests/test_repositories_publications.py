"""Publications repository tests. Uses the conftest `session` fixture
(PG mkt_test, truncated before each test) and `_truncate_tables` autouse."""
import pytest

from app.repositories import accounts as acc_repo
from app.repositories import videos as vid_repo
from app.repositories import publications as pub_repo


def _seed(session):
    aid = acc_repo.create(session, {"profile_name": "A1",
                                     "geelark_phone_id": "ph1"})
    vid = vid_repo.create(session, "v.mp4", "/x/v.mp4")
    return aid, vid


def _seed_with_account(session):
    """Like _seed, but also stamps the account onto the video so the
    JOIN-based reads (list_pending/list_initiated) surface an account_id."""
    aid, vid = _seed(session)
    vid_repo.set_account(session, vid, aid)
    return aid, vid


def test_create_and_list_for_video(session):
    _, vid = _seed(session)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    assert pid > 0
    rows = pub_repo.list_for_video(session, vid)
    assert len(rows) == 1
    assert rows[0]["platform"] == "tiktok"
    assert rows[0]["result"] == "PENDING"


def test_unique_video_id_platform(session):
    from sqlalchemy.exc import IntegrityError
    _, vid = _seed(session)
    pub_repo.create(session, video_id=vid, platform="tiktok")
    try:
        pub_repo.create(session, video_id=vid, platform="tiktok")
    except IntegrityError:
        session.rollback()
        return
    assert False, "expected IntegrityError on duplicate (video_id, platform)"


def test_create_for_each_platform(session):
    _, vid = _seed(session)
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        pub_repo.create(session, video_id=vid, platform=plat)
    rows = pub_repo.list_for_video(session, vid)
    assert sorted(r["platform"] for r in rows) == \
        ["facebook", "instagram", "tiktok", "youtube"]


def test_mark_initiated_and_set_result(session):
    aid, vid = _seed(session)
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="instagram")
    pub_repo.mark_initiated(session, pid, "cap", "ig1", "submitted")
    row = pub_repo.get(session, pid)
    assert row["result"] == "INITIATED"
    assert row["account_id"] == aid
    assert row["geelark_task_id"] == "ig1"
    assert row["posted_at"] is not None
    pub_repo.set_result(session, pid, "SUCCESS", "done")
    row = pub_repo.get(session, pid)
    assert row["result"] == "SUCCESS"
    assert row["last_checked_at"] is not None


def test_mark_failure(session):
    aid, vid = _seed(session)
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="facebook")
    pub_repo.mark_failure(session, pid, "upload failed")
    row = pub_repo.get(session, pid)
    assert row["result"] == "FAILURE"
    assert row["notes"] == "upload failed"


def test_schedule_retry_keeps_publication_pending_until_retry_time(session):
    from datetime import datetime, timedelta

    aid, vid = _seed(session)
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    retry_at = datetime.utcnow() + timedelta(minutes=5)

    pub_repo.set_result(session, pid, "FAILURE", "upload failed")
    pub_repo.schedule_retry(
        session,
        pid,
        notes="upload failed: GeeLark upload 502",
        next_retry_at=retry_at,
        fail_code=502,
        fail_screenshot="https://img.example/fail.jpg",
    )

    row = pub_repo.get(session, pid)
    assert row["result"] == "PENDING"
    assert row["retry_count"] == 1
    assert row["next_retry_at"] == retry_at
    assert row["notes"] == "upload failed: GeeLark upload 502"
    assert row["fail_code"] == 502
    assert row["fail_screenshot"] == "https://img.example/fail.jpg"

    pub_repo.mark_queued(session, pid)
    queued = pub_repo.get(session, pid)
    assert queued["result"] == "QUEUED"
    assert queued["next_retry_at"] is None


def test_reset_failed_for_retry_returns_failure_to_pending(session):
    from datetime import datetime

    aid, vid = _seed(session)
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.set_result(session, pid, "FAILURE", "blocked",
                        fail_code=20267,
                        fail_screenshot="https://img.example/fail.jpg")

    assert pub_repo.reset_failed_for_retry(session, pid) is True

    row = pub_repo.get(session, pid)
    assert row["result"] == "PENDING"
    assert row["retry_count"] == 0
    assert row["next_retry_at"] is None
    assert row["fail_code"] is None
    assert row["fail_screenshot"] is None
    assert isinstance(row["last_checked_at"], datetime)


def test_set_result_stores_failure_detail(session):
    """A FAILURE may carry GeeLark's failCode + the failure-point
    screenshot URL so the dashboard can show where the RPA got stuck."""
    aid, vid = _seed(session)
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="facebook")
    pub_repo.mark_initiated(session, pid, "c", "fb1", "submitted")
    pub_repo.set_result(session, pid, "FAILURE", "Reel is empty",
                        fail_code=20267,
                        fail_screenshot="https://img.example/fail.jpg")
    row = pub_repo.get(session, pid)
    assert row["result"] == "FAILURE"
    assert row["notes"] == "Reel is empty"
    assert row["fail_code"] == 20267
    assert row["fail_screenshot"] == "https://img.example/fail.jpg"
    # A plain SUCCESS leaves the failure columns untouched (None).
    pid2 = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.set_result(session, pid2, "SUCCESS", "completed")
    row2 = pub_repo.get(session, pid2)
    assert row2["fail_code"] is None
    assert row2["fail_screenshot"] is None


def test_record_attempt_numbers_and_classifies_failure(session):
    aid, vid = _seed(session)
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="facebook")

    first = pub_repo.record_attempt(
        session,
        pid,
        stage="caption",
        source="gemini",
        message="Gemini timed out",
        error_code="timeout",
    )
    second = pub_repo.record_attempt(
        session,
        pid,
        stage="status_check",
        source="geelark",
        message="Reel is empty",
        error_code="20267",
        fail_screenshot="https://img.example/fail.jpg",
        geelark_task_id="fb-task-1",
    )

    assert first > 0
    assert second > first
    rows = pub_repo.list_attempts(session, pid)
    assert [row["attempt_no"] for row in rows] == [1, 2]
    assert rows[0]["source"] == "gemini"
    assert rows[0]["stage"] == "caption"
    assert rows[0]["message"] == "Gemini timed out"
    assert rows[0]["error_code"] == "timeout"
    assert rows[1]["source"] == "geelark"
    assert rows[1]["stage"] == "status_check"
    assert rows[1]["message"] == "Reel is empty"
    assert rows[1]["error_code"] == "20267"
    assert rows[1]["fail_screenshot"] == "https://img.example/fail.jpg"
    assert rows[1]["geelark_task_id"] == "fb-task-1"
    assert rows[1]["created_at"] is not None


def test_mark_queued(session):
    aid, vid = _seed(session)
    # Legacy video-level assignment is copied to the publication on queue.
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="youtube")
    pub_repo.mark_queued(session, pid)
    row = pub_repo.get(session, pid)
    assert row["result"] == "QUEUED"
    assert row["account_id"] == aid


def test_mark_queued_noop_when_already_initiated(session):
    """Regression: stale defer/race must not flip INITIATED back to QUEUED.

    Once a publication has been submitted to GeeLark (result=INITIATED with
    a task_id), re-marking it as QUEUED would let auto_publish_scan treat
    it as fresh work and submit it a second time. See pub 6180 incident
    (2026-06-09): same video posted 3× to TikTok because mark_queued/unclaim
    blindly clobbered the INITIATED state."""
    aid, vid = _seed_with_account(session)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(session, pid, "cap", "geelark-tid-1", "ok")
    pub_repo.mark_queued(session, pid)
    row = pub_repo.get(session, pid)
    assert row["result"] == "INITIATED"
    assert row["geelark_task_id"] == "geelark-tid-1"


def test_unclaim_noop_when_initiated(session):
    """Regression: PhoneBusy on a stale defer must not unclaim an
    already-INITIATED publication back to PENDING. See pub 6180 incident."""
    aid, vid = _seed_with_account(session)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(session, pid, "cap", "geelark-tid-1", "ok")
    pub_repo.unclaim(session, pid)
    row = pub_repo.get(session, pid)
    assert row["result"] == "INITIATED"


def test_unclaim_noop_when_success(session):
    aid, vid = _seed_with_account(session)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(session, pid, "cap", "geelark-tid-1", "ok")
    pub_repo.set_result(session, pid, "SUCCESS", "completed")
    pub_repo.unclaim(session, pid)
    row = pub_repo.get(session, pid)
    assert row["result"] == "SUCCESS"


def test_unclaim_resets_queued_to_pending(session):
    """Normal path: PhoneBusy on a legitimately-QUEUED publication must
    still flip it back to PENDING so auto_publish_scan can retry."""
    aid, vid = _seed_with_account(session)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_queued(session, pid)
    pub_repo.unclaim(session, pid)
    row = pub_repo.get(session, pid)
    assert row["result"] == "PENDING"


def test_list_pending_filtered_by_platform(session):
    _, vid = _seed(session)
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        pub_repo.create(session, video_id=vid, platform=plat)
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        rows = pub_repo.list_pending(session, platform=plat)
        assert len(rows) == 1
        assert rows[0]["platform"] == plat
        # JOIN videos: stored_path comes through
        assert rows[0]["stored_path"] == "/x/v.mp4"
        assert rows[0]["original_filename"] == "v.mp4"


@pytest.mark.no_auto_prompt
def test_list_pending_require_group_skips_ungrouped_and_no_prompt(session):
    """require_group=True INNER-joins groups + filters caption_prompt_id NOT
    NULL. Reproduces the 2026-06-15 incident: auto_publish_scan's limit=50
    was getting eaten by ungrouped PENDINGs (oldest in id-order), so
    plan_assignments returned 0 every run for ~3 days."""
    from app.models import Group
    from app.repositories import groups as group_repo
    from app.repositories import caption_prompts as cp_repo

    # Ungrouped video (oldest id — must be filtered out).
    v_ungrouped = vid_repo.create(session, "ungrouped.mp4", "/x/ungrouped.mp4")
    # Group without caption prompt (must be filtered out).
    g_no_prompt = group_repo.create(session, "no-prompt")
    v_no_prompt = vid_repo.create(session, "noprompt.mp4", "/x/noprompt.mp4",
                                  group_id=g_no_prompt)
    # Group with caption prompt — only this one survives.
    cp_id = cp_repo.create(session, name="p1", body="brief body")
    g_ok = group_repo.create(session, "with-prompt")
    grp = session.get(Group, g_ok)
    grp.caption_prompt_id = cp_id
    session.add(grp)
    session.commit()
    v_ok = vid_repo.create(session, "ok.mp4", "/x/ok.mp4", group_id=g_ok)

    for v in (v_ungrouped, v_no_prompt, v_ok):
        pub_repo.create(session, video_id=v, platform="tiktok")

    # Default: all three returned (back-compat for manual publish flow).
    all_rows = pub_repo.list_pending(session, platform="tiktok")
    assert {r["video_id"] for r in all_rows} == {
        v_ungrouped, v_no_prompt, v_ok,
    }

    # require_group=True: only the grouped+prompt one.
    filtered = pub_repo.list_pending(session, platform="tiktok",
                                     require_group=True)
    assert [r["video_id"] for r in filtered] == [v_ok]


@pytest.mark.no_auto_prompt
def test_list_pending_require_group_limit_applies_after_filter(session):
    """The real-prod bug shape: limit=N must apply AFTER the group filter,
    else the oldest N ungrouped rows poison the budget."""
    from app.models import Group
    from app.repositories import groups as group_repo
    from app.repositories import caption_prompts as cp_repo

    # 3 ungrouped first (oldest ids).
    for i in range(3):
        v = vid_repo.create(session, f"u{i}.mp4", f"/x/u{i}.mp4")
        pub_repo.create(session, video_id=v, platform="tiktok")

    # Group with prompt.
    cp_id = cp_repo.create(session, name="p1", body="brief body")
    g_ok = group_repo.create(session, "with-prompt")
    grp = session.get(Group, g_ok)
    grp.caption_prompt_id = cp_id
    session.add(grp)
    session.commit()

    # 2 grouped (newer ids).
    grouped_vids = []
    for i in range(2):
        v = vid_repo.create(session, f"g{i}.mp4", f"/x/g{i}.mp4",
                            group_id=g_ok)
        grouped_vids.append(v)
        pub_repo.create(session, video_id=v, platform="tiktok")

    # limit=2 with require_group=True must yield 2 grouped rows — NOT the
    # 2 oldest ungrouped (which would be silently filtered to 0).
    rows = pub_repo.list_pending(session, platform="tiktok",
                                 limit=2, require_group=True)
    assert [r["video_id"] for r in rows] == grouped_vids


@pytest.mark.no_auto_prompt
def test_list_pending_in_groups_restricts_to_eligible_groups(session):
    """Second-layer window-poisoning case (2026-06-16): grouped publications
    whose group has no platform-logged-in account still poison the id-ASC
    LIMIT window. plan_assignments needs to pre-compute eligible groups and
    pass them so list_pending only returns rows that can plausibly be
    assigned this round."""
    from app.models import Group
    from app.repositories import groups as group_repo
    from app.repositories import caption_prompts as cp_repo

    cp_id = cp_repo.create(session, name="p1", body="brief body")
    group_ids = []
    for name in ("A", "B", "C"):
        gid = group_repo.create(session, name)
        grp = session.get(Group, gid)
        grp.caption_prompt_id = cp_id
        session.add(grp)
        session.commit()
        group_ids.append(gid)
    g_a, g_b, g_c = group_ids

    pids_by_group = {}
    for gid in group_ids:
        v = vid_repo.create(session, f"v{gid}.mp4", f"/x/v{gid}.mp4",
                            group_id=gid)
        pids_by_group[gid] = pub_repo.create(
            session, video_id=v, platform="tiktok")

    # in_groups=None (default) → all 3 returned, oldest first.
    all_rows = pub_repo.list_pending(session, platform="tiktok",
                                     require_group=True)
    assert [r["id"] for r in all_rows] == [pids_by_group[g] for g in group_ids]

    # in_groups=[B, C] → only B's and C's publications, A is filtered in SQL.
    filtered = pub_repo.list_pending(session, platform="tiktok",
                                     require_group=True,
                                     in_groups=[g_b, g_c])
    assert [r["id"] for r in filtered] == [
        pids_by_group[g_b], pids_by_group[g_c]]

    # in_groups=[] → empty (no eligible groups means no work).
    none_eligible = pub_repo.list_pending(session, platform="tiktok",
                                          require_group=True,
                                          in_groups=[])
    assert none_eligible == []


def test_list_initiated_returns_all_platforms(session):
    aid, vid = _seed(session)
    vid_repo.set_account(session, vid, aid)
    pids = {}
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        pids[plat] = pub_repo.create(session, video_id=vid, platform=plat)
    pub_repo.mark_initiated(session, pids["tiktok"], "c", "tk1", "n")
    pub_repo.mark_initiated(session, pids["facebook"], "c", "fb1", "n")
    rows = pub_repo.list_initiated(session)
    assert sorted(r["id"] for r in rows) == sorted([pids["tiktok"],
                                                     pids["facebook"]])


def test_list_completed_for_account_filters_and_embeds_video(session):
    aid, vid = _seed(session)
    other_aid = acc_repo.create(session, {"profile_name": "Other",
                                          "geelark_phone_id": "ph-other"})
    other_vid = vid_repo.create(session, "other.mp4", "/x/other.mp4")
    vid_repo.set_account(session, vid, aid)
    vid_repo.set_account(session, other_vid, other_aid)

    success = pub_repo.create(session, video_id=vid, platform="tiktok")
    failure = pub_repo.create(session, video_id=vid, platform="youtube")
    pending = pub_repo.create(session, video_id=vid, platform="instagram")
    other = pub_repo.create(session, video_id=other_vid, platform="tiktok")

    pub_repo.mark_initiated(session, success, "cap-ok", "tk-ok", "submitted")
    pub_repo.set_result(session, success, "SUCCESS", "done")
    pub_repo.mark_initiated(session, failure, "cap-fail", "yt-fail", "submitted")
    pub_repo.set_result(session, failure, "FAILURE", "blocked",
                        fail_code=22,
                        fail_screenshot="https://img.example/fail.jpg")
    pub_repo.mark_queued(session, pending)
    pub_repo.mark_initiated(session, other, "cap-other", "tk-other", "submitted")
    pub_repo.set_result(session, other, "SUCCESS", "done")

    rows = pub_repo.list_completed_for_account(session, aid)

    assert [r["id"] for r in rows] == [failure, success]
    assert {r["result"] for r in rows} == {"SUCCESS", "FAILURE"}
    assert all(r["account_id"] == aid for r in rows)
    assert rows[0]["original_filename"] == "v.mp4"
    assert rows[0]["stored_path"] == "/x/v.mp4"
    assert rows[0]["fail_code"] == 22
    assert rows[0]["fail_screenshot"] == "https://img.example/fail.jpg"


def test_cascade_delete_when_video_deleted(session):
    _, vid = _seed(session)
    for plat in ("tiktok", "youtube", "instagram", "facebook"):
        pub_repo.create(session, video_id=vid, platform=plat)
    vid_repo.delete(session, vid)
    assert pub_repo.list_for_video(session, vid) == []


def test_set_account_null_on_account_delete(session):
    aid, vid = _seed(session)
    vid_repo.set_account(session, vid, aid)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.mark_initiated(session, pid, "c", "t", "n")
    acc_repo.delete(session, aid)
    # The publication row survives; the FK SET NULL clears both legacy
    # video assignment and per-publication assignment.
    assert pub_repo.get(session, pid) is not None
    assert vid_repo.get(session, vid)["account_id"] is None
    assert pub_repo.get(session, pid)["account_id"] is None


def test_video_ids_all_terminal(session):
    _, v1 = _seed(session)
    v2 = vid_repo.create(session, "v2.mp4", "/x/v2.mp4")
    v3 = vid_repo.create(session, "v3.mp4", "/x/v3.mp4")  # no publications
    aid = acc_repo.create(session, {"profile_name": "B",
                                     "geelark_phone_id": "ph2"})
    vid_repo.set_account(session, v2, aid)
    p1 = pub_repo.create(session, video_id=v1, platform="tiktok")
    p2 = pub_repo.create(session, video_id=v1, platform="youtube")
    p3 = pub_repo.create(session, video_id=v2, platform="tiktok")
    pub_repo.set_result(session, p1, "SUCCESS", "ok")
    pub_repo.set_result(session, p2, "FAILURE", "err")
    pub_repo.mark_initiated(session, p3, "c", "tk", "n")
    terminal = pub_repo.video_ids_all_terminal(session)
    assert v1 in terminal
    assert v2 not in terminal
    assert v3 not in terminal


def test_set_share_link(session):
    from app.repositories import videos as vid_repo
    from app.repositories import publications as pub_repo

    vid = vid_repo.create(session, "s.mp4", "/tmp/s.mp4")
    pub_id = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.set_share_link(session, pub_id, "https://www.tiktok.com/@owner/video/1234567890")

    row = pub_repo.get(session, pub_id)
    assert row["share_link"] == "https://www.tiktok.com/@owner/video/1234567890"
    assert row["share_link_captured_at"] is not None
