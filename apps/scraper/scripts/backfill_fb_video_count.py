"""一次性回填 FB influencers.video_count。

历史 bug:scrape.py 在 profile.video_count=0(FB 不暴露 page 总视频数)时
直接覆盖 update_values["video_count"]=0,导致 FB 账号即使抓到 N 条 reel,
influencers 表里 video_count 也始终是 0,UI 显示 "0 视频"。

修复落在 _build_influencer_update_values 后,只对新一轮抓取生效;
历史 FB 账号需要本脚本一次性回填。

回填规则:用 videos 表里 (influencer_id, platform='facebook') 的真实条数填回。

默认 dry-run(只打印对账行);--apply 才真正写库。

用法(仓库根目录):
  cd apps/scraper
  .venv/bin/python3 scripts/backfill_fb_video_count.py            # dry-run
  .venv/bin/python3 scripts/backfill_fb_video_count.py --apply    # 执行
"""
import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg


def _db_url() -> str:
    """优先环境变量(容器里 env_file 已注入);否则读 apps/scraper/.env。"""
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
        # 对账:列出 FB 账号当前 video_count vs videos 表实际条数
        rows = await conn.fetch(
            """
            SELECT
                i.id,
                i.username,
                i.video_count AS old_count,
                COALESCE(v.actual_count, 0) AS actual_count
            FROM influencers i
            LEFT JOIN (
                SELECT influencer_id, count(*) AS actual_count
                FROM videos
                WHERE platform = 'facebook'
                GROUP BY influencer_id
            ) v ON v.influencer_id = i.id
            WHERE i.platform = 'facebook'
              AND COALESCE(v.actual_count, 0) <> i.video_count
            ORDER BY i.id
            """,
        )
        print(f"匹配 {len(rows)} 个 FB 账号(video_count 与 videos 表实际条数不一致):")
        for r in rows:
            print(
                f"  influencer#{r['id']} @{r['username']} "
                f"video_count: {r['old_count']} → {r['actual_count']}"
            )
        if not rows:
            print("无需回填。")
            return
        if not apply:
            print("\n[dry-run] 未写库。加 --apply 执行。")
            return
        result = await conn.execute(
            """
            UPDATE influencers i SET video_count = sub.actual_count
            FROM (
                SELECT influencer_id, count(*) AS actual_count
                FROM videos
                WHERE platform = 'facebook'
                GROUP BY influencer_id
            ) sub
            WHERE i.id = sub.influencer_id
              AND i.platform = 'facebook'
              AND i.video_count <> sub.actual_count
            """,
        )
        # 上面只处理 videos 表里有行的;补一刀:videos 表里 0 条但 influencers.video_count
        # 非 0 的(理论上不该出现,但安全起见)— 留空,跳过,避免误清真值
        print(f"\n[applied] {result}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv[1:]))
