"""
SQLite Agent 工厂

创建可操作本地 SQLite 数据库的 Deep Agent。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from langgraph.checkpoint.memory import MemorySaver

from app.langchain.llm_providers import get_llm
from app.langchain.tools.db_tools import make_db_tools


def create_sqlite_agent(
    db_path: str | Path,
    *,
    model: Optional[str] = None,
    extra_system_prompt: str = "",
):
    """
    创建绑定到指定 SQLite 数据库文件的 Deep Agent。

    Agent 拥有三个内置工具：
      - standard_tables()        — 列出所有标准表
      - standard_schema(table)   — 查看表结构
      - standard_query(sql)      — 执行只读 SQL（仅限 standard_ 前缀表）
    """
    from deepagents import create_deep_agent

    db_path = str(Path(db_path).resolve())
    llm = get_llm(model=model)
    tools = make_db_tools()

    system_prompt = (
        "你是一个标准数据库助手（MySQL）。\n"
        "可用工具：\n"
        "  standard_tables()          — 列出所有标准表\n"
        "  standard_schema(table)     — 查看表结构\n"
        "  standard_query(sql)        — 执行只读 SQL（仅限 standard_ 前缀表）\n"
        "操作完成后用中文简要汇报结果。"
    )
    if extra_system_prompt:
        system_prompt += f"\n{extra_system_prompt}"

    return create_deep_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=MemorySaver(),
    )
