"""
OceanBase 向量检索工具集

依赖：
  - standard_vec_meta    一标准一向量（cname + use_range）
  - standard_vec_chapter 一章节一向量（title + word 截断）
  两表均使用 VECTOR(EMBED_DIMENSION) + HNSW + cosine 距离索引（见 init_app.modify_db）。

工厂入口：make_vector_ob_tools(pool_id)，返回三个 @tool：
  - vector_search_standards_ob   语义召回相似标准
  - vector_search_chapters       语义召回章节（可指定 standard_no 范围）
  - vector_compare_standards     对两个标准做章节级对应映射，给差异分析做底
"""

from __future__ import annotations

import json
from typing import Annotated, List, Optional

import aiomysql
from langchain.tools import tool
from loguru import logger

from app.langchain.embedding_providers import get_local_embedding, vec_table_suffix
from app.services.mysql_pool import standard_pool


def _format_vector(vec: List[float]) -> str:
    """OceanBase 接受 '[v1,v2,...]' 字符串作为 VECTOR 字面量。"""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


async def _embed_one(text: str) -> List[float]:
    # 必须与 builder 端使用同一 provider（向量空间一致才能召回）
    return await get_local_embedding().embed_query(text)


def make_vector_ob_tools(pool_id: Optional[int] = None):
    """
    创建 OceanBase 向量检索工具组（async 工厂，pool_id 闭包注入）。

    Args:
        pool_id: 查重池 ID，None / -1 表示全库；非空时所有召回会附加
                 standard_no IN (...) 过滤条件。

    Returns:
        [vector_search_standards_ob, vector_search_chapters, vector_compare_standards]
    """
    allowed_holder: dict = {"loaded": False, "value": None}

    _s = vec_table_suffix()
    _meta_table = f"standard_vec_meta{_s}"
    _chapter_table = f"standard_vec_chapter{_s}"

    async def _ensure_allowed() -> Optional[List[str]]:
        if not allowed_holder["loaded"]:
            if pool_id is not None and pool_id != -1:
                from app.langchain.tools.db_tools import _get_allowed_standard_nos
                allowed_holder["value"] = await _get_allowed_standard_nos(pool_id)
            allowed_holder["loaded"] = True
        return allowed_holder["value"]

    def _build_in_clause(allowed_nos: Optional[List[str]]) -> str:
        if not allowed_nos:
            return ""
        quoted = ",".join("'" + no.replace("'", "''") + "'" for no in allowed_nos)
        return f" AND standard_no IN ({quoted})"

    @tool
    async def vector_search_standards_ob(
        query: Annotated[str, "查询语义文本，可以是标准名称、关键词、适用范围描述"],
        top_k: Annotated[int, "返回条数（建议 ≤50）"] = 10,
        exclude_no: Annotated[Optional[str], "需要排除的标准编号（通常是基线自己）"] = None,
        min_score: Annotated[float, "最低相关度阈值，低于则过滤"] = 0.5,
    ) -> str:
        """
        在 standard_vec_meta 上做标准级语义召回。
        返回相似度从高到低的候选标准（含 standard_no / cname / use_range / cosine_score，越接近 1 越相似）。
        """
        try:
            embedding = await _embed_one(query)
            vec_str = _format_vector(embedding)
            allowed_nos = await _ensure_allowed()

            where = "1=1"
            if exclude_no:
                where += " AND standard_no <> %s"
            where += _build_in_clause(allowed_nos)

            sql = (
                "SELECT standard_no, cname, use_range, "
                f"COSINE_DISTANCE(embedding, '{vec_str}') AS dist "
                f"FROM {_meta_table} "
                f"WHERE {where} "
                f"ORDER BY dist ASC LIMIT {int(top_k)}"
            )
            params: list = []
            if exclude_no:
                params.append(exclude_no)

            async with standard_pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(sql, params)
                    rows = list(await cur.fetchall())

            results = [
                {
                    "standard_no": r["standard_no"],
                    "cname": r["cname"],
                    "use_range": r["use_range"],
                    "cosine_score": round(1.0 - float(r["dist"]), 4),
                }
                for r in rows
                if (1.0 - float(r["dist"])) >= min_score
            ]
            return json.dumps({"ok": True, "count": len(results), "results": results}, ensure_ascii=False)
        except Exception as e:
            logger.exception("[vector_search_standards_ob] failed")
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    @tool
    async def vector_search_chapters(
        query: Annotated[str, "章节内容相关的查询文本（要求/方法/参数描述等）"],
        top_k: Annotated[int, "返回条数（建议 ≤50）"] = 10,
        scope_standard_nos: Annotated[
            Optional[str],
            "限定召回范围的标准号，多个用逗号分隔；不传则在全库章节中检索",
        ] = None,
        min_score: Annotated[float, "最低相关度阈值，低于则过滤"] = 0.5,
    ) -> str:
        """
        在 standard_vec_chapter 上做章节级语义召回。
        典型用法：把 A 标准某章节文本作为 query，scope_standard_nos 限定到 B/C/D 几个目标标准，
        看在它们里有没有对应章节。返回 standard_no / title_no / title / word_excerpt / cosine_score。
        """
        try:
            embedding = await _embed_one(query)
            vec_str = _format_vector(embedding)
            allowed_nos = await _ensure_allowed()

            scope_list: List[str] = []
            if scope_standard_nos:
                scope_list = [s.strip() for s in scope_standard_nos.split(",") if s.strip()]

            # 把 pool 限定与显式 scope 合并取交集
            final_scope: Optional[List[str]] = None
            if scope_list and allowed_nos:
                final_scope = [s for s in scope_list if s in set(allowed_nos)]
            elif scope_list:
                final_scope = scope_list
            elif allowed_nos:
                final_scope = allowed_nos

            where = "1=1"
            if final_scope is not None:
                if not final_scope:
                    return json.dumps({"ok": True, "count": 0, "results": []}, ensure_ascii=False)
                quoted = ",".join("'" + s.replace("'", "''") + "'" for s in final_scope)
                where += f" AND standard_no IN ({quoted})"

            sql = (
                "SELECT standard_no, chapter_id, title_no, title, word_excerpt, "
                f"COSINE_DISTANCE(embedding, '{vec_str}') AS dist "
                f"FROM {_chapter_table} "
                f"WHERE {where} "
                f"ORDER BY dist ASC LIMIT {int(top_k)}"
            )

            async with standard_pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(sql)
                    rows = list(await cur.fetchall())

            results = [
                {
                    "standard_no": r["standard_no"],
                    "chapter_id": r["chapter_id"],
                    "title_no": r["title_no"],
                    "title": r["title"],
                    "word_excerpt": r["word_excerpt"],
                    "cosine_score": round(1.0 - float(r["dist"]), 4),
                }
                for r in rows
                if (1.0 - float(r["dist"])) >= min_score
            ]
            return json.dumps({"ok": True, "count": len(results), "results": results}, ensure_ascii=False)
        except Exception as e:
            logger.exception("[vector_search_chapters] failed")
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    @tool
    async def vector_compare_standards(
        standard_no_a: Annotated[str, "源标准编号"],
        standard_no_b: Annotated[str, "目标标准编号"],
        top_k_per_section: Annotated[int, "A 每章在 B 中匹配多少个候选，默认 1"] = 1,
        max_sections: Annotated[int, "最多对比 A 多少章，默认 200，避免 token 爆炸"] = 200,
    ) -> str:
        """
        差异分析专用：拿 A 标准的每个章节去 B 标准章节中找语义最近邻。
        返回 [{A 章节, B 候选列表(含 cosine_score)}] 列表，按 A 章节 id 升序。
        相似度低（cosine_score 偏低）的对应关系即「A 有 B 缺 / 双方分歧大」的差异点候选。
        """
        try:
            async with standard_pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        "SELECT id, chapter_id, title_no, title, word_excerpt "
                        f"FROM {_chapter_table} "
                        "WHERE standard_no = %s "
                        "ORDER BY chapter_id "
                        "LIMIT %s",
                        [standard_no_a, int(max_sections)],
                    )
                    a_rows = list(await cur.fetchall())

            if not a_rows:
                return json.dumps(
                    {"ok": True, "note": f"{standard_no_a} 无章节向量", "pairs": []},
                    ensure_ascii=False,
                )

            # 用 A 端的 word_excerpt（落库时已组好 title + 截断正文）作为查询文本，
            # 批量 embed 后逐条检索 B 端最近邻。embed 走 provider 内置的并发分块，
            # 不会一次性打爆 DashScope。
            query_texts = [
                (r.get("word_excerpt") or r.get("title") or "")[:3000] for r in a_rows
            ]
            embedder = get_local_embedding()
            embeddings = await embedder.embed_texts(query_texts)

            pairs = []
            async with standard_pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    for ar, emb in zip(a_rows, embeddings):
                        vec_str = _format_vector(emb)
                        sql = (
                            "SELECT chapter_id, title_no, title, word_excerpt, "
                            f"COSINE_DISTANCE(embedding, '{vec_str}') AS dist "
                            f"FROM {_chapter_table} "
                            "WHERE standard_no = %s "
                            f"ORDER BY dist ASC LIMIT {int(top_k_per_section)}"
                        )
                        await cur.execute(sql, [standard_no_b])
                        b_rows = list(await cur.fetchall())

                        pairs.append({
                            "a": {
                                "chapter_id": ar["chapter_id"],
                                "title_no": ar["title_no"],
                                "title": ar["title"],
                                "word_excerpt": ar["word_excerpt"],
                            },
                            "b_candidates": [
                                {
                                    "chapter_id": br["chapter_id"],
                                    "title_no": br["title_no"],
                                    "title": br["title"],
                                    "word_excerpt": br["word_excerpt"],
                                    "cosine_score": round(1.0 - float(br["dist"]), 4),
                                }
                                for br in b_rows
                            ],
                        })

            return json.dumps(
                {
                    "ok": True,
                    "standard_no_a": standard_no_a,
                    "standard_no_b": standard_no_b,
                    "a_sections": len(a_rows),
                    "pairs": pairs,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.exception("[vector_compare_standards] failed")
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    return [vector_search_standards_ob, vector_search_chapters, vector_compare_standards]


__all__ = ["make_vector_ob_tools"]
