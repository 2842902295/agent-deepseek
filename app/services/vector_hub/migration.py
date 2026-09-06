"""
存量数据一次性迁移：旧 standard_vec_* 三表 → vec_library/vec_item（纯 SQL 复制
embedding，零重嵌）。由启动钩子 bootstrap() fire-and-forget 调用（在旧表
自动构建之前）。

守卫（三者同真才迁移）：
  ① 激活块维度 == 旧表实际维度；② 旧表非空；③ vec_item 中系统库条目为空。
否则跳过（维度不匹配 → 弃旧向量走重建路线，旧表原地改名 _deprecated）。

content 公式逐字符仿写 adapters.py 模板（单一真相源）；迁移额外套
LEFT(...,3000/3200) 截断与适配器一致，避免首轮增量把存量误判「内容变化」重嵌。

回滚点：
- 步骤 3-6 失败 → DELETE vec_item 两系统库行（旧表未动，回到迁移前）；
- 步骤 7 之后 → 旧表改名改回 + TRUNCATE vec_item + 库状态重置。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional

import aiomysql
from loguru import logger

from app.services.mysql_pool import standard_pool
from app.services.vector_hub.library_service import ensure_system_libraries
from app.services.vector_hub.state import get_active_embed_info, physical_table

_CHAPTER_BATCH = 50000  # 章节迁移按 chapter_id 区间分批，避免单事务过大


# ── 工具函数 ────────────────────────────────────────────────────────────────


async def _table_exists(cur, name: str) -> bool:
    await cur.execute("SHOW TABLES LIKE %s", [name])
    return await cur.fetchone() is not None


async def _probe_dim(cur, table: str) -> Optional[int]:
    """表 embedding 列的 VECTOR 维度（不存在返回 None）。"""
    try:
        await cur.execute(f"SHOW CREATE TABLE {table}")
        row = await cur.fetchone()
    except Exception:
        return None
    if not row:
        return None
    ddl = row.get("Create Table") if isinstance(row, dict) else row[1]
    ddl = ddl or ""
    m = re.search(r"`embedding`\s+VECTOR\((\d+)\)", ddl, re.IGNORECASE)
    return int(m.group(1)) if m else None


async def _count(cur, sql: str, params: Optional[list] = None) -> int:
    await cur.execute(sql, params or [])
    row = await cur.fetchone()
    return int(list(row.values())[0]) if row else 0


def _find_old_tables(tables: List[str]) -> Optional[str]:
    """从 SHOW TABLES 结果里找旧向量表后缀（排除 deprecated/bak 残留）。"""
    for t in tables:
        if t.startswith("standard_vec_meta_") and "deprecated" not in t and "bak" not in t:
            return t.removeprefix("standard_vec_meta")
    return None


async def _deprecated_name(cur, base: str) -> str:
    """改名目标：*_deprecated_{yyyymmdd}（冲突则追加时分秒）。"""
    d = datetime.now().strftime("%Y%m%d")
    name = f"{base}_deprecated_{d}"
    if await _table_exists(cur, name):
        name = f"{base}_deprecated_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    return name


async def _rename_deprecated(cur, tables: List[str]) -> List[str]:
    """批量改名保留（一条 RENAME 语句原子执行）。"""
    pairs = []
    for t in tables:
        if await _table_exists(cur, t):
            pairs.append((t, await _deprecated_name(cur, t)))
    if not pairs:
        return []
    sql = "RENAME TABLE " + ", ".join(f"{a} TO {b}" for a, b in pairs)
    await cur.execute(sql)
    return [b for _, b in pairs]


# ── 迁移主流程 ──────────────────────────────────────────────────────────────


async def run_migration_if_needed() -> str:
    """幂等一次性迁移。返回结果字符串（供日志 / 测试断言）。"""
    table = physical_table()
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 0) 定位旧表（后缀含旧 EMBED_PROVIDER+维度，如 _web_2048；
            #    激活块可能已与建表时不同，故直接扫描而非按当前配置推导）
            await cur.execute("SHOW TABLES LIKE 'standard_vec_meta_%'")
            sfx = _find_old_tables([list(r.values())[0] for r in await cur.fetchall()])
            if sfx is None:
                return "skipped:no-old-tables"

            meta_t = f"standard_vec_meta{sfx}"
            chapter_t = f"standard_vec_chapter{sfx}"
            failed_t = f"standard_vec_chapter_failed{sfx}"

            # 1) 守卫：旧表非空？
            meta_cnt = await _count(cur, f"SELECT COUNT(*) AS c FROM {meta_t}")
            chapter_cnt = await _count(cur, f"SELECT COUNT(*) AS c FROM {chapter_t}")
            if meta_cnt == 0 and chapter_cnt == 0:
                return "skipped:old-empty"

            # 2) 守卫：维度一致？（不一致 → 弃旧向量走重建，旧表改名保留）
            active_block, active_dim = get_active_embed_info()
            old_dim = await _probe_dim(cur, meta_t)
            if active_dim is None or old_dim is None or active_dim != old_dim:
                renamed = await _rename_deprecated(cur, [meta_t, chapter_t, failed_t])
                logger.warning(
                    f"[vector_hub.migrate] 维度不匹配（激活={active_dim} 旧表={old_dim}），"
                    f"弃旧向量走重建路线，旧表改名: {renamed}"
                )
                return "skipped:dim-mismatch(deprecated)"

            # 3) 种子系统库（拿 library_id；vec_library 已由 generate_schemas 建好）
            lib_ids = await ensure_system_libraries()
            meta_lib = lib_ids["standard_meta"]
            chapter_lib = lib_ids["standard_chapter"]

            # 4) 中断自愈：旧表还在（未改名）但系统库已有条目 → 上次迁移中断残留，清掉重来
            existed = await _count(
                cur,
                f"SELECT COUNT(*) AS c FROM {table} WHERE library_id IN (%s, %s)",
                [meta_lib, chapter_lib],
            )
            if existed > 0:
                logger.warning(
                    f"[vector_hub.migrate] 检测到上次中断的迁移残留 {existed} 行，清理后重新迁移"
                )
                await cur.execute(
                    f"DELETE FROM {table} WHERE library_id IN (%s, %s)", [meta_lib, chapter_lib]
                )

            try:
                # 5) meta：单条 INSERT..SELECT 复制 embedding
                await cur.execute(
                    f"INSERT INTO {table} (library_id, item_key, content, content_hash, payload, ref_key, embedding) "
                    f"SELECT %s, standard_no, "
                    f"LEFT(CONCAT('标准名称：',IFNULL(cname,''),'\\n适用范围：',IFNULL(use_range,'')),3000), "
                    f"MD5(LEFT(CONCAT('标准名称：',IFNULL(cname,''),'\\n适用范围：',IFNULL(use_range,'')),3000)), "
                    f"JSON_OBJECT('standard_no', standard_no, 'cname', cname, 'use_range', use_range), "
                    f"standard_no, embedding FROM {meta_t}",
                    [meta_lib],
                )
                # 6) chapter：按 chapter_id 区间分批
                await cur.execute(f"SELECT MIN(chapter_id) AS lo, MAX(chapter_id) AS hi FROM {chapter_t}")
                rng = await cur.fetchone()
                lo = int(rng["lo"] or 0)
                hi = int(rng["hi"] or 0)
                start = lo
                while start <= hi:
                    end = start + _CHAPTER_BATCH
                    await cur.execute(
                        f"INSERT INTO {table} (library_id, item_key, content, content_hash, payload, ref_key, embedding) "
                        f"SELECT %s, CONCAT(standard_no, '#', chapter_id), "
                        f"LEFT(CONCAT('【',IFNULL(title_no,''),'】',IFNULL(title,''),'\\n',IFNULL(word_excerpt,'')),3200), "
                        f"MD5(LEFT(CONCAT('【',IFNULL(title_no,''),'】',IFNULL(title,''),'\\n',IFNULL(word_excerpt,'')),3200)), "
                        f"JSON_OBJECT('standard_no', standard_no, 'chapter_id', chapter_id, "
                        f"'title_no', title_no, 'title', title), "
                        f"standard_no, embedding FROM {chapter_t} "
                        f"WHERE chapter_id >= %s AND chapter_id < %s",
                        [chapter_lib, start, end],
                    )
                    logger.info(f"[vector_hub.migrate] chapter 批次 [{start},{end}) 完成")
                    start = end
                # 7) failed 表可重试行 → vec_item_failed
                if await _table_exists(cur, failed_t):
                    from app.services.vector_hub.state import failed_table

                    await cur.execute(
                        f"INSERT IGNORE INTO {failed_table()} (library_id, item_key, error, retry_count) "
                        f"SELECT %s, CONCAT(IFNULL(standard_no,''), '#', chapter_id), error, retry_count "
                        f"FROM {failed_t} WHERE retry_count < 3",
                        [chapter_lib],
                    )
                # 8) 核对：新旧行数精确相等
                new_meta = await _count(cur, f"SELECT COUNT(*) AS c FROM {table} WHERE library_id=%s", [meta_lib])
                new_chapter = await _count(cur, f"SELECT COUNT(*) AS c FROM {table} WHERE library_id=%s", [chapter_lib])
                if new_meta != meta_cnt or new_chapter != chapter_cnt:
                    raise RuntimeError(
                        f"行数核对失败：meta {new_meta}/{meta_cnt}，chapter {new_chapter}/{chapter_cnt}"
                    )
            except Exception as e:
                # 回滚：清掉本次复制的行（旧表未动，系统回到迁移前）
                logger.error(f"[vector_hub.migrate] 迁移失败回滚: {e}")
                try:
                    await cur.execute(
                        f"DELETE FROM {table} WHERE library_id IN (%s, %s)", [meta_lib, chapter_lib]
                    )
                except Exception:
                    logger.exception("[vector_hub.migrate] 回滚删除失败（需人工检查）")
                return f"error:{e}"

            # 9) 旧表改名保留（一条 RENAME 原子）
            renamed = await _rename_deprecated(cur, [meta_t, chapter_t, failed_t])

            # 10) 写构建快照（= 当前激活块；派生态 ready）
            from app.models.standard import VecLibrary

            await VecLibrary.filter(id=meta_lib).update(
                embed_block=active_block, embed_dim=active_dim, item_count=new_meta,
                status="empty", last_built_at=datetime.now(), last_error=None,
            )
            await VecLibrary.filter(id=chapter_lib).update(
                embed_block=active_block, embed_dim=active_dim, item_count=new_chapter,
                status="empty", last_built_at=datetime.now(), last_error=None,
            )
            logger.info(
                f"[vector_hub.migrate] 迁移完成：meta {new_meta} 行，chapter {new_chapter} 行；"
                f"旧表改名 {renamed}"
            )
            return "migrated"


async def bootstrap() -> str:
    """启动钩子（fire-and-forget，在旧表自动构建之前）：种子系统库 + 一次性迁移。"""
    await ensure_system_libraries()
    try:
        return await run_migration_if_needed()
    except Exception as e:
        logger.exception(f"[vector_hub.migrate] 迁移异常（不影响启动）: {e}")
        return f"error:{e}"


__all__ = ["run_migration_if_needed", "bootstrap"]
