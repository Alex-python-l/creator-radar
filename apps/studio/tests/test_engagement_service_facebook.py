"""Tests for Facebook-aware helpers in engagement_service.

Covers:
- normalize_target_link(platform="facebook") accept/reject
- _sanitize_keywords strip/dedupe/empty/length/count limits
- _sanitize_fb_candidates strip/length/count limits
- _eligible_account per-platform behaviour (tiktok vs facebook)
"""

import pytest


# ---------------------------------------------------------------------------
# normalize_target_link — Facebook variants
# ---------------------------------------------------------------------------


def test_normalize_fb_post_url_accepts_profile_posts():
    from app.services import engagement_service

    link = "https://www.facebook.com/john.doe/posts/1234567890"
    assert engagement_service.normalize_target_link(
        link, platform="facebook"
    ) == link


def test_normalize_fb_post_url_accepts_permalink():
    from app.services import engagement_service

    link = "https://www.facebook.com/permalink.php?story_fbid=12345&id=67890"
    assert engagement_service.normalize_target_link(
        link, platform="facebook"
    ) == link


def test_normalize_fb_post_url_accepts_share_p():
    from app.services import engagement_service

    link = "https://www.facebook.com/share/p/AbCdEfGhIj/"
    assert engagement_service.normalize_target_link(
        link, platform="facebook"
    ) == link


def test_normalize_fb_post_url_accepts_reel():
    from app.services import engagement_service

    link = "https://www.facebook.com/reel/1234567890123456"
    assert engagement_service.normalize_target_link(
        link, platform="facebook"
    ) == link


def test_normalize_fb_post_url_accepts_groups_post():
    from app.services import engagement_service

    link = "https://www.facebook.com/groups/mygroup/posts/1234567890/"
    assert engagement_service.normalize_target_link(
        link, platform="facebook"
    ) == link


def test_normalize_fb_short_url_accepts_fb_watch():
    from app.services import engagement_service

    link = "https://fb.watch/aBcDeF123_/"
    assert engagement_service.normalize_target_link(
        link, platform="facebook"
    ) == link


def test_normalize_fb_url_rejects_tiktok_link():
    from app.services import engagement_service

    with pytest.raises(ValueError, match="Facebook"):
        engagement_service.normalize_target_link(
            "https://www.tiktok.com/@user/video/12345",
            platform="facebook",
        )


def test_normalize_fb_url_rejects_non_facebook_host():
    from app.services import engagement_service

    with pytest.raises(ValueError, match="Facebook"):
        engagement_service.normalize_target_link(
            "https://example.com/post/12345",
            platform="facebook",
        )


def test_normalize_fb_url_rejects_overlong_url():
    from app.services import engagement_service

    long_tail = "a" * 200
    link = f"https://www.facebook.com/john/posts/123/?ref={long_tail}"
    assert len(link) > engagement_service.FB_POST_ADDRESS_MAX_LEN
    with pytest.raises(ValueError, match="exceeds"):
        engagement_service.normalize_target_link(link, platform="facebook")


def test_normalize_target_link_tiktok_still_works_default_platform():
    """Calling without platform kwarg defaults to tiktok (backward compat)."""
    from app.services import engagement_service

    link = "https://www.tiktok.com/@user/video/1234567890"
    assert engagement_service.normalize_target_link(link) == link


def test_normalize_target_link_unsupported_platform_raises():
    from app.services import engagement_service

    with pytest.raises(ValueError, match="unsupported platform"):
        engagement_service.normalize_target_link(
            "https://example.com/", platform="instagram"
        )


# ---------------------------------------------------------------------------
# _sanitize_keywords
# ---------------------------------------------------------------------------


def test_sanitize_keywords_strips_and_dedupes_preserving_order():
    from app.services import engagement_service

    result = engagement_service._sanitize_keywords(
        ["  hello  ", "world", "hello", "  world  "]
    )
    assert result == ["hello", "world"]


def test_sanitize_keywords_rejects_empty_list():
    from app.services import engagement_service

    with pytest.raises(ValueError, match="at least one"):
        engagement_service._sanitize_keywords([])


def test_sanitize_keywords_rejects_none():
    from app.services import engagement_service

    with pytest.raises(ValueError, match="at least one"):
        engagement_service._sanitize_keywords(None)


def test_sanitize_keywords_rejects_all_blank_entries():
    from app.services import engagement_service

    with pytest.raises(ValueError, match="non-empty"):
        engagement_service._sanitize_keywords(["   ", "\t", ""])


def test_sanitize_keywords_rejects_entry_over_max_len():
    from app.services import engagement_service

    over = "x" * (engagement_service.FB_KEYWORD_MAX_LEN + 1)
    with pytest.raises(ValueError, match="exceeds"):
        engagement_service._sanitize_keywords(["ok", over])


def test_sanitize_keywords_rejects_over_max_count():
    from app.services import engagement_service

    too_many = [f"kw{i}" for i in range(engagement_service.FB_KEYWORD_MAX_COUNT + 1)]
    with pytest.raises(ValueError, match="at most"):
        engagement_service._sanitize_keywords(too_many)


def test_sanitize_keywords_rejects_non_string_entry():
    from app.services import engagement_service

    with pytest.raises(ValueError, match="strings"):
        engagement_service._sanitize_keywords(["ok", 123])


def test_sanitize_keywords_accepts_exact_max_count():
    from app.services import engagement_service

    exactly = [f"kw{i}" for i in range(engagement_service.FB_KEYWORD_MAX_COUNT)]
    result = engagement_service._sanitize_keywords(exactly)
    assert len(result) == engagement_service.FB_KEYWORD_MAX_COUNT


# ---------------------------------------------------------------------------
# _sanitize_fb_candidates
# ---------------------------------------------------------------------------


def test_sanitize_fb_candidates_strips_each_entry():
    from app.services import engagement_service

    result = engagement_service._sanitize_fb_candidates(
        ["  one  ", "two", "  three"]
    )
    assert result == ["one", "two", "three"]


def test_sanitize_fb_candidates_rejects_empty_list():
    from app.services import engagement_service

    with pytest.raises(ValueError, match="at least one"):
        engagement_service._sanitize_fb_candidates([])


def test_sanitize_fb_candidates_rejects_none():
    from app.services import engagement_service

    with pytest.raises(ValueError, match="at least one"):
        engagement_service._sanitize_fb_candidates(None)


def test_sanitize_fb_candidates_rejects_all_blank():
    from app.services import engagement_service

    with pytest.raises(ValueError, match="non-empty"):
        engagement_service._sanitize_fb_candidates(["", " ", "\t"])


def test_sanitize_fb_candidates_rejects_entry_over_max_len():
    from app.services import engagement_service

    over = "x" * (engagement_service.FB_COMMENT_MAX_LEN + 1)
    with pytest.raises(ValueError, match="exceeds"):
        engagement_service._sanitize_fb_candidates(["ok", over])


def test_sanitize_fb_candidates_rejects_over_max_count():
    from app.services import engagement_service

    too_many = ["text"] * (engagement_service.FB_COMMENT_MAX_COUNT + 1)
    with pytest.raises(ValueError, match="at most"):
        engagement_service._sanitize_fb_candidates(too_many)


def test_sanitize_fb_candidates_rejects_non_string_entry():
    from app.services import engagement_service

    with pytest.raises(ValueError, match="strings"):
        engagement_service._sanitize_fb_candidates(["ok", 42])


# ---------------------------------------------------------------------------
# _eligible_account — per-platform behaviour
# ---------------------------------------------------------------------------


def test_eligible_account_tiktok_default_platform_still_works():
    from app.services import engagement_service

    account = {
        "geelark_phone_id": "ph-1",
        "tiktok_logged_in": 1,
        "facebook_logged_in": 0,
        "warmed_up": 1,
    }
    assert engagement_service._eligible_account(account) is True


def test_eligible_account_tiktok_explicit_kwarg():
    from app.services import engagement_service

    account = {
        "geelark_phone_id": "ph-1",
        "tiktok_logged_in": 1,
        "facebook_logged_in": 0,
        "warmed_up": 1,
    }
    assert engagement_service._eligible_account(
        account, platform="tiktok"
    ) is True


def test_eligible_account_facebook_requires_facebook_logged_in():
    from app.services import engagement_service

    fb_logged_in = {
        "geelark_phone_id": "ph-1",
        "tiktok_logged_in": 0,
        "facebook_logged_in": 1,
        "warmed_up": 1,
    }
    assert engagement_service._eligible_account(
        fb_logged_in, platform="facebook"
    ) is True


def test_eligible_account_facebook_rejects_tiktok_only_account():
    from app.services import engagement_service

    tt_only = {
        "geelark_phone_id": "ph-1",
        "tiktok_logged_in": 1,
        "facebook_logged_in": 0,
        "warmed_up": 1,
    }
    assert engagement_service._eligible_account(
        tt_only, platform="facebook"
    ) is False


def test_eligible_account_facebook_requires_warmup():
    from app.services import engagement_service

    not_warm = {
        "geelark_phone_id": "ph-1",
        "facebook_logged_in": 1,
        "warmed_up": 0,
    }
    assert engagement_service._eligible_account(
        not_warm, platform="facebook"
    ) is False


def test_eligible_account_facebook_requires_phone_id():
    from app.services import engagement_service

    no_phone = {
        "geelark_phone_id": "",
        "facebook_logged_in": 1,
        "warmed_up": 1,
    }
    assert engagement_service._eligible_account(
        no_phone, platform="facebook"
    ) is False


def test_eligible_account_none_returns_false_for_any_platform():
    from app.services import engagement_service

    assert engagement_service._eligible_account(None) is False
    assert engagement_service._eligible_account(None, platform="facebook") is False


# ---------------------------------------------------------------------------
# create_comment_batch — Facebook branch
# ---------------------------------------------------------------------------


def _make_fb_eligible_account(session, *, profile, phone_id, group_id):
    from app.repositories import accounts as acc_repo

    return acc_repo.create(session, {
        "profile_name": profile,
        "facebook_logged_in": 1, "tiktok_logged_in": 0,
        "warmed_up": 1, "geelark_phone_id": phone_id,
        "group_id": group_id,
    })


def test_create_comment_batch_facebook_writes_platform_and_keywords(
    default_group_id,
):
    from sqlmodel import Session
    from app.db import engine
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service as svc

    with Session(engine) as s:
        a = _make_fb_eligible_account(
            s, profile="fb-a", phone_id="ph-fb-a", group_id=default_group_id,
        )
        result = svc.create_comment_batch(
            s,
            platform="facebook",
            account_ids=[a],
            target_links=["https://www.facebook.com/zuck/posts/abc"],
            comments_per_video=2,
            comment_mode=svc.COMMENT_MODE_PROVIDED,
            provided_comments=["Nice!", "Great!"],
            keywords=["english", "tip"],
        )
        task = eng_repo.get_task(s, result["task_id"])
    assert task["platform"] == "facebook"
    assert task["action"] == svc.ACTION_FACEBOOK_AUTO_COMMENT
    assert task["keywords"] == ["english", "tip"]
    assert task["comments_per_video"] == 2


def test_create_comment_batch_facebook_rejects_geelark_ai_mode(
    default_group_id,
):
    from sqlmodel import Session
    from app.db import engine
    from app.services import engagement_service as svc

    with Session(engine) as s:
        a = _make_fb_eligible_account(
            s, profile="fb-b", phone_id="ph-fb-b", group_id=default_group_id,
        )
        with pytest.raises(ValueError, match="comment_mode"):
            svc.create_comment_batch(
                s,
                platform="facebook",
                account_ids=[a],
                target_links=["https://www.facebook.com/zuck/posts/abc"],
                keywords=["k1"],
                comment_mode=svc.COMMENT_MODE_GEELARK_AI,
            )


def test_create_comment_batch_facebook_requires_keywords(default_group_id):
    from sqlmodel import Session
    from app.db import engine
    from app.services import engagement_service as svc

    with Session(engine) as s:
        a = _make_fb_eligible_account(
            s, profile="fb-c", phone_id="ph-fb-c", group_id=default_group_id,
        )
        with pytest.raises(ValueError, match="keywords"):
            svc.create_comment_batch(
                s,
                platform="facebook",
                account_ids=[a],
                target_links=["https://www.facebook.com/zuck/posts/abc"],
                comment_mode=svc.COMMENT_MODE_PROVIDED,
                provided_comments=["Nice"],
                keywords=None,
            )


def test_create_comment_batch_tiktok_rejects_nonempty_keywords(
    default_group_id,
):
    """防 UI 误传：TikTok 路径若收到非空 keywords 抛 ValueError。"""
    from sqlmodel import Session
    from app.db import engine
    from app.repositories import accounts as acc_repo
    from app.services import engagement_service as svc

    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "tk-z",
            "tiktok_logged_in": 1, "facebook_logged_in": 0,
            "warmed_up": 1, "geelark_phone_id": "ph-z",
            "group_id": default_group_id,
        })
        with pytest.raises(ValueError, match="keywords"):
            svc.create_comment_batch(
                s,
                platform="tiktok",
                account_ids=[a],
                target_links=["https://www.tiktok.com/@x/video/1234567890"],
                keywords=["bad"],
            )


def test_create_comment_batch_facebook_rejects_invalid_url(
    default_group_id,
):
    from sqlmodel import Session
    from app.db import engine
    from app.services import engagement_service as svc

    with Session(engine) as s:
        a = _make_fb_eligible_account(
            s, profile="fb-d", phone_id="ph-fb-d", group_id=default_group_id,
        )
        with pytest.raises(ValueError, match="Facebook"):
            svc.create_comment_batch(
                s,
                platform="facebook",
                account_ids=[a],
                target_links=["https://example.com/not-fb"],
                comment_mode=svc.COMMENT_MODE_PROVIDED,
                provided_comments=["Nice"],
                keywords=["k1"],
            )


def test_create_comment_batch_facebook_rejects_non_eligible_account(
    default_group_id,
):
    """账号 tiktok_logged_in=1 但 facebook_logged_in=0，FB 路径应拒。"""
    from sqlmodel import Session
    from app.db import engine
    from app.repositories import accounts as acc_repo
    from app.services import engagement_service as svc

    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "tk-only",
            "tiktok_logged_in": 1, "facebook_logged_in": 0,
            "warmed_up": 1, "geelark_phone_id": "ph-x",
            "group_id": default_group_id,
        })
        with pytest.raises(ValueError, match="Facebook"):
            svc.create_comment_batch(
                s,
                platform="facebook",
                account_ids=[a],
                target_links=["https://www.facebook.com/zuck/posts/abc"],
                keywords=["k"],
                comment_mode=svc.COMMENT_MODE_PROVIDED,
                provided_comments=["Nice"],
            )


# ---------------------------------------------------------------------------
# submit_run_to_geelark — FB dispatch
# ---------------------------------------------------------------------------


def test_submit_run_facebook_calls_geelark_with_candidates_and_keywords(
    default_group_id, monkeypatch,
):
    from sqlmodel import Session
    from app.db import engine
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service as svc

    # Stabilize provided-pool ordering so the assertion is deterministic;
    # _allocate_provided_pool shuffles by default.
    monkeypatch.setattr(svc.random, "shuffle", lambda lst: None)

    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "fb-submit",
            "facebook_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-submit",
            "group_id": default_group_id,
        })
        result = svc.create_comment_batch(
            s,
            platform="facebook",
            account_ids=[a],
            target_links=["https://www.facebook.com/zuck/posts/abc"],
            comments_per_video=3,
            comment_mode=svc.COMMENT_MODE_PROVIDED,
            provided_comments=["Nice!", "Great!", "Cool!"],
            keywords=["english"],
        )
        run_id = result["run_ids"][0]

    captured = {}

    class FakeClient:
        def create_facebook_auto_comment_task(
            self, env_id, *, post_address, comments, keywords,
            schedule_at=None, name=None, remark=None,
        ):
            captured.update(
                env_id=env_id, post_address=post_address,
                comments=comments, keywords=keywords,
                schedule_at=schedule_at, name=name, remark=remark,
            )
            return "fb-task-1"

    with Session(engine) as s:
        task_id = svc.submit_run_to_geelark(s, FakeClient(), run_id=run_id)
        run = eng_repo.get_run(s, run_id)

    assert task_id == "fb-task-1"
    assert captured["post_address"] == "https://www.facebook.com/zuck/posts/abc"
    assert captured["comments"] == ["Nice!", "Great!", "Cool!"]
    assert captured["keywords"] == ["english"]
    assert run["result"] == "INITIATED"
    assert run["geelark_task_id"] == "fb-task-1"
    assert run["comment_candidates"] == ["Nice!", "Great!", "Cool!"]
    assert run["comment_text"] == "Nice!"


def test_fb_run_candidates_studio_ai_does_not_call_tiktok_metadata(
    monkeypatch, default_group_id,
):
    """FB studio_ai must NOT call tiktok_metadata.fetch_tiktok_metadata
    (the URL is FB, not TikTok — calling it issues bogus HTTP requests
    that time out for 24s)."""
    from sqlmodel import Session
    from app.db import engine
    from app.repositories import accounts as acc_repo
    from app.repositories import comment_prompts as cp_repo
    from app.services import engagement_service as svc
    from app.services import tiktok_metadata

    called = {"count": 0}

    def boom(*args, **kwargs):
        called["count"] += 1
        raise AssertionError("tiktok_metadata.fetch_tiktok_metadata "
                             "must not be called on FB studio_ai path")

    monkeypatch.setattr(tiktok_metadata, "fetch_tiktok_metadata", boom)

    captured = {"prompt_bodies": []}

    def fake_generate(*, prompt_body, target_title, thinking_budget=0,
                     model=None, max_len=None):
        captured["prompt_bodies"].append(prompt_body)
        captured.setdefault("target_titles", []).append(target_title)
        return f"AI candidate #{len(captured['prompt_bodies'])}"

    from app.services import engagement_service as _svc_mod
    monkeypatch.setattr(
        _svc_mod.comment_generation, "generate_comment_for_target",
        fake_generate,
    )

    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "fb-meta-test",
            "facebook_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-meta",
            "group_id": default_group_id,
        })
        prompt_id = cp_repo.create(s, name="fb-prompt-test",
                                   body="Compose a short FB comment")
        result = svc.create_comment_batch(
            s,
            platform="facebook",
            account_ids=[a],
            target_links=["https://www.facebook.com/zuck/posts/abc"],
            comments_per_video=2,
            comment_mode=svc.COMMENT_MODE_STUDIO_AI,
            comment_prompt_id=prompt_id,
            keywords=["english"],
        )
        run_id = result["run_ids"][0]
        # Call _fb_run_candidates directly to isolate the metadata path
        from app.repositories import engagement as eng_repo
        run = eng_repo.get_run(s, run_id)
        parent = eng_repo.get_task(s, run["engagement_task_id"])
        out = svc._fb_run_candidates(s, run, parent)

    assert called["count"] == 0  # not called at all
    assert len(out) == 2
    assert all(t is None for t in captured["target_titles"])  # FB has no title


def test_submit_run_unsupported_platform_marks_failure_and_finalizes(
    monkeypatch, default_group_id,
):
    """Defensive branch: an engagement task whose platform doesn't
    support GeeLark engagement (YouTube/Instagram in the current
    registry) must mark the run FAILURE and finalize the session, not
    leave it PENDING/INITIATED forever."""
    from sqlmodel import Session
    from app.db import engine
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service as svc

    monkeypatch.setattr(svc.random, "shuffle", lambda lst: None)

    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "unsupported-engagement",
            "facebook_logged_in": 1, "youtube_logged_in": 1,
            "warmed_up": 1,
            "geelark_phone_id": "ph-unsupported",
            "group_id": default_group_id,
        })
        result = svc.create_comment_batch(
            s,
            platform="facebook",
            account_ids=[a],
            target_links=["https://www.facebook.com/zuck/posts/abc"],
            comments_per_video=1,
            comment_mode=svc.COMMENT_MODE_PROVIDED,
            provided_comments=["Nice"],
            keywords=["english"],
        )
        run_id = result["run_ids"][0]
        # Force a platform whose adapter doesn't support engagement
        # (YouTube doesn't have a GeeLark comment action).
        task = eng_repo.get_task(s, eng_repo.get_run(s, run_id)["engagement_task_id"])
        from sqlalchemy import text as sa_text
        s.execute(sa_text(
            "UPDATE engagement_tasks SET platform='youtube' WHERE id=:tid"
        ), {"tid": task["id"]})
        s.commit()

    class _FakeClient:
        def stop_phones(self, ids):
            pass

    import pytest
    with pytest.raises(RuntimeError, match="unsupported engagement platform"):
        with Session(engine) as s:
            svc.submit_run_to_geelark(s, _FakeClient(), run_id=run_id)

    with Session(engine) as s:
        run = eng_repo.get_run(s, run_id)
    assert run["result"] == "FAILURE"
    assert "unsupported engagement platform" in (run["notes"] or "")


def test_create_comment_batch_facebook_one_run_per_phone_target(default_group_id):
    """FB: candidates_per_post is the pool size INSIDE one run, not a run
    multiplier. 1 phone × 1 post × candidates_per_post=3 → 1 run with 3
    candidates, NOT 3 runs."""
    from sqlmodel import Session
    from app.db import engine
    from app.repositories import accounts as acc_repo
    from app.repositories import engagement as eng_repo
    from app.services import engagement_service as svc

    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "fb-onerun",
            "facebook_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-onerun",
            "group_id": default_group_id,
        })
        result = svc.create_comment_batch(
            s,
            platform="facebook",
            account_ids=[a],
            target_links=["https://www.facebook.com/zuck/posts/abc"],
            comments_per_video=3,
            comment_mode=svc.COMMENT_MODE_PROVIDED,
            provided_comments=["c1", "c2", "c3"],
            keywords=["english"],
        )
        run_id = result["run_ids"][0]
        run = eng_repo.get_run(s, run_id)
    assert result["created_runs"] == 1
    assert len(run["comment_candidates"]) == 3


def test_create_comment_batch_tiktok_still_multiplies_runs(default_group_id):
    """TikTok: comments_per_video=N still creates N runs per phone-target.
    This regression test locks the existing TikTok behavior."""
    from sqlmodel import Session
    from app.db import engine
    from app.repositories import accounts as acc_repo
    from app.services import engagement_service as svc

    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "tk-multi",
            "tiktok_logged_in": 1, "warmed_up": 1,
            "geelark_phone_id": "ph-tk-multi",
            "group_id": default_group_id,
        })
        result = svc.create_comment_batch(
            s,
            platform="tiktok",
            account_ids=[a],
            target_links=["https://www.tiktok.com/@x/video/1234567890"],
            comments_per_video=3,
            comment_mode=svc.COMMENT_MODE_PROVIDED,
            provided_comments=["c1", "c2", "c3"],
            comment_probability=100,
        )
    assert result["created_runs"] == 3
