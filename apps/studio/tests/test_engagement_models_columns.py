from sqlalchemy import text
from app.db import engine


def test_engagement_tasks_has_platform_and_keywords():
    with engine.connect() as conn:
        cols = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='engagement_tasks'"
            )).all()
        }
    assert "platform" in cols
    assert "keywords" in cols


def test_engagement_runs_has_comment_candidates():
    with engine.connect() as conn:
        cols = {
            row[0]
            for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='engagement_runs'"
            )).all()
        }
    assert "comment_candidates" in cols


def test_engagement_tasks_platform_default_is_tiktok():
    from app.repositories import engagement as eng_repo
    from sqlmodel import Session
    with Session(engine) as s:
        tid = eng_repo.create_task(
            s, group_id=None, action="tiktok_ai_comment",
            source="manual_links", target_links=["x"],
            target_publication_ids=[], comment_probability=100,
            comments_per_video=1,
        )
        task = eng_repo.get_task(s, tid)
    assert task["platform"] == "tiktok"
    assert task["keywords"] == []
