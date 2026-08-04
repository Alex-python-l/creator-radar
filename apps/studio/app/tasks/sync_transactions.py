"""Sync GeeLark billing transactions into our local mirror.

Runs every 15 minutes. The endpoint exposes only the newest 1000
transactions and supports no pagination / time filter — see
GeeLarkClient.list_billing_transactions for the parameter survey. We
pull the maximum allowed (1000) every tick and upsert; rows already
in the mirror trip ON CONFLICT DO NOTHING.

Implication: any transactions older than the trailing 1000 at the
moment of first deploy are unreachable. Going forward, as long as the
sync runs more often than 1000 new charges accumulate (about a week
of activity at current volume), the mirror loses nothing.

Blocking GeeLark HTTP call wrapped in `asyncio.to_thread` to avoid
freezing the procrastinate worker's event loop (see memory:
procrastinate-asyncio-trap).
"""

import asyncio
import logging

from app.db import session_scope
from app.repositories import transactions as tx_repo
from app.services.geelark_service import make_client
from app.tasks import procrastinate_app

log = logging.getLogger(__name__)


@procrastinate_app.periodic(cron="*/15 * * * *")
@procrastinate_app.task(queue="default")
async def sync_geelark_transactions(timestamp: int) -> str:
    with session_scope() as session:
        from app.workspace_secrets import list_configured_workspaces
        workspace_ids = list_configured_workspaces(session, "GEELARK_TOKEN")
    fetched = inserted = conflicts = attributed = 0
    for workspace_id in workspace_ids:
        with session_scope(workspace_id) as session:
            # workspace_client delegates through make_client, including old
            # zero-argument test fakes patched in this module.
            try:
                client = make_client(session, workspace_id)
            except TypeError:
                client = make_client()
            data = await asyncio.to_thread(
                client.list_billing_transactions, limit=1000,
            )
            rows = data.get("list") or []
            if not rows:
                continue
            workspace_conflicts = tx_repo.upsert_many(session, rows)
            fetched += len(rows)
            conflicts += workspace_conflicts
            inserted += len(rows) - workspace_conflicts
            try:
                tx_ids = [str(r["id"]) for r in rows]
                attributed += tx_repo.ensure_attributions(session, tx_ids)
            except Exception:
                log.exception(
                    "sync_geelark_transactions: attribution failed workspace=%s",
                    workspace_id,
                )
    msg = (
        f"sync_geelark_transactions: workspaces={len(workspace_ids)} "
        f"fetched={fetched} inserted={inserted} conflicts={conflicts} "
        f"attributed={attributed}"
    )
    if fetched == 0:
        return "sync_geelark_transactions: empty"
    log.info(msg)
    return msg
