"""Adapter registry.

Module import order matters: importing this module instantiates the
four built-in adapters. Adding a fifth platform = new module +
_register() call below + platform string added to
app/services/platforms.py.
"""
from app.infra.platforms.base import PlatformAdapter
from app.infra.platforms.facebook import FacebookAdapter
from app.infra.platforms.instagram import InstagramAdapter
from app.infra.platforms.tiktok import TikTokAdapter
from app.infra.platforms.youtube import YouTubeAdapter


_REGISTRY: dict[str, PlatformAdapter] = {}


def _register(adapter: PlatformAdapter) -> None:
    _REGISTRY[adapter.platform] = adapter


_register(TikTokAdapter())
_register(YouTubeAdapter())
_register(InstagramAdapter())
_register(FacebookAdapter())


def get(platform: str) -> PlatformAdapter:
    """Return the adapter for `platform`. Raises KeyError on unknown."""
    try:
        return _REGISTRY[platform]
    except KeyError as exc:
        raise KeyError(
            f"no PlatformAdapter registered for {platform!r}; "
            f"known={sorted(_REGISTRY)}"
        ) from exc


def all_platforms() -> tuple[str, ...]:
    return tuple(_REGISTRY.keys())
