"""
个人知识库 API（kb_entries collection 后端为 seekdb）

数据落 seekdb；可见性参照 agent_skill 的三档（private/role/public），由 metadata 字段承载。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from app.api.v1.ai.agent_skill import get_current_role_codes
from app.schemas.base import Fail, Success, SuccessExtra
from app.services.seekdb import (
    kb_delete as _kb_delete_sync,
    kb_get as _kb_get_sync,
    kb_hybrid_search as _kb_hybrid_search_sync,
    kb_list as _kb_list_sync,
    kb_upsert as _kb_upsert_sync,
)

router = APIRouter(prefix="/agent/kb", tags=["个人知识库"])


# ── seekdb 同步函数 → async 包装（不阻塞事件循环）─────────────────────────────
#
# seekdb 底层是 pymysql 同步调用，直接在 async 端点里调会卡死 uvicorn 事件循环，
# 导致整个 worker 不响应。所有 seekdb 操作必须经 asyncio.to_thread 扔到线程池。


async def kb_get(entry_id: str):
    return await asyncio.to_thread(_kb_get_sync, entry_id)


async def kb_list(*, limit: int = 100, offset: int = 0, where=None):
    return await asyncio.to_thread(_kb_list_sync, limit=limit, offset=offset, where=where)


async def kb_upsert(entry_id: str, document: str, metadata: dict):
    return await asyncio.to_thread(_kb_upsert_sync, entry_id, document, metadata)


async def kb_delete(entry_id: str):
    return await asyncio.to_thread(_kb_delete_sync, entry_id)


async def kb_hybrid_search(query_text: str, n_results: int = 5, where=None):
    return await asyncio.to_thread(_kb_hybrid_search_sync, query_text, n_results, where)


# ── helpers ──────────────────────────────────────────────────────────────────


def _now_ms() -> int:
    return int(time.time() * 1000)


def _build_document(title: str, summary: Optional[str], content: str) -> str:
    """seekdb 检索用文本：标题 + 摘要 + 正文。"""
    parts: list[str] = [title]
    if summary:
        parts.append(summary)
    if content:
        parts.append(content)
    return "\n\n".join(parts)


_VALID_ENTRY_TYPES = {"knowledge", "idea", "todo"}
_TODO_STATUSES = {"pending", "done", "overdue"}
_IDEA_STATUSES = {"active", "digested"}


def _normalize_entry_type(value: Optional[str]) -> str:
    v = (value or "knowledge").strip().lower()
    return v if v in _VALID_ENTRY_TYPES else "knowledge"


def _normalize_todo_status(value: Optional[str]) -> str:
    v = (value or "pending").strip().lower()
    return v if v in _TODO_STATUSES else "pending"


def _normalize_idea_status(value: Optional[str]) -> str:
    """灵感状态：active（默认，未处理）/ digested（已消化：要么实现了、要么细想后觉得没用）。"""
    v = (value or "active").strip().lower()
    return v if v in _IDEA_STATUSES else "active"


def _coerce_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _enforce_type_invariants(*, entry_type: str, fields: dict) -> None:
    """
    按 entry_type 强制清洗 fields。原地修改：
      - knowledge/idea：必须有 content
      - todo：必须有 due_at（毫秒），todo_status 默认 pending
    校验失败抛 ValueError。
    """
    if entry_type == "knowledge":
        if not (fields.get("content") or "").strip():
            raise ValueError("knowledge 必须有正文 content")
    elif entry_type == "idea":
        if not (fields.get("content") or "").strip():
            raise ValueError("idea 必须有正文 content（一两句话）")
        fields["idea_status"] = _normalize_idea_status(fields.get("idea_status"))
        fields["digested_at"] = _coerce_int(fields.get("digested_at"), default=0) or 0
    elif entry_type == "todo":
        if _coerce_int(fields.get("due_at")) is None:
            raise ValueError("todo 必须有 due_at（毫秒时间戳）")
        fields["due_at"] = _coerce_int(fields.get("due_at"))
        fields["todo_status"] = _normalize_todo_status(fields.get("todo_status"))
        fields["done_at"] = _coerce_int(fields.get("done_at"), default=0) or 0


def _dump_meta_json(meta: Optional[dict]) -> str:
    return json.dumps(meta or {}, ensure_ascii=False)


def _normalize_tag(t: Any) -> str:
    """单个标签的归一化形态：去 # / 空白 / 转小写。空串返回 ""。

    归一化是有损的——大小写差异会被抹掉（`Vue3` 和 `vue3` 视为同一）。
    这是 feature 而不是 bug：标签是分类轴，大小写差异属 noise。
    """
    if not isinstance(t, str):
        return ""
    return t.strip().lstrip("#").strip().lower()


def _build_tags_flat(tags: Any) -> str:
    """把 tags list 展平成存储/查询专用字符串：` | a | b | c | ` 形态。

    前后留分隔符是为了 `like '% | tagX | %'` 能精确匹配整段（避免 `vue` 误中 `vue3`）。
    seekdb / mysql 的 where 都不便对 JSON 内做安全的 contains，展平字段当索引/过滤入口
    最直接。**这是标签的唯一存储入口**——前端拿到的 `tags: string[]` 由 _parse_tags_flat 反向解出。
    """
    items: list[str] = []
    seen: set[str] = set()
    for raw in (tags or []):
        n = _normalize_tag(raw)
        if not n or n in seen:
            continue
        seen.add(n)
        items.append(n)
    if not items:
        return ""
    return " | " + " | ".join(items) + " | "


def _parse_tags_flat(flat: Any) -> list[str]:
    """把 ` | a | b | c | ` 形态切回 list；空 / 非 str 返回 []。"""
    if not isinstance(flat, str) or not flat.strip():
        return []
    return [seg.strip() for seg in flat.split("|") if seg.strip()]


def _read_tags(meta: dict) -> list[str]:
    """从 metadata 里读 tags 列表（前端展示 / 序列化用）。

    读取入口统一走这里：
    - 优先 tags_flat（已归一化）
    - 老条目 tags_flat 为空时 fallback 到 tags_json，但仍走 _build_tags_flat 做归一化
      （所以拿到的永远是归一化形态）。任何写入路径都会把 tags_flat 落上去，老条目
      被改一次后 tags_json 就再也不被读了，自然 deprecate。
    """
    flat = meta.get("tags_flat")
    if isinstance(flat, str) and flat:
        return _parse_tags_flat(flat)
    legacy_json = meta.get("tags_json")
    if isinstance(legacy_json, str) and legacy_json:
        try:
            raw = json.loads(legacy_json)
            return _parse_tags_flat(_build_tags_flat(raw))
        except Exception:
            return []
    return []


def _new_entry_metadata(
    *,
    title: str,
    summary: Optional[str],
    content: str,
    tags: list[str],
    user_id: Optional[int],
    visibility: str,
    allowed_role_codes: list[str],
    sources: list[dict],
    group_tag: Optional[str],
    entry_type: Optional[str] = None,
    meta: Optional[dict] = None,
    parent_id: Optional[str] = None,
    # ── 类型专属顶层字段（由 _enforce_type_invariants 校验/清洗）──────────
    due_at: Optional[int] = None,
    todo_status: Optional[str] = None,
    done_at: Optional[int] = None,
    primary_artifact_id: Optional[int] = None,
    svg_artifact_id: Optional[int] = None,
    idea_status: Optional[str] = None,
    digested_at: Optional[int] = None,
    # ── 附件（任何类型都可携带）──────────────────────────────────────
    attachments: Optional[list[dict]] = None,
) -> dict:
    now = _now_ms()
    et = _normalize_entry_type(entry_type)
    fields: dict = {
        "title": title,
        "summary": summary or "",
        "content": content or "",
        "due_at": due_at,
        "todo_status": todo_status,
        "done_at": done_at,
        "primary_artifact_id": primary_artifact_id,
        "svg_artifact_id": svg_artifact_id,
        "idea_status": idea_status,
        "digested_at": digested_at,
    }
    _enforce_type_invariants(entry_type=et, fields=fields)
    return {
        "title": fields["title"],
        "summary": fields["summary"],
        "content": fields["content"],
        "tags_flat": _build_tags_flat(tags),
        "user_id": user_id if user_id is not None else 0,
        "visibility": visibility,
        "allowed_role_codes_json": json.dumps(allowed_role_codes or [], ensure_ascii=False),
        "sources_json": json.dumps(sources or [], ensure_ascii=False),
        "group_tag": group_tag or "",
        "is_archived": 0,
        "hit_count": 0,
        "last_hit_at": 0,
        "created_at": now,
        "updated_at": now,
        # ── 知识库扩展字段 ───────────────────────────────────────────
        "entry_type": et,
        "meta_json": _dump_meta_json(meta),
        "parent_id": parent_id or "",
        "pinned_at": 0,
        "dismissed_until": 0,
        "last_feed_rank": -1,
        "last_feed_reason": "",
        # ── 类型专属顶层字段（None 不写）─────────────────────────────
        "due_at": fields.get("due_at") or 0,
        "todo_status": fields.get("todo_status") or "",
        "done_at": fields.get("done_at") or 0,
        "primary_artifact_id": fields.get("primary_artifact_id") or 0,
        "svg_artifact_id": fields.get("svg_artifact_id") or 0,
        "idea_status": fields.get("idea_status") or "",
        "digested_at": fields.get("digested_at") or 0,
        # ── 附件 ─────────────────────────────────────────────────
        "attachments_json": json.dumps(attachments or [], ensure_ascii=False),
    }


def _artifact_to_dict(a) -> dict:
    """AgentArtifact ORM → 前端可用 dict（含 downloadUrl）。a 为 None 时返回 None。"""
    if a is None:
        return None  # type: ignore[return-value]
    return {
        "id": a.id,
        "artifactType": a.artifact_type,
        "name": a.name,
        "description": a.description or "",
        "path": a.path or "",
        "size": a.size,
        "downloadUrl": f"/ai/agent/artifacts/{a.id}/download" if a.path else None,
    }


async def _resolve_artifact_dict(artifact_id: Optional[int]) -> Optional[dict]:
    if not artifact_id:
        return None
    from app.models.standard.agent import AgentArtifact

    a = await AgentArtifact.get_or_none(id=artifact_id)
    return _artifact_to_dict(a) if a else None


def _entry_to_dict(entry_id: str, metadata: dict, document: Optional[str] = None) -> dict:
    """从 seekdb 取回的 metadata 反序列化为前端可用结构。"""
    if metadata is None:
        return {"id": entry_id}
    tags = _read_tags(metadata)
    try:
        allowed = json.loads(metadata.get("allowed_role_codes_json") or "[]")
    except Exception:
        allowed = []
    try:
        sources = json.loads(metadata.get("sources_json") or "[]")
    except Exception:
        sources = []
    try:
        meta_extra = json.loads(metadata.get("meta_json") or "{}")
        if not isinstance(meta_extra, dict):
            meta_extra = {}
    except Exception:
        meta_extra = {}
    try:
        _raw_att = metadata.get("attachments_json")
        attachments_list = json.loads(_raw_att) if isinstance(_raw_att, str) and _raw_att else (_raw_att if isinstance(_raw_att, list) else [])
        if not isinstance(attachments_list, list):
            attachments_list = []
    except Exception:
        attachments_list = []
    return {
        "id": entry_id,
        "title": metadata.get("title", ""),
        "summary": metadata.get("summary", ""),
        "content": metadata.get("content", ""),
        "tags": tags,
        "userId": metadata.get("user_id") or None,
        "visibility": metadata.get("visibility", "private"),
        "allowedRoleCodes": allowed,
        "sources": sources,
        "groupTag": metadata.get("group_tag") or None,
        "isArchived": bool(metadata.get("is_archived", 0)),
        "hitCount": metadata.get("hit_count", 0),
        "lastHitAt": metadata.get("last_hit_at") or None,
        "createdAt": metadata.get("created_at") or None,
        "updatedAt": metadata.get("updated_at") or None,
        # ── 知识库扩展字段 ───────────────────────────────────────────
        "entryType": _normalize_entry_type(metadata.get("entry_type")),
        "meta": meta_extra,
        "parentId": metadata.get("parent_id") or None,
        "dismissedUntil": metadata.get("dismissed_until") or None,
        "lastFeedRank": metadata.get("last_feed_rank") if metadata.get("last_feed_rank", -1) != -1 else None,
        "lastFeedReason": metadata.get("last_feed_reason") or None,
        # ── 类型专属顶层字段 ──────────────────────────────────────
        "dueAt": metadata.get("due_at") or None,
        "todoStatus": metadata.get("todo_status") or None,
        "doneAt": metadata.get("done_at") or None,
        "primaryArtifactId": metadata.get("primary_artifact_id") or None,
        "svgArtifactId": metadata.get("svg_artifact_id") or None,
        "ideaStatus": metadata.get("idea_status") or None,
        "digestedAt": metadata.get("digested_at") or None,
        # ── 附件 ─────────────────────────────────────────────────
        "attachments": attachments_list,
    }


async def _entry_to_dict_with_artifacts(entry_id: str, metadata: dict) -> dict:
    """_entry_to_dict 的异步版本，额外把 primaryArtifact / svgArtifact 整个 dict 拉出来。"""
    base = _entry_to_dict(entry_id, metadata)
    pid = base.get("primaryArtifactId")
    sid = base.get("svgArtifactId")
    if pid:
        base["primaryArtifact"] = await _resolve_artifact_dict(pid)
    if sid:
        base["svgArtifact"] = await _resolve_artifact_dict(sid)
    return base


async def _attach_artifacts_batch(entries: list[dict]) -> None:
    """批量给一批 entry dict 加上 primaryArtifact / svgArtifact / attachments[].artifact 字段，原地修改。"""
    if not entries:
        return
    from app.models.standard.agent import AgentArtifact

    ids: set[int] = set()
    for e in entries:
        if e.get("primaryArtifactId"):
            ids.add(int(e["primaryArtifactId"]))
        if e.get("svgArtifactId"):
            ids.add(int(e["svgArtifactId"]))
        for att in (e.get("attachments") or []):
            if att.get("artifactId"):
                ids.add(int(att["artifactId"]))
    if not ids:
        return
    rows = await AgentArtifact.filter(id__in=list(ids)).all()
    by_id = {r.id: _artifact_to_dict(r) for r in rows}
    for e in entries:
        pid = e.get("primaryArtifactId")
        sid = e.get("svgArtifactId")
        if pid and pid in by_id:
            e["primaryArtifact"] = by_id[pid]
        if sid and sid in by_id:
            e["svgArtifact"] = by_id[sid]
        for att in (e.get("attachments") or []):
            aid = att.get("artifactId")
            if aid and aid in by_id:
                att["artifact"] = by_id[aid]


def _is_visible(meta: dict, uid: Optional[int], role_codes: list[str], is_super: bool) -> bool:
    if is_super:
        return True
    vis = (meta.get("visibility") or "private").lower()
    if vis == "public":
        return True
    if vis == "private":
        return meta.get("user_id") is not None and meta.get("user_id") == uid
    if vis == "role":
        try:
            allowed = json.loads(meta.get("allowed_role_codes_json") or "[]")
        except Exception:
            allowed = []
        return any(rc in role_codes for rc in allowed)
    return False


def _can_manage(meta: dict, uid: Optional[int], is_super: bool) -> bool:
    if is_super:
        return True
    return meta.get("user_id") is not None and meta.get("user_id") == uid


# ── Schemas ──────────────────────────────────────────────────────────────────


class KBCreateReq(BaseModel):
    title: str = Field(..., max_length=200)
    summary: Optional[str] = Field(None, max_length=400)
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    visibility: str = "private"
    allowedRoleCodes: list[str] = Field(default_factory=list)
    groupTag: Optional[str] = None
    sources: list[dict] = Field(default_factory=list)
    entryType: Optional[str] = Field(None, description="knowledge / idea / todo")
    meta: Optional[dict] = Field(None, description="附加 meta JSON（artifacts 列表等）")
    parentId: Optional[str] = None
    # ── 类型专属字段 ───────────────────────────────────────────
    dueAt: Optional[int] = Field(None, description="todo 截止时间毫秒戳")
    todoStatus: Optional[str] = Field(None, description="pending / done / overdue")
    doneAt: Optional[int] = None
    primaryArtifactId: Optional[int] = Field(None, description="主体 artifact id（兼容旧数据）")
    svgArtifactId: Optional[int] = Field(None, description="配对 svg artifact id（兼容旧数据）")
    ideaStatus: Optional[str] = Field(None, description="idea 状态：active / digested")
    digestedAt: Optional[int] = Field(None, description="灵感被消化的毫秒戳")
    # ── 附件（任何类型都可携带）──────────────────────────────────
    attachments: Optional[list[dict]] = Field(None, description="附件列表 [{name, path, size, isImage, artifactId?}]")


class KBUpdateReq(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None
    visibility: Optional[str] = None
    allowedRoleCodes: Optional[list[str]] = None
    groupTag: Optional[str] = None
    isArchived: Optional[bool] = None
    appendSource: Optional[dict] = Field(
        None, description="追加一条来源记录而不替换 sources"
    )
    entryType: Optional[str] = None
    meta: Optional[dict] = None
    parentId: Optional[str] = None
    # ── 类型专属字段 ───────────────────────────────────────────
    dueAt: Optional[int] = None
    todoStatus: Optional[str] = None
    doneAt: Optional[int] = None
    primaryArtifactId: Optional[int] = None
    svgArtifactId: Optional[int] = None
    ideaStatus: Optional[str] = None
    digestedAt: Optional[int] = None
    # ── 附件 ─────────────────────────────────────────────────
    attachments: Optional[list[dict]] = None


class KBMergeReq(BaseModel):
    sourceIds: list[str] = Field(..., min_length=2)
    targetTitle: str
    targetSummary: Optional[str] = None
    targetContent: str
    targetTags: list[str] = Field(default_factory=list)


class KBSplitReq(BaseModel):
    sourceId: str
    parts: list[KBCreateReq] = Field(..., min_length=2)
    deleteSource: bool = True


class KBSedimentReq(BaseModel):
    messageId: int = Field(..., description="要沉淀的 AgentMessage.id（必须是 assistant 消息）")
    source: str = Field("button", description="触发来源：button / instruction")


class KBSedimentSessionReq(BaseModel):
    session_key: str = Field(..., description="要沉淀整段对话的会话 key")


# ── 知识库：万用收件箱 / 卡片操作 / feed 相关 schema ───────────────────────────────


class NianInboxAttachment(BaseModel):
    name: str
    path: str
    size: int
    isImage: bool = False


class NianInboxCommitReq(BaseModel):
    text: str = Field(..., description="万用收件箱用户输入（自由文本）")
    sourceHint: Optional[str] = Field(
        None, description="来源提示，如 qa_message:<id> / freeform"
    )
    attachments: list[NianInboxAttachment] = Field(
        default_factory=list, description="用户上传的附件列表"
    )


class NianDismissReq(BaseModel):
    untilTs: int = Field(..., description="稍后再看截止毫秒时间戳；传 0 撤销")


class NianTrackReq(BaseModel):
    action: str = Field(..., description="opened / double_tap")


# ── 产物查询（供前端按 ID 拉取）──────────────────────────────────────────────


@router.get("/artifacts/lookup", summary="按 ID 批量取产物详情")
async def artifacts_lookup(ids: str = ""):
    """逗号分隔的 artifact id 列表，返回 dict[id, artifact]。"""
    if not ids.strip():
        return Success(data={})
    try:
        id_list = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        return Fail(msg="ids 格式错误，需逗号分隔整数")
    from app.models.standard.agent import AgentArtifact
    rows = await AgentArtifact.filter(id__in=id_list).all()
    return Success(data={r.id: _artifact_to_dict(r) for r in rows})


# ── CRUD ─────────────────────────────────────────────────────────────────────


@router.get("", summary="知识条目列表")
async def list_entries(
    keyword: Optional[str] = None,
    tag: Optional[str] = None,
    include_archived: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    uid, role_codes, is_super = await get_current_role_codes()

    # seekdb where 暂不支持 JSON 内匹配，先全量再 Python 过滤
    rows = await kb_list(limit=10_000, offset=0)
    visible: list[dict] = []
    for r in rows:
        meta = r.get("metadata") or {}
        if not _is_visible(meta, uid, role_codes, is_super):
            continue
        if not include_archived and meta.get("is_archived"):
            continue
        if keyword:
            kw = keyword.lower()
            if (
                kw not in (meta.get("title") or "").lower()
                and kw not in (meta.get("summary") or "").lower()
                and kw not in (meta.get("content") or "").lower()
            ):
                continue
        if tag:
            tags = _read_tags(meta)
            if _normalize_tag(tag) not in tags:
                continue
        visible.append(_entry_to_dict(r["id"], meta))

    visible.sort(key=lambda x: x.get("updatedAt") or 0, reverse=True)
    total = len(visible)
    page = visible[offset : offset + limit]
    await _attach_artifacts_batch(page)
    return SuccessExtra(data={"items": page}, total=total)


@router.get("/tags-stats", summary="标签统计（服务端聚合）")
async def tags_stats(
    entry_type: Optional[str] = None,
    limit: int = 30,
):
    """返回当前用户可见条目的标签及出现次数，按频次降序。"""
    uid, role_codes, is_super = await get_current_role_codes()
    rows = await kb_list(limit=10_000, offset=0)
    counter: dict[str, int] = {}
    for r in rows:
        meta = r.get("metadata") or {}
        if not _is_visible(meta, uid, role_codes, is_super):
            continue
        if meta.get("is_archived"):
            continue
        if entry_type and _normalize_entry_type(meta.get("entry_type")) != entry_type:
            continue
        for t in _read_tags(meta):
            counter[t] = counter.get(t, 0) + 1
    result = sorted(counter.items(), key=lambda x: x[1], reverse=True)[:limit]
    return Success(data=[{"tag": t, "count": c} for t, c in result])


# ── 标签聚类缓存 ──────────────────────────────────────────────────────────────
import asyncio as _asyncio

_tag_cluster_cache: dict[str, tuple[float, list]] = {}  # key → (timestamp, result)
_TAG_CACHE_TTL = 300  # 5 分钟


def _collect_tag_counts(entry_type: Optional[str], uid: Optional[int], role_codes: list[str], is_super: bool) -> dict[str, int]:
    """从 seekdb 收集当前用户可见条目的标签计数（同步，供 asyncio.to_thread 包装）。"""
    rows = _kb_list_sync(limit=10_000, offset=0)
    counter: dict[str, int] = {}
    for r in rows:
        meta = r.get("metadata") or {}
        if not _is_visible(meta, uid, role_codes, is_super):
            continue
        if meta.get("is_archived"):
            continue
        if entry_type and _normalize_entry_type(meta.get("entry_type")) != entry_type:
            continue
        for t in _read_tags(meta):
            counter[t] = counter.get(t, 0) + 1
    return counter


@router.get("/tags-clustered", summary="标签语义聚类（向量化）")
async def tags_clustered(
    entry_type: Optional[str] = None,
    threshold: float = 0.85,
    limit: int = 100,
):
    """对当前用户可见条目的所有标签做 embedding 余弦聚类，返回聚类组。

    每组含 canonical（最高频标签）、count（组内总次数）、members（组内所有标签）。
    结果缓存 5 分钟。
    """
    import time as _time

    uid, role_codes, is_super = await get_current_role_codes()
    cache_key = f"{uid}:{entry_type or 'all'}:{threshold}"

    # 缓存命中
    cached = _tag_cluster_cache.get(cache_key)
    if cached and (_time.time() - cached[0]) < _TAG_CACHE_TTL:
        return Success(data=cached[1][:limit])

    # 收集标签
    counter = await _asyncio.to_thread(_collect_tag_counts, entry_type, uid, role_codes, is_super)
    if not counter:
        return Success(data=[])

    tags = list(counter.keys())

    # 标签太少不值得聚类，直接返回
    if len(tags) <= 3:
        result = [
            {"canonical": t, "count": counter[t], "members": [t], "memberCounts": {t: counter[t]}, "size": 1}
            for t in sorted(counter, key=counter.get, reverse=True)
        ]
        _tag_cluster_cache[cache_key] = (_time.time(), result)
        return Success(data=result[:limit])

    # Embedding
    try:
        from app.langchain.embedding_providers import get_embedding
        import numpy as np

        embedder = get_embedding()
        vectors = await embedder.embed_texts(tags)
        mat = np.array(vectors, dtype=np.float32)

        # 余弦相似度矩阵 → 贪心聚类
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        normed = mat / norms
        sim = normed @ normed.T

        assigned: dict[int, int] = {}  # tag_idx → cluster_center_idx
        for i in range(len(tags)):
            if i in assigned:
                continue
            assigned[i] = i
            for j in range(i + 1, len(tags)):
                if j in assigned:
                    continue
                if sim[i][j] >= threshold:
                    assigned[j] = i

        # 构建聚类结果
        clusters: dict[int, list[int]] = {}
        for tag_idx, center_idx in assigned.items():
            clusters.setdefault(center_idx, []).append(tag_idx)

        result = []
        for center_idx, members in clusters.items():
            member_details = [{"tag": tags[m], "count": counter[tags[m]]} for m in members]
            member_details.sort(key=lambda x: x["count"], reverse=True)
            canonical = member_details[0]["tag"]
            total_count = sum(m["count"] for m in member_details)
            result.append({
                "canonical": canonical,
                "count": total_count,
                "members": [m["tag"] for m in member_details],
                "memberCounts": {m["tag"]: m["count"] for m in member_details},
                "size": len(members),
            })

        result.sort(key=lambda x: x["count"], reverse=True)

    except Exception as e:
        # embedding 失败时降级为纯统计
        logger.warning(f"[tags-clustered] embedding 失败，降级返回: {e}")
        result = [
            {"canonical": t, "count": counter[t], "members": [t], "memberCounts": {t: counter[t]}, "size": 1}
            for t in sorted(counter, key=counter.get, reverse=True)
        ]

    _tag_cluster_cache[cache_key] = (_time.time(), result)
    return Success(data=result[:limit])


@router.get("/{entry_id}", summary="知识条目详情")
async def get_entry(entry_id: str):
    uid, role_codes, is_super = await get_current_role_codes()
    raw = await kb_get(entry_id)
    if not raw:
        return Fail(msg="条目不存在")
    meta = raw.get("metadata") or {}
    if not _is_visible(meta, uid, role_codes, is_super):
        return Fail(msg="无权查看")
    return Success(data=await _entry_to_dict_with_artifacts(entry_id, meta))


@router.post("", summary="新建知识条目（手动）")
async def create_entry(req: KBCreateReq):
    uid, _role_codes, _is_super = await get_current_role_codes()
    entry_id = f"kb_{uuid.uuid4().hex[:16]}"
    try:
        metadata = _new_entry_metadata(
            title=req.title,
            summary=req.summary,
            content=req.content,
            tags=req.tags,
            user_id=uid,
            visibility=req.visibility,
            allowed_role_codes=req.allowedRoleCodes,
            sources=req.sources,
            group_tag=req.groupTag,
            entry_type=req.entryType,
            meta=req.meta,
            parent_id=req.parentId,
            due_at=req.dueAt,
            todo_status=req.todoStatus,
            done_at=req.doneAt,
            primary_artifact_id=req.primaryArtifactId,
            svg_artifact_id=req.svgArtifactId,
            idea_status=req.ideaStatus,
            digested_at=req.digestedAt,
            attachments=req.attachments,
        )
    except ValueError as e:
        return Fail(msg=str(e))
    document = _build_document(metadata.get("title", ""), metadata.get("summary"), metadata.get("content", ""))
    await kb_upsert(entry_id, document, metadata)
    return Success(data=await _entry_to_dict_with_artifacts(entry_id, metadata))


@router.patch("/{entry_id}", summary="更新知识条目")
async def update_entry(entry_id: str, req: KBUpdateReq):
    raw = await kb_get(entry_id)
    if not raw:
        return Fail(msg="条目不存在")
    meta = dict(raw.get("metadata") or {})
    uid, _role_codes, is_super = await get_current_role_codes()
    if not _can_manage(meta, uid, is_super):
        return Fail(msg="无权修改：仅创建者或管理员可改")

    if req.title is not None:
        meta["title"] = req.title[:200]
    if req.summary is not None:
        meta["summary"] = req.summary[:400]
    if req.content is not None:
        meta["content"] = req.content
    if req.tags is not None:
        meta["tags_flat"] = _build_tags_flat(req.tags)
    if req.visibility is not None:
        meta["visibility"] = req.visibility
    if req.allowedRoleCodes is not None:
        meta["allowed_role_codes_json"] = json.dumps(req.allowedRoleCodes, ensure_ascii=False)
    if req.groupTag is not None:
        meta["group_tag"] = req.groupTag
    if req.isArchived is not None:
        meta["is_archived"] = 1 if req.isArchived else 0
    if req.appendSource:
        try:
            existing = json.loads(meta.get("sources_json") or "[]")
        except Exception:
            existing = []
        existing.append(req.appendSource)
        meta["sources_json"] = json.dumps(existing, ensure_ascii=False)
    if req.entryType is not None:
        meta["entry_type"] = _normalize_entry_type(req.entryType)
    if req.meta is not None:
        meta["meta_json"] = _dump_meta_json(req.meta)
    if req.parentId is not None:
        meta["parent_id"] = req.parentId or ""
    if req.dueAt is not None:
        meta["due_at"] = _coerce_int(req.dueAt) or 0
    if req.todoStatus is not None:
        meta["todo_status"] = _normalize_todo_status(req.todoStatus)
    if req.doneAt is not None:
        meta["done_at"] = _coerce_int(req.doneAt) or 0
    if req.primaryArtifactId is not None:
        meta["primary_artifact_id"] = _coerce_int(req.primaryArtifactId) or 0
    if req.svgArtifactId is not None:
        meta["svg_artifact_id"] = _coerce_int(req.svgArtifactId) or 0
    if req.ideaStatus is not None:
        meta["idea_status"] = _normalize_idea_status(req.ideaStatus)
    if req.digestedAt is not None:
        meta["digested_at"] = _coerce_int(req.digestedAt) or 0
    if req.attachments is not None:
        meta["attachments_json"] = json.dumps(req.attachments, ensure_ascii=False)
    meta["updated_at"] = _now_ms()

    # 按当前最终 entry_type 强制清洗一遍（todo 必须有 due_at 等）
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
        return Fail(msg=str(e))
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

    document = _build_document(meta.get("title", ""), meta.get("summary"), meta.get("content", ""))
    await kb_upsert(entry_id, document, meta)
    return Success(data=await _entry_to_dict_with_artifacts(entry_id, meta))


@router.delete("/{entry_id}", summary="删除知识条目")
async def delete_entry(entry_id: str):
    raw = await kb_get(entry_id)
    if not raw:
        return Fail(msg="条目不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    if not _can_manage(raw.get("metadata") or {}, uid, is_super):
        return Fail(msg="无权删除：仅创建者或管理员可删")
    await kb_delete(entry_id)
    return Success(data=None, msg="已删除")


@router.get("/search/hybrid", summary="hybrid 检索")
async def search_hybrid(query: str, top_k: int = 5, max_distance: float = 0.5):
    uid, role_codes, is_super = await get_current_role_codes()
    raws = await kb_hybrid_search(query, n_results=top_k)
    items: list[dict] = []
    for r in raws:
        dist = r.get("distance")
        if dist is not None and dist > max_distance:
            continue
        meta = r.get("metadata") or {}
        if not _is_visible(meta, uid, role_codes, is_super):
            continue
        if meta.get("is_archived"):
            continue
        d = _entry_to_dict(r["id"], meta)
        d["distance"] = dist
        items.append(d)
    await _attach_artifacts_batch(items)
    return Success(data=items)


@router.post("/merge", summary="合并多条到一条新条目")
async def merge_entries(req: KBMergeReq):
    uid, _role_codes, is_super = await get_current_role_codes()
    sources_collected: list[dict] = []
    for sid in req.sourceIds:
        raw = await kb_get(sid)
        if not raw:
            return Fail(msg=f"条目不存在：{sid}")
        meta = raw.get("metadata") or {}
        if not _can_manage(meta, uid, is_super):
            return Fail(msg=f"无权操作：{sid}")
        try:
            sources_collected.extend(json.loads(meta.get("sources_json") or "[]"))
        except Exception:
            pass

    new_id = f"kb_{uuid.uuid4().hex[:16]}"
    metadata = _new_entry_metadata(
        title=req.targetTitle,
        summary=req.targetSummary,
        content=req.targetContent,
        tags=req.targetTags,
        user_id=uid,
        visibility="private",
        allowed_role_codes=[],
        sources=sources_collected,
        group_tag=None,
    )
    document = _build_document(req.targetTitle, req.targetSummary, req.targetContent)
    await kb_upsert(new_id, document, metadata)
    for sid in req.sourceIds:
        await kb_delete(sid)
    return Success(data=_entry_to_dict(new_id, metadata))


@router.post("/split", summary="把一条拆成多条")
async def split_entry(req: KBSplitReq):
    src = await kb_get(req.sourceId)
    if not src:
        return Fail(msg="源条目不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    src_meta = src.get("metadata") or {}
    if not _can_manage(src_meta, uid, is_super):
        return Fail(msg="无权操作")

    try:
        src_sources = json.loads(src_meta.get("sources_json") or "[]")
    except Exception:
        src_sources = []
    group = src_meta.get("group_tag") or f"split_{uuid.uuid4().hex[:8]}"

    created: list[dict] = []
    for part in req.parts:
        new_id = f"kb_{uuid.uuid4().hex[:16]}"
        metadata = _new_entry_metadata(
            title=part.title,
            summary=part.summary,
            content=part.content,
            tags=part.tags,
            user_id=uid,
            visibility=part.visibility or "private",
            allowed_role_codes=part.allowedRoleCodes,
            sources=part.sources or src_sources,
            group_tag=part.groupTag or group,
        )
        document = _build_document(part.title, part.summary, part.content)
        await kb_upsert(new_id, document, metadata)
        created.append(_entry_to_dict(new_id, metadata))

    if req.deleteSource:
        await kb_delete(req.sourceId)
    return Success(data=created)


# ── 沉淀流水线 ───────────────────────────────────────────────────────────────


@router.post("/sediment", summary="把一条 assistant 消息沉淀到个人知识库")
async def sediment_entry(req: KBSedimentReq):
    """单条粒度：让 qa_agent 沉淀指定那条 assistant 消息（凭 checkpoint 记忆 + sediment skill）。"""
    from app.api.v1.ai.sediment_runner import normalize_kb_payload, run_sediment
    from app.core.ctx import CTX_USER_ID
    from app.models.standard.agent import AgentMessage, AgentSession

    uid = CTX_USER_ID.get() or None
    if uid is None:
        return Fail(msg="未登录")

    msg = await AgentMessage.get_or_none(id=req.messageId)
    if msg is None:
        return Fail(msg="消息不存在")
    if msg.role != "assistant":
        return Fail(msg="只有 AI 回复可以沉淀")
    session = await AgentSession.get_or_none(id=msg.session_id)
    if session is None:
        return Fail(msg="会话不存在")

    trigger = (
        f"[系统触发] 把消息 id={req.messageId} 这条回答整理进知识库。"
        f"按 sediment skill 走，最后输出 <sediment-report> marker。"
    )
    outcome = await run_sediment(
        trigger_text=trigger, session_key=session.session_key, user_id=uid
    )
    return Success(data=normalize_kb_payload(outcome["report"]))


@router.post("/sediment-session/stream", summary="把整段会话沉淀到个人知识库（SSE）")
async def sediment_session_stream(req: KBSedimentSessionReq):
    """整段会话粒度：让 qa_agent 凭 checkpoint 记忆把这次对话整理进知识库。"""
    from app.api.v1.ai.sediment_runner import normalize_kb_payload, sse, stream_sediment
    from app.core.ctx import CTX_USER_ID

    uid = CTX_USER_ID.get() or None

    async def gen():
        if uid is None:
            yield sse({"type": "error", "message": "未登录"})
            return
        from app.langchain.billing.quota import check_quota
        quota_status = await check_quota(uid)
        if not quota_status.allowed:
            yield sse({
                "type": "quota_exceeded",
                "quota": quota_status.quota,
                "used": quota_status.used,
                "remaining": quota_status.remaining,
                "message": "积分余额不足，请联系管理员充值",
            })
            return
        trigger = (
            "[系统触发] 把刚才的整段对话整理进知识库。"
            "按 sediment skill 走，最后输出 <sediment-report> marker。"
        )
        async for ev in stream_sediment(
            trigger_text=trigger,
            session_key=req.session_key,
            user_id=uid,
            on_done=lambda report: {"result": normalize_kb_payload(report)},
        ):
            yield ev

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/stats/count", summary="获取当前用户已沉淀条数（用于徽章）")
async def kb_count_for_user():
    uid, role_codes, is_super = await get_current_role_codes()
    if uid is None:
        return Success(data={"count": 0})
    rows = await kb_list(limit=10_000, offset=0)
    n = 0
    for r in rows:
        meta = r.get("metadata") or {}
        if meta.get("is_archived"):
            continue
        if not _is_visible(meta, uid, role_codes, is_super):
            continue
        # 徽章按"我创建的"来记，不算别人 public 的
        if meta.get("user_id") == uid:
            n += 1
    return Success(data={"count": n})


# ── 知识库：万用收件箱（手输自由文本）─────────────────────────────────────────────


@router.post("/inbox/commit/stream", summary="万用收件箱：AI 整理后落库（SSE）")
async def inbox_commit_stream(req: NianInboxCommitReq):
    """SSE 版：done 事件 payload 与原 POST 一致 ({result: KBSedimentResult})，前端按事件流消费。

    旧的同步 POST 版本被 `qa_agent` 长链路常常压在 1~2 分钟以上，反向代理 / 浏览器按 60s 超时
    把连接掐断，前端拿到 504/timeout。SSE 让连接保持活跃（heartbeat 每 10s 一次），同时也
    给用户更早的 started 反馈。无 session 上下文——sediment_runner 会起一个匿名 thread。
    """
    from app.api.v1.ai.sediment_runner import normalize_kb_payload, sse, stream_sediment

    uid, _role_codes, _is_super = await get_current_role_codes()
    text = (req.text or "").strip()
    attachments = req.attachments or []

    async def gen():
        if uid is None:
            yield sse({"type": "error", "message": "未登录"})
            return
        if not text and not attachments:
            yield sse({"type": "error", "message": "内容为空"})
            return
        attachment_hint = ""
        if attachments:
            lines = []
            for a in attachments:
                kind = "图片" if a.isImage else "文件"
                lines.append(f"- {a.name} ({a.size} bytes, {kind}, path={a.path})")
            attachment_hint = "\n\n--- 附件 ---\n" + "\n".join(lines)
        trigger = (
            "[系统触发] 把下面这段内容整理进知识库。"
            "按 sediment skill 走，最后输出 <sediment-report> marker。"
            "附件信息附在末尾，如有附件请在创建的条目中通过 attachments 字段关联。\n\n"
            f"--- 用户输入 ---\n{text or '(仅附件)'}{attachment_hint}"
        )
        async for ev in stream_sediment(
            trigger_text=trigger,
            session_key=None,
            user_id=uid,
            on_done=lambda report: {"result": normalize_kb_payload(report)},
        ):
            yield ev

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/inbox/commit", summary="万用收件箱：AI 整理后落库（同步版，已废弃，改用 /inbox/commit/stream）")
async def inbox_commit(req: NianInboxCommitReq):
    """万用收件箱沉淀：让 qa_agent 走 sediment skill 把这段自由文本整理进知识库。

    无 session 上下文——sediment_runner 会起一个匿名 thread，避免污染任何真实会话。
    """
    from app.api.v1.ai.sediment_runner import normalize_kb_payload, run_sediment

    uid, _role_codes, _is_super = await get_current_role_codes()
    if uid is None:
        return Fail(msg="未登录")

    text = (req.text or "").strip()
    attachments = req.attachments or []
    if not text and not attachments:
        return Fail(msg="内容为空")

    attachment_hint = ""
    if attachments:
        lines = []
        for a in attachments:
            kind = "图片" if a.isImage else "文件"
            lines.append(f"- {a.name} ({a.size} bytes, {kind}, path={a.path})")
        attachment_hint = "\n\n--- 附件 ---\n" + "\n".join(lines)
    trigger = (
        "[系统触发] 把下面这段内容整理进知识库。"
        "按 sediment skill 走，最后输出 <sediment-report> marker。\n\n"
        f"--- 用户输入 ---\n{text or '(仅附件)'}{attachment_hint}"
    )
    outcome = await run_sediment(trigger_text=trigger, session_key=None, user_id=uid)
    return Success(data=normalize_kb_payload(outcome["report"]))


# ── 知识库：卡片操作（pin / dismiss / track）─────────────────────────────────


async def _persist_meta(entry_id: str, meta: dict) -> None:
    await kb_upsert(
        entry_id,
        _build_document(meta.get("title", ""), meta.get("summary"), meta.get("content", "")),
        meta,
    )


@router.post("/{entry_id}/dismiss", summary="稍后再看")
async def dismiss_entry(entry_id: str, req: NianDismissReq):
    raw = await kb_get(entry_id)
    if not raw:
        return Fail(msg="条目不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    meta = dict(raw.get("metadata") or {})
    if not _can_manage(meta, uid, is_super):
        return Fail(msg="无权操作")
    meta["dismissed_until"] = max(0, int(req.untilTs or 0))
    meta["updated_at"] = _now_ms()
    await _persist_meta(entry_id, meta)
    return Success(data=_entry_to_dict(entry_id, meta))


class NianIdeaStatusReq(BaseModel):
    status: str = Field(..., description="active / digested")


@router.post("/{entry_id}/idea-status", summary="切换灵感是否已消化（不刷 updated_at，保持原位）")
async def set_idea_status(entry_id: str, req: NianIdeaStatusReq):
    """
    把灵感标记为已消化 / 取消消化。
    刻意不刷 updated_at——状态切换不该把条目顶到列表前面，跟『打开/双击』的 track 一类。
    第一次切到 digested 时自动盖 digested_at；切回 active 不动 digested_at（保留历史）。
    """
    raw = await kb_get(entry_id)
    if not raw:
        return Fail(msg="条目不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    meta = dict(raw.get("metadata") or {})
    if not _can_manage(meta, uid, is_super):
        return Fail(msg="无权操作")
    if (meta.get("entry_type") or "knowledge") != "idea":
        return Fail(msg="仅 idea 类型支持该操作")
    normalized = _normalize_idea_status(req.status)
    meta["idea_status"] = normalized
    if normalized == "digested" and not meta.get("digested_at"):
        meta["digested_at"] = _now_ms()
    # 注意：刻意不刷 updated_at，避免状态切换把卡片顶到列表前
    await _persist_meta(entry_id, meta)
    return Success(data=_entry_to_dict(entry_id, meta))


@router.post("/{entry_id}/track", summary="记录交互痕迹（opened / double_tap）")
async def track_entry(entry_id: str, req: NianTrackReq):
    raw = await kb_get(entry_id)
    if not raw:
        return Fail(msg="条目不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    meta = dict(raw.get("metadata") or {})
    if not _can_manage(meta, uid, is_super):
        return Fail(msg="无权操作")
    action = (req.action or "").strip()
    if action not in {"opened", "double_tap"}:
        return Fail(msg="不支持的 action")

    try:
        meta_extra = json.loads(meta.get("meta_json") or "{}")
        if not isinstance(meta_extra, dict):
            meta_extra = {}
    except Exception:
        meta_extra = {}
    inter = meta_extra.get("interactions") or {}
    if not isinstance(inter, dict):
        inter = {}
    bucket_key = action + "_at"
    bucket = inter.get(bucket_key) or []
    if not isinstance(bucket, list):
        bucket = []
    bucket.append(_now_ms())
    bucket = bucket[-20:]  # 仅保留最近 20 条
    inter[bucket_key] = bucket
    meta_extra["interactions"] = inter
    meta["meta_json"] = _dump_meta_json(meta_extra)
    # 注意：交互痕迹不刷 updated_at，避免每次点开就把卡片顶到列表前
    await _persist_meta(entry_id, meta)
    return Success(data={"ok": True})


# ── 知识库：每日 feed ──────────────────────────────────────────────────────────


@router.get("/feed/today", summary="取当前用户今日 feed（已用 agent 排序）")
async def feed_today():
    """
    返回 {items, brief, generatedAt}：
    - items: 新加入的条目（agent 还没整理过的）置顶 + 已按 agent 排序的部分
    - brief: 晨间简报（agent 写的）
    - generatedAt: 上次 agent 跑的时刻；为 null 时表示从未跑过

    新加入条目（user 当天 / 上次 ranking 后才进来的）带 reason="新加入"，
    永远排在 AI 结果之前。这样新条目无需等夜间整理就能立即可见。
    """
    from datetime import date as _date

    from app.models.standard.nian import NianDailyFeed, NianFeedRunLog

    uid, _role_codes, _is_super = await get_current_role_codes()
    if uid is None:
        return Success(data={"items": [], "brief": "", "generatedAt": None})
    today = _date.today()
    feeds = await NianDailyFeed.filter(user_id=uid, feed_date=today).order_by("rank").all()
    last_run = (
        await NianFeedRunLog.filter(user_id=uid, feed_date=today, status="ok")
        .order_by("-id")
        .first()
    )

    ranked_ids: set[str] = {f.entry_id for f in feeds}

    # 1) ranking 已覆盖的部分
    ranked_items: list[dict] = []
    for f in feeds:
        raw = await kb_get(f.entry_id)
        if not raw:
            continue
        meta = raw.get("metadata") or {}
        if meta.get("is_archived"):
            continue
        d = _entry_to_dict(f.entry_id, meta)
        d["lastFeedRank"] = f.rank
        d["lastFeedReason"] = f.reason
        d["feedConfidence"] = f.confidence
        ranked_items.append(d)

    # 2) "新加入"：用户全部未归档 entry 中、不在 ranking 内、未在 dismiss 期的
    now_ms = _now_ms()
    fresh_items: list[dict] = []
    rows = await kb_list(limit=10_000, offset=0)
    for r in rows:
        meta = r.get("metadata") or {}
        if meta.get("user_id") != uid:
            continue
        if meta.get("is_archived"):
            continue
        if r["id"] in ranked_ids:
            continue
        dismissed = meta.get("dismissed_until") or 0
        if dismissed and dismissed > now_ms:
            continue
        d = _entry_to_dict(r["id"], meta)
        d["lastFeedRank"] = None
        d["lastFeedReason"] = "新加入"
        d["feedConfidence"] = None
        fresh_items.append(d)

    # 新加入按 updated_at desc，最新的最上
    fresh_items.sort(key=lambda x: x.get("updatedAt") or 0, reverse=True)

    items = fresh_items + ranked_items
    await _attach_artifacts_batch(items)
    return Success(
        data={
            "items": items,
            "brief": (last_run.brief if last_run else "") or "",
            "generatedAt": int(last_run.create_time.timestamp() * 1000) if last_run else None,
        }
    )


@router.post("/feed/rerun", summary="手动触发夜间排序 agent（开发期 + 用户『重新整理』按钮）")
async def feed_rerun():
    from datetime import date as _date

    from app.langchain.agents.tasks.kb.feed_ranking_agent import rank_feed_for_user

    uid, _role_codes, _is_super = await get_current_role_codes()
    if uid is None:
        return Fail(msg="未登录")
    try:
        n = await rank_feed_for_user(user_id=uid, feed_date=_date.today())
    except Exception as e:  # noqa: BLE001
        return Fail(msg=f"重排失败：{e}")
    return Success(data={"writtenItems": n})
