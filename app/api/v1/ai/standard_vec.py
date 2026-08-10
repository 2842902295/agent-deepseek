"""
标准向量库 API（status + build）

- GET  /api/v1/ai/standard-vec/status   查嵌入进度
- POST /api/v1/ai/standard-vec/build    触发增量构建（BackgroundTasks）
- POST /api/v1/ai/standard-vec/cancel   清除「正在跑」标记（防卡死）

后台任务在 FastAPI 主进程内异步执行，与 scheduler.py 的每日 job 共用 builder。
通过模块级标记防重——同一时刻只允许一个 build 在跑（meta + chapter 顺序执行）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from fastapi import APIRouter, BackgroundTasks
from loguru import logger
from pydantic import BaseModel, Field

from app.schemas.base import Fail, Success
from app.services.standard_vec import StandardVecBuilder

router = APIRouter(prefix="/standard-vec", tags=["标准向量库"])

# 模块级运行标记：同一时刻只允许一个 build 在跑
_running: bool = False
_running_started_at: Optional[float] = None
_last_finished_at: Optional[float] = None
_last_result: Optional[dict] = None


class BuildRequest(BaseModel):
    pool_id: Optional[int] = Field(default=None, description="查重池 ID 限定范围；不传或 -1 表示全库")
    only: str = Field(default="all", description="meta / chapter / all")
    limit: int = Field(default=0, ge=0, description="每子任务最多处理多少条，0=不限")
    batch_size: int = Field(default=16, ge=1, le=64, description="章节嵌入 batch（默认 16）")


async def _run_build(req: BuildRequest) -> None:
    """后台跑 build_meta + build_chapter；异常吞掉，仅记日志。"""
    global _running, _running_started_at, _last_finished_at, _last_result
    started = time.time()
    result: dict = {"started_at": started, "ok": True, "errors": []}
    try:
        builder = StandardVecBuilder()
        only = (req.only or "all").lower()

        if only in ("all", "meta"):
            try:
                stat = await builder.build_meta(pool_id=req.pool_id, limit=req.limit)
                result["meta"] = stat
                logger.info(f"[standard-vec API] build_meta done: {stat}")
            except Exception as e:  # noqa: BLE001
                logger.exception("[standard-vec API] build_meta 异常")
                result["errors"].append(f"meta: {e}")

        if only in ("all", "chapter"):
            try:
                stat = await builder.build_chapter(
                    pool_id=req.pool_id,
                    limit=req.limit,
                    batch_size=req.batch_size,
                )
                result["chapter"] = stat
                logger.info(f"[standard-vec API] build_chapter done: {stat}")
            except Exception as e:  # noqa: BLE001
                logger.exception("[standard-vec API] build_chapter 异常")
                result["errors"].append(f"chapter: {e}")
    finally:
        result["finished_at"] = time.time()
        result["duration_sec"] = round(result["finished_at"] - started, 1)
        _last_finished_at = result["finished_at"]
        _last_result = result
        _running = False
        _running_started_at = None
        logger.info(f"[standard-vec API] build 全部完成，耗时 {result['duration_sec']}s")


@router.get("/status", summary="向量库构建进度")
async def get_status():
    builder = StandardVecBuilder()
    try:
        stat = await builder.get_status()
    except Exception as e:  # noqa: BLE001
        return Fail(msg=f"查询状态失败：{e}")

    data = {
        "running": _running,
        "running_started_at": _running_started_at,
        "running_elapsed_sec": (
            round(time.time() - _running_started_at, 1) if _running_started_at else None
        ),
        "last_finished_at": _last_finished_at,
        "last_result": _last_result,
        **stat,
    }
    return Success(data=data)


@router.post("/build", summary="触发向量库增量构建（异步）")
async def trigger_build(req: BuildRequest, background: BackgroundTasks):
    global _running, _running_started_at
    if _running:
        return Fail(
            msg="已有构建任务在跑，请先 GET /status 查进度，或调 /cancel 清除卡死标记",
            data={"running_started_at": _running_started_at},
        )
    _running = True
    _running_started_at = time.time()
    # 通过 BackgroundTasks 在响应返回后异步执行；
    # 用 create_task 立刻交给当前事件循环跑（不阻塞响应）。
    asyncio.create_task(_run_build(req))
    return Success(
        msg="已触发后台构建，调 /status 查进度",
        data={"started_at": _running_started_at, "params": req.model_dump()},
    )


@router.post("/cancel", summary="清除「正在跑」标记（防卡死）")
async def cancel_running():
    """注意：仅清除模块级标记，不真正停止已发出的 embed 请求。

    用于「上一次 API 进程重启后标记没清掉」之类的卡死场景。
    """
    global _running, _running_started_at
    was_running = _running
    _running = False
    _running_started_at = None
    return Success(msg="已清除标记", data={"was_running": was_running})
