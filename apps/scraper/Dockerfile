# 普通 python:slim 基础镜像（~50MB）。代码已去除 Playwright，无需 ~2GB 浏览器镜像。
# yt-dlp / instagrapi / asyncpg / psycopg[binary] 都用 wheel，不需要 build 工具。
FROM python:3.12-slim-bookworm AS base
ARG RELEASE_COMMIT
LABEL org.opencontainers.image.revision="${RELEASE_COMMIT}"
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PATH=/app/.venv/bin:$PATH
WORKDIR /app

# ca-certificates：HTTPS 必须；curl：容器内调试用。其余依赖 wheel 自带二进制。
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
# scripts/: 一次性 backfill / 运维脚本(backfill_error_code、backfill_fb_video_count 等),
# 在生产以 docker exec <container> python scripts/<name>.py 方式运行
COPY scripts ./scripts

EXPOSE 8000
# uvicorn 单进程 = 平台 lane worker + APScheduler + /health 一体
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
