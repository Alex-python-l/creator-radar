"""daily_warmup_scan must skip accounts whose phone lease is held."""
import asyncio
from datetime import datetime, timedelta

from app.repositories import accounts as acc_repo
from app.services import groups_service as gs
from app.services import phone_runtime
from app.tasks import daily_warmup


def _make_eligible(db, group_id: int, profile: str, env_id: str,
                   exit_ip: str | None) -> int:
    aid = acc_repo.create(db, {
        "profile_name": profile,
        "tiktok_logged_in": 1,
        "geelark_phone_id": env_id,
        "warmup_mode": "daily",
        "warmed_up": 0,
        "group_id": group_id,
        "proxy_exit_ip": exit_ip,
    })
    acc_repo.update(db, aid, {
        "last_warm_at": datetime.utcnow() - timedelta(hours=12),
    })
    return aid


def test_scan_skips_lease_held_account(
        in_memory_procrastinate, db, warmup_eligible_account_id):
    import asyncio
    from app.tasks.daily_warmup import daily_warmup_scan
    aid = warmup_eligible_account_id
    # Mark eligible by cutoff but an in-flight phone_session blocks it.
    acc_repo.update(db, aid, {
        "warmup_mode": "daily",
        "last_warm_at": datetime.utcnow() - timedelta(hours=12),
    })
    a = acc_repo.get(db, aid)
    phone_runtime.start_session(
        db, account_id=aid, env_id=a["geelark_phone_id"],
        source="manual", owner_type="account", owner_id=aid,
        budget_seconds=600, created_by="test",
    )
    summary = asyncio.run(daily_warmup_scan(timestamp=0))
    assert "queued 0" in summary


def test_scan_picks_up_account_with_finalized_session(
        in_memory_procrastinate, db, warmup_eligible_account_id):
    """Once the prior phone_session is finalized, the account becomes
    eligible again."""
    import asyncio
    from app.tasks.daily_warmup import daily_warmup_scan
    aid = warmup_eligible_account_id
    acc_repo.update(db, aid, {
        "warmup_mode": "daily",
        "last_warm_at": datetime.utcnow() - timedelta(hours=12),
    })
    a = acc_repo.get(db, aid)
    sid = phone_runtime.start_session(
        db, account_id=aid, env_id=a["geelark_phone_id"],
        source="manual", owner_type="account", owner_id=aid,
        budget_seconds=600, created_by="test",
    )
    phone_runtime.finalize_success(db, sid)
    summary = asyncio.run(daily_warmup_scan(timestamp=0))
    assert "queued 1" in summary
    jobs = in_memory_procrastinate.jobs
    start_jobs = [j for j in jobs.values() if j["task_name"] == "start_warmup"]
    assert len(start_jobs) == 1
    assert start_jobs[0]["args"]["account_id"] == aid


def test_scan_picks_up_account_with_no_session(
        in_memory_procrastinate, db, warmup_eligible_account_id):
    """A daily-mode account with no phone_session is eligible."""
    import asyncio
    from app.tasks.daily_warmup import daily_warmup_scan
    aid = warmup_eligible_account_id
    acc_repo.update(db, aid, {
        "warmup_mode": "daily",
        "last_warm_at": datetime.utcnow() - timedelta(hours=12),
    })
    summary = asyncio.run(daily_warmup_scan(timestamp=0))
    assert "queued 1" in summary


def test_scan_skips_same_ip_when_group_is_proxy_isolated(
        in_memory_procrastinate, db, monkeypatch):
    """R_JP / R_Korea proxy supplier is 1-device-per-exit-IP. If one
    account on a given proxy_exit_ip already has an active phone_session,
    the scan must skip the other accounts sharing that IP."""
    gid = gs.create(db, name="iso-test-group")
    monkeypatch.setattr(daily_warmup, "PROXY_ISOLATION_GROUP_IDS", (gid,))

    busy = _make_eligible(db, gid, "iso-busy", "ph-iso-busy", "10.9.9.9")
    waiting = _make_eligible(db, gid, "iso-wait", "ph-iso-wait", "10.9.9.9")
    phone_runtime.start_session(
        db, account_id=busy, env_id="ph-iso-busy", source="warmup",
        owner_type="account", owner_id=busy,
        budget_seconds=1200, created_by="test",
    )
    db.commit()

    summary = asyncio.run(daily_warmup.daily_warmup_scan(timestamp=0))
    # `busy` is filtered by the existing active-session check;
    # `waiting` is filtered by the new IP-isolation clause.
    assert "queued 0 of 0" in summary
    assert not [
        j for j in in_memory_procrastinate.jobs.values()
        if j["task_name"] == "start_warmup"
        and j["args"]["account_id"] == waiting
    ]


def test_scan_isolates_per_ip_not_across_ips(
        in_memory_procrastinate, db, monkeypatch):
    """Two accounts in the isolation group on DIFFERENT exit IPs are
    independent — one being busy must not block the other."""
    gid = gs.create(db, name="iso-test-cross-ip")
    monkeypatch.setattr(daily_warmup, "PROXY_ISOLATION_GROUP_IDS", (gid,))

    busy = _make_eligible(db, gid, "iso-x-busy", "ph-iso-x-busy", "10.1.1.1")
    other = _make_eligible(db, gid, "iso-x-other", "ph-iso-x-other", "10.2.2.2")
    phone_runtime.start_session(
        db, account_id=busy, env_id="ph-iso-x-busy", source="warmup",
        owner_type="account", owner_id=busy,
        budget_seconds=1200, created_by="test",
    )
    db.commit()

    summary = asyncio.run(daily_warmup.daily_warmup_scan(timestamp=0))
    assert "queued 1 of 1" in summary
    queued = [
        j for j in in_memory_procrastinate.jobs.values()
        if j["task_name"] == "start_warmup"
    ]
    assert len(queued) == 1
    assert queued[0]["args"]["account_id"] == other


def test_scan_does_not_isolate_groups_outside_the_isolation_list(
        in_memory_procrastinate, db, monkeypatch):
    """Groups NOT in PROXY_ISOLATION_GROUP_IDS keep sharing IPs freely:
    two accounts on the same proxy_exit_ip can both be in scope (only
    the one with an active session is filtered out by the existing
    lease check; the other still queues)."""
    gid = gs.create(db, name="non-iso-group")
    # Force the isolation list to NOT include this group.
    monkeypatch.setattr(daily_warmup, "PROXY_ISOLATION_GROUP_IDS", (-99,))

    busy = _make_eligible(db, gid, "noniso-busy", "ph-noniso-busy", "10.5.5.5")
    sharer = _make_eligible(db, gid, "noniso-share", "ph-noniso-share", "10.5.5.5")
    phone_runtime.start_session(
        db, account_id=busy, env_id="ph-noniso-busy", source="warmup",
        owner_type="account", owner_id=busy,
        budget_seconds=1200, created_by="test",
    )
    db.commit()

    summary = asyncio.run(daily_warmup.daily_warmup_scan(timestamp=0))
    assert "queued 1 of 1" in summary
    queued = [
        j for j in in_memory_procrastinate.jobs.values()
        if j["task_name"] == "start_warmup"
    ]
    assert len(queued) == 1
    assert queued[0]["args"]["account_id"] == sharer
