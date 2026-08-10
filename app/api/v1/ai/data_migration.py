"""
数据迁移 API

提供从外部 MySQL 数据库导入数据的接口。
两个接口均立即返回，实际导入在后台执行，进度通过日志输出。
MySQL 采用服务端游标（aiomysql.SSDictCursor）分批拉取，避免一次性将大表加载进内存。
"""

import asyncio
import json
import re
import threading
import time
from datetime import date
from typing import List, Optional

import aiomysql
import httpx
from fastapi import APIRouter, BackgroundTasks
from loguru import logger
from pydantic import BaseModel

from tortoise import connections
from app.models.standard.base_info import StandardBaseInfo
from app.models.standard.jgh_pdf import StandardJghPdf, StandardJghPdfChapter, StandardJghPdfTable, StandardJghPdfFormula
from app.schemas.base import Success, Fail
from app.settings import APP_SETTINGS as settings

router = APIRouter(prefix="/data-migration", tags=["数据迁移"])

# 每次从 MySQL 拉取的批次大小
BATCH_SIZE = 5000

# Snowflake ID 生成器（epoch: 2020-01-01）
_SNOWFLAKE_EPOCH = 1577836800000
_snowflake_seq = 0
_snowflake_last_ms = -1
_snowflake_lock = threading.Lock()


def _next_id() -> int:
    global _snowflake_seq, _snowflake_last_ms
    with _snowflake_lock:
        ms = int(time.time() * 1000)
        if ms == _snowflake_last_ms:
            _snowflake_seq = (_snowflake_seq + 1) & 0xFFF
            if _snowflake_seq == 0:
                while ms <= _snowflake_last_ms:
                    ms = int(time.time() * 1000)
        else:
            _snowflake_seq = 0
        _snowflake_last_ms = ms
        return ((ms - _SNOWFLAKE_EPOCH) << 22) | _snowflake_seq


class MySQLConfig(BaseModel):
    host: str
    port: int = 3306
    user: str
    password: str
    database: str


class StructuredMigrationConfig(MySQLConfig):
    standard_nos: List[str]


class SyncImgWordConfig(BaseModel):
    standard_nos: Optional[List[str]] = None  # 空则全量
    concurrency: int = 8                       # 同时并发请求数


class SpecificMigrationConfig(MySQLConfig):
    standard_nos: List[str]


async def _open_mysql_conn(config: MySQLConfig) -> aiomysql.Connection:
    """异步打开 MySQL 连接（独立连接，迁移任务专用，不复用全局池）。"""
    return await aiomysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        db=config.database,
        charset="utf8mb4",
        connect_timeout=10,
        autocommit=True,
    )


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


_FILE_NAME_RE = re.compile(r'([^/]+\.(?:jpg|png))', re.IGNORECASE)


def _extract_file_name(image: str | None) -> str | None:
    """从 image 字段提取文件名：
    - 空值返回 None
    - 查找 .jpg 或 .png，向前截取直到 /，提取文件名
    - 示例：
      - /oss/privateDomain/20230917/2022/xxx.png → xxx.png
      - <img src="https://.../image/xxx.jpg" ...> → xxx.jpg
    """
    if not image:
        return None
    m = _FILE_NAME_RE.search(image)
    if m:
        return m.group(1)
    return None


def _trunc(value, max_len: int) -> str | None:
    """截取字符串到 max_len，超长时截断（避免 ORM CharField 长度校验报错）"""
    if value is None:
        return None
    s = str(value)
    return s[:max_len] if len(s) > max_len else s


def _coerce_bool(value) -> bool:
    """将源端 deleted 字段统一转成 bool。
    源端可能是 MySQL TINYINT/INT（返回 int 0/1）或 BIT（aiomysql 返回 bytes 如 b'\\x01'）。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, bytes):
        try:
            return int.from_bytes(value, "little") != 0
        except Exception:
            return bool(value)
    if isinstance(value, str):
        return value.strip() in ("1", "true", "True", "TRUE", "yes", "Yes")
    return bool(value)


def _row_to_base_info(row: dict) -> StandardBaseInfo | None:
    row_id = _trunc(row.get("id"), 64)
    if not row_id:
        return None
    return StandardBaseInfo(
        id=row_id,
        standard_no=_trunc(row.get("standard_no"), 200),
        cname=row.get("cname"),
        ename=row.get("ename"),
        use_range=row.get("use_range"),
        intl_cat=_trunc(row.get("intl_cat"), 200),
        nat_cat=_trunc(row.get("nat_cat"), 200),
        std_domain=_trunc(row.get("std_domain"), 200),
        std_field=_trunc(row.get("std_field"), 200),
        std_year=_trunc(row.get("std_year"), 10),
        std_obj=row.get("std_obj"),
        issue_date=_parse_date(row.get("issue_date")),
        act_date=_parse_date(row.get("act_date")),
        annul_date=_parse_date(row.get("annul_date")),
        approval_unit=row.get("approval_unit"),
        put_unit=row.get("put_unit"),
        lead_unit=row.get("lead_unit"),
        draft_unit_main=row.get("draft_unit_main"),
        draft_unit=row.get("draft_unit"),
        draft_staff=row.get("draft_staff"),
        chief_unit=row.get("chief_unit"),
        mgr_dept=row.get("mgr_dept"),
        is_secret=bool(row.get("is_secret")) if row.get("is_secret") is not None else None,
        std_nature=_trunc(row.get("std_nature"), 50),
        mandatory_clause=row.get("mandatory_clause"),
        patent_info=row.get("patent_info"),
        state=_trunc(row.get("state"), 50),
        security_level=_trunc(row.get("security_level"), 50),
        release_history=row.get("release_history"),
        release_std_no=_trunc(row.get("release_std_no"), 200),
        replace_description=row.get("replace_description"),
        replace_stds=row.get("replace_stds"),
        target_stds=row.get("target_stds"),
        adopt_situation=row.get("adopt_situation"),
        adopt_std_no=_trunc(row.get("adopt_std_no"), 200),
        adopt_text=row.get("adopt_text"),
        adopt_level=_trunc(row.get("adopt_level"), 50),
        adopt_type=_trunc(row.get("adopt_type"), 50),
        adopt_no=_trunc(row.get("adopt_no"), 200),
        adopt_name=row.get("adopt_name"),
        gjb_no=_trunc(row.get("gjb_no"), 200),
        std_type=_trunc(row.get("std_type"), 50),
        industry=_trunc(row.get("industry"), 200),
        remark=row.get("remark"),
        creator=_trunc(row.get("creator"), 64),
        updater=_trunc(row.get("updater"), 64),
        deleted=_coerce_bool(row.get("deleted")),
    )


async def _run_import_from_mysql(config: MySQLConfig) -> None:
    """
    后台任务：从 MySQL 分批读取三张表并全量写入本地数据库。
    使用 aiomysql.SSDictCursor 流式光标，避免一次性把整张表灌进内存。
    """
    logger.info("[数据迁移] 开始连接 MySQL ...")
    try:
        conn = await _open_mysql_conn(config)
    except Exception as e:
        logger.error(f"[数据迁移] 连接 MySQL 失败: {e}")
        return

    try:
        # ===== standard_base_info =====
        logger.info("[数据迁移] 清空 standard_base_info ...")
        await StandardBaseInfo.all().delete()
        logger.info("[数据迁移] standard_base_info 清空完成")
        async with conn.cursor(aiomysql.SSDictCursor) as cur:
            await cur.execute(
                "SELECT * FROM standard_base_info WHERE deleted = 0 OR deleted IS NULL"
            )
            base_count = 0
            while True:
                rows = await cur.fetchmany(BATCH_SIZE)
                if not rows:
                    break
                objs = [o for r in rows if (o := _row_to_base_info(r)) is not None]
                if objs:
                    await StandardBaseInfo.bulk_create(objs)
                base_count += len(objs)
                logger.info(f"[数据迁移] standard_base_info 已写入 {base_count} 条 ...")
        logger.info(f"[数据迁移] standard_base_info 完成，共 {base_count} 条")

        # ===== standard_jgh_pdf =====
        logger.info("[数据迁移] 清空 standard_jgh_pdf ...")
        await StandardJghPdf.all().delete()

        async with conn.cursor(aiomysql.SSDictCursor) as cur:
            await cur.execute("SELECT * FROM standard_jgh_pdf")
            pdf_count = 0
            while True:
                rows = await cur.fetchmany(BATCH_SIZE)
                if not rows:
                    break
                objs = [
                    StandardJghPdf(
                        id=r["id"],
                        main_task_id=r.get("main_task_id"),
                        file_uuid=_trunc(r.get("file_uuid"), 64),
                        name=_trunc(r.get("name"), 255),
                        standard_no=_trunc(r.get("standard_no"), 255),
                        cname=r.get("cname"),
                        deleted=_coerce_bool(r.get("deleted")),
                    )
                    for r in rows if r.get("id")
                ]
                if objs:
                    await StandardJghPdf.bulk_create(objs)
                pdf_count += len(objs)
                logger.info(f"[数据迁移] standard_jgh_pdf 已写入 {pdf_count} 条 ...")
        logger.info(f"[数据迁移] standard_jgh_pdf 完成，共 {pdf_count} 条")

        # ===== standard_jgh_pdf_chapter =====
        logger.info("[数据迁移] 清空 standard_jgh_pdf_chapter ...")
        db_conn = connections.get("conn_standard")
        await db_conn.execute_query("TRUNCATE TABLE standard_jgh_pdf_chapter")
        logger.info("[数据迁移] 清空 standard_jgh_pdf_chapter 完成")
        async with conn.cursor(aiomysql.DictCursor) as cur:
            chapter_count = 0
            last_id = 0
            while True:
                await cur.execute(
                    "SELECT * FROM standard_jgh_pdf_chapter WHERE id > %s ORDER BY id LIMIT %s",
                    (last_id, BATCH_SIZE),
                )
                rows = await cur.fetchall()
                if not rows:
                    break
                objs = [
                    StandardJghPdfChapter(
                        id=r["id"],
                        main_task_id=r.get("main_task_id"),
                        title=_trunc(r.get("title"), 500),
                        title_no=_trunc(r.get("title_no"), 100),
                        page=r.get("page"),
                        word=r.get("word"),
                        deleted=_coerce_bool(r.get("deleted")),
                    )
                    for r in rows if r.get("id")
                ]
                if objs:
                    await StandardJghPdfChapter.bulk_create(objs)
                chapter_count += len(objs)
                last_id = rows[-1]["id"]
                logger.info(f"[数据迁移] standard_jgh_pdf_chapter 已写入 {chapter_count} 条 ...")
        logger.info(f"[数据迁移] standard_jgh_pdf_chapter 完成，共 {chapter_count} 条")

        # ===== standard_jgh_pdf_table =====
        logger.info("[数据迁移] 开始迁移 standard_jgh_pdf_table（按 id 去重，已存在则跳过）...")
        existing_table_ids: set[int] = {
            obj.id async for obj in StandardJghPdfTable.all().only("id")
        }
        async with conn.cursor(aiomysql.SSDictCursor) as cur:
            await cur.execute("SELECT * FROM standard_jgh_pdf_table")
            table_count = 0
            table_skip = 0
            while True:
                rows = await cur.fetchmany(BATCH_SIZE)
                if not rows:
                    break
                new_objs = []
                for r in rows:
                    rid = r.get("id")
                    if not rid or rid in existing_table_ids:
                        table_skip += 1
                        continue
                    existing_table_ids.add(rid)
                    new_objs.append(
                        StandardJghPdfTable(
                            id=rid,
                            main_task_id=r.get("main_task_id"),
                            data_uuid=_trunc(r.get("data_uuid"), 64),
                            title=r.get("title"),
                            word=r.get("word"),
                            image=r.get("image"),
                            file_name=_extract_file_name(r.get("image")),
                            page=int(r["page"]) if r.get("page") is not None else None,
                            start=int(r["start"]) if r.get("start") is not None else None,
                            end=int(r["end"]) if r.get("end") is not None else None,
                            deleted=_coerce_bool(r.get("deleted")),
                        )
                    )
                if new_objs:
                    await StandardJghPdfTable.bulk_create(new_objs)
                table_count += len(new_objs)
                logger.info(f"[数据迁移] standard_jgh_pdf_table 已写入 {table_count} 条，跳过 {table_skip} 条 ...")
        logger.info(f"[数据迁移] standard_jgh_pdf_table 完成，写入 {table_count} 条，跳过 {table_skip} 条")

        # ===== standard_jgh_pdf_formula =====
        logger.info("[数据迁移] 开始迁移 standard_jgh_pdf_formula（按 id 去重，已存在则跳过）...")
        existing_formula_ids: set[int] = {
            obj.id async for obj in StandardJghPdfFormula.all().only("id")
        }
        async with conn.cursor(aiomysql.SSDictCursor) as cur:
            await cur.execute("SELECT * FROM standard_jgh_pdf_formula")
            formula_count = 0
            formula_skip = 0
            while True:
                rows = await cur.fetchmany(BATCH_SIZE)
                if not rows:
                    break
                new_objs = []
                for r in rows:
                    rid = r.get("id")
                    if not rid or rid in existing_formula_ids:
                        formula_skip += 1
                        continue
                    existing_formula_ids.add(rid)
                    new_objs.append(
                        StandardJghPdfFormula(
                            id=rid,
                            main_task_id=r.get("main_task_id"),
                            data_uuid=_trunc(r.get("data_uuid"), 64),
                            title=r.get("title"),
                            word=r.get("word"),
                            image=r.get("image"),
                            file_name=_extract_file_name(r.get("image")),
                            page=int(r["page"]) if r.get("page") is not None else None,
                            start=int(r["start"]) if r.get("start") is not None else None,
                            end=int(r["end"]) if r.get("end") is not None else None,
                            deleted=_coerce_bool(r.get("deleted")),
                        )
                    )
                if new_objs:
                    await StandardJghPdfFormula.bulk_create(new_objs)
                formula_count += len(new_objs)
                logger.info(f"[数据迁移] standard_jgh_pdf_formula 已写入 {formula_count} 条，跳过 {formula_skip} 条 ...")
        logger.info(f"[数据迁移] standard_jgh_pdf_formula 完成，写入 {formula_count} 条，跳过 {formula_skip} 条")

        logger.info(
            f"[数据迁移] 全部完成：base_info={base_count}，pdf={pdf_count}，chapter={chapter_count}，"
            f"table={table_count}，formula={formula_count}"
        )

    except Exception as e:
        logger.error(f"[数据迁移] 导入过程出错: {e}", exc_info=True)
    finally:
        conn.close()


async def _run_import_table_and_formula(config: MySQLConfig) -> None:
    """
    后台任务：从 MySQL 只拉取 standard_jgh_pdf_table 和 standard_jgh_pdf_formula 两张表。
    使用 aiomysql.SSDictCursor 流式光标，按 id 去重（已存在则跳过）。
    导入完成后，对 word 为空的记录调用第三方 API 获取文本并填充。
    """
    logger.info("[数据迁移] 开始连接 MySQL（仅 table + formula）...")
    try:
        conn = await _open_mysql_conn(config)
    except Exception as e:
        logger.error(f"[数据迁移] 连接 MySQL 失败: {e}")
        return

    try:
        # ===== standard_jgh_pdf_table =====
        logger.info("[数据迁移] 开始迁移 standard_jgh_pdf_table（按 id 去重，已存在则跳过）...")
        existing_table_ids: set[int] = {
            obj.id async for obj in StandardJghPdfTable.all().only("id")
        }
        async with conn.cursor(aiomysql.SSDictCursor) as cur:
            await cur.execute("SELECT * FROM standard_jgh_pdf_table")
            table_count = 0
            table_skip = 0
            while True:
                rows = await cur.fetchmany(BATCH_SIZE)
                if not rows:
                    break
                new_objs = []
                for r in rows:
                    rid = r.get("id")
                    if not rid or rid in existing_table_ids:
                        table_skip += 1
                        continue
                    existing_table_ids.add(rid)
                    new_objs.append(
                        StandardJghPdfTable(
                            id=rid,
                            main_task_id=r.get("main_task_id"),
                            data_uuid=_trunc(r.get("data_uuid"), 64),
                            title=r.get("title"),
                            word=r.get("word"),
                            image=r.get("image"),
                            file_name=_extract_file_name(r.get("image")),
                            page=int(r["page"]) if r.get("page") is not None else None,
                            start=int(r["start"]) if r.get("start") is not None else None,
                            end=int(r["end"]) if r.get("end") is not None else None,
                            deleted=_coerce_bool(r.get("deleted")),
                        )
                    )
                if new_objs:
                    await StandardJghPdfTable.bulk_create(new_objs)
                table_count += len(new_objs)
                logger.info(f"[数据迁移] standard_jgh_pdf_table 已写入 {table_count} 条，跳过 {table_skip} 条 ...")
        logger.info(f"[数据迁移] standard_jgh_pdf_table 完成，写入 {table_count} 条，跳过 {table_skip} 条")

        # ===== standard_jgh_pdf_formula =====
        logger.info("[数据迁移] 开始迁移 standard_jgh_pdf_formula（按 id 去重，已存在则跳过）...")
        existing_formula_ids: set[int] = {
            obj.id async for obj in StandardJghPdfFormula.all().only("id")
        }
        async with conn.cursor(aiomysql.SSDictCursor) as cur:
            await cur.execute("SELECT * FROM standard_jgh_pdf_formula")
            formula_count = 0
            formula_skip = 0
            while True:
                rows = await cur.fetchmany(BATCH_SIZE)
                if not rows:
                    break
                new_objs = []
                for r in rows:
                    rid = r.get("id")
                    if not rid or rid in existing_formula_ids:
                        formula_skip += 1
                        continue
                    existing_formula_ids.add(rid)
                    new_objs.append(
                        StandardJghPdfFormula(
                            id=rid,
                            main_task_id=r.get("main_task_id"),
                            data_uuid=_trunc(r.get("data_uuid"), 64),
                            title=r.get("title"),
                            word=r.get("word"),
                            image=r.get("image"),
                            file_name=_extract_file_name(r.get("image")),
                            page=int(r["page"]) if r.get("page") is not None else None,
                            start=int(r["start"]) if r.get("start") is not None else None,
                            end=int(r["end"]) if r.get("end") is not None else None,
                            deleted=_coerce_bool(r.get("deleted")),
                        )
                    )
                if new_objs:
                    await StandardJghPdfFormula.bulk_create(new_objs)
                formula_count += len(new_objs)
                logger.info(f"[数据迁移] standard_jgh_pdf_formula 已写入 {formula_count} 条，跳过 {formula_skip} 条 ...")
        logger.info(f"[数据迁移] standard_jgh_pdf_formula 完成，写入 {formula_count} 条，跳过 {formula_skip} 条")

        # ===== 调用第三方 API 填充 word 字段（已暂时禁用）=====
        # logger.info("[数据迁移] 开始调用第三方 API 填充 word 字段...")
        # await _sync_img_word_for_imported()

        logger.info(
            f"[数据迁移] table+formula 迁移完成：table={table_count}，formula={formula_count}"
        )

    except Exception as e:
        logger.error(f"[数据迁移] 导入过程出错: {e}", exc_info=True)
    finally:
        conn.close()


async def _sync_img_word_for_imported() -> None:
    """
    对 standard_jgh_pdf_table / standard_jgh_pdf_formula 中 word 为 NULL 或空字符串
    的记录，调用第三方 /get-config-item 接口并将返回值写入 word 字段。
    """
    semaphore = asyncio.Semaphore(8)  # 并发数 8

    pdf_records = await StandardJghPdf.all()
    if not pdf_records:
        logger.info("[SyncImgWord] 未找到任何标准，跳过 word 同步")
        return

    logger.info(f"[SyncImgWord] 开始处理 {len(pdf_records)} 个标准的图片/表格文本同步")

    total_ok = 0
    total_empty = 0
    total_skip = 0

    for pdf in pdf_records:
        main_task_id = pdf.main_task_id
        if not main_task_id:
            logger.debug(f"[SyncImgWord] {pdf.standard_no} 无 main_task_id，跳过")
            continue

        pdf_name = pdf.name or ""
        storage_path = pdf_name.rsplit(".", 1)[0] if "." in pdf_name else pdf_name
        if not storage_path:
            logger.debug(f"[SyncImgWord] {pdf.standard_no} 无 storage_path，跳过")
            continue

        # 查找 word 为 NULL 或空字符串的记录
        table_records = await StandardJghPdfTable.filter(
            main_task_id=main_task_id
        ).filter(
            word__isnull=True
        ).all() + await StandardJghPdfTable.filter(
            main_task_id=main_task_id, word=""
        ).all()

        formula_records = await StandardJghPdfFormula.filter(
            main_task_id=main_task_id
        ).filter(
            word__isnull=True
        ).all() + await StandardJghPdfFormula.filter(
            main_task_id=main_task_id, word=""
        ).all()

        all_records: list[tuple] = (
            [("table", r) for r in table_records]
            + [("formula", r) for r in formula_records]
        )

        if not all_records:
            logger.debug(f"[SyncImgWord] {pdf.standard_no} 无需处理的记录，跳过")
            total_skip += 1
            continue

        logger.info(
            f"[SyncImgWord] {pdf.standard_no} storage={storage_path!r}，"
            f"待处理 table={len(table_records)} formula={len(formula_records)}"
        )

        async def _process_one(kind: str, record) -> None:
            nonlocal total_ok, total_empty
            fn = (record.file_name or "").split("/")[-1].split("?")[0]
            if not fn:
                logger.debug(f"[SyncImgWord] id={record.id} file_name 为空，跳过")
                return
            async with semaphore:
                word = await _fetch_img_word_from_api(storage_path, fn)
            if word:
                record.word = word
                await record.save(update_fields=["word"])
                total_ok += 1
                logger.debug(f"[SyncImgWord] {kind} id={record.id} {fn} 写入成功")
            else:
                total_empty += 1
                logger.debug(f"[SyncImgWord] {kind} id={record.id} {fn} 接口返回空，跳过")

        await asyncio.gather(*[_process_one(kind, rec) for kind, rec in all_records])

    logger.info(
        f"[SyncImgWord] word 同步完成：成功写入={total_ok}，接口返回空={total_empty}，"
        f"标准无待处理记录={total_skip}"
    )


@router.post("/import-table-formula-from-mysql", summary="从 MySQL 只导入表格和公式数据")
async def import_table_formula_from_mysql(config: MySQLConfig, background_tasks: BackgroundTasks):
    """
    从指定 MySQL 数据库只导入 standard_jgh_pdf_table 和 standard_jgh_pdf_formula 两张表。
    按 id 去重，已存在的记录会被跳过。
    接口立即返回，实际导入在后台执行，进度请查看服务日志。
    """
    try:
        # 先验证连接是否可用，失败则立即报错
        test_conn = await _open_mysql_conn(config)
        test_conn.close()
    except Exception as e:
        return Fail(msg=f"MySQL 连接失败: {str(e)}")

    background_tasks.add_task(_run_import_table_and_formula, config)
    return Success(msg="表格和公式导入任务已在后台启动，请查看服务日志了解进度")


async def _run_import_structured_data(config: StructuredMigrationConfig) -> None:
    """
    后台任务：从 MySQL standard_ai_doc 表按标准号分批读取，解析 json_data 写入本地。
    """
    logger.info(f"[结构化迁移] 开始连接 MySQL，待处理标准号 {len(config.standard_nos)} 个 ...")
    try:
        conn = await _open_mysql_conn(config)
    except Exception as e:
        logger.error(f"[结构化迁移] 连接 MySQL 失败: {e}")
        return

    try:
        placeholders = ",".join(["%s"] * len(config.standard_nos))
        sql = (
            f"SELECT a.main_task_id, a.json_data, p.standard_no, p.id AS pdf_id, p.cname AS pdf_cname"
            f" FROM standard_ai_doc a"
            f" INNER JOIN standard_jgh_pdf p ON a.main_task_id = p.main_task_id"
            f" WHERE p.standard_no IN ({placeholders})"
            f" AND (a.deleted = 0 OR a.deleted IS NULL)"
        )
        std_nos = list(config.standard_nos)

        async with conn.cursor(aiomysql.SSDictCursor) as cur:
            await cur.execute(sql, std_nos)

            imported_count = 0
            chapter_count = 0
            row_index = 0

            while True:
                rows = await cur.fetchmany(BATCH_SIZE)
                if not rows:
                    break

                for row in rows:
                    row_index += 1
                    standard_no: str = row.get("standard_no", "")
                    json_data_raw = row.get("json_data")

                    if not standard_no or not json_data_raw:
                        logger.warning(f"[结构化迁移] [{row_index}] 跳过空数据：{standard_no!r}")
                        continue

                    try:
                        json_data = json.loads(json_data_raw) if isinstance(json_data_raw, str) else json_data_raw
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning(f"[结构化迁移] [{row_index}] {standard_no} json_data 解析失败: {e}")
                        continue

                    if not isinstance(json_data, list):
                        logger.warning(f"[结构化迁移] [{row_index}] {standard_no} json_data 格式不支持")
                        continue

                    cname = None
                    for section in json_data:
                        if section.get("name") == "封面信息":
                            for group in section.get("struts", []):
                                for item in group:
                                    if item.get("label") == "标准中文名称":
                                        cname = item.get("word")
                                        break
                            break

                    chapters_data = []
                    for section in json_data:
                        if section.get("name") == "章节":
                            for group in section.get("struts", []):
                                item_map = {it.get("label"): it.get("word") for it in group if isinstance(it, dict)}
                                chapters_data.append({
                                    "title": item_map.get("章节标题"),
                                    "title_no": item_map.get("章节编号"),
                                    "page": item_map.get("页码"),
                                    "word": item_map.get("章节内容"),
                                })
                            break

                    pdf_record = await StandardJghPdf.filter(standard_no=standard_no).first()
                    if pdf_record:
                        if cname and not pdf_record.cname:
                            pdf_record.cname = cname
                            await pdf_record.save()
                        main_task_id = pdf_record.main_task_id or pdf_record.id
                    else:
                        pdf_record = await StandardJghPdf.create(
                            id=row.get("pdf_id"),
                            main_task_id=row.get("main_task_id"),
                            standard_no=standard_no,
                            cname=cname,
                        )
                        main_task_id = pdf_record.main_task_id or pdf_record.id

                    await StandardJghPdfChapter.filter(main_task_id=main_task_id).delete()

                    chapter_objs = [
                        StandardJghPdfChapter(
                            id=_next_id(),
                            main_task_id=main_task_id,
                            title=_trunc(c.get("title"), 500),
                            title_no=_trunc(c.get("title_no"), 100),
                            page=int(c["page"]) if c.get("page") and str(c["page"]).isdigit() else None,
                            word=c.get("word"),
                        )
                        for c in chapters_data if isinstance(c, dict)
                    ]
                    if chapter_objs:
                        await StandardJghPdfChapter.bulk_create(chapter_objs)

                    this_count = len(chapter_objs)
                    chapter_count += this_count
                    imported_count += 1
                    logger.info(
                        f"[结构化迁移] [{row_index}] {standard_no} 完成，章节 {this_count} 条"
                    )

        logger.info(
            f"[结构化迁移] 全部完成：处理标准 {imported_count} 个，导入章节 {chapter_count} 条"
        )

    except Exception as e:
        logger.error(f"[结构化迁移] 导入过程出错: {e}", exc_info=True)
    finally:
        conn.close()


async def _fetch_img_word_from_api(storage_path: str, filename: str) -> str:
    """调用第三方 /get-config-item 接口获取图片/表格文本。"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.JGH_PDF_API_BASE}/standard/jgh-pdf/get-config-item",
                params={"storagePath": storage_path, "imgPath": f"images/{filename}"},
                headers={"X-App-Id": settings.JGH_PDF_APP_ID},
                timeout=30.0,
            )
            data = resp.json()
            if data.get("code") == 403:
                logger.error("[SyncImgWord] AppId 无效，请检查 JGH_PDF_APP_ID 配置")
                return ""
            if data.get("code") != 0:
                logger.warning(f"[SyncImgWord] {filename} API错误: {data.get('msg')}")
                return ""
            obj = data.get("data") or {}
            if obj.get("type") == "none":
                return ""
            return (obj.get("table_body") or "").strip()
    except Exception as e:
        logger.warning(f"[SyncImgWord] {filename} 请求异常: {e}")
        return ""


async def _run_sync_img_word(config: SyncImgWordConfig) -> None:
    """
    后台任务：对 standard_jgh_pdf_table / standard_jgh_pdf_formula 中 word 为 NULL 或空字符串
    的记录，调用第三方 /get-config-item 接口并将返回值写入 word 字段。
    """
    semaphore = asyncio.Semaphore(config.concurrency)

    # 1. 确定要处理的标准范围
    if config.standard_nos:
        pdf_records = await StandardJghPdf.filter(standard_no__in=config.standard_nos).all()
    else:
        pdf_records = await StandardJghPdf.all()

    if not pdf_records:
        logger.info("[SyncImgWord] 没有找到需要处理的标准")
        return

    logger.info(f"[SyncImgWord] 开始处理 {len(pdf_records)} 个标准的图片/表格文本同步")

    total_ok = 0
    total_empty = 0
    total_skip = 0
    total_err = 0

    for pdf in pdf_records:
        main_task_id = pdf.main_task_id
        if not main_task_id:
            logger.debug(f"[SyncImgWord] {pdf.standard_no} 无 main_task_id，跳过")
            continue

        pdf_name = pdf.name or ""
        storage_path = pdf_name.rsplit(".", 1)[0] if "." in pdf_name else pdf_name
        if not storage_path:
            logger.debug(f"[SyncImgWord] {pdf.standard_no} 无 storage_path，跳过")
            continue

        # 查找 word 为 NULL 或空字符串的记录
        table_records = await StandardJghPdfTable.filter(
            main_task_id=main_task_id
        ).filter(
            word__isnull=True
        ).all() + await StandardJghPdfTable.filter(
            main_task_id=main_task_id, word=""
        ).all()

        formula_records = await StandardJghPdfFormula.filter(
            main_task_id=main_task_id
        ).filter(
            word__isnull=True
        ).all() + await StandardJghPdfFormula.filter(
            main_task_id=main_task_id, word=""
        ).all()

        all_records: list[tuple] = (
            [("table", r) for r in table_records]
            + [("formula", r) for r in formula_records]
        )

        if not all_records:
            logger.debug(f"[SyncImgWord] {pdf.standard_no} 无需处理的记录，跳过")
            total_skip += 1
            continue

        logger.info(
            f"[SyncImgWord] {pdf.standard_no} storage={storage_path!r}，"
            f"待处理 table={len(table_records)} formula={len(formula_records)}"
        )

        async def _process_one(kind: str, record) -> None:
            nonlocal total_ok, total_empty, total_err
            fn = (record.file_name or "").split("/")[-1].split("?")[0]
            if not fn:
                logger.debug(f"[SyncImgWord] id={record.id} file_name 为空，跳过")
                return
            async with semaphore:
                word = await _fetch_img_word_from_api(storage_path, fn)
            if word:
                record.word = word
                await record.save(update_fields=["word"])
                total_ok += 1
                logger.debug(f"[SyncImgWord] {kind} id={record.id} {fn} 写入成功")
            else:
                total_empty += 1
                logger.debug(f"[SyncImgWord] {kind} id={record.id} {fn} 接口返回空，跳过")

        await asyncio.gather(*[_process_one(kind, rec) for kind, rec in all_records])

    logger.info(
        f"[SyncImgWord] 完成：成功写入={total_ok}，接口返回空={total_empty}，"
        f"标准无待处理记录={total_skip}"
    )


async def _bulk_delete_then_create(model, objs: list) -> tuple[int, int]:
    """按主键 id 先删后插，确保 deleted 等字段回到默认值（避免本地软删行残留）。"""
    if not objs:
        return 0, 0

    ids = [o.id for o in objs]
    deleted = await model.filter(id__in=ids).delete() or 0
    await model.bulk_create(objs, batch_size=BATCH_SIZE)

    return deleted, len(objs)


async def _migrate_specific_base_info(conn: aiomysql.Connection, standard_nos: list[str]) -> tuple[int, int]:
    """按标准号迁入 standard_base_info，存在则更新，不存在则新增。"""
    logger.info(f"[指定标准迁移] 开始迁入 standard_base_info，标准号 {len(standard_nos)} 个 ...")

    placeholders = ",".join(["%s"] * len(standard_nos))
    sql = (
        f"SELECT * FROM standard_base_info"
        f" WHERE standard_no IN ({placeholders})"
        f" AND (deleted = 0 OR deleted IS NULL)"
    )

    source_objs: list[StandardBaseInfo] = []
    async with conn.cursor(aiomysql.SSDictCursor) as cur:
        await cur.execute(sql, standard_nos)
        while True:
            rows = await cur.fetchmany(BATCH_SIZE)
            if not rows:
                break
            for r in rows:
                obj = _row_to_base_info(r)
                if obj:
                    source_objs.append(obj)

    if not source_objs:
        logger.info("[指定标准迁移] standard_base_info 无源数据")
        return 0, 0

    deleted, created = await _bulk_delete_then_create(StandardBaseInfo, source_objs)
    logger.info(
        f"[指定标准迁移] standard_base_info 完成，删除 {deleted} 条，新增 {created} 条"
    )
    return deleted, created


async def _migrate_specific_pdf(
    conn: aiomysql.Connection, standard_nos: list[str]
) -> tuple[int, int, dict[int | str, int | str]]:
    """按标准号迁入 standard_jgh_pdf，并返回 source_main_task_id -> local_main_task_id 映射。"""
    logger.info(f"[指定标准迁移] 开始迁入 standard_jgh_pdf ...")

    placeholders = ",".join(["%s"] * len(standard_nos))
    sql = f"SELECT * FROM standard_jgh_pdf WHERE standard_no IN ({placeholders})"

    source_rows: list[dict] = []
    async with conn.cursor(aiomysql.SSDictCursor) as cur:
        await cur.execute(sql, standard_nos)
        while True:
            rows = await cur.fetchmany(BATCH_SIZE)
            if not rows:
                break
            source_rows.extend([r for r in rows if r.get("id")])

    if not source_rows:
        logger.info("[指定标准迁移] standard_jgh_pdf 无源数据")
        return 0, 0, {}

    source_objs: list[StandardJghPdf] = []
    main_task_id_map: dict[int | str, int | str] = {}
    for r in source_rows:
        source_id = r["id"]
        source_main_task_id = r.get("main_task_id")
        local_main_task_id = source_main_task_id if source_main_task_id is not None else source_id
        source_key = source_main_task_id if source_main_task_id is not None else source_id
        main_task_id_map[source_key] = local_main_task_id
        source_objs.append(
            StandardJghPdf(
                id=source_id,
                main_task_id=local_main_task_id,
                file_uuid=_trunc(r.get("file_uuid"), 64),
                name=_trunc(r.get("name"), 255),
                standard_no=r.get("standard_no"),
                cname=r.get("cname"),
                deleted=_coerce_bool(r.get("deleted")),
            )
        )

    deleted, created = await _bulk_delete_then_create(StandardJghPdf, source_objs)
    logger.info(
        f"[指定标准迁移] standard_jgh_pdf 完成，删除 {deleted} 条，新增 {created} 条"
    )
    return deleted, created, main_task_id_map


async def _migrate_specific_chapters(
    conn: aiomysql.Connection,
    main_task_id_map: dict[int | str, int | str],
) -> tuple[int, int]:
    """按 main_task_id 迁入 standard_jgh_pdf_chapter。"""
    if not main_task_id_map:
        return 0, 0

    source_keys = list(main_task_id_map.keys())
    placeholders = ",".join(["%s"] * len(source_keys))
    sql = f"SELECT * FROM standard_jgh_pdf_chapter WHERE main_task_id IN ({placeholders})"

    source_objs: list[StandardJghPdfChapter] = []
    async with conn.cursor(aiomysql.SSDictCursor) as cur:
        await cur.execute(sql, source_keys)
        while True:
            rows = await cur.fetchmany(BATCH_SIZE)
            if not rows:
                break
            for r in rows:
                if not r.get("id"):
                    continue
                source_objs.append(
                    StandardJghPdfChapter(
                        id=r["id"],
                        main_task_id=main_task_id_map.get(r.get("main_task_id")),
                        title=_trunc(r.get("title"), 500),
                        title_no=_trunc(r.get("title_no"), 100),
                        page=int(r["page"]) if r.get("page") is not None else None,
                        word=r.get("word"),
                        deleted=_coerce_bool(r.get("deleted")),
                    )
                )

    deleted, created = await _bulk_delete_then_create(StandardJghPdfChapter, source_objs)
    logger.info(
        f"[指定标准迁移] standard_jgh_pdf_chapter 完成，删除 {deleted} 条，新增 {created} 条"
    )
    return deleted, created


async def _migrate_specific_tables(
    conn: aiomysql.Connection,
    main_task_id_map: dict[int | str, int | str],
) -> tuple[int, int]:
    """按 main_task_id 迁入 standard_jgh_pdf_table。"""
    if not main_task_id_map:
        return 0, 0

    source_keys = list(main_task_id_map.keys())
    placeholders = ",".join(["%s"] * len(source_keys))
    sql = f"SELECT * FROM standard_jgh_pdf_table WHERE main_task_id IN ({placeholders})"

    source_objs: list[StandardJghPdfTable] = []
    async with conn.cursor(aiomysql.SSDictCursor) as cur:
        await cur.execute(sql, source_keys)
        while True:
            rows = await cur.fetchmany(BATCH_SIZE)
            if not rows:
                break
            for r in rows:
                if not r.get("id"):
                    continue
                source_objs.append(
                    StandardJghPdfTable(
                        id=r["id"],
                        main_task_id=main_task_id_map.get(r.get("main_task_id")),
                        data_uuid=_trunc(r.get("data_uuid"), 64),
                        title=r.get("title"),
                        word=r.get("word"),
                        image=r.get("image"),
                        file_name=_extract_file_name(r.get("image")),
                        page=int(r["page"]) if r.get("page") is not None else None,
                        start=int(r["start"]) if r.get("start") is not None else None,
                        end=int(r["end"]) if r.get("end") is not None else None,
                        deleted=_coerce_bool(r.get("deleted")),
                    )
                )

    deleted, created = await _bulk_delete_then_create(StandardJghPdfTable, source_objs)
    logger.info(
        f"[指定标准迁移] standard_jgh_pdf_table 完成，删除 {deleted} 条，新增 {created} 条"
    )
    return deleted, created


async def _migrate_specific_formulas(
    conn: aiomysql.Connection,
    main_task_id_map: dict[int | str, int | str],
) -> tuple[int, int]:
    """按 main_task_id 迁入 standard_jgh_pdf_formula。"""
    if not main_task_id_map:
        return 0, 0

    source_keys = list(main_task_id_map.keys())
    placeholders = ",".join(["%s"] * len(source_keys))
    sql = f"SELECT * FROM standard_jgh_pdf_formula WHERE main_task_id IN ({placeholders})"

    source_objs: list[StandardJghPdfFormula] = []
    async with conn.cursor(aiomysql.SSDictCursor) as cur:
        await cur.execute(sql, source_keys)
        while True:
            rows = await cur.fetchmany(BATCH_SIZE)
            if not rows:
                break
            for r in rows:
                if not r.get("id"):
                    continue
                source_objs.append(
                    StandardJghPdfFormula(
                        id=r["id"],
                        main_task_id=main_task_id_map.get(r.get("main_task_id")),
                        data_uuid=_trunc(r.get("data_uuid"), 64),
                        title=r.get("title"),
                        word=r.get("word"),
                        image=r.get("image"),
                        file_name=_extract_file_name(r.get("image")),
                        page=int(r["page"]) if r.get("page") is not None else None,
                        start=int(r["start"]) if r.get("start") is not None else None,
                        end=int(r["end"]) if r.get("end") is not None else None,
                        deleted=_coerce_bool(r.get("deleted")),
                    )
                )

    deleted, created = await _bulk_delete_then_create(StandardJghPdfFormula, source_objs)
    logger.info(
        f"[指定标准迁移] standard_jgh_pdf_formula 完成，删除 {deleted} 条，新增 {created} 条"
    )
    return deleted, created


async def _run_import_specific_standards(config: SpecificMigrationConfig) -> None:
    """后台任务：按标准号列表迁入五张表的数据。"""
    standard_nos = list(config.standard_nos)
    logger.info(f"[指定标准迁移] 开始连接 MySQL，待处理标准号 {len(standard_nos)} 个 ...")

    try:
        conn = await _open_mysql_conn(config)
    except Exception as e:
        logger.error(f"[指定标准迁移] 连接 MySQL 失败: {e}")
        return

    try:
        base_del, base_create = await _migrate_specific_base_info(conn, standard_nos)
        pdf_del, pdf_create, main_task_id_map = await _migrate_specific_pdf(conn, standard_nos)

        chapter_del, chapter_create = await _migrate_specific_chapters(conn, main_task_id_map)
        table_del, table_create = await _migrate_specific_tables(conn, main_task_id_map)
        formula_del, formula_create = await _migrate_specific_formulas(conn, main_task_id_map)

        logger.info(
            f"[指定标准迁移] 全部完成："
            f"base_info 删除 {base_del}/新增 {base_create}，"
            f"pdf 删除 {pdf_del}/新增 {pdf_create}，"
            f"chapter 删除 {chapter_del}/新增 {chapter_create}，"
            f"table 删除 {table_del}/新增 {table_create}，"
            f"formula 删除 {formula_del}/新增 {formula_create}"
        )
    except Exception as e:
        logger.error(f"[指定标准迁移] 导入过程出错: {e}", exc_info=True)
    finally:
        conn.close()


@router.post("/import-from-mysql", summary="从 MySQL 导入数据")
async def import_from_mysql(config: MySQLConfig, background_tasks: BackgroundTasks):
    """
    从指定 MySQL 数据库全量导入标准相关数据（覆盖本地三张表）。
    接口立即返回，实际导入在后台执行，进度请查看服务日志。
    """
    try:
        # 先验证连接是否可用，失败则立即报错
        test_conn = await _open_mysql_conn(config)
        test_conn.close()
    except Exception as e:
        return Fail(msg=f"MySQL 连接失败: {str(e)}")

    background_tasks.add_task(_run_import_from_mysql, config)
    return Success(msg="导入任务已在后台启动，请查看服务日志了解进度")


@router.post("/import-specific-standards", summary="按标准号迁入指定标准数据")
async def import_specific_standards(
    config: SpecificMigrationConfig, background_tasks: BackgroundTasks
):
    """
    从指定 MySQL 数据库按标准号列表迁入五张表的数据：
    standard_base_info、standard_jgh_pdf、standard_jgh_pdf_chapter、
    standard_jgh_pdf_table、standard_jgh_pdf_formula。
    已存在的标准基础信息和 PDF 按标准号更新，子表记录按 id 更新；不存在则新增。
    接口立即返回，实际导入在后台执行，进度请查看服务日志。
    """
    if not config.standard_nos:
        return Fail(msg="请至少提供一个标准号")

    try:
        test_conn = await _open_mysql_conn(config)
        test_conn.close()
    except Exception as e:
        return Fail(msg=f"MySQL 连接失败: {str(e)}")

    background_tasks.add_task(_run_import_specific_standards, config)
    return Success(
        msg=f"指定标准迁入任务已在后台启动（共 {len(config.standard_nos)} 个标准号），请查看服务日志了解进度"
    )


@router.post("/import-structured-data", summary="从 MySQL 导入结构化数据")
async def import_structured_data(
        config: StructuredMigrationConfig, background_tasks: BackgroundTasks
):
    """
    从 MySQL 的 standard_ai_doc 表中读取指定标准号的结构化数据（json_data 字段），
    解析后导入到本地 standard_jgh_pdf / standard_jgh_pdf_chapter 表。
    接口立即返回，实际导入在后台执行，进度请查看服务日志。
    """
    if not config.standard_nos:
        return Fail(msg="请至少提供一个标准号")

    try:
        test_conn = await _open_mysql_conn(config)
        test_conn.close()
    except Exception as e:
        return Fail(msg=f"MySQL 连接失败: {str(e)}")

    background_tasks.add_task(_run_import_structured_data, config)
    return Success(
        msg=f"导入任务已在后台启动（共 {len(config.standard_nos)} 个标准号），请查看服务日志了解进度"
    )


@router.post("/sync-img-word", summary="同步图片/表格 word 字段（从第三方 API 填充）")
async def sync_img_word(config: SyncImgWordConfig, background_tasks: BackgroundTasks):
    """
    对 standard_jgh_pdf_table / standard_jgh_pdf_formula 中 word 为 NULL 或空字符串的记录，
    调用第三方 /get-config-item 接口获取文本并写入 word 字段。
    已有值的记录不会被覆盖。
    接口立即返回，实际同步在后台执行，进度请查看服务日志。
    """
    nos = config.standard_nos
    scope_msg = f"{len(nos)} 个标准" if nos else "全量"
    background_tasks.add_task(_run_sync_img_word, config)
    return Success(msg=f"同步任务已在后台启动（{scope_msg}），请查看服务日志了解进度")
