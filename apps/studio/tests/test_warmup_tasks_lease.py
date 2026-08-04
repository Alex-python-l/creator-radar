"""Procrastinate task wiring tests for start_warmup_task."""
import asyncio

import pytest

from app.services import warmup_service


def test_start_warmup_task_defers_check_on_success(
        in_memory_procrastinate, warmup_eligible_account_id, monkeypatch):
    from app.tasks import warmup as warmup_tasks

    def fake_start(session, client, account_id, duration_minutes=None):
        return "fake-gtid-7"

    monkeypatch.setattr(warmup_service, "start_warmup_one", fake_start)
    result = asyncio.run(warmup_tasks.start_warmup_task(account_id=warmup_eligible_account_id))
    assert "fake-gtid-7" in result
    # The chain should have queued exactly one check_warmup_status job
    jobs = in_memory_procrastinate.jobs
    check_jobs = [j for j in jobs.values() if j["task_name"] == "check_warmup_status"]
    assert len(check_jobs) == 1
    assert check_jobs[0]["args"]["task_id"] == "fake-gtid-7"


def test_start_warmup_task_phone_busy_returns_skipped(
        in_memory_procrastinate, warmup_eligible_account_id, monkeypatch):
    from app.tasks import warmup as warmup_tasks

    def busy(session, client, account_id, duration_minutes=None):
        raise warmup_service.PhoneBusy("busy")

    monkeypatch.setattr(warmup_service, "start_warmup_one", busy)
    result = asyncio.run(warmup_tasks.start_warmup_task(account_id=warmup_eligible_account_id))
    assert "skipped" in result.lower()
    jobs = in_memory_procrastinate.jobs
    assert not any(j["task_name"] == "check_warmup_status" for j in jobs.values())


def test_check_warmup_status_self_defers_on_running(
        in_memory_procrastinate, monkeypatch):
    from app.tasks import warmup as warmup_tasks

    monkeypatch.setattr(warmup_service, "advance_warmup_one",
                        lambda s, c, a, t: "running")
    out = asyncio.run(warmup_tasks.check_warmup_status_task(
        account_id=1, task_id="gt-1", attempts_remaining=5))
    assert out == "running"
    jobs = in_memory_procrastinate.jobs
    check_jobs = [j for j in jobs.values() if j["task_name"] == "check_warmup_status"]
    assert len(check_jobs) == 1
    assert check_jobs[0]["args"]["attempts_remaining"] == 4


def test_check_warmup_status_stops_chain_on_terminal(
        in_memory_procrastinate, monkeypatch):
    from app.tasks import warmup as warmup_tasks

    monkeypatch.setattr(warmup_service, "advance_warmup_one",
                        lambda s, c, a, t: "success")
    out = asyncio.run(warmup_tasks.check_warmup_status_task(
        account_id=1, task_id="gt-1", attempts_remaining=5))
    assert out == "success"
    jobs = in_memory_procrastinate.jobs
    assert not any(j["task_name"] == "check_warmup_status" for j in jobs.values())


def test_check_warmup_status_times_out_at_zero_attempts(
        in_memory_procrastinate, monkeypatch):
    """When the poll chain runs out (40 × 30s) and GeeLark still reports
    non-terminal, the task MUST drop into the timeout handler so the
    phone gets stopped and the lease released — otherwise the reclaim
    cron keeps renewing the lease and the phone bills forever."""
    from app.tasks import warmup as warmup_tasks

    monkeypatch.setattr(warmup_service, "advance_warmup_one",
                        lambda s, c, a, t: "running")
    calls = []
    monkeypatch.setattr(
        warmup_service, "mark_warmup_timed_out",
        lambda s, c, account_id, task_id: calls.append(
            (account_id, task_id)) or "timeout")

    out = asyncio.run(warmup_tasks.check_warmup_status_task(
        account_id=1, task_id="gt-1", attempts_remaining=0))

    assert out == "timeout"
    assert calls == [(1, "gt-1")]
    jobs = in_memory_procrastinate.jobs
    assert not any(j["task_name"] == "check_warmup_status" for j in jobs.values())
