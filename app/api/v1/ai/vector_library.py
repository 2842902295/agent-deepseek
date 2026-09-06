"""
向量库管理 API（统一向量库体系的管理端，读写全走 app/services/vector_hub/）

- GET    /ai/vector-lib              库列表（全体管理员可见全部库，含系统库与他人库）
- POST   /ai/vector-lib              建用户库（含 table_sync 来源）
- GET    /ai/vector-lib/{key}        库详情
- DELETE /ai/vector-lib/{key}        删库（软删注册行 + 物理清条目）
- GET    /ai/vector-lib/{key}/items    条目分页（keyword 关键词过滤；全文下发）
- POST   /ai/vector-lib/{key}/items    批量加手动条目（三档增量，不删既有条目）
- DELETE /ai/vector-lib/{key}/items    按 itemKey 批量删条目
- POST   /ai/vector-lib/{key}/build    后台构建/重建（陈旧库强制先清空再全量重嵌）
- GET    /ai/vector-lib/{key}/progress 构建进度查询（进程内注册表，构建期有效）
- POST   /ai/vector-lib/{key}/cancel   取消构建（批间生效，快照不刷 → 库保持陈旧判定）
- GET    /ai/vector-lib/{key}/search   测试检索（走统一 SearchService，验证召回效果）
- POST   /ai/vector-lib/{key}/upload 文件上传摄入（xlsx/csv/txt/md，后台解析）

权限口径（2026-08-27 统一）：**整个管理面仅管理员可访问**（R_SUPER / R_ADMIN，
路由级依赖门控，普通用户一律 4032 信封）；面内不再区分系统库 / 用户库，
所有库全体管理员同权——可读 / 可写 / 可重建；唯一例外：**系统库不可删除**
（DELETE 对 scope=system 返回 4000，见 vector_hub/library_service.py）。
业务错误 4000（绝不用鉴权错误码）。
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiomysql
from fastapi import APIRouter, Depends, File, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from app.api.v1.ai.agent_skill import get_current_role_codes
from app.langchain.model_selection import block_label
from app.schemas.base import Fail, Success
from app.services.mysql_pool import standard_pool
from app.services.vector_hub import build as hub_build
from app.services.vector_hub import library_service as hub_lib
from app.services.vector_hub.adapters import parse_upload_file
from app.services.vector_hub.ingest import VecCandidate, ingest_candidates
from app.services.vector_hub.state import derive_lib_state, failed_table, get_active_embed_info, is_lib_stale, physical_table


async def _require_admin():
    """管理员门控（路由级依赖）：非管理员直接返回 4032 信封，端点不执行。

    Fail 本身即 JSONResponse（HTTP 200 + 信封），依赖返回 Response 会短路端点。
    """
    _, role_codes, _ = await get_current_role_codes()
    if "R_SUPER" not in role_codes and "R_ADMIN" not in role_codes:
        return Fail(code="4032", msg="向量库管理仅管理员可用")


router = APIRouter(prefix="/vector-lib", tags=["AI-向量库管理"], dependencies=[Depends(_require_admin)])

# 单次上传大小上限（超大文件拒绝，防解析阻塞 / 内存爆）
_UPLOAD_MAX_BYTES = 20 * 1024 * 1024
# 单次手动加条目上限（防单请求嵌入过久）
_MANUAL_BATCH_LIMIT = 500
# 上传临时目录（构建完成后清理）
_UPLOAD_TMP = Path(".agent_workspace/_vec_uploads")

# 后台构建取消事件（库粒度；进程重启自然清空，与 build._building 占用表同生命周期）
_build_runs: Dict[int, asyncio.Event] = {}


# ── 序列化 ──────────────────────────────────────────────────────────────────


def _fmt_time(t) -> Optional[str]:
    if not t:
        return None
    return t.strftime("%Y-%m-%d %H:%M:%S") if isinstance(t, datetime) else str(t)


def _source_table(lib) -> Optional[str]:
    """同步源表名（前端卡片展示用）：表来源读 source_config；系统库为固定映射。"""
    if lib.source_type == "table_sync":
        cfg = lib.source_config or {}
        return cfg.get("table") or None
    if lib.source_type == "system_sync":
        return {
            "standard_meta": "standard_base_info",
            "standard_chapter": "standard_jgh_pdf_chapter",
            "standard_term": "standard_jgh_pdf_term",
        }.get(lib.library_key)
    return None


def _lib_view(
    lib,
    active_block: Optional[str],
    active_dim: Optional[int],
    uid: Optional[int],
    is_super: bool,
    *,
    owner_name: Optional[str] = None,
    failed_count: int = 0,
) -> Dict[str, Any]:
    """注册行 → 前端视图（陈旧态派生；写权限供前端控按钮）。

    owner_name：用户库拥有者显示名（超管看全部用户库时标识归属）。
    failed_count：嵌入失败待重试条数（vec_item_failed，retry_count<3）。
    """
    state = derive_lib_state(lib, active_block, active_dim)
    return {
        "libraryKey": lib.library_key,
        "name": lib.name,
        "description": lib.description,
        "scope": lib.scope,
        "sourceType": lib.source_type,
        "sourceTable": _source_table(lib),
        "itemCount": lib.item_count or 0,
        "embedBlock": lib.embed_block,
        "embedBlockLabel": block_label(lib.embed_block),
        "embedDim": lib.embed_dim,
        "state": state,
        # 独立陈旧判定（纯快照比较）：status=error 的库也可能是陈旧的，
        # 展示态被 error 短路时前端靠它决定「重建」按钮文案 / 费用警告
        "isStale": is_lib_stale(lib, active_block, active_dim),
        "lastError": lib.last_error,
        "lastBuiltAt": _fmt_time(lib.last_built_at),
        "isBuilding": hub_build.is_building(lib.id),
        "canWrite": hub_lib.can_write(lib, uid, is_super),
        "ownerName": owner_name,
        "failedCount": failed_count,
        "createTime": _fmt_time(lib.create_time),
    }


async def _owner_names(libs) -> Dict[int, str]:
    """批量取用户库拥有者显示名（{user_id: 显示名}）。"""
    uids = sorted({lib.user_id for lib in libs if lib.scope == "user" and lib.user_id})
    if not uids:
        return {}
    from app.models.system.admin import User

    users = await User.filter(id__in=uids)
    return {u.id: (u.nick_name or u.user_name) for u in users}


async def _failed_counts(lib_ids: List[int]) -> Dict[int, int]:
    """批量取嵌入失败待重试计数（{library_id: n}；retry_count>=3 已归档不计）。"""
    if not lib_ids:
        return {}
    ft = failed_table()
    ph = ",".join(["%s"] * len(lib_ids))
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                f"SELECT library_id, COUNT(*) AS c FROM {ft} WHERE library_id IN ({ph}) AND retry_count < 3 GROUP BY library_id",
                lib_ids,
            )
            rows = list(await cur.fetchall())
    return {int(r["library_id"]): int(r["c"]) for r in rows}


async def _ctx():
    """(uid, is_super)；未登录由全局 DependAnyAuth 拦截，这里兜底。"""
    uid, _, is_super = await get_current_role_codes()
    return uid, is_super


async def _get_lib_or_fail(key: str):
    lib = await hub_lib.get_library(key)
    if lib is None:
        return None, Fail(code="4000", msg=f"向量库不存在：{key}")
    return lib, None


# ── 库列表 / 建库 / 详情 / 删库 ─────────────────────────────────────────────


@router.get("", summary="向量库列表（全体管理员可见全部库）")
async def list_libraries():
    uid, is_super = await _ctx()
    from app.models.standard import VecLibrary

    # 管理面仅管理员可达（路由级门控），面内所有库同权可见
    libs = await VecLibrary.filter(is_deleted=0).order_by("scope", "id")
    active_block, active_dim = get_active_embed_info()
    owners = await _owner_names(libs)
    fails = await _failed_counts([lib.id for lib in libs])
    records = [
        _lib_view(
            lib,
            active_block,
            active_dim,
            uid,
            is_super,
            owner_name=owners.get(lib.user_id),
            failed_count=fails.get(lib.id, 0),
        )
        for lib in libs
    ]
    return Success(
        data={
            "records": records,
            "activeEmbedBlock": active_block,
            "activeEmbedBlockLabel": block_label(active_block),
            "activeEmbedDim": active_dim,
        }
    )


class LibraryCreate(BaseModel):
    """建库入参（驼峰 alias 对齐前端）。"""

    name: str = Field(..., min_length=1, max_length=128, description="库显示名")
    description: Optional[str] = Field(None, max_length=512, description="库用途说明")
    source_type: str = Field("manual", alias="sourceType", description="manual / file / table_sync")
    source_config: Optional[dict] = Field(None, alias="sourceConfig", description="table_sync 的表/列配置")

    class Config:
        populate_by_name = True


@router.post("", summary="新建用户向量库")
async def create_library(payload: LibraryCreate):
    uid, is_super = await _ctx()
    if not uid:
        return Fail(code="4000", msg="无法识别当前用户")
    source_type = (payload.source_type or "manual").strip()
    if source_type not in ("manual", "file", "table_sync"):
        return Fail(code="4000", msg=f"未知的内容来源类型：{source_type}")
    try:
        lib = await hub_lib.create_user_library(
            uid=uid,
            name=payload.name.strip(),
            description=payload.description,
            source_type=source_type,
            source_config=payload.source_config,
        )
    except ValueError as e:
        return Fail(code="4000", msg=str(e))
    active_block, active_dim = get_active_embed_info()
    return Success(msg="已创建", data=_lib_view(lib, active_block, active_dim, uid, is_super))


@router.get("/{key}", summary="向量库详情")
async def get_library(key: str):
    uid, is_super = await _ctx()
    lib, fail = await _get_lib_or_fail(key)
    if fail:
        return fail
    try:
        hub_lib.assert_can_read(lib, uid, is_super)
    except hub_lib.PermissionDeniedError as e:
        return Fail(code="4032", msg=str(e))
    active_block, active_dim = get_active_embed_info()
    owners = await _owner_names([lib])
    fails = await _failed_counts([lib.id])
    return Success(
        data=_lib_view(
            lib, active_block, active_dim, uid, is_super, owner_name=owners.get(lib.user_id), failed_count=fails.get(lib.id, 0)
        )
    )


@router.delete("/{key}", summary="删除向量库")
async def delete_library(key: str):
    uid, is_super = await _ctx()
    lib, fail = await _get_lib_or_fail(key)
    if fail:
        return fail
    try:
        hub_lib.assert_can_write(lib, uid, is_super)
    except hub_lib.PermissionDeniedError as e:
        return Fail(code="4032", msg=str(e))
    if not hub_lib.can_delete(lib):
        return Fail(code="4000", msg="系统向量库不可删除")
    if hub_build.is_building(lib.id):
        return Fail(code="4000", msg="该库正在构建中，请先取消构建再删除")
    await hub_lib.delete_library(lib)
    return Success(msg="已删除")


# ── 条目分页 / 批量增删 ─────────────────────────────────────────────────────


def _parse_payload(p) -> Optional[dict]:
    if isinstance(p, dict):
        return p
    if isinstance(p, str):
        try:
            v = json.loads(p)
            return v if isinstance(v, dict) else None
        except Exception:
            return None
    return None


def _like_escape(s: str) -> str:
    """LIKE 参数转义（防 % / _ / \\ 当通配符）。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/{key}/items", summary="条目分页列表（支持关键词过滤）")
async def list_items(key: str, current: int = 1, size: int = 20, keyword: str = ""):
    uid, is_super = await _ctx()
    lib, fail = await _get_lib_or_fail(key)
    if fail:
        return fail
    try:
        hub_lib.assert_can_read(lib, uid, is_super)
    except hub_lib.PermissionDeniedError as e:
        return Fail(code="4032", msg=str(e))
    current = max(1, current)
    size = max(1, min(100, size))
    where = "WHERE library_id=%s"
    params: List = [lib.id]
    kw = (keyword or "").strip()
    if kw:
        # 条目内容多为长文本无索引可走，库内范围 LIKE 全扫（管理面低频操作，可接受）
        esc = _like_escape(kw)
        where += " AND (content LIKE %s OR item_key LIKE %s)"
        params += [f"%{esc}%", f"%{esc}%"]
    table = physical_table()
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(f"SELECT COUNT(*) AS c FROM {table} {where}", params)
            total = int((await cur.fetchone())["c"])
            await cur.execute(
                f"SELECT item_key, content, payload, ref_key, updated_at "
                f"FROM {table} {where} ORDER BY id DESC LIMIT %s OFFSET %s",
                [*params, size, (current - 1) * size],
            )
            rows = list(await cur.fetchall())
    records = [
        {
            "itemKey": r["item_key"],
            # 全文下发（前端预览截断 + 展开看全文）；单页 ≤100 行 × ≤3200 字符，体量可控
            "content": r["content"],
            "payload": _parse_payload(r["payload"]),
            "refKey": r["ref_key"],
            "updatedAt": _fmt_time(r["updated_at"]),
        }
        for r in rows
    ]
    return Success(data={"records": records, "total": total, "current": current, "size": size})


class ItemCreate(BaseModel):
    """单条手动条目。"""

    item_key: Optional[str] = Field(None, alias="itemKey", max_length=191, description="条目唯一键，缺省自动生成")
    content: str = Field(..., min_length=1, description="被嵌入的文本内容")
    payload: Optional[dict] = Field(None, description="附加结构化数据（不参与嵌入）")
    ref_key: Optional[str] = Field(None, alias="refKey", max_length=128, description="等值/IN 过滤键（可选）")

    class Config:
        populate_by_name = True


class ItemsAddPayload(BaseModel):
    items: List[ItemCreate] = Field(..., min_length=1, description="条目列表")


@router.post("/{key}/items", summary="批量添加手动条目（三档增量）")
async def add_items(key: str, payload: ItemsAddPayload):
    uid, is_super = await _ctx()
    lib, fail = await _get_lib_or_fail(key)
    if fail:
        return fail
    try:
        hub_lib.assert_can_write(lib, uid, is_super)
    except hub_lib.PermissionDeniedError as e:
        return Fail(code="4032", msg=str(e))
    if hub_build.is_building(lib.id):
        return Fail(code="4000", msg="该库正在构建中，请稍候")
    if len(payload.items) > _MANUAL_BATCH_LIMIT:
        return Fail(code="4000", msg=f"单次最多添加 {_MANUAL_BATCH_LIMIT} 条")
    candidates: List[VecCandidate] = []
    for it in payload.items:
        ik = (it.item_key or "").strip() or f"m_{secrets.token_hex(4)}"
        candidates.append(
            VecCandidate(item_key=ik, content=it.content, payload=it.payload, ref_key=(it.ref_key or None))
        )
    try:
        counts = await ingest_candidates(lib.id, candidates, delete_missing=False)
    except RuntimeError as e:  # ensure_buildable_dims（维度漂移等）
        return Fail(code="4000", msg=str(e))
    return Success(msg="已写入", data=counts)


class ItemsDeletePayload(BaseModel):
    item_keys: List[str] = Field(..., alias="itemKeys", min_length=1, description="要删除的条目键")

    class Config:
        populate_by_name = True


@router.delete("/{key}/items", summary="批量删除条目")
async def delete_items(key: str, payload: ItemsDeletePayload):
    uid, is_super = await _ctx()
    lib, fail = await _get_lib_or_fail(key)
    if fail:
        return fail
    try:
        hub_lib.assert_can_write(lib, uid, is_super)
    except hub_lib.PermissionDeniedError as e:
        return Fail(code="4032", msg=str(e))
    if hub_build.is_building(lib.id):
        return Fail(code="4000", msg="该库正在构建中，请稍候")
    keys = [k[:191] for k in payload.item_keys if k]
    if not keys:
        return Fail(code="4000", msg="itemKeys 不能为空")
    table = physical_table()
    deleted = 0
    async with standard_pool.acquire() as conn:
        async with conn.cursor() as cur:
            for i in range(0, len(keys), 500):
                chunk = keys[i : i + 500]
                ph = ",".join(["%s"] * len(chunk))
                await cur.execute(
                    f"DELETE FROM {table} WHERE library_id=%s AND item_key IN ({ph})", [lib.id, *chunk]
                )
                if cur.rowcount and cur.rowcount > 0:
                    deleted += cur.rowcount
            await cur.execute(f"SELECT COUNT(*) FROM {table} WHERE library_id=%s", [lib.id])
            (cnt,) = await cur.fetchone()
    from app.models.standard import VecLibrary

    await VecLibrary.filter(id=lib.id).update(item_count=int(cnt))
    return Success(msg="已删除", data={"deleted": deleted, "itemCount": int(cnt)})


# ── 构建 / 取消 / 上传 ──────────────────────────────────────────────────────


async def _run_build_in_background(library_id: int, wipe_first: bool, cancel_event: asyncio.Event) -> None:
    """后台构建包装：异常只落库状态 + 日志（HTTP 请求早已返回）。"""
    try:
        lib = await hub_lib.get_library_by_id(library_id)
        if lib is None:
            return
        await hub_build.build_library(lib, wipe_first=wipe_first, cancel_event=cancel_event)
    except Exception as e:
        logger.exception(f"[vector-lib] 后台构建失败 library_id={library_id}: {e}")
    finally:
        _build_runs.pop(library_id, None)


@router.post("/{key}/build", summary="构建/重建向量库（后台执行）")
async def build_library_api(key: str, wipe: Optional[bool] = None):
    uid, is_super = await _ctx()
    lib, fail = await _get_lib_or_fail(key)
    if fail:
        return fail
    try:
        hub_lib.assert_can_write(lib, uid, is_super)
    except hub_lib.PermissionDeniedError as e:
        return Fail(code="4032", msg=str(e))
    if hub_build.is_building(lib.id):
        return Fail(code="4000", msg="该库正在构建中，请勿重复触发")

    # 陈旧库必须整库重嵌（旧空间向量只有清空后才会全部重嵌），强制 wipe。
    # 用 is_lib_stale（纯快照比较）：status=error 的陈旧库展示态是 error，
    # derive_lib_state 不会返回 'stale'，但构建语义上依然必须整库重嵌。
    active_block, active_dim = get_active_embed_info()
    stale = is_lib_stale(lib, active_block, active_dim)
    wipe_first = bool(wipe) or stale

    cancel_event = asyncio.Event()
    _build_runs[lib.id] = cancel_event
    asyncio.create_task(_run_build_in_background(lib.id, wipe_first, cancel_event))
    note = "（陈旧库：先清空再全量重嵌）" if stale else ""
    return Success(msg=f"构建任务已启动{note}", data={"wipeFirst": wipe_first})


@router.post("/{key}/cancel", summary="取消构建（批间生效）")
async def cancel_build(key: str):
    uid, is_super = await _ctx()
    lib, fail = await _get_lib_or_fail(key)
    if fail:
        return fail
    try:
        hub_lib.assert_can_write(lib, uid, is_super)
    except hub_lib.PermissionDeniedError as e:
        return Fail(code="4032", msg=str(e))
    ev = _build_runs.get(lib.id)
    if ev is None or not hub_build.is_building(lib.id):
        return Fail(code="4000", msg="该库没有正在进行的构建")
    ev.set()
    return Success(msg="已发送取消信号，将在当前嵌入批完成后停止")


@router.get("/{key}/progress", summary="构建进度查询（构建期有效）")
async def build_progress(key: str):
    uid, is_super = await _ctx()
    lib, fail = await _get_lib_or_fail(key)
    if fail:
        return fail
    try:
        hub_lib.assert_can_read(lib, uid, is_super)
    except hub_lib.PermissionDeniedError as e:
        return Fail(code="4032", msg=str(e))
    # 进度在进程内注册表（构建任务与 API 同进程）；进程重启/构建结束即无
    return Success(data={"building": hub_build.is_building(lib.id), "progress": hub_build.get_progress(lib.id)})


@router.get("/{key}/search", summary="测试检索（验证召回效果）")
async def test_search(key: str, query: str, topK: int = 5):
    """管理面测试检索：建完库/换块重建后快速验证语义召回。

    走统一 SearchService（陈旧库直接拒绝）。命中行附内容预览。
    """
    uid, is_super = await _ctx()
    lib, fail = await _get_lib_or_fail(key)
    if fail:
        return fail
    try:
        hub_lib.assert_can_read(lib, uid, is_super)
    except hub_lib.PermissionDeniedError as e:
        return Fail(code="4032", msg=str(e))
    q = (query or "").strip()
    if not q:
        return Fail(code="4000", msg="请输入检索内容")
    top_k = max(1, min(20, topK))
    from app.services.vector_hub.search import search_service
    from app.services.vector_hub.state import StaleLibraryError

    try:
        hits = await search_service.search([lib.library_key], q, top_k=top_k)
    except StaleLibraryError as e:
        return Fail(code="4000", msg=str(e))
    except KeyError as e:
        return Fail(code="4000", msg=str(e))
    previews: Dict[str, str] = {}
    if hits:
        table = physical_table()
        keys = [h["itemKey"] for h in hits]
        ph = ",".join(["%s"] * len(keys))
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    f"SELECT item_key, LEFT(content, 300) AS preview FROM {table} "
                    f"WHERE library_id=%s AND item_key IN ({ph})",
                    [lib.id, *keys],
                )
                previews = {r["item_key"]: r["preview"] for r in await cur.fetchall()}
    records = [{**h, "contentPreview": previews.get(h["itemKey"])} for h in hits]
    return Success(data={"records": records, "total": len(records)})


@router.post("/{key}/upload", summary="文件上传摄入（后台解析，三档增量）")
async def upload_file(key: str, file: UploadFile = File(...)):
    uid, is_super = await _ctx()
    lib, fail = await _get_lib_or_fail(key)
    if fail:
        return fail
    try:
        hub_lib.assert_can_write(lib, uid, is_super)
    except hub_lib.PermissionDeniedError as e:
        return Fail(code="4032", msg=str(e))
    if hub_build.is_building(lib.id):
        return Fail(code="4000", msg="该库正在构建中，请稍候")
    fname = file.filename or "upload"
    suffix = Path(fname).suffix.lower()
    if suffix not in (".xlsx", ".xlsm", ".csv", ".txt", ".md"):
        return Fail(code="4000", msg=f"不支持的文件类型：{suffix}（支持 xlsx / csv / txt / md）")

    # 落临时文件（异步写，限制总大小）
    _UPLOAD_TMP.mkdir(parents=True, exist_ok=True)
    tmp_path = _UPLOAD_TMP / f"{lib.id}_{secrets.token_hex(6)}{suffix}"
    size = 0
    try:
        with open(tmp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _UPLOAD_MAX_BYTES:
                    tmp_path.unlink(missing_ok=True)
                    return Fail(code="4000", msg="文件超过 20MB 上限，请拆分后上传")
                out.write(chunk)
    finally:
        await file.close()

    # 后台：线程池解析（同步阻塞）→ 三档增量摄入（文件=完整快照，源里消失的行删掉）
    asyncio.create_task(_run_upload_ingest_background(lib.id, str(tmp_path), fname))
    return Success(msg="文件已接收，正在后台解析并摄入", data={"fileName": fname, "size": size})


async def _run_upload_ingest_background(library_id: int, tmp_path: str, fname: str) -> None:
    try:
        lib = await hub_lib.get_library_by_id(library_id)
        if lib is None:
            return
        candidates = await asyncio.to_thread(parse_upload_file, tmp_path, fname)
        if not candidates:
            logger.info(f"[vector-lib] 文件 {fname} 解析结果为空，跳过摄入")
            return
        counts = await ingest_candidates(library_id, candidates, delete_missing=True)
        logger.info(f"[vector-lib] 文件 {fname} 摄入完成: {counts}")
    except Exception as e:
        logger.exception(f"[vector-lib] 文件 {fname} 摄入失败: {e}")
        try:
            from app.models.standard import VecLibrary

            await VecLibrary.filter(id=library_id).update(status="error", last_error=f"文件摄入失败：{str(e)[:500]}")
        except Exception:
            pass
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


__all__ = ["router"]
