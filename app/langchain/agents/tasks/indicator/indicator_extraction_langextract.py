"""
标准指标提取 - LangExtract 版本

基于 Google LangExtract 的精准提取，结合 indicator_extraction_agent.py 的成熟经验。

流程：
1. 获取目录并过滤噪音章节
2. 识别标准化对象（从适用范围）
3. 使用分组规划 Agent 智能分组章节
4. 分组提取（传入标准化对象作为上下文）
5. 简单后处理（去重、补充章节信息）
"""
from __future__ import annotations

import re
import textwrap
from typing import List, Optional, Dict, Tuple

import aiomysql
from loguru import logger
from pydantic import BaseModel, Field

try:
    import langextract as lx
except ImportError:
    raise ImportError("langextract 未安装")

from app.langchain.tools.langextract_tool import LangExtractTool
from app.langchain.agents.tasks.indicator.indicator_extraction_agent import (
    IndicatorItem,
    TestItem,
    ExtractionOutput,
)
from app.langchain.agents.structured_agent import create_structured_qa_agent
from app.services.mysql_pool import standard_pool


# ── 本地模型定义（扩展版，增加 group_type）────────────────────────────────────

class PlannedGroupItem(BaseModel):
    """group-planner 输出的单个分组（扩展版）"""
    group_name: str = Field(description="分组名称，必须是具体试验/主题名称")
    chapter_nos: List[str] = Field(default_factory=list, description="本组章节号列表")
    group_type: str = Field(description="分组类型：indicator/test")
    reason: str = Field(default="", description="一句话说明该分组依据")
    test_names: List[str] = Field(default_factory=list, description="试验组中包含的具体试验名称列表（仅 test 组需要填写）")


class GroupPlanOutput(BaseModel):
    """group-planner 输出"""
    groups: List[PlannedGroupItem] = Field(default_factory=list, description="最终可执行分组列表")
    rejected_groups: List[str] = Field(default_factory=list, description="被判定非法并剔除的分组名")


# ── 分组规划 Agent Prompt ──────────────────────────────────────────────────────
_GROUP_PLANNER_PROMPT = """\
你是分组规划专家，负责将标准章节分为两组：指标组和试验组。

## 输入
- standard_no
- standard_name
- use_range（适用范围）
- standard_object / applicable_object
- 章节目录（title_no/title/word_count）

## 输出
必须通过 GroupPlanOutput 提交：
- groups: 固定两个分组
  1. {group_name: "指标组", chapter_nos: [...], group_type: "indicator", reason: "..."}
  2. {group_name: "试验组", chapter_nos: [...], group_type: "test", test_names: [...], reason: "..."}
- rejected_groups: 被排除的章节列表（包括章节号和标题）

**特别要求：**
对于试验组，必须分析章节标题，列出具体的试验名称到 test_names 字段中。
例如：章节标题包含"整车侧翻试验"、"车身截段侧翻试验"、"准静态负荷试验"
→ test_names: ["整车侧翻试验", "车身截段侧翻试验", "准静态负荷试验"]

## 分组规则

**指标组（group_type: "indicator"）：**
- 明确要求：必须是对当前标准化对象的描述和要求
- 包含：技术要求、性能指标、质量要求、外观要求、尺寸要求、材料要求、安全要求、标识/标志、包装、运输、贮存、检验规则等
- 章节特征：规定了产品/材料/部件应该满足的技术指标和要求
- 判断依据：该章节是否在描述标准化对象本身的属性和要求

**试验组（group_type: "test"）：**
- 明确要求：必须包含完整的试验方法描述，包括试验名称、方法、条件、准备、步骤、判据等要素
- 包含：试验方法、检验方法、测试方法、测定方法、试验步骤、检测程序、取样方法、试验条件、试验装置等
- 章节特征：描述了如何进行测试和检验的完整方法
- 判断依据：该章节是否提供了可执行的试验操作流程

**建议排除（但不强制）：**
- 适用范围（范围）
- 前言、引言
- 规范性引用文件
- 术语和定义
- 参考文献
- 资料性附录

**注意：**
1. 章节不强制必须归入某组，只要分组合理即可
2. 无法明确判断的章节可以不归入任何组（列入 rejected_groups）
3. 如果整篇标准只有指标或只有试验，另一组的 chapter_nos 可以为空
4. 输出时 groups 必须包含两个元素，顺序为：指标组、试验组
"""


class ChapterInfo(BaseModel):
    """章节信息"""
    title_no: str = Field(description="章节编号")
    title: str = Field(description="章节标题")
    word: str = Field(description="章节内容")
    word_count: int = Field(default=0, description="字数")


class StandardObject(BaseModel):
    """标准化对象"""
    standard_object: str = Field(description="标准化对象（产品/材料/部件）")
    applicable_object: str = Field(default="", description="适用对象（使用主体）")


async def _get_toc(standard_no: str) -> List[ChapterInfo]:
    """获取标准目录"""
    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cursor:
            sql = """
                SELECT a.title_no, a.title, a.word
                FROM standard_jgh_pdf_chapter a
                JOIN standard_jgh_pdf b ON b.main_task_id = a.main_task_id
                WHERE b.standard_no = %s
                ORDER BY a.id ASC
            """
            await cursor.execute(sql, (standard_no,))
            rows = await cursor.fetchall()

            chapters = []
            for row in rows:
                chapters.append(
                    ChapterInfo(
                        title_no=row["title_no"] or "",
                        title=row["title"] or "",
                        word=row["word"] or "",
                        word_count=len(row["word"] or ""),
                    )
                )

            logger.info(f"获取标准 {standard_no} 目录: {len(chapters)} 个章节")
            return chapters


def _create_group_planner_agent(pool_id: Optional[int] = None):
    """创建分组规划 Agent"""
    from app.langchain.tools.db_tools import make_db_tools

    logger.info("初始化分组规划 Agent...")

    return create_structured_qa_agent(
        output_schema=GroupPlanOutput,
        system_prompt=_GROUP_PLANNER_PROMPT,
        extra_tools=make_db_tools(pool_id),
        pool_id=pool_id,
    )


async def _plan_groups_with_agent(
    standard_no: str,
    standard_name: str,
    use_range: str,
    standard_obj: StandardObject,
    chapters: List[ChapterInfo],
) -> List[PlannedGroupItem]:
    """
    使用分组规划 Agent 生成分组方案

    Args:
        standard_no: 标准编号
        standard_name: 标准名称
        use_range: 适用范围
        standard_obj: 标准化对象
        chapters: 过滤后的章节列表

    Returns:
        分组列表
    """
    import uuid

    agent = _create_group_planner_agent()

    # 构建目录信息
    toc_lines = [f"{ch.title_no} {ch.title} (字数: {ch.word_count})" for ch in chapters]
    toc_text = "\n".join(toc_lines)

    # 构建描述
    description = f"""请为以下标准生成分组方案：

标准编号：{standard_no}
标准名称：{standard_name or '未知'}
适用范围：{use_range or '未知'}
标准化对象：{standard_obj.standard_object or '未知'}
适用对象：{standard_obj.applicable_object or '无'}

章节目录：
{toc_text}

请按规则输出两个分组：指标组和试验组。"""

    logger.info(f"调用分组规划 Agent，章节数: {len(chapters)}")

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": description}]},
        config={"configurable": {"thread_id": str(uuid.uuid4())}},
    )

    if isinstance(result, GroupPlanOutput):
        logger.info(
            f"分组规划完成: {len(result.groups)} 组, "
            f"拒绝 {len(result.rejected_groups)} 个非法分组"
        )
        # 打印每个分组的详细信息
        for idx, group in enumerate(result.groups):
            log_msg = (
                f"  分组 {idx+1}: {group.group_name}\n"
                f"    类型: {group.group_type}\n"
                f"    章节: {group.chapter_nos}\n"
            )
            if group.group_type == "test" and group.test_names:
                log_msg += f"    试验名称: {group.test_names}\n"
            log_msg += f"    原因: {group.reason}"
            logger.info(log_msg)
        if result.rejected_groups:
            logger.info(f"  拒绝的分组: {result.rejected_groups}")
        return result.groups
    else:
        logger.warning(f"分组规划返回格式异常: {type(result)}")
        return []


async def _extract_standard_object(
    tool: LangExtractTool,
    scope_text: str,
) -> StandardObject:
    """从适用范围提取标准化对象"""
    if not scope_text.strip():
        logger.warning("适用范围章节为空，无法提取标准化对象")
        return StandardObject(standard_object="", applicable_object="")

    logger.info(f"开始提取标准化对象，输入文本长度: {len(scope_text)} 字符")

    prompt = textwrap.dedent("""\
        从文本中提取标准化对象信息：
        - standard_object: 本标准规定的标准化对象（产品/材料/部件名称），必须是被规定的直接对象
        - applicable_object: 适用对象（使用主体、应用场景），若无明确说明则留空

        示例：
        - "本标准规定了XX钢板的技术要求" → standard_object="XX钢板", applicable_object=""
        - "本标准适用于航空航天用铝合金" → standard_object="铝合金", applicable_object="航空航天"
    """)

    examples = [
        lx.data.ExampleData(
            text="本标准规定了柴油的技术要求、试验方法、检验规则等。适用于由石油制取的柴油。",
            extractions=[
                lx.data.Extraction(
                    extraction_class="standard_object",
                    extraction_text="柴油",
                    attributes={
                        "standard_object": "柴油",
                        "applicable_object": "",
                    },
                ),
            ],
        ),
    ]

    try:
        # 使用 keep_attributes=True 保留原始 attributes
        # 使用 allow_ungrounded=True 允许未接地结果（标准化对象不需要字符位置）
        results = tool.extract(
            text=scope_text,
            prompt=prompt,
            examples=examples,
            max_workers=5,
            max_char_buffer=2000,
            extraction_passes=1,
            keep_attributes=True,
            allow_ungrounded=True,
        )

        logger.info(f"标准化对象提取: 共 {len(results)} 条")

        if results:
            result = results[0]
            attrs = result.attributes or {}
            logger.info(f"  提取文本: {result.source_text}")
            logger.info(f"  attributes: {attrs}")

            obj = StandardObject(
                standard_object=attrs.get("standard_object", ""),
                applicable_object=attrs.get("applicable_object", ""),
            )
            logger.info(f"✓ 提取标准化对象: standard_object='{obj.standard_object}', applicable_object='{obj.applicable_object}'")
            return obj
        else:
            logger.warning("未提取到标准化对象")
            return StandardObject(standard_object="", applicable_object="")

    except Exception as e:
        logger.warning(f"提取标准化对象失败: {e}")
        return StandardObject(standard_object="", applicable_object="")


def _build_indicator_prompt(standard_object: str, applicable_object: str) -> str:
    """构建指标提取 prompt"""
    context_info = ""
    if standard_object:
        context_info += f"\n标准化对象：{standard_object}"
    if applicable_object:
        context_info += f"\n适用对象：{applicable_object}"

    return textwrap.dedent(f"""\
        从标准文本中提取所有技术指标，包括量化指标和非量化指标。{context_info}

        每个指标必须包含以下字段：
        - indicator_name: 指标名称（如"外观"、"硫含量"、"抗拉强度"）
        - requirement: 完整的指标要求，必须包含所有限定条件，确保指标独立、健全、自洽
          * 量化指标：保留完整的限值、单位、条件（如"应不大于0.05%（质量分数）"）
          * 非量化指标：保留完整的描述性要求（如"应为无色或淡黄色透明液体，无可见机械杂质"）

        关键原则：
        1. requirement 必须是完整、自洽的陈述，脱离上下文也能理解
        2. 不要将限值、单位、条件拆分到不同字段
        3. 使用原文精确措辞，不要改写或省略
        4. 必须排除：术语定义、测试参数本身（仪器精度、测量步骤）、空泛原则、管理性要求
        5. 按出现顺序排列""")


def _build_indicator_examples() -> List[lx.data.ExampleData]:
    """构建指标提取示例"""
    example_text_1 = "4.3.1 外观：产品应为无色或淡黄色透明液体，无可见机械杂质。"
    example_text_2 = "4.3.2 硫含量应不大于0.05%（质量分数），按GB/T 380规定的方法进行测定。"
    example_text_3 = "4.3.6 馏程：初馏点不低于280℃，终馏点不高于365℃，按GB/T 6536规定的方法进行测定。"

    return [
        lx.data.ExampleData(
            text=example_text_1,
            extractions=[
                lx.data.Extraction(
                    extraction_class="indicator",
                    extraction_text="产品应为无色或淡黄色透明液体，无可见机械杂质",
                    attributes={
                        "indicator_name": "外观",
                        "requirement": "产品应为无色或淡黄色透明液体，无可见机械杂质",
                    },
                ),
            ],
        ),
        lx.data.ExampleData(
            text=example_text_2,
            extractions=[
                lx.data.Extraction(
                    extraction_class="indicator",
                    extraction_text="硫含量应不大于0.05%（质量分数）",
                    attributes={
                        "indicator_name": "硫含量",
                        "requirement": "应不大于0.05%（质量分数）",
                    },
                ),
            ],
        ),
        lx.data.ExampleData(
            text=example_text_3,
            extractions=[
                lx.data.Extraction(
                    extraction_class="indicator",
                    extraction_text="初馏点不低于280℃，终馏点不高于365℃",
                    attributes={
                        "indicator_name": "馏程",
                        "requirement": "初馏点不低于280℃，终馏点不高于365℃",
                    },
                ),
            ],
        ),
    ]


def _build_test_prompt(test_names: Optional[List[str]] = None) -> str:
    """构建试验方法提取 prompt"""

    # 构建试验名称提示
    test_names_hint = ""
    if test_names:
        test_names_hint = f"\n预期的试验名称列表：{', '.join(test_names)}\n请重点关注这些试验的提取。\n"

    return textwrap.dedent(f"""\
        从试验方法章节中提取完整的试验条目。{test_names_hint}
        每个试验必须包含以下字段：
        - test_name: 试验名称，不含"试验"后缀（如"硫含量测定"、"抗拉强度"）
        - method_desc: 试验方法描述（试样制备、计算方法等）
        - conditions: 试验条件（温度、湿度、电压等环境参数，若无则留空）
        - preparation: 试验准备（试样处理、仪器校准、系统搭建，若无则留空）
        - procedure: 试验过程（按逻辑次序的操作步骤）
        - acceptance: 试验要求/合格判据（若无则留空）
        - report_items: 报告内容（应报告的项目，若无则留空）

        关键原则：
        1. 尽可能提取完整的试验方法描述
        2. 使用原文精确措辞，不要概括
        3. 如果某个字段在原文中没有，留空即可
        4. 按出现顺序排列""")


def _build_test_examples() -> List[lx.data.ExampleData]:
    """构建试验方法提取示例"""
    example_text = textwrap.dedent("""\
        6.3 硫含量测定

        6.3.1 方法
        按GB/T 380规定的方法进行测定。

        6.3.2 原理
        采用氧化燃烧法，将试样在氧气流中燃烧，硫转化为二氧化硫，用碘量法测定。

        6.3.3 试验条件
        试验应在温度15℃~25℃、相对湿度不大于85%的环境下进行。

        6.3.4 试验步骤
        a) 称取试样约2g，精确至0.001g；
        b) 将试样置于燃烧管中，通入氧气；
        c) 点燃试样，收集产生的二氧化硫；
        d) 用碘标准溶液滴定至终点。

        6.3.5 结果判定
        硫含量应不大于0.05%（质量分数）。

        6.3.6 试验报告
        试验报告应包括：试样信息、试验日期、测定结果、试验人员。
    """)

    return [
        lx.data.ExampleData(
            text=example_text,
            extractions=[
                lx.data.Extraction(
                    extraction_class="test",
                    extraction_text="硫含量测定",
                    attributes={
                        "test_name": "硫含量测定",
                        "method_desc": "按GB/T 380规定的方法进行测定。采用氧化燃烧法，将试样在氧气流中燃烧，硫转化为二氧化硫，用碘量法测定",
                        "conditions": "试验应在温度15℃~25℃、相对湿度不大于85%的环境下进行",
                        "preparation": "",
                        "procedure": "a) 称取试样约2g，精确至0.001g；b) 将试样置于燃烧管中，通入氧气；c) 点燃试样，收集产生的二氧化硫；d) 用碘标准溶液滴定至终点",
                        "acceptance": "硫含量应不大于0.05%（质量分数）",
                        "report_items": "试验报告应包括：试样信息、试验日期、测定结果、试验人员",
                    },
                ),
            ],
        ),
    ]


def _merge_chapters_text(chapters: List[ChapterInfo]) -> Tuple[str, Dict[int, str]]:
    """
    合并章节文本

    Returns:
        (合并后的文本, {char_offset: chapter_title} 映射)
    """
    full_text = ""
    chapter_offsets = {}

    for chapter in chapters:
        # 检查 title 是否已包含 title_no
        title_no = chapter.title_no.strip()
        title = chapter.title.strip()

        if title_no and title.startswith(title_no):
            # title 已包含 title_no，直接使用 title
            chapter_title = title
        elif title_no:
            # title 不包含 title_no，组合使用
            chapter_title = f"{title_no} {title}"
        else:
            # 没有 title_no，只用 title
            chapter_title = title

        chapter_offsets[len(full_text)] = chapter_title
        chapter_text = chapter.word or ""

        # 如果章节内容为空，记录警告
        if not chapter_text.strip():
            logger.warning(f"章节 {chapter_title} 内容为空 (word 字段为空)")

        full_text += f"\n{chapter_title}\n{chapter_text}\n"

    return full_text, chapter_offsets


def _find_chapter_for_position(
    char_pos: int,
    chapter_offsets: Dict[int, str]
) -> Optional[str]:
    """根据字符位置找到所属章节"""
    sorted_offsets = sorted(chapter_offsets.keys())
    chapter_title = None

    for offset in sorted_offsets:
        if offset <= char_pos:
            chapter_title = chapter_offsets[offset]
        else:
            break

    return chapter_title


async def extract_indicators_langextract(
    standard_no: str,
    standard_name: str = "",
    max_workers: int = 30,
    max_char_buffer: int = 1000,
    extraction_passes: int = 3,
    enable_debug: bool = False,
) -> ExtractionOutput:
    """
    使用 LangExtract 提取标准指标

    Args:
        standard_no: 标准编号
        standard_name: 标准名称（可选）
        max_workers: 并行线程数
        max_char_buffer: 分块大小
        extraction_passes: 提取轮次
        enable_debug: 是否启用调试日志

    Returns:
        ExtractionOutput(indicators, tests, standard_structure_type)
    """
    logger.info(f"开始 LangExtract 提取: {standard_no}")

    # 初始化工具
    tool = LangExtractTool(enable_debug_logging=enable_debug)

    # Step 1: 获取目录
    chapters = await _get_toc(standard_no)
    if not chapters:
        raise ValueError(f"标准 {standard_no} 无章节数据")

    # Step 2: 提取适用范围章节（章节号为"1"且标题包含"范围"）
    scope_chapter = None
    scope_text = ""
    for chapter in chapters:
        # 判断是否为第1章（章节号为"1"或"1."开头）
        if chapter.title_no.strip() in ("1", "1.") or chapter.title_no.startswith("1 "):
            if "范围" in chapter.title:
                scope_chapter = chapter
                scope_text = chapter.word
                logger.info(f"找到适用范围章节: {chapter.title_no} {chapter.title}")
            else:
                logger.warning(f"第1章标题不包含'范围': {chapter.title_no} {chapter.title}")
            break

    # Step 3: 识别标准化对象（适用范围存在时，结合标准名称和适用范围；否则只用标准名称）
    if scope_text.strip():
        # 结合标准名称和适用范围提取
        combined_text = f"标准名称：{standard_name}\n\n适用范围：{scope_text}"
        standard_obj = await _extract_standard_object(tool, combined_text)
        use_range = scope_text
    else:
        logger.warning("未找到适用范围章节，仅使用标准名称提取标准化对象")
        standard_obj = await _extract_standard_object(tool, f"标准名称：{standard_name}")
        use_range = ""

    # Step 4: 使用分组规划 Agent 智能分组（传入全部章节，由 agent 自行排除噪音章节）
    groups = await _plan_groups_with_agent(
        standard_no=standard_no,
        standard_name=standard_name,
        use_range=use_range,
        standard_obj=standard_obj,
        chapters=chapters,  # 传入全部章节
    )

    if not groups:
        logger.warning("分组规划失败，创建默认两组")
        groups = [
            PlannedGroupItem(
                group_name="指标组",
                chapter_nos=[ch.title_no for ch in chapters],
                group_type="indicator",
                reason="分组规划失败，使用默认分组"
            ),
            PlannedGroupItem(
                group_name="试验组",
                chapter_nos=[],
                group_type="test",
                reason="分组规划失败，试验组为空"
            )
        ]

    logger.info(f"分组方案: {len(groups)} 组")

    # 构建章节映射表（title_no -> ChapterInfo）
    chapter_map = {ch.title_no: ch for ch in chapters}

    # Step 5: 按分组提取指标和试验
    indicators = []
    tests = []

    for group_idx, group in enumerate(groups):
        group_name = group.group_name
        chapter_nos = group.chapter_nos
        group_type = group.group_type

        # 根据 chapter_nos 获取章节
        group_chapters = []
        for ch_no in chapter_nos:
            if ch_no in chapter_map:
                group_chapters.append(chapter_map[ch_no])
            else:
                logger.warning(f"分组 {group_name} 引用的章节 {ch_no} 不存在")

        if not group_chapters:
            logger.warning(f"分组 {group_name} 无有效章节，跳过")
            continue

        logger.info(
            f"处理分组 [{group_idx+1}/{len(groups)}]: {group_name} "
            f"(类型: {group_type}), {len(group_chapters)} 个章节"
        )

        # 合并章节文本
        full_text, chapter_offsets = _merge_chapters_text(group_chapters)

        # 根据 group_type 决定提取参数
        if group_type == "test":
            # 传递试验名称列表作为提示
            test_names_hint = group.test_names if hasattr(group, 'test_names') else []
            prompt = _build_test_prompt(test_names=test_names_hint)
            examples = _build_test_examples()
        elif group_type == "indicator":
            prompt = _build_indicator_prompt(
                standard_obj.standard_object,
                standard_obj.applicable_object
            )
            examples = _build_indicator_examples()
        else:
            logger.warning(f"未知的 group_type: {group_type}，跳过该分组")
            continue

        # 执行提取（使用 keep_attributes=True 以支持自定义字段）
        raw_extractions = tool.extract(
            text=full_text,
            prompt=prompt,
            examples=examples,
            max_workers=max_workers,
            max_char_buffer=max_char_buffer,
            extraction_passes=extraction_passes,
            keep_attributes=True,
        )

        logger.info(f"  提取结果: {len(raw_extractions)} 条")

        # 转换为对应的数据模型
        for ext in raw_extractions:
            attrs = ext.attributes or {}
            chapter_title = None
            if ext.char_start is not None:
                chapter_title = _find_chapter_for_position(ext.char_start, chapter_offsets)

            if group_type == "test":
                tests.append(
                    TestItem(
                        test_name=attrs.get("test_name", ""),
                        method_desc=attrs.get("method_desc", ""),
                        conditions=attrs.get("conditions", ""),
                        preparation=attrs.get("preparation", ""),
                        procedure=attrs.get("procedure", ""),
                        acceptance=attrs.get("acceptance", ""),
                        report_items=attrs.get("report_items", ""),
                        standard_object=standard_obj.standard_object,
                        applicable_object=standard_obj.applicable_object,
                        source_clause=chapter_title or "",
                    )
                )
            else:  # indicator
                indicators.append(
                    IndicatorItem(
                        indicator_object=attrs.get("indicator_name", ""),
                        source_value=attrs.get("requirement", ""),
                        standard_object=standard_obj.standard_object,
                        applicable_object=standard_obj.applicable_object,
                        source_clause=chapter_title or "",
                    )
                )

    logger.info(
        f"LangExtract 提取完成: {standard_no}, "
        f"指标 {len(indicators)} 条, 试验 {len(tests)} 条"
    )

    return ExtractionOutput(
        standard_structure_type="",
        indicators=indicators,
        tests=tests,
    )


__all__ = [
    "extract_indicators_langextract",
]
