from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from app import config
from app.auth import require_auth

def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Open the Procrastinate app so request handlers (notably
        # /api/geelark/callback's defer of process_geelark_callback_task)
        # have a live psycopg connection pool. Without this, defer_async
        # raises AppNotOpen and the worker never gets the wake.
        from app.tasks import procrastinate_app
        from app.db import session_scope
        from app.workspace_secrets import validate_secret_store
        with session_scope() as session:
            validate_secret_store(session)
        async with procrastinate_app.open_async():
            yield

    app = FastAPI(lifespan=lifespan)

    # Compress responses over the wire — the larger JSON list endpoints
    # (e.g. /api/videos, ~160KB) dominate payload size. Starlette's
    # GZipMiddleware skips text/event-stream by default, so the SSE log
    # streams (phones/create, warmup) keep flushing in real time.
    app.add_middleware(GZipMiddleware, minimum_size=500)

    from app.api import health as health_api
    app.include_router(health_api.router)  # NO auth — must be hittable externally

    from app.api import geelark as geelark_api
    # NO auth — GeeLark cannot send our session cookie. The endpoint
    # authenticates via ?token= against GEELARK_CALLBACK_TOKEN.
    app.include_router(geelark_api.router)

    from app.api import users as users_api
    # 只剩 GET /api/me(登录态探测,自带 require_auth)。注册/登录/登出已随
    # 2026-07-17 登录翻转移到 radar 的 /api/auth/*;前端也已并入 apps/web。
    app.include_router(users_api.router)

    from app.api import (accounts, videos, dashboard, settings, groups,
                          engagement, wallet, costs, caption_prompts,
                          comment_prompts, writer as writer_api,
                          ops as ops_api, monitor_analysis as monitor_analysis_api)
    app.include_router(accounts.router, dependencies=[Depends(require_auth)])
    app.include_router(videos.router, dependencies=[Depends(require_auth)])
    app.include_router(dashboard.router, dependencies=[Depends(require_auth)])
    app.include_router(settings.router, dependencies=[Depends(require_auth)])
    app.include_router(groups.router, dependencies=[Depends(require_auth)])
    app.include_router(caption_prompts.router,
                       dependencies=[Depends(require_auth)])
    app.include_router(comment_prompts.router,
                       dependencies=[Depends(require_auth)])
    app.include_router(engagement.router, dependencies=[Depends(require_auth)])
    app.include_router(wallet.router, dependencies=[Depends(require_auth)])
    app.include_router(costs.router, dependencies=[Depends(require_auth)])
    app.include_router(writer_api.router, dependencies=[Depends(require_auth)])
    app.include_router(ops_api.router, dependencies=[Depends(require_auth)])
    app.include_router(monitor_analysis_api.router,
                       dependencies=[Depends(require_auth)])

    from fastapi.responses import FileResponse

    thumbnails_dir = (config.DATA_DIR / "thumbnails").resolve()
    thumbnails_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/static/thumbnails/{workspace_id}/{filename}", include_in_schema=False,
             dependencies=[Depends(require_auth)])
    def get_thumbnail(workspace_id: int, filename: str, request: Request):
        # Was an unauthenticated StaticFiles mount — mounts bypass
        # router dependencies, so thumbnails leaked without a login.
        # Same-origin <img> tags send the session cookie automatically,
        # so the frontend needs no change.
        # {filename} is a single path segment (no '/'); resolve() +
        # parent check guards encoded '..' tricks and symlinks.
        active_id = int(request.state.workspace["id"])
        workspace_dir = (thumbnails_dir / str(active_id)).resolve()
        if workspace_id != active_id:
            raise HTTPException(status_code=404, detail="not found")
        candidate = (workspace_dir / filename).resolve()
        if candidate.parent != workspace_dir or not candidate.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(
            candidate,
            headers={"cache-control": "private, max-age=86400"},
        )

    # SPA 托管已退役(2026-07-18):前端并入 apps/web(Next),经 nginx 只有
    # /api、/healthz、/static/thumbnails 三类路径到达本服务;未知路径由
    # FastAPI 原生 404。旧 dist 若仍在磁盘上只是死文件,不再被服务。

    return app

app = create_app()
