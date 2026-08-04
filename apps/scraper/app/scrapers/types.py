"""跨 scraper 共享的数据类型。"""
from dataclasses import dataclass


@dataclass
class ScrapedProfile:
    """归一化的账号 profile 字段，handler 用来 update influencers 表。

    IG / TikTok 都返回这个结构。各平台特有指标（如 TK 的 heart_count、
    IG 的 reels engagement rate）暂不入库 — 等真要用再扩 schema。
    """

    follower_count: int
    following_count: int
    video_count: int
    display_name: str | None
    avatar_url: str | None
    bio: str | None
