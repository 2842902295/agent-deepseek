"""
LangExtract 工具 — 基于 Google LangExtract 的结构化文本提取

与现有提取方案的关系：
- fast_extraction.py：基于 LangChain + ReAct，面向大文档的分段并行提取
- langextract_tool：基于 LangExtract

使用场景：
1. 需要精确的源文本位置定位（char_interval）
2. 需要可视化标注结果
3. 对提取质量要求高，需要人工校验的场景
"""
from __future__ import annotations

import logging
import os
import textwrap
from typing import List, Optional

try:
    import langextract as lx
    from langextract.factory import ModelConfig
except ImportError:
    raise ImportError(
        "langextract 未安装。请运行: pip install 'langextract[openai]'"
    )

from loguru import logger
from pydantic import BaseModel, Field

from app.langchain.config import load_role


# 启用 LangExtract 详细日志
def enable_langextract_logging():
    """启用 LangExtract 的详细日志输出"""
    langextract_logger = logging.getLogger("langextract")
    langextract_logger.setLevel(logging.DEBUG)

    # 如果没有 handler，添加一个
    if not langextract_logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(name)s:%(funcName)s:%(lineno)d - %(message)s'
        )
        handler.setFormatter(formatter)
        langextract_logger.addHandler(handler)

    logger.info("LangExtract 详细日志已启用")


class IndicatorExtraction(BaseModel):
    """指标提取结果"""
    indicator_name: str = Field(description="指标名称（如'外观'、'硫含量'、'抗拉强度'）")
    requirement: str = Field(description="完整的指标要求（量化或非量化，包含限值、单位、条件等完整信息）")
    test_method: Optional[str] = Field(None, description="测试方法（标准号或方法描述）")
    source_text: str = Field(description="原文片段")
    char_start: Optional[int] = Field(None, description="字符起始位置")
    char_end: Optional[int] = Field(None, description="字符结束位置")
    attributes: Optional[dict] = Field(None, description="原始 attributes 字典（可选）")


class LangExtractTool:
    """LangExtract 工具封装"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_id: Optional[str] = None,
        enable_debug_logging: bool = True,
    ):
        """
        初始化工具

        Args:
            api_key: API Key（默认从项目 CHAT 角色配置读取）
            base_url: API 端点（默认从项目 CHAT 角色配置读取）
            model_id: 模型 ID（默认从项目 CHAT 角色配置读取）
            enable_debug_logging: 是否启用详细日志（默认 True）
        """
        # 启用详细日志
        if enable_debug_logging:
            enable_langextract_logging()

        # 从项目配置加载
        chat_config = load_role("CHAT")

        self.api_key = api_key or chat_config.api_key
        if not self.api_key:
            raise ValueError("未配置 API Key（检查 .env 中的 CHAT_API_KEY）")

        self.base_url = base_url or chat_config.base_url
        self.model_id = model_id or chat_config.model

        logger.info(
            f"LangExtract 初始化: model={self.model_id}, "
            f"base_url={self.base_url}"
        )

        self.model_config = ModelConfig(
            model_id=self.model_id,
            provider="openai",
            provider_kwargs={
                "api_key": self.api_key,
                "base_url": self.base_url,
            },
        )

    def get_default_prompt(self) -> str:
        """获取默认 prompt（标准指标提取）"""
        return textwrap.dedent("""\
            从标准文件文本中提取所有技术指标，包括量化指标和非量化指标。
            每个指标必须包含以下字段：
            - indicator_name: 指标名称（如"外观"、"硫含量"、"抗拉强度"、"使用环境"）
            - requirement: 完整的指标要求，必须包含所有限定条件，确保指标独立、健全、自洽
              * 量化指标：保留完整的限值、单位、条件（如"应不大于0.05%（质量分数）"、"初馏点不低于280℃，终馏点不高于365℃"）
              * 非量化指标：保留完整的描述性要求（如"应为无色或淡黄色透明液体，无可见机械杂质"、"应符合GB/T XXX中X级要求"）
              * 条件依赖：保留适用条件（如"当温度为25℃时，密度应为..."）
            - test_method: 测试方法（标准号如"GB/T 380"，或方法描述如"目视检查"；若无明确方法可留空）

            关键原则：
            1. requirement 必须是完整、自洽的陈述，脱离上下文也能理解
            2. 不要将限值、单位、条件拆分到不同字段
            3. 使用原文精确措辞，不要改写或省略
            4. 按出现顺序排列""")

    def get_default_examples(self) -> List[lx.data.ExampleData]:
        """获取默认 few-shot 示例（标准指标提取）"""
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
                            "test_method": "",
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
                            "test_method": "GB/T 380",
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
                            "test_method": "GB/T 6536",
                        },
                    ),
                ],
            ),
        ]

    def extract(
        self,
        text: str,
        prompt: Optional[str] = None,
        examples: Optional[List[lx.data.ExampleData]] = None,
        max_workers: int = 30,
        max_char_buffer: int = 1000,
        extraction_passes: int = 3,
        keep_attributes: bool = False,
        allow_ungrounded: bool = False,
    ) -> List[IndicatorExtraction]:
        """
        提取指标

        Args:
            text: 输入文本
            prompt: 自定义 prompt（可选）
            examples: 自定义示例（可选）
            max_workers: 并行处理的最大线程数（默认 10，可提高到 20-30）
            max_char_buffer: 每个分块的最大字符数（默认 8000，越大越快但可能漏提取）
            extraction_passes: 提取轮次（默认 1，增加到 2-3 可提高召回率但更慢）
            keep_attributes: 是否保留原始 attributes 字典（默认 False）
            allow_ungrounded: 是否允许未接地的提取结果（默认 False）

        Returns:
            指标提取结果列表
        """
        prompt = prompt or self.get_default_prompt()
        examples = examples or self.get_default_examples()

        logger.info(
            f"LangExtract 开始提取，文本长度: {len(text)} 字符, "
            f"并发: {max_workers}, 分块: {max_char_buffer}, 轮次: {extraction_passes}"
        )

        # 打印完整的提示词和正文（调试用）
        logger.info("=" * 80)
        logger.info("提示词 (Prompt):")
        logger.info(prompt)
        logger.info("=" * 80)
        logger.info("正文 (完整):")
        logger.info(text)
        logger.info("=" * 80)

        result = lx.extract(
            text_or_documents=text,
            prompt_description=prompt,
            examples=examples,
            config=self.model_config,
            max_workers=max_workers,
            max_char_buffer=max_char_buffer,
            extraction_passes=extraction_passes,
        )

        # 转换为兼容格式
        extractions = []
        grounded = [e for e in result.extractions if e.char_interval]
        ungrounded = [e for e in result.extractions if not e.char_interval]

        logger.info(
            f"提取完成: 总计 {len(result.extractions)} 条, "
            f"已接地 {len(grounded)} 条, 未接地 {len(ungrounded)} 条"
        )

        # 打印未接地的提取结果（调试用）
        if ungrounded:
            logger.warning(f"发现 {len(ungrounded)} 条未接地的提取结果:")
            for idx, ext in enumerate(ungrounded[:3]):  # 只打印前3条
                logger.warning(f"  未接地 {idx}: extraction_text={ext.extraction_text[:100]}..., attributes={ext.attributes}")

        # 打印已接地的 extraction 的 attributes（调试用）
        for idx, ext in enumerate(grounded[:3]):  # 只打印前3条
            logger.info(f"  已接地 {idx}: extraction_text={ext.extraction_text[:50]}..., attributes={ext.attributes}")

        # 根据 allow_ungrounded 决定是否包含未接地结果
        if allow_ungrounded:
            extraction_list = result.extractions
            logger.info(f"包含未接地结果，共处理 {len(extraction_list)} 条")
        else:
            extraction_list = grounded

        for ext in extraction_list:
            attrs = ext.attributes or {}
            span = ext.char_interval

            char_start = None
            char_end = None
            if span:
                char_start = getattr(span, 'start', getattr(span, 'begin', None))
                char_end = getattr(span, 'end', None)

            extractions.append(
                IndicatorExtraction(
                    indicator_name=attrs.get("indicator_name", ""),
                    requirement=attrs.get("requirement", ""),
                    test_method=attrs.get("test_method"),
                    source_text=ext.extraction_text,
                    char_start=char_start,
                    char_end=char_end,
                    attributes=attrs if keep_attributes else None,
                )
            )

        return extractions

    def extract_and_visualize(
        self,
        text: str,
        output_dir: str = "./langextract_output",
        prompt: Optional[str] = None,
        examples: Optional[List[lx.data.ExampleData]] = None,
        max_workers: int = 30,
        max_char_buffer: int = 1000,
        extraction_passes: int = 3,
    ) -> tuple[List[IndicatorExtraction], str]:
        """
        提取指标并生成可视化

        Args:
            text: 输入文本
            output_dir: 输出目录
            prompt: 自定义 prompt
            examples: 自定义示例
            max_workers: 并行处理的最大线程数
            max_char_buffer: 每个分块的最大字符数
            extraction_passes: 提取轮次

        Returns:
            (提取结果列表, HTML 文件路径)
        """
        import os

        prompt = prompt or self.get_default_prompt()
        examples = examples or self.get_default_examples()

        result = lx.extract(
            text_or_documents=text,
            prompt_description=prompt,
            examples=examples,
            config=self.model_config,
            max_workers=max_workers,
            max_char_buffer=max_char_buffer,
            extraction_passes=extraction_passes,
        )

        # 保存结果
        os.makedirs(output_dir, exist_ok=True)
        lx.io.save_annotated_documents(
            [result],
            output_name="results.jsonl",
            output_dir=output_dir
        )

        # 生成可视化
        html_path = None
        try:
            html = lx.visualize(f"{output_dir}/results.jsonl")
            html_content = html if isinstance(html, str) else html.data

            # LangExtract 生成的 HTML 可能缺少标准结构，补充完整的 HTML 框架
            if not html_content.strip().startswith('<!DOCTYPE') and not html_content.strip().startswith('<html'):
                html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>LangExtract Visualization</title>
</head>
<body>
{html_content}
</body>
</html>"""

            html_path = f"{output_dir}/visualization.html"
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info(f"可视化已生成: {html_path}")
        except Exception as e:
            logger.warning(f"可视化生成失败: {e}")

        # 转换为标准格式
        extractions = []
        grounded = [e for e in result.extractions if e.char_interval]

        for ext in grounded:
            attrs = ext.attributes or {}
            span = ext.char_interval

            char_start = None
            char_end = None
            if span:
                char_start = getattr(span, 'start', getattr(span, 'begin', None))
                char_end = getattr(span, 'end', None)

            extractions.append(
                IndicatorExtraction(
                    indicator_name=attrs.get("indicator_name", ""),
                    requirement=attrs.get("requirement", ""),
                    test_method=attrs.get("test_method"),
                    source_text=ext.extraction_text,
                    char_start=char_start,
                    char_end=char_end,
                )
            )

        return extractions, html_path or ""


# ============================================================
# 便捷函数：快速使用
# ============================================================

def extract_indicators(
    text: str,
    api_key: Optional[str] = None,
    model_id: Optional[str] = None,
) -> List[IndicatorExtraction]:
    """
    快速提取指标（使用项目配置）

    Args:
        text: 输入文本
        api_key: API Key（可选，默认使用项目配置）
        model_id: 模型 ID（可选，默认使用项目配置）

    Returns:
        提取结果列表
    """
    tool = LangExtractTool(api_key=api_key, model_id=model_id)
    return tool.extract(text)
