"""Wraps the reused geelark_client.GeeLarkClient. All functions take an
explicit client so they are unit-testable with a fake."""
import time
from urllib.parse import quote
from sqlalchemy import text
from sqlmodel import Session
from app import config
from app.db import set_workspace_context
from app.repositories import accounts as acc_repo
from app.workspace_secrets import get_secret

# GeeLark phone status codes (see Cloud Phone API / Query_status):
#   0 = Started, 1 = Starting, 2 = Shut down, 3 = Expired
_RUNNING_STATUSES = (0, 1)
# Errors in failDetails we can safely treat as "already gone" when deleting.
_GEELARK_NOT_FOUND = 42001

def current_workspace_id(session: Session) -> int:
    value = session.execute(text(
        "SELECT NULLIF(current_setting('app.workspace_id', true), '')::bigint"
    )).scalar_one_or_none()
    if value is None:
        if config.STUDIO_DEFAULT_WORKSPACE_ID:
            return int(config.STUDIO_DEFAULT_WORKSPACE_ID)
        raise RuntimeError("workspace_id is required for GeeLark operations")
    return int(value)


def workspace_for_row(session: Session, table: str, row_id: int) -> int:
    allowed = {"accounts", "publications", "engagement_runs", "videos"}
    if table not in allowed:
        raise ValueError("unsupported workspace lookup")
    value = session.execute(
        text(f"SELECT workspace_id FROM {table} WHERE id = :row_id"),
        {"row_id": row_id},
    ).scalar_one_or_none()
    if value is None:
        if config.STUDIO_DEFAULT_WORKSPACE_ID:
            value = int(config.STUDIO_DEFAULT_WORKSPACE_ID)
        else:
            raise RuntimeError(f"{table} row not found")
    set_workspace_context(session, int(value))
    return int(value)


def make_client(session: Session, workspace_id: int | None = None):
    from geelark_client import GeeLarkClient
    wid = workspace_id or current_workspace_id(session)
    token = get_secret(session, wid, "GEELARK_TOKEN")
    if not token and config.STUDIO_DEFAULT_WORKSPACE_ID:
        # Explicit test/migration compatibility only. Production leaves this
        # env unset and therefore cannot fall back to global credentials.
        token = config.GEELARK_TOKEN
    if token is None:
        raise RuntimeError("GEELARK_TOKEN is not configured for this workspace")
    return GeeLarkClient(token=token)


def workspace_client(session: Session, workspace_id: int | None = None):
    """Call the workspace-aware factory while tolerating old zero-arg fakes."""
    try:
        return make_client(session, workspace_id)
    except TypeError:
        return make_client()  # type: ignore[call-arg]

def _format_proxy(obj) -> str:
    """Turn GeeLark's proxy object into a `scheme://user:pass@host:port` URL.

    GeeLark returns proxy as a dict like
        {"type":"socks5","server":"h","port":1080,"username":"u","password":"p"}
    The accounts table stores it as a string (editable as one cell in the UI),
    so we serialize it here. Returns "" for anything unusable (no server/port).
    """
    if not isinstance(obj, dict):
        return ""
    server = (obj.get("server") or "").strip()
    port = obj.get("port")
    if not server or not port:
        return ""
    scheme = (obj.get("type") or "").strip() or "socks5"
    user = obj.get("username") or ""
    pwd = obj.get("password") or ""
    if user or pwd:
        return f"{scheme}://{quote(user, safe='')}:{quote(pwd, safe='')}@{server}:{port}"
    return f"{scheme}://{server}:{port}"

def import_from_geelark(session: Session, client) -> int:
    """Pull every cloud phone and upsert an accounts row keyed by phone id.
    Only fields GeeLark actually returns are written; email/pic/warmup stay
    blank (documented migration tradeoff).

    Proxy is overwritten only when the existing row has none — preserves any
    proxy the user edited by hand in the dashboard.
    """
    from app.repositories import groups as grp_repo

    imported = 0
    page = 1
    while True:
        data = client.list_phones(page=page, page_size=100)
        items = data.get("items", []) or data.get("list", [])
        if not items:
            break
        for it in items:
            phone_id = it.get("id") or it.get("envId")
            if not phone_id:
                continue
            name = (it.get("serialName") or it.get("name") or "").strip()
            group = ""
            g = it.get("group")
            if isinstance(g, dict):
                group = g.get("name", "")
            elif isinstance(g, str):
                group = g
            fields = {"profile_name": name, "group_name": group}

            # Look up or create group and set group_id
            if group:
                g_row = grp_repo.get_by_name(session, group)
                if g_row is None:
                    # Create the group lazily so the imported phone is reachable via group_id
                    grp_repo.create(session, name=group)
                    g_row = grp_repo.get_by_name(session, group)
                if g_row:
                    fields["group_id"] = g_row["id"]

            new_proxy = _format_proxy(it.get("proxy"))
            if new_proxy:
                row = session.execute(
                    text("SELECT proxy FROM accounts WHERE geelark_phone_id=:pid"),
                    {"pid": phone_id}).fetchone()
                if row is None or not (row[0] or "").strip():
                    fields["proxy"] = new_proxy
            acc_repo.upsert_by_phone_id(session, phone_id, fields)
            imported += 1
        if len(items) < 100:
            break
        page += 1
    return imported

def create_profiles_for_group(session: Session, client, group_id: int,
                              proxies: list[str],
                              count: int | None = None):
    """Generator yielding log lines. Creates `count` GeeLark phones inside
    the group, round-robining `proxies` across them. When `count` is None
    (the old contract), it defaults to `len(proxies)` so callers that
    relied on 1-phone-per-proxy keep working.

    Names follow `{group_name}-NN` starting at the first NN not already
    used in this group — see `accounts.next_serial_index`. No
    `unique_profile_name` wrap; numbers are picked to be unique.

    On per-phone failure we log the failure and move on — partial success
    is the desired behavior."""
    from app.repositories import accounts as acc_repo
    from app.repositories import groups as grp_repo

    group = grp_repo.get(session, group_id)
    if group is None:
        yield f"FAILED: group {group_id} not found"
        return
    group_name = group["name"]

    if not proxies:
        yield "FAILED: at least one proxy is required"
        return
    if count is None:
        count = len(proxies)
    if count < 1:
        yield f"FAILED: count must be >= 1 (got {count})"
        return

    start = acc_repo.next_serial_index(session, group_id, group_name)

    for i in range(count):
        proxy = proxies[i % len(proxies)]
        serial = f"{group_name}-{(start + i):02d}"
        aid = acc_repo.create(session, {
            "profile_name": serial,
            "group_id": group_id,
            "proxy": proxy,
        })
        yield f"Creating phone for {serial} (proxy {proxy})..."
        try:
            # Field names per GeeLark Open API docs (verified 2026-05-25):
            # `profileName` (not serialName), `proxyInformation` (the
            # `scheme://user:pass@host:port` URL — not a dict and not the
            # name `proxy`), `profileGroup` (not groupName). Sending the
            # wrong names returns code 45006 "proxy information error" or
            # 43023 "profile name is empty" inside an HTTP-200 response.
            data = client.create_phones([{
                "profileName": serial,
                "proxyInformation": proxy,
                "profileGroup": group_name,
            }])
        except Exception as e:
            yield f"  FAILED: create_phones raised: {e}"
            continue
        details = data.get("details") or data.get("list") or []
        if not details:
            yield f"  FAILED: no phone returned for {serial}"
            continue
        phone_id = details[0].get("id") or details[0].get("envId")
        if not phone_id:
            yield f"  FAILED: phone created but no id for {serial}"
            continue
        acc_repo.update(session, aid, {"geelark_phone_id": phone_id})
        yield f"  phone {phone_id} created, installing TikTok..."
        try:
            # `name="TikTok"` is a fuzzy GeeLark search — it returns several
            # apps (TikTok, TikTok Studio, TikTok Lite, TikTok Seller, ...).
            # Lock onto the canonical international TikTok by packageName
            # so we don't accidentally install the creator app.
            apps = client.get_installable_apps(phone_id, name="TikTok")
            items = apps.get("items", [])
            tt = next((i for i in items
                       if i.get("packageName") == "com.zhiliaoapp.musically"),
                      None)
            if tt is None:
                yield (f"  WARNING: TikTok (com.zhiliaoapp.musically) not in "
                       f"installable list for {serial}")
            else:
                # appVersionInfoList is sorted newest-first by GeeLark.
                versions = tt.get("appVersionInfoList") or []
                if not versions:
                    yield (f"  WARNING: TikTok has no installable versions "
                           f"for {serial}")
                else:
                    client.install_app(phone_id, versions[0]["id"])
                    yield f"  TikTok install requested for {serial}"
        except Exception as e:
            yield f"  WARNING: TikTok auto-install failed for {serial}: {e}"
    yield "Done."


def _wait_until_stopped(client, phone_ids: list[str],
                        timeout: float = 60.0, interval: float = 2.0) -> None:
    """Stop any running phones in `phone_ids`, then poll phone/status until
    none of them report Started/Starting. Raises RuntimeError on timeout.

    GeeLark's `phone/delete` rejects any phone whose env is currently started
    (error 43009), so this guard runs first. Already-stopped or expired phones
    pass through with no API call. Phones GeeLark no longer knows about
    (`env not found`) also satisfy the loop — there's nothing to wait for."""
    if not phone_ids:
        return
    deadline = time.monotonic() + timeout
    running = _running_ids(client, phone_ids)
    if running:
        client.stop_phones(running)
    while True:
        running = _running_ids(client, phone_ids)
        if not running:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Timed out waiting for cloud phones to stop: {running}")
        time.sleep(interval)


def _running_ids(client, phone_ids: list[str]) -> list[str]:
    data = client.get_phone_status(phone_ids)
    return [d["id"] for d in (data.get("successDetails") or [])
            if d.get("status") in _RUNNING_STATUSES]


def delete_accounts(session: Session, client,
                    ids: list[int]) -> dict:
    """Delete accounts from the local DB AND from GeeLark in one go.

    Contract:
      - Phones running on GeeLark are stopped first (polled until shut down),
        because GeeLark refuses to delete started envs.
      - GeeLark's `phone/delete` is called in batches of 100 (API limit).
      - If ANY phone fails to delete on GeeLark (other than `env not found`,
        which we treat as already-gone), no local rows are touched — the
        caller can retry without ending up with a split-brain state.
      - Accounts without `geelark_phone_id` skip the GeeLark round-trip.
    """
    rows = [r for r in (acc_repo.get(session, i) for i in ids) if r is not None]
    if not rows:
        return {"deleted": 0}

    # Refuse to delete an account mid-automation. Deleting one force-stops
    # its phone under a live warmup/publish/engagement RPA, and the
    # orphaned publication/run then needs the full watchdog cycle to
    # converge. Let the flight land first (20-min lease worst case).
    from app.services import phone_runtime
    busy = [
        r["id"] for r in rows
        if phone_runtime.get_active_for_account(session, r["id"]) is not None
    ]
    if busy:
        raise RuntimeError(
            f"account(s) {busy} have an in-flight phone session "
            "(warmup/publish/engagement running) — wait for it to finish "
            "or stop it first, then retry the delete")

    phone_ids = [r["geelark_phone_id"] for r in rows
                 if r.get("geelark_phone_id")]

    if phone_ids:
        _wait_until_stopped(client, phone_ids)
        for i in range(0, len(phone_ids), 100):
            chunk = phone_ids[i:i + 100]
            data = client.delete_phones(chunk)
            real_failures = [d for d in (data.get("failDetails") or [])
                             if d.get("code") != _GEELARK_NOT_FOUND]
            if real_failures:
                summary = "; ".join(
                    f"{d.get('id')}: {d.get('msg')}" for d in real_failures)
                raise RuntimeError(
                    f"GeeLark refused to delete some cloud phones: {summary}")

    for r in rows:
        acc_repo.delete(session, r["id"])
    return {"deleted": len(rows)}
