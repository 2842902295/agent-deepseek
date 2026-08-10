from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardCacheAISmart(BaseModel, TimestampMixin):
    """AI智能比对缓存表（compare-smart 方案，直接存储指标文本，不经 cache_ind 中转）"""

    id = fields.IntField(pk=True, description="主键ID")

    source_standard_no = fields.CharField(max_length=100, description="源标准编号")
    target_standard_no = fields.CharField(max_length=100, description="目标标准编号")

    # 指标文本（直接存储，不引用 cache_ind）
    source_indicator_object = fields.CharField(max_length=255, default="", description="源指标名称")
    source_value = fields.TextField(default="", description="源指标值（含单位）")
    source_clause = fields.CharField(max_length=64, default="", description="源条款号")
    target_indicator_object = fields.CharField(max_length=255, default="", description="目标指标名称")
    target_value = fields.TextField(default="", description="目标指标值（含单位）")
    target_clause = fields.CharField(max_length=64, default="", description="目标条款号")
    standard_object = fields.CharField(max_length=255, default="", description="标准化对象")

    # 比对结果
    comparison_type = fields.CharField(
        max_length=32,
        description="matched | source_only | target_only | _summary（汇总行）",
    )
    change_analysis = fields.TextField(default="", description="变化分析（matched 时填写）")

    # 汇总信息（仅 comparison_type='_summary' 的行使用）
    relationship = fields.CharField(max_length=64, default="", description="标准关系")
    overall_assessment = fields.TextField(default="", description="综合评价")

    is_valid = fields.BooleanField(default=True, description="缓存是否有效")
    algorithm_version = fields.CharField(max_length=32, default="smart_v1", description="算法版本")

    class Meta:
        table = "standard_cache_ai_smart"
        table_description = "AI智能比对缓存表（compare-smart）"
        indexes = [
            ("source_standard_no", "target_standard_no"),
            ("is_valid",),
        ]


__all__ = ["StandardCacheAISmart"]
