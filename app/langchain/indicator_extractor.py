"""
标准指标提取器

使用LLM从标准文档中提取具体的技术指标
"""

import asyncio
import re
from typing import List, Dict, Optional, Tuple

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from loguru import logger
from pydantic import BaseModel, Field

from app.langchain.llm_providers import get_llm
from app.models.standard.jgh_pdf import StandardJghPdf, StandardJghPdfChapter


class Indicator(BaseModel):
    """指标模型"""
    category: str = Field(description="指标大类")
    name: str = Field(description="指标名称")
    requirement: str = Field(description="指标要求内容")
    chapter_info: str = Field(description="所在章节信息")


class IndicatorExtractionResult(BaseModel):
    """指标提取结果"""
    indicators: List[Dict] = Field(description="提取到的指标列表，每个指标包含category、name、requirement、chapter_index字段")


class StandardIndicatorExtractor:
    """标准指标提取器"""

    def __init__(self, llm=None, batch_size: int = 20, temperature: float = 0.2, max_concurrent: Optional[int] = None):
        """
        初始化指标提取器

        Args:
            llm: LLM实例，如果为None则使用默认LLM
            batch_size: 每批处理的章节数量
            temperature: LLM温度参数
            max_concurrent: 最大并发批次数，默认使用配置文件中的 LLM_MAX_CONCURRENT
        """
        from app.langchain.config import langchain_config

        self.llm = llm or get_llm(temperature=temperature)
        self.batch_size = batch_size
        self.max_concurrent = max_concurrent if max_concurrent is not None else langchain_config.LLM_MAX_CONCURRENT

        # 系统提示词
        self.system_prompt = """你是一个专业的标准文档分析专家。请从标准文档的章节中提取具体的技术指标。

**什么是技术指标：**
技术指标是可测量、可验证、可对比的具体技术要求，分为定量指标和定性指标两大类：

**1. 定量指标**（可量化、可精确测量的技术要求）：
- 数值型指标：有明确数值、范围或限值（如"≥500 MPa"、"10-20℃"、"φ50±0.5 mm"）
- 性能参数：可通过试验测试得到具体数值的性能指标（如"抗拉强度≥500 MPa"、"硬度HRC 45-50"）
- 化学成分：有明确含量要求的成分指标（如"碳含量0.15%-0.25%"、"硫含量≤0.03%"）
- 物理参数：可测量的物理特性（如"密度7.85 g/cm³"、"熔点1500℃"）
- 尺寸规格：有明确尺寸要求的几何参数（如"长度1000±5 mm"、"壁厚≥3 mm"）
- 测试条件：可量化的测试方法和环境条件（如"在23℃±2℃、相对湿度50%±5%环境下测试"）

**2. 定性指标**（通过观察、检验、评价等方式判定的质量要求）：
- 外观质量：对外观的描述性要求（如"表面应光滑平整"、"不得有明显划痕"、"色泽应均匀一致"）
- 缺陷要求：对缺陷的禁止性规定（如"不得有裂纹"、"不允许有气孔"、"无分层现象"）
- 工艺质量：对加工工艺的质量要求（如"焊接应牢固"、"涂层应均匀"、"切割面应平整"）
- 等级评定：按等级或级别评定的质量标准（如"外观质量应符合A级要求"、"表面粗糙度应达到优良级"）
- 功能性描述：对功能的定性描述（如"应具有良好的耐腐蚀性"、"应便于安装和维护"）

**什么不是技术指标（务必排除）：**
- ❌ 概述性内容：总则、引言、背景介绍
- ❌ 规范性引用：引用的其他标准文件
- ❌ 通用性要求：过于宽泛、无法量化的原则性要求

**提取原则（必须严格遵守）：**
1. **必须具体**：指标必须有明确的技术要求，不能是笼统描述
2. **必须可测**：指标应该可以通过测量、试验或检验来验证
3. **必须技术**：指标必须是技术性内容，不是管理、定义或说明
4. **必须重要**：只提取对标准比对有实际意义的关键指标
5. **必须完整**：提取时要包含完整的要求内容，包括数值、单位、条件等

**提取格式：**
对每个指标，需要提取：
1. **指标大类**：规范统一的大类名称，如"尺寸要求"、"力学性能"、"化学成分"、"试验条件"等
2. **指标名称**：简洁明确的指标名称，如"抗拉强度"、"工作温度范围"、"外径"等
3. **指标要求**：该标准对这个指标的具体要求内容，必须包含数值、范围、条件等完整信息
4. **章节索引**：指标来自哪个章节（用章节编号标识，如章节1、章节2）

**示例（正确提取）：**

定量指标示例：
✅ 指标：抗拉强度 → 要求：≥500 MPa
✅ 指标：工作温度范围 → 要求：-40℃～+85℃
✅ 指标：外径尺寸 → 要求：φ50±0.5 mm
✅ 指标：碳含量 → 要求：0.15%-0.25%
✅ 指标：硬度 → 要求：HRC 45-50

定性指标示例：
✅ 指标：表面质量 → 要求：表面应光滑平整，不得有明显划痕和凹坑
✅ 指标：焊接质量 → 要求：焊缝应牢固，不得有裂纹、气孔和夹渣
✅ 指标：涂层要求 → 要求：涂层应均匀一致，无流挂、起泡和脱落现象
✅ 指标：外观等级 → 要求：外观质量应符合GB/T 1234中A级要求
✅ 指标：切割面质量 → 要求：切割面应平整，边缘无毛刺

**反例（不应提取）：**
❌ "本标准规定了XXX的术语和定义"（定义性描述）
❌ "适用于各类工业用途"（适用范围）
❌ "应符合相关质量管理要求"（通用性要求，过于宽泛）
❌ "产品应具有良好的性能"（过于笼统，无具体要求）

注意：
- chapter_index 是数字，表示指标来自第几个章节
- 严格按照上述原则筛选，宁缺毋滥
- 如果某个章节没有符合要求的技术指标，不要强行提取"""

    async def extract_indicators(
        self,
        standard: StandardJghPdf,
        chapters: List[StandardJghPdfChapter],
        enable_deduplication: bool = True
    ) -> List[Indicator]:
        """
        提取标准的所有指标（主方法，使用并发处理）

        Args:
            standard: 标准信息
            chapters: 章节列表
            enable_deduplication: 是否启用指标去重

        Returns:
            指标列表
        """
        logger.info(f"开始提取指标: {standard.standard_no}")

        # 标准主要信息（每段都会携带）
        standard_info = f"标准：{standard.cname} ({standard.standard_no})"

        # 过滤有效章节
        valid_chapters = [ch for ch in chapters if ch.word and ch.word.strip()]
        logger.info(f"有效章节数: {len(valid_chapters)}")

        if not valid_chapters:
            logger.warning("没有有效章节")
            return []

        # 将章节分批
        batches = []
        for i in range(0, len(valid_chapters), self.batch_size):
            batch_chapters = valid_chapters[i:i + self.batch_size]
            batches.append((i, batch_chapters))

        logger.info(f"共分为 {len(batches)} 个批次，最大并发数: {self.max_concurrent}")

        # 使用信号量控制并发数
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def process_batch_with_limit(batch_idx: int, batch_chapters: List[StandardJghPdfChapter]):
            """带并发控制的批次处理"""
            async with semaphore:
                batch_start = batch_idx + 1
                batch_end = min(batch_idx + self.batch_size, len(valid_chapters))
                logger.info(f"开始处理批次 {batch_start}-{batch_end}/{len(valid_chapters)}")

                try:
                    indicators = await self._extract_indicators_from_batch(
                        batch_chapters,
                        standard_info
                    )
                    logger.info(f"批次 {batch_start}-{batch_end} 完成，提取到 {len(indicators)} 个指标")
                    return indicators
                except Exception as e:
                    logger.error(f"批次 {batch_start}-{batch_end} 处理失败: {e}")
                    return []

        # 并发执行所有批次
        tasks = [
            process_batch_with_limit(batch_idx, batch_chapters)
            for batch_idx, batch_chapters in batches
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 汇总结果
        all_indicators = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"批次处理异常: {str(result)}")
                continue
            if isinstance(result, list):
                all_indicators.extend(result)

        logger.info(f"指标提取完成，共 {len(all_indicators)} 个指标")

        # 后处理：过滤和补充指标
        if all_indicators:
            all_indicators = self.post_process_indicators(all_indicators, chapters)
            logger.info(f"后处理完成，剩余 {len(all_indicators)} 个指标")

        # 去重
        if enable_deduplication and all_indicators:
            all_indicators = self.deduplicate_indicators(all_indicators)
            logger.info(f"去重后剩余 {len(all_indicators)} 个指标")

        return all_indicators

    async def _extract_indicators_from_batch(
        self,
        chapters: List[StandardJghPdfChapter],
        standard_info: str
    ) -> List[Indicator]:
        """
        从一批章节中提取指标

        Args:
            chapters: 章节列表
            standard_info: 标准基本信息

        Returns:
            指标列表
        """
        # 构建章节映射（用于后续根据索引还原章节信息）
        chapter_map = {}
        chapter_contents = []

        for idx, chapter in enumerate(chapters, start=1):
            # 构建章节标题（避免重复）
            if chapter.title_no and chapter.title:
                # 检查 title 是否已经包含 title_no
                if chapter.title.strip().startswith(chapter.title_no.strip()):
                    # 已经包含，直接使用 title
                    title = chapter.title.strip()
                else:
                    # 不包含，需要拼接
                    title = f"{chapter.title_no} {chapter.title}".strip()
            elif chapter.title:
                title = chapter.title.strip()
            elif chapter.title_no:
                title = chapter.title_no.strip()
            else:
                title = "未命名章节"

            # 保存章节映射
            chapter_map[idx] = title

            # 构建带编号的章节内容
            chapter_contents.append(f"【章节{idx}: {title}】\n{chapter.word}")

        content_text = "\n\n".join(chapter_contents)

        # 用户消息
        user_message = f"""{standard_info}

当前分段章节内容：
{content_text}

请提取这些章节中的所有具体技术指标，并标注每个指标来自哪个章节（用章节编号）。"""

        try:
            # 使用 Agent API 进行结构化输出
            agent = create_agent(
                model=self.llm,
                tools=[],
                response_format=ToolStrategy(
                    schema=IndicatorExtractionResult,
                    handle_errors=True  # 自动捕获验证错误并重试
                ),
                system_prompt=self.system_prompt
            )

            # 调用 agent
            agent_result = await agent.ainvoke({
                "messages": [{"role": "user", "content": user_message}]
            })

            # 从 agent 结果中提取结构化响应
            result: IndicatorExtractionResult = agent_result["structured_response"]

            # 解析为Indicator对象，并硬编码填充真实的章节信息
            indicators_data = result.indicators
            indicators = []

            for ind_data in indicators_data:
                # 获取章节索引
                chapter_idx = ind_data.get("chapter_index", 1)

                # 从映射中获取真实的章节信息（硬编码）
                chapter_info = chapter_map.get(chapter_idx, "未知章节")

                # 创建Indicator对象，用硬编码的章节信息
                indicator = Indicator(
                    category=ind_data.get("category", ""),
                    name=ind_data.get("name", ""),
                    requirement=ind_data.get("requirement", ""),
                    chapter_info=chapter_info  # 硬编码的章节信息
                )
                indicators.append(indicator)

            return indicators
        except Exception as e:
            logger.error(f"提取指标失败: {str(e)}")
            return []

    def deduplicate_indicators(self, indicators: List[Indicator]) -> List[Indicator]:
        """
        指标去重（基于指标名称和大类）

        Args:
            indicators: 原始指标列表

        Returns:
            去重后的指标列表
        """
        if not indicators:
            return []

        # 去重：基于指标大类+名称
        seen = set()
        unique_indicators = []

        for indicator in indicators:
            # 标准化文本用于比较
            key = f"{indicator.category}::{indicator.name}".strip().lower()

            if key not in seen:
                seen.add(key)
                unique_indicators.append(indicator)

        return unique_indicators

    def post_process_indicators(
        self,
        indicators: List[Indicator],
        chapters: List[StandardJghPdfChapter]
    ) -> List[Indicator]:
        """
        后处理指标：使用正则表达式过滤和补充指标内容

        处理逻辑：
        1. 过滤掉仅包含"按表X"、"见表X"、"如图X"等引用的指标
        2. 保留包含"按GB/T"、"按ISO"等标准引用的指标
        3. 识别并补充章节引用（如"见X.X章节"）的详细内容

        Args:
            indicators: 原始指标列表
            chapters: 所有章节列表（用于查找引用的章节）

        Returns:
            处理后的指标列表
        """
        if not indicators:
            return []

        # 构建章节映射（用于查找章节引用）
        chapter_map = self._build_chapter_map(chapters)

        processed_indicators = []
        filtered_count = 0
        enhanced_count = 0

        for indicator in indicators:
            requirement = indicator.requirement

            # 1. 检查是否应该过滤掉
            if self._should_filter_indicator(requirement):
                filtered_count += 1
                logger.debug(f"过滤指标: {indicator.name} - {requirement}")
                continue

            # 2. 尝试补充章节引用
            enhanced_requirement, was_enhanced = self._enhance_chapter_reference(
                requirement,
                chapter_map
            )

            if was_enhanced:
                enhanced_count += 1
                logger.debug(f"补充指标: {indicator.name}")
                # 创建新的指标对象（更新requirement）
                indicator = Indicator(
                    category=indicator.category,
                    name=indicator.name,
                    requirement=enhanced_requirement,
                    chapter_info=indicator.chapter_info
                )

            processed_indicators.append(indicator)

        logger.info(f"后处理完成：过滤 {filtered_count} 个指标，补充 {enhanced_count} 个指标")
        return processed_indicators

    def _build_chapter_map(self, chapters: List[StandardJghPdfChapter]) -> Dict[str, str]:
        """
        构建章节号到章节内容的映射

        Args:
            chapters: 章节列表

        Returns:
            章节号 -> 章节内容的映射
        """
        chapter_map = {}
        for chapter in chapters:
            if chapter.title_no and chapter.word:
                # 规范化章节号（去除空格）
                title_no = chapter.title_no.strip()
                chapter_map[title_no] = chapter.word.strip()
        return chapter_map

    def _should_filter_indicator(self, requirement: str) -> bool:
        """
        判断指标是否应该被过滤掉

        过滤规则：
        1. 仅包含"按表X"、"见表X"、"详见表X"等表格引用
        2. 仅包含"如图X"、"见图X"、"按图X"等图片引用
        3. 仅包含"见X章"、"见X.X"、"参照X.X"等章节引用（不含实质内容）

        保留规则：
        1. 包含"按GB/T"、"按ISO"、"按JIS"等标准引用
        2. 包含具体数值、技术要求的内容

        Args:
            requirement: 指标要求文本

        Returns:
            True表示应该过滤掉，False表示保留
        """
        if not requirement or not requirement.strip():
            return True

        requirement = requirement.strip()

        # 保留规则：包含标准引用（GB/T、ISO、JIS等）
        standard_patterns = [
            r'按\s*GB[/\s]*T',
            r'按\s*ISO',
            r'按\s*JIS',
            r'按\s*EN',
            r'符合\s*GB[/\s]*T',
            r'符合\s*ISO',
            r'依据\s*GB[/\s]*T',
            r'依据\s*ISO'
        ]
        for pattern in standard_patterns:
            if re.search(pattern, requirement, re.IGNORECASE):
                logger.debug(f"保留标准引用: {requirement[:50]}...")
                return False

        # 过滤规则1：仅包含表格引用
        table_only_patterns = [
            r'^(按|见|详见|参见)\s*表\s*[\d\.]+\s*$',
            r'^表\s*[\d\.]+\s*$',
            r'^(详见|参见|见)\s*表\s*[\d\.]+\s*(所示)?$',
            # 匹配"XXX见表X"（长前缀情况）
            r'^.{0,50}见\s*表\s*[\d\.]+\s*$',
            # 匹配"XXX应符合表X的规定"、"XXX按表X的要求"等格式
            r'^.{0,40}(应|须|应当)?\s*(符合|满足|按照|依据|遵守|按)\s*表\s*[\d\.]+\s*(的)?\s*(规定|要求|内容|标准)?\s*(执行|实施)?\s*$',
            # 匹配"XXX按表X执行"等格式
            r'^.{0,40}(按|依据|参照)\s*表\s*[\d\.]+\s*(执行|实施)?\s*$'
        ]
        for pattern in table_only_patterns:
            if re.match(pattern, requirement):
                logger.debug(f"过滤表格引用: {requirement}")
                return True

        # 过滤规则2：仅包含图片引用
        figure_only_patterns = [
            r'^(按|见|如|详见|参见)\s*图\s*[\d\.]+\s*$',
            r'^图\s*[\d\.]+\s*$',
            r'^(详见|参见|见|如)\s*图\s*[\d\.]+\s*(所示)?$'
        ]
        for pattern in figure_only_patterns:
            if re.match(pattern, requirement):
                logger.debug(f"过滤图片引用: {requirement}")
                return True

        # 过滤规则3：仅包含章节引用（且内容很短，说明没有实质内容）
        if len(requirement) < 30:  # 短文本才可能是纯引用
            chapter_only_patterns = [
                r'^(见|参照|参见|详见)\s*[\d\.]+\s*(章|节|条|款)?\s*$',
                r'^[\d\.]+\s*(章|节|条|款)\s*$',
                r'^(见|参照|参见|详见)\s*第?\s*[\d\.]+\s*(章|节|条|款)?\s*$'
            ]
            for pattern in chapter_only_patterns:
                if re.match(pattern, requirement):
                    logger.debug(f"过滤章节引用: {requirement}")
                    return True

        # 默认保留
        return False

    def _enhance_chapter_reference(
        self,
        requirement: str,
        chapter_map: Dict[str, str]
    ) -> Tuple[str, bool]:
        """
        识别并补充章节引用的详细内容

        如果指标要求中提到"见X.X章节"等，尝试找到对应章节的内容并补充

        Args:
            requirement: 原始要求文本
            chapter_map: 章节号 -> 章节内容的映射

        Returns:
            (补充后的文本, 是否进行了补充)
        """
        if not requirement or not chapter_map:
            return requirement, False

        # 匹配章节引用模式：见X.X、参照X.X、详见第X章等
        chapter_ref_patterns = [
            r'(见|参照|参见|详见)\s*(第\s*)?(\d+(?:\.\d+)*)\s*(章|节|条|款)?',
        ]

        enhanced = False
        result = requirement

        for pattern in chapter_ref_patterns:
            matches = list(re.finditer(pattern, requirement))

            for match in matches:
                chapter_no = match.group(3)  # 提取章节号

                # 在映射中查找对应章节
                if chapter_no in chapter_map:
                    chapter_content = chapter_map[chapter_no]

                    # 限制补充内容长度（避免过长）
                    max_length = 200
                    if len(chapter_content) > max_length:
                        chapter_content = chapter_content[:max_length] + "..."

                    # 补充内容
                    replacement = f"{match.group(0)}（{chapter_content}）"
                    result = result[:match.start()] + replacement + result[match.end():]
                    enhanced = True

                    logger.debug(f"补充章节引用: {chapter_no}")
                    break  # 只补充第一个匹配

                if enhanced:
                    break

            if enhanced:
                break

        return result, enhanced

    def classify_indicators(self, indicators: List[Indicator]) -> Dict[str, List[Indicator]]:
        """
        指标分类（按指标大类）

        Args:
            indicators: 指标列表

        Returns:
            分类后的指标字典
        """
        classification = {}

        for indicator in indicators:
            category = indicator.category
            if category not in classification:
                classification[category] = []
            classification[category].append(indicator)

        return classification

    def get_statistics(self, indicators: List[Indicator]) -> Dict:
        """
        获取指标统计信息

        Args:
            indicators: 指标列表

        Returns:
            统计信息字典
        """
        classification = self.classify_indicators(indicators)

        stats = {
            "total_count": len(indicators),
            "by_category": {
                category: len(inds)
                for category, inds in classification.items()
            },
            "average_requirement_length": sum(len(ind.requirement) for ind in indicators) / len(indicators) if indicators else 0
        }

        return stats


def get_indicator_extractor(**kwargs) -> StandardIndicatorExtractor:
    """
    获取指标提取器的便捷函数

    Args:
        **kwargs: 传递给 StandardIndicatorExtractor 的参数

    Returns:
        StandardIndicatorExtractor 实例
    """
    return StandardIndicatorExtractor(**kwargs)


__all__ = ["StandardIndicatorExtractor", "Indicator", "get_indicator_extractor"]
