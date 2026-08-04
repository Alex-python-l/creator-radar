"""Recover a paid-for Bright Data snapshot into discovered_videos.

Usage: uv run python scripts/ingest_bd_snapshot.py <snapshot_id> [batch_size]

Bright Data charges at collection time, so a snapshot whose download
failed is already paid for and sits on their servers for ~30 days.
This script re-downloads it in jsonl parts (robust against the CDN
IncompleteRead that kills large single-GET downloads), buckets records
by search_keyword, applies the configured filters, and upserts via
discovery_service — exactly what the discover_tiktok task would have
done had the download succeeded.
"""
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import session_scope  # noqa: E402  (needs sys.path tweak)
from app.services import discovery_service as ds  # noqa: E402

BASE = "https://api.brightdata.com/datasets/v3"


def parse_concatenated_json(text: str) -> list[dict]:
    """Parse BD's jsonl output, which is NOT valid NDJSON: TikTok
    descriptions contain raw (unescaped) newlines, so splitting on
    line breaks corrupts records. raw_decode walks object-by-object
    regardless of embedded newlines."""
    # strict=False tolerates unescaped control chars inside strings
    dec = json.JSONDecoder(strict=False)
    out: list[dict] = []
    idx, n = 0, len(text)
    while idx < n:
        while idx < n and text[idx] in " \r\n\t":
            idx += 1
        if idx >= n:
            break
        obj, idx = dec.raw_decode(text, idx)
        out.append(obj)
    return out


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: ingest_bd_snapshot.py <snapshot_id> [batch_size]")
    snap = sys.argv[1]
    batch = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    token = os.environ["BRIGHTDATA_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}
    params = {"format": "jsonl", "batch_size": batch}

    # Requesting the snapshot with a new format/batch_size combination
    # triggers a server-side build of that file layout. Poll part=1 until
    # the build completes, THEN /parts reports how many parts exist.
    deadline = time.monotonic() + 900
    while True:
        probe = requests.get(
            f"{BASE}/snapshot/{snap}",
            params={**params, "part": 1},
            headers=headers,
            timeout=(30, 300),
        )
        body_head = probe.content[:200].decode("utf-8", "replace")
        if probe.status_code == 200 and not body_head.startswith('{"status"'):
            break
        print(f"build pending: {probe.status_code} {body_head[:120]}")
        if time.monotonic() > deadline:
            raise SystemExit("snapshot build did not finish in 15 min")
        time.sleep(20)

    r = requests.get(f"{BASE}/snapshot/{snap}/parts", params=params,
                     headers=headers, timeout=60)
    r.raise_for_status()
    body = r.json()
    if isinstance(body, dict) and "parts" in body:
        n_parts = int(body["parts"])
    elif isinstance(body, int):
        n_parts = body
    else:
        raise SystemExit(f"unexpected parts response: {body!r}")
    print(f"snapshot {snap}: {n_parts} part(s) at batch_size={batch}")

    records: list[dict] = []
    for part in range(1, n_parts + 1):
        for attempt in range(4):
            try:
                pr = requests.get(
                    f"{BASE}/snapshot/{snap}",
                    params={**params, "part": part},
                    headers=headers,
                    timeout=(30, 300),
                )
                pr.raise_for_status()
                chunk = parse_concatenated_json(pr.content.decode("utf-8"))
                records.extend(chunk)
                print(f"part {part}/{n_parts}: +{len(chunk)} records")
                break
            except Exception as e:  # noqa: BLE001 — retry any transport error
                print(f"part {part} attempt {attempt + 1} failed: {e}")
                time.sleep(5 * (attempt + 1))
        else:
            raise SystemExit(f"part {part} failed after retries")

    print(f"total raw records: {len(records)}")

    with session_scope() as session:
        cfg = ds._parse_settings(session)
        ins_total = upd_total = 0
        for kw, rows in ds._bucket_by_keyword(records, cfg.keywords).items():
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
                print(f"{kw}: {len(rows)} raw -> {len(drafts)} kept "
                      f"(ins={ins} upd={upd})")
        print(f"DONE inserted={ins_total} updated={upd_total}")


if __name__ == "__main__":
    main()
