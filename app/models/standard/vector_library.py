"""
向量库注册表（统一向量库体系的「目录」）。

一个注册行 = 一个向量库（系统库或用户自建库）；库内容统一存 `vec_item` 大表，
靠 `library_id` 列区分（表内字段区分，不再用表名后缀）。

- 系统库（scope=system，user_id=NULL，不可删除）：standard_meta（标准元数据）/
  standard_chapter（标准章节）/ standard_term（标准术语），由启动钩子幂等种子，
  同步源为 standard_base_info / standard_jgh_pdf_chapter / standard_jgh_pdf_term
  （source_type=system_sync）。
- 用户库（scope=user）：手动条目 / 文件上传（含 xlsx）/ 数据表同步源，
  归属与权限见 app/services/vector_hub/library_service.py。

陈旧语义（派生，不靠状态位）：`embed_block` / `embed_dim` 是**构建时快照**，
读出时与激活 embed 块比较，不等即陈旧 → SearchService 拒绝查询（StaleLibraryError），
切块只置陈旧、绝不自动重建（重建=全量重嵌，是成本事件，必须显式触发）。

条目物理表（vec_item / vec_item_failed）是裸 SQL DDL（VECTOR 列 + HNSW 索引，
维度启动期解析，见 app/core/init_app.py），不走 Tortoise 建模。
"""

from tortoise import fields

from app.models.system.utils import BaseModel, TimestampMixin


class VecLibrary(BaseModel, TimestampMixin):
    """向量库注册表（系统库 + 用户自建库统一登记）。"""

    id = fields.BigIntField(pk=True, description="主键ID")
    library_key = fields.CharField(max_length=64, unique=True, description="库唯一标识：系统库 standard_meta/standard_chapter/standard_term；用户库 user_{uid}_{随机}")
    name = fields.CharField(max_length=128, description="库显示名")
    description = fields.CharField(max_length=512, null=True, default=None, description="库用途说明")
    scope = fields.CharField(max_length=16, default="user", description="归属：system=系统库（全员可读）/ user=用户库（仅本人+超管）")
    user_id = fields.BigIntField(null=True, default=None, description="用户库归属用户ID；系统库为 NULL")
    source_type = fields.CharField(max_length=16, default="manual", description="内容来源：manual=手动条目 / file=文件上传 / table_sync=数据表同步 / system_sync=系统内置同步")
    source_config = fields.JSONField(null=True, default=None, description="来源配置（table_sync 存表名/列映射等；其余来源可空）")
    embed_block = fields.CharField(max_length=64, null=True, default=None, description="构建快照：最近一次成功构建时的激活 embed 块名（陈旧判定用）")
    embed_dim = fields.IntField(null=True, default=None, description="构建快照：最近一次成功构建时的向量维度（陈旧判定用）")
    item_count = fields.IntField(default=0, description="条目数（反范式，构建后刷新，不实时 COUNT）")
    status = fields.CharField(max_length=16, default="empty", description="运行状态：empty=空闲 / building=构建中 / error=最近构建失败（ready 与陈旧为派生态）")
    last_error = fields.TextField(null=True, default=None, description="最近一次失败的错误信息")
    last_built_at = fields.DatetimeField(null=True, default=None, description="最近一次成功构建完成时间")
    is_deleted = fields.IntField(default=0, description="软删除：0=正常 1=已删除（物理条目由清理任务删）")

    class Meta:
        table = "vec_library"
        table_description = "向量库注册表（统一向量库体系）"


__all__ = ["VecLibrary"]
