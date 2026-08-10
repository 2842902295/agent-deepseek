"""
标准向量库 builder（OceanBase + 本地 Qwen3-Embedding-8B）。

职责：
  - build_meta()    增量嵌入 standard_base_info → standard_vec_meta
  - build_chapter() 增量嵌入 standard_jgh_pdf_chapter → standard_vec_chapter
                    跳前 7 类导航性章节，失败入 standard_vec_chapter_failed
  - get_status()    查各表已嵌 / 待嵌 / 失败计数

水位（watermark）：
  - meta：LEFT JOIN 已嵌入表，WHERE meta.standard_no IS NULL
  - chapter：MAX(chapter_id) of vec/failed 之外的所有章节；retry_count<3 的失败优先

调度入口：app/core/scheduler.py 每天 03:30 调 build_meta + build_chapter。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import aiomysql
from loguru import logger

from app.langchain.embedding_providers import get_local_embedding, vec_table_suffix
from app.services.mysql_pool import standard_pool

# 章节标题前缀精确头部匹配，命中即跳过（不嵌入）
_SKIP_TITLE_PREFIXES: tuple[str, ...] = (
    "范围",
    "规范性引用文件",
    "术语和定义",
    "前言",
    "参考文献",
    "编制说明",
    "索引",
)

# 章节 word_excerpt 截断长度
_WORD_LIMIT = 3000

# 失败最多重试 N 次后归档（不再自动重试）
_MAX_RETRY = 3
# placeholder-builder


def _format_vector(vec: List[float]) -> str:
    """OceanBase 接受 '[v1,v2,...]' 字符串作为 VECTOR 字面量。"""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


def _should_skip_chapter(title: Optional[str]) -> bool:
    """章节标题以 _SKIP_TITLE_PREFIXES 任意一项开头即跳过（嵌入会浪费 token）。"""
    if not title:
        return False
    t = title.strip()
    return any(t.startswith(p) for p in _SKIP_TITLE_PREFIXES)


async def _resolve_pool_filter(pool_id: Optional[int]) -> Optional[List[str]]:
    """读 standard_pool_check.standard_nos；None/-1 表示全库。"""
    if pool_id is None or pool_id == -1:
        return None
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT standard_nos FROM standard_pool_check WHERE id=%s AND is_active=1 LIMIT 1",
                [pool_id],
            )
            row = await cur.fetchone()
    if not row:
        logger.warning(f"[standard_vec] pool_id={pool_id} 未找到，回退全库")
        return None
    nos = row["standard_nos"]
    if isinstance(nos, str):
        nos = json.loads(nos)
    return list(nos)


def _quote_in_clause(values: List[str]) -> str:
    return ",".join("'" + v.replace("'", "''") + "'" for v in values)
# placeholder-helpers


class StandardVecBuilder:
    """主流程：嵌入 + 入库 + 失败重试。

    一次构造，build_meta / build_chapter 可重复调用；
    embedder 单例线程安全，无状态。
    """

    def __init__(self, embedder: Any = None):
        self.embedder = embedder or get_local_embedding()
        s = vec_table_suffix()
        self._meta_table = f"standard_vec_meta{s}"
        self._chapter_table = f"standard_vec_chapter{s}"
        self._failed_table = f"standard_vec_chapter_failed{s}"

    # ── meta ────────────────────────────────────────────────────────────
    async def build_meta(
        self,
        *,
        pool_id: Optional[int] = None,
        limit: int = 0,
        batch_size: int = 32,
    ) -> Dict[str, int]:
        """增量嵌入标准级元数据，返回 {processed, succeeded, failed}。"""
        await self.embedder.detect_dimension()
        pool_filter = await _resolve_pool_filter(pool_id)

        targets = await self._fetch_meta_targets(pool_filter, limit)
        if not targets:
            logger.info("[standard_vec.meta] 无待嵌入标准")
            return {"processed": 0, "succeeded": 0, "failed": 0}

        logger.info(f"[standard_vec.meta] 待处理 {len(targets)} 条")
        succeeded = failed = 0
        for i in range(0, len(targets), batch_size):
            batch = targets[i : i + batch_size]
            texts = [
                f"标准名称：{t['cname'] or ''}\n适用范围：{t['use_range'] or ''}"[:_WORD_LIMIT]
                for t in batch
            ]
            try:
                vecs = await self.embedder.embed_texts(texts)
            except Exception as e:
                logger.error(f"[standard_vec.meta] batch {i} embed 失败: {e}")
                failed += len(batch)
                continue

            ok, fail = await self._write_meta_batch(batch, vecs)
            succeeded += ok
            failed += fail
            logger.info(
                f"[standard_vec.meta] 进度 {min(i + batch_size, len(targets))}/{len(targets)}"
                f"（成功 {succeeded} 失败 {failed}）"
            )

        return {"processed": len(targets), "succeeded": succeeded, "failed": failed}
# placeholder-meta-impl

    async def _fetch_meta_targets(
        self, pool_filter: Optional[List[str]], limit: int
    ) -> List[Dict[str, Any]]:
        """LEFT JOIN 已嵌入表，自然取增量。"""
        where = ["b.cname IS NOT NULL", "b.cname <> ''", "v.standard_no IS NULL"]
        if pool_filter:
            if not pool_filter:
                return []
            where.append(f"b.standard_no IN ({_quote_in_clause(pool_filter)})")

        sql = (
            "SELECT b.standard_no, b.cname, b.use_range "
            "FROM standard_base_info b "
            f"LEFT JOIN {self._meta_table} v ON v.standard_no = b.standard_no "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY b.standard_no"
        )
        if limit > 0:
            sql += f" LIMIT {int(limit)}"

        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql)
                return list(await cur.fetchall())

    async def _write_meta_batch(
        self, batch: List[Dict[str, Any]], vecs: List[List[float]]
    ) -> tuple[int, int]:
        ok = fail = 0
        async with standard_pool.acquire() as conn:
            async with conn.cursor() as cur:
                for t, v in zip(batch, vecs):
                    vec_str = _format_vector(v)
                    try:
                        await cur.execute(
                            f"INSERT INTO {self._meta_table} "
                            f"(standard_no, cname, use_range, embedding) "
                            f"VALUES (%s, %s, %s, '{vec_str}') "
                            f"ON DUPLICATE KEY UPDATE "
                            f"cname=VALUES(cname), use_range=VALUES(use_range), "
                            f"embedding=VALUES(embedding)",
                            [t["standard_no"], t["cname"], t["use_range"]],
                        )
                        ok += 1
                    except Exception as e:
                        logger.error(f"[standard_vec.meta] 写入失败 {t['standard_no']}: {e}")
                        fail += 1
        return ok, fail
# placeholder-chapter-impl

    # ── chapter ─────────────────────────────────────────────────────────
    async def build_chapter(
        self,
        *,
        pool_id: Optional[int] = None,
        limit: int = 0,
        batch_size: int = 16,
        retry_failed: bool = True,
    ) -> Dict[str, int]:
        """增量嵌入章节级文本，跳前 7 类导航性章节。

        retry_failed=True 时优先重试 standard_vec_chapter_failed.retry_count<_MAX_RETRY 的记录。
        """
        await self.embedder.detect_dimension()
        pool_filter = await _resolve_pool_filter(pool_id)

        # 1) 先把可重试的失败记录 chapter_id 拿出来，加进本次嵌入计划
        retry_ids: List[int] = []
        if retry_failed:
            retry_ids = await self._fetch_retryable_failed_ids()

        targets = await self._fetch_chapter_targets(pool_filter, limit, retry_ids)
        # 业务过滤：跳前 7 类标题
        before = len(targets)
        targets = [t for t in targets if not _should_skip_chapter(t.get("title"))]
        skipped = before - len(targets)
        if skipped:
            logger.info(f"[standard_vec.chapter] 标题前缀过滤跳过 {skipped} 条")

        if not targets:
            logger.info("[standard_vec.chapter] 无待嵌入章节")
            return {"processed": 0, "succeeded": 0, "failed": 0, "skipped": skipped}

        logger.info(f"[standard_vec.chapter] 待处理 {len(targets)} 条（含失败重试 {len(retry_ids)} 条）")
        succeeded = failed = 0
        for i in range(0, len(targets), batch_size):
            batch = targets[i : i + batch_size]
            payload = self._prep_chapter_batch(batch)
            try:
                vecs = await self.embedder.embed_texts([p["text"] for p in payload])
            except Exception as e:
                logger.error(f"[standard_vec.chapter] batch {i} embed 失败: {e}")
                await self._mark_failed(batch, str(e))
                failed += len(batch)
                continue

            ok, fail = await self._write_chapter_batch(payload, vecs)
            succeeded += ok
            failed += fail
            logger.info(
                f"[standard_vec.chapter] 进度 {min(i + batch_size, len(targets))}/{len(targets)}"
                f"（成功 {succeeded} 失败 {failed}）"
            )

        return {
            "processed": len(targets),
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "retried": len(retry_ids),
        }
# placeholder-chapter-helpers

    async def _fetch_retryable_failed_ids(self) -> List[int]:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    f"SELECT chapter_id FROM {self._failed_table} "
                    "WHERE retry_count < %s ORDER BY last_attempt_at ASC LIMIT 5000",
                    [_MAX_RETRY],
                )
                return [r["chapter_id"] for r in await cur.fetchall()]

    async def _fetch_chapter_targets(
        self,
        pool_filter: Optional[List[str]],
        limit: int,
        retry_ids: List[int],
    ) -> List[Dict[str, Any]]:
        """两路 UNION：① 失败重试 ② 增量水位（chapter.id > MAX(已嵌))，并跳过 vec 表已有。"""
        # ② 增量水位
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    f"SELECT GREATEST("
                    f"  COALESCE((SELECT MAX(chapter_id) FROM {self._chapter_table}), 0),"
                    f"  COALESCE((SELECT MAX(chapter_id) FROM {self._failed_table}), 0)"
                    f") AS last_id"
                )
                row = await cur.fetchone()
                last_id = int(row["last_id"] or 0)

        where = ["c.word IS NOT NULL", "c.word <> ''", "c.id > %s"]
        params: list = [last_id]
        if pool_filter:
            where.append(f"p.standard_no IN ({_quote_in_clause(pool_filter)})")

        sql_incremental = (
            "SELECT c.id AS chapter_id, p.standard_no, c.title_no, c.title, c.word "
            "FROM standard_jgh_pdf_chapter c "
            "JOIN standard_jgh_pdf p ON p.main_task_id = c.main_task_id "
            f"WHERE {' AND '.join(where)} ORDER BY c.id"
        )
        if limit > 0:
            sql_incremental += f" LIMIT {int(limit)}"

        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql_incremental, params)
                rows_incr = list(await cur.fetchall())

                # ① 失败重试
                rows_retry: List[Dict[str, Any]] = []
                if retry_ids:
                    quoted_ids = ",".join(str(int(i)) for i in retry_ids)
                    await cur.execute(
                        "SELECT c.id AS chapter_id, p.standard_no, c.title_no, c.title, c.word "
                        "FROM standard_jgh_pdf_chapter c "
                        "JOIN standard_jgh_pdf p ON p.main_task_id = c.main_task_id "
                        f"WHERE c.id IN ({quoted_ids})"
                    )
                    rows_retry = list(await cur.fetchall())

        # 重试在前，新增在后；按 chapter_id 去重保留首次
        seen: set = set()
        merged: List[Dict[str, Any]] = []
        for r in rows_retry + rows_incr:
            cid = r["chapter_id"]
            if cid in seen:
                continue
            seen.add(cid)
            merged.append(r)
        return merged

    def _prep_chapter_batch(self, batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for t in batch:
            word = (t.get("word") or "")[:_WORD_LIMIT]
            title_no = t.get("title_no") or ""
            title = t.get("title") or ""
            text = f"【{title_no}】{title}\n{word}"
            out.append({"row": t, "excerpt": word, "text": text[: _WORD_LIMIT + 200]})
        return out
# placeholder-chapter-write

    async def _write_chapter_batch(
        self, payload: List[Dict[str, Any]], vecs: List[List[float]]
    ) -> tuple[int, int]:
        ok = fail = 0
        async with standard_pool.acquire() as conn:
            async with conn.cursor() as cur:
                for p, v in zip(payload, vecs):
                    t = p["row"]
                    vec_str = _format_vector(v)
                    try:
                        await cur.execute(
                            f"INSERT INTO {self._chapter_table} "
                            f"(standard_no, chapter_id, title_no, title, word_excerpt, embedding) "
                            f"VALUES (%s, %s, %s, %s, %s, '{vec_str}') "
                            f"ON DUPLICATE KEY UPDATE "
                            f"title_no=VALUES(title_no), title=VALUES(title), "
                            f"word_excerpt=VALUES(word_excerpt), embedding=VALUES(embedding)",
                            [
                                t["standard_no"],
                                t["chapter_id"],
                                t.get("title_no"),
                                t.get("title"),
                                p["excerpt"],
                            ],
                        )
                        # 写入成功就把 failed 表里的同 chapter_id 删掉
                        await cur.execute(
                            f"DELETE FROM {self._failed_table} WHERE chapter_id=%s",
                            [t["chapter_id"]],
                        )
                        ok += 1
                    except Exception as e:
                        logger.error(
                            f"[standard_vec.chapter] 写入失败 chapter_id={t['chapter_id']}: {e}"
                        )
                        await self._mark_failed([t], str(e))
                        fail += 1
        return ok, fail

    async def _mark_failed(self, batch: List[Dict[str, Any]], error: str) -> None:
        """整批 / 单条 落 failed 表。retry_count 自增。"""
        if not batch:
            return
        err_truncated = (error or "")[:1000]
        async with standard_pool.acquire() as conn:
            async with conn.cursor() as cur:
                for t in batch:
                    try:
                        await cur.execute(
                            f"INSERT INTO {self._failed_table} "
                            "(chapter_id, standard_no, error, retry_count) "
                            "VALUES (%s, %s, %s, 1) "
                            "ON DUPLICATE KEY UPDATE "
                            "error=VALUES(error), retry_count=retry_count+1, "
                            "last_attempt_at=CURRENT_TIMESTAMP",
                            [t["chapter_id"], t.get("standard_no"), err_truncated],
                        )
                    except Exception as inner:
                        logger.error(
                            f"[standard_vec.chapter] failed 表写入异常 chapter_id={t.get('chapter_id')}: {inner}"
                        )
# placeholder-status

    # ── 状态 ─────────────────────────────────────────────────────────────
    async def get_status(self) -> Dict[str, Any]:
        """各表当前计数 / 失败/重试可见度，给运维看一眼用。"""
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                async def _count(sql: str) -> int:
                    await cur.execute(sql)
                    row = await cur.fetchone()
                    return int(list(row.values())[0]) if row else 0

                meta_done = await _count(f"SELECT COUNT(*) AS c FROM {self._meta_table}")
                meta_total = await _count("SELECT COUNT(*) AS c FROM standard_base_info")
                chapter_done = await _count(f"SELECT COUNT(*) AS c FROM {self._chapter_table}")
                chapter_total = await _count(
                    "SELECT COUNT(*) AS c FROM standard_jgh_pdf_chapter "
                    "WHERE word IS NOT NULL AND word <> ''"
                )
                failed_pending = await _count(
                    f"SELECT COUNT(*) AS c FROM {self._failed_table} "
                    f"WHERE retry_count < {_MAX_RETRY}"
                )
                failed_archived = await _count(
                    f"SELECT COUNT(*) AS c FROM {self._failed_table} "
                    f"WHERE retry_count >= {_MAX_RETRY}"
                )

        return {
            "meta": {"done": meta_done, "total": meta_total, "pending": max(0, meta_total - meta_done)},
            "chapter": {
                "done": chapter_done,
                "total": chapter_total,
                "pending": max(0, chapter_total - chapter_done),
            },
            "failed": {"pending_retry": failed_pending, "archived": failed_archived},
            "embedder": self.embedder.get_metadata(),
        }


__all__ = ["StandardVecBuilder"]
