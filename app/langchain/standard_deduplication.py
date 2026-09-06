"""
标准查重 Chain

结合全文检索和 LLM 分析，判断标准是否重复
"""

from typing import Dict, Any, List, Optional

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.runnables import RunnableLambda
from loguru import logger
from pydantic import BaseModel, Field

from app.langchain.llm_providers import get_llm


def is_same_series(standard_no1: Optional[str], standard_no2: Optional[str]) -> bool:
    """
    判断两个标准编号是否属于同一系列

    同系列标准的判断规则：
    - GB/T 11313.24-2009 和 GB/T 11313.25-2009 属于同系列
    - GB/T 11313.24-2009 和 GB/T 11313-2009 属于同系列
    - 提取标准编号的基础部分（去掉点号后的部分和年份）进行比较

    Examples:
        >>> is_same_series("GB/T 11313.24-2009", "GB/T 11313.25-2009")
        True
        >>> is_same_series("GB/T 27001-2022", "GB/T 28001-2011")
        False
    """
    import re

    if not standard_no1 or not standard_no2:
        return False

    def extract_base_number(std_no: str) -> str:
        """提取标准编号的基础部分"""
        # 去除空格
        std_no = std_no.strip()

        # 匹配模式：GB/T 11313.24-2009 或 GB/T 11313-2009
        # 提取 GB/T 11313 这部分
        match = re.match(r'^([A-Z]+(?:/[A-Z]+)?)\s*(\d+)(?:\.\d+)?(?:-\d+)?', std_no, re.IGNORECASE)
        if match:
            prefix = match.group(1)  # GB/T
            base_num = match.group(2)  # 11313
            return f"{prefix} {base_num}".upper()

        return std_no.upper()

    base1 = extract_base_number(standard_no1)
    base2 = extract_base_number(standard_no2)

    return base1 == base2 and base1 != standard_no1.upper()  # 确保不是完全相同的编号


# 标签定义 - 用于评判标准之间的关系
class SimilarityTags(BaseModel):
    """相似度标签（不包括同系列标准，该标签由系统硬编码判断）"""
    标准化对象一致: bool = Field(description="标准化的核心对象/主题是否完全相同或高度一致")
    通用专用关系: bool = Field(description="一个是通用标准，另一个是针对特定领域/产品的专用标准")
    适用范围重叠: bool = Field(description="两个标准在实际应用中是否可能被同一个主体同时采用或实施（有实质性的用户群体交集和应用场景重叠）")


class SimilarStandardAnalysis(BaseModel):
    """单个相似标准的分析结果"""
    standard_id: str = Field(description="标准ID")
    standard_no: Optional[str] = Field(description="标准编号")
    standard_name: str = Field(description="标准名称")
    tags: SimilarityTags = Field(description="相似度标签")
    关系说明: str = Field(description="该标准与输入标准的关系说明（50字内）")


class DeduplicationAnalysis(BaseModel):
    """查重分析结果 - 基于标签的评判"""
    相似标准分析: List[SimilarStandardAnalysis] = Field(description="每个相似标准的标签分析")


async def fulltext_search_step(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    步骤1：MySQL 全文检索相似标准（替代向量检索）
    """
    import aiomysql

    from app.services.mysql_pool import standard_pool

    input_name = inputs.get("input_name", "")
    input_use_range = inputs.get("input_use_range", "")
    standard_no = inputs.get("standard_no")

    logger.info(f"开始查重：{input_name} (编号: {standard_no or '无'})")

    similar_results: List[tuple] = []
    try:
        sql = """
              SELECT id,
                     standard_no,
                     cname,
                     use_range, MATCH (cname, use_range) AGAINST (
                     %s IN BOOLEAN MODE) AS score
              FROM standard_base_info
              WHERE MATCH (cname
                  , use_range) AGAINST (%s IN BOOLEAN MODE)
                AND (%s IS NULL
                 OR standard_no != %s)
              ORDER BY score DESC
                  LIMIT 100
              """
        async with standard_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, [input_name, input_name, standard_no, standard_no])
                rows = list(await cur.fetchall())

        max_score = max((r[4] for r in rows), default=1) or 1
        for row in rows:
            metadata = {"id": str(row[0]), "standard_no": row[1], "cname": row[2], "use_range": row[3]}
            score = round(float(row[4]) / max_score, 4)
            similar_results.append((metadata, score))

    except Exception as e:
        logger.error(f"全文检索失败: {e}")

    logger.info(f"全文检索到 {len(similar_results)} 个相似标准")

    similar_standards_text = ""
    if similar_results:
        for i, (metadata, score) in enumerate(similar_results, 1):
            similar_standards_text += f"\n{i}. 标准ID：{metadata['id']}\n"
            similar_standards_text += f"   标准编号：{metadata.get('standard_no', '无')}\n"
            similar_standards_text += f"   标准名称：{metadata['cname']}\n"
            similar_standards_text += f"   适用范围：{metadata['use_range']}\n"
            similar_standards_text += f"   相关度：{score:.2%}\n"
    else:
        similar_standards_text = "未找到相似标准"

    return {
        **inputs,
        "similar_results": similar_results,
        "similar_standards_text": similar_standards_text,
    }


async def quick_screening_step(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    阶段1：粗筛 - 快速过滤低相关度标准

    让LLM快速判断哪些标准值得详细分析，过滤掉明显不相关的标准
    """
    similar_results = inputs["similar_results"]

    # 如果没有相似标准，直接返回
    if not similar_results:
        return {
            **inputs,
            "screened_results": [],
            "screened_standards_text": "未找到相似标准"
        }

    logger.info(f"🔍 阶段1-粗筛：开始快速筛选 {len(similar_results)} 个相似标准")

    try:
        llm = get_llm()

        # 构建粗筛提示词 - 简洁高效
        screening_prompt = f"""你是标准相似度初筛专家。请快速判断以下相似标准中，哪些与输入标准有较高关联度。

**输入标准：**
- 标准名称：{inputs['input_name']}
- 适用范围：{inputs.get('input_use_range', '无')}

**待筛选的相似标准：**
{inputs['similar_standards_text']}

**筛选标准（满足任一条件即保留）：**
1. 标准化对象相同或高度相似（如同一产品、同一技术）
2. 存在通用-专用关系（一个是通用标准，另一个是行业/领域专用版本）
3. 实际应用中会被同一主体同时采用
4. 同系列标准（如GB/T 11313.1、GB/T 11313.2）

**排除标准：**
- 仅仅是同一技术领域但对象完全不同（如"螺栓"vs"螺母"）
- 关键词相似但实质内容无关
- 纯理论关联，实际应用场景不重叠

**输出要求：**
返回JSON格式的标准ID列表，只包含值得详细分析的标准。如果没有高相关度标准，返回空数组。

格式示例：
{{"selected_ids": ["id1", "id2", "id3"]}}

**注意：明显不相关的要果断排除。**"""

        # 定义粗筛结果的数据模型
        class ScreeningResult(BaseModel):
            selected_ids: List[str] = Field(description="筛选出的标准ID列表")

        # 使用Agent API进行粗筛
        agent = create_agent(
            model=llm,
            tools=[],
            response_format=ToolStrategy(
                schema=ScreeningResult,
                handle_errors=True
            ),
            system_prompt="你是标准相似度初筛专家，快速准确地识别高相关度标准。"
        )

        agent_result = await agent.ainvoke({
            "messages": [{"role": "user", "content": screening_prompt}]
        })

        screening_result: ScreeningResult = agent_result["structured_response"]
        selected_ids = set(screening_result.selected_ids)

        # 过滤出被选中的标准
        screened_results = [
            (metadata, score)
            for metadata, score in similar_results
            if metadata["id"] in selected_ids
        ]

        logger.info(f"✅ 阶段1-粗筛完成：{len(similar_results)} → {len(screened_results)} 个标准")

        # 更新similar_standards_text（只包含筛选后的标准）
        screened_standards_text = ""
        if screened_results:
            for i, (metadata, score) in enumerate(screened_results, 1):
                similar_std_no = metadata.get('standard_no', '无')
                screened_standards_text += f"\n{i}. 标准ID：{metadata['id']}\n"
                screened_standards_text += f"   标准编号：{similar_std_no}\n"
                screened_standards_text += f"   标准名称：{metadata['cname']}\n"
                screened_standards_text += f"   适用范围：{metadata['use_range']}\n"
                screened_standards_text += f"   向量相似度：{score:.2%}\n"
        else:
            screened_standards_text = "粗筛后无高相关度标准"

        return {
            **inputs,
            "screened_results": screened_results,
            "screened_standards_text": screened_standards_text
        }

    except Exception as e:
        logger.error(f"❌ 粗筛步骤失败: {e}")
        logger.exception(e)
        # 降级处理：保留所有标准
        return {
            **inputs,
            "screened_results": similar_results,
            "screened_standards_text": inputs["similar_standards_text"]
        }


async def llm_analysis_step(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    阶段2：细筛 - LLM 基于标签的详细分析

    使用 Agent API + ToolStrategy 实现自动错误处理和重试
    """
    # 改为使用粗筛后的结果
    screened_results = inputs.get("screened_results", inputs["similar_results"])
    screened_standards_text = inputs.get("screened_standards_text", inputs["similar_standards_text"])

    # 如果没有相似标准，直接返回
    if not screened_results:
        return {
            **inputs,
            "llm_result": {
                "need_attention": False,
                "相似标准分析": []
            }
        }

    # 调用 LLM - 使用 Agent API
    try:
        llm = get_llm()

        logger.info(f"🔬 阶段2-细筛：开始详细标签分析 {len(screened_results)} 个标准")

        # 构建用户消息
        user_message = f"""**输入标准：**
- 标准名称：{inputs['input_name']}
- 适用范围：{inputs.get('input_use_range', '无')}

**检索到的相似标准：**
{screened_standards_text}

**请分析：**
为每个相似标准打上合适的标签，并说明关系。
**注意：只返回相似程度非常高的标准，关联度一般的标准请直接忽略，不要包含在结果中。**"""

        # system prompt
        system_prompt = """你是标准相似度分析专家。为每个相似标准打上精准的标签。

**标签体系（必须极其严格判定）：**

1. **标准化对象一致**（最严格！）：核心对象/主题是否完全相同
   - ✅ 只有针对完全相同的具体对象、产品或技术才标记 true
   - ❌ 以下情况绝对不算对象一致：
     * **通用 vs 专用**（如"通用驱动器" vs "注塑机专用驱动器" - 这应标记为"通用专用关系"）
     * 同领域不同对象（如"螺栓" vs "螺母"）
     * 不同方面（如"产品要求" vs "试验方法" - 虽然针对同一产品，但关注点不同）
   - 例：✅ "电动汽车充电桩技术要求" vs "电动汽车充电桩安全规范" ｜ ❌ "软件测试" vs "软件开发"

2. **通用专用关系**：一个是通用标准，另一个是针对特定领域/产品/行业的专用标准
   - ✅ 专用标准是对通用标准在某个方向上的具体化/细化
   - ⚠️ **互斥规则：如果标记为"通用专用关系"，则"标准化对象一致"必须为 false**
   - 例：✅ "通用驱动器标准" vs "注塑机专用驱动系统标准"
   - 例：✅ "信息安全管理要求" vs "医疗行业信息安全管理要求"
   - 例：✅ "通用螺栓标准" vs "汽车用高强度螺栓标准"

3. **适用范围重叠**：同一主体是否会同时采用两个标准
   - ✅ 只有实际应用中同一企业/组织会同时使用才标记 true
   - ❌ 理论可能性不算，必须是实际常见情况
   - 例：✅ "建筑施工安全" vs "建筑质量验收" ｜ ❌ "汽车检测" vs "电子产品检测"

**核心原则（宁缺毋滥）：**
- 所有标签必须极其严格判定，有疑问时标记 false
- "对象一致" 必须是完全相同的对象，通用与专用不算对象一致
- "通用专用关系" 强调一个宽泛、一个具体
- **"通用专用关系" 与 "标准化对象一致" 互斥，不能同时为 true**
- "范围重叠" ≠ 理论可能，必须实际常用

请准确分析每个相似标准。"""

        # 创建 Agent，使用 ToolStrategy（现在兼容 Qwen 了！）
        agent = create_agent(
            model=llm,
            tools=[],  # 不需要额外工具
            response_format=ToolStrategy(
                schema=DeduplicationAnalysis,
                handle_errors=True  # 🎯 自动捕获验证错误并重试！
            ),
            system_prompt=system_prompt
        )

        # 调用 agent
        agent_result = await agent.ainvoke({
            "messages": [{"role": "user", "content": user_message}]
        })

        # 从 agent 结果中提取结构化响应
        result: DeduplicationAnalysis = agent_result["structured_response"]

        logger.info(f"LLM 分析成功，相似标准数: {len(result.相似标准分析)}")

        # 获取输入标准编号（用于判断同系列标准）
        input_standard_no = inputs.get("standard_no")

        # 构建完整的相似标准分析列表（添加硬编码的同系列标准标签）
        complete_analysis = []
        for item in result.相似标准分析:
            # 硬编码判断是否同系列标准
            is_series = is_same_series(input_standard_no, item.standard_no)

            # 创建包含完整标签的字典
            complete_item = {
                "standard_id": item.standard_id,
                "standard_no": item.standard_no,
                "standard_name": item.standard_name,
                "tags": {
                    "同系列标准": is_series,
                    "标准化对象一致": item.tags.标准化对象一致,
                    "通用专用关系": item.tags.通用专用关系,
                    "适用范围重叠": item.tags.适用范围重叠,
                },
                "关系说明": item.关系说明,
                # 保存原始对象以便后续判断
                "_tags_obj": item.tags,
                "_is_series": is_series
            }
            complete_analysis.append(complete_item)

        # 硬编码计算是否需要关注（基于完整的标签信息）
        need_attention = False
        for item in complete_analysis:
            tags = item["tags"]

            # 判断规则：标准化对象一致 + 适用范围重叠 + 非同系列标准 + 非通用专用关系
            if (tags["标准化对象一致"] and
                tags["适用范围重叠"] and
                not tags["同系列标准"] and
                not tags["通用专用关系"]):
                need_attention = True
                break

        # 转换为字典（移除临时字段）
        llm_result_dict = {
            "need_attention": need_attention,
            "相似标准分析": [
                {
                    "standard_id": item["standard_id"],
                    "standard_no": item["standard_no"],
                    "standard_name": item["standard_name"],
                    "tags": item["tags"],
                    "关系说明": item["关系说明"]
                }
                for item in complete_analysis
            ]
        }

        logger.info(f"LLM 分析完成：需要关注={need_attention}，相似标准数={len(complete_analysis)}")

        return {
            **inputs,
            "llm_result": llm_result_dict
        }

    except Exception as e:
        logger.error(f"LLM 分析失败: {e}")
        logger.exception(e)

        # 降级处理：返回基础结构
        return {
            **inputs,
            "llm_result": {
                "need_attention": False,
                "相似标准分析": []
            }
        }


async def batch_llm_analysis(
    batch_inputs: List[Dict[str, Any]],
    max_concurrent: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    批量并发调用LLM进行分析

    Args:
        batch_inputs: 批量输入数据列表
        max_concurrent: 最大并发数，如果为None则从配置读取

    Returns:
        分析结果列表
    """
    import asyncio
    from asyncio import Semaphore
    from app.langchain.config import langchain_config

    # 如果未指定并发数，从配置读取
    if max_concurrent is None:
        max_concurrent = langchain_config.LLM_MAX_CONCURRENT

    logger.info(f"批量LLM分析：{len(batch_inputs)}个任务，最大并发数={max_concurrent}")

    semaphore = Semaphore(max_concurrent)

    async def analyze_with_semaphore(inputs: Dict[str, Any]) -> Dict[str, Any]:
        """带信号量控制的分析"""
        async with semaphore:
            return await llm_analysis_step(inputs)

    # 并发执行所有分析任务
    tasks = [analyze_with_semaphore(inputs) for inputs in batch_inputs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 处理异常结果
    processed_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"批量分析第{i}项失败: {result}")
            processed_results.append({
                **batch_inputs[i],
                "llm_result": {
                    "need_attention": False,
                    "相似标准分析": []
                }
            })
        else:
            processed_results.append(result)

    return processed_results


async def save_result_step(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    步骤3：保存查重结果到数据库（基于标签的新结构）
    """
    llm_result = inputs["llm_result"]
    similar_results = inputs.get("screened_results", inputs["similar_results"])

    similar_analysis_dict = {
        item["standard_id"]: item
        for item in llm_result.get("相似标准分析", [])
    }

    vector_results_dict = {
        metadata["id"]: (metadata, score)
        for metadata, score in similar_results
    }

    similar_standards_list = []
    for std_id, analysis in similar_analysis_dict.items():
        if std_id not in vector_results_dict:
            logger.warning(f"LLM 返回的标准 {std_id} 不在检索结果中，跳过")
            continue

        metadata, search_score = vector_results_dict[std_id]

        if not metadata.get("cname"):
            logger.warning(f"跳过无效标准：ID={std_id}，cname 为空")
            continue

        tags = analysis.get("tags", {})

        similar_standards_list.append({
            "id": std_id,
            "cname": metadata["cname"],
            "use_range": metadata["use_range"],
            "standard_no": metadata.get("standard_no"),
            "vector_score": float(search_score),
            "tags": tags,
            "relation_desc": analysis.get("关系说明", "")
        })

    return {
        "success": True,
        "result_id": None,
        "need_attention": llm_result.get("need_attention", False),
        "similar_standards": similar_standards_list
    }


def create_deduplication_chain():
    """
    创建标准查重 Chain（两阶段筛选）

    流程：
    1. 全文检索：MySQL FULLTEXT 检索候选标准
    2. 粗筛：LLM快速过滤低相关度标准
    3. 细筛：LLM详细标签分析（精确打标签）
    4. 保存结果
    """
    chain = (
            RunnableLambda(fulltext_search_step)  # 步骤1：全文检索
        | RunnableLambda(quick_screening_step)  # 步骤2：粗筛（快速过滤）
        | RunnableLambda(llm_analysis_step)     # 步骤3：细筛（详细标签分析）
        | RunnableLambda(save_result_step)      # 步骤4：保存结果
    )

    return chain


__all__ = ["create_deduplication_chain", "fulltext_search_step", "llm_analysis_step", "save_result_step"]
