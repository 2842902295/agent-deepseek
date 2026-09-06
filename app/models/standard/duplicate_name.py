from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardDuplicateName(BaseModel, TimestampMixin):
    """标准查重结果记录表（每条对应一个标准的查重结果）"""

    id = fields.IntField(pk=True, description="主键ID")

    batch_id = fields.BigIntField(description="所属批次ID")

    standard_no = fields.CharField(max_length=200, description="标准编号")
    standard_name = fields.CharField(max_length=500, null=True, description="标准名称")
    use_range = fields.TextField(null=True, description="适用范围")

    found = fields.BooleanField(default=False, description="是否在库中找到")
    error = fields.CharField(max_length=500, null=True, description="错误信息")

    need_attention = fields.BooleanField(default=False, description="是否需要关注")
    general_evaluation = fields.TextField(null=True, description="总体评价")
    suggestion = fields.TextField(null=True, description="建议措施")

    similar_standards = fields.JSONField(null=True, description="相似标准列表")

    task_status = fields.CharField(
        max_length=20, default="done",
        description="任务状态：pending/running/done/failed（用于断点续跑）"
    )

    class Meta:
        table = "standard_duplicate_name"
        table_description = "标准查重结果记录表"
        indexes = [
            ("batch_id",),
            ("standard_no",),
            ("need_attention",),
            ("found",),
            ("task_status",),
        ]


__all__ = ["StandardDuplicateName"]
