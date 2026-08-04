from scripts.backfill_fb_ids import build_backfill_plan, fb_lookup_url


def test_fb_lookup_url_for_each_identifier_form():
    assert fb_lookup_url("61590296374856") == "https://www.facebook.com/61590296374856"
    assert (
        fb_lookup_url("profile.php?id=12345")
        == "https://www.facebook.com/profile.php?id=12345"
    )
    assert fb_lookup_url("Cristiano") == "https://www.facebook.com/Cristiano"
    assert (
        fb_lookup_url("people/Andra-Alexa/61590139985976")
        == "https://www.facebook.com/people/Andra-Alexa/61590139985976"
    )


def test_plan_renames_non_numeric_to_resolved_id():
    rows = [{"id": 5, "username": "Cristiano", "resolved_id": "100044296486382", "video_rows": 3}]
    plan = build_backfill_plan(rows)
    assert plan["renames"] == [{"id": 5, "new_username": "100044296486382"}]
    assert plan["merges"] == []
    assert plan["unresolved"] == []


def test_plan_skips_already_numeric():
    rows = [{"id": 5, "username": "100044296486382", "resolved_id": "100044296486382", "video_rows": 3}]
    plan = build_backfill_plan(rows)
    assert plan["renames"] == []
    assert plan["merges"] == []


def test_plan_merges_same_resolved_id_keeps_more_videos():
    rows = [
        {"id": 5, "username": "Cristiano", "resolved_id": "100044296486382", "video_rows": 2},
        {"id": 9, "username": "people/Cris/61", "resolved_id": "100044296486382", "video_rows": 7},
    ]
    plan = build_backfill_plan(rows)
    # 主账号取 video_rows 多者 (id=9)
    assert {"primary_id": 9, "duplicate_id": 5} in plan["merges"]
    # primary 非纯数字 → 改名为纯数字
    assert {"id": 9, "new_username": "100044296486382"} in plan["renames"]


def test_plan_collects_unresolved():
    rows = [{"id": 5, "username": "ghost", "resolved_id": None, "video_rows": 0}]
    plan = build_backfill_plan(rows)
    assert plan["unresolved"] == [5]
    assert plan["renames"] == []
