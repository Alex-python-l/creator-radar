"""Unit tests for generate_caption.build_prompt()."""
import generate_caption


def test_brief_appears_in_output():
    out = generate_caption.build_prompt("Travel vlogger, Tokyo focus.", 220)
    assert "Travel vlogger, Tokyo focus." in out


def test_max_len_is_substituted():
    out = generate_caption.build_prompt("anything", 180)
    assert "180" in out
    assert "220" not in out


def test_none_brief_falls_back_to_generic():
    out = generate_caption.build_prompt(None, 220)
    assert "IELTS" not in out  # no implicit IELTS bias
    assert "generic short-form video caption" in out


def test_empty_brief_falls_back_to_generic():
    out = generate_caption.build_prompt("", 220)
    assert "generic short-form video caption" in out


def test_whitespace_only_brief_falls_back():
    out = generate_caption.build_prompt("   \n  ", 220)
    assert "generic short-form video caption" in out


def test_output_rules_section_present():
    out = generate_caption.build_prompt("x", 220)
    assert "Output rules" in out
    assert "No Markdown" in out
    assert 'Option 1 / Option 2' in out


def test_avoid_section_present():
    out = generate_caption.build_prompt("x", 220)
    assert "Avoid" in out
    assert "amazing" in out
    assert "Em dashes" in out
