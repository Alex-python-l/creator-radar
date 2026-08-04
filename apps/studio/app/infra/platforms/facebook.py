"""Facebook PlatformAdapter."""
import re

from app.infra.platforms.base import (
    EngagementContext,
    PublishContext,
    TrustGeelarkAdapter,
    ValidatedCaption,
)


FB_POST_ADDRESS_MAX_LEN = 128  # GeeLark API limit; spec §11.1
FB_KEYWORD_MAX_COUNT = 10
FB_KEYWORD_MAX_LEN = 100
FB_COMMENT_MAX_COUNT = 10
FB_COMMENT_MAX_LEN = 8000


_FB_POST_RE = re.compile(
    r"^https://(?:www\.|web\.|m\.|mbasic\.)?facebook\.com/"
    r"(?:[\w.\-]+/posts/|permalink\.php\?|share/[pv]/|reel/|video\.php\?|story\.php\?|"
    r"groups/[\w.\-]+/posts/|[\w.\-]+/videos/)"
    r"[\w./?=&%\-]*$"
)
_FB_SHORT_RE = re.compile(
    r"^https://fb\.(?:watch|com)/[A-Za-z0-9_\-]+/?(?:[?#].*)?$"
)


class FacebookAdapter(TrustGeelarkAdapter):
    platform = "facebook"

    # Publish
    max_caption_len = 500

    # Engagement
    supports_engagement = True
    engagement_action = "facebook_auto_comment"
    daily_limit_setting_key = "daily_comment_limit_facebook"

    def validate_caption(self, draft: str) -> ValidatedCaption:
        text = (draft or "")[: self.max_caption_len]
        return ValidatedCaption(text=text)

    def submit_publish(self, client, ctx: PublishContext) -> str:
        return client.create_facebook_post_task(
            ctx.env_id, ctx.resource_url, description=ctx.caption.text,
        )

    def normalize_target_link(self, raw: str) -> str:
        link = (raw or "").strip()
        if not (_FB_POST_RE.match(link) or _FB_SHORT_RE.match(link)):
            raise ValueError("target link must be a Facebook post/reel URL")
        if len(link) > FB_POST_ADDRESS_MAX_LEN:
            raise ValueError(
                f"facebook post URL length {len(link)} exceeds GeeLark API "
                f"limit ({FB_POST_ADDRESS_MAX_LEN})"
            )
        return link

    def submit_engagement(self, client, ctx: EngagementContext) -> str:
        return client.create_facebook_auto_comment_task(
            ctx.env_id,
            post_address=ctx.target_link,
            comments=ctx.candidates or [],
            keywords=ctx.keywords or [],
            schedule_at=ctx.schedule_at,
            name=ctx.name,
            remark=ctx.remark,
        )
