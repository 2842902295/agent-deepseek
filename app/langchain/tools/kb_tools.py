"""
KB 管理工具集（agent 用）

通过对话指挥 agent 整理个人知识库。每个工具都通过工厂函数闭包注入 user_id，
避免 LLM 看到或篡改 user_id。

工具列表：
- kb_search:  按 query 检索知识库（hybrid）
- kb_create:  新建一条知识
- kb_update:  更新（含追加来源、归档）
- kb_delete:  删除
- kb_merge:   把多条合并成一条
- kb_split:   把一条拆成多条

返回值统一 JSON 字符串，便于 agent 阅读。

注意：seekdb 客户端是同步阻塞 RPC，所有 @tool 一律 async + asyncio.to_thread。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Annotated, Any, Optional

from langchain.tools import tool

from app.api.v1.ai.kb import (
    _build_document,
    _build_tags_flat,
    _new_entry_metadata,
    _normalize_tag,
    _now_ms,
    _read_tags,
)
from app.services.seekdb import (
    kb_delete as _kb_delete_impl,
)
from app.services.seekdb import (
    kb_get,
    kb_hybrid_search,
    kb_list,
    kb_upsert,
)


def _ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def _coerce_str_list(value: Any) -> list[str]:
    """
    某些 LLM（如 qwen3.7-max）调工具时会把 list[str] 错误地序列化成 JSON 字符串
    `'["a","b"]'`，Pydantic 校验直接挂掉。这里宽松解析：
      - None / 空 → []
      - list → 直接用，过滤非 str
      - JSON 字符串数组 → 反序列化
      - 单一字符串 → 按 [, /、，] 切分
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if x is not None and str(x).strip()]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed if x is not None and str(x).strip()]
            except Exception:
                pass
        return [t.strip() for t in s.replace("，", ",").replace("、", ",").split(",") if t.strip()]
    return []


def _parse_meta_for_view(meta: dict) -> dict:
    return {
        "title": meta.get("title", ""),
        "summary": meta.get("summary", ""),
        "tags": _read_tags(meta),
        "groupTag": meta.get("group_tag") or None,
        "isArchived": bool(meta.get("is_archived", 0)),
    }


def make_kb_tools(user_id: Optional[int]):
    """根据 user_id 创建一组 KB 管理工具，返回工具列表。"""

    if user_id is None:
        # 未登录态：返回空列表，避免 agent 误用
        return []

    @tool
    async def kb_search(
        query: Annotated[
            str,
            "检索的关键词或问题。可以为空——为空时配合 tags 做纯标签过滤"
            "（『列出 #客户管理系统 下所有条目』这类需求）。"
        ],
        top_k: Annotated[int, "返回数量，默认 5"] = 5,
        tags: Annotated[
            Optional[list[str]],
            "标签过滤（可选）。传了之后只返回**同时打有所有这些标签**的条目（AND 语义）。"
            "和 query 是正交两根轴：query 管相关性，tags 管精确归属。"
            "想知道某个标签下有什么 → 只传 tags；想在某标签范围内找语义相关的 → 两个都传。",
        ] = None,
    ) -> str:
        """
        在用户的「知识库」个人知识库里检索相关条目。

        ⚠️ 此工具仅用于用户个人收集/整理的知识（笔记、灵感、待办等），
        **不包含标准信息**。查询标准（标准号、标准名称、标准正文、标准对比等）
        请使用 vector_search_standards_ob / get_standard_chapters 等标准专用工具，
        不要调用本工具。

        两根独立轴：
        - **query**（可选）：fulltext + 语义 hybrid 检索，按相关度排序
        - **tags**（可选）：精确过滤，必须同时打齐所有指定标签的条目才返回

        两者都不传时返回最近更新的条目（兜底用，避免空结果）。
        返回命中条目的 id、标题、摘要、标签。
        """
        q = (query or "").strip()
        tag_filters = [t.strip().lstrip("#") for t in (tags or []) if t and t.strip()]

        try:
            if q:
                # 有 query：走 hybrid。tags 过滤走 tags_flat 这一展平字段（Python 端做包含判断；
                # seekdb where 不便对 JSON 内做安全 contains，展平字段更直接，未来要 pushdown
                # 到数据层 like 也很轻）。多召回几条以补偿 tag 过滤后的余量。
                pool = max(top_k * 4, 20) if tag_filters else top_k
                hits = await asyncio.to_thread(kb_hybrid_search, q, pool)
            else:
                # 无 query：走全表 list 拿候选（仍然是当前用户的范围），按 updated_at 倒序
                rows = await asyncio.to_thread(kb_list, None, 10_000, 0)
                hits = sorted(
                    rows,
                    key=lambda r: (r.get("metadata") or {}).get("updated_at") or 0,
                    reverse=True,
                )
        except Exception as e:  # noqa: BLE001
            return _err(f"检索失败：{e}")

        items = []
        for h in hits:
            meta = h.get("metadata") or {}
            if meta.get("user_id") != user_id:
                continue
            if meta.get("is_archived"):
                continue
            if tag_filters:
                # 用展平字段做精确包含过滤；_read_tags 已经处理了老条目 fallback
                entry_tags = set(_read_tags(meta))
                if not all(_normalize_tag(f) in entry_tags for f in tag_filters):
                    continue
            items.append({
                "id": h["id"],
                **_parse_meta_for_view(meta),
                "distance": h.get("distance"),
            })
            if len(items) >= top_k:
                break
        return _ok(items)

    @tool
    async def kb_create(
        title: Annotated[str, "标题"],
        entry_type: Annotated[
            str,
            "条目类型：knowledge(知识，长文)/idea(灵感，短文)/todo(待办)",
        ],
        content: Annotated[
            Optional[str],
            "正文：knowledge=markdown 长文必填；idea=一两句必填；todo=补充描述可选",
        ] = None,
        summary: Annotated[Optional[str], "摘要/说明，1~2 句"] = None,
        tags: Annotated[
            Any,
            "标签数组 list[str]。用作目录维度（『归到哪类』），不是描述维度。优先选对象/主题"
            "（如 #客户管理系统 #读书），避开动作/状态/时间。1~3 个，最多 5 个。"
            "拿不准某个标签是否已在库里、或它的 canonical 形态是什么——直接 kb_search 这个标签词，"
            "看返回条目的 tags 字段沿用即可。详见 sediment skill 的「标签纪律」。",
        ] = None,
        due_at: Annotated[Optional[int], "todo 截止时间毫秒戳（todo 必填）"] = None,
        todo_status: Annotated[Optional[str], "todo 状态：pending(默认) / done / overdue"] = None,
        primary_artifact_id: Annotated[
            Optional[int],
            "主体 artifact id（兼容旧数据用，新条目建议用 attachments）",
        ] = None,
        svg_artifact_id: Annotated[
            Optional[int],
            "配对 SVG artifact id（兼容旧数据用）",
        ] = None,
        idea_status: Annotated[
            Optional[str],
            "idea 状态：active(默认，未消化) / digested(已消化：要么实现了、要么细想后觉得没用)",
        ] = None,
        attachments: Annotated[
            Optional[list],
            "附件列表，每项 {name, path, size, isImage, artifactId?}。任何类型条目都可携带文件或图。",
        ] = None,
        meta: Annotated[Optional[dict], "其他附加 meta JSON"] = None,
    ) -> str:
        """新建一条知识库（个人知识库）条目，归属当前用户。返回新条目 id。

        ⚠️ 仅用于用户个人的笔记/灵感/待办，不要用来存储标准信息。"""
        try:
            entry_id = f"kb_{uuid.uuid4().hex[:16]}"
            metadata = _new_entry_metadata(
                title=title,
                summary=summary,
                content=content or "",
                tags=_coerce_str_list(tags),
                user_id=user_id,
                visibility="private",
                allowed_role_codes=[],
                sources=[{"sedimentedAt": _now_ms(), "via": "agent_tool"}],
                group_tag=None,
                entry_type=entry_type,
                meta=meta,
                due_at=due_at,
                todo_status=todo_status,
                primary_artifact_id=primary_artifact_id,
                svg_artifact_id=svg_artifact_id,
                idea_status=idea_status,
                attachments=attachments,
            )
            await asyncio.to_thread(
                kb_upsert,
                entry_id,
                _build_document(metadata.get("title", ""), metadata.get("summary"), metadata.get("content", "")),
                metadata,
            )
            return _ok({"id": entry_id, "title": title, "entry_type": metadata.get("entry_type")})
        except ValueError as e:
            return _err(f"参数不合法：{e}")
        except Exception as e:  # noqa: BLE001
            return _err(str(e))

    @tool
    async def kb_update(
        entry_id: Annotated[str, "条目 id"],
        title: Annotated[Optional[str], "新标题"] = None,
        content: Annotated[Optional[str], "新正文（替换）"] = None,
        summary: Annotated[Optional[str], "新摘要"] = None,
        tags: Annotated[
            Any,
            "新标签 list[str]（**整体替换**而非增量）。也可以单独为「补标签」调用本工具——"
            "sediment 时若发现这条老条目也属于本次新发现的主题轴（如 #客户管理系统），"
            "应把这个标签补进它的 tags 里、其他字段不动，让相关条目能在标签视图聚到一起。"
            "原则同 kb_create：目录维度优先、kb_search 验证沿用、近义归一。",
        ] = None,
        archive: Annotated[Optional[bool], "True 归档 False 取消归档"] = None,
        append_paragraph: Annotated[
            Optional[str],
            "若不为空，把这段文本追加到 content 末尾（用于补充而非替换）",
        ] = None,
        entry_type: Annotated[
            Optional[str],
            "改类型：knowledge / idea / todo / file / diagram",
        ] = None,
        due_at: Annotated[Optional[int], "todo 截止时间毫秒戳"] = None,
        todo_status: Annotated[Optional[str], "pending / done / overdue"] = None,
        primary_artifact_id: Annotated[Optional[int], "file/diagram 的主体 artifact id"] = None,
        svg_artifact_id: Annotated[Optional[int], "diagram 配对 svg artifact id"] = None,
        idea_status: Annotated[
            Optional[str],
            "idea 状态：active(未消化) / digested(已消化：实现或想通了不做)。把灵感标记为已处理就传 digested",
        ] = None,
        meta_patch: Annotated[
            Optional[dict],
            "合并到 meta_json 的字段（不会清空既有 meta，仅更新传入的 key）",
        ] = None,
    ) -> str:
        """更新一条知识库（个人知识库）条目。仅创建者可改。"""
        from app.api.v1.ai.kb import _enforce_type_invariants, _normalize_entry_type, _normalize_idea_status, _normalize_todo_status, _coerce_int

        raw = await asyncio.to_thread(kb_get, entry_id)
        if not raw:
            return _err("条目不存在")
        meta = dict(raw.get("metadata") or {})
        if meta.get("user_id") != user_id:
            return _err("无权操作：仅创建者可改")
        if title is not None:
            meta["title"] = title[:200]
        if summary is not None:
            meta["summary"] = summary[:400]
        if content is not None:
            meta["content"] = content
        if append_paragraph:
            meta["content"] = (meta.get("content", "") + "\n\n" + append_paragraph).strip()
        if tags is not None:
            normalized = _coerce_str_list(tags)
            meta["tags_flat"] = _build_tags_flat(normalized)
        if archive is not None:
            meta["is_archived"] = 1 if archive else 0
        if entry_type is not None:
            meta["entry_type"] = _normalize_entry_type(entry_type)
        if due_at is not None:
            meta["due_at"] = _coerce_int(due_at) or 0
        if todo_status is not None:
            meta["todo_status"] = _normalize_todo_status(todo_status)
        if primary_artifact_id is not None:
            meta["primary_artifact_id"] = _coerce_int(primary_artifact_id) or 0
        if svg_artifact_id is not None:
            meta["svg_artifact_id"] = _coerce_int(svg_artifact_id) or 0
        if idea_status is not None:
            normalized = _normalize_idea_status(idea_status)
            meta["idea_status"] = normalized
            if normalized == "digested" and not meta.get("digested_at"):
                meta["digested_at"] = _now_ms()
        if meta_patch is not None:
            try:
                existing_meta = json.loads(meta.get("meta_json") or "{}")
                if not isinstance(existing_meta, dict):
                    existing_meta = {}
            except Exception:
                existing_meta = {}
            existing_meta.update(meta_patch)
            meta["meta_json"] = json.dumps(existing_meta, ensure_ascii=False)
        meta["updated_at"] = _now_ms()

        # 按当前 entry_type 校验 + 清洗
        et = _normalize_entry_type(meta.get("entry_type"))
        fields = {
            "title": meta.get("title", ""),
            "summary": meta.get("summary") or "",
            "content": meta.get("content") or "",
            "due_at": meta.get("due_at"),
            "todo_status": meta.get("todo_status"),
            "done_at": meta.get("done_at"),
            "primary_artifact_id": meta.get("primary_artifact_id"),
            "svg_artifact_id": meta.get("svg_artifact_id"),
            "idea_status": meta.get("idea_status"),
            "digested_at": meta.get("digested_at"),
        }
        try:
            _enforce_type_invariants(entry_type=et, fields=fields)
        except ValueError as e:
            return _err(f"参数不合法：{e}")
        meta["title"] = fields["title"]
        meta["summary"] = fields["summary"]
        meta["content"] = fields["content"]
        meta["due_at"] = fields.get("due_at") or 0
        meta["todo_status"] = fields.get("todo_status") or ""
        meta["done_at"] = fields.get("done_at") or 0
        meta["primary_artifact_id"] = fields.get("primary_artifact_id") or 0
        meta["svg_artifact_id"] = fields.get("svg_artifact_id") or 0
        meta["idea_status"] = fields.get("idea_status") or ""
        meta["digested_at"] = fields.get("digested_at") or 0

        await asyncio.to_thread(
            kb_upsert,
            entry_id,
            _build_document(meta.get("title", ""), meta.get("summary"), meta.get("content", "")),
            meta,
        )
        return _ok({"id": entry_id, "title": meta.get("title", "")})

    @tool
    async def kb_delete(entry_id: Annotated[str, "条目 id"]) -> str:
        """删除一条知识库（个人知识库）条目。仅创建者可删，不可恢复（建议优先用 archive）。"""
        raw = await asyncio.to_thread(kb_get, entry_id)
        if not raw:
            return _err("条目不存在")
        meta = raw.get("metadata") or {}
        if meta.get("user_id") != user_id:
            return _err("无权操作：仅创建者可删")
        await asyncio.to_thread(_kb_delete_impl, entry_id)
        return _ok({"id": entry_id, "deleted": True})

    @tool
    async def kb_merge(
        source_ids: Annotated[list[str], "要合并的条目 id 列表，至少 2 个"],
        target_title: Annotated[str, "合并后的新标题"],
        target_content: Annotated[str, "合并后的新正文"],
        target_summary: Annotated[Optional[str], "合并后的摘要"] = None,
        target_tags: Annotated[Optional[list[str]], "合并后的标签"] = None,
    ) -> str:
        """把多条知识库（个人知识库）条目合并成一条新条目，并删除原条目。仅当全部源条目都属于当前用户时才允许。"""
        if not source_ids or len(source_ids) < 2:
            return _err("至少需要 2 条源 id")
        sources_collected: list[dict] = []
        for sid in source_ids:
            raw = await asyncio.to_thread(kb_get, sid)
            if not raw:
                return _err(f"条目不存在：{sid}")
            meta = raw.get("metadata") or {}
            if meta.get("user_id") != user_id:
                return _err(f"无权操作：{sid}")
            try:
                sources_collected.extend(json.loads(meta.get("sources_json") or "[]"))
            except Exception:
                pass
        new_id = f"kb_{uuid.uuid4().hex[:16]}"
        metadata = _new_entry_metadata(
            title=target_title,
            summary=target_summary,
            content=target_content,
            tags=list(target_tags or []),
            user_id=user_id,
            visibility="private",
            allowed_role_codes=[],
            sources=sources_collected,
            group_tag=None,
        )
        await asyncio.to_thread(
            kb_upsert, new_id, _build_document(target_title, target_summary, target_content), metadata
        )
        for sid in source_ids:
            await asyncio.to_thread(_kb_delete_impl, sid)
        return _ok({"id": new_id, "title": target_title, "merged_from": source_ids})

    @tool
    async def kb_split(
        source_id: Annotated[str, "要拆分的源条目 id"],
        parts: Annotated[
            list[dict],
            "拆出的子条目数组，每项 {title, content, summary?, tags?}",
        ],
        delete_source: Annotated[bool, "拆完后是否删除源条目"] = True,
    ) -> str:
        """把一条知识库（个人知识库）条目拆成多条，子条目共享 group_tag，方便后续从任一条回溯。仅创建者可操作。"""
        if not parts or len(parts) < 2:
            return _err("至少需要 2 个拆分子项")
        src = await asyncio.to_thread(kb_get, source_id)
        if not src:
            return _err("源条目不存在")
        meta = src.get("metadata") or {}
        if meta.get("user_id") != user_id:
            return _err("无权操作")
        try:
            src_sources = json.loads(meta.get("sources_json") or "[]")
        except Exception:
            src_sources = []
        group = meta.get("group_tag") or f"split_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        created = []
        for p in parts:
            title = p.get("title")
            content = p.get("content")
            if not title or not content:
                return _err("每个 part 必须有 title 和 content")
            new_id = f"kb_{uuid.uuid4().hex[:16]}"
            md = _new_entry_metadata(
                title=title,
                summary=p.get("summary"),
                content=content,
                tags=list(p.get("tags") or []),
                user_id=user_id,
                visibility="private",
                allowed_role_codes=[],
                sources=src_sources,
                group_tag=group,
            )
            await asyncio.to_thread(
                kb_upsert, new_id, _build_document(title, p.get("summary"), content), md
            )
            created.append({"id": new_id, "title": title})
        if delete_source:
            await asyncio.to_thread(_kb_delete_impl, source_id)
        return _ok({"created": created, "group_tag": group})

    # with_lenient_args：兼容模型把 source_ids/target_tags/meta 等数组/对象参数
    # 传成 JSON 字符串（如 "[1,2]"、"a,b"），校验前自动还原
    from app.langchain.tools._tool_args import with_lenient_args

    return [with_lenient_args(t) for t in [
        kb_search,
        kb_create,
        kb_update,
        kb_delete,
        kb_merge,
        kb_split,
    ]]
