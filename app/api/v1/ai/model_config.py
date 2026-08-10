"""
模型切换 API（仅 R_SUPER 可用）

超管在 qa-glass 页面全局切换三类模型（chat / image / video），切换对所有用户立即生效：
- GET  /ai/model-config   三段预设清单 + 当前选中（不含 api_key）
- PUT  /ai/model-config   切换某类别选中的预设块，写库 + 刷新内存映射 + 清缓存

预设块定义与凭据存 agent_model_block 表（运行时真相源；播种基线在代码
model_selection.py::_SEED_PRESETS，新装库首启自动落库），
块的增删改由 system-admin 子 agent 完成；页面只选块名、不出现 api_key。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.v1.ai.agent_skill import get_current_role_codes
from app.core.ctx import CTX_USER_ID
from app.langchain.model_selection import (
    SUPPORTED_CATEGORIES,
    available_blocks,
    clear_model_caches,
    get_catalog_with_current,
    set_active_block,
)
from app.models.standard import AgentModelConfig
from app.schemas.base import Fail, Success

router = APIRouter(prefix="/model-config", tags=["AI-模型切换"])


async def _ensure_super() -> Optional[Fail]:
    """非超管直接返回 Fail；超管返回 None。"""
    _, _, is_super = await get_current_role_codes()
    if not is_super:
        return Fail(code="4032", msg="forbidden: 仅管理员可访问")
    return None


class ModelConfigUpdate(BaseModel):
    """切换入参：category（chat/image/video）+ selectedKey（预设块名）"""

    category: str = Field(..., description="类别：chat / image / video")
    selected_key: str = Field(..., alias="selectedKey", description="选中的预设块名")

    class Config:
        populate_by_name = True


@router.get("", summary="模型预设清单 + 当前选中（仅超管）")
async def get_model_config():
    if (fail := await _ensure_super()) is not None:
        return fail
    return Success(data=await get_catalog_with_current())


@router.put("", summary="切换全局模型（仅超管，对所有用户生效）")
async def set_model_config(payload: ModelConfigUpdate):
    if (fail := await _ensure_super()) is not None:
        return fail

    category = payload.category.lower()
    key = payload.selected_key
    # 业务校验：类别合法 + 该预设块已配置且可选（业务错误用 4000，绝不用鉴权码）
    if category not in SUPPORTED_CATEGORIES:
        return Fail(code="4000", msg=f"未知的模型类别：{payload.category}")
    if key not in {p["key"] for p in available_blocks(category)}:
        return Fail(code="4000", msg="该模型未配置或不可选")

    uid = CTX_USER_ID.get() or None
    await AgentModelConfig.filter(category=category).update(selected_key=key, updated_by=uid)
    # 刷新内存映射并清 4 层缓存，使新模型对所有用户立即生效
    set_active_block(category, key)
    clear_model_caches()

    return Success(msg="模型已切换，对所有用户生效")
