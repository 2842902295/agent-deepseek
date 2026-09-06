"""
标准库同步管理 API（指纹增量同步：源库 → 本地业务库，引擎见 app/services/std_sync.py）

- GET  /ai/std-sync/status   同步状态（运行标记 + 实时进度 + 最近一次落库记录 + 源库信息 + 定时开关）
- POST /ai/std-sync/trigger  手工触发一轮同步（后台执行，并发守卫防重入）

权限口径：仅管理员（R_SUPER / R_ADMIN，路由级依赖门控，普通用户一律 4032 信封）。
业务错误 4000（绝不用鉴权错误码）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends

from app.api.v1.ai.agent_skill import get_current_role_codes
from app.schemas.base import Fail, Success
from app.services import std_sync


async def _require_admin():
    """管理员门控（路由级依赖）：非管理员直接返回 4032 信封，端点不执行。

    Fail 本身即 JSONResponse（HTTP 200 + 信封），依赖返回 Response 会短路端点。
    """
    _, role_codes, _ = await get_current_role_codes()
    if "R_SUPER" not in role_codes and "R_ADMIN" not in role_codes:
        return Fail(code="4032", msg="标准库同步仅管理员可用")


router = APIRouter(prefix="/std-sync", tags=["AI-标准库同步"], dependencies=[Depends(_require_admin)])


def _fmt_time(t) -> Optional[str]:
    if not t:
        return None
    return t.strftime("%Y-%m-%d %H:%M:%S") if isinstance(t, datetime) else str(t)


def _parse_stats(raw) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


@router.get("/status", summary="同步状态")
async def sync_status():
    src = std_sync.get_source_config()
    row = await std_sync.get_state_row() or {}
    return Success(
        data={
            "running": std_sync.is_running(),
            "progress": std_sync.live_progress(),
            "tables": [{"name": s.name, "label": s.label} for s in std_sync.TABLES],
            "status": row.get("status") or "idle",
            "trigger": row.get("trigger_by"),
            "startedAt": _fmt_time(row.get("started_at")),
            "finishedAt": _fmt_time(row.get("finished_at")),
            "durationSec": row.get("duration_sec"),
            "stats": _parse_stats(row.get("stats_json")),
            "error": row.get("last_error"),
            "source": {"host": src.host, "port": src.port, "database": src.database},
            "dailyEnabled": std_sync.daily_enabled(),
        }
    )


@router.post("/trigger", summary="手工触发同步（后台执行）")
async def trigger_sync():
    if std_sync.is_running():
        return Fail(code="4000", msg="已有同步任务在运行，请勿重复触发")
    asyncio.create_task(std_sync.run_sync("manual"))
    return Success(msg="同步任务已启动")
