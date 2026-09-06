"""
基于 StructuredQAAgent 的标准查重 API

与 /ai/deduplication/batch-check 能力相同，但内部使用 StructuredQAAgent 实现
（DeepAgent 自主检索 + 推理 → 结构化输出）

支持两种模式：
- deep（默认）：standard_deduplication_agent，效果好但耗时长
- fast：standard_deduplication_embedding_agent，纯向量召回，速度快效果略差，
  推荐在批量数 > 200 时使用
"""

from typing import Literal, Optional, List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.langchain.agents.tasks.standard.standard_deduplication_agent import (
    run_structured_batch_deduplication as run_deep,
)
from app.langchain.agents.tasks.standard.standard_deduplication_embedding_agent import (
    run_structured_batch_deduplication as run_fast,
)
from app.schemas.base import Success, Fail

router = APIRouter(prefix="/deduplication-agent", tags=["标准查重-Agent"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class AgentBatchDeduplicationRequest(BaseModel):
    """批量查重请求（Agent 版本）"""
    standard_nos: List[str] = Field(..., description="标准编号列表", min_length=1)
    pool_id: Optional[int] = Field(None, description="查重池ID，如果不指定则使用全库")
    batch_name: Optional[str] = Field(None, description="批次名称", max_length=200)
    check_existing: bool = Field(False, description="仅当standard_nos为1时生效：检查是否已有记录，有则直接返回true，无则创建")
    mode: Literal["deep", "fast"] = Field(
        "deep",
        description="查重模式：deep=完整 Agent 推理（默认，准确但慢）；fast=嵌入式向量召回（快但效果略差，建议批量>200时使用）",
    )


# ── 路由 ──────────────────────────────────────────────────────────────────────

@router.post("/batch-check", summary="批量标准相似度分析（Agent版）")
async def agent_batch_check_deduplication(request: AgentBatchDeduplicationRequest):
    """
    批量标准相似度分析接口（使用 StructuredQAAgent）

    - mode=deep：DeepAgent 自主完成向量检索 + SQL 召回 + 标签分析（默认）
    - mode=fast：仅用嵌入向量直接打标，速度大幅提升，效果略差

    结果保存到批次记录表，返回批次ID。
    """
    runner = run_fast if request.mode == "fast" else run_deep
    error, data = await runner(
        standard_nos=request.standard_nos,
        pool_id=request.pool_id,
        batch_name=request.batch_name,
        check_existing=request.check_existing,
    )
    if error:
        return Fail(code="4004", msg=error)
    return Success(data=True, msg="批量查重完成")


__all__ = ["router"]
