import pytest


def test_create_returns_id_and_persists_fields(db):
    from app.repositories import groups as gr
    gid = gr.create(db, name="project-jp", notes="JP batch")
    row = gr.get(db, gid)
    assert row["name"] == "project-jp"
    assert row["notes"] == "JP batch"


def test_create_duplicate_name_raises(db):
    from app.repositories import groups as gr
    gr.create(db, name="dup")
    with pytest.raises(Exception):
        gr.create(db, name="dup")


def test_list_all_returns_all_groups(db):
    from app.repositories import groups as gr
    gr.create(db, name="a")
    gr.create(db, name="b")
    names = [r["name"] for r in gr.list_all(db)]
    assert set(names) == {"a", "b"}


def test_get_by_name(db):
    from app.repositories import groups as gr
    gid = gr.create(db, name="lookup-me")
    row = gr.get_by_name(db, "lookup-me")
    assert row is not None and row["id"] == gid
    assert gr.get_by_name(db, "nope") is None
