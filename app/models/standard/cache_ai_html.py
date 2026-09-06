from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardCacheAIHtml(BaseModel, TimestampMixin):
    """AI比对结果 HTML 展示缓存表"""

    id = fields.IntField(pk=True, description="主键ID")

    # 会话标识
    source_standard_no = fields.CharField(max_length=100, description="源标准编号")
    target_standard_no = fields.CharField(max_length=100, description="目标标准编号")

    # HTML 内容（仅 matched 指标）
    html_content = fields.TextField(description="大模型生成的匹配指标 HTML 片段")

    # 会话级别结论
    relationship = fields.CharField(max_length=100, null=True, description="标准关系")
    overall_assessment = fields.TextField(null=True, description="综合评价")
    calculation_time = fields.FloatField(null=True, description="比对耗时（秒）")

    # 元信息
    generation_time = fields.FloatField(null=True, description="HTML 生成耗时（秒）")
    is_valid = fields.BooleanField(default=True, description="缓存是否有效")
    algorithm_version = fields.CharField(max_length=50, default="v3_html", description="算法版本")

    class Meta:
        table = "standard_cache_ai_html"
        table_description = "AI比对结果 HTML 展示缓存表"
        indexes = [
            ("source_standard_no", "target_standard_no"),
            ("is_valid",),
            ("create_time",),
        ]


__all__ = ["StandardCacheAIHtml"]
