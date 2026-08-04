"""TikTok PlatformAdapter."""
import re

from app.infra.platforms.base import (
    EngagementContext,
    PublishContext,
    TrustGeelarkAdapter,
    ValidatedCaption,
)


_TIKTOK_VIDEO_RE = re.compile(
    r"^https://(www\.)?tiktok\.com/@[^/]+/video/\d+([/?#].*)?$"
)
_TIKTOK_SHORT_RE = re.compile(
    r"^https://vt\.tiktok\.com/[A-Za-z0-9]+/?([?#].*)?$"
)


class TikTokAdapter(TrustGeelarkAdapter):
    platform = "tiktok"

    # Publish
    max_caption_len = 2200

    # Engagement
    supports_engagement = True
    engagement_action = "tiktok_ai_comment"
    daily_limit_setting_key = "daily_comment_limit_tiktok"

    def validate_caption(self, draft: str) -> ValidatedCaption:
        text = (draft or "")[: self.max_caption_len]
        return ValidatedCaption(text=text)

    def submit_publish(self, client, ctx: PublishContext) -> str:
        return client.create_video_post_task(
            ctx.env_id, ctx.resource_url, video_desc=ctx.caption.text,
        )

    def normalize_target_link(self, raw: str) -> str:
        link = (raw or "").strip()
        if _TIKTOK_VIDEO_RE.match(link) or _TIKTOK_SHORT_RE.match(link):
            return link
        raise ValueError("target link must be a TikTok video URL")

    def submit_engagement(self, client, ctx: EngagementContext) -> str:
        return client.create_tiktok_ai_comment_task(
            ctx.env_id,
            [ctx.target_link],
            schedule_at=ctx.schedule_at,
            comment_probability=ctx.comment_probability,
            comment=ctx.comment_text,
            name=ctx.name,
            remark=ctx.remark,
        )
