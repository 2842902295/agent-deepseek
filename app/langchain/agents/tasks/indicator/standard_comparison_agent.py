"""
标准指标比对 Agent（任务级）

  compare_standards(source_standard_no, target_standard_no, ...)
      → Agent 从 standard_cache_ind 读取两份指标列表，纯做比对
      → 调用方负责将结果写入 standard_cache_ai

指标提取请使用 indicator_extraction_agent.extract_indicators
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from app.langchain.agents.structured_agent import create_structured_qa_agent, StructuredQAAgent


# 数据库路径不再需要，改用 db_tools 内的 MySQL 连接配置


# ── Schema ─────────────────────────────────────────────────────────────────────

class ComparisonItem(BaseModel):
    """单条指标比对结果，直接对应 standard_cache_ai 的一行"""
    source_ind_id: Optional[int] = Field(default=None, description="源标准指标ID（matched/source_only 时填写）")
    target_ind_id: Optional[int] = Field(default=None, description="目标标准指标ID（matched/target_only 时填写）")
    comparison_type: str = Field(description='"matched" | "source_only" | "target_only"')
    change_analysis: str = Field(
        default="",
        description=(
            "matched时必填，格式为「词汇：一句话说明原因」，词汇只能从以下选项中选（可用顿号组合多个），不得造词："
            "一致、收严、放宽、条件增加、条件删减、范围扩大、范围缩小、方法替换、要求细化、要求概化。"
            "示例：「收严：目标标准将允许偏差从±5%收紧至±3%」「条件增加、要求细化：新增了适用温度范围并明确了测试频次」。"
            "词义：一致=实质相同仅表述有差异；收严=要求更严格；放宽=要求更宽松；"
            "条件增加=新增适用条件或例外；条件删减=删除某些条件；"
            "范围扩大/缩小=适用对象或场景变化；方法替换=测试或实施方法改变；"
            "要求细化=原笼统要求变具体；要求概化=原具体要求变原则性。"
            "若两标准仅文字表述不同但含义实质相同，填「一致：两者表述不同但要求实质相同」。"
        ),
    )


class StandardComparisonOutput(BaseModel):
    """双标准比对输出"""
    items: List[ComparisonItem] = Field(default_factory=list)
    relationship: str = Field(description='替代关系/互补关系/平行关系/部分重叠')
    overall_assessment: str = Field(description="综合评价，2~4句话")


# ── 比对 Agent ──────────────────────────────────────────────────────────────────

_comparison_agent: Optional[StructuredQAAgent] = None

_COMPARISON_SYSTEM_PROMPT = """\
你是一位资深标准化专家，任务是对比两篇标准的技术指标差异。

## 工作流程

### 第一步：获取指标

同时调用以下两个工具，获取两篇标准的全部指标（含 id）：
- get_cached_indicators(standard_no=源标准编号)
- get_cached_indicators(standard_no=目标标准编号)

### 第二步：分组

综合 indicator_category、standard_object、applicable_object 对两边指标进行分组，分组方案由你自主判断。
目标是让语义相关的指标落入同一组，便于后续组内匹配。

### 第三步：组内匹配

对每个分组，取源标准和目标标准中属于该组的指标，在组内做一对一匹配。

综合指标名称（indicator_object / experiment_name）与内容字段（source_value / source_result / source_input_params 等）整体判断两条指标是否描述同一技术要求：
- 语义近似视为同一指标（如"拉伸强度"="抗拉强度"，"耐压试验"="绝缘耐压试验"）
- 跨类型（static↔dynamic）同样适用，以名称与内容综合判断
- 名称相近但内容指向明显不同技术要求的，不应强行匹配
- 不允许跨组匹配；若某条指标在对方标准的同组内找不到对应，归为独有指标

### 第四步：处理独有指标

两边各组匹配完成后，未被匹配的指标全部输出为 source_only 或 target_only。

### 第五步：输出结果

汇总所有匹配结果和独有指标，填写 change_analysis，提交最终输出。

## 一对一原则

每条源指标最多匹配一条目标指标，反之亦然。同组内有多个候选时，选语义最接近的一条，其余归为独有指标。

comparison_type：matched=两标准均有 | source_only=仅源标准有 | target_only=仅目标标准有

## 输出规则

每条比对结果只填写 id，不重复填写指标内容：
- matched：source_ind_id=源指标id，target_ind_id=目标指标id，填写 change_analysis
- source_only：source_ind_id=源指标id，target_ind_id=null
- target_only：source_ind_id=null，target_ind_id=目标指标id

change_analysis（matched 时必填）：
- 格式：「词汇：一句话说明原因」，词汇可用顿号组合多个，不得造词、不得省略说明
- 可用词汇：
  · 一致：两者实质相同，仅文字表述有差异
  · 收严：要求更严格（如从"宜"改为"应"，或增加了限制条件）
  · 放宽：要求更宽松
  · 条件增加：新增了适用条件、前提或例外
  · 条件删减：删除了某些条件或限制
  · 范围扩大：适用对象或场景更广
  · 范围缩小：适用对象或场景更窄
  · 方法替换：测试、验证或实施方法改变
  · 要求细化：原来笼统的要求变得更具体
  · 要求概化：原来具体的要求变得更原则性或笼统
- 示例：「收严：目标标准将允许偏差从±5%收紧至±3%」
- 示例：「条件增加、要求细化：新增了适用温度范围并明确了测试频次」
- 含义实质相同（仅文字表述不同）→ 填「一致：两者表述不同但要求实质相同」
- 跨类型匹配（一方 static、一方 dynamic）→ 从上述词汇中选最能体现差异的，并说明原因
"""


def get_comparison_agent() -> StructuredQAAgent:
    global _comparison_agent
    if _comparison_agent is None:
        from app.langchain.tools.db_tools import make_cache_ind_tool
        logger.info("初始化指标比对 Agent...")
        _comparison_agent = create_structured_qa_agent(
            output_schema=StandardComparisonOutput,
            system_prompt=_COMPARISON_SYSTEM_PROMPT,
            extra_tools=[make_cache_ind_tool()],
        )
    return _comparison_agent


async def compare_standards(
        source_standard_no: str,
        target_standard_no: str,
        source_name: str = "",
        target_name: str = "",
) -> StandardComparisonOutput:
    """
    Agent 自主从 standard_cache_ind 获取两篇标准的指标进行比对。
    返回结果直接包含 source_ind_id / target_ind_id，可直接写入 standard_cache_ai。
    """
    agent = get_comparison_agent()
    user_message = (
            f"请对比以下两个标准的技术指标差异：\n"
            f"源标准：{source_standard_no}" + (f"（{source_name}）" if source_name else "") + "\n"
                                                                                           f"目标标准：{target_standard_no}" + (
                f"（{target_name}）" if target_name else "")
    )

    result: StandardComparisonOutput = await agent.ainvoke(
        {"messages": [{"role": "user", "content": user_message}]},
        config={"configurable": {"thread_id": str(uuid.uuid4())}},
    )

    matched = sum(1 for i in result.items if i.comparison_type == "matched")
    logger.info(
        f"[比对] {source_standard_no} <-> {target_standard_no} 完成，"
        f"共 {len(result.items)} 条，匹配 {matched} 条"
    )
    return result


__all__ = [
    "compare_standards",
    "get_comparison_agent",
    "StandardComparisonOutput",
    "ComparisonItem",
]
