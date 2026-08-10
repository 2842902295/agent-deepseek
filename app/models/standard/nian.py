from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class NianDailyFeed(BaseModel, TimestampMixin):
    """知识库 · 每日 feed 排序结果（夜间 agent 写入）"""

    id = fields.IntField(pk=True, description="主键ID")

    user_id = fields.IntField(description="所属用户 ID（不做外键，跨库）")
    feed_date = fields.DateField(description="这条 feed 服务于哪一天")
    entry_id = fields.CharField(max_length=64, description="KB entry id (kb_xxxx)")

    rank = fields.IntField(description="序，0 = 第一位")
    reason = fields.CharField(max_length=80, default="", description="徽标理由，如『昨天问过』")
    confidence = fields.FloatField(null=True, description="agent 自评 0-1")

    class Meta:
        table = "nian_daily_feed"
        table_description = "知识库每日 feed 排序结果"
        unique_together = (("user_id", "feed_date", "entry_id"),)
        indexes = [
            ("user_id", "feed_date", "rank"),
        ]


class NianFeedRunLog(BaseModel, TimestampMixin):
    """知识库 · 夜间 agent 运行日志（用于排查）"""

    id = fields.IntField(pk=True, description="主键ID")

    user_id = fields.IntField(description="所属用户 ID")
    feed_date = fields.DateField(description="本次跑的是哪一天的 feed")
    status = fields.CharField(max_length=16, default="ok", description="ok / failed / skipped")
    items_written = fields.IntField(default=0, description="本次写入条数")
    brief = fields.TextField(null=True, description="agent 输出的晨间简报")
    error = fields.TextField(null=True, description="失败信息")
    duration_ms = fields.IntField(default=0, description="耗时（毫秒）")

    class Meta:
        table = "nian_feed_run_log"
        table_description = "知识库夜间 agent 运行日志"
        indexes = [
            ("user_id", "feed_date"),
            ("status",),
        ]


__all__ = ["NianDailyFeed", "NianFeedRunLog"]
