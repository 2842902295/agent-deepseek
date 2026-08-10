"""
批量标准查重 Agent（任务级）

架构：主 agent 统筹（查目标标准信息、规划召回维度）
      → 并行委托 dedup-analyzer 子 agent 按各维度 SQL 召回 + 打标评分 + 直接提交
      → 代码层合并去重、阈值筛选、计算 need_attention、写库
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Annotated, Dict, List, Optional, Tuple

from langchain.tools import tool
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from app.langchain.agents.structured_agent import StructuredQAAgent, create_structured_qa_agent

# ── Schema ─────────────────────────────────────────────────────────────────────

class SimilarityTagsOutput(BaseModel):
    同系列标准: bool = Field(False)
    标准化对象一致: bool = Field(False)
    通用专用关系: bool = Field(False)
    适用范围重叠: bool = Field(False)


class SimilarStandardOutput(BaseModel):
    standard_no: Optional[str] = Field(None)
    cname: str = Field(description="标准名称")
    use_range: Optional[str] = Field(None)
    match_score: float = Field(description="召回匹配度（0~1）")
    llm_score: float = Field(description="大模型综合相似度评分（0~1）")
    tags: SimilarityTagsOutput = Field(description="相似度标签")
    need_attention: bool = Field(False)
    relation_desc: str = Field(description="关系说明（50字内）")

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class StandardDeduplicationResult(BaseModel):
    """单个标准的查重分析结果"""
    standard_no: str
    standard_name: Optional[str] = None
    use_range: Optional[str] = None
    found: bool
    need_attention: Optional[bool] = None
    similar_standards: Optional[List[SimilarStandardOutput]] = None
    general_evaluation: Optional[str] = None
    suggestion: Optional[str] = None
    error: Optional[str] = None

    @field_validator("similar_standards", mode="before")
    @classmethod
    def parse_similar_standards(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


class DeduplicationSummary(BaseModel):
    """主 agent 最终输出（轻量摘要，候选已由子 agent 通过 submit_candidates 提交）"""
    found: bool = Field(description="是否在数据库中找到该标准")
    standard_name: str = Field(default="", description="标准名称，找不到时留空")
    use_range: str = Field(default="", description="适用范围，找不到时留空")
    general_evaluation: str = Field(default="", description="总体评价")
    suggestion: str = Field(default="", description="建议措施")
    total_submitted: int = Field(description="已通过 submit_candidates 提交的候选总数（去重前）")


# ── 子 Agent Prompt ────────────────────────────────────────────────────────────

_ANALYZER_PROMPT = """\
你是一位标准查重分析专家，负责按指定维度召回候选标准，并对每条候选完成打标和评分，最后通过工具提交结果。

## 你的任务

主 agent 会告诉你：
- 目标标准的编号、名称、适用范围
- 本次召回的维度和具体查询思路

你需要：
1. 调用 `search_candidate_standards` 按指定维度搜索候选标准
2. 对每条候选进行标签分析和 llm_score 评分
3. 调用 `submit_candidates(candidates_json)` 提交本维度全部结果

## 可用工具

`search_candidate_standards(target_no, keywords, search_field, limit)`
- search_field='cname'：在标准名称中模糊匹配关键词（多个关键词用逗号分隔，取 OR）
- search_field='use_range'：在适用范围中模糊匹配关键词
- 同一维度可多次调用（如同义词各查一次），结果合并去重后统一打标

## 关于同系列标准

同系列标准（同前缀同主号，如 GB/T 11313 与 GB/T 11313.2-2008）由代码规则**已经预先召回并打标**，
**你不需要重复处理**。即便你的 SQL 偶然召回了同系列条目（standard_no 前缀和主号与目标一致），
请直接**跳过、不要提交**，避免重复并避免覆盖规则已定的标签。

## 标签体系（必须极其严格判定）

### 标准化对象一致（最严格）
- ✅ 针对完全相同的具体对象、产品或技术
- ❌ 通用 vs 专用（应标记为"通用专用关系"）
- ❌ 同领域不同对象（如"螺栓" vs "螺母"）

### 通用专用关系
一个是通用标准，另一个是针对特定领域/产品的专用标准。
**互斥规则：标记为"通用专用关系"时，"标准化对象一致"必须为 false**

### 适用范围重叠
同一主体是否会同时采用两个标准（必须是实际常见情况）。

## llm_score 评分

- 1.0 = 高度重复，几乎是同一标准
- 0.8~0.9 = 强相关，存在明显重叠
- 0.6~0.7 = 中等相关，部分交叉
- 0.3~0.5 = 弱相关，仅有边缘关联
- < 0.3 = 几乎无关

**四个标签全部为 false 的候选无需关注，可跳过不提交，也可提交，代码层会自动过滤。**

## submit_candidates 调用规范

打标完成后必须调用 `submit_candidates`，传入 JSON 数组字符串，每条格式：
```json
{
  "standard_no": "...",
  "cname": "...",
  "use_range": "...",
  "match_score": 0.8,
  "llm_score": 0.75,
  "tags": {
    "标准化对象一致": true,
    "通用专用关系": false,
    "适用范围重叠": true
  },
  "relation_desc": "..."
}
```

若本维度无有效候选（全部被过滤），传入空数组 `[]`。
"""

# ── 主 Agent Prompt ────────────────────────────────────────────────────────────

_DEDUPLICATION_SYSTEM_PROMPT = """\
你是一位标准查重专家，负责识别标准之间的重复和交叉关系。

## 工作流程

收到标准编号后：

1. 用 `standard_query` 从 `standard_base_info` 查出该标准的 `cname` 和 `use_range`；
   若查不到则直接输出 `found=false`，`total_submitted=0`，结束。

2. **严格按下面给定的两个维度委托 dedup-analyzer**，每个有意义的维度发起一个 `task(dedup-analyzer, ...)`：
   - 在同一轮思考中**并行**发起，不要串行等待
   - description 中说明：目标标准编号/名称/适用范围，本维度查询思路和关键词
   - 子 agent 会自行 `submit_candidates` 提交，你只需等待返回确认

3. 所有子 agent 完成后，根据各维度返回的提交数量汇总 `total_submitted`，
   撰写 `general_evaluation` 和 `suggestion`，调用 `DeduplicationSummary` 提交。

**重要：候选数据由子 agent 直接通过 submit_candidates 提交，主 agent 无需转发任何候选 JSON。**

## 召回维度规划（只能从以下两个维度）

### 维度一：同对象标准
针对完全相同的产品/材料/技术对象，可能存在多个标准。
提取核心名词，考虑同义词、简称、全称变体。

### 维度二：通用-专用关系
若输入标准含行业限定词（"汽车用"、"建筑用"），查通用版本；
若是通用标准，查各类专用版本。

"""


# ── 提交工具 ───────────────────────────────────────────────────────────────────

def _make_submit_tool(accumulator: List[SimilarStandardOutput]):
    """创建闭包工具，子 agent 每完成一个维度后调用此工具提交候选。"""

    @tool
    def submit_candidates(
            candidates_json: Annotated[
                str, "候选标准 JSON 数组，每条包含 standard_no/cname/use_range/match_score/llm_score/tags/need_attention/relation_desc"],
    ) -> str:
        """提交本维度召回并打标完成的候选标准列表。"""
        try:
            items = json.loads(candidates_json)
            if not isinstance(items, list):
                items = [items]
            parsed = [SimilarStandardOutput(**item) for item in items]
            accumulator.extend(parsed)
            return f"已提交 {len(parsed)} 条候选，累计 {len(accumulator)} 条"
        except Exception as e:
            return f"提交失败：{e}。请检查 JSON 格式后重试。"

    return submit_candidates


# ── 子 Agent 定义 ──────────────────────────────────────────────────────────────

def _make_subagents(submit_tool=None, pool_id: Optional[int] = None) -> List:
    from app.langchain.agents.structured_agent import make_subagent_runnable
    from app.langchain.tools.db_tools import make_dedup_tools

    dedup_tools = make_dedup_tools(pool_id)
    analyzer_tools = dedup_tools + ([submit_tool] if submit_tool else [])

    return [
        {
            "name": "dedup-analyzer",
            "description": "按指定维度执行 SQL 召回候选标准，对每条候选打标评分，调用 submit_candidates 提交结果。",
            "runnable": make_subagent_runnable(
                name="dedup-analyzer",
                system_prompt=_ANALYZER_PROMPT,
                tools=analyzer_tools,
            ),
        }
    ]


# ── 同系列规则判定 ────────────────────────────────────────────────────────────

# 标准号解析：捕获前缀（GB/T、JT/T、YY 等）、主号、可选子号、可选年份
_STD_NO_RE = re.compile(
    r"^\s*"
    r"([A-Za-z]+(?:/[A-Za-z]+)?)"     # 1: 前缀
    r"\s*"
    r"(\d+)"                           # 2: 主号
    r"(?:\.(\d+(?:\.\d+)*))?"          # 3: 子号（支持多级 .1.2）
    r"(?:\s*[-—]\s*(\d{4}))?"          # 4: 年份（可选）
    r"\s*$"
)


def _parse_standard_no(no: Optional[str]) -> Optional[tuple]:
    """解析标准号 → (prefix, main_no, sub_no)，失败返回 None。"""
    if not no:
        return None
    m = _STD_NO_RE.match(no.strip().upper())
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def _is_same_series(target_no: str, candidate_no: str) -> bool:
    """
    判定两个标准号是否属于同系列：
    - 前缀完全相同（GB/T == GB/T，但 GB ≠ GB/T，强制性与推荐性不混）
    - 主号相同（11313 == 11313）
    - 子号、年份不参与（GB/T 11313 与 GB/T 11313.2-2008 视为同系列）
    - 同一编号本身不算（self-match 不视为同系列候选）
    """
    if not target_no or not candidate_no:
        return False
    if target_no.strip().upper() == candidate_no.strip().upper():
        return False
    t = _parse_standard_no(target_no)
    c = _parse_standard_no(candidate_no)
    if not t or not c:
        return False
    return t[0] == c[0] and t[1] == c[1]


async def _recall_same_series_candidates(
        target_no: str,
        pool_id: Optional[int] = None,
) -> List[SimilarStandardOutput]:
    """
    纯代码召回同系列标准（不经过 LLM）。

    解析目标标准号 → 按 prefix + main_no 走 SQL 精确召回 →
    用 _is_same_series 二次校验 → 直接生成 SimilarStandardOutput。

    同系列标签固定为 True，其他三个标签留给主 agent 后续维度叠加判断。
    """
    import aiomysql
    from app.langchain.tools.db_tools import _get_allowed_standard_nos
    from app.services.mysql_pool import standard_pool

    parsed = _parse_standard_no(target_no)
    if not parsed:
        return []
    prefix, main_no, _ = parsed

    allowed_nos: Optional[list] = None
    if pool_id is not None and pool_id != -1:
        allowed_nos = await _get_allowed_standard_nos(pool_id)

    # 按 "前缀 + 空格? + 主号" 模糊召回，覆盖 "GB/T 11313"、"GB/T 11313.2-2008"
    like_pattern = f"{prefix} {main_no}%"
    like_pattern_no_space = f"{prefix}{main_no}%"
    sql = (
        "SELECT standard_no, cname, use_range "
        "FROM standard_base_info "
        "WHERE (UPPER(standard_no) LIKE %s OR UPPER(standard_no) LIKE %s) "
        "AND UPPER(standard_no) != %s"
    )
    params: list = [like_pattern, like_pattern_no_space, target_no.strip().upper()]

    if allowed_nos:
        quoted = ",".join(f"'{no.replace(chr(39), chr(39) * 2)}'" for no in allowed_nos)
        sql += f" AND standard_no IN ({quoted})"

    sql += " LIMIT 200"

    try:
        async with standard_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = list(await cur.fetchall())
    except Exception as e:
        logger.error(f"[Dedup] 同系列召回失败 target={target_no}: {e}")
        return []

    candidates: List[SimilarStandardOutput] = []
    for row in rows:
        cand_no = row.get("standard_no")
        if not _is_same_series(target_no, cand_no or ""):
            continue
        candidates.append(SimilarStandardOutput(
            standard_no=cand_no,
            cname=row.get("cname") or "",
            use_range=row.get("use_range"),
            match_score=1.0,
            llm_score=0.0,
            tags=SimilarityTagsOutput(同系列标准=True),
            need_attention=False,
            relation_desc="同系列标准（规则匹配）",
        ))

    logger.info(f"[Dedup] 同系列规则召回 target={target_no} → {len(candidates)} 条")
    return candidates


def _correct_same_series_tag(
        target_no: str,
        candidates: List[SimilarStandardOutput],
) -> List[SimilarStandardOutput]:
    """
    用代码规则强制覆盖 LLM 的"同系列标准"标签判定。
    LLM 偶有误判（如把 GB 11313 与 GB/T 11313 判为同系列），规则更可靠。
    """
    for c in candidates:
        if not c.standard_no:
            c.tags.同系列标准 = False
            continue
        rule_val = _is_same_series(target_no, c.standard_no)
        if c.tags.同系列标准 != rule_val:
            logger.debug(
                f"[Dedup] 同系列标签纠正：target={target_no} candidate={c.standard_no} "
                f"LLM={c.tags.同系列标准} → rule={rule_val}"
            )
        c.tags.同系列标准 = rule_val
    return candidates


# ── 候选去重 & 阈值筛选 ────────────────────────────────────────────────────────

def _dedup_candidates(candidates: List[SimilarStandardOutput]) -> List[SimilarStandardOutput]:
    """按 standard_no 去重，保留 llm_score 最高的那条。"""
    best: Dict[str, SimilarStandardOutput] = {}
    no_no: List[SimilarStandardOutput] = []
    for c in candidates:
        key = (c.standard_no or "").strip()
        if not key:
            no_no.append(c)
            continue
        if key not in best or c.llm_score > best[key].llm_score:
            best[key] = c
    return list(best.values()) + no_no


def _filter_candidates(
        candidates: List[SimilarStandardOutput],
        threshold: float = 0.4,
        min_keep: int = 3,
) -> List[SimilarStandardOutput]:
    """
    过滤低分候选：
    - 同系列标准（规则召回）一律保留，不受 threshold 限制
    - 其余候选保留 llm_score >= threshold 的
    - 若非同系列保留数 < min_keep，从剩余非同系列中按分数补足
    最终输出：同系列在前，其余按 llm_score 降序。
    """
    series = [c for c in candidates if c.tags.同系列标准]
    others = [c for c in candidates if not c.tags.同系列标准]

    above = [c for c in others if c.llm_score >= threshold]
    if len(above) >= min_keep:
        kept_others = sorted(above, key=lambda x: x.llm_score, reverse=True)
    else:
        sorted_others = sorted(others, key=lambda x: x.llm_score, reverse=True)
        kept_others = sorted_others[:max(min_keep, len(above))]

    return series + kept_others


def _filter_all_false_tags(candidates: List[SimilarStandardOutput]) -> List[SimilarStandardOutput]:
    """过滤四个标签全为 false 的候选。"""

    def _has_any_tag(c: SimilarStandardOutput) -> bool:
        t = c.tags
        return t.同系列标准 or t.标准化对象一致 or t.通用专用关系 or t.适用范围重叠

    return [c for c in candidates if _has_any_tag(c)]


def _recalc_need_attention(candidates: List[SimilarStandardOutput]) -> List[SimilarStandardOutput]:
    """代码层重新计算 need_attention，不依赖 agent 判断。"""
    for c in candidates:
        t = c.tags
        c.need_attention = (
                t.标准化对象一致
                and t.适用范围重叠
                and not t.同系列标准
                and not t.通用专用关系
                and c.llm_score >= 0.7
        )
    return candidates


# ── Agent 工厂 ─────────────────────────────────────────────────────────────────

def _create_agent(submit_tool=None, pool_id: Optional[int] = None) -> StructuredQAAgent:
    """每次调用创建新的主 agent（submit_tool 是闭包，不能复用单例）。"""
    return create_structured_qa_agent(
        output_schema=DeduplicationSummary,
        system_prompt=_DEDUPLICATION_SYSTEM_PROMPT,
        subagents=_make_subagents(submit_tool=submit_tool, pool_id=pool_id),
        pool_id=None,  # 主 agent 只查目标标准自身信息，不受 pool 范围限制
    )


# ── 兜底维度（适用范围交叉） ─────────────────────────────────────────────────

_USE_RANGE_FALLBACK_PROMPT = """\
你是一位标准查重分析专家，本次仅负责执行【适用范围交叉】兜底召回。

主 agent 在同对象 / 通用专用维度均未召回任何相关候选，现由你从适用范围切入兜底。

## 你的任务

1. 从目标标准的适用范围（use_range）中提取 1~3 个核心应用场景词（如行业、产品类别、使用环境）；
2. 调用 `search_candidate_standards(target_no=..., keywords=..., search_field='use_range')` 召回候选；
   - 关键词太宽泛会导致命中过多，请尽量挑选有区分度的词
   - 同义词可多次调用合并
3. 对每条候选完成标签和 llm_score 评分；
4. 调用 `submit_candidates(candidates_json)` 提交。无有效候选传 `[]`。

## 关于同系列标准

同系列标准（同前缀同主号，如 GB/T 11313 与 GB/T 11313.2-2008）由代码规则**已经预先召回并打标**，
**你不需要重复处理**。即便你的 SQL 偶然召回了同系列条目，请直接**跳过、不要提交**。

## 标签体系（必须严格判定）

### 标准化对象一致（最严格）
- ✅ 针对完全相同的具体对象、产品或技术
- ❌ 通用 vs 专用（应标记为"通用专用关系"）
- ❌ 同领域不同对象

### 通用专用关系
一个是通用标准，另一个是针对特定领域/产品的专用标准。
**互斥规则：标记为"通用专用关系"时，"标准化对象一致"必须为 false**

### 适用范围重叠
同一主体是否会同时采用两个标准（必须是实际常见情况）。

## llm_score 评分

- 1.0 = 高度重复，几乎是同一标准
- 0.8~0.9 = 强相关，存在明显重叠
- 0.6~0.7 = 中等相关，部分交叉
- 0.3~0.5 = 弱相关，仅有边缘关联
- < 0.3 = 几乎无关

四个标签全部为 false 的候选可不提交，代码层会自动过滤。
"""


async def _run_use_range_fallback(
        standard_no: str,
        standard_name: str,
        use_range: str,
        accumulator: List[SimilarStandardOutput],
        pool_id: Optional[int],
) -> None:
    """
    主 agent 完全无召回时的兜底维度：单独建一个分析 agent，按 use_range 召回。
    无需主 agent，直接调 dedup-analyzer 风格的 runnable。
    """
    from app.langchain.agents.structured_agent import make_subagent_runnable
    from app.langchain.tools.db_tools import make_dedup_tools

    if not (use_range and use_range.strip()):
        logger.info(f"[Dedup] {standard_no} 兜底跳过：use_range 为空")
        return

    submit_tool = _make_submit_tool(accumulator)
    tools = make_dedup_tools(pool_id) + [submit_tool]

    runnable = make_subagent_runnable(
        name="dedup-fallback",
        system_prompt=_USE_RANGE_FALLBACK_PROMPT,
        tools=tools,
    )

    user_msg = (
        f"目标标准编号：{standard_no}\n"
        f"标准名称：{standard_name}\n"
        f"适用范围：{use_range}\n\n"
        f"请按【适用范围交叉】维度兜底召回并提交结果。"
    )

    pre_len = len(accumulator)
    try:
        await runnable.ainvoke({"messages": [{"role": "user", "content": user_msg}]})
        logger.info(
            f"[Dedup] {standard_no} 兜底维度完成，新增 {len(accumulator) - pre_len} 条"
        )
    except Exception as e:
        logger.error(f"[Dedup] {standard_no} 兜底维度执行失败: {e}")



# ── 单标准分析 ─────────────────────────────────────────────────────────────────

_MAX_RETRY = 10


async def _analyze_single_standard(
        standard_no: str,
        semaphore: asyncio.Semaphore,
        pool_id=None,
) -> Dict:
    async with semaphore:
        for attempt in range(1, _MAX_RETRY + 1):
            accumulated: List[SimilarStandardOutput] = []
            # 维度三：同系列标准——纯代码规则召回，不经过 LLM
            same_series = await _recall_same_series_candidates(standard_no, pool_id=pool_id)
            accumulated.extend(same_series)
            pre_agent_len = len(accumulated)
            submit_tool = _make_submit_tool(accumulated)

            try:
                agent = _create_agent(submit_tool=submit_tool, pool_id=pool_id)
                summary: DeduplicationSummary = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": f"请对标准编号 {standard_no} 进行查重分析。"}]},
                    config={"configurable": {"thread_id": f"dedup-{standard_no}-{uuid.uuid4().hex[:8]}"}},
                )

                if not summary.found:
                    return {
                        "standard_no": standard_no,
                        "standard_name": None,
                        "use_range": None,
                        "found": False,
                        "need_attention": False,
                        "similar_standards": None,
                        "general_evaluation": None,
                        "suggestion": None,
                        "error": None,
                    }

                # 兜底：主 agent 跑完同对象/通用专用两维度均未召回任何候选时，
                # 触发 use_range 维度独立 agent
                if len(accumulated) == pre_agent_len:
                    logger.info(
                        f"[Dedup] {standard_no} 主 agent 无召回，触发 use_range 兜底"
                    )
                    await _run_use_range_fallback(
                        standard_no=standard_no,
                        standard_name=summary.standard_name or "",
                        use_range=summary.use_range or "",
                        accumulator=accumulated,
                        pool_id=pool_id,
                    )

                # 去重 + 同系列规则纠正 + 阈值筛选 + 重算 need_attention
                candidates = _dedup_candidates(accumulated)
                candidates = _correct_same_series_tag(standard_no, candidates)
                candidates = _filter_all_false_tags(candidates)
                candidates = _filter_candidates(candidates, threshold=0.6, min_keep=3)
                candidates = _recalc_need_attention(candidates)

                need_attention = any(c.need_attention for c in candidates)

                logger.info(
                    f"[Dedup] {standard_no} 完成，"
                    f"累计 {len(accumulated)} 条 → 筛选后 {len(candidates)} 条，"
                    f"need_attention={need_attention}"
                )

                return {
                    "standard_no": standard_no,
                    "standard_name": summary.standard_name or None,
                    "use_range": summary.use_range or None,
                    "found": True,
                    "need_attention": need_attention,
                    "similar_standards": [c.model_dump() for c in candidates],
                    "general_evaluation": summary.general_evaluation or None,
                    "suggestion": summary.suggestion or None,
                    "error": None,
                }

            except Exception as e:
                logger.error(f"标准 {standard_no} 第 {attempt}/{_MAX_RETRY} 次查重失败: {e}")

        return {
            "standard_no": standard_no,
            "standard_name": None,
            "use_range": None,
            "found": False,
            "need_attention": False,
            "similar_standards": None,
            "general_evaluation": None,
            "suggestion": None,
            "error": f"分析失败：重试 {_MAX_RETRY} 次后仍无法获得有效结果",
        }


# ── 单条任务执行（含落库） ────────────────────────────────────────────────────

async def _execute_one_task(
        name_id: int,
        standard_no: str,
        batch_id: int,
        semaphore: asyncio.Semaphore,
        pool_id: Optional[int],
) -> Dict:
    """
    完整跑完一条 name 任务并把结果落到该条 name 记录。
    出错也会捕获并写回 task_status='failed'。
    返回最终的结果 dict（供调用方汇总用）。
    """
    from app.models.standard import StandardDuplicateName

    # 标记 running
    try:
        await StandardDuplicateName.filter(id=name_id).update(task_status="running")
    except Exception as e:
        logger.warning(f"[BatchDedup] 标记 running 失败 name_id={name_id}: {e}")

    try:
        result = await _analyze_single_standard(standard_no, semaphore, pool_id=pool_id)
    except Exception as e:
        logger.error(f"[BatchDedup] 标准 {standard_no} 执行异常: {e}")
        result = {
            "standard_no": standard_no,
            "standard_name": None,
            "use_range": None,
            "found": False,
            "need_attention": False,
            "similar_standards": None,
            "general_evaluation": None,
            "suggestion": None,
            "error": f"执行异常: {e}",
        }

    # 写回该 name 记录
    final_status = "failed" if result.get("error") else "done"
    try:
        await StandardDuplicateName.filter(id=name_id).update(
            standard_name=result.get("standard_name"),
            use_range=result.get("use_range"),
            found=result.get("found", False),
            error=result.get("error"),
            need_attention=result.get("need_attention", False),
            general_evaluation=result.get("general_evaluation"),
            suggestion=result.get("suggestion"),
            similar_standards=result.get("similar_standards"),
            task_status=final_status,
        )
    except Exception as e:
        logger.error(f"[BatchDedup] 写回 name_id={name_id} 失败: {e}")

    return result


# ── 汇总更新 batch ─────────────────────────────────────────────────────────────

async def _finalize_batch(batch_id: int) -> None:
    """根据 name 表实际状态重算并更新 batch 汇总、状态。"""
    from app.models.standard import StandardDuplicateBatch, StandardDuplicateName

    items = await StandardDuplicateName.filter(batch_id=batch_id).all()
    total = len(items)
    success = sum(1 for x in items if x.task_status == "done" and x.found)
    failed = sum(1 for x in items if x.task_status == "failed" or (x.task_status == "done" and not x.found))
    duplicate = sum(1 for x in items if x.need_attention)
    pending_or_running = sum(1 for x in items if x.task_status in ("pending", "running"))

    status = "processing" if pending_or_running > 0 else "completed"

    try:
        await StandardDuplicateBatch.filter(id=batch_id).update(
            total_count=total,
            success_count=success,
            failed_count=failed,
            duplicate_count=duplicate,
            status=status,
        )
        logger.info(
            f"[BatchDedup] batch {batch_id} 汇总更新：total={total} success={success} "
            f"failed={failed} attention={duplicate} status={status}"
        )
    except Exception as e:
        logger.error(f"[BatchDedup] 更新 batch {batch_id} 汇总失败: {e}")


# ── 批量分析入口 ───────────────────────────────────────────────────────────────

async def run_structured_batch_deduplication(
        standard_nos: List[str],
        pool_id: Optional[int],
        batch_name: Optional[str],
        check_existing: bool = False,
) -> Tuple[Optional[str], Optional[Dict]]:
    """
    使用主 agent + dedup-analyzer 子 agent 执行批量标准查重。

    增量持久化：进入即创建 batch 和占位 name 记录，每完成一条立即落库；
    服务中途重启不会丢已完成的结果，启动钩子可挑出残留 pending/running 续跑。

    Returns:
        (error_msg, result_data) — error_msg 不为 None 时表示出错
    """
    from app.langchain.config import langchain_config
    from app.models.standard import StandardDuplicateBatch, StandardDuplicateName

    if check_existing and len(standard_nos) == 1:
        existing = await StandardDuplicateName.filter(
            standard_no=standard_nos[0],
            batch__pool_id=pool_id,
            found=True
        ).first()
        if existing:
            logger.info(f"[BatchDedup] 标准 {standard_nos[0]} 在 pool_id={pool_id} 已有记录，直接返回")
            return None, {"exists": True}

    logger.info(f"[BatchDedup] 开始批量查重，共 {len(standard_nos)} 个标准")

    # 1. 进入即建 batch（status=processing），并预创建 name 占位（task_status=pending）
    batch_record = await StandardDuplicateBatch.create(
        batch_name=batch_name,
        pool_id=pool_id,
        total_count=len(standard_nos),
        success_count=0,
        failed_count=0,
        duplicate_count=0,
        status="processing",
    )
    batch_id = batch_record.id

    name_records = await StandardDuplicateName.bulk_create([
        StandardDuplicateName(
            batch_id=batch_id,
            standard_no=sno,
            found=False,
            need_attention=False,
            task_status="pending",
        )
        for sno in standard_nos
    ])
    # bulk_create 不一定回填 id，重查一次保证有 id
    name_records = await StandardDuplicateName.filter(batch_id=batch_id).order_by("id").all()

    # 2. 并发执行各条任务，每条完成即落库
    semaphore = asyncio.Semaphore(langchain_config.LLM_MAX_CONCURRENT)
    tasks = [
        _execute_one_task(
            name_id=rec.id,
            standard_no=rec.standard_no,
            batch_id=batch_id,
            semaphore=semaphore,
            pool_id=pool_id,
        )
        for rec in name_records
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=False)

    # 3. 汇总刷新 batch
    await _finalize_batch(batch_id)

    processed = sum(1 for r in raw_results if r.get("found"))
    return None, {
        "batch_id": batch_id,
        "total": len(standard_nos),
        "processed": processed,
        "results": raw_results,
    }


# ── 续跑入口 ──────────────────────────────────────────────────────────────────

async def resume_pending_batch(batch_id: int) -> Tuple[Optional[str], Optional[Dict]]:
    """
    重新调度某个 processing 批次内剩余 pending/failed 的标准。

    注意：不会处理 task_status='running' 的记录（避免与正在跑的任务冲突）。
    服务重启后，由启动钩子先把僵尸 running 改回 pending 再调用此函数。
    """
    from app.langchain.config import langchain_config
    from app.models.standard import StandardDuplicateBatch, StandardDuplicateName

    batch = await StandardDuplicateBatch.filter(id=batch_id).first()
    if not batch:
        return f"批次 {batch_id} 不存在", None

    pending_items = await StandardDuplicateName.filter(
        batch_id=batch_id,
        task_status__in=["pending", "failed"],
    ).order_by("id").all()

    if not pending_items:
        # 没残留就把状态置为 completed
        await _finalize_batch(batch_id)
        return None, {"batch_id": batch_id, "resumed": 0}

    logger.info(f"[BatchDedup] 续跑批次 {batch_id}，剩余 {len(pending_items)} 条")

    semaphore = asyncio.Semaphore(langchain_config.LLM_MAX_CONCURRENT)
    tasks = [
        _execute_one_task(
            name_id=rec.id,
            standard_no=rec.standard_no,
            batch_id=batch_id,
            semaphore=semaphore,
            pool_id=batch.pool_id,
        )
        for rec in pending_items
    ]
    await asyncio.gather(*tasks, return_exceptions=False)

    await _finalize_batch(batch_id)
    return None, {"batch_id": batch_id, "resumed": len(pending_items)}


__all__ = ["run_structured_batch_deduplication", "resume_pending_batch", "resume_batch_dispatch"]


# ── 统一调度入口（按 batch.remark 路由 deep / fast） ──────────────────────────

async def resume_batch_dispatch(batch_id: int) -> Tuple[Optional[str], Optional[Dict]]:
    """
    根据 batch.mode 自动选择 deep 或 fast 版本进行续跑：
      - mode == 'fast' → 走 standard_deduplication_embedding_agent.resume_pending_batch
      - 其他           → 走当前模块的 resume_pending_batch（deep）
    """
    from app.models.standard import StandardDuplicateBatch

    batch = await StandardDuplicateBatch.filter(id=batch_id).first()
    if not batch:
        return f"批次 {batch_id} 不存在", None

    if (batch.mode or "deep") == "fast":
        from app.langchain.agents.tasks.standard.standard_deduplication_embedding_agent import (
            resume_pending_batch as _fast_resume,
        )
        return await _fast_resume(batch_id)
    return await resume_pending_batch(batch_id)
