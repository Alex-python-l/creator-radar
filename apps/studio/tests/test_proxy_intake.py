import pytest


def test_parse_lines_valid_url():
    from app.services import proxy_intake as pi
    out = pi.parse_lines("socks5://u:p@1.2.3.4:1080")
    assert out == [{"line_no": 1, "proxy": "socks5://u:p@1.2.3.4:1080",
                    "error": None}]


# --- normalize_proxy: shapes commonly pasted from proxy vendors --------------

def test_normalize_userinfo_at_host_port_defaults_to_socks5():
    """user:pass@host:port — what kookeey.info and most vendors give you."""
    from app.services import proxy_intake as pi
    assert pi.normalize_proxy("984a4549:6eca3dd4@ph353.kookeey.info:22795") \
        == "socks5://984a4549:6eca3dd4@ph353.kookeey.info:22795"


def test_normalize_host_port_no_auth():
    from app.services import proxy_intake as pi
    assert pi.normalize_proxy("1.2.3.4:1080") == "socks5://1.2.3.4:1080"


def test_normalize_four_segment_host_port_user_pass():
    """1.2.3.4:8080:user:pass — the IP:Port:User:Pass vendor format."""
    from app.services import proxy_intake as pi
    assert pi.normalize_proxy("1.2.3.4:8080:user:pass") \
        == "socks5://user:pass@1.2.3.4:8080"


def test_normalize_four_segment_user_pass_host_port():
    from app.services import proxy_intake as pi
    assert pi.normalize_proxy("user:pass:1.2.3.4:8080") \
        == "socks5://user:pass@1.2.3.4:8080"


def test_normalize_keeps_existing_scheme():
    from app.services import proxy_intake as pi
    assert pi.normalize_proxy("http://1.2.3.4:8080") == "http://1.2.3.4:8080"
    assert pi.normalize_proxy("socks5h://u:p@h.example:1080") \
        == "socks5h://u:p@h.example:1080"


def test_normalize_rejects_unknown_scheme():
    from app.services import proxy_intake as pi
    assert pi.normalize_proxy("ftp://1.2.3.4:21") is None


def test_normalize_rejects_garbage():
    from app.services import proxy_intake as pi
    assert pi.normalize_proxy("not-a-url") is None
    assert pi.normalize_proxy("") is None
    assert pi.normalize_proxy("   ") is None


def test_normalize_strips_whitespace():
    from app.services import proxy_intake as pi
    assert pi.normalize_proxy("  1.2.3.4:1080  ") == "socks5://1.2.3.4:1080"


def test_normalize_percent_encodes_special_chars_in_userinfo():
    """Passwords with @ or : must be encoded or the URL parser misreads them."""
    from app.services import proxy_intake as pi
    assert pi.normalize_proxy("u:p@ss@1.2.3.4:1080") \
        == "socks5://u:p%40ss@1.2.3.4:1080"


# --- parse_lines now accepts the normalized shapes --------------------------

def test_parse_lines_accepts_userinfo_at_host_port_form():
    from app.services import proxy_intake as pi
    out = pi.parse_lines("984a4549:6eca3dd4@ph353.kookeey.info:22795")
    assert out[0]["error"] is None
    assert out[0]["proxy"] \
        == "socks5://984a4549:6eca3dd4@ph353.kookeey.info:22795"


def test_parse_lines_accepts_four_segment_form():
    from app.services import proxy_intake as pi
    out = pi.parse_lines("1.2.3.4:8080:user:pass")
    assert out[0]["error"] is None
    assert out[0]["proxy"] == "socks5://user:pass@1.2.3.4:8080"


# --- check_batch normalizes before probing and returns the normalized form --

def test_check_batch_normalizes_inputs_before_probing():
    """The probe service can't dial a schemeless URL — check_batch must
    rewrite the input first AND echo the normalized form back so the UI
    persists the form the rest of the system understands."""
    from app.services import proxy_intake as pi
    seen = []

    def fake_check(proxy):
        seen.append(proxy)
        return {"ok": True, "exit_ip": "9.9.9.9",
                "latency_ms": 100, "error": None}

    out = pi.check_batch(
        ["984a4549:6eca3dd4@ph353.kookeey.info:22795", "1.2.3.4:8080:u:p"],
        check_one=fake_check)
    assert seen == [
        "socks5://984a4549:6eca3dd4@ph353.kookeey.info:22795",
        "socks5://u:p@1.2.3.4:8080",
    ]
    assert [r["proxy"] for r in out] == [
        "socks5://984a4549:6eca3dd4@ph353.kookeey.info:22795",
        "socks5://u:p@1.2.3.4:8080",
    ]
    assert all(r["ok"] for r in out)


def test_check_batch_unrecognized_input_reports_error_without_probing():
    from app.services import proxy_intake as pi
    called = []

    def fake_check(proxy):
        called.append(proxy)
        return {"ok": True, "exit_ip": None, "latency_ms": 1, "error": None}

    out = pi.check_batch(["not-a-url"], check_one=fake_check)
    assert called == []
    assert out[0]["proxy"] == "not-a-url"  # echo original so UI shows what user typed
    assert out[0]["ok"] is False
    assert out[0]["error"] and "format" in out[0]["error"].lower()


def test_parse_lines_blank_lines_skipped():
    from app.services import proxy_intake as pi
    out = pi.parse_lines("\n\nhttp://1.1.1.1:8080\n\n")
    assert len(out) == 1
    assert out[0]["line_no"] == 3


def test_parse_lines_invalid_url_returns_error_not_raises():
    from app.services import proxy_intake as pi
    out = pi.parse_lines("not-a-url")
    assert out[0]["proxy"] == "not-a-url"
    assert out[0]["error"] is not None


def test_parse_lines_accepts_no_userinfo():
    from app.services import proxy_intake as pi
    out = pi.parse_lines("socks5://10.0.0.1:1080")
    assert out[0]["error"] is None


def test_check_batch_calls_check_one_per_proxy():
    from app.services import proxy_intake as pi
    calls = []

    def fake_check(proxy):
        calls.append(proxy)
        return {"ok": True, "exit_ip": "9.9.9.9",
                "latency_ms": 100, "error": None}

    out = pi.check_batch(["socks5://a:1080", "socks5://b:1080"],
                          check_one=fake_check)
    assert len(out) == 2
    assert all(r["ok"] for r in out)
    assert sorted(calls) == ["socks5://a:1080", "socks5://b:1080"]


def test_check_batch_per_proxy_failure_is_isolated():
    from app.services import proxy_intake as pi

    def fake_check(proxy):
        if "b" in proxy:
            raise RuntimeError("blew up")
        return {"ok": True, "exit_ip": "1.1.1.1",
                "latency_ms": 50, "error": None}

    out = pi.check_batch(["socks5://a:1080", "socks5://b:1080"],
                          check_one=fake_check)
    by_proxy = {r["proxy"]: r for r in out}
    assert by_proxy["socks5://a:1080"]["ok"] is True
    assert by_proxy["socks5://b:1080"]["ok"] is False
    assert "blew up" in by_proxy["socks5://b:1080"]["error"]
