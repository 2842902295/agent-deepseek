"""
compare-smart-v2：快/慢路径标准差异对比

快路径判断（与 extract-batch-fast 的单标准判断口径一致）：
  源、目标各自的全文 compact 字符数 <= SMART_V2_FAST_BATCH_COMPACT_LIMIT → 进入快路径
  降级：任一标准的 compact 超阈值 → 调用 run_smart_comparison()（smart_v1 流程）

快路径流程：
  1. 拉取全部章节，html_to_compact()，处理内部交叉引用
  2. compact 文本分别判断（源、目标各自与阈值比较，任一超限即整体降级）
  3. 图片引用解析：并行调 /get-config-item，将 [REF:图片:xxx] 替换为实际文本内联进 prompt
  4. 并行提取源/目标指标（与 extract-batch-fast 复用同一次 LLM one-shot 提取逻辑）
  5. 用已提取的指标 JSON（体量远小于原文）直接调 LLM 比对差异，无需再做字符判断
"""
from __future__ import annotations

import asyncio
import json
import re

import aiomysql
from loguru import logger

from app.langchain.agents.tasks.indicator.fast_extraction import (
    _build_full_compact,
    _run_fast_ind_extraction,
    _strip_raw_img_tags,
)
from app.langchain.agents.tasks.indicator.smart_comparison import (
    SmartCompareItem,
    SmartCompareResult,
    _RE_IMG_REF,
    _RE_IMG_PLAIN,
    _RE_NORM,
    _get_tests_for_compare,
    _fetch_all_chapters,
    _fetch_img_word,
    _llm_call,
    _parse_json_obj,
    enrich_key_indicators,
    fetch_chapters,
    fetch_standard_meta,
    link_tests_to_result,
    run_smart_comparison,
)
from app.services.mysql_pool import standard_pool
from app.settings.config import settings

async def _resolve_img_refs_in_text(text: str, storage_path: str) -> str:
    """将 compact 文本中的 [REF:图片:xxx]/[图片:xxx] 占位符替换为实际图片文本。"""
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

    logger.info(f"[SmartV2] 快路径图片解析: {len(filenames)} 个图片，storage={storage_path!r}")
    words = await asyncio.gather(*[_fetch_img_word(storage_path, fn) for fn in filenames])
    word_map = dict(zip(filenames, words))

    def _replace(m: re.Match) -> str:
        src = m.group(1).strip()
        fn = src.split("/")[-1].split("?")[0] or src
        word = word_map.get(fn, "")
        return word  # 无 OCR 文本则移除占位符，避免文件名流入 LLM 提示

    text = _RE_IMG_REF.sub(_replace, text)
    text = _RE_IMG_PLAIN.sub(_replace, text)
    return text


def _strip_img_refs_from_result(result: SmartCompareResult) -> SmartCompareResult:
    """兜底：剥除比对结果中残留的图片引用占位符及原始 <img> 标签，保留文件名作可读标记（与 v1 一致）。"""
    for item in result.items:
        for attr in ("source_value", "target_value"):
            val = getattr(item, attr) or ""
            if not val:
                continue
            if "[REF:图片:" in val or "[图片:" in val:
                val = _RE_IMG_REF.sub(lambda m: m.group(1), val)
                val = _RE_IMG_PLAIN.sub(lambda m: m.group(1), val)
            if "<img" in val.lower():
                val = _strip_raw_img_tags(val)
            setattr(item, attr, val)
    return result


# ── 阈值（与 extract-batch-fast 共用同一个单标准 compact 上限）────────────────
def _fast_compact_limit() -> int:
    return settings.SMART_V2_FAST_BATCH_COMPACT_LIMIT

# 非技术章节跳过规则 / _fetch_all_chapters / _should_skip / _build_full_compact
# 已收敛至 fast_extraction（顶部 import）


# ── 快路径缓存读取 ────────────────────────────────────────────────────────────

def _build_ind_lookup(inds: list[dict]) -> dict[str, dict]:
    """根据 indicator_object 构建指标查找表，用于回填 indicator_category / object_type。"""
    lookup: dict[str, dict] = {}
    for ind in inds:
        key = (ind.get("indicator_object") or "").strip()
        if key:
            lookup[key] = ind
    return lookup


async def _load_inds_from_cache(standard_no: str) -> list[dict] | None:
    """
    从 standard_cache_ind 读取有效的"静态形态"指标；无有效缓存时返回 None。

    indicator_type 取值有两套体系，字段形状相同（indicator_object + source_value），
    均可直接复用：
      - static       ：老版 /extract-batch（v3）及 AI对比自身写回的缓存
      - inherent     ：新版 /extract-batch-fast 写入，语义上对应固有指标（≈static）
      - experimental ：新版 /extract-batch-fast 写入，语义上对应试验指标；但落库时
                        仍是 indicator_object/source_value 形态，未使用 dynamic 专属
                        字段（experiment_name 等），因此结构上同样兼容
    'dynamic' 类型使用完全不同的字段（experiment_name/source_input_params/...），
    不在此复用范围内，故不纳入过滤条件。

    返回的 dict 结构与 _run_fast_ind_extraction() 输出格式兼容，
    可直接传入 _COMPARE_INDICATORS_PROMPT。
    """
    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT indicator_object, source_value, source_clause,
                           standard_object, applicable_object,
                           object_type, norm_class, indicator_category
                    FROM standard_cache_ind
                    WHERE standard_no = %s
                      AND indicator_type IN ('static', 'inherent', 'experimental')
                      AND is_valid = 1
                    ORDER BY id
                    """,
                    [standard_no],
                )
                rows = list(await cur.fetchall())
        if not rows:
            return None
        # 不同 algorithm_version（如 v3-fast 与 smart_v2 自缓存）可能同时存在有效记录，
        # 按 (indicator_object规范化, standard_object规范化) 去重，避免同一指标重复喂给比对 LLM
        seen: set[tuple[str, str]] = set()
        result: list[dict] = []
        for r in rows:
            indicator_object = r.get("indicator_object") or ""
            if not indicator_object:
                continue
            standard_object = r.get("standard_object") or ""
            key = (
                _RE_NORM.sub("", indicator_object).lower(),
                _RE_NORM.sub("", standard_object).lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "indicator_object": indicator_object,
                "source_value": r.get("source_value") or "",
                "source_clause": r.get("source_clause") or "",
                "standard_object": standard_object,
                "applicable_object": r.get("applicable_object") or "",
                "object_type": r.get("object_type") or "",
                "norm_class": r.get("norm_class") or "",
                "indicator_category": r.get("indicator_category") or "",
            })
        return result or None
    except Exception as e:
        logger.warning(f"[SmartV2] 缓存读取失败({standard_no}): {e}")
        return None


async def _get_indicators_for_compare(
    standard_no: str, standard_name: str, use_range: str, compact_text: str
) -> list[dict]:
    """先查缓存，命中则直接返回；未命中则调用 _run_fast_ind_extraction。"""
    cached = await _load_inds_from_cache(standard_no)
    if cached is not None:
        logger.info(f"[SmartV2] 缓存命中: {standard_no}，{len(cached)} 条指标")
        return cached
    logger.info(f"[SmartV2] 缓存未命中: {standard_no}，调用 LLM 提取")
    return await _run_fast_ind_extraction(standard_no, standard_name, use_range, compact_text)


# ── 两步比对 Prompt ───────────────────────────────────────────────────────────

_COMPARE_INDICATORS_PROMPT = """\
你是资深标准化专家。以下是已从两个标准中提取的技术指标，请逐条对比差异。

## 源标准：{source_no}（{source_name}）
{source_indicators_json}

---

## 目标标准：{target_no}（{target_name}）
{target_indicators_json}

---

**对比规则**：

1. **同义指标（matched）**：两标准中针对**同一性能项目或参数**提出要求即视为同义指标——即使指标名称不同（如"抗拉强度"与"拉伸强度"）或数值不同，只要衡量的是同一件事就判 matched；对有单位指标，单位在物理上概念相同即可。**判断核心：这两个指标是否在衡量同一件事？**
2. **源标准独有指标（source_only）**：目标标准中没有任何对应指标。
3. **目标标准独有指标（target_only）**：源标准中没有任何对应指标。
4. 若指标仅一方具有，**不得**归类为 matched。

**字段填写规则**：
- comparison_type：matched | source_only | target_only
- change_analysis：matched 时简要说明差异，优先使用专业词汇（收严、放宽、一致、条件增加、条件删减、范围扩大、范围缩小、方法替换、要求细化、要求概化等）+ 一句具体说明；source_only / target_only 时留空字符串
- source_indicator_object / source_value / source_clause：matched 和 source_only 时从源标准填；target_only 时全部填 ""
- target_indicator_object / target_value / target_clause：matched 和 target_only 时从目标标准填；source_only 时全部填 ""
- standard_object：matched 时取源标准侧（双方不同则取源标准）；source_only 取源标准，target_only 取目标标准
- norm_class：从对应来源指标直接透传（matched 时使用源标准侧的 norm_class，源侧为空则取目标标准侧）
- **所有字段必须是非空字符串，不得为 null，无内容时填 ""**

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
    }}
  ],
  "relationship": "替代关系",
  "overall_assessment": "综合评价"
}}
"""


# ── 主入口 ────────────────────────────────────────────────────────────────────
async def run_smart_comparison_v2(
    source_standard_no: str,
    target_standard_no: str,
) -> tuple[SmartCompareResult, str]:
    """
    compare-smart-v2 主入口。

    返回 (SmartCompareResult, path_taken)
      path_taken: "fast" | "slow" | "slow_fallback"
        fast          — 快路径 one-shot 成功
        slow          — 源/目标任一 compact 超阈值，走 smart_v1 慢路径
        slow_fallback — 快路径 LLM 返回无效结果，降级走 smart_v1
    """
    logger.info(f"[SmartV2] 开始: {source_standard_no} <-> {target_standard_no}")

    src_meta, tgt_meta = await asyncio.gather(
        fetch_standard_meta(source_standard_no),
        fetch_standard_meta(target_standard_no),
    )

    # ── 拉全文，分别计算源/目标 compact 字符（判断口径与 extract-batch-fast 一致）──
    src_chapters, tgt_chapters = await asyncio.gather(
        _fetch_all_chapters(source_standard_no),
        _fetch_all_chapters(target_standard_no),
    )

    src_text, tgt_text = await asyncio.gather(
        _build_full_compact(source_standard_no, src_chapters),
        _build_full_compact(target_standard_no, tgt_chapters),
    )

    limit = _fast_compact_limit()
    logger.info(
        f"[SmartV2] compact 字符: 源 {len(src_text)}，目标 {len(tgt_text)}，阈值: {limit}"
    )

    if len(src_text) > limit or len(tgt_text) > limit:
        logger.info("[SmartV2] 源或目标 compact 超限，走慢路径")
        slow_result = await run_smart_comparison(source_standard_no, target_standard_no)
        return _strip_img_refs_from_result(slow_result), "slow"

    # ── 快路径：图片引用解析（内联到文本，LLM 可直接读取） ────────────────────
    logger.info("[SmartV2] 快路径图片引用解析")
    src_text, tgt_text = await asyncio.gather(
        _resolve_img_refs_in_text(src_text, src_meta["storage_path"]),
        _resolve_img_refs_in_text(tgt_text, tgt_meta["storage_path"]),
    )

    # ── 快路径 Step A：并行提取双标准指标 + 试验 ──────────────────────────────
    logger.info(
        f"[SmartV2] 快路径: 源文 {len(src_text)} 字符 + 目标文 {len(tgt_text)} 字符"
        f"，开始并行提取"
    )
    try:
        src_inds, tgt_inds, source_tests, target_tests = await asyncio.gather(
            _get_indicators_for_compare(
                source_standard_no, src_meta["name"], src_meta["use_range"], src_text
            ),
            _get_indicators_for_compare(
                target_standard_no, tgt_meta["name"], tgt_meta["use_range"], tgt_text
            ),
            _get_tests_for_compare(source_standard_no, src_meta["name"], src_text),
            _get_tests_for_compare(target_standard_no, tgt_meta["name"], tgt_text),
        )
    except Exception as e:
        logger.warning(f"[SmartV2] 快路径提取失败（{e}），降级慢路径")
        slow_result = await run_smart_comparison(source_standard_no, target_standard_no)
        return _strip_img_refs_from_result(slow_result), "slow_fallback"

    logger.info(
        f"[SmartV2] Step A 完成: 源标准 {len(src_inds)} 条指标, 目标标准 {len(tgt_inds)} 条指标"
    )

    # ── 快路径 Step B：LLM 比对已提取指标 ────────────────────────────────────
    compare_prompt = _COMPARE_INDICATORS_PROMPT.format(
        source_no=source_standard_no,
        source_name=src_meta["name"],
        target_no=target_standard_no,
        target_name=tgt_meta["name"],
        source_indicators_json=json.dumps(src_inds, ensure_ascii=False, indent=2),
        target_indicators_json=json.dumps(tgt_inds, ensure_ascii=False, indent=2),
    )
    try:
        text = await _llm_call(compare_prompt)
    except Exception as e:
        logger.warning(f"[SmartV2] 快路径比对 LLM 调用失败（{e}），降级慢路径")
        slow_result = await run_smart_comparison(source_standard_no, target_standard_no)
        return _strip_img_refs_from_result(slow_result), "slow_fallback"

    raw = _parse_json_obj(text)
    if not raw:
        logger.warning("[SmartV2] 比对返回无效 JSON，降级慢路径")
        slow_result = await run_smart_comparison(source_standard_no, target_standard_no)
        return _strip_img_refs_from_result(slow_result), "slow_fallback"

    items_raw = raw.get("items") or []

    def _sanitize_item(item: dict) -> dict:
        for key in ["source_indicator_object", "source_value", "source_clause",
                    "target_indicator_object", "target_value", "target_clause",
                    "standard_object", "change_analysis", "norm_class"]:
            if item.get(key) is None:
                item[key] = ""
        return item

    try:
        items = [SmartCompareItem(**_sanitize_item(it)) for it in items_raw if isinstance(it, dict)]
    except Exception as e:
        logger.warning(f"[SmartV2] 结果解析失败：{e}，降级慢路径")
        slow_result = await run_smart_comparison(source_standard_no, target_standard_no)
        return _strip_img_refs_from_result(slow_result), "slow_fallback"

    # 从原始提取结果回填 indicator_category 和 object_type（LLM 比对输出不含这两个字段）
    src_lookup = _build_ind_lookup(src_inds)
    tgt_lookup = _build_ind_lookup(tgt_inds)
    for item in items:
        if item.source_indicator_object and item.source_indicator_object in src_lookup:
            item.source_indicator_category = src_lookup[item.source_indicator_object].get("indicator_category") or ""
            item.source_object_type = src_lookup[item.source_indicator_object].get("object_type") or ""
        if item.target_indicator_object and item.target_indicator_object in tgt_lookup:
            item.target_indicator_category = tgt_lookup[item.target_indicator_object].get("indicator_category") or ""
            item.target_object_type = tgt_lookup[item.target_indicator_object].get("object_type") or ""

    result = SmartCompareResult(
        items=items,
        relationship=raw.get("relationship", "部分重叠"),
        overall_assessment=raw.get("overall_assessment", ""),
    )
    result.source_tests = source_tests
    result.target_tests = target_tests
    result = _strip_img_refs_from_result(result)
    # 重点关注指标挑选只依赖 result.items，与指标↔试验关联互不依赖（写不同字段），
    # 并行执行以将挑选调用的耗时完全隐藏在关联阶段内，不增加接口整体时长
    await asyncio.gather(
        link_tests_to_result(result, source_standard_no, target_standard_no),
        enrich_key_indicators(result),
    )
    logger.info(
        f"[SmartV2] 快路径完成: {len(result.items)} 条比对结果, "
        f"源试验 {len(source_tests)} 条, 目标试验 {len(target_tests)} 条"
    )
    return result, "fast"
