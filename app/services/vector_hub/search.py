"""
统一向量搜索服务：内部消费方（查重召回 / QA 向量工具 / 调度）与对外 MCP 全走这里。

行为契约：
- 陈旧库拒绝查询（StaleLibraryError，无逃生门、不降级）——库的构建快照与激活
  embed 块不一致即陈旧；切块只置陈旧、绝不自动重建。
- 过滤 ANN：`WHERE library_id IN (...) ORDER BY COSINE_DISTANCE LIMIT k`
  （生产已有先例：旧 vector_compare_standards；ref_key 列承载 standard_no
  等值/IN 过滤，避免 payload JSON_EXTRACT）。
- 返回行带 libraryKey / itemKey / refKey / payload / score（1 - cosine 距离）。
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

import aiomysql

from app.services.mysql_pool import standard_pool
from app.services.vector_hub.state import (
    StaleLibraryError,
    format_vector_literal,
    get_active_embed_info,
    is_lib_stale,
    physical_table,
)


class SearchService:
    """统一搜索入口（无状态，随用随建）。"""

    async def _resolve_libraries(self, library_keys: List[str]) -> List:
        """取库 + 陈旧检查。任一库陈旧 → StaleLibraryError（整批拒绝）。"""
        from app.models.standard import VecLibrary

        libs = await VecLibrary.filter(library_key__in=library_keys, is_deleted=0)
        found = {lib.library_key: lib for lib in libs}
        missing = [k for k in library_keys if k not in found]
        if missing:
            raise KeyError(f"向量库不存在：{missing}")
        active_block, active_dim = get_active_embed_info()
        ordered = [found[k] for k in library_keys]
        for lib in ordered:
            # is_lib_stale（纯快照比较）：status=error 的陈旧库同样拒绝查询，
            # 展示态被 error 短路不代表向量空间有效（无逃生门语义）
            if is_lib_stale(lib, active_block, active_dim):
                raise StaleLibraryError(
                    library_keys=[lib.library_key],
                    lib_snapshot=f"{lib.embed_block}/{lib.embed_dim}",
                    active_block=active_block or "?",
                    active_dim=active_dim,
                )
        return ordered

    async def search(
        self,
        library_keys: List[str],
        query: str,
        *,
        top_k: int = 10,
        ref_key: Optional[str] = None,
        ref_in: Optional[List[str]] = None,
        min_score: Optional[float] = None,
    ) -> List[Dict]:
        """文本查询：嵌入 query 后走 search_with_vector。"""
        from app.langchain.embedding_providers import get_embedding

        await self._resolve_libraries(library_keys)  # 先查陈旧，避免白烧一次嵌入
        vec = await get_embedding().embed_query(query)
        return await self.search_with_vector(
            library_keys,
            vec,
            top_k=top_k,
            ref_key=ref_key,
            ref_in=ref_in,
            min_score=min_score,
        )

    async def search_with_vector(
        self,
        library_keys: List[str],
        vector: List[float],
        *,
        top_k: int = 10,
        ref_key: Optional[str] = None,
        ref_in: Optional[List[str]] = None,
        min_score: Optional[float] = None,
    ) -> List[Dict]:
        """向量查询（A 端批量嵌入逐条比对等场景直接复用）。"""
        libs = await self._resolve_libraries(library_keys)
        key_by_id = {lib.id: lib.library_key for lib in libs}
        ids = list(key_by_id.keys())

        if ref_in is not None and not ref_in:
            return []  # 显式空过滤集 → 无结果（与旧工具语义一致）

        table = physical_table()
        where = f"library_id IN ({','.join(['%s'] * len(ids))})"
        params: List = list(ids)
        if ref_key is not None:
            where += " AND ref_key = %s"
            params.append(ref_key)
        elif ref_in is not None:
            where += f" AND ref_key IN ({','.join(['%s'] * len(ref_in))})"
            params.extend(ref_in)

        vec_str = format_vector_literal(vector)
        sql = (
            f"SELECT library_id, item_key, ref_key, payload, "
            f"(1 - COSINE_DISTANCE(embedding, '{vec_str}')) AS score "
            f"FROM {table} "
            f"WHERE {where} "
            f"ORDER BY COSINE_DISTANCE(embedding, '{vec_str}') ASC "
            f"LIMIT {int(top_k)}"
        )
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = list(await cur.fetchall())

        results: List[Dict] = []
        for r in rows:
            # SQL 里 score = 1 - COSINE_DISTANCE，直接取
            score = round(float(r["score"]), 4)
            if min_score is not None and score < min_score:
                continue
            payload = r["payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = None
            results.append(
                {
                    "libraryKey": key_by_id.get(r["library_id"]),
                    "itemKey": r["item_key"],
                    "refKey": r["ref_key"],
                    "payload": payload,
                    "score": score,
                }
            )
        return results


# 默认单例（无状态）
search_service = SearchService()


__all__ = ["SearchService", "search_service"]
