"""
标准库指纹增量同步（源库 → 本地业务库，六张 standard_* 表）

背景：原「迁入数据」是全清重写（三张表 DELETE 全量 + 重建，两张表按 id 跳过式追加），
中途失败会留下空表 / 半表，且源端硬删除、字段更新永远追不平。
本模块改为**纯指纹 diff**（不依赖时间水位线，无状态、可自愈）：

- 源 / 本地各自流式扫描「主键 + 行指纹」（MD5 列值，keyset 分页，内存有界）
- 双指针归并：源有本地无 → 新增；指纹不等 → 更新；本地有源无 → 删除
- diff 完成后分批回源取整行，`INSERT ... ON DUPLICATE KEY UPDATE` 落库

语义（与全量迁进口径对齐，但增量执行、可重复跑）：
- standard_base_info：源端只取 deleted=0/NULL 行；源端软删 = 本地硬删
- standard_jgh_pdf / chapter / term：忠实镜像（含 deleted 标记；
  term 无图片列、无 OCR 回填，word 正常参与指纹）
- standard_jgh_pdf_table / formula：word 可能被本地 OCR 回填（sync-img-word 接口），
  故 **word 不参与指纹**；更新时源端 word 为空则保留本地已回填值，源端非空才覆盖

指纹口径注意：
- NULL 用哨兵 '\\N' 占位（CONCAT_WS 会跳过 NULL 参数，不占位会产生列间歧义碰撞）
- 本地写入侧的转换必须镜像进指纹：CharField 截断 → SUBSTRING；TEXT → MD5(col)；
  DATE → DATE_FORMAT('%Y-%m-%d')；deleted → IFNULL(deleted,0)
- pymysql 带参执行时 % 需转义为 %%（DATE_FORMAT 的格式串）

定时任务：每日 02:30（仅 BRAND_VARIANT=standard 变体注册；env STD_SYNC_DAILY_ENABLED 开关，
见 core/scheduler.py），排在 03:30 向量库同步之前，新增章节当天即可入库向量。
"""

from __future__ import annotations

import json
import os
import time
from array import array
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiomysql
from loguru import logger

from app.api.v1.ai.data_migration import (
    MySQLConfig,
    _coerce_bool,
    _extract_file_name,
    _open_mysql_conn,
    _row_to_base_info,
    _trunc,
)
from app.services.mysql_pool import standard_pool
from app.settings import APP_SETTINGS as settings

# ── 参数 ──────────────────────────────────────────────────────────────────────

FP_PAGE = 20_000        # 指纹扫描 keyset 分页大小
APPLY_FETCH = 2_000     # 回源取整行的 IN 批大小
WRITE_CHUNK = 500       # 单条 INSERT 语句的行数
DELETE_CHUNK = 1_000    # 单条 DELETE 语句的 id 数
PROGRESS_LOG_EVERY = 100_000  # 扫描期日志步长（长表归并期也要有心跳，防"看起来像中断了"）

# ── 源库连接配置 ──────────────────────────────────────────────────────────────


def get_source_config() -> MySQLConfig:
    """源库连接：默认复用本地 STANDARD_MYSQL_* 凭据（同实例），库名默认 std_main。

    异构部署可用 STD_SYNC_SOURCE_HOST/PORT/USER/PASSWORD/DB 整套覆盖。
    """
    return MySQLConfig(
        host=os.getenv("STD_SYNC_SOURCE_HOST") or settings.STANDARD_MYSQL_HOST,
        port=int(os.getenv("STD_SYNC_SOURCE_PORT") or settings.STANDARD_MYSQL_PORT),
        user=os.getenv("STD_SYNC_SOURCE_USER") or settings.STANDARD_MYSQL_USER,
        password=os.getenv("STD_SYNC_SOURCE_PASSWORD") or settings.STANDARD_MYSQL_PASSWORD,
        database=os.getenv("STD_SYNC_SOURCE_DB") or "std_main",
    )


def daily_enabled() -> bool:
    # 标准库维护仅 standard（行业版）变体启用（调度器注册口径一致，见 core/scheduler.py）；
    # 非 standard 变体定时同步不注册，即使 env 开关为 true 也视为关闭
    if (settings.BRAND_VARIANT or "standard").lower() != "standard":
        return False
    return (os.getenv("STD_SYNC_DAILY_ENABLED", "true") or "true").lower() in ("1", "true", "yes", "on")


# ── 指纹表达式工具 ────────────────────────────────────────────────────────────

# NULL 哨兵：SQL 文本 '\\N'（字面反斜杠 N）。注意 MySQL 里 '\N' 是 NULL 转义，必须双反斜杠。
_NULL = "'\\\\N'"


def _fp(*parts: str) -> str:
    return "MD5(CONCAT_WS('|', " + ", ".join(parts) + "))"


def _s(col: str, n: int) -> str:
    """可空字符串（镜像 _trunc 截断）。"""
    return f"IFNULL(SUBSTRING(`{col}`,1,{n}),{_NULL})"


def _t(col: str) -> str:
    """可空长文本：先 MD5 收敛，避免 CONCAT 大缓冲。"""
    return f"IFNULL(MD5(`{col}`),{_NULL})"


def _p(col: str) -> str:
    """可空数值/原样列。"""
    return f"IFNULL(`{col}`,{_NULL})"


def _d(col: str) -> str:
    """DATE 列（镜像 _parse_date 只留年月日）。先 DATE() 收敛（源端若是 DATETIME/字符串也只留日期），
    再 CAST AS CHAR 得 'YYYY-MM-DD'——不用 DATE_FORMAT，避开 % 与 pymysql %% 转义纠缠。"""
    return f"IFNULL(CAST(DATE(`{col}`) AS CHAR),{_NULL})"


_DEL = "IFNULL(`deleted`,0)"

# is_secret：源端存 '是'/'否' 等杂值，写入侧统一走 _coerce_bool（'是'/'1'/1→1，其余→0，NULL 保持）。
# 指纹表达式必须与写入口径完全一致，否则每轮同步全表误报「已变化」。
# 字符串列走 IN 字面量集合；数值 / BIT 列靠 `= 1` 分支兜底（避开二进制串与字面量的 collation 冲突）。
_IS_SECRET = (
    f"CASE WHEN `is_secret` IS NULL THEN {_NULL}"
    " WHEN `is_secret` IN ('1','true','True','TRUE','yes','Yes','是') OR `is_secret` = 1 THEN '1'"
    " ELSE '0' END"
)

# ── 行转换器（源行 dict → 与 insert 列序一致的 values 元组） ─────────────────

_BASE_INFO_COLS = (
    "id", "standard_no", "cname", "ename", "use_range", "intl_cat", "nat_cat",
    "std_domain", "std_field", "std_year", "std_obj", "issue_date", "act_date",
    "annul_date", "approval_unit", "put_unit", "lead_unit",
    "draft_unit", "draft_staff", "chief_unit", "mgr_dept", "is_secret",
    "std_nature", "mandatory_clause", "patent_info", "state", "security_level",
    "release_history", "release_std_no", "replace_description", "replace_stds",
    "target_stds", "adopt_situation", "adopt_std_no", "adopt_text", "adopt_level",
    "adopt_type", "adopt_no", "adopt_name", "gjb_no", "std_type", "industry",
    "remark", "creator", "updater", "deleted",
)


def _base_info_values(row: dict) -> Optional[tuple]:
    """复用全量迁移同款转换器，保证指纹与写入口径一致。"""
    obj = _row_to_base_info(row)
    if obj is None:
        return None
    return tuple(getattr(obj, c) for c in _BASE_INFO_COLS)


def _pdf_values(row: dict) -> Optional[tuple]:
    if not row.get("id"):
        return None
    return (
        row["id"],
        row.get("main_task_id"),
        _trunc(row.get("file_uuid"), 64),
        _trunc(row.get("name"), 255),
        _trunc(row.get("standard_no"), 255),
        row.get("cname"),
        _coerce_bool(row.get("deleted")),
    )


def _chapter_values(row: dict) -> Optional[tuple]:
    if not row.get("id"):
        return None
    return (
        row["id"],
        row.get("main_task_id"),
        _trunc(row.get("title"), 500),
        _trunc(row.get("title_no"), 10240),
        row.get("page"),
        row.get("word"),
        _coerce_bool(row.get("deleted")),
    )


def _term_values(row: dict) -> Optional[tuple]:
    if not row.get("id"):
        return None
    return (
        row["id"],
        _trunc(row.get("file_uuid"), 100),
        row.get("main_task_id"),
        _trunc(row.get("title"), 255),
        _trunc(row.get("title_ename"), 500),
        _trunc(row.get("chapter_no"), 100),
        row.get("word"),
        int(row["page"]) if row.get("page") is not None else None,
        int(row["start"]) if row.get("start") is not None else None,
        int(row["end"]) if row.get("end") is not None else None,
        _coerce_bool(row.get("deleted")),
    )


def _table_formula_values(row: dict) -> Optional[tuple]:
    if not row.get("id"):
        return None
    image = row.get("image")
    return (
        row["id"],
        row.get("main_task_id"),
        _trunc(row.get("data_uuid"), 64),
        row.get("title"),
        row.get("word"),
        image,
        _extract_file_name(image),
        int(row["page"]) if row.get("page") is not None else None,
        int(row["start"]) if row.get("start") is not None else None,
        int(row["end"]) if row.get("end") is not None else None,
        _coerce_bool(row.get("deleted")),
    )


# ── 表规格 ────────────────────────────────────────────────────────────────────


@dataclass
class TableSpec:
    name: str
    label: str
    key_is_int: bool
    fp_expr: str
    insert_cols: Tuple[str, ...]
    row_to_values: Callable[[dict], Optional[tuple]]
    src_where: str = ""  # 源端附加过滤（如 base_info 排除软删行）
    # 列级自定义 UPDATE 表达式（默认 col=VALUES(col)）
    on_dup_override: Dict[str, str] = field(default_factory=dict)

    @property
    def sentinel(self):
        return -1 if self.key_is_int else ""

    @property
    def id_array(self):
        """bigint 主键用 array('q') 存（8B/个），千万级 diff 内存可控。"""
        return array("q") if self.key_is_int else []


_BASE_INFO_FP = _fp(
    _s("standard_no", 200), _t("cname"), _t("ename"), _t("use_range"),
    _s("intl_cat", 200), _s("nat_cat", 200), _s("std_domain", 200),
    _s("std_field", 200), _s("std_year", 10), _t("std_obj"),
    _d("issue_date"), _d("act_date"), _d("annul_date"),
    _t("approval_unit"), _t("put_unit"), _t("lead_unit"),
    _t("draft_unit"), _t("draft_staff"), _t("chief_unit"), _t("mgr_dept"),
    _IS_SECRET, _s("std_nature", 50), _t("mandatory_clause"), _t("patent_info"),
    _s("state", 50), _s("security_level", 50), _t("release_history"),
    _s("release_std_no", 200), _t("replace_description"), _t("replace_stds"),
    _t("target_stds"), _t("adopt_situation"), _s("adopt_std_no", 200),
    _t("adopt_text"), _s("adopt_level", 50), _s("adopt_type", 50),
    _s("adopt_no", 200), _t("adopt_name"), _s("gjb_no", 200), _s("std_type", 50),
    _s("industry", 200), _t("remark"), _s("creator", 64), _s("updater", 64),
    _DEL,
)

_PDF_FP = _fp(
    _p("main_task_id"), _s("file_uuid", 64), _s("name", 255),
    _s("standard_no", 255), _t("cname"), _DEL,
)

_CHAPTER_FP = _fp(
    _p("main_task_id"), _s("title", 500), _s("title_no", 10240),
    _p("page"), _t("word"), _DEL,
)

# table/formula 的 word 不参与指纹（本地可能 OCR 回填，见模块 docstring）
_TABLE_FORMULA_FP = _fp(
    _p("main_task_id"), _s("data_uuid", 64), _t("title"), _t("image"),
    _p("page"), _p("start"), _p("end"), _DEL,
)

_TERM_FP = _fp(
    _s("file_uuid", 100), _p("main_task_id"), _s("title", 255),
    _s("title_ename", 500), _s("chapter_no", 100), _t("word"),
    _p("page"), _p("start"), _p("end"), _DEL,
)

# word 保留规则：源端为空（NULL/''）时保留本地现值（OCR 回填成果），源端非空才覆盖
_KEEP_WORD = "IF(VALUES(`word`) IS NULL OR VALUES(`word`) = '', `word`, VALUES(`word`))"

TABLES: Tuple[TableSpec, ...] = (
    TableSpec(
        name="standard_base_info",
        label="标准基础信息",
        key_is_int=False,
        fp_expr=_BASE_INFO_FP,
        insert_cols=_BASE_INFO_COLS,
        row_to_values=_base_info_values,
        # 与全量迁移一致：只迁未软删行；源端转软删的行在本地视为删除
        src_where="(`deleted` = 0 OR `deleted` IS NULL)",
        on_dup_override={"update_time": "CURRENT_TIMESTAMP"},
    ),
    TableSpec(
        name="standard_jgh_pdf",
        label="标准文档",
        key_is_int=True,
        fp_expr=_PDF_FP,
        insert_cols=("id", "main_task_id", "file_uuid", "name", "standard_no", "cname", "deleted"),
        row_to_values=_pdf_values,
    ),
    TableSpec(
        name="standard_jgh_pdf_chapter",
        label="标准章节",
        key_is_int=True,
        fp_expr=_CHAPTER_FP,
        insert_cols=("id", "main_task_id", "title", "title_no", "page", "word", "deleted"),
        row_to_values=_chapter_values,
    ),
    TableSpec(
        name="standard_jgh_pdf_table",
        label="标准表格",
        key_is_int=True,
        fp_expr=_TABLE_FORMULA_FP,
        insert_cols=("id", "main_task_id", "data_uuid", "title", "word", "image", "file_name", "page", "start", "end", "deleted"),
        row_to_values=_table_formula_values,
        on_dup_override={"word": _KEEP_WORD},
    ),
    TableSpec(
        name="standard_jgh_pdf_formula",
        label="标准公式",
        key_is_int=True,
        fp_expr=_TABLE_FORMULA_FP,
        insert_cols=("id", "main_task_id", "data_uuid", "title", "word", "image", "file_name", "page", "start", "end", "deleted"),
        row_to_values=_table_formula_values,
        on_dup_override={"word": _KEEP_WORD},
    ),
    TableSpec(
        name="standard_jgh_pdf_term",
        label="标准术语",
        key_is_int=True,
        fp_expr=_TERM_FP,
        insert_cols=("id", "file_uuid", "main_task_id", "title", "title_ename", "chapter_no", "word", "page", "start", "end", "deleted"),
        row_to_values=_term_values,
    ),
)

# ── 运行状态（进程内 + DB 双层） ─────────────────────────────────────────────

STATE_TABLE = "standard_data_sync_state"

_running = False
_live: Dict[str, str] = {}  # 进程内实时进度 {table, phase, detail}


def is_running() -> bool:
    return _running


def live_progress() -> Optional[Dict[str, str]]:
    return dict(_live) if _running and _live else None


def _progress(table: str, phase: str, detail: str = "") -> None:
    _live.update({"table": table, "phase": phase, "detail": detail})


async def _state_upsert(raw: Optional[Dict[str, str]] = None, **fields) -> None:
    """单行状态表幂等写（id 恒为 1）。失败仅告警，不阻塞同步主流程。

    fields：参数化赋值的列；raw：列 → 原生 SQL 表达式（如 started_at → NOW()，
    时间取 DB 现值，避免应用服务器时钟漂移）。
    """
    raw = raw or {}
    try:
        cols = ["id", *fields.keys(), *raw.keys()]
        vals = [1, *fields.values()]
        vals_sql = ", ".join(["%s"] * len(vals))
        if raw:
            vals_sql += ", " + ", ".join(raw.values())
        updates = ", ".join(
            [f"`{c}`=VALUES(`{c}`)" for c in fields.keys()] + [f"`{c}`={expr}" for c, expr in raw.items()]
        )
        sql = f"INSERT INTO {STATE_TABLE} ({', '.join(cols)}) VALUES ({vals_sql}) ON DUPLICATE KEY UPDATE {updates}"
        async with standard_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, vals)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[std-sync] 同步状态落库失败（不影响同步本身）: {e}")


async def get_state_row() -> Optional[dict]:
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(f"SELECT * FROM {STATE_TABLE} WHERE id = 1")
                return await cur.fetchone()
    except Exception:
        return None


# ── 指纹流与归并 ──────────────────────────────────────────────────────────────


async def _fp_stream(conn: aiomysql.Connection, spec: TableSpec, is_source: bool):
    """按主键升序流式产出 (id, fp)，keyset 分页（每页独立查询，无长活游标）。"""
    last = spec.sentinel
    extra = f" AND {spec.src_where}" if (is_source and spec.src_where) else ""
    while True:
        sql = (
            f"SELECT `id`, {spec.fp_expr} AS fp FROM `{spec.name}` "
            f"WHERE `id` > %s{extra} ORDER BY `id` LIMIT {FP_PAGE}"
        )
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, (last,))
            rows = await cur.fetchall()
        if not rows:
            return
        for r in rows:
            yield r["id"], r["fp"]
        last = rows[-1]["id"]


async def _sync_table(spec: TableSpec, src_conn: aiomysql.Connection) -> Dict[str, int]:
    """单表：扫描归并 → 回源取行批量 upsert → 批量删本地多余行。返回四段计数。"""
    t0 = time.time()
    logger.info(f"[std-sync] {spec.name} 开始指纹扫描归并 ...")
    _progress(spec.name, "diff", "指纹扫描中")

    added = spec.id_array
    updated = spec.id_array
    removed = spec.id_array
    unchanged = 0

    async def _first(it):
        try:
            return await it.__anext__()
        except StopAsyncIteration:
            return None

    it_s = _fp_stream(src_conn, spec, is_source=True)
    async with standard_pool.acquire() as lconn:
        it_l = _fp_stream(lconn, spec, is_source=False)
        sa: Optional[Tuple[Any, str]] = await _first(it_s)
        sl: Optional[Tuple[Any, str]] = await _first(it_l)
        scanned = 0
        while sa is not None and sl is not None:
            ida, fpa = sa
            idl, fpl = sl
            if ida == idl:
                if fpa == fpl:
                    unchanged += 1
                else:
                    updated.append(ida)
                sa = await _first(it_s)
                sl = await _first(it_l)
            elif ida < idl:
                added.append(ida)  # 源有本地无 → 新增
                sa = await _first(it_s)
            else:
                removed.append(idl)  # 本地有源无 → 删除
                sl = await _first(it_l)
            scanned += 1
            if scanned % PROGRESS_LOG_EVERY == 0:
                logger.info(
                    f"[std-sync] {spec.name} 归并中：已比对 {scanned:,} 行 "
                    f"(新增 {len(added):,} / 更新 {len(updated):,} / 删除 {len(removed):,})"
                )
                _progress(spec.name, "diff", f"已比对 {scanned:,} 行")
        while sa is not None:
            added.append(sa[0])
            sa = await _first(it_s)
        while sl is not None:
            removed.append(sl[0])
            sl = await _first(it_l)

    n_add, n_upd, n_del = len(added), len(updated), len(removed)
    logger.info(
        f"[std-sync] {spec.name} 归并完成：新增 {n_add:,} / 更新 {n_upd:,} / "
        f"删除 {n_del:,} / 未变 {unchanged:,}（扫描 {time.time() - t0:.1f}s）"
    )

    # ── 写入：新增 + 更新（回源取整行，ON DUPLICATE KEY UPDATE） ──
    upsert_ids = list(added) + list(updated)
    del added, updated  # 释放
    if upsert_ids:
        _progress(spec.name, "upsert", f"0/{len(upsert_ids):,}")
        await _apply_upserts(spec, src_conn, upsert_ids)

    # ── 删除：本地多余行 ──
    if n_del:
        _progress(spec.name, "delete", f"0/{n_del:,}")
        await _apply_deletes(spec, removed)

    stats = {"added": n_add, "updated": n_upd, "deleted": n_del, "unchanged": unchanged}
    logger.info(f"[std-sync] {spec.name} 完成：{stats}（总耗时 {time.time() - t0:.1f}s）")
    _progress(spec.name, "done", "")
    return stats


async def _apply_upserts(spec: TableSpec, src_conn: aiomysql.Connection, ids: List[Any]) -> None:
    """分批：IN 回源取整行 → 转换 → INSERT ... ON DUPLICATE KEY UPDATE。"""
    cols_sql = ", ".join(f"`{c}`" for c in spec.insert_cols)
    ph_row = ", ".join(["%s"] * len(spec.insert_cols))
    updates = ", ".join(
        f"`{c}`={spec.on_dup_override.get(c, f'VALUES(`{c}`)')}"
        for c in spec.insert_cols if c != "id"
    )
    total = len(ids)
    done = 0
    for i in range(0, total, APPLY_FETCH):
        chunk_ids = ids[i : i + APPLY_FETCH]
        ph = ", ".join(["%s"] * len(chunk_ids))
        async with src_conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(f"SELECT * FROM `{spec.name}` WHERE `id` IN ({ph})", chunk_ids)
            rows = {r["id"]: r for r in await cur.fetchall()}
        values = []
        for rid in chunk_ids:
            row = rows.get(rid)
            if row is None:
                continue  # diff 与 apply 间隙被源端删掉，跳过
            v = spec.row_to_values(row)
            if v is not None:
                values.append(v)
        for j in range(0, len(values), WRITE_CHUNK):
            batch = values[j : j + WRITE_CHUNK]
            values_sql = ", ".join(f"({ph_row})" for _ in batch)
            params = [x for v in batch for x in v]
            async with standard_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"INSERT INTO `{spec.name}` ({cols_sql}) VALUES {values_sql} "
                        f"ON DUPLICATE KEY UPDATE {updates}",
                        params,
                    )
        done += len(chunk_ids)
        _progress(spec.name, "upsert", f"{done:,}/{total:,}")
        # 回写是最慢的阶段（回源取整行 + 大宽 INSERT），逐批打日志当心跳，
        # 不然几十分钟的首轮全量同步在控制台看起来像挂了
        logger.info(f"[std-sync] {spec.name} 回写中：{done:,}/{total:,} 行")


async def _apply_deletes(spec: TableSpec, ids) -> None:
    total = len(ids)
    done = 0
    for i in range(0, total, DELETE_CHUNK):
        chunk = list(ids[i : i + DELETE_CHUNK])
        ph = ", ".join(["%s"] * len(chunk))
        async with standard_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"DELETE FROM `{spec.name}` WHERE `id` IN ({ph})", chunk)
        done += len(chunk)
        if done % (DELETE_CHUNK * 10) == 0 or done == total:
            _progress(spec.name, "delete", f"{done:,}/{total:,}")
            # 删除很快，每万行打一条心跳即可
            logger.info(f"[std-sync] {spec.name} 删除中：{done:,}/{total:,} 行")


# ── 主入口 ────────────────────────────────────────────────────────────────────


async def run_sync(trigger: str = "manual") -> None:
    """执行一轮全表指纹同步。并发守卫：已有任务在跑则直接返回。

    所有异常内部消化并落到状态表（调用方：API 后台任务 / 定时器）。
    """
    global _running
    if _running:
        logger.warning("[std-sync] 已有同步任务在运行，本次触发忽略")
        return
    _running = True
    _live.clear()
    started = time.time()
    await _state_upsert(
        {"started_at": "NOW()"},
        status="running", trigger_by=trigger,
        finished_at=None, duration_sec=None, stats_json=None, last_error=None,
    )

    src_cfg = get_source_config()
    stats: Dict[str, Dict[str, int]] = {}
    logger.info(f"[std-sync] 开始（触发：{trigger}）→ {src_cfg.host}:{src_cfg.port}/{src_cfg.database}")
    src_conn: Optional[aiomysql.Connection] = None
    try:
        src_conn = await _open_mysql_conn(src_cfg)
        for spec in TABLES:
            stats[spec.name] = await _sync_table(spec, src_conn)
        await _state_upsert(
            {"finished_at": "NOW()"},
            status="done",
            duration_sec=round(time.time() - started, 1),
            stats_json=json.dumps(stats, ensure_ascii=False), last_error=None,
        )
        logger.info(f"[std-sync] 全部完成，总耗时 {time.time() - started:.1f}s：{stats}")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[std-sync] 同步失败：{e}")
        await _state_upsert(
            {"finished_at": "NOW()"},
            status="error",
            duration_sec=round(time.time() - started, 1),
            stats_json=json.dumps(stats, ensure_ascii=False) if stats else None,
            last_error=str(e)[:2000],
        )
    finally:
        if src_conn is not None:
            try:
                src_conn.close()
            except Exception:  # noqa: BLE001
                pass
        _running = False
        _live.clear()
