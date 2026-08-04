"""Weekly Procrastinate task that discovers IELTS TikTok videos via
Bright Data and upserts them into discovered_videos. Cron: 0 4 * * 1
(Mondays). BD charges ~$1.5/1k records at collection time — a 45-keyword
batch is ~10k records ≈ $15 per run, so daily would burn ~$450/month for
mostly-duplicate results. Weekly top-ups + 90-day retention keep the
candidate pool at 1000+ for a fraction of that.
See docs/superpowers/specs/2026-06-09-tiktok-ielts-discover-design.md.
"""
import asyncio
import os

from app.tasks import procrastinate_app
from app.db import session_scope
from app.services import discovery_service
from app.repositories import job_runs

from brightdata_client import BrightDataClient


def _make_client() -> BrightDataClient:
    return BrightDataClient(
        token=os.environ["BRIGHTDATA_TOKEN"],
        dataset_id=os.environ.get(
            "BRIGHTDATA_KEYWORD_DATASET_ID",
            "gd_lilwhto81z415d9mdl",
        ),
    )


@procrastinate_app.periodic(cron="0 4 * * 1")
@procrastinate_app.task(
    queue="default",
    name="discover_tiktok",
    queueing_lock="discover_tiktok",
)
async def discover_tiktok_task(timestamp: int):
    def _work() -> str:
        with session_scope() as session:
            job_id = job_runs.start(session, "discover_tiktok")
        client = _make_client()
        try:
            with session_scope() as session:
                stats = discovery_service.run_discovery_cycle(session, client)
            status = "SUCCESS" if stats.keywords_failed == 0 else "PARTIAL"
            summary = str(stats)
        except Exception as e:
            status = "FAILURE"
            summary = f"discover cycle crashed: {e!r}"
        with session_scope() as session:
            job_runs.finish(session, job_id, status, summary)
        return summary
    return await asyncio.to_thread(_work)
