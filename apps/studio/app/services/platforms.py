"""Canonical platform set and per-platform column maps.

This is the only file that hard-codes the four supported platforms.
API validation, service dispatch, repository updates and the scheduler
all import from here so adding a fifth platform (or disabling one) is
a single-file change."""

PLATFORMS: tuple[str, ...] = ("tiktok", "youtube", "instagram", "facebook")

# Text input limits enforced at the geelark_client layer (truncates).
TEXT_LIMIT: dict[str, int] = {
    "tiktok":    2200,
    "youtube":   100,
    "instagram": 2200,
    "facebook":  500,
}

# Column on accounts that holds the last-post timestamp per platform.
LAST_POSTED_COL: dict[str, str] = {
    "tiktok":    "last_posted_at",
    "youtube":   "last_posted_at_youtube",
    "instagram": "last_posted_at_instagram",
    "facebook":  "last_posted_at_facebook",
}

LOGGED_IN_COL: dict[str, str] = {
    "tiktok":    "tiktok_logged_in",
    "youtube":   "youtube_logged_in",
    "instagram": "instagram_logged_in",
    "facebook":  "facebook_logged_in",
}


def validate(value: str) -> str:
    """Return the normalized platform string, or raise ValueError."""
    if not isinstance(value, str):
        raise ValueError(f"platform must be a string, got {type(value).__name__}")
    norm = value.strip().lower()
    if norm not in PLATFORMS:
        raise ValueError(
            f"unknown platform: {value!r}; expected one of {PLATFORMS}")
    return norm
