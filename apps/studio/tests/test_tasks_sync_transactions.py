"""sync_transactions makes one call per tick (GeeLark ignores `page`)."""

import asyncio

from app.tasks import sync_transactions
from app.repositories import transactions as tx_repo


def _row(txn_id: str, env: str = "ph-z", t: int = 1700000000) -> dict:
    return {
        "id": txn_id,
        "envId": env,
        "amount": -0.05,
        "usedTime": 7,
        "type": 2,
        "chargeType": 2,
        "createdTime": t,
    }


class _FakeClient:
    def __init__(self, response: list[dict]):
        self.response = response
        self.calls = 0

    def list_billing_transactions(self, limit: int = 1000):
        self.calls += 1
        return {"total": len(self.response), "list": self.response}


def test_sync_inserts_new_rows(db, monkeypatch):
    fake = _FakeClient([
        _row("brand-new-1", t=1700001000),
        _row("brand-new-2", t=1700000999),
    ])
    monkeypatch.setattr(sync_transactions, "make_client", lambda: fake)

    msg = asyncio.run(
        sync_transactions.sync_geelark_transactions(timestamp=0)
    )
    assert fake.calls == 1
    assert "fetched=2" in msg
    assert "inserted=2" in msg
    assert "conflicts=0" in msg


def test_sync_counts_conflicts_on_second_run(db, monkeypatch):
    """Re-running the sync against the same response is a no-op."""
    fake = _FakeClient([_row("dup", t=1700000000)])
    monkeypatch.setattr(sync_transactions, "make_client", lambda: fake)

    asyncio.run(sync_transactions.sync_geelark_transactions(timestamp=0))
    msg = asyncio.run(
        sync_transactions.sync_geelark_transactions(timestamp=0)
    )
    assert "conflicts=1" in msg
    assert "inserted=0" in msg


def test_sync_empty_response(db, monkeypatch):
    fake = _FakeClient([])
    monkeypatch.setattr(sync_transactions, "make_client", lambda: fake)
    msg = asyncio.run(
        sync_transactions.sync_geelark_transactions(timestamp=0)
    )
    assert msg.endswith("empty")
