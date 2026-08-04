"""Instagram PlatformAdapter."""
from app.infra.platforms.base import (
    NoEngagementMixin,
    PublishContext,
    TrustGeelarkAdapter,
    ValidatedCaption,
)


class InstagramAdapter(NoEngagementMixin, TrustGeelarkAdapter):
    platform = "instagram"
    max_caption_len = 2200

    def validate_caption(self, draft: str) -> ValidatedCaption:
        text = (draft or "")[: self.max_caption_len]
        return ValidatedCaption(text=text)

    def submit_publish(self, client, ctx: PublishContext) -> str:
        # Instagram Reels API accepts up to 10 clips; single-clip use
        # passes [url].
        return client.create_instagram_post_task(
            ctx.env_id, [ctx.resource_url], description=ctx.caption.text,
        )
