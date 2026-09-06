"""
vector_hub：统一向量库服务层。

一个注册表（vec_library）+ 一张统一条目表（vec_item，表内 library_id 区分库）。
所有消费方——查重召回、QA 向量工具、调度同步、对外 MCP、前端管理页——
一律经本包读写，不直接碰 vec_item。

模块分工：
- state.py           激活块解析 / 陈旧派生 / 物理表间接层
- library_service.py 注册表 CRUD + 权限 + 系统库种子
- ingest.py          三档增量摄入（Python diff / SQL 下推两路）
- adapters.py        来源适配器（内容模板单一真相源）
- build.py           库构建执行器（调度/启动自建/管理页共用，占用表防并发）
- search.py          SearchService（陈旧拒绝，无逃生门）
- migration.py       存量迁移 + 启动钩子
"""

from app.services.vector_hub.search import SearchService, search_service
from app.services.vector_hub.state import (
    StaleLibraryError,
    derive_lib_state,
    ensure_buildable_dims,
    get_active_embed_info,
    get_vec_item_dim,
)

__all__ = [
    "StaleLibraryError",
    "derive_lib_state",
    "ensure_buildable_dims",
    "get_active_embed_info",
    "get_vec_item_dim",
    "SearchService",
    "search_service",
]
