"""
定时任务管理 API

提供定时任务的查询、暂停、恢复、删除接口。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.core.ctx import CTX_USER_ID
from app.models.standard.agent_task import AgentScheduledTask, AgentScheduledTaskRun
from app.schemas.base import Success, Fail

router = APIRouter(prefix="/task", tags=["定时任务"])


@router.get("/scheduled/list", summary="定时任务列表")
async def list_scheduled_tasks(
    status: Optional[str] = Query(None, description="筛选状态：active/paused/canceled"),
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """查询当前用户的所有定时任务（含近期执行记录）"""
    user_id = CTX_USER_ID.get()
    if not user_id:
        return Fail(msg="需要登录")

    q = AgentScheduledTask.filter(user_id=user_id, is_deleted=0)
    if status:
        q = q.filter(status=status)
    q = q.order_by("-create_time")

    total = await q.count()
    tasks = await q.offset((current - 1) * size).limit(size)

    items = []
    for t in tasks:
        task_dict = await t.to_dict()
        # 附带最近 10 条执行记录
        runs = await AgentScheduledTaskRun.filter(task_id=t.id).order_by("-create_time").limit(10)
        task_dict["recentRuns"] = [await r.to_dict() for r in runs]
        items.append(task_dict)

    return Success(data={"items": items, "total": total, "current": current, "size": size})


@router.post("/scheduled/{task_id}/pause", summary="暂停定时任务")
async def pause_scheduled_task(task_id: int):
    """暂停一个定时任务（从 APScheduler 移除，保留 DB 记录）"""
    user_id = CTX_USER_ID.get()
    if not user_id:
        return Fail(msg="需要登录")

    task = await AgentScheduledTask.get_or_none(id=task_id, user_id=user_id, is_deleted=0)
    if not task:
        return Fail(msg="任务不存在")
    if task.status != "active":
        return Fail(msg="仅 active 状态的任务可暂停")

    task.status = "paused"
    await task.save()

    # 从 APScheduler 移除，避免无效触发
    from app.core.scheduler import unregister_scheduled_task
    unregister_scheduled_task(task)

    return Success(data={"task_key": task.task_key, "status": "paused"})


@router.post("/scheduled/{task_id}/resume", summary="恢复定时任务")
async def resume_scheduled_task(task_id: int):
    """恢复暂停的定时任务"""
    user_id = CTX_USER_ID.get()
    if not user_id:
        return Fail(msg="需要登录")

    task = await AgentScheduledTask.get_or_none(id=task_id, user_id=user_id, is_deleted=0)
    if not task:
        return Fail(msg="任务不存在")
    if task.status != "paused":
        return Fail(msg="仅 paused 状态的任务可恢复")

    task.status = "active"
    await task.save()

    # 重新注册到 APScheduler
    from app.core.scheduler import register_scheduled_task
    register_scheduled_task(task)

    return Success(data={"task_key": task.task_key, "status": "active"})


@router.delete("/scheduled/{task_id}", summary="删除定时任务")
async def delete_scheduled_task(task_id: int):
    """删除（软删）一个定时任务"""
    user_id = CTX_USER_ID.get()
    if not user_id:
        return Fail(msg="需要登录")

    task = await AgentScheduledTask.get_or_none(id=task_id, user_id=user_id, is_deleted=0)
    if not task:
        return Fail(msg="任务不存在")

    task.status = "canceled"
    task.is_deleted = 1
    await task.save()

    # 从 APScheduler 移除
    from app.core.scheduler import unregister_scheduled_task
    unregister_scheduled_task(task)

    return Success(data={"task_key": task.task_key})
