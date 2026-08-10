"""
Deep Agents 基本使用示例

展示如何用 deepagents 创建一个具备任务规划、文件操作和子代理委托能力的智能体。
运行方式：python -m app.langchain.examples.deepagents_example
"""

import asyncio
import json
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, SystemMessage
from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver

from app.langchain.llm_providers import get_llm, LLMProvider
from app.langchain.config import langchain_config


# ── 自定义工具 ────────────────────────────────────────────────────────────────

@tool
def get_current_time() -> str:
    """获取当前系统时间"""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def add_numbers(a: float, b: float) -> float:
    """计算两个数字之和"""
    return a + b


# ── 工具函数：打印消息详情 ─────────────────────────────────────────────────────

def print_separator(title: str = ""):
    line = "─" * 60
    if title:
        print(f"\n┌{line}┐")
        print(f"│  {title:<58}│")
        print(f"└{line}┘")
    else:
        print(f"{'─' * 62}")


def print_message_detail(msg, index: int):
    """打印单条消息的详细信息"""
    msg_type = type(msg).__name__

    if isinstance(msg, SystemMessage):
        print(f"  [{index}] 🔧 SystemMessage")
        content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
        # 只显示前 80 字符
        preview = content[:80] + "..." if len(content) > 80 else content
        print(f"       内容: {preview}")

    elif isinstance(msg, HumanMessage):
        print(f"  [{index}] 👤 HumanMessage")
        print(f"       内容: {msg.content}")

    elif isinstance(msg, AIMessage):
        print(f"  [{index}] 🤖 AIMessage")
        # 文本内容
        if msg.content:
            content_str = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
            preview = content_str[:100] + "..." if len(content_str) > 100 else content_str
            print(f"       文本: {preview}")
        # 工具调用
        if msg.tool_calls:
            for tc in msg.tool_calls:
                print(f"       ▶ 调用工具: {tc['name']}")
                print(f"         参数: {json.dumps(tc['args'], ensure_ascii=False)}")
                print(f"         tool_call_id: {tc['id']}")
        # usage_metadata
        if hasattr(msg, "usage_metadata") and msg.usage_metadata:
            um = msg.usage_metadata
            print(f"       Token: 输入={um.get('input_tokens','-')} 输出={um.get('output_tokens','-')}")

    elif isinstance(msg, ToolMessage):
        print(f"  [{index}] 🔩 ToolMessage  (tool_call_id: {msg.tool_call_id})")
        print(f"       工具名: {msg.name}")
        content_str = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
        print(f"       返回值: {content_str}")

    else:
        print(f"  [{index}] ❓ {msg_type}: {str(msg)[:100]}")


def print_turn_detail(question: str, result: dict):
    """打印本轮对话的完整消息链路"""
    messages = result.get("messages", [])

    print_separator(f"用户: {question}")

    # 找出本轮新增的消息（排除 SystemMessage 和之前轮次的历史）
    # 实际上 invoke 返回的是完整对话历史，我们打印全部以便理解
    print(f"\n  本次 invoke 返回消息总数: {len(messages)}")
    print()

    for i, msg in enumerate(messages):
        print_message_detail(msg, i)

    # 最终答案
    last = messages[-1]
    final = last.content if isinstance(last.content, str) else json.dumps(last.content, ensure_ascii=False)
    print(f"\n  ✅ 最终答案: {final}")


# ── 创建 Agent ────────────────────────────────────────────────────────────────

def create_example_agent():
    """创建示例 Deep Agent"""
    llm = get_llm()

    agent = create_deep_agent(
        model=llm,
        tools=[get_current_time, add_numbers],
        system_prompt="你是一个智能助手，可以查询时间和进行数学计算。请用中文回复。",
        checkpointer=MemorySaver(),
    )
    return agent


# ── 流式模式：逐步打印每个 LangGraph 节点的输出 ───────────────────────────────

def run_with_stream(agent, question: str, config: dict):
    """使用 stream 模式，实时打印每个 LangGraph 节点的状态更新"""
    print_separator(f"[STREAM] 用户: {question}")
    print()

    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": question}]},
        config=config,
        stream_mode="updates",   # 每个节点的增量更新
    ):
        for node_name, node_output in chunk.items():
            print(f"  📦 节点: {node_name}")
            for msg in node_output.get("messages", []):
                print_message_detail(msg, "→")
            print()



async def main():
    agent = create_example_agent()
    config = {"configurable": {"thread_id": "demo-detail-001"}}

    invoke_questions = [
        "现在几点了？",
        "帮我计算 123 + 456 等于多少？",
        "你还记得我刚才问了什么问题吗？",
    ]
    for q in invoke_questions:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": q}]},
            config=config,
        )
        print_turn_detail(q, result)


if __name__ == "__main__":
    asyncio.run(main())
