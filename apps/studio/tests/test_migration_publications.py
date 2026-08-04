"""Verify the publications-and-platform-columns migration round-trips
cleanly: upgrade head -> all columns and table present; downgrade -1
-> publications gone, videos columns restored."""
import os
import pytest
from alembic import command
from alembic.config import Config

# Use the dev DB explicitly — this test mutates schema and shouldn't
# run against mkt_test (which truncates per-test).
DEV_URL = os.environ.get(
    "ALEMBIC_TEST_URL",
    "postgresql+psycopg://"+os.environ["USER"]+"@127.0.0.1:5432/mkt_dev_alembic")


def _cfg():
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", DEV_URL)
    return cfg


def _has_table(engine, name):
    import sqlalchemy as sa
    with engine.connect() as c:
        return c.execute(sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name=:n"), {"n": name}).first() is not None


def _has_column(engine, table, column):
    import sqlalchemy as sa
    with engine.connect() as c:
        return c.execute(sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name=:t AND column_name=:c"),
            {"t": table, "c": column}).first() is not None


@pytest.fixture
def alembic_engine():
    """Fresh DB, run migrations to head once."""
    from sqlalchemy import create_engine
    import sqlalchemy as sa
    # Setup: drop+create the dedicated alembic test db
    admin = create_engine(
        DEV_URL.rsplit("/", 1)[0] + "/postgres",
        isolation_level="AUTOCOMMIT")
    db_name = DEV_URL.rsplit("/", 1)[1]
    try:
        with admin.connect() as c:
            c.execute(sa.text(
              f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
            c.execute(sa.text(f"CREATE DATABASE {db_name}"))
    except Exception as exc:
        admin.dispose()
        pytest.skip(f"cannot create test database {db_name}: {exc}")
    admin.dispose()
    engine = create_engine(DEV_URL)
    command.upgrade(_cfg(), "head")
    yield engine
    engine.dispose()


def test_upgrade_head_creates_publications(alembic_engine):
    assert _has_table(alembic_engine, "publications")
    for col in ("youtube_logged_in", "youtube_login_info",
                "last_posted_at_youtube",
                "instagram_logged_in", "instagram_login_info",
                "last_posted_at_instagram",
                "facebook_logged_in", "facebook_login_info",
                "last_posted_at_facebook",
                "warmup_count", "target_warmup_count"):
        assert _has_column(alembic_engine, "accounts", col), col
    for dropped in ("time_spent_warming", "max_warm_time"):
        assert not _has_column(alembic_engine, "accounts", dropped), dropped
    # 69748d477a7f dropped the legacy publishing-state columns from videos.
    # videos.account_id remains as a legacy bulk-picker column, while
    # publications.account_id is the per-platform phone assignment.
    for dropped in ("result", "geelark_task_id",
                    "notes", "posted_at", "last_checked_at"):
        assert not _has_column(alembic_engine, "videos", dropped), dropped
    assert _has_column(alembic_engine, "videos", "account_id")
    assert _has_column(alembic_engine, "publications", "account_id")
    assert _has_column(alembic_engine, "publications", "retry_count")
    assert _has_column(alembic_engine, "publications", "next_retry_at")
    assert _has_table(alembic_engine, "publication_attempts")
    for col in ("publication_id", "attempt_no", "stage", "source",
                "message", "error_code", "fail_screenshot",
                "geelark_task_id", "created_at"):
        assert _has_column(alembic_engine, "publication_attempts", col), col


def test_downgrade_one_restores_videos_columns(alembic_engine):
    command.downgrade(_cfg(), "d7279ba4a07f")
    assert not _has_table(alembic_engine, "publications")
    for restored in ("result", "account_id", "geelark_task_id",
                     "notes", "posted_at", "last_checked_at"):
        assert _has_column(alembic_engine, "videos", restored), restored


def test_idempotent_upgrade_then_downgrade_then_upgrade(alembic_engine):
    command.downgrade(_cfg(), "d7279ba4a07f")
    command.upgrade(_cfg(), "head")
    assert _has_table(alembic_engine, "publications")


def test_upgrade_backfills_tiktok_publication_from_legacy_video(alembic_engine):
    """Going from the procrastinate revision (one revision back) up to
    head must seed a 'tiktok' publication for every existing video,
    carrying its publishing state.

    At head, account_id is available both on the legacy videos column and
    on the platform publication. The round-trip should end with one
    tiktok publication carrying result/caption/task_id/account_id, and
    videos.account_id restored from the legacy column.
    """
    import sqlalchemy as sa
    # Roll back to the procrastinate schema (videos still has the legacy columns).
    command.downgrade(_cfg(), "d7279ba4a07f")
    # Seed one account + one video with publishing state set.
    with alembic_engine.begin() as c:
        c.execute(sa.text(
            "INSERT INTO accounts(id, profile_name, tiktok_logged_in, "
            "pic_status, warmed_up, time_spent_warming, max_warm_time) "
            "VALUES (1, 'A', 0, 'none', 0, 0, 500)"))
        c.execute(sa.text("""
            INSERT INTO videos
              (id, original_filename, stored_path, caption, account_id,
               result, geelark_task_id, notes, posted_at, last_checked_at)
            VALUES (1, 'f.mp4', '/x/f.mp4', 'cap', 1, 'INITIATED',
                    'tk1', 'submitted',
                    '2026-05-01 10:00:00', '2026-05-01 10:01:00')
        """))
    # Re-upgrade — the backfill chain should run.
    command.upgrade(_cfg(), "head")
    with alembic_engine.connect() as c:
        rows = c.execute(sa.text(
            "SELECT video_id, platform, account_id, result, caption, "
            "geelark_task_id, notes, posted_at, last_checked_at "
            "FROM publications")).all()
        vrow = c.execute(sa.text(
            "SELECT account_id FROM videos WHERE id=1")).first()
    assert len(rows) == 1
    r = rows[0]
    assert r[0] == 1                  # video_id
    assert r[1] == "tiktok"
    assert r[2] == 1                  # publication.account_id
    assert r[3] == "INITIATED"        # result
    assert r[4] == "cap"
    assert r[5] == "tk1"
    assert r[6] == "submitted"
    assert r[7] is not None           # posted_at
    assert r[8] is not None           # last_checked_at
    assert vrow[0] == 1               # videos.account_id round-tripped
