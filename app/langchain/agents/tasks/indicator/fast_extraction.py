"""
fast_extraction.py — 快速指标与试验提取

不使用 ReAct Agent，全程单次 _llm_call(prompt) + JSON 解析：
  快路径（全文 compact 字符 <= SMART_V2_FAST_BATCH_COMPACT_LIMIT，合计 1 次 LLM）:
    单次 LLM one-shot 完成全部指标 / 试验提取
  慢路径（compact 超阈值，合计 2~3 轮 LLM）:
    1 次分组规划 + N 组并行指标提取 + 并行试验提取（各提取 prompt 已含分类字段）

object_type / indicator_category / norm_class 直接由提取 prompt 一并输出，
不再调用 _correct_objects / _classify_indicators / _classify_tests。
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from html.parser import HTMLParser
from typing import List, Optional

import aiomysql
import httpx
from loguru import logger

from app.langchain.agents.tasks.indicator.indicator_extraction_agent import (
    ExtractionOutput,
    IndicatorItem,
    TestItem,
    _STANDARD_TYPES,
    _CATEGORY_FIELD_DESC,
    _NORM_CLASS_FIELD_DESC,
    _deduplicate,
    _deduplicate_tests,
)
from app.langchain.agents.mind_map import build_taxonomy_tree_desc
from app.services.mysql_pool import standard_pool
from app.settings import APP_SETTINGS as settings

# ── 阈值（从 .env 读取，可按模型上下文大小动态调整） ──────────────────────────
def _fast_compact_limit() -> int:
    return settings.SMART_V2_FAST_BATCH_COMPACT_LIMIT

# ── 分类字段说明（静态，嵌入 prompt，避免额外 LLM 轮次）──────────────────────
_CLASSIFICATION_GUIDE = (
    "- object_type：标准化对象类型：产品类对象 | 服务类对象 | 过程类对象\n"
    f"- indicator_category：{_CATEGORY_FIELD_DESC}\n"
    f"- norm_class（仅指标有，试验无此字段）：{_NORM_CLASS_FIELD_DESC}"
)

# ── 快路径专用分类树描述（从 TAXONOMY 动态生成，两个接口共用，改一处全局生效）──
_TAXONOMY_TREE_DESC: str = build_taxonomy_tree_desc()

# ── source_value 填写规范（快慢路径 + 两个接口共用，改一处全局生效）───────────────
# 两条核心约定：
#   1. 完整保真——保留标准号引用、等效/替代选项、适用条件等全部限定语，不只截核心词；
#   2. 语义粒度——同一指标随某参数变化的取值矩阵汇总为"一条"，而非按行炸成多条。
_SOURCE_VALUE_GUIDE = """\
### source_value 填写规范（重要）
- **信息充分、语义自洽（首要）**：source_value 不能只写一个干瘪的数值/短语，必须写成**自洽、可独立解读**的完整描述，让读者单看 source_value（不看指标名）就能明白这是在规定什么、针对哪个对象/状态/条件下的什么性能。原则上：能写完整一句话就写完整一句话，把指标名所指的性能项、被测对象、适用范围、限定条件与具体取值一并组织进 source_value。**判定标准**：若 source_value 单独抽出后无法判断它与指标名称的关联（如只写"≥370""0.5""合格"等纯数值或单字），视为不合格，必须补充上下文扩充为完整描述（如"钢管母材纵向抗拉强度 ≥370 MPa"而非"≥370"）。
- **完整保真**：在信息充分的前提下，source_value 必须是对该指标规定内容的完整、自洽转述，保留全部限定条件，保留句子前后所引用的标准号、等效/替代选项（"或…"）、适用条件（"当…时"）、牌号/材质及其来源等，不得只截取核心词而丢弃限定语
- 表格是"同一指标在不同条件下的取值矩阵"（如不同电压下的电阻、不同温度下的抗拉强度、不同规格/等级下的尺寸公差、不同频率下的衰减等）→ 提取为**一条**指标，indicator_object 用该指标的通用名（如"电阻""外径"），把"条件→取值"的对应关系整体汇总写入 source_value（如"12 V:≥100 Ω；24 V:≥200 Ω"或"DN50:φ50±0.5 mm；DN80:φ80±0.8 mm"），**不要**按行拆成多条指标
- 仅当表格各行代表**不同的指标项 / 被测对象**（不同性能项目、不同部件或材料）时，才按项拆分为多条指标
- 无论汇总为一条还是拆成多条，都必须把实际数值展开写入 source_value；**绝对不得**用"见表X""如表所示""符合表X规定"等引用性描述代替具体值
- 规定值在附录中（如"按附录A.1计算""见附录B"）→ 查阅附录内容，把实际计算公式或数值填入，**绝对不得**保留"按附录X计算""见附录X"等引用性描述
- 指标的牌号/等级/材质/方法引用了外部标准（GB/T、GB、HB 等）→ 保留标准号原文，连同牌号/方法一起写入 source_value，不要只留牌号或只留标准号
- 表格 / 附录内容确实无法确定具体值 → source_value 填空字符串 ""，不提取该指标
- 正文中若仍残留对图片/表格/附录的引用标记（如 `[REF:图片:...]`）而未被替换为具体文字内容，说明该图片/表格解析失败、无实际取值可查：涉及该引用的指标**不得提取**，**绝对不得**直接把"见图X""见表X.X""见附录X"等引用原文当作 source_value 输出"""

# ── indicator_object 命名规范（快慢路径 + 两个接口共用，改一处全局生效）──────────
# 两条核心约定：
#   1. 同名互斥——输出结果中指标名不得重复（含同义/去空白标点后同名），输出前强制自查；
#   2. 合并优先、命名区分兜底——同一参数随条件变化的多条合并为一条（条件→值写入
#      source_value）；确属独立性能项时在名称上加限定词区分，名称必须自描述。
_INDICATOR_NAMING_GUIDE = """\
### indicator_object（指标名称）填写规范（重要）
- **同名互斥（输出前强制自查）**：逐条检查输出结果，indicator_object **不允许重复**（包括同义表述、或去除空白/标点后相同的名称）。发现重名时，必须按下列两种方式之一处理，**绝对不得**把多条重名指标原样并列输出：
  1. **合并为一条（优先）**：多条仅是同一性能参数随条件（维度、方向、方位、部位、等级、规格、状态、适用场景等）取值不同 → 合并为一条指标：indicator_object 用该参数的通用名，把"条件→取值"的对应关系整体写入 source_value（如"垂直：≤X m；水平：≤Y m""输入端：≥A V；输出端：≥B V""常温：≥C MPa；高温：≥D MPa"）；
  2. **命名区分**：仅当各条确属不同的性能项 / 不同被测对象（语义不同、无法用同一名称概括）时，才保留多条，并在名称中加入区分性限定词（方向、维度、部位、组分、输入/输出等），使名称**自描述**——单看名称即可明白在规定哪个条件下的哪个参数（如区分为"垂直XX精度""水平XX精度"），仍不得出现重名。
- **判定标准**：凡正文 / 表格 / 条款按维度、方向、部位、条件分别规定同一参数的取值，无论拆合，最终输出都不得产生多条相同名称——要么把条件并入 source_value（规则 1），要么把条件差异体现到名称中（规则 2）"""

# ── 并发控制 ──────────────────────────────────────────────────────────────────
_LLM_SEM = asyncio.Semaphore(20)

# ── MinerU 兜底：DB + 外部 API 均未命中时，直接解析图片 ───────────────────────
# 与定时任务 fill_image_text 共用同一图片服务地址（settings.JGH_IMAGE_BASE_URL）
_MINERU_IMAGE_BASE_URL = settings.JGH_IMAGE_BASE_URL
# MinerU 单次解析 10-60s，限制并发避免拖垮快路径
_MINERU_SEM = asyncio.Semaphore(3)

# ── 图片引用正则 ──────────────────────────────────────────────────────────────
_RE_IMG_REF = re.compile(r"\[REF:图片:([^\]]+)\]")
_RE_IMG_PLAIN = re.compile(r"\[图片:([^\]]+)\]")
_RE_NORM = re.compile(r"[^\w一-鿿]+")
# 原始 <img> 标签（图片/表格 OCR 结果中可能嵌套的子图片引用，禁止原样流入指标/试验文本）
_RE_RAW_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


def _strip_raw_img_tags(text: str) -> str:
    """去除文本中残留的原始 <img> 标签，避免图片/表格 OCR 结果内嵌套的子图片引用原样暴露给用户。"""
    return _RE_RAW_IMG_TAG.sub("", text).strip() if text else text

# ── 跳过章节关键词 ────────────────────────────────────────────────────────────
_SKIP_TITLES = frozenset({
    "前言", "引言", "规范性引用文件",
    "术语和定义", "术语、定义和缩略语", "术语和定义及缩略语",
    "参考文献", "索引",
})
_SKIP_KW = ("资料性附录",)
_SKIP_KW_PAIRS = (("术语", "定义"), ("术语", "缩略语"))

# 试验提取单块最大字符数（超过此值按章节边界切分）
_MAX_TEST_CHUNK = 30_000


# ── HTML → 紧凑文本 ───────────────────────────────────────────────────────────

class _TableParser(HTMLParser):
    """将单个 <table> 解析为 Markdown 表格。"""

    def __init__(self):
        super().__init__()
        self._rows: list[list[str]] = []
        self._cur_row: list[str] = []
        self._cur_cell: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cur_row = []
        elif tag in ("td", "th"):
            self._cur_cell = []
            self._in_cell = True

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._cur_row.append("".join(self._cur_cell).strip())
            self._in_cell = False
        elif tag == "tr":
            if self._cur_row:
                self._rows.append(self._cur_row)

    def handle_data(self, data):
        if self._in_cell:
            self._cur_cell.append(data)

    def to_markdown(self) -> str:
        if not self._rows:
            return ""
        lines = []
        for i, row in enumerate(self._rows):
            lines.append("| " + " | ".join(row) + " |")
            if i == 0:
                lines.append("| " + " | ".join(["---"] * len(row)) + " |")
        return "\n".join(lines)


def _html_to_compact(html: str) -> str:
    """HTML → 紧凑文本：表格→Markdown，img→占位符，去除其余标签保留文本。"""
    if not html:
        return ""

    def _img_to_ref(m: re.Match) -> str:
        src_m = re.search(r'src=["\']([^"\']+)["\']', m.group(), re.IGNORECASE)
        if src_m:
            fname = src_m.group(1).split("/")[-1].split("?")[0]
            if fname:
                return f"[REF:图片:{fname}]"
        return ""

    html = re.sub(r"<img[^>]*/?>", _img_to_ref, html, flags=re.IGNORECASE)

    def _conv_table(m: re.Match) -> str:
        p = _TableParser()
        p.feed(m.group())
        md = p.to_markdown()
        return ("\n" + md + "\n") if md else ""

    html = re.sub(r"<table[^>]*>.*?</table>", _conv_table, html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(
        r"<(?:br|p|div|li|h[1-6])[^>]*/?>|</(?:p|div|li|h[1-6])>",
        "\n", html, flags=re.IGNORECASE,
    )
    html = re.sub(r"<[^>]+>", "", html)
    for ent, ch in (("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"), ("&quot;", '"')):
        html = html.replace(ent, ch)
    html = re.sub(r"\n{3,}", "\n\n", html)
    html = re.sub(r"[ \t]+", " ", html)
    return html.strip()


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _fetch_standard_meta(standard_no: str) -> dict:
    """读取标准基本信息 + 完整目录（含各章节 word_count）。"""
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT cname, use_range, name FROM standard_jgh_pdf WHERE standard_no = %s LIMIT 1",
                [standard_no],
            )
            meta = await cur.fetchone() or {}
            await cur.execute(
                """
                SELECT a.title_no, a.title, LENGTH(COALESCE(a.word, '')) AS word_count
                FROM standard_jgh_pdf_chapter a
                JOIN standard_jgh_pdf b ON b.main_task_id = a.main_task_id
                WHERE b.standard_no = %s
                ORDER BY a.id
                """,
                [standard_no],
            )
            toc = list(await cur.fetchall())

    pdf_name = meta.get("name") or ""
    storage_path = pdf_name.rsplit(".", 1)[0] if "." in pdf_name else pdf_name
    return {
        "standard_no": standard_no,
        "name": (meta.get("cname") or ""),
        "use_range": (meta.get("use_range") or "")[:500],
        "storage_path": storage_path,
        "toc": toc,
    }


async def _fetch_all_chapters(standard_no: str) -> list[dict]:
    """读取标准全部章节（title_no, title, word）。"""
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                """
                SELECT a.title_no, a.title, a.word
                FROM standard_jgh_pdf_chapter a
                JOIN standard_jgh_pdf b ON b.main_task_id = a.main_task_id
                WHERE b.standard_no = %s
                ORDER BY a.id
                """,
                [standard_no],
            )
            return list(await cur.fetchall())


async def _fetch_chapters_by_nos(standard_no: str, chapter_nos: list[str]) -> list[dict]:
    """读取指定章节及其所有子章节。"""
    if not chapter_nos:
        return []
    ors: list[str] = []
    params: list = [standard_no]
    for cn in chapter_nos:
        ors.append("(a.title_no = %s OR a.title_no LIKE %s)")
        params += [cn, f"{cn}.%"]
    sql = f"""
        SELECT a.title_no, a.title, a.word
        FROM standard_jgh_pdf_chapter a
        JOIN standard_jgh_pdf b ON b.main_task_id = a.main_task_id
        WHERE b.standard_no = %s AND ({' OR '.join(ors)})
        ORDER BY a.id
    """
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            return list(await cur.fetchall())


# ── LLM 调用 ──────────────────────────────────────────────────────────────────

async def _llm_call(prompt: str) -> str:
    """单次 LLM 调用，带并发信号量。"""
    async with _LLM_SEM:
        from app.langchain.llm_providers import get_llm
        llm = get_llm()
        resp = await llm.ainvoke(prompt)
        return resp.content if hasattr(resp, "content") else str(resp)


def _parse_json_array(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    # 截断恢复：逐条解析已完整输出的 item
    items = []
    for item_m in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        try:
            obj = json.loads(item_m.group())
            if obj.get("indicator_object") or obj.get("test_name"):
                items.append(obj)
        except Exception:
            continue
    return items


def _parse_json_obj(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return {}


# ── 图片引用处理 ──────────────────────────────────────────────────────────────

async def _fetch_img_word(storage_path: str, filename: str) -> str:
    """获取图片/表格文本，两级来源，任一命中非空即返回：

    1. 本地 DB（standard_jgh_pdf_table / standard_jgh_pdf_formula 的 word 字段）
    2. 外部 get-config-item 接口现场识别

    MinerU 兜底层已停用（见末尾注释）：DB + 外部 API 均未命中视为无文本。
    """
    if not storage_path or not filename:
        return ""

    main_task_id: Optional[int] = None  # 供 MinerU 兜底成功后回填 DB 定位记录

    # 1. 本地 DB 查询（standard_jgh_pdf_table + standard_jgh_pdf_formula）
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT main_task_id FROM standard_jgh_pdf WHERE name LIKE %s LIMIT 1",
                    [f"{storage_path}%"],
                )
                pdf_row = await cur.fetchone()
                if pdf_row and pdf_row.get("main_task_id"):
                    main_task_id = pdf_row["main_task_id"]
                    await cur.execute(
                        """
                        SELECT word FROM standard_jgh_pdf_table
                        WHERE main_task_id = %s AND (file_name = %s OR file_name LIKE %s)
                          AND word IS NOT NULL AND word != ''
                        UNION ALL
                        SELECT word FROM standard_jgh_pdf_formula
                        WHERE main_task_id = %s AND (file_name = %s OR file_name LIKE %s)
                          AND word IS NOT NULL AND word != ''
                        LIMIT 1
                        """,
                        [main_task_id, filename, f"%/{filename}", main_task_id, filename, f"%/{filename}"],
                    )
                    row = await cur.fetchone()
                    if row and row.get("word"):
                        logger.debug(f"[FastExtract] {filename} 命中本地 DB")
                        return _strip_raw_img_tags(row["word"].strip())
    except Exception as e:
        logger.debug(f"[FastExtract] 本地 DB 查询失败，fallback 外部 API: {e}")

    # 2. fallback：调用外部 API（失败或无文本时返回 ""，不再走 MinerU 兜底）
    url = f"{settings.JGH_PDF_API_BASE}/standard/jgh-pdf/get-config-item"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                params={"storagePath": storage_path, "imgPath": f"images/{filename}"},
                headers={"X-App-Id": settings.JGH_PDF_APP_ID},
                timeout=30.0,
            )
            if resp.status_code != 200:
                logger.warning(f"[FastExtract] 图片 {filename} 请求失败: HTTP {resp.status_code}, URL: {url}, 响应: {resp.text[:500]}")
            else:
                data = resp.json()
                code = data.get("code")
                if code == 403:
                    logger.error("[FastExtract] AppId 无效，请检查 JGH_PDF_APP_ID 配置")
                    return ""
                if code != 0:
                    logger.warning(f"[FastExtract] {filename} API错误: {data.get('msg')}")
                    return ""
                obj = data.get("data") or {}
                if obj.get("type") == "none":
                    return ""
                table_body = (obj.get("table_body") or "").strip()
                if table_body == "[IMAGE_ONLY]":
                    logger.debug(f"[FastExtract] {filename} 图片识别结果为[IMAGE_ONLY]，视为无文本")
                    return ""
                if table_body:
                    return _strip_raw_img_tags(table_body)
    except Exception as e:
        logger.warning(f"[FastExtract] 图片 {filename} 请求失败: URL: {url}, 错误: {type(e).__name__}: {e}")

    # 3. 最终兜底：MinerU 解析图片（与定时任务 fill_image_text 同源），成功后回填 DB word
    # ── 已停用 ──────────────────────────────────────────────────────────────
    # 纯图片（外观照/示意图/原理图等）对标准指标提取价值有限，承载指标取值的表格 / 公式
    # 已在 DB 层（standard_jgh_pdf_table / standard_jgh_pdf_formula）解析完毕；外部
    # get-config-item 接口还能兜一层现场表格识别。MinerU 单张 10-60s、并发上限 3，
    # 落到快路径会严重拖慢整体，且其额外解析出的纯图片文本对指标提取增益甚微，
    # 故注释掉 MinerU 兜底层。DB + 外部 API 均未命中的图片视为无文本，按 prompt
    # 约定相关指标直接不提取。
    # return await _fetch_img_word_via_mineru(filename, main_task_id)
    return ""


async def _fetch_img_word_via_mineru(filename: str, main_task_id: Optional[int]) -> str:
    """DB 与外部 API 均未命中时，调 MinerU 解析图片文本；成功则回填 DB word 供后续命中。"""
    from app.utils.mineru import MinerUError, convert_to_markdown

    img_url = _MINERU_IMAGE_BASE_URL + filename.lstrip("/")
    try:
        async with _MINERU_SEM:
            markdown = (await convert_to_markdown(img_url)).strip()
    except MinerUError as e:
        logger.warning(f"[FastExtract] {filename} MinerU 解析失败: {e}")
        return ""
    except Exception as e:
        logger.warning(f"[FastExtract] {filename} MinerU 调用异常: {type(e).__name__}: {e}")
        return ""

    if not markdown:
        return ""
    markdown = _strip_raw_img_tags(markdown)
    if not markdown:
        return ""

    logger.info(f"[FastExtract] {filename} 命中 MinerU 兜底解析（{len(markdown)} 字）")
    if main_task_id is not None:
        await _backfill_img_word(main_task_id, filename, markdown)
    return markdown


async def _backfill_img_word(main_task_id: int, filename: str, markdown: str) -> None:
    """将 MinerU 解析结果回填到 table / formula 表的 word 字段（只填空记录，不覆盖已有）。

    与定时任务 fill_image_text 语义一致：命中哪张表更新哪张，供后续同图直接命中本地 DB。
    """
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor() as cur:
                for tbl in ("standard_jgh_pdf_table", "standard_jgh_pdf_formula"):
                    await cur.execute(
                        f"""
                        UPDATE {tbl} SET word = %s
                        WHERE main_task_id = %s AND (file_name = %s OR file_name LIKE %s)
                          AND (word IS NULL OR word = '')
                        """,
                        [markdown, main_task_id, filename, f"%/{filename}"],
                    )
    except Exception as e:
        logger.warning(f"[FastExtract] {filename} MinerU 结果回填 DB 失败: {e}")


_IMG_RESOLVE_PROMPT = """\
以下是标准图片/表格经解析后的 Markdown 文本：

{markdown_text}

以下指标的 source_value 中含对该图片/表格的引用，请根据上方内容填入具体数值。

{need_json}

规则：
1. 找到对应指标的具体规定值（含单位）
2. 若同一指标有多个规格，用"；"分隔全部列出
3. 图片/表格中确实没有该指标的具体值时，resolved_value 留空字符串

只输出 JSON 数组（不要 markdown 代码块）：
[{{"index": 0, "resolved_value": "≥370 MPa"}}]
"""

_TEST_IMG_RESOLVE_PROMPT = """\
以下是标准图片/表格经解析后的 Markdown 文本：

{markdown_text}

以下试验字段中含对该图片/表格的引用，请根据上方内容替换为具体内容。

{need_json}

字段说明：field 是试验的哪个字段（conditions/preparation/procedure/acceptance），current_value 是当前含引用的值。

规则：
1. 将引用替换为图片/表格中对应的实际内容
2. 图片中无对应内容时，resolved_value 留空字符串（则保留原值不变）

只输出 JSON 数组（不要 markdown 代码块）：
[{{"index": 0, "field": "acceptance", "resolved_value": "≥370 MPa"}}]
"""


def _scan_img_refs_in_dicts(items: list[dict], fields: list[str]) -> dict[str, list[tuple[int, str]]]:
    """扫描 dict 列表的指定字段，返回 {图片文件名: [(item_index, field_name), ...]}。"""
    result: dict[str, list[tuple[int, str]]] = {}
    for idx, item in enumerate(items):
        for field in fields:
            val = item.get(field) or ""
            for m in _RE_IMG_REF.finditer(val):
                src = m.group(1).strip().split("/")[-1].split("?")[0] or m.group(1).strip()
                result.setdefault(src, []).append((idx, field))
    return result


async def _resolve_one_img_in_indicators(
    img_src: str, refs: list[tuple[int, str]], items: list[dict], storage_path: str
) -> None:
    """解析单张图片引用，原地更新 items[idx][field] 中的指标 source_value。"""
    filename = img_src.split("/")[-1].split("?")[0] or img_src
    word_text = await _fetch_img_word(storage_path, filename)
    if not word_text:
        return

    need = [
        {"index": idx, "indicator_object": items[idx].get("indicator_object", ""),
         "current_value": items[idx].get("source_value", "")}
        for idx, _ in refs
    ]
    prompt = _IMG_RESOLVE_PROMPT.format(
        markdown_text=word_text[:4000],
        need_json=json.dumps(need, ensure_ascii=False),
    )
    text = await _llm_call(prompt)
    for item in _parse_json_array(text):
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        val = item.get("resolved_value", "")
        if idx is not None and val and 0 <= idx < len(items):
            old = items[idx].get("source_value") or ""
            items[idx]["source_value"] = _RE_IMG_REF.sub(val, old, count=1)


async def _resolve_one_img_in_tests(
    img_src: str, refs: list[tuple[int, str]], items: list[dict], storage_path: str
) -> None:
    """解析单张图片引用，原地更新 items[idx][field] 中的试验字段。"""
    filename = img_src.split("/")[-1].split("?")[0] or img_src
    word_text = await _fetch_img_word(storage_path, filename)
    if not word_text:
        return

    need = [
        {"index": idx, "field": field,
         "current_value": items[idx].get(field, "")}
        for idx, field in refs
    ]
    prompt = _TEST_IMG_RESOLVE_PROMPT.format(
        markdown_text=word_text[:4000],
        need_json=json.dumps(need, ensure_ascii=False),
    )
    text = await _llm_call(prompt)
    for item in _parse_json_array(text):
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        field = item.get("field")
        val = item.get("resolved_value", "")
        if idx is not None and field and val and 0 <= idx < len(items):
            old = items[idx].get(field) or ""
            items[idx][field] = _RE_IMG_REF.sub(val, old, count=1)


async def _resolve_img_refs_inline(text: str, storage_path: str) -> str:
    """快路径：将 compact 文本中的 [REF:图片:xxx] 替换为实际图片文本（内联进 prompt）。"""
    seen: set[str] = set()
    filenames: list[str] = []
    for m in _RE_IMG_REF.finditer(text):
        fn = m.group(1).strip().split("/")[-1].split("?")[0]
        if fn and fn not in seen:
            seen.add(fn)
            filenames.append(fn)
    if not filenames:
        return text

    words = await asyncio.gather(*[_fetch_img_word(storage_path, fn) for fn in filenames])
    word_map = dict(zip(filenames, words))

    def _replace(m: re.Match) -> str:
        src = m.group(1).strip()
        fn = src.split("/")[-1].split("?")[0] or src
        return word_map.get(fn, "")

    return _RE_IMG_REF.sub(_replace, text)


def _strip_img_refs(items: list[dict], fields: list[str]) -> None:
    """兜底：剥除残留的图片占位符外壳及原始 <img> 标签（正常情况下提取前已解析，不应触发），保留可读标记。

    同时处理 [REF:图片:...] 与 [图片:...] 两种占位符写法（与 compare-smart-v2 一致）。
    """
    for item in items:
        for field in fields:
            val = item.get(field) or ""
            if not val:
                continue
            if "[REF:图片:" in val or "[图片:" in val:
                val = _RE_IMG_REF.sub(lambda m: m.group(1), val)
                val = _RE_IMG_PLAIN.sub(lambda m: m.group(1), val).strip()
            if "<img" in val.lower():
                val = _strip_raw_img_tags(val)
            item[field] = val



# ── 跳过逻辑 + 全文 compact 构建 ─────────────────────────────────────────────

def _should_skip(title: str) -> bool:
    t = (title or "").strip()
    return (
        t in _SKIP_TITLES
        or any(kw in t for kw in _SKIP_KW)
        or any(a in t and b in t for a, b in _SKIP_KW_PAIRS)
    )


async def _build_full_compact(standard_no: str, chapters: list[dict]) -> str:
    """所有技术章节 HTML → 拼接紧凑文本（跳过前言/术语等无关章节）。"""
    # 先收集需跳过的父章节编号
    skip_prefixes: set[str] = set()
    for ch in chapters:
        if _should_skip(ch.get("title") or ""):
            tn = (ch.get("title_no") or "").strip()
            if tn:
                skip_prefixes.add(tn)

    def _should_skip_ch(ch: dict) -> bool:
        if _should_skip(ch.get("title") or ""):
            return True
        tn = (ch.get("title_no") or "").strip()
        return bool(tn and any(tn == p or tn.startswith(p + ".") for p in skip_prefixes))

    parts: list[str] = []
    for ch in chapters:
        if _should_skip_ch(ch):
            continue
        compact = _html_to_compact(ch.get("word") or "")
        if compact.strip():
            parts.append(f"### [{ch['title_no']}] {ch['title']}\n{compact}")

    return "\n\n".join(parts)


# ── 慢路径：分组规划 ──────────────────────────────────────────────────────────

_GROUP_PLANNER_PROMPT = """\
你是分组规划专家，只负责为指标/试验提取生成可执行分组，不负责提取字段。

## 标准信息
- standard_no: {standard_no}
- standard_name: {standard_name}
- use_range: {use_range}

## 章节目录（title_no  标题  字数）
{toc_text}

## 输出格式（纯 JSON，不要 markdown 代码块，不要其他文字）
{{
  "standard_structure_type": "has_ind_and_test | has_ind_only | has_test_only | ind_embedded_in_test",
  "standard_object": "标准直接规定的最具体对象，如钢管、光伏背板",
  "applicable_object": "standard_object 的上级使用主体，无则留空",
  "groups": [
    {{"group_name": "外观与尺寸", "chapter_nos": ["4.1", "4.2"], "reason": "一句话说明"}},
    {{"group_name": "拉伸强度", "chapter_nos": ["4.3", "5.3"], "reason": "技术要求与对应试验合并"}}
  ]
}}

## standard_structure_type 定义
- has_ind_and_test：有技术要求章节（规定指标值），且有独立试验方法章节
- has_ind_only：只有技术要求/性能要求，无独立试验方法章节
- has_test_only：只有试验规程，无判定准则（纯方法类标准）
- ind_embedded_in_test：指标与试验混写在同一章节，无法分离

## 硬规则
1. chapter_nos 只填章节号；每组至少一个。
2. 禁止以流程阶段词单独成组：预处理、初始测试、最终测试、检查维护、结果计算、性能评价。
3. 共用章节（通用条件、取样方法等）并入每个用到它的组，允许跨组重复引用。
4. 同名试验、不同对象，必须拆分为独立组。
5. 最小必要分组：能合并就合并，不得为"工整"而拆。
6. 有对应试验方法的技术要求：技术要求章节和试验章节合并为同一组。
7. 无对应试验的技术要求（外观、尺寸、标识等）：单独成组，不得丢弃。
8. 必须排除：前言、引言、规范性引用文件、术语和定义、参考文献、资料性附录——及其所有子章节（如章节3是"术语和定义"，则3.1、3.2、3.3等子章节也一律排除，即使其标题看起来像技术指标名称）。
"""


async def _plan_groups(meta: dict) -> dict:
    """单次 LLM 调用完成结构类型判断 + 分组规划，返回原始 dict。"""
    toc_text = "\n".join(
        f"  {c['title_no']}  {c['title']}  ({c.get('word_count', 0)} 字)"
        for c in meta["toc"]
    )
    prompt = _GROUP_PLANNER_PROMPT.format(
        standard_no=meta["standard_no"],
        standard_name=meta["name"],
        use_range=meta["use_range"],
        toc_text=toc_text,
    )
    text = await _llm_call(prompt)
    raw = _parse_json_obj(text)
    if not raw:
        logger.warning(f"[FastExtract] {meta['standard_no']} 分组规划解析失败")
    return raw


# ── 慢路径：指标提取 ──────────────────────────────────────────────────────────

_INDICATOR_EXTRACTION_PROMPT = ("""\
你是标准化专家，从标准章节中提取全部技术指标。

## 标准信息
- 标准编号：{standard_no}
- 标准名称：{standard_name}
- 适用范围：{use_range}
- 本组标准化对象：{standard_object}（适用主体：{applicable_object}）

## 章节内容
{chunk_text}

## 提取规则

### 必须提取
- 有具体规定值的技术要求（尺寸、性能、成分、外观、标志、包装贮存等）
- 试验判定准则（合格接受条件）
- 含数量限定的要求（"不少于X个"、"不超过Y%"）
- 功能性要求、逻辑要求

### 必须排除
- 术语定义、说明性文字
- 测试操作步骤本身（"将试样放入..."）
- 管理性要求（"应建立台账..."）

### 字段说明
- indicator_object：指标名称（如"外径"、"碳含量"、"拉伸强度"）；名称不得重复、同名合并/区分规则见下方【indicator_object（指标名称）填写规范】
- source_value：规定值含单位（如"≥500 MPa"、"φ50±0.5 mm"）；source_value 的完整保真、表格 / 附录 / 引用展开规则见下方【source_value 填写规范】
- indicator_type：inherent（固有指标，独立于任何试验，如尺寸/成分/外观）|
  experimental（试验指标，需依附试验，如耐压试验后结果/冲击后漏电流）
- standard_object：指标直接规定的对象（最具体的名称，如"钢管"而非"钢管产品"）
- applicable_object：standard_object 的上级使用主体，无则留空
- source_clause：来源条款号（如"4.3.1"，**严禁**包含章节标题文字、正文内容或任何 HTML/图片标签，如 `<img ...>`），必须取自该指标所在 `### [x.x]` 章节标题中的编号，不要自行推断
- **object_type / norm_class / indicator_category（三字段联动）**：从以下分类树中选一条路径，三字段必须属于同一分支：
""" + _TAXONOMY_TREE_DESC + """

""" + _SOURCE_VALUE_GUIDE + """

""" + _INDICATOR_NAMING_GUIDE + """

## 输出（纯 JSON 数组，不要 markdown 代码块，不要其他文字）
[
  {{
    "indicator_object": "拉伸强度",
    "source_value": "Q235≥370 MPa；Q345≥470 MPa",
    "indicator_type": "inherent",
    "standard_object": "钢管",
    "applicable_object": "",
    "source_clause": "4.3.1",
    "object_type": "产品类对象",
    "indicator_category": "外在特性",
    "norm_class": "规范类要素"
  }},
  {{
      "indicator_object": "壁厚",
      "source_value": "DN15: 1.5mm; DN20: 2.0mm; DN25: 2.5mm; DN32: 3.0mm",
      "indicator_type": "inherent",
      "standard_object": "钢管",
      "applicable_object": "",
      "source_clause": "4.2",
      "object_type": "产品类对象",
      "norm_class": "规范类要素",
      "indicator_category": "外在特性"
    }}
]
""")


async def _build_chunk_text(
    standard_no: str, chapter_nos: list[str], toc_summary: str, storage_path: str = ""
) -> str:
    """读取分组章节 HTML，转紧凑文本，附 TOC 概览。图片引用在发给 LLM 前内联解析，失败则移除。"""
    chapters = await _fetch_chapters_by_nos(standard_no, chapter_nos)
    parts = [
        f"### [{ch['title_no']}] {ch['title']}\n{_html_to_compact(ch.get('word') or '')}"
        for ch in chapters
        if (ch.get('word') or '').strip()
    ]
    chunk_text = "\n\n".join(parts)
    chunk_text = await _resolve_img_refs_inline(chunk_text, storage_path)
    return f"【章节目录概览】\n{toc_summary}\n\n【本组章节内容】\n{chunk_text}"


async def _extract_indicators_from_chunk(
    standard_no: str,
    standard_name: str,
    use_range: str,
    standard_object: str,
    applicable_object: str,
    chunk_text: str,
) -> list[dict]:
    """单组指标提取，1 次 LLM 调用。"""
    prompt = _INDICATOR_EXTRACTION_PROMPT.format(
        standard_no=standard_no,
        standard_name=standard_name,
        use_range=use_range,
        standard_object=standard_object,
        applicable_object=applicable_object,
        chunk_text=chunk_text,
    )
    text = await _llm_call(prompt)
    result = _parse_json_array(text)
    return [r for r in result if isinstance(r, dict) and r.get("indicator_object")]


def _merge_indicators(chunk_results: list[list[dict]]) -> list[dict]:
    """合并多组提取结果，按 (indicator_object规范化, standard_object规范化) 去重。"""
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []
    for chunk in chunk_results:
        for ind in chunk:
            key = (
                _RE_NORM.sub("", (ind.get("indicator_object") or "")).lower(),
                _RE_NORM.sub("", (ind.get("standard_object") or "")).lower(),
            )
            if key[0] and key not in seen:
                seen.add(key)
                merged.append(ind)
    return merged


# ── 慢路径：试验提取 ──────────────────────────────────────────────────────────

_TEST_EXTRACTION_PROMPT = ("""\
你是标准化专家，从以下标准章节中提取全部试验条目（依据 GB/T 20001.4-2015）。

标准编号：{standard_no}
标准名称：{standard_name}

章节内容：
{text}

## 提取规则
- 只提取有完整试验过程描述的条目（含步骤、操作方法或判断依据）
- 纯技术指标（尺寸、成分等固有属性）不提取，只提取试验类内容
- test_name 不含"试验"后缀（如填"拉伸强度"而非"拉伸强度试验"）
- 各字段无对应内容时留空字符串，不得填 null
- indicators 字段：有 acceptance 时从中提取（indicator_object=指标名称，source_value=判据内容），无 acceptance 时留空数组
- acceptance / indicators[].source_value 中若仍残留对图片/表格/附录的引用标记（如 `[REF:图片:...]`）而未被替换为具体文字内容，说明该图片/表格解析失败：**绝对不得**直接把"见图X""见表X.X""见附录X"等引用原文当作判据值输出，该字段留空字符串
- 分类字段（object_type / indicator_category，每条必填，含义同指标字段，无 norm_class）：
""" + _CLASSIFICATION_GUIDE.split("\n- norm_class")[0] + """

只输出纯 JSON 数组，不要 markdown 代码块，不要其他文字：
[
  {{
    "test_name": "拉伸强度",
    "method_desc": "按 GB/T 228.1 方法，万能试验机测定",
    "conditions": "室温（23±2）℃，相对湿度 50%±10%",
    "preparation": "从成品截取试样，长度不小于 200 mm",
    "procedure": "将试样固定于夹具，以 5 mm/min 速率均匀施加载荷至断裂，记录最大载荷",
    "acceptance": "抗拉强度≥370 MPa",
    "report_items": "试样编号、试验速率、最大载荷、抗拉强度计算值",
    "source_clause": "6.3",
    "standard_object": "钢管",
    "applicable_object": "",
    "object_type": "产品类对象",
    "indicator_category": "性能",
    "indicators": [
      {{"indicator_object": "拉伸强度", "source_value": "≥370 MPa"}}
    ]
  }}
]
""")


def _split_by_chapters(text: str, max_chunk: int = _MAX_TEST_CHUNK) -> list[str]:
    """将带 '### [...]' 章节标题的 compact 文本按章节边界切分，每块 ≤ max_chunk 字符。"""
    sections = re.split(r"(?=\n### \[)", text)
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len: int = 0
    for sec in sections:
        if current_len + len(sec) > max_chunk and current_parts:
            chunks.append("".join(current_parts))
            current_parts = [sec]
            current_len = len(sec)
        else:
            current_parts.append(sec)
            current_len += len(sec)
    if current_parts:
        chunks.append("".join(current_parts))
    return [c for c in chunks if c.strip()]


async def _extract_tests_from_chunk(standard_no: str, standard_name: str, chunk_text: str) -> list[dict]:
    """单块试验提取，1 次 LLM 调用。"""
    if not chunk_text.strip():
        return []
    prompt = _TEST_EXTRACTION_PROMPT.format(
        standard_no=standard_no,
        standard_name=standard_name,
        text=chunk_text,
    )
    text = await _llm_call(prompt)
    result = _parse_json_array(text)
    return [r for r in result if isinstance(r, dict) and r.get("test_name")]


def _merge_tests(chunk_results: list[list[dict]]) -> list[dict]:
    """合并多块试验，按 (test_name规范化, source_clause) 去重。"""
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []
    for chunk in chunk_results:
        for t in chunk:
            key = (
                _RE_NORM.sub("", (t.get("test_name") or "")).lower(),
                (t.get("source_clause") or "").strip(),
            )
            if key[0] and key not in seen:
                seen.add(key)
                merged.append(t)
    return merged


async def _extract_all_tests(standard_no: str, standard_name: str, compact_text: str) -> list[dict]:
    """从全文 compact 文本中提取所有试验，自动切块并行。"""
    if not compact_text.strip():
        return []
    chunks = _split_by_chapters(compact_text)
    if not chunks:
        return []
    results = await asyncio.gather(*[
        _extract_tests_from_chunk(standard_no, standard_name, c)
        for c in chunks
    ])
    tests = _merge_tests(list(results))
    logger.info(f"[FastExtract] {standard_no} 试验提取 {len(tests)} 条（{len(chunks)} 块并行）")
    return tests


# ── 快路径：并行指标/试验提取 Prompt ──────────────────────────────────────────

_FAST_IND_PROMPT = (
    """\
你是资深标准化专家，逐章节仔细阅读以下标准全文，提取**全部**技术指标，不得遗漏。

## 标准信息
- 标准编号：{standard_no}
- 标准名称：{standard_name}
- 适用范围：{use_range}

## 全文章节内容
{full_text}

---

## 字段说明
- standard_object：标准直接规定的最具体对象（如"钢管"）；applicable_object：上级使用主体，无则留空
- indicator_object：指标名称；名称不得重复、同名合并/区分规则见下方【indicator_object（指标名称）填写规范】
- source_value：规定值含单位；source_value 的完整保真、表格 / 附录 / 引用展开规则见下方【source_value 填写规范】
- indicator_type：inherent（固有指标，如尺寸/成分/外观）| experimental（试验指标，如耐压后漏电流）
- source_clause：来源章节号（如"4.3.1"、"A.1" **严禁**包含章节标题文字、正文内容或任何 HTML/图片标签，如 `<img ...>`），必须取自该指标所在 `### [x.x]` 章节标题中的编号，不要自行推断
- **object_type / norm_class / indicator_category（三字段联动）**：从以下分类树中选一条路径，三字段必须属于同一分支：
"""
    + _TAXONOMY_TREE_DESC
    + """

### 提取原则（指标类型宁多勿少，但同一指标的不同取值应合并，避免过度拆分）

### 必须提取的类型
- 尺寸、公差、形位公差等几何要求
- 力学、理化、电气、热学等性能要求
- 化学成分、纯度、含量等成分要求
- 外观、表面质量、缺陷限制等感官要求
- 标识、标志、标签的规定内容、位置、方式
- 包装材料、包装方式、包装防护等包装要求
- 贮存温度、湿度、期限等贮存条件
- 运输要求
- 试验判定准则（合格/不合格判据）
- 含数量限定的要求（"不少于X个"、"不超过Y%"）
- 无单位定性要求（"外观应符合..."、"表面不应有..."）
- 抽样数量或比例要求
- 功能性要求、逻辑要求

"""
    + _SOURCE_VALUE_GUIDE
    + """

"""
    + _INDICATOR_NAMING_GUIDE
    + """

### 必须排除（仅排除以下，其余一律提取）
- 纯操作步骤（"将试样夹持于..."、"以X mm/min 速率施加..."）
- 纯管理流程要求（"应建立台账"、"记录应保存X年"）
- **忽略术语和定义**：条款号为 x.y 且内容为名词定义（无量值要求、无合格判据）的，均不提取

- has_test_only 标准：从各试验 acceptance 中提取指标，indicator_type 均为 experimental
- **重要：所有字段必须是非空字符串，不得为 null，无对应内容时填 ""**

只输出纯 JSON，不要 markdown 代码块：
{{
  "standard_object": "钢管",
  "applicable_object": "",
  "object_type": "产品类对象",
  "indicators": [
    {{
      "indicator_object": "外径",
      "source_value": "φ50±0.5 mm",
      "indicator_type": "inherent",
      "standard_object": "钢管",
      "applicable_object": "",
      "source_clause": "4.1",
      "object_type": "产品类对象",
      "norm_class": "规范类要素",
      "indicator_category": "外在特性"
    }},
    {{
      "indicator_object": "壁厚",
      "source_value": "DN15: 1.5mm; DN20: 2.0mm; DN25: 2.5mm; DN32: 3.0mm",
      "indicator_type": "inherent",
      "standard_object": "钢管",
      "applicable_object": "",
      "source_clause": "4.2",
      "object_type": "产品类对象",
      "norm_class": "规范类要素",
      "indicator_category": "外在特性"
    }}
  ]
}}
"""
)

_FAST_TEST_PROMPT = """\
你是资深标准化专家，从以下标准章节中提取全部试验条目（依据 GB/T 20001.4-2015）。

标准编号：{standard_no}
标准名称：{standard_name}

章节内容：
{full_text}

## 提取规则
- 只提取有完整试验过程描述的条目（含步骤、操作方法或判断依据）
- 纯技术指标（尺寸、成分等固有属性）不提取
- test_name 不含"试验"后缀（填"拉伸强度"而非"拉伸强度试验"）
- 各字段无对应内容时留空字符串，不得填 null
- object_type：产品类对象 | 服务类对象 | 过程类对象（根据标准化对象类型判断）
- indicator_category：试验所测指标的分类（如"性能"、"外在特性"、"感官"等），从 object_type 对应分支中选取；无明确分类时填 ""
- acceptance 中若仍残留对图片/表格/附录的引用标记（如 `[REF:图片:...]`）而未被替换为具体文字内容，说明该图片/表格解析失败：**绝对不得**直接把"见图X""见表X.X""见附录X"等引用原文当作判据值输出，acceptance 留空字符串

只输出纯 JSON 数组，不要 markdown 代码块：
[
  {{
    "test_name": "拉伸强度",
    "method_desc": "按 GB/T 228.1 方法，万能试验机测定",
    "conditions": "室温（23±2）℃，相对湿度 50%±10%",
    "preparation": "从成品截取试样，长度不小于 200 mm",
    "procedure": "将试样固定于夹具，以 5 mm/min 速率均匀施加载荷至断裂，记录最大载荷",
    "acceptance": "抗拉强度≥370 MPa",
    "report_items": "试样编号、试验速率、最大载荷、抗拉强度计算值",
    "source_clause": "6.3",
    "standard_object": "钢管",
    "applicable_object": "",
    "object_type": "产品类对象",
    "indicator_category": "性能"
  }}
]
"""


# ── dict → IndicatorItem / TestItem 转换 ────────────────────────────────────

def _to_indicator_item(d: dict) -> IndicatorItem:
    return IndicatorItem(
        indicator_type=d.get("indicator_type") or "",
        standard_object=d.get("standard_object") or "",
        applicable_object=d.get("applicable_object") or "",
        indicator_object=d.get("indicator_object") or "",
        source_value=d.get("source_value") or "",
        source_clause=d.get("source_clause") or "",
        object_type=d.get("object_type") or "",
        indicator_category=d.get("indicator_category") or "",
        norm_class=d.get("norm_class") or "",
    )


def _to_test_item(d: dict) -> TestItem:
    raw_inds = d.get("indicators") or []
    sub_inds = [
        IndicatorItem(
            indicator_object=i.get("indicator_object") or "",
            source_value=i.get("source_value") or "",
            standard_object=d.get("standard_object") or "",
            applicable_object=d.get("applicable_object") or "",
        )
        for i in raw_inds
        if isinstance(i, dict) and i.get("indicator_object")
    ]
    return TestItem(
        test_name=d.get("test_name") or "",
        method_desc=d.get("method_desc") or "",
        conditions=d.get("conditions") or "",
        preparation=d.get("preparation") or "",
        procedure=d.get("procedure") or "",
        acceptance=d.get("acceptance") or "",
        report_items=d.get("report_items") or "",
        source_clause=d.get("source_clause") or "",
        standard_object=d.get("standard_object") or "",
        applicable_object=d.get("applicable_object") or "",
        object_type=d.get("object_type") or "",
        indicator_category=d.get("indicator_category") or "",
        indicators=sub_inds,
    )


# ── 指标↔试验关联（证据门控，LLM 验证）──────────────────────────────────────
# 设计：删除章节号硬过滤；章节号引用 / 方法标准号引用 / 同条款共存 作为正面证据
# 候选信号，由 _generate_link_candidates 确定性生成候选 → LLM 只在小候选集上判定
# （须输出 evidence 原文片段）。零候选指标默认跳过 LLM；残留由 _LINK_FALLBACK_ENABLED
# 控制是否再跑一次批量兜底。保证速度：候选生成纯 CPU、零候选不进 LLM、按对象分组并发。

async def _fetch_clause_texts(standard_no: str, clause_nos: list[str]) -> dict[str, str]:
    """批量读取条款原文，返回 {title_no: compact_text}（全文不截断，供证据抽取）。"""
    if not clause_nos or not standard_no:
        return {}
    chapters = await _fetch_chapters_by_nos(standard_no, clause_nos)
    return {ch["title_no"]: _html_to_compact(ch.get("word") or "") for ch in chapters}


# 候选生成正则：方法标准号 / 章节号引用（至少一个点，避免把普通数值当章节号）
_RE_METHOD_STD = re.compile(r"(?:GB/T|GB|ISO|IEC|ASTM|JIS|JJG|HB|QB|DB|TB|NB)\s*/?\s*\d+(?:[.\-]\d+)*")
_RE_CLAUSE_NUM = re.compile(r"\d+(?:\.\d+)+")


def _norm_std(s: str) -> str:
    return re.sub(r"\s+", "", s).upper()


def _extract_method_stds(text: str) -> set[str]:
    """从文本中抽取方法标准号集合（去空白、大写归一）。"""
    if not text:
        return set()
    return {_norm_std(m.group()) for m in _RE_METHOD_STD.finditer(text)}


def _extract_clause_refs(text: str) -> set[str]:
    """从条款原文中抽取被引用的章节号（形如 6.3 / 5.2.3）。"""
    if not text:
        return set()
    return {m.group() for m in _RE_CLAUSE_NUM.finditer(text)}


def _clause_parts(clause: str) -> list[str]:
    """拆分逗号分隔的章节号（试验 source_clause 可为 '9.1, 9.5'）。"""
    return [p.strip() for p in (clause or "").split(",") if p.strip()]


def _generate_link_candidates(
    indicators: List[IndicatorItem],
    tests: List[TestItem],
    clause_text_map: dict[str, str],
) -> dict[int, list[str]]:
    """确定性候选生成：{indicator_index: [test_name,...]}，三类证据信号并集。

    1. 章节号引用：指标 clause_text 引用试验 source_clause（父子章节双向匹配）
    2. 方法标准引用：指标 clause_text 与试验 method_desc/acceptance 共享方法标准号
    3. 同条款共存：指标 source_clause 与试验 source_clause 共有章节号

    仅生成候选，最终是否关联由 LLM 依据 evidence 判定。
    """
    test_by_name = {t.test_name: t for t in tests if t.test_name}
    test_std: dict[str, set[str]] = {}
    test_clause: dict[str, list[str]] = {}
    for t in tests:
        if not t.test_name:
            continue
        test_std[t.test_name] = _extract_method_stds((t.method_desc or "") + " " + (t.acceptance or ""))
        test_clause[t.test_name] = _clause_parts(t.source_clause)

    candidates: dict[int, list[str]] = {}
    for i, ind in enumerate(indicators):
        text = clause_text_map.get(ind.source_clause or "", "") or ""
        ind_stds = _extract_method_stds(text)
        ind_refs = _extract_clause_refs(text)
        ind_cparts = set(_clause_parts(ind.source_clause))

        hits: list[str] = []
        seen: set[str] = set()
        for tname in test_by_name:
            tc_parts = test_clause.get(tname, [])
            # 信号1：章节号引用（父子双向）
            sig1 = False
            if ind_refs and tc_parts:
                for tc in tc_parts:
                    for ref in ind_refs:
                        if ref == tc or tc.startswith(ref + ".") or ref.startswith(tc + "."):
                            sig1 = True
                            break
                    if sig1:
                        break
            # 信号2：方法标准号交集
            sig2 = bool(ind_stds and (ind_stds & test_std.get(tname, set())))
            # 信号3：同条款共存
            sig3 = bool(ind_cparts and tc_parts and (ind_cparts & set(tc_parts)))
            if sig1 or sig2 or sig3:
                if tname not in seen:
                    seen.add(tname)
                    hits.append(tname)
        if hits:
            candidates[i] = hits
    return candidates


# 零候选残留是否再跑一次批量 LLM 兜底（默认开：捕获"附录A的方法"等非标准号引用）
_LINK_FALLBACK_ENABLED = True


_FAST_LINK_IND_TO_TESTS_PROMPT = """\
你是标准化专家。以下是从同一份标准中独立提取的"技术指标"及其"候选试验方法"。
请判断每条技术指标是否确实对应其候选试验列表中的某个试验。

候选试验（含 test_name, method_desc, source_clause, acceptance）：
{tests_json}

技术指标（含 ind_idx、其 candidate_tests、indicator_object、source_value、source_clause、clause_text）：
{indicators_json}

**关联判据（必须命中下列证据之一，并在 evidence 中引用原文片段或标准号）**：
1. 章节号引用：指标 clause_text 显式引用某试验的 source_clause（如"按6.3进行""见6.3""按第6章"）。
2. 方法标准引用：指标 clause_text 与试验 method_desc/acceptance 引用了同一方法标准号（如 GB/T 228.1、ISO 527、IEC 60529）。
3. 同条款共存：指标 source_clause == 试验 source_clause，且试验 acceptance 含该指标 source_value 的数值/单位。

**禁止**：仅凭指标名、标准化对象、数值相似即关联；数值一致但无任何原文引用不得关联。

每条指标最多关联一个试验；多个候选时选证据最强者；无满足条件的候选时 test_name 留空。
evidence 必须引用原文片段或标准号，不得泛泛而谈；evidence_type 取 "clause_ref" | "method_std" | "same_clause" | ""。

只输出纯 JSON 数组，元素数量与指标数一致，按 ind_idx 升序，不要其他文字：
[{{"ind_idx": 0, "test_name": "拉伸强度", "evidence_type": "method_std", "evidence": "指标'按GB/T 228.1测定'↔试验method_desc'按GB/T 228.1'", "confidence": 0.9}}, {{"ind_idx": 1, "test_name": "", "evidence_type": "", "evidence": "仅数值相似，无原文引用", "confidence": 0.0}}]
"""


async def _link_ind_to_tests(
    standard_no: str, indicators: List[IndicatorItem], tests: List[TestItem]
) -> None:
    """证据门控关联，写回 test.indicators（原地修改）。

    流程：确定性候选生成 → 有候选指标按 standard_object 分组并发 LLM 验证
    → 零候选残留批量兜底（_LINK_FALLBACK_ENABLED）→ 写回。
    - 删除章节号硬过滤；章节号/方法标准号/同条款作为正面证据候选信号
    - LLM 只在有候选的小集上判定，零候选指标默认跳过 LLM
    - 关联必须命中 evidence 文本，否则不写回
    - 写回：有产出才覆盖（保留提取阶段弱关联）；去重 key 并入 source_value
    """
    if not indicators or not tests:
        return

    # 预取指标所在条款原文（主证据源，全文不截断）
    ind_clause_nos = list({ind.source_clause for ind in indicators if ind.source_clause})
    try:
        clause_text_map = await _fetch_clause_texts(standard_no, ind_clause_nos)
        logger.info(f"[FastExtract] 条款原文预取: {len(clause_text_map)} 条")
    except Exception as e:
        logger.warning(f"[FastExtract] 条款原文预取失败，降级无条款文本模式: {e}")
        clause_text_map = {}

    test_by_name: dict[str, TestItem] = {t.test_name: t for t in tests if t.test_name}

    # Step 1: 确定性候选生成（CPU，瞬时）
    candidates = _generate_link_candidates(indicators, tests, clause_text_map)
    has_cand_idx = [i for i in range(len(indicators)) if i in candidates]
    no_cand_idx = [i for i in range(len(indicators)) if i not in candidates]
    logger.info(
        f"[FastExtract] 候选生成: {len(has_cand_idx)} 条指标有候选，"
        f"{len(no_cand_idx)} 条零候选（跳过 LLM）"
    )

    # 收集关联结果：{test_name: [IndicatorItem]}（多指标可关联同一试验）
    pending: dict[str, list[IndicatorItem]] = {}

    def _collect(ind_idx: int, test_name: str, ind: IndicatorItem) -> None:
        test = test_by_name.get(test_name)
        if test is None:
            return
        pending.setdefault(test_name, []).append(IndicatorItem(
            indicator_object=ind.indicator_object,
            source_value=ind.source_value,
            standard_object=ind.standard_object or test.standard_object,
            applicable_object=ind.applicable_object or test.applicable_object,
            source_clause=ind.source_clause,
        ))

    async def _verify_group(
        obj_key: str, indexed_inds: list[tuple[int, IndicatorItem]]
    ) -> None:
        """一组有候选指标的 LLM 验证：每条指标只喂自己的候选试验。"""
        indicators_for_llm: list[dict] = []
        group_test_names: list[str] = []
        seen_t: set[str] = set()
        for g_idx, ind in indexed_inds:
            cands = candidates.get(g_idx, [])
            for cn in cands:
                if cn not in seen_t:
                    seen_t.add(cn)
                    group_test_names.append(cn)
            indicators_for_llm.append({
                "ind_idx": g_idx,
                "indicator_object": ind.indicator_object,
                "source_value": ind.source_value,
                "source_clause": ind.source_clause,
                "clause_text": clause_text_map.get(ind.source_clause, ""),
                "candidate_tests": cands,
            })
        tests_for_llm: list[dict] = []
        for tname in group_test_names:
            t = test_by_name.get(tname)
            if t is None:
                continue
            tests_for_llm.append({
                "test_name": t.test_name,
                "method_desc": t.method_desc,
                "source_clause": t.source_clause,
                "acceptance": t.acceptance,
            })
        if not indicators_for_llm or not tests_for_llm:
            return

        prompt = _FAST_LINK_IND_TO_TESTS_PROMPT.format(
            tests_json=json.dumps(tests_for_llm, ensure_ascii=False, indent=2),
            indicators_json=json.dumps(indicators_for_llm, ensure_ascii=False, indent=2),
        )
        try:
            text = await _llm_call(prompt)
            arr = _parse_json_array(text)
            idx_to_ind = {g_idx: ind for g_idx, ind in indexed_inds}
            for entry in arr:
                if not isinstance(entry, dict):
                    continue
                ind_idx = entry.get("ind_idx")
                test_name = entry.get("test_name") or ""
                evidence = (entry.get("evidence") or "").strip()
                if ind_idx is None or not test_name or not evidence:
                    continue
                # 输出校验：test_name 必须在该指标候选集 + 全局试验表里
                if test_name not in candidates.get(ind_idx, []) or test_name not in test_by_name:
                    continue
                ind = idx_to_ind.get(ind_idx)
                if ind is None:
                    continue
                _collect(ind_idx, test_name, ind)
        except Exception as e:
            logger.warning(f"[FastExtract] 对象组[{obj_key}]关联验证失败: {e}")

    # Step 2: 有候选指标按 standard_object 分组并发 LLM 验证
    by_obj: dict[str, list[tuple[int, IndicatorItem]]] = {}
    for i in has_cand_idx:
        ind = indicators[i]
        key = ind.standard_object or "其他"
        by_obj.setdefault(key, []).append((i, ind))

    await asyncio.gather(*[
        _verify_group(obj_key, indexed_inds)
        for obj_key, indexed_inds in by_obj.items()
    ])

    # Step 3: 零候选残留批量兜底（用全量试验，仍受证据门控）
    if _LINK_FALLBACK_ENABLED and no_cand_idx:
        await _verify_fallback(no_cand_idx, indicators, tests, clause_text_map, test_by_name, pending, _collect)

    # Step 4: 写回（有产出才覆盖；去重 key 并入 source_value，防同指标名不同牌号误并）
    linked_count = 0
    for test_name, ind_items in pending.items():
        test = test_by_name.get(test_name)
        if test is None:
            continue
        seen_names: set[tuple[str, str]] = set()
        deduped: list[IndicatorItem] = []
        for item in ind_items:
            k = _RE_NORM.sub("", (item.indicator_object or "")).lower()
            kv = _RE_NORM.sub("", (item.source_value or "")).lower()
            key = (k, kv)
            if k and key not in seen_names:
                seen_names.add(key)
                deduped.append(item)
        # 有产出才覆盖，无产出保留提取阶段弱关联（避免一次 LLM 抖动清空数据）
        if deduped:
            test.indicators = deduped
            linked_count += 1

    logger.info(
        f"[FastExtract] 指标↔试验关联完成（证据门控），"
        f"{linked_count}/{len(tests)} 个试验关联到指标"
        f"（有候选 {len(has_cand_idx)}，零候选 {len(no_cand_idx)}，"
        f"兜底={'on' if _LINK_FALLBACK_ENABLED else 'off'}）"
    )


async def _verify_fallback(
    no_cand_idx: list[int],
    indicators: List[IndicatorItem],
    tests: List[TestItem],
    clause_text_map: dict[str, str],
    test_by_name: dict[str, TestItem],
    pending: dict[str, list[IndicatorItem]],
    _collect,
) -> None:
    """零候选残留的批量兜底：用全量试验让 LLM 找任意原文引用证据。

    candidate_tests 设为全量试验名；仍要求命中 evidence 才写回。
    捕获"附录A的方法"等非标准号/非章节号引用（候选生成正则抓不到的）。
    """
    indicators_for_llm: list[dict] = []
    all_test_names = [t.test_name for t in tests if t.test_name]
    for i in no_cand_idx:
        ind = indicators[i]
        indicators_for_llm.append({
            "ind_idx": i,
            "indicator_object": ind.indicator_object,
            "source_value": ind.source_value,
            "source_clause": ind.source_clause,
            "clause_text": clause_text_map.get(ind.source_clause, ""),
            "candidate_tests": all_test_names,
        })
    tests_for_llm: list[dict] = []
    for t in tests:
        if not t.test_name:
            continue
        tests_for_llm.append({
            "test_name": t.test_name,
            "method_desc": t.method_desc,
            "source_clause": t.source_clause,
            "acceptance": t.acceptance,
        })
    if not indicators_for_llm or not tests_for_llm:
        return

    prompt = _FAST_LINK_IND_TO_TESTS_PROMPT.format(
        tests_json=json.dumps(tests_for_llm, ensure_ascii=False, indent=2),
        indicators_json=json.dumps(indicators_for_llm, ensure_ascii=False, indent=2),
    )
    try:
        text = await _llm_call(prompt)
        arr = _parse_json_array(text)
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            ind_idx = entry.get("ind_idx")
            test_name = entry.get("test_name") or ""
            evidence = (entry.get("evidence") or "").strip()
            if ind_idx is None or not test_name or not evidence:
                continue
            if test_name not in test_by_name:
                continue
            if not (0 <= ind_idx < len(indicators)):
                continue
            _collect(ind_idx, test_name, indicators[ind_idx])
    except Exception as e:
        logger.warning(f"[FastExtract] 零候选兜底关联失败: {e}")


# ── compare-smart-v2 快路径复用入口 ──────────────────────────────────────────

async def _run_fast_ind_extraction(
    standard_no: str,
    standard_name: str,
    use_range: str,
    compact_text: str,
) -> list[dict]:
    """
    快路径指标提取，供 compare-smart-v2 快路径复用。

    入参 compact_text 由调用方传入（v2 已完成图片解析），避免重复读 DB。
    返回 raw indicator dict 列表，含 object_type / norm_class / indicator_category。
    """
    text = await _llm_call(_FAST_IND_PROMPT.format(
        standard_no=standard_no,
        standard_name=standard_name,
        use_range=use_range,
        full_text=compact_text,
    ))
    raw = _parse_json_obj(text)
    if not raw:
        logger.warning(f"[FastExtract] {standard_no} 快路径指标提取返回无效 JSON")
        return []
    indicators = [
        r for r in (raw.get("indicators") or [])
        if isinstance(r, dict) and r.get("indicator_object")
    ]
    _strip_img_refs(indicators, ["source_value"])
    return indicators


# ── 主入口 ────────────────────────────────────────────────────────────────────

async def fast_extract_indicators(
    standard_no: str,
    standard_name: str = "",
    compact_limit: Optional[int] = None,
) -> ExtractionOutput:
    """
    快速指标与试验提取主入口。

    输出格式与 indicator_extraction_agent.extract_indicators() 完全相同，
    可直接写入 standard_cache_ind / standard_cache_test。

    快路径（compact <= 阈值）: 1 次 LLM one-shot（含分类字段）
    慢路径: 1 次分组规划 + N 组并行指标提取 + 并行试验提取（均含分类字段）
    后处理（共用）: 去重 → 指标↔试验关联补充（无额外 LLM 轮次）
    """
    # ── 1. 读取标准元数据 ───────────────────────────────────────────────────
    meta = await _fetch_standard_meta(standard_no)
    if not standard_name:
        standard_name = meta["name"]
    use_range = meta["use_range"]
    storage_path = meta["storage_path"]

    # 供后处理分类 agent 使用的上下文
    std_context = ""
    if standard_name:
        std_context += f"\n标准全称：{standard_name}"
    if use_range:
        std_context += f"\n适用范围：{use_range}"

    # ── 2. 拉取全部章节并构建 compact 文本（慢路径 _extract_all_tests 也需要）──
    chapters = await _fetch_all_chapters(standard_no)
    compact_text = await _build_full_compact(standard_no, chapters)
    compact_len = len(compact_text)
    _compact_limit = compact_limit if compact_limit is not None else _fast_compact_limit()
    logger.info(f"[FastExtract] {standard_no} compact 字符: {compact_len}，阈值: {_compact_limit}")

    raw_indicators: list[dict] = []
    raw_tests: list[dict] = []
    standard_structure_type: str = ""

    if compact_len <= _compact_limit:
        # ── 快路径：全文 compact + 图片内联 → 并行指标/试验提取 ────────────
        logger.info(
            f"[FastExtract] {standard_no} 快路径（compact={compact_len} 字符）"
        )
        compact_text = await _resolve_img_refs_inline(compact_text, storage_path)

        ind_text, test_text = await asyncio.gather(
            _llm_call(_FAST_IND_PROMPT.format(
                standard_no=standard_no,
                standard_name=standard_name,
                use_range=use_range,
                full_text=compact_text,
            )),
            _llm_call(_FAST_TEST_PROMPT.format(
                standard_no=standard_no,
                standard_name=standard_name,
                full_text=compact_text,
            )),
        )

        raw_ind_result = _parse_json_obj(ind_text)
        if raw_ind_result:
            standard_structure_type = raw_ind_result.get("standard_structure_type", "")
            raw_indicators = [r for r in (raw_ind_result.get("indicators") or []) if isinstance(r, dict)]
        else:
            logger.warning(f"[FastExtract] {standard_no} 指标提取返回无效 JSON")

        raw_tests = [r for r in _parse_json_array(test_text) if isinstance(r, dict) and r.get("test_name")]

        _strip_img_refs(raw_indicators, ["source_value"])
        _strip_img_refs(raw_tests, ["conditions", "preparation", "procedure", "acceptance"])

        logger.info(
            f"[FastExtract] {standard_no} 快路径完成，"
            f"指标 {len(raw_indicators)} 条，试验 {len(raw_tests)} 条"
        )

    else:
        # ── 慢路径：分组规划 → 并行提取 ────────────────────────────────────
        logger.info(
            f"[FastExtract] {standard_no} 慢路径（compact={compact_len} 字符，"
            f"compact阈值={_compact_limit}）"
        )
        plan = await _plan_groups(meta)
        standard_structure_type = plan.get("standard_structure_type", "")
        standard_object = plan.get("standard_object", "")
        applicable_object = plan.get("applicable_object", "")
        groups = plan.get("groups") or []

        logger.info(
            f"[FastExtract] {standard_no} 分组规划完成，{len(groups)} 组，"
            f"structure={standard_structure_type}"
        )

        toc_summary = "\n".join(
            f"  {c['title_no']} {c['title']} ({c.get('word_count', 0)}字)"
            for c in meta["toc"]
        )

        # 并行：构建各组 chunk 文本（含图片解析）
        chunk_texts = await asyncio.gather(*[
            _build_chunk_text(standard_no, g["chapter_nos"], toc_summary, storage_path)
            for g in groups
        ]) if groups else []

        # 并行：各组指标提取 + 全文试验提取
        need_tests = standard_structure_type not in ("has_ind_only",)
        need_inds = standard_structure_type not in ("has_test_only",)

        ind_tasks = [
            _extract_indicators_from_chunk(
                standard_no, standard_name, use_range,
                standard_object, applicable_object, ct,
            )
            for ct in chunk_texts
        ] if need_inds else []

        compact_text_for_tests = await _resolve_img_refs_inline(compact_text, storage_path) if need_tests else compact_text
        test_task = _extract_all_tests(standard_no, standard_name, compact_text_for_tests) if need_tests else None

        if ind_tasks and test_task is not None:
            *ind_results_list, raw_tests = await asyncio.gather(*ind_tasks, test_task)
        elif ind_tasks:
            ind_results_list = list(await asyncio.gather(*ind_tasks))
            raw_tests = []
        elif test_task is not None:
            ind_results_list = []
            raw_tests = await test_task
        else:
            ind_results_list = []
            raw_tests = []

        raw_indicators = _merge_indicators(ind_results_list)

        # 兜底：移除任何残留的图片占位符（不保留文件名）
        _strip_img_refs(raw_indicators, ["source_value"])
        _strip_img_refs(raw_tests, ["conditions", "preparation", "procedure", "acceptance"])

        logger.info(
            f"[FastExtract] {standard_no} 慢路径提取完成，"
            f"指标 {len(raw_indicators)} 条，试验 {len(raw_tests)} 条"
        )

    # ── 3. dict → IndicatorItem / TestItem ────────────────────────────────
    indicators = [_to_indicator_item(d) for d in raw_indicators if d.get("indicator_object")]
    tests = [_to_test_item(d) for d in raw_tests if d.get("test_name")]

    # ── 4. 去重 ────────────────────────────────────────────────────────────
    indicators = _deduplicate(indicators)
    tests = _deduplicate_tests(tests)
    # 二次去重：仅按 test_name 规范化，防止 standard_object/applicable_object 轻微差异漏网
    _seen_tnames: set[str] = set()
    _dedup_tests: list[TestItem] = []
    for _t in tests:
        _k = _RE_NORM.sub("", _t.test_name).lower()
        if _k not in _seen_tnames:
            _seen_tnames.add(_k)
            _dedup_tests.append(_t)
    tests = _dedup_tests

    # ── 5. 指标↔试验关联（LLM） ────────────────────────────────────────────
    await _link_ind_to_tests(standard_no, indicators, tests)

    # ── 6. 兜底推断 standard_structure_type ────────────────────────────────
    if not standard_structure_type or standard_structure_type not in _STANDARD_TYPES:
        if indicators and tests:
            standard_structure_type = "has_ind_and_test"
        elif indicators:
            standard_structure_type = "has_ind_only"
        elif tests:
            standard_structure_type = "has_test_only"
        else:
            standard_structure_type = ""

    logger.info(
        f"[FastExtract] {standard_no} 全流程完成，"
        f"structure={standard_structure_type}，"
        f"指标 {len(indicators)} 条，试验 {len(tests)} 条"
    )

    return ExtractionOutput(
        standard_structure_type=standard_structure_type,
        indicators=indicators,
        tests=tests,
    )


__all__ = ["fast_extract_indicators", "_run_fast_ind_extraction", "_FAST_IND_PROMPT", "_TAXONOMY_TREE_DESC"]
