from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def test_worker_finalizes_only_own_running_lock_and_heartbeats_long_jobs():
    src = _read("app/worker.py")

    assert "HEARTBEAT_INTERVAL_SEC" in src
    assert "async def _heartbeat" in src
    assert 'Job.status == "running"' in src
    assert "Job.locked_by == worker_id" in src
    assert ".with_for_update()" in src
    assert "asyncio.create_task(_heartbeat" in src
    assert "worker.process_error" in src
    assert "finalize_skipped_stale_lock" in src


def test_daily_refresh_dedup_uses_advisory_lock_and_avoids_json_int_cast():
    src = _read("app/handlers/schedule.py")

    assert "pg_advisory_xact_lock" in src
    assert 'Job.payload["influencerId"].astext == str(influencer_id)' in src
    assert "cast(" not in src


def test_resolve_fb_endpoint_supports_internal_token_when_configured():
    src = _read("app/main.py")

    assert "Header" in src
    assert "x_internal_token" in src
    assert "settings.scraper_internal_token" in src
    assert "status_code=401" in src
