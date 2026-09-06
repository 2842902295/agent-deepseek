from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardCacheSim(BaseModel, TimestampMixin):
    """全文相似度计算缓存表"""

    id = fields.IntField(pk=True, description="主键ID")

    # 标准信息
    source_standard_no = fields.CharField(max_length=100, description="源标准编号")
    source_standard_name = fields.CharField(max_length=500, null=True, description="源标准名称")
    target_standard_no = fields.CharField(max_length=100, description="目标标准编号")
    target_standard_name = fields.CharField(max_length=500, null=True, description="目标标准名称")

    # 相似度结果
    similarity_percentage = fields.FloatField(description="相似度百分比")
    matched_sentence_count = fields.IntField(description="匹配的句子数")
    source_total_sentence_count = fields.IntField(description="源标准总句子数")
    target_total_sentence_count = fields.IntField(description="目标标准总句子数")

    # 匹配详情（JSON 格式存储）
    matches = fields.JSONField(description="匹配详情列表")
    # 格式: [{"source_chapter_id": 1, "source_sentence_text": "...", "similarity": 85.5, ...}, ...]

    # 元数据
    calculation_time = fields.FloatField(null=True, description="计算耗时（秒）")
    threshold = fields.FloatField(default=0.70, description="相似度阈值")
    algorithm_version = fields.CharField(max_length=50, default="ngram_v1", description="算法版本")

    # 状态管理
    is_valid = fields.BooleanField(default=True, description="缓存是否有效")
    expire_time = fields.DatetimeField(null=True, description="过期时间")

    class Meta:
        table = "standard_cache_sim"
        table_description = "全文相似度计算缓存表"
        indexes = [
            ("source_standard_no", "target_standard_no"),  # 联合索引，用于快速查询
            ("create_time",),
            ("is_valid",),
        ]
        # 联合唯一约束：同一对标准只保留一条有效记录
        unique_together = [("source_standard_no", "target_standard_no", "is_valid")]


__all__ = ["StandardCacheSim"]
