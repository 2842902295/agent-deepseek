"""
Agent 调用上下文：把当前的 session/message 传到工具函数里。
工具（tool）函数不能直接收到这些信息，所以走 ContextVar。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AgentCallContext:
    session_id: Optional[int] = None
    session_key: Optional[str] = None
    message_id: Optional[int] = None
    workspace_dir: Optional[Path] = None
    # 工作流画板「选中节点协作」作用域（qa.py 从请求透传）：
    # {"workflow_key": str, "focus_id": str, "scope_ids": list[str]}
    # 仅 edit_workflow_board 消费：写操作限在 scope_ids 内，越界跳过并报告；为 None 即全板模式。
    workflow_scope: Optional[dict] = None


CTX_AGENT_CALL: contextvars.ContextVar[Optional[AgentCallContext]] = contextvars.ContextVar(
    "agent_call_ctx", default=None
)


def set_agent_call_context(ctx: Optional[AgentCallContext]) -> None:
    """直接覆盖（不依赖 token）。跨 task / generator 调用更安全。"""
    CTX_AGENT_CALL.set(ctx)


def clear_agent_call_context() -> None:
    CTX_AGENT_CALL.set(None)


def get_agent_call_context() -> Optional[AgentCallContext]:
    return CTX_AGENT_CALL.get()
