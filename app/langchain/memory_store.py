"""
长期记忆 Store 单例：基于自定义 OceanBaseStore（seekdb collection 后端）。

- 存储位置：seekdb collection（OceanBase 之上），collection 名见
  APP_SETTINGS.SEEKDB_MEMSTORE_COLLECTION，默认 `langmem_store`
- 跨会话、跨用户共享，按 namespace 隔离（langmem 用 ("memories", "{user_id}")）
- 向量索引：seekdb HNSW + 全文索引 ik，hybrid search 默认开启
- 嵌入字段：默认 ["content"]，与 langmem.create_manage_memory_tool 写入 schema 对齐

历史：早期用 langgraph.store.sqlite.AsyncSqliteStore + .agent_workspace/memory.sqlite，
该文件已不再被读写但仍保留磁盘备份；如需迁移到新后端可单独写脚本读出再 aput。
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from app.langchain.oceanbase_store import OceanBaseStore


_store: Optional[OceanBaseStore] = None


async def get_memory_store() -> OceanBaseStore:
    """异步获取长期记忆 Store 单例。首次调用会触发 seekdb collection 创建。"""
    global _store
    if _store is not None:
        return _store

    _store = OceanBaseStore(default_index_fields=["content"])
    # 主动 warm up 一次 collection（捕捉早期连接错误，比 lazy 触发更可读）
    try:
        _store._get_coll()  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        logger.warning(f"长期记忆 Store warmup 失败（首次 put/search 时会重试）: {e}")
    logger.info("长期记忆 Store 已初始化（OceanBaseStore + seekdb）")
    return _store


__all__ = ["get_memory_store"]
