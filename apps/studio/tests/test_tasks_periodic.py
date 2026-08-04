import asyncio


def test_periodic_registry_matches_current_worker_schedule():
    import app.tasks.auto_publish  # noqa: F401
    import app.tasks.daily_warmup  # noqa: F401
    import app.tasks.proxy_check  # noqa: F401
    import app.tasks.warmup  # noqa: F401
    from app.tasks import procrastinate_app

    periodic = procrastinate_app.periodic_registry.periodic_tasks
    crons = {
        task.task.name: task.cron
        for task in periodic.values()
    }

    assert "app.tasks.warmup.warmup_task" not in crons
    assert "app.tasks.pic_check.pic_check_task" not in crons
    assert crons["auto_publish_scan"] == "0 * * * *"
    assert crons["app.tasks.proxy_check.proxy_check_task"] == "0 4 * * *"
    assert crons["daily_warmup_scan"] == "*/10 * * * *"


def test_phone_mutating_tasks_share_the_phones_queue():
    """phones-queue routing groups GeeLark-touching tasks under one worker
    pool; per-phone lock (set at defer time, not declared on the task) keeps
    same-phone jobs serial while letting different phones run in parallel."""
    from app.tasks.engagement import submit_engagement_run_task
    from app.tasks.publish import publish_publication_task
    from app.tasks.warmup import start_warmup_task

    for task in (start_warmup_task,
                 publish_publication_task, submit_engagement_run_task):
        assert task.queue == "phones"

    # No decorator-level lock; defer sites attach a per-phone lock keyed by
    # account_id.
    for task in (start_warmup_task,
                 publish_publication_task, submit_engagement_run_task):
        assert task.lock is None


def test_read_only_periodic_tasks_do_not_take_the_phones_lock():
    from app.tasks.auto_publish import auto_publish_scan_task
    from app.tasks.daily_warmup import daily_warmup_scan
    from app.tasks.proxy_check import proxy_check_task
    from app.tasks.publish import check_publication_status_task

    for task in (
        auto_publish_scan_task,
        daily_warmup_scan,
        proxy_check_task,
        check_publication_status_task,
    ):
        assert task.queue == "default"
        assert task.lock is None


def test_proxy_check_task_delegates_to_proxy_check_service(monkeypatch):
    from app.tasks import proxy_check as proxy_check_module

    seen = {}

    def fake_run_proxy_check(session):
        seen["session"] = session
        return "1 ok, 0 fail"

    monkeypatch.setattr(
        proxy_check_module.proxy_check_service,
        "run_proxy_check",
        fake_run_proxy_check,
    )

    assert asyncio.run(proxy_check_module.proxy_check_task(timestamp=0)) == (
        "1 ok, 0 fail"
    )
    assert seen["session"] is not None


def test_auto_publish_scan_skips_videos_without_group_id(
    session,
    procrastinate_app,
    tmp_path,
):
    from app.repositories import accounts as acc_repo
    from app.repositories import publications as pub_repo
    from app.repositories import videos as vid_repo
    from app.tasks.auto_publish import auto_publish_scan_task

    acc_repo.create(session, {
        "profile_name": "ungrouped-account",
        "geelark_phone_id": "ph-ungrouped",
        "warmed_up": 1,
        "tiktok_logged_in": 1,
    })
    f = tmp_path / "draft.mp4"
    f.write_bytes(b"x")
    vid = vid_repo.create(session, "draft.mp4", str(f), group_id=None)
    pid = pub_repo.create(session, video_id=vid, platform="tiktok")

    result = asyncio.run(auto_publish_scan_task(timestamp=0))

    assert result == "0 publication(s) deferred"
    assert pub_repo.get(session, pid)["result"] == "PENDING"
    assert len(procrastinate_app.connector.jobs) == 0


def test_daily_warmup_scan_registered_as_cron():
    import app.tasks.daily_warmup  # noqa: F401
    from app.tasks import procrastinate_app
    cron_task_names = {
        d.task.name
        for d in procrastinate_app.periodic_registry.periodic_tasks.values()
    }
    assert "daily_warmup_scan" in cron_task_names


def test_daily_warmup_scan_enqueues_stale_accounts_in_either_mode(
    db, procrastinate_app, monkeypatch,
):
    """daily_warmup_scan covers both modes:
      - daily: warmed_up is irrelevant (kept queued forever)
      - count: in scope only until warmup_count hits target (warmed_up=1)
    Mode-blind filter (warmed_up=0 OR mode='daily') is the contract.
    """
    import asyncio
    from datetime import datetime, timedelta
    from app.tasks import daily_warmup as scan_module
    from app.repositories import accounts as acc_repo

    # Fresh daily-mode account (just warmed) — should NOT be queued
    fresh = acc_repo.create(db, {
        "geelark_phone_id": "p-fresh",
        "profile_name": "fresh",
        "tiktok_logged_in": 1,
        "warmed_up": 0,
        "warmup_mode": "daily",
        "last_warm_at": datetime.utcnow() - timedelta(hours=1),
    })
    # Stale daily-mode account — should be queued
    stale = acc_repo.create(db, {
        "geelark_phone_id": "p-stale",
        "profile_name": "stale",
        "tiktok_logged_in": 1,
        "warmed_up": 0,
        "warmup_mode": "daily",
        "last_warm_at": datetime.utcnow() - timedelta(hours=7),
    })
    # Never-warmed daily-mode account — should be queued
    never = acc_repo.create(db, {
        "geelark_phone_id": "p-never",
        "profile_name": "never",
        "tiktok_logged_in": 1,
        "warmed_up": 0,
        "warmup_mode": "daily",
    })
    # count-mode stale, not yet at target — SHOULD be queued (operator
    # 2026-06-16 clarification: "次数模式，没达到次数之前都要预热")
    count_stale = acc_repo.create(db, {
        "geelark_phone_id": "p-count-stale",
        "profile_name": "count-stale",
        "tiktok_logged_in": 1,
        "warmed_up": 0,
        "warmup_mode": "count",
        "last_warm_at": datetime.utcnow() - timedelta(hours=7),
    })
    # count-mode already at target — should NOT be queued
    count_done = acc_repo.create(db, {
        "geelark_phone_id": "p-count-done",
        "profile_name": "count-done",
        "tiktok_logged_in": 1,
        "warmed_up": 1,
        "warmup_mode": "count",
        "last_warm_at": datetime.utcnow() - timedelta(hours=7),
    })
    # daily-mode already warmed_up — STILL queued. Daily mode means "N
    # warmups per day, forever"; the warmed_up flag is for count-mode only.
    # Accounts converted from count to daily after target carry warmed_up=1
    # from their count-mode days and would silently freeze without this.
    warmed_daily = acc_repo.create(db, {
        "geelark_phone_id": "p-warmed-daily",
        "profile_name": "warmed-daily",
        "tiktok_logged_in": 1,
        "warmed_up": 1,
        "warmup_mode": "daily",
        "last_warm_at": datetime.utcnow() - timedelta(hours=7),
    })

    asyncio.run(scan_module.daily_warmup_scan(timestamp=0))

    jobs = procrastinate_app.connector.jobs
    queued_account_ids = sorted(
        j["args"]["account_id"] for j in jobs.values()
        if "start_warmup" in j["task_name"]
    )
    assert queued_account_ids == sorted([stale, never, count_stale, warmed_daily])


def test_heartbeat_module_is_not_registered():
    """Regression guard: heartbeat was removed 2026-06-11 (see spec
    2026-06-11-remove-heartbeat-fix-worker-stability-design.md).
    Re-adding it to import_paths brings back the noisy down/up emails."""
    from app.tasks import procrastinate_app
    assert "app.tasks.heartbeat" not in procrastinate_app.import_paths


def test_auto_publish_scan_sync_returns_empty_when_no_publications(session, monkeypatch):
    """The sync helper must be callable from a plain thread, returning
    (pub_ids=[], next_ready=None) when there is nothing to publish and no
    cooled-down candidate to wait on."""
    from app.tasks import auto_publish as ap_module
    from app.services import posting_service

    monkeypatch.setattr(ap_module, "session_scope", lambda: _ScopeCtx(session))
    monkeypatch.setattr(
        posting_service, "check_initiated", lambda s, c: "0 pending")
    monkeypatch.setattr(
        posting_service, "plan_assignments",
        lambda s, *, platform, cooldown_hours, limit=None: [])
    monkeypatch.setattr(
        posting_service, "next_publish_scan_ready_at",
        lambda s, *, cooldown_hours, limit_per_platform=None: None)
    # geelark_service.make_client is called but its return is opaque to the
    # mocked posting_service stubs above.
    from app.services import geelark_service
    monkeypatch.setattr(geelark_service, "make_client", lambda: object())

    pub_ids, next_ready = ap_module._auto_publish_scan_sync()

    assert pub_ids == []
    assert next_ready is None


class _ScopeCtx:
    """Tiny `with` context that yields a pre-built SQLAlchemy session.
    `session_scope()` is the canonical contextmanager in app.db; tests
    that want to use a fixture-built session swap it via monkeypatch."""
    def __init__(self, session):
        self._s = session
    def __enter__(self):
        return self._s
    def __exit__(self, *exc):
        return False
