"""Warmup service — phone runtime via PhoneRuntimeManager.

Lease ownership lives entirely in phone_sessions. The T1↔T3 crash
window is detected via phone_sessions.geelark_task_id IS NULL
(session created but no GeeLark task ever attached).

start_warmup_one: T1 (start_session) → T2 (start phone + create GeeLark
task) → T3 (attach_geelark_task). Fire-and-forget — polling lives in
the procrastinate task layer.

advance_warmup_one: one GeeLark poll → DB transition (finalize on
terminal).

reclaim_in_flight_one: cron backstop. Either clears a T1↔T3 crash
window (session with no task_id) or hands off to advance_warmup_one
after renewing the lease.
"""
import logging
import re
from datetime import datetime

from app.repositories import accounts as acc_repo
from app.services import phone_runtime

logger = logging.getLogger(__name__)

# GeeLark packages the warmup RPA against a specific TikTok build; error
# 48004 names the version it wants. Prefer the mainline package, fall back
# to the Asia build — both ship every version in the installable catalog.
_TIKTOK_PACKAGES = ("com.zhiliaoapp.musically", "com.ss.android.ugc.trill")

LEASE_TTL_SECONDS = 20 * 60   # 20 minutes — covers 10-min worst-case warmup +
                              #              20-min poll chain with safety margin


class PhoneBusy(RuntimeError):
    """The phone lease is already held by another in-flight task.

    Distinct from phone_runtime.PhoneBusy so app/tasks/warmup.py can
    keep its `except warmup_service.PhoneBusy` clause untouched —
    Procrastinate's retry semantics are wired to this exact class.
    """


def _utcnow_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _finalize_session(
        session, account_id: int, *, kind: str,
        reason: str | None = None,
        observed_status: str | None = None) -> None:
    """Close out this account's active warmup phone_session.

    kind ∈ {'success', 'failure', 'timeout'}.

    No-op when there is no in-flight session, or the in-flight session
    belongs to a different source/owner — the warmup service must not
    touch a publish/engagement/manual hold.
    """
    active = phone_runtime.get_active_for_account(session, account_id)
    if active is None:
        return
    if (
        active.get("source") != "warmup"
        or active.get("owner_type") != "account"
        or active.get("owner_id") != account_id
    ):
        return
    if kind == "success":
        phone_runtime.finalize_success(
            session, active["id"], observed_status=observed_status,
        )
    elif kind == "failure":
        phone_runtime.finalize_failure(
            session, active["id"],
            reason=reason or "warmup failed",
            observed_status=observed_status,
        )
    elif kind == "timeout":
        phone_runtime.mark_timeout(
            session, active["id"],
            reason=reason or "warmup timed out",
            observed_status=observed_status,
        )
    else:
        raise ValueError(f"unknown finalize kind {kind!r}")


def _try_auto_install_tiktok(client, env_id: str, error_message: str) -> str | None:
    """Best-effort recovery for GeeLark 48004 (TikTok build too old for the
    warmup RPA). Parse the required version out of the error message, find
    its appVersionId in the installable catalog, and submit an install while
    the phone is still running — the caller's failure path stops the phone
    right after, but GeeLark keeps the APK download going server-side, so
    one of the next daily_warmup_scan retries lands on an upgraded device
    (observed converging by the 2nd-3rd attempt).

    Returns a short note for last_warm_notes, or None when out of scope.
    """
    m = re.search(r"version\s+([\d.]+)", error_message)
    if not m:
        return None
    want = m.group(1)
    try:
        catalog = client.get_installable_apps(env_id, "TikTok")
        version_id = None
        for pkg in _TIKTOK_PACKAGES:
            for app in catalog.get("items", []):
                if app.get("packageName") != pkg:
                    continue
                for v in app.get("appVersionInfoList", []):
                    if v.get("versionName") == want:
                        version_id = v.get("id")
                        break
            if version_id:
                break
        if not version_id:
            return f"auto-install: TikTok {want} not in catalog"
        try:
            client.install_app(env_id, version_id)
        except Exception as exc:
            # 42003 — an install submitted by a previous attempt is still
            # running; that is the outcome we want, not a failure.
            if "[42003]" not in str(exc):
                raise
            return f"auto-install: TikTok {want} already installing"
        return f"auto-install: TikTok {want} submitted"
    except Exception as exc:
        logger.warning(
            "auto-install TikTok %s failed for env %s: %s", want, env_id, exc)
        return None


def start_warmup_one(session, client, account_id: int,
                      duration_minutes: int | None = None) -> str:
    """Submit one warmup task to GeeLark. Returns the GeeLark task_id on
    success. Raises PhoneBusy if the lease is already held. Raises other
    exceptions on GeeLark failure (lease + phone released first)."""
    a = acc_repo.get(session, account_id)
    if a is None:
        return "skipped: account not found"
    if not (a.get("tiktok_logged_in") and a.get("geelark_phone_id")):
        return "skipped: not eligible"
    if a.get("warmup_mode") != "daily" and a.get("warmed_up"):
        return "skipped: already warmed (count mode)"

    pid = a["geelark_phone_id"]

    # T1: create phone_session + paired task_run in one DB transaction.
    try:
        phone_runtime.start_session(
            session,
            account_id=account_id,
            env_id=pid,
            source="warmup",
            owner_type="account",
            owner_id=account_id,
            budget_seconds=LEASE_TTL_SECONDS,
            created_by="warmup",
            kind="warmup",
        )
    except phone_runtime.PhoneBusy:
        raise PhoneBusy(f"phone lease busy for account {account_id}")

    # Fixed 3 minutes. Was random.randint(2, 5); operator standardized
    # to 3 so the start/boot/stop overhead amortizes the same way every
    # run, and so the polling cadence below can plan around it.
    dur = duration_minutes or 3
    try:
        # T2: start phone + submit GeeLark task
        client.start_phones([pid])
        gtid = client.create_warmup_task(pid, dur)
    except Exception as exc:
        # 48004: the phone's TikTok build is too old for the RPA. The phone
        # is still running here — submit the upgrade before stopping it.
        note = None
        if "[48004]" in str(exc):
            note = _try_auto_install_tiktok(client, pid, str(exc))
        try:
            client.stop_phones([pid])
        except Exception:
            pass
        # Order matters: write Failed state BEFORE releasing the lease,
        # so a DB crash mid-cleanup still leaves the row reflecting failure
        # (lease auto-expires in 20 min as fallback).
        notes = f"start_warmup error: {exc}"
        if note:
            notes = f"{notes} | {note}"
        acc_repo.update(session, account_id, {
            "last_warm_at": _utcnow_str(),
            "last_warm_result": "Failed",
            "last_warm_notes": notes})
        _finalize_session(
            session, account_id, kind="failure",
            reason=f"start_warmup error: {exc}",
        )
        raise

    # T3: attach the GeeLark task to the in-flight phone_session.
    active = phone_runtime.get_active_for_account(session, account_id)
    if active is not None:
        phone_runtime.attach_geelark_task(session, active["id"], gtid)
    return gtid


def _chain_owns_active_session(active, task_id: str) -> bool:
    """A poll chain may only apply side effects while the account's
    in-flight session is the warmup session it was spawned for.

    Duplicate chains happen: reclaim re-defers a check chain for a
    delayed-but-alive original (worker backlog), and both then poll the
    same GeeLark task — whose terminal status is durable, so each late
    arrival would re-apply warmup_count++ / stop_phones / result writes.
    After the first chain converges, the account may even be running a
    *publish* session on the same phone; a stale warmup timeout would
    stop it mid-post. Session gone, foreign source, or another task id
    ⇒ this chain is superseded and must go silent.
    """
    return (
        active is not None
        and active.get("source") == "warmup"
        and active.get("geelark_task_id") == task_id
    )


def advance_warmup_one(session, client, account_id: int, task_id: str) -> str:
    """One poll cycle. Returns one of:
      'running'    — GeeLark says non-terminal; caller should reschedule
      'success'    — GeeLark says status=3; warmup_count incremented, session closed
      'failure'    — GeeLark says status=4/7 OR task purged; session closed
      'missing'    — account vanished; session left as-is for watchdog
      'superseded' — this chain's session is already finalized (or the
                     phone now belongs to another source); no side effects
    """
    a = acc_repo.get(session, account_id)
    if a is None:
        return "missing"

    # phone_sessions is the source of truth: an in-flight session past
    # its budget deadline → timeout.
    now = datetime.utcnow()
    active = phone_runtime.get_active_for_account(session, account_id)
    if not _chain_owns_active_session(active, task_id):
        return "superseded"
    if active is not None and active["budget_deadline_at"] < now:
        return mark_warmup_timed_out(
            session,
            client,
            account_id,
            task_id,
            reason=f"lease expired; task {task_id} exceeded phone runtime budget",
        )

    res = client.query_tasks([task_id])
    if not res:
        # GeeLark has no record of this task — treat as terminal failure
        # so the phone stops and the lease releases. Otherwise the polling
        # chain spins forever on status=None.
        pid = a.get("geelark_phone_id")
        if pid:
            try:
                client.stop_phones([pid])
            except Exception:
                pass
        acc_repo.update(session, account_id, {
            "last_warm_at": _utcnow_str(),
            "last_warm_result": "Failed",
            "last_warm_notes": f"task {task_id} not found on GeeLark"})
        _finalize_session(
            session, account_id, kind="failure",
            reason=f"task {task_id} not found on GeeLark",
        )
        return "failure"

    status = res[0].get("status")
    if status not in (3, 4, 7):
        return "running"

    pid = a.get("geelark_phone_id")
    if pid:
        try:
            client.stop_phones([pid])
        except Exception:
            pass

    if status == 3:
        count = int(a.get("warmup_count") or 0) + 1
        target = int(a.get("target_warmup_count") or 10)
        mode = a.get("warmup_mode") or "count"
        patch = {"warmup_count": count,
                 "last_warm_at": _utcnow_str(),
                 "last_warm_result": "Success",
                 "last_warm_notes": None}
        if mode == "count" and count >= target:
            patch["warmed_up"] = 1
        # Order matters: write Success state BEFORE releasing the lease,
        # so a DB crash mid-cleanup still leaves the row reflecting completion
        # (lease auto-expires in 20 min as fallback).
        acc_repo.update(session, account_id, patch)
        _finalize_session(
            session, account_id, kind="success",
            observed_status=str(status),
        )
        return "success"

    # Order matters: write Failed state BEFORE releasing the lease,
    # so a DB crash mid-cleanup still leaves the row reflecting failure
    # (lease auto-expires in 20 min as fallback).
    acc_repo.update(session, account_id, {
        "last_warm_at": _utcnow_str(),
        "last_warm_result": "Failed",
        "last_warm_notes": f"task status {status}"})
    _finalize_session(
        session, account_id, kind="failure",
        reason=f"GeeLark task status {status}",
        observed_status=str(status),
    )
    return "failure"


def mark_warmup_timed_out(session, client, account_id: int,
                           task_id: str, *,
                           reason: str | None = None) -> str:
    """Polling chain exhausted while GeeLark still reported a non-terminal
    status. Cancel the RPA task, stop the phone, record a Failed result,
    and release the lease. Returns:
      'timeout'    — normal path
      'missing'    — account vanished
      'superseded' — session already finalized / owned by another source;
                     nothing cancelled or stopped
    """
    a = acc_repo.get(session, account_id)
    if a is None:
        return "missing"
    active = phone_runtime.get_active_for_account(session, account_id)
    if not _chain_owns_active_session(active, task_id):
        return "superseded"
    pid = a.get("geelark_phone_id")
    # Cancel the GeeLark RPA task first so its remote loop stops
    # billing-side activity (Phase 0a verified <6s transition to
    # status=7); then stop the cloud phone.
    if task_id:
        try:
            client.cancel_tasks([task_id])
        except Exception:
            pass
    if pid:
        try:
            client.stop_phones([pid])
        except Exception:
            pass
    notes = reason or f"poll timeout; task {task_id} stayed non-terminal"
    acc_repo.update(session, account_id, {
        "last_warm_at": _utcnow_str(),
        "last_warm_result": "Failed",
        "last_warm_notes": notes})
    _finalize_session(
        session, account_id, kind="timeout", reason=notes,
    )
    return "timeout"


def reclaim_in_flight_one(session, client, account_id: int) -> str:
    """Cron-side reclaim. Returns:
      'pending_cleared' — T1↔T3 crash window cleared (session had no
                          GeeLark task_id attached).
      'resume:<task_id>' — caller should defer a check_warmup_status_task.
      'no_task'         — no in-flight warmup session.
      'missing'         — account vanished.
    """
    a = acc_repo.get(session, account_id)
    if a is None:
        return "missing"
    active = phone_runtime.get_active_for_account(session, account_id)
    if active is None or active.get("source") != "warmup":
        return "no_task"
    gtid = active.get("geelark_task_id")
    if not gtid:
        # T1↔T3 crash: phone_session created but GeeLark task never
        # attached. Stop the phone and finalize the session.
        pid = a.get("geelark_phone_id")
        if pid:
            try:
                client.stop_phones([pid])
            except Exception:
                pass
        _finalize_session(
            session, account_id, kind="failure",
            reason="T1↔T3 crash window — phone_session had no task_id",
        )
        logger.warning(
            "reclaim: cleared T1↔T3 crash window for account %d",
            account_id)
        return "pending_cleared"
    # Active session with task_id: renew the budget so the poll chain
    # can resume. renew_budget CASes on stopped_at IS NULL — a failure
    # means a concurrent path just finalized the session, so there is
    # nothing left to resume.
    try:
        phone_runtime.renew_budget(
            session, active["id"], budget_seconds=LEASE_TTL_SECONDS,
        )
    except ValueError:
        return "no_task"
    return f"resume:{gtid}"
