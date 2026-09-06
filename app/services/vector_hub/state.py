"""
vector_hub 公共状态：激活 embed 块解析、陈旧派生、物理表间接层。

陈旧语义（全体系唯一口径）：库上的 embed_block/embed_dim 是**构建时快照**，
读出时与激活块比较，不等即陈旧（派生，不靠状态位）。陈旧库拒绝查询
（StaleLibraryError，无逃生门）；切块只置陈旧、绝不自动重建。
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# 统一条目物理表。physical_table() 间接层：本期一律返回 vec_item；
# 若日后某个库（如章节库）的量压垮单表，可按库拆物理表而上层零改动。
_VEC_ITEM_TABLE = "vec_item"
_VEC_ITEM_FAILED_TABLE = "vec_item_failed"

_vec_item_dim_cache: Optional[int] = None


class StaleLibraryError(Exception):
    """库的构建快照与激活 embed 块不一致 → 向量空间已失效，拒绝查询（无逃生门）。

    切块只置陈旧、绝不自动重嵌（防费用事件）；恢复必须显式重建。
    """

    def __init__(self, library_keys: List[str], lib_snapshot: str, active_block: str, active_dim: Optional[int]):
        self.library_keys = library_keys
        self.lib_snapshot = lib_snapshot
        self.active_block = active_block
        self.active_dim = active_dim
        super().__init__(
            f"向量库 {library_keys} 已陈旧：构建快照 {lib_snapshot}，"
            f"当前激活 embed 块 {active_block}（维度 {active_dim}）。请先重建向量库"
        )


def physical_table(library=None) -> str:
    """条目物理表（间接层，见模块注释）。"""
    return _VEC_ITEM_TABLE


def failed_table() -> str:
    """嵌入失败重试表。"""
    return _VEC_ITEM_FAILED_TABLE


def format_vector_literal(vec) -> str:
    """OceanBase 接受 '[v1,v2,...]' 字符串作为 VECTOR 字面量。"""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


def get_active_embed_info() -> Tuple[str, Optional[int]]:
    """返回 (激活 embed 块名, 维度)。

    块名走 _active_blocks（启动时从 DB 加载，未加载用默认块）；
    维度先读物化 env {块名}_DIMENSION，回退 load_role("EMBED").dimension。
    """
    import os

    from app.langchain.config import get_active_block

    block = get_active_block("embed", "EMBED_DASHSCOPE")
    dim: Optional[int] = None
    v = os.getenv(f"{block}_DIMENSION")
    if v:
        try:
            dim = int(v)
        except ValueError:
            dim = None
    if dim is None:
        try:
            from app.langchain.config import load_role

            dim = load_role("EMBED").dimension
        except Exception:
            dim = None
    return block, dim


def is_lib_stale(lib, active_block: Optional[str] = None, active_dim: Optional[int] = None) -> bool:
    """快照陈旧判定（纯快照比较，独立于 status 列运行态）。

    陈旧 = 构建快照（embed_block/embed_dim）与激活块不一致；快照为空（从未构建）不陈旧。
    与 derive_lib_state 的分工：后者是展示状态（building/error 优先短路），
    本函数是构建侧的硬判定——构建失败的陈旧库依然是陈旧的，增量同步同样必须
    拒绝（否则新旧向量空间混杂），重建同样必须强制 wipe。
    """
    if active_block is None:
        active_block, active_dim = get_active_embed_info()
    return lib.embed_dim is not None and (lib.embed_block != active_block or lib.embed_dim != active_dim)


def derive_lib_state(lib, active_block: Optional[str] = None, active_dim: Optional[int] = None) -> str:
    """派生库的展示状态：building / error / stale / ready / empty。

    陈旧 = 构建快照（embed_block/embed_dim）与激活块不一致，纯派生无状态位；
    快照为空（从未构建）不算陈旧，算 empty。
    status 列只存运行态（empty=空闲 / building / error），ready 由 item_count>0 派生。
    注意：status=error 的陈旧库展示为 error（错误优先），构建侧请用 is_lib_stale。
    """
    if lib.status == "building":
        return "building"
    if lib.status == "error":
        return "error"
    if is_lib_stale(lib, active_block, active_dim):
        return "stale"
    if (lib.item_count or 0) > 0:
        return "ready"
    return "empty"


async def get_vec_item_dim(force_refresh: bool = False) -> Optional[int]:
    """探测 vec_item 表实际 VECTOR 维度（None = 表不存在）。

    modify_db 的维度漂移自愈只在启动期发生、且早于本函数首次调用，进程内缓存安全。
    """
    global _vec_item_dim_cache
    if _vec_item_dim_cache is not None and not force_refresh:
        return _vec_item_dim_cache
    from app.services.mysql_pool import standard_pool

    row = None
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SHOW CREATE TABLE vec_item")
                row = await cur.fetchone()
    except Exception:
        return None
    ddl = ""
    if row:
        ddl = row[1] if isinstance(row, (tuple, list)) else (row.get("Create Table") or "")
    m = re.search(r"`embedding`\s+VECTOR\((\d+)\)", ddl or "", re.IGNORECASE)
    if m:
        _vec_item_dim_cache = int(m.group(1))
    return _vec_item_dim_cache


async def ensure_buildable_dims() -> None:
    """构建/摄入前置校验：激活 embed 维度必须与 vec_item 物理表维度一致。

    超管运行期切到不同维度块时，vec_item 列维度不会跟着变（切块只置陈旧），
    此时写入会塞进错误维度的向量 → 直接拒绝。维度漂移自愈只发生在重启的
    modify_db（RENAME 旧表 + 重建），因此报错文案引导重启。
    """
    _, active_dim = get_active_embed_info()
    table_dim = await get_vec_item_dim()
    if table_dim is None:
        raise RuntimeError("vec_item 表不存在（启动期自动创建，请重启服务）")
    if active_dim is None:
        raise RuntimeError("无法解析激活 embed 块的维度（检查 embed 块配置是否完整）")
    if table_dim != active_dim:
        raise RuntimeError(
            f"激活 embed 块维度 {active_dim} 与 vec_item 物理表维度 {table_dim} 不一致："
            "需重启服务触发维度漂移自愈后再重建向量库"
        )


__all__ = [
    "StaleLibraryError",
    "physical_table",
    "failed_table",
    "format_vector_literal",
    "get_active_embed_info",
    "is_lib_stale",
    "derive_lib_state",
    "get_vec_item_dim",
    "ensure_buildable_dims",
]
