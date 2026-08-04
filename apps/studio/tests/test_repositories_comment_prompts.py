"""Repository-level CRUD over comment_prompts."""
from app.repositories import comment_prompts as repo


def test_create_then_get(session):
    pid = repo.create(session, name="snarky", body="be snarky")
    row = repo.get(session, pid)
    assert row["id"] == pid
    assert row["name"] == "snarky"
    assert row["body"] == "be snarky"


def test_list_all_sorted_by_name(session):
    repo.create(session, name="beta", body="b")
    repo.create(session, name="alpha", body="a")
    repo.create(session, name="gamma", body="g")
    names = [p["name"] for p in repo.list_all(session)]
    assert names == ["alpha", "beta", "gamma"]


def test_get_by_name(session):
    repo.create(session, name="x", body="b")
    assert repo.get_by_name(session, "x")["name"] == "x"
    assert repo.get_by_name(session, "missing") is None


def test_update_partial(session):
    pid = repo.create(session, name="p", body="old")
    assert repo.update(session, pid, body="new") is True
    assert repo.get(session, pid)["body"] == "new"
    assert repo.get(session, pid)["name"] == "p"


def test_update_returns_false_for_unknown(session):
    assert repo.update(session, 99999, body="x") is False


def test_delete(session):
    pid = repo.create(session, name="p", body="b")
    assert repo.delete(session, pid) is True
    assert repo.get(session, pid) is None
    assert repo.delete(session, pid) is False
