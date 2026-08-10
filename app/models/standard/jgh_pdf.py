"""
标准 PDF 模型
"""

from tortoise import fields
from tortoise.models import Model


class StandardJghPdf(Model):
    """标准 PDF 基础信息表"""

    id = fields.BigIntField(pk=True, generated=False, description="主键ID")
    main_task_id = fields.BigIntField(null=True, description="主任务ID")
    file_uuid = fields.CharField(max_length=64, null=True, description="文件UUID（MD5）")
    name = fields.CharField(max_length=255, null=True, description="文件名（如 63f9dd50-d476-4f47-92f9-140862944715.pdf）")
    standard_no = fields.CharField(max_length=255, null=True, description="标准编号")
    cname = fields.TextField(null=True, description="标准名称")
    deleted = fields.BooleanField(default=False, description="软删除标记")
    # 其他字段可根据实际表结构补充

    class Meta:
        table = "standard_jgh_pdf"
        table_description = "标准PDF基础信息表"


class StandardJghPdfChapter(Model):
    """标准 PDF 章节表"""

    id = fields.BigIntField(pk=True, generated=False, description="章节ID")
    main_task_id = fields.BigIntField(null=True, index=True, description="主任务ID")
    title = fields.CharField(max_length=500, null=True, description="章节标题")
    title_no = fields.CharField(max_length=100, null=True, description="章节编号")
    page = fields.IntField(null=True, description="页码")
    word = fields.TextField(null=True, description="章节文本内容")
    deleted = fields.BooleanField(default=False, description="软删除标记")

    class Meta:
        table = "standard_jgh_pdf_chapter"
        table_description = "标准PDF章节表"


class StandardJghPdfTable(Model):
    """标准 PDF 表格表"""

    id = fields.BigIntField(pk=True, generated=False)
    main_task_id = fields.BigIntField(null=True, index=True)
    data_uuid = fields.CharField(max_length=64, null=True)
    title = fields.TextField(null=True)
    word = fields.TextField(null=True)
    image = fields.TextField(null=True)
    file_name = fields.TextField(null=True)
    page = fields.IntField(null=True)
    start = fields.IntField(null=True)
    end = fields.IntField(null=True)
    deleted = fields.BooleanField(default=False, null=True, description="软删除标记")

    class Meta:
        table = "standard_jgh_pdf_table"
        table_description = "标准PDF表格表"


class StandardJghPdfFormula(Model):
    """标准 PDF 公式表"""

    id = fields.BigIntField(pk=True, generated=False)
    main_task_id = fields.BigIntField(null=False)
    data_uuid = fields.CharField(max_length=64, null=True)
    title = fields.TextField(null=True)
    word = fields.TextField(null=True)
    image = fields.TextField(null=True)
    file_name = fields.TextField(null=True)
    page = fields.IntField(null=True)
    start = fields.IntField(null=True)
    end = fields.IntField(null=True)
    deleted = fields.BooleanField(default=False, null=True, description="软删除标记")

    class Meta:
        table = "standard_jgh_pdf_formula"
        table_description = "标准PDF公式表"
