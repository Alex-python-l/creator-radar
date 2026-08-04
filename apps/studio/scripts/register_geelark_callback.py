"""Register our /api/geelark/callback URL with GeeLark.

Phase 0 of the 2026-06-13 phone-runtime-architecture spec (callback
ingest). Hits GeeLark's `/open/v1/callback/set` to point inbound task
callbacks at our public URL. Idempotent — re-running just overwrites
the registered URL.

Discovery from the 2026-06-09 task-callback design verified that the
live endpoints are `/open/v1/callback/{get,set}`, NOT `/webhook/*` as
the OpenAPI yaml claims. See the spec for details.

Usage
=====

    uv run python scripts/register_geelark_callback.py \\
        --base-url https://igotyoufrank.cn/studio

(2026-07-17 起回调走域名;历史上注册的是裸 IP
http://139.196.52.92:8080,该入口暂保留作后路,勿再注册回去。)

Optional:
    --check-only    Don't change anything; just GET the current URL
                    and print it. Use to verify after a deploy.
    --dry-run       Print the request that would be sent, exit without
                    hitting GeeLark.

Both the base URL and `GEELARK_CALLBACK_TOKEN` (from .env) must be set
before the script will run — refusing rather than registering a
guessable token URL.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app import config  # noqa: E402
from app.services.geelark_service import make_client  # noqa: E402


def _build_url(base_url: str, token: str) -> str:
    """Join the public base URL with /api/geelark/callback?token=…"""
    base = base_url.rstrip("/")
    return f"{base}/api/geelark/callback?token={quote(token, safe='')}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default=None,
        help="Public base URL of the mkt web tier (e.g. "
             "https://igotyoufrank.cn/studio). Required unless --check-only.",
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="Only GET the current registered URL; do not set.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the would-be POST and exit.",
    )
    args = parser.parse_args()

    client = make_client()

    if args.check_only:
        current = client._post("/open/v1/callback/get", {})
        print(json.dumps(current, indent=2, ensure_ascii=False))
        return 0

    token = config.GEELARK_CALLBACK_TOKEN
    if not token:
        print("ERROR: GEELARK_CALLBACK_TOKEN is empty. Set it in .env "
              "before registering.", file=sys.stderr)
        return 2
    if not args.base_url:
        print("ERROR: --base-url is required unless --check-only.",
              file=sys.stderr)
        return 2

    url = _build_url(args.base_url, token)
    payload = {"url": url}

    if args.dry_run:
        print("would POST /open/v1/callback/set with:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    # GeeLark's `/open/v1/callback/set` accepts plain HTTP and any URL
    # string (no validation). Returns {} on success.
    data = client._post("/open/v1/callback/set", payload)
    print(f"set callback URL → {url}")
    print(f"response: {json.dumps(data, ensure_ascii=False)}")

    # Verify by reading back.
    current = client._post("/open/v1/callback/get", {})
    print(f"verified registered URL: "
          f"{json.dumps(current, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
