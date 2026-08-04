"""Publishing orchestration, publications-driven, platform-aware.

A publication row pairs one video with one target platform. This
service iterates PENDING publications for a chosen platform, picks an
eligible account for that platform, submits the platform-specific
GeeLark task, and records the resulting task ID. A separate poll
(`check_initiated` or the per-publication `check_publication_one`)
resolves INITIATED publications to SUCCESS/FAILURE later."""
import os
import time
from datetime import datetime, timedelta, timezone

from app.repositories import accounts as acc_repo
from app.repositories import settings as set_repo
from app.repositories import videos as vid_repo
from app.repositories import publications as pub_repo
from app.infra import platforms as platform_adapters
from app.infra.platforms import PublishContext, RawTaskStatus
from app.services import phone_runtime
from app.services.assignment import build_pool, assign
from app.services.platforms import (
    LAST_POSTED_COL, LOGGED_IN_COL, PLATFORMS, validate as validate_platform,
)


def _finalize_session(
        session, account_id: int | None, *, kind: str,
        owner_id: int | None,
        reason: str | None = None,
        observed_status: str | None = None) -> None:
    """Close out a publish-owned phone_session.

    No-op when there is no in-flight session, or the in-flight session
    belongs to a different source/owner — publish must not touch a
    warmup/engagement/manual hold.

    kind ∈ {'success', 'failure', 'timeout'}.
    """
    if account_id is None:
        return
    active = phone_runtime.get_active_for_account(session, account_id)
    if active is None:
        return
    if (
        active.get("source") != "publish"
        or active.get("owner_type") != "publication"
        or active.get("owner_id") != owner_id
    ):
        return
    if kind == "success":
        phone_runtime.finalize_success(
            session, active["id"], observed_status=observed_status,
        )
    elif kind == "failure":
        phone_runtime.finalize_failure(
            session, active["id"],
            reason=reason or "publish failed",
            observed_status=observed_status,
        )
    elif kind == "timeout":
        phone_runtime.mark_timeout(
            session, active["id"],
            reason=reason or "publish timed out",
            observed_status=observed_status,
        )
    else:
        raise ValueError(f"unknown finalize kind {kind!r}")


class PhoneBusy(RuntimeError):
    """Raised when a publication targets a phone already running a task."""


class CaptionPromptMissing(RuntimeError):
    """Raised when an account's group has no caption_prompt_id bound, or the
    bound prompt has been deleted. Publishing is blocked until the operator
    picks a prompt in the group settings UI."""


RETRIABLE_FAILURE_SOURCES = {"gemini", "geelark", "server"}

# Written into publications.geelark_task_id right before the GeeLark
# create call; overwritten by mark_initiated with the real task id.
# A QUEUED row still carrying it means a submit crashed mid-flight —
# outcome unknown on GeeLark's side, so it must never be auto-retried.
TASK_ID_PLACEHOLDER = "pending"


# GeeLark failure-code → user-facing hint. Add codes here as we learn them.
# Keep messages in Chinese (this dashboard's user-facing locale).
FAIL_CODE_HINTS: dict[int, str] = {
    20267: "Instagram REEL 被拒：请确认视频规格 9:16 / 3-90 秒 / H.264 mp4 / 不超过 100MB",
}


def format_failure_hint(code: int | None) -> str | None:
    """Look up a human-readable hint for a GeeLark fail code.

    Returns None for None / unknown codes — the API layer renders no hint."""
    if code is None:
        return None
    return FAIL_CODE_HINTS.get(code)


def _resolve_caption_config(session, group_id: int | None) -> dict:
    """Resolve brief/thinking_budget/model for a group.

    The brief comes from the group's bound caption_prompts row — there is no
    global fallback. A group with no prompt (or a stale FK to a deleted
    prompt) raises CaptionPromptMissing so the caller can block publish and
    surface the reason in the UI. thinking_budget is read from the global
    gemini_high_quality switch; model stays instance-wide via settings.
    """
    from app import config
    from app.repositories import caption_prompts as cp_repo
    from app.repositories import groups as g_repo
    if group_id is None:
        raise CaptionPromptMissing(
            "publication has no group_id; cannot resolve caption prompt")
    g = g_repo.get(session, group_id)
    if g is None:
        raise CaptionPromptMissing(f"group {group_id} not found")
    prompt_id = g.get("caption_prompt_id")
    if prompt_id is None:
        raise CaptionPromptMissing(
            f"group '{g.get('name')}' has no caption prompt assigned")
    prompt = cp_repo.get(session, prompt_id)
    if prompt is None:
        raise CaptionPromptMissing(
            f"group '{g.get('name')}' bound to deleted prompt {prompt_id}")
    brief = (prompt.get("body") or "").strip() or None
    if not brief:
        raise CaptionPromptMissing(
            f"caption prompt '{prompt.get('name')}' has empty body")
    s = {**config.SETTINGS_DEFAULTS, **set_repo.get_all(session)}
    return {
        "brief":           brief,
        "thinking_budget": _thinking_budget(s),
        "model":           (s.get("gemini_model") or "").strip() or None,
    }


def _thinking_budget(settings_map: dict) -> int:
    """Global Gemini thinking switch.

    Reads ``gemini_high_quality`` from settings ("1" = on). Every Gemini API
    call in the project — caption + comment — should honour this single
    switch via this helper.
    """
    return 4096 if str(settings_map.get("gemini_high_quality") or "0") == "1" else 0


def group_has_caption_prompt(session, group_id: int | None) -> bool:
    """Cheap pre-check used by auto_publish_scan and the publish endpoint to
    skip / block work for groups without a usable prompt — avoids paying for
    the per-publication GeeLark hop only to fail at caption time."""
    if group_id is None:
        return False
    try:
        _resolve_caption_config(session, group_id)
    except CaptionPromptMissing:
        return False
    return True


def _caption(session, local_path: str, acc: dict) -> str:
    from generate_caption import generate_caption_for_local_file
    from app.services.geelark_service import current_workspace_id
    from app.workspace_secrets import get_secret
    cfg = _resolve_caption_config(session, acc.get("group_id"))
    workspace_id = current_workspace_id(session)
    kwargs = {}
    gemini_key = get_secret(session, workspace_id, "GEMINI_API_KEY")
    if gemini_key:
        kwargs["api_key"] = gemini_key
    return generate_caption_for_local_file(
        local_path,
        brief=cfg["brief"],
        thinking_budget=cfg["thinking_budget"],
        model=cfg["model"],
        **kwargs,
    )


def _caption_for_publication(session, pub: dict, local_path: str,
                             acc: dict) -> str:
    """Video-level caption cache backed by sibling publications.

    The schema stores captions on publications, so the first successful
    generation for a video is copied into its publication immediately.
    Later platform publications for the same video reuse that caption.
    """
    for sibling in pub_repo.list_for_video(session, pub["video_id"]):
        caption = (sibling.get("caption") or "").strip()
        if caption:
            return sibling["caption"]
    caption = _caption(session, local_path, acc)
    pub_repo.set_caption(session, pub["id"], caption)
    return caption


def _epoch(ts):
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc).timestamp()
        return ts.timestamp()
    try:
        return (
            datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
    except (ValueError, TypeError):
        return None


def _account_available_for_platform(account: dict | None, platform: str,
                                    *, cooldown_hours: float) -> bool:
    if not account:
        return False
    if not account.get("warmed_up"):
        return False
    if not account.get(LOGGED_IN_COL[platform]):
        return False
    if not (account.get("profile_name") or "").strip():
        return False
    last = _epoch(account.get(LAST_POSTED_COL[platform]))
    cutoff = time.time() - cooldown_hours * 3600
    return last is None or last < cutoff


def _account_matches_video_group(account: dict | None, pub: dict) -> bool:
    group_id = pub.get("group_id")
    return bool(account) and group_id is not None \
        and account.get("group_id") == group_id


def _retry_ready(pub: dict) -> bool:
    next_retry = _epoch(pub.get("next_retry_at"))
    return next_retry is None or next_retry <= time.time()


def _cooldown_hours_for_publication(pub: dict, cooldown_hours: float) -> float:
    if int(pub.get("retry_count") or 0) > 0:
        return 0
    return cooldown_hours


def _publish_retry_delays_seconds(session) -> list[int]:
    from app import config

    s = {**config.SETTINGS_DEFAULTS, **set_repo.get_all(session)}
    raw = str(s.get("publish_retry_delays_seconds") or "").strip()
    if not raw:
        return []
    delays: list[int] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            value = int(piece)
        except ValueError:
            continue
        if value > 0:
            delays.append(value)
    return delays


def schedule_retry_if_allowed(session, publication_id: int) -> datetime | None:
    pub = pub_repo.get(session, publication_id)
    if pub is None:
        return None
    attempts = pub_repo.list_attempts(session, publication_id)
    latest = attempts[-1] if attempts else None
    if not latest or latest.get("source") not in RETRIABLE_FAILURE_SOURCES:
        return None
    delays = _publish_retry_delays_seconds(session)
    retry_count = int(pub.get("retry_count") or 0)
    if retry_count >= len(delays):
        return None
    retry_at = datetime.utcnow() + timedelta(seconds=delays[retry_count])
    won = pub_repo.schedule_retry(
        session,
        publication_id,
        notes=pub.get("notes") or latest["message"],
        next_retry_at=retry_at,
        fail_code=pub.get("fail_code"),
        fail_screenshot=pub.get("fail_screenshot"),
    )
    # Lost the race — a concurrent resolver already scheduled this retry
    # (or the row moved on); don't report a retry we didn't book.
    return retry_at if won else None


def next_publish_scan_ready_at(
        session, *, cooldown_hours: float,
        limit_per_platform: int | None = None) -> datetime | None:
    from app.repositories import engagement as eng_repo

    accounts = acc_repo.list_all(session)
    by_account_id = {a["id"]: a for a in accounts}
    now = time.time()
    candidates: list[float] = []

    for platform in PLATFORMS:
        last_col = LAST_POSTED_COL[platform]
        for pub in pub_repo.list_pending(
                session, platform=platform, limit=limit_per_platform,
                require_group=True):
            next_retry = _epoch(pub.get("next_retry_at"))
            if next_retry is not None and next_retry > now:
                candidates.append(next_retry)
                continue
            pool = (
                [by_account_id.get(pub["account_id"])]
                if pub.get("account_id") is not None
                else accounts
            )
            for account in pool:
                if not _account_matches_video_group(account, pub):
                    continue
                if not account.get("warmed_up"):
                    continue
                if not account.get(LOGGED_IN_COL[platform]):
                    continue
                if not (account.get("profile_name") or "").strip():
                    continue
                if pub_repo.account_has_inflight(session, account["id"]):
                    continue
                if eng_repo.account_has_inflight(session, account["id"]):
                    continue
                last = _epoch(account.get(last_col))
                if last is None:
                    continue
                ready = last + cooldown_hours * 3600
                if ready > now:
                    candidates.append(ready)

    if not candidates:
        return None
    return datetime.utcfromtimestamp(min(candidates))


def _submit_via_adapter(client, platform: str, env_id: str,
                         resource_url: str, caption: str) -> str:
    """Route to the platform's adapter for caption validation +
    GeeLark task submission. Replaces the old per-platform branch."""
    adapter = platform_adapters.get(platform)
    ctx = PublishContext(
        env_id=env_id,
        resource_url=resource_url,
        caption=adapter.validate_caption(caption),
    )
    return adapter.submit_publish(client, ctx)


def _stored_file_missing_message(stored_path: str) -> str:
    return f"stored file missing on server: {stored_path}"


def _exception_message(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__


def _record_attempt_and_fail(
    session,
    publication_id: int,
    *,
    stage: str,
    source: str,
    message: str,
    notes: str | None = None,
    error_code: str | None = None,
    fail_screenshot: str | None = None,
    geelark_task_id: str | None = None,
) -> None:
    pub_repo.record_attempt(
        session,
        publication_id,
        stage=stage,
        source=source,
        message=message,
        error_code=error_code,
        fail_screenshot=fail_screenshot,
        geelark_task_id=geelark_task_id,
    )
    pub_repo.mark_failure(session, publication_id, notes or message)


def check_initiated(session, client) -> str:
    """Synchronous poll across all platforms. Used by the legacy path.
    Cleans up the local stored_path file when every publication of a
    video is successful."""
    rows = pub_repo.list_initiated(session)
    if not rows:
        return "0 pending"
    ok = fail = 0
    for p in rows:
        outcome = check_publication_one(session, client, p["id"])
        if outcome == "success":
            ok += 1
        elif outcome in ("failure", "timeout"):
            schedule_retry_if_allowed(session, p["id"])
            fail += 1
    for vid in pub_repo.video_ids_all_success(session):
        v = vid_repo.get(session, vid)
        if not v or not v.get("stored_path"):
            continue
        try:
            os.remove(v["stored_path"])
        except OSError:
            pass
        vid_repo.clear_stored_path(session, vid)
    return f"{ok} success, {fail} failure"


# ----- Per-publication path (Procrastinate task callers) ----- #

def plan_assignments(session, *, platform: str,
                     cooldown_hours: float = 2,
                     limit: int | None = None) -> list[int]:
    """Pick eligible (warmed-up + logged-in + outside cooldown) accounts
    for PENDING publications of `platform`. For each chosen publication:
    if the publication has no account_id yet, write it; flip the
    publication to QUEUED. Returns the publication IDs the caller should
    defer."""
    from app.repositories import engagement as eng_repo

    platform = validate_platform(platform)
    accounts = acc_repo.list_all(session)
    by_account_id = {a["id"]: a for a in accounts}
    # Pre-compute which groups have at least one warmed-up, platform-logged-in
    # account; pass to list_pending so the id-ASC LIMIT window doesn't get
    # poisoned by groups where no work is assignable for this platform (e.g.
    # tiktok-only groups full of facebook PENDINGs).
    logged_col = LOGGED_IN_COL[platform]
    eligible_groups = {
        a["group_id"] for a in accounts
        if a.get("group_id") is not None
        and a.get("warmed_up")
        and a.get(logged_col)
    }
    pending = [
        p for p in pub_repo.list_pending(
            session, platform=platform, limit=limit,
            require_group=True,
            in_groups=sorted(eligible_groups))
        if _retry_ready(p)
    ]
    if not pending:
        return []

    # Split into "publication already has a phone" vs "needs assignment".
    already = [p for p in pending if p.get("account_id") is not None]
    needs_assign = [p for p in pending if p.get("account_id") is None]

    queued: list[int] = []
    used_account_ids: set[int] = set()
    # For publications whose video already has a phone, QUEUE immediately.
    for p in already:
        account = by_account_id.get(p["account_id"])
        if account and account["id"] in used_account_ids:
            continue
        if account and pub_repo.account_has_inflight(
                session, account["id"], exclude_publication_id=p["id"]):
            continue
        if account and eng_repo.account_has_inflight(session, account["id"]):
            continue
        if not _account_matches_video_group(account, p):
            continue
        if not _account_available_for_platform(
                account,
                platform,
                cooldown_hours=_cooldown_hours_for_publication(
                    p, cooldown_hours)):
            continue
        pub_repo.mark_queued(session, p["id"])
        used_account_ids.add(account["id"])
        queued.append(p["id"])

    if not needs_assign:
        return queued

    last_col = LAST_POSTED_COL[platform]
    for p in needs_assign:
        candidates = [
            a for a in accounts
            if a["id"] not in used_account_ids
            and _account_matches_video_group(a, p)
            and not pub_repo.account_has_inflight(session, a["id"])
            and not eng_repo.account_has_inflight(session, a["id"])
            and _account_available_for_platform(
                a,
                platform,
                cooldown_hours=_cooldown_hours_for_publication(
                    p, cooldown_hours))
        ]
        candidates.sort(key=lambda a: (
            _epoch(a.get(last_col))
            if _epoch(a.get(last_col)) is not None else 0
        ))
        if not candidates:
            continue
        chosen = candidates[0]
        pub_repo.set_account(session, p["id"], chosen["id"])
        pub_repo.mark_queued(session, p["id"])
        used_account_ids.add(chosen["id"])
        queued.append(p["id"])
    return queued


def submit_publication_to_geelark(session, client, *,
                                  publication_id: int) -> str:
    """Single-publication submit used by publish_publication_task.
    On caption/upload/submit failure, marks the publication FAILED and
    re-raises so the task framework can record the error."""
    from app.repositories import engagement as eng_repo

    pub = pub_repo.get(session, publication_id)
    if pub is None:
        raise RuntimeError(f"publication {publication_id} not found")
    v = vid_repo.get(session, pub["video_id"])
    group_id = v.get("group_id") if v else None
    if not group_has_caption_prompt(session, group_id):
        _record_attempt_and_fail(
            session,
            publication_id,
            stage="preflight",
            source="account_config",
            message="group has no caption prompt assigned",
            notes="group has no caption prompt assigned",
        )
        raise CaptionPromptMissing(
            f"group {group_id} has no caption prompt")
    # Idempotency gate (pub 6180 incident 2026-06-09): only QUEUED rows are
    # eligible for submit. Anything else — INITIATED/SUCCESS (already at
    # GeeLark), FAILURE (terminal, awaiting manual reset), or PENDING (race
    # with another defer that hasn't claimed yet) — must NOT touch GeeLark
    # again. Without this, a stale defer or worker-restart re-enqueue
    # creates a second GeeLark task and posts the same video twice.
    if pub["result"] != "QUEUED":
        return pub.get("geelark_task_id") or ""
    # 'pending' placeholder = a previous submit crashed between the
    # GeeLark create call and mark_initiated, so a task may or may not
    # exist on GeeLark's side. Re-submitting could post the video twice;
    # go silent and let the QUEUED reaper route the row to FAILURE for
    # manual verification.
    if pub.get("geelark_task_id") == TASK_ID_PLACEHOLDER:
        return ""
    v = vid_repo.get(session, pub["video_id"])
    if v is None or not v.get("stored_path"):
        _record_attempt_and_fail(
            session,
            publication_id,
            stage="preflight",
            source="server",
            message="stored_path missing",
        )
        raise RuntimeError("stored_path missing")
    if not os.path.exists(v["stored_path"]):
        message = _stored_file_missing_message(v["stored_path"])
        pub_repo.mark_failure(
            session,
            publication_id,
            message,
        )
        pub_repo.record_attempt(
            session,
            publication_id,
            stage="preflight",
            source="server",
            message=message,
        )
        raise FileNotFoundError(v["stored_path"])
    account_id = pub.get("account_id")
    if account_id is None:
        _record_attempt_and_fail(
            session,
            publication_id,
            stage="preflight",
            source="account_config",
            message="publication has no assigned phone",
        )
        raise RuntimeError(
            f"publication {publication_id} has no account_id; "
            f"plan_assignments should have stamped one")
    acc = acc_repo.get(session, account_id)
    if acc is None:
        _record_attempt_and_fail(
            session,
            publication_id,
            stage="preflight",
            source="account_config",
            message="account vanished",
        )
        raise RuntimeError("account vanished")
    pub_with_group = {**pub, "group_id": group_id}
    if not _account_matches_video_group(acc, pub_with_group):
        _record_attempt_and_fail(
            session,
            publication_id,
            stage="preflight",
            source="account_config",
            message="assigned account does not belong to the video's group",
        )
        raise RuntimeError(
            "assigned account does not belong to the video's group")
    # PhoneBusy paths deliberately KEEP the row QUEUED. The task-layer
    # retry loop (40 x 30s) re-enters this function, and the idempotency
    # gate above only admits QUEUED rows — unclaiming to PENDING made
    # every retry a silent no-op, so "wait for the phone to free up"
    # degraded into "wait for the next hourly scan". If the retry budget
    # runs out, the QUEUED-orphan reaper returns the row to PENDING.
    if eng_repo.account_has_inflight(session, account_id):
        raise PhoneBusy(
            f"account {account_id} already has an active engagement task")
    if pub_repo.account_has_inflight(
            session,
            account_id,
            exclude_publication_id=publication_id,
            results=("INITIATED",)):
        raise PhoneBusy(f"account {account_id} already has an active task")
    from app.services.warmup_service import LEASE_TTL_SECONDS
    try:
        phone_runtime.start_session(
            session,
            account_id=account_id,
            env_id=acc["geelark_phone_id"],
            source="publish",
            owner_type="publication",
            owner_id=publication_id,
            budget_seconds=LEASE_TTL_SECONDS,
            created_by="publish",
            kind="publish",
        )
    except phone_runtime.PhoneBusy:
        raise PhoneBusy(
            f"account {account_id} phone lease already held")
    # Persist submit intent BEFORE any GeeLark side effect. If the worker
    # dies between the create call below and mark_initiated, the row
    # still carries the placeholder and the gate refuses a blind re-run.
    pub_repo.set_task_placeholder(session, publication_id)
    try:
        caption = _caption_for_publication(session, pub, v["stored_path"], acc)
    except Exception as e:
        message = _exception_message(e)
        _record_attempt_and_fail(
            session,
            publication_id,
            stage="caption",
            source="gemini",
            message=message,
            notes=f"caption failed: {message}",
        )
        # Order matters: write FAILURE state BEFORE releasing the lease,
        # so a DB crash mid-cleanup still leaves the row reflecting failure
        # (lease auto-expires in 20 min as fallback).
        _finalize_session(
            session, account_id, kind="failure",
            owner_id=publication_id,
            reason=f"caption failed: {message}",
        )
        raise
    ext = os.path.splitext(v["original_filename"] or "")[1].lstrip(".") or "mp4"
    # PublishSaga step: PHONE_STARTING → UPLOADING_RESOURCE. The
    # upload is the slowest single op in the chain; surfacing it as a
    # distinct step lets the ops view distinguish "waiting on upload"
    # from "waiting on GeeLark RPA".
    active = phone_runtime.get_active_for_account(session, account_id)
    if active is not None:
        phone_runtime.advance_task_run_state(
            session, active["id"], phone_runtime.TASK_STATE_UPLOADING,
        )
    try:
        res_url = client.upload_file(v["stored_path"], ext)
    except Exception as e:
        message = _exception_message(e)
        _record_attempt_and_fail(
            session,
            publication_id,
            stage="upload",
            source="geelark",
            message=message,
            notes=f"upload failed: {message}",
        )
        # Same ordering as caption path: record FAILURE before releasing lease.
        _finalize_session(
            session, account_id, kind="failure",
            owner_id=publication_id,
            reason=f"upload failed: {message}",
        )
        raise
    try:
        task_id = _submit_via_adapter(
            client, pub["platform"],
            acc["geelark_phone_id"], res_url, caption)
    except Exception as e:
        message = _exception_message(e)
        _record_attempt_and_fail(
            session,
            publication_id,
            stage="submit",
            source="geelark",
            message=message,
            notes=f"submit failed: {message}",
        )
        # Same ordering as caption path: record FAILURE before releasing lease.
        _finalize_session(
            session, account_id, kind="failure",
            owner_id=publication_id,
            reason=f"submit failed: {message}",
        )
        raise
    pub_repo.mark_initiated(session, publication_id,
                            caption, task_id, "submitted to GeeLark")
    acc_repo.touch_last_posted(session, account_id, pub["platform"])
    # Record the GeeLark task id on the phone_session row too, so the
    # Phase-2 callback lookup can resolve it via either path.
    active = phone_runtime.get_active_for_account(session, account_id)
    if active is not None:
        phone_runtime.attach_geelark_task(session, active["id"], task_id)
    return task_id


def mark_publication_timed_out(
        session, client, publication_id: int, *,
        reason: str | None = None) -> str:
    """Poll chain exhausted while GeeLark still reported non-terminal.
    Stop the phone, mark the publication FAILURE, release the lease.
    Returns 'timeout' on the normal path, or 'unknown' / 'success' /
    'failure' if the publication already left INITIATED concurrently.
    """
    pub = pub_repo.get(session, publication_id)
    if pub is None:
        return "unknown"
    if pub["result"] in ("SUCCESS", "FAILURE"):
        return "success" if pub["result"] == "SUCCESS" else "failure"

    # Last look before declaring death. The poll chain has a blind spot
    # (up to CHECK_INTERVAL between the final poll and this verdict, or
    # the whole flight when the chain died early and reclaim landed here
    # via the expired-lease shortcut). A task that actually reached a
    # terminal status in that gap must NOT be recorded FAILURE — the
    # retry path would then re-post a video that is already live.
    # Returning "running" leaves the publication INITIATED; the reclaim
    # cron re-arms a check chain within 5 minutes and the normal
    # convergence path (verify_real etc.) processes the real outcome.
    gtid_seen = pub.get("geelark_task_id")
    if gtid_seen:
        try:
            last_look = client.query_tasks([gtid_seen])
        except Exception:
            last_look = None  # can't tell — proceed with the verdict
        if last_look and last_look[0].get("status") in (3, 4, 7):
            return "running"

    account_id = pub.get("account_id")
    acc = acc_repo.get(session, account_id) if account_id else None
    phone_id = acc.get("geelark_phone_id") if acc else None

    notes = reason or "poll timeout; GeeLark task stayed non-terminal"
    pub_repo.record_attempt(
        session,
        publication_id,
        stage="status_check",
        source="server",
        message=notes,
        geelark_task_id=pub.get("geelark_task_id"),
    )
    pub_repo.set_result(session, publication_id, "FAILURE", notes)
    # Cancel the GeeLark task first so the RPA loop stops; then stop
    # the cloud phone. Phase 0a verified cancel transitions task to
    # status=7 within ~6s.
    gtid = pub.get("geelark_task_id")
    if gtid:
        try:
            client.cancel_tasks([gtid])
        except Exception:
            pass
    _stop_phone_best_effort(client, phone_id)
    _finalize_session(
        session, account_id, kind="timeout", owner_id=publication_id,
        reason=notes,
    )
    return "timeout"


def _stop_phone_best_effort(client, phone_id: str | None) -> None:
    """Stop a GeeLark cloud phone, swallowing transient errors.

    GeeLark bills per minute of phone uptime — finalizing the phone_session
    frees the *DB* lease but does NOT stop the env. Every publish
    terminal branch must call this or the phone keeps running until
    something else stops it (worst case: never).
    """
    if not phone_id:
        return
    try:
        client.stop_phones([phone_id])
    except Exception:
        return


def check_publication_one(session, client, publication_id: int) -> str:
    """Single-publication status poll used by check_publication_status_task.
    Returns 'running' | 'success' | 'failure' | 'timeout' | 'unknown'."""
    pub = pub_repo.get(session, publication_id)
    if pub is None:
        return "unknown"
    if pub["result"] in ("SUCCESS", "FAILURE"):
        return "success" if pub["result"] == "SUCCESS" else "failure"
    if pub["result"] != "INITIATED" or not pub["geelark_task_id"]:
        return "unknown"

    acc = acc_repo.get(session, pub["account_id"]) if pub.get("account_id") else None
    # phone_sessions is the source of truth for the budget, but an
    # expired lease alone must not decide the verdict — the GeeLark
    # answer below does. Declaring timeout before asking (the old
    # shortcut here) marked publications FAILURE whose RPA had already
    # finished inside the polling gap, and the retry path then posted
    # them a second time.
    now = datetime.utcnow()
    active = (
        phone_runtime.get_active_for_account(session, pub["account_id"])
        if pub.get("account_id") else None
    )
    lease_expired = (
        active is not None and active["budget_deadline_at"] < now
    )

    try:
        res = client.query_tasks([pub["geelark_task_id"]])
    except Exception:
        return "running"  # transient — retry

    phone_id = acc.get("geelark_phone_id") if acc else None

    if not res:
        pub_repo.record_attempt(
            session,
            publication_id,
            stage="status_check",
            source="geelark",
            message="task not found",
            geelark_task_id=pub["geelark_task_id"],
        )
        pub_repo.set_result(session, publication_id, "FAILURE",
                            "task not found")
        _stop_phone_best_effort(client, phone_id)
        _finalize_session(
            session, pub["account_id"], kind="failure",
            owner_id=publication_id,
            reason="GeeLark task not found",
        )
        return "failure"
    status_raw = res[0].get("status")
    try:
        status_int = int(status_raw) if status_raw is not None else -1
    except (TypeError, ValueError):
        status_int = -1
    raw_status = RawTaskStatus(
        task_id=pub["geelark_task_id"],
        status=status_int,
        share_link=res[0].get("shareLink"),
        raw=res[0],
    )
    # PublishSaga step: a terminal-looking GeeLark status (3/4/7)
    # triggers a platform-specific second confirmation
    # (adapter.verify_real). Mark VERIFYING so a stuck audit lookup —
    # notably YouTube's audit_screenshot probe — is visible on the
    # ops dashboard separately from a still-running RPA.
    if active is not None and status_int in (3, 4, 7):
        phone_runtime.advance_task_run_state(
            session, active["id"], phone_runtime.TASK_STATE_VERIFYING,
        )
    adapter = platform_adapters.get(pub["platform"])
    outcome = adapter.verify_real(client, raw_status)

    if outcome.state == "success":
        if outcome.share_link:
            pub_repo.set_share_link(
                session, publication_id, outcome.share_link)
        if outcome.audit_screenshot:
            # Adapter-supplied audit evidence (e.g. YouTube final RPA
            # screenshot) — record on the attempt row for operator
            # follow-up. Does NOT block success in this iteration.
            pub_repo.record_attempt(
                session,
                publication_id,
                stage="status_check",
                source="geelark",
                message=(
                    f"{pub['platform']} task reported "
                    f"status={outcome.observed_status}"
                ),
                fail_screenshot=outcome.audit_screenshot,
                geelark_task_id=pub["geelark_task_id"],
            )
        pub_repo.set_result(session, publication_id, "SUCCESS", "completed")
        _stop_phone_best_effort(client, phone_id)
        _finalize_session(
            session, pub["account_id"], kind="success",
            owner_id=publication_id,
            observed_status=outcome.observed_status,
        )
        _maybe_cleanup_video(session, pub["video_id"])
        return "success"

    if outcome.state == "failure":
        reason = outcome.fail_reason or f"GeeLark task status {status_raw}"
        pub_repo.record_attempt(
            session,
            publication_id,
            stage="status_check",
            source="geelark",
            message=reason,
            error_code=(
                str(outcome.fail_code)
                if outcome.fail_code is not None else None
            ),
            fail_screenshot=outcome.fail_screenshot,
            geelark_task_id=pub["geelark_task_id"],
        )
        pub_repo.set_result(
            session, publication_id, "FAILURE", reason,
            fail_code=outcome.fail_code,
            fail_screenshot=outcome.fail_screenshot,
        )
        _stop_phone_best_effort(client, phone_id)
        _finalize_session(
            session, pub["account_id"], kind="failure",
            owner_id=publication_id,
            reason=reason,
            observed_status=outcome.observed_status,
        )
        _maybe_cleanup_video(session, pub["video_id"])
        return "failure"

    # GeeLark confirmed non-terminal. Only now is an expired lease a
    # timeout verdict — the evidence and the budget agree the flight is
    # both overdue and genuinely still running.
    if lease_expired:
        return mark_publication_timed_out(
            session,
            client,
            publication_id,
            reason="lease expired; GeeLark task exceeded phone runtime budget",
        )
    return "running"


def _maybe_cleanup_video(session, video_id: int) -> None:
    """If every publication for `video_id` is SUCCESS, remove the local
    file and clear stored_path."""
    success = pub_repo.video_ids_all_success(session)
    if video_id not in success:
        return
    v = vid_repo.get(session, video_id)
    if not v or not v.get("stored_path"):
        return
    try:
        os.remove(v["stored_path"])
    except OSError:
        pass
    vid_repo.clear_stored_path(session, video_id)
