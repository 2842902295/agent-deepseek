"""
LangServe 路由模块

使用 LangServe 将 LangChain 链暴露为 API 端点
"""

from fastapi import APIRouter
from langserve import add_routes

from app.langchain.chains import (
    ChainFactory,
    create_streaming_chain
)
from app.langchain.graphs import (
    create_simple_chat_graph,
    create_multi_step_workflow,
    create_reflection_workflow
)

router = APIRouter(prefix="/langserve", tags=["LangServe"])


def register_langserve_routes(app_router: APIRouter):
    """
    注册 LangServe 路由

    将 LangChain 链和图通过 LangServe 暴露为标准的 API 端点

    Args:
        app_router: FastAPI 路由器实例
    """

    # 1. 简单对话链
    simple_chain = ChainFactory.create_simple_chat_chain()
    add_routes(
        app_router,
        simple_chain,
        path="/langserve/simple-chat",
        enabled_endpoints=["invoke", "batch", "stream"],
    )

    # 2. 代码助手链
    code_chain = ChainFactory.create_code_assistant_chain()
    add_routes(
        app_router,
        code_chain,
        path="/langserve/code-assistant",
        enabled_endpoints=["invoke", "batch", "stream"],
    )

    # 3. 流式对话链
    streaming_chain = create_streaming_chain()
    add_routes(
        app_router,
        streaming_chain,
        path="/langserve/streaming-chat",
        enabled_endpoints=["invoke", "stream"],
    )

    # 4. 简单对话图
    simple_graph = create_simple_chat_graph()
    add_routes(
        app_router,
        simple_graph,
        path="/langserve/chat-graph",
        enabled_endpoints=["invoke", "stream"],
    )

    # 5. 多步骤工作流
    multi_step_graph = create_multi_step_workflow()
    add_routes(
        app_router,
        multi_step_graph,
        path="/langserve/multi-step-workflow",
        enabled_endpoints=["invoke"],
    )

    # 6. 反思工作流
    reflection_graph = create_reflection_workflow()
    add_routes(
        app_router,
        reflection_graph,
        path="/langserve/reflection-workflow",
        enabled_endpoints=["invoke"],
    )


@router.get("/endpoints", summary="获取 LangServe 端点列表")
async def get_langserve_endpoints():
    """
    获取所有 LangServe 暴露的端点

    返回:
    - 端点列表
    - 每个端点支持的操作
    - 使用说明
    """
    endpoints = [
        {
            "name": "simple-chat",
            "path": "/api/ai/langserve/simple-chat",
            "description": "简单对话链",
            "operations": ["invoke", "batch", "stream"],
            "input_schema": {
                "input": "string",
                "chat_history": "array (optional)"
            }
        },
        {
            "name": "code-assistant",
            "path": "/api/ai/langserve/code-assistant",
            "description": "代码助手链",
            "operations": ["invoke", "batch", "stream"],
            "input_schema": {
                "input": "string",
                "chat_history": "array (optional)"
            }
        },
        {
            "name": "streaming-chat",
            "path": "/api/ai/langserve/streaming-chat",
            "description": "流式对话链",
            "operations": ["invoke", "stream"],
            "input_schema": {
                "input": "string"
            }
        },
        {
            "name": "chat-graph",
            "path": "/api/ai/langserve/chat-graph",
            "description": "简单对话图 (LangGraph)",
            "operations": ["invoke", "stream"],
            "input_schema": {
                "messages": "array of messages"
            }
        },
        {
            "name": "multi-step-workflow",
            "path": "/api/ai/langserve/multi-step-workflow",
            "description": "多步骤任务工作流 (LangGraph)",
            "operations": ["invoke"],
            "input_schema": {
                "task_description": "string",
                "messages": "array",
                "steps": "array",
                "current_step": "integer",
                "results": "array"
            }
        },
        {
            "name": "reflection-workflow",
            "path": "/api/ai/langserve/reflection-workflow",
            "description": "反思改进工作流 (LangGraph)",
            "operations": ["invoke"],
            "input_schema": {
                "messages": "array of messages",
                "max_iterations": "integer"
            }
        }
    ]

    return {
        "total": len(endpoints),
        "endpoints": endpoints,
        "usage": {
            "invoke": "POST {path}/invoke - 单次调用",
            "batch": "POST {path}/batch - 批量调用",
            "stream": "POST {path}/stream - 流式调用",
            "playground": "访问 {path}/playground 查看交互式界面"
        }
    }
