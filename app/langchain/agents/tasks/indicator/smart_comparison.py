"""
compare-smart：高速高精度标准差异对比

相比 compare-v3：
  - LLM 分组规划：单次结构化 JSON 调用（不走多轮 agent），两标准并行
  - HTML→Markdown 预处理：章节正文去除无意义标签，降低 Token 噪声
  - 所有分组并行提取（两标准同时）
  - 图片引用：提取阶段写 [REF:图片:src] 占位，Step 5.6 调 /get-config-item API + LLM 解析
  - 比对：全量（≤150条）或按 standard_object 分组并行
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

import aiomysql
from loguru import logger
from pydantic import BaseModel, Field

from app.langchain.agents.tasks.indicator.fast_extraction import (
    _FAST_LINK_IND_TO_TESTS_PROMPT,
    _GROUP_PLANNER_PROMPT,
    _INDICATOR_EXTRACTION_PROMPT,
    _LINK_FALLBACK_ENABLED,
    _SOURCE_VALUE_GUIDE,
    _TAXONOMY_TREE_DESC,
    _TEST_EXTRACTION_PROMPT,
    _RE_IMG_PLAIN,
    _RE_IMG_REF,
    _RE_NORM,
    _clause_parts,
    _extract_clause_refs,
    _extract_method_stds,
    _fetch_all_chapters,
    _fetch_img_word,
    _html_to_compact as html_to_compact,
    _strip_img_refs,
)
from app.langchain.llm_providers import get_smart_comparison_llm
from app.services.mysql_pool import standard_pool
from app.settings import APP_SETTINGS as settings

# ── 并发限制 ──────────────────────────────────────────────────────────────────
_LLM_SEM = asyncio.Semaphore(20)

# ── 正则 ──────────────────────────────────────────────────────────────────────
# _RE_IMG_REF / _RE_IMG_PLAIN / _RE_NORM 已收敛至 fast_extraction（顶部 import）
_CROSS_REF_RE = re.compile(r"(?:参见|详见|按照|参照|遵照|遵循|满足|执行|见|按|如|符合|依据)\s*(\d+\.\d+[\d.]*)")


# ── Pydantic Schema ───────────────────────────────────────────────────────────
class GroupItem(BaseModel):
    group_name: str
    chapter_nos: list[str]
    reason: str = ""


class GroupPlanResult(BaseModel):
    standard_structure_type: str = "has_ind_and_test"
    standard_object: str = ""
    applicable_object: str = ""
    groups: list[GroupItem] = Field(default_factory=list)


class SmartCompareItem(BaseModel):
    source_indicator_object: Optional[str] = ""
    source_value: Optional[str] = ""
    source_clause: Optional[str] = ""
    source_indicator_category: Optional[str] = ""  # 源指标分类，从提取结果回填
    source_object_type: Optional[str] = ""          # 源对象类型，从提取结果回填
    target_indicator_object: Optional[str] = ""
    target_value: Optional[str] = ""
    target_clause: Optional[str] = ""
    target_indicator_category: Optional[str] = ""  # 目标指标分类，从提取结果回填
    target_object_type: Optional[str] = ""          # 目标对象类型，从提取结果回填
    standard_object: Optional[str] = ""
    comparison_type: str  # matched | source_only | target_only
    change_analysis: Optional[str] = ""
    norm_class: Optional[str] = ""  # 提取阶段赋值，比对阶段透传


class SmartCompareResult(BaseModel):
    items: list[SmartCompareItem] = Field(default_factory=list)
    relationship: str = "部分重叠"
    overall_assessment: str = ""
    source_tests: list[dict] = Field(default_factory=list)
    target_tests: list[dict] = Field(default_factory=list)
    # idx → test_name，由 link_tests_to_result() 填充；一个指标最多对应一个试验
    source_test_links: dict[int, str] = Field(default_factory=dict)
    target_test_links: dict[int, str] = Field(default_factory=dict)


# ── HTML → 紧凑文本 ───────────────────────────────────────────────────────────
# _TableParser / html_to_compact 已收敛至 fast_extraction（顶部 import）


# ── DB helpers ────────────────────────────────────────────────────────────────
async def fetch_standard_meta(standard_no: str) -> dict:
    """读取标准基本信息 + 完整 TOC（含各章节 word_count）。"""
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


async def fetch_chapters(standard_no: str, chapter_nos: list[str]) -> list[dict]:
    """读取指定章节及其所有子章节的原始 HTML 内容。"""
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


# _fetch_all_chapters 已收敛至 fast_extraction（顶部 import）


async def _std_exists(standard_no: str) -> bool:
    """检查标准是否存在于 DB。"""
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT 1 FROM standard_jgh_pdf WHERE standard_no = %s LIMIT 1",
                    [standard_no],
                )
                return bool(await cur.fetchone())
    except Exception:
        return False


# ── JSON 解析工具 ─────────────────────────────────────────────────────────────
def _parse_json_array(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return []


def _parse_json_obj(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    # 响应被截断时：尝试从 items 数组里提取所有已完整输出的 item
    partial_items: list[dict] = []
    for item_m in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
        try:
            obj = json.loads(item_m.group())
            if "source_indicator_object" in obj:
                partial_items.append(obj)
        except Exception:
            continue
    if partial_items:
        logger.warning(f"[SmartCompare] JSON 截断，已从截断响应中恢复 {len(partial_items)} 条 item")
        return {"items": partial_items}
    return {}


# ── LLM 调用（带信号量） ───────────────────────────────────────────────────────
async def _llm_call(prompt: str) -> str:
    async with _LLM_SEM:
        llm = get_smart_comparison_llm()
        resp = await llm.ainvoke(prompt)
        return resp.content if hasattr(resp, "content") else str(resp)


# ── Step 2：LLM 分组规划 ──────────────────────────────────────────────────────
# 提示词与 fast_extraction.py 慢路径共用（见 import），保证两个接口分组逻辑一致。


async def plan_groups(meta: dict) -> GroupPlanResult:
    """单次 LLM 调用，完成结构类型判断 + 分组规划。"""
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
        logger.warning(f"[SmartPlan] {meta['standard_no']} 分组规划解析失败，返回空分组")
        return GroupPlanResult()
    try:
        return GroupPlanResult(**raw)
    except Exception as e:
        logger.warning(f"[SmartPlan] {meta['standard_no']} 分组结构校验失败: {e}")
        return GroupPlanResult()


# ── Step 3：构建 chunk 文本 ───────────────────────────────────────────────────
_CROSS_REF_VERIFY_PROMPT = """\
以下片段来自某标准的章节文本，每条包含一个疑似"引用本标准内部其他章节"的编号及其上下文。

{candidates_json}

请判断每条是否真的是对本标准内部章节的引用。排除以下情况：
- 外部标准引用（如 GB/T 1234、ISO 123、IEC 456 等）
- 图、表、公式编号（如"图4.1"、"表3.2"、"公式(4.1)"）
- 数字量值巧合（如"3.5倍"、"4.2mm"、"pH 6.8"）

只输出确认为本标准内部章节引用的章节号 JSON 数组（无则输出空数组，不要 markdown 代码块）：
["4.3", "5.1.2"]
"""


async def _verify_cross_refs(chunk_text: str, candidates: set[str]) -> set[str]:
    """正则候选章节引用经 LLM 验证，过滤误报；LLM 失败时回退到正则结果。"""
    # 每个候选只取首次出现的上下文，避免重复
    seen: set[str] = set()
    contexts: list[dict] = []
    for m in _CROSS_REF_RE.finditer(chunk_text):
        ref = m.group(1)
        if ref not in candidates or ref in seen:
            continue
        seen.add(ref)
        start = max(0, m.start() - 60)
        end = min(len(chunk_text), m.end() + 60)
        contexts.append({"ref": ref, "context": chunk_text[start:end].strip()})

    if not contexts:
        return candidates

    prompt = _CROSS_REF_VERIFY_PROMPT.format(
        candidates_json=json.dumps(contexts, ensure_ascii=False)
    )
    try:
        text = await _llm_call(prompt)
    except Exception as e:
        logger.warning(f"[CrossRef] LLM 验证调用失败（{e}），回退到正则结果")
        return candidates

    verified_raw = _parse_json_array(text)
    # _parse_json_array 解析失败时返回 []，无法与 LLM 真正返回空数组区分；
    # 用原始文本做二次判断：若文本中完全没有 "[" 才认为是解析失败，回退到正则结果
    if not verified_raw and "[" not in text:
        logger.warning("[CrossRef] LLM 验证返回格式异常，回退到正则结果")
        return candidates

    result = {str(r) for r in verified_raw if isinstance(r, str)} & candidates
    logger.debug(f"[CrossRef] 候选 {candidates} → 验证通过 {result}")
    return result


async def _build_chunk_text(
    standard_no: str, chapter_nos: list[str], toc_summary: str, storage_path: str = ""
) -> str:
    """读取章节 HTML，转紧凑文本，追加同标准跨章引用章节，图片引用内联解析，附 TOC 概览。"""
    chapters = await fetch_chapters(standard_no, chapter_nos)

    included: set[str] = {ch["title_no"] for ch in chapters}
    parts = [
        f"### [{ch['title_no']}] {ch['title']}\n{html_to_compact(ch.get('word') or '')}"
        for ch in chapters
    ]
    chunk_text = "\n\n".join(parts)

    # 正则找候选，LLM 验证后追加被引章节（仅同标准内）
    candidates = set(_CROSS_REF_RE.findall(chunk_text)) - included
    if candidates:
        refs = await _verify_cross_refs(chunk_text, candidates)
        if refs:
            extra = await fetch_chapters(standard_no, list(refs))
            for ch in extra:
                if ch["title_no"] not in included:
                    compact = html_to_compact(ch.get("word") or "")
                    chunk_text += f"\n\n### [引用章节 {ch['title_no']}] {ch['title']}\n{compact}"

    # 图片引用在提取前内联解析（与 fast_extraction.py 慢路径一致）：让提取模型
    # 直接看到表格/图片实际内容，而不是先占位、提取后再单独调 LLM 回填数值——
    # 后者无法发现"指标名称本身只出现在表格里"的情况。
    chunk_text = await _resolve_img_refs_inline(chunk_text, storage_path)

    return f"【章节目录概览】\n{toc_summary}\n\n【本组章节内容】\n{chunk_text}"


# ── Step 4：并行提取 ──────────────────────────────────────────────────────────
# 指标提取 Prompt 与 fast_extraction.py 慢路径共用同一个常量（见 import _INDICATOR_EXTRACTION_PROMPT），
# 保证两个接口提取口径与输出字段语义完全一致，避免两份文案分别维护后逐渐漂移。


async def _extract_chunk(
    standard_no: str, standard_name: str, use_range: str,
    standard_object: str, applicable_object: str, chunk_text: str,
) -> list[dict]:
    """单分组提取，1 次 LLM 调用，JSON 容错解析。"""
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
    if not isinstance(result, list):
        return []
    return [r for r in result if isinstance(r, dict) and r.get("indicator_object")]


def _merge_indicators(chunk_results: list[list[dict]]) -> list[dict]:
    """合并多个分组的提取结果，按 (indicator_object, standard_object) 去重。"""
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


async def _extract_standard(meta: dict, plan: GroupPlanResult) -> list[dict]:
    """并行提取一个标准的所有分组指标。"""
    if not plan.groups:
        logger.warning(f"[SmartExtract] {meta['standard_no']} 无可用分组，跳过提取")
        return []

    toc_summary = "\n".join(
        f"  {c['title_no']} {c['title']} ({c.get('word_count', 0)}字)"
        for c in meta["toc"]
    )
    storage_path = meta.get("storage_path", "")
    chunk_texts = await asyncio.gather(*[
        _build_chunk_text(meta["standard_no"], g.chapter_nos, toc_summary, storage_path)
        for g in plan.groups
    ])
    results = await asyncio.gather(*[
        _extract_chunk(
            meta["standard_no"], meta["name"], meta["use_range"],
            plan.standard_object, plan.applicable_object, ct,
        )
        for ct in chunk_texts
    ])
    indicators = _merge_indicators(list(results))
    # 兜底：剥除任何残留的图片占位符外壳（正常情况下提取前已内联解析，不应再出现）
    _strip_img_refs(indicators, ["source_value"])
    logger.info(f"[SmartExtract] {meta['standard_no']} 提取 {len(indicators)} 条指标")
    return indicators


# ── 图片引用解析（提取前内联，与 fast_extraction.py 慢路径一致）────────────────
# _fetch_img_word / _strip_img_refs 已收敛至 fast_extraction（顶部 import）


async def _resolve_img_refs_inline(text: str, storage_path: str) -> str:
    """将文本中的 [REF:图片:xxx]/[图片:xxx] 占位符替换为图片/表格实际解析文本。

    提取前直接内联替换（不经 LLM 消歧），让提取模型能看到表格/图片的完整内容——
    这样才能提取出"指标名称本身写在表格里"的情况，而不只是给已知指标补数值。
    """
    filenames: list[str] = []
    seen: set[str] = set()
    for pattern in (_RE_IMG_REF, _RE_IMG_PLAIN):
        for m in pattern.finditer(text):
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

    text = _RE_IMG_REF.sub(_replace, text)
    text = _RE_IMG_PLAIN.sub(_replace, text)
    return text


# _strip_img_refs 已收敛至 fast_extraction（顶部 import）


# ── Step 6：比对 ──────────────────────────────────────────────────────────────
_COMPARISON_PROMPT = """\
你是资深标准化专家。请对比以下两个标准之间的技术指标差异，给出逐条分析结果。

## 源标准：{source_no}（{source_name}）
适用范围：{source_range}

{source_text}

---

## 目标标准：{target_no}（{target_name}）
适用范围：{target_range}

{target_text}

---

**任务**：从两个标准中提取全部技术指标，按以下规则逐条对比：

1. **同义指标（matched）**：两标准中针对**同一性能项目或参数**提出要求，即视为同义指标。包括：完全相同或同义的指标名称；无单位的定性要求（如两者均要求"外观符合"/"表面无缺陷"等）。即使数值不同，只要衡量的是同一性能，就视为 matched；对于有单位的指标，其单位在物理上概念相同即可判定为matched **判断核心：这两个指标是否在衡量同一件事？**
2. **源标准独有指标（source_only）**：目标标准中没有任何对应指标的，归为此类。
3. **目标标准独有指标（target_only）**：源标准中没有任何对应指标的，归为此类。
4. **如果指标在源标准或目标标准中只有其中一方具有，则不应归类到同义指标上。**

字段说明：
- comparison_type：matched | source_only | target_only
- change_analysis：matched 时简要说明差异，source_only / target_only 时留空字符串。matched 说明优先使用专业词汇（收严、放宽、一致、条件增加、条件删减、范围扩大、范围缩小、方法替换、要求细化、要求概化等），再加一句具体说明
- norm_class：从对应指标的 norm_class 字段直接透传（matched 时取源指标值；source_only 取源；target_only 取目标），不重新判断
- **重要：所有字段必须是非空字符串，不得为 null。当指标不存在于某一方时，对应方的 indicator_object / value / clause 全部填 ""（空字符串），而不是 null。**

只输出纯 JSON，不要 markdown 代码块：
{{
  "items": [
    {{
      "source_indicator_object": "拉伸强度",
      "source_value": "≥370 MPa",
      "source_clause": "4.3.1",
      "target_indicator_object": "抗拉强度",
      "target_value": "≥420 MPa",
      "target_clause": "5.2",
      "standard_object": "钢管",
      "comparison_type": "matched",
      "change_analysis": "收严：由 370 MPa 提升至 420 MPa",
      "norm_class": "规范类要素"
    }},
    {{
      "source_indicator_object": "导线截面积",
      "source_value": "≥6 mm²",
      "source_clause": "8.2",
      "target_indicator_object": "",
      "target_value": "",
      "target_clause": "",
      "standard_object": "导线连接",
      "comparison_type": "source_only",
      "change_analysis": "",
      "norm_class": "规范类要素"
    }},
    {{
      "source_indicator_object": "",
      "source_value": "",
      "source_clause": "",
      "target_indicator_object": "回路压降",
      "target_value": "≤1.0 V",
      "target_clause": "5.2.2",
      "standard_object": "导线连接",
      "comparison_type": "target_only",
      "change_analysis": "",
      "norm_class": "规范类要素"
    }},
    {{
      "source_indicator_object": "冲击功",
      "source_value": "≥27 J",
      "source_clause": "4.5.2",
      "target_indicator_object": "",
      "target_value": "",
      "target_clause": "",
      "standard_object": "钢管",
      "comparison_type": "source_only",
      "change_analysis": "",
      "norm_class": "规范类要素"
    }},
    {{
      "source_indicator_object": "",
      "source_value": "",
      "source_clause": "",
      "target_indicator_object": "晶间腐蚀",
      "target_value": "合格",
      "target_clause": "6.3",
      "standard_object": "钢管",
      "comparison_type": "target_only",
      "change_analysis": "",
      "norm_class": "规范类要素"
    }}
  ],
  "relationship": "替代关系",
  "overall_assessment": "综合评价"
}}
"""


def _build_ind_lookup(inds: list[dict]) -> dict[str, dict]:
    """根据 indicator_object 构建指标查找表，用于回填 indicator_category / object_type。"""
    lookup: dict[str, dict] = {}
    for ind in inds:
        key = (ind.get("indicator_object") or "").strip()
        if key:
            lookup[key] = ind
    return lookup


async def _compare_once(
    source_inds: list[dict],
    target_inds: list[dict],
    src_meta: dict,
    tgt_meta: dict,
) -> SmartCompareResult:
    """单次 LLM 比对调用。"""
    prompt = _COMPARISON_PROMPT.format(
        source_no=src_meta["standard_no"],
        source_name=src_meta["name"],
        source_range=src_meta.get("use_range", ""),
        source_text=json.dumps(source_inds, ensure_ascii=False),
        target_no=tgt_meta["standard_no"],
        target_name=tgt_meta["name"],
        target_range=tgt_meta.get("use_range", ""),
        target_text=json.dumps(target_inds, ensure_ascii=False),
    )
    logger.info(f"[SmartCompare] 开始比对: 源 {len(source_inds)} 条 / 目标 {len(target_inds)} 条")
    text = await _llm_call(prompt)
    raw = _parse_json_obj(text)
    if not raw:
        logger.warning(f"[SmartCompare] LLM 返回无效 JSON，原始响应（前 300 字）: {text[:300]!r}")
        return SmartCompareResult()
    items_raw = raw.get("items", [])
    if not items_raw:
        logger.warning(f"[SmartCompare] LLM 返回 items 为空，relationship={raw.get('relationship')!r}，原始（前 300 字）: {text[:300]!r}")
    try:
        items = [SmartCompareItem(**it) for it in items_raw]
        # 从原始提取结果回填 indicator_category 和 object_type（LLM 比对输出不含这两个字段）
        src_lookup = _build_ind_lookup(source_inds)
        tgt_lookup = _build_ind_lookup(target_inds)
        for item in items:
            if item.source_indicator_object and item.source_indicator_object in src_lookup:
                item.source_indicator_category = src_lookup[item.source_indicator_object].get("indicator_category") or ""
                item.source_object_type = src_lookup[item.source_indicator_object].get("object_type") or ""
            if item.target_indicator_object and item.target_indicator_object in tgt_lookup:
                item.target_indicator_category = tgt_lookup[item.target_indicator_object].get("indicator_category") or ""
                item.target_object_type = tgt_lookup[item.target_indicator_object].get("object_type") or ""
        logger.info(f"[SmartCompare] 解析完成: {len(items)} 条（matched={sum(1 for i in items if i.comparison_type=='matched')}）")
        return SmartCompareResult(
            items=items,
            relationship=raw.get("relationship", "部分重叠"),
            overall_assessment=raw.get("overall_assessment", ""),
        )
    except Exception as e:
        logger.warning(f"[SmartCompare] 比对结果解析失败: {e}，原始（前 300 字）: {text[:300]!r}")
        return SmartCompareResult()


def _group_by_object(indicators: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for ind in indicators:
        key = ind.get("standard_object") or "其他"
        groups.setdefault(key, []).append(ind)
    return groups


# ── Step 5.7: standard_object 语义对齐 ───────────────────────────────────────
_OBJECT_ALIGN_PROMPT = """\
以下是两个标准各自提取指标时使用的 standard_object（被规定对象）名称列表。\
请判断目标标准中哪些名称与源标准中的名称指同一物理对象，给出映射关系。

源标准 {source_no} 的对象列表：{source_objects}
目标标准 {target_no} 的对象列表：{target_objects}

规则：
- 仅映射"明确为同一物理对象不同表述"的条目（如"不锈钢管"→"钢管"、"光伏组件"→"太阳能电池板"）
- 不确定时不映射，保持原名
- 只输出 JSON，无 markdown 代码块

输出格式（将目标对象名映射到对应的源对象名，无映射则输出空对象）：
{{"target_to_source": {{"不锈钢管": "钢管"}}}}
"""


async def _align_standard_objects(
    source_inds: list[dict],
    target_inds: list[dict],
    src_meta: dict,
    tgt_meta: dict,
) -> None:
    """
    对齐两标准的 standard_object 标签：将目标标准中语义相同但名称不同的对象名
    重命名为源标准对应的名称，保证后续分组 key 一致。
    原地修改 target_inds 中的 standard_object 字段。
    """
    src_objects = sorted({ind.get("standard_object") or "其他" for ind in source_inds})
    tgt_objects = sorted({ind.get("standard_object") or "其他" for ind in target_inds})

    # 完全一致，无需对齐
    if set(src_objects) == set(tgt_objects):
        logger.info("[ObjectAlign] standard_object 完全一致，跳过对齐")
        return

    # 快路径：双方各只有 1 个对象且名称不同 → 直接将目标映射到源，不消耗 LLM
    if len(src_objects) == 1 and len(tgt_objects) == 1:
        old, new = tgt_objects[0], src_objects[0]
        logger.info(f"[ObjectAlign] 单对象快速对齐: '{old}' → '{new}'")
        for ind in target_inds:
            if (ind.get("standard_object") or "其他") == old:
                ind["standard_object"] = new
        return

    # 通用路径：调用 LLM 判断多对象间的映射关系（仅 1 次调用）
    prompt = _OBJECT_ALIGN_PROMPT.format(
        source_no=src_meta["standard_no"],
        target_no=tgt_meta["standard_no"],
        source_objects=json.dumps(src_objects, ensure_ascii=False),
        target_objects=json.dumps(tgt_objects, ensure_ascii=False),
    )
    text = await _llm_call(prompt)
    raw = _parse_json_obj(text)
    mapping: dict[str, str] = (raw.get("target_to_source") or {}) if raw else {}

    if not mapping:
        logger.warning("[ObjectAlign] LLM 未返回有效映射，跳过对齐")
        return

    for ind in target_inds:
        old = ind.get("standard_object") or "其他"
        if old in mapping:
            ind["standard_object"] = mapping[old]

    logger.info(f"[ObjectAlign] 对齐完成，映射: {mapping}")


async def _compare_all(
    source_inds: list[dict],
    target_inds: list[dict],
    src_meta: dict,
    tgt_meta: dict,
) -> SmartCompareResult:
    """根据指标总量选择单次比对或按 standard_object 分组并行比对。"""
    total = len(source_inds) + len(target_inds)

    if total <= 150:
        return await _compare_once(source_inds, target_inds, src_meta, tgt_meta)

    # 分组前先对齐 standard_object，防止同一对象因名称差异被分入不同组
    await _align_standard_objects(source_inds, target_inds, src_meta, tgt_meta)

    # 分组
    src_groups = _group_by_object(source_inds)
    tgt_groups = _group_by_object(target_inds)
    all_keys = list(set(src_groups) | set(tgt_groups))
    logger.info(f"[SmartCompare] 指标总量 {total}，共 {len(all_keys)} 个 standard_object 分组")

    _SMALL_THRESHOLD = 10  # 两侧合计 ≤ 此值的组合并为一次批调用

    direct_items: list[SmartCompareItem] = []  # 单侧组：直接生成，不调 LLM
    small_src: list[dict] = []                 # 小组合并池
    small_tgt: list[dict] = []
    llm_tasks: list = []                       # 大组：每组独立 LLM 任务

    for k in all_keys:
        src = src_groups.get(k, [])
        tgt = tgt_groups.get(k, [])

        if not tgt:
            # 源标准独有，结果确定，无需 LLM
            for ind in src:
                direct_items.append(SmartCompareItem(
                    source_indicator_object=ind.get("indicator_object", ""),
                    source_value=ind.get("source_value", ""),
                    source_clause=ind.get("source_clause", ""),
                    source_indicator_category=ind.get("indicator_category") or "",
                    source_object_type=ind.get("object_type") or "",
                    standard_object=k,
                    comparison_type="source_only",
                    norm_class=ind.get("norm_class", ""),
                ))
        elif not src:
            # 目标标准独有，结果确定，无需 LLM
            for ind in tgt:
                direct_items.append(SmartCompareItem(
                    target_indicator_object=ind.get("indicator_object", ""),
                    target_value=ind.get("source_value", ""),
                    target_clause=ind.get("source_clause", ""),
                    target_indicator_category=ind.get("indicator_category") or "",
                    target_object_type=ind.get("object_type") or "",
                    standard_object=k,
                    comparison_type="target_only",
                    norm_class=ind.get("norm_class", ""),
                ))
        elif len(src) + len(tgt) <= _SMALL_THRESHOLD:
            # 两侧都有数据但指标少，合并到批调用池
            small_src.extend(src)
            small_tgt.extend(tgt)
        else:
            # 大组：独立 LLM 调用
            llm_tasks.append(_compare_once(src, tgt, src_meta, tgt_meta))

    # 并行执行：小组批次（最多 1 次）+ 各大组
    tasks = []
    has_small_batch = bool(small_src or small_tgt)
    if has_small_batch:
        tasks.append(_compare_once(small_src, small_tgt, src_meta, tgt_meta))
    tasks.extend(llm_tasks)

    results = await asyncio.gather(*tasks)
    all_items = direct_items + [item for r in results for item in r.items]

    logger.info(
        f"[SmartCompare] 直接生成 {len(direct_items)} 条（跳过LLM），"
        f"LLM调用 {len(tasks)} 次"
        f"（小组合批 {'1' if has_small_batch else '0'} 次 + 大组 {len(llm_tasks)} 次）"
    )

    # 取前20条做一次汇总调用，只为获取 relationship 和 overall_assessment
    summary = await _compare_once(source_inds[:20], target_inds[:20], src_meta, tgt_meta)
    return SmartCompareResult(
        items=all_items,
        relationship=summary.relationship,
        overall_assessment=summary.overall_assessment,
    )


# ── 重点关注指标挑选（综合评价增强）───────────────────────────────────────────
# LLM 只负责"从比对结果里挑选 + 写理由"，格式（**加粗** 拼接、数量硬截断、
# 名称校验防编造、去重）全部由后端保证。compare-smart-v1 / v2（快/慢路径）共用。

_MAX_KEY_INDICATORS = 10       # 重点关注指标上限（太多会显得综合评价冗长）
_KEY_IND_LINES_LIMIT = 250     # 喂给挑选 LLM 的指标行数上限（一行一条，防 prompt 过长）

_KEY_INDICATORS_PROMPT = """\
你是资深标准化专家。以下是两个标准的技术指标差异清单，请从中挑选**最值得重点关注**的指标（最多 {max_count} 条）。

## 差异指标清单
{items_text}

**挑选原则（按优先级）**：
1. matched 中发生实质性变化的指标（收严、放宽、数值大幅变化、条件增减、方法替换）
2. 一方独有的关键性能 / 安全 / 环保类指标（source_only / target_only）
3. 对产品设计、检验验收或合规判定影响重大的指标
4. 不挑选"一致"的、纯编辑性的、常规通用项（如外观、标志）

**输出字段**：
- name：指标名称（matched 用源标准侧名称，独有指标用存在一方的名称），必须原样取自清单
- reason：一句话说明为何重点关注，不超过 20 字（如"收严：370→420 MPa"、"目标标准新增"）
- 宁缺毋滥：值得关注的不足 {max_count} 条就少选，没有值得关注的就输出空数组

只输出纯 JSON，不要 markdown 代码块：
{{"key_indicators": [{{"name": "抗拉强度", "reason": "收严：370→420 MPa"}}]}}
"""


def _key_indicator_lines(items: list[SmartCompareItem]) -> list[str]:
    """将比对结果压缩为"一行一指标"的精简文本，供重点关注指标挑选 LLM 阅读。

    排序：有实质差异的（matched 非一致 + 两侧独有）在前，"一致"的 matched 在后；
    超 _KEY_IND_LINES_LIMIT 行时截断（被截掉的基本都是一致项，不影响挑选质量）。
    """
    changed: list[str] = []
    consistent: list[str] = []
    for item in items:
        if item.comparison_type == "matched":
            name = (item.source_indicator_object or item.target_indicator_object or "").strip()
            line = f"[matched] {name}：{item.source_value} → {item.target_value}"
            if item.change_analysis:
                line += f"（{item.change_analysis}）"
            (consistent if (item.change_analysis or "").strip().startswith("一致") else changed).append(line)
        elif item.comparison_type == "source_only":
            changed.append(
                f"[source_only] {item.source_indicator_object}：{item.source_value}（仅源标准有）"
            )
        else:
            changed.append(
                f"[target_only] {item.target_indicator_object}：{item.target_value}（仅目标标准有）"
            )
    return (changed + consistent)[:_KEY_IND_LINES_LIMIT]


async def enrich_key_indicators(result: SmartCompareResult) -> None:
    """LLM 挑选 ≤ _MAX_KEY_INDICATORS 条重点关注指标，以加粗段落追加到 overall_assessment 末尾。

    追加格式：\\n\\n**重点关注指标**：**指标A**（理由）、**指标B**（理由）……
    失败（LLM 异常 / 解析失败 / 无有效挑选）时保持原 overall_assessment 不变，不影响主流程。
    """
    if not result.items:
        return

    lines = _key_indicator_lines(result.items)
    if not lines:
        return

    items_text = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))
    prompt = _KEY_INDICATORS_PROMPT.format(
        max_count=_MAX_KEY_INDICATORS,
        items_text=items_text,
    )
    try:
        text = await _llm_call(prompt)
    except Exception as e:
        logger.warning(f"[KeyIndicators] LLM 挑选调用失败（{e}），保持原综合评价")
        return

    raw = _parse_json_obj(text)
    entries = (raw.get("key_indicators") or []) if raw else []
    if not isinstance(entries, list):
        entries = []

    # 合法指标名集合（规范化小写），校验 LLM 输出、防编造不存在的指标
    valid_names: set[str] = set()
    for item in result.items:
        for n in (item.source_indicator_object, item.target_indicator_object):
            n = (n or "").strip()
            if n:
                valid_names.add(_RE_NORM.sub("", n).lower())

    seen: set[str] = set()
    parts: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        norm = _RE_NORM.sub("", name).lower()
        if norm in seen or norm not in valid_names:
            continue
        seen.add(norm)
        reason = str(entry.get("reason") or "").strip()
        parts.append(f"**{name}**" + (f"（{reason}）" if reason else ""))
        if len(parts) >= _MAX_KEY_INDICATORS:
            break

    if not parts:
        logger.info("[KeyIndicators] 未挑选出重点关注指标，保持原综合评价")
        return

    suffix = "**重点关注指标**：" + "、".join(parts)
    base = (result.overall_assessment or "").strip()
    result.overall_assessment = (base + "\n\n" + suffix) if base else suffix
    logger.info(f"[KeyIndicators] 综合评价追加 {len(parts)} 条重点关注指标")


# ── 试验提取 ──────────────────────────────────────────────────────────────────
# 提示词与 fast_extraction.py 慢路径共用（见 import），带 object_type/indicator_category
# 分类字段及 indicators 关联子字段，保证两个接口试验提取的分类字段语义一致。


_MAX_TEST_CHUNK = 30_000  # 单次试验提取 LLM 调用的最大文本字符数


def _split_text_by_chapters(text: str, max_chunk: int = _MAX_TEST_CHUNK) -> list[str]:
    """将带 '### [...]' 章节标题的紧凑文本按章节边界切分，每块不超过 max_chunk 字符。"""
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


async def _extract_tests_chunk(standard_no: str, standard_name: str, chunk_text: str) -> list[dict]:
    """单块试验提取，不截断，1 次 LLM 调用。"""
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
    """合并多块试验提取结果，按 (test_name_归一化, source_clause) 去重。"""
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


async def _extract_tests_for_standard(
    standard_no: str, standard_name: str, compact_text: str
) -> list[dict]:
    """从紧凑全文中提取所有试验条目，自动按章节切块并发（每块 ≤ _MAX_TEST_CHUNK 字符）。"""
    if not compact_text.strip():
        return []
    chunks = _split_text_by_chapters(compact_text)
    if not chunks:
        return []
    results = await asyncio.gather(*[
        _extract_tests_chunk(standard_no, standard_name, c)
        for c in chunks
    ])
    tests = _merge_tests(list(results))
    # 兜底：剥除残留的图片占位符外壳及原始 <img> 标签（compare-smart-v2 快/慢路径共用此函数）
    _strip_img_refs(tests, ["conditions", "preparation", "procedure", "acceptance"])
    logger.info(f"[TestExtract] {standard_no} 提取 {len(tests)} 条试验（{len(chunks)} 块并发）")
    return tests


_TEST_CACHE_FIELDS = (
    "test_name", "method_desc", "conditions", "preparation", "procedure",
    "acceptance", "report_items", "source_clause", "standard_object",
    "applicable_object", "object_type", "indicator_category",
)


async def _load_tests_from_cache(standard_no: str) -> list[dict] | None:
    """
    从 standard_cache_test 读取有效试验；无有效缓存时返回 None。

    _TEST_EXTRACTION_PROMPT 在 extract-batch-fast 与 compare-smart（快/慢路径）间
    共用（见 import），落库字段完全一致，无需按算法版本或类型过滤，可直接复用。
    """
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    f"""
                    SELECT {", ".join(f"`{f}`" for f in _TEST_CACHE_FIELDS)}
                    FROM standard_cache_test
                    WHERE standard_no = %s AND is_valid = 1
                    ORDER BY id
                    """,
                    [standard_no],
                )
                rows = list(await cur.fetchall())
        if not rows:
            return None
        # 不同 algorithm_version（如 v3-fast 与 smart_v2 自缓存）可能同时存在有效记录，
        # 按 (test_name规范化, source_clause) 去重，与 _merge_tests 保持一致的去重口径
        seen: set[tuple[str, str]] = set()
        result: list[dict] = []
        for r in rows:
            test_name = r.get("test_name") or ""
            if not test_name:
                continue
            source_clause = (r.get("source_clause") or "").strip()
            key = (_RE_NORM.sub("", test_name).lower(), source_clause)
            if key in seen:
                continue
            seen.add(key)
            result.append({field: (r.get(field) or "") for field in _TEST_CACHE_FIELDS})
        return result or None
    except Exception as e:
        logger.warning(f"[TestExtract] 缓存读取失败({standard_no}): {e}")
        return None


async def _get_tests_for_compare(
    standard_no: str, standard_name: str, compact_text: str
) -> list[dict]:
    """先查 standard_cache_test 缓存，命中则直接返回；未命中则调用 LLM 提取。"""
    cached = await _load_tests_from_cache(standard_no)
    if cached is not None:
        logger.info(f"[TestExtract] 缓存命中: {standard_no}，{len(cached)} 条试验")
        return cached
    logger.info(f"[TestExtract] 缓存未命中: {standard_no}，调用 LLM 提取")
    return await _extract_tests_for_standard(standard_no, standard_name, compact_text)


# ── 指标↔试验 关联（证据门控，与 fast_extraction 一致）──────────────────────
# 设计同 fast_extraction._link_ind_to_tests：确定性候选生成（章节号引用 / 方法
# 标准号交集 / 同条款共存）→ 有候选指标按 standard_object 分组并发 LLM 验证
# → 零候选残留批量兜底（_LINK_FALLBACK_ENABLED）。source / target 各自独立跑
# 一遍，两侧并行；每侧内部按对象分组并发，受 _LLM_SEM 节流。
# 关联必须命中 evidence 文本，否则填空串（不触发下游"降级旧逻辑"误挂试验）。


def _generate_side_candidates(
    active: list[tuple[int, "SmartCompareItem"]],
    tests: list[dict],
    ind_clause_key: str,
    clause_map: dict[str, str],
) -> dict[int, list[str]]:
    """单侧确定性候选生成：{global_item_idx: [test_name,...]}，三类证据信号并集。

    1. 章节号引用：指标 clause_text 引用试验 source_clause（父子章节双向）
    2. 方法标准引用：指标 clause_text 与试验 method_desc/acceptance 共享方法标准号
    3. 同条款共存：指标 source_clause 与试验 source_clause 共有章节号
    """
    test_std: dict[str, set[str]] = {}
    test_clause: dict[str, list[str]] = {}
    test_names: list[str] = []
    for t in tests:
        tname = t.get("test_name", "")
        if not tname:
            continue
        test_names.append(tname)
        test_std[tname] = _extract_method_stds((t.get("method_desc") or "") + " " + (t.get("acceptance") or ""))
        test_clause[tname] = _clause_parts(t.get("source_clause", ""))

    candidates: dict[int, list[str]] = {}
    for gi, item in active:
        clause_no = getattr(item, ind_clause_key, "") or ""
        text = clause_map.get(clause_no, "") or ""
        ind_stds = _extract_method_stds(text)
        ind_refs = _extract_clause_refs(text)
        ind_cparts = set(_clause_parts(clause_no))

        hits: list[str] = []
        seen: set[str] = set()
        for tname in test_names:
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
            candidates[gi] = hits
    return candidates


async def _link_side_evidence_gated(
    items: list["SmartCompareItem"],
    tests: list[dict],
    side: str,  # "source" or "target"
    clause_map: dict[str, str],
) -> dict[int, str]:
    """单侧证据门控关联：候选生成→分组并发验证→零候选兜底。

    返回 {global_item_idx: test_name}。有该侧指标名的 item 必出现（无证据填 ""），
    无指标名的 item 缺席（避免下游将"未关联"误判为"关联未运行"而降级旧逻辑）。
    """
    ind_obj_key = f"{side}_indicator_object"
    ind_clause_key = f"{side}_clause"
    ind_val_key = f"{side}_value"

    active = [
        (gi, item) for gi, item in enumerate(items)
        if getattr(item, ind_obj_key, "")
    ]
    # 有该侧指标名的 item 一律占位 ""（无证据即不挂试验）
    links: dict[int, str] = {gi: "" for gi, _ in active}
    if not active or not tests:
        return links

    test_by_name = {t["test_name"]: t for t in tests if t.get("test_name")}
    if not test_by_name:
        return links

    # Step 1: 候选生成（CPU，瞬时）
    candidates = _generate_side_candidates(active, tests, ind_clause_key, clause_map)
    has_cand = [gi for gi, _ in active if gi in candidates]
    no_cand = [gi for gi, _ in active if gi not in candidates]
    logger.info(
        f"[LinkTests][{side}] 候选生成: 有候选 {len(has_cand)}，零候选 {len(no_cand)}"
    )

    def _collect(gi: int, test_name: str) -> None:
        links[gi] = test_name

    # Step 2: 有候选按 standard_object 分组并发 LLM 验证
    by_obj: dict[str, list[tuple[int, "SmartCompareItem"]]] = {}
    for gi in has_cand:
        item = items[gi]
        key = item.standard_object or "其他"
        by_obj.setdefault(key, []).append((gi, item))

    async def _verify_group(obj_key: str, indexed: list[tuple[int, "SmartCompareItem"]]) -> None:
        """一组有候选指标的 LLM 验证：每条指标只喂自己的候选试验。"""
        indicators_for_llm: list[dict] = []
        group_test_names: list[str] = []
        seen_t: set[str] = set()
        for gi, item in indexed:
            cands = candidates.get(gi, [])
            for cn in cands:
                if cn not in seen_t:
                    seen_t.add(cn)
                    group_test_names.append(cn)
            indicators_for_llm.append({
                "ind_idx": gi,
                "indicator_object": getattr(item, ind_obj_key, "") or "",
                "source_value": getattr(item, ind_val_key, "") or "",
                "source_clause": getattr(item, ind_clause_key, "") or "",
                "clause_text": clause_map.get(getattr(item, ind_clause_key, "") or "", ""),
                "candidate_tests": cands,
            })
        tests_for_llm: list[dict] = []
        for tname in group_test_names:
            t = test_by_name.get(tname)
            if t is None:
                continue
            tests_for_llm.append({
                "test_name": t.get("test_name", ""),
                "method_desc": t.get("method_desc", ""),
                "source_clause": t.get("source_clause", ""),
                "acceptance": t.get("acceptance", ""),
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
                # 输出校验：test_name 必须在该指标候选集 + 全局试验表里
                if test_name not in candidates.get(ind_idx, []) or test_name not in test_by_name:
                    continue
                try:
                    gi = int(ind_idx)
                except (TypeError, ValueError):
                    continue
                _collect(gi, test_name)
        except Exception as e:
            logger.warning(f"[LinkTests][{side}] 对象组[{obj_key}]验证失败: {e}")

    await asyncio.gather(*[_verify_group(k, v) for k, v in by_obj.items()])

    # Step 3: 零候选残留批量兜底（用全量试验，仍受证据门控）
    if _LINK_FALLBACK_ENABLED and no_cand:
        await _verify_side_fallback(
            no_cand, items, tests, ind_obj_key, ind_clause_key, ind_val_key,
            clause_map, test_by_name, _collect, side,
        )

    return links


async def _verify_side_fallback(
    no_cand: list[int],
    items: list["SmartCompareItem"],
    tests: list[dict],
    ind_obj_key: str,
    ind_clause_key: str,
    ind_val_key: str,
    clause_map: dict[str, str],
    test_by_name: dict[str, dict],
    _collect,
    side: str,
) -> None:
    """单侧零候选残留兜底：用全量试验让 LLM 找任意原文引用证据。

    candidate_tests 设为全量试验名；仍要求命中 evidence 才写回。
    捕获"附录A的方法"等非标准号/非章节号引用（候选生成正则抓不到的）。
    """
    all_test_names = [t["test_name"] for t in tests if t.get("test_name")]
    indicators_for_llm: list[dict] = []
    for gi in no_cand:
        item = items[gi]
        indicators_for_llm.append({
            "ind_idx": gi,
            "indicator_object": getattr(item, ind_obj_key, "") or "",
            "source_value": getattr(item, ind_val_key, "") or "",
            "source_clause": getattr(item, ind_clause_key, "") or "",
            "clause_text": clause_map.get(getattr(item, ind_clause_key, "") or "", ""),
            "candidate_tests": all_test_names,
        })
    tests_for_llm: list[dict] = []
    for t in tests:
        if not t.get("test_name"):
            continue
        tests_for_llm.append({
            "test_name": t.get("test_name", ""),
            "method_desc": t.get("method_desc", ""),
            "source_clause": t.get("source_clause", ""),
            "acceptance": t.get("acceptance", ""),
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
            try:
                gi = int(ind_idx)
            except (TypeError, ValueError):
                continue
            if not (0 <= gi < len(items)):
                continue
            _collect(gi, test_name)
    except Exception as e:
        logger.warning(f"[LinkTests][{side}] 零候选兜底关联失败: {e}")


async def _fetch_clause_map(standard_no: str, clause_nos: list[str]) -> dict[str, str]:
    """批量读取条款原文，返回 {title_no: compact_text}（全文不截断，供证据抽取）。"""
    if not clause_nos or not standard_no:
        return {}
    chs = await fetch_chapters(standard_no, clause_nos)
    return {ch["title_no"]: html_to_compact(ch.get("word") or "") for ch in chs}


async def link_tests_to_result(
    result: SmartCompareResult,
    src_standard_no: str = "",
    tgt_standard_no: str = "",
) -> None:
    """证据门控关联双侧指标↔试验，写回 result.source_test_links / target_test_links。

    source / target 各自独立跑一遍（候选生成→分组并发验证→零候选兜底），
    两侧并行；每侧内部按 standard_object 分组并发，受 _LLM_SEM 节流。
    - 删除章节号硬过滤；章节号/方法标准号/同条款作为正面证据候选信号
    - 有该侧指标名的 item 必出现（无证据填 ""），避免下游降级旧逻辑误挂试验
    """
    # Step 1: 预取双侧条款原文（全文不截断，供证据抽取）
    src_clause_map: dict[str, str] = {}
    tgt_clause_map: dict[str, str] = {}
    if src_standard_no and tgt_standard_no:
        try:
            src_clauses = list({item.source_clause for item in result.items if item.source_clause})
            tgt_clauses = list({item.target_clause for item in result.items if item.target_clause})
            src_clause_map, tgt_clause_map = await asyncio.gather(
                _fetch_clause_map(src_standard_no, src_clauses),
                _fetch_clause_map(tgt_standard_no, tgt_clauses),
            )
            logger.info(f"[LinkTests] 条款原文预取: 源 {len(src_clause_map)} 条，目标 {len(tgt_clause_map)} 条")
        except Exception as e:
            logger.warning(f"[LinkTests] 条款原文预取失败，降级无条款文本模式: {e}")

    # Step 2: source / target 并行（各自内部再按对象分组并发）
    src_links, tgt_links = await asyncio.gather(
        _link_side_evidence_gated(result.items, result.source_tests, "source", src_clause_map),
        _link_side_evidence_gated(result.items, result.target_tests, "target", tgt_clause_map),
    )

    result.source_test_links = src_links
    result.target_test_links = tgt_links
    logger.info(
        f"[LinkTests] 完成（证据门控）: 源关联 {sum(1 for v in src_links.values() if v)} 条, "
        f"目标关联 {sum(1 for v in tgt_links.values() if v)} 条"
    )


# ── 章节过滤工具 ──────────────────────────────────────────────────────────────
_NONTECHNICAL_TITLES = frozenset({
    "前言", "引言", "规范性引用文件",
    "术语和定义", "术语、定义和缩略语", "术语和定义及缩略语",
    "参考文献", "索引",
})
_NONTECHNICAL_KWS = ("资料性附录",)
# 关键词组合匹配，处理"3 术语和定义"等带章节号前缀的变体
_NONTECHNICAL_KW_PAIRS = (
    ("术语", "定义"),
    ("术语", "缩略语"),
)


def _chapters_to_technical_text(chapters: list[dict]) -> str:
    """
    将全量章节列表转换为技术章节紧凑文本。
    过滤前言/引言/术语等非技术章节及其所有子章节，
    防止术语定义（如"3.4 最大偏转频率"）被 LLM 误提取为技术指标或试验。
    """
    def _is_nontechnical(title: str) -> bool:
        t = (title or "").strip()
        return (
            t in _NONTECHNICAL_TITLES
            or any(kw in t for kw in _NONTECHNICAL_KWS)
            or any(a in t and b in t for a, b in _NONTECHNICAL_KW_PAIRS)
        )

    # 第一遍：收集父章节编号前缀
    skip_prefixes: set[str] = set()
    for ch in chapters:
        if _is_nontechnical(ch.get("title") or ""):
            tn = (ch.get("title_no") or "").strip()
            if tn:
                skip_prefixes.add(tn)

    # 第二遍：构建文本，跳过非技术章节及其子章节
    parts: list[str] = []
    for c in chapters:
        if _is_nontechnical(c.get("title") or ""):
            continue
        tn = (c.get("title_no") or "").strip()
        if tn and any(tn == p or tn.startswith(p + ".") for p in skip_prefixes):
            continue
        compact = html_to_compact(c.get("word") or "")
        if compact.strip():
            parts.append(f"### [{c['title_no']}] {c['title']}\n{compact}")
    return "\n\n".join(parts)


# ── 主入口 ────────────────────────────────────────────────────────────────────
async def run_smart_comparison(
    source_standard_no: str, target_standard_no: str
) -> SmartCompareResult:
    """
    完整 compare-smart 流程：

    Step 1: 并行读 TOC（DB, 无 LLM）
    Step 2: 并行 LLM 分组规划（各 1 次 LLM）
    Step 3+4: 并行读章节 + 提取（两标准同时，每组 1 次 LLM；图片引用在提取前内联解析，无额外 LLM 调用）
    Step 5: 试验提取（两标准并行）
    Step 6: 比对（1 次或分组并行）
    """
    logger.info(f"[Smart] 开始: {source_standard_no} <-> {target_standard_no}")

    # Step 1
    src_meta, tgt_meta = await asyncio.gather(
        fetch_standard_meta(source_standard_no),
        fetch_standard_meta(target_standard_no),
    )

    # Step 2
    logger.info("[Smart] Step2 LLM 分组规划")
    src_plan, tgt_plan = await asyncio.gather(
        plan_groups(src_meta),
        plan_groups(tgt_meta),
    )
    logger.info(
        f"[Smart] 源标准分 {len(src_plan.groups)} 组，目标标准分 {len(tgt_plan.groups)} 组"
    )

    # Step 3+4
    logger.info("[Smart] Step3+4 并行提取指标")
    source_inds, target_inds = await asyncio.gather(
        _extract_standard(src_meta, src_plan),
        _extract_standard(tgt_meta, tgt_plan),
    )

    # Step 5：试验提取（复用已加载的全量章节；指标的图片引用已在 Step3+4 提取前内联解析）
    logger.info("[Smart] Step5 试验提取")
    src_chapters, tgt_chapters = await asyncio.gather(
        _fetch_all_chapters(source_standard_no),
        _fetch_all_chapters(target_standard_no),
    )
    src_full_text = _chapters_to_technical_text(src_chapters)
    tgt_full_text = _chapters_to_technical_text(tgt_chapters)
    # 图片引用在试验提取前内联解析（与指标提取一致），避免 [REF:图片:xxx] 占位符原样流入 acceptance 等字段
    src_full_text, tgt_full_text = await asyncio.gather(
        _resolve_img_refs_inline(src_full_text, src_meta.get("storage_path", "")),
        _resolve_img_refs_inline(tgt_full_text, tgt_meta.get("storage_path", "")),
    )
    source_tests, target_tests = await asyncio.gather(
        _get_tests_for_compare(source_standard_no, src_meta["name"], src_full_text),
        _get_tests_for_compare(target_standard_no, tgt_meta["name"], tgt_full_text),
    )
    logger.info(f"[Smart] 试验提取完成: 源 {len(source_tests)} 条，目标 {len(target_tests)} 条")

    # Step 6
    logger.info("[Smart] Step6 比对")
    result = await _compare_all(source_inds, target_inds, src_meta, tgt_meta)
    result.source_tests = source_tests
    result.target_tests = target_tests
    # 重点关注指标挑选只依赖 result.items，与指标↔试验关联互不依赖（写不同字段），
    # 并行执行以将挑选调用的耗时完全隐藏在关联阶段内，不增加接口整体时长
    await asyncio.gather(
        link_tests_to_result(result, source_standard_no, target_standard_no),
        enrich_key_indicators(result),
    )
    logger.info(f"[Smart] 完成: {len(result.items)} 条比对结果")
    return result
