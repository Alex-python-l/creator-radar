"""一次性回填 fetch_logs.error_code。

只回填能 100% 确定的模式：error_message 含 "secondary user ID" → account_gone。
同时把这些匹配行里残留的旧 status='gone' 同步改成 'failed'
（status 现在只有 success/failed，原因在 error_code；防上一版 dead-account
代码写过的 gone 残留在 UI 上变灰点/原始 gone 文案）。
默认 dry-run（只打印）。--apply 才真正写库。

用法（仓库根目录）：
  cd apps/scraper
  .venv/bin/python3 scripts/backfill_error_code.py            # dry-run
  .venv/bin/python3 scripts/backfill_error_code.py --apply    # 执行
"""
import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg

_PATTERN = "%secondary user ID%"
_CODE = "account_gone"


def _db_url() -> str:
    """优先环境变量（容器里 env_file 已注入）；否则读 apps/scraper/.env。"""
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        env = Path(__file__).resolve().parents[1] / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    raw = line.split("=", 1)[1].strip()
                    break
    if not raw:
        raise SystemExit("DATABASE_URL not set (env or apps/scraper/.env)")
    return re.sub(r"\+asyncpg", "", raw)


async def main(apply: bool) -> None:
    conn = await asyncpg.connect(_db_url())
    try:
        rows = await conn.fetch(
            """
            SELECT fl.id, fl.influencer_id, i.username, fl.status, fl.error_code
            FROM fetch_logs fl
            LEFT JOIN influencers i ON i.id = fl.influencer_id
            WHERE fl.error_message ILIKE $1
              AND (fl.error_code IS DISTINCT FROM $2)
            ORDER BY fl.id
            """,
            _PATTERN,
            _CODE,
        )
        gone_rows = [r for r in rows if r["status"] == "gone"]
        print(f"匹配 {len(rows)} 行（error_message 含 'secondary user ID'）：")
        for r in rows:
            print(
                f"  log#{r['id']} influencer={r['influencer_id']} "
                f"@{r['username']} status={r['status']} "
                f"error_code={r['error_code']!r}"
            )
        print(f"其中 status='gone' 残留 {len(gone_rows)} 行（将一并改 'failed'）。")
        if not rows:
            print("无需回填。")
            return
        if not apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
            return
        code_result = await conn.execute(
            "UPDATE fetch_logs SET error_code = $1 WHERE error_message ILIKE $2 "
            "AND (error_code IS DISTINCT FROM $1)",
            _CODE,
            _PATTERN,
        )
        # 仅限定 status='gone' 的匹配行，不动其它（success/failed）行的 status
        status_result = await conn.execute(
            "UPDATE fetch_logs SET status = 'failed' "
            "WHERE error_message ILIKE $1 AND status = 'gone'",
            _PATTERN,
        )
        print(f"\n[applied] error_code: {code_result} | status: {status_result}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv[1:]))
