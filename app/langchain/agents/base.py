"""
Agent 运行基础工具

提供统一的流式输出打印器，适用于所有基于 DeepAgents 的 Agent。
"""

from __future__ import annotations

from typing import Any, Iterator


def _text(content: Any) -> str:
    """将 LangChain 消息 content 统一转为纯文本字符串。"""
    if isinstance(content, list):
        return "".join(
            i["text"] if isinstance(i, dict) and "text" in i else str(i)
            for i in content
        )
    return str(content or "")


def run_agent_stream(
    agent,
    task: str,
    config: dict,
    *,
    max_result_chars: int = 300,
    max_args_chars: int = 120,
) -> None:
    """
    以流式方式运行 Agent 并按步骤打印输出。

    每个步骤对应一次工具调用、工具结果或模型回复。

    Args:
        agent:           deepagents 创建的 agent 实例（支持 .stream）
        task:            用户任务文本
        config:          LangGraph 配置，需包含 configurable.thread_id
        max_result_chars: 工具返回结果的最大显示字符数
        max_args_chars:  工具参数的最大显示字符数

    Example:
        agent = create_sqlite_agent(db_path="/path/to/db.sqlite3")
        run_agent_stream(
            agent,
            task="查询 users 表的前 5 条记录",
            config={"configurable": {"thread_id": "demo-001"}},
        )
    """
    from langchain_core.messages import AIMessage, ToolMessage

    step = 0

    for msg, _ in agent.stream(
        {"messages": [{"role": "user", "content": task}]},
        config=config,
        stream_mode="messages",
    ):
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                step += 1
                args = str(tc.get("args", {}))
                truncated = args[:max_args_chars] + ("..." if len(args) > max_args_chars else "")
                print(f"\n[步骤 {step}] 🔧 {tc['name']}  {truncated}")
            if text := _text(msg.content).strip():
                step += 1
                icon = "💭" if msg.tool_calls else "💬"
                print(f"\n[步骤 {step}] {icon}  {text}")

        elif isinstance(msg, ToolMessage):
            step += 1
            result = _text(msg.content)
            truncated = result[:max_result_chars] + ("..." if len(result) > max_result_chars else "")
            print(f"\n[步骤 {step}] 📋 [{msg.name}]  {truncated}")
