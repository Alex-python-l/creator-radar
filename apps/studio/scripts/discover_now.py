"""One-off manual TikTok discovery for an explicit keyword set.

Unlike the weekly `discover_tiktok` task (which reads keywords from the
`settings` table), this triggers Bright Data for keywords passed on the CLI
— or a built-in default set — so a new vertical can be topped up immediately
without waiting for the Monday cron or editing the long-term settings config.

Bright Data charges at collection time. The snapshot_id for every batch is
printed up front — if a download dies mid-way, the snapshot is already paid
for and can be recovered for free with:
    uv run python scripts/ingest_bd_snapshot.py <snapshot_id>

Usage:
    uv run python scripts/discover_now.py                 # built-in ArenaPlus set
    uv run python scripts/discover_now.py kw1,kw2,kw3      # explicit comma list
    uv run python scripts/discover_now.py @keywords.txt    # one keyword per line
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import session_scope  # noqa: E402  (needs sys.path tweak)
from app.services import discovery_service as ds  # noqa: E402
from brightdata_client import BrightDataClient  # noqa: E402

# Bright Data's datasets/v3 /trigger accepts up to 100 inputs per POST.
BD_TRIGGER_MAX = 100

# ArenaPlus (PH sports-betting) vertical — 73 keywords across brand, betting,
# events, reach, Tagalog, and competitor terms. Kept in source so the exact
# set is reviewable in git; override on the CLI for ad-hoc runs.
ARENA_KEYWORDS = [
    # --- ArenaPlus brand ---
    "ArenaPlus", "ArenaPlusPH", "SamaSamaSaArena", "AstigSaSports",
    "WatchItOnArenaPlus", "100MMVPBracket", "ArenaPlusPlayoffsMVP",
    "1BillionMVPDream",
    # --- betting / parlay ---
    "parlay", "parlays", "parlaypicks", "parlaybets", "sportsbetting",
    "sportsbet", "sportsbook", "sportbetting", "betting", "bettingtips",
    "bettingpicks", "bettingexpert", "freepicks", "dailypicks",
    "sportsbettingtips", "sportsbettingpicks", "sportsbettingtiktok",
    "picks", "props",
    # --- events / worldcup ---
    "worldcup", "worldcup2026", "fifaworldcup", "fifa2026", "soccerworldcup",
    "roadto2026", "football", "soccer", "futbol", "fifa", "nba", "pba",
    "pvl", "basketball", "hoops", "nbaplayoffs",
    # --- reach / fyp ---
    "fyp", "foryou", "foryoupage", "viral", "viralvideo", "trending", "fypシ",
    # --- Philippines / Tagalog ---
    "pinoy", "pinoytiktok", "philippines", "ph", "pinoybasketball", "pusta",
    "pustahan", "taya", "panalo", "swerte",
    # --- competitor brands ---
    "okbet", "bingoplus", "gamezone", "peraplay", "mnl168", "phlwin",
    "bwinph", "nustabet", "stake", "bet365", "1xbet", "dafabet", "22bet",
]


def _chunks(seq: list[str], size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _load_keywords(arg: str | None) -> list[str]:
    if not arg:
        raw = list(ARENA_KEYWORDS)
    elif arg.startswith("@"):
        with open(arg[1:], encoding="utf-8") as fh:
            raw = [ln.strip() for ln in fh]
    else:
        raw = arg.split(",")
    # dedupe, preserve order, drop blanks
    seen: set[str] = set()
    out: list[str] = []
    for k in raw:
        k = k.strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def main() -> None:
    keywords = _load_keywords(sys.argv[1] if len(sys.argv) > 1 else None)
    if not keywords:
        raise SystemExit("no keywords to search")

    token = os.environ["BRIGHTDATA_TOKEN"]
    dataset_id = os.environ.get(
        "BRIGHTDATA_KEYWORD_DATASET_ID", "gd_lilwhto81z415d9mdl"
    )
    client = BrightDataClient(token=token, dataset_id=dataset_id)

    # Read filter thresholds once (min_views / window_days / max_per_keyword).
    # These are topic-agnostic numbers; we bucket by the keywords WE sent, not
    # by the settings keyword list, so the ArenaPlus rows classify correctly.
    with session_scope() as session:
        cfg = ds._parse_settings(session)

    n_batches = (len(keywords) + BD_TRIGGER_MAX - 1) // BD_TRIGGER_MAX
    print(
        f"discover_now: {len(keywords)} keyword(s) in {n_batches} batch(es); "
        f"filters min_views={cfg.min_views} window_days={cfg.window_days} "
        f"max_per_keyword={cfg.max_per_keyword}",
        flush=True,
    )

    ins_total = upd_total = 0
    for bi, chunk in enumerate(_chunks(keywords, BD_TRIGGER_MAX), 1):
        print(f"[batch {bi}/{n_batches}] triggering {len(chunk)} keyword(s)...",
              flush=True)
        snap = client.trigger_keyword_searches(chunk)
        # Print the snapshot_id BEFORE the (slow) poll so it is durably logged
        # even if the download later fails — recovery is then free.
        print(f"[batch {bi}/{n_batches}] snapshot_id={snap}  "
              f"(recover: scripts/ingest_bd_snapshot.py {snap})", flush=True)

        records = client.poll_until_ready(snap, max_wait_seconds=7200)
        print(f"[batch {bi}/{n_batches}] {len(records)} raw record(s)",
              flush=True)

        with session_scope() as session:
            for kw, rows in ds._bucket_by_keyword(records, chunk).items():
                drafts = ds._filter_and_normalize(
                    rows, kw,
                    min_views=cfg.min_views,
                    window_days=cfg.window_days,
                    max_per_keyword=cfg.max_per_keyword,
                )
                ins, upd = ds._upsert_videos(session, drafts)
                ins_total += ins
                upd_total += upd
                if drafts:
                    print(f"  {kw}: {len(rows)} raw -> {len(drafts)} kept "
                          f"(ins={ins} upd={upd})", flush=True)

    print(f"DONE inserted={ins_total} updated={upd_total}", flush=True)


if __name__ == "__main__":
    main()
