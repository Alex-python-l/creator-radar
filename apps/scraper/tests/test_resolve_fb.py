"""POST /resolve-fb-url:把 FB share 短链 / profile.php / pfbid URL
跟 302 后归一化成 normalized identifier。给 web 端 Server Action 在入库前调用。

Web 端 (Vercel) 没有 FB 代理基础设施 + Chrome 指纹 headers,share 短链 resolve
只能放 scraper 这边复用 _httpx_get + 代理池。
"""
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.handlers.resolve_fb import resolve_fb_identifier
from app.main import app


class _MockResp:
    def __init__(self, final_url: str, status_code: int = 200, text: str = ""):
        self.url = httpx.URL(final_url)
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore[arg-type]


# ---------- resolve_fb_identifier 纯函数(给 endpoint 调) ----------

@pytest.mark.asyncio
async def test_share_url_resolves_to_numeric_via_people_path():
    """share/<code> follow 落到 /people/<name>/<numeric>/ → 纯数字 id。"""
    final = "https://www.facebook.com/people/Andra-Alexa/61590139985976/?mibextid=xxx"

    async def fake_get(url, proxy):
        return _MockResp(final)

    with patch("app.handlers.resolve_fb._httpx_get", side_effect=fake_get):
        ident, final_url = await resolve_fb_identifier(
            "https://www.facebook.com/share/1CTxoxSKCJ/?mibextid=wwXIfr"
        )
    assert ident == "61590139985976"
    assert final_url == final


@pytest.mark.asyncio
async def test_share_reel_url_resolves_to_numeric_via_story_id():
    """share/r/<code> follow 落到 story.php?...&id=<profile_id> → 纯数字 id。"""
    final = (
        "https://www.facebook.com/story.php?"
        "story_fbid=122102908941341636&id=61590249099138&rdid=tMH9QwEIbPwtwLBr"
    )

    async def fake_get(url, proxy):
        return _MockResp(final)

    with patch("app.handlers.resolve_fb._httpx_get", side_effect=fake_get):
        ident, final_url = await resolve_fb_identifier(
            "https://www.facebook.com/share/r/193uzPf9ZQ/"
        )
    assert ident == "61590249099138"
    assert final_url == final


@pytest.mark.asyncio
async def test_share_url_resolves_to_vanity():
    """share 短链 follow 后落到 /<vanity> → identifier = vanity。"""
    async def fake_get(url, proxy):
        return _MockResp("https://www.facebook.com/Cristiano")

    with patch("app.handlers.resolve_fb._httpx_get", side_effect=fake_get):
        ident, _ = await resolve_fb_identifier(
            "https://www.facebook.com/share/abc/"
        )
    assert ident == "Cristiano"


@pytest.mark.asyncio
async def test_profile_php_returns_numeric_id():
    """profile.php?id= 无 HTML 抽取时,从 URL 收敛成纯数字 id。"""
    raw = "https://www.facebook.com/profile.php?id=61589956126863"

    async def fake_get(url, proxy):
        return _MockResp(raw)

    with patch("app.handlers.resolve_fb._httpx_get", side_effect=fake_get):
        ident, _ = await resolve_fb_identifier(raw)
    assert ident == "61589956126863"


@pytest.mark.asyncio
async def test_numeric_profile_path_is_resolved_through_profile_php():
    """facebook.com/<numeric> 会撞登录墙;resolver 应先改写成 profile.php?id=<numeric>。"""
    raw = "https://www.facebook.com/61590448588289"
    expected_lookup = "https://www.facebook.com/profile.php?id=61590448588289"
    html = '<meta property="al:android:url" content="fb://profile/122095612281348286" />'
    seen: list[str] = []

    async def fake_get(url, proxy):
        seen.append(url)
        return _MockResp(
            "https://www.facebook.com/people/Miguel-Garcia/pfbidNEW/",
            text=html,
        )

    with patch("app.handlers.resolve_fb._httpx_get", side_effect=fake_get):
        ident, _ = await resolve_fb_identifier(raw)

    assert seen == [expected_lookup]
    assert ident == "122095612281348286"


@pytest.mark.asyncio
async def test_pfbid_url_normalizes_to_people_path():
    """people/<n>/pfbid URL 即便 FB 把 pfbid 换成新 token,identifier 提取仍正确。"""
    pfbid_new = "pfbid0NEW123abcXYZ"

    async def fake_get(url, proxy):
        return _MockResp(f"https://www.facebook.com/people/Xshushx-Studied/{pfbid_new}/")

    with patch("app.handlers.resolve_fb._httpx_get", side_effect=fake_get):
        ident, _ = await resolve_fb_identifier(
            "https://www.facebook.com/people/Xshushx-Studied/pfbid02OLD.../"
        )
    assert ident == f"people/Xshushx-Studied/{pfbid_new}"


@pytest.mark.asyncio
async def test_pfbid_url_prefers_stable_profile_id_from_html():
    """people/pfbid 页面 HTML 带 fb://profile/<id> → 返回纯数字 id。"""
    html = '<meta property="al:ios:url" content="fb://profile/61590123456789" />'

    async def fake_get(url, proxy):
        return _MockResp(
            "https://www.facebook.com/people/Jeannie-Study/pfbidNEW/",
            text=html,
        )

    with patch("app.handlers.resolve_fb._httpx_get", side_effect=fake_get):
        ident, _ = await resolve_fb_identifier(
            "https://www.facebook.com/people/Jeannie-Study/pfbidOLD/"
        )
    assert ident == "61590123456789"


@pytest.mark.asyncio
async def test_html_userid_fallback_when_no_deeplink():
    """HTML 无 fb://profile 但有 "userID":"<id>" → 用 userID 次选。"""
    html = '{"shouldUseFXIMProfilePicEditor":false,"userID":"100057571594903"}'

    async def fake_get(url, proxy):
        return _MockResp("https://www.facebook.com/MrBeast6000", text=html)

    with patch("app.handlers.resolve_fb._httpx_get", side_effect=fake_get):
        ident, _ = await resolve_fb_identifier("https://www.facebook.com/MrBeast6000")
    assert ident == "100057571594903"


@pytest.mark.asyncio
async def test_cover_photo_id_not_mis_extracted():
    """cover_photo.photo.id 在前,正确 fb://profile id 在后 → 抽到正确的,不被封面照片 id 污染。"""
    html = (
        '"cover_photo":{"photo":{"id":"322648385888375"}}'
        '<meta property="al:android:url" content="fb://profile/100044296486382" />'
    )

    async def fake_get(url, proxy):
        return _MockResp("https://www.facebook.com/Cristiano", text=html)

    with patch("app.handlers.resolve_fb._httpx_get", side_effect=fake_get):
        ident, _ = await resolve_fb_identifier("https://www.facebook.com/share/abc/")
    assert ident == "100044296486382"


@pytest.mark.asyncio
async def test_all_proxies_fail_raises():
    """全代理都炸 → 抛 RuntimeError 让 endpoint 转 502。"""
    async def fake_get(url, proxy):
        raise httpx.ConnectError("simulated")

    with (
        patch("app.handlers.resolve_fb._httpx_get", side_effect=fake_get),
        pytest.raises(RuntimeError),
    ):
        await resolve_fb_identifier("https://www.facebook.com/share/x/")


@pytest.mark.asyncio
async def test_final_url_with_no_extractable_identifier_raises():
    """final URL 不是任何 FB identifier 形态(比如登录墙落到 /login)→ 抛。"""
    async def fake_get(url, proxy):
        return _MockResp("https://www.facebook.com/login/?next=...")

    with (
        patch("app.handlers.resolve_fb._httpx_get", side_effect=fake_get),
        pytest.raises(RuntimeError),
    ):
        await resolve_fb_identifier("https://www.facebook.com/share/x/")


# ---------- POST /resolve-fb-url endpoint ----------

@pytest.fixture
def client():
    return TestClient(app)


def test_endpoint_returns_normalized_identifier(client):
    async def fake_resolve(url):
        return ("100044296486382", "https://www.facebook.com/Cristiano")

    with patch("app.main.resolve_fb_identifier", side_effect=fake_resolve):
        r = client.post("/resolve-fb-url", json={"url": "https://www.facebook.com/share/abc/"})
    assert r.status_code == 200
    assert r.json() == {
        "identifier": "100044296486382",
        "finalUrl": "https://www.facebook.com/Cristiano",
    }


def test_endpoint_502_on_resolve_failure(client):
    async def fake_resolve(url):
        raise RuntimeError("all proxies failed: simulated")

    with patch("app.main.resolve_fb_identifier", side_effect=fake_resolve):
        r = client.post("/resolve-fb-url", json={"url": "https://www.facebook.com/share/abc/"})
    assert r.status_code == 502
    assert "simulated" in r.json()["detail"]


def test_endpoint_400_on_non_fb_url(client):
    """非 facebook.com URL 直接拒,不浪费代理。"""
    r = client.post("/resolve-fb-url", json={"url": "https://example.com/foo"})
    assert r.status_code == 400


def test_endpoint_400_on_empty_url(client):
    r = client.post("/resolve-fb-url", json={"url": ""})
    assert r.status_code in (400, 422)  # 422 = pydantic min_length
