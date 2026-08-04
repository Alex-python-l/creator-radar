"""FB 抓取链路 smoke — 直接调 fetch_facebook_videos,打印结果。

本地跑:
    cd apps/scraper && uv run python scripts/smoke_facebook.py Cristiano

服务器跑(部署验证清单步骤 4):
    docker compose exec scraper python -m scripts.smoke_facebook Cristiano

预期输出:
- profile.display_name 含 "Cristiano"
- 至少抓到 1 条 reel(profile 行 + 多个 video 行)
"""
import asyncio
import sys

from app.scrapers import fetch_facebook_videos


async def main(username: str) -> int:
    print(f"=== smoke fetch_facebook_videos({username!r}) ===")
    try:
        profile, videos = await fetch_facebook_videos(username)
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    print(f"profile: name={profile.display_name!r}, follower={profile.follower_count}")
    print(f"got {len(videos)} videos:")
    for v in videos[:5]:
        print(f"  - {v.video_id}: play={v.play_count}, reactions={v.like_count}, "
              f"dur={v.duration_seconds}s, published={v.published_at}")
    if len(videos) > 5:
        print(f"  ... and {len(videos) - 5} more")
    return 0 if videos else 1


if __name__ == "__main__":
    username = sys.argv[1] if len(sys.argv) > 1 else "Cristiano"
    sys.exit(asyncio.run(main(username)))
