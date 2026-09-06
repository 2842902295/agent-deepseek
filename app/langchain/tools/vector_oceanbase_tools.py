"""
OceanBase 向量检索工具集（统一向量库体系）

底层走 app/services/vector_hub 的 SearchService：
  - standard_meta    系统库：一标准一向量（标准名称 + 适用范围）
  - standard_chapter 系统库：一章节一向量（【章节号】标题 + 正文摘录）
陈旧库（构建快照与激活 embed 块不一致）会被 SearchService 拒绝查询，
工具层转 {"ok": false, "error": ...}，由 agent 提示先重建向量库。

工厂入口：make_vector_ob_tools(pool_id)，返回三个 @tool：
  - vector_search_standards_ob   语义召回相似标准
  - vector_search_chapters       语义召回章节（可指定 standard_no 范围）
  - vector_compare_standards     对两个标准做章节级对应映射，给差异分析做底
"""

from __future__ import annotations

import json
from typing import Annotated, Dict, List, Optional

import aiomysql
from langchain.tools import tool
from loguru import logger

from app.services.mysql_pool import standard_pool
from app.services.vector_hub.search import search_service


async def _load_word_map(chapter_ids: List[int]) -> Dict[int, str]:
    """按 chapter_id 批量取正文摘录（与库内收录口径一致：word[:3000]）。

    vec_item 的 payload 不含正文（正文已嵌入 content，payload 保持精简），
    工具返回需要 word_excerpt 时回源表补。
    """
    ids = sorted({int(i) for i in chapter_ids if i is not None})
    if not ids:
        return {}
    out: Dict[int, str] = {}
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                ph = ",".join(["%s"] * len(chunk))
                await cur.execute(
                    f"SELECT id, LEFT(IFNULL(word,''),3000) AS word_excerpt "
                    f"FROM standard_jgh_pdf_chapter WHERE id IN ({ph})",
                    chunk,
                )
                for r in await cur.fetchall():
                    out[int(r["id"])] = r["word_excerpt"] or ""
    return out


# 元数据补齐口径：整行全字段返回（只剔纯系统列），不维护手工挑选的字段子集——
# 历史上子集遗漏导致过「返回字段缺 act_date」这类问题，全字段才不会再漏。
_META_DROP_COLUMNS = {"id", "deleted", "creator", "updater", "create_time", "update_time", "is_relate_standard_jgh", "is_relate_standard_pdf"}


async def _load_meta_fields(standard_nos: List[str]) -> Dict[str, dict]:
    """按 standard_no 批量补齐 standard_base_info 全部元数据字段。

    语义召回结果直接带上该标准的全字段（剔 id/deleted/creator/updater 等
    纯系统列，空值字段由调用方剥离），agent 拿到即可直接交付，无需另发一轮
    SQL「补数」——历史教训：补数环节曾被偷换成关键词 LIKE 重搜，导致语义
    召回结果被丢弃；也曾是手工字段子集，导致缺字段。
    """
    nos = sorted({s for s in standard_nos if s})
    if not nos:
        return {}
    out: Dict[str, dict] = {}
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            for i in range(0, len(nos), 500):
                chunk = nos[i : i + 500]
                ph = ",".join(["%s"] * len(chunk))
                await cur.execute(
                    f"SELECT * FROM standard_base_info WHERE standard_no IN ({ph})",
                    chunk,
                )
                for r in await cur.fetchall():
                    out[r["standard_no"]] = {
                        k: v for k, v in r.items() if k not in _META_DROP_COLUMNS and k != "standard_no"
                    }
    return out


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

    async def _ensure_allowed() -> Optional[List[str]]:
        if not allowed_holder["loaded"]:
            if pool_id is not None and pool_id != -1:
                from app.langchain.tools.db_tools import _get_allowed_standard_nos
                allowed_holder["value"] = await _get_allowed_standard_nos(pool_id)
            allowed_holder["loaded"] = True
        return allowed_holder["value"]

    @tool
    async def vector_search_standards_ob(
        query: Annotated[str, "查询语义文本，可以是标准名称、关键词、适用范围描述"],
        top_k: Annotated[
            int,
            "返回条数，默认 100。按目标领域的宽窄自行决定：目标是找全且找准——"
            "宽泛领域（子方向多、相关标准多）传更大的值保证召回充分，"
            "窄而精确的查找用小值；无固定上限",
        ] = 100,
        exclude_no: Annotated[Optional[str], "需要排除的标准编号（通常是基线自己）"] = None,
        min_score: Annotated[float, "最低相关度阈值，低于则过滤"] = 0.5,
    ) -> str:
        """
        在标准元数据语义库上做标准级语义召回。
        返回相似度从高到低的候选标准，每条含 standard_no / cname / use_range /
        cosine_score（越接近 1 越相似），并已随结果补齐该标准的全部元数据字段
        （归口/起草单位、发布/实施/废止日期、状态、采标/替代关系等，空字段剔除），
        可直接用于清单编制与导出，无需再发 SQL 补字段。
        """
        try:
            allowed_nos = await _ensure_allowed()
            # 池过滤走 ref_key IN（ref_key = standard_no）；exclude_no 多取一条再剔除。
            # 池列表为空 = 不过滤（与旧语义一致；SearchService 的显式空列表会返回空结果）
            fetch_k = int(top_k) + (1 if exclude_no else 0)
            rows = await search_service.search(
                ["standard_meta"],
                query,
                top_k=fetch_k,
                ref_in=allowed_nos or None,
            )
            results = []
            for r in rows:
                if exclude_no and r["refKey"] == exclude_no:
                    continue
                if len(results) >= int(top_k):
                    break
                if r["score"] < min_score:
                    continue
                p = r["payload"] or {}
                results.append(
                    {
                        "standard_no": p.get("standard_no") or r["refKey"],
                        "cname": p.get("cname"),
                        "use_range": p.get("use_range"),
                        "cosine_score": r["score"],
                    }
                )
            # 按编号批量补齐常用元数据（只补非空字段，控制返回体积）
            meta_map = await _load_meta_fields([item["standard_no"] for item in results])
            for item in results:
                extra = meta_map.get(item["standard_no"]) or {}
                for k, v in extra.items():
                    if v is not None and v != "":
                        item[k] = v
            # default=str：issue_date 等 DATE 列返回 datetime.date，需转字符串
            return json.dumps({"ok": True, "count": len(results), "results": results}, ensure_ascii=False, default=str)
        except Exception as e:
            logger.exception("[vector_search_standards_ob] failed")
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    @tool
    async def vector_search_chapters(
        query: Annotated[str, "章节内容相关的查询文本（要求/方法/参数描述等）"],
        top_k: Annotated[int, "返回条数，按任务需要自行决定（广撒网/盘点类任务可放大，精确查找用小值），无固定上限"] = 100,
        scope_standard_nos: Annotated[
            Optional[str],
            "限定召回范围的标准号，多个用逗号分隔；不传则在全库章节中检索",
        ] = None,
        min_score: Annotated[float, "最低相关度阈值，低于则过滤"] = 0.5,
    ) -> str:
        """
        在标准章节语义库上做章节级语义召回。
        典型用法：把 A 标准某章节文本作为 query，scope_standard_nos 限定到 B/C/D 几个目标标准，
        看在它们里有没有对应章节。返回 standard_no / title_no / title / word_excerpt / cosine_score。
        """
        try:
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

            rows = await search_service.search(
                ["standard_chapter"],
                query,
                top_k=int(top_k),
                ref_in=final_scope,
            )

            kept = [r for r in rows if r["score"] >= min_score]
            word_map = await _load_word_map([(r["payload"] or {}).get("chapter_id") for r in kept])
            results = []
            for r in kept:
                p = r["payload"] or {}
                cid = p.get("chapter_id")
                results.append(
                    {
                        "standard_no": p.get("standard_no") or r["refKey"],
                        "chapter_id": cid,
                        "title_no": p.get("title_no"),
                        "title": p.get("title"),
                        "word_excerpt": word_map.get(int(cid), "") if cid is not None else "",
                        "cosine_score": r["score"],
                    }
                )
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
            from app.langchain.embedding_providers import get_embedding
            from app.services.vector_hub.adapters import CHAPTER_WORD_LIMIT, chapter_source_where

            # A 端章节直读源表（与库内收录同口径：非空正文 + 跳导航标题）
            # chapter_source_where() 含 LIKE 通配字面 %；本句带参执行时 aiomysql
            # 会 %-formatting 整句，须先把字面 % 加倍转义，否则崩溃
            skip_where = chapter_source_where().replace("%", "%%")
            async with standard_pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        "SELECT c.id AS chapter_id, c.title_no, c.title, "
                        f"LEFT(IFNULL(c.word,''),{CHAPTER_WORD_LIMIT}) AS word_excerpt "
                        "FROM standard_jgh_pdf_chapter c "
                        "JOIN standard_jgh_pdf p ON p.main_task_id = c.main_task_id "
                        f"WHERE p.standard_no = %s AND {skip_where} "
                        "ORDER BY c.id "
                        "LIMIT %s",
                        [standard_no_a, int(max_sections)],
                    )
                    a_rows = list(await cur.fetchall())

            if not a_rows:
                return json.dumps(
                    {"ok": True, "note": f"{standard_no_a} 无章节向量", "pairs": []},
                    ensure_ascii=False,
                )

            # A 端批量 embed（provider 内置并发分块 + 限流退避），
            # 逐条经 SearchService 在 B 端做 ref_key 过滤 ANN。
            query_texts = [(r.get("word_excerpt") or r.get("title") or "")[:3000] for r in a_rows]
            embeddings = await get_embedding().embed_texts(query_texts)

            raw_pairs = []
            all_b_ids: List[int] = []
            for ar, emb in zip(a_rows, embeddings):
                b_rows = await search_service.search_with_vector(
                    ["standard_chapter"],
                    emb,
                    top_k=int(top_k_per_section),
                    ref_key=standard_no_b,
                )
                raw_pairs.append((ar, b_rows))
                all_b_ids.extend((br["payload"] or {}).get("chapter_id") for br in b_rows)

            word_map = await _load_word_map(all_b_ids)

            pairs = []
            for ar, b_rows in raw_pairs:
                b_candidates = []
                for br in b_rows:
                    p = br["payload"] or {}
                    cid = p.get("chapter_id")
                    b_candidates.append(
                        {
                            "chapter_id": cid,
                            "title_no": p.get("title_no"),
                            "title": p.get("title"),
                            "word_excerpt": word_map.get(int(cid), "") if cid is not None else "",
                            "cosine_score": br["score"],
                        }
                    )
                pairs.append(
                    {
                        "a": {
                            "chapter_id": ar["chapter_id"],
                            "title_no": ar["title_no"],
                            "title": ar["title"],
                            "word_excerpt": ar["word_excerpt"],
                        },
                        "b_candidates": b_candidates,
                    }
                )

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
