from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardBaseInfo(BaseModel, TimestampMixin):
    """标准基础信息表（源数据表）"""

    id = fields.CharField(pk=True, max_length=64, description="主键ID（MD5）")
    standard_no = fields.CharField(max_length=200, null=True, description="标准号")
    cname = fields.TextField(null=True, description="标准名称")
    ename = fields.TextField(null=True, description="标准英文名称")
    use_range = fields.TextField(null=True, description="适用范围")
    intl_cat = fields.CharField(max_length=200, null=True, description="国际分类号")
    nat_cat = fields.CharField(max_length=200, null=True, description="国内分类号")
    std_domain = fields.CharField(max_length=200, null=True, description="标准领域")
    std_field = fields.CharField(max_length=200, null=True, description="标准领域分类")
    std_year = fields.CharField(max_length=10, null=True, description="标准年份")
    std_obj = fields.TextField(null=True, description="标准对象")
    issue_date = fields.DateField(null=True, description="发布日期")
    act_date = fields.DateField(null=True, description="实施日期")
    annul_date = fields.DateField(null=True, description="废止日期")
    approval_unit = fields.TextField(null=True, description="批准单位")
    put_unit = fields.TextField(null=True, description="提出单位")
    lead_unit = fields.TextField(null=True, description="归口单位")
    draft_unit_main = fields.TextField(null=True, description="主要起草单位")
    draft_unit = fields.TextField(null=True, description="起草单位")
    draft_staff = fields.TextField(null=True, description="起草人")
    chief_unit = fields.TextField(null=True, description="主管部门")
    mgr_dept = fields.TextField(null=True, description="行业管理部门")
    is_secret = fields.BooleanField(null=True, description="是否涉密")
    std_nature = fields.CharField(max_length=50, null=True, description="标准性质")
    mandatory_clause = fields.TextField(null=True, description="强制性条款")
    patent_info = fields.TextField(null=True, description="专利信息")
    state = fields.CharField(max_length=50, null=True, description="标准状态")
    security_level = fields.CharField(max_length=50, null=True, description="密级")
    release_history = fields.TextField(null=True, description="发布历史")
    release_std_no = fields.CharField(max_length=200, null=True, description="发布标准号")
    replace_description = fields.TextField(null=True, description="替代说明")
    replace_stds = fields.TextField(null=True, description="替代标准")
    target_stds = fields.TextField(null=True, description="被替代标准")
    adopt_situation = fields.TextField(null=True, description="采用情况")
    adopt_std_no = fields.CharField(max_length=200, null=True, description="采用标准号")
    adopt_text = fields.TextField(null=True, description="采用说明")
    adopt_level = fields.CharField(max_length=50, null=True, description="采用程度")
    adopt_type = fields.CharField(max_length=50, null=True, description="采用方式")
    adopt_no = fields.CharField(max_length=200, null=True, description="采用编号")
    adopt_name = fields.TextField(null=True, description="采用标准名称")
    gjb_no = fields.CharField(max_length=200, null=True, description="国军标编号")
    std_type = fields.CharField(max_length=50, null=True, description="标准类型")
    industry = fields.CharField(max_length=200, null=True, description="所属行业")
    remark = fields.TextField(null=True, description="备注")
    creator = fields.CharField(max_length=64, null=True, description="创建人")
    updater = fields.CharField(max_length=64, null=True, description="更新人")

    # 软删除标记
    deleted = fields.BooleanField(default=False, description="软删除标记")

    class Meta:
        table = "standard_base_info"
        table_description = "标准基础信息表"


__all__ = ["StandardBaseInfo"]
