from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardObjRel(BaseModel, TimestampMixin):
    """标准化对象关系表"""

    id = fields.BigIntField(pk=True, description="主键ID")

    subj_obj = fields.CharField(max_length=200, description="主体对象")
    rel_type = fields.CharField(max_length=50, description="关系类型：包含 | 属于 | 细分 | 归属 | 配套")
    obj_obj = fields.CharField(max_length=200, description="客体对象")
    rel_desc = fields.CharField(max_length=500, null=True, description="关系描述")

    src_type = fields.CharField(max_length=20, default="agent", description="来源类型：agent | seed 等")
    src_standard_no = fields.CharField(max_length=100, description="来源标准号")
    src_clause_id = fields.BigIntField(null=True, description="来源章节ID（standard_jgh_pdf_chapter.id）")

    confidence = fields.CharField(max_length=10, default="high", description="置信度：high | medium | low")
    deleted = fields.BooleanField(default=False, description="软删除")

    class Meta:
        table = "standard_obj_rel"
        table_description = "标准化对象关系表"
        indexes = [
            ("subj_obj",),
            ("obj_obj",),
            ("rel_type",),
            ("src_type",),
            ("deleted",),
        ]


__all__ = ["StandardObjRel"]
