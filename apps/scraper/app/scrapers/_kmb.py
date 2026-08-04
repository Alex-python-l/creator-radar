"""把 FB UI 的 "51K" / "1.2M" / "3B" / "1,234" 转成 int。

FB title 字段里 likes / view 都用这种缩写形式,
而 reactions 必须从 title 字符串 regex 提取,所以这个工具是必需的。
"""
import re

_KMB_PATTERN = re.compile(r"^\s*([\d.,]+)\s*([KMBkmb])?\s*$")
_MULTIPLIERS = {"K": 1000, "M": 1000000, "B": 1000000000}


def parse_kmb(text: str | None) -> int:
    """把 "51K" / "1.2M" / "3B" / "1,234" / "999" 转成 int。

    无效输入(None / "" / "abc" / "--")统一返回 0,
    跟 fetch_logs 里"拿不到字段"的默认值对齐,不抛异常。
    """
    if not text:
        return 0
    m = _KMB_PATTERN.match(text)
    if not m:
        return 0
    digits, suffix = m.group(1), m.group(2)
    # 去千位逗号
    digits = digits.replace(",", "")
    try:
        value = float(digits)
    except ValueError:
        return 0
    if suffix:
        value *= _MULTIPLIERS[suffix.upper()]
    return int(value)
