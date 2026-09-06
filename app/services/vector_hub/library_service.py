"""
向量库注册表 CRUD + 权限规则 + 系统库种子。

权限规则（2026-08-27 起统一口径）：向量库管理面整体仅管理员可达
（API 路由级 _require_admin 门控；agent 工具两桥仅 is_admin 挂载）。
面内不再区分系统库 / 用户库、不再区分超管 / 普通管理员：
**所有库全体管理员同权**——可读 / 可写 / 可重建。
唯一例外：**系统库（scope=system）不可删除**（can_delete 恒拒，
API 层 4000 信封、前端不渲染删除按钮）。
QA 语义搜索三件（消费 standard_meta / standard_chapter）不走本层，仍全员可用。

系统库历史兼容：早期版本系统库可删（软删），启动种子发现历史软删行
会自动恢复为空库（后续构建 / 每日同步重新灌入）——现行规则下
系统库已不可删，该分支只是兜底历史数据。

PermissionDeniedError 保留（业务拒绝由 API/MCP 层转 4032 / {"ok":false}，
绝不用鉴权错误码），现行规则下不会再抛出。
"""

from __future__ import annotations

import secrets
from typing import Optional

from loguru import logger

from app.models.standard import VecLibrary

# 系统库种子（幂等）：标准级 + 章节级 + 术语级三层语义检索，同步源见 adapters.py
SYSTEM_LIBRARIES = [
    {
        "library_key": "standard_meta",
        "name": "标准元数据语义库",
        "description": "标准级语义索引（标准名称 + 适用范围），源 standard_base_info，自动增量同步",
        "source_type": "system_sync",
    },
    {
        "library_key": "standard_chapter",
        "name": "标准章节语义库",
        "description": "章节级语义索引（章节标题 + 正文摘录），源 standard_jgh_pdf_chapter，自动增量同步",
        "source_type": "system_sync",
    },
    {
        "library_key": "standard_term",
        "name": "标准术语语义库",
        "description": "术语级语义索引（术语名称 + 英文 + 释义），源 standard_jgh_pdf_term，自动增量同步",
        "source_type": "system_sync",
    },
]


class PermissionDeniedError(Exception):
    """越权操作（对外转 4032 / {"ok":false}，绝不用鉴权错误码）。"""


async def ensure_system_libraries() -> dict:
    """幂等种子系统库（现三个：meta / chapter / term），返回 {library_key: id}。

    历史版本系统库可删（软删），启动时在此自动恢复为空库
    （条目已随删除物理清除，靠后续构建 / 每日同步重新灌入）；
    现行规则系统库已不可删，该分支仅兜底历史数据。
    """
    ids = {}
    for seed in SYSTEM_LIBRARIES:
        lib = await VecLibrary.filter(library_key=seed["library_key"]).first()
        if lib is None:
            lib = await VecLibrary.create(
                library_key=seed["library_key"],
                name=seed["name"],
                description=seed["description"],
                scope="system",
                source_type=seed["source_type"],
            )
            logger.info(f"[vector_hub] 种子系统向量库 {lib.library_key}")
        elif lib.is_deleted:
            lib.is_deleted = 0
            lib.status = "empty"
            lib.item_count = 0
            lib.embed_block = None
            lib.embed_dim = None
            lib.last_error = None
            await lib.save(
                update_fields=["is_deleted", "status", "item_count", "embed_block", "embed_dim", "last_error", "update_time"]
            )
            logger.info(f"[vector_hub] 恢复被删除的系统向量库 {lib.library_key}（空库，待重建）")
        ids[lib.library_key] = lib.id
    return ids


async def get_library(library_key: str) -> Optional[VecLibrary]:
    """按 key 取未删除的库（不存在返回 None）。"""
    return await VecLibrary.filter(library_key=library_key, is_deleted=0).first()


async def get_library_by_id(library_id: int) -> Optional[VecLibrary]:
    """按 id 取未删除的库（后台任务持有 id 用；不存在返回 None）。"""
    return await VecLibrary.filter(id=library_id, is_deleted=0).first()


def can_read(lib, uid, is_super: bool) -> bool:
    """恒真：管理面仅管理员可达（调用方门控），面内所有库全体管理员可读。

    签名保留 uid / is_super 以兼容既有调用点；规则见模块 docstring。
    """
    return True


def can_write(lib, uid, is_super: bool) -> bool:
    """恒真：管理面仅管理员可达（调用方门控），面内所有库全体管理员可写/重建。"""
    return True


def can_delete(lib) -> bool:
    """系统库（scope=system）不可删除；用户库全体管理员同权可删。"""
    return lib.scope != "system"


def assert_can_read(lib, uid, is_super: bool) -> None:
    if not can_read(lib, uid, is_super):
        raise PermissionDeniedError(f"无权访问向量库 {lib.library_key}")


def assert_can_write(lib, uid, is_super: bool) -> None:
    if not can_write(lib, uid, is_super):
        raise PermissionDeniedError(f"无权操作向量库 {lib.library_key}")


async def create_user_library(
    *,
    uid: int,
    name: str,
    description: Optional[str] = None,
    source_type: str = "manual",
    source_config: Optional[dict] = None,
) -> VecLibrary:
    """建用户库（管理面全体管理员可建，含 table_sync 来源）。"""
    if source_type not in ("manual", "file", "table_sync"):
        raise ValueError(f"未知的内容来源类型：{source_type}")
    key = f"user_{uid}_{secrets.token_hex(4)}"
    return await VecLibrary.create(
        library_key=key,
        name=name[:128],
        description=(description or "")[:512] or None,
        scope="user",
        user_id=uid,
        source_type=source_type,
        source_config=source_config,
        status="empty",
        item_count=0,
    )


async def delete_library(lib: VecLibrary) -> None:
    """软删注册行 + 物理删除条目 / 失败记录。系统库拒绝删除。"""
    from tortoise import connections

    from app.services.vector_hub.state import failed_table, physical_table

    if not can_delete(lib):
        raise ValueError(f"系统向量库 {lib.library_key} 不可删除")

    lib.is_deleted = 1
    await lib.save(update_fields=["is_deleted", "update_time"])
    conn = connections.get("conn_standard")
    await conn.execute_query(f"DELETE FROM {physical_table()} WHERE library_id=%s", [lib.id])
    await conn.execute_query(f"DELETE FROM {failed_table()} WHERE library_id=%s", [lib.id])
    logger.info(f"[vector_hub] 删除向量库 {lib.library_key}（id={lib.id}）及其条目")


__all__ = [
    "SYSTEM_LIBRARIES",
    "PermissionDeniedError",
    "ensure_system_libraries",
    "get_library",
    "get_library_by_id",
    "can_read",
    "can_write",
    "can_delete",
    "assert_can_read",
    "assert_can_write",
    "create_user_library",
    "delete_library",
]
