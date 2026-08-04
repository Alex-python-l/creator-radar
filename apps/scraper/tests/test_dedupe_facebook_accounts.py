from scripts.dedupe_facebook_accounts import build_merge_plans


def test_build_merge_plans_keeps_account_with_more_videos():
    rows = [
        {"id": 260, "username": "people/Jeannie-Study/pfbidA", "display_name": "Jeannie Study", "video_rows": 6, "active_jobs": 0},
        {"id": 379, "username": "people/Jeannie-Study/pfbidB", "display_name": "Jeannie Study", "video_rows": 0, "active_jobs": 0},
    ]

    plans = build_merge_plans(rows)

    assert len(plans) == 1
    assert plans[0]["primary_id"] == 260
    assert plans[0]["duplicate_id"] == 379
    assert plans[0]["reason"] == "same display_name"


def test_build_merge_plans_skips_accounts_with_active_jobs():
    rows = [
        {"id": 260, "username": "people/Jeannie-Study/pfbidA", "display_name": "Jeannie Study", "video_rows": 6, "active_jobs": 0},
        {"id": 379, "username": "people/Jeannie-Study/pfbidB", "display_name": "Jeannie Study", "video_rows": 0, "active_jobs": 1},
    ]

    assert build_merge_plans(rows) == []
