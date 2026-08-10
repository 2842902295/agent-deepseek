from tortoise import fields
from tortoise.models import Model


class StandardImageFillLog(Model):
    """图片文本回填任务日志。每条记录对应一次对单张图片的 MinerU 解析尝试。"""

    id = fields.BigIntField(pk=True)
    run_date = fields.DateField(description="执行日期，用于按天聚合统计")
    source_table = fields.CharField(max_length=64, index=True, description="来源表名: standard_jgh_pdf_table / standard_jgh_pdf_formula")
    source_id = fields.BigIntField(index=True, description="来源表的主键 ID")
    file_name = fields.TextField(null=True, description="图片文件名")
    status = fields.CharField(max_length=16, index=True, description="处理结果: ok / failed")
    error_msg = fields.TextField(null=True, description="失败时的错误信息")
    elapsed_ms = fields.IntField(null=True, description="本条记录处理耗时（毫秒）")
    create_time = fields.DatetimeField(auto_now_add=True, description="日志写入时间")

    class Meta:
        table = "standard_image_fill_log"
        table_description = "图片文本回填任务日志"
