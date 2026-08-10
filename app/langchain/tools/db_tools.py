"""
数据库工具集（MySQL，aiomysql 全 async 实现）

standard_ 系列表统一连接 MySQL（由 settings 中的 STANDARD_MYSQL_* 配置）。
所有暴露给 agent 的 @tool 都是原生 async，通过 standard_pool 复用连接，
不会进 to_thread，也不会卡 FastAPI event loop。

注意：make_db_tools / make_dedup_tools / make_cache_ind_tool 现在是 async 工厂，
调用方必须 await（agent 构建路径都是 async，配合自然）。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Annotated, Optional

import aiomysql
import httpx
from langchain.tools import tool
from loguru import logger

from app.services.mysql_pool import standard_pool
from app.services.agent_runtime.call_context import get_agent_call_context
from app.settings.config import settings

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DEFAULT_WORKSPACE = _PROJECT_ROOT / ".agent_workspace"
_IMAGE_BASE_URL = settings.JGH_IMAGE_BASE_URL

# get_standard_chapters 上下文保护（根因修复）：
# 章节正文无上限时，一次调用即可把整部标准（实测 666 章、MB 级 HTML）灌进 LLM 上下文，
# 之后每一轮调用都重复携带，是上下文爆炸的最大单一来源。
_CHAPTER_WORD_CAP = 2000  # 单章正文最多返回的字符数（SQL 层截断）
_CHAPTER_LIMIT_DEFAULT = 30  # 单次最多返回章节数默认值
_CHAPTER_LIMIT_MAX = 100  # 单次最多返回章节数硬上限（模型显式传大值也截住）

# standard_query 返回体积保护：
# 工具结果会作为 ToolMessage 驻留在之后每一轮的 LLM 上下文里，
# 裸 JSON 的 null/空串字段和超大结果集是 payload 滚雪球的主要来源。
# 剥离冗余字段 + 总体积封顶，把"一轮查太肥"对后续所有轮次的连带代价截住。
_QUERY_RESULT_CAP = 30000  # 结果 rows 序列化后的字符数上限（超出按行截断）

# get_standard_chapters 章节媒体随取（include_tables）：
# 章节正文里的 <img> 占位符与 standard_jgh_pdf_table / standard_jgh_pdf_formula 的
# file_name 一一对应（实测全命中）——读章节时把解析好的表格 HTML / 公式 LaTeX / 图片
# 一并带回，省掉额外一轮工具调用。
_MAX_CHAPTER_MEDIA_PER_CALL = 8  # 单次读章最多处理的媒体条目数（超出计入提示）
_MAX_MEDIA_CONTENT_CHARS = 10000  # 单条解析内容（表格 HTML / 公式文本）字符上限


def _compact_row(row: dict) -> dict:
    """剥离行内 None / 空串字段。0、False 等有意义的值保留。"""
    return {k: v for k, v in row.items() if v is not None and v != ""}


def _resolve_images_dir() -> Path:
    ctx = get_agent_call_context()
    workspace = ctx.workspace_dir if ctx and ctx.workspace_dir else _DEFAULT_WORKSPACE
    return workspace / "images"


# ── standard_query 安全门 ─────────────────────────────────────────────────────
# 该工具专为查 standard_ 系列标准表而设，旧实现把传入 SQL 原样对全库执行：
# 任意表可读（如 users 的密码哈希）、写语句仅靠 docstring「只读」自律、
# 注释/多语句可夹带——提示词注入或模型幻觉都可能越权。此处加四道闸：
#   1. 仅单条 SELECT / WITH...SELECT（禁多语句、禁注释、禁其他语句类型）
#   2. 写操作/危险关键字按词边界硬拒（update_time 这类列名不受影响；
#      ORDER BY ... DESC 合法，故不封 desc）
#   3. FROM/JOIN 引用的表必须全部 standard_ 前缀（WITH 的 CTE 名豁免）
#   4. 禁碰 information_schema / mysql / performance_schema / sys

_STANDARD_TABLE_PREFIX = "standard_"
_DEFAULT_QUERY_LIMIT = 200  # 未显式写 LIMIT 时自动追加的行数上限

_DENY_KEYWORD_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|rename|grant|revoke|"
    r"call|load|handler|lock|unlock|flush|kill|purge|reset|prepare|execute|use|show|"
    r"describe|do|set|start|stop|into|outfile|dumpfile|sleep|benchmark)\b",
    re.IGNORECASE,
)

_SYSTEM_DB_TOKENS = ("information_schema", "performance_schema", "mysql.", "sys.")

_FROM_JOIN_RE = re.compile(r"\b(from|join)\b", re.IGNORECASE)
_IDENT_RE = re.compile(r"`?([A-Za-z0-9_]+)`?")


def _cte_names(sql: str) -> set:
    """提取 WITH 子句定义的 CTE 名（它们会出现在 FROM 后但不是实体表，白名单豁免）。"""
    names: set = set()
    if not re.match(r"\s*with\b", sql, re.IGNORECASE):
        return names
    for m in re.finditer(r"\b([A-Za-z0-9_]+)\s+as\s*\(", sql, re.IGNORECASE):
        names.add(m.group(1).lower())
    return names


def _extract_tables(sql: str) -> list:
    """提取 FROM/JOIN 后引用的表名（含 FROM 逗号并列多表；子查询跳过，其内部 FROM/JOIN 由各自的匹配扫到）。"""
    tables: list = []
    for m in _FROM_JOIN_RE.finditer(sql):
        is_from = m.group(1).lower() == "from"
        pos = m.end()
        while True:
            while pos < len(sql) and sql[pos].isspace():
                pos += 1
            if pos >= len(sql) or sql[pos] == "(":  # 子查询：跳过整段，内部 FROM/JOIN 另有匹配
                break
            tm = _IDENT_RE.match(sql, pos)
            if not tm:
                break
            tables.append(tm.group(1))
            if not is_from:  # JOIN 后只跟一张表
                break
            pos = tm.end()
            # FROM t1 a, t2 b：[AS] 别名后接逗号 → 继续解析下一张表
            rest_m = re.match(r"\s*(?:as\s+)?[A-Za-z0-9_]+\s*,", sql[pos:], re.IGNORECASE)
            if rest_m:
                pos += rest_m.end()
                continue
            comma_m = re.match(r"\s*,", sql[pos:])
            if comma_m:
                pos += comma_m.end()
                continue
            break
    return tables


def _validate_standard_sql(sql: str) -> Optional[str]:
    """standard_query 安全门。返回拒绝原因，None 表示放行。"""
    s = (sql or "").strip()
    if s.endswith(";"):
        s = s[:-1].strip()
    if not s:
        return "SQL 为空"
    if ";" in s:
        return "不允许多条语句，一次只能执行一条 SELECT"
    if "--" in s or "/*" in s or "#" in s:
        return "不允许 SQL 注释（--、/*、#）"
    if not re.match(r"^(select|with)\b", s, re.IGNORECASE):
        return "仅允许只读 SELECT 查询"
    m = _DENY_KEYWORD_RE.search(s)
    if m:
        return f"含禁用关键字 {m.group(1).upper()}，仅允许只读查询"
    low = s.lower()
    for bad in _SYSTEM_DB_TOKENS:
        if bad in low:
            return f"不允许访问系统库表：{bad.rstrip('.')}"
    ctes = _cte_names(s)
    bad_tables = sorted({t for t in _extract_tables(s) if not t.lower().startswith(_STANDARD_TABLE_PREFIX) and t.lower() not in ctes})
    if bad_tables:
        return f"只允许查询 {_STANDARD_TABLE_PREFIX} 前缀的标准表，不允许的表：{', '.join(bad_tables)}"
    return None


def _ensure_limit(sql: str) -> str:
    """查询未显式写 LIMIT 时自动追加，防止全表拉爆上下文。"""
    if not re.search(r"\blimit\b", sql, re.IGNORECASE):
        return f"{sql.rstrip()} LIMIT {_DEFAULT_QUERY_LIMIT}"
    return sql


# ── 工厂函数 ──────────────────────────────────────────────────────────────────


def make_db_tools(pool_id: Optional[int] = None):
    """
    创建绑定到 MySQL standard 数据库的工具集（同步工厂）。

    pool_id 解析延后到工具第一次被调用时（async lazy load + cache），
    工厂自身不阻塞 IO，可在 sync / async 上下文混用调用。

    Args:
        pool_id: 查重池ID，None 或 -1 表示不限制；由业务层传入，大模型不可见

    Returns:
        包含四个工具的列表：[standard_query, standard_tables, standard_schema,
                              get_standard_chapters]
    """
    allowed_holder: dict = {"loaded": False, "value": None}

    async def _ensure_allowed() -> Optional[list]:
        if not allowed_holder["loaded"]:
            if pool_id is not None and pool_id != -1:
                allowed_holder["value"] = await _get_allowed_standard_nos(pool_id)
            allowed_holder["loaded"] = True
        return allowed_holder["value"]

    @tool
    async def standard_query(
        sql: Annotated[str, "要执行的 SELECT 语句（只能查 standard_ 前缀表）"],
    ) -> str:
        """
        对标准数据表（standard_ 前缀）执行只读 SQL 查询，返回 JSON 格式结果。

        限制（违反会直接拒绝，按提示改写即可）：
        - 只允许单条 SELECT；禁止任何写操作、多语句和注释
        - 只能查 standard_ 前缀表（如 standard_base_info、standard_jgh_pdf_chapter）
        - 不写 LIMIT 会自动追加 LIMIT 200
        """
        return await _exec_query(sql, await _ensure_allowed())

    @tool
    async def standard_tables() -> str:
        """列出数据库中所有标准数据表（standard_ 前缀）的表名。"""
        return await _exec_tables()

    @tool
    async def standard_schema(table: Annotated[str, "要查询结构的表名（standard_ 前缀）"]) -> str:
        """获取指定标准数据表的列信息（列名、类型、是否非空）。只允许 standard_ 前缀表。"""
        return await _exec_schema(table)

    @tool
    async def get_standard_chapters(
        standard_no: Annotated[str, "标准编号，如 GB/T 1234-2020"],
        toc_only: Annotated[bool, "仅返回目录结构（title_no + title + word_count），不含正文"] = False,
        title_no_prefix: Annotated[Optional[str], "按章节号前缀过滤，多个用逗号分隔，如 '4,5,6' 获取第4/5/6章，'附录A,附录B' 获取多个附录"] = None,
        keyword: Annotated[Optional[str], "在章节标题或正文中搜索关键词（模糊匹配）"] = None,
        near_title_no: Annotated[Optional[str], "查看指定章节及其后续章节，用于 title 不清晰时了解上下文"] = None,
        window: Annotated[int, "near_title_no 模式下向后查看的章节数"] = 3,
        limit: Annotated[int, "每次返回的最大章节数（上限 100）"] = _CHAPTER_LIMIT_DEFAULT,
        offset: Annotated[int, "分页偏移量"] = 0,
        include_tables: Annotated[bool, "是否还原各章内的表格/公式/图片并附在每章 media 字段；仅纯文本浏览可设 false 提速"] = True,
    ) -> "str | list":
        """
        查询指定标准的正文章节内容，支持目录预览、按章节号过滤、关键词搜索和分页。

        **阅读策略：**
        - 用户点名了章节号/章节名：直接用 title_no_prefix 一次取回正文（接受章节号 "4"/"附录A" 或章节名 "范围"），**无需先取目录**
        - 未点名、需先了解结构：先 toc_only=True 看目录（含各章字数），再 title_no_prefix 定向取
        - title_no_prefix 未命中时，返回会自动附带完整目录（toc 字段），据此重选重试即可，不要再单独调 toc_only
        - 正文较多用 limit/offset 分批；章节异常截断用 near_title_no + window 看后续

        **媒体随取（默认开启）：** 正文 <img> 占位符自动关联表格/公式解析，附在该章 media 字段（按正文顺序）：
        - {type: "table"/"formula", title, content, path}——content 为解析好的表格 HTML / 公式 LaTeX，就是数据本身，优先使用；path 是截图，展示给用户时登记 artifact 用
        - {type: "image", path}——普通插图，已下载到 workspace；主模型支持视觉时会以多模态块直接内联
        遇"见表X"跨章引用，直接 keyword="表X" 读该表所在章节即可。

        注意：每章正文最多返回 2000 字符（word_chars 为原始字符数）；word_chars > 2000 说明被截断（media 不受影响），缩小 title_no_prefix 范围或用 near_title_no 分批精读。
        """
        return await _exec_standard_chapters(
            standard_no,
            toc_only,
            title_no_prefix,
            keyword,
            near_title_no,
            window,
            limit,
            offset,
            include_tables,
        )

    return [standard_query, standard_tables, standard_schema, get_standard_chapters]


async def _download_img_tag(img_tag: str) -> str:
    """从 <img> 标签中提取 src，下载图片到 workspace/images/，返回 workspace 内相对路径。"""
    src_match = re.search(r'src=["\']([^"\']+)["\']', img_tag, re.IGNORECASE)
    if not src_match:
        return img_tag  # 无 src，原样返回
    url = src_match.group(1).strip()
    if not url.startswith(("http://", "https://")):
        url = _IMAGE_BASE_URL + url.lstrip("/")
    filename = url.split("/")[-1].split("?")[0] or "img.jpg"
    if not any(filename.lower().endswith(e) for e in (".png", ".jpeg", ".jpg", ".gif", ".webp")):
        filename += ".jpg"
    images_dir = _resolve_images_dir()
    local_path = images_dir / filename
    await asyncio.to_thread(images_dir.mkdir, parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            await asyncio.to_thread(local_path.write_bytes, resp.content)
        return f"images/{filename}"
    except Exception as e:
        return f"下载失败: {e}"


async def _load_media_index(main_task_id: int) -> tuple[dict, dict]:
    """按 main_task_id 批量加载表格/公式解析索引：{file_name 基名: 记录}。

    file_name 与章节正文 <img src> 的文件名一一对应，是章节与解析内容的精确关联键。
    """
    table_idx: dict[str, dict] = {}
    formula_idx: dict[str, dict] = {}
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT title, word, image, file_name FROM standard_jgh_pdf_table WHERE main_task_id = %s AND file_name IS NOT NULL AND file_name != ''",
                [main_task_id],
            )
            for r in await cur.fetchall():
                table_idx[r["file_name"].split("/")[-1]] = r
            await cur.execute(
                "SELECT title, word, image, file_name FROM standard_jgh_pdf_formula WHERE main_task_id = %s AND file_name IS NOT NULL AND file_name != ''",
                [main_task_id],
            )
            for r in await cur.fetchall():
                formula_idx[r["file_name"].split("/")[-1]] = r
    return table_idx, formula_idx


_IMG_SRC_RE = re.compile(r'<img\s[^>]*src=["\']([^"\']+)["\'][^>]*>', re.IGNORECASE)


async def _build_media_entry(
    src: str,
    table_idx: dict,
    formula_idx: dict,
    path_cache: dict,
) -> dict:
    """把单个 <img src> 还原为一条 media 条目（解析命中 → table/formula，否则 image）。"""
    fn = src.split("/")[-1].split("?")[0]
    rec = table_idx.get(fn)
    kind = "table"
    if rec is None:
        rec = formula_idx.get(fn)
        kind = "formula"

    if src in path_cache:
        path = path_cache[src]
    else:
        # 命中解析记录用其裁剪截图（更清晰），否则用正文原始引用
        img_src = (rec.get("image") or "").strip() if rec else ""
        path = await _download_img_tag(f'<img src="{img_src or src}">')
        path_cache[src] = path

    if rec and (rec.get("word") or "").strip():
        parsed = rec["word"].strip()
        entry: dict = {
            "type": kind,
            "title": (rec.get("title") or "").strip(),
            "content": parsed[:_MAX_MEDIA_CONTENT_CHARS],
            "path": path,
        }
        if len(parsed) > _MAX_MEDIA_CONTENT_CHARS:
            entry["content_truncated"] = True
        return entry
    # 命中但 word 为空（解析回填中）或未命中 → 纯图片（视觉模型可内联直读）
    return {"type": "image", "path": path}


async def _extract_chapter_media(chapters: list[dict], main_task_id: Optional[int]) -> int:
    """把各章正文里的 <img> 占位符还原为解析好的表格/公式/图片，写入该章 media 字段。

    关联规则（file_name 精确匹配 standard_jgh_pdf_table / standard_jgh_pdf_formula）：
    - 命中且解析文本（word）非空 → {type: "table"/"formula", title, content, path}，
      content 为解析好的表格 HTML / 公式 LaTeX，超长按 _MAX_MEDIA_CONTENT_CHARS 截断；
    - 命中但 word 为空或未命中（普通插图）→ {type: "image", path}。
    图片统一下到 workspace/images/（同一 src 全调用去重）。必须在正文截断前调用。
    单次调用最多处理 _MAX_CHAPTER_MEDIA_PER_CALL 条，返回因超额跳过的媒体数。
    """
    if not any(_IMG_SRC_RE.search(r.get("word") or "") for r in chapters):
        return 0

    table_idx, formula_idx = await _load_media_index(main_task_id) if main_task_id else ({}, {})
    budget = _MAX_CHAPTER_MEDIA_PER_CALL
    skipped = 0
    path_cache: dict[str, str] = {}  # src → workspace 相对路径（跨章节去重）

    for row in chapters:
        srcs = _IMG_SRC_RE.findall(row.get("word") or "")
        if not srcs:
            continue
        media: list[dict] = []
        for src in srcs:
            if budget <= 0:
                skipped += 1
                continue
            budget -= 1
            media.append(await _build_media_entry(src, table_idx, formula_idx, path_cache))
        if media:
            row["media"] = media
    return skipped


# 单次调用最多内联的图片数量（防单次工具结果撑爆上下文）；
# 超出的仍保留 path，模型可 register_artifact 展示给用户
_MAX_INLINE_MEDIA_IMAGES = 4


async def _maybe_inline_media_images(json_result: str) -> "str | list":
    """原生视觉模型多模态直传：把章节 media 中 type=image 的插图直接内联为 content blocks。

    只内联插图：表格/公式条目已带回解析文本（HTML/LaTeX），无需再看截图。
    纯文本模型 / 无插图 / 读取失败时原样返回字符串，行为不变。
    """
    try:
        from app.langchain.role_model_profile import effective_chat_supports_vision

        if not effective_chat_supports_vision():
            return json_result
        parsed = json.loads(json_result)
    except Exception:
        return json_result

    paths: list[str] = []

    def _walk(node):
        if isinstance(node, dict):
            if node.get("type") == "image" and isinstance(node.get("path"), str) and node["path"].startswith("images/"):
                paths.append(node["path"])
            else:
                for v in node.values():
                    _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(parsed)
    if not paths:
        return json_result

    import mimetypes as _mimetypes
    from app.api.v1.ai.upload import publish_conversation_image
    from app.langchain.tools.vision_tools import _maybe_resize_sync

    images_dir = _resolve_images_dir()
    blocks: list[dict] = []
    for p in dict.fromkeys(paths[:_MAX_INLINE_MEDIA_IMAGES]):  # 去重且保序
        try:
            fp = images_dir / p.split("/", 1)[1]
            raw = await asyncio.to_thread(fp.read_bytes)
            mime = _mimetypes.guess_type(fp.name)[0] or "image/jpeg"
            raw, mime = await asyncio.to_thread(_maybe_resize_sync, raw, mime)
            # url 用 PUBLIC_BASE_URL 公网链接（publish_conversation_media 统一发布），消息里只留一行 URL
            url = await publish_conversation_image(raw, mime)
            if url:
                blocks.append({"type": "image_url", "image_url": {"url": url}})
            else:
                logger.warning(f"[chapters] 图片发布公网 URL 失败，跳过内联：{p}")
        except Exception as e:
            logger.warning(f"[chapters] 内联图片失败 {p}: {e}")
    if not blocks:
        return json_result
    note = f"\n（其中 {len(blocks)} 张图片已作为多模态内容直接内联在本结果中，可直接看到）"
    return [{"type": "text", "text": json_result + note}, *blocks]


def make_cache_ind_tool():
    """创建查询 standard_cache_ind 的专用工具（同步工厂）。"""

    @tool
    async def get_cached_indicators(
        standard_no: Annotated[str, "标准编号"],
        indicator_type: Annotated[Optional[str], "指标类型：static=静态 | dynamic=动态 | 不传=全部"] = None,
        keyword: Annotated[Optional[str], "在 indicator_object 或 experiment_name 中模糊搜索关键词"] = None,
        limit: Annotated[int, "最大返回条数，默认 300"] = 300,
    ) -> str:
        """
        从 standard_cache_ind 查询指定标准的已提取指标列表（含 id 字段）。
        比对时必须使用返回的 id 作为 source_ind_id / target_ind_id。
        """
        return await _exec_cached_indicators(standard_no, indicator_type, keyword, limit)

    return get_cached_indicators


def make_dedup_tools(pool_id: Optional[int] = None):
    """
    创建查重专用工具集（同步工厂）。

    pool_id 同样延后到第一次工具调用时再解析。
    """
    allowed_holder: dict = {"loaded": False, "value": None}

    async def _ensure_allowed() -> Optional[list]:
        if not allowed_holder["loaded"]:
            if pool_id is not None and pool_id != -1:
                v = await _get_allowed_standard_nos(pool_id)
                allowed_holder["value"] = v
                if v is not None:
                    logger.info(f"[DeduTools] pool_id={pool_id}，范围限制 {len(v)} 个标准")
                else:
                    logger.warning(f"[DeduTools] pool_id={pool_id}，未能加载范围，将查全库")
            allowed_holder["loaded"] = True
        return allowed_holder["value"]

    @tool
    async def search_candidate_standards(
        target_no: Annotated[str, "目标标准编号，用于排除自身"],
        keywords: Annotated[str, "搜索关键词，多个用逗号分隔"],
        search_field: Annotated[str, "搜索字段：'cname'=标准名称，'use_range'=适用范围，'standard_no'=标准号前缀匹配"] = "cname",
        limit: Annotated[int, "最多返回条数，默认 50"] = 50,
    ) -> str:
        """
        从标准库中搜索与目标标准可能相关的候选标准。

        - search_field='cname'：在标准名称中模糊匹配关键词（多个关键词取 OR）
        - search_field='use_range'：在适用范围中模糊匹配关键词
        - search_field='standard_no'：按标准号前缀匹配同系列标准（如 'GB/T 11313' 匹配所有子标准）

        返回字段：standard_no、cname、use_range（已排除目标标准自身）。
        """
        return await _exec_search_candidate_standards(
            target_no,
            keywords,
            search_field,
            limit,
            await _ensure_allowed(),
        )

    return [search_candidate_standards]


# ── 内部实现 ──────────────────────────────────────────────────────────────────


async def _exec_query(sql: str, allowed_nos: Optional[list] = None) -> str:
    sql = (sql or "").strip().rstrip(";").strip()
    # 安全门在执行口统一拦截（不信任任何上游调用方），拒绝原因随结果返回给模型
    reason = _validate_standard_sql(sql)
    if reason:
        logger.warning(f"[standard_query] SQL 被拒：{reason} | {sql[:200]}")
        return json.dumps({"ok": False, "error": f"查询被拒绝：{reason}"}, ensure_ascii=False)
    sql = _ensure_limit(sql)
    try:
        if allowed_nos:
            sql = _inject_pool_filter(sql, allowed_nos)
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql)
                rows = [_compact_row(r) for r in await cur.fetchall()]

        # 体积封顶：超限时保留前 N 行并提示收窄查询
        truncated = False
        if rows:
            total_size = len(json.dumps(rows, ensure_ascii=False, default=str))
            if total_size > _QUERY_RESULT_CAP:
                kept: list = []
                acc = 2  # "[]"
                for r in rows:
                    piece = len(json.dumps(r, ensure_ascii=False, default=str)) + 1
                    if acc + piece > _QUERY_RESULT_CAP and kept:
                        break
                    kept.append(r)
                    acc += piece
                truncated = len(kept) < len(rows)
                rows = kept

        resp: dict = {"ok": True, "rows": rows, "count": len(rows)}
        if truncated:
            resp["note"] = f"结果过大，仅返回前 {len(rows)} 行（总体积超过上限）。请缩小 SELECT 字段范围、加更精确的 WHERE 条件或更小的 LIMIT 分批获取。"
        return json.dumps(resp, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


async def _exec_tables() -> str:
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME")
                # 只暴露 standard_ 前缀表（agent_* / users 等系统表对 LLM 不可见）
                tables = [r["TABLE_NAME"] for r in await cur.fetchall() if r["TABLE_NAME"].startswith(_STANDARD_TABLE_PREFIX)]
        return json.dumps({"ok": True, "tables": tables}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


async def _exec_schema(table: str) -> str:
    table = (table or "").strip().strip("`")
    if not table.lower().startswith(_STANDARD_TABLE_PREFIX):
        return json.dumps(
            {"ok": False, "error": f"查询被拒绝：只允许查询 {_STANDARD_TABLE_PREFIX} 前缀的标准表"},
            ensure_ascii=False,
        )
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT COLUMN_NAME AS name, DATA_TYPE AS type, "
                    "(IS_NULLABLE = 'NO') AS notnull "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    [table],
                )
                columns = [{"name": r["name"], "type": r["type"], "notnull": bool(r["notnull"])} for r in await cur.fetchall()]
        return json.dumps({"ok": True, "table": table, "columns": columns}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


async def _exec_standard_chapters(
    standard_no: str,
    toc_only: bool = False,
    title_no_prefix: Optional[str] = None,
    keyword: Optional[str] = None,
    near_title_no: Optional[str] = None,
    window: int = 3,
    limit: int = _CHAPTER_LIMIT_DEFAULT,
    offset: int = 0,
    include_tables: bool = True,
) -> "str | list":
    try:
        if near_title_no:
            return await _exec_near_chapters(standard_no, near_title_no, window, toc_only, include_tables)

        # 硬上限：模型显式传大值（如 1000）也截住，防止一次调用灌爆上下文
        limit = max(1, min(int(limit or _CHAPTER_LIMIT_DEFAULT), _CHAPTER_LIMIT_MAX))

        if toc_only:
            select_cols = "a.title_no, a.title, LENGTH(COALESCE(a.word, '')) AS word_count"
        elif include_tables:
            # 媒体还原需要全章正文（<img> 占位符可能在 2000 字符截断位之后）：
            # SQL 取全文，Python 侧先还原媒体再截断，word_chars 顺带本地计算。
            # main_task_id 仅用于查表格/公式解析索引，不进响应
            select_cols = "a.title_no, a.title, a.word, a.main_task_id"
        else:
            # SQL 层截断单章正文（SUBSTRING 按字符计），word_chars 返回原始字符数供模型判断是否被截断
            select_cols = f"a.title_no, a.title, SUBSTRING(a.word, 1, {_CHAPTER_WORD_CAP}) AS word, CHAR_LENGTH(COALESCE(a.word, '')) AS word_chars"

        conditions = ["b.standard_no = %s"]
        params: list = [standard_no]

        if title_no_prefix:
            prefixes = [p.strip() for p in title_no_prefix.split(",") if p.strip()]
            or_parts = []
            for p in prefixes:
                if re.search(r"\D", p):
                    # 含非数字字符（章节名，如"范围"）：追加 contains 兜底，
                    # 让不查目录直接传章节名也能命中（如 "范围" → "1 范围"）。
                    # 纯数字前缀不加，避免 "4" 误匹配 "14 xxx" 等章节。
                    or_parts.append("(a.title_no = %s OR a.title = %s OR a.title LIKE %s OR a.title LIKE %s)")
                    params += [p, p, f"{p} %", f"%{p}%"]
                else:
                    or_parts.append("(a.title_no = %s OR a.title = %s OR a.title LIKE %s)")
                    params += [p, p, f"{p} %"]
            if or_parts:
                conditions.append(f"({' OR '.join(or_parts)})")

        if keyword:
            if toc_only:
                conditions.append("a.title LIKE %s")
            else:
                conditions.append("(a.title LIKE %s OR a.word LIKE %s)")
                params.append(f"%{keyword}%")
            params.append(f"%{keyword}%")

        where = " AND ".join(conditions)
        count_sql = f"""
            SELECT COUNT(*) AS cnt FROM standard_jgh_pdf_chapter a
            JOIN standard_jgh_pdf b ON b.main_task_id = a.main_task_id
            WHERE {where}
        """
        data_sql = f"""
            SELECT {select_cols}
            FROM standard_jgh_pdf_chapter a
            JOIN standard_jgh_pdf b ON b.main_task_id = a.main_task_id
            WHERE {where}
            ORDER BY a.id
            LIMIT %s OFFSET %s
        """

        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(count_sql, params)
                total = (await cur.fetchone())["cnt"]

                if total == 0:
                    # 区分"该标准没有正文"与"过滤条件没命中"：
                    # 后者把完整目录随结果带回，模型一次调用即可改选章节重试，
                    # 不必再单独发起一轮 toc_only 调用。
                    if title_no_prefix or keyword:
                        base_count_sql = f"""
                            SELECT COUNT(*) AS cnt FROM standard_jgh_pdf_chapter a
                            JOIN standard_jgh_pdf b ON b.main_task_id = a.main_task_id
                            WHERE b.standard_no = %s
                        """
                        await cur.execute(base_count_sql, [standard_no])
                        base_total = (await cur.fetchone())["cnt"]
                        if base_total > 0:
                            toc_sql = f"""
                                SELECT a.title_no, a.title,
                                       LENGTH(COALESCE(a.word, '')) AS word_count
                                FROM standard_jgh_pdf_chapter a
                                JOIN standard_jgh_pdf b ON b.main_task_id = a.main_task_id
                                WHERE b.standard_no = %s
                                ORDER BY a.id
                                LIMIT %s
                            """
                            await cur.execute(toc_sql, [standard_no, _CHAPTER_LIMIT_MAX * 2])
                            toc_rows = list(await cur.fetchall())
                            return json.dumps(
                                {
                                    "ok": True,
                                    "standard_no": standard_no,
                                    "total": 0,
                                    "chapters": [],
                                    "note": (
                                        "指定的章节号/标题/关键词未匹配到任何章节。"
                                        "已附带该标准完整目录（toc 字段），请从中挑选正确的 "
                                        "title_no 或 title 重新传入 title_no_prefix，"
                                        "无需再单独调用 toc_only。"
                                    ),
                                    "toc": toc_rows,
                                },
                                ensure_ascii=False,
                                default=str,
                            )
                    return json.dumps({"ok": True, "standard_no": standard_no, "total": 0, "chapters": [], "note": "未找到该标准的正文数据"}, ensure_ascii=False)

                await cur.execute(data_sql, params + [limit, offset])
                rows = list(await cur.fetchall())

        next_offset = offset + len(rows)
        resp: dict = {
            "ok": True,
            "standard_no": standard_no,
            "total": total,
            "offset": offset,
            "returned": len(rows),
            "next_offset": next_offset,
            "has_more": next_offset < total,
            "chapters": rows,
        }
        if not toc_only:
            notes: list[str] = []
            if include_tables:
                # 先对全文还原媒体（表格/公式/图片），再截断正文（顺序不可颠倒）
                mtid = rows[0].get("main_task_id") if rows else None
                for r in rows:
                    r.pop("main_task_id", None)
                skipped = await _extract_chapter_media(rows, mtid)
                for r in rows:
                    full = r.get("word") or ""
                    r["word_chars"] = len(full)
                    if len(full) > _CHAPTER_WORD_CAP:
                        r["word"] = full[:_CHAPTER_WORD_CAP]
                if skipped:
                    notes.append(f"另有 {skipped} 条媒体超出单次调用上限（{_MAX_CHAPTER_MEDIA_PER_CALL} 条）未提取，如需要请缩小 title_no_prefix 范围后重读对应章节。")
            resp["total_chars"] = sum(len(r.get("word") or "") for r in rows)
            if any((r.get("word_chars") or 0) > _CHAPTER_WORD_CAP for r in rows):
                notes.append(
                    f"为控制上下文体积，每章正文最多返回 {_CHAPTER_WORD_CAP} 字符（word_chars 为原始字符数）。"
                    f"word_chars 超过 {_CHAPTER_WORD_CAP} 的章节已被截断（media 字段不受影响，系从全章还原），"
                    f"请缩小 title_no_prefix 范围或用 near_title_no 分批精读，不要据此断言内容缺失。"
                )
            if notes:
                resp["note"] = " ".join(notes)
        result = json.dumps(resp, ensure_ascii=False, default=str)
        if not toc_only and include_tables:
            return await _maybe_inline_media_images(result)
        return result

    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


async def _get_allowed_standard_nos(pool_id: int) -> Optional[list]:
    """根据 pool_id 从 MySQL 异步查询允许的标准编号列表"""
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT standard_nos FROM standard_pool_check WHERE id = %s AND is_active = 1 LIMIT 1",
                    [pool_id],
                )
                row = await cur.fetchone()
        if not row:
            logger.warning(f"[DeduPool] pool_id={pool_id} 未找到或已禁用，不限制范围")
            return None
        nos = row["standard_nos"]
        if isinstance(nos, str):
            nos = json.loads(nos)
        logger.info(f"[DeduPool] pool_id={pool_id} 加载 {len(nos)} 个标准编号")
        return nos
    except Exception as e:
        logger.error(f"[DeduPool] 获取 pool_id={pool_id} 失败: {e}，不限制范围")
        return None


def _inject_pool_filter(sql: str, allowed_nos: list) -> str:
    """在 SQL WHERE 子句中注入 pool 过滤条件（IN 列表）"""
    sql_lower = sql.lower()
    if "standard_base_info" in sql_lower and "where" in sql_lower:
        quoted = ",".join(f"'{no.replace(chr(39), chr(39) * 2)}'" for no in allowed_nos)
        where_pos = sql_lower.rfind("where")
        return sql[: where_pos + 5] + f" standard_no IN ({quoted}) AND" + sql[where_pos + 5 :]
    return sql


async def _exec_near_chapters(standard_no: str, near_title_no: str, window: int, toc_only: bool, include_tables: bool = True) -> "str | list":
    """返回指定章节及其后续 window 个章节。"""
    try:
        if toc_only:
            select_cols = "a.title_no, a.title, LENGTH(COALESCE(a.word, '')) AS word_count"
        else:
            # main_task_id 仅用于媒体索引，响应前移除
            select_cols = "a.title_no, a.title, a.word, a.main_task_id"

        id_sql = """
                 SELECT a.id
                 FROM standard_jgh_pdf_chapter a
                          JOIN standard_jgh_pdf b ON b.main_task_id = a.main_task_id
                 WHERE b.standard_no = %s
                 ORDER BY a.id
                 """
        pos_sql = """
                  SELECT a.id
                  FROM standard_jgh_pdf_chapter a
                           JOIN standard_jgh_pdf b ON b.main_task_id = a.main_task_id
                  WHERE b.standard_no = %s
                    AND (a.title_no = %s OR a.title = %s OR a.title LIKE %s)
                  ORDER BY a.id LIMIT 1
                  """

        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(id_sql, [standard_no])
                all_ids = [r["id"] for r in await cur.fetchall()]

                if not all_ids:
                    return json.dumps({"ok": True, "standard_no": standard_no, "chapters": [], "note": "未找到该标准数据"}, ensure_ascii=False)

                await cur.execute(pos_sql, [standard_no, near_title_no, near_title_no, f"{near_title_no} %"])
                row = await cur.fetchone()

                if not row:
                    return json.dumps({"ok": False, "error": f"未找到章节 {near_title_no}"}, ensure_ascii=False)

                target_id = row["id"]
                try:
                    idx = all_ids.index(target_id)
                except ValueError:
                    return json.dumps({"ok": False, "error": "章节定位失败"}, ensure_ascii=False)

                slice_ids = all_ids[idx : idx + 1 + window]

                data_sql = f"""
                    SELECT {select_cols} FROM standard_jgh_pdf_chapter a
                    JOIN standard_jgh_pdf b ON b.main_task_id = a.main_task_id
                    WHERE b.standard_no = %s AND a.id IN ({",".join(["%s"] * len(slice_ids))})
                    ORDER BY a.id
                """
                await cur.execute(data_sql, [standard_no] + slice_ids)
                chapters = list(await cur.fetchall())

        # near 模式本就返回全文，表格/公式/图片同样随取随回（媒体还原不截断正文）
        if not toc_only and include_tables and chapters:
            mtid = chapters[0].get("main_task_id")
            for r in chapters:
                r.pop("main_task_id", None)
            await _extract_chapter_media(chapters, mtid)
        result = json.dumps(
            {"ok": True, "standard_no": standard_no, "near_title_no": near_title_no, "returned": len(chapters), "chapters": chapters},
            ensure_ascii=False,
            default=str,
        )
        if not toc_only and include_tables and chapters:
            return await _maybe_inline_media_images(result)
        return result
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


async def _exec_cached_indicators(
    standard_no: str,
    indicator_type: Optional[str],
    keyword: Optional[str],
    limit: int,
) -> str:
    conditions = ["standard_no = %s", "is_valid = 1"]
    params: list = [standard_no]
    if indicator_type:
        conditions.append("indicator_type = %s")
        params.append(indicator_type)
    if keyword:
        conditions.append("(indicator_object LIKE %s OR experiment_name LIKE %s)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    where = " AND ".join(conditions)
    sql = f"""
        SELECT id, indicator_type, standard_object, applicable_object,
               indicator_object, source_value,
               experiment_name, source_input_params, source_process_logic, source_result,
               source_clause
        FROM standard_cache_ind
        WHERE {where}
        ORDER BY id
        LIMIT %s
    """
    params.append(limit)
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = list(await cur.fetchall())
        return json.dumps(
            {"ok": True, "standard_no": standard_no, "count": len(rows), "indicators": rows},
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


async def _exec_search_candidate_standards(
    target_no: str,
    keywords: str,
    search_field: str,
    limit: int,
    allowed_nos: Optional[list],
) -> str:
    try:
        kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
        if not kw_list:
            return json.dumps({"ok": False, "error": "keywords 不能为空"}, ensure_ascii=False)

        params: list = [target_no]
        conditions = ["standard_no != %s"]

        if search_field == "standard_no":
            or_parts = ["standard_no LIKE %s" for _ in kw_list]
            params += [f"{k}%" for k in kw_list]
        elif search_field == "use_range":
            or_parts = ["use_range LIKE %s" for _ in kw_list]
            params += [f"%{k}%" for k in kw_list]
        else:  # cname
            or_parts = ["cname LIKE %s" for _ in kw_list]
            params += [f"%{k}%" for k in kw_list]

        conditions.append(f"({' OR '.join(or_parts)})")

        if allowed_nos:
            quoted = ",".join(f"'{no.replace(chr(39), chr(39) * 2)}'" for no in allowed_nos)
            conditions.append(f"standard_no IN ({quoted})")

        where = " AND ".join(conditions)
        sql = f"SELECT standard_no, cname, use_range FROM standard_base_info WHERE {where} LIMIT {int(limit)}"

        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = list(await cur.fetchall())

        logger.debug(f"[search_candidate_standards] field={search_field} kw={kw_list} pool_filter={'YES' if allowed_nos else 'NO'} → {len(rows)} 条")
        return json.dumps({"ok": True, "count": len(rows), "rows": rows}, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
