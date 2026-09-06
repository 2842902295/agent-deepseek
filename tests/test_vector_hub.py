"""
vector_hub 单测（不依赖 DB / 网络）：

- 三档 diff 分类（classify_candidates）
- 内容模板与迁移 SQL 常量对拍（adapters 是单一真相源）
- TableSyncAdapter 白名单校验
- 陈旧派生（derive_lib_state）
- 文件解析（txt 切块 / xlsx 行解析）

运行：.venv/bin/pytest tests/test_vector_hub.py -v
"""

import hashlib
import os
import re
import tempfile
from types import SimpleNamespace

from app.services.vector_hub.adapters import (
    CHAPTER_CONTENT_MAX,
    CHAPTER_WORD_LIMIT,
    META_CONTENT_MAX,
    SystemChapterAdapter,
    SystemMetaAdapter,
    TableSyncAdapter,
    chapter_content,
    meta_content,
    parse_excel_file,
    parse_txt_file,
)
from app.services.vector_hub.ingest import VecCandidate, classify_candidates, content_hash
from app.services.vector_hub.state import derive_lib_state, format_vector_literal

# ── 三档 diff ────────────────────────────────────────────────────────────────


def _existing(*pairs):
    """构造库内现状映射：(item_key, hash, payload) 元组列表。"""
    return {k: {"hash": h, "payload": p} for k, h, p in pairs}


def test_classify_three_tier_diff():
    h_same = content_hash("内容A")
    existing = _existing(
        ("k_same", h_same, {"a": 1}),  # 内容+payload 都不变
        ("k_payload", h_same, {"a": 1}),  # 内容不变、payload 变 → 免嵌档
        ("k_changed", content_hash("旧内容"), {"a": 1}),  # 内容变 → 重嵌档
        ("k_gone", content_hash("将消失"), None),  # 源里没有 → 删除档
    )
    candidates = [
        VecCandidate(item_key="k_same", content="内容A", payload={"a": 1}),
        VecCandidate(item_key="k_payload", content="内容A", payload={"a": 2}),
        VecCandidate(item_key="k_changed", content="新内容", payload={"a": 1}),
        VecCandidate(item_key="k_new", content="全新", payload=None),  # 新增档
    ]
    cls = classify_candidates(existing, candidates, delete_missing=True)

    actions = {c.item_key: a for c, _, a in cls["to_embed"]}
    assert actions == {"k_changed": "reembed", "k_new": "add"}
    assert [c.item_key for c in cls["payload_only"]] == ["k_payload"]
    assert cls["unchanged"] == 1
    assert cls["delete"] == ["k_gone"]


def test_classify_no_delete_and_dedup():
    existing = _existing(("k_gone", content_hash("x"), None))
    candidates = [
        VecCandidate(item_key="dup", content="1"),
        VecCandidate(item_key="dup", content="2"),  # 同批重复只取第一条
        VecCandidate(item_key="", content="空 key 跳过"),
        VecCandidate(item_key="nocontent", content=""),
    ]
    cls = classify_candidates(existing, candidates, delete_missing=False)
    assert cls["delete"] == []
    assert [c.item_key for c, _, _ in cls["to_embed"]] == ["dup"]
    assert cls["to_embed"][0][0].content == "1"


def test_classify_payload_none_semantics():
    """falsy payload（None / {}）统一归一为 NULL；有值↔无值的变化才算 payload 档。"""
    h = content_hash("c")
    existing = _existing(("k1", h, None))
    cls = classify_candidates(existing, [VecCandidate(item_key="k1", content="c", payload=None)])
    assert cls["unchanged"] == 1 and not cls["payload_only"]
    cls = classify_candidates(existing, [VecCandidate(item_key="k1", content="c", payload={})])
    assert cls["unchanged"] == 1 and not cls["payload_only"]  # {} ≡ NULL，免嵌也不更新
    cls = classify_candidates(_existing(("k1", h, {"a": 1})), [VecCandidate(item_key="k1", content="c", payload=None)])
    assert [c.item_key for c in cls["payload_only"]] == ["k1"]  # 有值 → NULL 才是变化


# ── 内容模板 / 迁移常量对拍 ─────────────────────────────────────────────────


def test_meta_content_formula():
    """Python 模板与迁移/适配器 SQL 公式（逐字符）一致。"""
    cname, use_range = "GB/T 1.1 标准化工作导则", "全国"
    expect = f"标准名称：{cname}\n适用范围：{use_range}"[:META_CONTENT_MAX]
    assert meta_content(cname, use_range) == expect
    assert meta_content(None, None) == "标准名称：\n适用范围："
    # 截断边界
    long_range = "范" * 5000
    assert len(meta_content("X", long_range)) == META_CONTENT_MAX
    # content_hash 与 SQL MD5(content) 同算法（utf-8 小写 hex）
    assert content_hash("abc") == hashlib.md5("abc".encode()).hexdigest()


def test_chapter_content_formula():
    title_no, title = "5.2", "某要求"
    word = "正" * 4000
    expect = f"【{title_no}】{title}\n{word[:CHAPTER_WORD_LIMIT]}"
    expect = expect[:CHAPTER_CONTENT_MAX]
    assert chapter_content(title_no, title, word) == expect
    assert CHAPTER_WORD_LIMIT == 3000 and CHAPTER_CONTENT_MAX == 3200
    # title 超长时外层截断生效（迁移已知毛边：仅当 title_no+title 合计超 3200 才触发）
    long_title = "题" * 3300
    assert len(chapter_content("1", long_title, "w")) == CHAPTER_CONTENT_MAX


def test_adapter_sql_matches_template_constants():
    """适配器 SQL 使用与 Python 模板相同的字面量/截断常量（防公式漂移）。"""
    meta_sql = SystemMetaAdapter().inner_select_sql()
    assert "标准名称：" in meta_sql and "\\n适用范围：" in meta_sql
    assert f",{META_CONTENT_MAX}) AS content" in meta_sql
    assert "MD5(t.content) AS src_chash" in meta_sql
    # item_key = ref_key = standard_no
    assert "b.standard_no AS item_key" in meta_sql and "b.standard_no AS ref_key" in meta_sql

    ch_sql = SystemChapterAdapter().inner_select_sql()
    assert "'【'" in ch_sql and "'】'" in ch_sql
    assert f"LEFT(IFNULL(c.word,''),{CHAPTER_WORD_LIMIT})" in ch_sql
    assert f"),{CHAPTER_CONTENT_MAX}) AS content" in ch_sql
    assert "CONCAT(p.standard_no, '#', c.id) AS item_key" in ch_sql
    # keys_sql 与 inner 同源过滤（导航标题跳过），保证新增/删除两路一致
    skip_frag = "TRIM(IFNULL(c.title,'')) NOT LIKE '范围%'"
    assert skip_frag in ch_sql and skip_frag in SystemChapterAdapter().keys_sql()
    for p in ("规范性引用文件", "术语和定义", "前言", "参考文献", "编制说明", "索引"):
        assert f"NOT LIKE '{p}%'" in ch_sql


# ── TableSyncAdapter 白名单 ─────────────────────────────────────────────────


def test_table_sync_adapter_validation():
    ok = TableSyncAdapter(
        {"table": "standard_pool_check", "key_column": "id", "content_columns": ["pool_name"], "ref_column": "id"}
    )
    sql = ok.inner_select_sql()
    assert "`standard_pool_check`" in sql and "CONCAT_WS" in sql
    assert f",{META_CONTENT_MAX}) AS content" in sql  # 内容截断保护

    for bad in (
        {"table": "users", "key_column": "id", "content_columns": ["x"]},  # 非 standard_ 前缀
        {"table": "standard_a; DROP TABLE x", "key_column": "id", "content_columns": ["x"]},
        {"table": "standard_ok", "key_column": "id; --", "content_columns": ["x"]},
        {"table": "standard_ok", "key_column": "id", "content_columns": []},
    ):
        try:
            TableSyncAdapter(bad)
            raise AssertionError(f"应拒绝非法配置: {bad}")
        except ValueError:
            pass


# ── 陈旧派生 ────────────────────────────────────────────────────────────────


def _lib(status="empty", block=None, dim=None, count=0):
    return SimpleNamespace(status=status, embed_block=block, embed_dim=dim, item_count=count)


def test_derive_lib_state():
    active = ("EMBED_DASHSCOPE", 2048)
    assert derive_lib_state(_lib(status="building"), *active) == "building"
    assert derive_lib_state(_lib(status="error"), *active) == "error"
    # 从未构建（无快照）→ empty，不算陈旧
    assert derive_lib_state(_lib(), *active) == "empty"
    # 快照匹配 + 有数据 → ready
    assert derive_lib_state(_lib(block="EMBED_DASHSCOPE", dim=2048, count=10), *active) == "ready"
    # 快照失配（换块或换维度）→ stale
    assert derive_lib_state(_lib(block="EMBED_LOCAL", dim=2048, count=10), *active) == "stale"
    assert derive_lib_state(_lib(block="EMBED_DASHSCOPE", dim=4096, count=10), *active) == "stale"
    # 快照匹配但无数据（如 wipe 后）→ empty
    assert derive_lib_state(_lib(block="EMBED_DASHSCOPE", dim=2048, count=0), *active) == "empty"


def test_format_vector_literal():
    assert format_vector_literal([0.5, -1.0]) == "[0.500000,-1.000000]"


# ── 文件解析 ────────────────────────────────────────────────────────────────


def test_parse_txt_chunking():
    text = ("段落甲。" * 80 + "\n\n") * 5  # 每段约 320 字
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        cands = parse_txt_file(path)
        assert len(cands) >= 2  # 多段必然切多块
        assert all(c.item_key.startswith("chunk#") for c in cands)
        assert all(len(c.content) <= 1100 for c in cands)  # 块大小受控（单段超长硬切）
    finally:
        os.unlink(path)


def test_parse_excel_rows():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["名称", "指标"])
    ws.append(["抗拉强度", "≥400MPa"])
    ws.append(["", ""])  # 空行跳过
    ws.append(["屈服强度", "≥235MPa"])
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        path = f.name
    wb.save(path)
    try:
        cands = parse_excel_file(path)
        assert len(cands) == 2
        assert cands[0].item_key == "Sheet1#2"
        assert cands[0].content == "名称：抗拉强度\n指标：≥400MPa"
        assert cands[0].payload == {"名称": "抗拉强度", "指标": "≥400MPa"}
        assert cands[1].item_key == "Sheet1#4"  # 行号跟随原表（含空行）
    finally:
        os.unlink(path)


# ── P0 遗留对拍：block_key 正则收 EMBED ────────────────────────────────────


def test_block_key_regex_accepts_embed():
    pat = re.compile(r"^(CHAT|IMAGE|VIDEO|EMBED)(_[A-Z0-9]+)?$")
    assert pat.match("EMBED_DASHSCOPE") and pat.match("EMBED_LOCAL") and pat.match("EMBED")
    assert not pat.match("EMBED_x") and not pat.match("FOO_BAR")
