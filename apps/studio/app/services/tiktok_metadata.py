import html as html_lib
import os
import re

from dotenv import load_dotenv
import requests

load_dotenv()

_META_RE = re.compile(
    r"<meta\b(?P<attrs>[^>]+)>",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(
    r"""(?P<key>[\w:-]+)\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""",
    re.IGNORECASE | re.DOTALL,
)
_TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SPACE_RE = re.compile(r"\s+")

_FETCH_TIMEOUT = 12
_OEMBED_URL = "https://www.tiktok.com/oembed"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = html_lib.unescape(value)
    text = _SPACE_RE.sub(" ", text).strip()
    return text or None


def _meta_tags(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _META_RE.finditer(html or ""):
        attrs = {
            attr.group("key").lower(): html_lib.unescape(attr.group("value"))
            for attr in _ATTR_RE.finditer(match.group("attrs"))
        }
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content")
        if key and content and key not in out:
            out[key] = content
    return out


def extract_tiktok_metadata(html: str, source_url: str) -> dict:
    meta = _meta_tags(html)
    title = (
        meta.get("og:title")
        or meta.get("twitter:title")
        or meta.get("title")
    )
    description = (
        meta.get("og:description")
        or meta.get("twitter:description")
        or meta.get("description")
    )
    if not title:
        m = _TITLE_RE.search(html or "")
        title = m.group(1) if m else None
    return {
        "source_url": source_url,
        "title": _clean(title),
        "description": _clean(description),
    }


def _headers() -> dict[str, str]:
    return {
        "User-Agent": _USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }


def _proxies() -> dict[str, str] | None:
    proxy = (
        os.getenv("TIKTOK_METADATA_PROXY", "").strip()
        or os.getenv("DRIVE_IMPORT_PROXY", "").strip()
    )
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _fetch_oembed_metadata(url: str) -> dict | None:
    try:
        response = requests.get(
            _OEMBED_URL,
            params={"url": url},
            timeout=_FETCH_TIMEOUT,
            headers=_headers(),
            proxies=_proxies(),
        )
        response.raise_for_status()
        body = response.json()
    except Exception:
        return None

    title = _clean(body.get("title"))
    author = _clean(body.get("author_name"))
    author_url = _clean(body.get("author_url"))
    if not title and not author:
        return None
    return {
        "source_url": author_url or url,
        "title": title,
        "description": author,
    }


def fetch_tiktok_metadata(url: str) -> dict:
    oembed = _fetch_oembed_metadata(url)
    if oembed is not None:
        return oembed

    try:
        response = requests.get(
            url,
            timeout=_FETCH_TIMEOUT,
            headers=_headers(),
            proxies=_proxies(),
            allow_redirects=True,
        )
        response.raise_for_status()
    except Exception:
        return {"source_url": url, "title": None, "description": None}
    return extract_tiktok_metadata(response.text, response.url or url)
