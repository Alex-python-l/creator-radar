#!/usr/bin/env python3
"""Revive engagement_runs that an operator hand-stopped (or that the
worker mis-failed) before the workflow could finish.

Matches `notes` exactly and `result` (default SKIPPED, can override to
FAILURE for retry-burned rows); safe to re-run because each row's notes
flip to a new sentinel after revival.

Each matched run is reset to PENDING with `created_at = now()` so the
daily-limit gate (`count_attempts_today` filters created_at >=
today_midnight) actually sees it as today's work, then a
`submit_engagement_run` job is deferred — but per-phone staggered so
same-phone PENDING runs don't race for the phone lease and burn through
MAX_SUBMIT_RETRIES.

Per-phone schedule: each phone gets at most `daily_limit` runs per UTC
day, spaced `slot_seconds` apart within the day. So 65 runs on one
phone with daily_limit=20, slot_seconds=600 spreads across ~3.3 days
with one run starting every 10 minutes — the prior `MAX_SUBMIT_RETRIES
× 30s = 20 min` window then comfortably covers any individual run.

Usage:
  uv run python scripts/revive_manually_skipped_engagement.py --dry-run
  uv run python scripts/revive_manually_skipped_engagement.py --apply
  uv run python scripts/revive_manually_skipped_engagement.py --apply \\
    --match-result FAILURE \\
    --notes 'submit retries exhausted while account was busy' \\
    --new-notes 'revived 2026-06-17 after stagger fix'

Default match is the 2026-06-12 hand-stop batch (SKIPPED). Override
--notes / --match-result / --new-notes when reusing for another incident.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import update
from sqlmodel import Session, select

from app.config import SETTINGS_DEFAULTS
from app.db import engine
from app.models import EngagementRun
from app.repositories import engagement as eng_repo
from app.repositories import settings as set_repo


DEFAULT_MATCH_NOTES = "manually stopped 2026-06-12 16:25 CST"
DEFAULT_REVIVED_NOTES = "revived 2026-06-16; daily limit now throttles"
DEFAULT_MATCH_RESULT = "SKIPPED"
CHUNK_SIZE = 200
SLOT_SECONDS = 600  # 10 min between consecutive submits on the same phone
ONE_DAY_SECONDS = 86_400


def _matching_run_ids(session: Session, match_notes: str,
                      revived_notes: str, *,
                      match_result: str = DEFAULT_MATCH_RESULT) -> list[int]:
    """Return ids with (result, notes) == (match_result, match_notes).
    Rows already flipped to `revived_notes` are skipped — that's what
    makes the script idempotent."""
    return list(session.exec(
        select(EngagementRun.id).where(
            EngagementRun.result == match_result,
            EngagementRun.notes == match_notes,
        ).order_by(EngagementRun.id)
    ).all())


def _flip_rows(session: Session, run_ids: list[int],
               revived_notes: str) -> set[int]:
    """Reset the rows to PENDING (incl. created_at=now, so daily-limit
    actually sees the row as today's work) and return the set of parent
    task ids whose summary needs recomputing.

    Resetting `created_at` matters: `count_attempts_today` filters
    `created_at >= today_midnight`, so a row whose `created_at` is days
    old will permanently count 0 toward today's quota and bypass the
    throttle the revive design depends on."""
    if not run_ids:
        return set()
    task_id_rows = session.exec(
        select(EngagementRun.engagement_task_id)
        .where(EngagementRun.id.in_(run_ids))
    ).all()
    session.exec(
        update(EngagementRun)
        .where(EngagementRun.id.in_(run_ids))
        .values(
            result="PENDING",
            notes=revived_notes,
            created_at=datetime.utcnow(),
            finished_at=None,
            last_checked_at=None,
            geelark_task_id=None,
            fail_code=None,
        )
    )
    session.commit()
    return set(task_id_rows)


def _lookup_phones(session: Session,
                   run_ids: list[int]) -> list[tuple[int, int | None]]:
    """Return [(run_id, account_id), ...] preserving the input order.

    Account_id may be None (orphan row); _compute_per_phone_schedule
    treats None as its own bucket so orphans don't accidentally share a
    slot with a real phone."""
    if not run_ids:
        return []
    rows = session.exec(
        select(EngagementRun.id, EngagementRun.account_id)
        .where(EngagementRun.id.in_(run_ids))
    ).all()
    by_id = {rid: aid for rid, aid in rows}
    return [(rid, by_id.get(rid)) for rid in run_ids if rid in by_id]


def _compute_per_phone_schedule(
    rows: list[tuple[int, object]],
    *,
    daily_limit: int,
    slot_seconds: int = SLOT_SECONDS,
) -> list[tuple[int, int]]:
    """Spread runs across days/slots so same-phone runs never share a
    submit time.

    Per phone:
      - first `daily_limit` runs land on day 0 at slots 0..(limit-1) of
        `slot_seconds`
      - next `daily_limit` runs land on day 1
      - etc.

    If `daily_limit <= 0` (feature off) the spread is single-day —
    same-phone runs still get distinct `slot_seconds`-spaced delays so
    they don't race for the phone lease.

    Returns [(run_id, delay_seconds), ...] one entry per input row,
    preserving input order."""
    counts: dict[object, int] = {}
    out: list[tuple[int, int]] = []
    for run_id, phone in rows:
        idx = counts.get(phone, 0)
        counts[phone] = idx + 1
        if daily_limit and daily_limit > 0:
            day = idx // daily_limit
            slot = idx % daily_limit
        else:
            day = 0
            slot = idx
        delay = day * ONE_DAY_SECONDS + slot * slot_seconds
        out.append((run_id, delay))
    return out


async def _defer_submits(scheduled: list[tuple[int, int]]) -> None:
    """Defer one submit_engagement_run job per (run_id, delay) pair."""
    from app.tasks import procrastinate_app
    from app.tasks.engagement import submit_engagement_run_task

    async with procrastinate_app.open_async():
        for run_id, delay in scheduled:
            await submit_engagement_run_task.configure(
                schedule_in={"seconds": delay},
            ).defer_async(run_id=run_id)


def _already_revived_run_ids(session: Session, revived_notes: str) -> list[int]:
    """Rows previously flipped but possibly missing their Procrastinate
    job (e.g. flip committed but defer crashed). Re-deferring is safe —
    worker dedup is via run_id; the second job would simply observe a
    non-PENDING state and noop."""
    return list(session.exec(
        select(EngagementRun.id).where(
            EngagementRun.result == "PENDING",
            EngagementRun.notes == revived_notes,
        ).order_by(EngagementRun.id)
    ).all())


def _resolve_daily_limit(session: Session, *, override: int | None,
                         platform_key: str) -> int:
    """Override > settings > SETTINGS_DEFAULTS. 0 means disabled."""
    if override is not None:
        return max(0, override)
    raw = (
        set_repo.get_all(session).get(platform_key)
        or SETTINGS_DEFAULTS.get(platform_key)
        or "0"
    )
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


async def _run(apply: bool, match_notes: str, revived_notes: str,
               match_result: str, daily_limit_override: int | None,
               platform_key: str, slot_seconds: int) -> int:
    # Phase A: matching rows → flip to PENDING + recompute task.
    with Session(engine) as session:
        matched_ids = _matching_run_ids(
            session, match_notes, revived_notes, match_result=match_result,
        )
        daily_limit = _resolve_daily_limit(
            session, override=daily_limit_override, platform_key=platform_key,
        )
    print(f"phase A: {len(matched_ids)} {match_result} runs match "
          f"notes={match_notes!r}")
    print(f"   daily_limit={daily_limit} (key={platform_key}), "
          f"slot_seconds={slot_seconds}")

    if not apply:
        with Session(engine) as session:
            already = _already_revived_run_ids(session, revived_notes)
        print(f"phase B: {len(already)} previously revived runs still "
              f"PENDING (would be re-deferred too)")
        print("--dry-run: no rows modified, no jobs deferred")
        if matched_ids:
            print(f"  sample {match_result} ids: {matched_ids[:5]}")
        return 0

    total_flipped = 0
    total_tasks = 0
    for start in range(0, len(matched_ids), CHUNK_SIZE):
        chunk = matched_ids[start:start + CHUNK_SIZE]
        with Session(engine) as session:
            tasks_to_recompute = _flip_rows(session, chunk, revived_notes)
            for tid in tasks_to_recompute:
                eng_repo.recompute_task_status(session, tid)
        total_flipped += len(chunk)
        total_tasks += len(tasks_to_recompute)
        print(f"  flipped {total_flipped}/{len(matched_ids)} runs, "
              f"recomputed {total_tasks} parent tasks")

    # Phase B: every PENDING-with-revived-notes run gets a fresh
    # submit_engagement_run job, per-phone staggered. This covers both
    # rows we just flipped AND rows a previous interrupted run left
    # orphaned.
    with Session(engine) as session:
        ids_to_defer = _already_revived_run_ids(session, revived_notes)
        rows_with_phones = _lookup_phones(session, ids_to_defer)
    scheduled = _compute_per_phone_schedule(
        rows_with_phones,
        daily_limit=daily_limit,
        slot_seconds=slot_seconds,
    )
    max_delay = max((d for _, d in scheduled), default=0)
    print(f"phase B: deferring {len(scheduled)} submit_engagement_run jobs, "
          f"max delay {max_delay // 3600}h {(max_delay % 3600) // 60}m ...")
    await _defer_submits(scheduled)
    print(f"deferred {len(scheduled)} jobs")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually flip rows and defer jobs")
    ap.add_argument("--dry-run", action="store_true",
                    help="print plan only (default if --apply not given)")
    ap.add_argument("--notes", default=DEFAULT_MATCH_NOTES,
                    help=f"match this notes value (default: {DEFAULT_MATCH_NOTES!r})")
    ap.add_argument("--new-notes", default=DEFAULT_REVIVED_NOTES,
                    help=f"set revived rows' notes to this "
                         f"(default: {DEFAULT_REVIVED_NOTES!r})")
    ap.add_argument("--match-result", default=DEFAULT_MATCH_RESULT,
                    choices=("SKIPPED", "FAILURE"),
                    help=f"match this result (default: {DEFAULT_MATCH_RESULT})")
    ap.add_argument("--daily-limit", type=int, default=None,
                    help="override per-phone daily quota (default: from "
                         "settings/SETTINGS_DEFAULTS based on --platform-key)")
    ap.add_argument("--platform-key", default="daily_comment_limit_tiktok",
                    help="settings key for daily quota (default: tiktok)")
    ap.add_argument("--slot-seconds", type=int, default=SLOT_SECONDS,
                    help=f"seconds between same-phone submits within a day "
                         f"(default: {SLOT_SECONDS})")
    args = ap.parse_args()

    apply = args.apply and not args.dry_run
    asyncio.run(_run(
        apply,
        args.notes,
        args.new_notes,
        args.match_result,
        args.daily_limit,
        args.platform_key,
        args.slot_seconds,
    ))


if __name__ == "__main__":
    main()
