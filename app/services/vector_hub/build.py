"""
向量库构建/重建执行器（调度同步 / 启动自动构建 / 管理端 API 共用）。

- SQL 来源（system_sync / table_sync）：sync_sql_source 三档增量（diff 下推）。
- manual / file 来源：无自动来源可拉——重建 = 把库内既有条目的 content
  重新嵌入一遍（换块重建场景；条目本身是用户写入/上传的，内容不变）。
- 库粒度占用表防并发重复构建（调用方拿到 RuntimeError 即已有构建在跑）。

状态机：构建开始置 status=building；ingest 内部收尾负责刷计数/快照（正常完成）
或只刷计数（中途取消，快照不动 → 库保持陈旧判定）；异常置 status=error + last_error。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiomysql
from loguru import logger

from app.models.standard import VecLibrary
from app.services.mysql_pool import standard_pool
from app.services.vector_hub.adapters import make_adapter_for_library
from app.services.vector_hub.ingest import VecCandidate, ingest_candidates, sync_sql_source
from app.services.vector_hub.state import is_lib_stale, physical_table

# 进程内占用表：{library_id}（构建可能跨小时级，进程重启自然释放）
_building: set = set()

# 进程内构建进度注册表（管理端进度查询用；与 _building 同生命周期）。
# 值：{"processed": int, "total": Optional[int], "phase": str, "startedAt": str}
# total 仅 Python diff 路（手动/文件来源）可知；SQL 下推路的工作集要流完才知道
# 总量，不做全表 COUNT（超大源表上等于把 diff 重跑一遍），total=None 前端走
# 不定长进度条 + 实时处理数。
_build_progress: Dict[int, Dict[str, Any]] = {}


def is_building(library_id: int) -> bool:
    return library_id in _building


def get_progress(library_id: int) -> Optional[Dict[str, Any]]:
    """当前构建进度副本（None = 该库无构建在跑）。"""
    p = _build_progress.get(library_id)
    return dict(p) if p is not None else None


async def _read_items_as_candidates(library_id: int) -> List[VecCandidate]:
    """把库内既有条目读回候选流（换块重建：重嵌内容，不改内容本身）。"""
    import json

    table = physical_table()
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                f"SELECT item_key, content, payload, ref_key FROM {table} WHERE library_id=%s",
                [library_id],
            )
            rows = list(await cur.fetchall())
    out: List[VecCandidate] = []
    for r in rows:
        p = r["payload"]
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:
                p = None
        out.append(VecCandidate(item_key=r["item_key"], content=r["content"], payload=p, ref_key=r["ref_key"]))
    return out


async def build_library(
    lib: VecLibrary,
    *,
    wipe_first: bool = False,
    cancel_event: Optional[asyncio.Event] = None,
) -> Dict[str, int]:
    """构建/重建一个向量库，返回摄入计数（含 completed 标记）。

    wipe_first=True 用于换块重建（先清空再全量重嵌，见 ingest 模块注释）。
    已有同库构建在跑时抛 RuntimeError（业务层转 4000，不用鉴权码）。
    """
    if lib.id in _building:
        raise RuntimeError(f"向量库 {lib.library_key} 正在构建中，请勿重复触发")
    if lib.is_deleted:
        raise RuntimeError(f"向量库 {lib.library_key} 已删除")

    # 陈旧库守卫：切过 embed 块的库必须显式重建（wipe）。三档增量只对内容变化行
    # 重嵌，陈旧行保留旧空间向量——直接增量会让新旧向量空间混杂，收尾还会把快照
    # 刷成激活块，劣化被静默掩盖（红线：重建是全量重嵌的费用事件，必须显式触发）。
    # 用 is_lib_stale（纯快照比较）而非 derive_lib_state：status=error 的陈旧库
    # 展示态是 error，但快照依然陈旧，增量同步同样必须拒绝。
    # 管理端 API 已对陈旧库强制 wipe_first=True，正常页面操作到不了这里；
    # 这里拦住调度器 / agent 工具等不带 wipe 的调用方。
    if not wipe_first and is_lib_stale(lib):
        raise RuntimeError(
            f"向量库 {lib.library_key} 已陈旧（构建快照 {lib.embed_block} ≠ 当前激活向量模型）："
            "增量同步不会重嵌未变条目，请显式触发「重建」全量重嵌后再同步"
        )

    # manual/file 空库没有可构建的内容，直接幂等返回（不置 building 态）
    is_sql_source = lib.source_type in ("system_sync", "table_sync")
    candidates: List[VecCandidate] = []
    if not is_sql_source:
        candidates = await _read_items_as_candidates(lib.id)
        if not candidates:
            logger.info(f"[vector_hub.build] 库 {lib.library_key} 无条目，跳过构建")
            return {"added": 0, "reembedded": 0, "payload_only": 0, "deleted": 0, "failed": 0, "unchanged": 0, "completed": 1}

    _building.add(lib.id)
    logger.info(
        f"[vector_hub.build] 库 {lib.library_key} 开始构建"
        f"（来源 {lib.source_type}, wipe={wipe_first}）"
    )
    _build_progress[lib.id] = {
        "processed": 0,
        "total": None,
        "phase": "preparing",
        "startedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        await VecLibrary.filter(id=lib.id).update(status="building", last_error=None)
        progress = _build_progress[lib.id]
        if is_sql_source:
            adapter = make_adapter_for_library(lib)
            counts = await sync_sql_source(
                lib.id, adapter, wipe_first=wipe_first, cancel_event=cancel_event, progress=progress
            )
        else:
            counts = await ingest_candidates(
                lib.id,
                candidates,
                delete_missing=False,
                wipe_first=wipe_first,
                cancel_event=cancel_event,
                progress=progress,
            )
        logger.info(f"[vector_hub.build] 库 {lib.library_key} 构建完成: {counts}")
        return counts
    except Exception as e:
        logger.exception(f"[vector_hub.build] 库 {lib.library_key} 构建失败: {e}")
        try:
            await VecLibrary.filter(id=lib.id).update(status="error", last_error=str(e)[:2000])
        except Exception:
            logger.exception("[vector_hub.build] 写 error 状态失败")
        raise
    finally:
        _building.discard(lib.id)
        _build_progress.pop(lib.id, None)


async def recover_orphaned_building() -> int:
    """启动自愈：回收进程重启遗留的僵尸 building 行。

    构建状态是进程内注册表（_building / _build_progress），容器重启后清空；
    但 DB 里 status='building' 的行还在——它们对应的构建任务已被上一个进程
    带走，永远不会再推进：前端卡片永远转圈、重建按钮被占用表逻辑之外的
    「正在构建」状态误导。统一置为 error + 可读原因（管理端横幅展示），
    由用户显式重建或等下一次定时同步。幂等，多实例同时执行也安全。
    """
    n = await VecLibrary.filter(status="building", is_deleted=0).update(
        status="error",
        last_error="上次构建被进程重启中断（构建状态仅在进程内存活，重启后不会自动续跑）。请重新触发构建，或等待下一次定时自动同步",
    )
    if n:
        logger.warning(f"[vector_hub.build] 启动自愈：{n} 个库的残留 building 状态已置为 error")
    return n


__all__ = ["is_building", "build_library", "get_progress", "recover_orphaned_building"]
