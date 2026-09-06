from tortoise import fields

from app.models.system.utils import BaseModel


class StandardCacheIndTestRel(BaseModel):
    """指标-试验关联表"""

    id = fields.IntField(pk=True)
    ind_id = fields.IntField(description="关联 standard_cache_ind.id")
    test_id = fields.IntField(description="关联 standard_cache_test.id")
    run_id = fields.CharField(max_length=64, null=True, description="所属提取批次")

    class Meta:
        table = "standard_cache_ind_test_rel"
        table_description = "指标-试验关联表"
        indexes = [
            ("ind_id",),
            ("test_id",),
        ]


__all__ = ["StandardCacheIndTestRel"]
