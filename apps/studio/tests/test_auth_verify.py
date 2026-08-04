"""require_auth — Radar 工作空间实时校验语义。

会话真源在 radar(better-auth,同域 cookie);studio 只转发 Cookie 头到
RADAR_AUTH_VERIFY_URL 并要求 role=admin。网络层 _fetch_radar_session 在这里
按用例覆写(conftest 的 autouse stub 提供默认 admin 替身)。
"""
import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from app import auth as studio_auth


class _Req:
    """require_auth 只读 request.headers['cookie'] 的最小替身。"""

    def __init__(self, cookie: str | None):
        self.headers = {} if cookie is None else {"cookie": cookie}
        self.state = SimpleNamespace()


def _payload(role="member"):
    return {
        "user": {"id": 7, "username": "will"},
        "workspace": {
            "id": 11, "kind": "team", "name": "T", "role": role,
            "canManageMembers": role in {"owner", "admin"},
            "canManageSecrets": role in {"owner", "admin"},
        },
    }


def _call(cookie, fetch, monkeypatch):
    monkeypatch.setattr(studio_auth, "_fetch_radar_session", fetch)
    studio_auth._reset_cache()
    return studio_auth.require_auth(_Req(cookie))  # type: ignore[arg-type]


def test_admin_session_passes(monkeypatch):
    user = _call("s=1", lambda _: _payload("admin"),
                 monkeypatch)
    assert user["id"] == 7


def test_no_cookie_is_fast_401(monkeypatch):
    """无 cookie 不打网络:radar 不可达也能明确回 401。"""

    def boom(_):
        raise AssertionError("must not hit the network without a cookie")

    with pytest.raises(HTTPException) as e:
        _call(None, boom, monkeypatch)
    assert e.value.status_code == 401


def test_logged_out_session_401(monkeypatch):
    """radar 200 + null(未登录)→ 401。"""
    with pytest.raises(HTTPException) as e:
        _call("s=1", lambda _: None, monkeypatch)
    assert e.value.status_code == 401


def test_missing_workspace_fails_closed(monkeypatch):
    with pytest.raises(HTTPException) as e:
        _call("s=1", lambda _: {"id": 8, "username": "pub", "role": "user"},
              monkeypatch)
    assert e.value.status_code == 503


def test_radar_unreachable_503(monkeypatch):
    """radar 故障必须 fail-closed(503),绝不放行也不误判成未登录。"""

    def down(_):
        raise RuntimeError("connect refused")

    with pytest.raises(HTTPException) as e:
        _call("s=1", down, monkeypatch)
    assert e.value.status_code == 503


def test_success_is_revalidated_every_time(monkeypatch):
    calls = {"n": 0}

    def counting(_):
        calls["n"] += 1
        return _payload()

    monkeypatch.setattr(studio_auth, "_fetch_radar_session", counting)
    studio_auth._reset_cache()
    studio_auth.require_auth(_Req("s=1"))  # type: ignore[arg-type]
    studio_auth.require_auth(_Req("s=1"))  # type: ignore[arg-type]
    assert calls["n"] == 2

    studio_auth.require_auth(_Req("s=2"))  # type: ignore[arg-type]
    assert calls["n"] == 3


def test_failed_lookup_not_cached(monkeypatch):
    """未登录结果不缓存:登录动作后必须立刻生效,不用等 TTL。"""
    state = {"user": None}

    monkeypatch.setattr(studio_auth, "_fetch_radar_session",
                        lambda _: state["user"])
    studio_auth._reset_cache()
    with pytest.raises(HTTPException):
        studio_auth.require_auth(_Req("s=1"))  # type: ignore[arg-type]

    state["user"] = _payload()
    user = studio_auth.require_auth(_Req("s=1"))  # type: ignore[arg-type]
    assert user["username"] == "will"


def test_me_endpoint_shape(client):
    """GET /api/me 保持旧形状 {id, username, email}(SPA 登录态探测)。"""
    client.post("/api/login", json={"username": "tester", "password": "pw"})
    r = client.get("/api/me")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"id", "username", "email"}
    assert body["username"] == "tester"


def test_me_401_without_cookie(client):
    assert client.get("/api/me").status_code == 401
