"""Tests for scripts/revive_manually_skipped_engagement.py.

Covers the 2026-06-17 bug fix where the original script:
  1. left `created_at` stale on revived rows — daily-limit count_attempts_today
     uses `created_at >= today_midnight` to count today's quota, so revived
     rows from past days were permanently counted as 0 and bypassed the
     daily throttle entirely.
  2. deferred all submits at the same time, causing same-phone PENDING runs
     to race over the phone lease; runs that lost the race burned through
     MAX_SUBMIT_RETRIES (20 min) before being marked FAILURE.
"""
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_REVIVE_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "revive_manually_skipped_engagement.py"
)


def _load_revive_module():
    """Load the script as a module without making `scripts/` a package."""
    spec = importlib.util.spec_from_file_location(
        "revive_manually_skipped_engagement", _REVIVE_PATH,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_run_with_notes(session, *, aid, gid, notes, link, result="SKIPPED"):
    """Insert one engagement_run with the given notes + terminal result."""
    from app.repositories import engagement as eng_repo

    tid = eng_repo.create_task(
        session, group_id=gid, action="tiktok_ai_comment",
        source="manual_links", target_links=[link],
        target_publication_ids=[], comment_probability=100,
    )
    rid = eng_repo.create_run(
        session, engagement_task_id=tid, account_id=aid,
        target_link=link, action="tiktok_ai_comment",
    )
    if result == "SKIPPED":
        eng_repo.mark_skipped(session, rid, notes)
    elif result == "FAILURE":
        eng_repo.set_result(session, rid, "FAILURE", notes)
    else:
        raise ValueError(f"unsupported result {result} for seed helper")
    return rid


def test_flip_rows_resets_created_at(session):
    """Revival must reset `created_at` to ~now. Without this, daily-limit's
    `count_attempts_today` (filters created_at >= today_midnight) will
    permanently see the row as 'pre-today' and count 0 — bypassing the
    throttle that the whole revive design depends on."""
    from sqlmodel import select

    from app.models import EngagementRun
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo

    mod = _load_revive_module()

    gid = group_repo.create(session, name="g")
    aid = acc_repo.create(session, {
        "profile_name": "p", "geelark_phone_id": "ph",
        "group_id": gid, "tiktok_logged_in": 1,
    })
    rid = _seed_run_with_notes(
        session, aid=aid, gid=gid, notes="old-batch",
        link="https://x/v/1",
    )

    five_days_ago = datetime.utcnow() - timedelta(days=5)
    row = session.exec(
        select(EngagementRun).where(EngagementRun.id == rid)
    ).one()
    row.created_at = five_days_ago
    session.add(row)
    session.commit()

    before = datetime.utcnow()
    mod._flip_rows(session, [rid], "revived")
    after = datetime.utcnow()

    refreshed = session.exec(
        select(EngagementRun).where(EngagementRun.id == rid)
    ).one()
    assert refreshed.result == "PENDING"
    assert refreshed.notes == "revived"
    assert before <= refreshed.created_at <= after, (
        f"created_at not reset to now — got {refreshed.created_at}, "
        f"expected in [{before}, {after}]"
    )


def test_compute_per_phone_schedule_staggers_within_and_across_days():
    """A phone with N > daily_limit runs must spread:
    - same phone gets at most daily_limit runs per day
    - within a day, runs are spaced by slot_seconds (no two same-phone
      runs share the same delay)
    - different phones don't block each other
    """
    mod = _load_revive_module()

    # 65 runs on phone A, 5 on phone B, 1 on phone C.
    rows = (
        [(rid, "A") for rid in range(1, 66)]
        + [(rid, "B") for rid in range(100, 105)]
        + [(rid, "C") for rid in range(200, 201)]
    )

    scheduled = mod._compute_per_phone_schedule(
        rows, daily_limit=20, slot_seconds=600,
    )

    # Result is list of (run_id, delay_seconds), one per input row.
    assert len(scheduled) == len(rows)
    assert sorted(rid for rid, _ in scheduled) == sorted(rid for rid, _ in rows)

    # Phone A: 65 runs total.
    a_delays = sorted(d for (rid, d) in scheduled if rid < 100)
    assert len(a_delays) == 65
    # Unique within phone — no race.
    assert len(set(a_delays)) == 65
    # First 20 slots: day 0, spaced 600s.
    assert a_delays[:20] == [i * 600 for i in range(20)]
    # 21st = day 1, first slot.
    assert a_delays[20] == 86400
    # 41st = day 2, first slot.
    assert a_delays[40] == 2 * 86400
    # 65th = day 3, slot 4.
    assert a_delays[64] == 3 * 86400 + 4 * 600

    # Phone B: 5 runs all on day 0, slots 0..4.
    b_delays = sorted(d for (rid, d) in scheduled if 100 <= rid < 200)
    assert b_delays == [i * 600 for i in range(5)]

    # Phone C: 1 run on day 0, slot 0.
    c_delays = sorted(d for (rid, d) in scheduled if rid >= 200)
    assert c_delays == [0]


def test_compute_per_phone_schedule_handles_disabled_limit():
    """daily_limit=0 means no daily-limit feature; the script should still
    stagger same-phone runs by slot_seconds so they don't race for the
    phone lease, but never roll over to next day."""
    mod = _load_revive_module()

    rows = [(rid, "A") for rid in range(1, 6)]
    scheduled = mod._compute_per_phone_schedule(
        rows, daily_limit=0, slot_seconds=600,
    )
    delays = sorted(d for _, d in scheduled)
    assert delays == [i * 600 for i in range(5)]


def test_matching_run_ids_supports_failure_match(session):
    """For the 2026-06-17 second revival we must match
    result='FAILURE' AND notes='submit retries exhausted...', not SKIPPED."""
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo

    mod = _load_revive_module()

    gid = group_repo.create(session, name="g")
    aid = acc_repo.create(session, {
        "profile_name": "p", "geelark_phone_id": "ph",
        "group_id": gid, "tiktok_logged_in": 1,
    })

    # FAILURE with target notes — should be picked when match_result=FAILURE.
    failure_match = _seed_run_with_notes(
        session, aid=aid, gid=gid, notes="match-me", link="https://x/fail",
        result="FAILURE",
    )
    # SKIPPED with same notes — should NOT be picked when match_result=FAILURE.
    skipped_same_notes = _seed_run_with_notes(
        session, aid=aid, gid=gid, notes="match-me", link="https://x/skip",
        result="SKIPPED",
    )
    # FAILURE with different notes — should NOT be picked.
    failure_other_notes = _seed_run_with_notes(
        session, aid=aid, gid=gid, notes="other", link="https://x/other",
        result="FAILURE",
    )

    failure_ids = mod._matching_run_ids(
        session, "match-me", "revived", match_result="FAILURE",
    )
    assert failure_match in failure_ids
    assert skipped_same_notes not in failure_ids
    assert failure_other_notes not in failure_ids

    # Default (SKIPPED) still works.
    skipped_ids = mod._matching_run_ids(session, "match-me", "revived")
    assert skipped_same_notes in skipped_ids
    assert failure_match not in skipped_ids


def test_lookup_phones_returns_pairs_in_input_order(session):
    """`_lookup_phones` must return (run_id, account_id) tuples preserving
    the input ordering — _compute_per_phone_schedule relies on stable
    intra-phone ordering to assign slots deterministically."""
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo

    mod = _load_revive_module()

    gid = group_repo.create(session, name="g")
    a1 = acc_repo.create(session, {
        "profile_name": "p1", "geelark_phone_id": "ph-1",
        "group_id": gid, "tiktok_logged_in": 1,
    })
    a2 = acc_repo.create(session, {
        "profile_name": "p2", "geelark_phone_id": "ph-2",
        "group_id": gid, "tiktok_logged_in": 1,
    })

    r1 = _seed_run_with_notes(
        session, aid=a1, gid=gid, notes="n", link="https://x/1",
    )
    r2 = _seed_run_with_notes(
        session, aid=a2, gid=gid, notes="n", link="https://x/2",
    )
    r3 = _seed_run_with_notes(
        session, aid=a1, gid=gid, notes="n", link="https://x/3",
    )

    pairs = mod._lookup_phones(session, [r1, r2, r3])
    assert pairs == [(r1, a1), (r2, a2), (r3, a1)]
