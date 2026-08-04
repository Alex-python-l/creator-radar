"""sanitize_caption() honors the max_len kwarg."""
import generate_caption


def test_default_max_len_uses_220():
    caption = "x" * 250
    out = generate_caption.sanitize_caption(caption)
    assert len(out) <= 220


def test_explicit_max_len_truncates_shorter():
    caption = "x" * 150
    out = generate_caption.sanitize_caption(caption, max_len=100)
    assert len(out) <= 100


def test_explicit_max_len_allows_longer():
    caption = "hello\n#one #two #three"
    out = generate_caption.sanitize_caption(caption, max_len=500)
    assert out == caption
