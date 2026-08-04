"""Account pool building for posting. `platform` selects which
last_posted_at_* column and which <platform>_logged_in flag to use."""
import time
from app.services.platforms import LAST_POSTED_COL, LOGGED_IN_COL


def _eligible(account: dict, platform: str) -> bool:
    if not account.get("warmed_up"):
        return False
    if not account.get(LOGGED_IN_COL[platform]):
        return False
    return True


def build_pool(accounts: list[dict], platform: str = "tiktok") -> list[dict]:
    col = LAST_POSTED_COL[platform]
    pool = []
    for a in accounts:
        if not _eligible(a, platform):
            continue
        name = (a.get("profile_name") or "").strip()
        if not name:
            continue
        pool.append({
            "profile_name": name,
            "account_id": a.get("id"),
            "last_posted_at": a.get(col),
        })
    pool.sort(key=lambda p: p["last_posted_at"]
              if p["last_posted_at"] is not None else 0)
    return pool


def assign(videos: list, pool: list[dict],
           cooldown_hours: float = 2) -> list[dict]:
    cutoff = time.time() - cooldown_hours * 3600

    def _epoch(t):
        if t is None:
            return None
        if hasattr(t, "timestamp"):
            return t.timestamp()
        return t  # already epoch

    available = [p for p in pool
                 if _epoch(p["last_posted_at"]) is None
                 or _epoch(p["last_posted_at"]) < cutoff]
    assignments = []
    for video in videos:
        if not available:
            break
        chosen = available.pop(0)
        assignments.append({
            "video": video,
            "profile_name": chosen["profile_name"],
            "account_id": chosen["account_id"],
        })
    return assignments
