"""CommentSource registry — strategy classes for each comment_mode.

engagement_service used to branch on `comment_mode == "studio_ai" / ...`
in three places (create_comment_batch input validation, the TikTok
submit path's `comment_text` prep, the Facebook submit path's
`candidates` prep). The matrix scaled poorly: adding a fifth mode meant
touching the same three sites and remembering to handle Facebook's
no-candidates-for-geelark-ai exception in each.

Now each comment_mode is a class with two methods. New mode = new
class + one _register() call.

The class layer stays thin: the heavy lifting (Gemini calls, sanitize
loops) still lives in engagement_service so we don't have to thread
Gemini wiring through two modules. The strategy objects just dispatch
to the right helper.
"""
from __future__ import annotations

from typing import Optional, Protocol


COMMENT_MODE_GEELARK_AI = "geelark_ai"
COMMENT_MODE_STUDIO_AI = "studio_ai"
COMMENT_MODE_PROVIDED = "provided"


class CommentSource(Protocol):
    mode: str
    # True when this mode supplies the GeeLark task with explicit
    # `comment_text`; False means GeeLark's own AI authors inline.
    supplies_comment_text: bool
    # True when this mode requires a comment_prompt_id at batch
    # creation; False when a freeform brief is enough.
    requires_prompt: bool

    def prepare_tiktok_comment_text(
            self, session, run: dict, parent: dict,
    ) -> Optional[str]: ...

    def prepare_facebook_candidates(
            self, session, run: dict, parent: dict,
    ) -> list[str]: ...


class _GeelarkAISource:
    mode = COMMENT_MODE_GEELARK_AI
    supplies_comment_text = False
    requires_prompt = False

    def prepare_tiktok_comment_text(self, session, run, parent):
        # GeeLark's own AI composes the comment inline; we send None so
        # create_tiktok_ai_comment_task picks GeeLark-AI behaviour.
        return None

    def prepare_facebook_candidates(self, session, run, parent):
        raise RuntimeError(
            "geelark_ai is not supported for Facebook engagement"
        )


class _StudioAISource:
    mode = COMMENT_MODE_STUDIO_AI
    supplies_comment_text = True
    requires_prompt = True

    def prepare_tiktok_comment_text(self, session, run, parent):
        from app.services.engagement_service import _prepare_studio_comment
        return _prepare_studio_comment(session, run, parent)

    def prepare_facebook_candidates(self, session, run, parent):
        from app.services.engagement_service import (
            _generate_fb_studio_candidates,
        )
        return _generate_fb_studio_candidates(session, run, parent)


class _ProvidedSource:
    mode = COMMENT_MODE_PROVIDED
    supplies_comment_text = True
    requires_prompt = False

    def prepare_tiktok_comment_text(self, session, run, parent):
        text = (run.get("comment_text") or "").strip()
        if not text:
            raise RuntimeError("provided comment text missing")
        return text

    def prepare_facebook_candidates(self, session, run, parent):
        candidates = run.get("comment_candidates") or []
        if not candidates:
            raise RuntimeError(
                f"engagement run {run['id']} has no comment_candidates "
                "for FB provided mode"
            )
        return candidates


_REGISTRY: dict[str, CommentSource] = {}


def _register(source: CommentSource) -> None:
    _REGISTRY[source.mode] = source


_register(_GeelarkAISource())
_register(_StudioAISource())
_register(_ProvidedSource())


def get(mode: str) -> CommentSource:
    """Return the CommentSource for `mode`. Raises ValueError on unknown."""
    try:
        return _REGISTRY[mode]
    except KeyError as exc:
        raise ValueError(
            f"unknown comment_mode: {mode!r}; "
            f"expected one of {sorted(_REGISTRY)}"
        ) from exc


def all_modes() -> tuple[str, ...]:
    return tuple(_REGISTRY.keys())
