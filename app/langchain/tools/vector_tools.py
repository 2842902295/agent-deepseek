"""
向量检索工具集

封装 FAISS 向量库，提供语义相似度检索能力，供 AI Agent 使用。
"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Optional

from langchain.tools import tool

_executor = ThreadPoolExecutor(max_workers=4)


def _run_async(coro):
    """在独立线程中运行协程，避免与外层事件循环冲突。"""
    return asyncio.run(coro)


def make_vector_search_tools(pool_id: Optional[int] = None):
    """
    创建向量检索工具，用于按语义相似度检索标准。

    Args:
        pool_id: 查重池ID，None 或 -1 表示不限制；由业务层传入，大模型不可见

    Returns:
        包含一个工具的列表：[vector_search_standards]
    """
    # pool_id 在工厂创建时固定，通过闭包注入工具，大模型无法感知或修改
    _allowed_set = None
    if pool_id is not None and pool_id != -1:
        # 直接走 db_tools 里的同步 pymysql 查询，避免在 async 上下文里跑 loop.run_until_complete
        try:
            from app.langchain.tools.db_tools import _get_allowed_standard_nos
            nos = _get_allowed_standard_nos(pool_id)
            if nos:
                _allowed_set = set(nos)
                import logging
                logging.getLogger(__name__).info(
                    f"[VectorTool] pool_id={pool_id}，范围限制 {len(_allowed_set)} 个标准"
                )
            else:
                import logging
                logging.getLogger(__name__).warning(
                    f"[VectorTool] pool_id={pool_id}，未能加载范围，将查全库"
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                f"[VectorTool] 加载 pool_id={pool_id} 失败: {e}，将查全库"
            )

    @tool
    def vector_search_standards(
        query_name: Annotated[str, "查询用的标准名称或关键词"],
        query_use_range: Annotated[Optional[str], "查询用的适用范围描述，可选"] = None,
        top_k: Annotated[int, "返回结果数量，默认 10，最大建议 30"] = 10,
    ) -> str:
        """
        通过语义向量检索，查找与给定名称或描述最相似的标准。
        适合用于模糊查询：如"有没有关于XXX的标准"、"哪些标准和XXX相关"等。
        结果按相似度降序排列，similarity 字段值域为 0~1，越高越相似。
        """
        from app.langchain.vector_store import get_vector_store

        async def _search():
            vector_store = get_vector_store()
            return await vector_store.search_similar(
                query_name=query_name,
                query_use_range=query_use_range,
                top_k=top_k if _allowed_set is None else top_k * 5,
            )

        try:
            future = _executor.submit(_run_async, _search())
            results = future.result()

            if not results:
                return json.dumps({"ok": True, "count": 0, "results": []}, ensure_ascii=False)

            items = [
                {
                    "standard_no": metadata.get("standard_no"),
                    "cname": metadata.get("cname"),
                    "use_range": metadata.get("use_range"),
                    "similarity": round(score, 4),
                }
                for metadata, score in results
            ]

            # 按查重池过滤
            if _allowed_set is not None:
                items = [i for i in items if i.get("standard_no") in _allowed_set]

            # 裁剪到 top_k
            items = items[:top_k]

            return json.dumps({"ok": True, "count": len(items), "results": items}, ensure_ascii=False)

        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    return [vector_search_standards]
