"""
聊天相关 API

提供简单对话、流式对话等功能
"""

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.langchain.chains import ChainFactory, create_streaming_chain

router = APIRouter(prefix="/chat", tags=["Chat"])


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., description="用户消息")
    provider: Optional[str] = Field(None, description="LLM 提供商（已废弃，忽略）")
    model: Optional[str] = Field(None, description="模型名称")
    temperature: Optional[float] = Field(None, ge=0.0, le=1.0, description="温度参数")
    chat_history: Optional[list] = Field(default=[], description="对话历史")


class ChatResponse(BaseModel):
    """聊天响应"""
    response: str = Field(..., description="AI 响应")
    provider: str = Field(..., description="使用的 LLM 提供商")
    model: str = Field(..., description="使用的模型")


@router.post("/simple", response_model=ChatResponse, summary="简单对话")
async def simple_chat(request: ChatRequest):
    """
    简单的对话接口

    支持:
    - 基本对话功能
    - 自定义 LLM 提供商
    - 自定义模型和温度参数

    示例:
    ```json
    {
        "message": "你好",
        "provider": "ollama",
        "model": "llama3.2",
        "temperature": 0.7
    }
    ```
    """
    # 创建对话链
    chain = ChainFactory.create_simple_chat_chain(
        model=request.model,
        temperature=request.temperature
    )

    # 调用链
    result = await chain.ainvoke({
        "input": request.message,
        "chat_history": request.chat_history
    })

    return ChatResponse(
        response=result,
        provider=request.provider or "default",
        model=request.model or "default"
    )


@router.post("/code-assistant", response_model=ChatResponse, summary="代码助手")
async def code_assistant(request: ChatRequest):
    """
    代码助手接口

    专门用于编程相关的问题,提供代码示例和解释

    示例:
    ```json
    {
        "message": "如何用 Python 读取 CSV 文件?",
        "provider": "ollama"
    }
    ```
    """
    chain = ChainFactory.create_code_assistant_chain(
        model=request.model,
        temperature=request.temperature
    )

    result = await chain.ainvoke({
        "input": request.message,
        "chat_history": request.chat_history
    })

    return ChatResponse(
        response=result,
        provider=request.provider or "default",
        model=request.model or "default"
    )


@router.post("/stream", summary="流式对话")
async def stream_chat(request: ChatRequest):
    """
    流式对话接口

    以 SSE (Server-Sent Events) 方式返回响应,支持实时流式输出

    示例:
    ```json
    {
        "message": "讲一个笑话",
        "provider": "ollama"
    }
    ```
    """
    chain = create_streaming_chain(
        model=request.model,
        temperature=request.temperature
    )

    async def generate():
        """生成流式响应"""
        async for chunk in chain.astream({
            "input": request.message,
            "chat_history": request.chat_history
        }):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream"
    )


class DocumentQARequest(BaseModel):
    """文档问答请求"""
    question: str = Field(..., description="问题")
    context: str = Field(..., description="文档上下文")
    provider: Optional[str] = Field(None, description="LLM 提供商（已废弃，忽略）")
    model: Optional[str] = Field(None, description="模型名称")
    temperature: Optional[float] = Field(None, ge=0.0, le=1.0, description="温度参数")


@router.post("/document-qa", response_model=ChatResponse, summary="文档问答")
async def document_qa(request: DocumentQARequest):
    """
    基于文档的问答接口

    根据提供的文档上下文回答问题

    示例:
    ```json
    {
        "question": "Python 是什么?",
        "context": "Python 是一种解释型、面向对象的编程语言...",
        "provider": "ollama"
    }
    ```
    """
    chain = ChainFactory.create_document_qa_chain(
        context=request.context,
        model=request.model,
        temperature=request.temperature
    )

    result = await chain.ainvoke({
        "input": request.question
    })

    return ChatResponse(
        response=result,
        provider=request.provider or "default",
        model=request.model or "default"
    )
