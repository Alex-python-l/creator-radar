"""Thin HTTP wrapper for Bright Data's datasets/v3 API.

Mirrors the shape of geelark_client.py. Used by the discover_tiktok
periodic task to surface candidate TikTok videos for the CommentModal
'Discover' tab. See docs/superpowers/specs/2026-06-09-tiktok-ielts-discover-design.md.
"""
from __future__ import annotations

from urllib.parse import quote_plus

import time

import requests


class BrightDataError(RuntimeError):
    pass


class BrightDataTimeout(BrightDataError):
    pass


class BrightDataNotReady(BrightDataError):
    """Raised when /snapshot returns a status object instead of the
    records array — i.e. the dataset is marked ready upstream but the
    binary file isn't materialized yet. Caller should retry."""
    pass


def _parse_concatenated_json(text: str) -> list[dict]:
    """Parse BD's jsonl output, which is NOT valid NDJSON: TikTok
    descriptions contain raw (unescaped) newlines, so splitting on line
    breaks corrupts records. raw_decode walks object-by-object regardless
    of embedded newlines."""
    import json as _json

    # strict=False tolerates unescaped control chars inside strings
    dec = _json.JSONDecoder(strict=False)
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


class BrightDataClient:
    BASE = "https://api.brightdata.com/datasets/v3"

    def __init__(self, token: str, *, dataset_id: str, timeout: int = 30):
        self.token = token
        self.dataset_id = dataset_id
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def trigger_keyword_search(
        self, keyword: str, *, country: str = ""
    ) -> str:
        return self.trigger_keyword_searches([keyword], country=country)

    def trigger_keyword_searches(
        self, keywords: list[str], *, country: str = ""
    ) -> str:
        """Batch-trigger N keyword inputs in one /trigger call.

        Bright Data's datasets/v3 trigger accepts up to 100 inputs per POST
        and returns a single snapshot_id covering the whole batch. Each
        result row carries its originating search_keyword.
        """
        if not keywords:
            raise BrightDataError("no keywords provided")
        body = [
            {
                "url": f"https://www.tiktok.com/search?q={quote_plus(kw)}",
                "search_keyword": kw,
                "country": country,
                "keyword_type": "",
            }
            for kw in keywords
        ]
        r = requests.post(
            f"{self.BASE}/trigger",
            params={"dataset_id": self.dataset_id},
            headers=self._headers(),
            json=body,
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise BrightDataError(
                f"trigger failed {r.status_code}: {r.text[:200]}"
            )
        snap = r.json().get("snapshot_id")
        if not snap:
            raise BrightDataError(f"trigger missing snapshot_id: {r.text[:200]}")
        return snap

    def get_progress(self, snapshot_id: str) -> str:
        # Transient tolerance matters here more than anywhere: the poll
        # loop calls this ~180 times over an hour for a snapshot that is
        # ALREADY PAID FOR — one network blip must not abandon the batch
        # (recovery is a manual log-grep + ingest script run).
        last_exc: Exception | None = None
        for attempt in range(4):
            if attempt:
                time.sleep(5 * attempt)
            try:
                r = requests.get(
                    f"{self.BASE}/progress/{snapshot_id}",
                    headers=self._headers(),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_exc = exc
                continue
            if r.status_code >= 500:
                last_exc = BrightDataError(
                    f"progress failed {r.status_code}: {r.text[:200]}"
                )
                continue
            if r.status_code >= 400:
                raise BrightDataError(
                    f"progress failed {r.status_code}: {r.text[:200]}"
                )
            status = r.json().get("status")
            if not status:
                raise BrightDataError(
                    f"progress missing status: {r.text[:200]}")
            return status
        raise BrightDataError(
            f"progress unreachable after retries: {last_exc}")

    # Records per download part. BD's parts flow (format=jsonl +
    # batch_size + part=N) is the documented way to download large
    # snapshots; single-GET json of a 45-keyword batch (~27 MB) gets
    # truncated mid-stream by their CDN (IncompleteRead).
    BATCH_SIZE = 1000

    def fetch_snapshot(self, snapshot_id: str) -> list[dict]:
        """Download all records of a snapshot via the jsonl parts flow.

        Requesting a new format/batch_size combination triggers a
        server-side build of that file layout; until it finishes, the
        endpoint answers {"status": "building"} which we surface as
        BrightDataNotReady so poll_until_ready keeps waiting.
        """
        import json as _json

        params = {"format": "jsonl", "batch_size": self.BATCH_SIZE}

        first = self._fetch_part(snapshot_id, params, 1)
        if first is None:
            raise BrightDataNotReady(
                f"snapshot {snapshot_id} jsonl build not finished"
            )

        r = requests.get(
            f"{self.BASE}/snapshot/{snapshot_id}/parts",
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise BrightDataError(
                f"parts lookup failed {r.status_code}: {r.text[:200]}"
            )
        body = r.json()
        if isinstance(body, dict) and "parts" in body:
            n_parts = int(body["parts"])
        elif isinstance(body, int):
            n_parts = int(body)
        else:
            raise BrightDataError(f"unexpected parts response: {str(body)[:200]}")

        records = list(first)
        for part in range(2, n_parts + 1):
            chunk = self._fetch_part(snapshot_id, params, part)
            if chunk is None:
                raise BrightDataError(
                    f"part {part}/{n_parts} reported building after build done"
                )
            records.extend(chunk)
        return records

    def _fetch_part(
        self, snapshot_id: str, params: dict, part: int
    ) -> list[dict] | None:
        """GET one snapshot part. Returns records, or None when the file
        layout is still building server-side. Retries transport errors."""
        import json as _json

        last_err: Exception | None = None
        for attempt in range(4):
            try:
                r = requests.get(
                    f"{self.BASE}/snapshot/{snapshot_id}",
                    params={**params, "part": part},
                    headers=self._headers(),
                    timeout=(30, 300),
                )
                if r.status_code >= 400:
                    raise BrightDataError(
                        f"part {part} failed {r.status_code}: {r.text[:200]}"
                    )
                body = r.content
                head = body[:80].decode("utf-8", "replace").lstrip()
                if head.startswith('{"status"'):
                    return None  # jsonl build still running
                return _parse_concatenated_json(body.decode("utf-8"))
            except (requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.ReadTimeout,
                    _json.JSONDecodeError) as e:
                last_err = e
                time.sleep(min(10 * (attempt + 1), 30))
        raise BrightDataError(
            f"part {part} fetch failed after 4 retries: {last_err!r}"
        )

    def poll_until_ready(
        self,
        snapshot_id: str,
        *,
        interval_seconds: int = 20,
        max_wait_seconds: int = 3600,
    ) -> list[dict]:
        start = time.monotonic()
        while True:
            status = self.get_progress(snapshot_id)
            if status == "ready":
                # /progress flips to ready before /snapshot finishes building
                # the file — retry fetch_snapshot until it returns the array.
                try:
                    return self.fetch_snapshot(snapshot_id)
                except BrightDataNotReady:
                    pass
            elif status == "failed":
                raise BrightDataError(
                    f"snapshot {snapshot_id} reported failed status"
                )
            if time.monotonic() - start >= max_wait_seconds:
                raise BrightDataTimeout(
                    f"snapshot {snapshot_id} timeout after "
                    f"{max_wait_seconds}s (last status: {status})"
                )
            time.sleep(interval_seconds)
