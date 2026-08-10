"""
seekdb 客户端封装（个人知识库后端）

设计：
- 连接复用 STANDARD_MYSQL_*（同实例同端口）
- collection 在应用启动时通过 ensure_kb_collection() 创建/获取
- 提供同步 embedding 适配器，把项目 async embedding provider 包成 pyseekdb 需要的 sync EmbeddingFunction
- 提供 add / upsert / get / delete / hybrid_search 等高层封装
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

from loguru import logger

from app.langchain.embedding_providers import OpenAICompatibleEmbeddingProvider, get_embedding
from app.settings import APP_SETTINGS

_client_lock = threading.RLock()
_client: Optional[Any] = None  # 主线程 client（event loop 上的同步调用）
_tls = threading.local()        # 线程级隔离：每个线程独立的 client / collection
_kb_collection: Optional[Any] = None
_memstore_collection: Optional[Any] = None
_embed_fn_singleton: Optional["AsyncBridgeEmbeddingFunction"] = None


# ── Embedding 适配器 ─────────────────────────────────────────────────────────


class AsyncBridgeEmbeddingFunction:
    """
    把项目 async OpenAICompatibleEmbeddingProvider 适配成 pyseekdb 的 sync EmbeddingFunction protocol。

    pyseekdb 在 add/query 时同步调用 __call__(documents) -> List[List[float]]。
    我们用线程内 event loop 桥接，避免阻塞调用方的 loop。
    """

    @staticmethod
    def name() -> str:
        return "async_bridge_embedding"

    def __init__(self, provider: Optional[OpenAICompatibleEmbeddingProvider] = None):
        self._provider = provider or get_embedding()

    def __call__(self, documents: Any) -> list[list[float]]:
        if isinstance(documents, str):
            texts = [documents]
        else:
            texts = list(documents)
        if not texts:
            return []
        return _run_coroutine_blocking(self._provider.embed_texts(texts))

    def get_config(self) -> dict:
        return {
            "model": self._provider.model_name,
            "dimension": self._provider.dimension,
        }

    @staticmethod
    def build_from_config(config: dict) -> "AsyncBridgeEmbeddingFunction":
        return AsyncBridgeEmbeddingFunction()


def _run_coroutine_blocking(coro):
    """在独立线程的事件循环里跑 coroutine，避免与外层 loop 冲突。"""
    result_holder: dict = {}

    def _runner():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result_holder["value"] = loop.run_until_complete(coro)
        except Exception as e:  # noqa: BLE001
            result_holder["error"] = e
        finally:
            loop.close()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if "error" in result_holder:
        raise result_holder["error"]
    return result_holder["value"]


def get_embedding_function() -> AsyncBridgeEmbeddingFunction:
    global _embed_fn_singleton
    if _embed_fn_singleton is None:
        # 注册到 pyseekdb 全局，便于打开已存在 collection 时反序列化命中
        try:
            from pyseekdb import register_embedding_function
            register_embedding_function(AsyncBridgeEmbeddingFunction)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[seekdb] register_embedding_function 失败（可忽略）: {e}")
        _embed_fn_singleton = AsyncBridgeEmbeddingFunction()
    return _embed_fn_singleton


# ── 客户端单例 ───────────────────────────────────────────────────────────────


def get_client():
    """
    获取 pyseekdb RemoteServerClient。

    - 主线程（event loop）：返回全局单例，供 ensure_kb_collection 等启动期操作使用
    - 线程池线程（asyncio.to_thread）：每个线程独立 client + 独立 pymysql 连接，
      避免多线程共享连接导致 Packet sequence number wrong

    pymysql 不是线程安全的，RemoteServerClient 内部只持有一条连接，
    多线程并发写同一条连接必然协议错乱。线程级隔离是根本解法。
    """
    # 判断是否在 asyncio.to_thread 的线程池线程中
    if threading.current_thread() is threading.main_thread():
        global _client
        if _client is not None:
            return _client
        with _client_lock:
            if _client is not None:
                return _client
            _client = _create_client()
            return _client
    else:
        # 线程池线程：每个线程独立的 client
        client = getattr(_tls, "client", None)
        if client is None:
            client = _create_client()
            _tls.client = client
        return client


def _create_client():
    """创建一个新的 RemoteServerClient 实例。"""
    from pyseekdb import RemoteServerClient

    host = APP_SETTINGS.STANDARD_MYSQL_HOST
    port = APP_SETTINGS.STANDARD_MYSQL_PORT
    user_raw = APP_SETTINGS.STANDARD_MYSQL_USER
    password = APP_SETTINGS.STANDARD_MYSQL_PASSWORD
    database = APP_SETTINGS.STANDARD_MYSQL_DB
    user, tenant = user_raw.rsplit("@", 1) if "@" in user_raw else (user_raw, "sys")
    logger.info(f"[seekdb] 创建 client: {user}@{tenant}@{host}:{port}/{database} (thread={threading.current_thread().name})")
    return RemoteServerClient(
        host=host,
        port=port,
        user=user,
        tenant=tenant,
        password=password,
        database=database,
    )


# ── KB collection ───────────────────────────────────────────────────────────


def ensure_kb_collection():
    """
    确保知识库 collection 存在；返回 Collection 对象。

    线程安全：主线程返回全局单例，线程池线程返回线程级独立 collection
    （配套线程级独立 client，避免 pymysql 共享连接并发问题）。
    """
    # 线程池线程：返回线程级 collection
    if threading.current_thread() is not threading.main_thread():
        coll = getattr(_tls, "kb_collection", None)
        if coll is not None:
            return coll
        coll = _ensure_kb_collection_impl()
        _tls.kb_collection = coll
        return coll

    # 主线程：全局单例
    global _kb_collection
    if _kb_collection is not None:
        return _kb_collection
    with _client_lock:
        if _kb_collection is not None:
            return _kb_collection
        _kb_collection = _ensure_kb_collection_impl()
        return _kb_collection


def _ensure_kb_collection_impl():
    """实际的 collection 创建逻辑（不区分线程，由调用方管理缓存）。"""
    from pyseekdb import (
        FulltextIndexConfig,
        HNSWConfiguration,
        Schema,
        VectorIndexConfig,
    )

    client = get_client()
    provider = get_embedding()
    embedding_fn = get_embedding_function()
    name = APP_SETTINGS.SEEKDB_KB_COLLECTION

    # 维度漂移检测：已有 collection 维度与当前 provider 不一致时，
    # 先导出全部文本+metadata，删除重建后重新插入（自动重新向量化）
    _rebuild_data = None
    try:
        if client.has_collection(name):
            existing = client.get_collection(name)
            if existing.dimension != provider.dimension:
                logger.warning(
                    f"[seekdb] {name} 维度漂移（{existing.dimension} → {provider.dimension}），"
                    f"导出 {existing.count()} 条数据后删除重建…"
                )
                old = existing.get(include=["metadatas", "documents"])
                _rebuild_data = {
                    "ids": old.get("ids") or [],
                    "documents": old.get("documents") or [],
                    "metadatas": old.get("metadatas") or [],
                }
                client.delete_collection(name)
    except Exception as e:
        logger.warning(f"[seekdb] 维度检测/导出异常（继续执行，数据可能丢失）: {e}")
        _rebuild_data = None

    logger.info(f"[seekdb] 构造 schema (dim={provider.dimension}) …")
    schema = Schema(
        vector_index=VectorIndexConfig(
            hnsw=HNSWConfiguration(
                dimension=provider.dimension,
                distance="cosine",
            ),
            embedding_function=embedding_fn,
        ),
        fulltext_index=FulltextIndexConfig(analyzer="ik"),
    )
    logger.info(f"[seekdb] get_or_create_collection({name}) 开始 …")
    coll = client.get_or_create_collection(
        name=name,
        schema=schema,
        embedding_function=embedding_fn,
    )
    logger.info(f"[seekdb] KB collection 已就绪: {name} (dim={provider.dimension})")

    # 维度漂移重建后，把导出的文本数据重新插入（自动触发向量化）
    if _rebuild_data and _rebuild_data["ids"]:
        try:
            ids = _rebuild_data["ids"]
            docs = _rebuild_data["documents"]
            metas = _rebuild_data["metadatas"]
            batch_size = 50
            for i in range(0, len(ids), batch_size):
                end = min(i + batch_size, len(ids))
                coll.add(
                    ids=ids[i:end],
                    documents=docs[i:end],
                    metadatas=metas[i:end],
                )
            logger.info(f"[seekdb] {name} 已重新导入 {len(ids)} 条数据（新维度向量化完成）")
        except Exception as e:
            logger.error(f"[seekdb] {name} 重新导入失败（数据已导出到内存但未写入）: {e}")

    return coll


def get_kb_collection():
    """同 ensure_kb_collection，名字更顺手。"""
    return ensure_kb_collection()


# ── 高层封装 ─────────────────────────────────────────────────────────────────


def kb_upsert(
    entry_id: str,
    document: str,
    metadata: dict,
):
    """新增或更新一条知识条目。"""
    coll = get_kb_collection()
    coll.upsert(ids=entry_id, documents=document, metadatas=metadata)


def kb_delete(entry_id: str):
    coll = get_kb_collection()
    coll.delete(ids=entry_id)


def kb_get(entry_id: str) -> Optional[dict]:
    coll = get_kb_collection()
    res = coll.get(ids=entry_id, include=["metadatas", "documents"])
    if not res or not res.get("ids"):
        return None
    return {
        "id": res["ids"][0],
        "document": (res.get("documents") or [None])[0],
        "metadata": (res.get("metadatas") or [None])[0],
    }


def kb_list(
    where: Optional[dict] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    coll = get_kb_collection()
    res = coll.get(
        where=where,
        limit=limit,
        offset=offset,
        include=["metadatas", "documents"],
    )
    ids = res.get("ids") or []
    docs = res.get("documents") or [None] * len(ids)
    metas = res.get("metadatas") or [None] * len(ids)
    return [
        {"id": ids[i], "document": docs[i], "metadata": metas[i]}
        for i in range(len(ids))
    ]


def kb_hybrid_search(
    query_text: str,
    n_results: int = 5,
    where: Optional[dict] = None,
) -> list[dict]:
    """
    hybrid search：fulltext + 向量同时召回，原生 SQL 实现。

    绕过 pyseekdb 的 DBMS_HYBRID_SEARCH.GET_SQL（OceanBase CE MySQL 模式不支持该 PL/SQL 包），
    分别执行全文检索和向量近邻查询，再用 RRF (Reciprocal Rank Fusion) 合并排序。

    线程安全：get_kb_collection() 在线程池线程中返回线程级独立 collection + client，
    因此 coll._client._ensure_connection() 拿到的连接也是线程独立的，无并发问题。
    """
    import json as _json
    import re

    coll = get_kb_collection()
    table_name = f"c$v2${coll._id}"
    conn = coll._client._ensure_connection()

    # ── 1. 向量检索 ──────────────────────────────────────────────────────
    embed_fn = coll._embedding_function
    query_embedding = embed_fn([query_text])[0]
    vec_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    vec_sql = (
        f"SELECT _id, document, metadata, cosine_distance(embedding, %s) AS distance "
        f"FROM `{table_name}` ORDER BY distance APPROXIMATE LIMIT %s"
    )
    with conn.cursor() as cur:
        cur.execute(vec_sql, (vec_str, n_results * 3))
        vec_rows = cur.fetchall()

    # ── 2. 全文检索 ──────────────────────────────────────────────────────
    ft_query = re.sub(r'[+\-><()~*"]', " ", query_text).strip()
    ft_rows = []
    if ft_query:
        ft_sql = (
            f"SELECT _id, document, metadata, "
            f"MATCH(document) AGAINST(%s IN NATURAL LANGUAGE MODE) AS ft_score "
            f"FROM `{table_name}` "
            f"WHERE MATCH(document) AGAINST(%s IN NATURAL LANGUAGE MODE) "
            f"ORDER BY ft_score DESC LIMIT %s"
        )
        try:
            with conn.cursor() as cur:
                cur.execute(ft_sql, (ft_query, ft_query, n_results * 3))
                ft_rows = cur.fetchall()
        except Exception as e:
            logger.warning(f"[seekdb] 全文检索失败（降级为纯向量）: {e}")

    # ── 3. RRF 合并 ─────────────────────────────────────────────────────
    RRF_K = 60
    scores: dict[str, float] = {}
    merged: dict[str, dict] = {}

    for rank, row in enumerate(vec_rows):
        rid = row["_id"].decode("utf-8") if isinstance(row["_id"], (bytes, bytearray)) else str(row["_id"])
        scores[rid] = scores.get(rid, 0) + 1.0 / (RRF_K + rank + 1)
        merged[rid] = {
            "id": rid,
            "document": row.get("document"),
            "metadata": _json.loads(row["metadata"]) if isinstance(row.get("metadata"), str) else (row.get("metadata") or {}),
            "distance": float(row.get("distance") or 0),
        }

    for rank, row in enumerate(ft_rows):
        rid = row["_id"].decode("utf-8") if isinstance(row["_id"], (bytes, bytearray)) else str(row["_id"])
        scores[rid] = scores.get(rid, 0) + 1.0 / (RRF_K + rank + 1)
        if rid not in merged:
            merged[rid] = {
                "id": rid,
                "document": row.get("document"),
                "metadata": _json.loads(row["metadata"]) if isinstance(row.get("metadata"), str) else (row.get("metadata") or {}),
                "distance": None,
            }

    # 按 RRF 分数降序，取 top N
    ranked_ids = sorted(scores, key=scores.get, reverse=True)[:n_results]
    return [merged[rid] for rid in ranked_ids]


def kb_count(where: Optional[dict] = None) -> int:
    coll = get_kb_collection()
    if where is None:
        return coll.count()
    # seekdb count 不支持 where，用 get 兜底
    return len(kb_list(where=where, limit=10_000, offset=0))


# ── langmem Store collection ─────────────────────────────────────────────────


def ensure_memstore_collection():
    """
    长期记忆（langmem / langgraph BaseStore）专用 collection。

    与 kb_entries 隔离：KB 是用户主动管理的知识条目，memstore 是 agent 自动写
    的随手记，schema、生命周期、可见性都不一样，不能混。

    自动检测维度漂移：不一致时删除重建。

    schema：
    - documents: 用于向量化和全文检索的文本（来自 value 中 index_fields 拼接，
      或整个 value JSON）
    - metadata:
      - ns:        namespace 序列化（"a/b/c"，元素中的 / 编码为 %2F）
      - ns_depth:  namespace 长度，用于 list_namespaces 过滤
      - ns_p0..p7: namespace 前 8 层，用于 prefix/suffix 过滤
      - mem_key:   业务 key
      - value_json: 原始 value 的 JSON 字符串
      - created_at / updated_at: ISO 字符串
    """
    global _memstore_collection
    if _memstore_collection is not None:
        return _memstore_collection
    with _client_lock:
        if _memstore_collection is not None:
            return _memstore_collection
        from pyseekdb import (
            FulltextIndexConfig,
            HNSWConfiguration,
            Schema,
            VectorIndexConfig,
        )

        client = get_client()
        provider = get_embedding()
        embedding_fn = get_embedding_function()
        name = APP_SETTINGS.SEEKDB_MEMSTORE_COLLECTION

        # 维度漂移检测：导出 → 删除 → 重建 → 重新插入
        _rebuild_data = None
        try:
            if client.has_collection(name):
                existing = client.get_collection(name)
                if existing.dimension != provider.dimension:
                    logger.warning(
                        f"[seekdb] {name} 维度漂移（{existing.dimension} → {provider.dimension}），"
                        f"导出 {existing.count()} 条数据后删除重建…"
                    )
                    old = existing.get(include=["metadatas", "documents"])
                    _rebuild_data = {
                        "ids": old.get("ids") or [],
                        "documents": old.get("documents") or [],
                        "metadatas": old.get("metadatas") or [],
                    }
                    client.delete_collection(name)
        except Exception as e:
            logger.warning(f"[seekdb] 维度检测/导出异常（继续执行）: {e}")
            _rebuild_data = None

        logger.info(f"[seekdb] 构造 memstore schema (dim={provider.dimension}) …")
        schema = Schema(
            vector_index=VectorIndexConfig(
                hnsw=HNSWConfiguration(
                    dimension=provider.dimension,
                    distance="cosine",
                ),
                embedding_function=embedding_fn,
            ),
            fulltext_index=FulltextIndexConfig(analyzer="ik"),
        )
        logger.info(f"[seekdb] get_or_create_collection({name}) 开始 …")
        _memstore_collection = client.get_or_create_collection(
            name=name,
            schema=schema,
            embedding_function=embedding_fn,
        )
        logger.info(f"[seekdb] memstore collection 已就绪: {name} (dim={provider.dimension})")

        # 维度漂移重建后，重新导入数据
        if _rebuild_data and _rebuild_data["ids"]:
            try:
                ids = _rebuild_data["ids"]
                docs = _rebuild_data["documents"]
                metas = _rebuild_data["metadatas"]
                batch_size = 50
                for i in range(0, len(ids), batch_size):
                    end = min(i + batch_size, len(ids))
                    _memstore_collection.add(
                        ids=ids[i:end],
                        documents=docs[i:end],
                        metadatas=metas[i:end],
                    )
                logger.info(f"[seekdb] {name} 已重新导入 {len(ids)} 条数据")
            except Exception as e:
                logger.error(f"[seekdb] {name} 重新导入失败: {e}")

        return _memstore_collection
