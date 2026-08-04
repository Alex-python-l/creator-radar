"""End-to-end FB engagement flow: API → submit → GeeLark → check."""
from sqlmodel import Session

from app.db import engine
from app.repositories import accounts as acc_repo
from app.repositories import engagement as eng_repo
from app.services import engagement_service as svc


class _FakeClient:
    """Records calls; configurable status returns for query_tasks."""

    def __init__(self, status_seq=(3,)):
        self.created: list[dict] = []
        self.status_seq = list(status_seq)
        self.queried: list[str] = []
        self.stopped: list[str] = []

    def create_facebook_auto_comment_task(
        self, env_id, *, post_address, comments, keywords,
        schedule_at=None, name=None, remark=None,
    ):
        self.created.append(dict(
            env_id=env_id, post_address=post_address,
            comments=comments, keywords=keywords,
        ))
        return "fake-fb-task-1"

    def query_tasks(self, ids):
        self.queried.extend(ids)
        status = self.status_seq.pop(0) if self.status_seq else 3
        return [{"id": ids[0], "status": status, "cost": 100}]

    def get_task_detail(self, tid):
        return {"resultImages": ["https://shot/result.jpg"]}

    def stop_phones(self, ids):
        self.stopped.extend(ids)


def test_full_flow_success(default_group_id):
    with Session(engine) as s:
        aid = acc_repo.create(s, {
            "profile_name": "fb-flow",
            "facebook_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-flow",
            "group_id": default_group_id,
        })
        result = svc.create_comment_batch(
            s, platform="facebook", account_ids=[aid],
            target_links=["https://www.facebook.com/zuck/posts/abc"],
            comments_per_video=2,
            comment_mode=svc.COMMENT_MODE_PROVIDED,
            provided_comments=["Nice!", "Great!"],
            keywords=["english"],
        )
        run_id = result["run_ids"][0]

    client = _FakeClient(status_seq=[3])

    with Session(engine) as s:
        task_id = svc.submit_run_to_geelark(s, client, run_id=run_id)
        assert task_id == "fake-fb-task-1"
        outcome = svc.check_engagement_run(s, client, run_id)
        run = eng_repo.get_run(s, run_id)

    assert outcome == "success"
    assert run["result"] == "SUCCESS"
    assert run["result_screenshot"] == "https://shot/result.jpg"
    assert client.stopped == ["ph-flow"]


def test_full_flow_failure_status_4(default_group_id):
    with Session(engine) as s:
        aid = acc_repo.create(s, {
            "profile_name": "fb-fail",
            "facebook_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-fail",
            "group_id": default_group_id,
        })
        result = svc.create_comment_batch(
            s, platform="facebook", account_ids=[aid],
            target_links=["https://www.facebook.com/zuck/posts/abc"],
            comments_per_video=1,
            comment_mode=svc.COMMENT_MODE_PROVIDED,
            provided_comments=["Nice!"],
            keywords=["k"],
        )
        run_id = result["run_ids"][0]

    class _FailClient(_FakeClient):
        def get_task_detail(self, tid):
            return {"failDesc": "kw not found", "failCode": 1001,
                    "resultImages": ["https://shot/fail.jpg"]}

    client = _FailClient(status_seq=[4])

    with Session(engine) as s:
        svc.submit_run_to_geelark(s, client, run_id=run_id)
        outcome = svc.check_engagement_run(s, client, run_id)
        run = eng_repo.get_run(s, run_id)

    assert outcome == "failure"
    assert run["result"] == "FAILURE"
    assert run["fail_code"] == 1001
    assert "kw not found" in run["notes"]
