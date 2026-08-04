"""Layer 1 of the Writer Factory — video understanding.

One Gemini call per video. Strict JSON schema. The result is cached on
videos.understanding so all later layers (and every generation rerun)
read from DB rather than re-paying for understanding.

This module deliberately reuses the lower-level Gemini Files helpers
already proven inside generate_caption.py rather than building parallel
infrastructure. When the writer factory is fully landed (Phase 5)
generate_caption.py can be refactored to share these helpers; for
Phase 1 we just import what's there and move on.
"""
import json
import logging
import time
from pathlib import Path

import httpx

from app.writer.schemas import VideoFacts

log = logging.getLogger(__name__)

# Reuse the timeouts from generate_caption.py — same Gemini Files API
# semantics. Importing constants keeps both modules in lockstep.
from generate_caption import (
    FILE_PROCESSING_POLL,
    FILE_PROCESSING_TIMEOUT,
    GEMINI_FILE_STATUS_TIMEOUT_MS,
    GEMINI_MODEL,
    _generate_content_with_retry,
    _get_client,
)


_PROMPT_PATH = Path(__file__).parent / "prompts" / "understanding.txt"


def _read_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _wait_for_active(client, file_name: str) -> None:
    """Poll the Files API until the upload finishes processing.

    Mirrors the polling loop in generate_caption.generate_caption_for_local_file.
    Kept as a private helper so when we refactor to share, the
    refactor diff stays small.
    """
    deadline = time.time() + FILE_PROCESSING_TIMEOUT
    poll_config = {"http_options": {"timeout": GEMINI_FILE_STATUS_TIMEOUT_MS}}
    last_poll_error: Exception | None = None
    while time.time() < deadline:
        try:
            f = client.files.get(name=file_name, config=poll_config)
            last_poll_error = None
        except httpx.TransportError as e:
            last_poll_error = e
            log.warning("  Gemini file status poll failed; retrying: %s", e)
            time.sleep(FILE_PROCESSING_POLL)
            continue
        state = f.state
        state_val = state.value if hasattr(state, "value") else str(state)
        if str(state_val) in ("2", "ACTIVE"):
            return
        if str(state_val) in ("3", "FAILED"):
            raise RuntimeError(f"Gemini file processing failed (state={state_val})")
        time.sleep(FILE_PROCESSING_POLL)
    msg = (
        f"Timed out waiting for Gemini to process file after "
        f"{FILE_PROCESSING_TIMEOUT}s"
    )
    if last_poll_error is not None:
        msg += f"; last status poll failed: {last_poll_error}"
    raise RuntimeError(msg)


def extract_video_facts(local_path: str, *, model: str | None = None,
                        api_key: str | None = None) -> VideoFacts:
    """Upload `local_path` to Gemini, run the understanding prompt with
    strict JSON-schema output, and return a validated `VideoFacts`.

    The Gemini file is deleted on exit regardless of success or failure.

    Raises:
        RuntimeError — Gemini failed (timeout, FAILED state, empty output)
        pydantic.ValidationError — Gemini's JSON didn't match VideoFacts schema
    """
    client = _get_client(api_key) if api_key else _get_client()
    chosen_model = (model or "").strip() or GEMINI_MODEL

    log.info("  writer/understanding: uploading %s", Path(local_path).name)
    with open(local_path, "rb") as f:
        gemini_file = client.files.upload(
            file=f, config={"mime_type": "video/mp4"},
        )
    file_name = gemini_file.name

    try:
        _wait_for_active(client, file_name)

        log.info("  writer/understanding: generating facts (model=%s)", chosen_model)
        response = _generate_content_with_retry(
            client,
            model=chosen_model,
            contents=[
                {"role": "user", "parts": [
                    {"file_data": {"file_uri": gemini_file.uri,
                                   "mime_type": "video/mp4"}},
                    {"text": _read_prompt()},
                ]},
            ],
            config={
                # JSON output without a `response_schema`. Two attempts at
                # passing a schema (raw model_json_schema() + the Pydantic
                # class itself) both surfaced `additional_properties`
                # fields the Gemini API rejects with 400 INVALID_ARGUMENT.
                # The prompt is explicit about the required shape and
                # VideoFacts (extra='forbid') validates the result on the
                # way back — that combination is sufficient.
                "response_mime_type": "application/json",
                # No thinking budget — understanding is mechanical
                # description, not creative work. Cheaper + faster.
                "thinking_config": {"thinking_budget": 0},
            },
        )
        raw = (response.text or "").strip()
    finally:
        try:
            client.files.delete(name=file_name)
        except Exception as e:
            log.warning("  writer/understanding: failed to delete Gemini file %s: %s", file_name, e)

    if not raw:
        raise RuntimeError("Gemini returned empty understanding response")

    data = _parse_first_json_object(raw)
    return VideoFacts.model_validate(data)


def _parse_first_json_object(raw: str) -> dict:
    """Extract the first JSON object from Gemini's response.

    Gemini's JSON-mode occasionally emits trailing tokens after the
    closing brace (markdown fences, a second object, stray newlines).
    `json.loads` rejects that with `Extra data: line N column 1`.
    `JSONDecoder.raw_decode` returns the first valid object and tells
    us where it ended — anything after is discarded. We also strip the
    common ```json fence prefix some Gemini calls add despite the
    explicit `response_mime_type=application/json` config.
    """
    text = raw.strip()
    if text.startswith("```"):
        # ```json\n...\n``` or ``` ... ``` — strip the fences before parsing.
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()
    obj, _ = json.JSONDecoder().raw_decode(text.lstrip())
    if not isinstance(obj, dict):
        raise ValueError(
            f"expected top-level JSON object, got {type(obj).__name__}")
    return obj
