#!/usr/bin/env python3
"""
generate_caption.py — Generate a TikTok caption for a video using Gemini.

Standalone usage:
    python generate_caption.py --file-id <GOOGLE_DRIVE_FILE_ID>

When called as a module, use generate_caption_for_file().

.env variables required:
    GEMINI_API_KEY
    GEMINI_PROXY (optional; falls back to DRIVE_IMPORT_PROXY)
    SERVICE_ACCOUNT_PATH
"""
import os
import re
import sys
import time
import logging
import argparse
import tempfile

import httpx
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Hard cap matches the prompt's stated upper bound; TikTok itself allows more,
# but anything past this here is almost always Gemini ignoring the brief.
CAPTION_MAX_LEN_DEFAULT = 220

PROMPT_TEMPLATE = """\
Write ONE short TikTok caption for the given video.

Output rules (STRICT — violating any of these makes the caption unusable):
- Output ONLY the final caption text. No preamble like "Here is…", "Below is…", "Here are a few options…".
- No Markdown. No **bold**, no headings, no "Title:" / "Caption:" labels.
- Do NOT provide multiple versions, alternatives, or "Option 1 / Option 2".
- Total length: target {max_len} characters or fewer, INCLUDING hashtags.

Content rules:
- Open with a relatable or emotional hook. Vary the hook every time — don't reuse the same opener.
- Casual, personal, mobile-friendly tone. 1–2 emojis.
- End with 3–5 hashtags that fit the lane.
- Ignore any usernames, watermarks, or third-party handles in the video — never copy them into the caption.
- Sound authentic, not promotional.

Avoid (these read as AI-written):
- Filler superlatives: "amazing", "literally", "incredible", "absolutely", "truly"
- Em dashes (—) and en dashes (–); use commas, periods, or line breaks
- Generic vocatives ("guys", "y'all") unless they fit the lane

Account positioning (stay in this lane):
{brief}
"""


def build_prompt(brief: str | None, max_len: int) -> str:
    cleaned = (brief or "").strip()
    if not cleaned:
        cleaned = "(no specific positioning — write a generic short-form video caption)"
    return PROMPT_TEMPLATE.format(brief=cleaned, max_len=max_len)


# ── sanitization ──────────────────────────────────────────────────────────────
# Belt and suspenders: even with the strict prompt, Gemini 2.5 Flash sometimes
# falls back to its default chat shape (Markdown labels, "Here is…" preamble,
# or multiple "Option N" blocks). Posting any of those literally to TikTok
# looks like obvious AI slop. This sanitizer rescues what's safely rescuable
# and refuses (raises) on the one case we won't guess at — multiple options.

_BOLD_RE = re.compile(r"\*\*(.*?)\*\*", re.DOTALL)
_LABEL_RE = re.compile(r"^\s*(?:Title|Caption)\s*:\s*", re.IGNORECASE)
_PREAMBLE_RE = re.compile(r"^\s*(?:Here(?:'s| is| are)|Below is)\b", re.IGNORECASE)
_OPTION_HEADER_RE = re.compile(r"^\s*Option\s*\d+\s*:?\s*$", re.IGNORECASE)
# "Option 2" or higher anywhere in the output means we got >1 caption.
_MULTI_OPTION_RE = re.compile(r"\bOption\s*[2-9]\d*\b", re.IGNORECASE)


def sanitize_caption(
    raw: str,
    *,
    max_len: int = CAPTION_MAX_LEN_DEFAULT,
) -> str:
    """Clean up a Gemini caption response into something postable to TikTok.

    Raises RuntimeError if the model emitted multiple caption options — we'd
    rather fail loudly and let the caller retry than silently pick one.
    """
    if _MULTI_OPTION_RE.search(raw):
        raise RuntimeError("Gemini returned multiple caption options; rejecting")

    text = _BOLD_RE.sub(r"\1", raw)

    kept = []
    for line in text.split("\n"):
        if _PREAMBLE_RE.match(line):
            continue
        if _OPTION_HEADER_RE.match(line):
            continue
        line = _LABEL_RE.sub("", line)
        kept.append(line)

    out = "\n".join(kept)
    out = re.sub(r"\n{2,}", "\n", out).strip()

    if len(out) > max_len:
        out = _truncate_preserving_hashtags(out, max_len)

    return out


def _truncate_preserving_hashtags(text: str, limit: int) -> str:
    """Trim caption to `limit` chars, keeping the trailing hashtag block intact.

    Hashtags drive discoverability, so we'd rather cut body prose than lose
    them. If the hashtag block alone exceeds the limit, it wins and gets
    truncated from the right.
    """
    lines = text.split("\n")
    tail_lines = []
    while lines and lines[-1].lstrip().startswith("#"):
        tail_lines.insert(0, lines.pop())
    tail = "\n".join(tail_lines).rstrip()
    body = "\n".join(lines).rstrip()

    if not tail:
        return text[:limit].rstrip()
    if not body:
        return tail[:limit]

    room_for_body = limit - len(tail) - 1  # -1 for the joining newline
    if room_for_body <= 0:
        return tail[:limit]
    return body[:room_for_body].rstrip() + "\n" + tail

GEMINI_MODEL = "gemini-3-flash-preview"

# How long to wait for Gemini to finish processing an uploaded file (seconds)
FILE_PROCESSING_TIMEOUT = 120
FILE_PROCESSING_POLL    = 5
GEMINI_UPLOAD_TIMEOUT_MS = 300_000  # ms — google-genai HttpOptions.timeout is in milliseconds (300s upload cap)
GEMINI_FILE_STATUS_TIMEOUT_MS = 30_000

# generate_content retry on transient server-side errors. 503 ("model is
# currently overloaded") is the dominant failure mode on preview models; 429
# happens during burst quota spikes; 500/502/504 cover the rest of Google's
# upstream blips. 4xx other than 429 are real client errors (bad request,
# auth, content policy) — never retry those.
RETRIABLE_GENAI_CODES = {429, 500, 502, 503, 504}
GENERATE_CONTENT_RETRY_DELAYS_S = (5, 15, 45)

# Substrings that disqualify a model from the caption-picker dropdown.
# "live" catches gemini-*-flash-live-preview (real-time streaming, not for
# offline captioning). The rest skip non-text-generation surfaces.
_MODEL_BLACKLIST = ("embed", "tts", "image", "live", "text-to-speech", "stt")


def list_models(*, api_key: str | None = None) -> list[dict]:
    """Return Gemini models suitable for video-caption generation.

    Calls client.models.list() and filters down to gemini-* flash/pro variants
    that support generateContent. GA models come before Preview models.

    Each entry: {"id": str, "display_name": str, "status": "GA" | "Preview"}.
    """
    client = _get_client(api_key) if api_key else _get_client()
    out: list[dict] = []
    for m in client.models.list():
        full_name = m.name or ""
        mid = full_name.split("/")[-1]
        if not mid.startswith("gemini-"):
            continue
        actions = m.supported_actions or []
        if "generateContent" not in actions:
            continue
        low = mid.lower()
        if any(tag in low for tag in _MODEL_BLACKLIST):
            continue
        if "flash" not in low and "pro" not in low:
            continue
        out.append({
            "id": mid,
            "display_name": m.display_name or mid,
            "status": "Preview" if "preview" in low else "GA",
        })

    out.sort(key=lambda x: (0 if x["status"] == "GA" else 1, x["id"]))
    return out


def _get_client(api_key: str | None = None):
    try:
        from google import genai
    except ImportError:
        raise RuntimeError(
            "google-genai is not installed. Run: pip install google-genai"
        )
    resolved_key = api_key or GEMINI_API_KEY
    if not resolved_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    http_options: dict = {"timeout": GEMINI_UPLOAD_TIMEOUT_MS}

    # Production can use a dedicated Gemini egress proxy. If unset, fall back
    # to the Drive import proxy to preserve older deployments.
    # google-genai 2.x splats HttpOptions.client_args into httpx.Client(**),
    # and httpx 0.28 takes a singular `proxy=` keyword.
    proxy = (
        os.getenv("GEMINI_PROXY", "").strip()
        or os.getenv("DRIVE_IMPORT_PROXY", "").strip()
    )
    if proxy:
        http_options["client_args"] = {"proxy": proxy}
        http_options["async_client_args"] = {"proxy": proxy}

    return genai.Client(
        api_key=resolved_key,
        http_options=http_options,
    )


def _generate_content_with_retry(client, **kwargs):
    """Wrap client.models.generate_content with backoff on 429/5xx.

    google-genai raises ``google.genai.errors.APIError`` (or its subclass
    ``ServerError``) carrying ``.code`` set to the HTTP status. We only retry
    transient server-side codes — other errors (content-policy 4xx, auth,
    deserialization) propagate immediately so they fail loudly and stay
    diagnosable.

    Runs in the worker's ``asyncio.to_thread`` slot, so ``time.sleep`` blocks
    that thread only, not the event loop.
    """
    try:
        from google.genai import errors as genai_errors
        api_error_cls: tuple = (genai_errors.APIError,)
    except ImportError:
        api_error_cls = ()
    for attempt in range(len(GENERATE_CONTENT_RETRY_DELAYS_S) + 1):
        try:
            return client.models.generate_content(**kwargs)
        except api_error_cls as e:
            code = getattr(e, "code", None)
            if code not in RETRIABLE_GENAI_CODES \
                    or attempt >= len(GENERATE_CONTENT_RETRY_DELAYS_S):
                raise
            delay = GENERATE_CONTENT_RETRY_DELAYS_S[attempt]
            log.warning(
                "  Gemini %s, retrying in %ds (attempt %d/%d): %s",
                code, delay, attempt + 1,
                len(GENERATE_CONTENT_RETRY_DELAYS_S) + 1,
                getattr(e, "message", None) or str(e),
            )
            time.sleep(delay)


def generate_caption_for_local_file(
    local_path: str,
    *,
    brief: str | None = None,
    max_len: int | None = None,
    thinking_budget: int = 0,
    model: str | None = None,
    api_key: str | None = None,
) -> str:
    """
    Upload a local video file to Gemini, generate a caption, and return it.
    Cleans up the Gemini file after use.
    Raises RuntimeError on any failure.

    `brief` is the account-positioning paragraph; falls back to a generic
    placeholder if None / empty.
    `max_len` caps the total caption length (incl. hashtags); defaults to
    CAPTION_MAX_LEN_DEFAULT.
    `thinking_budget` controls Gemini's reasoning token budget; 0 disables
    thinking (faster + cheaper).
    `model` overrides the GEMINI_MODEL default so the dashboard's model
    picker can flow through without touching this module's globals.
    """
    client = _get_client(api_key) if api_key else _get_client()

    log.info(f"  Uploading to Gemini File API: {os.path.basename(local_path)}")
    with open(local_path, "rb") as f:
        gemini_file = client.files.upload(
            file=f,
            config={"mime_type": "video/mp4"},
        )

    file_name = gemini_file.name
    log.info(f"  Gemini file uploaded: {file_name} — waiting for processing...")

    deadline = time.time() + FILE_PROCESSING_TIMEOUT
    poll_config = {
        "http_options": {"timeout": GEMINI_FILE_STATUS_TIMEOUT_MS},
    }
    last_poll_error: Exception | None = None
    while time.time() < deadline:
        try:
            gemini_file = client.files.get(name=file_name, config=poll_config)
            last_poll_error = None
        except httpx.TransportError as e:
            last_poll_error = e
            log.warning("  Gemini file status poll failed; retrying: %s", e)
            time.sleep(FILE_PROCESSING_POLL)
            continue
        state = gemini_file.state
        # State: PROCESSING=1, ACTIVE=2, FAILED=3 (numeric or string depending on SDK)
        state_val = state.value if hasattr(state, "value") else str(state)
        if str(state_val) in ("2", "ACTIVE"):
            break
        if str(state_val) in ("3", "FAILED"):
            client.files.delete(name=file_name)
            raise RuntimeError(f"Gemini file processing failed (state={state_val})")
        time.sleep(FILE_PROCESSING_POLL)
    else:
        client.files.delete(name=file_name)
        msg = (
            f"Timed out waiting for Gemini to process file after "
            f"{FILE_PROCESSING_TIMEOUT}s"
        )
        if last_poll_error is not None:
            msg += f"; last status poll failed: {last_poll_error}"
        raise RuntimeError(msg)

    chosen_model = (model or "").strip() or GEMINI_MODEL
    effective_max_len = max_len or CAPTION_MAX_LEN_DEFAULT
    prompt = build_prompt(brief, effective_max_len)

    log.info(f"  Generating caption (model={chosen_model})...")
    try:
        response = _generate_content_with_retry(
            client,
            model=chosen_model,
            contents=[
                {"role": "user", "parts": [
                    {"file_data": {"file_uri": gemini_file.uri, "mime_type": "video/mp4"}},
                    {"text": prompt},
                ]},
            ],
            config={"thinking_config": {"thinking_budget": thinking_budget}},
        )
        raw = (response.text or "").strip()
    finally:
        try:
            client.files.delete(name=file_name)
        except Exception:
            pass

    if not raw:
        raise RuntimeError("Gemini returned an empty caption")

    # sanitize_caption may raise RuntimeError("multiple…") — that's intentional:
    # we'd rather mark the video FAILURE than publish one of N options at random.
    caption = sanitize_caption(raw, max_len=effective_max_len)
    if not caption:
        raise RuntimeError("Caption became empty after sanitization")
    return caption


def generate_caption_for_file(
    service_account_path: str,
    file_id: str,
    local_path: str | None = None,
    tmp_dir: str | None = None,
) -> str:
    """
    Generate a caption for a Google Drive video file.

    If local_path is provided and exists, use it directly (no download).
    Otherwise, download the file first.

    Returns the caption string.
    Raises RuntimeError on failure.
    """
    from video_utils import download_drive_file, get_drive_filename, file_extension

    owns_tmp = False
    owns_file = False

    try:
        if local_path and os.path.exists(local_path):
            log.info(f"  Reusing already-downloaded file for captioning: {local_path}")
        else:
            if tmp_dir is None:
                tmp_dir = tempfile.mkdtemp(prefix="caption_")
                owns_tmp = True
            filename = get_drive_filename(service_account_path, file_id)
            ext = file_extension(filename)
            local_path = os.path.join(tmp_dir, f"{file_id}.{ext}")
            log.info(f"  Downloading for captioning: {filename}")
            download_drive_file(service_account_path, file_id, local_path)
            owns_file = True

        return generate_caption_for_local_file(local_path)

    finally:
        if owns_file and local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except OSError:
                pass
        if owns_tmp and tmp_dir and os.path.isdir(tmp_dir):
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(
        description="Generate TikTok captions for all videos in a Google Drive folder using Gemini."
    )
    parser.add_argument(
        "--folder-id",
        default=os.getenv("DRIVE_FOLDER_ID"),
        help="Google Drive folder ID containing the videos",
    )
    args = parser.parse_args()

    if not args.folder_id:
        log.error("No folder ID provided. Pass --folder-id or set DRIVE_FOLDER_ID in .env")
        sys.exit(1)

    service_account = os.getenv("SERVICE_ACCOUNT_PATH", "service_account.json")

    from video_utils import list_drive_videos
    print()
    print("=" * 60)
    print("  TikTok Caption Generator (Gemini)")
    print("=" * 60)
    print(f"  Folder ID : {args.folder_id}")
    print()

    try:
        videos = list_drive_videos(service_account, args.folder_id)
    except Exception as e:
        log.error(f"Failed to list Drive folder: {e}")
        sys.exit(1)

    if not videos:
        log.info("No videos found in the folder.")
        return

    log.info(f"Found {len(videos)} video(s). Generating captions...\n")
    succeeded = 0
    failed = 0

    for idx, video in enumerate(videos, start=1):
        print(f"[{idx}/{len(videos)}] {video['name']}")
        try:
            caption = generate_caption_for_file(
                service_account_path=service_account,
                file_id=video["id"],
            )
            print(f"  CAPTION: {caption}")
            succeeded += 1
        except RuntimeError as e:
            log.error(f"  FAILED: {e}")
            failed += 1
        print()

    print("=" * 60)
    print(f"Done — {succeeded} succeeded, {failed} failed")
    print("=" * 60)


if __name__ == "__main__":
    main()
