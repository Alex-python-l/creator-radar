"""Smoke tests for POST /api/writer/understanding/{vid}/refresh."""


def test_refresh_endpoint_404_for_missing_video(client):
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post("/api/writer/understanding/99999/refresh")
    assert r.status_code == 404


def test_refresh_endpoint_defers_extract_task(client, monkeypatch,
                                               default_group_id, tmp_path):
    from app.repositories import videos as vid_repo
    from app.db import session_scope
    from app.tasks.writer_understanding import extract_understanding_task
    from app.tasks import procrastinate_app

    f = tmp_path / "v.mp4"; f.write_bytes(b"\x00")
    with session_scope() as s:
        vid = vid_repo.create(s, "v.mp4", str(f), group_id=default_group_id)

    deferred: list[dict] = []

    async def fake_defer_async(**kwargs):
        deferred.append(kwargs)
        return type("Job", (), {"id": "j"})()

    monkeypatch.setattr(extract_understanding_task, "defer_async",
                        fake_defer_async)
    fake_deferrer = type("FakeDeferrer", (),
                        {"defer_async": staticmethod(fake_defer_async)})()
    monkeypatch.setattr(procrastinate_app, "configure_task",
                        lambda name, **kw: fake_deferrer)

    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.post(f"/api/writer/understanding/{vid}/refresh")
    assert r.status_code == 200
    assert r.json()["queued"] is True
    assert deferred == [{"video_id": vid}]
