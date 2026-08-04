"""US-side proxy probe service.

A tiny FastAPI service that lives on an overseas (US) VPS. Given a proxy URL,
it tries to fetch ipinfo.io through it and reports back whether the proxy
works, the observed exit IP, and the latency.

Why this exists: the main app runs in mainland China and the residential
proxies used by the TikTok accounts are only reachable from overseas. The
main app calls this service over plain HTTPS and lets it do the actual
proxied request.

Auth is a shared bearer token via the X-Probe-Token header.

Run with:
    uvicorn server:app --host 127.0.0.1 --port 8090
behind nginx + TLS. See README.md for systemd + nginx examples.
"""
from __future__ import annotations

import hmac
import os
import time
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# 5 s connect / 8 s read is enough — residential proxies that take longer
# than that to *just fetch an IP* are unusable in practice.
_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)
# ipinfo.io/json returns a small JSON; if it ever flakes we have a backup.
_TARGETS = ("https://ipinfo.io/json", "https://ifconfig.co/json")

TOKEN = os.getenv("PROBE_TOKEN", "")

app = FastAPI(title="proxy-probe", version="1")


class CheckRequest(BaseModel):
    proxy: str


class CheckResponse(BaseModel):
    ok: bool
    exit_ip: str | None = None
    country: str | None = None
    region: str | None = None
    latency_ms: int | None = None
    error: str | None = None


def _extract_ip(payload: dict[str, Any]) -> str | None:
    # ipinfo uses "ip"; ifconfig.co uses "ip" too — but be defensive.
    for k in ("ip", "addr", "address"):
        v = payload.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _extract_country(payload: dict[str, Any]) -> str | None:
    # ipinfo returns ISO alpha-2 in "country"; ifconfig.co uses
    # "country_iso". Anything not a 2-letter code is dropped — the UI
    # treats `country` as ISO alpha-2 for emoji flag lookup.
    for k in ("country", "country_iso"):
        v = payload.get(k)
        if isinstance(v, str) and len(v) == 2 and v.isalpha():
            return v.upper()
    return None


def _extract_region(payload: dict[str, Any]) -> str | None:
    # ipinfo's "city" is the most useful display ("San Jose"); fall back
    # to "region" (state/province) and ifconfig.co's "region_name".
    for k in ("city", "region", "region_name"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:64]
    return None


def _probe(proxy: str) -> CheckResponse:
    """Return a CheckResponse for `proxy`. Never raises."""
    proxy = (proxy or "").strip()
    if not proxy:
        return CheckResponse(ok=False, error="empty proxy")
    last_error: str | None = None
    for url in _TARGETS:
        started = time.monotonic()
        try:
            # httpx 0.27+: pass `proxy=` (singular). Trust httpx to handle
            # socks5://, http://, https:// schemes; if the user gave us
            # something exotic we want it to fail loudly.
            with httpx.Client(proxy=proxy, timeout=_TIMEOUT,
                              trust_env=False) as client:
                r = client.get(url)
                r.raise_for_status()
                data = r.json() if r.headers.get("content-type", "")\
                    .startswith("application/json") else {}
            elapsed = int((time.monotonic() - started) * 1000)
            ip = _extract_ip(data) if data else None
            country = _extract_country(data) if data else None
            region = _extract_region(data) if data else None
            if ip is None:
                # A 200 with no parseable exit IP means the proxy answered
                # but we can't confirm it actually egressed (hijacked
                # captive portal, half-broken proxy returning HTML). Try
                # the next target rather than green-lighting it.
                last_error = f"{url}: 200 without a parseable exit IP"
                continue
            return CheckResponse(ok=True, exit_ip=ip, country=country,
                                 region=region, latency_ms=elapsed)
        except httpx.HTTPError as e:
            # Strip httpx's wrapping noise; the type name + first line is
            # enough for an operator to recognise auth/timeout/DNS failures.
            last_error = f"{type(e).__name__}: {str(e).splitlines()[0]}"\
                if str(e) else type(e).__name__
        except ValueError as e:  # bad proxy URL parse, etc.
            last_error = f"invalid proxy: {e}"
            break
    return CheckResponse(ok=False, error=last_error or "unreachable")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/check", response_model=CheckResponse)
def check(body: CheckRequest,
          x_probe_token: str | None = Header(default=None)) -> CheckResponse:
    if not TOKEN:
        # Fail closed: an unconfigured probe must never accept requests.
        raise HTTPException(status_code=503, detail="probe not configured")
    if not x_probe_token or not hmac.compare_digest(x_probe_token, TOKEN):
        raise HTTPException(status_code=401, detail="bad token")
    return _probe(body.proxy)
