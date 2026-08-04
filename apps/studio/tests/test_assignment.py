import time
from app.services.assignment import build_pool, assign


def _acc(name, warmed=True, account_id=1, **kw):
    base = {
        "id": account_id,
        "profile_name": name,
        "warmed_up": 1 if warmed else 0,
        "tiktok_logged_in": kw.pop("tiktok_logged_in", 1),
        "last_posted_at": kw.pop("last_posted_at", None),
        "youtube_logged_in": kw.pop("youtube_logged_in", 0),
        "last_posted_at_youtube": kw.pop("last_posted_at_youtube", None),
        "instagram_logged_in": kw.pop("instagram_logged_in", 0),
        "last_posted_at_instagram": kw.pop("last_posted_at_instagram", None),
        "facebook_logged_in": kw.pop("facebook_logged_in", 0),
        "last_posted_at_facebook": kw.pop("last_posted_at_facebook", None),
    }
    base.update(kw)
    return base


def test_pool_excludes_unwarmed_and_sorts_oldest_first():
    accounts = [
        _acc("new", account_id=1),
        _acc("old", account_id=2, last_posted_at=1000),
        _acc("recent", account_id=3, last_posted_at=9_000_000_000),
        _acc("cold", account_id=4, warmed=False),
    ]
    pool = build_pool(accounts)
    assert [p["profile_name"] for p in pool] == ["new", "old", "recent"]


def test_pool_requires_platform_logged_in():
    accounts = [
        _acc("tt-only", account_id=1),
        _acc("yt-yes", account_id=2, youtube_logged_in=1),
        _acc("ig-yes", account_id=3, instagram_logged_in=1),
        _acc("fb-yes", account_id=4, facebook_logged_in=1),
    ]
    assert [p["profile_name"] for p in build_pool(accounts, "tiktok")] == \
        ["tt-only", "yt-yes", "ig-yes", "fb-yes"]
    assert [p["profile_name"] for p in build_pool(accounts, "youtube")] == ["yt-yes"]
    assert [p["profile_name"] for p in build_pool(accounts, "instagram")] == ["ig-yes"]
    assert [p["profile_name"] for p in build_pool(accounts, "facebook")] == ["fb-yes"]


def test_assign_respects_cooldown_per_platform():
    now = time.time()
    accounts = [
        _acc("split", account_id=1, last_posted_at=now - 600,
             youtube_logged_in=1, last_posted_at_youtube=None),
        _acc("yt-hot", account_id=2, youtube_logged_in=1,
             last_posted_at_youtube=now - 600),
    ]
    yt_pool = build_pool(accounts, "youtube")
    out = assign(["v1"], yt_pool, cooldown_hours=2)
    assert out == [{"video": "v1", "profile_name": "split", "account_id": 1}]


def test_assign_respects_cooldown_and_one_per_run():
    now = time.time()
    accounts = [
        _acc("free", account_id=1),
        _acc("hot",  account_id=2, last_posted_at=now - 600),
    ]
    out = assign(["v1", "v2"], build_pool(accounts), cooldown_hours=2)
    assert out == [{"video": "v1", "profile_name": "free", "account_id": 1}]
