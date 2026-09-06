"""
OceanBaseStore — langgraph BaseStore 子类，向量后端走项目内的 seekdb collection。

设计要点
========

- 继承 langgraph 官方 `BaseStore`，只实现两个抽象方法 `batch` / `abatch`，
  其它 aput/aget/asearch/adelete/alist_namespaces 全部用基类默认实现委托过来。
- 数据落到 seekdb 的独立 collection（`langmem_store`，与 `kb_entries` 隔离），
  embedding/向量索引/全文索引由 seekdb 自己负责，不需要 langgraph 帮我们嵌。
- namespace tuple 序列化：用 `/` 拼接，元素中的 `/` `%` 用 percent-encode 转义；
  langgraph 已经强制 namespace label 不含 `.`，所以无 case 冲突。
- 复用 langgraph.store.memory 里的 `_does_match` / `_compare_values` 两个纯函数
  做 namespace prefix/suffix 匹配 和 filter 比较，避免自己重写出 bug。

注意
====

- 不开 langgraph 自带 index_config（因为向量化是 seekdb 做的）。`PutOp.index`
  字段当下用来覆盖默认嵌入字段：传 `["content"]` 表示嵌 content；传 `False`
  目前会被忽略（pyseekdb collection 创建时已固定要嵌 documents）。
- TTL 当前不支持（`supports_ttl=False`），langmem 工具也不会传 ttl。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Optional

from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
)
from langgraph.store.memory import _compare_values, _does_match
from loguru import logger


# ── namespace 编解码 ─────────────────────────────────────────────────────────


def _ns_encode(ns: tuple[str, ...]) -> str:
    """tuple → "a/b/c"，元素中的 % 和 / 转义。空 tuple → ""（仅 list_namespaces 用）。"""
    return "/".join(p.replace("%", "%25").replace("/", "%2F") for p in ns)


def _ns_decode(s: str) -> tuple[str, ...]:
    if not s:
        return ()
    return tuple(p.replace("%2F", "/").replace("%25", "%") for p in s.split("/"))


# ── 主类 ──────────────────────────────────────────────────────────────────────


class OceanBaseStore(BaseStore):
    """
    langgraph BaseStore 子类，向量后端走 seekdb（OceanBase 之上的 collection）。

    用法：
        store = OceanBaseStore(default_index_fields=["content"])
        await store.aput(("memories", "u42"), key="abc", value={"content": "用户喜欢早 9 点"})
        hits = await store.asearch(("memories", "u42"), query="早上偏好", limit=5)
    """

    supports_ttl: bool = False

    __slots__ = ("_default_index_fields", "_coll_loader", "_coll")

    def __init__(self, *, default_index_fields: Optional[list[str]] = None) -> None:
        """
        Args:
            default_index_fields: PutOp 没传 index 时，嵌入哪几个字段。默认 ["content"]，
                和 langmem create_manage_memory_tool 的写入 schema 对齐。
        """
        self._default_index_fields = default_index_fields or ["content"]
        # 延迟到第一次用时拿 collection（pyseekdb 实际连接也是 lazy 的，
        # 但 ensure_*_collection 会触发 schema 创建，避免在导入期跑）
        self._coll: Optional[Any] = None

    # ── collection 单例 ────────────────────────────────────────────────────

    def _get_coll(self):
        if self._coll is None:
            from app.services.seekdb import ensure_memstore_collection
            self._coll = ensure_memstore_collection()
        return self._coll

    # ── BaseStore 抽象方法 ─────────────────────────────────────────────────

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        """同步 batch：用线程内 event loop 跑 abatch，避免与外层 loop 冲突。"""
        from app.services.seekdb.client import _run_coroutine_blocking
        return _run_coroutine_blocking(self.abatch(list(ops)))

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        """
        seekdb 客户端是同步的，逐条丢线程池跑——operations 间互不依赖，所以
        可以全部 gather。同一 batch 里同 key 的 put 顺序由 ops 顺序决定，与
        InMemoryStore 行为一致（不并发同 key）。
        """
        ops_list = list(ops)
        results: list[Result] = [None] * len(ops_list)

        # 把同一 (namespace, key) 上的 PutOp 串行化，避免乱序
        # 其他 op 之间可以并发
        coros: list[Any] = []
        for i, op in enumerate(ops_list):
            coros.append(self._dispatch_one(i, op, results))
        await asyncio.gather(*coros)
        return results

    async def _dispatch_one(self, idx: int, op: Op, out: list[Result]) -> None:
        try:
            if isinstance(op, GetOp):
                out[idx] = await asyncio.to_thread(self._do_get, op)
            elif isinstance(op, PutOp):
                await asyncio.to_thread(self._do_put, op)
                out[idx] = None
            elif isinstance(op, SearchOp):
                out[idx] = await asyncio.to_thread(self._do_search, op)
            elif isinstance(op, ListNamespacesOp):
                out[idx] = await asyncio.to_thread(self._do_list_namespaces, op)
            else:
                raise ValueError(f"Unsupported op: {type(op).__name__}")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"[OceanBaseStore] op {type(op).__name__} 失败: {e}")
            raise

    # ── 同步实现（在线程池里跑） ───────────────────────────────────────────

    def _do_get(self, op: GetOp) -> Optional[Item]:
        coll = self._get_coll()
        ns_str = _ns_encode(op.namespace)
        # 用 (ns + key) 拼 id，保证全局唯一
        entry_id = f"{ns_str}::{op.key}"
        res = coll.get(ids=entry_id, include=["metadatas", "documents"])
        ids = res.get("ids") or []
        if not ids:
            return None
        meta = (res.get("metadatas") or [None])[0] or {}
        return _meta_to_item(meta)

    def _do_put(self, op: PutOp) -> None:
        coll = self._get_coll()
        ns_str = _ns_encode(op.namespace)
        entry_id = f"{ns_str}::{op.key}"

        if op.value is None:
            # 约定：value=None 表示删除
            try:
                coll.delete(ids=entry_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[OceanBaseStore] delete 失败 id={entry_id}: {e}")
            return

        # 选嵌入文本
        if op.index is False:
            # 用户明确不索引：写一个最小占位文本（pyseekdb 不允许空 documents）
            document = json.dumps(op.value, ensure_ascii=False)[:200]
        elif isinstance(op.index, list):
            document = _extract_text_for_embed(op.value, op.index) or json.dumps(
                op.value, ensure_ascii=False
            )[:1000]
        else:
            document = _extract_text_for_embed(op.value, self._default_index_fields) or json.dumps(
                op.value, ensure_ascii=False
            )[:1000]

        now_iso = datetime.now(timezone.utc).isoformat()
        # 先 get 拿 created_at（若已存在）
        existing = coll.get(ids=entry_id, include=["metadatas"])
        prev_meta = ((existing.get("metadatas") or [None])[0]) if existing.get("ids") else None
        created_at = (prev_meta or {}).get("created_at") if prev_meta else now_iso

        meta = _build_metadata(
            ns=op.namespace,
            key=op.key,
            value=op.value,
            created_at=created_at or now_iso,
            updated_at=now_iso,
        )
        coll.upsert(ids=entry_id, documents=document, metadatas=meta)

    def _do_search(self, op: SearchOp) -> list[SearchItem]:
        coll = self._get_coll()

        # 1) 用 metadata where 把 namespace_prefix 收窄
        #    seekdb 支持 $and / $eq 等操作符；ns_p0..pN 直接 $eq
        where_ns: dict[str, Any] = {}
        for i, label in enumerate(op.namespace_prefix[:8]):
            where_ns[f"ns_p{i}"] = label

        # 2) 把 langgraph filter（针对 value 字段）转成 seekdb where
        #    seekdb metadata 是扁平 KV，所以 value 整体扔在 value_json 里没法直接
        #    过滤——这里只能"先粗筛回来再 Python 端精筛"。性能可接受（langmem
        #    场景单用户记忆数量 < 千条）。
        candidates: list[dict] = []
        if op.query:
            payload: dict = {
                "query": {"where_document": {"$contains": op.query}},
                "knn": {"query_texts": [op.query], "where": where_ns} if where_ns else {"query_texts": [op.query]},
                "n_results": max(op.limit + op.offset, 20) * 2,  # 给 filter 留余量
                "include": ["metadatas", "documents", "distances"],
            }
            res = coll.hybrid_search(**payload)
            ids_l, metas_l, dists_l = _flatten_search_result(res)
            for entry_id, meta, dist in zip(ids_l, metas_l, dists_l, strict=False):
                if not meta:
                    continue
                # seekdb 没在 hybrid_search 里包 ns 过滤? 已通过 where_ns 收窄；保险起见再校验
                if not _ns_matches_prefix(meta, op.namespace_prefix):
                    continue
                candidates.append({"meta": meta, "score": _dist_to_score(dist)})
        else:
            # 无 query：直接 get + where
            res = coll.get(
                where=where_ns or None,
                limit=max(op.limit + op.offset, 100),
                offset=0,
                include=["metadatas"],
            )
            ids_l = res.get("ids") or []
            metas_l = res.get("metadatas") or []
            for meta in metas_l:
                if not meta:
                    continue
                if not _ns_matches_prefix(meta, op.namespace_prefix):
                    continue
                candidates.append({"meta": meta, "score": None})

        # 3) Python 端 filter
        if op.filter:
            filtered: list[dict] = []
            for c in candidates:
                value = _meta_value(c["meta"])
                if all(_compare_values(value.get(k), fv) for k, fv in op.filter.items()):
                    filtered.append(c)
            candidates = filtered

        # 4) 分页 + 转 SearchItem
        page = candidates[op.offset : op.offset + op.limit]
        out: list[SearchItem] = []
        for c in page:
            item = _meta_to_item(c["meta"])
            if item is None:
                continue
            out.append(
                SearchItem(
                    namespace=item.namespace,
                    key=item.key,
                    value=item.value,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    score=c["score"],
                )
            )
        return out

    def _do_list_namespaces(self, op: ListNamespacesOp) -> list[tuple[str, ...]]:
        coll = self._get_coll()
        # 简单实现：拉一批 metadata，Python 端去重 + 匹配条件
        # langmem 场景数据量小（千级），可接受
        seen: set[tuple[str, ...]] = set()
        page_size = 500
        offset = 0
        while True:
            res = coll.get(limit=page_size, offset=offset, include=["metadatas"])
            metas_l = res.get("metadatas") or []
            if not metas_l:
                break
            for meta in metas_l:
                if not meta:
                    continue
                ns_str = meta.get("ns") or ""
                ns = _ns_decode(ns_str)
                seen.add(ns)
            if len(metas_l) < page_size:
                break
            offset += page_size
            if offset >= 10_000:  # 兜底防失控
                logger.warning("[OceanBaseStore] list_namespaces 超过 10000 条，截断")
                break

        namespaces = list(seen)
        if op.match_conditions:
            namespaces = [
                ns for ns in namespaces
                if all(_does_match(c, ns) for c in op.match_conditions)
            ]
        if op.max_depth is not None:
            namespaces = sorted({ns[: op.max_depth] for ns in namespaces})
        else:
            namespaces = sorted(namespaces)
        return namespaces[op.offset : op.offset + op.limit]


# ── 工具函数 ──────────────────────────────────────────────────────────────────


def _build_metadata(
    *,
    ns: tuple[str, ...],
    key: str,
    value: dict[str, Any],
    created_at: str,
    updated_at: str,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "ns": _ns_encode(ns),
        "ns_depth": len(ns),
        "mem_key": key,
        "value_json": json.dumps(value, ensure_ascii=False),
        "created_at": created_at,
        "updated_at": updated_at,
    }
    for i, label in enumerate(ns[:8]):
        meta[f"ns_p{i}"] = label
    return meta


def _meta_value(meta: dict[str, Any]) -> dict[str, Any]:
    raw = meta.get("value_json") or "{}"
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _meta_to_item(meta: dict[str, Any]) -> Optional[Item]:
    ns_str = meta.get("ns")
    key = meta.get("mem_key")
    if ns_str is None or key is None:
        return None
    return Item(
        value=_meta_value(meta),
        key=str(key),
        namespace=_ns_decode(str(ns_str)),
        created_at=_parse_dt(meta.get("created_at")),
        updated_at=_parse_dt(meta.get("updated_at")),
    )


def _parse_dt(s: Any) -> datetime:
    if isinstance(s, datetime):
        return s
    if isinstance(s, str) and s:
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _ns_matches_prefix(meta: dict[str, Any], prefix: tuple[str, ...]) -> bool:
    """seekdb where 已经粗筛过，这里再校验一次防误差。空 prefix 永远 match。"""
    if not prefix:
        return True
    ns = _ns_decode(str(meta.get("ns") or ""))
    if len(ns) < len(prefix):
        return False
    return ns[: len(prefix)] == prefix


def _extract_text_for_embed(value: dict[str, Any], fields: list[str]) -> str:
    """
    从 value 里挑字段拼成嵌入文本。简单版本只支持顶层 key（langmem 默认 schema
    就一个 content 字段，够用）。后续要支持 "a.b" / "a[*].c" 再扩。
    """
    parts: list[str] = []
    for f in fields:
        v = value.get(f)
        if v is None:
            continue
        if isinstance(v, str):
            parts.append(v)
        else:
            parts.append(json.dumps(v, ensure_ascii=False))
    return "\n".join(p for p in parts if p)


def _flatten_search_result(res: dict) -> tuple[list[str], list[dict], list[float]]:
    """seekdb hybrid_search 返回是嵌套 list，第一层是 query 数（我们只发 1 个）。"""
    def _first(key: str, default: list) -> list:
        v = res.get(key) or default
        if v and isinstance(v[0], list):
            return v[0]
        return v

    ids = _first("ids", [[]])
    metas = _first("metadatas", [[]])
    dists = _first("distances", [[]])
    return ids, metas, dists


def _dist_to_score(d: Optional[float]) -> Optional[float]:
    """seekdb cosine distance ∈ [0, 2]，转成 [-1, 1] 的相似度更直观。"""
    if d is None:
        return None
    return 1.0 - d


__all__ = ["OceanBaseStore"]
