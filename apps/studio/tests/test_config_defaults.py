from app.config import SETTINGS_DEFAULTS


def test_tiktok_discover_defaults_present():
    raw = SETTINGS_DEFAULTS["tiktok_discover_keywords"]
    kws = [k.strip() for k in raw.split(",") if k.strip()]
    # Recall was widened from the original 5 to 45 IELTS / English-study terms
    # so the daily BD batch produces ~1000+ candidate videos.
    assert len(kws) == 45
    assert kws[0] == "ielts"
    # Spot-check that the seed bucket and the broader study/exam terms are in.
    for expected in (
        "ielts", "ieltsspeaking", "ieltswriting", "ieltsreading",
        "ieltslistening", "ieltspreparation", "ieltstips", "studyabroad",
        "toefl", "studyenglish", "learnenglish",
    ):
        assert expected in kws, expected
    assert SETTINGS_DEFAULTS["tiktok_discover_min_views"] == "100"
    assert SETTINGS_DEFAULTS["tiktok_discover_window_days"] == "3650"
    assert SETTINGS_DEFAULTS["tiktok_discover_retain_days"] == "90"
    assert SETTINGS_DEFAULTS["tiktok_discover_max_per_keyword"] == "500"


def test_daily_comment_limit_defaults_present():
    assert SETTINGS_DEFAULTS["daily_comment_limit_tiktok"] == "20"
    assert SETTINGS_DEFAULTS["daily_comment_limit_facebook"] == "20"
