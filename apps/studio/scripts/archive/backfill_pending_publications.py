#!/usr/bin/env python3
"""One-shot: for every video classified as PENDING in the UI, ensure a
publication row exists for every platform in PLATFORMS.

A video is PENDING when it has no publications, OR at least one
publication is still non-terminal (PENDING/QUEUED/INITIATED). Videos
whose every publication is already in a terminal state (SUCCESS/FAILURE)
are skipped — re-publishing already-shipped content to newly-added
platforms would surprise the user.

Idempotent via pub_repo.find_or_create — safe to re-run.

Usage:
  uv run python scripts/backfill_pending_publications.py
  uv run python scripts/backfill_pending_publications.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlmodel import Session, select

from app.db import engine
from app.models import Video
from app.repositories import publications as pub_repo
from app.services.platforms import PLATFORMS

TERMINAL = {"SUCCESS", "FAILURE"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print plan but don't write")
    args = ap.parse_args()

    created = 0
    pending_videos = 0
    skipped = 0
    with Session(engine) as session:
        videos = session.exec(select(Video)).all()
        for v in videos:
            pubs = pub_repo.list_for_video(session, v.id)
            is_pending = (not pubs) or any(
                p["result"] not in TERMINAL for p in pubs)
            if not is_pending:
                skipped += 1
                continue
            pending_videos += 1
            existing = {p["platform"] for p in pubs}
            missing = [p for p in PLATFORMS if p not in existing]
            if not missing:
                continue
            print(f"  video #{v.id} {v.original_filename!r} "
                  f"+ {', '.join(missing)}")
            if args.dry_run:
                created += len(missing)
                continue
            for plat in missing:
                pub_repo.find_or_create(session, video_id=v.id, platform=plat)
                created += 1

    verb = "would create" if args.dry_run else "created"
    print(f"\n{pending_videos} pending video(s), {skipped} terminal skipped; "
          f"{verb} {created} publication row(s)")


if __name__ == "__main__":
    main()
