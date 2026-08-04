"""_kmb.py 边界测试:精确数字、K/M/B 缩写、小数、空值。"""

from app.scrapers._kmb import parse_kmb


def test_plain_int():
    assert parse_kmb("0") == 0
    assert parse_kmb("999") == 999


def test_k_suffix():
    assert parse_kmb("1K") == 1000
    assert parse_kmb("51K") == 51000
    assert parse_kmb("999K") == 999000


def test_k_with_decimal():
    assert parse_kmb("4.8K") == 4800
    assert parse_kmb("1.5K") == 1500


def test_m_suffix():
    assert parse_kmb("1M") == 1000000
    assert parse_kmb("1.2M") == 1200000
    assert parse_kmb("999M") == 999000000


def test_b_suffix():
    assert parse_kmb("3B") == 3000000000
    assert parse_kmb("2.5B") == 2500000000


def test_lowercase_suffix():
    # FB 偶尔小写,容忍
    assert parse_kmb("51k") == 51000
    assert parse_kmb("1.2m") == 1200000


def test_with_commas():
    # 大数字带千位逗号(>=1000 且未缩写时 FB 也会显示)
    assert parse_kmb("1,234") == 1234
    assert parse_kmb("1,234,567") == 1234567


def test_none_and_empty():
    assert parse_kmb(None) == 0
    assert parse_kmb("") == 0
    assert parse_kmb("   ") == 0


def test_invalid_returns_zero():
    assert parse_kmb("abc") == 0
    assert parse_kmb("--") == 0
