"""SETTINGS_DEFAULTS includes the three caption-related keys."""
from app.config import SETTINGS_DEFAULTS


def test_caption_brief_is_not_in_defaults():
    """Removed when the prompt pool replaced the global caption_brief
    fallback — groups must now bind a prompt explicitly."""
    assert "caption_brief" not in SETTINGS_DEFAULTS


def test_caption_max_len_was_removed():
    """Removed when the per-generation max length was dropped — captions now
    always use the hard-coded CAPTION_MAX_LEN_DEFAULT (220)."""
    assert "caption_max_len" not in SETTINGS_DEFAULTS


def test_caption_high_quality_was_renamed():
    """The old caption-only switch is gone; ``gemini_high_quality`` is the
    global replacement (captions + comments)."""
    assert "caption_high_quality" not in SETTINGS_DEFAULTS
    assert SETTINGS_DEFAULTS["gemini_high_quality"] == "0"


def test_publish_retry_delays_default_one_attempt():
    # Narrowed back from "300,900,1800,3600" (4 retries) — every retry
    # restarts the phone for another polling window, so a stuck task
    # used to burn 5 cloud-phone uptimes before landing in FAILURE.
    # One retry after 15 min recovers transient GeeLark hiccups; tasks
    # that are genuinely stuck go to FAILURE for manual review.
    assert SETTINGS_DEFAULTS["publish_retry_delays_seconds"] == "900"


def test_default_target_warmup_count_is_10():
    assert SETTINGS_DEFAULTS["default_target_warmup_count"] == "10"
    assert "default_max_warm_time" not in SETTINGS_DEFAULTS


def test_daily_warmup_count_default_is_4():
    from app.config import SETTINGS_DEFAULTS
    assert SETTINGS_DEFAULTS["daily_warmup_count"] == "4"


def test_legacy_create_phone_defaults_were_removed():
    """default_group / default_os carried per-install GeeLark cosmetics that
    operators set in the GeeLark UI instead; the OS is hard-coded to
    Android 15 in geelark_client now."""
    assert "default_group" not in SETTINGS_DEFAULTS
    assert "default_os" not in SETTINGS_DEFAULTS


def test_caption_banned_terms_was_removed():
    """The hard-filter banlist (incl. the CTA structural check) was removed —
    content control happens in the caption prompt instead."""
    assert "caption_banned_terms" not in SETTINGS_DEFAULTS
