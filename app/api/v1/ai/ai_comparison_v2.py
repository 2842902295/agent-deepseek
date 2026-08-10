"""
AI比对分析 V2 - 基于指标表格对比

使用LangChain智能分析两篇标准的指标差异
"""

import asyncio
import time
from typing import List, Tuple, Optional

import faiss
import numpy as np
from fastapi import APIRouter, Depends
from app.core.redis import AioRedis
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from loguru import logger
from pydantic import BaseModel, Field

from app.langchain import get_llm, get_indicator_extractor, Indicator
from app.langchain.config import langchain_config
from app.langchain.embedding_providers import get_embedding
from app.models.standard.jgh_pdf import StandardJghPdf, StandardJghPdfChapter
from app.schemas.base import Success

router = APIRouter(prefix="/ai-comparison", tags=["AI比对"])

# Redis 分布式锁（compare-smart-v1 防重复提交，多 worker 安全）
_SMART_LOCK_PREFIX = "smart_cmp_lock:"
_SMART_LOCK_TTL_MS = 7_200_000  # 2 小时 TTL，防进程崩溃后死锁
_SMART_LOCK_POLL_S = 2.0        # 等待锁释放时的轮询间隔
_SMART_LOCK_TIMEOUT_S = 7260    # 最长等待时间（121 分钟，略超 TTL）


async def _smart_lock_acquire(redis, key: str) -> bool:
    """SET NX PX 原子加锁，成功返回 True。"""
    result = await redis.set(key, "1", nx=True, px=_SMART_LOCK_TTL_MS)
    return result is not None


async def _smart_lock_release(redis, key: str) -> None:
    await redis.delete(key)


async def _smart_lock_wait(redis, key: str) -> None:
    """轮询直到锁释放或超时。"""
    waited = 0.0
    while waited < _SMART_LOCK_TIMEOUT_S:
        if not await redis.exists(key):
            return
        await asyncio.sleep(_SMART_LOCK_POLL_S)
        waited += _SMART_LOCK_POLL_S


async def get_cached_indicators(standard_no: str) -> Optional[List[Indicator]]:
    """
    从缓存中获取已提取的指标

    Args:
        standard_no: 标准编号

    Returns:
        指标列表，如果缓存不存在返回 None
    """
    from app.models.standard import StandardCacheInd

    try:
        cached = await StandardCacheInd.filter(
            standard_no=standard_no,
            is_valid=True
        ).first()

        if cached and cached.indicators:
            logger.info(f"从缓存加载指标: {standard_no}, 共 {cached.indicators_count} 个指标")
            # 将 JSON 数据转换为 Indicator 对象
            indicators = [Indicator(**ind) for ind in cached.indicators]
            return indicators

        return None
    except Exception as e:
        logger.error(f"获取指标缓存失败: {str(e)}")
        return None


async def save_indicators_to_cache(
    standard_no: str,
    standard_name: str,
    indicators: List[Indicator],
    extraction_time: float,
    total_chapters: int,
    filtered_chapters: int
):
    """
    保存指标到缓存

    Args:
        standard_no: 标准编号
        standard_name: 标准名称
        indicators: 指标列表
        extraction_time: 提取耗时
        total_chapters: 总章节数
        filtered_chapters: 过滤后章节数
    """
    from app.models.standard import StandardCacheInd

    try:
        # 将旧缓存标记为无效
        await StandardCacheInd.filter(
            standard_no=standard_no,
            is_valid=True
        ).update(is_valid=False)

        # 转换为可序列化的格式
        indicators_data = [ind.dict() for ind in indicators]

        # 创建新缓存
        await StandardCacheInd.create(
            standard_no=standard_no,
            standard_name=standard_name,
            indicators=indicators_data,
            indicators_count=len(indicators),
            extraction_time=extraction_time,
            total_chapters=total_chapters,
            filtered_chapters=filtered_chapters,
            is_valid=True,
            algorithm_version="v1"
        )

        logger.info(f"指标已缓存: {standard_no}, 共 {len(indicators)} 个指标")

    except Exception as e:
        logger.error(f"保存指标缓存失败: {str(e)}")
        # 保存失败不影响主流程


def should_filter_chapter(chapter: StandardJghPdfChapter, all_chapters: List[StandardJghPdfChapter]) -> bool:
    """
    判断章节是否应该被过滤掉

    Args:
        chapter: 章节对象
        all_chapters: 所有章节列表

    Returns:
        True表示应该过滤掉，不参与指标提取
    """
    title = (chapter.title or "").lower()
    title_no = (chapter.title_no or "").strip()

    # 解析 title_no 获取第一级章节号
    first_level_chapter_no = parse_first_level_chapter_no(title_no)

    # 1. 前三章中的引用、术语、适用范围章节 (章节号 <= 3)
    if first_level_chapter_no is not None and first_level_chapter_no <= 3:
        if "引用" in title or "术语" in title or "适用范围" in title:
            logger.info(f"过滤前置章节: titleNo={chapter.title_no}, title={chapter.title}")
            return True

    # 2. 检查是否是"引用"、"术语"、"适用范围"章节的子章节
    if title_no:
        for parent_chapter in all_chapters:
            parent_title = (parent_chapter.title or "").lower()
            parent_title_no = (parent_chapter.title_no or "").strip()

            # 如果父章节标题包含"引用"、"术语"或"适用范围"
            if "引用" in parent_title or "术语" in parent_title or "适用范围" in parent_title:
                # 检查当前章节是否是该父章节的子章节
                if parent_title_no and is_sub_chapter(title_no, parent_title_no):
                    logger.info(f"过滤引用/术语/适用范围章节的子章节: titleNo={chapter.title_no}, title={chapter.title}, "
                              f"父章节: titleNo={parent_chapter.title_no}, title={parent_chapter.title}")
                    return True

    # 3. 没有 title_no 或 title_no 为非数字的章节，检查是否是封面、前言等非正文章节
    if first_level_chapter_no is None:
        if any(keyword in title for keyword in ["封面", "前言", "引言", "目录", "参考文献", "索引", "bibliography", "references"]):
            logger.info(f"过滤无有效章节号的非正文章节: titleNo={chapter.title_no}, title={chapter.title}")
            return True

    return False


def is_sub_chapter(child_title_no: str, parent_title_no: str) -> bool:
    """
    判断 child_title_no 是否是 parent_title_no 的子章节
    例如: "3.1" 是 "3" 的子章节, "3.1.2" 是 "3" 和 "3.1" 的子章节

    Args:
        child_title_no: 子章节号
        parent_title_no: 父章节号

    Returns:
        True表示是子章节
    """
    if not child_title_no or not parent_title_no:
        return False

    # 如果子章节号以"父章节号."开头，则是子章节
    return child_title_no.startswith(parent_title_no + ".")


def parse_first_level_chapter_no(title_no: str) -> Optional[int]:
    """
    解析 title_no 获取第一级章节号
    例如: "1" -> 1, "3.1" -> 3, "3.3.2" -> 3

    Args:
        title_no: 章节号

    Returns:
        第一级章节号，如果无法解析则返回 None
    """
    if not title_no:
        return None

    # 去除前后空格
    title_no = title_no.strip()

    # 如果包含点号，取第一个点号前的部分
    dot_index = title_no.find('.')
    first_part = title_no[:dot_index] if dot_index > 0 else title_no

    # 尝试转换为整数
    try:
        return int(first_part)
    except ValueError:
        logger.debug(f"无法解析章节号: title_no={title_no}")
        return None


class AIComparisonRequest(BaseModel):
    """AI比对请求"""
    source_standard_no: str = Field(..., description="源标准编号")
    target_standard_no: str = Field(..., description="目标标准编号")
    force_recalculate: bool = Field(default=False, description="是否强制重新计算（忽略缓存）")


class MatchedIndicator(BaseModel):
    """匹配的指标对"""
    category: str = Field(description="指标大类")
    name: str = Field(description="指标名称")
    source_requirement: str = Field(description="源标准要求")
    target_requirement: str = Field(description="目标标准要求")
    change_analysis: str = Field(description="变化分析")
    source_chapter: str = Field(description="源标准章节", default="")
    target_chapter: str = Field(description="目标标准章节", default="")


class UniqueIndicator(BaseModel):
    """独有指标"""
    category: str = Field(description="指标大类")
    name: str = Field(description="指标名称")
    requirement: str = Field(description="指标要求")
    chapter_info: str = Field(description="所在章节", default="")


class IndicatorMatchResult(BaseModel):
    """指标匹配验证结果"""
    is_match: bool = Field(description="两个指标是否匹配")
    change_analysis: str = Field(description="变化分析（仅在匹配时需要）", default="")


async def get_embeddings_batch(texts: List[str], max_batch_size: int = 10, max_concurrent: Optional[int] = None) -> np.ndarray:
    """
    批量获取文本的embedding向量（异步，使用 Embedding 抽象层）

    Args:
        texts: 文本列表
        max_batch_size: 每批最大文本数
        max_concurrent: 最大并发请求数，默认使用配置文件中的 LLM_MAX_CONCURRENT

    Returns:
        embedding向量矩阵，shape为(n, embedding_dim)
    """
    if max_concurrent is None:
        max_concurrent = langchain_config.LLM_MAX_CONCURRENT

    if not texts:
        raise ValueError("文本列表不能为空")

    embedding_provider = get_embedding()

    # 如果文本数量不超过限制，直接调用
    if len(texts) <= max_batch_size:
        embeddings = await embedding_provider.embed_texts(texts)
        return np.array(embeddings, dtype=np.float32)

    # 需要分批并发处理
    logger.info(f"文本数量 {len(texts)} 超过限制，将分成多批并发处理（每批{max_batch_size}个，最大并发{max_concurrent}）")

    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_embedding_batch(batch_idx: int, batch_texts: List[str]):
        """处理单个embedding批次"""
        async with semaphore:
            try:
                batch_embeddings = await embedding_provider.embed_texts(batch_texts)
                logger.info(f"Embedding批次 {batch_idx+1}-{min(batch_idx+max_batch_size, len(texts))}/{len(texts)} 完成")
                return batch_idx, batch_embeddings
            except Exception as e:
                logger.error(f"调用embedding API失败（批次{batch_idx//max_batch_size + 1}）: {str(e)}")
                raise

    # 分批
    batches = []
    for i in range(0, len(texts), max_batch_size):
        batch_texts = texts[i:i + max_batch_size]
        batches.append((i, batch_texts))

    # 并发执行所有批次
    tasks = [process_embedding_batch(idx, batch) for idx, batch in batches]
    results = await asyncio.gather(*tasks)

    # 按顺序合并结果
    results_sorted = sorted(results, key=lambda x: x[0])
    all_embeddings = []
    for _, embeddings in results_sorted:
        all_embeddings.extend(embeddings)

    return np.array(all_embeddings, dtype=np.float32)


def build_faiss_index(embeddings: np.ndarray) -> faiss.IndexFlatIP:
    """
    构建FAISS索引（使用内积相似度）

    Args:
        embeddings: embedding向量矩阵

    Returns:
        FAISS索引对象
    """
    # 归一化向量，使内积等价于余弦相似度
    faiss.normalize_L2(embeddings)

    # 创建索引（使用内积）
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings)

    return index


async def verify_match_with_llm(
    source_indicator: Indicator,
    target_indicator: Indicator,
    similarity_score: float
) -> Optional[MatchedIndicator]:
    """
    使用LLM验证两个指标是否真正匹配，并分析变化

    Args:
        source_indicator: 源指标
        target_indicator: 目标指标
        similarity_score: 向量相似度分数

    Returns:
        如果匹配则返回MatchedIndicator对象，否则返回None
    """
    llm = get_llm(temperature=0.1)

    system_prompt = """你是标准文档对比专家。请判断两个指标是否匹配。

**判断标准**：
- 指标名称必须语义一致或高度相关
- 不要求指标要求内容完全相同，但必须是同一个技术指标
- 例如："抗拉强度" vs "抗拉强度" → 匹配
- 例如："工作温度" vs "使用温度范围" → 匹配
- 例如："外形尺寸" vs "材料成分" → 不匹配

**变化分析**（仅在匹配时，必须极简）：
- 完全相同：写"一致"
- 有差异：用3-6个字概括核心差异
- 示例："数值提高"、"范围放宽"、"增加条件"、"标准降低"、"单位变化"
- 禁止：冗长描述、完整句子、解释性文字"""

    user_message = f"""源指标：
- 类别：{source_indicator.category}
- 名称：{source_indicator.name}
- 要求：{source_indicator.requirement}
- 章节：{source_indicator.chapter_info}

目标指标：
- 类别：{target_indicator.category}
- 名称：{target_indicator.name}
- 要求：{target_indicator.requirement}
- 章节：{target_indicator.chapter_info}

向量相似度分数：{similarity_score:.3f}

请判断这两个指标是否匹配。"""

    try:
        # 使用 Agent API 进行结构化输出
        agent = create_agent(
            model=llm,
            tools=[],
            response_format=ToolStrategy(
                schema=IndicatorMatchResult,
                handle_errors=True  # 自动捕获验证错误并重试
            ),
            system_prompt=system_prompt
        )

        # 调用 agent
        agent_result = await agent.ainvoke({
            "messages": [{"role": "user", "content": user_message}]
        })

        # 从 agent 结果中提取结构化响应
        result: IndicatorMatchResult = agent_result["structured_response"]

        if result.is_match:
            return MatchedIndicator(
                category=source_indicator.category,
                name=source_indicator.name,
                source_requirement=source_indicator.requirement,
                target_requirement=target_indicator.requirement,
                change_analysis=result.change_analysis,
                source_chapter=source_indicator.chapter_info,
                target_chapter=target_indicator.chapter_info
            )
        return None
    except Exception as e:
        logger.error(f"LLM验证失败: {str(e)}")
        return None


async def match_indicators_with_llm(
    source_indicators: List[Indicator],
    target_indicators: List[Indicator],
    source_standard: StandardJghPdf,
    target_standard: StandardJghPdf,
    top_k: int = 3,
    similarity_threshold: float = 0.6,
    max_concurrent: Optional[int] = None
) -> Tuple[List[MatchedIndicator], List[UniqueIndicator], List[UniqueIndicator]]:
    """
    使用向量库+并发LLM匹配两个标准的指标

    新架构：
    1. 使用embedding将所有指标向量化
    2. 使用FAISS进行相似度搜索，为每个源指标找top-k个候选
    3. 使用asyncio.gather并发调用LLM验证每个候选对
    4. 汇总结果

    Args:
        source_indicators: 源标准指标列表
        target_indicators: 目标标准指标列表
        source_standard: 源标准信息
        target_standard: 目标标准信息
        top_k: 为每个源指标找top-k个最相似的目标指标
        similarity_threshold: 相似度阈值，低于此值的不考虑
        max_concurrent: 最大并发LLM调用数，默认使用配置文件中的 LLM_MAX_CONCURRENT

    Returns:
        (匹配的指标列表, 源标准独有指标, 目标标准独有指标)
    """
    # 使用配置文件中的默认值
    if max_concurrent is None:
        max_concurrent = langchain_config.LLM_MAX_CONCURRENT

    logger.info(f"开始使用向量库+并发LLM匹配指标...")
    logger.info(f"源指标数: {len(source_indicators)}, 目标指标数: {len(target_indicators)}")

    if not source_indicators or not target_indicators:
        logger.warning("源指标或目标指标为空")
        source_unique = [
            UniqueIndicator(
                category=ind.category,
                name=ind.name,
                requirement=ind.requirement,
                chapter_info=ind.chapter_info
            ) for ind in source_indicators
        ]
        target_unique = [
            UniqueIndicator(
                category=ind.category,
                name=ind.name,
                requirement=ind.requirement,
                chapter_info=ind.chapter_info
            ) for ind in target_indicators
        ]
        return [], source_unique, target_unique

    # Step 1: 构建指标文本（用于embedding）
    logger.info("Step 1: 构建指标文本...")
    source_texts = [
        f"[{ind.category}] {ind.name}: {ind.requirement}"
        for ind in source_indicators
    ]
    target_texts = [
        f"[{ind.category}] {ind.name}: {ind.requirement}"
        for ind in target_indicators
    ]

    # Step 2: 获取embeddings
    logger.info("Step 2: 获取embeddings...")
    try:
        source_embeddings = await get_embeddings_batch(source_texts)
        target_embeddings = await get_embeddings_batch(target_texts)
        logger.info(f"Embedding维度: {source_embeddings.shape[1]}")
    except Exception as e:
        logger.error(f"获取embeddings失败: {str(e)}")
        raise

    # Step 3: 构建FAISS索引
    logger.info("Step 3: 构建FAISS索引...")
    target_index = build_faiss_index(target_embeddings.copy())

    # Step 4: 搜索最相似的候选对
    logger.info(f"Step 4: 为每个源指标搜索top-{top_k}个候选...")
    # 归一化源向量
    faiss.normalize_L2(source_embeddings)
    # 搜索
    similarities, indices = target_index.search(source_embeddings, min(top_k, len(target_indicators)))

    # 构建候选对列表
    candidate_pairs = []  # (source_idx, target_idx, similarity)
    for source_idx in range(len(source_indicators)):
        for k_idx in range(len(indices[source_idx])):
            target_idx = indices[source_idx][k_idx]
            similarity = float(similarities[source_idx][k_idx])

            # 过滤低相似度的候选
            if similarity >= similarity_threshold:
                candidate_pairs.append((source_idx, target_idx, similarity))

    logger.info(f"共找到 {len(candidate_pairs)} 个候选对（相似度 >= {similarity_threshold}）")

    # Step 5: 并发调用LLM验证候选对
    logger.info(f"Step 5: 并发验证候选对（最大并发: {max_concurrent}）...")

    async def verify_candidate(source_idx: int, target_idx: int, similarity: float):
        """验证单个候选对"""
        return await verify_match_with_llm(
            source_indicators[source_idx],
            target_indicators[target_idx],
            similarity
        )

    # 使用信号量控制并发数
    semaphore = asyncio.Semaphore(max_concurrent)

    async def verify_with_limit(source_idx: int, target_idx: int, similarity: float):
        async with semaphore:
            return (source_idx, target_idx, await verify_candidate(source_idx, target_idx, similarity))

    # 并发执行所有验证任务
    verification_tasks = [
        verify_with_limit(source_idx, target_idx, similarity)
        for source_idx, target_idx, similarity in candidate_pairs
    ]

    verification_results = await asyncio.gather(*verification_tasks, return_exceptions=True)

    # Step 6: 汇总结果
    logger.info("Step 6: 汇总结果...")
    matched_indicators = []
    matched_source_indices = set()
    matched_target_indices = set()

    # 按源指标分组
    matches_by_source = {}
    for result in verification_results:
        if isinstance(result, Exception):
            logger.error(f"验证任务失败: {str(result)}")
            continue

        source_idx, target_idx, matched = result
        if matched:
            if source_idx not in matches_by_source:
                matches_by_source[source_idx] = []
            matches_by_source[source_idx].append((target_idx, matched))

    # 为每个源指标选择最佳匹配（避免一对多）
    for source_idx, matches in matches_by_source.items():
        if matches:
            # 选择第一个匹配（因为已经按相似度排序）
            target_idx, matched = matches[0]

            # 检查目标指标是否已被匹配
            if target_idx not in matched_target_indices:
                matched_indicators.append(matched)
                matched_source_indices.add(source_idx)
                matched_target_indices.add(target_idx)

    # 找出独有指标
    source_unique = []
    for idx, ind in enumerate(source_indicators):
        if idx not in matched_source_indices:
            source_unique.append(UniqueIndicator(
                category=ind.category,
                name=ind.name,
                requirement=ind.requirement,
                chapter_info=ind.chapter_info
            ))

    target_unique = []
    for idx, ind in enumerate(target_indicators):
        if idx not in matched_target_indices:
            target_unique.append(UniqueIndicator(
                category=ind.category,
                name=ind.name,
                requirement=ind.requirement,
                chapter_info=ind.chapter_info
            ))

    logger.info(f"匹配完成：匹配 {len(matched_indicators)} 个，源独有 {len(source_unique)} 个，目标独有 {len(target_unique)} 个")

    return matched_indicators, source_unique, target_unique


@router.get("/cache", summary="获取AI比对缓存结果")
async def get_ai_comparison_cache(
    source_standard_no: str,
    target_standard_no: str
):
    """
    快速获取已有的AI比对缓存结果（不重新计算）

    Args:
        source_standard_no: 源标准编号（查询参数）
        target_standard_no: 目标标准编号（查询参数，即 standard_no 字段，不是 id）

    Returns:
        缓存的比对结果，如果不存在则返回 null
    """
    try:
        from app.models.standard import StandardCacheAI, StandardJghPdf

        # 查询缓存
        cached = await StandardCacheAI.filter(
            source_standard_no=source_standard_no,
            target_standard_no=target_standard_no,
            is_valid=True
        ).first()

        if not cached:
            return Success(
                data=None,
                msg="未找到缓存数据"
            )

        # 返回缓存数据
        return Success(
            data={
                'success': True,
                'from_cache': True,
                'cache_id': cached.id,
                'cache_time': cached.create_time.isoformat() if cached.create_time else None,
                'source_standard_no': cached.source_standard_no,
                'source_standard_name': cached.source_standard_name or '',
                'target_standard_no': cached.target_standard_no,
                'target_standard_name': cached.target_standard_name or '',
                'matched_indicators': cached.matched_indicators,
                'source_unique_indicators': cached.source_unique_indicators,
                'target_unique_indicators': cached.target_unique_indicators,
                'calculation_time': cached.calculation_time,
                'relationship': cached.relationship
            },
            msg="获取缓存成功"
        )

    except Exception as e:
        logger.error(f"获取缓存失败: {str(e)}")
        return Success(data=None, msg=f"获取缓存失败: {str(e)}")


@router.post("/compare-v2", summary="AI智能比对两篇标准（表格形式）")
async def ai_compare_standards_v2(request: AIComparisonRequest):
    """
    使用LangChain智能分析两篇标准的指标差异

    返回表格形式的对比结果：
    1. 匹配的指标对比表
    2. 源标准独有指标列表
    3. 目标标准独有指标列表
    """
    try:
        from app.models.standard import StandardBaseInfo, StandardCacheAI

        start_time = time.time()

        # 1. 查询源标准
        source_standard = await StandardJghPdf.filter(standard_no=request.source_standard_no).first()
        if not source_standard:
            return Success(data=None, msg=f"源标准不存在: {request.source_standard_no}")

        logger.info(f"源标准: {source_standard.standard_no} - {source_standard.cname}")

        # 2. 查询目标标准
        target_standard = await StandardJghPdf.filter(standard_no=request.target_standard_no).first()
        if not target_standard:
            return Success(data=None, msg=f"目标标准不存在: {request.target_standard_no}")

        logger.info(f"目标标准: {target_standard.standard_no} - {target_standard.cname}")

        # 3. 检查缓存（如果不是强制重新计算）
        if not request.force_recalculate:
            cached = await StandardCacheAI.filter(
                source_standard_no=source_standard.standard_no,
                target_standard_no=target_standard.standard_no,
                is_valid=True
            ).first()

            if cached and cached.matched_indicators:  # 确保是新版本的缓存
                logger.info(f"使用缓存数据（ID: {cached.id}，创建时间: {cached.create_time}）")
                return Success(
                    data={
                        'success': True,
                        'from_cache': True,
                        'cache_id': cached.id,
                        'cache_time': cached.create_time.isoformat() if cached.create_time else None,
                        'source_standard_no': cached.source_standard_no,
                        'source_standard_name': cached.source_standard_name or '',
                        'target_standard_no': cached.target_standard_no,
                        'target_standard_name': cached.target_standard_name or '',
                        'matched_indicators': cached.matched_indicators,
                        'source_unique_indicators': cached.source_unique_indicators,
                        'target_unique_indicators': cached.target_unique_indicators,
                        'calculation_time': cached.calculation_time
                    },
                    msg="从缓存加载成功"
                )

        # 4. 执行实际计算
        logger.info("开始执行AI比对分析...")

        # 5. 获取所有章节
        source_chapters_all = await StandardJghPdfChapter.filter(
            main_task_id=source_standard.main_task_id
        ).all()

        target_chapters_all = await StandardJghPdfChapter.filter(
            main_task_id=target_standard.main_task_id
        ).all()

        # 过滤无意义的章节
        source_chapters = [ch for ch in source_chapters_all if not should_filter_chapter(ch, source_chapters_all)]
        target_chapters = [ch for ch in target_chapters_all if not should_filter_chapter(ch, target_chapters_all)]

        logger.info(f"章节数 - 源: {len(source_chapters_all)} -> {len(source_chapters)} (过滤后), "
                   f"目标: {len(target_chapters_all)} -> {len(target_chapters)} (过滤后)")

        # 6. 提取指标（优先使用缓存）
        # 6.1 提取源标准指标
        source_indicators = await get_cached_indicators(source_standard.standard_no)
        if source_indicators:
            logger.info(f"源标准指标从缓存加载")
        else:
            logger.info("提取源标准指标...")
            extraction_start = time.time()
            extractor = get_indicator_extractor(batch_size=50, temperature=0.2)
            source_indicators = await extractor.extract_indicators(source_standard, source_chapters)
            extraction_time = time.time() - extraction_start

            # 保存到缓存（异步执行，不阻塞主流程）
            asyncio.create_task(save_indicators_to_cache(
                standard_no=source_standard.standard_no,
                standard_name=source_standard.cname or '',
                indicators=source_indicators,
                extraction_time=extraction_time,
                total_chapters=len(source_chapters_all),
                filtered_chapters=len(source_chapters)
            ))

        # 6.2 提取目标标准指标
        target_indicators = await get_cached_indicators(target_standard.standard_no)
        if target_indicators:
            logger.info(f"目标标准指标从缓存加载")
        else:
            logger.info("提取目标标准指标...")
            extraction_start = time.time()
            extractor = get_indicator_extractor(batch_size=50, temperature=0.2)
            target_indicators = await extractor.extract_indicators(target_standard, target_chapters)
            extraction_time = time.time() - extraction_start

            # 保存到缓存（异步执行，不阻塞主流程）
            asyncio.create_task(save_indicators_to_cache(
                standard_no=target_standard.standard_no,
                standard_name=target_standard.cname or '',
                indicators=target_indicators,
                extraction_time=extraction_time,
                total_chapters=len(target_chapters_all),
                filtered_chapters=len(target_chapters)
            ))

        if not source_indicators and not target_indicators:
            return Success(data=None, msg="两个标准都未能提取到有效指标")

        # 7. 使用LLM匹配指标
        matched_indicators, source_unique, target_unique = await match_indicators_with_llm(
            source_indicators,
            target_indicators,
            source_standard,
            target_standard
        )

        # 计算耗时
        calculation_time = time.time() - start_time
        logger.info(f"AI比对完成，耗时: {calculation_time:.2f}秒")

        # 8. 保存到缓存表
        try:
            # 如果存在旧缓存，先将其标记为无效
            await StandardCacheAI.filter(
                source_standard_no=source_standard.standard_no,
                target_standard_no=target_standard.standard_no,
                is_valid=True
            ).update(is_valid=False)

            # 转换为可序列化的格式
            matched_data = [item.dict() for item in matched_indicators]
            source_unique_data = [item.dict() for item in source_unique]
            target_unique_data = [item.dict() for item in target_unique]

            # 生成简单的关系描述
            relationship_summary = f"共匹配 {len(matched_indicators)} 个指标"
            if source_unique:
                relationship_summary += f"，源标准独有 {len(source_unique)} 个指标"
            if target_unique:
                relationship_summary += f"，目标标准独有 {len(target_unique)} 个指标"

            # 生成关键相似点摘要（从匹配的指标中提取）
            key_similarities = []
            for idx, matched in enumerate(matched_data[:5], 1):  # 只取前5个作为关键相似点
                key_similarities.append({
                    "point": f"{matched['category']}: {matched['name']}",
                    "explanation": matched.get('change_analysis', '一致'),
                    "source_indicator": matched.get('source_requirement', ''),
                    "target_content": matched.get('target_requirement', '')
                })

            # 创建新缓存记录
            cache_record = await StandardCacheAI.create(
                source_standard_no=source_standard.standard_no,
                source_standard_name=source_standard.cname or '',
                target_standard_no=target_standard.standard_no,
                target_standard_name=target_standard.cname or '',
                relationship=relationship_summary,  # 提供关系描述
                key_similarities=key_similarities,  # 提供关键相似点
                indicator_matches=[],  # V1字段，提供空列表
                matched_indicators=matched_data,
                source_unique_indicators=source_unique_data,
                target_unique_indicators=target_unique_data,
                indicators_count=len(source_indicators) + len(target_indicators),
                matches_count=len(matched_indicators),
                calculation_time=calculation_time,
                algorithm_version='indicator_match_v2',
                is_valid=True
            )

            logger.info(f"缓存已保存（ID: {cache_record.id}）")

        except Exception as cache_error:
            logger.error(f"保存缓存失败: {str(cache_error)}")
            # 保存缓存失败不影响返回结果

        # 9. 返回结果
        return Success(
            data={
                'success': True,
                'from_cache': False,
                'source_standard_no': source_standard.standard_no or '',
                'source_standard_name': source_standard.cname or '',
                'target_standard_no': target_standard.standard_no or '',
                'target_standard_name': target_standard.cname or '',
                'matched_indicators': matched_data,
                'source_unique_indicators': source_unique_data,
                'target_unique_indicators': target_unique_data,
                'calculation_time': round(calculation_time, 2)
            },
            msg="AI比对成功"
        )

    except Exception as e:
        logger.error(f"AI比对失败: {str(e)}")
        logger.exception(e)
        return Success(data=None, msg=f"AI比对失败: {str(e)}")


class AIComparisonBatchRequest(BaseModel):
    """批量AI比对请求"""
    items: List[AIComparisonRequest] = Field(..., description="比对任务列表")


@router.post("/compare-v2/batch", summary="批量AI智能比对（顺序执行）")
async def ai_compare_standards_v2_batch(request: AIComparisonBatchRequest):
    """
    批量调用 compare-v2 接口，顺序执行每个比对任务，逐条返回结果。
    """
    import json
    results = []
    for item in request.items:
        result = await ai_compare_standards_v2(item)
        results.append(json.loads(result.body))
    return Success(data=results, msg="批量比对完成")


__all__ = ["router"]


# ── compare-v3：基于 StructuredQAAgent 的全新接口 ──────────────────────────────



class AIComparisonV3BatchRequest(BaseModel):
    """批量AI比对请求（V3）"""
    items: List[AIComparisonRequest] = Field(..., description="比对任务列表")


@router.post("/compare-v3", summary="AI智能比对（Agent版，更强大）")
async def ai_compare_standards_v3(request: AIComparisonRequest):
    """
    使用 StructuredQAAgent 自主阅读标准章节，端到端完成指标对比。

    相比 compare-v2，Agent 可以：
    - 按需阅读章节，不做冗余 I/O
    - 识别任意类型技术要求，不受固定模板限制
    - 输出额外的关系类型（relationship）和综合评价（overall_assessment）
    """
    try:
        from app.models.standard import StandardCacheAI
        from app.models.standard.cache_ind import StandardCacheInd

        start_time = time.time()

        source_standard_no = request.source_standard_no
        target_standard_no = request.target_standard_no

        logger.info(f"[V3] 比对: {source_standard_no} <-> {target_standard_no}")

        # 2. 查询源标准名称（供日志和缓存）
        from app.models.standard.jgh_pdf import StandardJghPdf as _JghPdf
        source_pdf = await _JghPdf.filter(standard_no=source_standard_no).first()
        target_pdf = await _JghPdf.filter(standard_no=target_standard_no).first()
        source_name = (source_pdf.cname if source_pdf else "") or ""
        target_name = (target_pdf.cname if target_pdf else "") or ""

        # 3. 检查缓存（standard_cache_ai）
        if not request.force_recalculate:
            from app.models.standard.cache_ai import StandardCacheAI as _CacheAI
            cache_items = await _CacheAI.filter(
                source_standard_no=source_standard_no,
                target_standard_no=target_standard_no,
                is_valid=True,
            ).all()
            if cache_items:
                logger.info(f"[V3] 命中缓存，共 {len(cache_items)} 条，联表查全量指标")
                all_ind_ids = {i.source_ind_id for i in cache_items if i.source_ind_id} | \
                              {i.target_ind_id for i in cache_items if i.target_ind_id}
                ind_map = {ind.id: ind for ind in await StandardCacheInd.filter(id__in=list(all_ind_ids)).all()}
                cached_indicators = []
                for item in cache_items:
                    src = ind_map.get(item.source_ind_id) if item.source_ind_id else None
                    tgt = ind_map.get(item.target_ind_id) if item.target_ind_id else None
                    ind = src or tgt
                    if not ind:
                        continue
                    ct = item.comparison_type
                    entry = {
                        "comparison_type": ct,
                        "source_indicator_type": src.indicator_type if src else "",
                        "target_indicator_type": tgt.indicator_type if tgt else "",
                        "standard_object": ind.standard_object or "",
                        "applicable_object": ind.applicable_object or "",
                        "indicator_category": ind.indicator_category or "",
                        "norm_class": ind.norm_class or "",
                        # static 字段
                        "source_indicator_object": src.indicator_object if src else "",
                        "source_value": src.source_value if src else "",
                        "source_clause": src.source_clause if src else "",
                        "target_indicator_object": tgt.indicator_object if tgt else "",
                        "target_value": tgt.source_value if tgt else "",
                        "target_clause": tgt.source_clause if tgt else "",
                        # dynamic 字段
                        "source_experiment_name": src.experiment_name if src else "",
                        "source_input_params": src.source_input_params if src else "",
                        "source_process_logic": src.source_process_logic if src else "",
                        "source_result": src.source_result if src else "",
                        "target_experiment_name": tgt.experiment_name if tgt else "",
                        "target_input_params": tgt.source_input_params if tgt else "",
                        "target_process_logic": tgt.source_process_logic if tgt else "",
                        "target_result": tgt.source_result if tgt else "",
                        "change_analysis": item.change_analysis or "",
                    }
                    cached_indicators.append(entry)
                return Success(
                    data={
                        'success': True,
                        'from_cache': True,
                        'source_standard_no': source_standard_no,
                        'source_standard_name': source_name,
                        'target_standard_no': target_standard_no,
                        'target_standard_name': target_name,
                        'all_indicators': cached_indicators,
                        'stats': {
                            'matched': sum(1 for i in cached_indicators if i['comparison_type'] == 'matched'),
                            'source_only': sum(1 for i in cached_indicators if i['comparison_type'] == 'source_only'),
                            'target_only': sum(1 for i in cached_indicators if i['comparison_type'] == 'target_only'),
                        },
                    },
                    msg="从缓存加载成功"
                )

        # 4. 两步流程
        from app.langchain.agents.tasks.indicator.indicator_extraction_agent import extract_indicators
        from app.langchain.agents.tasks.indicator.standard_comparison_agent import compare_standards
        async def get_or_extract(standard_no: str, standard_name: str) -> None:
            """确保 standard_cache_ind 中有该标准的有效指标，不存在则调用 Agent 提取。"""
            exists = await StandardCacheInd.filter(standard_no=standard_no, is_valid=True).exists()
            if exists:
                logger.info(f"[V3] 指标缓存命中: {standard_no}")
                return

            logger.info(f"[V3] 开始提取指标: {standard_no}")
            ext_start = time.time()
            extraction = await extract_indicators(standard_no, standard_name)
            extraction_time = round(time.time() - ext_start, 2)

            try:
                await StandardCacheInd.filter(standard_no=standard_no, is_valid=True).update(is_valid=False)
                for ind in extraction.indicators:
                    if ind.indicator_type == "static":
                        await StandardCacheInd.create(
                            standard_no=standard_no, standard_name=standard_name,
                            indicator_type="static", standard_object=ind.standard_object,
                            applicable_object=ind.applicable_object or "",
                            indicator_category=ind.indicator_category or "",
                            indicator_object=ind.indicator_object, source_value=ind.source_value,
                            source_clause=ind.source_clause, extraction_time=extraction_time,
                            algorithm_version="v3_agent", is_valid=True,
                        )
                    else:
                        await StandardCacheInd.create(
                            standard_no=standard_no, standard_name=standard_name,
                            indicator_type="dynamic", standard_object=ind.standard_object,
                            applicable_object=ind.applicable_object or "",
                            indicator_category=ind.indicator_category or "",
                            experiment_name=ind.experiment_name, source_input_params=ind.source_input_params,
                            source_process_logic=ind.source_process_logic, source_result=ind.source_result,
                            source_clause=ind.source_clause, extraction_time=extraction_time,
                            algorithm_version="v3_agent", is_valid=True,
                        )
                static_count = sum(1 for i in extraction.indicators if i.indicator_type == "static")
                dynamic_count = sum(1 for i in extraction.indicators if i.indicator_type == "dynamic")
                logger.info(f"[V3] 指标已缓存: {standard_no}（静态 {static_count}，动态 {dynamic_count}）")
            except Exception as e:
                logger.error(f"[V3] 指标缓存写入失败: {e}")

        await asyncio.gather(
            get_or_extract(source_standard_no, source_name),
            get_or_extract(target_standard_no, target_name),
        )

        # ── 步骤二：比对（Agent 自主从 cache_ind 取指标）──
        logger.info(f"[V3] 开始比对: {source_standard_no} <-> {target_standard_no}")
        result = await compare_standards(
            source_standard_no=source_standard_no,
            target_standard_no=target_standard_no,
            source_name=source_name,
            target_name=target_name,
        )

        calculation_time = time.time() - start_time
        matched_count = sum(1 for i in result.items if i.comparison_type == "matched")
        logger.info(f"[V3] Agent 推理完成，耗时 {calculation_time:.2f}s，匹配 {matched_count} 条")

        # 5. 写入 cache_ai
        try:
            await StandardCacheAI.filter(
                source_standard_no=source_standard_no,
                target_standard_no=target_standard_no,
                is_valid=True
            ).update(is_valid=False)

            for item in result.items:
                await StandardCacheAI.create(
                    source_standard_no=source_standard_no,
                    target_standard_no=target_standard_no,
                    source_ind_id=item.source_ind_id,
                    target_ind_id=item.target_ind_id,
                    comparison_type=item.comparison_type,
                    change_analysis=item.change_analysis,
                    algorithm_version="v3_agent",
                    is_valid=True,
                )
            logger.info(f"[V3] 比对缓存已保存（{len(result.items)} 条）")
        except Exception as cache_err:
            logger.error(f"[V3] 保存缓存失败: {cache_err}")

        # 6. 从 cache_ind 查指标详情，构建结构化数据
        all_ind_ids = {i.source_ind_id for i in result.items if i.source_ind_id} | \
                      {i.target_ind_id for i in result.items if i.target_ind_id}
        ind_map = {ind.id: ind for ind in await StandardCacheInd.filter(id__in=list(all_ind_ids)).all()}

        matched_items, all_indicators = [], []

        for item in result.items:
            src = ind_map.get(item.source_ind_id) if item.source_ind_id else None
            tgt = ind_map.get(item.target_ind_id) if item.target_ind_id else None
            ind = src or tgt
            if not ind:
                continue
            ct = item.comparison_type
            entry = {
                "source_indicator_type": src.indicator_type if src else "",
                "target_indicator_type": tgt.indicator_type if tgt else "",
                "standard_object": ind.standard_object or "",
                "applicable_object": ind.applicable_object or "",
                "indicator_category": ind.indicator_category or "",
                "norm_class": ind.norm_class or "",
                # static 字段
                "source_indicator_object": src.indicator_object if src else "",
                "source_value": src.source_value if src else "",
                "source_clause": src.source_clause if src else "",
                "target_indicator_object": tgt.indicator_object if tgt else "",
                "target_value": tgt.source_value if tgt else "",
                "target_clause": tgt.source_clause if tgt else "",
                # dynamic 字段
                "source_experiment_name": src.experiment_name if src else "",
                "source_input_params": src.source_input_params if src else "",
                "source_process_logic": src.source_process_logic if src else "",
                "source_result": src.source_result if src else "",
                "target_experiment_name": tgt.experiment_name if tgt else "",
                "target_input_params": tgt.source_input_params if tgt else "",
                "target_process_logic": tgt.source_process_logic if tgt else "",
                "target_result": tgt.source_result if tgt else "",
                "change_analysis": item.change_analysis or "",
            }
            all_indicators.append({**entry, "comparison_type": ct})
            if ct == "matched":
                matched_items.append(entry)

        # 6.5 生成 HTML（仅 matched 部分）
        # from app.models.standard.cache_ai_html import StandardCacheAIHtml
        # from app.langchain.agents.tasks.indicator.html_generator_agent import generate_comparison_html
        # html_content = None
        # try:
        #     await StandardCacheAIHtml.filter(
        #         source_standard_no=source_standard_no,
        #         target_standard_no=target_standard_no,
        #         is_valid=True
        #     ).update(is_valid=False)
        #
        #     html_start = time.time()
        #     html_content = await generate_comparison_html(
        #         matched_items=matched_items,
        #         source_no=source_standard_no,
        #         target_no=target_standard_no,
        #     )
        #     html_time = time.time() - html_start
        #
        #     await StandardCacheAIHtml.create(
        #         source_standard_no=source_standard_no,
        #         target_standard_no=target_standard_no,
        #         html_content=html_content,
        #         relationship=result.relationship,
        #         overall_assessment=result.overall_assessment,
        #         calculation_time=round(calculation_time, 2),
        #         generation_time=round(html_time, 2),
        #         algorithm_version="v3_html",
        #         is_valid=True,
        #     )
        #     logger.info(f"[V3] HTML 已生成并缓存，耗时 {html_time:.2f}s")
        # except Exception as html_err:
        #     logger.error(f"[V3] HTML 生成失败: {html_err}")

        # 7. 返回
        return Success(
            data={
                'success': True,
                'from_cache': False,
                'source_standard_no': source_standard_no,
                'source_standard_name': source_name,
                'target_standard_no': target_standard_no,
                'target_standard_name': target_name,
                'all_indicators': all_indicators,
                'relationship': result.relationship,
                'overall_assessment': result.overall_assessment,
                'calculation_time': round(calculation_time, 2),
                'stats': {
                    'matched': sum(1 for i in all_indicators if i['comparison_type'] == 'matched'),
                    'source_only': sum(1 for i in all_indicators if i['comparison_type'] == 'source_only'),
                    'target_only': sum(1 for i in all_indicators if i['comparison_type'] == 'target_only'),
                },
            },
            msg="AI比对成功（V3 Agent）"
        )

    except Exception as e:
        logger.error(f"[V3] AI比对失败: {e}")
        logger.exception(e)
        return Success(data=None, msg=f"AI比对失败: {str(e)}")


@router.post("/compare-v3/batch", summary="批量AI智能比对——Agent版（顺序执行）")
async def ai_compare_standards_v3_batch(request: AIComparisonV3BatchRequest):
    """
    批量调用 compare-v3 接口，顺序执行每个比对任务，逐条返回结果。
    """
    import json
    results = []
    for item in request.items:
        result = await ai_compare_standards_v3(item)
        results.append(json.loads(result.body))
    return Success(data=results, msg="批量比对完成（V3 Agent）")


@router.get("/compare-v3/detail", summary="获取AI比对缓存详情（HTML + 全量指标）")
async def get_comparison_detail(
    source_standard_no: str,
    target_standard_no: str,
):
    """
    查询缓存的比对结果：
    - standard_cache_ai_html：HTML 片段 + 元信息
    - standard_cache_ai × standard_cache_ind：全量指标（含 comparison_type）
    不存在缓存时返回 data=null。
    """
    try:
        from app.models.standard.cache_ai_html import StandardCacheAIHtml
        from app.models.standard.cache_ai import StandardCacheAI
        from app.models.standard.cache_ind import StandardCacheInd

        html_cache = await StandardCacheAIHtml.filter(
            source_standard_no=source_standard_no,
            target_standard_no=target_standard_no,
            is_valid=True,
        ).first()

        if not html_cache:
            return Success(data=None, msg="暂无缓存，请先触发比对")

        # 联表查全量指标
        cache_items = await StandardCacheAI.filter(
            source_standard_no=source_standard_no,
            target_standard_no=target_standard_no,
            is_valid=True,
        ).all()

        all_ind_ids = {i.source_ind_id for i in cache_items if i.source_ind_id} | \
                      {i.target_ind_id for i in cache_items if i.target_ind_id}
        ind_map = {ind.id: ind for ind in await StandardCacheInd.filter(id__in=list(all_ind_ids)).all()}

        all_indicators = []
        for item in cache_items:
            src = ind_map.get(item.source_ind_id) if item.source_ind_id else None
            tgt = ind_map.get(item.target_ind_id) if item.target_ind_id else None
            ind = src or tgt
            if not ind:
                continue
            ct = item.comparison_type
            entry = {
                "comparison_type": ct,
                "source_indicator_type": src.indicator_type if src else "",
                "target_indicator_type": tgt.indicator_type if tgt else "",
                "standard_object": ind.standard_object or "",
                "applicable_object": ind.applicable_object or "",
                "indicator_category": ind.indicator_category or "",
                "norm_class": ind.norm_class or "",
                # static 字段
                "source_indicator_object": src.indicator_object if src else "",
                "source_value": src.source_value if src else "",
                "source_clause": src.source_clause if src else "",
                "target_indicator_object": tgt.indicator_object if tgt else "",
                "target_value": tgt.source_value if tgt else "",
                "target_clause": tgt.source_clause if tgt else "",
                # dynamic 字段
                "source_experiment_name": src.experiment_name if src else "",
                "source_input_params": src.source_input_params if src else "",
                "source_process_logic": src.source_process_logic if src else "",
                "source_result": src.source_result if src else "",
                "target_experiment_name": tgt.experiment_name if tgt else "",
                "target_input_params": tgt.source_input_params if tgt else "",
                "target_process_logic": tgt.source_process_logic if tgt else "",
                "target_result": tgt.source_result if tgt else "",
                "change_analysis": item.change_analysis or "",
            }
            all_indicators.append(entry)

        stats = {
            'matched': sum(1 for i in all_indicators if i['comparison_type'] == 'matched'),
            'source_only': sum(1 for i in all_indicators if i['comparison_type'] == 'source_only'),
            'target_only': sum(1 for i in all_indicators if i['comparison_type'] == 'target_only'),
        }

        return Success(
            data={
                'source_standard_no': source_standard_no,
                'target_standard_no': target_standard_no,
                'matched_html': html_cache.html_content,
                'all_indicators': all_indicators,
                'relationship': html_cache.relationship,
                'overall_assessment': html_cache.overall_assessment,
                'calculation_time': html_cache.calculation_time,
                'generation_time': html_cache.generation_time,
                'create_time': html_cache.create_time.isoformat() if html_cache.create_time else None,
                'stats': stats,
            },
            msg="获取成功"
        )
    except Exception as e:
        logger.error(f"[V3] 获取比对详情失败: {e}")
        return Success(data=None, msg=f"获取失败: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# compare-smart-v1：高速高精度比对
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/compare-smart-v1", summary="AI智能比对（Smart V1，高速高精度）")
async def ai_compare_standards_smart_v1(request: AIComparisonRequest, redis: AioRedis):
    """
    compare-smart-v1 方案：

    - LLM 分组规划（单次调用，两标准并行）
    - HTML→Markdown 预处理（降低 Token 噪声）
    - 所有分组并行提取（两标准同时）
    - 外部标准引用解析（DB 定位章节 + 1次LLM补值）
    - 全量或按 standard_object 分组并行比对

    LLM 调用约 13～20 次，预期耗时 2～4 分钟（vs compare-v3 的 5～10 分钟）。
    复用 standard_cache_ind + standard_cache_ai，算法版本标记为 smart_v1。
    """
    from app.models.standard import StandardCacheAI, StandardCacheInd
    from app.models.standard.jgh_pdf import StandardJghPdf as _JghPdf
    from app.langchain.agents.tasks.indicator.smart_comparison import run_smart_comparison

    _ALGO = "smart_v1"
    start_time = time.time()
    source_standard_no = request.source_standard_no
    target_standard_no = request.target_standard_no

    logger.info(f"[SmartV1] 比对: {source_standard_no} <-> {target_standard_no}")

    try:
        # 查标准名称
        src_pdf, tgt_pdf = await asyncio.gather(
            _JghPdf.filter(standard_no=source_standard_no).first(),
            _JghPdf.filter(standard_no=target_standard_no).first(),
        )
        source_name = (src_pdf.cname if src_pdf else "") or ""
        target_name = (tgt_pdf.cname if tgt_pdf else "") or ""

        # 检查缓存（按 algorithm_version 区分，不影响 compare-v3 的缓存）
        if not request.force_recalculate:
            cache_items = await StandardCacheAI.filter(
                source_standard_no=source_standard_no,
                target_standard_no=target_standard_no,
                algorithm_version=_ALGO,
                is_valid=True,
            ).all()
            if cache_items:
                logger.info(f"[SmartV1] 命中缓存，共 {len(cache_items)} 条，联表查指标详情")
                all_ind_ids = (
                    {c.source_ind_id for c in cache_items if c.source_ind_id}
                    | {c.target_ind_id for c in cache_items if c.target_ind_id}
                )
                ind_map = {
                    ind.id: ind
                    for ind in await StandardCacheInd.filter(id__in=list(all_ind_ids)).all()
                }
                cached_indicators = []
                for item in cache_items:
                    src = ind_map.get(item.source_ind_id) if item.source_ind_id else None
                    tgt = ind_map.get(item.target_ind_id) if item.target_ind_id else None
                    ind = src or tgt
                    if not ind:
                        continue
                    cached_indicators.append({
                        "comparison_type": item.comparison_type,
                        "standard_object": ind.standard_object or "",
                        "applicable_object": ind.applicable_object or "",
                        "indicator_category": "",
                        "norm_class": "",
                        "source_indicator_type": "static",
                        "target_indicator_type": "static",
                        "source_indicator_object": src.indicator_object if src else "",
                        "source_value": src.source_value if src else "",
                        "source_clause": src.source_clause if src else "",
                        "target_indicator_object": tgt.indicator_object if tgt else "",
                        "target_value": tgt.source_value if tgt else "",
                        "target_clause": tgt.source_clause if tgt else "",
                        "source_experiment_name": "",
                        "source_input_params": "",
                        "source_process_logic": "",
                        "source_result": "",
                        "target_experiment_name": "",
                        "target_input_params": "",
                        "target_process_logic": "",
                        "target_result": "",
                        "change_analysis": item.change_analysis or "",
                    })
                return Success(
                    data={
                        "success": True,
                        "from_cache": True,
                        "source_standard_no": source_standard_no,
                        "source_standard_name": source_name,
                        "target_standard_no": target_standard_no,
                        "target_standard_name": target_name,
                        "all_indicators": cached_indicators,
                        "stats": {
                            "matched": sum(1 for i in cached_indicators if i["comparison_type"] == "matched"),
                            "source_only": sum(1 for i in cached_indicators if i["comparison_type"] == "source_only"),
                            "target_only": sum(1 for i in cached_indicators if i["comparison_type"] == "target_only"),
                        },
                    },
                    msg="从缓存加载成功",
                )

        # 防重复提交：Redis 分布式锁，多 worker 安全
        _lock_key = f"{_SMART_LOCK_PREFIX}{source_standard_no}:{target_standard_no}"
        _acquired = await _smart_lock_acquire(redis, _lock_key)
        if not _acquired:
            logger.info(f"[SmartV1] 相同比对进行中，等待锁释放: {source_standard_no} <-> {target_standard_no}")
            await _smart_lock_wait(redis, _lock_key)
            wait_cache = await StandardCacheAI.filter(
                source_standard_no=source_standard_no,
                target_standard_no=target_standard_no,
                algorithm_version=_ALGO,
                is_valid=True,
            ).all()
            if wait_cache:
                wait_ind_ids = (
                    {c.source_ind_id for c in wait_cache if c.source_ind_id}
                    | {c.target_ind_id for c in wait_cache if c.target_ind_id}
                )
                wait_ind_map = {
                    ind.id: ind
                    for ind in await StandardCacheInd.filter(id__in=list(wait_ind_ids)).all()
                }
                wait_indicators = []
                for item in wait_cache:
                    src = wait_ind_map.get(item.source_ind_id) if item.source_ind_id else None
                    tgt = wait_ind_map.get(item.target_ind_id) if item.target_ind_id else None
                    ind = src or tgt
                    if not ind:
                        continue
                    wait_indicators.append({
                        "comparison_type": item.comparison_type,
                        "standard_object": ind.standard_object or "",
                        "applicable_object": ind.applicable_object or "",
                        "indicator_category": "",
                        "norm_class": "",
                        "source_indicator_type": "static",
                        "target_indicator_type": "static",
                        "source_indicator_object": src.indicator_object if src else "",
                        "source_value": src.source_value if src else "",
                        "source_clause": src.source_clause if src else "",
                        "target_indicator_object": tgt.indicator_object if tgt else "",
                        "target_value": tgt.source_value if tgt else "",
                        "target_clause": tgt.source_clause if tgt else "",
                        "source_experiment_name": "",
                        "source_input_params": "",
                        "source_process_logic": "",
                        "source_result": "",
                        "target_experiment_name": "",
                        "target_input_params": "",
                        "target_process_logic": "",
                        "target_result": "",
                        "change_analysis": item.change_analysis or "",
                    })
                return Success(
                    data={
                        "success": True,
                        "from_cache": True,
                        "source_standard_no": source_standard_no,
                        "source_standard_name": source_name,
                        "target_standard_no": target_standard_no,
                        "target_standard_name": target_name,
                        "all_indicators": wait_indicators,
                        "stats": {
                            "matched": sum(1 for i in wait_indicators if i["comparison_type"] == "matched"),
                            "source_only": sum(1 for i in wait_indicators if i["comparison_type"] == "source_only"),
                            "target_only": sum(1 for i in wait_indicators if i["comparison_type"] == "target_only"),
                        },
                    },
                    msg="从缓存加载成功（等待并发任务完成）",
                )
            return Success(data=None, msg="AI比对任务已完成，但结果暂不可用，请稍后重试")

        # 成功获锁，完成（含失败）后统一释放
        try:
            # 执行 compare-smart-v1 流程
            result = await run_smart_comparison(source_standard_no, target_standard_no)
            calculation_time = round(time.time() - start_time, 2)
        finally:
            await _smart_lock_release(redis, _lock_key)

        # 写缓存：先将旧的 smart_v1 结果失效，再写入新记录
        try:
            # 失效旧的 smart_v1 指标（不影响 compare-v3 的 v3_agent 版本）
            await StandardCacheInd.filter(
                standard_no__in=[source_standard_no, target_standard_no],
                algorithm_version=_ALGO,
                is_valid=True,
            ).update(is_valid=False)
            await StandardCacheAI.filter(
                source_standard_no=source_standard_no,
                target_standard_no=target_standard_no,
                algorithm_version=_ALGO,
                is_valid=True,
            ).update(is_valid=False)

            # 写指标到 standard_cache_ind，获取 id 用于关联（smart_v1）
            async def _save_ind(standard_no: str, standard_nm: str, ind: dict) -> int:
                obj = await StandardCacheInd.create(
                    standard_no=standard_no,
                    standard_name=standard_nm,
                    indicator_type="static",
                    standard_object=ind.get("standard_object") or "",
                    applicable_object=ind.get("applicable_object") or "",
                    indicator_object=ind.get("indicator_object") or "",
                    source_value=ind.get("source_value") or "",
                    source_clause=ind.get("source_clause") or "",
                    algorithm_version=_ALGO,
                    is_valid=True,
                )
                return obj.id

            for item in result.items:
                src_id: Optional[int] = None
                tgt_id: Optional[int] = None

                if item.comparison_type in ("matched", "source_only") and item.source_indicator_object:
                    src_id = await _save_ind(
                        source_standard_no, source_name,
                        {
                            "standard_object": item.standard_object,
                            "indicator_object": item.source_indicator_object,
                            "source_value": item.source_value,
                            "source_clause": item.source_clause,
                        },
                    )
                if item.comparison_type in ("matched", "target_only") and item.target_indicator_object:
                    tgt_id = await _save_ind(
                        target_standard_no, target_name,
                        {
                            "standard_object": item.standard_object,
                            "indicator_object": item.target_indicator_object,
                            "source_value": item.target_value,
                            "source_clause": item.target_clause,
                        },
                    )

                await StandardCacheAI.create(
                    source_standard_no=source_standard_no,
                    target_standard_no=target_standard_no,
                    source_ind_id=src_id,
                    target_ind_id=tgt_id,
                    comparison_type=item.comparison_type,
                    change_analysis=item.change_analysis,
                    algorithm_version=_ALGO,
                    is_valid=True,
                )

            logger.info(f"[SmartV1] 缓存已保存（{len(result.items)} 条）")
        except Exception as cache_err:
            logger.error(f"[SmartV1] 保存缓存失败: {cache_err}")

        # 构建返回
        all_indicators = [
            {
                "comparison_type": item.comparison_type,
                "standard_object": item.standard_object,
                "applicable_object": "",
                "indicator_category": "",
                "norm_class": "",
                "source_indicator_type": "static",
                "target_indicator_type": "static",
                "source_indicator_object": item.source_indicator_object,
                "source_value": item.source_value,
                "source_clause": item.source_clause,
                "target_indicator_object": item.target_indicator_object,
                "target_value": item.target_value,
                "target_clause": item.target_clause,
                "source_experiment_name": "",
                "source_input_params": "",
                "source_process_logic": "",
                "source_result": "",
                "target_experiment_name": "",
                "target_input_params": "",
                "target_process_logic": "",
                "target_result": "",
                "change_analysis": item.change_analysis,
            }
            for item in result.items
        ]

        return Success(
            data={
                "success": True,
                "from_cache": False,
                "source_standard_no": source_standard_no,
                "source_standard_name": source_name,
                "target_standard_no": target_standard_no,
                "target_standard_name": target_name,
                "all_indicators": all_indicators,
                "relationship": result.relationship,
                "overall_assessment": result.overall_assessment,
                "calculation_time": calculation_time,
                "stats": {
                    "matched": sum(1 for i in all_indicators if i["comparison_type"] == "matched"),
                    "source_only": sum(1 for i in all_indicators if i["comparison_type"] == "source_only"),
                    "target_only": sum(1 for i in all_indicators if i["comparison_type"] == "target_only"),
                },
            },
            msg="AI比对成功（Smart V1）",
        )

    except Exception as e:
        logger.error(f"[SmartV1] AI比对失败: {e}")
        logger.exception(e)
        return Success(data=None, msg=f"AI比对失败: {str(e)}")


@router.post("/compare-smart-v2", summary="AI智能比对（Smart V2，两阶段快/慢路径）")
async def ai_compare_standards_smart_v2(request: AIComparisonRequest, redis: AioRedis):
    """
    compare-smart-v2：在 smart_v1 基础上增加两阶段快路径判断。

    - Stage 1: TOC 原始字符合计 ≤ 140k → 进入快路径
    - Stage 2: 全文 inline 外部引用后 compact ≤ 60k → 单次 LLM one-shot（1 次调用）
    - 超限则自动降级到 smart_v1 流程（约 13～20 次调用）

    response 新增字段 path_taken: "fast" | "slow" | "slow_fallback"
    """
    from app.models.standard import StandardCacheAI, StandardCacheInd
    from app.models.standard.jgh_pdf import StandardJghPdf as _JghPdf
    from app.langchain.agents.tasks.indicator.smart_comparison_v2 import run_smart_comparison_v2

    _ALGO = "smart_v2"
    _LOCK_PREFIX = "smart_v2_cmp_lock:"
    start_time = time.time()
    source_standard_no = request.source_standard_no
    target_standard_no = request.target_standard_no

    logger.info(f"[SmartV2] 比对: {source_standard_no} <-> {target_standard_no}")

    def _build_all_indicators(cache_items, ind_map, src_tests=None, tgt_tests=None):
        src_tests = src_tests or []
        tgt_tests = tgt_tests or []
        result = []
        for item in cache_items:
            src = ind_map.get(item.source_ind_id) if item.source_ind_id else None
            tgt = ind_map.get(item.target_ind_id) if item.target_ind_id else None
            if not (src or tgt):
                continue
            obj = (src or tgt).standard_object or ""
            # 优先用 LLM 关联结果过滤；字段为 None 时降级到旧 standard_object 匹配
            _raw_src = item.source_test_names  # JSONField：存单元素列表 [name] 或 []
            _raw_tgt = item.target_test_names
            src_name: str | None = (_raw_src[0] if _raw_src else "") if _raw_src is not None else None
            tgt_name: str | None = (_raw_tgt[0] if _raw_tgt else "") if _raw_tgt is not None else None
            result.append({
                "comparison_type": item.comparison_type,
                "standard_object": obj,
                "applicable_object": "",
                "indicator_category": "",
                "norm_class": (src or tgt).norm_class or "",
                "source_indicator_type": "static",
                "target_indicator_type": "static",
                "source_indicator_object": src.indicator_object if src else "",
                "source_value": src.source_value if src else "",
                "source_clause": src.source_clause if src else "",
                "target_indicator_object": tgt.indicator_object if tgt else "",
                "target_value": tgt.source_value if tgt else "",
                "target_clause": tgt.source_clause if tgt else "",
                "source_experiment_name": "", "source_input_params": "",
                "source_process_logic": "", "source_result": "",
                "target_experiment_name": "", "target_input_params": "",
                "target_process_logic": "", "target_result": "",
                "change_analysis": item.change_analysis or "",
                "source_tests": (
                    []
                    if item.comparison_type == "target_only"
                    else (
                        [t for t in src_tests if src_name and t.get("test_name") == src_name][:1]
                        if src_name is not None
                        else [t for t in src_tests if not obj or (t.get("standard_object") or "") == obj][:1]
                    )
                ),
                "target_tests": (
                    []
                    if item.comparison_type == "source_only"
                    else (
                        [t for t in tgt_tests if tgt_name and t.get("test_name") == tgt_name][:1]
                        if tgt_name is not None
                        else [t for t in tgt_tests if not obj or (t.get("standard_object") or "") == obj][:1]
                    )
                ),
            })
        return result

    def _calc_element_stats(indicators: list, src_tests: list, tgt_tests: list) -> dict:
        by_class: dict[str, int] = {}
        for ind in indicators:
            nc = ind.get("norm_class") or ""
            if nc:
                by_class[nc] = by_class.get(nc, 0) + 1
        return {
            "total": len(indicators),
            "source_test_count": len(src_tests),
            "target_test_count": len(tgt_tests),
            "by_norm_class": by_class,
        }

    def _test_obj_to_dict(t) -> dict:
        return {
            "test_name": t.test_name or "",
            "method_desc": t.method_desc or "",
            "conditions": t.conditions or "",
            "preparation": t.preparation or "",
            "procedure": t.procedure or "",
            "acceptance": t.acceptance or "",
            "report_items": t.report_items or "",
            "source_clause": t.source_clause or "",
            "standard_object": t.standard_object or "",
        }

    try:
        src_pdf, tgt_pdf = await asyncio.gather(
            _JghPdf.filter(standard_no=source_standard_no).first(),
            _JghPdf.filter(standard_no=target_standard_no).first(),
        )
        source_name = (src_pdf.cname if src_pdf else "") or ""
        target_name = (tgt_pdf.cname if tgt_pdf else "") or ""

        # 缓存检查
        if not request.force_recalculate:
            cache_items = await StandardCacheAI.filter(
                source_standard_no=source_standard_no,
                target_standard_no=target_standard_no,
                algorithm_version=_ALGO,
                is_valid=True,
            ).all()
            if cache_items:
                from app.models.standard.cache_test import StandardCacheTest as _CacheTest
                from app.models.standard.cache_ai_html import StandardCacheAIHtml as _CacheAIHtml
                all_ind_ids = (
                    {c.source_ind_id for c in cache_items if c.source_ind_id}
                    | {c.target_ind_id for c in cache_items if c.target_ind_id}
                )
                ind_map = {
                    ind.id: ind
                    for ind in await StandardCacheInd.filter(id__in=list(all_ind_ids)).all()
                }
                src_tests_raw, tgt_tests_raw, html_cache = await asyncio.gather(
                    _CacheTest.filter(standard_no=source_standard_no, algorithm_version=_ALGO, is_valid=True).all(),
                    _CacheTest.filter(standard_no=target_standard_no, algorithm_version=_ALGO, is_valid=True).all(),
                    _CacheAIHtml.filter(source_standard_no=source_standard_no, target_standard_no=target_standard_no, algorithm_version=_ALGO, is_valid=True).first(),
                )
                src_tests_data = [_test_obj_to_dict(t) for t in src_tests_raw]
                tgt_tests_data = [_test_obj_to_dict(t) for t in tgt_tests_raw]
                cached_indicators = _build_all_indicators(cache_items, ind_map, src_tests_data, tgt_tests_data)
                return Success(
                    data={
                        "success": True,
                        "from_cache": True,
                        "source_standard_no": source_standard_no,
                        "source_standard_name": source_name,
                        "target_standard_no": target_standard_no,
                        "target_standard_name": target_name,
                        "all_indicators": cached_indicators,
                        "source_tests": src_tests_data,
                        "target_tests": tgt_tests_data,
                        "element_stats": _calc_element_stats(cached_indicators, src_tests_data, tgt_tests_data),
                        "relationship": html_cache.relationship if html_cache else None,
                        "overall_assessment": html_cache.overall_assessment if html_cache else None,
                        "stats": {
                            "matched": sum(1 for i in cached_indicators if i["comparison_type"] == "matched"),
                            "source_only": sum(1 for i in cached_indicators if i["comparison_type"] == "source_only"),
                            "target_only": sum(1 for i in cached_indicators if i["comparison_type"] == "target_only"),
                        },
                    },
                    msg="从缓存加载成功",
                )

        # 分布式锁防重复提交
        lock_key = f"{_LOCK_PREFIX}{source_standard_no}:{target_standard_no}"
        acquired = await _smart_lock_acquire(redis, lock_key)
        if not acquired:
            logger.info(f"[SmartV2] 相同比对进行中，等待锁: {source_standard_no} <-> {target_standard_no}")
            await _smart_lock_wait(redis, lock_key)
            wait_cache = await StandardCacheAI.filter(
                source_standard_no=source_standard_no,
                target_standard_no=target_standard_no,
                algorithm_version=_ALGO,
                is_valid=True,
            ).all()
            if wait_cache:
                from app.models.standard.cache_test import StandardCacheTest as _CacheTest
                from app.models.standard.cache_ai_html import StandardCacheAIHtml as _CacheAIHtml
                wait_ind_ids = (
                    {c.source_ind_id for c in wait_cache if c.source_ind_id}
                    | {c.target_ind_id for c in wait_cache if c.target_ind_id}
                )
                wait_ind_map = {
                    ind.id: ind
                    for ind in await StandardCacheInd.filter(id__in=list(wait_ind_ids)).all()
                }
                wsrc_tests_raw, wtgt_tests_raw, wait_html_cache = await asyncio.gather(
                    _CacheTest.filter(standard_no=source_standard_no, algorithm_version=_ALGO, is_valid=True).all(),
                    _CacheTest.filter(standard_no=target_standard_no, algorithm_version=_ALGO, is_valid=True).all(),
                    _CacheAIHtml.filter(source_standard_no=source_standard_no, target_standard_no=target_standard_no, algorithm_version=_ALGO, is_valid=True).first(),
                )
                wsrc_data = [_test_obj_to_dict(t) for t in wsrc_tests_raw]
                wtgt_data = [_test_obj_to_dict(t) for t in wtgt_tests_raw]
                wait_indicators = _build_all_indicators(wait_cache, wait_ind_map, wsrc_data, wtgt_data)
                return Success(
                    data={
                        "success": True,
                        "from_cache": True,
                        "source_standard_no": source_standard_no,
                        "source_standard_name": source_name,
                        "target_standard_no": target_standard_no,
                        "target_standard_name": target_name,
                        "all_indicators": wait_indicators,
                        "source_tests": wsrc_data,
                        "target_tests": wtgt_data,
                        "element_stats": _calc_element_stats(wait_indicators, wsrc_data, wtgt_data),
                        "relationship": wait_html_cache.relationship if wait_html_cache else None,
                        "overall_assessment": wait_html_cache.overall_assessment if wait_html_cache else None,
                        "stats": {
                            "matched": sum(1 for i in wait_indicators if i["comparison_type"] == "matched"),
                            "source_only": sum(1 for i in wait_indicators if i["comparison_type"] == "source_only"),
                            "target_only": sum(1 for i in wait_indicators if i["comparison_type"] == "target_only"),
                        },
                    },
                    msg="从缓存加载成功（等待并发任务完成）",
                )
            return Success(data=None, msg="AI比对任务已完成，但结果暂不可用，请稍后重试")

        try:
            result, path_taken = await run_smart_comparison_v2(source_standard_no, target_standard_no)
            calculation_time = round(time.time() - start_time, 2)
        finally:
            await _smart_lock_release(redis, lock_key)

        # 写缓存
        try:
            from app.models.standard.cache_test import StandardCacheTest as _CacheTest
            from app.models.standard.cache_ind_test_rel import StandardCacheIndTestRel as _IndTestRel

            await StandardCacheInd.filter(
                standard_no__in=[source_standard_no, target_standard_no],
                algorithm_version=_ALGO,
                is_valid=True,
            ).update(is_valid=False)
            await StandardCacheAI.filter(
                source_standard_no=source_standard_no,
                target_standard_no=target_standard_no,
                algorithm_version=_ALGO,
                is_valid=True,
            ).update(is_valid=False)
            await _CacheTest.filter(
                standard_no__in=[source_standard_no, target_standard_no],
                algorithm_version=_ALGO,
                is_valid=True,
            ).update(is_valid=False)

            # 先保存试验，建立 test_name → test_id 映射，用于后续创建指标-试验关联
            src_test_name_to_id: dict[str, int] = {}
            for t in result.source_tests:
                tr = await _CacheTest.create(
                    standard_no=source_standard_no,
                    standard_name=source_name,
                    test_name=t.get("test_name") or "",
                    method_desc=t.get("method_desc") or "",
                    conditions=t.get("conditions") or "",
                    preparation=t.get("preparation") or "",
                    procedure=t.get("procedure") or "",
                    acceptance=t.get("acceptance") or "",
                    report_items=t.get("report_items") or "",
                    source_clause=t.get("source_clause") or "",
                    standard_object=t.get("standard_object") or "",
                    algorithm_version=_ALGO,
                    is_valid=True,
                )
                if t.get("test_name"):
                    src_test_name_to_id[t["test_name"]] = tr.id

            tgt_test_name_to_id: dict[str, int] = {}
            for t in result.target_tests:
                tr = await _CacheTest.create(
                    standard_no=target_standard_no,
                    standard_name=target_name,
                    test_name=t.get("test_name") or "",
                    method_desc=t.get("method_desc") or "",
                    conditions=t.get("conditions") or "",
                    preparation=t.get("preparation") or "",
                    procedure=t.get("procedure") or "",
                    acceptance=t.get("acceptance") or "",
                    report_items=t.get("report_items") or "",
                    source_clause=t.get("source_clause") or "",
                    standard_object=t.get("standard_object") or "",
                    algorithm_version=_ALGO,
                    is_valid=True,
                )
                if t.get("test_name"):
                    tgt_test_name_to_id[t["test_name"]] = tr.id

            async def _save_ind(std_no: str, std_nm: str, ind: dict) -> int:
                obj = await StandardCacheInd.create(
                    standard_no=std_no,
                    standard_name=std_nm,
                    indicator_type="static",
                    standard_object=ind.get("standard_object") or "",
                    applicable_object=ind.get("applicable_object") or "",
                    indicator_category=ind.get("indicator_category") or "",
                    indicator_object=ind.get("indicator_object") or "",
                    source_value=ind.get("source_value") or "",
                    source_clause=ind.get("source_clause") or "",
                    norm_class=ind.get("norm_class") or "",
                    object_type=ind.get("object_type") or "",
                    algorithm_version=_ALGO,
                    is_valid=True,
                )
                return obj.id

            for i, item in enumerate(result.items):
                src_id: Optional[int] = None
                tgt_id: Optional[int] = None

                if item.comparison_type in ("matched", "source_only") and item.source_indicator_object:
                    src_id = await _save_ind(
                        source_standard_no, source_name,
                        {
                            "standard_object": item.standard_object,
                            "norm_class": item.norm_class,
                            "indicator_object": item.source_indicator_object,
                            "source_value": item.source_value,
                            "source_clause": item.source_clause,
                            "indicator_category": item.source_indicator_category,
                            "object_type": item.source_object_type,
                        },
                    )
                if item.comparison_type in ("matched", "target_only") and item.target_indicator_object:
                    tgt_id = await _save_ind(
                        target_standard_no, target_name,
                        {
                            "standard_object": item.standard_object,
                            "norm_class": item.norm_class,
                            "indicator_object": item.target_indicator_object,
                            "source_value": item.target_value,
                            "source_clause": item.target_clause,
                            "indicator_category": item.target_indicator_category,
                            "object_type": item.target_object_type,
                        },
                    )

                _src_test = result.source_test_links.get(i, "")
                _tgt_test = result.target_test_links.get(i, "")
                await StandardCacheAI.create(
                    source_standard_no=source_standard_no,
                    target_standard_no=target_standard_no,
                    source_ind_id=src_id,
                    target_ind_id=tgt_id,
                    comparison_type=item.comparison_type,
                    change_analysis=item.change_analysis,
                    source_test_names=[_src_test] if _src_test else [],
                    target_test_names=[_tgt_test] if _tgt_test else [],
                    algorithm_version=_ALGO,
                    is_valid=True,
                )

                # 写入指标-试验关联表，标准指标页通过此表展示关联试验
                if src_id and _src_test and _src_test in src_test_name_to_id:
                    await _IndTestRel.create(ind_id=src_id, test_id=src_test_name_to_id[_src_test], run_id=None)
                if tgt_id and _tgt_test and _tgt_test in tgt_test_name_to_id:
                    await _IndTestRel.create(ind_id=tgt_id, test_id=tgt_test_name_to_id[_tgt_test], run_id=None)

            logger.info(f"[SmartV2] 缓存已保存（{len(result.items)} 条指标，{len(result.source_tests)} 源试验，{len(result.target_tests)} 目标试验）")
        except Exception as cache_err:
            logger.error(f"[SmartV2] 保存缓存失败: {cache_err}")

        # 保存会话级结论（relationship / overall_assessment）到 standard_cache_ai_html
        try:
            from app.models.standard.cache_ai_html import StandardCacheAIHtml as _CacheAIHtml
            await _CacheAIHtml.filter(
                source_standard_no=source_standard_no,
                target_standard_no=target_standard_no,
                algorithm_version=_ALGO,
                is_valid=True,
            ).update(is_valid=False)
            await _CacheAIHtml.create(
                source_standard_no=source_standard_no,
                target_standard_no=target_standard_no,
                html_content="",
                relationship=result.relationship,
                overall_assessment=result.overall_assessment,
                calculation_time=round(calculation_time, 2),
                algorithm_version=_ALGO,
                is_valid=True,
            )
            logger.info(f"[SmartV2] 综合评价已保存到 standard_cache_ai_html")
        except Exception as html_err:
            logger.error(f"[SmartV2] 保存综合评价失败: {html_err}")

        source_tests_data = result.source_tests
        target_tests_data = result.target_tests
        all_indicators = []
        for i, item in enumerate(result.items):
            src_name = result.source_test_links.get(i)  # None 表示关联未运行，降级旧逻辑
            tgt_name = result.target_test_links.get(i)
            all_indicators.append({
                "comparison_type": item.comparison_type,
                "standard_object": item.standard_object,
                "applicable_object": "",
                "indicator_category": "",
                "norm_class": item.norm_class,
                "source_indicator_type": "static",
                "target_indicator_type": "static",
                "source_indicator_object": item.source_indicator_object,
                "source_value": item.source_value,
                "source_clause": item.source_clause,
                "target_indicator_object": item.target_indicator_object,
                "target_value": item.target_value,
                "target_clause": item.target_clause,
                "source_experiment_name": "", "source_input_params": "",
                "source_process_logic": "", "source_result": "",
                "target_experiment_name": "", "target_input_params": "",
                "target_process_logic": "", "target_result": "",
                "change_analysis": item.change_analysis,
                "source_tests": (
                    []
                    if item.comparison_type == "target_only"
                    else (
                        [t for t in source_tests_data if src_name and t.get("test_name") == src_name][:1]
                        if src_name is not None
                        else [t for t in source_tests_data
                              if not item.standard_object or (t.get("standard_object") or "") == item.standard_object][:1]
                    )
                ),
                "target_tests": (
                    []
                    if item.comparison_type == "source_only"
                    else (
                        [t for t in target_tests_data if tgt_name and t.get("test_name") == tgt_name][:1]
                        if tgt_name is not None
                        else [t for t in target_tests_data
                              if not item.standard_object or (t.get("standard_object") or "") == item.standard_object][:1]
                    )
                ),
            })

        return Success(
            data={
                "success": True,
                "from_cache": False,
                "source_standard_no": source_standard_no,
                "source_standard_name": source_name,
                "target_standard_no": target_standard_no,
                "target_standard_name": target_name,
                "all_indicators": all_indicators,
                "source_tests": source_tests_data,
                "target_tests": target_tests_data,
                "element_stats": _calc_element_stats(all_indicators, source_tests_data, target_tests_data),
                "path_taken": path_taken,
                "relationship": result.relationship,
                "overall_assessment": result.overall_assessment,
                "calculation_time": calculation_time,
                "stats": {
                    "matched": sum(1 for i in all_indicators if i["comparison_type"] == "matched"),
                    "source_only": sum(1 for i in all_indicators if i["comparison_type"] == "source_only"),
                    "target_only": sum(1 for i in all_indicators if i["comparison_type"] == "target_only"),
                },
            },
            msg="AI比对成功（Smart V2）",
        )

    except Exception as e:
        logger.error(f"[SmartV2] AI比对失败: {e}")
        logger.exception(e)
        return Success(data=None, msg=f"AI比对失败: {str(e)}")
