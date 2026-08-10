"""seekdb 客户端封装"""

from app.services.seekdb.client import (
    AsyncBridgeEmbeddingFunction,
    ensure_kb_collection,
    ensure_memstore_collection,
    get_client,
    get_embedding_function,
    get_kb_collection,
    kb_count,
    kb_delete,
    kb_get,
    kb_hybrid_search,
    kb_list,
    kb_upsert,
)

__all__ = [
    "AsyncBridgeEmbeddingFunction",
    "ensure_kb_collection",
    "ensure_memstore_collection",
    "get_client",
    "get_embedding_function",
    "get_kb_collection",
    "kb_count",
    "kb_delete",
    "kb_get",
    "kb_hybrid_search",
    "kb_list",
    "kb_upsert",
]
