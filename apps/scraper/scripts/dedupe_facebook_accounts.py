"""合并历史 Facebook 重复账号。

默认 dry-run 只打印计划;加 --apply 才写库。

自动模式只处理 display_name 完全相同、且组内没有 queued/running 抓取任务的 FB 账号。
主账号选择规则:videos 行数最多者优先,并列取 id 最小者。

用法:
  cd apps/scraper
  .venv/bin/python3 scripts/dedupe_facebook_accounts.py
  .venv/bin/python3 scripts/dedupe_facebook_accounts.py --apply
  .venv/bin/python3 scripts/dedupe_facebook_accounts.py --primary-id 260 --duplicate-id 379 --apply
"""
import argparse
import asyncio
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import asyncpg


def _db_url() -> str:
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


def _norm_display_name(value: object) -> str:
    return str(value or "").strip().casefold()


def _to_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_merge_plans(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 display_name 生成保守合并计划。纯函数,便于测试。"""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = _norm_display_name(row.get("display_name"))
        if key:
            groups[key].append(row)

    plans: list[dict[str, Any]] = []
    for display_name, members in groups.items():
        if len(members) < 2:
            continue
        if any(_to_int(m.get("active_jobs")) > 0 for m in members):
            continue

        primary = sorted(
            members,
            key=lambda m: (-_to_int(m.get("video_rows")), _to_int(m.get("id"))),
        )[0]
        primary_id = _to_int(primary.get("id"))
        for duplicate in sorted(members, key=lambda m: _to_int(m.get("id"))):
            duplicate_id = _to_int(duplicate.get("id"))
            if duplicate_id == primary_id:
                continue
            plans.append(
                {
                    "display_name": display_name,
                    "primary_id": primary_id,
                    "primary_username": primary.get("username"),
                    "duplicate_id": duplicate_id,
                    "duplicate_username": duplicate.get("username"),
                    "reason": "same display_name",
                }
            )
    return plans


async def _fetch_rows(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT
            i.id,
            i.username,
            i.display_name,
            COUNT(v.id)::int AS video_rows,
            COUNT(j.id)::int AS active_jobs
        FROM influencers i
        LEFT JOIN videos v ON v.influencer_id = i.id
        LEFT JOIN jobs j
          ON j.kind = 'account.fetch'
         AND j.status IN ('queued', 'running')
         AND j.payload->>'influencerId' = i.id::text
        WHERE i.platform = 'facebook'
        GROUP BY i.id, i.username, i.display_name
        ORDER BY i.id
        """,
    )
    return [dict(r) for r in rows]


async def _apply_merge(
    conn: asyncpg.Connection,
    *,
    primary_id: int,
    duplicate_id: int,
) -> None:
    async with conn.transaction():
        active_jobs = await conn.fetchval(
            """
            SELECT COUNT(*)::int
            FROM jobs
            WHERE kind = 'account.fetch'
              AND status IN ('queued', 'running')
              AND payload->>'influencerId' IN ($1, $2)
            """,
            str(primary_id),
            str(duplicate_id),
        )
        if active_jobs:
            raise RuntimeError(
                f"skip merge {duplicate_id} -> {primary_id}: active account.fetch job exists"
            )

        await conn.execute(
            """
            DELETE FROM videos dup
            USING videos pri
            WHERE dup.influencer_id = $2
              AND pri.influencer_id = $1
              AND dup.platform = pri.platform
              AND dup.video_id = pri.video_id
            """,
            primary_id,
            duplicate_id,
        )
        await conn.execute(
            "UPDATE videos SET influencer_id = $1 WHERE influencer_id = $2",
            primary_id,
            duplicate_id,
        )
        await conn.execute(
            "UPDATE fetch_logs SET influencer_id = $1 WHERE influencer_id = $2",
            primary_id,
            duplicate_id,
        )
        await conn.execute(
            """
            UPDATE jobs
            SET payload = jsonb_set(payload, '{influencerId}', to_jsonb($1::int), false),
                updated_at = now()
            WHERE kind = 'account.fetch'
              AND payload->>'influencerId' = $2
            """,
            primary_id,
            str(duplicate_id),
        )
        await conn.execute("DELETE FROM influencers WHERE id = $1", duplicate_id)
        await conn.execute(
            """
            UPDATE influencers i
            SET video_count = sub.video_rows
            FROM (
                SELECT COUNT(*)::int AS video_rows
                FROM videos
                WHERE influencer_id = $1
            ) sub
            WHERE i.id = $1
            """,
            primary_id,
        )


def _manual_plan(primary_id: int, duplicate_id: int) -> list[dict[str, Any]]:
    return [
        {
            "display_name": "",
            "primary_id": primary_id,
            "primary_username": None,
            "duplicate_id": duplicate_id,
            "duplicate_username": None,
            "reason": "manual ids",
        }
    ]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正写库;默认 dry-run")
    parser.add_argument("--primary-id", type=int, help="手动指定保留账号 id")
    parser.add_argument("--duplicate-id", type=int, help="手动指定待合并/删除账号 id")
    args = parser.parse_args()

    if bool(args.primary_id) != bool(args.duplicate_id):
        raise SystemExit("--primary-id 和 --duplicate-id 必须同时提供")

    conn = await asyncpg.connect(_db_url())
    try:
        if args.primary_id and args.duplicate_id:
            plans = _manual_plan(args.primary_id, args.duplicate_id)
        else:
            plans = build_merge_plans(await _fetch_rows(conn))

        print(f"匹配 {len(plans)} 个 FB 重复账号合并计划:")
        for plan in plans:
            print(
                f"  duplicate#{plan['duplicate_id']} @{plan['duplicate_username']} "
                f"-> primary#{plan['primary_id']} @{plan['primary_username']} "
                f"({plan['reason']})"
            )

        if not plans:
            print("无需处理。")
            return
        if not args.apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
            return

        for plan in plans:
            await _apply_merge(
                conn,
                primary_id=int(plan["primary_id"]),
                duplicate_id=int(plan["duplicate_id"]),
            )
        print(f"\n[applied] merged {len(plans)} duplicate account(s)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
