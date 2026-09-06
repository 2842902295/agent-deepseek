from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardDuplicateBatch(BaseModel, TimestampMixin):
    """批量标准查重批次表"""

    id = fields.IntField(pk=True, description="主键ID")

    batch_name = fields.CharField(max_length=200, null=True, description="批次名称")

    total_count = fields.IntField(default=0, description="总数")
    success_count = fields.IntField(default=0, description="成功数")
    failed_count = fields.IntField(default=0, description="失败数")
    duplicate_count = fields.IntField(default=0, description="需要关注数")

    status = fields.CharField(max_length=20, default="completed", description="状态：processing/completed/failed")
    mode = fields.CharField(max_length=20, default="deep", description="查重模式：deep=完整Agent推理 / fast=嵌入式向量召回")
    remark = fields.TextField(null=True, description="备注")
    pool_id = fields.IntField(null=True, description="查重池ID（-1=全库，null=未指定，正整数=具体池）")

    class Meta:
        table = "standard_duplicate_batch"
        table_description = "批量标准查重批次表"
        indexes = [
            ("create_time",),
            ("status",),
        ]


__all__ = ["StandardDuplicateBatch"]
