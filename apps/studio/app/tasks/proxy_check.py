"""Periodic proxy check.

Daily at 04:00 server time. Does NOT acquire the phones lock — it only
queries proxy health via the overseas probe service, never starts phones.
"""

import asyncio

from app.db import session_scope
from app.services import proxy_check_service
from app.tasks import procrastinate_app


def _proxy_check_sync() -> str:
    with session_scope() as session:
        return proxy_check_service.run_proxy_check(session)


@procrastinate_app.periodic(cron="0 4 * * *")
@procrastinate_app.task(queue="default")
async def proxy_check_task(timestamp: int) -> str:
    return await asyncio.to_thread(_proxy_check_sync)
