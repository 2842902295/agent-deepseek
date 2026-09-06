"""
工作流相关 API

提供 LangGraph 工作流的 API 接口
"""

from typing import Optional

from fastapi import APIRouter
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.langchain.graphs import (
    create_simple_chat_graph,
    create_multi_step_workflow,
    create_reflection_workflow
)

router = APIRouter(prefix="/workflows", tags=["Workflows"])


class SimpleGraphRequest(BaseModel):
    """简单图请求"""
    message: str = Field(..., description="用户消息")
    provider: Optional[str] = Field(None, description="LLM 提供商（已废弃，忽略）")
    model: Optional[str] = Field(None, description="模型名称")


class SimpleGraphResponse(BaseModel):
    """简单图响应"""
    response: str = Field(..., description="AI 响应")
    message_count: int = Field(..., description="消息数量")


@router.post("/simple-chat", response_model=SimpleGraphResponse, summary="简单对话图")
async def simple_chat_graph(request: SimpleGraphRequest):
    """
    使用 LangGraph 的简单对话

    这是 LangGraph 的基础示例,展示如何使用图结构处理对话

    示例:
    ```json
    {
        "message": "你好,介绍一下 LangGraph",
        "provider": "ollama"
    }
    ```
    """
    graph = create_simple_chat_graph(model=request.model)

    result = await graph.ainvoke({
        "messages": [HumanMessage(content=request.message)]
    })

    return SimpleGraphResponse(
        response=result["messages"][-1].content,
        message_count=len(result["messages"])
    )


class MultiStepRequest(BaseModel):
    """多步骤任务请求"""
    task: str = Field(..., description="任务描述")
    provider: Optional[str] = Field(None, description="LLM 提供商（已废弃，忽略）")
    model: Optional[str] = Field(None, description="模型名称")


class MultiStepResponse(BaseModel):
    """多步骤任务响应"""
    task: str = Field(..., description="任务描述")
    steps: list[str] = Field(..., description="执行步骤")
    results: list[str] = Field(..., description="每步的结果")
    final_answer: str = Field(..., description="最终答案")
    total_steps: int = Field(..., description="总步骤数")


@router.post("/multi-step", response_model=MultiStepResponse, summary="多步骤任务工作流")
async def multi_step_workflow(request: MultiStepRequest):
    """
    多步骤任务处理工作流

    将复杂任务分解为多个步骤,依次执行并综合结果

    工作流程:
    1. 分析任务并制定计划
    2. 依次执行每个步骤
    3. 综合所有结果生成最终答案

    示例:
    ```json
    {
        "task": "研究 Python 的异步编程,包括基本概念、使用场景和最佳实践",
        "provider": "ollama"
    }
    ```
    """
    graph = create_multi_step_workflow(model=request.model)

    result = await graph.ainvoke({
        "messages": [],
        "task_description": request.task,
        "steps": [],
        "current_step": 0,
        "results": [],
        "final_answer": None
    })

    return MultiStepResponse(
        task=request.task,
        steps=result["steps"],
        results=result["results"],
        final_answer=result["final_answer"],
        total_steps=len(result["steps"])
    )


class ReflectionRequest(BaseModel):
    """反思工作流请求"""
    question: str = Field(..., description="问题")
    max_iterations: int = Field(default=2, ge=1, le=5, description="最大迭代次数")
    provider: Optional[str] = Field(None, description="LLM 提供商（已废弃，忽略）")
    model: Optional[str] = Field(None, description="模型名称")


class ReflectionResponse(BaseModel):
    """反思工作流响应"""
    question: str = Field(..., description="原始问题")
    final_response: str = Field(..., description="最终答案")
    iterations: int = Field(..., description="实际迭代次数")
    improvement_history: list[dict] = Field(..., description="改进历史")


@router.post("/reflection", response_model=ReflectionResponse, summary="反思改进工作流")
async def reflection_workflow(request: ReflectionRequest):
    """
    带反思机制的工作流

    AI 会:
    1. 生成初始回答
    2. 自我批评和评估
    3. 根据批评改进回答
    4. 重复直到达到最大迭代次数

    这个工作流展示了 AI 自我反思和迭代改进的能力

    示例:
    ```json
    {
        "question": "什么是人工智能?请详细解释",
        "max_iterations": 2,
        "provider": "ollama"
    }
    ```
    """
    graph = create_reflection_workflow(
        model=request.model,
        max_iterations=request.max_iterations
    )

    result = await graph.ainvoke({
        "messages": [HumanMessage(content=request.question)],
        "draft": None,
        "critique": None,
        "final_response": None,
        "iteration": 0,
        "max_iterations": request.max_iterations
    })

    # 提取改进历史
    improvement_history = []
    for i in range(result["iteration"]):
        improvement_history.append({
            "iteration": i + 1,
            "draft": "...",  # 可以从 messages 中提取
            "critique": "..."  # 可以从 messages 中提取
        })

    return ReflectionResponse(
        question=request.question,
        final_response=result["final_response"],
        iterations=result["iteration"],
        improvement_history=improvement_history
    )


@router.get("/available", summary="获取可用的工作流")
async def get_available_workflows():
    """
    获取所有可用的工作流列表

    返回系统中所有可用的 LangGraph 工作流及其描述
    """
    workflows = [
        {
            "name": "simple-chat",
            "title": "简单对话图",
            "description": "基础的图结构对话,展示 LangGraph 的基本用法",
            "endpoint": "/api/ai/workflows/simple-chat"
        },
        {
            "name": "multi-step",
            "title": "多步骤任务工作流",
            "description": "将复杂任务分解为多个步骤并依次执行",
            "endpoint": "/api/ai/workflows/multi-step"
        },
        {
            "name": "reflection",
            "title": "反思改进工作流",
            "description": "通过自我批评和反思来改进回答质量",
            "endpoint": "/api/ai/workflows/reflection"
        }
    ]

    return {
        "total": len(workflows),
        "workflows": workflows
    }
