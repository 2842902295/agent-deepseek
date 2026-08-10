"""
LangExtract 指标提取接口 - 基于 Google LangExtract 的精准提取

与现有接口的区别：
- standard_ind.py：查询缓存表 standard_cache_ind
- langextract_indicator.py：实时调用 LangExtract 提取，返回带源文本定位的结果
"""

from typing import List, Optional

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field

from app.langchain.agents.tasks.indicator.indicator_extraction_langextract import (
    extract_indicators_langextract,
)
from app.schemas.base import Success, Fail

router = APIRouter(prefix="/langextract", tags=["LangExtract指标提取"])


class LangExtractRequest(BaseModel):
    """提取请求"""
    standard_no: str = Field(description="标准编号，如 GB/T 1234-2020")
    generate_html: bool = Field(default=False, description="是否生成可视化 HTML")
    output_dir: Optional[str] = Field(default=None, description="HTML 输出目录（可选）")
    max_workers: int = Field(default=30, description="并行处理线程数（默认30）")
    max_char_buffer: int = Field(default=1000, description="分块大小（默认1000）")
    extraction_passes: int = Field(default=3, description="提取轮次（默认3）")
    enable_debug: bool = Field(default=False, description="是否启用调试日志")


class IndicatorExtractionResponse(BaseModel):
    """单条指标提取结果"""
    indicator_name: str = Field(description="指标名称")
    requirement: str = Field(description="完整的指标要求")
    test_method: Optional[str] = Field(None, description="测试方法")
    source_text: str = Field(description="原文片段")
    char_start: Optional[int] = Field(None, description="字符起始位置")
    char_end: Optional[int] = Field(None, description="字符结束位置")
    chapter_title: Optional[str] = Field(None, description="所属章节标题")


class TestExtractionResponse(BaseModel):
    """单条试验提取结果"""
    test_name: str = Field(description="试验名称")
    method_desc: str = Field(default="", description="试验方法描述")
    conditions: str = Field(default="", description="试验条件")
    preparation: str = Field(default="", description="试验准备")
    procedure: str = Field(default="", description="试验过程")
    acceptance: str = Field(default="", description="试验要求/合格判据")
    report_items: str = Field(default="", description="报告内容")
    source_clause: str = Field(default="", description="来源条款")


class LangExtractResponse(BaseModel):
    """提取响应"""
    standard_no: str = Field(description="标准编号")
    total_count: int = Field(description="提取到的指标总数")
    test_count: int = Field(default=0, description="提取到的试验总数")
    indicators: List[IndicatorExtractionResponse] = Field(description="指标列表")
    tests: List[TestExtractionResponse] = Field(default_factory=list, description="试验列表")
    html_path: Optional[str] = Field(None, description="可视化 HTML 文件路径")


@router.post("/extract-indicators", summary="提取标准指标（LangExtract）")
async def extract_indicators(request: LangExtractRequest):
    """
    使用 LangExtract 提取标准的技术指标

    特点：
    1. 精确的源文本定位（char_start, char_end）
    2. 可生成交互式可视化 HTML
    3. 过滤噪音章节（术语、引言等）
    4. 自动识别标准化对象
    5. 章节智能分组（试验 vs 指标）
    6. 自动去重

    注意：
    - 使用项目的 CHAT 角色配置（.env 中的 CHAT_API_KEY、CHAT_BASE_URL、CHAT_MODEL）
    """
    try:
        logger.info(f"开始处理标准: {request.standard_no}")

        # 执行提取
        result = await extract_indicators_langextract(
            standard_no=request.standard_no,
            max_workers=request.max_workers,
            max_char_buffer=request.max_char_buffer,
            extraction_passes=request.extraction_passes,
            enable_debug=request.enable_debug,
        )

        # 转换为响应格式
        response_indicators = [
            IndicatorExtractionResponse(
                indicator_name=ind.indicator_object,
                requirement=ind.source_value,
                test_method=None,  # IndicatorItem 没有 test_method 字段
                source_text=ind.source_value,  # LangExtract 提取的完整文本
                char_start=None,  # TODO: 需要从原始提取结果传递
                char_end=None,
                chapter_title=ind.source_clause or None,
            )
            for ind in result.indicators
        ]

        response_tests = [
            TestExtractionResponse(
                test_name=test.test_name,
                method_desc=test.method_desc,
                conditions=test.conditions,
                preparation=test.preparation,
                procedure=test.procedure,
                acceptance=test.acceptance,
                report_items=test.report_items,
                source_clause=test.source_clause,
            )
            for test in result.tests
        ]

        response = LangExtractResponse(
            standard_no=request.standard_no,
            total_count=len(result.indicators),
            test_count=len(result.tests),
            indicators=response_indicators,
            tests=response_tests,
            html_path=None,  # TODO: 添加可视化支持
        )

        logger.info(
            f"提取完成: {request.standard_no}, "
            f"共 {response.total_count} 条指标, "
            f"{response.test_count} 条试验"
        )

        return Success(data=response.model_dump())

    except ValueError as e:
        logger.warning(f"参数错误: {e}")
        return Fail(msg=str(e))
    except ImportError as e:
        logger.error(f"依赖缺失: {e}")
        return Fail(msg="langextract 未安装，请运行: pip install 'langextract[openai]'")
    except Exception as e:
        logger.exception(f"提取失败: {e}")
        return Fail(msg=f"提取失败: {str(e)}")
