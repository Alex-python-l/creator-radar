from sqlmodel import Session
from app.db import engine
from app.models import Account, Publication, Video
from app.repositories import publications as pub_repo


def _seed_publication(session, *, platform: str, share_link: str):
    v = Video(stored_path="/tmp/x.mp4", original_filename="x.mp4")
    session.add(v); session.commit(); session.refresh(v)
    a = Account(profile_name=f"acc-{platform}",
                geelark_phone_id=f"ph-{platform}")
    session.add(a); session.commit(); session.refresh(a)
    p = Publication(
        video_id=v.id, account_id=a.id,
        platform=platform, result="SUCCESS", share_link=share_link,
    )
    session.add(p); session.commit(); session.refresh(p)
    return p.id


def test_list_comment_targets_returns_only_facebook_when_requested():
    with Session(engine) as s:
        fb_pub = _seed_publication(s, platform="facebook",
                                    share_link="https://www.facebook.com/x/posts/1")
        _seed_publication(s, platform="tiktok",
                          share_link="https://www.tiktok.com/@x/video/1")
    with Session(engine) as s:
        items = pub_repo.list_comment_targets(s, platform="facebook")
    assert [i["publication_id"] for i in items] == [fb_pub]


def test_list_comment_targets_default_platform_is_tiktok():
    with Session(engine) as s:
        _seed_publication(s, platform="facebook",
                          share_link="https://www.facebook.com/x/posts/1")
        tk_pub = _seed_publication(s, platform="tiktok",
                                    share_link="https://www.tiktok.com/@x/video/1")
    with Session(engine) as s:
        items = pub_repo.list_comment_targets(s)
    assert [i["publication_id"] for i in items] == [tk_pub]
