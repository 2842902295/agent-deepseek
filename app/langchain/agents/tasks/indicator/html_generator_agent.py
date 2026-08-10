"""
HTML 展示生成

将已匹配指标的结构化数据转换为简洁、美观的 HTML 片段（仅 matched 部分）。
不依赖工具，直接接收数据，通过单次 LLM 调用生成。
"""

from __future__ import annotations

import json
from typing import List

from langchain_core.prompts import ChatPromptTemplate
from loguru import logger
from pydantic import BaseModel, Field

from app.langchain.llm_providers import get_llm


class HtmlOutput(BaseModel):
    html_content: str = Field(description="HTML 片段（含内联 CSS，无需 DOCTYPE/body 包裹）")


_SYSTEM_PROMPT = """\
你是一位前端展示专家，任务是将已匹配的技术指标对比数据转换为简洁美观的 HTML 片段。

## 输入数据格式

JSON 数组，每条记录包含：
- indicator_type：static=静态指标 | dynamic=动态指标
- standard_object：标准化对象名称
- change_analysis：一致/收紧/放宽/扩充范围/缩减范围/指标替换/引用标准更新 等（文字表述不同但含义相同归为「一致」）

静态指标额外字段：indicator_object, source_value, source_clause, target_value, target_clause
动态指标额外字段：experiment_name, source_input_params, source_process_logic, source_result,
               source_clause, target_input_params, target_process_logic, target_result, target_clause

## HTML 设计要求

**静态指标**：紧凑表格
- 列：标准化对象 | 指标对象 | 源要求 | 目标要求 | 变化

**动态指标**：使用 <details> 折叠
- <summary> 只显示：实验名称 | 源判定结果 → 目标判定结果 | 变化
- 展开后显示：输入参数、过程逻辑对比

**变化标签颜色**（内联 style）：
- 一致：background:#e8f5e9;color:#2e7d32
- 收紧/缩减范围：background:#fff3e0;color:#e65100
- 放宽/扩充范围：background:#fce4ec;color:#c62828
- 其他：background:#f5f5f5;color:#616161

**其他样式规范**：
- 内联 CSS，无外部依赖
- 表格斑马纹（:nth-child(even) 用 #fafafa）
- 输出纯 HTML 片段，不要包含 <!DOCTYPE>、<html>、<head>、<body>
"""

_prompt = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM_PROMPT),
    ("user", "源标准：{source_no}\n目标标准：{target_no}\n\n以下是已匹配的指标数据：\n{data}"),
])

_chain = None


def _get_chain():
    global _chain
    if _chain is None:
        llm = get_llm()
        _chain = _prompt | llm.with_structured_output(HtmlOutput)
    return _chain


async def generate_comparison_html(
        matched_items: List[dict],
        source_no: str,
        target_no: str,
) -> str:
    """
    将 matched 指标数据转换为 HTML 片段。
    直接接收结构化数据，单次 LLM 调用，无工具依赖。
    """
    if not matched_items:
        return "<p style='color:#999;padding:16px'>暂无匹配指标</p>"

    chain = _get_chain()
    result: HtmlOutput = await chain.ainvoke({
        "source_no": source_no,
        "target_no": target_no,
        "data": json.dumps(matched_items, ensure_ascii=False),
    })

    logger.info(f"[HTML] 生成完成，{len(matched_items)} 条匹配指标，{len(result.html_content)} 字符")
    return result.html_content


__all__ = ["generate_comparison_html", "HtmlOutput"]
