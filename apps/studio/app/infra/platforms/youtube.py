"""YouTube PlatformAdapter.

Two layered protections against the false-success bug surfaced
2026-06-18:

1. validate_caption truncates to YouTube's mobile UI validator unit count
   with a 10-unit buffer so emoji/regional-indicator titles never push
   the visible counter to 101/100 and disable Upload Short.

2. verify_real records the final RPA screenshot on every status=3
   completion. The first iteration does NOT block "success" — operators
   manually classify screenshots that show 101/100 or a disabled button
   and reset those rows via scripts/audit_youtube_false_success.py.
"""
from app.infra.platforms.base import (
    NoEngagementMixin,
    PlatformOutcome,
    PublishContext,
    RawTaskStatus,
    TrustGeelarkAdapter,
    ValidatedCaption,
    failure_outcome_from_detail,
)


YOUTUBE_TITLE_SAFE_UTF16_UNITS = 90
YOUTUBE_TITLE_FALLBACK = "Short"


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _truncate_utf16_units(value: str, limit: int) -> str:
    out: list[str] = []
    used = 0
    for ch in value:
        units = _utf16_units(ch)
        if used + units > limit:
            break
        out.append(ch)
        used += units
    return "".join(out)


class YouTubeAdapter(NoEngagementMixin, TrustGeelarkAdapter):
    platform = "youtube"
    max_caption_len = YOUTUBE_TITLE_SAFE_UTF16_UNITS  # units, NOT chars

    def validate_caption(self, draft: str) -> ValidatedCaption:
        title = (draft or "").strip()
        if not title:
            return ValidatedCaption(text=YOUTUBE_TITLE_FALLBACK)
        title = _truncate_utf16_units(
            title, YOUTUBE_TITLE_SAFE_UTF16_UNITS,
        ).strip()
        return ValidatedCaption(text=title or YOUTUBE_TITLE_FALLBACK)

    def submit_publish(self, client, ctx: PublishContext) -> str:
        return client.create_youtube_post_task(
            ctx.env_id, ctx.resource_url, title=ctx.caption.text,
        )

    def verify_real(self, client, raw: RawTaskStatus) -> PlatformOutcome:
        if raw.status == 3:
            return PlatformOutcome(
                state="success",
                share_link=(raw.share_link or "").strip() or None,
                observed_status="3",
                audit_screenshot=_audit_screenshot(client, raw.task_id),
            )
        if raw.status in (4, 7):
            return failure_outcome_from_detail(client, raw)
        return PlatformOutcome(
            state="running", observed_status=str(raw.status),
        )


def _audit_screenshot(client, task_id: str) -> str | None:
    """Best-effort RPA final screenshot probe. Returns None on any
    exception so a transient detail-API hiccup never blocks the success
    path."""
    try:
        detail = client.get_task_detail(task_id)
    except Exception:
        return None
    images = detail.get("resultImages") or []
    return images[-1] if images else None
