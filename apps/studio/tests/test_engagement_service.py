import pytest


def _group_with_accounts(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="Engage Group")
    owner = acc_repo.create(session, {
        "profile_name": "Owner",
        "geelark_phone_id": "ph-owner",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    commenter = acc_repo.create(session, {
        "profile_name": "Commenter",
        "geelark_phone_id": "ph-commenter",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })
    logged_out = acc_repo.create(session, {
        "profile_name": "Logged Out",
        "geelark_phone_id": "ph-out",
        "group_id": gid,
        "tiktok_logged_in": 0,
        "warmed_up": 1,
    })
    return gid, owner, commenter, logged_out


def test_create_comment_batch_for_manual_links(session):
    from app.services import engagement_service
    from app.repositories import engagement as eng_repo

    gid, owner, commenter, logged_out = _group_with_accounts(session)

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[owner, commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )

    assert result["created_runs"] == 1
    assert result["skipped_runs"] == 0
    runs = eng_repo.list_runs_for_task(session, result["task_id"])
    assert [r["account_id"] for r in runs] == [owner]


def test_create_comment_batch_dedupes_duplicate_manual_links(session):
    from app.services import engagement_service
    from app.repositories import engagement as eng_repo

    gid, owner, commenter, logged_out = _group_with_accounts(session)

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[owner, commenter],
        target_links=[
            "https://www.tiktok.com/@owner/video/1234567890",
            "https://www.tiktok.com/@owner/video/1234567890",
            "https://www.tiktok.com/@owner/video/1234567891",
        ],
        target_publication_ids=[],
        comment_probability=100,
    )

    assert result["created_runs"] == 2
    assert len(result["task_ids"]) == 2
    assert result["task_id"] == result["task_ids"][0]

    tasks = [eng_repo.get_task(session, task_id) for task_id in result["task_ids"]]
    runs = [
        eng_repo.list_runs_for_task(session, task_id)[0]
        for task_id in result["task_ids"]
    ]
    assert [task["target_links"] for task in tasks] == [
        ["https://www.tiktok.com/@owner/video/1234567890"],
        ["https://www.tiktok.com/@owner/video/1234567891"],
    ]
    assert [run["target_link"] for run in runs] == [
        "https://www.tiktok.com/@owner/video/1234567890",
        "https://www.tiktok.com/@owner/video/1234567891",
    ]


def test_create_comment_batch_round_robins_one_account_per_target(session):
    from app.services import engagement_service
    from app.repositories import engagement as eng_repo

    gid, owner, commenter, logged_out = _group_with_accounts(session)

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[owner, commenter],
        target_links=[
            "https://www.tiktok.com/@owner/video/1234567890",
            "https://www.tiktok.com/@owner/video/1234567891",
            "https://www.tiktok.com/@owner/video/1234567892",
        ],
        target_publication_ids=[],
        comment_probability=100,
    )

    assert result["created_runs"] == 3
    assert len(result["task_ids"]) == 3
    runs = [
        eng_repo.list_runs_for_task(session, task_id)[0]
        for task_id in result["task_ids"]
    ]
    assert all(run["account_id"] in {owner, commenter} for run in runs)


def test_create_comment_batch_rejects_invalid_link(session):
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)

    with pytest.raises(ValueError, match="TikTok video URL"):
        engagement_service.create_comment_batch(
            session,
            group_id=gid,
            account_ids=[owner],
            target_links=["https://example.com/not-tiktok"],
            target_publication_ids=[],
            comment_probability=100,
        )


def test_create_comment_batch_accepts_none_publication_ids(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[owner],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=None,
        comment_probability=100,
    )

    runs = eng_repo.list_runs_for_task(session, result["task_id"])
    assert result["created_runs"] == 1
    assert [r["account_id"] for r in runs] == [owner]


def test_create_comment_batch_rejects_empty_or_null_targets(session):
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)

    for target_links, target_publication_ids in (([], []), (None, None)):
        with pytest.raises(ValueError, match="at least one target link is required"):
            engagement_service.create_comment_batch(
                session,
                group_id=gid,
                account_ids=[owner],
                target_links=target_links,
                target_publication_ids=target_publication_ids,
                comment_probability=100,
            )


def test_create_comment_batch_skips_whitespace_phone_id(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    whitespace_phone = acc_repo.create(session, {
        "profile_name": "Whitespace",
        "geelark_phone_id": "   ",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "warmed_up": 1,
    })

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[owner],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )

    runs = eng_repo.list_runs_for_task(session, result["task_id"])
    assert result["created_runs"] == 1
    assert result["skipped_runs"] == 0
    assert [r["account_id"] for r in runs] == [owner]


def test_create_comment_batch_excludes_publication_owner(session, tmp_path):
    from app.repositories import publications as pub_repo
    from app.repositories import videos as vid_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    video_path = tmp_path / "owned.mp4"
    video_path.write_bytes(b"x")
    vid = vid_repo.create(session, "owned.mp4", str(video_path), group_id=gid)
    pub_id = pub_repo.create(session, video_id=vid, platform="tiktok")
    pub_repo.set_account(session, pub_id, owner)
    pub_repo.set_share_link(session, pub_id, "https://www.tiktok.com/@owner/video/1234567890")

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[owner, commenter],
        target_links=[],
        target_publication_ids=[pub_id],
        comment_probability=100,
    )

    runs = eng_repo.list_runs_for_task(session, result["task_id"])
    assert [r["account_id"] for r in runs] == [commenter]
    assert runs[0]["target_publication_id"] == pub_id
    assert runs[0]["target_account_id"] == owner


def test_create_comment_batch_dedupe_preserves_publication_owner(session, tmp_path):
    from app.repositories import publications as pub_repo
    from app.repositories import videos as vid_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    video_path = tmp_path / "owned.mp4"
    video_path.write_bytes(b"x")
    vid = vid_repo.create(session, "owned.mp4", str(video_path), group_id=gid)
    pub_id = pub_repo.create(session, video_id=vid, platform="tiktok")
    link = "https://www.tiktok.com/@owner/video/1234567890"
    pub_repo.set_account(session, pub_id, owner)
    pub_repo.set_share_link(session, pub_id, link)

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[owner, commenter],
        target_links=[link],
        target_publication_ids=[pub_id],
        comment_probability=100,
    )

    runs = eng_repo.list_runs_for_task(session, result["task_id"])
    task = eng_repo.get_task(session, result["task_id"])
    assert result["created_runs"] == 1
    assert [r["account_id"] for r in runs] == [commenter]
    assert runs[0]["target_publication_id"] == pub_id
    assert runs[0]["target_account_id"] == owner
    assert task["target_publication_ids"] == [pub_id]


def test_submit_engagement_run_to_geelark_marks_initiated(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="geelark_ai",
    )
    run = eng_repo.list_runs_for_task(session, result["task_id"])[0]

    class FakeClient:
        def create_tiktok_ai_comment_task(self, env_id, links, **kwargs):
            assert env_id == "ph-commenter"
            assert links == ["https://www.tiktok.com/@owner/video/1234567890"]
            assert kwargs["comment_probability"] == 100
            return "gee-comment-1"

    task_id = engagement_service.submit_run_to_geelark(
        session, FakeClient(), run_id=run["id"])

    assert task_id == "gee-comment-1"
    row = eng_repo.get_run(session, run["id"])
    assert row["result"] == "INITIATED"
    assert row["geelark_task_id"] == "gee-comment-1"


def test_submit_engagement_run_skips_terminal_stale_job(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="geelark_ai",
    )
    run = eng_repo.list_runs_for_task(session, result["task_id"])[0]
    eng_repo.set_result(session, run["id"], "SUCCESS", "already done")

    class FakeClient:
        submit_calls = 0

        def create_tiktok_ai_comment_task(self, *args, **kwargs):
            self.submit_calls += 1
            return "must-not-submit"

    client = FakeClient()
    task_id = engagement_service.submit_run_to_geelark(
        session, client, run_id=run["id"])

    assert task_id == ""
    assert client.submit_calls == 0
    assert eng_repo.get_run(session, run["id"])["result"] == "SUCCESS"


def test_submit_engagement_run_generates_studio_ai_comment(session, monkeypatch):
    from app.repositories import comment_prompts as cp_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    pid = cp_repo.create(
        session,
        name="ielts-mock",
        body=(
            "Push the daily AI mock interview angle. "
            "Mention practice naturally without sounding like an ad."
        ),
    )
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        comment_prompt_id=pid,
    )
    run = eng_repo.list_runs_for_task(session, result["task_id"])[0]

    monkeypatch.setattr(
        engagement_service.tiktok_metadata,
        "fetch_tiktok_metadata",
        lambda link: {
            "source_url": link,
            "title": "How to improve IELTS speaking part 2",
            "description": "A creator explains a daily speaking practice loop.",
        },
    )

    def fake_generate_comment_for_target(**kwargs):
        assert kwargs["target_title"] == "How to improve IELTS speaking part 2"
        assert "daily AI mock interview angle" in kwargs["prompt_body"]
        return "This kind of daily speaking loop is exactly what helps it stick."

    monkeypatch.setattr(
        engagement_service.comment_generation,
        "generate_comment_for_target",
        fake_generate_comment_for_target,
    )

    class FakeClient:
        def create_tiktok_ai_comment_task(self, env_id, links, **kwargs):
            assert env_id == "ph-commenter"
            assert kwargs["comment"] == (
                "This kind of daily speaking loop is exactly what helps it stick."
            )
            return "gee-studio-comment-1"

    task_id = engagement_service.submit_run_to_geelark(
        session, FakeClient(), run_id=run["id"])

    assert task_id == "gee-studio-comment-1"
    row = eng_repo.get_run(session, run["id"])
    assert row["result"] == "INITIATED"
    assert row["target_title"] == "How to improve IELTS speaking part 2"
    assert row["target_description"] == "A creator explains a daily speaking practice loop."
    assert row["comment_text"] == (
        "This kind of daily speaking loop is exactly what helps it stick."
    )


def test_create_comment_batch_provided_mode_writes_comment_per_run(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    pool = [
        "girl this is so good 😍",
        "need to try this asap",
        "wait the texture looks insane",
    ]
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comments_per_video=3,
        comment_probability=100,
        comment_mode="provided",
        provided_comments=pool,
    )
    assert result["created_runs"] == 3
    runs = [eng_repo.get_run(session, rid) for rid in result["run_ids"]]
    texts = [r["comment_text"] for r in runs]
    sources = [r["comment_source"] for r in runs]
    assert sorted(texts) == sorted(pool)
    assert sources == ["provided"] * 3


def test_create_comment_batch_provided_mode_cycles_short_pool(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comments_per_video=5,
        comment_probability=100,
        comment_mode="provided",
        provided_comments=["aaa", "bbb"],
    )
    runs = [eng_repo.get_run(session, rid) for rid in result["run_ids"]]
    texts = [r["comment_text"] for r in runs]
    assert len(texts) == 5
    assert set(texts) == {"aaa", "bbb"}


def test_create_comment_batch_provided_mode_rejects_empty_pool(session):
    from app.services import engagement_service

    gid, _owner, commenter, _ = _group_with_accounts(session)
    with pytest.raises(ValueError, match="provided_comments"):
        engagement_service.create_comment_batch(
            session,
            group_id=gid,
            account_ids=[commenter],
            target_links=["https://www.tiktok.com/@owner/video/1234567890"],
            target_publication_ids=[],
            comment_probability=100,
            comment_mode="provided",
            provided_comments=["   ", ""],
        )


def test_submit_engagement_run_provided_mode_reuses_stored_comment(session, monkeypatch):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="provided",
        provided_comments=["girl this is so good 😍"],
    )
    run = eng_repo.list_runs_for_task(session, result["task_id"])[0]

    # Gemini path must NOT be called in provided mode.
    def boom(**kwargs):
        raise AssertionError("provided mode should not call generate_comment_for_target")

    monkeypatch.setattr(
        engagement_service.comment_generation,
        "generate_comment_for_target",
        boom,
    )

    class FakeClient:
        def create_tiktok_ai_comment_task(self, env_id, links, **kwargs):
            assert kwargs["comment"] == "girl this is so good 😍"
            return "gee-provided-1"

    task_id = engagement_service.submit_run_to_geelark(
        session, FakeClient(), run_id=run["id"])

    assert task_id == "gee-provided-1"
    row = eng_repo.get_run(session, run["id"])
    assert row["result"] == "INITIATED"
    assert row["comment_text"] == "girl this is so good 😍"
    assert row["comment_source"] == "provided"


def test_create_comment_batch_requires_prompt_id_for_studio_ai(session):
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)

    with pytest.raises(ValueError, match="comment_prompt_id is required"):
        engagement_service.create_comment_batch(
            session,
            group_id=gid,
            account_ids=[commenter],
            target_links=["https://www.tiktok.com/@owner/video/1234567890"],
            target_publication_ids=[],
            comment_probability=100,
            comment_mode="studio_ai",
        )


def test_prepare_studio_comment_falls_back_to_legacy_brief(session, monkeypatch):
    """Rows persisted before the prompt pool keep working: when
    comment_prompt_id is NULL, _combined_brief() supplies the brief."""
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    tid = eng_repo.create_task(
        session,
        group_id=gid,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        comment_brief="legacy free-text brief about CEFR highlighter",
        comments_per_video=1,
    )
    rid = eng_repo.create_run(
        session,
        engagement_task_id=tid,
        account_id=commenter,
        target_link="https://www.tiktok.com/@owner/video/1234567890",
        action="tiktok_ai_comment",
        target_publication_id=None,
        target_account_id=None,
    )

    monkeypatch.setattr(
        engagement_service.tiktok_metadata,
        "fetch_tiktok_metadata",
        lambda link: {"source_url": link, "title": "t", "description": "d"},
    )

    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(
        engagement_service.comment_generation,
        "generate_comment_for_target",
        fake_generate,
    )

    parent = eng_repo.get_task(session, tid)
    run = eng_repo.get_run(session, rid)
    engagement_service._prepare_studio_comment(session, run, parent)

    assert "legacy free-text brief about CEFR highlighter" in captured["prompt_body"]


def test_submit_engagement_run_failure_marks_result_and_stops_phone(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="geelark_ai",
    )
    run = eng_repo.list_runs_for_task(session, result["task_id"])[0]

    class FailingClient:
        stopped = []

        def create_tiktok_ai_comment_task(self, env_id, links, **kwargs):
            raise RuntimeError("GeeLark submit timeout")

        def stop_phones(self, ids):
            self.stopped.extend(ids)
            return {}

    client = FailingClient()
    with pytest.raises(RuntimeError, match="GeeLark submit timeout"):
        engagement_service.submit_run_to_geelark(session, client, run_id=run["id"])

    row = eng_repo.get_run(session, run["id"])
    parent = eng_repo.get_task(session, result["task_id"])
    assert row["result"] == "FAILURE"
    assert "submit failed" in row["notes"]
    assert "GeeLark submit timeout" in row["notes"]
    assert parent["status"] == "FAILURE"
    assert client.stopped == ["ph-commenter"]


def test_check_engagement_run_success_stops_phone(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]
    eng_repo.mark_initiated(session, run_id, task_id="gee-ok", schedule_at=123,
                            notes="submitted")

    class DoneClient:
        stopped = []
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 3, "cost": 135}]
        def get_task_detail(self, task_id):
            return {
                "resultImages": [
                    "https://img/success-first.jpg",
                    "https://img/success-last.jpg",
                ],
            }
        def stop_phones(self, ids):
            self.stopped.extend(ids)
            return {}

    client = DoneClient()
    outcome = engagement_service.check_engagement_run(session, client, run_id)

    assert outcome == "success"
    assert eng_repo.get_run(session, run_id)["result"] == "SUCCESS"
    assert eng_repo.get_run(session, run_id)["cost"] == 135
    assert eng_repo.get_run(session, run_id)["result_screenshot"] == (
        "https://img/success-last.jpg"
    )
    assert client.stopped == ["ph-commenter"]


def test_mark_run_timed_out_fails_run_and_stops_phone(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]
    eng_repo.mark_initiated(session, run_id, task_id="gee-slow", schedule_at=123,
                            notes="submitted")

    class Client:
        stopped = []

        def stop_phones(self, ids):
            self.stopped.extend(ids)
            return {}

    client = Client()
    outcome = engagement_service.mark_run_timed_out(session, client, run_id)
    row = eng_repo.get_run(session, run_id)
    parent = eng_repo.get_task(session, result["task_id"])

    assert outcome == "failure"
    assert row["result"] == "FAILURE"
    assert "timed out" in row["notes"]
    assert parent["status"] == "FAILURE"
    assert client.stopped == ["ph-commenter"]


def test_mark_submit_retries_exhausted_fails_queued_run(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]
    eng_repo.mark_queued(session, run_id)

    outcome = engagement_service.mark_submit_retries_exhausted(session, run_id)
    row = eng_repo.get_run(session, run_id)
    parent = eng_repo.get_task(session, result["task_id"])

    assert outcome == "failure"
    assert row["result"] == "FAILURE"
    assert "submit" in row["notes"]
    assert "exhausted" in row["notes"]
    assert parent["status"] == "FAILURE"


def test_check_engagement_run_failure_saves_detail(session):
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, logged_out = _group_with_accounts(session)
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]
    eng_repo.mark_initiated(session, run_id, task_id="gee-fail", schedule_at=123,
                            notes="submitted")

    class FailedClient:
        def query_tasks(self, ids):
            return [{"id": ids[0], "status": 4}]
        def get_task_detail(self, task_id):
            return {"failCode": 20267, "failDesc": "Comment box missing",
                    "resultImages": ["https://img/first.jpg", "https://img/last.jpg"]}
        def stop_phones(self, ids):
            return {}

    outcome = engagement_service.check_engagement_run(session, FailedClient(), run_id)
    row = eng_repo.get_run(session, run_id)

    assert outcome == "failure"
    assert row["result"] == "FAILURE"
    assert row["fail_code"] == 20267
    assert row["fail_screenshot"] == "https://img/last.jpg"
    assert row["notes"] == "Comment box missing"


def test_engagement_task_has_comments_per_video_column(session):
    from sqlalchemy import inspect
    from app.db import engine
    cols = {c["name"] for c in inspect(engine).get_columns("engagement_tasks")}
    assert "comments_per_video" in cols


def test_eng_repo_create_task_persists_comments_per_video(session):
    from app.repositories import engagement as eng_repo
    task_id = eng_repo.create_task(
        session,
        group_id=None,
        action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@x/video/1"],
        target_publication_ids=[],
        comment_probability=100,
        comment_mode="studio_ai",
        comment_brief=None,
        comments_per_video=5,
    )
    task = eng_repo.get_task(session, task_id)
    assert task["comments_per_video"] == 5


def test_create_comment_batch_reuses_phones_when_per_video_exceeds_phone_count(session):
    from app.services import engagement_service
    from app.repositories import engagement as eng_repo

    gid, _owner, commenter, _logged_out = _group_with_accounts(session)

    result = engagement_service.create_comment_batch(
        session,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@x/video/1"],
        target_publication_ids=[],
        comments_per_video=3,
    )

    all_runs = []
    for tid in result["task_ids"]:
        all_runs.extend(eng_repo.list_runs_for_task(session, tid))
    assert len(all_runs) == 3
    assert all(r["account_id"] == commenter for r in all_runs)


def test_create_comment_batch_creates_videos_times_per_video_runs(session):
    from app.services import engagement_service
    from app.repositories import engagement as eng_repo

    gid, _owner, commenter, _logged_out = _group_with_accounts(session)

    result = engagement_service.create_comment_batch(
        session,
        account_ids=[commenter],
        target_links=[
            "https://www.tiktok.com/@x/video/1",
            "https://www.tiktok.com/@x/video/2",
        ],
        target_publication_ids=[],
        comments_per_video=2,
    )

    assert result["created_runs"] == 4
    all_runs = []
    for tid in result["task_ids"]:
        all_runs.extend(eng_repo.list_runs_for_task(session, tid))
    assert len(all_runs) == 4


def test_create_comment_batch_rejects_unknown_account(session):
    from app.services import engagement_service

    with pytest.raises(ValueError, match="does not exist"):
        engagement_service.create_comment_batch(
            session,
            account_ids=[99999],
            target_links=["https://www.tiktok.com/@x/video/1"],
            target_publication_ids=[],
            comments_per_video=1,
        )


def test_create_comment_batch_rejects_empty_account_ids(session):
    from app.services import engagement_service

    with pytest.raises(ValueError, match="at least one phone must be selected"):
        engagement_service.create_comment_batch(
            session,
            account_ids=[],
            target_links=["https://www.tiktok.com/@x/video/1"],
            target_publication_ids=[],
            comments_per_video=1,
        )


def test_create_comment_batch_distributes_targets_across_phones(session):
    """Regression: per-target itertools.cycle previously made every target
    pick accounts[0]. Cycle must be shared across targets."""
    from app.services import engagement_service
    from app.repositories import engagement as eng_repo
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo

    gid = group_repo.create(session, name="dist")
    a = acc_repo.create(session, {
        "profile_name": "A", "geelark_phone_id": "ph-a",
        "group_id": gid, "tiktok_logged_in": 1, "warmed_up": 1,
    })
    b = acc_repo.create(session, {
        "profile_name": "B", "geelark_phone_id": "ph-b",
        "group_id": gid, "tiktok_logged_in": 1, "warmed_up": 1,
    })
    c = acc_repo.create(session, {
        "profile_name": "C", "geelark_phone_id": "ph-c",
        "group_id": gid, "tiktok_logged_in": 1, "warmed_up": 1,
    })
    d = acc_repo.create(session, {
        "profile_name": "D", "geelark_phone_id": "ph-d",
        "group_id": gid, "tiktok_logged_in": 1, "warmed_up": 1,
    })

    result = engagement_service.create_comment_batch(
        session,
        account_ids=[a, b, c, d],
        target_links=[
            "https://www.tiktok.com/@x/video/1",
            "https://www.tiktok.com/@x/video/2",
            "https://www.tiktok.com/@x/video/3",
            "https://www.tiktok.com/@x/video/4",
        ],
        target_publication_ids=[],
        comments_per_video=1,
    )

    all_runs = []
    for tid in result["task_ids"]:
        all_runs.extend(eng_repo.list_runs_for_task(session, tid))
    assigned = [r["account_id"] for r in all_runs]
    assert set(assigned) == {a, b, c, d}, (
        f"expected each of 4 phones used once, got {assigned}"
    )


# ----- Daily per-phone per-platform comment limit -----------------------------


def _seed_completed_runs(session, *, gid: int, aid: int, count: int,
                         platform: str = "tiktok", result: str = "SUCCESS"):
    """Insert `count` engagement runs for (aid, platform) that consume quota."""
    from app.repositories import engagement as eng_repo

    action = (
        "facebook_auto_comment" if platform == "facebook"
        else "tiktok_ai_comment"
    )
    base = "https://facebook.com/post/" if platform == "facebook" else "https://x/v/"
    for i in range(count):
        tid = eng_repo.create_task(
            session,
            group_id=gid,
            action=action,
            source="manual_links",
            target_links=[f"{base}{i}"],
            target_publication_ids=[],
            comment_probability=100,
            platform=platform,
        )
        rid = eng_repo.create_run(
            session, engagement_task_id=tid, account_id=aid,
            target_link=f"{base}{i}", action=action,
        )
        if result == "SUCCESS":
            eng_repo.set_result(session, rid, "SUCCESS", "ok")
        elif result == "FAILURE":
            eng_repo.set_result(session, rid, "FAILURE", "boom")


def test_create_comment_batch_accepts_account_over_daily_limit(session):
    """Daily-limit enforcement is at worker submit time, not at batch
    create time. Operators should be able to queue tomorrow's work today
    even if today's quota is exhausted."""
    from app.repositories import engagement as eng_repo
    from app.repositories import settings as srepo
    from app.services import engagement_service

    gid, owner, commenter, _ = _group_with_accounts(session)
    srepo.update(session, {"daily_comment_limit_tiktok": "3"})
    _seed_completed_runs(session, gid=gid, aid=commenter, count=3)

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    runs = eng_repo.list_runs_for_task(session, result["task_id"])
    assert [r["account_id"] for r in runs] == [commenter]
    assert runs[0]["result"] == "PENDING"


def test_create_comment_batch_failure_runs_do_not_consume_quota(session):
    """FAILURE runs should not block new batches; only SUCCESS / in-flight do."""
    from app.repositories import engagement as eng_repo
    from app.repositories import settings as srepo
    from app.services import engagement_service

    gid, owner, commenter, _ = _group_with_accounts(session)
    srepo.update(session, {"daily_comment_limit_tiktok": "3"})
    _seed_completed_runs(
        session, gid=gid, aid=commenter, count=5, result="FAILURE",
    )

    # 5 FAILURE runs do not consume quota, so a new batch must still succeed.
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    runs = eng_repo.list_runs_for_task(session, result["task_id"])
    assert [r["account_id"] for r in runs] == [commenter]


def test_create_comment_batch_zero_limit_disables_cap(session):
    """daily_comment_limit_tiktok='0' disables the cap entirely."""
    from app.repositories import engagement as eng_repo
    from app.repositories import settings as srepo
    from app.services import engagement_service

    gid, owner, commenter, _ = _group_with_accounts(session)
    srepo.update(session, {"daily_comment_limit_tiktok": "0"})
    _seed_completed_runs(session, gid=gid, aid=commenter, count=100)

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    assert result["created_runs"] == 1


def test_create_comment_batch_facebook_limit_is_separate_from_tiktok(session):
    """Facebook quota is independent of TikTok quota — exhausting TikTok
    doesn't block a Facebook batch."""
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.repositories import groups as group_repo
    from app.repositories import settings as srepo
    from app.services import engagement_service

    gid = group_repo.create(session, name="g")
    commenter = acc_repo.create(session, {
        "profile_name": "FB Commenter",
        "geelark_phone_id": "ph-fb",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "facebook_logged_in": 1,
        "warmed_up": 1,
    })

    srepo.update(session, {
        "daily_comment_limit_tiktok": "2",
        "daily_comment_limit_facebook": "2",
    })
    # Exhaust the TikTok quota.
    _seed_completed_runs(
        session, gid=gid, aid=commenter, count=2, platform="tiktok",
    )

    # FB batch with provided pool — should succeed despite TikTok being exhausted.
    result = engagement_service.create_comment_batch(
        session,
        platform="facebook",
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.facebook.com/share/p/abc123def/"],
        target_publication_ids=[],
        comment_mode="provided",
        provided_comments=["nice post"],
        keywords=["seed"],
    )
    assert result["created_runs"] == 1


def test_create_comment_batch_fb_accepts_over_fb_limit(session):
    """FB account at FB daily limit should still accept the batch — worker
    will requeue to tomorrow on submit."""
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as group_repo
    from app.repositories import settings as srepo
    from app.services import engagement_service

    gid = group_repo.create(session, name="g")
    commenter = acc_repo.create(session, {
        "profile_name": "FB Commenter",
        "geelark_phone_id": "ph-fb",
        "group_id": gid,
        "tiktok_logged_in": 1,
        "facebook_logged_in": 1,
        "warmed_up": 1,
    })

    srepo.update(session, {"daily_comment_limit_facebook": "2"})
    _seed_completed_runs(
        session, gid=gid, aid=commenter, count=2, platform="facebook",
    )

    result = engagement_service.create_comment_batch(
        session,
        platform="facebook",
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.facebook.com/share/p/xyz999www/"],
        target_publication_ids=[],
        comment_mode="provided",
        provided_comments=["nice post"],
        keywords=["seed"],
    )
    assert result["created_runs"] == 1


# ----- worker-side daily-limit handling --------------------------------------


def test_submit_run_over_daily_limit_marks_queued_and_raises(session):
    """When the account has hit the daily quota at submit time, the run
    should be mark_queued (not SKIPPED) and DailyLimitReached raised so
    the worker can reschedule to the next UTC day."""
    from app.repositories import engagement as eng_repo
    from app.repositories import settings as srepo
    from app.services import engagement_service

    gid, owner, commenter, _ = _group_with_accounts(session)
    srepo.update(session, {"daily_comment_limit_tiktok": "2"})

    # Create the batch first (PENDING run), then exhaust quota separately so
    # the worker submit picks up the over-limit condition.
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]

    # Exhaust quota with two SUCCESS runs from earlier batches.
    _seed_completed_runs(session, gid=gid, aid=commenter, count=2)

    class FakeClient:
        def create_tiktok_ai_comment_task(self, *args, **kwargs):
            raise AssertionError("must not reach GeeLark when over limit")

    with pytest.raises(engagement_service.DailyLimitReached):
        engagement_service.submit_run_to_geelark(
            session, FakeClient(), run_id=run_id,
        )

    row = eng_repo.get_run(session, run_id)
    assert row["result"] == "QUEUED"


def test_submit_run_at_limit_minus_one_proceeds_normally(session):
    """Quota is checked strictly less-than; one slot left → run dispatched."""
    from app.repositories import engagement as eng_repo
    from app.repositories import settings as srepo
    from app.services import engagement_service

    gid, owner, commenter, _ = _group_with_accounts(session)
    srepo.update(session, {"daily_comment_limit_tiktok": "3"})

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]
    # Two started attempts have used 2/3 slots; queued backlog does not count.
    _seed_completed_runs(session, gid=gid, aid=commenter, count=2)

    class FakeClient:
        def create_tiktok_ai_comment_task(self, *args, **kwargs):
            return "gee-ok"

    task_id = engagement_service.submit_run_to_geelark(
        session, FakeClient(), run_id=run_id,
    )
    assert task_id == "gee-ok"
    assert eng_repo.get_run(session, run_id)["result"] == "INITIATED"


def test_submit_run_over_daily_limit_wins_over_predecessor_inflight(session):
    """Daily-limit must be evaluated BEFORE predecessor-inflight, otherwise
    over-quota runs raise PhoneBusy (consuming MAX_SUBMIT_RETRIES) instead
    of DailyLimitReached (which reschedules cleanly past midnight).

    Regression: 2026-06-17 lost 670 revived runs after fixing the stagger
    because predecessor-inflight (QUEUED head from prior daily-limit
    reschedule) burned through retries on every subsequent same-phone
    PENDING."""
    from app.repositories import engagement as eng_repo
    from app.repositories import settings as srepo
    from app.services import engagement_service

    gid, owner, commenter, _ = _group_with_accounts(session)
    srepo.update(session, {"daily_comment_limit_tiktok": "2"})

    # Two prior SUCCESS runs exhaust the daily quota.
    _seed_completed_runs(session, gid=gid, aid=commenter, count=2)

    # Two same-account PENDING runs — head is QUEUED (predecessor inflight),
    # second is the one we'll submit. Seed directly via the repo since
    # create_comment_batch round-robins one run per target per account.
    tid = eng_repo.create_task(
        session, group_id=gid, action="tiktok_ai_comment",
        source="manual_links",
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[], comment_probability=100,
    )
    head_run_id = eng_repo.create_run(
        session, engagement_task_id=tid, account_id=commenter,
        target_link="https://www.tiktok.com/@owner/video/1234567890",
        action="tiktok_ai_comment",
    )
    second_run_id = eng_repo.create_run(
        session, engagement_task_id=tid, account_id=commenter,
        target_link="https://www.tiktok.com/@owner/video/1234567891",
        action="tiktok_ai_comment",
    )
    # Make the head act as a real predecessor (QUEUED counts as inflight).
    eng_repo.mark_queued(session, head_run_id)

    class FakeClient:
        def create_tiktok_ai_comment_task(self, *args, **kwargs):
            raise AssertionError("must not reach GeeLark when over limit")

    # Critical assertion: DailyLimitReached, NOT PhoneBusy. If PhoneBusy
    # leaks here, the worker would consume an attempts_remaining and
    # eventually mark FAILURE — which is exactly the 670-loss bug.
    with pytest.raises(engagement_service.DailyLimitReached):
        engagement_service.submit_run_to_geelark(
            session, FakeClient(), run_id=second_run_id,
        )

    assert eng_repo.get_run(session, second_run_id)["result"] == "QUEUED"


def test_submit_run_head_is_not_blocked_by_large_queued_backlog(session):
    """A batch larger than the daily limit must still let its head start.

    Regression: counting PENDING/QUEUED siblings as already-used quota made
    every run in a 50-run batch observe 49/20 and reschedule forever.
    """
    from app.repositories import engagement as eng_repo
    from app.repositories import settings as srepo
    from app.services import engagement_service

    gid, owner, commenter, _ = _group_with_accounts(session)
    srepo.update(session, {"daily_comment_limit_tiktok": "2"})

    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=[
            "https://www.tiktok.com/@owner/video/1234567890",
            "https://www.tiktok.com/@owner/video/1234567891",
            "https://www.tiktok.com/@owner/video/1234567892",
        ],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_ids = [
        eng_repo.list_runs_for_task(session, task_id)[0]["id"]
        for task_id in result["task_ids"]
    ]

    class FakeClient:
        def create_tiktok_ai_comment_task(self, *args, **kwargs):
            return "gee-head"

    task_id = engagement_service.submit_run_to_geelark(
        session, FakeClient(), run_id=run_ids[0],
    )

    assert task_id == "gee-head"
    assert eng_repo.get_run(session, run_ids[0])["result"] == "INITIATED"
    assert [eng_repo.get_run(session, rid)["result"] for rid in run_ids[1:]] == [
        "PENDING",
        "PENDING",
    ]


def test_submit_run_pauses_before_geelark_when_proxy_is_known_bad(session):
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service

    gid, owner, commenter, _ = _group_with_accounts(session)
    acc_repo.update(session, commenter, {"proxy_status": "fail"})
    result = engagement_service.create_comment_batch(
        session,
        group_id=gid,
        account_ids=[commenter],
        target_links=["https://www.tiktok.com/@owner/video/1234567890"],
        target_publication_ids=[],
        comment_probability=100,
    )
    run_id = eng_repo.list_runs_for_task(session, result["task_id"])[0]["id"]

    class NoNetwork:
        def create_tiktok_ai_comment_task(self, *args, **kwargs):
            raise AssertionError("known-bad proxy must block before GeeLark")

    with pytest.raises(engagement_service.ProxyUnavailable):
        engagement_service.submit_run_to_geelark(
            session, NoNetwork(), run_id=run_id,
        )

    assert eng_repo.get_run(session, run_id)["result"] == "QUEUED"
