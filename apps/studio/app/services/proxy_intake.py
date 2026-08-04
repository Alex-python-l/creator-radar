"""Validate and probe proxy URLs provided by the wizard.

Splits user-pasted text into lines, normalizes each to a standard
`scheme://[user:pass@]host:port` URL, and fans probe calls out
concurrently. Per-line and per-probe failures are returned as data so the
UI can present them; nothing here raises on a bad line.

The normalizer is intentionally permissive — proxy vendors hand out half
a dozen formats (some with scheme, some without, some IP:Port:User:Pass)
and pasting any of them should Just Work. httpx and the GeeLark API both
demand the scheme-prefixed form, so we rewrite into that everywhere.
"""
from __future__ import annotations
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypedDict
from urllib.parse import quote

from app.services import proxy_check_service as pcs


# Schemes httpx (and therefore the probe service) actually supports. We
# reject anything else up front instead of letting the probe round-trip a
# guaranteed failure.
_VALID_SCHEMES = {"socks5", "socks5h", "socks4", "socks4a", "http", "https"}
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+\-.]*)://", re.I)
# Hostnames and IPv4 — letters/digits/dot/hyphen. We deliberately don't
# accept IPv6 here; if anyone ever pastes one we'll surface "unrecognized"
# rather than try to parse bracketed addresses and get it subtly wrong.
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+$")

_CONCURRENCY = 8

# Default scheme when the input has none. socks5 matches the rest of the
# system (geelark_service._format_proxy, the wizard placeholder text) and
# the dominant proxy type for TikTok automation. Users who genuinely have
# an http proxy must paste it with `http://` — we don't try to guess.
_DEFAULT_SCHEME = "socks5"


def normalize_proxy(raw: str, default_scheme: str = _DEFAULT_SCHEME) -> str | None:
    """Rewrite a vendor-pasted proxy string into a standard URL.

    Accepted shapes (whitespace stripped):
      - scheme://[user:pass@]host:port
      - user:pass@host:port
      - host:port
      - host:port:user:pass        (IP:Port:User:Pass — common vendor format)
      - user:pass:host:port

    Returns None when no host+port can be extracted, or when the explicit
    scheme isn't one httpx supports. Caller surfaces the error so the user
    fixes their input rather than us silently rewriting their intent.
    """
    s = (raw or "").strip()
    if not s:
        return None

    scheme = default_scheme
    m = _SCHEME_RE.match(s)
    if m:
        scheme = m.group(1).lower()
        if scheme not in _VALID_SCHEMES:
            return None
        s = s[m.end():]

    user: str | None = None
    pwd: str | None = None
    host: str | None = None
    port: str | None = None

    if "@" in s:
        # Use rpartition so a stray '@' inside the password doesn't trick
        # us into reading the host as part of userinfo. The host:port must
        # be on the right side of the *last* @.
        userinfo, _, hostport = s.rpartition("@")
        if ":" in userinfo and ":" in hostport:
            u, _, p = userinfo.partition(":")
            h, _, prt = hostport.rpartition(":")
            if h and prt.isdigit() and _HOST_RE.match(h):
                user, pwd, host, port = u, p, h, prt
    else:
        parts = s.split(":")
        if len(parts) == 2 and parts[1].isdigit() and _HOST_RE.match(parts[0]):
            host, port = parts
        elif len(parts) == 4:
            # Ambiguous between host:port:user:pass and user:pass:host:port.
            # The port segment is always a number, so look at parts[1] and
            # parts[3] to figure out which slot is the port.
            if parts[1].isdigit() and _HOST_RE.match(parts[0]):
                host, port, user, pwd = parts
            elif parts[3].isdigit() and _HOST_RE.match(parts[2]):
                user, pwd, host, port = parts

    if not host or not port:
        return None

    if user is not None:
        # Percent-encode userinfo so passwords containing '@' or ':' don't
        # break the resulting URL when httpx parses it again downstream.
        return (f"{scheme}://{quote(user, safe='')}:"
                f"{quote(pwd or '', safe='')}@{host}:{port}")
    return f"{scheme}://{host}:{port}"


class ParsedLine(TypedDict):
    line_no: int
    proxy: str
    error: str | None


def parse_lines(text: str) -> list[ParsedLine]:
    """One result per non-empty line. error=None means we recognized a
    proxy; `proxy` then holds the *normalized* URL the rest of the system
    stores and probes. error≠None means the original input is echoed back
    so the UI can highlight the bad line."""
    out: list[ParsedLine] = []
    for idx, line in enumerate((text or "").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        normalized = normalize_proxy(stripped)
        if normalized:
            out.append({"line_no": idx, "proxy": normalized, "error": None})
        else:
            out.append({"line_no": idx, "proxy": stripped,
                        "error": "unrecognized proxy format (expected "
                                 "user:pass@host:port, host:port:user:pass, "
                                 "or scheme://...)"})
    return out


def check_batch(proxies: list[str],
                check_one: Callable[[str], dict] | None = None) -> list[dict]:
    """Probe each proxy URL in parallel. Returns one dict per input proxy
    with keys: proxy, ok, exit_ip, latency_ms, error.

    Each input is normalized before probing — the probe service runs httpx
    which rejects schemeless URLs outright. The returned `proxy` is the
    normalized form on success (so the UI saves what we'll later re-probe)
    or the user's original string on a normalization failure (so they can
    see what they typed).

    `check_one` defaults to proxy_check_service.check_one; tests inject a
    fake to avoid network."""
    fn = check_one or pcs.check_one
    results: list[dict | None] = [None] * len(proxies)
    normalized = [normalize_proxy(p) for p in proxies]

    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as ex:
        futures: dict = {}
        for i, n in enumerate(normalized):
            if n is None:
                results[i] = {"proxy": proxies[i], "ok": False,
                              "exit_ip": None, "latency_ms": None,
                              "error": "unrecognized proxy format"}
            else:
                futures[ex.submit(fn, n)] = i
        for fut, i in futures.items():
            n = normalized[i]
            try:
                r = fut.result()
            except Exception as e:  # pragma: no cover - defensive
                r = {"ok": False, "exit_ip": None, "latency_ms": None,
                      "error": f"{type(e).__name__}: {e}"}
            results[i] = {"proxy": n, **r}
    return results  # type: ignore[return-value]
