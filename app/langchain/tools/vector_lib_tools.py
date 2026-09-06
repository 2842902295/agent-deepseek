"""
向量库管理工具集（agent 用，统一向量库体系的对话侧入口）

工厂闭包注入 uid / is_super（LLM 看不到也改不了归属）。读写全走
app/services/vector_hub/，权限口径与管理页 API 一致（2026-08-27 统一）：
工具组仅管理员挂载（两桥 is_admin 门控），面内所有库（含系统库与他人库）
全体管理员同权可读可写。陈旧库搜索返回 {"ok": false}（无逃生门）。

工具列表：
- vector_lib_list:         列出全部向量库（系统库 + 所有用户库）
- vector_lib_search:       语义搜索（跨库；陈旧库拒绝）
- vector_lib_create:       新建用户向量库（手动条目来源）
- vector_lib_add_items:    批量添加条目（三档增量）
- vector_lib_delete_items: 按 itemKey 批量删除条目

返回值统一 JSON 字符串，便于 agent 阅读。
"""

from __future__ import annotations

import json
import secrets
from typing import Annotated, Any, List, Optional

from langchain.tools import tool

from app.services.vector_hub import library_service as hub_lib
from app.services.vector_hub.ingest import VecCandidate, ingest_candidates
from app.services.vector_hub.search import search_service
from app.services.vector_hub.state import StaleLibraryError, derive_lib_state, get_active_embed_info


def _ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def _coerce_items(value: Any) -> List[dict]:
    """宽松解析 items 参数（LLM 可能把 list 序列化成 JSON 字符串）。"""
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                value = parsed
        except Exception:
            return []
    if not isinstance(value, list):
        return []
    out: List[dict] = []
    for it in value:
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, str) and it.strip():
            out.append({"content": it})
    return out


def make_vector_lib_tools(uid: int, is_super: bool = False):
    """按用户构建向量库工具集（两桥挂载入口）。"""

    async def _visible_libs():
        from app.models.standard import VecLibrary

        # 工具组仅管理员挂载（两桥 is_admin 门控），面内所有库同权可见
        return await VecLibrary.filter(is_deleted=0).order_by("scope", "id")

    async def _get_writable(key: str):
        """取库 + 写权限校验，返回 (lib, err_json)。"""
        lib = await hub_lib.get_library(key)
        if lib is None:
            return None, _err(f"向量库不存在：{key}")
        try:
            hub_lib.assert_can_write(lib, uid, is_super)
        except hub_lib.PermissionDeniedError as e:
            return None, _err(str(e))
        return lib, None

    @tool
    async def vector_lib_list() -> str:
        """列出全部向量库（系统库 + 所有用户库，均可读写）。

        返回每个库的 libraryKey、名称、来源类型、条目数、状态
        （ready=可搜索 / stale=陈旧需重建 / building / error / empty）。
        """
        libs = await _visible_libs()
        active_block, active_dim = get_active_embed_info()
        rows = []
        for lib in libs:
            rows.append(
                {
                    "libraryKey": lib.library_key,
                    "name": lib.name,
                    "scope": lib.scope,
                    "sourceType": lib.source_type,
                    "itemCount": lib.item_count or 0,
                    "state": derive_lib_state(lib, active_block, active_dim),
                    "description": lib.description,
                }
            )
        return _ok(rows)

    @tool
    async def vector_lib_search(
        query: Annotated[str, "搜索文本（自然语言查询）"],
        library_key: Annotated[Optional[str], "目标库 libraryKey；缺省搜索全部可见库"] = None,
        top_k: Annotated[int, "返回条数，按任务需要自行决定（广撒网/盘点类任务可放大，精确查找用小值），无固定上限"] = 10,
    ) -> str:
        """在向量库中做语义搜索（用户自建资料 / 系统标准库统一入口）。

        返回相似条目（itemKey / refKey / payload / score，score=1-余弦距离）。
        陈旧库（切换过向量模型后未重建）会明确报错，不会返回失效结果。
        """
        if library_key:
            keys = [library_key]
        else:
            keys = [lib.library_key for lib in await _visible_libs()]
            if not keys:
                return _err("没有可搜索的向量库")
        try:
            results = await search_service.search(keys, query, top_k=max(1, int(top_k)))
        except StaleLibraryError as e:
            return _err(str(e))
        except KeyError as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"搜索失败：{e}")
        return _ok(results)

    @tool
    async def vector_lib_create(
        name: Annotated[str, "库显示名"],
        description: Annotated[Optional[str], "库用途说明"] = None,
    ) -> str:
        """新建一个用户向量库（内容来源：手动条目，用 vector_lib_add_items 写入）。

        返回新库的 libraryKey，后续写入 / 搜索都用它。
        """
        try:
            lib = await hub_lib.create_user_library(
                uid=uid, name=name.strip(), description=description, source_type="manual"
            )
        except ValueError as e:
            return _err(str(e))
        return _ok({"libraryKey": lib.library_key, "name": lib.name})

    @tool
    async def vector_lib_add_items(
        library_key: Annotated[str, "目标库 libraryKey"],
        items: Annotated[list, "条目列表，每项 {content: 必填文本, itemKey?, refKey?, payload?}"],
    ) -> str:
        """向用户向量库批量添加条目（三档增量：内容不变不重嵌）。

        itemKey 缺省自动生成；重复 itemKey 视为更新。单次最多 500 条。
        """
        lib, err = await _get_writable(library_key)
        if err:
            return err
        parsed = _coerce_items(items)
        if not parsed:
            return _err("items 为空或格式错误（需要 [{content, itemKey?, refKey?, payload?}]）")
        if len(parsed) > 500:
            return _err("单次最多添加 500 条")
        candidates: List[VecCandidate] = []
        for it in parsed:
            content = str(it.get("content") or "").strip()
            if not content:
                continue
            ik = str(it.get("itemKey") or "").strip() or f"m_{secrets.token_hex(4)}"
            payload = it.get("payload")
            candidates.append(
                VecCandidate(
                    item_key=ik,
                    content=content,
                    payload=payload if isinstance(payload, dict) else None,
                    ref_key=str(it["refKey"])[:128] if it.get("refKey") else None,
                )
            )
        if not candidates:
            return _err("没有有效条目（content 不能为空）")
        try:
            counts = await ingest_candidates(lib.id, candidates, delete_missing=False)
        except RuntimeError as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"写入失败：{e}")
        return _ok(counts)

    @tool
    async def vector_lib_delete_items(
        library_key: Annotated[str, "目标库 libraryKey"],
        item_keys: Annotated[list, "要删除的 itemKey 列表"],
    ) -> str:
        """从用户向量库中按 itemKey 批量删除条目。"""
        lib, err = await _get_writable(library_key)
        if err:
            return err
        if isinstance(item_keys, str):
            try:
                item_keys = json.loads(item_keys)
            except Exception:
                item_keys = [item_keys]
        keys = [str(k)[:191] for k in (item_keys or []) if k]
        if not keys:
            return _err("item_keys 不能为空")
        from app.models.standard import VecLibrary
        from app.services.mysql_pool import standard_pool
        from app.services.vector_hub.state import physical_table

        deleted = 0
        table = physical_table()
        async with standard_pool.acquire() as conn:
            async with conn.cursor() as cur:
                for i in range(0, len(keys), 500):
                    chunk = keys[i : i + 500]
                    ph = ",".join(["%s"] * len(chunk))
                    await cur.execute(
                        f"DELETE FROM {table} WHERE library_id=%s AND item_key IN ({ph})",
                        [lib.id, *chunk],
                    )
                    if cur.rowcount and cur.rowcount > 0:
                        deleted += cur.rowcount
                await cur.execute(f"SELECT COUNT(*) FROM {table} WHERE library_id=%s", [lib.id])
                (cnt,) = await cur.fetchone()
        await VecLibrary.filter(id=lib.id).update(item_count=int(cnt))
        return _ok({"deleted": deleted, "itemCount": int(cnt)})

    return [vector_lib_list, vector_lib_search, vector_lib_create, vector_lib_add_items, vector_lib_delete_items]


__all__ = ["make_vector_lib_tools"]
