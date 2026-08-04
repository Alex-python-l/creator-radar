"""Unit tests for app.services.posting_service helpers."""
from app.services.posting_service import format_failure_hint


def test_format_failure_hint_known_code():
    h = format_failure_hint(20267)
    assert h is not None
    assert "Instagram" in h


def test_format_failure_hint_unknown_code():
    assert format_failure_hint(99999) is None


def test_format_failure_hint_none():
    assert format_failure_hint(None) is None
