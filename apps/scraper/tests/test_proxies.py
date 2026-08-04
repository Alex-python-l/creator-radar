import random

from app.scrapers.proxies import (
    effective_proxy_pool,
    instagram_proxy,
    normalize_proxy_url,
    pick_tiktok_proxy,
    pooled_proxy_candidates,
)


def test_normalize_none_and_empty():
    assert normalize_proxy_url(None) is None
    assert normalize_proxy_url("") is None
    assert normalize_proxy_url("   ") is None


def test_normalize_adds_http_scheme():
    assert (
        normalize_proxy_url("u:p@host.example:1234")
        == "http://u:p@host.example:1234"
    )
    assert normalize_proxy_url("  u:p@host.example:1234  ") == "http://u:p@host.example:1234"


def test_normalize_preserves_existing_scheme():
    for url in [
        "http://u:p@host:1",
        "https://u:p@host:1",
        "socks5://u:p@host:1",
        "socks5h://u:p@host:1",
    ]:
        assert normalize_proxy_url(url) == url


def test_instagram_prefers_dedicated_url():
    assert (
        instagram_proxy("u:p@ig:1", "u:p@old:1")
        == "http://u:p@ig:1"
    )


def test_instagram_falls_back_to_proxy_url():
    assert instagram_proxy(None, "u:p@old:1") == "http://u:p@old:1"
    assert instagram_proxy(None, None) is None


def test_instagram_falls_back_to_common_pool_first_entry():
    # 都没配专用 → 取 common pool 第一条（固定，不随机）
    pool = "u:p@a:1,u:p@b:2,u:p@c:3"
    assert instagram_proxy(None, None, pool) == "http://u:p@a:1"
    # 多次调用稳定取同一条（sticky）
    for _ in range(5):
        assert instagram_proxy(None, None, pool) == "http://u:p@a:1"


def test_instagram_dedicated_overrides_common_pool():
    # 配了专用 → 不看 common pool
    assert instagram_proxy("u:p@ig:1", None, "u:p@a:1,u:p@b:2") == "http://u:p@ig:1"
    assert instagram_proxy(None, "u:p@old:1", "u:p@a:1") == "http://u:p@old:1"


def test_instagram_empty_common_pool_returns_none():
    assert instagram_proxy(None, None, "") is None
    assert instagram_proxy(None, None, ",,  ,") is None


def test_tiktok_picks_from_pool():
    pool = "u:p@a:1,u:p@b:2,u:p@c:3"
    seen = set()
    random.seed(0)
    for _ in range(50):
        seen.add(pick_tiktok_proxy(pool, None))
    assert seen == {"http://u:p@a:1", "http://u:p@b:2", "http://u:p@c:3"}


def test_tiktok_falls_back_to_proxy_url_when_pool_empty():
    assert pick_tiktok_proxy(None, "u:p@old:1") == "http://u:p@old:1"
    assert pick_tiktok_proxy("", "u:p@old:1") == "http://u:p@old:1"
    assert pick_tiktok_proxy(None, None) is None


def test_tiktok_pool_ignores_empty_items():
    # 逗号尾随、连续逗号、空白条都丢弃，剩下的还能用
    assert pick_tiktok_proxy(",,u:p@x:1, ,", None) == "http://u:p@x:1"

def test_pooled_returns_up_to_limit():
    pool = "u:p@a:1,u:p@b:2,u:p@c:3,u:p@d:4,u:p@e:5"
    cand = pooled_proxy_candidates(pool, None, limit=3)
    assert len(cand) == 3
    # 全部 normalize 后带 http://
    assert all(c.startswith("http://") for c in cand)
    # 池里有 5 条,取的 3 条必须互不相同
    assert len(set(cand)) == 3


def test_pooled_returns_all_when_pool_smaller_than_limit():
    pool = "u:p@a:1,u:p@b:2"
    cand = pooled_proxy_candidates(pool, None, limit=4)
    assert len(cand) == 2  # 池子只有 2 条


def test_pooled_falls_back_to_proxy_url():
    cand = pooled_proxy_candidates(None, "u:p@old:1", limit=4)
    assert cand == ["http://u:p@old:1"]


def test_pooled_returns_single_none_when_both_empty():
    """池空 + proxy_url 也空 → 返回 [None],代表直连。"""
    cand = pooled_proxy_candidates(None, None, limit=4)
    assert cand == [None]


def test_effective_proxy_pool_prefers_platform_pool():
    assert effective_proxy_pool("u:p@fb:1", "u:p@common:2") == "u:p@fb:1"


def test_effective_proxy_pool_falls_back_to_common_pool():
    assert effective_proxy_pool(None, "u:p@common:2") == "u:p@common:2"
    assert effective_proxy_pool("", "u:p@common:2") == "u:p@common:2"


def test_effective_proxy_pool_empty_when_both_empty():
    assert effective_proxy_pool(None, None) is None
    assert effective_proxy_pool("", " ") is None
