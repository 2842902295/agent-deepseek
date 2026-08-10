from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardPoolCheck(BaseModel, TimestampMixin):
    """查重池表"""

    id = fields.IntField(pk=True, description="主键ID")

    # 基本信息
    pool_name = fields.CharField(max_length=200, description="查重池名称")
    description = fields.TextField(null=True, description="查重池描述")

    # 查重池包含的标准编号列表
    standard_nos = fields.JSONField(description="标准编号列表")

    # 统计信息
    standard_count = fields.IntField(default=0, description="标准数量")

    # 是否为默认查重池（全库）
    is_default = fields.BooleanField(default=False, description="是否为默认查重池（全库）")

    # 是否启用
    is_active = fields.BooleanField(default=True, description="是否启用")

    class Meta:
        table = "standard_pool_check"
        table_description = "查重池表"
        indexes = [
            ("pool_name",),
            ("is_default",),
            ("is_active",),
            ("create_time",),
        ]


__all__ = ["StandardPoolCheck"]
