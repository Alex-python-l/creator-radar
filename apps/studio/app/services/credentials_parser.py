import re

from app.services.platforms import validate


def parse_login_info_lines(raw: str, *, platform: str = "tiktok") -> list[dict]:
    """Parse one pasted blob into account rows.

    Blank lines are account separators. If the paste has no blank separators,
    each non-empty line is treated as one account. The account block is stored
    as-is apart from trimming line edges and normalizing CRLF to LF.
    """
    field = f"{validate(platform)}_login_info"
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    if re.search(r"\n[ \t]*\n", text):
        chunks = re.split(r"\n[ \t]*\n+", text)
    else:
        chunks = text.splitlines()
    rows = []
    for chunk in chunks:
        normalized = _normalize_account_block(chunk)
        if normalized:
            rows.append({field: normalized})
    return rows


def _normalize_account_block(chunk: str) -> str:
    return "\n".join(line.strip() for line in chunk.strip().splitlines()
                     if line.strip())
