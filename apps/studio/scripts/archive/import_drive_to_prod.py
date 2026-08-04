#!/usr/bin/env python3
"""Download public Google Drive videos locally, then upload them to production.

This is an operator-side import path. Google Drive traffic uses the local
proxy supplied by --proxy / DRIVE_IMPORT_PROXY. The Aliyun upload goes straight
to the existing FastAPI /api/videos/upload endpoint.
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import gdown
import requests
from dotenv import load_dotenv


PROD_BASE_URL = "http://139.196.52.92:8080"
PLATFORMS = ("tiktok", "youtube", "instagram", "facebook")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


@dataclass(frozen=True)
class DriveVideo:
    file_id: str
    name: str


def is_video_name(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXTENSIONS


def video_entries(listing: Iterable[object]) -> list[DriveVideo]:
    videos: list[DriveVideo] = []
    for item in listing:
        file_id = str(getattr(item, "id"))
        name = Path(str(getattr(item, "path"))).name
        if is_video_name(name):
            videos.append(DriveVideo(file_id=file_id, name=name))
    return videos


def list_drive_videos(folder_url: str, proxy: str | None) -> list[DriveVideo]:
    listing = gdown.download_folder(
        url=folder_url,
        skip_download=True,
        quiet=False,
        proxy=proxy,
    )
    return video_entries(listing or [])


def unique_local_path(download_dir: Path, name: str, file_id: str) -> Path:
    safe = Path(name).name or f"{file_id}.mp4"
    dest = download_dir / safe
    if not dest.exists():
        return dest
    return download_dir / f"{Path(safe).stem}_{file_id}{Path(safe).suffix}"


def download_video(video: DriveVideo, download_dir: Path, proxy: str | None) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_local_path(download_dir, video.name, video.file_id)
    result = gdown.download(
        id=video.file_id,
        output=str(dest),
        quiet=False,
        proxy=proxy,
    )
    if result is None or not dest.exists():
        raise RuntimeError(f"download failed: {video.name}")
    return dest


def login(session: requests.Session, base_url: str, password: str) -> None:
    resp = session.post(
        f"{base_url.rstrip('/')}/api/login",
        json={"password": password},
        timeout=30,
    )
    resp.raise_for_status()


def make_prod_session() -> requests.Session:
    session = requests.Session()
    # Production upload is a direct LAN/Internet path to Aliyun. Do not let
    # HTTP_PROXY/HTTPS_PROXY/ALL_PROXY from the operator shell reroute it.
    session.trust_env = False
    return session


def upload_file_to_prod(
    session: requests.Session,
    base_url: str,
    path: Path,
    group_id: int | None = None,
) -> dict:
    data = {"platforms": ",".join(PLATFORMS)}
    if group_id is not None:
        data["group_id"] = str(group_id)
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as fh:
        resp = session.post(
            f"{base_url.rstrip('/')}/api/videos/upload",
            data=data,
            files={"files": (path.name, fh, content_type)},
            timeout=600,
        )
    resp.raise_for_status()
    return resp.json()


def run(args: argparse.Namespace) -> int:
    load_dotenv()
    proxy = args.proxy or os.getenv("DRIVE_IMPORT_PROXY")
    password = args.password or os.getenv("DASHBOARD_PASSWORD")
    if not proxy:
        print("error: set --proxy or DRIVE_IMPORT_PROXY for Google Drive egress", file=sys.stderr)
        return 2
    if not password:
        print("error: set --password or DASHBOARD_PASSWORD for production login", file=sys.stderr)
        return 2

    base_url = args.base_url.rstrip("/")
    work_dir = Path(args.download_dir) if args.download_dir else Path(tempfile.mkdtemp(prefix="drive-import-"))
    remove_work_dir = args.download_dir is None
    try:
        videos = list_drive_videos(args.folder_url, proxy)
        if args.limit is not None:
            videos = videos[: args.limit]
        if not videos:
            print("No video files found.")
            return 0
        print(f"Found {len(videos)} video(s).")
        if args.dry_run:
            for video in videos:
                print(f"{video.file_id}\t{video.name}")
            return 0

        session = make_prod_session()
        login(session, base_url, password)
        imported = 0
        for index, video in enumerate(videos, start=1):
            print(f"[{index}/{len(videos)}] downloading {video.name}")
            local_path = download_video(video, work_dir, proxy)
            print(f"[{index}/{len(videos)}] uploading {local_path.name} to {base_url}")
            upload_file_to_prod(session, base_url, local_path, group_id=args.group_id)
            imported += 1
        print(f"Imported {imported} video(s).")
        return 0
    finally:
        if remove_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder_url", help="Public Google Drive folder URL")
    parser.add_argument("--base-url", default=PROD_BASE_URL,
                        help=f"Production base URL (default: {PROD_BASE_URL})")
    parser.add_argument("--group-id", type=int, default=None,
                        help="Optional target group id for uploaded videos")
    parser.add_argument("--proxy", default=None,
                        help="Proxy for Google Drive traffic")
    parser.add_argument("--password", default=None,
                        help="Dashboard password; defaults to DASHBOARD_PASSWORD")
    parser.add_argument("--download-dir", default=None,
                        help="Keep downloads in this directory instead of a temp dir")
    parser.add_argument("--limit", type=int, default=None,
                        help="Import only the first N videos")
    parser.add_argument("--dry-run", action="store_true",
                        help="List videos without downloading or uploading")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
