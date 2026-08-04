import itertools
import random
import time
from datetime import datetime
from typing import Iterable

import generate_comment as comment_generation

from app.config import SETTINGS_DEFAULTS
from app.infra import platforms as platform_adapters
from app.infra.platforms import EngagementContext, EngagementNotSupported
from app.infra.platforms.facebook import (
    FB_COMMENT_MAX_COUNT,
    FB_COMMENT_MAX_LEN,
    FB_KEYWORD_MAX_COUNT,
    FB_KEYWORD_MAX_LEN,
    FB_POST_ADDRESS_MAX_LEN,
)
from app.repositories import accounts as acc_repo
from app.repositories import comment_prompts as cp_repo
from app.repositories import engagement as eng_repo
from app.repositories import publications as pub_repo
from app.repositories import settings as set_repo
from app.services import phone_runtime
from app.services import tiktok_metadata
from app.services.platforms import LOGGED_IN_COL, validate as validate_platform
from app.services.warmup_service import LEASE_TTL_SECONDS


def _finalize_session(
        session, account_id: int | None, *, kind: str,
        owner_id: int | None,
        reason: str | None = None,
        observed_status: str | None = None) -> None:
    """Close out an engagement-owned phone_session.

    No-op when there is no in-flight session, or the in-flight session
    belongs to a different source/owner — engagement must not touch a
    warmup/publish/manual hold.

    kind ∈ {'success', 'failure', 'timeout'}.
    """
    if account_id is None:
        return
    active = phone_runtime.get_active_for_account(session, account_id)
    if active is None:
        return
    if (
        active.get("source") != "engagement"
        or active.get("owner_type") != "engagement_run"
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
            reason=reason or "engagement failed",
            observed_status=observed_status,
        )
    elif kind == "timeout":
        phone_runtime.mark_timeout(
            session, active["id"],
            reason=reason or "engagement timed out",
            observed_status=observed_status,
        )
    else:
        raise ValueError(f"unknown finalize kind {kind!r}")

# GeeLark action names — kept as module-level constants for back-compat
# with external callers (tests, ad-hoc scripts). The source of truth is
# adapter.engagement_action.
ACTION_TIKTOK_AI_COMMENT = "tiktok_ai_comment"
ACTION_FACEBOOK_AUTO_COMMENT = "facebook_auto_comment"

COMMENT_MODE_GEELARK_AI = "geelark_ai"
COMMENT_MODE_STUDIO_AI = "studio_ai"
COMMENT_MODE_PROVIDED = "provided"
COMMENT_MODES = (
    COMMENT_MODE_GEELARK_AI,
    COMMENT_MODE_STUDIO_AI,
    COMMENT_MODE_PROVIDED,
)
PROVIDED_COMMENT_MAX_LEN = 300


class PhoneBusy(RuntimeError):
    pass


class QueueBlocked(PhoneBusy):
    """Expected wait for the account's serialized phone queue."""


class ProxyUnavailable(RuntimeError):
    """Known-bad or expired account proxy; wait for operator recovery."""


class DailyLimitReached(RuntimeError):
    """Raised by submit_run_to_geelark when the account's daily comment
    quota for this platform is exhausted. The worker reschedules the
    submit task to the next UTC midnight instead of failing the run."""


def normalize_target_link(raw, *, platform: str = "tiktok"):
    """Validate + normalize a target link for the given platform.

    Routes to the platform adapter. YouTube / Instagram adapters raise
    EngagementNotSupported, which is mapped to ValueError here so the
    public contract (ValueError on bad input) stays the same.
    """
    adapter = platform_adapters.get(platform)
    if not adapter.supports_engagement:
        raise ValueError(f"unsupported platform: {platform!r}")
    try:
        return adapter.normalize_target_link(raw)
    except EngagementNotSupported as exc:
        raise ValueError(f"unsupported platform: {platform!r}") from exc


def _validate_probability(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError("comment_probability must be between 0 and 100")


def _validate_comment_config(comment_mode: str) -> str:
    mode = (comment_mode or COMMENT_MODE_GEELARK_AI).strip()
    if mode not in COMMENT_MODES:
        raise ValueError(
            "comment_mode must be studio_ai, geelark_ai, or provided"
        )
    return mode


def _sanitize_provided_pool(raw: list[str] | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("provided_comments must be strings")
        text = item.strip()
        if not text:
            continue
        if len(text) > PROVIDED_COMMENT_MAX_LEN:
            raise ValueError(
                f"provided comment exceeds {PROVIDED_COMMENT_MAX_LEN} characters"
            )
        out.append(text)
    return out


def _allocate_provided_pool(pool: list[str], total: int) -> list[str]:
    """Return ``total`` comments drawn from ``pool``.

    If the pool has at least ``total`` items, returns a random sample of
    ``total`` items (no repeats). If the pool is shorter, cycles the pool
    re-shuffled each pass so adjacent runs don't share text. ``pool`` must
    be non-empty.
    """
    if not pool:
        raise ValueError("provided_comments pool is empty")
    if total <= 0:
        return []
    if len(pool) >= total:
        shuffled = list(pool)
        random.shuffle(shuffled)
        return shuffled[:total]
    out: list[str] = []
    while len(out) < total:
        chunk = list(pool)
        random.shuffle(chunk)
        out.extend(chunk)
    return out[:total]


def _sanitize_keywords(raw: list[str] | None) -> list[str]:
    """1 ≤ N ≤ 10，每个 strip 后 ≤ 100 字，保序去重。"""
    if not raw:
        raise ValueError("keywords must contain at least one entry")
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("keywords must be strings")
        text = item.strip()
        if not text:
            continue
        if len(text) > FB_KEYWORD_MAX_LEN:
            raise ValueError(
                f"keyword exceeds {FB_KEYWORD_MAX_LEN} characters"
            )
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    if not out:
        raise ValueError("keywords must contain at least one non-empty entry")
    if len(out) > FB_KEYWORD_MAX_COUNT:
        raise ValueError(
            f"keywords must contain at most {FB_KEYWORD_MAX_COUNT} entries"
        )
    return out


def _sanitize_fb_candidates(raw: list[str] | None) -> list[str]:
    """1 ≤ N ≤ 10，每条 strip 后 ≤ 8000 字。"""
    if not raw:
        raise ValueError("fb candidates must contain at least one entry")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("fb candidates must be strings")
        text = item.strip()
        if not text:
            continue
        if len(text) > FB_COMMENT_MAX_LEN:
            raise ValueError(
                f"fb candidate exceeds {FB_COMMENT_MAX_LEN} characters"
            )
        out.append(text)
    if not out:
        raise ValueError("fb candidates must contain at least one non-empty entry")
    if len(out) > FB_COMMENT_MAX_COUNT:
        raise ValueError(
            f"fb candidates must contain at most {FB_COMMENT_MAX_COUNT} entries"
        )
    return out


def _eligible_account(account: dict | None, *, platform: str = "tiktok") -> bool:
    if not (account
            and (account.get("geelark_phone_id") or "").strip()
            and account.get("warmed_up") == 1):
        return False
    col = LOGGED_IN_COL[platform]
    return account.get(col) == 1


def _proxy_unavailable_reason(account: dict) -> str | None:
    if account.get("proxy_status") == "fail":
        return "proxy health check is failing"
    expires_at = account.get("proxy_expires_at")
    if expires_at is not None and expires_at <= datetime.utcnow():
        return "proxy is expired"
    return None


def daily_comment_limit(session, platform: str) -> int:
    """Return the configured per-phone daily comment quota, or 0 if disabled
    (or the platform is not subject to a quota). Falls back to
    SETTINGS_DEFAULTS so a freshly-deployed instance enforces the cap
    without requiring an operator to hit Save first."""
    try:
        adapter = platform_adapters.get(platform)
    except KeyError:
        return 0
    key = adapter.daily_limit_setting_key
    if not key:
        return 0
    raw = (
        set_repo.get_all(session).get(key)
        or SETTINGS_DEFAULTS.get(key)
        or "0"
    )
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _is_over_daily_limit(session, account_id: int, platform: str,
                         *, exclude_run_id: int | None = None) -> tuple[bool, int, int]:
    """Return (over_limit, used, limit) for attempts that started today."""
    limit = daily_comment_limit(session, platform)
    if limit <= 0:
        return False, 0, 0
    used = eng_repo.count_account_attempts_today(
        session, account_id=account_id, platform=platform,
        exclude_run_id=exclude_run_id,
    )
    return used >= limit, used, limit


def _publication_targets(session, publication_ids: Iterable[int],
                         *, platform: str = "tiktok") -> list[dict]:
    targets = []
    for pub_id in publication_ids:
        publication = pub_repo.get(session, pub_id)
        if publication is None:
            raise ValueError(f"publication {pub_id} does not exist")
        if publication.get("platform") != platform:
            label = "Facebook" if platform == "facebook" else "TikTok"
            raise ValueError(f"target publication must be a {label} publication")
        share_link = normalize_target_link(
            publication.get("share_link"), platform=platform,
        )
        targets.append({
            "target_publication_id": publication["id"],
            "target_account_id": publication.get("account_id"),
            "target_link": share_link,
        })
    return targets


def _expand_targets(manual_links: list[str], publication_targets: list[dict]) -> list[dict]:
    targets = [
        {
            "target_publication_id": None,
            "target_account_id": None,
            "target_link": link,
        }
        for link in manual_links
    ]
    targets.extend(publication_targets)
    return targets


def _dedupe_targets(targets: list[dict]) -> list[dict]:
    deduped = []
    indexes = {}
    for target in targets:
        link = target["target_link"]
        if link not in indexes:
            indexes[link] = len(deduped)
            deduped.append(target)
            continue

        existing_index = indexes[link]
        existing = deduped[existing_index]
        if (
            existing.get("target_publication_id") is None
            and target.get("target_publication_id") is not None
        ):
            deduped[existing_index] = target
    return deduped


def create_comment_batch(
    session,
    *,
    platform: str = "tiktok",
    account_ids: list[int],
    target_links: list[str] | None = None,
    target_publication_ids: list[int] | None = None,
    comments_per_video: int = 1,
    group_id: int | None = None,
    comment_probability: int = 100,
    comment_mode: str = COMMENT_MODE_GEELARK_AI,
    comment_prompt_id: int | None = None,
    provided_comments: list[str] | None = None,
    keywords: list[str] | None = None,
) -> dict:
    platform = validate_platform(platform)

    # ---- platform-specific validation ----
    if platform == "facebook":
        if comment_mode == COMMENT_MODE_GEELARK_AI:
            raise ValueError(
                "comment_mode 'geelark_ai' is not supported for Facebook "
                "comments; use 'studio_ai' or 'provided'."
            )
        keywords_clean = _sanitize_keywords(keywords)
        if comment_probability != 100:
            comment_probability = 100  # ignored on FB path
    else:
        if keywords:
            raise ValueError(
                "keywords are only accepted when platform='facebook'"
            )
        keywords_clean = []

    if not isinstance(account_ids, list) or not account_ids:
        raise ValueError("at least one phone must be selected")
    _validate_probability(comment_probability)
    comment_mode = _validate_comment_config(comment_mode)
    if comment_mode == COMMENT_MODE_STUDIO_AI:
        if comment_prompt_id is None:
            raise ValueError(
                "comment_prompt_id is required when comment_mode is studio_ai"
            )
        if cp_repo.get(session, comment_prompt_id) is None:
            raise ValueError(
                f"comment_prompt_id {comment_prompt_id} does not exist"
            )
    if not isinstance(comments_per_video, int) or comments_per_video < 1 or comments_per_video > 20:
        raise ValueError("comments_per_video must be between 1 and 20")
    pool: list[str] = []
    if comment_mode == COMMENT_MODE_PROVIDED:
        if platform == "facebook":
            pool = _sanitize_fb_candidates(provided_comments)
        else:
            pool = _sanitize_provided_pool(provided_comments)
        if not pool:
            raise ValueError(
                "provided_comments must contain at least one non-empty entry"
            )
    target_links = target_links or []
    target_publication_ids = target_publication_ids or []

    # Explicit list: validate each and raise if ineligible. Daily-limit
    # enforcement happens at worker submit time (see submit_run_to_geelark
    # → DailyLimitReached), so operators can queue tomorrow's work today.
    accounts = []
    for account_id in account_ids:
        account = acc_repo.get(session, account_id)
        if account is None:
            raise ValueError(f"account {account_id} does not exist")
        if not _eligible_account(account, platform=platform):
            label = (
                "Facebook commenter" if platform == "facebook"
                else "TikTok commenter"
            )
            raise ValueError(
                f"account {account_id} is not an eligible {label}"
            )
        accounts.append(account)

    if group_id is None:
        group_ids = {a.get("group_id") for a in accounts}
        group_ids.discard(None)
        group_id = next(iter(group_ids)) if len(group_ids) == 1 else None

    manual_links = [normalize_target_link(link, platform=platform)
                    for link in target_links]
    publication_targets = _publication_targets(
        session, target_publication_ids, platform=platform,
    )
    targets = _dedupe_targets(_expand_targets(manual_links, publication_targets))
    if not targets:
        raise ValueError("at least one target link is required")

    planned: list[tuple[dict, dict]] = []
    cyc = itertools.cycle(accounts)
    # FB: one run per phone-target; comments_per_video is the candidate pool
    # size inside that single run (GeeLark picks one). TikTok: one run per
    # comment per phone-target.
    inner_count = 1 if platform == "facebook" else comments_per_video
    for target in targets:
        owner_id = target.get("target_account_id")
        eligible_for_target = [a for a in accounts if owner_id != a["id"]]
        if not eligible_for_target:
            continue
        for _ in range(inner_count):
            # Spin the shared cycle past the target's owner if needed.
            # eligible_for_target is non-empty here, so at most len(accounts)
            # spins gets us to a non-owner.
            for _attempt in range(len(accounts) + 1):
                candidate = next(cyc)
                if candidate["id"] != owner_id:
                    break
            planned.append((candidate, target))

    if not planned:
        label = "Facebook" if platform == "facebook" else "TikTok"
        raise ValueError(f"no eligible {label} comment phones found")

    allocated_comments: list[str] = []
    allocated_fb_pools: list[list[str]] = []
    if comment_mode == COMMENT_MODE_PROVIDED:
        if platform == "facebook":
            flat = _allocate_provided_pool(pool, len(planned) * comments_per_video)
            allocated_fb_pools = [
                flat[i * comments_per_video:(i + 1) * comments_per_video]
                for i in range(len(planned))
            ]
        else:
            allocated_comments = _allocate_provided_pool(pool, len(planned))

    action = platform_adapters.get(platform).engagement_action

    task_ids: list[int] = []
    run_ids: list[int] = []
    for idx, (account, target) in enumerate(planned):
        source = (
            "publication_share_links"
            if target.get("target_publication_id") is not None
            else "manual_links"
        )
        task_id = eng_repo.create_task(
            session,
            group_id=group_id,
            action=action,
            source=source,
            target_links=[target["target_link"]],
            target_publication_ids=[target["target_publication_id"]]
            if target.get("target_publication_id") is not None
            else [],
            comment_probability=comment_probability,
            comment_mode=comment_mode,
            comment_prompt_id=comment_prompt_id,
            comments_per_video=comments_per_video,
            platform=platform,
            keywords=keywords_clean,
        )
        run_id = eng_repo.create_run(
            session,
            engagement_task_id=task_id,
            account_id=account["id"],
            target_link=target["target_link"],
            action=action,
            target_publication_id=target.get("target_publication_id"),
            target_account_id=target.get("target_account_id"),
        )
        if comment_mode == COMMENT_MODE_PROVIDED:
            if platform == "facebook":
                eng_repo.set_comment_candidates(
                    session, run_id,
                    candidates=allocated_fb_pools[idx],
                    source=COMMENT_MODE_PROVIDED,
                )
            else:
                eng_repo.set_generated_comment(
                    session,
                    run_id,
                    comment_text=allocated_comments[idx],
                    source=COMMENT_MODE_PROVIDED,
                )
        task_ids.append(task_id)
        run_ids.append(run_id)

    return {
        "task_id": task_ids[0],
        "task_ids": task_ids,
        "run_ids": run_ids,
        "created_runs": len(planned),
        "skipped_runs": 0,
    }


def _combined_brief(parent: dict) -> str | None:
    """Merge the legacy ``product_info`` column with ``comment_brief``.

    The UI was simplified to a single brief input, but rows persisted before
    that change may still have a non-null ``product_info``. Prepend it so the
    LLM still honours the original intent until those rows drain.
    """
    parts: list[str] = []
    legacy = (parent.get("product_info") or "").strip()
    brief = (parent.get("comment_brief") or "").strip()
    if legacy:
        parts.append(legacy)
    if brief:
        parts.append(brief)
    return "\n\n".join(parts) or None


def _prepare_studio_comment(session, run: dict, parent: dict) -> str:
    meta = tiktok_metadata.fetch_tiktok_metadata(run["target_link"])
    title = meta.get("title")
    description = meta.get("description")
    if title or description:
        eng_repo.set_target_context(
            session,
            run["id"],
            title=title,
            description=description,
        )
    prompt_id = parent.get("comment_prompt_id")
    if prompt_id is not None:
        prompt = cp_repo.get(session, prompt_id)
        if prompt is None:
            raise RuntimeError(
                f"comment_prompts row {prompt_id} vanished before submit"
            )
        prompt_body = prompt["body"]
    else:
        prompt_body = _combined_brief(parent) or ""
    from app.services.posting_service import _thinking_budget
    settings_map = {**SETTINGS_DEFAULTS, **set_repo.get_all(session)}
    from app.services.geelark_service import current_workspace_id
    from app.workspace_secrets import get_secret
    gemini_key = get_secret(
        session, current_workspace_id(session), "GEMINI_API_KEY",
    )
    secret_kwargs = {"api_key": gemini_key} if gemini_key else {}
    comment = comment_generation.generate_comment_for_target(
        prompt_body=prompt_body,
        target_title=title,
        thinking_budget=_thinking_budget(settings_map),
        model=str(settings_map.get("gemini_model") or ""),
        **secret_kwargs,
    )
    eng_repo.set_generated_comment(
        session,
        run["id"],
        comment_text=comment,
        source=COMMENT_MODE_STUDIO_AI,
    )
    return comment


def _generate_fb_studio_candidates(
        session, run: dict, parent: dict) -> list[str]:
    """Generate K fresh studio-AI Facebook candidates via Gemini and
    persist them on the run row.

    Called by comment_sources._StudioAISource.prepare_facebook_candidates.
    FB has no in-app metadata helper; pass title=None to Gemini.
    Earlier versions called tiktok_metadata.fetch_tiktok_metadata(...)
    here, but that issued real HTTP calls to TikTok's oembed API with
    an FB URL, then to Facebook itself — both timed out for 24s total
    per run.
    """
    k = max(1, int(parent.get("comments_per_video") or 1))
    title = None
    prompt_id = parent.get("comment_prompt_id")
    if prompt_id is not None:
        prompt = cp_repo.get(session, prompt_id)
        if prompt is None:
            raise RuntimeError(
                f"comment_prompts row {prompt_id} vanished before submit"
            )
        prompt_body = prompt["body"]
    else:
        prompt_body = _combined_brief(parent) or ""

    from app.services.posting_service import _thinking_budget
    settings_map = {**SETTINGS_DEFAULTS, **set_repo.get_all(session)}
    budget = _thinking_budget(settings_map)
    model = str(settings_map.get("gemini_model") or "")
    from app.services.geelark_service import current_workspace_id
    from app.workspace_secrets import get_secret
    gemini_key = get_secret(
        session, current_workspace_id(session), "GEMINI_API_KEY",
    )

    candidates: list[str] = []
    for _ in range(k):
        secret_kwargs = {"api_key": gemini_key} if gemini_key else {}
        c = comment_generation.generate_comment_for_target(
            prompt_body=prompt_body,
            target_title=title,
            thinking_budget=budget,
            model=model,
            **secret_kwargs,
        )
        candidates.append(c)
    candidates = _sanitize_fb_candidates(candidates)
    eng_repo.set_comment_candidates(
        session, run["id"], candidates=candidates,
        source=COMMENT_MODE_STUDIO_AI,
    )
    return candidates


def _fb_run_candidates(session, run: dict, parent: dict) -> list[str]:
    """Build the candidate pool for an FB engagement run via the
    matching CommentSource. Kept as a thin wrapper so callers (and
    tests) reach a stable entry point regardless of which source class
    is in play."""
    from app.services import comment_sources
    mode = parent.get("comment_mode") or COMMENT_MODE_PROVIDED
    return comment_sources.get(mode).prepare_facebook_candidates(
        session, run, parent,
    )


def submit_run_to_geelark(session, client, *, run_id: int) -> str:
    run = eng_repo.get_run(session, run_id)
    if run is None:
        raise RuntimeError(f"engagement run {run_id} not found")
    if run["result"] == "INITIATED":
        return run.get("geelark_task_id") or ""
    if run["result"] in ("SUCCESS", "FAILURE", "SKIPPED"):
        return ""
    if run["result"] not in ("PENDING", "QUEUED"):
        return ""
    if run.get("geelark_task_id") == "pending":
        # A previous submit crashed between the GeeLark create call and
        # mark_initiated — a task (and its comment) may exist on
        # GeeLark's side. Never resubmit blindly; park for manual
        # verification. (There is no auto-retry pipeline for FAILURE
        # runs, so this is terminal until a human revives it.)
        eng_repo.set_result(
            session, run_id, "FAILURE",
            "submit interrupted mid-flight; verify on GeeLark before "
            "reviving")
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        return ""

    account = acc_repo.get(session, run["account_id"])
    if account is None:
        eng_repo.set_result(session, run_id, "FAILURE", "account vanished")
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        raise RuntimeError("account vanished")

    parent = eng_repo.get_task(session, run["engagement_task_id"])
    parent_action = parent.get("action") or ACTION_TIKTOK_AI_COMMENT
    platform = parent.get("platform") or "tiktok"

    if not _eligible_account(account, platform=platform):
        eng_repo.set_result(session, run_id, "SKIPPED", "account is not eligible")
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        raise RuntimeError("account is not eligible")

    proxy_reason = _proxy_unavailable_reason(account)
    if proxy_reason is not None:
        eng_repo.mark_queued(session, run_id)
        raise ProxyUnavailable(
            f"account {account['id']} {proxy_reason}"
        )

    # Quota counts only runs that have actually started. Queued siblings are
    # backlog, not usage; counting them makes a batch larger than the limit
    # deadlock before its first run can submit.
    over_limit, used, limit = _is_over_daily_limit(
        session, account["id"], platform, exclude_run_id=run_id,
    )
    if over_limit:
        eng_repo.mark_queued(session, run_id)
        raise DailyLimitReached(
            f"account {account['id']} reached {platform} daily limit "
            f"({used}/{limit})"
        )

    if pub_repo.account_has_inflight(session, account["id"]):
        eng_repo.mark_queued(session, run_id)
        raise QueueBlocked(
            f"account {account['id']} already has an active publication")
    if eng_repo.account_has_predecessor_inflight(session, account["id"], run_id):
        eng_repo.mark_queued(session, run_id)
        raise QueueBlocked(
            f"account {account['id']} has an earlier engagement still running")

    try:
        phone_runtime.start_session(
            session,
            account_id=account["id"],
            env_id=account["geelark_phone_id"],
            source="engagement",
            owner_type="engagement_run",
            owner_id=run_id,
            budget_seconds=LEASE_TTL_SECONDS,
            created_by="engagement",
            kind="engagement",
        )
    except phone_runtime.PhoneBusy:
        eng_repo.mark_queued(session, run_id)
        raise QueueBlocked(f"account {account['id']} phone lease already held")

    schedule_at = int(time.time())

    adapter = platform_adapters.get(platform)
    if not adapter.supports_engagement:
        eng_repo.set_result(
            session, run_id, "FAILURE",
            f"unsupported engagement platform: {platform!r}",
        )
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        _stop_phone_best_effort(client, account["geelark_phone_id"])
        _finalize_session(
            session, account["id"], kind="failure",
            owner_id=run_id,
            reason=f"unsupported engagement platform: {platform!r}",
        )
        raise RuntimeError(
            f"unsupported engagement platform: {platform!r}",
        )

    # EngagementSaga step: PHONE_STARTING → PREPARING_COMMENT. Studio-AI
    # comment generation and Facebook candidate prep both run Gemini
    # synchronously; surfacing the step lets the ops view distinguish
    # "stuck on Gemini" from "stuck on GeeLark".
    active = phone_runtime.get_active_for_account(session, account["id"])
    if active is not None:
        phone_runtime.advance_task_run_state(
            session, active["id"],
            phone_runtime.TASK_STATE_PREPARING_COMMENT,
        )

    # Per-platform pre-submit data prep. The shape of EngagementContext
    # is platform-neutral; each adapter picks the fields it needs.
    ctx_kwargs: dict = {
        "env_id": account["geelark_phone_id"],
        "target_link": run["target_link"],
        "schedule_at": schedule_at,
        "remark": f"engagement_run_id={run_id}",
    }

    if platform == "tiktok":
        ctx_kwargs["name"] = "Iris AI Comment"
        from app.services import comment_sources
        parent_mode = parent.get("comment_mode") or COMMENT_MODE_GEELARK_AI
        source = comment_sources.get(parent_mode)
        try:
            comment_text = source.prepare_tiktok_comment_text(
                session, run, parent,
            )
        except Exception as exc:
            reason = (
                "provided comment text missing"
                if str(exc) == "provided comment text missing"
                else f"comment generation failed: {exc}"
            )
            eng_repo.set_result(session, run_id, "FAILURE", reason)
            eng_repo.recompute_task_status(session, run["engagement_task_id"])
            _finalize_session(
                session, account["id"], kind="failure",
                owner_id=run_id, reason=reason,
            )
            raise
        ctx_kwargs["comment_text"] = comment_text
        ctx_kwargs["comment_probability"] = int(
            parent.get("comment_probability") or 100)
    elif platform == "facebook":
        ctx_kwargs["name"] = "Iris FB Comment"
        try:
            candidates = _fb_run_candidates(session, run, parent)
        except Exception as exc:
            eng_repo.set_result(
                session, run_id, "FAILURE",
                f"candidate preparation failed: {exc}",
            )
            eng_repo.recompute_task_status(session, run["engagement_task_id"])
            _finalize_session(
                session, account["id"], kind="failure",
                owner_id=run_id,
                reason=f"candidate preparation failed: {exc}",
            )
            raise
        ctx_kwargs["candidates"] = candidates
        ctx_kwargs["keywords"] = parent.get("keywords") or []
    else:
        # supports_engagement was True but we don't know how to prepare
        # data for this platform. This is a wiring bug; surface it
        # rather than papering over.
        raise RuntimeError(
            f"engagement data prep not implemented for {platform!r}",
        )

    # Persist submit intent BEFORE the GeeLark side effect: if the worker
    # dies between the create call and mark_initiated, the placeholder
    # makes the gate refuse a blind re-run (unknown outcome = possible
    # duplicate comment). Known failures below overwrite it via
    # set_result's FAILURE terminal state.
    eng_repo.set_task_placeholder(session, run_id)
    try:
        task_id = adapter.submit_engagement(
            client, EngagementContext(**ctx_kwargs),
        )
    except Exception as exc:
        eng_repo.set_result(session, run_id, "FAILURE",
                            f"submit failed: {exc}")
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        _stop_phone_best_effort(client, account["geelark_phone_id"])
        _finalize_session(
            session, account["id"], kind="failure",
            owner_id=run_id,
            reason=f"submit failed: {exc}",
        )
        raise

    eng_repo.mark_initiated(
        session,
        run_id,
        task_id=task_id,
        schedule_at=schedule_at,
        notes="submitted to GeeLark",
    )
    # Record the GeeLark task id on the phone_session row too, so the
    # Phase-2 callback lookup can resolve it via either path.
    active = phone_runtime.get_active_for_account(session, account["id"])
    if active is not None:
        phone_runtime.attach_geelark_task(session, active["id"], task_id)
    return task_id


def _geelark_failure_detail(client, task_id: str) -> tuple[int | None, str, str | None]:
    try:
        detail = client.get_task_detail(task_id)
    except Exception:
        return None, "task failed", None

    reason = (detail.get("failDesc") or "").strip() or "task failed"
    images = detail.get("resultImages") or []
    screenshot = images[-1] if images else None
    return detail.get("failCode"), reason, screenshot


def _geelark_result_screenshot(client, task_id: str) -> str | None:
    try:
        detail = client.get_task_detail(task_id)
    except Exception:
        return None
    images = detail.get("resultImages") or []
    return images[-1] if images else None


def _stop_phone_best_effort(client, phone_id: str | None) -> None:
    if not phone_id:
        return
    try:
        client.stop_phones([phone_id])
    except Exception:
        return


def mark_run_timed_out(session, client, run_id: int, *,
                       reason: str | None = None) -> str:
    run = eng_repo.get_run(session, run_id)
    if run is None:
        return "unknown"
    if run["result"] in ("SUCCESS", "FAILURE", "SKIPPED"):
        return "success" if run["result"] == "SUCCESS" else "failure"

    # Last look before declaring death (same double-post guard as
    # posting_service.mark_publication_timed_out): a comment that went
    # terminal inside the polling blind spot must not be recorded
    # FAILURE — it is already live on the platform and would escape the
    # daily quota. "running" leaves the run INITIATED for the reclaim
    # cron to re-arm a check chain that processes the real outcome.
    gtid_seen = run.get("geelark_task_id")
    if gtid_seen:
        try:
            last_look = client.query_tasks([gtid_seen])
        except Exception:
            last_look = None  # can't tell — proceed with the verdict
        if last_look and last_look[0].get("status") in (3, 4, 7):
            return "running"

    account = acc_repo.get(session, run["account_id"])
    phone_id = account.get("geelark_phone_id") if account else None
    eng_repo.set_result(
        session,
        run_id,
        "FAILURE",
        reason or "timed out waiting for GeeLark task",
    )
    eng_repo.recompute_task_status(session, run["engagement_task_id"])
    # Cancel the GeeLark task first so the RPA loop stops; Phase 0a
    # verified cancel transitions to status=7 within ~6s.
    gtid = run.get("geelark_task_id")
    if gtid:
        try:
            client.cancel_tasks([gtid])
        except Exception:
            pass
    _stop_phone_best_effort(client, phone_id)
    if account:
        _finalize_session(
            session, account["id"], kind="timeout",
            owner_id=run_id,
            reason=reason or "timed out waiting for GeeLark task",
        )
    return "failure"


def mark_submit_retries_exhausted(session, run_id: int) -> str:
    run = eng_repo.get_run(session, run_id)
    if run is None:
        return "unknown"
    if run["result"] in ("SUCCESS", "FAILURE", "SKIPPED"):
        return "success" if run["result"] == "SUCCESS" else "failure"

    account = acc_repo.get(session, run["account_id"])
    eng_repo.set_result(
        session,
        run_id,
        "FAILURE",
        "submit retries exhausted while account was busy",
    )
    eng_repo.recompute_task_status(session, run["engagement_task_id"])
    # No release here: when PhoneBusy was raised, this run never acquired
    # the lease in the first place. Whoever DID acquire it owns the release.
    return "failure"


def check_engagement_run(session, client, run_id: int) -> str:
    run = eng_repo.get_run(session, run_id)
    if run is None:
        return "unknown"
    if run["result"] in ("SUCCESS", "FAILURE", "SKIPPED"):
        return "success" if run["result"] == "SUCCESS" else "failure"
    if run["result"] != "INITIATED" or not run.get("geelark_task_id"):
        return "unknown"

    account = acc_repo.get(session, run["account_id"])
    phone_id = account.get("geelark_phone_id") if account else None
    task_id = run["geelark_task_id"]
    # phone_sessions is the source of truth for the budget, but an
    # expired lease alone must not decide the verdict — the GeeLark
    # answer below does. Short-circuiting to timeout here recorded
    # FAILURE for comments that had already been posted inside the
    # polling gap (and let them slip past the daily quota, which only
    # counts terminal-success results).
    now = datetime.utcnow()
    active = (
        phone_runtime.get_active_for_account(session, run["account_id"])
        if account else None
    )
    lease_expired = (
        active is not None and active["budget_deadline_at"] < now
    )

    try:
        rows = client.query_tasks([task_id])
    except Exception:
        return "running"

    if not rows:
        eng_repo.set_result(session, run_id, "FAILURE", "task not found")
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        _stop_phone_best_effort(client, phone_id)
        _finalize_session(
            session, run["account_id"], kind="failure",
            owner_id=run_id,
            reason="GeeLark task not found",
        )
        return "failure"

    row = rows[0]
    status = row.get("status")
    cost = row.get("cost")
    # EngagementSaga step: a terminal-looking GeeLark status (3/4/7)
    # triggers per-platform follow-up — screenshot fetch on success,
    # detail+screenshot fetch on failure. Surface VERIFYING so a
    # stuck post-terminal probe doesn't look like a still-running RPA.
    if active is not None and isinstance(status, int) and status in (3, 4, 7):
        phone_runtime.advance_task_run_state(
            session, active["id"],
            phone_runtime.TASK_STATE_VERIFYING,
        )
    if status == 3:
        screenshot = _geelark_result_screenshot(client, task_id)
        eng_repo.set_result(
            session,
            run_id,
            "SUCCESS",
            "completed",
            cost=cost,
            result_screenshot=screenshot,
        )
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        _stop_phone_best_effort(client, phone_id)
        _finalize_session(
            session, run["account_id"], kind="success",
            owner_id=run_id,
            observed_status=str(status),
        )
        return "success"
    if status in (4, 7):
        fail_code, reason, screenshot = _geelark_failure_detail(client, task_id)
        if status == 7 and reason == "task failed":
            reason = "task cancelled"
        eng_repo.set_result(
            session,
            run_id,
            "FAILURE",
            reason,
            fail_code=fail_code,
            fail_screenshot=screenshot,
            result_screenshot=screenshot,
            cost=cost,
        )
        eng_repo.recompute_task_status(session, run["engagement_task_id"])
        _stop_phone_best_effort(client, phone_id)
        _finalize_session(
            session, run["account_id"], kind="failure",
            owner_id=run_id,
            reason=reason or f"GeeLark task status {status}",
            observed_status=str(status),
        )
        return "failure"

    # GeeLark confirmed non-terminal — only now may an expired lease
    # turn into a timeout verdict.
    if lease_expired:
        return mark_run_timed_out(
            session,
            client,
            run_id,
            reason="lease expired; GeeLark task exceeded phone runtime budget",
        )
    return "running"
