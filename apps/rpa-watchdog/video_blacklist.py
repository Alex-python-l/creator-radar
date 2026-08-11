from __future__ import annotations

import argparse
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

try:
    import fcntl
except ImportError:  # Windows 本地测试没有 fcntl；生产服务器为 Linux。
    fcntl = None


DEFAULT_FILE = Path(__file__).resolve().parent / "ignored_video_links.jsonl"
CONTENT_ID_PATTERN = re.compile(r"/(?:video|photo)/(\d+)(?:/|$)")


class InvalidVideoUrl(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedVideo:
    key: str
    url: str


def normalize_video_url(value: str) -> NormalizedVideo:
    raw = value.strip()
    if not raw:
        raise InvalidVideoUrl("视频链接不能为空")

    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or not (host == "tiktok.com" or host.endswith(".tiktok.com")):
        raise InvalidVideoUrl("只支持 TikTok 视频或图文链接")

    if host.startswith("www."):
        host = host[4:]
    elif host.startswith("m."):
        host = host[2:]

    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/") or "/"
    canonical_url = urlunsplit(("https", host, path, "", ""))
    content_id = CONTENT_ID_PATTERN.search(path)
    key = (
        f"tiktok:{content_id.group(1)}"
        if content_id is not None
        else f"url:{canonical_url.casefold()}"
    )
    return NormalizedVideo(key=key, url=canonical_url)


class VideoBlacklist:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _locked_file(self) -> Iterator[Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as file:
            if fcntl is not None:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX)
            try:
                file.seek(0)
                yield file
            finally:
                if fcntl is not None:
                    fcntl.flock(file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _active_records(file: Any) -> dict[str, dict[str, Any]]:
        active: dict[str, dict[str, Any]] = {}
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"黑名单文件第 {line_number} 行不是有效 JSON") from exc

            key = str(record.get("videoKey", "")).strip()
            action = record.get("action")
            if not key or action not in {"add", "remove"}:
                raise RuntimeError(f"黑名单文件第 {line_number} 行缺少 videoKey/action")
            if action == "add":
                active[key] = record
            else:
                active.pop(key, None)
        return active

    def find(self, video_url: str) -> dict[str, Any] | None:
        normalized = normalize_video_url(video_url)
        with self._locked_file() as file:
            return self._active_records(file).get(normalized.key)

    def list(self) -> list[dict[str, Any]]:
        with self._locked_file() as file:
            return sorted(
                self._active_records(file).values(),
                key=lambda item: str(item.get("addedAt", "")),
            )

    def add(
        self,
        video_url: str,
        note: str = "",
        *,
        source: str = "manual",
        device_id: str | None = None,
        flow_id: str | None = None,
        reservation_id: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        normalized = normalize_video_url(video_url)
        with self._locked_file() as file:
            existing = self._active_records(file).get(normalized.key)
            if existing is not None:
                return False, existing

            record = {
                "action": "add",
                "videoKey": normalized.key,
                "videoUrl": normalized.url,
                "originalUrl": video_url.strip(),
                "addedAt": datetime.now(timezone.utc).isoformat(),
            }
            if note.strip():
                record["note"] = note.strip()
            if source.strip():
                record["source"] = source.strip()
            if device_id is not None and device_id.strip():
                record["deviceId"] = device_id.strip()
            if flow_id is not None and flow_id.strip():
                record["flowId"] = flow_id.strip()
            if reservation_id is not None and reservation_id.strip():
                record["reservationId"] = reservation_id.strip()
            file.seek(0, 2)
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()
            return True, record

    def remove(
        self,
        video_url: str,
        *,
        expected_reservation_id: str | None = None,
        reason: str = "",
    ) -> tuple[bool, dict[str, Any] | None]:
        normalized = normalize_video_url(video_url)
        with self._locked_file() as file:
            existing = self._active_records(file).get(normalized.key)
            if existing is None:
                return False, None
            if (
                expected_reservation_id is not None
                and existing.get("reservationId") != expected_reservation_id
            ):
                return False, existing

            record = {
                "action": "remove",
                "videoKey": normalized.key,
                "videoUrl": normalized.url,
                "removedAt": datetime.now(timezone.utc).isoformat(),
            }
            if reason.strip():
                record["reason"] = reason.strip()
            if expected_reservation_id is not None:
                record["reservationId"] = expected_reservation_id
            file.seek(0, 2)
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()
            return True, existing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="手动维护 RPA 视频黑名单")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="添加视频链接")
    add_parser.add_argument("url")
    add_parser.add_argument("--note", default="")

    check_parser = subparsers.add_parser("check", help="检查视频链接")
    check_parser.add_argument("url")

    remove_parser = subparsers.add_parser("remove", help="解除视频链接")
    remove_parser.add_argument("url")

    subparsers.add_parser("list", help="列出当前黑名单")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    blacklist = VideoBlacklist(args.file)
    try:
        if args.command == "add":
            added, record = blacklist.add(args.url, args.note)
            print("已加入黑名单" if added else "链接已在黑名单中")
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0
        if args.command == "check":
            record = blacklist.find(args.url)
            print("在黑名单中" if record else "不在黑名单中")
            if record:
                print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0 if record else 1
        if args.command == "remove":
            removed, record = blacklist.remove(args.url)
            print("已解除黑名单" if removed else "链接不在黑名单中")
            if record:
                print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0 if removed else 1

        records = blacklist.list()
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0
    except (InvalidVideoUrl, RuntimeError) as exc:
        print(f"错误：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
