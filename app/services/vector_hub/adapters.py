"""
向量库来源适配器：把各类来源变成候选流 / SQL 片段。

**内容模板是全体系单一真相源**——migration.py 的迁移 SQL 逐字符仿写这里的公式；
改模板必须同步改迁移注释（既有库首轮增量会按模板重算 hash，漂移会引发大面积重嵌）。

模板：
- meta:    f"标准名称：{cname}\\n适用范围：{use_range}" 再 [:3000]
- chapter: f"【{title_no}】{title}\\n{word[:3000]}" 再 [:3200]
- term:    f"术语：{title}\\n英文：{title_ename}\\n释义：{word[:3000]}" 再 [:3200]
"""

from __future__ import annotations

import csv
import re
from typing import Dict, List, Optional

from app.services.vector_hub.ingest import VecCandidate

# ── 常量（与迁移 SQL / 旧 builder 对齐，改动见模块注释）──────────────────────
META_CONTENT_MAX = 3000
CHAPTER_WORD_LIMIT = 3000
CHAPTER_CONTENT_MAX = 3200
TERM_WORD_LIMIT = 3000
TERM_CONTENT_MAX = 3200

# 章节标题前缀精确头部匹配，命中即跳过（导航性章节，嵌入浪费）
SKIP_TITLE_PREFIXES: tuple = (
    "范围",
    "规范性引用文件",
    "术语和定义",
    "前言",
    "参考文献",
    "编制说明",
    "索引",
)

# 文件上传单次行数硬上限（超大文件拒绝，防解析阻塞 / 内存爆）
FILE_ROW_LIMIT = 50000

# table_sync 源表白名单前缀 + 标识符字符集（无裸 SQL，全部显式列名）
_TABLE_PREFIX = "standard_"
_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


def meta_content(cname: Optional[str], use_range: Optional[str]) -> str:
    """标准级内容模板（与 SystemMetaAdapter SQL / 迁移 SQL 对齐）。"""
    return f"标准名称：{cname or ''}\n适用范围：{use_range or ''}"[:META_CONTENT_MAX]


def chapter_content(title_no: Optional[str], title: Optional[str], word: Optional[str]) -> str:
    """章节级内容模板（与 SystemChapterAdapter SQL / 迁移 SQL 对齐）。"""
    text = f"【{title_no or ''}】{title or ''}\n{(word or '')[:CHAPTER_WORD_LIMIT]}"
    return text[:CHAPTER_CONTENT_MAX]


def term_content(title: Optional[str], title_ename: Optional[str], word: Optional[str]) -> str:
    """术语级内容模板（与 SystemTermAdapter SQL 对齐）。"""
    text = f"术语：{title or ''}\n英文：{title_ename or ''}\n释义：{(word or '')[:TERM_WORD_LIMIT]}"
    return text[:TERM_CONTENT_MAX]


# ── 系统库（system_sync，SQL 下推）──────────────────────────────────────────

# 章节导航标题过滤：source SQL 与 keys SQL 共用，保证新增/删除两路判断一致
_CHAPTER_SKIP_SQL = " AND ".join(
    f"TRIM(IFNULL(c.title,'')) NOT LIKE '{p}%'" for p in SKIP_TITLE_PREFIXES
)


def chapter_source_where() -> str:
    """章节源表过滤条件（非空正文 + 跳导航标题），与库内收录口径一致。

    供消费方直读源表时复用（如 compare 工具拉 A 端章节），
    保证「读源表」与「库里有什么」判断一致。
    """
    return f"c.word IS NOT NULL AND c.word <> '' AND {_CHAPTER_SKIP_SQL}"


class SystemMetaAdapter:
    """standard_base_info → standard_meta 库。

    item_key = ref_key = standard_no；payload 全部字段都出现在 content 里，
    因此 SQL 下推只需两档（内容不变 ⇒ payload 必然不变）。
    """

    library_key = "standard_meta"

    def inner_select_sql(self) -> str:
        return (
            "SELECT t.item_key, t.ref_key, t.content, MD5(t.content) AS src_chash, t.payload_json FROM ("
            "SELECT b.standard_no AS item_key, b.standard_no AS ref_key, "
            f"LEFT(CONCAT('标准名称：',IFNULL(b.cname,''),'\\n适用范围：',IFNULL(b.use_range,'')),{META_CONTENT_MAX}) AS content, "
            "JSON_OBJECT('standard_no', b.standard_no, 'cname', b.cname, 'use_range', b.use_range) AS payload_json "
            "FROM standard_base_info b "
            "WHERE b.cname IS NOT NULL AND b.cname <> ''"
            ") t"
        )

    def keys_sql(self) -> str:
        return (
            "SELECT b.standard_no AS item_key FROM standard_base_info b "
            "WHERE b.cname IS NOT NULL AND b.cname <> ''"
        )


class SystemChapterAdapter:
    """standard_jgh_pdf_chapter → standard_chapter 库。

    item_key = {standard_no}#{chapter_id}；ref_key = standard_no（池过滤 / 按标准检索用）。
    跳过 7 类导航性标题前缀（与旧 builder 一致）。
    """

    library_key = "standard_chapter"

    def inner_select_sql(self) -> str:
        return (
            "SELECT t.item_key, t.ref_key, t.content, MD5(t.content) AS src_chash, t.payload_json FROM ("
            "SELECT CONCAT(p.standard_no, '#', c.id) AS item_key, p.standard_no AS ref_key, "
            "LEFT(CONCAT('【',IFNULL(c.title_no,''),'】',IFNULL(c.title,''),'\\n',"
            f"LEFT(IFNULL(c.word,''),{CHAPTER_WORD_LIMIT})),{CHAPTER_CONTENT_MAX}) AS content, "
            "JSON_OBJECT('standard_no', p.standard_no, 'chapter_id', c.id, "
            "'title_no', c.title_no, 'title', c.title) AS payload_json "
            "FROM standard_jgh_pdf_chapter c "
            "JOIN standard_jgh_pdf p ON p.main_task_id = c.main_task_id "
            f"WHERE c.word IS NOT NULL AND c.word <> '' AND {_CHAPTER_SKIP_SQL}"
            ") t"
        )

    def keys_sql(self) -> str:
        return (
            "SELECT CONCAT(p.standard_no, '#', c.id) AS item_key "
            "FROM standard_jgh_pdf_chapter c "
            "JOIN standard_jgh_pdf p ON p.main_task_id = c.main_task_id "
            f"WHERE c.word IS NOT NULL AND c.word <> '' AND {_CHAPTER_SKIP_SQL}"
        )


class SystemTermAdapter:
    """standard_jgh_pdf_term → standard_term 库。

    item_key = {standard_no}#{term_id}；ref_key = standard_no（池过滤 / 按标准检索用）。
    经 main_task_id 关联 standard_jgh_pdf（命中率 ~99.6%，远高于 file_uuid）；
    过滤已删除行（deleted=1）与空释义行。
    """

    library_key = "standard_term"

    _WHERE = "t.word IS NOT NULL AND t.word <> '' AND IFNULL(t.deleted,0)=0"

    def inner_select_sql(self) -> str:
        return (
            "SELECT t.item_key, t.ref_key, t.content, MD5(t.content) AS src_chash, t.payload_json FROM ("
            "SELECT CONCAT(p.standard_no, '#', t.id) AS item_key, p.standard_no AS ref_key, "
            "LEFT(CONCAT('术语：',IFNULL(t.title,''),'\\n英文：',IFNULL(t.title_ename,''),'\\n释义：',"
            f"LEFT(IFNULL(t.word,''),{TERM_WORD_LIMIT})),{TERM_CONTENT_MAX}) AS content, "
            "JSON_OBJECT('standard_no', p.standard_no, 'term_id', t.id, "
            "'title', t.title, 'title_ename', t.title_ename, 'chapter_no', t.chapter_no) AS payload_json "
            "FROM standard_jgh_pdf_term t "
            "JOIN standard_jgh_pdf p ON p.main_task_id = t.main_task_id "
            f"WHERE {self._WHERE}"
            ") t"
        )

    def keys_sql(self) -> str:
        return (
            "SELECT CONCAT(p.standard_no, '#', t.id) AS item_key "
            "FROM standard_jgh_pdf_term t "
            "JOIN standard_jgh_pdf p ON p.main_task_id = t.main_task_id "
            f"WHERE {self._WHERE}"
        )


SYSTEM_ADAPTERS: Dict[str, object] = {
    SystemMetaAdapter.library_key: SystemMetaAdapter(),
    SystemChapterAdapter.library_key: SystemChapterAdapter(),
    SystemTermAdapter.library_key: SystemTermAdapter(),
}


# ── 文件上传（file 来源，Python diff；解析是同步阻塞，调用方必须 to_thread）──


def _chunk_text(text: str, chunk_size: int = 1000) -> List[str]:
    """按段落累积切块（段落不拆散，除非单段超长）。"""
    chunks: List[str] = []
    buf = ""
    for para in text.replace("\r\n", "\n").split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(buf) + len(para) + 1 > chunk_size and buf:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n{para}" if buf else para
        while len(buf) > chunk_size:  # 单段超长：硬切
            chunks.append(buf[:chunk_size])
            buf = buf[chunk_size:]
    if buf:
        chunks.append(buf)
    return chunks


def parse_txt_file(path: str) -> List[VecCandidate]:
    """txt / md：整文按段落切块，item_key = chunk#{n}。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return [
        VecCandidate(item_key=f"chunk#{i + 1}", content=c, payload={"chunk": i + 1})
        for i, c in enumerate(_chunk_text(text))
        if c.strip()
    ]


def parse_excel_file(path: str) -> List[VecCandidate]:
    """xlsx：openpyxl read_only 流式；首行为表头，内容 = 「列名：值」逐行。

    item_key = {sheet}#{行号}；重传按行级 hash 天然增量（内容变了才重嵌）。
    """
    from openpyxl import load_workbook

    out: List[VecCandidate] = []
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            header: List[str] = []
            row_no = 0
            for row in ws.iter_rows(values_only=True):
                row_no += 1
                cells = ["" if v is None else str(v).strip() for v in row]
                if not any(cells):
                    continue
                if not header:
                    header = cells
                    continue
                if len(out) >= FILE_ROW_LIMIT:
                    raise ValueError(f"文件行数超过上限 {FILE_ROW_LIMIT}，请拆分后上传")
                pairs = [(header[i] or f"列{i + 1}", cells[i]) for i in range(min(len(header), len(cells))) if cells[i]]
                if not pairs:
                    continue
                content = "\n".join(f"{h}：{v}" for h, v in pairs)[:META_CONTENT_MAX]
                out.append(
                    VecCandidate(
                        item_key=f"{ws.title}#{row_no}",
                        content=content,
                        payload={h: v for h, v in pairs},
                    )
                )
    finally:
        wb.close()
    return out


def parse_csv_file(path: str) -> List[VecCandidate]:
    """csv：首行表头，规则同 xlsx。"""
    out: List[VecCandidate] = []
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f)
        header: List[str] = []
        row_no = 0
        for row in reader:
            row_no += 1
            cells = ["" if v is None else str(v).strip() for v in row]
            if not any(cells):
                continue
            if not header:
                header = cells
                continue
            if len(out) >= FILE_ROW_LIMIT:
                raise ValueError(f"文件行数超过上限 {FILE_ROW_LIMIT}，请拆分后上传")
            pairs = [(header[i] or f"列{i + 1}", cells[i]) for i in range(min(len(header), len(cells))) if cells[i]]
            if not pairs:
                continue
            content = "\n".join(f"{h}：{v}" for h, v in pairs)[:META_CONTENT_MAX]
            out.append(
                VecCandidate(
                    item_key=f"csv#{row_no}",
                    content=content,
                    payload={h: v for h, v in pairs},
                )
            )
    return out


def parse_upload_file(path: str, filename: str) -> List[VecCandidate]:
    """按扩展名分派（同步阻塞 —— 调用方必须 asyncio.to_thread）。"""
    name = (filename or path).lower()
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        return parse_excel_file(path)
    if name.endswith(".csv"):
        return parse_csv_file(path)
    if name.endswith((".txt", ".md")):
        return parse_txt_file(path)
    raise ValueError(f"不支持的文件类型：{filename}（支持 xlsx / csv / txt / md）")


# ── 数据表同步源（table_sync，SQL 下推，仅超管可建）──────────────────────────


class TableSyncAdapter:
    """显式配置的数据表同步源（无裸 SQL：表名/列名全部白名单校验）。

    source_config = {
        "table": "standard_xxx",          # 必须 standard_ 前缀
        "key_column": "id",               # 唯一键列（必填）
        "content_columns": ["a", "b"],    # 参与嵌入的列（必填；内容 = 各列换行拼接）
        "ref_column": "standard_no",      # 可选：写入 ref_key 供等值/IN 过滤
    }
    payload 由 content_columns 派生（内容不变 ⇒ payload 不变，满足两档语义）。
    """

    def __init__(self, source_config: dict):
        cfg = source_config or {}
        table = str(cfg.get("table") or "")
        key_col = str(cfg.get("key_column") or "")
        cols = [str(c) for c in (cfg.get("content_columns") or [])]
        ref_col = str(cfg.get("ref_column") or "")
        if not table.startswith(_TABLE_PREFIX) or not _IDENT_RE.match(table):
            raise ValueError(f"table_sync 源表必须以 {_TABLE_PREFIX} 开头且仅含字母数字下划线：{table!r}")
        if not key_col or not _IDENT_RE.match(key_col):
            raise ValueError(f"非法的 key_column：{key_col!r}")
        if not cols or any(not _IDENT_RE.match(c) for c in cols):
            raise ValueError(f"非法的 content_columns：{cols!r}")
        if ref_col and not _IDENT_RE.match(ref_col):
            raise ValueError(f"非法的 ref_column：{ref_col!r}")
        self.table = table
        self.key_col = key_col
        self.content_cols = cols
        self.ref_col = ref_col or None

    def _content_expr(self) -> str:
        # 各内容列换行拼接（NULL → 空串），与 ingest 侧 CONCAT_WS 语义一致
        parts = ",".join(f"IFNULL(CAST(`{c}` AS CHAR),'')" for c in self.content_cols)
        return f"CONCAT_WS('\\n',{parts})"

    def inner_select_sql(self) -> str:
        content = self._content_expr()
        ref = f"CAST(`{self.ref_col}` AS CHAR)" if self.ref_col else "NULL"
        payload_pairs = ",".join(f"'{c}', `{c}`" for c in self.content_cols)
        return (
            "SELECT t.item_key, t.ref_key, t.content, MD5(t.content) AS src_chash, t.payload_json FROM ("
            f"SELECT CAST(`{self.key_col}` AS CHAR) AS item_key, {ref} AS ref_key, "
            f"LEFT({content},{META_CONTENT_MAX}) AS content, JSON_OBJECT({payload_pairs}) AS payload_json "
            f"FROM `{self.table}` WHERE `{self.key_col}` IS NOT NULL"
            ") t"
        )

    def keys_sql(self) -> str:
        return (
            f"SELECT CAST(`{self.key_col}` AS CHAR) AS item_key "
            f"FROM `{self.table}` WHERE `{self.key_col}` IS NOT NULL"
        )


def make_adapter_for_library(lib):
    """按库来源取适配器（系统库 / table_sync；manual/file 无适配器走候选流摄入）。"""
    if lib.source_type == "system_sync":
        adapter = SYSTEM_ADAPTERS.get(lib.library_key)
        if adapter is None:
            raise ValueError(f"系统库 {lib.library_key} 无对应同步适配器")
        return adapter
    if lib.source_type == "table_sync":
        return TableSyncAdapter(lib.source_config or {})
    raise ValueError(f"库 {lib.library_key} 的来源类型 {lib.source_type} 不支持自动同步")


__all__ = [
    "META_CONTENT_MAX",
    "CHAPTER_WORD_LIMIT",
    "CHAPTER_CONTENT_MAX",
    "TERM_WORD_LIMIT",
    "TERM_CONTENT_MAX",
    "SKIP_TITLE_PREFIXES",
    "FILE_ROW_LIMIT",
    "meta_content",
    "chapter_content",
    "term_content",
    "chapter_source_where",
    "SystemMetaAdapter",
    "SystemChapterAdapter",
    "SystemTermAdapter",
    "SYSTEM_ADAPTERS",
    "TableSyncAdapter",
    "parse_upload_file",
    "parse_txt_file",
    "parse_excel_file",
    "parse_csv_file",
    "make_adapter_for_library",
]
