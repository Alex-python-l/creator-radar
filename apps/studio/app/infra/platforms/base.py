"""PlatformAdapter contracts.

Domain-level value objects shared across all platforms. Adapter
implementations live next door in tiktok.py / youtube.py / etc.

Design notes:
- ValidatedCaption is the only caption type Adapter.submit_publish
  accepts. Caller pulls the raw Gemini text through validate_caption
  first, which lets each Adapter encode its own truncation/fallback
  rules (e.g. YouTube's UTF-16 unit cap with a "Short" fallback).
- RawTaskStatus carries a single GeeLark /task/query row plus the
  task_id so verify_real() can call /task/detail if needed.
- PlatformOutcome models "what really happened" — after any
  platform-specific second-look. state is the only field the publish
  service branches on; everything else is contextual evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class ValidatedCaption:
    """Caption text after platform-specific normalization (truncation,
    fallback). Adapter guarantees this fits the platform's API limit
    without further client-side trimming."""
    text: str


@dataclass(frozen=True)
class PublishContext:
    """Inputs to Adapter.submit_publish."""
    env_id: str
    resource_url: str
    caption: ValidatedCaption


@dataclass(frozen=True)
class EngagementContext:
    """Inputs to Adapter.submit_engagement. Carries every field GeeLark
    needs across the supported actions; per-adapter submit_engagement
    picks which ones to forward."""
    env_id: str
    target_link: str
    schedule_at: int                              # unix timestamp seconds
    name: str
    remark: str
    comment_probability: int = 100                # TikTok
    comment_text: Optional[str] = None            # TikTok (Studio AI / provided)
    candidates: Optional[list[str]] = None        # Facebook
    keywords: Optional[list[str]] = None          # Facebook


class EngagementNotSupported(RuntimeError):
    """Adapter does not support GeeLark engagement (YouTube / Instagram)."""
    def __init__(self, platform: str):
        super().__init__(f"engagement not supported on {platform!r}")
        self.platform = platform


@dataclass(frozen=True)
class RawTaskStatus:
    """A single GeeLark /task/query row, paired with the task_id so
    verify_real can do follow-up calls."""
    task_id: str
    status: int                          # 0/1/2 running, 3 success, 4/7 failure
    share_link: Optional[str] = None
    raw: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class PlatformOutcome:
    """The outcome of a GeeLark task after platform-specific
    verification. Only `state` drives publish-service branching;
    everything else is evidence (share_link, fail details, audit
    screenshots) the publish service records into the publication row.
    """
    state: str                                   # "running" | "success" | "failure"
    share_link: Optional[str] = None             # success
    fail_code: Optional[int] = None              # failure
    fail_reason: Optional[str] = None            # failure
    fail_screenshot: Optional[str] = None        # failure
    observed_status: Optional[str] = None        # raw GeeLark status, passed through to phone_session
    audit_screenshot: Optional[str] = None       # success-side audit (YouTube false-success evidence)


class PlatformAdapter(Protocol):
    platform: str

    # Publish capability — every adapter implements these.
    max_caption_len: int
    def validate_caption(self, draft: str) -> ValidatedCaption: ...
    def submit_publish(self, client, ctx: PublishContext) -> str: ...
    def verify_real(self, client, raw: RawTaskStatus) -> PlatformOutcome: ...

    # Engagement capability — only TikTok / Facebook support this on
    # GeeLark today. Callers must check supports_engagement before
    # invoking normalize_target_link / submit_engagement; the
    # unsupported adapters raise EngagementNotSupported instead.
    supports_engagement: bool
    engagement_action: str                       # GeeLark task action name, or "" if N/A
    daily_limit_setting_key: Optional[str]       # settings key for the per-phone quota

    def normalize_target_link(self, raw: str) -> str: ...
    def submit_engagement(self, client, ctx: EngagementContext) -> str: ...


# ---------------------------------------------------------------------------
# Shared verify_real default + failure detail probe
# ---------------------------------------------------------------------------
def failure_outcome_from_detail(client, raw: RawTaskStatus) -> PlatformOutcome:
    """Common failure path: call GeeLark /task/detail to pull fail_code,
    failDesc, and the last RPA screenshot. Falls back gracefully if the
    detail call itself errors."""
    try:
        detail = client.get_task_detail(raw.task_id)
    except Exception:
        return PlatformOutcome(
            state="failure",
            fail_reason=f"task status {raw.status}",
            observed_status=str(raw.status),
        )
    code_raw = detail.get("failCode")
    fail_code: Optional[int]
    if isinstance(code_raw, int):
        fail_code = code_raw
    elif isinstance(code_raw, str) and code_raw.isdigit():
        fail_code = int(code_raw)
    else:
        fail_code = None
    images = detail.get("resultImages") or []
    return PlatformOutcome(
        state="failure",
        fail_code=fail_code,
        fail_reason=detail.get("failDesc") or f"task status {raw.status}",
        fail_screenshot=images[-1] if images else None,
        observed_status=str(raw.status),
    )


class TrustGeelarkAdapter:
    """Mixin: the standard verify_real implementation that trusts
    GeeLark's status verbatim — status=3 → success, status in (4,7) →
    failure with detail, everything else → running.

    YouTubeAdapter overrides verify_real to layer false-success audit
    evidence on top of the trusted status=3 path.
    """

    def verify_real(self, client, raw: RawTaskStatus) -> PlatformOutcome:
        if raw.status == 3:
            share = (raw.share_link or "").strip()
            return PlatformOutcome(
                state="success",
                share_link=share or None,
                observed_status="3",
            )
        if raw.status in (4, 7):
            return failure_outcome_from_detail(client, raw)
        return PlatformOutcome(
            state="running", observed_status=str(raw.status),
        )


class NoEngagementMixin:
    """Default 'engagement not supported' implementation for YouTube /
    Instagram. Calling normalize_target_link / submit_engagement on these
    adapters raises EngagementNotSupported."""
    supports_engagement = False
    engagement_action = ""
    daily_limit_setting_key: Optional[str] = None

    def normalize_target_link(self, raw: str) -> str:
        raise EngagementNotSupported(self.platform)

    def submit_engagement(self, client, ctx: EngagementContext) -> str:
        raise EngagementNotSupported(self.platform)
