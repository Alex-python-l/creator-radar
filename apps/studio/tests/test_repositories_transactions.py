"""Tests for the GeeLark transactions mirror repository.

Covers:
- upsert_many is idempotent.
- list_with_attribution maps publications / engagement_runs / warmup
  completions onto the right `kind`.
- "Nearest wins" when multiple candidates are inside the window.
- The `kind=idle` filter surfaces unattributed rows.
"""

import calendar
from datetime import datetime, timedelta

import pytest

from app.repositories import cost_attributions as ca_repo
from app.repositories import transactions as tx_repo
from app.models import (
    Account, EngagementRun, EngagementTask, Publication, PublicationAttempt,
    Video,
)


def _txn(
    *,
    txn_id: str,
    env_id: str,
    created_at_epoch: int,
    used_min: int = 7,
    amount: float = -0.049,
    type_: int = 2,
    charge_type: int = 2,
) -> dict:
    return {
        "id": txn_id,
        "envId": env_id,
        "amount": amount,
        "usedTime": used_min,
        "type": type_,
        "chargeType": charge_type,
        "createdTime": created_at_epoch,
    }


def _epoch(dt: datetime) -> int:
    """Treat naive `dt` as UTC and return its unix epoch — mirrors the
    repository's `datetime.utcfromtimestamp` round-trip so post-decode
    datetimes match what the test wrote into other tables."""
    return calendar.timegm(dt.timetuple())


@pytest.fixture
def account_with_phone(db):
    a = Account(profile_name="alice", geelark_phone_id="ph-1")
    db.add(a); db.commit(); db.refresh(a)
    return a


def test_upsert_many_idempotent(db, account_with_phone):
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    rows = [
        _txn(txn_id="aaa", env_id="ph-1", created_at_epoch=_epoch(t0)),
        _txn(txn_id="bbb", env_id="ph-1", created_at_epoch=_epoch(t0) + 60),
    ]
    conflicts = tx_repo.upsert_many(db, rows)
    assert conflicts == 0

    # Re-running with the same rows → all conflict.
    conflicts2 = tx_repo.upsert_many(db, rows)
    assert conflicts2 == 2

    # Mixed: one already there, one new.
    rows.append(
        _txn(txn_id="ccc", env_id="ph-1", created_at_epoch=_epoch(t0) + 120))
    conflicts3 = tx_repo.upsert_many(db, rows)
    assert conflicts3 == 2  # aaa + bbb conflict, ccc inserts


def test_attribution_publish(db, account_with_phone):
    a = account_with_phone
    v = Video(original_filename="x.mp4", stored_path="/tmp/x.mp4")
    db.add(v); db.commit(); db.refresh(v)
    # GeeLark txn at t0 covering 7 minutes (window: t0-7min..t0)
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="SUCCESS", posted_at=t0 - timedelta(minutes=3),
    )
    db.add(p); db.commit(); db.refresh(p)
    tx_repo.upsert_many(
        db, [_txn(txn_id="t1", env_id="ph-1", created_at_epoch=_epoch(t0))],
    )

    items, total = tx_repo.list_with_attribution(db)
    assert total == 1
    assert items[0]["attribution"]["kind"] == "publish"
    assert items[0]["attribution"]["ref_type"] == "publication"
    assert items[0]["attribution"]["ref_id"] == p.id
    assert items[0]["account"]["id"] == a.id


def test_attribution_engagement(db, account_with_phone):
    a = account_with_phone
    et = EngagementTask(action="comment", source="manual")
    db.add(et); db.commit(); db.refresh(et)
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    er = EngagementRun(
        engagement_task_id=et.id, account_id=a.id, target_link="x",
        action="comment", result="SUCCESS",
        finished_at=t0 - timedelta(minutes=2),
    )
    db.add(er); db.commit(); db.refresh(er)
    tx_repo.upsert_many(
        db, [_txn(txn_id="t2", env_id="ph-1", created_at_epoch=_epoch(t0))],
    )

    items, _ = tx_repo.list_with_attribution(db)
    assert items[0]["attribution"]["kind"] == "engagement"
    assert items[0]["attribution"]["ref_id"] == er.id


def test_attribution_warmup(db, account_with_phone):
    a = account_with_phone
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    a.last_warm_at = t0 - timedelta(minutes=4)
    db.add(a); db.commit()
    tx_repo.upsert_many(
        db, [_txn(txn_id="t3", env_id="ph-1", created_at_epoch=_epoch(t0))],
    )
    items, _ = tx_repo.list_with_attribution(db)
    assert items[0]["attribution"]["kind"] == "warmup"


def test_attribution_idle(db, account_with_phone):
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    tx_repo.upsert_many(
        db, [_txn(txn_id="t4", env_id="ph-1", created_at_epoch=_epoch(t0))],
    )
    items, _ = tx_repo.list_with_attribution(db)
    assert items[0]["attribution"]["kind"] == "idle"
    assert items[0]["attribution"]["ref_id"] is None


def test_attribution_warmup_history_without_precise_match_is_idle(
        db, account_with_phone):
    """Warmup history alone is not enough to label a billing row warmup.

    Long unrelated phone-on sessions were previously shown as
    "Warmup (approx)", which made the cost audit misleading. Only a
    precise last_warm_at match should count as warmup.
    """
    a = account_with_phone
    a.warmup_count = 3   # account has warmed before
    a.last_warm_at = datetime(2026, 6, 1, 12, 0, 0)  # too old to match
    db.add(a); db.commit()
    # Transaction 8 hours later — outside the window of the last warmup
    t0 = datetime(2026, 6, 1, 20, 0, 0)
    tx_repo.upsert_many(
        db, [_txn(txn_id="approx-warmup", env_id="ph-1",
                  created_at_epoch=_epoch(t0))],
    )
    items, _ = tx_repo.list_with_attribution(db)
    assert items[0]["attribution"]["kind"] == "idle"
    assert items[0]["attribution"]["label"] == "Unattributed"


def test_attribution_precise_warmup_still_matches(db, account_with_phone):
    """When recent last_warm_at is inside the billing window, the row is
    attributed to warmup."""
    a = account_with_phone
    a.warmup_count = 5
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    a.last_warm_at = t0 - timedelta(minutes=3)  # in the window
    db.add(a); db.commit()
    tx_repo.upsert_many(
        db, [_txn(txn_id="precise", env_id="ph-1",
                  created_at_epoch=_epoch(t0))],
    )
    items, _ = tx_repo.list_with_attribution(db)
    assert items[0]["attribution"]["kind"] == "warmup"
    assert items[0]["attribution"]["label"] == "Warmup"


def test_attribution_nearest_wins(db, account_with_phone):
    """A publication at -1min vs a warmup at -4min — publish should win."""
    a = account_with_phone
    v = Video(original_filename="x.mp4", stored_path="/tmp/x.mp4")
    db.add(v); db.commit(); db.refresh(v)
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    a.last_warm_at = t0 - timedelta(minutes=4)
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="SUCCESS", posted_at=t0 - timedelta(minutes=1),
    )
    db.add(a); db.add(p); db.commit()
    tx_repo.upsert_many(
        db, [_txn(txn_id="t5", env_id="ph-1", created_at_epoch=_epoch(t0))],
    )
    items, _ = tx_repo.list_with_attribution(db)
    assert items[0]["attribution"]["kind"] == "publish"


def test_filter_kind_idle(db, account_with_phone):
    a = account_with_phone
    v = Video(original_filename="x.mp4", stored_path="/tmp/x.mp4")
    db.add(v); db.commit(); db.refresh(v)
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="SUCCESS", posted_at=t0 - timedelta(minutes=3),
    )
    db.add(p); db.commit()
    tx_repo.upsert_many(db, [
        _txn(txn_id="match", env_id="ph-1", created_at_epoch=_epoch(t0)),
        _txn(txn_id="orphan", env_id="ph-1",
             created_at_epoch=_epoch(t0 + timedelta(hours=2))),
    ])
    items, total = tx_repo.list_with_attribution(db, kind="idle")
    assert total == 1
    assert items[0]["id"] == "orphan"


def test_attribution_publish_recovers_when_posted_at_outside_window(
        db, account_with_phone):
    """R3 scenario: publication posted_at lands after the tx billing
    window closes (or never lands at all), but last_checked_at — written
    by every poll while the phone is on — sits inside the window. The
    fix attributes the cost to the publication with confidence='medium'
    instead of leaving it as idle/Unattributed."""
    a = account_with_phone
    v = Video(original_filename="x.mp4", stored_path="/tmp/x.mp4")
    db.add(v); db.commit(); db.refresh(v)
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="SUCCESS",
        # posted_at lands AFTER the tx closed — the R3 condition
        posted_at=t0 + timedelta(minutes=2),
        # last_checked_at written while polling, inside the window
        last_checked_at=t0 - timedelta(minutes=3),
    )
    db.add(p); db.commit(); db.refresh(p)
    tx_repo.upsert_many(
        db, [_txn(txn_id="r3-lc", env_id="ph-1",
                  created_at_epoch=_epoch(t0))],
    )
    items, _ = tx_repo.list_with_attribution(db)
    assert items[0]["attribution"]["kind"] == "publish"
    assert items[0]["attribution"]["ref_id"] == p.id
    assert items[0]["attribution"]["confidence"] == "medium"
    assert "last_checked_at" in items[0]["attribution"]["reason"]


def test_attribution_publish_recovers_via_publication_attempt(
        db, account_with_phone):
    """Mid-flight publish with no posted_at and no last_checked_at yet:
    any submit or status_check attempt inside the window still labels
    the cost as publish with confidence='medium'. Catches the case where
    GeeLark stops the phone between submit and first poll."""
    a = account_with_phone
    v = Video(original_filename="x.mp4", stored_path="/tmp/x.mp4")
    db.add(v); db.commit(); db.refresh(v)
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="INITIATED",
    )
    db.add(p); db.commit(); db.refresh(p)
    db.add(PublicationAttempt(
        publication_id=p.id, attempt_no=1, stage="submit",
        source="geelark", message="submitted",
        created_at=t0 - timedelta(minutes=2),
    ))
    db.commit()
    tx_repo.upsert_many(
        db, [_txn(txn_id="r3-attempt", env_id="ph-1",
                  created_at_epoch=_epoch(t0))],
    )
    items, _ = tx_repo.list_with_attribution(db)
    assert items[0]["attribution"]["kind"] == "publish"
    assert items[0]["attribution"]["ref_id"] == p.id
    assert items[0]["attribution"]["confidence"] == "medium"
    assert "publication_attempts" in items[0]["attribution"]["reason"]
    assert "submit" in items[0]["attribution"]["reason"]


def test_attribution_publish_preflight_attempt_alone_does_not_match(
        db, account_with_phone):
    """A preflight attempt (caption / upload / preflight) is written
    BEFORE the phone starts — it must NOT pull a tx into the publish
    bucket on its own. Only submit / status_check attempts count."""
    a = account_with_phone
    v = Video(original_filename="x.mp4", stored_path="/tmp/x.mp4")
    db.add(v); db.commit(); db.refresh(v)
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="INITIATED",
    )
    db.add(p); db.commit(); db.refresh(p)
    db.add(PublicationAttempt(
        publication_id=p.id, attempt_no=1, stage="preflight",
        source="system", message="ok",
        created_at=t0 - timedelta(minutes=2),
    ))
    db.commit()
    tx_repo.upsert_many(
        db, [_txn(txn_id="preflight-only", env_id="ph-1",
                  created_at_epoch=_epoch(t0))],
    )
    items, _ = tx_repo.list_with_attribution(db)
    assert items[0]["attribution"]["kind"] == "idle"


def test_attribution_confidence_high_when_posted_at_in_window(
        db, account_with_phone):
    """High-confidence path verification: posted_at inside the window
    produces confidence='high' + the canonical reason string."""
    a = account_with_phone
    v = Video(original_filename="x.mp4", stored_path="/tmp/x.mp4")
    db.add(v); db.commit(); db.refresh(v)
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    p = Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="SUCCESS", posted_at=t0 - timedelta(minutes=3),
    )
    db.add(p); db.commit(); db.refresh(p)
    tx_repo.upsert_many(
        db, [_txn(txn_id="hi", env_id="ph-1",
                  created_at_epoch=_epoch(t0))],
    )
    items, _ = tx_repo.list_with_attribution(db)
    assert items[0]["attribution"]["confidence"] == "high"
    assert items[0]["attribution"]["reason"] == "posted_at in tx window"


def test_attribution_idle_carries_low_confidence_and_reason(
        db, account_with_phone):
    """Idle rows still surface confidence/reason so the UI can render
    every row uniformly without branching on kind."""
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    tx_repo.upsert_many(
        db, [_txn(txn_id="idle-meta", env_id="ph-1",
                  created_at_epoch=_epoch(t0))],
    )
    items, _ = tx_repo.list_with_attribution(db)
    assert items[0]["attribution"]["kind"] == "idle"
    assert items[0]["attribution"]["confidence"] == "low"
    assert "no publish/warmup/engagement" in items[0]["attribution"]["reason"]


def test_summary_kinds(db, account_with_phone):
    a = account_with_phone
    v = Video(original_filename="x.mp4", stored_path="/tmp/x.mp4")
    db.add(v); db.commit(); db.refresh(v)
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    db.add(Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="SUCCESS", posted_at=t0 - timedelta(minutes=3),
    ))
    db.commit()
    tx_repo.upsert_many(db, [
        _txn(txn_id="m", env_id="ph-1", created_at_epoch=_epoch(t0),
             amount=-0.05),
        _txn(txn_id="o", env_id="ph-1",
             created_at_epoch=_epoch(t0 + timedelta(hours=2)),
             amount=-0.07),
    ])
    rows = tx_repo.summary(db, group_by="kind")
    keyed = {r["key"]: r for r in rows}
    assert pytest.approx(keyed["publish"]["total"], abs=1e-4) == -0.05
    assert pytest.approx(keyed["idle"]["total"], abs=1e-4) == -0.07


def test_list_uses_persisted_attribution_when_scope_complete(
        db, account_with_phone):
    """A complete cost_attributions ledger wins over legacy inference.

    Without the persisted row this transaction has no matching business
    timestamp and would render as idle.
    """
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    tx_repo.upsert_many(
        db, [_txn(txn_id="ledger-publish", env_id="ph-1",
                  created_at_epoch=_epoch(t0), amount=-0.05)],
    )
    ca_repo.upsert(
        db, "ledger-publish",
        kind="publish", confidence="high", reason="persisted test row",
    )
    db.commit()

    items, total = tx_repo.list_with_attribution(db)
    assert total == 1
    assert items[0]["id"] == "ledger-publish"
    assert items[0]["attribution"]["kind"] == "publish"
    assert items[0]["attribution"]["reason"] == "persisted test row"


def test_summary_kind_uses_persisted_attribution_when_scope_complete(
        db, account_with_phone):
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    tx_repo.upsert_many(db, [
        _txn(txn_id="ledger-a", env_id="ph-1",
             created_at_epoch=_epoch(t0), amount=-0.10),
        _txn(txn_id="ledger-b", env_id="ph-1",
             created_at_epoch=_epoch(t0 + timedelta(minutes=10)),
             amount=-0.05),
    ])
    ca_repo.upsert(
        db, "ledger-a",
        kind="publish", confidence="high", reason="persisted publish",
    )
    ca_repo.upsert(
        db, "ledger-b",
        kind="unattributed", confidence="low", reason="persisted idle",
    )
    db.commit()

    rows = tx_repo.summary(db, group_by="kind")
    keyed = {r["key"]: r for r in rows}
    assert pytest.approx(keyed["publish"]["total"], abs=1e-4) == -0.10
    assert pytest.approx(keyed["idle"]["total"], abs=1e-4) == -0.05


def test_kpis_idle_pct_uses_persisted_attribution_when_scope_complete(
        db, account_with_phone):
    now = datetime(2026, 6, 15, 12, 0, 0)
    t0 = now - timedelta(days=1)
    tx_repo.upsert_many(db, [
        _txn(txn_id="ledger-kpi-a", env_id="ph-1",
             created_at_epoch=_epoch(t0), amount=-1.0),
        _txn(txn_id="ledger-kpi-b", env_id="ph-1",
             created_at_epoch=_epoch(t0 + timedelta(minutes=10)),
             amount=-1.0),
    ])
    ca_repo.upsert(
        db, "ledger-kpi-a",
        kind="publish", confidence="high", reason="persisted publish",
    )
    ca_repo.upsert(
        db, "ledger-kpi-b",
        kind="unattributed", confidence="low", reason="persisted idle",
    )
    db.commit()

    assert tx_repo.kpis(db, now=now)["idle_30d_pct"] == 50.0
