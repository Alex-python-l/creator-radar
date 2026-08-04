"""username 防御性清洗：早期 web 单条录入 bug 会把整条主页 URL
（含 `?_r=...&_t=...` 分享参数）原样存进 influencers.username，
下游 `_profile_url` 再套一层前缀拼出 `tiktok.com/@<整条URL>` 双重 URL。"""
from app.handlers.scrape import _clean_username


def test_plain_username_unchanged():
    assert _clean_username("dhancathoven", "tiktok") == "dhancathoven"


def test_strips_at_prefix():
    assert _clean_username("@dhancathoven", "tiktok") == "dhancathoven"


def test_tiktok_full_url():
    assert (
        _clean_username("https://www.tiktok.com/@dhancathoven", "tiktok")
        == "dhancathoven"
    )


def test_tiktok_share_url_with_query():
    assert (
        _clean_username(
            "https://www.tiktok.com/@gelstudies?_r=1&_t=ZS-95v1sXmJWHk", "tiktok"
        )
        == "gelstudies"
    )


def test_instagram_full_url():
    assert (
        _clean_username("https://www.instagram.com/natgeo/", "instagram") == "natgeo"
    )


def test_instagram_url_with_query():
    assert (
        _clean_username("https://www.instagram.com/natgeo/?hl=en", "instagram")
        == "natgeo"
    )


def test_youtube_handle_url():
    assert (
        _clean_username("https://www.youtube.com/@mkbhd/shorts?si=abc", "youtube")
        == "mkbhd"
    )


def test_youtube_channel_c_and_user_urls():
    assert (
        _clean_username(
            "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw/shorts",
            "youtube",
        )
        == "channel/UC_x5XG1OV2P6uZZ5FSM9Ttw"
    )
    assert _clean_username("https://www.youtube.com/c/Veritasium", "youtube") == "c/Veritasium"
    assert _clean_username("https://www.youtube.com/user/Google", "youtube") == "user/Google"


def test_username_with_dot_and_underscore_preserved():
    assert _clean_username("khaby.lame_", "tiktok") == "khaby.lame_"


def test_empty_input():
    assert _clean_username("", "tiktok") == ""


# ---------- Facebook ----------

def test_facebook_strips_full_url():
    assert _clean_username("https://www.facebook.com/Cristiano", "facebook") == "Cristiano"
    assert _clean_username("https://facebook.com/Cristiano/", "facebook") == "Cristiano"
    assert _clean_username("https://www.facebook.com/Cristiano/reels/", "facebook") == "Cristiano"


def test_facebook_strips_query_string():
    assert _clean_username("https://www.facebook.com/Cristiano?ref=share", "facebook") == "Cristiano"


def test_facebook_plain_username_passthrough():
    assert _clean_username("Cristiano", "facebook") == "Cristiano"
    assert _clean_username("@Cristiano", "facebook") == "Cristiano"


def test_facebook_numeric_page_id():
    """没设 vanity URL 的 Page，FB URL 是 facebook.com/<id>；原样保留数字 id。"""
    assert _clean_username("https://www.facebook.com/100044113672304", "facebook") == "100044113672304"


# ---------- Facebook 非 vanity 形态 (profile.php / people/pfbid) ----------
# 用户实测发现:批量导入 26 个 FB URL 时,8 个 profile.php?id= + 4 个 people/.../pfbid
# 都被旧正则错认成 "profile.php" / "people",scraper 抓取必败。
# 新规则:这两种形态保留完整 identifier,scraper 端 _page_url 拼 ?sk=reels_tab。

def test_facebook_profile_php_with_id():
    """profile.php?id=<numeric> → 纯数字 id。"""
    assert (
        _clean_username("https://www.facebook.com/profile.php?id=61589956126863", "facebook")
        == "61589956126863"
    )


def test_facebook_profile_php_strips_extra_query():
    """额外 query 被剥离,只留纯数字 id。"""
    assert (
        _clean_username(
            "https://www.facebook.com/profile.php?id=61589956126863&ref=share", "facebook"
        )
        == "61589956126863"
    )


def test_facebook_people_pfbid_path():
    """people/<name>/pfbid<token>/ 完整保留为 'people/<name>/<pfbid>'。"""
    pfbid = "pfbid02pb5DGo8aGp71dmeQmv85sP4GotPPM9FzbfvcayHUfXHsGLQ6jcqKBiEy1gBJQUKHl"
    assert (
        _clean_username(
            f"https://www.facebook.com/people/Xshushx-Studied/{pfbid}/", "facebook"
        )
        == f"people/Xshushx-Studied/{pfbid}"
    )


def test_facebook_people_numeric_path():
    """people/<name>/<numeric>:numeric token 即 profile id,返回纯数字。"""
    assert (
        _clean_username(
            "https://www.facebook.com/people/Andra-Alexa/61590139985976/", "facebook"
        )
        == "61590139985976"
    )


def test_facebook_normalized_profile_php_passthrough():
    """已归一化的 'profile.php?id=<id>' → 纯数字。"""
    assert _clean_username("profile.php?id=12345", "facebook") == "12345"


def test_facebook_story_php_with_id():
    """share/r 短链 final URL: story.php?...&id=<profile_id> → 纯数字。"""
    assert (
        _clean_username(
            "https://www.facebook.com/story.php?"
            "story_fbid=122102908941341636&id=61590249099138&rdid=tMH9QwEIbPwtwLBr",
            "facebook",
        )
        == "61590249099138"
    )


def test_facebook_pure_numeric_id_passthrough():
    """已是纯数字 id → 原样返回。"""
    assert _clean_username("61590296374856", "facebook") == "61590296374856"


def test_facebook_normalized_people_path_passthrough():
    """已归一化的纯 identifier 'people/<name>/<token>' 原样返回。"""
    assert (
        _clean_username("people/Cha-Cha/pfbid0sFiUnz", "facebook")
        == "people/Cha-Cha/pfbid0sFiUnz"
    )


def test_facebook_vanity_with_trailing_question_mark():
    """用户原始数据 'chan.rinkoyamato?' (尾随 ? 没 query):提取 vanity。"""
    assert (
        _clean_username("https://www.facebook.com/chan.rinkoyamato?", "facebook")
        == "chan.rinkoyamato"
    )
