from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardCacheAI(BaseModel, TimestampMixin):
    """AI智能比对缓存表（每条记录存储一个指标的比对关系）"""

    id = fields.IntField(pk=True, description="主键ID")

    # 会话标识
    source_standard_no = fields.CharField(max_length=100, description="源标准编号")
    target_standard_no = fields.CharField(max_length=100, description="目标标准编号")

    # 关联 standard_cache_ind（指标主表）
    source_ind_id = fields.IntField(null=True, description="源标准指标ID（matched/source_only 时有值）")
    target_ind_id = fields.IntField(null=True, description="目标标准指标ID（matched/target_only 时有值）")

    # 比对结果
    comparison_type = fields.CharField(max_length=20, description="matched | source_only | target_only")
    change_analysis = fields.TextField(null=True, description="变化分析（matched 时填写）")
    source_test_names = fields.JSONField(null=True, description="关联的源标准试验名称列表（LLM 关联后填充）")
    target_test_names = fields.JSONField(null=True, description="关联的目标标准试验名称列表（LLM 关联后填充）")

    # 状态
    is_valid = fields.BooleanField(default=True, description="缓存是否有效")
    algorithm_version = fields.CharField(max_length=50, default="v3_agent", description="算法版本")

    class Meta:
        table = "standard_cache_ai"
        table_description = "AI智能比对缓存表"
        indexes = [
            ("source_standard_no", "target_standard_no"),
            ("source_ind_id",),
            ("target_ind_id",),
            ("is_valid",),
            ("create_time",),
        ]


__all__ = ["StandardCacheAI"]
