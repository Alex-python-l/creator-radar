"""Tests for the /healthz liveness endpoint."""


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["db"] == "ok"
    assert "worker_last_seen" in data
    # worker_last_seen is None in tests since no actual worker has run
