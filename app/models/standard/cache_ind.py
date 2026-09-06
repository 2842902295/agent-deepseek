from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardCacheInd(BaseModel, TimestampMixin):
    """标准指标提取缓存表（每条记录存储一个指标）"""

    id = fields.IntField(pk=True, description="主键ID")

    # 标准信息
    standard_no = fields.CharField(max_length=100, description="标准编号")
    standard_name = fields.CharField(max_length=500, null=True, description="标准名称")

    # 提取批次
    run_id = fields.CharField(max_length=64, null=True, description="提取批次ID（同一次提取共享）")
    run_remark = fields.CharField(max_length=500, null=True, description="提取备注（如模型名称、实验说明等）")

    # 指标类型
    standard_structure_type = fields.CharField(max_length=30, null=True,
                                               description="标准性质：has_ind_and_test/has_ind_only/has_test_only/ind_embedded_in_test/not_extractable")
    indicator_type = fields.CharField(max_length=20,
                                      description="指标类型：static=静态指标 | dynamic=动态指标（由 norm_class 推导）")

    # 标准化对象（所有指标通用）
    standard_object = fields.CharField(max_length=200, null=True, description="标准化对象（产品/材料/部件名称）")
    applicable_object = fields.CharField(max_length=200, null=True,
                                         description='应用对象（与标准化对象并列的应用主体，如"乘用车"）')
    object_type = fields.CharField(max_length=50, null=True,
                                   description="标准化对象类型：产品类对象/服务类对象/过程类对象")
    indicator_category = fields.CharField(max_length=50, null=True,
                                          description="指标分类（来自标准化对象分类体系）")
    norm_class = fields.CharField(max_length=20, null=True,
                                  description="规范类别：基础类/规范类/方法类")

    # 静态指标字段（indicator_type=static 时使用）
    indicator_object = fields.CharField(max_length=200, null=True, description="[静态] 指标对象名称")
    source_value = fields.TextField(null=True, description="[静态] 规定值（含单位）")

    # 动态指标字段（indicator_type=dynamic 时使用）
    experiment_name = fields.CharField(max_length=200, null=True, description="[动态] 实验项目名称")
    source_input_params = fields.TextField(null=True, description="[动态] 输入参数")
    source_process_logic = fields.TextField(null=True, description="[动态] 过程逻辑")
    source_result = fields.TextField(null=True, description="[动态] 判定结果（含单位）")

    # 公共字段
    source_clause = fields.CharField(max_length=200, null=True, description="对应条款")

    # 元信息
    extraction_time = fields.FloatField(null=True, description="提取耗时（秒）")
    is_valid = fields.BooleanField(default=True, description="缓存是否有效（同一标准只有最新run有效）")
    algorithm_version = fields.CharField(max_length=50, default="v1", description="提取算法版本")

    class Meta:
        table = "standard_cache_ind"
        table_description = "标准指标提取缓存表"
        indexes = [
            ("standard_no",),
            ("run_id",),
            ("indicator_type",),
            ("is_valid",),
            ("create_time",),
        ]


__all__ = ["StandardCacheInd"]

