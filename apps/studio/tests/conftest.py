"""Pytest fixtures for PG-backed tests.

Test database is `mkt_test` (the DATABASE_URL must contain "test").
A safety check refuses to run against any other database name.

Schema is kept in sync with Alembic migrations — run
  DATABASE_URL=... uv run alembic upgrade head
before the test suite if you see missing-table errors.

Each test starts with TRUNCATED tables (RESTART IDENTITY CASCADE) so
autoincrement ids start at 1, making assertions on ids stable.
"""

import os

os.environ.setdefault("STUDIO_DEFAULT_WORKSPACE_ID", "1")

from alembic.config import Config
from alembic.script import ScriptDirectory

# ---------------------------------------------------------------------------
# Safety check FIRST — before any app/db import that would open a connection.
# ---------------------------------------------------------------------------
_db_url = os.environ.get("DATABASE_URL", "")
if "test" not in _db_url:
    # Override to mkt_test automatically so developers can run `pytest`
    # without manually setting DATABASE_URL each time.
    _fallback = "postgresql+psycopg://Zhuanz@127.0.0.1:5432/mkt_test"
    os.environ["DATABASE_URL"] = _fallback
    _db_url = _fallback

if "test" not in _db_url:
    raise RuntimeError(
        f"DATABASE_URL ({_db_url!r}) doesn't look like a test DB. "
        "Tests refuse to run. Set "
        "DATABASE_URL=postgresql+psycopg://...mkt_test"
    )

# ---------------------------------------------------------------------------
# Now it is safe to import app modules (they read DATABASE_URL at import time).
# ---------------------------------------------------------------------------
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.db import engine
from app.main import app as fastapi_app

# 2026-07-17 登录翻转:studio 的 require_auth 转发 Cookie 到 radar 校验
# (admin-only)。测试里没有 radar——conftest 做两件事保住旧测试的写法:
#   1. monkeypatch app.auth._fetch_radar_session:带 cookie 即返回这个
#      admin 身份(无 cookie 走 require_auth 的快速 401,"未登录 401"
#      断言不受影响);
#   2. 在 app 上挂一个测试专用 POST /api/login stub(见下),旧套件里
#      ~130 处 client.post("/api/login", ...) 零改动——它只负责给
#      TestClient 种一个假 cookie 并回 200。
TEST_WORKSPACE = {
    "id": 1, "kind": "team", "name": "Test Team", "organizationId": 1,
    "role": "owner", "canManageMembers": True, "canManageSecrets": True,
    "monitorQuota": 20,
}
TEST_ADMIN = {"id": 1, "username": "tester", "name": "tester",
              "email": "tester@example.com", "role": "admin",
              "workspace": TEST_WORKSPACE}


# Tables to truncate between tests — order matters for FK constraints but
# RESTART IDENTITY CASCADE handles it automatically.
_TABLES = (
    "publication_attempts, engagement_runs, engagement_tasks, videos, "
    "accounts, groups, caption_prompts, comment_prompts, settings, "
    "job_runs, discovered_videos, geelark_transactions, "
    "geelark_callback_log, callback_inbox, outbox, phone_sessions, "
    "task_runs, cost_attributions, workspace_secrets, users"
)


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Once per pytest session: verify the test DB is at Alembic head.

    This intentionally does not call SQLModel.metadata.create_all(). Alembic
    owns schema creation; creating tables here can mask a stale
    alembic_version and make later migrations fail with duplicate objects.
    """
    config = Config("alembic.ini")
    expected_heads = set(ScriptDirectory.from_config(config).get_heads())
    try:
        with engine.connect() as conn:
            current_versions = {
                row[0]
                for row in conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).all()
            }
    except SQLAlchemyError as exc:
        raise RuntimeError(
            "Test database schema is not initialized with Alembic. "
            "Run `uv run alembic upgrade head` before running tests."
        ) from exc

    if current_versions != expected_heads:
        raise RuntimeError(
            "Test database schema is not at Alembic head. "
            f"Current alembic_version={sorted(current_versions)!r}; "
            f"expected heads={sorted(expected_heads)!r}. "
            "Run `uv run alembic upgrade head` before running tests."
        )
    yield


@pytest.fixture(autouse=True)
def _truncate_tables():
    """Before each test: wipe all rows, reset sequences to 1, then seed
    settings defaults so tests that read settings get the expected values.
    """
    from app.config import SETTINGS_DEFAULTS
    from app.models import Setting

    with engine.begin() as conn:
        conn.execute(text(
            f"TRUNCATE TABLE {_TABLES} RESTART IDENTITY CASCADE"
        ))

    # Re-seed settings defaults after truncation
    with Session(engine) as s:
        for k, v in SETTINGS_DEFAULTS.items():
            s.add(Setting(workspace_id=1, key=k, value=str(v)))
        s.commit()

    yield


_TEST_SESSION_COOKIE = ("better-auth.session_token", "test-session-token")


@pytest.fixture(autouse=True)
def _stub_radar_auth(monkeypatch):
    """require_auth 的网络层替身:任何带 cookie 的请求都是 TEST_ADMIN。

    无 cookie 的请求仍走 require_auth 的快速 401 路径,保住旧套件里
    "未登录 → 401" 的行为断言;每个测试清一次 30s verify 缓存,避免
    跨测试串身份。"""
    from app import auth as studio_auth

    studio_auth._reset_cache()
    monkeypatch.setattr(studio_auth, "_fetch_radar_session",
                        lambda cookie_header: dict(TEST_ADMIN))
    yield
    studio_auth._reset_cache()


def _install_login_stub(app) -> None:
    """测试专用 POST /api/login:旧套件 ~130 处调用零改动。

    生产里该端点已随登录翻转删除(登录在 radar 的 /login);这里只给
    TestClient 种假 cookie + 回 200,让后续请求带 cookie 命中
    _stub_radar_auth 的 admin 身份。"""
    if any(getattr(r, "path", None) == "/api/login" for r in app.routes):
        return

    from fastapi.responses import JSONResponse

    @app.post("/api/login", include_in_schema=False)
    def _test_login(body: dict):
        resp = JSONResponse({"id": TEST_ADMIN["id"],
                             "username": TEST_ADMIN["username"],
                             "email": TEST_ADMIN["email"]})
        name, value = _TEST_SESSION_COOKIE
        resp.set_cookie(name, value, httponly=True, samesite="lax")
        return resp

    # 挪到路由表最前。SPA catch-all 已随前端并入 apps/web 退役(2026-07-18),
    # append 顺序其实已够;保留 insert(0) 作防御——未来再出现 catch-all 时
    # stub 不会被吞成 405。
    app.router.routes.insert(0, app.router.routes.pop())


_install_login_stub(fastapi_app)

# 部分测试文件自建 app(from app.main import create_app)——包一层让每个
# 新建实例都带 login stub。conftest 在任何测试模块之前加载,后续
# from-import 拿到的都是包装后的函数。
import app.main as _app_main  # noqa: E402

_original_create_app = _app_main.create_app


def _create_app_with_login_stub(*args, **kwargs):
    application = _original_create_app(*args, **kwargs)
    _install_login_stub(application)
    return application


_app_main.create_app = _create_app_with_login_stub


@pytest.fixture(autouse=True)
def _auto_bind_caption_prompt(monkeypatch, request):
    """Default group + bound caption prompt + auto-stamped group_id on every
    new video and account, so the rest of the suite keeps exercising
    publish/warmup behaviour without each test having to wire up prompts.

    Behaviour:
      - Inserts one default caption_prompts row + one default groups row
        bound to it at test start.
      - Wraps gr.create so any newly created group is also bound to the
        default prompt (preserves the autouse contract from before).
      - Wraps vid_repo.create and acc_repo.create so they default
        group_id to the default group when the caller didn't pass one
        explicitly.

    Tests that *do* exercise the prompt-missing path opt out via
    @pytest.mark.no_auto_prompt. Tests that bind their own group still
    work because the wrappers honour an explicit group_id.
    """
    if "no_auto_prompt" in request.keywords:
        yield
        return

    from app.repositories import caption_prompts as cp_repo
    from app.repositories import groups as gr_repo
    from app.services import groups_service as gs_svc

    original_gr_create = gr_repo.create

    def wrapped_gr_create(session, *, name, notes=None):
        gid = original_gr_create(session, name=name, notes=notes)
        existing = cp_repo.get_by_name(session, "__test_default__")
        if existing:
            pid = existing["id"]
        else:
            pid = cp_repo.create(
                session, name="__test_default__",
                body="default test caption brief",
            )
        gs_svc.set_caption_prompt(session, gid, pid)
        return gid

    monkeypatch.setattr(gr_repo, "create", wrapped_gr_create)
    yield


@pytest.fixture(autouse=True)
def _undo_procrastinate_task_monkeypatch_leaks():
    """pytest's `monkeypatch.setattr` leaks instance attributes on
    procrastinate Task objects: `configure` and `defer_async` live on the
    Task class and are accessed via descriptor, so monkeypatch's teardown
    `setattr`s the descriptor-resolved value back onto the instance
    instead of `delattr`-ing the override. The instance attr then shadows
    the class method forever, breaking later tests that monkeypatch the
    same chain (fluent `task.configure(...).defer_async(...)` calls hit
    the stale fake_deferrer from the prior test).

    Symptom: a test that mocks defer_async to raise gets HTTP 200 instead
    of 5xx because the leaked configure stub returns its own fake_deferrer
    that swallows the new defer_async monkeypatch.

    Fix: after every test, scrub `configure`/`defer_async` from every
    procrastinate Task's `__dict__`.
    """
    yield
    from app.tasks import procrastinate_app
    for task in procrastinate_app.tasks.values():
        for attr in ("configure", "defer_async"):
            task.__dict__.pop(attr, None)


@pytest.fixture
def db():
    """SQLModel Session for direct DB manipulation in tests.

    Named `db` for backwards-compatibility with the old SQLite fixture.
    """
    with Session(engine) as session:
        yield session


@pytest.fixture
def session(db):
    """Alias for the `db` fixture. New tests can use either name."""
    return db


@pytest.fixture
def client():
    """FastAPI TestClient.

    Each test gets a fresh TestClient wired to the same PG engine as the
    `db` fixture. Tables are wiped by _truncate_tables before each test.
    """
    with TestClient(fastapi_app) as c:
        yield c


@pytest.fixture
def sample_video(db):
    """Insert one Video row and return the model.

    Logs in the global TestClient so it can be hit straight away — tests
    using this fixture share its session by reading from the same engine.
    """
    from app.models import Video

    v = Video(original_filename="sample.mp4",
              stored_path="/tmp/sample.mp4",
              thumbnail_path="thumb/sample.jpg",
              duration_seconds=42)
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@pytest.fixture
def sample_video_with_publication(db):
    """Insert one Video, one Account, and one Publication wired to that
    Account so the per-platform `phone_label` join surfaces non-null.
    """
    from app.models import Account, Publication, Video

    acc = Account(profile_name="PhoneOwner",
                  geelark_phone_id="ph-sample")
    db.add(acc)
    db.flush()

    v = Video(original_filename="sample-with-pub.mp4",
              stored_path="/tmp/sample-with-pub.mp4",
              thumbnail_path="thumb/sample-with-pub.jpg",
              duration_seconds=21)
    db.add(v)
    db.flush()

    p = Publication(video_id=v.id,
                    platform="tiktok",
                    result="PENDING",
                    account_id=acc.id)
    db.add(p)
    db.commit()
    db.refresh(v)
    return v


@pytest.fixture
def procrastinate_app():
    """Procrastinate App backed by InMemoryConnector for tests.

    Replaces the real PsycopgConnector with an in-memory one so tests
    don't queue jobs in PG. Use this fixture's returned app for
    `.defer_async()` calls or to inspect jobs via the connector
    (``procrastinate_app.connector.jobs``).

    Uses the official ``App.replace_connector()`` context-manager API
    (procrastinate 3.x) for clean teardown — restores the original
    connector when the test ends.
    """
    from procrastinate.testing import InMemoryConnector
    from app.tasks import procrastinate_app as real_app

    in_memory = InMemoryConnector()
    with real_app.replace_connector(in_memory):
        yield real_app


@pytest.fixture
def in_memory_procrastinate():
    """InMemoryConnector instance wired into the shared procrastinate_app.

    Exposes `.jobs` directly so tests can assert on deferred task names and
    args without going through `procrastinate_app.connector.jobs`.

    Complements the `procrastinate_app` fixture — use this one when you need
    direct access to the connector's job dict (e.g., task-wiring tests).
    """
    from procrastinate.testing import InMemoryConnector
    from app.tasks import procrastinate_app as real_app

    in_memory = InMemoryConnector()
    with real_app.replace_connector(in_memory):
        yield in_memory


@pytest.fixture
def default_group_id(db):
    """A group already bound to the autouse default caption prompt.
    Use this when a test needs publish/warmup behaviour to "just work"
    and doesn't care about specific group properties."""
    from app.services import groups_service as gs
    return gs.create(db, name="default-test-group")


@pytest.fixture
def warmup_eligible_account_id(db, default_group_id):
    """Create a warmup-eligible Account and return its id.

    Eligibility: tiktok_logged_in=1, geelark_phone_id set, warmup_mode=daily.
    Bound to a group with a caption prompt so publish paths don't block.
    """
    from app.repositories import accounts as acc_repo

    aid = acc_repo.create(db, {
        "profile_name": "warmup-eligible-test",
        "tiktok_logged_in": 1,
        "geelark_phone_id": "phone-test-X",
        "warmup_mode": "daily",
        "warmed_up": 0,
        "group_id": default_group_id,
    })
    return aid


@pytest.fixture
def sample_publication_with_account(db, warmup_eligible_account_id,
                                    default_group_id, tmp_path):
    import pathlib
    from app.models import Video, Publication
    vid_file = tmp_path / "test.mp4"
    vid_file.write_bytes(b"fake-mp4")
    v = Video(stored_path=str(vid_file), original_filename="test.mp4",
              group_id=default_group_id)
    db.add(v); db.commit(); db.refresh(v)
    p = Publication(
        video_id=v.id, account_id=warmup_eligible_account_id,
        platform="tiktok", result="PENDING")
    db.add(p); db.commit(); db.refresh(p)
    return {"publication_id": p.id, "account_id": warmup_eligible_account_id}
