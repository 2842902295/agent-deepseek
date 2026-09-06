from fastapi import APIRouter

from app.core.dependency import DependAnyAuth
from .ai import ai_router, ai_public_router
from .auth import router_auth
from .route import router_route
from .system_manage import router_system_manage

v1_router = APIRouter()

v1_router.include_router(router_auth, prefix="/auth", tags=["权限认证"])
v1_router.include_router(router_route, prefix="/route", tags=["路由管理"])
v1_router.include_router(router_system_manage, prefix="/system-manage", tags=["系统管理"])
# 需要鉴权的 AI 接口
v1_router.include_router(ai_router, prefix="", tags=["AI"], dependencies=[DependAnyAuth])
# 走 token 的公开 AI 接口（如 artifact 下载）
v1_router.include_router(ai_public_router, prefix="", tags=["AI"])
