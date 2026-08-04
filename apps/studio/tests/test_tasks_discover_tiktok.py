import asyncio
from unittest.mock import MagicMock

from app.tasks import discover_tiktok as task_module


def test_task_registered_on_default_queue():
    task = task_module.discover_tiktok_task
    # procrastinate stores queue on the underlying task object
    assert getattr(task, "queue", None) == "default"


def test_task_writes_jobrun_and_calls_cycle(monkeypatch):
    seen_summary = {}
    stats = task_module.discovery_service.DiscoveryStats(
        keywords_processed=1, keywords_failed=0,
        failed_keywords=[], inserted=5, updated=2, pruned=0,
    )
    monkeypatch.setattr(
        "app.tasks.discover_tiktok.discovery_service.run_discovery_cycle",
        lambda session, client: stats,
    )
    monkeypatch.setattr(
        "app.tasks.discover_tiktok._make_client",
        lambda: MagicMock(),
    )
    started = []
    finished = []
    monkeypatch.setattr(
        "app.tasks.discover_tiktok.job_runs.start",
        lambda s, job_type: started.append(job_type) or 7,
    )
    monkeypatch.setattr(
        "app.tasks.discover_tiktok.job_runs.finish",
        lambda s, job_id, status, summary: (
            seen_summary.update({"job_id": job_id, "status": status,
                                 "summary": summary})
        ),
    )
    asyncio.run(task_module.discover_tiktok_task(timestamp=1234))
    assert started == ["discover_tiktok"]
    assert seen_summary["job_id"] == 7
    assert seen_summary["status"] == "SUCCESS"
    assert "ins=5" in seen_summary["summary"]
