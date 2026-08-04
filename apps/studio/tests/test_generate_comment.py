def test_render_prompt_appends_title():
    from generate_comment import render_prompt

    body = "You are a sports fan. Reply with a short comment."
    out = render_prompt(body, target_title="Manchester United wins late")

    assert out == (
        "You are a sports fan. Reply with a short comment."
        "\n\nVideo title: Manchester United wins late"
    )


def test_render_prompt_omits_appendix_when_title_missing():
    from generate_comment import render_prompt

    body = "You are a sports fan. Reply with a short comment."

    assert render_prompt(body, target_title=None) == body
    assert render_prompt(body, target_title="") == body
    assert render_prompt(body, target_title="   ") == body


def test_render_prompt_does_not_double_newline_when_body_ends_with_lf():
    from generate_comment import render_prompt

    out = render_prompt("hello\n", target_title="Some video")
    assert out == "hello\nVideo title: Some video"


def test_render_prompt_leaves_brace_tokens_untouched():
    """Users may have placeholder syntax like {caption_or_hashtags} in their
    body; we don't substitute anything — it must reach Gemini verbatim."""
    from generate_comment import render_prompt

    body = "Hashtags: {caption_or_hashtags}. Title token: {title}."
    out = render_prompt(body, target_title="Hello")

    assert "{caption_or_hashtags}" in out
    assert "{title}" in out  # not substituted anymore
    assert out.endswith("Video title: Hello")


def test_sanitize_comment_limits_length_and_removes_labels():
    from generate_comment import sanitize_comment

    out = sanitize_comment(
        "Comment: This routine pairs well with daily mock prompts.",
        max_len=28,
    )

    assert out == "This routine pairs well with"
