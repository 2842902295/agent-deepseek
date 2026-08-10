"""
Agent 定时任务 ORM。

- AgentScheduledTask：用户通过 Agent 创建的定时任务（cron 调度），到点自动触发一轮 Agent 执行。
"""

from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class AgentScheduledTask(BaseModel, TimestampMixin):
    """定时任务：用户通过对话创建，APScheduler 按 cron 表达式定期触发 Agent 执行"""

    id = fields.BigIntField(pk=True, description="主键ID")

    task_key = fields.CharField(max_length=64, unique=True, description="任务 key：stask_{hex}")
    user_id = fields.IntField(description="所属用户ID")

    title = fields.CharField(max_length=200, description="任务标题")
    prompt = fields.TextField(description="每次触发时发给 Agent 的消息内容")

    cron_expr = fields.CharField(max_length=100, description="cron 表达式（分 时 日 月 周）")
    timezone = fields.CharField(max_length=32, default="Asia/Shanghai", description="时区")

    status = fields.CharField(
        max_length=16, default="active",
        description="active/paused/canceled",
    )

    last_run_at = fields.DatetimeField(null=True, description="上次执行时间")
    last_session_key = fields.CharField(
        max_length=64, null=True,
        description="上次执行产生的会话 key（用于前端跳转查看执行结果）",
    )
    run_count = fields.IntField(default=0, description="累计执行次数")

    last_fire_slot = fields.CharField(
        max_length=12, null=True,
        description="去重槽位：最近一次抢到执行权的触发分钟槽（yyyyMMddHHmm）。"
        "多实例共用同一 DB 时各自都会注册任务，触发时靠原子 UPDATE 抢槽位，仅抢到的实例执行",
    )

    is_deleted = fields.IntField(default=0, description="软删：0 未删 1 已删")

    class Meta:
        table = "agent_scheduled_task"
        table_description = "Agent 定时任务"
        indexes = [
            ("user_id", "is_deleted", "status"),
            ("task_key",),
        ]


__all__ = ["AgentScheduledTask"]


class AgentScheduledTaskRun(BaseModel, TimestampMixin):
    """定时任务执行记录：每次 APScheduler 触发时写一条"""

    id = fields.BigIntField(pk=True, description="主键ID")

    task_id = fields.BigIntField(description="agent_scheduled_task.id")
    user_id = fields.IntField(description="所属用户ID（冗余，加速按用户查询）")

    session_key = fields.CharField(max_length=64, null=True, description="本次执行产生的会话 key")
    status = fields.CharField(
        max_length=16, default="done",
        description="done/error",
    )

    result_summary = fields.TextField(null=True, description="Agent 输出的摘要（截取前 500 字）")
    error = fields.TextField(null=True, description="执行失败时的错误信息")

    duration_ms = fields.IntField(null=True, description="执行耗时（毫秒）")

    class Meta:
        table = "agent_scheduled_task_run"
        table_description = "定时任务执行记录"
        indexes = [
            ("task_id", "create_time"),
            ("user_id", "create_time"),
        ]


__all__ = ["AgentScheduledTask", "AgentScheduledTaskRun"]
