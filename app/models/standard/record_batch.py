from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardRecordBatch(BaseModel, TimestampMixin):
    """批量标准查重记录表"""

    id = fields.IntField(pk=True, description="主键ID")

    # 批次信息
    batch_name = fields.CharField(max_length=200, null=True, description="批次名称")
    input_standard_nos = fields.JSONField(description="输入的标准编号列表")

    # 统计信息
    total_count = fields.IntField(default=0, description="总数")
    success_count = fields.IntField(default=0, description="成功数")
    failed_count = fields.IntField(default=0, description="失败数")
    duplicate_count = fields.IntField(default=0, description="高相似数")

    # 查重结果（完整的 JSON）
    results = fields.JSONField(null=True, description="查重结果详情")

    # 状态
    status = fields.CharField(max_length=20, default="completed", description="状态：processing/completed/failed")

    # 备注
    remark = fields.TextField(null=True, description="备注")

    class Meta:
        table = "standard_record_batch"
        table_description = "批量标准查重记录表"
        indexes = [
            ("create_time",),
            ("status",),
            ("total_count",),
        ]


__all__ = ["StandardRecordBatch"]
