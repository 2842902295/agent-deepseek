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

# 项目根（本文件位于 app/services/agent_runtime/ 下，向上四级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def default_user_workspace(user_id: Optional[int]) -> Path:
    """按 uid 推导用户持久工作目录：<项目根>/.agent_workspace/users/{uid}/。

    workspace 对 uid 是纯函数（与 qa.py::_user_workspace 同口径，此处不 mkdir）。
    HTTP 桥重建调用上下文时若回合登记缺失，用本函数兜底——保证相对路径永远解析到
    用户自己的目录，而不是项目根工作区。
    """
    uid = str(user_id) if user_id else "anonymous"
    return _PROJECT_ROOT / ".agent_workspace" / "users" / uid


def set_agent_call_context(ctx: Optional[AgentCallContext]) -> None:
    """直接覆盖（不依赖 token）。跨 task / generator 调用更安全。"""
    CTX_AGENT_CALL.set(ctx)


def clear_agent_call_context() -> None:
    CTX_AGENT_CALL.set(None)


def get_agent_call_context() -> Optional[AgentCallContext]:
    return CTX_AGENT_CALL.get()
