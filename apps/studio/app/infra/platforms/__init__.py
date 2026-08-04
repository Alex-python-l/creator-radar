"""PlatformAdapter registry: one adapter per supported platform.

Each adapter owns the platform-specific shape of caption validation,
GeeLark task submission, and status verification (including "did the
post really land" checks like YouTube's audit screenshot). The publish
service routes through `get(platform)` instead of branching on the
platform string.
"""
from app.infra.platforms.base import (
    EngagementContext,
    EngagementNotSupported,
    PlatformAdapter,
    PlatformOutcome,
    PublishContext,
    RawTaskStatus,
    ValidatedCaption,
)
from app.infra.platforms.registry import all_platforms, get

__all__ = [
    "EngagementContext",
    "EngagementNotSupported",
    "PlatformAdapter",
    "PlatformOutcome",
    "PublishContext",
    "RawTaskStatus",
    "ValidatedCaption",
    "all_platforms",
    "get",
]
