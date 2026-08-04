"""Orchestrate the daily TikTok-discovery cycle.

Reads settings → for each keyword: BD trigger → poll → filter → upsert.
Then prunes stale rows. Surfaces stats for JobRun logging.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, select

from app.config import SETTINGS_DEFAULTS
from app.models import DiscoveredVideo, EngagementRun
from app.repositories import settings as srepo


@dataclass(frozen=True)
class DiscoverConfig:
    keywords: list[str]
    min_views: int
    window_days: int
    retain_days: int
    max_per_keyword: int


def _parse_settings(session: Session) -> DiscoverConfig:
    raw = srepo.get_all(session)
    def _get(key: str) -> str:
        return (raw.get(key) or SETTINGS_DEFAULTS[key]).strip()

    keywords_raw = _get("tiktok_discover_keywords")
    keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]

    return DiscoverConfig(
        keywords=keywords,
        min_views=int(_get("tiktok_discover_min_views")),
        window_days=int(_get("tiktok_discover_window_days")),
        retain_days=int(_get("tiktok_discover_retain_days")),
        max_per_keyword=int(_get("tiktok_discover_max_per_keyword")),
    )


@dataclass
class DiscoveredVideoDraft:
    url: str
    video_id: str
    author_unique_id: str | None
    author_nickname: str | None
    author_followers: int | None
    title: str | None
    play_count: int | None
    like_count: int | None
    comment_count: int | None
    share_count: int | None
    collect_count: int | None
    thumbnail_url: str | None
    create_time: datetime | None
    search_keyword: str
    country: str | None


_AUTHOR_RE = re.compile(r"tiktok\.com/@([^/?#]+)")


def _parse_author_unique_id(profile_url: str | None) -> str | None:
    if not profile_url:
        return None
    m = _AUTHOR_RE.search(profile_url)
    return m.group(1) if m else None


def _parse_create_time(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        # BD format: "2026-04-18T13:37:28.000Z"
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _filter_and_normalize(
    records: list[dict],
    keyword: str,
    *,
    min_views: int,
    window_days: int,
    max_per_keyword: int,
    now: datetime | None = None,
) -> list[DiscoveredVideoDraft]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    seen_ids: set[str] = set()
    out: list[DiscoveredVideoDraft] = []
    for r in records:
        url = r.get("post_url")
        if not url:
            continue
        post_id = str(r.get("post_id") or "")
        if not post_id or post_id in seen_ids:
            continue
        play_count = r.get("playCount") or 0
        if play_count < min_views:
            continue
        ct = _parse_create_time(r.get("create_time"))
        if ct is None or ct < cutoff:
            continue
        seen_ids.add(post_id)
        out.append(DiscoveredVideoDraft(
            url=url,
            video_id=post_id,
            author_unique_id=_parse_author_unique_id(r.get("profile_url")),
            author_nickname=r.get("profile_nickname"),
            author_followers=r.get("profile_followers"),
            title=r.get("description"),
            play_count=play_count,
            like_count=r.get("diggCount"),
            comment_count=r.get("commentCount"),
            share_count=r.get("shareCount"),
            collect_count=r.get("collectCount"),
            thumbnail_url=r.get("thumbnail"),
            create_time=ct,
            search_keyword=keyword,
            country=r.get("country"),
        ))
        if len(out) >= max_per_keyword:
            break
    return out


def _draft_row(d: DiscoveredVideoDraft) -> dict:
    row = asdict(d)
    # discovered_at / last_seen_at handled by DB defaults
    return row


def _upsert_videos(
    session: Session,
    drafts: list[DiscoveredVideoDraft],
) -> tuple[int, int]:
    if not drafts:
        return (0, 0)
    tbl = DiscoveredVideo.__table__
    rows = [_draft_row(d) for d in drafts]
    existing_urls = set(session.exec(
        select(DiscoveredVideo.url).where(
            DiscoveredVideo.url.in_([d.url for d in drafts])
        )
    ).all())
    stmt = pg_insert(tbl).values(rows)
    excluded = stmt.excluded
    overwrite_keys = [
        "play_count", "like_count", "comment_count",
        "share_count", "collect_count",
        "author_followers", "country", "video_id",
    ]
    coalesce_keys = ["title", "thumbnail_url", "author_nickname",
                     "author_unique_id"]
    set_ = {k: getattr(excluded, k) for k in overwrite_keys}
    for k in coalesce_keys:
        set_[k] = func.coalesce(getattr(excluded, k), tbl.c[k])
    set_["last_seen_at"] = func.now()
    stmt = stmt.on_conflict_do_update(
        index_elements=["workspace_id", "url"], set_=set_
    )
    session.execute(stmt)
    session.commit()
    inserted = sum(1 for d in drafts if d.url not in existing_urls)
    updated = len(drafts) - inserted
    return (inserted, updated)


def _prune_stale(session: Session, retain_days: int) -> int:
    tbl = DiscoveredVideo.__table__
    cutoff_clause = text(f"now() - interval '{int(retain_days)} days'")
    stmt = delete(tbl).where(
        tbl.c.last_seen_at < cutoff_clause,
        ~exists().where(EngagementRun.target_link == tbl.c.url),
    )
    result = session.execute(stmt)
    session.commit()
    return result.rowcount or 0


logger = logging.getLogger(__name__)


@dataclass
class DiscoveryStats:
    keywords_processed: int = 0
    keywords_failed: int = 0
    failed_keywords: list[str] = None
    inserted: int = 0
    updated: int = 0
    pruned: int = 0

    def __post_init__(self):
        if self.failed_keywords is None:
            self.failed_keywords = []

    def __str__(self):
        return (
            f"kw_ok={self.keywords_processed} kw_fail={self.keywords_failed} "
            f"ins={self.inserted} upd={self.updated} pruned={self.pruned} "
            f"failed={self.failed_keywords}"
        )


def _bucket_by_keyword(
    records: list[dict], keywords: list[str]
) -> dict[str, list[dict]]:
    """Group BD result rows by the search_keyword they came from.

    BD echoes the input search_keyword on each output record, so we trust
    that. Rows with an unrecognized keyword (e.g. BD rewrote it) are
    bucketed under the first configured keyword as a safety net.
    """
    valid = set(keywords)
    fallback = keywords[0] if keywords else ""
    buckets: dict[str, list[dict]] = {kw: [] for kw in keywords}
    for r in records:
        kw = (r.get("search_keyword") or "").strip()
        if kw not in valid:
            kw = fallback
        buckets.setdefault(kw, []).append(r)
    return buckets


# Bright Data's datasets/v3 /trigger accepts at most 100 inputs per POST.
# A merged multi-vertical keyword list (e.g. IELTS 45 + ArenaPlus 73 = 118)
# must be split into ≤100-keyword batches, each its own snapshot.
BD_TRIGGER_MAX = 100


def _chunk_keywords(keywords: list[str], size: int):
    for i in range(0, len(keywords), size):
        yield keywords[i:i + size]


def run_discovery_cycle(session: Session, client) -> DiscoveryStats:
    cfg = _parse_settings(session)
    stats = DiscoveryStats()
    if not cfg.keywords:
        return stats

    # Try batch trigger first (1 BD call for all 45 keywords). Fall back to
    # per-keyword loop if the client lacks the batch method (older tests).
    if hasattr(client, "trigger_keyword_searches"):
        # BD caps /trigger at 100 inputs, so split into batches. Each batch is
        # an independent snapshot: one batch failing marks only its own
        # keywords failed and the loop continues — an early return here would
        # silently drop every keyword in the later batches.
        for chunk in _chunk_keywords(cfg.keywords, BD_TRIGGER_MAX):
            snap = None
            try:
                snap = client.trigger_keyword_searches(chunk)
                # BD charges at collection time — log the snapshot id so a
                # failed download can be recovered for free later via
                # scripts/ingest_bd_snapshot.py.
                logger.info("discover batch snapshot_id=%s", snap)
                # 100-keyword chunks routinely exceed the old 3600s default —
                # scripts/discover_now.py already runs with 7200s from
                # field experience; keep the periodic path in sync so a
                # paid snapshot isn't abandoned as failed at the 1h mark.
                records = client.poll_until_ready(
                    snap, max_wait_seconds=7200)
            except Exception as e:
                logger.warning(
                    "discover batch failed (snapshot_id=%s, recover with "
                    "scripts/ingest_bd_snapshot.py): %s", snap, e,
                )
                stats.keywords_failed += len(chunk)
                stats.failed_keywords.extend(chunk)
                continue
            for kw, rows in _bucket_by_keyword(records, chunk).items():
                try:
                    drafts = _filter_and_normalize(
                        rows, kw,
                        min_views=cfg.min_views,
                        window_days=cfg.window_days,
                        max_per_keyword=cfg.max_per_keyword,
                    )
                    ins, upd = _upsert_videos(session, drafts)
                    stats.inserted += ins
                    stats.updated += upd
                    stats.keywords_processed += 1
                except Exception as e:
                    stats.keywords_failed += 1
                    stats.failed_keywords.append(kw)
                    logger.warning(
                        "discover keyword %s upsert failed: %s", kw, e
                    )
    else:
        for kw in cfg.keywords:
            try:
                snap = client.trigger_keyword_search(kw)
                # 100-keyword chunks routinely exceed the old 3600s default —
                # scripts/discover_now.py already runs with 7200s from
                # field experience; keep the periodic path in sync so a
                # paid snapshot isn't abandoned as failed at the 1h mark.
                records = client.poll_until_ready(
                    snap, max_wait_seconds=7200)
                drafts = _filter_and_normalize(
                    records, kw,
                    min_views=cfg.min_views,
                    window_days=cfg.window_days,
                    max_per_keyword=cfg.max_per_keyword,
                )
                ins, upd = _upsert_videos(session, drafts)
                stats.inserted += ins
                stats.updated += upd
                stats.keywords_processed += 1
            except Exception as e:
                stats.keywords_failed += 1
                stats.failed_keywords.append(kw)
                logger.warning("discover keyword %s failed: %s", kw, e)

    stats.pruned = _prune_stale(session, cfg.retain_days)
    return stats
