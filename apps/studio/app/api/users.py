"""Session introspection for the SPA.

2026-07-17 起用户体系在 radar(better-auth):注册/登录/登出/改密/用户管理
全部退役,由主站 `/login` 页与其 `/api/auth/*` 承担。这里只剩 GET /api/me
——App.tsx 启动时探测登录态用,形状与旧版保持 {id, username, email}。
require_auth 已含 admin-only 校验(非 admin 403,前端显示提示页)。
"""
from fastapi import APIRouter, Depends

from app.auth import require_auth

router = APIRouter(prefix="/api")


@router.get("/me")
def me(user: dict = Depends(require_auth)):
    # better-auth 把数字主键序列化成字符串;旧 Me 契约是 int,这里转回。
    raw_id = user.get("id")
    try:
        uid = int(raw_id)
    except (TypeError, ValueError):
        uid = raw_id
    return {
        "id": uid,
        "username": user.get("displayUsername") or user.get("username")
        or user.get("name"),
        "email": user.get("email"),
    }
