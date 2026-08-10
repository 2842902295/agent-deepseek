"""
标准指标提取 Agent（任务级）

  extract_indicators(standard_no, standard_name?)
      → Agent 阅读标准章节，提取结构化指标清单 + 试验清单
      → 调用方负责将结果写入 standard_cache_ind / standard_cache_test
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Annotated, List, Optional, Set, Tuple, cast

from langchain.tools import tool
from loguru import logger
from pydantic import BaseModel, Field

from app.langchain.agents.mind_map import TAXONOMY
from app.langchain.agents.structured_agent import create_structured_qa_agent, StructuredQAAgent

# ── 标准性质类型 ────────────────────────────────────────────────────────────────

_STANDARD_TYPES: frozenset[str] = frozenset([
    "has_ind_and_test",  # 有指标有试验，两者可分离
    "has_ind_only",  # 只有指标，无试验方法章节
    "has_test_only",  # 只有试验规程，无判定准则
    "ind_embedded_in_test",  # 指标和试验混写，无法分离
])



# ── 分类体系（来自 mind_map）──────────────────────────────────────────────────────

# 包含 TAXONOMY 中已有类型 + _OBJECT_TYPE_HINTS 中预留类型（模块加载后由 _build_object_type_prompt 填充）
_OBJECT_TYPE_OPTIONS: frozenset[str] = frozenset(TAXONOMY.keys())

_CATEGORY_OPTIONS: frozenset[str] = frozenset(
    e
    for groups in TAXONOMY.values()
    for elements in groups.values()
    for e in elements
    if e not in ("其他", "子主题")
)

_NORM_CLASS_OPTIONS: frozenset[str] = frozenset(
    group_name
    for groups in TAXONOMY.values()
    for group_name in groups.keys()
)


def _norm_class_field_desc() -> str:
    parts = ["要素组分类，根据标准化对象类型从对应选项中选一个："]
    for obj_type, groups in TAXONOMY.items():
        parts.append(f"【{obj_type}】{'、'.join(groups.keys())}")
    return "\n".join(parts)


_NORM_CLASS_FIELD_DESC = _norm_class_field_desc()


def _normalize_object_type(raw: str) -> str:
    """将 LLM 返回的 object_type 规范化为标准值，容忍常见变体。"""
    if not raw:
        return ""
    if raw in _OBJECT_TYPE_OPTIONS:
        return raw
    # 子串匹配：LLM 可能返回 "产品类" 或 "产品类对象（最常见）" 等
    for opt in _OBJECT_TYPE_OPTIONS:
        if opt in raw or raw in opt:
            return opt
    return ""


def _category_field_desc() -> str:
    parts = ["指标分类，根据标准化对象类型从对应选项中选一个最合适的："]
    for obj_type, groups in TAXONOMY.items():
        valid = [e for g in groups.values() for e in g if e not in ("其他", "子主题")]
        if valid:
            parts.append(f"【{obj_type}】{'、'.join(valid)}")
    return "\n".join(parts)


_CATEGORY_FIELD_DESC = _category_field_desc()


# ── Schema ─────────────────────────────────────────────────────────────────────

class IndicatorItem(BaseModel):
    """单条指标（提取阶段输出）"""
    indicator_type: str = Field(
        default="",
        description="指标类型，由分类阶段填写：inherent 或 experimental"
    )
    # 标准化对象（所有指标必填）
    standard_object: str = Field(
        description=(
            "本条指标的标准化对象（产品/材料/部件名称），填写最具体的直接被规定对象。"
            "优先从条款上下文判断，标准名称仅作参考；"
            "当标准涵盖多种材料/部件时，按条款实际对象填写，不强求全篇一致。"
            "示例：标准名称'光伏组件封装材料'，某条款专门规定玻璃要求 → '玻璃'；"
            "标准名称'乘用车前后端保护装置' → '前后端保护装置'。"
        )
    )
    # 应用对象（所有指标，可选）
    applicable_object: str = Field(
        default="",
        description=(
            "standard_object 的上级使用/应用主体，无则留空。"
            "示例：standard_object='玻璃'，其上级为'光伏组件封装材料' → applicable_object='光伏组件封装材料'；"
            "standard_object='前后端保护装置' → applicable_object='乘用车'；"
            "standard_object='光伏背板'（无明确上级）→ 留空。"
        ),
    )
    # 标准化对象类型（全篇一致，分类 Pass 会统一校正）
    object_type: str = Field(
        default="",
        description="标准化对象类型：产品类对象 | 服务类对象 | 过程类对象",
    )
    # 指标分类（所有指标必填，分类 Pass 会做一致性校正）
    indicator_category: str = Field(
        default="",
        description=_CATEGORY_FIELD_DESC,
    )
    # 规范类别（由分类 Pass 独立判断）
    norm_class: str = Field(
        default="",
        description=_NORM_CLASS_FIELD_DESC
    )
    # 指标字段（所有类型通用）
    indicator_object: str = Field(default="", description="指标对象名称，如：外径、碳含量、表面质量、电气强度、抗拉强度")
    source_value: str = Field(default="", description="规定值/判定结果，单位写在值里，如：≥500 MPa、0.15%~0.25%、不击穿")
    # 公共字段
    source_clause: str = Field(default="",
                               description="对应条款编号，仅填写章节编号（数字和点号组成），不含字母后缀（如 a)、b)、i) 等）。单个条款如：4.2；跨多个条款时用逗号分隔，如：4.2,5.1.3。不填标题文字。")


class TestItem(BaseModel):
    """单条试验（提取阶段输出，依据 GB/T 20001.4-2015）"""
    test_name: str = Field(description="试验名称，不含‘试验’后缀，如：电气强度、抗拉强度")
    method_desc: str = Field(default="", description="试验方法（试样制备、计算方法等）")
    conditions: str = Field(default="", description="试验条件（温度、湿度、电压等环境参数）")
    preparation: str = Field(default="", description="试验准备（试样处理、仪器校准、系统搭建）")
    procedure: str = Field(default="", description="试验过程（按逻辑次序的操作步骤）")
    acceptance: str = Field(default="", description="试验要求；合格判据，纯方法类标准（has_test_only）此字段留空")
    report_items: str = Field(default="", description="报告")
    source_clause: str = Field(default="", description="来源条款编号，如：5.3 或 5.3,6.1")
    # 分类字段（与指标共用体系，由分类 Pass 填写）
    standard_object: str = Field(default="", description="标准化对象")
    applicable_object: str = Field(default="", description="适用对象，无则留空")
    object_type: str = Field(default="", description="标准化对象类型：产品类对象 | 服务类对象 | 过程类对象")
    indicator_category: str = Field(default="", description="指标类别")
    indicators: List["IndicatorItem"] = Field(
        default_factory=list,
        description="本条试验对应的指标列表：有 acceptance 时从中提取，indicator_object=指标名称，source_value=判据内容；无 acceptance 时留空"
    )


class ExtractionOutput(BaseModel):
    """指标提取输出"""
    standard_structure_type: str = Field(
        default="",
        description="标准性质：has_ind_and_test/has_ind_only/has_test_only/ind_embedded_in_test"
    )
    indicators: List[IndicatorItem] = Field(
        default_factory=list,
        description="提取的指标列表（has_test_only 时为空）"
    )
    tests: List[TestItem] = Field(
        default_factory=list,
        description="提取的试验列表（has_ind_only 时为空）"
    )


class ObjectTypeMappingItem(BaseModel):
    """单个 standard_object 的对象类型判断结果"""
    standard_object: str = Field(description="标准化对象名称")
    object_type: str = Field(description="产品类对象 | 服务类对象 | 过程类对象")
    reasoning: str = Field(default="", description="一句话判断依据")


class ObjectTypeMappingOutput(BaseModel):
    """Step 1 输出：每个不同 standard_object 对应的 object_type"""
    mappings: List[ObjectTypeMappingItem] = Field(
        default_factory=list,
        description="每个 standard_object 的 object_type 映射，必须覆盖全部不同的 standard_object"
    )


class IndicatorClassificationItem(BaseModel):
    """单条指标的分类结果（Step 2）"""
    index: int = Field(description="指标在列表中的序号（从0开始）")
    indicator_category: str = Field(description="指标分类，从给定选项中选一个")
    norm_class: str = Field(description="要素组分类，从给定选项中选一个")
    indicator_type: str = Field(
        description="指标类型：inherent（固有指标，可独立于试验存在）| experimental（试验指标，需依附试验才有完整意义）")


class BatchClassificationOutput(BaseModel):
    """Step 2 单批次输出"""
    classifications: List[IndicatorClassificationItem] = Field(
        default_factory=list,
        description="本批次每条指标的分类结果，必须覆盖批次内全部指标"
    )


class ResolvedIndicatorItem(IndicatorItem):
    """resolver 返回的单条指标，带原始 index 以便程序合并"""
    index: int = Field(description="指标在原列表中的序号（从0开始），必须与输入中的 index 一致")


class ResolverOutput(BaseModel):
    """独立 resolver agent 输出"""
    resolved: List[ResolvedIndicatorItem] = Field(
        default_factory=list,
        description="已补全的指标条目（只返回被修改过的），每条必须包含 index 字段"
    )
    unresolvable_indices: List[int] = Field(
        default_factory=list,
        description="确认无法解析的指标 index（外部标准不在库中、图片无法识别等）"
    )


class ResolvedTestItem(TestItem):
    """resolver 返回的单条试验，带原始 index 以便程序合并"""
    index: int = Field(description="试验在原列表中的序号（从0开始），必须与输入中的 index 一致")


class TestResolverOutput(BaseModel):
    """独立试验引用补全 Agent 输出"""
    resolved: List[ResolvedTestItem] = Field(
        default_factory=list,
        description="已补全的试验条目（只返回被修改过的），每条必须包含 index 字段"
    )
    unresolvable_indices: List[int] = Field(
        default_factory=list,
        description="确认无法解析的试验 index（外部标准不在库中、图片无法识别等）"
    )


class PlannedGroupItem(BaseModel):
    """group-planner 输出的单个分组"""
    group_name: str = Field(description="分组名称，必须是具体试验/主题名称，不得是流程阶段词")
    chapter_nos: List[str] = Field(default_factory=list, description="本组章节号列表，如 ['6.3.2', '6.5']")
    reason: str = Field(default="", description="一句话说明该分组依据")


class GroupPlanOutput(BaseModel):
    """group-planner 输出"""
    groups: List[PlannedGroupItem] = Field(default_factory=list, description="最终可执行分组列表")
    rejected_groups: List[str] = Field(default_factory=list, description="被判定非法并剔除的分组名")


class ExtractionSummary(BaseModel):
    """主 agent 最终输出（轻量摘要，指标数据已通过 submit_group_indicators 工具分批提交）"""
    standard_structure_type: str = Field(
        description="标准性质：has_ind_and_test/has_ind_only/has_test_only/ind_embedded_in_test"
    )
    skip_reason: str = Field(default="", description="保留字段，暂不使用")
    standard_object: str = Field(default="", description="标准化对象")
    applicable_object: str = Field(default="", description="适用对象，无则留空")
    group_count: int = Field(default=0, description="提取分组数")
    total_submitted: int = Field(default=0, description="已通过 submit_group_indicators 提交的指标总数")
    test_submitted: int = Field(default=0, description="已通过 submit_group_tests 提交的试验总数")


class ObjectCorrectionItem(BaseModel):
    """单条对象校正结果"""
    original_standard_object: str = Field(description="原始 standard_object")
    original_applicable_object: str = Field(description="原始 applicable_object，无则空字符串")
    corrected_standard_object: str = Field(description="校正后的 standard_object，无需修改则与原值相同")
    corrected_applicable_object: str = Field(description="校正后的 applicable_object，无需修改则与原值相同")
    reason: str = Field(default="", description="一句话说明校正依据，无需修改则留空")


class ObjectCorrectionOutput(BaseModel):
    """对象整理 Agent 输出"""
    corrections: List[ObjectCorrectionItem] = Field(
        default_factory=list,
        description="每个输入的 (standard_object, applicable_object) 组合对应一条校正结果，必须覆盖全部输入组合"
    )


# ── 子 Agent Prompts ────────────────────────────────────────────────────────────

_INDICATOR_EXTRACTOR_PROMPT = """\
你是指标与试验提取专家，负责从标准章节中提取技术指标和/或试验条目。

## 你的任务

主 agent 会在 description 中给你：标准编号、标准名称、适用范围、standard_object、applicable_object、本组章节列表、分组名称、reason。

其中 standard_object / applicable_object 是主 agent 从适用范围初步提取的参考值：
- **applicable_object**：通常参考主 agent 的standard_object / applicable_object 得出，除非条款明确指向不同的使用主体
- **standard_object**：以章节实际内容为准，标准涵盖多种材料/部件时按条款对象填写，不受参考值约束

你需要：
1. 读取全部指定章节（先读完，再提取）
2. 根据分组内容（分组名称、章节列表、reason）判断本组包含什么：
   - **只含技术指标**（外观、尺寸、标识、包装、贮存等，无试验方法章节）→ 只调用 `submit_group_indicators`
   - **只含试验**（纯方法类，无判定准则）→ 只调用 `submit_group_tests`
   - **同时含指标和试验**→ 先调用 `submit_group_indicators`，再调用 `submit_group_tests`
3. 按对应规则提取并提交

## 指标提取规则

所有指标统一用 `indicator_object`（指标名称）+ `source_value`（规定值/判定结果）两个字段。

- 定量示例：`indicator_object=”外径”`, `source_value=”φ50±0.5 mm”`
- 定性示例：`indicator_object=”表面质量”`, `source_value=”不得有裂纹”`
- 试验结论示例：`indicator_object=”电气强度”`, `source_value=”不击穿”`

必须排除：术语定义、测试参数本身（仪器精度、测量步骤）、空泛原则、管理性要求。

## 试验提取规则（依据 GB/T 20001.4-2015）

| 字段 | 说明 |
|------|------|
| `test_name` | 试验名称，不含”试验”后缀，如：电气强度、抗拉强度 |
| `method_desc` | 试验方法：试样制备与保存、结果表述（含计算方法、准确度或测量不确定度）等原理性说明 |
| `conditions` | 试验条件：试验对象之外影响试验的环境参数，如温度、湿度、气压、电压、频率等 |
| `preparation` | 试验准备：执行试验步骤前需完成的预备工作，包括试样处理、仪器校准、系统搭建等 |
| `procedure` | 试验过程：按逻辑次序排列的操作步骤，使用祈使句，不含结论要求 |
| `acceptance` | 试验要求：试验完成后判断试样是否通过的具体技术指标或接受准则；无则留空 |
| `report_items` | 报告：试验报告应包含的内容；无则留空 |
| `source_clause` | 来源条款编号 |
| `standard_object` | 标准化对象 |
| `applicable_object` | 适用对象，无则留空 |

- **standard_object**：条款直接规定的产品/材料/部件，填最具体的对象；标准涵盖多种材料时按条款实际对象填写，不强求全篇一致
- **applicable_object**：standard_object 的上级使用/应用主体，无则留空
- 示例：标准名称"光伏组件封装材料"，某条款规定玻璃要求 → standard_object="玻璃"，applicable_object="光伏组件封装材料"；标准名称"乘用车前后端保护装置" → standard_object="前后端保护装置"，applicable_object="乘用车"


**对象维度约束**：同一试验方法作用于不同对象时，必须拆分为多条记录，`test_name` 不包含对象限定信息（如：`高压蒸煮试验`）。

**必须排除**：流程辅助章节（预处理、试样制备、仪器校准、检查维护、结果计算、报告格式等）不得单独提取为一条试验，应填入具体试验的 `preparation`/`procedure`/`report_items`。

**每条试验的 indicators**：
- 有 `acceptance` → 从中提取，`indicator_object`=指标名称，`source_value`=判据内容
- 无 `acceptance`（纯方法类）→ `indicators` 留空

## 表格与公式处理

章节内容含有表格、公式或图片时，优先调用 `parse_with_mineru` 工具传入图片文件名（`<img>` 标签的 src 值，不含路径和域名）获取结构化文本，再从中提取，不要依赖模型自身对原始 HTML/图片的解析。

## 占位标记规则

遇到以下情况，在对应字段填写占位标记，不要当场处理：
- “见表X”、”按表X” → `”[REF:表X]”`
- 含 `<img>` 的图片 → `”[REF:图片:src值]”`
- “按X.X规定”、”见附录X” → `”[REF:X.X]”`
- “按GB/T XXXX进行” → `”[REF:GB/T XXXX]”`

## 提交规范

- `submit_group_indicators`：`group_name` 为分组名称，`indicators_json` 包含 standard_object、applicable_object、indicator_object、source_value、source_clause（只写章节号，不含字母后缀）
- `submit_group_tests`：`group_name` 为分组名称，`tests_json` 包含全部试验字段及 `indicators` 子列表

调用成功后，将工具返回的确认信息作为最终回复返回。
"""


_GROUP_PLANNER_PROMPT = """\
你是分组规划专家，只负责为指标/试验提取生成可执行分组，不负责提取字段。

## 输入
- standard_no
- standard_name
- use_range（适用范围）
- standard_structure_type
- standard_object / applicable_object
- 章节目录（title_no/title/word_count）

## 输出
必须通过 GroupPlanOutput 提交：
- groups: [{group_name, chapter_nos, reason}]
- rejected_groups: 被判定非法的分组名

## 可提取内容范围

凡规定了具体技术要求的章节均应提取，包括但不限于：技术要求、性能要求、安全要求、试验方法、标识/标志、包装、运输、贮存、检验规则。

## 硬规则
1. 分组必须可执行（每组至少一个 chapter_no）。
2. 禁止流程阶段词单独成组：预处理、初始测试、最终测试、检查维护、结果计算、性能评价、分级、监测、通用条件、试样、试验装置。
3. 若章节属于流程辅助内容（取样方法、试验条件、通用条件等），不得独立成组；若该内容被多个试验组共用，必须在每个用到它的组中都列入 chapter_nos，允许跨组重复引用。
4. 同名试验不同对象不得合并。
5. has_test_only / has_ind_and_test 都必须优先按”可独立命名的试验项目”成组。
6. 无对应试验的技术要求章节（如外观、尺寸、标识、材料成分等）必须单独成组，不得丢弃或强行并入试验组。
7. 最小必要分组原则：在保证可执行与语义清晰的前提下，分组数越少越好；能合并为一组时必须合并，不得为了”看起来工整”而拆分。
8. 只有在以下情形才允许拆成多组：
   - 存在可独立命名且可独立得出结论的多个试验项目；
   - 同名试验但对象不同（必须拆分）；
   - 明确属于不同主题且合并后会造成指标/试验字段混淆。
9. 若候选章节整体围绕同一主题或同一试验项目，必须输出单组（groups 长度=1），并把通用条件/准备/报告等章节并入该组。

## 按 standard_structure_type 的分组口径
- has_ind_and_test：按”要求+对应试验”的可执行主题分组；技术要求章节中无对应试验方法的指标（如外观、尺寸、标识等），其所在章节必须单独成组，不得丢弃或强行并入试验组。若全篇只围绕一个主题，可单组回传。
- has_test_only：优先按“可独立命名且可独立得出结论的试验项目”分组；共用章节并入对应试验组，不单独成组。
- has_ind_only：按”指标主题/对象域”分组，不按试验思路拆分；若章节都服务于同一指标主题，必须单组回传。
- ind_embedded_in_test：按”混写主题块”分组（同一章节内含要求+方法视为一个主题块）；禁止把同一主题块再拆成流程小组。

## 输出质量要求
- group_name 必须是具体试验/主题名，不得为空
- chapter_nos 仅填章节号，不填标题
- reason 一句话说明依据，并显式说明“为何不继续拆分”
"""

_REFERENCE_SUBAGENT_PROMPT = """\
你是一位资深标准化专家，专门负责将技术指标中的所有占位引用补全为具体数值。

## 你的任务

主 agent 会给你：
- 标准编号
- 指标列表（部分字段含 `[REF:...]` 占位标记，或仍残留"见表X"、"按X.X规定"等未解析引用语）

你需要做两件事：

**第一步：主动扫描所有指标的所有字段**，找出以下任何一种未补全内容：
- `[REF:...]` 占位标记
- "见表X"、"按表X"、"如表X"等表格引用语
- 含 `<img>` 的图片标签
- "按X.X规定"、"符合X.X要求"、"见附录X"、"在X.X试验条件下"等章节引用语
- "按GB/T XXXX进行"等外部标准引用语
- 任何明显是引用而非具体值的内容

**第二步：逐一查阅并替换**，将所有未补全内容替换为具体数值。

**只返回被修改过的指标条目**（即原本含有占位或引用语的那些），主 agent 会自行将返回结果合并回完整列表。

## 占位标记说明与处理方式

| 占位格式 | 含义 | 处理方式 |
|---------|------|---------|
| `[REF:表X]` | 表格引用 | `get_standard_chapters` 读取含该表的章节，提取表格数据 |
| `[REF:图片:src值]` | 图片/公式图 | `fetch_image(src值)` 下载，`read_file` 读取，识别图中数值 |
| `[REF:X.X]` | 章节引用 | `get_standard_chapters(title_no_prefix="X.X")` 读取被引用章节正文 |
| `[REF:附录X]` | 附录引用 | `get_standard_chapters(title_no_prefix="附录X")` 读取附录正文 |
| `[REF:GB/T XXXX]` | 外部标准引用 | 先尝试 `get_standard_chapters(standard_no="GB/T XXXX")`；若库中无此标准，保留标准号原文 |

## 处理顺序

1. 扫描全部指标的全部字段（`source_value`），记录含未补全内容的指标及其 index
2. 按引用目标分组，批量读取（同一表格/章节只读一次）
3. 逐条将未补全内容替换为具体值
4. **替换完成后再次扫描**，确认无任何残留引用，若仍有则继续处理

## 绝对禁止

返回的指标字段中不得出现：
- `[REF:...]` 占位标记
- "见X.X"、"按X.X"、"同X.X"、"参见"
- "如表X"、"按表X"、"见表X"

补全后若仍无具体值（如外部标准库中不存在且无法推断）→ 该条指标保持原样返回。

## 返回格式

只返回被修改过的指标条目（JSON 列表），格式与输入 indicators 中单条一致：
```json
{"resolved": [...]}
```
"""

_STANDALONE_RESOLVER_PROMPT = """\
你是一位资深标准化专家，专门负责将技术指标中的所有占位引用补全为具体数值。

## 你的任务

你将收到：
- 标准编号
- 若干条含未解析引用的指标（每条带 `index` 字段标识其在原列表中的位置）

你需要：
1. 逐条扫描所有字段，找出 `[REF:...]` 占位标记及残留的自然语言引用（"见表X"、"按X.X规定"等）
2. 通过工具查阅对应内容，将引用替换为具体数值
3. 返回结构化结果

## 占位标记处理方式

| 占位格式 | 处理方式 |
|---------|---------|
| `[REF:表X]` | `get_standard_chapters` 读取含该表的章节，提取表格数据 |
| `[REF:图片:src值]` | `fetch_image(src值)` 下载，`read_file` 读取，识别图中数值 |
| `[REF:X.X]` | `get_standard_chapters(title_no_prefix="X.X")` 读取被引用章节正文 |
| `[REF:附录X]` | `get_standard_chapters(title_no_prefix="附录X")` 读取附录正文 |
| `[REF:GB/T XXXX]` 等外部标准引用 | 先尝试 `get_standard_chapters(standard_no="GB/T XXXX")`；**若库中无此标准，将 `[REF:GB/T XXXX]` 替换为标准号原文（如 `GB/T XXXX`）**，并将该指标 index 加入 `unresolvable_indices` |

自然语言残留（"见表X"、"按X.X规定"、"符合X.X要求"、"见附录X"）按相同逻辑处理。

## 无法解析时的处理

- **外部标准引用**（GB/T、GB、HB、GJB、QJ 等）库中不存在时：去掉 `[REF:]` 包装，保留标准号原文作为字段值，index 加入 `unresolvable_indices`
- **内部引用**（表格、章节、附录）：必须解析，不允许放弃
- **图片引用**下载或识别失败时：保留 `[图片无法识别]` 标记，index 加入 `unresolvable_indices`

## 绝对禁止

返回的指标字段中不得残留 `[REF:...]` 占位标记（外部标准已去包装的除外）。

## 返回格式

通过 ResolverOutput 工具提交结果：
- `resolved`：已补全的指标列表，每条必须包含 `index` 字段（与输入一致）
- `unresolvable_indices`：确认无法解析的指标 index 列表
"""

_STANDALONE_TEST_RESOLVER_PROMPT = """\
你是一位资深标准化专家，专门负责将试验条目中的所有占位引用补全为具体内容。

## 你的任务

你将收到：
- 标准编号
- 若干条含未解析引用的试验（每条带 `index` 字段标识其在原列表中的位置）

你需要：
1. 逐条扫描所有字段，找出 `[REF:...]` 占位标记及残留的自然语言引用（"见表X"、"按X.X规定"等）
2. 通过工具查阅对应内容，将引用替换为具体内容
3. 返回结构化结果

## 占位标记处理方式

| 占位格式 | 处理方式 |
|---------|---------|
| `[REF:表X]` | `get_standard_chapters` 读取含该表的章节，提取表格数据 |
| `[REF:图片:src值]` | `fetch_image(src值)` 下载，`read_file` 读取，识别图中数值 |
| `[REF:X.X]` | `get_standard_chapters(title_no_prefix="X.X")` 读取被引用章节正文 |
| `[REF:附录X]` | `get_standard_chapters(title_no_prefix="附录X")` 读取附录正文 |
| `[REF:GB/T XXXX]` 等外部标准引用 | 先尝试 `get_standard_chapters(standard_no="GB/T XXXX")`；**若库中无此标准，将 `[REF:GB/T XXXX]` 替换为标准号原文**，并将该试验 index 加入 `unresolvable_indices` |

自然语言残留（"见表X"、"按X.X规定"、"符合X.X要求"、"见附录X"）按相同逻辑处理。

## 无法解析时的处理

- **外部标准引用**库中不存在时：去掉 `[REF:]` 包装，保留标准号原文，index 加入 `unresolvable_indices`
- **内部引用**（表格、章节、附录）：必须解析，不允许放弃
- **图片引用**下载或识别失败时：保留 `[图片无法识别]` 标记，index 加入 `unresolvable_indices`

## 绝对禁止

返回的试验字段中不得残留 `[REF:...]` 占位标记（外部标准已去包装的除外）。

## 返回格式

通过 TestResolverOutput 工具提交结果：
- `resolved`：已补全的试验列表，每条必须包含 `index` 字段（与输入一致）
- `unresolvable_indices`：确认无法解析的试验 index 列表
"""

# ── 对象整理 Agent Prompt ────────────────────────────────────────────────────────

_OBJECT_CORRECTION_PROMPT = """\
你是一位资深标准化专家，负责对已提取指标/试验中的标准化对象和适用对象进行统一审查和校正。

## 字段含义

- **standard_object**：本条指标/试验直接规定的产品/材料/部件，填写最具体的直接被规定对象
  - 示例：标准名称"光伏组件封装材料"，某条款规定玻璃要求 → standard_object="玻璃"
  - 示例：标准名称"乘用车前后端保护装置" → standard_object="前后端保护装置"

- **applicable_object**：standard_object 的上级使用/应用主体，无则留空
  - 示例：standard_object="玻璃" → applicable_object="光伏组件封装材料"
  - 示例：standard_object="前后端保护装置" → applicable_object="乘用车"
  - 示例：standard_object="光伏背板"（无明确上级）→ applicable_object=""

## 常见错误模式

1. **两者填反**：standard_object 填了上级主体，applicable_object 填了具体部件
   - 错误示例：standard_object="乘用车"，applicable_object="前后端保护装置" → 应对调
2. **applicable_object 填成同级**：两者是并列关系而非上下级关系，applicable_object 应是 standard_object 的使用场景/搭载平台
3. **standard_object 过于宽泛**：填了标准名称全称而非具体被规定对象
   - 错误示例：标准名称"光伏组件封装材料"，standard_object 也填"光伏组件封装材料" → 应结合其他组合判断具体是玻璃/胶膜/背板中的哪一种
4. **standard_object 无实际含义**：如"其他产品"、"相关产品"、"该产品"、"本产品"等泛指词，单独看毫无意义
   - 处理方式：结合同一标准的其他组合和标准名称，推断其真实所指并替换；若无法确定则改为标准名称中的主体对象
5. **applicable_object 无中生有**：标准名称和适用范围中没有明确使用主体，却强行填写一个上级对象
   - 处理方式：若标准本身就是最顶层对象（如"光伏背板"），applicable_object 应留空，不要凭空捏造
6. **同一标准内对象命名不统一**：同一个对象在不同条款被提取为不同名称（如"保护装置"和"前后端保护装置"混用）
   - 处理方式：综合全部组合，统一为最准确、最完整的表述

## 审查方法

- **先整体看**：拿到所有组合后，先横向对比，找出命名不一致、明显异常的条目
- **再逐条判断**：结合标准名称和适用范围，对每个组合作出保留或校正的决定
- **工具辅助**：对无法仅凭名称判断的组合，调用 `get_standard_chapters` 查阅原文，或调用 `get_cached_indicators` 查看该组合下的具体指标内容，再作判断

## 你的任务

你将收到：
- 标准名称、适用范围
- 本标准提取出的所有 (standard_object, applicable_object) 组合及其出现次数

对每个组合，输出一条校正结果：合理的原样返回（corrected 值与 original 相同），不合理的给出校正值和一句话理由。

**只校正明显错误，不要过度干预合理的多样性。**
"""

# ── 主 Agent Prompt ─────────────────────────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """\
你是一位资深标准化专家，负责编排和执行技术标准的指标与试验提取任务。

## 执行流程

收到任务后，立即用 `write_todos` 初始化以下清单，不得合并或省略：

1. 读取目录（toc_only=true），获取全部章节的 title_no、title、word_count
2. 读取适用范围章节，初步确定 standard_object 和 applicable_object（仅作参考，indicator-extractor 按条款实际内容填写）
3. **判断标准内容结构类型**（standard_structure_type），从以下五类中选一个：
   - `has_ind_and_test`：有技术要求章节（规定指标值），且有独立的试验方法章节
   - `has_ind_only`：只有技术要求/性能要求，无独立试验方法章节（或试验方法仅引用外部标准）
   - `has_test_only`：只有试验规程，无判定准则（纯方法类标准）
   - `ind_embedded_in_test`：指标与试验混写在同一章节，无法分离

4. 排除适用范围（范围）、前言、引言、规范性引用文件、术语和定义、参考文献、资料性附录，其余章节全量传给 group-planner
5. 调用 `task(group-planner, ...)` 生成分组方案，description 中必须包含：标准编号、标准名称、适用范围、standard_object、applicable_object、standard_structure_type、全量章节目录
6. **并行**委托所有分组给 indicator-extractor：
   - 严格按 group-planner 返回的分组执行，不得自行新增、拆分或改名
   - 在同一轮思考中，对所有分组**同时发起** `task(indicator-extractor, ...)` 调用，不要逐组串行等待
   - 每个 task 调用需在 description 中说明：标准编号、标准名称、适用范围、standard_object、applicable_object、本组章节列表、分组名称
   - 若分组数量较多（>10组），可分几批并行，每批同时启动
7. 调用 ExtractionSummary 提交摘要（不含指标数据，只含统计信息）

**重要：**
- 指标数据由 indicator-extractor 直接通过 submit_group_indicators 提交，主 agent 无需转发任何指标 JSON
- 试验数据由 indicator-extractor 直接通过 submit_group_tests 提交，主 agent 无需转发任何试验 JSON
- 调用子 agent 时，description 只写必要的结构化信息（编号、名称、章节列表等），不得添加任何解释性文字、背景说明或建议

---

## 步骤 2：standard_object / applicable_object 提取

- **standard_object**：条款直接规定的产品/材料/部件，填最具体的对象；标准涵盖多种材料时按条款实际对象填写，不强求全篇一致
- **applicable_object**：standard_object 的上级使用/应用主体，无则留空
- 示例：标准名称"光伏组件封装材料"，某条款规定玻璃要求 → standard_object="玻璃"，applicable_object="光伏组件封装材料"；标准名称"乘用车前后端保护装置" → standard_object="前后端保护装置"，applicable_object="乘用车"

---

## 步骤 4：排除无关章节

从目录中去掉以下章节，其余章节**全部**传给 group-planner，不做任何预筛选或预分组：

- 适用范围（范围）、前言、引言、规范性引用文件、术语和定义、参考文献、资料性附录

---

## 步骤 5：分组执行约束

- 分组策略与最终分组结果由 `group-planner` 全权负责。
- 主 agent 不得在本步骤制定、改写或补充分组规则。
- 主 agent 只可将第 4 步剩余的全量章节与上下文交给 `group-planner`，并严格使用其返回分组执行后续提取。

---

## 可用工具

- `get_standard_chapters(standard_no, toc_only?, title_no_prefix?, keyword?, near_title_no?, window?)`
  读取标准章节。先用 toc_only=true 获取目录，再按需读取正文。
- `standard_query(sql)` 执行只读 SQL（仅限 standard_ 前缀表），不写 LIMIT 会自动追加 LIMIT 200。
- `task(subagent_type, description)` 委托子 agent 执行重型任务。

可用子 agent：
- `group-planner`：读取目录与关键章节，输出结构化分组（GroupPlanOutput），并执行非法分组自检。
- `indicator-extractor`：读取指定章节，根据分组内容提取技术指标和/或试验，分别调用 `submit_group_indicators` 和 `submit_group_tests` 提交结果。
  委托时必须在 description 中说明：标准编号、standard_object、applicable_object、通用试验条件（若有）、本组章节列表、分组名称。

**禁止不看目录就一次性读取整篇标准。**
"""


# ── 子 Agent 定义 ────────────────────────────────────────────────────────────────

def _make_subagents(submit_tool=None, submit_tests_tool=None, pool_id: Optional[int] = None):
    """创建指标提取所需的子 agent 列表（3 层架构）。"""
    from app.langchain.tools.db_tools import make_db_tools
    from app.langchain.agents.structured_agent import (
        make_default_tools,
        make_subagent_runnable,
    )

    extractor_tools = make_default_tools(pool_id)
    if submit_tool:
        extractor_tools = extractor_tools + [submit_tool]
    if submit_tests_tool:
        extractor_tools = extractor_tools + [submit_tests_tool]

    # ── indicator-extractor（直接提取，无协调层）──────────────────────────────────
    logger.info("初始化提取 Agent（indicator-extractor）...")
    indicator_extractor = {
        "name": "indicator-extractor",
        "description": "根据分组内容（分组名称、章节列表、reason）自行判断提取指标和/或试验，调用 submit_group_indicators / submit_group_tests 提交。",
        "runnable": make_subagent_runnable(
            name="indicator-extractor",
            system_prompt=_INDICATOR_EXTRACTOR_PROMPT,
            tools=extractor_tools,
        ),
    }

    return [
        {
            "name": "group-planner",
            "description": "基于目录与章节信息生成结构化分组方案，执行非法分组自检，输出 GroupPlanOutput。",
            "runnable": make_subagent_runnable(
                name="group-planner",
                system_prompt=_GROUP_PLANNER_PROMPT,
                tools=make_db_tools(pool_id),
            ),
        },
        indicator_extractor,
        {
            "name": "reference-resolver",
            "description": "处理指标字段中的占位引用（见表X、按X.X规定、附录等），补全为具体数值，返回完整指标列表。",
            "runnable": make_subagent_runnable(
                name="reference-resolver",
                system_prompt=_REFERENCE_SUBAGENT_PROMPT,
                tools=make_default_tools(pool_id),
            ),
        },
    ]


# ── 提交工具 & Agent 工厂 ──────────────────────────────────────────────────────────

def _make_submit_tool(accumulator: List[IndicatorItem]):
    """创建闭包工具，主 agent 每完成一组提取后调用此工具提交指标到累积器。"""

    @tool
    def submit_group_indicators(
            group_name: Annotated[str, "分组名称，如'力学性能'、'电气安全'"],
            indicators_json: Annotated[str, "本组指标的 JSON 数组，每条格式与 IndicatorItem 字段一致"],
    ) -> str:
        """提交一组提取完成的指标到累积器。每完成一组提取后必须立即调用，不要等到最后一起提交。"""
        try:
            items = json.loads(indicators_json)
            if not isinstance(items, list):
                items = [items]
            parsed = [IndicatorItem(**item) for item in items]
            accumulator.extend(parsed)
            return f"已提交分组「{group_name}」{len(parsed)} 条指标，累计 {len(accumulator)} 条"
        except Exception as e:
            return f"提交失败：{e}。请检查 JSON 格式后重试。"

    return submit_group_indicators


def _make_submit_tests_tool(accumulator: List[TestItem]):

    @tool
    def submit_group_tests(
            group_name: Annotated[str, "分组名称，如'力学性能'、'电气安全'"],
            tests_json: Annotated[str, "本组试验的 JSON 数组，每条格式与 TestItem 字段一致，必须包含 indicators 子列表"],
    ) -> str:
        """提交一组提取完成的试验到累积器。每完成一组提取后必须立即调用，不要等到最后一起提交。"""
        try:
            items = json.loads(tests_json)
            if not isinstance(items, list):
                items = [items]
            parsed = [TestItem(**item) for item in items]
            accumulator.extend(parsed)
            return f"已提交分组「{group_name}」{len(parsed)} 条试验，累计 {len(accumulator)} 条"
        except Exception as e:
            return f"提交失败：{e}。请检查 JSON 格式后重试。"

    return submit_group_tests


def _create_extraction_agent(submit_tool=None, submit_tests_tool=None) -> StructuredQAAgent:
    """每次调用创建新的主 agent（因为 submit 工具是闭包，不能复用单例）。"""
    logger.info("创建指标提取主 Agent（分组提取模式）...")
    return create_structured_qa_agent(
        output_schema=ExtractionSummary,
        system_prompt=_EXTRACTION_SYSTEM_PROMPT,
        subagents=_make_subagents(submit_tool=submit_tool, submit_tests_tool=submit_tests_tool),
    )


_RE_NON_ALNUM = re.compile(r"[^\w一-鿿]+")


def _normalize_name(s: str) -> str:
    """去除标点/空白/符号后转小写，用于模糊去重比较。"""
    return _RE_NON_ALNUM.sub("", s).lower()


def _deduplicate(indicators: List[IndicatorItem]) -> List[IndicatorItem]:
    """去重：indicator_object + standard_object + applicable_object 规范化后三者均相同才视为重复。"""
    seen: set[tuple] = set()
    result: List[IndicatorItem] = []
    for ind in indicators:
        key = (
            _normalize_name(ind.indicator_object),
            _normalize_name(ind.standard_object),
            _normalize_name(ind.applicable_object),
        )
        if key not in seen:
            seen.add(key)
            result.append(ind)
    return result


def _deduplicate_tests(tests: List[TestItem]) -> List[TestItem]:
    """去重：test_name + standard_object + applicable_object 规范化后三者均相同才视为重复。"""
    seen: set[tuple] = set()
    result: List[TestItem] = []
    for t in tests:
        key = (
            _normalize_name(t.test_name),
            _normalize_name(t.standard_object),
            _normalize_name(t.applicable_object),
        )
        if key not in seen:
            seen.add(key)
            result.append(t)
    return result


# ── 提取 Agent（旧单例，保留兼容） ──────────────────────────────────────────────────

_extraction_agent: Optional[StructuredQAAgent] = None


def get_extraction_agent() -> StructuredQAAgent:
    global _extraction_agent
    if _extraction_agent is None:
        logger.info("初始化指标提取 Agent（主 agent + 2 个子 agent）...")
        _extraction_agent = create_structured_qa_agent(
            output_schema=ExtractionSummary,
            system_prompt=_EXTRACTION_SYSTEM_PROMPT,
            subagents=_make_subagents(),
        )
    return _extraction_agent


# ── 引用补全循环 ──────────────────────────────────────────────────────────────────

_MAX_RESOLVE_ROUNDS = 3
_VALUE_FIELDS = ("source_value",)
_TEST_VALUE_FIELDS = ("conditions", "preparation", "procedure", "method_desc", "acceptance")

_RE_REF_PLACEHOLDER = re.compile(r"\[REF:[^\]]+\]")
_RE_NATURAL_REF = re.compile(
    r"(?:见|按|如|同|参见|参照|符合|按照|依据)\s*"
    r"(?:表\s*[\dA-Za-z.]+|"
    r"[\d]+\.[\d.]+\s*(?:规定|要求|条件)?|"
    r"附录\s*[A-Za-z\d]+|"
    r"图\s*[\dA-Za-z.]+)"
)
_RE_EXTERNAL_STD = re.compile(
    r"\[REF:((?:GB/T|GB|HB|QJ|GJB|YB|JB|NB|SJ|DL|JC|T/)\s*[\d.]+(?:-\d{4})?[^\]]*)\]"
)


def _scan_unresolved(indicators: List[IndicatorItem], skip: Set[int]) -> List[Tuple[int, IndicatorItem]]:
    """扫描所有指标的值字段，返回含未解析引用的 (index, item) 列表。"""
    unresolved = []
    for idx, item in enumerate(indicators):
        if idx in skip:
            continue
        for field in _VALUE_FIELDS:
            val = getattr(item, field, "") or ""
            if _RE_REF_PLACEHOLDER.search(val) or _RE_NATURAL_REF.search(val):
                unresolved.append((idx, item))
                break
    return unresolved


def _cleanup_external_refs(item: IndicatorItem) -> IndicatorItem:
    """去掉无法解析的外部标准引用的 [REF:] 包装，保留标准号原文。"""
    updates = {}
    for field in _VALUE_FIELDS:
        val = getattr(item, field, "") or ""
        if not val:
            continue
        cleaned = val
        if _RE_EXTERNAL_STD.search(cleaned):
            cleaned = _RE_EXTERNAL_STD.sub(r"\1", cleaned)
        if _RE_REF_PLACEHOLDER.search(cleaned):
            cleaned = re.sub(r"\[REF:([^\]]+)\]", r"\1", cleaned)
        if cleaned != val:
            updates[field] = cleaned
    return item.model_copy(update=updates) if updates else item


def _scan_unresolved_tests(tests: List[TestItem], skip: Set[int]) -> List[Tuple[int, TestItem]]:
    """扫描所有试验的内容字段，返回含未解析引用的 (index, item) 列表。"""
    unresolved = []
    for idx, item in enumerate(tests):
        if idx in skip:
            continue
        for field in _TEST_VALUE_FIELDS:
            val = getattr(item, field, "") or ""
            if _RE_REF_PLACEHOLDER.search(val) or _RE_NATURAL_REF.search(val):
                unresolved.append((idx, item))
                break
    return unresolved


def _cleanup_external_refs_test(item: TestItem) -> TestItem:
    """去掉试验字段中无法解析的外部标准引用的 [REF:] 包装，保留标准号原文。"""
    updates = {}
    for field in _TEST_VALUE_FIELDS:
        val = getattr(item, field, "") or ""
        if not val:
            continue
        cleaned = val
        if _RE_EXTERNAL_STD.search(cleaned):
            cleaned = _RE_EXTERNAL_STD.sub(r"\1", cleaned)
        if _RE_REF_PLACEHOLDER.search(cleaned):
            cleaned = re.sub(r"\[REF:([^\]]+)\]", r"\1", cleaned)
        if cleaned != val:
            updates[field] = cleaned
    return item.model_copy(update=updates) if updates else item


_resolver_agent: Optional[StructuredQAAgent] = None


def get_resolver_agent() -> StructuredQAAgent:
    """获取独立的引用补全 Agent（程序级循环调用）。"""
    global _resolver_agent
    if _resolver_agent is None:
        from app.langchain.agents.structured_agent import _make_fetch_image_tool
        logger.info("初始化独立引用补全 Agent...")
        _resolver_agent = create_structured_qa_agent(
            output_schema=ResolverOutput,
            system_prompt=_STANDALONE_RESOLVER_PROMPT,
            extra_tools=[_make_fetch_image_tool()],
        )
    return _resolver_agent


async def _resolve_references(
        standard_no: str,
        indicators: List[IndicatorItem],
) -> List[IndicatorItem]:
    """程序级循环：扫描残留引用 → 调用 resolver agent → 合并，最多 _MAX_RESOLVE_ROUNDS 轮。"""
    confirmed_unresolvable: Set[int] = set()

    for round_num in range(1, _MAX_RESOLVE_ROUNDS + 1):
        unresolved = _scan_unresolved(indicators, skip=confirmed_unresolvable)
        if not unresolved:
            logger.info(f"[补全] 第 {round_num} 轮扫描无残留引用，跳过")
            break

        logger.info(f"[补全] 第 {round_num}/{_MAX_RESOLVE_ROUNDS} 轮，发现 {len(unresolved)} 条含未解析引用")

        resolver_input = []
        for idx, item in unresolved:
            d = item.model_dump()
            d["index"] = idx
            resolver_input.append(d)

        resolver_message = (
                f"标准编号：{standard_no}\n\n"
                f"以下 {len(resolver_input)} 条指标含未解析引用，请逐一查阅并补全：\n\n"
                + json.dumps(resolver_input, ensure_ascii=False, indent=2)
        )

        try:
            resolver_result = cast(ResolverOutput, await get_resolver_agent().ainvoke(
                {"messages": [{"role": "user", "content": resolver_message}]},
                config={"configurable": {"thread_id": str(uuid.uuid4())}},
            ))
        except Exception as e:
            logger.warning(f"[补全] 第 {round_num} 轮 resolver 调用失败，跳过: {e}")
            break

        merged = 0
        for resolved_item in resolver_result.resolved:
            idx = resolved_item.index
            if 0 <= idx < len(indicators):
                indicators[idx] = IndicatorItem(**{
                    k: v for k, v in resolved_item.model_dump().items() if k != "index"
                })
                merged += 1

        confirmed_unresolvable.update(resolver_result.unresolvable_indices)
        logger.info(
            f"[补全] 第 {round_num} 轮完成，合并 {merged} 条，"
            f"不可解析 {len(resolver_result.unresolvable_indices)} 条"
        )

    indicators = [_cleanup_external_refs(item) for item in indicators]
    return indicators


_test_resolver_agent: Optional[StructuredQAAgent] = None


def get_test_resolver_agent() -> StructuredQAAgent:
    """获取独立的试验引用补全 Agent。"""
    global _test_resolver_agent
    if _test_resolver_agent is None:
        from app.langchain.agents.structured_agent import _make_fetch_image_tool
        logger.info("初始化试验引用补全 Agent...")
        _test_resolver_agent = create_structured_qa_agent(
            output_schema=TestResolverOutput,
            system_prompt=_STANDALONE_TEST_RESOLVER_PROMPT,
            extra_tools=[_make_fetch_image_tool()],
        )
    return _test_resolver_agent


async def _resolve_test_references(
        standard_no: str,
        tests: List[TestItem],
) -> List[TestItem]:
    """程序级循环：扫描试验字段残留引用 → 调用 resolver → 合并，最多 _MAX_RESOLVE_ROUNDS 轮。"""
    if not tests:
        return tests

    confirmed_unresolvable: Set[int] = set()

    for round_num in range(1, _MAX_RESOLVE_ROUNDS + 1):
        unresolved = _scan_unresolved_tests(tests, skip=confirmed_unresolvable)
        if not unresolved:
            logger.info(f"[试验补全] 第 {round_num} 轮扫描无残留引用，跳过")
            break

        logger.info(f"[试验补全] 第 {round_num}/{_MAX_RESOLVE_ROUNDS} 轮，发现 {len(unresolved)} 条含未解析引用")

        resolver_input = []
        for idx, item in unresolved:
            d = item.model_dump()
            d["index"] = idx
            resolver_input.append(d)

        resolver_message = (
                f"标准编号：{standard_no}\n\n"
                f"以下 {len(resolver_input)} 条试验含未解析引用，请逐一查阅并补全：\n\n"
                + json.dumps(resolver_input, ensure_ascii=False, indent=2)
        )

        try:
            resolver_result = cast(TestResolverOutput, await get_test_resolver_agent().ainvoke(
                {"messages": [{"role": "user", "content": resolver_message}]},
                config={"configurable": {"thread_id": str(uuid.uuid4())}},
            ))
        except Exception as e:
            logger.warning(f"[试验补全] 第 {round_num} 轮 resolver 调用失败，跳过: {e}")
            break

        merged = 0
        for resolved_item in resolver_result.resolved:
            idx = resolved_item.index
            if 0 <= idx < len(tests):
                tests[idx] = TestItem(**{
                    k: v for k, v in resolved_item.model_dump().items() if k != "index"
                })
                merged += 1

        confirmed_unresolvable.update(resolver_result.unresolvable_indices)
        logger.info(
            f"[试验补全] 第 {round_num} 轮完成，合并 {merged} 条，"
            f"不可解析 {len(resolver_result.unresolvable_indices)} 条"
        )

    tests = [_cleanup_external_refs_test(item) for item in tests]
    return tests


# ── 对象整理 Agent ────────────────────────────────────────────────────────────────

_object_correction_agent: Optional[StructuredQAAgent] = None


def get_object_correction_agent(pool_id: Optional[int] = None) -> StructuredQAAgent:
    global _object_correction_agent
    if _object_correction_agent is None:
        from app.langchain.tools.db_tools import make_db_tools, make_cache_ind_tool
        logger.info("初始化对象整理 Agent...")
        _object_correction_agent = create_structured_qa_agent(
            output_schema=ObjectCorrectionOutput,
            system_prompt=_OBJECT_CORRECTION_PROMPT,
            extra_tools=make_db_tools(pool_id) + [make_cache_ind_tool()],
        )
    return _object_correction_agent


async def _correct_objects(
        standard_no: str,
        standard_name: str,
        std_context: str,
        indicators: List[IndicatorItem],
        tests: List[TestItem],
) -> tuple[List[IndicatorItem], List[TestItem]]:
    """
    对象整理 Pass：聚合所有 (standard_object, applicable_object) 组合，
    交给 Agent 审查，将校正映射批量回写到指标和试验列表。
    """
    # 聚合唯一组合及出现次数
    combo_counts: dict[tuple[str, str], int] = {}
    for ind in indicators:
        key = (ind.standard_object or "", ind.applicable_object or "")
        combo_counts[key] = combo_counts.get(key, 0) + 1
    for t in tests:
        key = (t.standard_object or "", t.applicable_object or "")
        combo_counts[key] = combo_counts.get(key, 0) + 1

    if not combo_counts:
        return indicators, tests

    combo_lines = "\n".join(
        f'- standard_object="{so}", applicable_object="{ao}", 出现{cnt}次'
        for (so, ao), cnt in sorted(combo_counts.items(), key=lambda x: -x[1])
    )

    message = (
            f"标准编号：{standard_no}"
            + (f"（{standard_name}）" if standard_name else "")
            + std_context
            + f"\n\n本标准提取出以下 {len(combo_counts)} 个对象组合，请逐一审查：\n\n{combo_lines}"
    )

    try:
        result = cast(ObjectCorrectionOutput, await get_object_correction_agent().ainvoke(
            {"messages": [{"role": "user", "content": message}]},
            config={"configurable": {"thread_id": str(uuid.uuid4())}},
        ))
    except Exception as e:
        logger.warning(f"[对象整理] {standard_no} Agent 调用失败，跳过: {e}")
        return indicators, tests

    # 构建校正映射 (original_so, original_ao) → (corrected_so, corrected_ao)
    correction_map: dict[tuple[str, str], tuple[str, str]] = {}
    for item in result.corrections:
        orig = (item.original_standard_object, item.original_applicable_object)
        corrected = (item.corrected_standard_object, item.corrected_applicable_object)
        if orig != corrected:
            correction_map[orig] = corrected
            logger.info(
                f"[对象整理] {standard_no} 校正：{orig} → {corrected}"
                + (f"（{item.reason}）" if item.reason else "")
            )

    if not correction_map:
        logger.info(f"[对象整理] {standard_no} 无需校正")
        return indicators, tests

    def _apply(so: str, ao: str) -> tuple[str, str]:
        return correction_map.get((so, ao), (so, ao))

    corrected_indicators = []
    for ind in indicators:
        new_so, new_ao = _apply(ind.standard_object or "", ind.applicable_object or "")
        corrected_indicators.append(ind.model_copy(update={"standard_object": new_so, "applicable_object": new_ao}))

    corrected_tests = []
    for t in tests:
        new_so, new_ao = _apply(t.standard_object or "", t.applicable_object or "")
        corrected_tests.append(t.model_copy(update={"standard_object": new_so, "applicable_object": new_ao}))

    logger.info(f"[对象整理] {standard_no} 完成，校正了 {len(correction_map)} 个组合")
    return corrected_indicators, corrected_tests


# ── 分类 Agent（两步走）──────────────────────────────────────────────────────────

# Step 1：判断每个 standard_object 的 object_type

# 各对象类型的一句话描述（含 txt 中尚未收录的预留类型）
_OBJECT_TYPE_HINTS: dict[str, str] = {
    "产品类对象": "规定产品/材料/部件/零件本身的技术要求（最常见，如钢管、电缆、保护装置、光伏背板等）",
    "服务类对象": "规定服务提供的要求，侧重于服务的框架、流程、组织与保障（如信息技术服务、医疗服务、检测服务等）",
    "过程类对象": "规定具体的物理/化学操作步骤或试验方法，侧重于‘如何做’出某个结果（如焊接工艺、加速老化试验方法、化学滴定规程等）"
}


def _build_object_type_prompt() -> str:
    """从 MIND_MAP_ROOT 深度1节点动态生成对象类型选项，描述来自 _OBJECT_TYPE_HINTS。"""
    from app.langchain.agents.mind_map import MIND_MAP_ROOT

    prompt = (
        "你是一位资深标准化专家。你将收到若干个「标准化对象」名称，以及该标准的基本信息。\n"
        "请判断每个标准化对象属于哪类标准化对象类型，从以下选项中选一个：\n\n"
    )

    seen: set[str] = set()
    for node in MIND_MAP_ROOT.children:
        name = node.name
        seen.add(name)
        desc = _OBJECT_TYPE_HINTS.get(name, "")
        prompt += f"- **{name}**" + (f"：{desc}\n" if desc else "\n")

    for name, desc in _OBJECT_TYPE_HINTS.items():
        if name not in seen:
            prompt += f"- **{name}**：{desc}\n"

    prompt += "\n必须覆盖全部给出的 standard_object，每个返回一条映射。"
    return prompt


_OBJECT_TYPE_SYSTEM_PROMPT: str = _build_object_type_prompt()

# 扩充为包含预留类型，使 _normalize_object_type 能识别 LLM 返回的所有合法值
_OBJECT_TYPE_OPTIONS = frozenset(set(_OBJECT_TYPE_OPTIONS) | set(_OBJECT_TYPE_HINTS.keys()))

_object_type_agent: Optional[StructuredQAAgent] = None


def get_object_type_agent() -> StructuredQAAgent:
    global _object_type_agent
    if _object_type_agent is None:
        logger.info("初始化 object_type 判断 Agent...")
        _object_type_agent = create_structured_qa_agent(
            output_schema=ObjectTypeMappingOutput,
            system_prompt=_OBJECT_TYPE_SYSTEM_PROMPT,
        )
    return _object_type_agent


# Step 2：按 object_type 批量分类 indicator_category + norm_class

_BATCH_SIZE = 25  # 每批指标数量


@tool
def get_category_info(object_type: str, category_name: str) -> str:
    """查询某个 indicator_category（要素）的完整说明和示例，用于分类存疑时辅助判断。

    Args:
        object_type:   对象类型，如"产品类对象"
        category_name: 要素名称，如"性能"、"外在特性"

    Returns:
        该要素的完整说明文字和示例（来自标准化对象分类体系）
    """
    from app.langchain.agents.mind_map import get_annotation
    annotations = get_annotation(object_type, category_name)
    if not annotations:
        return f"未找到「{object_type}」下「{category_name}」的说明信息。"
    return "\n\n".join(annotations)


def _build_batch_classification_prompt(object_type: str) -> str:
    """为指定 object_type 构建批量分类 system prompt，树形展示 norm_class→indicator_category 层级。"""
    groups = TAXONOMY.get(object_type, {})

    if groups:
        tree_str = ""
        for group_name, elements in groups.items():
            tree_str += f"- **{group_name}**\n"
            for e in elements:
                if e in ("其他", "子主题"):
                    continue
                tree_str += f"  - {e}\n"
    else:
        tree_str = (
            f"该对象类型（{object_type}）暂无预定义分类，"
            "请根据指标内容自行填写最合适的 norm_class 和 indicator_category。"
        )

    return f"""\
你是一位资深标准化专家，任务是为技术指标批量指定 indicator_category、norm_class 和 indicator_type。

## 字段含义

- **norm_class**：要素组，填下方选项树中**加粗**的上级节点名称（如"规范类要素"）
- **indicator_category**：要素，填下方选项树中缩进的下级节点名称（如"性能"）
- 两者必须来自同一组，即 indicator_category 必须是所选 norm_class 的子节点

## 当前标准化对象类型：{object_type}

## 选项（norm_class → indicator_category）

{tree_str}

## indicator_type 选项

- **inherent**：固有指标，可独立于试验存在（如尺寸、成分、外观要求、标识）
- **experimental**：试验指标，需依附试验才有完整意义（如耐压、抗拉、老化、冲击）

## 判断规则

1. 同类型指标必须归入同一分类，先通览全批再逐条判断
2. 对某个要素的范围有疑问时，调用 get_category_info(object_type, category_name) 查看完整说明和示例后再判断

## 输出要求

必须覆盖批次内全部指标（按 index 对应），不得遗漏。norm_class / indicator_category / indicator_type必须使用选项中的值，不得自己创建。
"""


_batch_cls_agents: dict[str, StructuredQAAgent] = {}


def get_batch_cls_agent(object_type: str) -> StructuredQAAgent:
    """每个 object_type 维护一个独立的批量分类 agent 单例。"""
    if object_type not in _batch_cls_agents:
        prompt = _build_batch_classification_prompt(object_type)
        logger.info(f"初始化批量分类 Agent（{object_type}），system prompt：\n{prompt}")
        _batch_cls_agents[object_type] = create_structured_qa_agent(
            output_schema=BatchClassificationOutput,
            system_prompt=prompt,
            extra_tools=[get_category_info],
        )
    return _batch_cls_agents[object_type]


async def _classify_indicators(
        standard_no: str,
        standard_name: str,
        std_context: str,
        indicators: List[IndicatorItem],
) -> tuple[List[IndicatorItem], dict[str, str]]:
    """
    两步分类：
      Step 1 — 判断每个不同 standard_object 的 object_type
      Step 2 — 按 object_type 分组，并行批量分类 indicator_category + norm_class
    """
    import asyncio

    if not indicators:
        return indicators, {}

    # ── Step 1：收集所有不同的 standard_object，批量判断 object_type ──────────────
    unique_objects = list(dict.fromkeys(
        ind.standard_object for ind in indicators if ind.standard_object
    ))
    if not unique_objects:
        logger.warning(f"[分类] {standard_no} 所有指标的 standard_object 为空，跳过分类")
        return indicators, {}

    obj_list_str = "\n".join(f"- {o}" for o in unique_objects)
    step1_message = (
            f"标准编号：{standard_no}"
            + (f"（{standard_name}）" if standard_name else "")
            + std_context
            + f"\n\n需要判断以下 {len(unique_objects)} 个标准化对象的类型：\n{obj_list_str}"
    )

    try:
        mapping_result = cast(ObjectTypeMappingOutput, await get_object_type_agent().ainvoke(
            {"messages": [{"role": "user", "content": step1_message}]},
            config={"configurable": {"thread_id": str(uuid.uuid4())}},
        ))
        obj_type_map: dict[str, str] = {}
        for item in mapping_result.mappings:
            normalized = _normalize_object_type(item.object_type)
            if normalized:
                obj_type_map[item.standard_object] = normalized
                logger.info(f"[分类-Step1] {item.standard_object!r} → {normalized!r}（原始：{item.object_type!r}）")
            else:
                logger.warning(f"[分类-Step1] {item.standard_object!r} object_type 无法识别：{item.object_type!r}")
    except Exception as e:
        logger.warning(f"[分类-Step1] {standard_no} object_type 判断失败，跳过分类: {e}")
        return indicators, {}

    # ── Step 2：按 object_type 分组，并行批量分类 ──────────────────────────────────

    # 给每条指标打上 object_type（先按 standard_object 查映射，查不到则空）
    indicators_with_ot: List[tuple[int, IndicatorItem, str]] = []
    for idx, ind in enumerate(indicators):
        ot = obj_type_map.get(ind.standard_object, "")
        indicators_with_ot.append((idx, ind, ot))

    # 按 object_type 分组（空 object_type 的单独一组，用空字符串键）
    groups: dict[str, List[tuple[int, IndicatorItem]]] = {}
    for idx, ind, ot in indicators_with_ot:
        groups.setdefault(ot, []).append((idx, ind))

    # 构建所有批次任务
    async def _run_batch(object_type: str, batch: List[tuple[int, IndicatorItem]]) -> dict[int, tuple[str, str]]:
        """运行单个批次，返回 {index: (indicator_category, norm_class)}"""
        if not object_type:
            return {}

        lines = []
        for idx, ind in batch:
            so = f', standard_object="{ind.standard_object}"' if ind.standard_object else ""
            ao = f', applicable_object="{ind.applicable_object}"' if ind.applicable_object else ""
            core = f'indicator_object="{ind.indicator_object}", source_value="{ind.source_value}"'
            lines.append(f'[{idx}] type={ind.indicator_type}{so}{ao}, {core}, source_clause="{ind.source_clause}"')

        batch_message = (
                f"标准编号：{standard_no}"
                + (f"（{standard_name}）" if standard_name else "")
                + f"\n\n以下 {len(batch)} 条指标均属于【{object_type}】，请为每条指定 indicator_category 和 norm_class：\n\n"
                + "\n".join(lines)
        )

        try:
            result = cast(BatchClassificationOutput, await get_batch_cls_agent(object_type).ainvoke(
                {"messages": [{"role": "user", "content": batch_message}]},
                config={"configurable": {"thread_id": str(uuid.uuid4())}},
            ))
            out = {}
            for item in result.classifications:
                cat = item.indicator_category
                norm = item.norm_class if item.norm_class in _NORM_CLASS_OPTIONS else next(
                    (opt for opt in _NORM_CLASS_OPTIONS if opt in item.norm_class or item.norm_class in opt), ""
                )
                ind_type = item.indicator_type if item.indicator_type in ("inherent", "experimental") else ""
                if cat:
                    out[item.index] = (cat, norm, ind_type)
            return out
        except Exception as e:
            logger.warning(f"[分类-Step2] {standard_no} 批次失败（{object_type}，{len(batch)} 条）: {e}")
            return {}

    # 切分批次并并行执行
    tasks = []
    for ot, group_items in groups.items():
        for i in range(0, len(group_items), _BATCH_SIZE):
            batch = group_items[i: i + _BATCH_SIZE]
            tasks.append((ot, batch))

    logger.info(f"[分类-Step2] {standard_no} 共 {len(tasks)} 个批次，并行执行...")
    for _bi, (_ot, _batch) in enumerate(tasks):
        _names = ", ".join(ind.indicator_object for _, ind in _batch)
        logger.info(f"[分类-Step2] 批次{_bi + 1} object_type={_ot!r}（{len(_batch)}条）: {_names}")
    batch_results = await asyncio.gather(*[_run_batch(ot, batch) for ot, batch in tasks])

    # 合并所有批次结果
    cls_map: dict[int, tuple[str, str, str]] = {}
    for br in batch_results:
        cls_map.update(br)

    # 重试：indicator_category 或 norm_class 任一为空都重试，最多 3 次
    _MAX_CLS_RETRIES = 3
    for _retry in range(_MAX_CLS_RETRIES):
        missing = [
            (idx, ind, ot) for idx, ind, ot in indicators_with_ot
            if ot and (idx not in cls_map or not cls_map[idx][0] or not cls_map[idx][1])
        ]
        if not missing:
            break
        # 清掉不完整的条目，避免半填结果干扰重试
        for idx, _, _ in missing:
            cls_map.pop(idx, None)
        logger.warning(f"[分类-Step2] {standard_no} 第{_retry + 1}次重试，{len(missing)} 条 category/norm_class 不完整")
        retry_groups: dict[str, List[tuple[int, IndicatorItem]]] = {}
        for idx, ind, ot in missing:
            retry_groups.setdefault(ot, []).append((idx, ind))
        retry_tasks = [
            (ot, retry_groups[ot][i: i + _BATCH_SIZE])
            for ot in retry_groups
            for i in range(0, len(retry_groups[ot]), _BATCH_SIZE)
        ]
        retry_results = await asyncio.gather(*[_run_batch(ot, batch) for ot, batch in retry_tasks])
        for br in retry_results:
            cls_map.update(br)

    still_missing = sum(
        1 for idx, _, ot in indicators_with_ot
        if ot and (idx not in cls_map or not cls_map[idx][0] or not cls_map[idx][1])
    )
    if still_missing:
        logger.warning(f"[分类-Step2] {standard_no} 重试后仍有 {still_missing} 条 category/norm_class 不完整")

    # 应用分类结果
    final_indicators = []
    for idx, ind, ot in indicators_with_ot:
        update: dict = {}
        if ot:
            update["object_type"] = ot
        if idx in cls_map:
            cat, norm, ind_type = cls_map[idx]
            update["indicator_category"] = cat
            if norm:
                update["norm_class"] = norm
            if ind_type:
                update["indicator_type"] = ind_type
        final_indicators.append(ind.model_copy(update=update) if update else ind)

    classified = sum(1 for idx, _, _ in indicators_with_ot if idx in cls_map)
    logger.info(
        f"[分类] {standard_no} 完成，覆盖 {classified}/{len(indicators)} 条，"
        f"object_type 分布：{ {k: len(v) for k, v in groups.items()} }"
    )
    return final_indicators, obj_type_map


async def _classify_tests(
        standard_no: str,
        standard_name: str,
        std_context: str,
        tests: List[TestItem],
        obj_type_map: dict[str, str] | None = None,
) -> List[TestItem]:
    """
    两步分类（试验）：
      Step 1 — 判断每个不同 standard_object 的 object_type（优先复用指标分类已建立的映射）
      Step 2 — 按 object_type 分组，并行批量分类 indicator_category
    """
    import asyncio

    if not tests:
        return tests

    unique_objects = list(dict.fromkeys(t.standard_object for t in tests if t.standard_object))
    if not unique_objects:
        logger.warning(f"[试验分类] {standard_no} 所有试验的 standard_object 为空，跳过分类")
        return tests

    # Step 1：优先复用指标分类已建立的 obj_type_map，避免重复判断导致结果不一致
    if obj_type_map is None:
        obj_list_str = "\n".join(f"- {o}" for o in unique_objects)
        step1_message = (
                f"标准编号：{standard_no}"
                + (f"（{standard_name}）" if standard_name else "")
                + std_context
                + f"\n\n需要判断以下 {len(unique_objects)} 个标准化对象的类型：\n{obj_list_str}"
        )
        try:
            mapping_result = cast(ObjectTypeMappingOutput, await get_object_type_agent().ainvoke(
                {"messages": [{"role": "user", "content": step1_message}]},
                config={"configurable": {"thread_id": str(uuid.uuid4())}},
            ))
            obj_type_map = {}
            for item in mapping_result.mappings:
                normalized = _normalize_object_type(item.object_type)
                if normalized:
                    obj_type_map[item.standard_object] = normalized
        except Exception as e:
            logger.warning(f"[试验分类-Step1] {standard_no} object_type 判断失败，跳过: {e}")
            return tests
    else:
        logger.info(f"[试验分类-Step1] {standard_no} 复用指标分类的 obj_type_map，跳过重新判断")

    tests_with_ot: List[tuple[int, TestItem, str]] = [
        (idx, t, obj_type_map.get(t.standard_object, "")) for idx, t in enumerate(tests)
    ]
    groups: dict[str, List[tuple[int, TestItem]]] = {}
    for idx, t, ot in tests_with_ot:
        groups.setdefault(ot, []).append((idx, t))

    async def _run_test_batch(object_type: str, batch: List[tuple[int, TestItem]]) -> dict[int, str]:
        """运行单个批次，返回 {index: indicator_category}"""
        if not object_type:
            return {}
        lines = []
        for idx, t in batch:
            so = f', standard_object="{t.standard_object}"' if t.standard_object else ""
            acc = f', acceptance="{t.acceptance[:80]}"' if t.acceptance else ""
            lines.append(f'[{idx}] test_name="{t.test_name}"{so}{acc}, source_clause="{t.source_clause}"')
        batch_message = (
                f"标准编号：{standard_no}"
                + (f"（{standard_name}）" if standard_name else "")
                + f"\n\n以下 {len(batch)} 条试验均属于【{object_type}】，请为每条指定 indicator_category 和 norm_class：\n\n"
                + "\n".join(lines)
        )
        try:
            result = cast(BatchClassificationOutput, await get_batch_cls_agent(object_type).ainvoke(
                {"messages": [{"role": "user", "content": batch_message}]},
                config={"configurable": {"thread_id": str(uuid.uuid4())}},
            ))
            return {item.index: item.indicator_category for item in result.classifications if item.indicator_category}
        except Exception as e:
            logger.warning(f"[试验分类-Step2] {standard_no} 批次失败（{object_type}，{len(batch)} 条）: {e}")
            return {}

    tasks_list = [
        (ot, groups[ot][i: i + _BATCH_SIZE])
        for ot in groups
        for i in range(0, len(groups[ot]), _BATCH_SIZE)
    ]
    logger.info(f"[试验分类-Step2] {standard_no} 共 {len(tasks_list)} 个批次，并行执行...")
    batch_results = await asyncio.gather(*[_run_test_batch(ot, batch) for ot, batch in tasks_list])

    cls_map: dict[int, str] = {}
    for br in batch_results:
        cls_map.update(br)

    # 重试：indicator_category 或 norm_class 任一为空都重试，最多 3 次
    _MAX_CLS_RETRIES = 3
    for _retry in range(_MAX_CLS_RETRIES):
        missing_tests = [
            (idx, t, ot) for idx, t, ot in tests_with_ot
            if ot and (idx not in cls_map or not cls_map[idx])
        ]
        if not missing_tests:
            break
        for idx, _, _ in missing_tests:
            cls_map.pop(idx, None)
        logger.warning(f"[试验分类-Step2] {standard_no} 第{_retry + 1}次重试，{len(missing_tests)} 条 category 不完整")
        retry_groups: dict[str, List[tuple[int, TestItem]]] = {}
        for idx, t, ot in missing_tests:
            retry_groups.setdefault(ot, []).append((idx, t))
        retry_tasks = [
            (ot, retry_groups[ot][i: i + _BATCH_SIZE])
            for ot in retry_groups
            for i in range(0, len(retry_groups[ot]), _BATCH_SIZE)
        ]
        retry_results = await asyncio.gather(*[_run_test_batch(ot, batch) for ot, batch in retry_tasks])
        for br in retry_results:
            cls_map.update(br)

    still_missing = sum(1 for idx, _, ot in tests_with_ot if ot and (idx not in cls_map or not cls_map[idx]))
    if still_missing:
        logger.warning(f"[试验分类-Step2] {standard_no} 重试后仍有 {still_missing} 条 category 不完整")

    final_tests = []
    for idx, t, ot in tests_with_ot:
        update: dict = {}
        if ot:
            update["object_type"] = ot
        if idx in cls_map:
            update["indicator_category"] = cls_map[idx]
        final_tests.append(t.model_copy(update=update) if update else t)

    classified = sum(1 for idx, _, _ in tests_with_ot if idx in cls_map)
    logger.info(f"[试验分类] {standard_no} 完成，覆盖 {classified}/{len(tests)} 条")
    return final_tests


async def extract_indicators(
        standard_no: str,
        standard_name: str = "",
) -> ExtractionOutput:
    """
    从标准正文中提取指标与试验，返回结构化清单。
    流程：主 agent 分组提取 → 指标引用补全 → 指标分类 → indicator_type 推导 → 去重
                            → 试验引用补全 → 试验分类 → 指标-试验关联。
    调用方负责将结果写入 standard_cache_ind / standard_cache_test。
    """
    # ── 1. 创建累积器和提交工具 ──────────────────────────────────────────────────
    accumulated: List[IndicatorItem] = []
    accumulated_tests: List[TestItem] = []
    submit_tool = _make_submit_tool(accumulated)
    submit_tests_tool = _make_submit_tests_tool(accumulated_tests)

    # ── 2. 主 agent 分组提取 ────────────────────────────────────────────────────
    agent = _create_extraction_agent(submit_tool=submit_tool, submit_tests_tool=submit_tests_tool)
    user_message = (
            f"请提取以下标准的全部技术指标与试验：\n"
            f"标准编号：{standard_no}"
            + (f"（{standard_name}）" if standard_name else "")
    )

    summary = cast(ExtractionSummary, await agent.ainvoke(
        {"messages": [{"role": "user", "content": user_message}]},
        config={"configurable": {"thread_id": str(uuid.uuid4())}},
    ))

    standard_structure_type = summary.standard_structure_type if summary.standard_structure_type in _STANDARD_TYPES else ""

    logger.info(
        f"[提取] {standard_no} 主 agent 完成，{summary.group_count} 组，"
        f"指标 {len(accumulated)} 条，试验 {len(accumulated_tests)} 条，standard_structure_type={standard_structure_type or 'unknown'}"
    )

    # ── 3. 读取标准元数据（供分类 agent 使用）────────────────────────────────────
    std_context = ""
    try:
        from app.models.standard.base_info import StandardBaseInfo
        row = await StandardBaseInfo.filter(standard_no=standard_no).first()
        if row:
            if row.cname:
                std_context += f"\n标准全称：{row.cname.strip()}"
            if row.use_range:
                std_context += f"\n适用范围：{row.use_range.strip()[:200]}"
    except Exception:
        pass

    # ── 4. 去重（指标）────────────────────────────────────────────────────────────
    before_dedup = len(accumulated)
    accumulated = _deduplicate(accumulated)
    if before_dedup != len(accumulated):
        logger.info(f"[提取-去重] {standard_no} 去重 {before_dedup} → {len(accumulated)} 条")

    # ── 5. 指标引用补全 ──────────────────────────────────────────────────────────
    indicators = await _resolve_references(standard_no, accumulated)

    # ── 5.5 对象整理 ─────────────────────────────────────────────────────────────
    indicators, accumulated_tests = await _correct_objects(standard_no, standard_name, std_context, indicators,
                                                           accumulated_tests)

    # ── 6. 指标分类 ──────────────────────────────────────────────────────────────
    indicators, obj_type_map = await _classify_indicators(standard_no, standard_name, std_context, indicators)

    # ── 8. 去重（试验）──────────────────────────────────────────────────────────
    before_dedup_tests = len(accumulated_tests)
    accumulated_tests = _deduplicate_tests(accumulated_tests)
    if before_dedup_tests != len(accumulated_tests):
        logger.info(f"[提取-去重] {standard_no} 试验去重 {before_dedup_tests} → {len(accumulated_tests)} 条")

    # ── 8.5 试验引用补全 ──────────────────────────────────────────────────────────
    tests = await _resolve_test_references(standard_no, accumulated_tests)

    # ── 9. 试验分类 ──────────────────────────────────────────────────────────────
    tests = await _classify_tests(standard_no, standard_name, std_context, tests, obj_type_map=obj_type_map)

    if not standard_structure_type:
        if indicators and tests:
            standard_structure_type = "has_ind_and_test"
        elif indicators:
            standard_structure_type = "has_ind_only"
        elif tests:
            standard_structure_type = "has_test_only"

    return ExtractionOutput(
        standard_structure_type=standard_structure_type,
        indicators=indicators,
        tests=tests,
    )


__all__ = [
    "extract_indicators",
    "get_extraction_agent",
    "get_object_correction_agent",
    "ObjectCorrectionOutput",
    "ObjectCorrectionItem",
    "get_batch_cls_agent",
    "get_resolver_agent",
    "get_test_resolver_agent",
    "ExtractionOutput",
    "ExtractionSummary",
    "TestItem",
    "ResolvedTestItem",
    "TestResolverOutput",
    "ObjectTypeMappingOutput",
    "ObjectTypeMappingItem",
    "BatchClassificationOutput",
    "IndicatorClassificationItem",
    "ResolverOutput",
    "IndicatorItem",
    "ResolvedIndicatorItem",
]
