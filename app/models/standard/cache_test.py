from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class StandardCacheTest(BaseModel, TimestampMixin):
    """标准试验提取缓存表（每条记录存储一个试验）"""

    id = fields.IntField(pk=True)

    standard_no = fields.CharField(max_length=100, description="标准编号")
    standard_name = fields.CharField(max_length=500, null=True, description="标准名称")

    run_id = fields.CharField(max_length=64, null=True, description="提取批次ID（与 standard_cache_ind 共享）")
    run_remark = fields.CharField(max_length=500, null=True, description="提取备注")

    # 试验本体（对应 GB/T 20001.4 各节）
    test_name = fields.CharField(max_length=200, description="试验名称（不含‘试验’后缀）")
    method_desc = fields.TextField(null=True, description="试验方法概述（试样制备、计算方法等）")
    conditions = fields.TextField(null=True, description="试验条件（温度、湿度、电压等环境参数）")
    preparation = fields.TextField(null=True, description="试验准备（试样处理、仪器校准、系统搭建）")
    procedure = fields.TextField(null=True, description="试验过程（按逻辑次序的操作步骤）")
    acceptance = fields.TextField(null=True, description="合格判据（为空表示纯方法类，无判定准则）")
    report_items = fields.TextField(null=True, description="报告要点（主要用于纯试验方法类标准）")
    source_clause = fields.CharField(max_length=200, null=True, description="来源条款")

    # 分类（与指标共用体系）
    standard_object = fields.CharField(max_length=200, null=True, description="标准化对象")
    applicable_object = fields.CharField(max_length=200, null=True, description="适用对象")
    object_type = fields.CharField(max_length=50, null=True, description="标准化对象类型")
    indicator_category = fields.CharField(max_length=50, null=True, description="指标类别")

    is_valid = fields.BooleanField(default=True, description="缓存是否有效")
    algorithm_version = fields.CharField(max_length=50, default="v3", description="算法版本")

    class Meta:
        table = "standard_cache_test"
        table_description = "标准试验提取缓存表"
        indexes = [
            ("standard_no",),
            ("run_id",),
            ("is_valid",),
        ]


__all__ = ["StandardCacheTest"]
