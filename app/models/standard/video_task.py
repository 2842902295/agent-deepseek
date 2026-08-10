"""
视频生成任务（HappyHorse / DashScope）相关 ORM。

- AgentVideoTask：单个视频生成任务的全生命周期跟踪（提交 → 轮询 → 落盘 → 计费）
- AgentUsageLog ：通用使用流水。所有计费源（chat / vision / embed / mcp / video）统一写这一张表，
                  看板按它聚合，不用再 union 分散来源。
"""

from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class AgentVideoTask(BaseModel, TimestampMixin):
    """视频生成任务"""

    id = fields.BigIntField(pk=True, description="主键ID")

    task_key = fields.CharField(max_length=64, unique=True, description="本地任务 key（前后端统一标识）")
    remote_task_id = fields.CharField(max_length=96, null=True, description="DashScope 返回的 task_id")
    request_id = fields.CharField(max_length=96, null=True, description="DashScope request_id")

    user_id = fields.IntField(null=True, description="发起用户ID")
    session_id = fields.IntField(null=True, description="所属会话ID")
    message_id = fields.BigIntField(null=True, description="所属消息ID（assistant message）")
    artifact_id = fields.BigIntField(null=True, description="完成后登记的 agent_artifact.id")

    # 请求参数（仅冗余，便于排查 / 重放）
    model = fields.CharField(
        max_length=64, description="happyhorse-1.0-t2v / -i2v / -r2v / -video-edit"
    )
    prompt = fields.TextField(null=True, description="文本提示词")
    media_json = fields.JSONField(null=True, description="input.media 数组（i2v/r2v/video-edit）")
    parameters_json = fields.JSONField(null=True, description="parameters 字段")

    # 状态机
    status = fields.CharField(
        max_length=16, default="PENDING",
        description="PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED/UNKNOWN",
    )

    # 结果
    video_url = fields.CharField(max_length=1024, null=True, description="DashScope 返回的视频 URL（24h 过期）")
    local_path = fields.CharField(
        max_length=512, null=True,
        description="本地缓存路径（相对项目根），用于 URL 过期后回看",
    )

    # usage / 计费
    duration_sec = fields.IntField(null=True, description="计费时长（秒），来自响应 usage.duration")
    output_duration_sec = fields.IntField(null=True, description="输出视频时长")
    input_video_duration_sec = fields.IntField(null=True, description="输入视频时长（仅 video-edit）")
    resolution_sr = fields.IntField(null=True, description="分辨率（720/1080）")
    ratio = fields.CharField(max_length=8, null=True, description="宽高比")
    cost_yuan = fields.DecimalField(max_digits=10, decimal_places=4, null=True, description="估算费用（元）")

    # 错误
    error_code = fields.CharField(max_length=64, null=True, description="DashScope 错误码")
    error_message = fields.TextField(null=True, description="错误详情")

    # 时间线（来自响应字段）
    submit_time = fields.DatetimeField(null=True, description="DashScope 提交时间")
    scheduled_time = fields.DatetimeField(null=True, description="DashScope 调度时间")
    end_time = fields.DatetimeField(null=True, description="DashScope 完成时间")

    is_deleted = fields.IntField(default=0, description="软删")

    class Meta:
        table = "agent_video_task"
        table_description = "视频生成任务（HappyHorse）"
        indexes = [
            ("user_id", "is_deleted", "create_time"),
            ("session_id",),
            ("message_id",),
            ("status",),
            ("task_key",),
        ]


class AgentUsageLog(BaseModel, TimestampMixin):
    """通用使用流水：所有计费源的统一记账表，费用页基于此聚合。

    - module 用资源类型（chat / vision / embed / mcp / video），便于按资源切片求和
    - 业务入口（qa / nian / workbench / qa-batch）单列在 biz_entry，由 ContextVar 注入
    - cost_yuan 是真值，credits 是按汇率折算的冗余展示字段；改汇率时一次性 UPDATE 这一列即可
    - pricing_snapshot_json 保存写入时的单价快照，用于历史对账
    """

    id = fields.BigIntField(pk=True, description="主键ID")

    user_id = fields.IntField(null=True, description="使用方用户ID")
    module = fields.CharField(max_length=32, description="资源类型：chat/vision/embed/mcp/video")
    biz_entry = fields.CharField(
        max_length=32, null=True,
        description="业务入口：qa/nian/workbench/qa-batch/...（来自调用方 ContextVar）",
    )
    provider = fields.CharField(max_length=32, description="dashscope/openai/anthropic/...")
    model = fields.CharField(max_length=96, null=True, description="model id（如 happyhorse-1.0-t2v）")

    ref_type = fields.CharField(
        max_length=32, null=True,
        description="video_task/message/batch_item/...",
    )
    ref_id = fields.BigIntField(null=True, description="ref_type 对应主键")
    session_id = fields.IntField(null=True, description="冗余，便于按会话查询")

    units_json = fields.JSONField(
        null=True,
        description="计量明细 JSON：{video_sec, resolution} / {token_in, token_out, ...}",
    )
    cost_yuan = fields.DecimalField(max_digits=10, decimal_places=4, default=0, description="费用（元）")
    pricing_snapshot_json = fields.JSONField(
        null=True,
        description="写入时的单价快照：{unit_type: {pricing_id, price, qty, line}, ...}",
    )
    credits = fields.DecimalField(
        max_digits=16, decimal_places=4, null=True,
        description="冗余：写入时按汇率折算的积分（cost_yuan / CREDIT_RATE_YUAN）",
    )
    success = fields.IntField(default=1, description="1 计费成功 0 失败（失败任务不计费但记日志便于审计）")

    class Meta:
        table = "agent_usage_log"
        table_description = "AI 使用流水（通用记账）"
        indexes = [
            ("user_id", "create_time"),
            ("module", "create_time"),
            ("biz_entry", "create_time"),
            ("provider", "model"),
            ("ref_type", "ref_id"),
        ]
