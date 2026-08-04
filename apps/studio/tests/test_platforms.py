from app.services import platforms as plat


def test_platforms_tuple_has_expected_members():
    assert set(plat.PLATFORMS) == {"tiktok", "youtube", "instagram", "facebook"}
    # tiktok must be first so list-default behaviors stay stable.
    assert plat.PLATFORMS[0] == "tiktok"


def test_per_platform_maps_share_the_same_keys():
    keys = set(plat.PLATFORMS)
    assert set(plat.TEXT_LIMIT) == keys
    assert set(plat.LAST_POSTED_COL) == keys
    assert set(plat.LOGGED_IN_COL) == keys


def test_text_limits_match_api_contracts():
    # Hardcoded so a future contributor doesn't accidentally widen them.
    assert plat.TEXT_LIMIT == {
        "tiktok":    2200,
        "youtube":   100,
        "instagram": 2200,
        "facebook":  500,
    }


def test_last_posted_col_preserves_tiktok_legacy_name():
    # The pre-multi-platform column was called `last_posted_at` and remains.
    assert plat.LAST_POSTED_COL["tiktok"] == "last_posted_at"
    assert plat.LAST_POSTED_COL["youtube"] == "last_posted_at_youtube"
    assert plat.LAST_POSTED_COL["instagram"] == "last_posted_at_instagram"
    assert plat.LAST_POSTED_COL["facebook"] == "last_posted_at_facebook"


def test_validate_returns_normalized_platform():
    assert plat.validate("tiktok") == "tiktok"
    assert plat.validate("YouTube") == "youtube"
    assert plat.validate(" Instagram ") == "instagram"


def test_validate_rejects_unknown():
    import pytest
    with pytest.raises(ValueError):
        plat.validate("linkedin")
    with pytest.raises(ValueError):
        plat.validate(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        plat.validate("")
