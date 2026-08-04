import pytest
from sqlmodel import Session
from app.db import engine
from app.repositories import engagement as eng_repo


def test_create_task_writes_platform_and_keywords():
    with Session(engine) as s:
        tid = eng_repo.create_task(
            s, group_id=None, action="facebook_auto_comment",
            source="manual_links", target_links=["x"],
            target_publication_ids=[], comment_probability=0,
            comments_per_video=3, platform="facebook",
            keywords=["k1", "k2"],
        )
        task = eng_repo.get_task(s, tid)
    assert task["platform"] == "facebook"
    assert task["keywords"] == ["k1", "k2"]


def test_list_tasks_filters_by_platform():
    with Session(engine) as s:
        tk_id = eng_repo.create_task(
            s, group_id=None, action="tiktok_ai_comment",
            source="manual_links", target_links=["a"],
            target_publication_ids=[], comment_probability=100,
            comments_per_video=1,
        )
        fb_id = eng_repo.create_task(
            s, group_id=None, action="facebook_auto_comment",
            source="manual_links", target_links=["b"],
            target_publication_ids=[], comment_probability=0,
            comments_per_video=2, platform="facebook",
            keywords=["x"],
        )
        fb_tasks = eng_repo.list_tasks(s, platform="facebook")
        tk_tasks = eng_repo.list_tasks(s, platform="tiktok")
    assert {t["id"] for t in fb_tasks} == {fb_id}
    assert {t["id"] for t in tk_tasks} == {tk_id}


def test_set_comment_candidates_writes_pool():
    with Session(engine) as s:
        tid = eng_repo.create_task(
            s, group_id=None, action="facebook_auto_comment",
            source="manual_links", target_links=["x"],
            target_publication_ids=[], comment_probability=0,
            comments_per_video=2, platform="facebook",
            keywords=["k1"],
        )
        rid = eng_repo.create_run(
            s, engagement_task_id=tid, account_id=None,
            target_link="x", action="facebook_auto_comment",
        )
        eng_repo.set_comment_candidates(
            s, rid, candidates=["c1", "c2"], source="studio_ai",
        )
        run = eng_repo.get_run(s, rid)
    assert run["comment_candidates"] == ["c1", "c2"]
    assert run["comment_text"] == "c1"  # 第一条作展示用
    assert run["comment_source"] == "studio_ai"


def test_list_runs_filters_by_platform():
    with Session(engine) as s:
        fb_task = eng_repo.create_task(
            s, group_id=None, action="facebook_auto_comment",
            source="manual_links", target_links=["fb-target"],
            target_publication_ids=[], comment_probability=0,
            comments_per_video=1, platform="facebook", keywords=["k"],
        )
        tk_task = eng_repo.create_task(
            s, group_id=None, action="tiktok_ai_comment",
            source="manual_links", target_links=["tk-target"],
            target_publication_ids=[], comment_probability=100,
            comments_per_video=1,
        )
        fb_run = eng_repo.create_run(
            s, engagement_task_id=fb_task, account_id=None,
            target_link="fb-target", action="facebook_auto_comment",
        )
        tk_run = eng_repo.create_run(
            s, engagement_task_id=tk_task, account_id=None,
            target_link="tk-target", action="tiktok_ai_comment",
        )
        fb_runs = eng_repo.list_runs(s, platform="facebook")
        tk_runs = eng_repo.list_runs(s, platform="tiktok")
    assert {r["id"] for r in fb_runs} == {fb_run}
    assert {r["id"] for r in tk_runs} == {tk_run}
