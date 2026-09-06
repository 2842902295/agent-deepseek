"""
向量库摄入：候选流 → 三档增量 diff → 批量嵌入 → 写库。

三档增量（content_hash = md5(content)，只哈希被嵌入文本）：
  1. 新 item_key            → 嵌入 + INSERT
  2. content_hash 变化      → 重嵌 + UPDATE
  3. 内容未变但 payload 变化 → 免嵌，仅 UPDATE payload（仅 Python diff 路提供）
  4. item_key 源里消失      → 删行（delete_missing 时）

两条入口：
- ingest_candidates()：Python 端 diff（手动 / 文件来源，用户库量级；
  全库 hash 表读进内存比对，百万行的表来源不要走这里）
- sync_sql_source()：diff 下推 SQL（system_sync / table_sync 来源；
  源端 SQL 直接产出新增/重嵌/删除工作集，不把全库 hash 拉进内存）

重建语义：换块后重建必须 wipe_first=True（先清空再全量嵌入）——
三档 diff 只对「内容变化」重嵌，陈旧行的旧空间向量只有清空后才会全部重嵌。
中途取消时只刷计数不刷快照，库保持陈旧判定（避免新旧向量空间混杂还被当成可用）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional

import aiomysql
from loguru import logger

from app.services.mysql_pool import standard_pool
from app.services.vector_hub.state import (
    ensure_buildable_dims,
    failed_table,
    format_vector_literal,
    get_active_embed_info,
    physical_table,
)

# 心跳日志步长（构建动辄几十分钟，控制台必须有进度，防"看起来像中断了"）：
# Python diff 路每 20 个嵌入批（320 条）一条；SQL 下推路每累计 1000 条一条
EMBED_LOG_EVERY_BATCHES = 20
SQL_PROGRESS_LOG_STEP = 1_000

# 失败熔断阈值：累计失败达到该数量且零成功写入 → 判定写入链路系统性损坏
# （典型：OB 7604 向量索引内存不足 / 数据库不可用），立即中止构建。
# 事故教训（2026-08/09）：章节库 42 万条在写入全失败的情况下"成功"跑完，
# 白烧全量嵌入额度，失败表堆出 130 万行，且每天 03:30 循环重演。
_FAIL_FAST_MIN_FAILED = 50


@dataclass
class VecCandidate:
    """一条待摄入候选（手动 / 文件来源用）。"""

    item_key: str
    content: str
    payload: Optional[dict] = None
    ref_key: Optional[str] = None


def content_hash(content: str) -> str:
    """内容指纹（只哈希被嵌入文本；迁移 SQL 用 MD5() 同式对齐）。"""
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def classify_candidates(existing: Dict[str, dict], candidates: Iterable[VecCandidate], delete_missing: bool = True) -> Dict:
    """三档 diff 分类（纯函数，无 DB 依赖，可独立单测）。

    existing: {item_key: {"hash": str, "payload": dict|None}}（库内现状）
    返回 {"to_embed": [(candidate, hash, "add"|"reembed")],
          "payload_only": [candidate], "unchanged": int, "delete": [item_key]}
    """
    to_embed: List[tuple] = []
    payload_only: List[VecCandidate] = []
    unchanged = 0
    seen = set()
    for c in candidates:
        if not c.item_key or not c.content:
            continue
        c.item_key = str(c.item_key)[:191]
        if c.item_key in seen:
            continue  # 同批重复 key 只取第一条
        seen.add(c.item_key)
        h = content_hash(c.content)
        ex = existing.get(c.item_key)
        if ex is None:
            to_embed.append((c, h, "add"))
        elif ex["hash"] != h:
            to_embed.append((c, h, "reembed"))
        elif ex["payload"] != (c.payload or None):
            payload_only.append(c)
        else:
            unchanged += 1
    delete = [k for k in existing if k not in seen] if delete_missing else []
    return {"to_embed": to_embed, "payload_only": payload_only, "unchanged": unchanged, "delete": delete}


# ── 底层写入 ─────────────────────────────────────────────────────────────────


async def _load_existing(library_id: int) -> Dict[str, dict]:
    """全库 hash / payload 映射（Python diff 用，用户库量级）。"""
    table = physical_table()
    out: Dict[str, dict] = {}
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                f"SELECT item_key, content_hash, payload FROM {table} WHERE library_id=%s",
                [library_id],
            )
            rows = await cur.fetchall()
    for r in rows:
        p = r["payload"]
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:
                p = None
        out[r["item_key"]] = {"hash": r["content_hash"], "payload": p}
    return out


async def _upsert_batch(library_id: int, rows: List[dict]) -> tuple:
    """批量 upsert 条目（向量字面量仅含浮点，直接内插）。

    rows: [{item_key, content, hash, payload_json, ref_key, vec}]
    返回 (成功写入的 rows, 失败的 rows, 最后一条写入错误信息或 None)。
    """
    table = physical_table()
    ok_rows: List[dict] = []
    failed_rows: List[dict] = []
    last_error: Optional[str] = None
    async with standard_pool.acquire() as conn:
        async with conn.cursor() as cur:
            for r in rows:
                vec_str = format_vector_literal(r["vec"])
                try:
                    await cur.execute(
                        f"INSERT INTO {table} (library_id, item_key, content, content_hash, payload, ref_key, embedding) "
                        f"VALUES (%s, %s, %s, %s, %s, %s, '{vec_str}') "
                        f"ON DUPLICATE KEY UPDATE content=VALUES(content), content_hash=VALUES(content_hash), "
                        f"payload=VALUES(payload), ref_key=VALUES(ref_key), embedding=VALUES(embedding)",
                        [library_id, r["item_key"], r["content"], r["hash"], r["payload_json"], r["ref_key"]],
                    )
                    ok_rows.append(r)
                except Exception as e:
                    logger.error(f"[vector_hub.ingest] 写入失败 library={library_id} key={r['item_key']}: {e}")
                    last_error = str(e)
                    failed_rows.append(r)
    return ok_rows, failed_rows, last_error


def _check_fail_fast(counts: Dict[str, int], library_id: int, last_error: Optional[str] = None) -> None:
    """失败熔断：失败累计足够多且零成功写入 → 立即中止构建。

    只在"系统性失败"时触发（不是个别坏行）：已尝试行全部失败、无任何成功写入。
    典型场景：OB 7604 向量索引内存不足、数据库不可用。抛 RuntimeError，
    由 build_library 落 status=error + last_error 后向上抛（管理端转 4000）。
    """
    if counts["failed"] < _FAIL_FAST_MIN_FAILED:
        return
    if counts["added"] or counts["reembedded"] or counts["payload_only"]:
        return
    err = last_error or ""
    hint = ""
    if "7604" in err or "vsag" in err.lower() or "bad_alloc" in err.lower():
        hint = "（疑似 OceanBase 向量索引内存不足：请检查租户内存规格与 GV$OB_VECTOR_MEMORY 水位）"
    raise RuntimeError(
        f"向量库(id={library_id})写入链路持续失败，已紧急中止：累计 {counts['failed']} 条全部失败、"
        f"零成功写入{hint}。中止是为了避免继续浪费嵌入额度；请排查数据库侧问题后重新构建。"
        + (f"最后错误：{err[:300]}" if err else "")
    )


async def _mark_failed(library_id: int, keys_with_err: List[tuple]) -> None:
    """失败行落重试表（retry_count 自增；反复失败的坏行由 retry_count>=3 归档）。

    retry_count 封顶 3（LEAST(count,2)+1）：同一批坏行反复构建不应无限累加，
    封顶后管理端「失败条目数」统计（只计 <3）也能稳定归零，不再虚高。
    """
    ft = failed_table()
    async with standard_pool.acquire() as conn:
        async with conn.cursor() as cur:
            for key, err in keys_with_err:
                try:
                    await cur.execute(
                        f"INSERT INTO {ft} (library_id, item_key, error, retry_count) VALUES (%s, %s, %s, 1) "
                        f"ON DUPLICATE KEY UPDATE error=VALUES(error), retry_count=LEAST(retry_count,2)+1, "
                        f"last_attempt_at=CURRENT_TIMESTAMP",
                        [library_id, str(key)[:191], (err or "")[:1000]],
                    )
                except Exception as inner:
                    logger.error(f"[vector_hub.ingest] failed 表写入异常 key={key}: {inner}")


async def _clear_failed(library_id: int, keys: List[str]) -> None:
    """写入成功后清掉对应失败记录。"""
    if not keys:
        return
    ft = failed_table()
    async with standard_pool.acquire() as conn:
        async with conn.cursor() as cur:
            for i in range(0, len(keys), 500):
                chunk = keys[i : i + 500]
                ph = ",".join(["%s"] * len(chunk))
                await cur.execute(
                    f"DELETE FROM {ft} WHERE library_id=%s AND item_key IN ({ph})",
                    [library_id, *chunk],
                )


async def _delete_keys(library_id: int, keys: List[str]) -> None:
    table = physical_table()
    ph = ",".join(["%s"] * len(keys))
    async with standard_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"DELETE FROM {table} WHERE library_id=%s AND item_key IN ({ph})",
                [library_id, *keys],
            )


async def _embed_contents(contents: List[str]) -> List[List[float]]:
    """统一嵌入入口（内置限流退避重试 + 并发上限，见 embedding_providers）。"""
    from app.langchain.embedding_providers import get_embedding

    return await get_embedding().embed_texts(contents)


async def _update_item_count_only(library_id: int) -> int:
    """只刷反范式计数（中途取消时用；快照不动 → 库保持陈旧判定）。
    同时把运行态置回 empty（取消不是错误，构建占用也已释放）。"""
    from app.models.standard import VecLibrary

    table = physical_table()
    async with standard_pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT COUNT(*) FROM {table} WHERE library_id=%s", [library_id])
            (cnt,) = await cur.fetchone()
    await VecLibrary.filter(id=library_id).update(item_count=int(cnt), status="empty")
    return int(cnt)


async def _refresh_library_stats(library_id: int, counts: Optional[Dict[str, int]] = None) -> int:
    """完整收尾：计数 + 构建快照（当前激活块）+ 完成时间。仅构建完整跑完时调用。

    带部分失败时（全失败已被熔断中止，走不到这里）把失败摘要写进 last_error，
    前端在非 error 状态下也会以橙色横幅提示（状态仍为可用，检索不受影响）。
    """
    from app.models.standard import VecLibrary

    cnt = await _update_item_count_only(library_id)
    block, dim = get_active_embed_info()
    last_error: Optional[str] = None
    if counts and counts.get("failed", 0) > 0:
        ok = counts.get("added", 0) + counts.get("reembedded", 0) + counts.get("payload_only", 0)
        last_error = (
            f"本次构建已完成，但有 {counts['failed']} 条嵌入/写入失败（成功 {ok} 条）。"
            "失败条目已记入重试表，下次构建自动续跑；若持续失败请查看后端日志"
        )
    await VecLibrary.filter(id=library_id).update(
        embed_block=block,
        embed_dim=dim,
        last_built_at=datetime.now(),
        last_error=last_error,
        status="empty",
    )
    return cnt


async def _finalize_ingest(library_id: int, canceled: bool, counts: Optional[Dict[str, int]] = None) -> None:
    if canceled:
        await _update_item_count_only(library_id)
    else:
        await _refresh_library_stats(library_id, counts)


# ── Python diff 路（手动 / 文件来源）────────────────────────────────────────


async def ingest_candidates(
    library_id: int,
    candidates: Iterable[VecCandidate],
    *,
    delete_missing: bool = True,
    embed_batch: int = 16,
    wipe_first: bool = False,
    cancel_event: Optional[asyncio.Event] = None,
    progress: Optional[dict] = None,
) -> Dict[str, int]:
    """三档增量摄入（全库 hash 读进内存 diff，适合用户库量级）。

    wipe_first=True 先清空该库全部条目（换块重建用，见模块注释）。
    progress：构建进度可变字典（{"processed","total","phase"}），由 build.py 传入。
    返回计数 {added, reembedded, payload_only, deleted, failed, unchanged, completed}。
    """
    await ensure_buildable_dims()
    counts = {"added": 0, "reembedded": 0, "payload_only": 0, "deleted": 0, "failed": 0, "unchanged": 0}
    table = physical_table()

    if wipe_first:
        async with standard_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"DELETE FROM {table} WHERE library_id=%s", [library_id])
        existing: Dict[str, dict] = {}
    else:
        existing = await _load_existing(library_id)

    cls = classify_candidates(existing, candidates, delete_missing)
    to_embed = cls["to_embed"]
    payload_only = cls["payload_only"]
    to_delete = cls["delete"]
    counts["unchanged"] = cls["unchanged"]
    logger.info(
        f"[vector_hub.ingest] library={library_id} 三档分类完成："
        f"待嵌入 {len(to_embed):,} / 仅payload {len(payload_only):,} / "
        f"未变 {counts['unchanged']:,} / 待删 {len(to_delete):,}"
        + ("（换块重建，已先清空）" if wipe_first else "")
    )

    if progress is not None:
        progress["total"] = len(to_embed)
        progress["phase"] = "embedding" if to_embed else "merging"

    canceled = False
    last_error: Optional[str] = None  # 最近一次批级错误（熔断提示用）

    # 嵌入 + 写入（批间查取消标记）
    for i in range(0, len(to_embed), embed_batch):
        if cancel_event is not None and cancel_event.is_set():
            canceled = True
            logger.info(f"[vector_hub.ingest] library={library_id} 已取消（{i}/{len(to_embed)}）")
            break
        batch = to_embed[i : i + embed_batch]
        try:
            vecs = await _embed_contents([c.content for c, _, _ in batch])
        except Exception as e:
            logger.error(f"[vector_hub.ingest] library={library_id} 批嵌入失败: {e}")
            last_error = str(e)
            await _mark_failed(library_id, [(c.item_key, str(e)) for c, _, _ in batch])
            counts["failed"] += len(batch)
            if progress is not None:
                progress["processed"] = min(i + embed_batch, len(to_embed))
            _check_fail_fast(counts, library_id, last_error)
            continue
        processed = min(i + embed_batch, len(to_embed))
        if progress is not None:
            progress["processed"] = processed
        if (i // embed_batch) % EMBED_LOG_EVERY_BATCHES == 0 or processed == len(to_embed):
            logger.info(f"[vector_hub.ingest] library={library_id} 嵌入中：{processed:,}/{len(to_embed):,}")
        rows = [
            {
                "item_key": c.item_key,
                "content": c.content,
                "hash": h,
                "payload_json": json.dumps(c.payload, ensure_ascii=False, sort_keys=True) if c.payload else None,
                "ref_key": c.ref_key,
                "vec": v,
            }
            for (c, h, _), v in zip(batch, vecs)
        ]
        ok_rows, failed_rows, last_error = await _upsert_batch(library_id, rows)
        failed_keys = {r["item_key"] for r in failed_rows}
        for c, _, action in batch:
            if c.item_key in failed_keys:
                counts["failed"] += 1
            else:
                counts["added" if action == "add" else "reembedded"] += 1
        await _mark_failed(library_id, [(r["item_key"], last_error or "写入失败") for r in failed_rows])
        await _clear_failed(library_id, [r["item_key"] for r in ok_rows])
        _check_fail_fast(counts, library_id, last_error)

    # payload-only 免嵌更新（内容未变，只刷 payload）
    if progress is not None:
        progress["phase"] = "merging"
    if not canceled:
        for c in payload_only:
            try:
                async with standard_pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            f"UPDATE {table} SET payload=%s WHERE library_id=%s AND item_key=%s",
                            [
                                json.dumps(c.payload, ensure_ascii=False, sort_keys=True) if c.payload else None,
                                library_id,
                                c.item_key,
                            ],
                        )
                counts["payload_only"] += 1
            except Exception as e:
                logger.error(f"[vector_hub.ingest] payload 更新失败 key={c.item_key}: {e}")
                counts["failed"] += 1

        # 源里消失 → 删行
        for i in range(0, len(to_delete), 500):
            chunk = to_delete[i : i + 500]
            await _delete_keys(library_id, chunk)
            counts["deleted"] += len(chunk)

    await _finalize_ingest(library_id, canceled, counts)
    counts["completed"] = int(not canceled)  # type: ignore[assignment]
    logger.info(f"[vector_hub.ingest] library={library_id} 完成: {counts}")
    return counts


# ── SQL 下推路（系统库 / 表同步源）───────────────────────────────────────────


async def _process_sql_batch(library_id: int, batch: List[dict], counts: Dict[str, int]) -> Optional[str]:
    """工作集一批：嵌入 + upsert + 计数 + 失败落表。返回最后一条错误信息（None=本批全部成功）。"""
    try:
        vecs = await _embed_contents([r["content"] for r in batch])
    except Exception as e:
        logger.error(f"[vector_hub.ingest] library={library_id} 批嵌入失败: {e}")
        await _mark_failed(library_id, [(r["item_key"], str(e)) for r in batch])
        counts["failed"] += len(batch)
        return str(e)
    rows = []
    for r, v in zip(batch, vecs):
        pj = r["payload_json"]
        if isinstance(pj, (dict, list)):
            pj = json.dumps(pj, ensure_ascii=False, sort_keys=True)
        rows.append(
            {
                "item_key": str(r["item_key"])[:191],
                "content": r["content"],
                "hash": r["src_chash"],
                "payload_json": pj,
                "ref_key": r["ref_key"],
                "vec": v,
            }
        )
    ok_rows, failed_rows, last_error = await _upsert_batch(library_id, rows)
    failed_keys = {r["item_key"] for r in failed_rows}
    for r in batch:
        if str(r["item_key"])[:191] in failed_keys:
            counts["failed"] += 1
        elif r["action"] == "add":
            counts["added"] += 1
        else:
            counts["reembedded"] += 1
    await _mark_failed(library_id, [(r["item_key"], last_error or "写入失败") for r in failed_rows])
    await _clear_failed(library_id, [r["item_key"] for r in ok_rows])
    return last_error


async def sync_sql_source(
    library_id: int,
    adapter,
    *,
    embed_batch: int = 16,
    wipe_first: bool = False,
    cancel_event: Optional[asyncio.Event] = None,
    progress: Optional[dict] = None,
) -> Dict[str, int]:
    """三档增量同步（diff 下推 SQL，不把全库 hash 拉进内存）。

    adapter 需提供 inner_select_sql()（产出 item_key / ref_key / content /
    payload_json / src_chash 列）与 keys_sql()（仅产出 item_key，供删除档比对）。
    流式游标取工作集，构建期间占用一个连接（长任务单连接的代价，池容量 20 足够）。
    progress：构建进度可变字典；SQL 路工作集总量要流完才知，total 保持 None。
    注：SQL 源不提供「仅 payload 更新」档——其 payload 由内容列派生，
    构造上内容不变 ⇒ payload 不变。
    """
    await ensure_buildable_dims()
    counts = {"added": 0, "reembedded": 0, "payload_only": 0, "deleted": 0, "failed": 0, "unchanged": 0}
    table = physical_table()

    if wipe_first:
        async with standard_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"DELETE FROM {table} WHERE library_id=%s", [library_id])

    # library_id 以 int 内插、不走 %s 占位符：适配器 SQL 片段含字面 %（LIKE 通配，
    # 见 adapters._CHAPTER_SKIP_SQL），传 args 时 aiomysql 会按 %-formatting
    # 格式化整句，把字面 % 误当格式符崩溃（如 standard_chapter 构建）
    lid = int(library_id)
    inner = adapter.inner_select_sql()
    work_sql = (
        f"SELECT s.item_key, s.ref_key, s.content, s.payload_json, s.src_chash, "
        f"CASE WHEN v.id IS NULL THEN 'add' ELSE 'reembed' END AS action "
        f"FROM ({inner}) s LEFT JOIN {table} v "
        f"ON v.library_id = {lid} AND v.item_key = s.item_key "
        f"WHERE v.id IS NULL OR v.content_hash <> s.src_chash"
    )

    if progress is not None:
        progress["phase"] = "diffing"
    logger.info(f"[vector_hub.sync] library={library_id} diff 下推中（源表大时这步要几分钟）...")

    canceled = False
    # SSDictCursor = 服务端流式游标：工作集（章节库约 42 万行 / 1.4GB）逐批拉取，
    # 不全量缓冲进客户端内存（DictCursor 是客户端缓冲，会把整个结果集读进进程）
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.SSDictCursor) as cur:
            await cur.execute(work_sql)
            logger.info(f"[vector_hub.sync] library={library_id} diff 下推完成，开始流式嵌入 ...")
            if progress is not None:
                progress["phase"] = "embedding"
            buf: List[dict] = []
            processed_total = 0
            logged = 0
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    canceled = True
                    logger.info(f"[vector_hub.ingest] library={library_id} SQL 同步已取消")
                    break
                rows = await cur.fetchmany(256)
                if not rows:
                    break
                buf.extend(rows)
                while len(buf) >= embed_batch:
                    batch = buf[:embed_batch]
                    buf = buf[embed_batch:]
                    last_error = await _process_sql_batch(library_id, batch, counts)
                    processed_total += len(batch)
                    if progress is not None:
                        progress["processed"] = processed_total
                    if logged == 0 or processed_total - logged >= SQL_PROGRESS_LOG_STEP:
                        logged = processed_total
                        logger.info(
                            f"[vector_hub.sync] library={library_id} 嵌入中：{processed_total:,} 条"
                            f"（新增 {counts['added']:,} / 重嵌 {counts['reembedded']:,} / 失败 {counts['failed']:,}）"
                        )
                    _check_fail_fast(counts, library_id, last_error)
            if not canceled and buf:
                last_error = await _process_sql_batch(library_id, buf, counts)
                processed_total += len(buf)
                if progress is not None:
                    progress["processed"] = processed_total
                logger.info(
                    f"[vector_hub.sync] library={library_id} 嵌入中：{processed_total:,} 条"
                    f"（新增 {counts['added']:,} / 重嵌 {counts['reembedded']:,} / 失败 {counts['failed']:,}）"
                )
                _check_fail_fast(counts, library_id, last_error)

    # 删除档：源里消失的 item_key（同样流式）
    if progress is not None:
        progress["phase"] = "deleting"
    if not canceled:
        keys_sql = (
            f"SELECT v.item_key FROM {table} v "
            f"LEFT JOIN ({adapter.keys_sql()}) s ON s.item_key = v.item_key "
            f"WHERE v.library_id = {lid} AND s.item_key IS NULL"
        )
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.SSCursor) as cur:
                await cur.execute(keys_sql)
                del_buf: List[str] = []
                del_logged = 0
                while True:
                    rows = await cur.fetchmany(1000)
                    if not rows:
                        break
                    del_buf.extend(r[0] for r in rows)
                    while len(del_buf) >= 1000:
                        chunk = del_buf[:1000]
                        del_buf = del_buf[1000:]
                        await _delete_keys(library_id, chunk)
                        counts["deleted"] += len(chunk)
                        if counts["deleted"] - del_logged >= 10_000:
                            del_logged = counts["deleted"]
                            logger.info(f"[vector_hub.sync] library={library_id} 删除中：{counts['deleted']:,} 条")
                if del_buf:
                    await _delete_keys(library_id, del_buf)
                    counts["deleted"] += len(del_buf)
                    logger.info(f"[vector_hub.sync] library={library_id} 删除中：{counts['deleted']:,} 条")

    await _finalize_ingest(library_id, canceled, counts)
    counts["completed"] = int(not canceled)  # type: ignore[assignment]
    logger.info(f"[vector_hub.sync] library={library_id} 完成: {counts}")
    return counts


__all__ = [
    "VecCandidate",
    "content_hash",
    "ingest_candidates",
    "sync_sql_source",
]
