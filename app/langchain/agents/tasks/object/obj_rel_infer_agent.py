"""
标准化对象关系推断 Agent（任务级）

  infer_obj_rels(source_obj)
      → 先用拆分 Agent 判断是否需要拆分，若拆出多个对象则先记录它们之间的直接关系
      → 再对每个对象独立运行关系推断 Agent
      → 调用方负责将结果写入 standard_obj_rel

关系类型：
  包含 / 属于   — 整体与部件（互为反向）
  细分 / 归属   — 上位类与下位类（互为反向）
  配套          — 对称关系
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from app.langchain.agents.structured_agent import create_structured_qa_agent, StructuredQAAgent

# 互为反向的关系对，供调用层做双向验证用
REVERSE_MAP = {
    "包含": "属于",
    "属于": "包含",
    "细分": "归属",
    "归属": "细分",
    "配套": "配套",
}


# ── Schema ─────────────────────────────────────────────────────────────────────

class ObjRelItem(BaseModel):
    """单条对象关系"""
    subj_obj: str = Field(description="主体对象名称")
    rel_type: str = Field(description="关系类型：包含 | 属于 | 细分 | 归属 | 配套")
    obj_obj: str = Field(description="客体对象名称")
    rel_desc: str = Field(default="", description="关系自然语言描述（可选）")
    confidence: str = Field(default="high", description="置信度：high | medium | low")
    src_standard_no: str = Field(description="来源标准号，必填，取自 standard_base_info.standard_no")
    src_clause_id: Optional[int] = Field(default=None,
                                         description="来源章节ID，取自 standard_jgh_pdf_chapter.id，无则留空")


class ObjRelOutput(BaseModel):
    """对象关系推断输出"""
    relations: List[ObjRelItem] = Field(
        default_factory=list,
        description="推断出的对象关系列表"
    )


class SplitItem(BaseModel):
    """拆分出的单个对象及其与其他拆分对象的关系"""
    obj_name: str = Field(description="拆分后的对象名称")
    rel_to_others: List[ObjRelItem] = Field(
        default_factory=list,
        description="该对象与同批拆分出的其他对象之间的关系"
    )


class SplitOutput(BaseModel):
    """拆分 Agent 输出"""
    need_split: bool = Field(description="是否需要拆分")
    objects: List[SplitItem] = Field(
        default_factory=list,
        description="拆分后的对象列表，不拆分时只含原对象且 rel_to_others 为空"
    )


# ── 拆分 Agent ─────────────────────────────────────────────────────────────────

_split_agent: Optional[StructuredQAAgent] = None

_SPLIT_SYSTEM_PROMPT = """\
你是一位资深标准化领域专家，任务是判断一个标准化对象名称是否隐含了多个独立对象，并在需要时进行拆分。

## 拆分规则

名称中含"用"、"的"、"式"、"型"、"专用"、"适用于"等连接词，且两部分均为独立的标准化对象时，才需要拆分。

**需要拆分的例子**：
- "电动自行车用电池" → ["电动自行车", "电池"]，关系：电动自行车 包含 电池
- "便携式电子产品用锂离子电池" → ["便携式电子产品", "锂离子电池"]，关系：便携式电子产品 包含 锂离子电池

**不需要拆分的例子**：
- "锂离子蓄电池系统" → 不拆，"系统"是整体名称的一部分
- "插入式耳机" → 不拆，"插入式"只是修饰语
- "头戴耳机" → 不拆

## 注意

- 拆分出的对象之间一定存在关系，必须输出它们之间的关系（通常是包含/归属）
- 拆分后的关系中 src_standard_no 填空字符串（来源为名称本身，无标准号依据）
- 不需要拆分时，need_split=false，objects 中只放原对象，rel_to_others 为空列表
"""


def get_split_agent() -> StructuredQAAgent:
    global _split_agent
    if _split_agent is None:
        logger.info("初始化对象拆分 Agent...")
        _split_agent = create_structured_qa_agent(
            output_schema=SplitOutput,
            system_prompt=_SPLIT_SYSTEM_PROMPT,
        )
    return _split_agent


# ── 关系推断 Agent ─────────────────────────────────────────────────────────────

_rel_agent: Optional[StructuredQAAgent] = None

_INFER_SYSTEM_PROMPT = """\
你是一位资深标准化领域专家，任务是分析一个标准化对象与其他对象之间的语义关系。

## 关系类型定义

只允许使用以下五种关系类型，不得自创：

- **包含**：主体对象在物理上或功能上由客体对象构成（整体 → 部件）
  例：电动自行车 包含 电动机；蓄电池系统 包含 单体电池

- **属于**：主体对象是客体对象的一个部件（部件 → 整体）
  例：电动机 属于 电动自行车

- **细分**：客体对象是主体对象的一种具体类型（上位类 → 下位类）
  例：耳机 细分 头戴耳机；蓄电池系统 细分 锂离子蓄电池系统

- **归属**：主体对象是客体对象的一种具体类型（下位类 → 上位类）
  例：头戴耳机 归属 耳机；锂离子蓄电池系统 归属 蓄电池系统

- **配套**：两个对象协同使用，无主从、无包含关系（对称）
  例：充电器 配套 蓄电池；插入式耳机 配套 耳模拟器

## 对象名称提取原则（所有步骤均适用）

任何时候从标准名称、适用范围或其他文本中提取关联对象时，必须遵守：
- **只输出纯粹的对象名称**，不能包含"用"、"的"、"式"、"型"、"专用"等连接词或修饰语
- 若提取到的文本含有连接词，必须先拆分再取对象：
  "电动自行车用踏板" → 取"电动自行车"和"踏板"，而不是"电动自行车用踏板"
  "便携式电子产品的电池" → 取"便携式电子产品"和"电池"
- 对象名称应是独立存在的标准化实体，不依附于任何具体场景描述

1. **像人类专家一样逐步思考**，不要一次性列举，要有推断过程
2. **宁可少不可错**，没有充分依据的关系不输出
3. **不输出等价关系**，等价由同义词库单独处理
4. **不输出跨领域牵强关系**

## 推断步骤

### 第一步：检索相关标准，从标准名称中发现关联对象
以目标对象为关键词，查询涉及该对象的标准：
```sql
SELECT standard_no, cname, std_obj
FROM standard_base_info
WHERE (cname LIKE '%目标对象%' OR std_obj LIKE '%目标对象%')
  AND deleted = 0
LIMIT 50
```
逐条分析返回的标准名称（cname）：
- 若标准名称形如"X用Y"、"X的Y"，则 X 与 Y 之间存在包含/归属关系
- 若标准名称中出现了其他对象，判断该对象与目标对象的关系类型
- 同一批标准中反复出现的对象，关联置信度更高
- **记录每条关系来自哪个 standard_no**，作为 src_standard_no 输出

若需进一步确定具体章节，可查询章节表获取 chapter id：
```sql
SELECT id, title_no, title
FROM standard_jgh_pdf_chapter
WHERE main_task_id = (SELECT main_task_id FROM standard_jgh_pdf WHERE standard_no = '对应标准号')
  AND (title LIKE '%关键词%' OR word LIKE '%关键词%')
LIMIT 10
```
能定位到具体章节时，将章节 id 作为 src_clause_id 输出；无法定位时留空。

### 第二步：联想扩展并验证
始终以目标对象为中心，思考它基于五种关系还可能涉及哪些对象。
可以多次反复思考，每次从不同角度联想，但始终围绕目标对象本身，不要顺着新发现的对象往下延伸。

对每个联想到的候选对象，去数据库验证其是否真实存在：
```sql
SELECT standard_no, std_obj FROM standard_base_info
WHERE std_obj LIKE '%候选对象%' AND deleted = 0
LIMIT 5
```
- 能查到 → 保留，纳入关系输出，**将查到的 standard_no 作为 src_standard_no**
- 查不到 → 丢弃，不输出

### 第三步：综合输出
对所有发现的关系（来自第一步和第二步）：
- 选择最准确的关系类型（包含、属于、细分、归属、配套）
- 给出置信度（high=有明确标准依据；medium=联想后验证；low=弱关联）
"""


def get_rel_agent() -> StructuredQAAgent:
    global _rel_agent
    if _rel_agent is None:
        logger.info("初始化对象关系推断 Agent...")
        _rel_agent = create_structured_qa_agent(
            output_schema=ObjRelOutput,
            system_prompt=_INFER_SYSTEM_PROMPT,
        )
    return _rel_agent


# ── 入口 ───────────────────────────────────────────────────────────────────────

async def infer_obj_rels(source_obj: str, source_standard_no: str = "") -> ObjRelOutput:
    """
    推断源对象与其他对象之间的语义关系。

    Args:
        source_obj:          源对象名称
        source_standard_no:  该对象来源的标准号，用于填充拆分关系的 src_standard_no

    流程：
      1. 拆分 Agent 判断是否需要拆分，若拆出多个对象则先收集它们之间的直接关系
      2. 对每个对象独立运行关系推断 Agent
      3. 合并所有关系返回

    Returns:
        ObjRelOutput，调用方负责写入 standard_obj_rel
    """
    import asyncio

    # ── Step 1: 拆分 ────────────────────────────────────────────────────────────
    split_agent = get_split_agent()
    split_result: SplitOutput = await split_agent.ainvoke(  # type: ignore[assignment]
        {"messages": [{"role": "user", "content": f"对象名称：{source_obj}"}]},
        config={"configurable": {"thread_id": str(uuid.uuid4())}},
    )

    objs = [item.obj_name for item in split_result.objects if item.obj_name]
    if not objs:
        objs = [source_obj]

    # 拆分对象间的直接关系，src_standard_no 直接用来源标准号填充，并自动补反向关系
    split_rels: List[ObjRelItem] = []
    for item in split_result.objects:
        for rel in item.rel_to_others:
            fwd = rel.model_copy(update={"src_standard_no": source_standard_no})
            split_rels.append(fwd)
            # 自动补反向
            rev_type = REVERSE_MAP.get(rel.rel_type)
            if rev_type:
                split_rels.append(ObjRelItem(
                    subj_obj=rel.obj_obj,
                    rel_type=rev_type,
                    obj_obj=rel.subj_obj,
                    rel_desc=rel.rel_desc,
                    confidence=rel.confidence,
                    src_standard_no=source_standard_no,
                    src_clause_id=rel.src_clause_id,
                ))

    if split_result.need_split:
        logger.info(f"[对象关系] {source_obj} 拆分为 {objs}")

    # ── Step 2: 对每个对象独立推断关系 ─────────────────────────────────────────
    rel_agent = get_rel_agent()

    async def _infer_one(obj: str) -> ObjRelOutput:
        return await rel_agent.ainvoke(  # type: ignore[assignment]
            {"messages": [
                {"role": "user", "content": f"目标对象：{obj}\n\n请推断该对象与其他标准化对象之间的语义关系。"}]},
            config={"configurable": {"thread_id": str(uuid.uuid4())}},
        )

    infer_results: List[ObjRelOutput] = list(await asyncio.gather(*[_infer_one(obj) for obj in objs]))

    # ── Step 3: 合并 ────────────────────────────────────────────────────────────
    all_relations = split_rels[:]
    for r in infer_results:
        all_relations.extend(r.relations)

    logger.info(
        f"[对象关系] {source_obj} 推断完成，共 {len(all_relations)} 条关系（拆分={split_result.need_split}，对象数={len(objs)}）")
    return ObjRelOutput(relations=all_relations)


async def _get_all_std_objects() -> List[str]:
    """
    从 standard_base_info.std_obj 获取所有去重后的标准化对象名称。
    """
    from app.models.standard.base_info import StandardBaseInfo
    rows = await StandardBaseInfo.filter(deleted=False).exclude(std_obj=None).values_list("std_obj", flat=True)
    objs: set[str] = set()
    for raw in rows:
        if raw:
            for part in str(raw).split("、"):
                part = part.strip()
                if part:
                    objs.add(part)
    result = sorted(objs)
    logger.info(f"[对象关系] 从 standard_base_info 获取到 {len(result)} 个去重对象")
    return result


__all__ = [
    "infer_obj_rels",
    "get_rel_agent",
    "get_split_agent",
    "ObjRelOutput",
    "ObjRelItem",
    "SplitOutput",
    "REVERSE_MAP",
]
