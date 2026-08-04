"""Tests for the proxy-probe service.

The probe HTTP call is injected (`http_post`), so these tests never hit the
network. We assert: status is persisted, exit IP is sticky across blips,
empty-proxy accounts are skipped, and the daily job is a no-op when the
probe isn't configured.
"""
import pytest

from app import config as cfg
from app.services import proxy_check_service as pcs
from app.repositories import accounts as ar


@pytest.fixture(autouse=True)
def _probe_configured(monkeypatch):
    """Default to "probe is configured" so most tests don't have to repeat
    this. Individual tests can monkeypatch back to "" to assert the skip
    path."""
    monkeypatch.setattr(cfg, "PROBE_URL", "https://probe.test/check")
    monkeypatch.setattr(cfg, "PROBE_TOKEN", "shared")


def _ok(ip="203.0.113.5", latency=812):
    return {"ok": True, "exit_ip": ip, "latency_ms": latency, "error": None}


def _fail(err="ConnectError: timeout"):
    return {"ok": False, "exit_ip": None, "latency_ms": None, "error": err}


def test_skipped_when_probe_not_configured(db, monkeypatch):
    monkeypatch.setattr(cfg, "PROBE_URL", "")
    ar.create(db, {"profile_name": "A", "proxy": "socks5://a:b@h:1080"})
    summary = pcs.run_proxy_check(db, http_post=lambda *a, **k: _ok())
    assert "skipped" in summary
    # Status untouched
    assert ar.list_all(db)[0]["proxy_status"] is None


def test_no_accounts_with_proxy(db):
    ar.create(db, {"profile_name": "A"})  # no proxy
    ar.create(db, {"profile_name": "B", "proxy": "   "})  # whitespace only
    summary = pcs.run_proxy_check(
        db, http_post=lambda *a, **k: pytest.fail("must not call probe"))
    assert summary == "no accounts with a proxy"


def test_ok_result_persisted(db):
    aid = ar.create(db, {"profile_name": "A",
                         "proxy": "socks5://u:p@h.example:1080"})
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers))
        return _ok(ip="198.51.100.7", latency=540)

    summary = pcs.run_proxy_check(db, http_post=fake_post)
    assert summary == "1 ok, 0 fail"
    row = ar.get(db, aid)
    assert row["proxy_status"] == "ok"
    assert row["proxy_exit_ip"] == "198.51.100.7"
    assert row["proxy_checked_at"] is not None
    # Auth header is what the probe expects.
    assert calls[0][2]["X-Probe-Token"] == "shared"
    assert calls[0][1]["proxy"] == "socks5://u:p@h.example:1080"


def test_fail_does_not_blank_known_good_ip(db):
    """A transient probe failure must keep the last-known good exit IP — it's
    the most useful breadcrumb for the operator and shouldn't disappear on
    the first hiccup."""
    aid = ar.create(db, {"profile_name": "A",
                         "proxy": "socks5://u:p@h:1080",
                         "proxy_status": "ok",
                         "proxy_exit_ip": "203.0.113.99"})
    summary = pcs.run_proxy_check(db, http_post=lambda *a, **k: _fail())
    assert summary == "0 ok, 1 fail"
    row = ar.get(db, aid)
    assert row["proxy_status"] == "fail"
    assert row["proxy_exit_ip"] == "203.0.113.99"  # preserved


def test_mixed_accounts_counted(db):
    ar.create(db, {"profile_name": "OK1", "proxy": "socks5://u:p@good1:1080"})
    ar.create(db, {"profile_name": "OK2", "proxy": "socks5://u:p@good2:1080"})
    ar.create(db, {"profile_name": "BAD", "proxy": "socks5://u:p@bad:1080"})
    ar.create(db, {"profile_name": "NO_PROXY"})  # ignored

    def fake_post(url, json, headers, timeout):
        return _fail() if "bad" in json["proxy"] else _ok()

    summary = pcs.run_proxy_check(db, http_post=fake_post)
    # All three with proxies were probed; NO_PROXY untouched.
    assert summary == "2 ok, 1 fail"
    statuses = {r["profile_name"]: r["proxy_status"] for r in ar.list_all(db)}
    assert statuses == {"OK1": "ok", "OK2": "ok", "BAD": "fail",
                        "NO_PROXY": None}


def test_check_one_returns_skipped_shape_when_unconfigured(monkeypatch):
    monkeypatch.setattr(cfg, "PROBE_URL", "")
    out = pcs.check_one("socks5://x")
    assert out["ok"] is False
    assert "not configured" in (out["error"] or "")

