#!/usr/bin/env python3
"""Generate one TikTok comment with Gemini using a user-supplied prompt.

The prompt body lives in the comment_prompts table — it is sent verbatim
to Gemini. The target TikTok video's title (if any) is appended to the
end of the prompt as ``Video title: <title>`` so the operator never has
to think about template variables.
"""

import re

from generate_caption import (
    GEMINI_MODEL, _generate_content_with_retry, _get_client,
)

COMMENT_MAX_LEN_DEFAULT = 180

_LABEL_RE = re.compile(r"^\s*(?:Comment|评论)\s*:\s*", re.IGNORECASE)


def render_prompt(body: str, *, target_title: str | None) -> str:
    """Return ``body`` with the video title appended.

    The body is sent verbatim — no variable substitution. When a title is
    available, ``\\n\\nVideo title: <title>`` is appended so the LLM has
    something concrete to reference. When the title is missing, the body
    is returned untouched.
    """
    body = body or ""
    title = (target_title or "").strip()
    if not title:
        return body
    sep = "" if body.endswith("\n") else "\n\n"
    return f"{body}{sep}Video title: {title}"


def sanitize_comment(raw: str, *, max_len: int = COMMENT_MAX_LEN_DEFAULT) -> str:
    """Trim quotes/labels and enforce a max length.

    Multi-line output is preserved (TikTok lets the user write newlines, and
    some prompts intentionally emit short single-line replies anyway). We do
    NOT reject "multiple options" output — the prompt itself decides what
    shape it wants.
    """
    text = (raw or "").strip().strip('"').strip("'")
    text = _LABEL_RE.sub("", text)
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


def generate_comment_for_target(
    *,
    prompt_body: str,
    target_title: str | None,
    max_len: int = COMMENT_MAX_LEN_DEFAULT,
    thinking_budget: int = 0,
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    client = _get_client(api_key) if api_key else _get_client()
    prompt = render_prompt(prompt_body, target_title=target_title)
    # Same 429/5xx backoff as the caption path — a single transient
    # Gemini error must not turn into a terminal FAILURE on the run
    # (there is no auto-revive pipeline for engagement FAILUREs).
    response = _generate_content_with_retry(
        client,
        model=(model or "").strip() or GEMINI_MODEL,
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config={"thinking_config": {"thinking_budget": thinking_budget}},
    )
    comment = sanitize_comment(response.text or "", max_len=max_len)
    if not comment:
        raise RuntimeError("Gemini returned an empty comment")
    return comment
