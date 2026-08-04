"""SQLModel definitions for the mkt persistence layer.

Generated from the SQLite schema in app/db.py SCHEMA + _migrate().
All datetime fields are stored as PostgreSQL TIMESTAMP WITHOUT TIME ZONE
(matches SQLite TEXT 'YYYY-MM-DD HH:MM:SS' semantics — datetime.utcnow()).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (BigInteger, Column, DateTime, ForeignKey, Index, Integer,
                        JSON, LargeBinary, Numeric, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _utcnow_col():
    """Server-side default for created_at / updated_at columns."""
    return {"server_default": text("CURRENT_TIMESTAMP")}


class Group(SQLModel, table=True):
    __tablename__ = "groups"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    notes: Optional[str] = None
    caption_prompt_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("caption_prompts.id", ondelete="SET NULL"),
        ),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class CaptionPrompt(SQLModel, table=True):
    __tablename__ = "caption_prompts"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, nullable=False)
    body: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class CommentPrompt(SQLModel, table=True):
    __tablename__ = "comment_prompts"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, nullable=False)
    body: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class Account(SQLModel, table=True):
    __tablename__ = "accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    geelark_phone_id: Optional[str] = Field(default=None, unique=True, index=True)
    profile_name: Optional[str] = Field(default=None, unique=True, index=True)
    email: Optional[str] = None
    tiktok_login_info: Optional[str] = None
    proxy: Optional[str] = None
    profile_pic_url: Optional[str] = None
    profile_pic_path: Optional[str] = None
    group_name: Optional[str] = None  # legacy, kept for backfill; group_id is the truth
    group_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("groups.id", ondelete="SET NULL")),
    )
    tiktok_logged_in: int = Field(default=0)
    pic_status: str = Field(default="none")
    pic_task_id: Optional[str] = None
    warmed_up: int = Field(default=0)
    warmup_count: int = Field(default=0)
    target_warmup_count: int = Field(default=10)
    warmup_mode: str = Field(default="count", nullable=False, max_length=16)
    last_warm_at: Optional[datetime] = None
    last_warm_result: Optional[str] = None
    last_warm_notes: Optional[str] = None
    last_posted_at: Optional[datetime] = None
    # YouTube (added 2026-05-28 multi-platform)
    youtube_logged_in: int = Field(default=0, nullable=False)
    youtube_login_info: Optional[str] = None
    last_posted_at_youtube: Optional[datetime] = None

    # Instagram (added 2026-05-28 multi-platform)
    instagram_logged_in: int = Field(default=0, nullable=False)
    instagram_login_info: Optional[str] = None
    last_posted_at_instagram: Optional[datetime] = None

    # Facebook (added 2026-05-28 multi-platform)
    facebook_logged_in: int = Field(default=0, nullable=False)
    facebook_login_info: Optional[str] = None
    last_posted_at_facebook: Optional[datetime] = None
    proxy_status: Optional[str] = None
    proxy_checked_at: Optional[datetime] = None
    proxy_exit_ip: Optional[str] = None
    proxy_expires_at: Optional[datetime] = None
    proxy_country: Optional[str] = None
    proxy_region: Optional[str] = None
    password: Optional[str] = None
    tiktok_password: Optional[str] = None
    recovery_email: Optional[str] = None
    mail_inbox_url: Optional[str] = None
    raw_credentials: Optional[str] = None
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class Video(SQLModel, table=True):
    __tablename__ = "videos"
    __table_args__ = (
        Index("idx_videos_media_kind", "media_kind"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    original_filename: Optional[str] = None
    stored_path: Optional[str] = None
    caption: Optional[str] = None
    group_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("groups.id", ondelete="SET NULL")),
    )
    account_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer,
                          ForeignKey("accounts.id", ondelete="SET NULL"),
                          nullable=True))
    drive_file_id: Optional[str] = None
    thumbnail_path: Optional[str] = None
    duration_seconds: Optional[int] = None
    media_kind: str = Field(
        default="video",
        sa_column=Column(String(length=16), nullable=False, server_default="video"),
    )
    uploaded_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    understanding: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB(), nullable=True),
    )
    understanding_status: str = Field(
        default="pending",
        sa_column=Column(String(length=20), nullable=False,
                         server_default="pending"),
    )


class Publication(SQLModel, table=True):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint("video_id", "platform",
                          name="uq_publications_video_platform"),
        Index("idx_publications_status", "result", "platform"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(
        sa_column=Column(Integer,
                          ForeignKey("videos.id", ondelete="CASCADE"),
                          nullable=False))
    platform: str = Field(nullable=False)
    account_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer,
                          ForeignKey("accounts.id", ondelete="SET NULL"),
                          nullable=True))
    # Per-platform publishing state. The account (which GeeLark phone
    # publishes this item) is per publication, so sibling platforms for
    # the same video can use different cloud phones.
    result: str = Field(default="PENDING", nullable=False)
    caption: Optional[str] = None
    geelark_task_id: Optional[str] = None
    notes: Optional[str] = None
    posted_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    # Failure diagnostics captured from GeeLark when a publication goes to
    # FAILURE (see app/infra/platforms/base.py failure_outcome_from_detail): the numeric
    # failCode, and the URL of the last RPA screenshot — i.e. where the
    # automation got stuck on the cloud phone.
    fail_code: Optional[int] = None
    fail_screenshot: Optional[str] = None
    retry_count: int = Field(default=0, nullable=False)
    next_retry_at: Optional[datetime] = None
    share_link: Optional[str] = None
    share_link_captured_at: Optional[datetime] = None
    created_at: datetime = Field(
        sa_column=Column(DateTime,
                          server_default=text("CURRENT_TIMESTAMP"),
                          nullable=False))


class PublicationAttempt(SQLModel, table=True):
    __tablename__ = "publication_attempts"
    __table_args__ = (
        Index("idx_publication_attempts_publication",
              "publication_id", "attempt_no"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    publication_id: int = Field(
        sa_column=Column(Integer,
                         ForeignKey("publications.id", ondelete="CASCADE"),
                         nullable=False))
    attempt_no: int = Field(nullable=False)
    stage: str = Field(nullable=False)
    source: str = Field(nullable=False)
    message: str = Field(nullable=False)
    error_code: Optional[str] = None
    fail_screenshot: Optional[str] = None
    geelark_task_id: Optional[str] = None
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class EngagementTask(SQLModel, table=True):
    __tablename__ = "engagement_tasks"
    __table_args__ = (
        Index("idx_engagement_tasks_group_status", "group_id", "status"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    group_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("groups.id", ondelete="SET NULL")),
    )
    action: str = Field(nullable=False)
    source: str = Field(nullable=False)
    target_links: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default=text("'[]'::json")),
    )
    target_publication_ids: list[int] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, server_default=text("'[]'::json")),
    )
    comment_probability: int = Field(default=100, nullable=False)
    comment_mode: str = Field(default="geelark_ai", nullable=False)
    comments_per_video: int = Field(default=1, nullable=False, sa_column_kwargs={"server_default": "1"})
    comment_prompt_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("comment_prompts.id", ondelete="SET NULL"),
        ),
    )
    product_info: Optional[str] = Field(default=None, sa_column=Column(Text))
    comment_brief: Optional[str] = Field(default=None, sa_column=Column(Text))
    platform: str = Field(
        default="tiktok",
        nullable=False,
        sa_column_kwargs={"server_default": "tiktok"},
    )
    keywords: list[str] = Field(
        default_factory=list,
        sa_column=Column(
            JSON, nullable=False, server_default=text("'[]'::json"),
        ),
    )
    status: str = Field(default="PENDING", nullable=False)
    created_by: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    finished_at: Optional[datetime] = None


class EngagementRun(SQLModel, table=True):
    __tablename__ = "engagement_runs"
    __table_args__ = (
        Index("idx_engagement_runs_task", "engagement_task_id"),
        Index("idx_engagement_runs_account_result", "account_id", "result"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_task_id: int = Field(
        sa_column=Column(Integer,
                         ForeignKey("engagement_tasks.id",
                                    ondelete="CASCADE"),
                         nullable=False)
    )
    account_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL")),
    )
    target_publication_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer,
                         ForeignKey("publications.id", ondelete="SET NULL")),
    )
    target_account_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL")),
    )
    target_link: str = Field(nullable=False)
    action: str = Field(nullable=False)
    target_title: Optional[str] = Field(default=None, sa_column=Column(Text))
    target_description: Optional[str] = Field(default=None, sa_column=Column(Text))
    comment_text: Optional[str] = Field(default=None, sa_column=Column(Text))
    comment_source: Optional[str] = None
    comment_generated_at: Optional[datetime] = None
    comment_candidates: Optional[list[str]] = Field(
        default=None, sa_column=Column(JSON, nullable=True),
    )
    result: str = Field(default="PENDING", nullable=False)
    geelark_task_id: Optional[str] = None
    schedule_at: Optional[int] = None
    notes: Optional[str] = None
    fail_code: Optional[int] = None
    fail_screenshot: Optional[str] = None
    result_screenshot: Optional[str] = None
    cost: Optional[int] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    workspace_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger,
            primary_key=True,
            server_default=text(
                "COALESCE(NULLIF(current_setting('app.workspace_id', true), '')::bigint, 1)"
            ),
        ),
    )
    key: str = Field(primary_key=True)
    value: Optional[str] = None


class WorkspaceSecret(SQLModel, table=True):
    __tablename__ = "workspace_secrets"

    workspace_id: int = Field(primary_key=True)
    key: str = Field(primary_key=True, max_length=64)
    ciphertext: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    nonce: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    key_fingerprint: str = Field(max_length=64, nullable=False)
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class JobRun(SQLModel, table=True):
    __tablename__ = "job_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    job_type: Optional[str] = None
    started_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    finished_at: Optional[datetime] = None
    status: str = Field(default="running")
    summary: Optional[str] = None


class GeelarkTransaction(SQLModel, table=True):
    __tablename__ = "geelark_transactions"
    __table_args__ = (
        Index("idx_geelark_tx_env_time", "env_id",
              text("geelark_created_at DESC")),
        Index("idx_geelark_tx_time", text("geelark_created_at DESC")),
    )

    workspace_id: int = Field(primary_key=True)
    id: str = Field(primary_key=True, max_length=32)
    env_id: str = Field(nullable=False, max_length=32)
    amount: float = Field(
        sa_column=Column(Numeric(precision=10, scale=4), nullable=False),
    )
    used_time_min: int = Field(nullable=False)
    type: int = Field(nullable=False)
    charge_type: int = Field(nullable=False)
    geelark_created_at: datetime = Field(nullable=False)
    synced_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class DiscoveredVideo(SQLModel, table=True):
    __tablename__ = "discovered_videos"
    __table_args__ = (
        UniqueConstraint("url", name="uq_discovered_videos_url"),
        Index("idx_discovered_videos_play_count", "play_count"),
        Index("idx_discovered_videos_create_time", "create_time"),
        Index("idx_discovered_videos_last_seen_at", "last_seen_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(nullable=False)
    video_id: str = Field(nullable=False)
    author_unique_id: Optional[str] = None
    author_nickname: Optional[str] = None
    author_followers: Optional[int] = None
    title: Optional[str] = Field(default=None, sa_column=Column(Text))
    play_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    collect_count: Optional[int] = None
    thumbnail_url: Optional[str] = Field(default=None, sa_column=Column(Text))
    create_time: Optional[datetime] = None
    search_keyword: str = Field(nullable=False)
    country: Optional[str] = None
    discovered_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    last_seen_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class PhoneSession(SQLModel, table=True):
    """Cloud-phone-runtime ledger — the single source of truth for
    "this phone is held by this business right now."

    Wired into publish/warmup/engagement via PhoneRuntimeManager
    (app/services/phone_runtime.py). Idle and overdue detection in
    idle_phone_sweeper / overdue_session_watchdog read from this
    table directly.
    """
    __tablename__ = "phone_sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(nullable=False)
    env_id: str = Field(nullable=False, max_length=64)
    # 'publish' | 'warmup' | 'engagement' | 'manual' | 'external_detected'
    source: str = Field(nullable=False, max_length=32)
    owner_type: Optional[str] = Field(default=None, max_length=32)
    owner_id: Optional[int] = None
    geelark_task_id: Optional[str] = Field(default=None, max_length=64)
    started_at: datetime = Field(nullable=False)
    budget_deadline_at: datetime = Field(nullable=False)
    stop_requested_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    # Runtime state — covers the in-flight middle that final_status can't
    # express: ACTIVE / STOPPING / STOPPED / TIMEOUT / STOP_VERIFY_FAILED.
    status: str = Field(nullable=False, max_length=32)
    # Only populated at terminal: STOPPED / TIMEOUT / FAILED. NULL until
    # stop_requested_at, so reports can SELECT WHERE final_status IS NOT NULL.
    final_status: Optional[str] = Field(default=None, max_length=32)
    stop_reason: Optional[str] = Field(default=None, sa_column=Column(Text))
    last_observed_status: Optional[str] = Field(
        default=None, max_length=32,
    )
    cost_transaction_id: Optional[str] = Field(default=None, max_length=64)
    created_by: str = Field(nullable=False, max_length=32)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class TaskRun(SQLModel, table=True):
    """Business-side state machine (2026-06-13 spec Phase 2).

    Pairs 1:1 with a PhoneSession in the common path but lives in its
    own table so the two lifecycles can diverge in failure modes
    (e.g. task_run TIMEOUT while phone_session is still STOPPING).
    """
    __tablename__ = "task_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(nullable=False, max_length=32)
    ref_type: str = Field(nullable=False, max_length=32)
    ref_id: int = Field(nullable=False)
    account_id: int = Field(nullable=False)
    phone_session_id: Optional[int] = None
    geelark_task_id: Optional[str] = Field(default=None, max_length=64)
    state: str = Field(nullable=False, max_length=32)
    attempt: int = Field(default=1, nullable=False)
    planned_at: datetime = Field(nullable=False)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    deadline_at: Optional[datetime] = None
    failure_reason: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class VideoAsset(SQLModel, table=True):
    __tablename__ = "video_assets"
    __table_args__ = (
        UniqueConstraint(
            "video_id", "idx", name="uq_video_assets_video_idx",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    idx: int = Field(nullable=False)
    stored_path: str = Field(sa_column=Column(Text, nullable=False))
    original_filename: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )


class User(SQLModel, table=True):
    """DEPRECATED(2026-07-17 登录翻转):登录已移到 radar 的 better-auth,
    studio 不再读写本表。模型保留仅为 (a) Alembic 历史/autogenerate 不误删
    mkt.users,(b) scripts/migrate-users-to-radar.mjs 的数据来源说明。
    切勿在新代码引用。"""
    __tablename__ = "users"
    # Case-insensitive uniqueness; functional indexes (not unique columns)
    # so the original casing is kept for display. Mirror the indexes in
    # the users / users-email migrations.
    __table_args__ = (
        Index("uq_users_username_lower", text("lower(username)"), unique=True),
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(sa_column=Column(String(length=32), nullable=False))
    # Collected at registration; not verified yet (verification / password
    # reset are future work that will key off this column).
    email: str = Field(sa_column=Column(String(length=255), nullable=False))
    # 'scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>' — see app.auth.hash_password.
    password_hash: str = Field(sa_column=Column(Text, nullable=False))
    # Embedded in every session cookie; bumped on password change so all
    # previously issued cookies for this user stop validating.
    session_ver: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default="1"),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs=_utcnow_col(),
    )
