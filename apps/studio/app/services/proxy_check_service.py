"""Proxy health checks via the overseas probe service.

The probe (probe/server.py, deployed on a US VPS) is the only piece that
can actually reach residential proxies — the main app can't dial them from
mainland China. So this service just POSTs the proxy URL to the probe and
records what comes back on the account row.

Public entry point is `run_proxy_check(session, http_post=None) -> str`. The
scheduler calls it daily; tests pass a fake `http_post` to avoid real HTTP.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, TypedDict

from sqlmodel import Session

from app import config
from app.repositories import accounts as acc_repo


class _ProbeResult(TypedDict, total=False):
    ok: bool
    exit_ip: str | None
    country: str | None
    region: str | None
    latency_ms: int | None
    error: str | None


# Type alias for the injectable HTTP POST. Returning a dict (not a httpx
# Response) keeps the seam thin and tests trivial.
HttpPost = Callable[[str, dict, dict, float], _ProbeResult]

# 12 s budget per check: the probe itself caps each attempt at 8 s read,
# plus we cross the Pacific. 12 s is generous without dragging the daily
# job past a few minutes for 100+ accounts.
_REQUEST_TIMEOUT = 12.0
# Probe is cheap but the proxies behind it are not — 8 in-flight checks
# keeps total wall time ~= ceil(N/8) * per-check seconds without slamming
# the residential network. Tune via env if needed.
_CONCURRENCY = 8


def _default_post(url: str, json: dict, headers: dict,
                  timeout: float) -> _ProbeResult:
    # Imported lazily so tests that monkeypatch this function never need
    # httpx installed. Production paths pay the import once per process.
    import httpx

    try:
        r = httpx.post(url, json=json, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError as e:
        return {"ok": False, "error":
                f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}",
                "exit_ip": None, "country": None, "region": None,
                "latency_ms": None}
    except ValueError as e:  # bad JSON
        return {"ok": False, "error": f"bad probe response: {e}",
                "exit_ip": None, "country": None, "region": None,
                "latency_ms": None}
    return {
        "ok": bool(data.get("ok")),
        "exit_ip": data.get("exit_ip"),
        "country": data.get("country"),
        "region": data.get("region"),
        "latency_ms": data.get("latency_ms"),
        "error": data.get("error"),
    }


def check_one(proxy: str, http_post: HttpPost | None = None) -> _ProbeResult:
    """Probe a single proxy URL. Returns the same dict shape regardless of
    success — caller decides what to persist."""
    if not config.PROBE_URL or not config.PROBE_TOKEN:
        return {"ok": False, "error": "probe not configured",
                "exit_ip": None, "country": None, "region": None,
                "latency_ms": None}
    fn = http_post or _default_post
    return fn(config.PROBE_URL, {"proxy": proxy},
              {"X-Probe-Token": config.PROBE_TOKEN}, _REQUEST_TIMEOUT)


def _now_iso() -> str:
    # Seconds precision is enough for a "last checked" display; trimming
    # microseconds keeps the SQLite text shorter and easier to read.
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _apply_result(session: Session, account_id: int,
                  result: _ProbeResult) -> None:
    """Persist a probe result. proxy_exit_ip / proxy_country /
    proxy_region are only overwritten on success so a transient failure
    doesn't blank a known-good cache."""
    patch: dict[str, str | None] = {
        "proxy_status": "ok" if result.get("ok") else "fail",
        "proxy_checked_at": _now_iso(),
    }
    if result.get("ok"):
        if result.get("exit_ip"):
            patch["proxy_exit_ip"] = result["exit_ip"]
        if result.get("country"):
            patch["proxy_country"] = result["country"]
        if result.get("region"):
            patch["proxy_region"] = result["region"]
    acc_repo.update(session, account_id, patch)


def run_proxy_check(session: Session,
                    http_post: HttpPost | None = None) -> str:
    """Check every account that has a non-empty `proxy` and write back
    status/checked_at/exit_ip. Returns a one-line summary for job_runs."""
    if not config.PROBE_URL or not config.PROBE_TOKEN:
        return "skipped: PROBE_URL/PROBE_TOKEN not set"

    rows = [r for r in acc_repo.list_all(session) if (r.get("proxy") or "").strip()]
    if not rows:
        return "no accounts with a proxy"

    fn = http_post or _default_post
    ok = 0
    fail = 0
    # ThreadPoolExecutor over a list of (id, proxy) — each task does one
    # HTTPS POST to the probe and the probe does one proxied GET. Session
    # writes happen serially in the main loop, not inside threads.
    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as ex:
        future_to_row = {
            ex.submit(check_one, r["proxy"], fn): r for r in rows
        }
        for fut in future_to_row:
            r = future_to_row[fut]
            try:
                result = fut.result()
            except Exception as e:  # defensive — check_one already swallows HTTPError
                result = {"ok": False, "error": f"{type(e).__name__}: {e}",
                          "exit_ip": None, "latency_ms": None}
            _apply_result(session, r["id"], result)
            if result.get("ok"):
                ok += 1
            else:
                fail += 1
    return f"{ok} ok, {fail} fail"
