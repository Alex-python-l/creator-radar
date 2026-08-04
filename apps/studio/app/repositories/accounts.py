from datetime import datetime, timedelta

from sqlalchemy import case, func
from sqlmodel import Session, select

from app.models import Account, Group, PhoneSession

_COLUMNS = {
    "geelark_phone_id", "profile_name", "email", "tiktok_login_info", "proxy",
    "group_name", "group_id", "tiktok_logged_in",
    "warmed_up", "warmup_count", "target_warmup_count", "warmup_mode",
    "last_warm_at",
    "last_warm_result", "last_warm_notes", "last_posted_at",
    "proxy_status", "proxy_checked_at", "proxy_exit_ip",
    "proxy_expires_at", "proxy_country", "proxy_region",
    "password", "tiktok_password", "recovery_email", "mail_inbox_url",
    "raw_credentials",
    # multi-platform-publish: per-platform login + last-post columns
    "youtube_logged_in", "youtube_login_info", "last_posted_at_youtube",
    "instagram_logged_in", "instagram_login_info", "last_posted_at_instagram",
    "facebook_logged_in", "facebook_login_info", "last_posted_at_facebook",
}

PROXY_DEFAULT_TTL = timedelta(days=30)

_RETIRED_COLUMNS = {
    "profile_pic_url",
    "profile_pic_path",
    "pic_status",
    "pic_task_id",
}


def _public_row(obj: Account) -> dict:
    row = obj.model_dump()
    for key in _RETIRED_COLUMNS:
        row.pop(key, None)
    return row


def _filter(data: dict) -> dict:
    return {k: v for k, v in data.items() if k in _COLUMNS}


def _stamp_proxy_lifecycle(d: dict, previous: str | None) -> dict:
    """Whenever a non-empty `proxy` is written and the value actually
    changed from `previous`, reset the proxy lifecycle: set
    `proxy_expires_at = now + 30d` and clear the probe-derived columns so
    the next scan repopulates them. Callers may still override any of
    these by passing them explicitly — explicit wins.
    """
    if "proxy" not in d:
        return d
    new_proxy = (d.get("proxy") or "").strip()
    prev = (previous or "").strip()
    if not new_proxy:
        if not prev:
            return d
        # Proxy removed — end its lifecycle. Leaving proxy_status='fail'
        # / proxy_expires_at behind kept the dashboard's proxy_down and
        # proxy_expiring alarms lit forever: the daily probe only scans
        # non-empty proxies, so nothing ever overwrote the stale state.
        return {
            "proxy_expires_at": None,
            "proxy_status": None,
            "proxy_checked_at": None,
            "proxy_exit_ip": None,
            "proxy_country": None,
            "proxy_region": None,
            **d,
        }
    if new_proxy == prev:
        return d
    return {
        "proxy_expires_at": datetime.utcnow() + PROXY_DEFAULT_TTL,
        "proxy_status": None,
        "proxy_checked_at": None,
        "proxy_exit_ip": None,
        "proxy_country": None,
        "proxy_region": None,
        **d,
    }


def create(session: Session, data: dict) -> int:
    d = _filter(data)
    if not d:
        raise ValueError("create() requires at least one valid column")
    d = _stamp_proxy_lifecycle(d, previous=None)
    obj = Account(**d)
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj.id


def get(session: Session, account_id: int) -> dict | None:
    obj = session.get(Account, account_id)
    return _public_row(obj) if obj else None


def list_all(session: Session) -> list[dict]:
    stmt = (
        select(Account, Group.name, PhoneSession.budget_deadline_at)
        .outerjoin(Group, Account.group_id == Group.id)
        .outerjoin(
            PhoneSession,
            (PhoneSession.account_id == Account.id)
            & (PhoneSession.stopped_at == None),  # noqa: E711
        )
        .order_by(Account.id)
    )
    rows = session.exec(stmt).all()
    out = []
    for account, group_name, active_deadline in rows:
        row = _public_row(account)
        if group_name is not None:
            row["group_name"] = group_name
        if active_deadline is not None:
            row["phone_busy_until"] = active_deadline
        out.append(row)
    return out


def update(session: Session, account_id: int, data: dict) -> bool:
    obj = session.get(Account, account_id)
    if obj is None:
        return False
    d = _filter(data)
    if not d:
        return True
    d = _stamp_proxy_lifecycle(d, previous=obj.proxy)
    for k, v in d.items():
        if hasattr(obj, k):
            setattr(obj, k, v)
    obj.updated_at = datetime.utcnow()
    session.add(obj)
    session.commit()
    return True


def profile_name_exists(session: Session, profile_name: str,
                        exclude_account_id: int | None = None) -> bool:
    stmt = select(Account.id).where(Account.profile_name == profile_name)
    if exclude_account_id is not None:
        stmt = stmt.where(Account.id != exclude_account_id)
    return session.exec(stmt).first() is not None


def delete(session: Session, account_id: int) -> None:
    obj = session.get(Account, account_id)
    if obj is None:
        return
    session.delete(obj)
    session.commit()


def unique_profile_name(session: Session, desired: str, phone_id: str) -> str:
    """A profile_name not already taken by a *different* account.

    GeeLark allows duplicate phone names, but accounts.profile_name is UNIQUE.
    On collision, append ' (2)', ' (3)', ... — stable across re-imports since
    a name the phone already owns counts as available.
    """
    base = (desired or "").strip() or phone_id
    candidate = base
    n = 1
    while True:
        obj = session.exec(
            select(Account).where(Account.profile_name == candidate)
        ).first()
        if obj is None or obj.geelark_phone_id == phone_id:
            return candidate
        n += 1
        candidate = f"{base} ({n})"


def next_serial_index(session: Session, group_id: int,
                      group_name: str) -> int:
    """Lowest integer N such that `{group_name}-NN` is not already used by
    any row in this group.

    Takes the substring of profile_name after the first `-`, stops at the
    first non-digit character, and parses what's left as an integer. So
    `g-01` -> 1, `g-01 (2)` -> 1 (a leftover from unique_profile_name still
    occupies slot 1), `g-100` -> 100. Rows where nothing parses are
    ignored. Returns 1 for an empty group.
    """
    prefix = f"{group_name}-"
    stmt = (
        select(Account.profile_name)
        .where(Account.group_id == group_id)
        .where(Account.profile_name.like(prefix + "%"))
    )
    seen: set[int] = set()
    for profile_name in session.exec(stmt).all():
        tail = profile_name[len(prefix):]
        digits = ""
        for ch in tail:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            seen.add(int(digits))
    return max(seen) + 1 if seen else 1


def upsert_by_phone_id(session: Session, phone_id: str, data: dict) -> None:
    if "profile_name" in data:
        data = {**data, "profile_name":
                unique_profile_name(session, data["profile_name"], phone_id)}
    obj = session.exec(
        select(Account).where(Account.geelark_phone_id == phone_id)
    ).first()
    if obj:
        update(session, obj.id, data)
    else:
        create(session, {**data, "geelark_phone_id": phone_id})


def list_by_group(session: Session, group_id: int) -> list[dict]:
    stmt = select(Account).where(Account.group_id == group_id).order_by(Account.id)
    rows = session.exec(stmt).all()
    return [_public_row(r) for r in rows]


def list_tiktok_commenters(session: Session, group_id: int) -> list[dict]:
    stmt = (
        select(Account)
        .where(Account.group_id == group_id)
        .where(Account.tiktok_logged_in == 1)
        .where(Account.warmed_up == 1)
        .where(Account.geelark_phone_id != None)  # noqa: E711
        .where(func.btrim(Account.geelark_phone_id) != "")
        .order_by(Account.id)
    )
    rows = session.exec(stmt).all()
    return [_public_row(r) for r in rows]


def bulk_set_logged_in(session: Session, ids: list[int]) -> int:
    if not ids:
        return 0
    stmt = select(Account).where(Account.id.in_(ids))
    rows = session.exec(stmt).all()
    for r in rows:
        r.tiktok_logged_in = 1
        r.updated_at = datetime.utcnow()
        session.add(r)
    session.commit()
    return len(rows)


def mark_warmed_up(session: Session, ids: list[int]) -> int:
    if not ids:
        return 0
    stmt = select(Account).where(Account.id.in_(ids))
    rows = session.exec(stmt).all()
    now = datetime.utcnow()
    for r in rows:
        target = r.target_warmup_count or 10
        r.target_warmup_count = target
        r.warmup_count = max(r.warmup_count or 0, target)
        r.warmed_up = 1
        r.warmup_mode = "count"   # exit daily-maintenance mode in one click
        r.last_warm_at = now
        r.last_warm_result = "Success"
        r.last_warm_notes = "Manually marked warmup complete"
        r.updated_at = now
        session.add(r)
    session.commit()
    return len(rows)


def bulk_set_warmup_mode(session: Session, ids: list[int], mode: str) -> int:
    if not ids:
        return 0
    stmt = select(Account).where(Account.id.in_(ids))
    rows = session.exec(stmt).all()
    now = datetime.utcnow()
    for r in rows:
        r.warmup_mode = mode
        # Switching to count mode is the only chance to backfill warmed_up
        # for accounts that already met target under daily mode — the warmup
        # task path (advance_warmup_one) only flips warmed_up at the moment a
        # fresh warmup succeeds, which never happens once count >= target.
        if mode == "count" and (r.warmup_count or 0) >= (r.target_warmup_count or 0):
            r.warmed_up = 1
        r.updated_at = now
        session.add(r)
    session.commit()
    return len(rows)


def count_for_group(session: Session, group_id: int) -> dict:
    """{phones, assigned, logged_in, warmed} for one group.

    `assigned` = at least one platform's login-info blob is non-empty.
    (Credentials were pasted in for at least one of TikTok/YouTube/
    Instagram/Facebook.)
    """
    has_login_info = (
        ((Account.tiktok_login_info != None) & (Account.tiktok_login_info != ""))  # noqa: E711
        | ((Account.youtube_login_info != None) & (Account.youtube_login_info != ""))  # noqa: E711
        | ((Account.instagram_login_info != None) & (Account.instagram_login_info != ""))  # noqa: E711
        | ((Account.facebook_login_info != None) & (Account.facebook_login_info != ""))  # noqa: E711
    )
    row = session.exec(
        select(
            func.count().label("phones"),
            func.sum(case((has_login_info, 1), else_=0)).label("assigned"),
            func.sum(case((Account.tiktok_logged_in == 1, 1), else_=0)).label("logged_in"),
            func.sum(case((Account.warmed_up == 1, 1), else_=0)).label("warmed"),
        ).where(Account.group_id == group_id)
    ).first()
    if row is None:
        return {"phones": 0, "assigned": 0, "logged_in": 0, "warmed": 0}
    return {
        "phones": row[0] or 0,
        "assigned": row[1] or 0,
        "logged_in": row[2] or 0,
        "warmed": row[3] or 0,
    }


def touch_last_posted(session: Session, account_id: int,
                      platform: str) -> None:
    """Stamp the per-platform last-posted timestamp on an account."""
    from app.services.platforms import LAST_POSTED_COL, validate

    col = LAST_POSTED_COL[validate(platform)]
    acc = session.get(Account, account_id)
    if acc is None:
        return
    now = datetime.utcnow()
    setattr(acc, col, now)
    acc.updated_at = now
    session.add(acc)
    session.commit()


