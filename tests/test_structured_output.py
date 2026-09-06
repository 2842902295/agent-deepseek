"""
验证 StructuredQAAgent 能否正常完成工具调用 + 结构化输出。
运行：python -m pytest tests/test_structured_output.py -s -v
"""

import pytest
from pydantic import BaseModel, Field


class SimpleResult(BaseModel):
    answer: str = Field(description="回答内容")
    confidence: float = Field(description="置信度 0~1")


class DBQueryResult(BaseModel):
    table_count: int = Field(description="数据库中的表数量")
    table_names: list[str] = Field(description="所有表名列表")


@pytest.mark.asyncio
async def test_simple_structured_output():
    """无工具调用，验证基础结构化输出"""
    from app.langchain.agents.structured_agent import create_structured_qa_agent

    agent = create_structured_qa_agent(
        output_schema=SimpleResult,
        system_prompt="你是一个简单的问答助手，直接回答问题并给出置信度。",
    )
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "1+1等于几？"}]},
        config={"configurable": {"thread_id": "test-simple-001"}},
    )
    print(f"\nanswer: {result.answer}, confidence: {result.confidence}")
    assert isinstance(result, SimpleResult)
    assert result.answer
    assert 0.0 <= result.confidence <= 1.0
