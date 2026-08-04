import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "app.db"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Local cache of GeeLark RPA failure screenshots. The raw URLs live on
# GeeLark's slow Singapore origin (1-4s TTFB even for tiny images) and
# expire after ~3 days; we fetch once and serve the cached copy.
SCREENSHOT_DIR = Path(os.getenv("SCREENSHOT_DIR", DATA_DIR / "screenshots"))
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

GEELARK_TOKEN = os.getenv("GEELARK_TOKEN", "")
# Shared token in the ?token= query string of /api/geelark/callback.
# GeeLark cannot send our session cookie, so the receiver authenticates
# against this random string. Empty value disables the endpoint (returns
# 503) — useful for environments that have not registered a callback URL
# yet. Rotate via .env + `scripts/register_geelark_callback.py`.
GEELARK_CALLBACK_TOKEN = os.getenv("GEELARK_CALLBACK_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Studio 不再自持登录；每个请求都向 Radar 解析当前工作空间。
RADAR_AUTH_VERIFY_URL = os.getenv(
    "RADAR_AUTH_VERIFY_URL", "http://127.0.0.1:3000/api/workspace/session")

# 仅供迁移脚本、测试或显式指定工作空间的后台进程使用。生产请求永远由
# Radar 会话覆盖，不能靠这个值选择租户。
STUDIO_DEFAULT_WORKSPACE_ID = os.getenv("STUDIO_DEFAULT_WORKSPACE_ID", "")

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL must be set in .env. "
        "Format: postgresql+psycopg://user:pass@host:port/dbname"
    )
DRIVE_IMPORT_PROXY = os.getenv("DRIVE_IMPORT_PROXY", "")

# Overseas proxy-probe service — see probe/README.md. PROBE_URL is the full
# POST endpoint (e.g. https://probe.example.com/check); PROBE_TOKEN is the
# shared bearer in the X-Probe-Token header. Empty values disable the
# proxy_check job — the dashboard still shows the column, just always grey.
PROBE_URL = os.getenv("PROBE_URL", "")
PROBE_TOKEN = os.getenv("PROBE_TOKEN", "")

# Optional standalone GeeLark RPA progress watchdog. Ops forwards the current
# workspace's encrypted GeeLark token server-side; the browser never receives it.
RPA_WATCHDOG_BASE_URL = os.getenv("RPA_WATCHDOG_BASE_URL", "").rstrip("/")
RPA_WATCHDOG_TIMEOUT_SECONDS = float(
    os.getenv("RPA_WATCHDOG_TIMEOUT_SECONDS", "8")
)

SETTINGS_DEFAULTS = {
    "default_target_warmup_count": "10",
    "daily_warmup_count": "4",
    "post_cooldown_hours": "2",
    "gemini_high_quality": "0",
    # Was "300,900,1800,3600" (4 retries). Each retry restarts the phone
    # for another 5-min polling window — a single stuck publication used
    # to burn 5 × 20min ≈ $0.70 in cloud-phone time. One retry after
    # 15 min is enough to recover from transient GeeLark hiccups; tasks
    # that are genuinely broken should land in FAILURE for manual review
    # rather than re-burning the phone budget.
    "publish_retry_delays_seconds": "900",
    # 45 keywords × ~25 BD rows = ~1100 candidate videos worldwide.
    # Bright Data /trigger accepts up to 100 inputs in one batch, so we send
    # them in a single POST and dedupe per-url at upsert time.
    "tiktok_discover_keywords": (
        "ielts,ieltsspeaking,ieltswriting,ieltsreading,ieltslistening,"
        "ieltspreparation,ieltspractice,ieltsexam,ieltstest,ieltstips,"
        "ieltsteacher,ieltsclass,ieltscourse,ieltsband,ieltsband9,"
        "ieltsband8,ieltsband7,ieltsacademic,ieltsgeneral,ielts9,"
        "ielts8,ielts7,ielts6,ieltsessay,ieltsvocabulary,"
        "ieltsgrammar,ieltspronunciation,ieltsstudy,ieltsmotivation,ieltsjourney,"
        "studyenglish,learnenglish,englishspeaking,englishlearning,englishtips,"
        "englishlesson,studyabroad,englishtest,esl,toefl,"
        "britishenglish,americanenglish,fluentenglish,englishvocabulary,englishgrammar"
    ),
    # window 3650 ≈ no age limit: IELTS content is evergreen — most of a
    # 10k-record BD batch is 1-3 year old videos that still pull search
    # traffic, so an age gate just throws away paid records.
    "tiktok_discover_min_views": "100",
    "tiktok_discover_window_days": "3650",
    "tiktok_discover_retain_days": "90",
    "tiktok_discover_max_per_keyword": "500",
    # Per-phone, per-platform daily ceiling on comment runs that count
    # toward GeeLark spend. SUCCESS and in-flight (PENDING/QUEUED/INITIATED)
    # runs are counted; FAILURE/SKIPPED do not consume the daily quota.
    # Set to 0 to disable the cap for that platform.
    "daily_comment_limit_tiktok": "20",
    "daily_comment_limit_facebook": "20",
}
