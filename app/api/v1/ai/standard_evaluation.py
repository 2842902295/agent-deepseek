"""
标准整合评估 API

入参：标准号
流程：查询标准基础信息 → DeepAgent 分析关联关系 → 输出整合评估结论
"""

import asyncio
from typing import Optional, AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from app.langchain.agents.base import _text
from app.langchain.agents.tasks.standard.standard_evaluation_agent import (
    create_standard_evaluation_agent,
    build_evaluation_task,
)
from app.schemas.base import Success, Fail

router = APIRouter(prefix="/standard-evaluation", tags=["标准整合评估"])

# Agent 单例（懒初始化）
_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        _agent = create_standard_evaluation_agent()
    return _agent


# ── Schemas ───────────────────────────────────────────────────────────────────

class EvaluationRequest(BaseModel):
    """标准整合评估请求"""
    standard_no: str = Field(..., description="标准号，如 GB/T 18336")
    thread_id: Optional[str] = Field(None, description="会话线程 ID（留空自动生成）")


class EvaluationResponse(BaseModel):
    """标准整合评估响应"""
    standard_no: str = Field(description="标准号")
    conclusion: str = Field(description="整合评估结论（Agent 生成）")
    steps: int = Field(description="Agent 执行步骤数")


# ── 接口：同步评估（等待 Agent 完成后返回） ────────────────────────────────────

@router.post("/evaluate", summary="标准整合评估（同步）")
async def evaluate_standard(request: EvaluationRequest):
    """
    对指定标准号进行整合评估。

    - Agent 自主查询数据库，分析目标标准及其关联标准
    - 返回结构化评估结论

    注意：Agent 可能需要多步推理，响应时间较长（建议使用流式接口）
    """
    try:
        from langchain_core.messages import AIMessage

        agent = _get_agent()
        task = build_evaluation_task(request.standard_no)
        thread_id = request.thread_id or f"eval-{request.standard_no}"
        config = {"configurable": {"thread_id": thread_id}}


        steps = 0
        conclusion = ""

        for msg, _ in agent.stream(
            {"messages": [{"role": "user", "content": task}]},
            config=config,
            stream_mode="messages",
        ):
            if isinstance(msg, AIMessage):
                steps += 1
                text = _text(msg.content).strip()
                if text and not msg.tool_calls:
                    conclusion = text  # 取最后一条非工具调用的 AI 消息作为结论

        if not conclusion:
            return Fail(code="5001", msg="Agent 未生成有效评估结论")

        return Success(
            data=EvaluationResponse(
                standard_no=request.standard_no,
                conclusion=conclusion,
                steps=steps,
            ).model_dump(),
            msg="评估完成",
        )

    except Exception as e:
        logger.exception(f"标准整合评估失败：standard_no={request.standard_no}")
        return Fail(code="5000", msg=f"评估失败：{str(e)}")


# ── 接口：流式评估（SSE，逐步返回 Agent 思考过程） ────────────────────────────

@router.post("/evaluate/stream", summary="标准整合评估（流式）")
async def evaluate_standard_stream(request: EvaluationRequest):
    """
    流式返回 Agent 的逐步推理过程（Server-Sent Events）。

    前端可逐步展示 Agent 的每个动作，提升交互体验。
    """
    from langchain_core.messages import AIMessage, ToolMessage

    from app.api.v1.ai.agent_skill import get_current_role_codes
    from app.services.agent_runtime.timeline_sanitize import sanitize_tool_args, sanitize_tool_result

    # 时间线入参脱敏豁免：超管/管理员保留原始入参细节（排查用），普通用户脱敏
    _rc_uid, _rc_codes, _rc_is_super = await get_current_role_codes()
    timeline_details = _rc_is_super or "R_ADMIN" in _rc_codes

    agent = _get_agent()
    task = build_evaluation_task(request.standard_no)
    thread_id = request.thread_id or f"eval-stream-{request.standard_no}"
    config = {"configurable": {"thread_id": thread_id}}

    async def event_generator() -> AsyncGenerator[str, None]:
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def sync_producer():
            try:
                step = 0
                for msg, _ in agent.stream(
                    {"messages": [{"role": "user", "content": task}]},
                    config=config,
                    stream_mode="messages",
                ):
                    if isinstance(msg, AIMessage):
                        for tc in msg.tool_calls or []:
                            step += 1
                            _args = tc.get("args", {}) or {}
                            loop.call_soon_threadsafe(queue.put_nowait, {
                                "type": "tool_call", "step": step,
                                "tool": tc["name"],
                                "args": _args if timeline_details else sanitize_tool_args(tc["name"], _args),
                            })
                        if text := _text(msg.content).strip():
                            step += 1
                            event_type = "thinking" if msg.tool_calls else "conclusion"
                            loop.call_soon_threadsafe(queue.put_nowait, {
                                "type": event_type, "step": step, "content": text,
                            })
                    elif isinstance(msg, ToolMessage):
                        step += 1
                        _res = _text(msg.content)
                        if not timeline_details:
                            _res = sanitize_tool_result(msg.name or "", _res, getattr(msg, "status", "") == "error")
                        loop.call_soon_threadsafe(queue.put_nowait, {
                            "type": "tool_result", "step": step,
                            "tool": msg.name, "content": _res,
                        })
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "done", "steps": step})
            except Exception as e:
                logger.exception("流式评估异常")
                loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(e)})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # 哨兵，通知消费者结束

        loop.run_in_executor(None, sync_producer)

        while True:
            item = await queue.get()
            if item is None:
                break
            yield _sse(item)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── 工具函数 ───────────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    """格式化为 SSE 数据帧"""
    import json
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
