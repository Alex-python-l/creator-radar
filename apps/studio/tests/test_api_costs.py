"""API surface tests for /api/costs/*."""

import calendar
from datetime import datetime, timedelta

import pytest

from app.models import Account, Publication, Video
from app.repositories import transactions as tx_repo


def _epoch(dt: datetime) -> int:
    return calendar.timegm(dt.timetuple())


@pytest.fixture
def authed_client(client, monkeypatch):
    import app.config as cfg
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    return client


@pytest.fixture
def seeded(db):
    a = Account(profile_name="alice", geelark_phone_id="ph-1")
    db.add(a); db.commit(); db.refresh(a)
    v = Video(original_filename="x.mp4", stored_path="/tmp/x.mp4")
    db.add(v); db.commit(); db.refresh(v)
    t0 = datetime(2026, 6, 1, 12, 0, 0)
    db.add(Publication(
        video_id=v.id, account_id=a.id, platform="tiktok",
        result="SUCCESS", posted_at=t0 - timedelta(minutes=3),
    ))
    db.commit()
    tx_repo.upsert_many(db, [
        {"id": "rowA", "envId": "ph-1", "amount": -0.05, "usedTime": 7,
         "type": 2, "chargeType": 2, "createdTime": _epoch(t0)},
        {"id": "rowB", "envId": "ph-1", "amount": -0.10, "usedTime": 14,
         "type": 1, "chargeType": 2,
         "createdTime": _epoch(t0 + timedelta(hours=3))},
    ])
    return {"account_id": a.id, "t0": t0}


def test_requires_auth(client):
    assert client.get("/api/costs/transactions").status_code == 401
    assert client.get("/api/costs/summary").status_code == 401
    assert client.get("/api/costs/kpis").status_code == 401
    assert client.get("/api/costs/export.csv").status_code == 401


def test_list_transactions_basic(authed_client, seeded):
    r = authed_client.get("/api/costs/transactions")
    body = r.json()
    assert r.status_code == 200
    assert body["total"] == 2
    kinds = {item["id"]: item["attribution"]["kind"] for item in body["items"]}
    assert kinds["rowA"] == "publish"
    assert kinds["rowB"] == "idle"


def test_list_transactions_filter_kind(authed_client, seeded):
    r = authed_client.get("/api/costs/transactions?kind=idle")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "rowB"


def test_summary_kind(authed_client, seeded):
    r = authed_client.get("/api/costs/summary?group_by=kind")
    keyed = {g["key"]: g for g in r.json()["groups"]}
    assert pytest.approx(keyed["publish"]["total"], abs=1e-4) == -0.05
    assert pytest.approx(keyed["idle"]["total"], abs=1e-4) == -0.10


def test_kpis(authed_client, seeded):
    r = authed_client.get("/api/costs/kpis")
    body = r.json()
    assert {"today_spent", "mtd_spent", "day_avg_30d", "idle_30d_pct"} <= body.keys()


def test_export_csv(authed_client, seeded):
    r = authed_client.get("/api/costs/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    text = r.text
    assert "time_utc,env_id" in text.splitlines()[0]
    assert "rowA" not in text  # the id itself isn't a CSV column
    # Two data rows
    assert len([ln for ln in text.splitlines() if ln.strip()]) == 1 + 2
