from datetime import datetime


def test_engagement_models_persist_task_and_run(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.models import EngagementRun, EngagementTask

    gid = group_repo.create(session, name="Self Engage")
    aid = acc_repo.create(session, {
        "profile_name": "M1",
        "geelark_phone_id": "ph-m1",
        "group_id": gid,
        "tiktok_logged_in": 1,
    })

    task = EngagementTask(
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    session.add(task)
    session.commit()
    session.refresh(task)

    run = EngagementRun(
        engagement_task_id=task.id,
        account_id=aid,
        target_link="https://www.tiktok.com/@owner/video/1234567890",
        action="tiktok_ai_comment",
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    assert task.status == "PENDING"
    assert task.target_links == [
        "https://www.tiktok.com/@owner/video/1234567890"
    ]
    assert run.result == "PENDING"
    assert run.account_id == aid


def test_publication_share_link_fields_persist(session):
    from app.models import Publication, Video

    video = Video(original_filename="share.mp4", stored_path="/tmp/share.mp4")
    session.add(video)
    session.commit()
    session.refresh(video)

    captured_at = datetime(2026, 6, 4, 12, 30, 0)
    publication = Publication(
        video_id=video.id,
        platform="tiktok",
        share_link="https://www.tiktok.com/@owner/video/1234567890",
        share_link_captured_at=captured_at,
    )
    session.add(publication)
    session.commit()
    session.refresh(publication)

    assert publication.share_link == (
        "https://www.tiktok.com/@owner/video/1234567890"
    )
    assert publication.share_link_captured_at == captured_at


def test_create_engagement_task_and_run(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo

    gid = group_repo.create(session, name="Self Engage Repo")
    aid = acc_repo.create(session, {
        "profile_name": "M1",
        "geelark_phone_id": "ph-m1",
        "group_id": gid,
        "tiktok_logged_in": 1,
    })

    task_id = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.create_run(
        session,
        engagement_task_id=task_id,
        account_id=aid,
        target_link="https://www.tiktok.com/@owner/video/1234567890",
        action="tiktok_ai_comment",
    )

    task = eng_repo.get_task(session, task_id)
    run = eng_repo.get_run(session, run_id)

    assert task["status"] == "PENDING"
    assert task["target_links"] == [
        "https://www.tiktok.com/@owner/video/1234567890"
    ]
    assert run["result"] == "PENDING"
    assert run["account_id"] == aid


def test_engagement_run_lifecycle_and_parent_status(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo

    gid = group_repo.create(session, name="Lifecycle")
    aid = acc_repo.create(session, {
        "profile_name": "M2",
        "geelark_phone_id": "ph-m2",
        "group_id": gid,
        "tiktok_logged_in": 1,
    })
    task_id = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.create_run(
        session,
        engagement_task_id=task_id,
        account_id=aid,
        target_link="https://www.tiktok.com/@owner/video/1234567890",
        action="tiktok_ai_comment",
    )

    eng_repo.mark_queued(session, run_id)
    assert eng_repo.account_has_inflight(session, aid) is True

    eng_repo.mark_initiated(
        session,
        run_id,
        task_id="gee-1",
        schedule_at=123,
        notes="submitted to GeeLark",
    )
    row = eng_repo.get_run(session, run_id)
    assert row["result"] == "INITIATED"
    assert row["geelark_task_id"] == "gee-1"

    assert eng_repo.mark_queued(session, run_id) is False
    assert eng_repo.get_run(session, run_id)["result"] == "INITIATED"

    eng_repo.set_result(session, run_id, "SUCCESS", "completed", cost=135)
    eng_repo.recompute_task_status(session, task_id)

    assert eng_repo.account_has_inflight(session, aid) is False
    assert eng_repo.get_run(session, run_id)["cost"] == 135
    assert eng_repo.get_task(session, task_id)["status"] == "SUCCESS"


def test_parent_status_partial_failure(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo

    gid = group_repo.create(session, name="Partial")
    a1 = acc_repo.create(session, {"profile_name": "A1", "group_id": gid})
    a2 = acc_repo.create(session, {"profile_name": "A2", "group_id": gid})
    task_id = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    r1 = eng_repo.create_run(session, engagement_task_id=task_id, account_id=a1,
                             target_link="https://www.tiktok.com/@owner/video/1234567890",
                             action="tiktok_ai_comment")
    r2 = eng_repo.create_run(session, engagement_task_id=task_id, account_id=a2,
                             target_link="https://www.tiktok.com/@owner/video/1234567890",
                             action="tiktok_ai_comment")
    eng_repo.set_result(session, r1, "SUCCESS", "completed")
    eng_repo.set_result(session, r2, "FAILURE", "blocked",
                        fail_code=88, fail_screenshot="https://img/fail.jpg")
    eng_repo.recompute_task_status(session, task_id)

    assert eng_repo.get_task(session, task_id)["status"] == "PARTIAL_FAILURE"
    assert eng_repo.get_run(session, r2)["fail_screenshot"] == "https://img/fail.jpg"


def test_list_tasks_filters_by_group_newest_first_and_clamps_limit(session):
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo

    gid = group_repo.create(session, name="List Group")
    other_gid = group_repo.create(session, name="Other List Group")
    older_id = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/111"],
        target_publication_ids=[],
        comment_probability=100,
    )
    other_id = eng_repo.create_task(
        session,
        group_id=other_gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/222"],
        target_publication_ids=[],
        comment_probability=100,
    )
    newer_id = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/333"],
        target_publication_ids=[],
        comment_probability=100,
    )

    rows = eng_repo.list_tasks(session, group_id=gid)
    assert [row["id"] for row in rows] == [newer_id, older_id]
    assert other_id not in [row["id"] for row in rows]

    clamped_rows = eng_repo.list_tasks(session, limit=0)
    assert len(clamped_rows) == 1
    assert clamped_rows[0]["id"] == newer_id


def test_account_has_predecessor_inflight_only_blocks_smaller_ids(session):
    """Batch siblings serialize by id — smallest-id sibling has no predecessor
    and may proceed; later siblings wait."""
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="g")
    aid = acc_repo.create(session, {
        "profile_name": "phone", "geelark_phone_id": "ph-1",
        "group_id": gid, "tiktok_logged_in": 1, "warmed_up": 1,
    })
    tid = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@x/video/1"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        comment_brief=None,
        comments_per_video=1,
    )
    r1 = eng_repo.create_run(
        session, engagement_task_id=tid, account_id=aid,
        target_link="https://www.tiktok.com/@x/video/1",
        action="tiktok_ai_comment",
        target_publication_id=None, target_account_id=None,
    )
    r2 = eng_repo.create_run(
        session, engagement_task_id=tid, account_id=aid,
        target_link="https://www.tiktok.com/@x/video/2",
        action="tiktok_ai_comment",
        target_publication_id=None, target_account_id=None,
    )
    r3 = eng_repo.create_run(
        session, engagement_task_id=tid, account_id=aid,
        target_link="https://www.tiktok.com/@x/video/3",
        action="tiktok_ai_comment",
        target_publication_id=None, target_account_id=None,
    )

    # All three are PENDING initially → mark them QUEUED to mimic post-defer state.
    eng_repo.mark_queued(session, r1)
    eng_repo.mark_queued(session, r2)
    eng_repo.mark_queued(session, r3)

    # r1 (smallest id) has no predecessor → may proceed.
    assert eng_repo.account_has_predecessor_inflight(session, aid, r1) is False
    # r2 sees r1 → blocked.
    assert eng_repo.account_has_predecessor_inflight(session, aid, r2) is True
    # r3 sees r1 and r2 → blocked.
    assert eng_repo.account_has_predecessor_inflight(session, aid, r3) is True

    # Once r1 finishes, r2 unblocks.
    eng_repo.set_result(session, r1, "SUCCESS", "done")
    assert eng_repo.account_has_predecessor_inflight(session, aid, r2) is False
    # r3 still waits on r2.
    assert eng_repo.account_has_predecessor_inflight(session, aid, r3) is True


def test_list_runs_for_task_includes_account_profile_name(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import engagement as eng_repo

    gid = group_repo.create(session, name="Run List")
    aid = acc_repo.create(session, {
        "profile_name": "Runner One",
        "geelark_phone_id": "ph-runner",
        "group_id": gid,
        "tiktok_logged_in": 1,
    })
    task_id = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/444"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.create_run(
        session,
        engagement_task_id=task_id,
        account_id=aid,
        target_link="https://www.tiktok.com/@owner/video/444",
        action="tiktok_ai_comment",
    )

    rows = eng_repo.list_runs_for_task(session, task_id)

    assert [row["id"] for row in rows] == [run_id]
    assert rows[0]["account_profile_name"] == "Runner One"


def _seed_run_with_result(session, *, gid: int, aid: int, source: str,
                          result: str, link: str = "https://x/v/1",
                          platform: str = "tiktok") -> int:
    from app.repositories import engagement as eng_repo

    action = "facebook_auto_comment" if platform == "facebook" else "tiktok_ai_comment"
    tid = eng_repo.create_task(
        session,
        group_id=gid,
        action=action,
        source=source,
        target_links=[link],
        target_publication_ids=[],
        comment_probability=100,
        platform=platform,
    )
    rid = eng_repo.create_run(
        session,
        engagement_task_id=tid,
        account_id=aid,
        target_link=link,
        action="tiktok_ai_comment",
    )
    if result == "SUCCESS":
        eng_repo.set_result(session, rid, "SUCCESS", "ok")
    elif result == "FAILURE":
        eng_repo.set_result(session, rid, "FAILURE", "boom")
    elif result == "QUEUED":
        eng_repo.mark_queued(session, rid)
    elif result == "INITIATED":
        eng_repo.mark_queued(session, rid)
        eng_repo.mark_initiated(
            session, rid, task_id="gl-task", schedule_at=0, notes="started",
        )
    # PENDING is the default — no transition needed.
    return rid


def test_list_runs_before_id_returns_only_older_rows(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="Cursor Group")
    aid = acc_repo.create(session, {
        "profile_name": "P", "geelark_phone_id": "ph-c",
        "group_id": gid, "tiktok_logged_in": 1,
    })
    older = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="SUCCESS",
    )
    middle = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="QUEUED",
    )
    newest = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="QUEUED",
    )

    page1 = eng_repo.list_runs(session, limit=2)
    assert [r["id"] for r in page1] == [newest, middle]

    # Compound cursor: page2 reuses the last item's activity_at + id.
    last = page1[-1]
    page2 = eng_repo.list_runs(
        session, limit=2,
        before_activity_at=last["activity_at"], before_id=last["id"],
    )
    assert [r["id"] for r in page2] == [older]


def test_list_runs_ids_filter_returns_requested_rows(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="Ids Group")
    aid = acc_repo.create(session, {
        "profile_name": "P", "geelark_phone_id": "ph-ids",
        "group_id": gid, "tiktok_logged_in": 1,
    })
    older = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="QUEUED",
    )
    _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="SUCCESS",
    )
    newest = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="INITIATED",
    )

    rows = eng_repo.list_runs(session, ids=[older, newest], limit=50)

    assert [r["id"] for r in rows] == [newest, older]
    assert [r["result"] for r in rows] == ["INITIATED", "QUEUED"]


def test_list_runs_orders_by_activity_at_desc(session):
    """Comments page must show 'just-published' rows at the top. Sort
    uses COALESCE(finished_at, started_at, created_at) DESC so:
      - just-finished SUCCESS climbs above older inflight rows
      - INITIATED with newest started_at outranks earlier PENDING/QUEUED
      - tie-breaker is id desc (matches the historical UX)."""
    from datetime import datetime, timedelta

    from sqlmodel import select

    from app.models import EngagementRun
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="Order Group")
    aid = acc_repo.create(session, {
        "profile_name": "P", "geelark_phone_id": "ph-o",
        "group_id": gid, "tiktok_logged_in": 1,
    })

    # Insertion order: A, B, C, D — so without time-sort, ids climb A<B<C<D.
    # We'll then back-date A's created_at, B's started_at, C's finished_at
    # so that timeline order is reversed: most-recent activity is on A, B
    # also recent, C oldest activity, D in the middle.
    a = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="PENDING",
        link="https://x/a",
    )
    b = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="INITIATED",
        link="https://x/b",
    )
    c = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="SUCCESS",
        link="https://x/c",
    )
    d = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="QUEUED",
        link="https://x/d",
    )

    now = datetime.utcnow()
    row_a = session.exec(
        select(EngagementRun).where(EngagementRun.id == a)
    ).one()
    row_b = session.exec(
        select(EngagementRun).where(EngagementRun.id == b)
    ).one()
    row_c = session.exec(
        select(EngagementRun).where(EngagementRun.id == c)
    ).one()
    row_d = session.exec(
        select(EngagementRun).where(EngagementRun.id == d)
    ).one()

    # Activity-at timeline (newest first): A, B, D, C.
    row_a.created_at = now - timedelta(minutes=1)
    row_b.started_at = now - timedelta(minutes=2)
    row_d.created_at = now - timedelta(minutes=3)
    row_c.finished_at = now - timedelta(minutes=4)
    for row in (row_a, row_b, row_c, row_d):
        session.add(row)
    session.commit()

    page = eng_repo.list_runs(session, limit=10)
    assert [r["id"] for r in page] == [a, b, d, c]
    # Each row exposes activity_at for the frontend / pagination cursor.
    for r in page:
        assert r["activity_at"] is not None


def test_list_runs_compound_cursor_does_not_skip_rows(session):
    """The bug we're protecting against: id desc cursor with a
    non-id sort skips rows. The compound (activity_at, id) cursor must
    visit every row exactly once across paginated calls."""
    from datetime import datetime, timedelta

    from sqlmodel import select

    from app.models import EngagementRun
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="Cursor2 Group")
    aid = acc_repo.create(session, {
        "profile_name": "P", "geelark_phone_id": "ph-c2",
        "group_id": gid, "tiktok_logged_in": 1,
    })

    # 5 runs, insertion order = id order, but we'll scramble activity_at
    # so that small ids have NEWER activity than big ones — i.e. id sort
    # would visit them last, activity sort visits them first.
    ids = [
        _seed_run_with_result(
            session, gid=gid, aid=aid, source="manual_links",
            result="PENDING", link=f"https://x/{i}",
        )
        for i in range(5)
    ]
    now = datetime.utcnow()
    # Activity timeline newest → oldest: ids[0], ids[2], ids[4], ids[1], ids[3]
    activity_offsets = [1, 4, 2, 5, 3]
    for rid, off in zip(ids, activity_offsets):
        row = session.exec(
            select(EngagementRun).where(EngagementRun.id == rid)
        ).one()
        row.created_at = now - timedelta(minutes=off)
        session.add(row)
    session.commit()

    expected_order = [ids[0], ids[2], ids[4], ids[1], ids[3]]

    visited: list[int] = []
    cursor: dict | None = None
    while True:
        kwargs: dict = {"limit": 2}
        if cursor:
            kwargs["before_activity_at"] = cursor["activity_at"]
            kwargs["before_id"] = cursor["id"]
        page = eng_repo.list_runs(session, **kwargs)
        if not page:
            break
        visited.extend(r["id"] for r in page)
        cursor = {"activity_at": page[-1]["activity_at"], "id": page[-1]["id"]}
        if len(page) < kwargs["limit"]:
            break

    assert visited == expected_order, (
        f"cursor walk visited {visited}, expected {expected_order}"
    )


def test_list_runs_status_filter_groups_results(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="Status Group")
    aid = acc_repo.create(session, {
        "profile_name": "P", "geelark_phone_id": "ph-s",
        "group_id": gid, "tiktok_logged_in": 1,
    })
    succ = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="SUCCESS",
    )
    fail = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="FAILURE",
    )
    queued = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="QUEUED",
    )
    running = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="INITIATED",
    )

    assert [r["id"] for r in eng_repo.list_runs(session, status="success")] \
        == [succ]
    assert [r["id"] for r in eng_repo.list_runs(session, status="failed")] \
        == [fail]
    assert [r["id"] for r in eng_repo.list_runs(session, status="queued")] \
        == [queued]
    assert [r["id"] for r in eng_repo.list_runs(session, status="running")] \
        == [running]
    # 'all' (default) returns every row, newest first.
    assert [r["id"] for r in eng_repo.list_runs(session)] == [
        running, queued, fail, succ,
    ]


def test_list_runs_source_filter_splits_owned_and_manual(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="Source Group")
    aid = acc_repo.create(session, {
        "profile_name": "P", "geelark_phone_id": "ph-o",
        "group_id": gid, "tiktok_logged_in": 1,
    })
    owned = _seed_run_with_result(
        session, gid=gid, aid=aid,
        source="publication_share_links", result="SUCCESS",
    )
    manual = _seed_run_with_result(
        session, gid=gid, aid=aid,
        source="manual_links", result="SUCCESS",
    )

    assert [r["id"] for r in eng_repo.list_runs(session, source="owned")] \
        == [owned]
    assert [r["id"] for r in eng_repo.list_runs(session, source="manual")] \
        == [manual]


def test_runs_stats_buckets_results_and_respects_source(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="Stats Group")
    aid = acc_repo.create(session, {
        "profile_name": "P", "geelark_phone_id": "ph-st",
        "group_id": gid, "tiktok_logged_in": 1,
    })

    # 2 SUCCESS owned, 1 SUCCESS manual, 1 FAILURE manual,
    # 1 INITIATED owned, 1 QUEUED manual, 1 PENDING manual.
    for _ in range(2):
        _seed_run_with_result(session, gid=gid, aid=aid,
                              source="publication_share_links", result="SUCCESS")
    _seed_run_with_result(session, gid=gid, aid=aid,
                          source="manual_links", result="SUCCESS")
    _seed_run_with_result(session, gid=gid, aid=aid,
                          source="manual_links", result="FAILURE")
    _seed_run_with_result(session, gid=gid, aid=aid,
                          source="publication_share_links", result="INITIATED")
    _seed_run_with_result(session, gid=gid, aid=aid,
                          source="manual_links", result="QUEUED")
    _seed_run_with_result(session, gid=gid, aid=aid,
                          source="manual_links", result="PENDING")

    assert eng_repo.runs_stats(session) == {
        "total": 7, "success": 3, "running": 1, "queued": 2, "failed": 1,
    }
    assert eng_repo.runs_stats(session, source="owned") == {
        "total": 3, "success": 2, "running": 1, "queued": 0, "failed": 0,
    }
    assert eng_repo.runs_stats(session, source="manual") == {
        "total": 4, "success": 1, "running": 0, "queued": 2, "failed": 1,
    }


# ----- count_account_attempts_today --------------------------------------


def test_count_attempts_today_counts_started_runs_not_backlog(session):
    """Queued backlog must not consume quota before a submit attempt starts."""
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="g")
    aid = acc_repo.create(session, {
        "profile_name": "p", "geelark_phone_id": "ph",
        "group_id": gid, "tiktok_logged_in": 1,
    })

    for status in ("SUCCESS", "PENDING", "QUEUED", "INITIATED"):
        _seed_run_with_result(
            session, gid=gid, aid=aid, source="manual_links", result=status,
        )

    n = eng_repo.count_account_attempts_today(
        session, account_id=aid, platform="tiktok",
    )
    assert n == 2  # SUCCESS + INITIATED; PENDING / QUEUED are only backlog


def test_count_attempts_today_counts_started_failure_but_not_pre_submit_failure(
        session):
    """A failed GeeLark attempt is charged; a pre-submit failure is not."""
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="g")
    aid = acc_repo.create(session, {
        "profile_name": "p", "geelark_phone_id": "ph",
        "group_id": gid, "tiktok_logged_in": 1,
    })

    _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="SUCCESS",
    )
    pre_submit_failure = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="FAILURE",
    )
    started_failure = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links", result="INITIATED",
        link="https://x/started-failure",
    )
    eng_repo.set_result(session, started_failure, "FAILURE", "task failed")
    # SKIPPED via mark_skipped
    tid = eng_repo.create_task(
        session, group_id=gid, action="tiktok_ai_comment",
        source="manual_links", target_links=["https://x/skip"],
        target_publication_ids=[], comment_probability=100,
    )
    rid = eng_repo.create_run(
        session, engagement_task_id=tid, account_id=aid,
        target_link="https://x/skip", action="tiktok_ai_comment",
    )
    eng_repo.mark_skipped(session, rid, "ineligible")

    n = eng_repo.count_account_attempts_today(
        session, account_id=aid, platform="tiktok",
    )
    assert pre_submit_failure != started_failure
    assert n == 2  # SUCCESS + the FAILURE that reached GeeLark


def test_count_attempts_today_segregates_platforms(session):
    """tiktok runs are not counted toward facebook quota and vice versa."""
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="g")
    aid = acc_repo.create(session, {
        "profile_name": "p", "geelark_phone_id": "ph",
        "group_id": gid, "tiktok_logged_in": 1, "facebook_logged_in": 1,
    })

    _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="SUCCESS", platform="tiktok",
    )
    _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="SUCCESS", platform="facebook",
    )
    _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="QUEUED", platform="facebook",
        link="https://facebook.com/post/2",
    )

    assert eng_repo.count_account_attempts_today(
        session, account_id=aid, platform="tiktok",
    ) == 1
    assert eng_repo.count_account_attempts_today(
        session, account_id=aid, platform="facebook",
    ) == 1


def test_count_attempts_today_ignores_other_accounts(session):
    """Runs belonging to other accounts do not bleed into this account's count."""
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="g")
    a1 = acc_repo.create(session, {
        "profile_name": "p1", "geelark_phone_id": "ph-1",
        "group_id": gid, "tiktok_logged_in": 1,
    })
    a2 = acc_repo.create(session, {
        "profile_name": "p2", "geelark_phone_id": "ph-2",
        "group_id": gid, "tiktok_logged_in": 1,
    })

    _seed_run_with_result(
        session, gid=gid, aid=a1, source="manual_links", result="SUCCESS",
    )
    _seed_run_with_result(
        session, gid=gid, aid=a2, source="manual_links", result="SUCCESS",
    )
    _seed_run_with_result(
        session, gid=gid, aid=a2, source="manual_links", result="QUEUED",
    )

    assert eng_repo.count_account_attempts_today(
        session, account_id=a1, platform="tiktok",
    ) == 1
    assert eng_repo.count_account_attempts_today(
        session, account_id=a2, platform="tiktok",
    ) == 1


def test_count_attempts_today_excludes_runs_from_yesterday(session):
    """A SUCCESS run with created_at < today_UTC start does not consume quota."""
    from datetime import datetime, timedelta

    from sqlmodel import select

    from app.models import EngagementRun
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="g")
    aid = acc_repo.create(session, {
        "profile_name": "p", "geelark_phone_id": "ph",
        "group_id": gid, "tiktok_logged_in": 1,
    })

    # Seed two SUCCESS runs, then back-date one to yesterday.
    rid_old = _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="SUCCESS", link="https://x/v/old",
    )
    _seed_run_with_result(
        session, gid=gid, aid=aid, source="manual_links",
        result="SUCCESS", link="https://x/v/today",
    )

    yesterday = datetime.utcnow() - timedelta(days=1)
    row = session.exec(
        select(EngagementRun).where(EngagementRun.id == rid_old)
    ).one()
    row.created_at = yesterday
    session.add(row)
    session.commit()

    n = eng_repo.count_account_attempts_today(
        session, account_id=aid, platform="tiktok",
    )
    assert n == 1  # only today's SUCCESS counts
