"""把存量 Facebook 账号 username 统一回填成纯数字 profile id。

非纯数字 username(vanity / profile.php?id= / people/...)经 resolve_fb_identifier
解析成纯数字 id;多条解析到同一 id 的合并(复用 dedupe_facebook_accounts._apply_merge)。
默认 dry-run,加 --apply 才写库。

用法:
  cd apps/scraper
  .venv/bin/python3 scripts/backfill_fb_ids.py
  .venv/bin/python3 scripts/backfill_fb_ids.py --apply
"""
import argparse
import asyncio
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import asyncpg

from app.handlers.resolve_fb import resolve_fb_identifier
from app.handlers.scrape import _clean_username
from scripts.dedupe_facebook_accounts import _apply_merge


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


def _to_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def fb_lookup_url(username: str) -> str:
    """influencers.username(identifier 形态)→ 可 resolve 的 facebook URL。"""
    return f"https://www.facebook.com/{username}"


def build_backfill_plan(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """纯函数。rows: [{id, username, resolved_id(纯数字 or None), video_rows}]
    → {renames:[{id,new_username}], merges:[{primary_id,duplicate_id}], unresolved:[id]}。"""
    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unresolved: list[int] = []
    for r in rows:
        rid = r.get("resolved_id")
        if not rid:
            unresolved.append(_to_int(r.get("id")))
            continue
        by_target[str(rid)].append(r)

    renames: list[dict[str, Any]] = []
    merges: list[dict[str, Any]] = []
    for target_id, members in by_target.items():
        primary = sorted(
            members,
            key=lambda m: (-_to_int(m.get("video_rows")), _to_int(m.get("id"))),
        )[0]
        primary_id = _to_int(primary.get("id"))
        if str(primary.get("username")) != target_id:
            renames.append({"id": primary_id, "new_username": target_id})
        for dup in sorted(members, key=lambda m: _to_int(m.get("id"))):
            dup_id = _to_int(dup.get("id"))
            if dup_id != primary_id:
                merges.append({"primary_id": primary_id, "duplicate_id": dup_id})
    return {"renames": renames, "merges": merges, "unresolved": unresolved}


async def _fetch_rows(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT i.id, i.username, COUNT(v.id)::int AS video_rows
        FROM influencers i
        LEFT JOIN videos v ON v.influencer_id = i.id
        WHERE i.platform = 'facebook'
        GROUP BY i.id, i.username
        ORDER BY i.id
        """,
    )
    return [dict(r) for r in rows]


async def _resolve_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for r in rows:
        username = str(r["username"])
        # 本地能直接收敛纯数字的(纯数字 / profile.php?id= / people/<n>/<numeric>)
        # 不抓 HTML,且不依赖账号存活;只有 vanity / people/pfbid 才真正 resolve。
        local = _clean_username(username, "facebook")
        if local.isdigit():
            r["resolved_id"] = local
            continue
        try:
            ident, _ = await resolve_fb_identifier(fb_lookup_url(username))
            r["resolved_id"] = ident if ident.isdigit() else None
        except Exception as exc:  # resolve 失败 → 留待人工
            print(f"  resolve 失败 id={r['id']} @{username}: {exc}")
            r["resolved_id"] = None
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正写库;默认 dry-run")
    args = parser.parse_args()

    conn = await asyncpg.connect(_db_url())
    try:
        rows = await _resolve_rows(await _fetch_rows(conn))
        plan = build_backfill_plan(rows)

        print(f"改写 {len(plan['renames'])} 条 username → 纯数字 id:")
        for r in plan["renames"]:
            print(f"  influencer#{r['id']} → {r['new_username']}")
        print(f"合并 {len(plan['merges'])} 组撞号(同一 profile id):")
        for m in plan["merges"]:
            print(f"  duplicate#{m['duplicate_id']} → primary#{m['primary_id']}")
        if plan["unresolved"]:
            print(f"无法解析 {len(plan['unresolved'])} 条(需人工): {plan['unresolved']}")

        if not plan["renames"] and not plan["merges"]:
            print("无需回填。")
            return
        if not args.apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
            return

        # 先合并(删 duplicate、迁移外键),再改名 primary,避免撞 unique(platform, username)
        for m in plan["merges"]:
            await _apply_merge(
                conn,
                primary_id=int(m["primary_id"]),
                duplicate_id=int(m["duplicate_id"]),
            )
        for r in plan["renames"]:
            await conn.execute(
                "UPDATE influencers SET username = $1, profile_url = $2 WHERE id = $3",
                r["new_username"],
                fb_lookup_url(r["new_username"]),
                int(r["id"]),
            )
        print(
            f"\n[applied] merged {len(plan['merges'])} dup(s), "
            f"renamed {len(plan['renames'])} account(s)"
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
