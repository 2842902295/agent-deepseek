"""
全文相似度分析 API

实时计算两个标准之间的全文相似度（带缓存优化）
"""

import asyncio
import time
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from loguru import logger

from app.models.standard.jgh_pdf import StandardJghPdf, StandardJghPdfChapter
from app.models.standard.cache_sim import StandardCacheSim
from app.utils.text_similarity import (
    split_into_sentences,
    calculate_text_similarity_score_fast,  # 使用优化的快速算法
    should_filter_chapter,
    should_filter_sentence,  # 新增：过滤引用标准的句子
    get_matching_blocks
)
from app.schemas.base import Success

router = APIRouter(prefix="/full-text-similarity", tags=["全文相似度"])


class FullTextSimilarityRequest(BaseModel):
    """全文相似度计算请求"""
    source_standard_no: str = Field(..., description="源标准编号")
    target_standard_no: str = Field(..., description="目标标准编号")
    force_recalculate: bool = Field(default=False, description="是否强制重新计算（忽略缓存）")


class ChapterMatch(BaseModel):
    """章节匹配详情"""
    source_chapter_id: int
    source_chapter_title: str
    source_chapter_no: str
    source_page: Optional[int]
    source_sentence_index: int
    source_sentence_text: str

    target_chapter_id: int
    target_chapter_title: str
    target_chapter_no: str
    target_page: Optional[int]
    target_sentence_index: int
    target_sentence_text: str

    similarity: float


class FullTextSimilarityResponse(BaseModel):
    """全文相似度响应"""
    success: bool
    source_standard_no: str
    source_standard_name: str
    target_standard_no: str
    target_standard_name: str

    similarity_percentage: float = Field(description="相似度百分比")
    matched_sentence_count: int = Field(description="匹配的句子数")
    source_total_sentence_count: int = Field(description="源标准总句子数")
    target_total_sentence_count: int = Field(description="目标标准总句子数")

    matches: list = Field(description="匹配详情列表")


def _compute_similarity_sync(source_chapters, target_chapters) -> dict:
    """
    CPU 密集部分：分句 + 全文相似度计算 + 匹配详情构建。

    独立为同步函数，由端点通过 asyncio.to_thread 放到线程池执行，
    避免在 workers=1 的单事件循环中阻塞整个服务（见 CLAUDE.md async 规则）。
    入参为已加载的章节对象列表，仅做属性读取，不触发 DB 访问，线程安全。
    """
    source_sentences_with_chapter = []  # [(chapter, sentence_index, sentence_text), ...]
    target_sentences_with_chapter = []

    for chapter in source_chapters:
        if should_filter_chapter(chapter.title or '', chapter.title_no):
            logger.debug(f"过滤源标准章节: {chapter.title}")
            continue
        if not chapter.word:
            continue
        sentences = split_into_sentences(chapter.word, min_length=15)
        for idx, sentence in enumerate(sentences):
            if should_filter_sentence(sentence):
                logger.debug(f"过滤源标准引用句子: {sentence[:50]}...")
                continue
            source_sentences_with_chapter.append((chapter, idx, sentence))

    for chapter in target_chapters:
        if should_filter_chapter(chapter.title or '', chapter.title_no):
            logger.debug(f"过滤目标标准章节: {chapter.title}")
            continue
        if not chapter.word:
            continue
        sentences = split_into_sentences(chapter.word, min_length=15)
        for idx, sentence in enumerate(sentences):
            if should_filter_sentence(sentence):
                logger.debug(f"过滤目标标准引用句子: {sentence[:50]}...")
                continue
            target_sentences_with_chapter.append((chapter, idx, sentence))

    logger.info(f"有效句子数 - 源: {len(source_sentences_with_chapter)}, 目标: {len(target_sentences_with_chapter)}")

    source_sentences = [sent for _, _, sent in source_sentences_with_chapter]
    target_sentences = [sent for _, _, sent in target_sentences_with_chapter]

    similarity_result = calculate_text_similarity_score_fast(
        source_sentences,
        target_sentences,
        threshold=0.70  # 70% 相似度阈值
    )

    # 构建匹配详情（包含章节信息）
    detailed_matches = []
    for match in similarity_result['matches']:
        source_idx = match['source_index']
        target_idx = match['target_index']

        source_chapter, source_sent_idx, source_sent = source_sentences_with_chapter[source_idx]
        target_chapter, target_sent_idx, target_sent = target_sentences_with_chapter[target_idx]

        matching_blocks = get_matching_blocks(source_sent, target_sent)

        detailed_matches.append({
            'source_chapter_id': source_chapter.id,
            'source_chapter_title': source_chapter.title or '',
            'source_chapter_no': source_chapter.title_no or '',
            'source_page': source_chapter.page,
            'source_sentence_index': source_sent_idx,
            'source_sentence_text': source_sent,

            'target_chapter_id': target_chapter.id,
            'target_chapter_title': target_chapter.title or '',
            'target_chapter_no': target_chapter.title_no or '',
            'target_page': target_chapter.page,
            'target_sentence_index': target_sent_idx,
            'target_sentence_text': target_sent,

            'similarity': round(float(match['similarity']) * 100, 2),  # 确保转换为 Python float 并转换为百分比
            'matching_blocks': matching_blocks  # 匹配的文本块
        })

    return {
        'similarity_result': similarity_result,
        'detailed_matches': detailed_matches,
        'source_sentence_count': len(source_sentences),
        'target_sentence_count': len(target_sentences),
    }


@router.post("/calculate", summary="计算两个标准的全文相似度")
async def calculate_full_text_similarity(request: FullTextSimilarityRequest):
    """
    计算两个标准之间的全文相似度（带缓存优化）

    工作流程：
    1. 查询缓存，如果存在且有效则直接返回
    2. 如果缓存不存在或强制重新计算，则执行计算
    3. 保存计算结果到缓存表

    示例：
    ```json
    {
        "source_standard_no": "GB/T 11313.24-2009",
        "target_standard_no": "GB/T 11313.24-2020",
        "force_recalculate": false
    }
    ```
    """
    try:
        # 开始计时
        start_time = time.time()

        # 1. 查询源标准信息
        source_standard = await StandardJghPdf.filter(standard_no=request.source_standard_no).first()
        if not source_standard:
            return Success(
                data=None,
                msg=f"源标准不存在: {request.source_standard_no}"
            )

        logger.info(f"源标准: {source_standard.standard_no} - {source_standard.cname}")

        # 2. 查询目标标准
        target_standard = await StandardJghPdf.filter(standard_no=request.target_standard_no).first()
        if not target_standard:
            return Success(
                data=None,
                msg=f"目标标准不存在: {request.target_standard_no}"
            )

        logger.info(f"目标标准: {target_standard.standard_no} - {target_standard.cname}")

        # 4. 检查缓存（如果不是强制重新计算）
        if not request.force_recalculate:
            cached = await StandardCacheSim.filter(
                source_standard_no=source_standard.standard_no,
                target_standard_no=target_standard.standard_no,
                is_valid=True
            ).first()

            if cached:
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
                        'similarity_percentage': cached.similarity_percentage,
                        'matched_sentence_count': cached.matched_sentence_count,
                        'source_total_sentence_count': cached.source_total_sentence_count,
                        'target_total_sentence_count': cached.target_total_sentence_count,
                        'matches': cached.matches
                    },
                    msg="从缓存加载成功"
                )

        # 5. 缓存不存在或强制重新计算，执行实际计算
        logger.info("开始执行相似度计算...")

        # 查询源标准的所有章节
        source_chapters = await StandardJghPdfChapter.filter(
            main_task_id=source_standard.main_task_id
        ).all()

        logger.info(f"源标准章节数: {len(source_chapters)}")

        # 查询目标标准的所有章节
        target_chapters = await StandardJghPdfChapter.filter(
            main_task_id=target_standard.main_task_id
        ).all()

        logger.info(f"目标标准章节数: {len(target_chapters)}")

        # CPU 密集计算（分句 / 相似度 / 匹配详情）放入线程池，避免阻塞事件循环（workers=1）
        compute_result = await asyncio.to_thread(
            _compute_similarity_sync, source_chapters, target_chapters
        )
        similarity_result = compute_result['similarity_result']
        detailed_matches = compute_result['detailed_matches']
        source_sentence_count = compute_result['source_sentence_count']
        target_sentence_count = compute_result['target_sentence_count']

        # 计算耗时
        calculation_time = time.time() - start_time

        logger.info(f"相似度计算完成: {similarity_result['similarity_percentage']}%，耗时: {calculation_time:.2f}秒")

        # 6. 保存到缓存表
        try:
            # 如果存在旧缓存，先将其标记为无效
            await StandardCacheSim.filter(
                source_standard_no=source_standard.standard_no,
                target_standard_no=target_standard.standard_no,
                is_valid=True
            ).update(is_valid=False)

            # 创建新缓存记录
            cache_record = await StandardCacheSim.create(
                source_standard_no=source_standard.standard_no,
                source_standard_name=source_standard.cname or '',
                target_standard_no=target_standard.standard_no,
                target_standard_name=target_standard.cname or '',
                similarity_percentage=similarity_result['similarity_percentage'],
                matched_sentence_count=similarity_result['matched_sentence_count'],
                source_total_sentence_count=source_sentence_count,
                target_total_sentence_count=target_sentence_count,
                matches=detailed_matches,
                calculation_time=calculation_time,
                threshold=0.70,
                algorithm_version='ngram_v1',
                is_valid=True
            )

            logger.info(f"缓存已保存（ID: {cache_record.id}）")

        except Exception as cache_error:
            logger.error(f"保存缓存失败: {str(cache_error)}")
            # 保存缓存失败不影响返回结果

        # 使用 Success 包装器返回响应
        return Success(
            data={
                'success': True,
                'from_cache': False,
                'source_standard_no': source_standard.standard_no or '',
                'source_standard_name': source_standard.cname or '',
                'target_standard_no': target_standard.standard_no or '',
                'target_standard_name': target_standard.cname or '',
                'similarity_percentage': similarity_result['similarity_percentage'],
                'matched_sentence_count': similarity_result['matched_sentence_count'],
                'source_total_sentence_count': source_sentence_count,
                'target_total_sentence_count': target_sentence_count,
                'matches': detailed_matches,
                'calculation_time': round(calculation_time, 2)
            },
            msg="计算成功"
        )

    except Exception as e:
        logger.error(f"计算全文相似度失败: {str(e)}")
        logger.exception(e)
        return Success(
            data=None,
            msg=f"计算失败: {str(e)}"
        )


class FullTextSimilarityBatchRequest(BaseModel):
    """批量全文相似度计算请求"""
    items: List[FullTextSimilarityRequest] = Field(..., description="计算任务列表")


@router.post("/calculate/batch", summary="批量计算全文相似度（顺序执行）")
async def calculate_full_text_similarity_batch(request: FullTextSimilarityBatchRequest):
    """
    批量调用 calculate 接口，顺序执行每个相似度计算任务，逐条返回结果。
    """
    import json
    results = []
    for item in request.items:
        result = await calculate_full_text_similarity(item)
        results.append(json.loads(result.body))
    return Success(data=results, msg="批量计算完成")


__all__ = ["router"]
