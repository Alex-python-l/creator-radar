from sqlmodel import Session
from app.db import engine
from app.repositories import accounts as acc_repo
from app.repositories import eligible_phones as eph_repo


def test_eligible_commenters_filters_by_facebook_logged_in(default_group_id):
    with Session(engine) as s:
        a_fb_ok = acc_repo.create(s, {
            "profile_name": "fb-ok",
            "facebook_logged_in": 1,
            "tiktok_logged_in": 0,
            "warmed_up": 1,
            "geelark_phone_id": "ph-fb-1",
            "group_id": default_group_id,
        })
        a_tk_only = acc_repo.create(s, {
            "profile_name": "tk-only",
            "facebook_logged_in": 0,
            "tiktok_logged_in": 1,
            "warmed_up": 1,
            "geelark_phone_id": "ph-tk-1",
            "group_id": default_group_id,
        })
    with Session(engine) as s:
        fb_items = eph_repo.list_eligible_commenters(s, platform="facebook")
        tk_items = eph_repo.list_eligible_commenters(s, platform="tiktok")
    fb_ids = {i["id"] for i in fb_items}
    tk_ids = {i["id"] for i in tk_items}
    assert a_fb_ok in fb_ids and a_tk_only not in fb_ids
    assert a_tk_only in tk_ids and a_fb_ok not in tk_ids


def test_eligible_commenters_default_platform_is_tiktok(default_group_id):
    """向后兼容：不传 platform 时仍按 tiktok 行为。"""
    from sqlmodel import Session
    with Session(engine) as s:
        a = acc_repo.create(s, {
            "profile_name": "default-tk",
            "tiktok_logged_in": 1, "facebook_logged_in": 0,
            "warmed_up": 1, "geelark_phone_id": "ph-d-1",
            "group_id": default_group_id,
        })
    with Session(engine) as s:
        items = eph_repo.list_eligible_commenters(s)
    assert a in {i["id"] for i in items}
