"""
定时任务管理工具（Agent 调用）。

make_task_tools(user_id) → 3 个工具：
  - create_scheduled_task：创建定时任务
  - list_scheduled_tasks：列出当前用户的所有定时任务
  - delete_scheduled_task：删除（取消）一个定时任务
"""

from __future__ import annotations

import json
import secrets
from typing import Annotated, Optional

from langchain.tools import tool
from loguru import logger


def make_task_tools(user_id: int) -> list:
    """定时任务管理工具工厂（同步，绑定 user_id）。"""

    @tool
    async def create_scheduled_task(
        title: Annotated[str, "任务标题，简短描述任务做什么"],
        prompt: Annotated[str, "每次触发时发给 Agent 的完整指令内容"],
        cron_expr: Annotated[str, "cron 表达式（分 时 日 月 周），如 '0 9 * * *' 表示每天早上9点"],
    ) -> str:
        """创建定时任务。用户说"每天/每周/定时/定期"做某事时使用此工具。"""
        from apscheduler.triggers.cron import CronTrigger as _CT

        from app.models.standard.agent_task import AgentScheduledTask
        from app.core.scheduler import register_scheduled_task

        # 先校验 cron 再落库，避免产生永远无法注册的脏记录
        cron_expr = cron_expr.strip()
        try:
            _CT.from_crontab(cron_expr)
        except (ValueError, KeyError) as e:
            return json.dumps(
                {"ok": False, "error": f"cron 表达式不合法：{e}。请检查后重试（格式：分 时 日 月 周）"},
                ensure_ascii=False,
            )

        task_key = f"stask_{secrets.token_hex(6)}"
        task = await AgentScheduledTask.create(
            task_key=task_key,
            user_id=user_id,
            title=title,
            prompt=prompt,
            cron_expr=cron_expr,
        )

        register_scheduled_task(task)

        return json.dumps(
            {
                "ok": True,
                "task_key": task_key,
                "title": title,
                "cron_expr": cron_expr,
                "message": f"定时任务已创建：{title}（{cron_expr}）",
            },
            ensure_ascii=False,
        )

    @tool
    async def list_scheduled_tasks() -> str:
        """列出当前用户的所有定时任务，包括状态、执行次数、上次执行时间。"""
        from app.models.standard.agent_task import AgentScheduledTask

        tasks = await AgentScheduledTask.filter(
            user_id=user_id, is_deleted=0,
        ).order_by("-create_time")

        items = []
        for t in tasks:
            items.append({
                "task_key": t.task_key,
                "title": t.title,
                "cron_expr": t.cron_expr,
                "status": t.status,
                "run_count": t.run_count,
                "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
                "last_session_key": t.last_session_key,
            })

        return json.dumps({"ok": True, "total": len(items), "tasks": items}, ensure_ascii=False)

    @tool
    async def delete_scheduled_task(
        task_key: Annotated[str, "要删除的定时任务的 task_key"],
    ) -> str:
        """删除（取消）一个定时任务。"""
        from app.models.standard.agent_task import AgentScheduledTask
        from app.core.scheduler import unregister_scheduled_task

        task = await AgentScheduledTask.get_or_none(task_key=task_key, user_id=user_id, is_deleted=0)
        if not task:
            return json.dumps({"ok": False, "error": "任务不存在或无权删除"}, ensure_ascii=False)

        task.status = "canceled"
        task.is_deleted = 1
        await task.save()

        unregister_scheduled_task(task)

        return json.dumps(
            {"ok": True, "task_key": task_key, "message": f"定时任务已删除：{task.title}"},
            ensure_ascii=False,
        )

    return [create_scheduled_task, list_scheduled_tasks, delete_scheduled_task]
