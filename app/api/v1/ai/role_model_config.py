"""
按角色模型配置 API（仅 R_SUPER 可用）

超管按角色差异化配置 chat / 生图 / 生视频模型。每角色一行（agent_role_model_config），
块字段三态：null=跟随全局 | 预设块名 | "DISABLED"=禁用（仅 image/video）。
OTHER 为保留兜底行（roles 表里不存在，兜底未配置的角色）。

运行期解析（role_model_profile.py）：用户角色按固定顺序 R_SUPER → R_ADMIN →
R_USER → 自创角色（role.id 升序），第一个在表里有行的角色**整行生效**（行内 null
字段跟随全局，不跨行混搭）→ OTHER 兜底行 → 全局激活块。

- GET  /ai/role-model-config   全部角色行（固定顺序，OTHER 恒在末尾）× 三态值与失效警告位
                               + 三类预设块选项 + 当前全局激活块（前端「跟随全局」展示用）
- PUT  /ai/role-model-config   保存单个角色配置；三字段全 null = 删行（回退跟随全局）。
                               写成功后清按用户缓存的 agent 实例，下一条消息即生效。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.v1.ai.agent_skill import get_current_role_codes
from app.core.ctx import CTX_USER_ID
from app.langchain.config import GEN_DISABLED, get_active_block
from app.langchain.model_selection import DEFAULT_BLOCKS, available_blocks
from app.langchain.role_model_profile import _CUSTOM_TIER, _ROLE_ORDER, OTHER_ROLE_CODE, clear_agent_instances
from app.models.standard import AgentRoleModelConfig
from app.models.system.admin import Role
from app.schemas.base import Fail, Success

router = APIRouter(prefix="/role-model-config", tags=["AI-按角色模型配置"])


async def _ensure_super() -> Optional[Fail]:
    """非超管直接返回 Fail；超管返回 None。"""
    _, _, is_super = await get_current_role_codes()
    if not is_super:
        return Fail(code="4032", msg="forbidden: 仅管理员可访问")
    return None


class RoleModelConfigUpdate(BaseModel):
    """保存入参：roleCode + 三个三态块字段（null=跟随全局；image/video 可 "DISABLED"）"""

    role_code: str = Field(..., alias="roleCode", description="角色编码，或保留兜底行 OTHER")
    chat_block_key: Optional[str] = Field(None, alias="chatBlockKey", description="chat 预设块名；null=跟随全局")
    image_block_key: Optional[str] = Field(None, alias="imageBlockKey", description="image 块名；null=跟随全局；DISABLED=禁用")
    video_block_key: Optional[str] = Field(None, alias="videoBlockKey", description="video 块名；null=跟随全局；DISABLED=禁用")

    class Config:
        populate_by_name = True


@router.get("", summary="按角色模型配置清单（仅超管）")
async def get_role_model_config():
    if (fail := await _ensure_super()) is not None:
        return fail

    roles = await Role.all().order_by("id")
    rows = {r.role_code: r for r in await AgentRoleModelConfig.all()}

    def _block_valid(cat: str, val: Optional[str]) -> bool:
        """null / DISABLED 恒有效；块名仅当仍在可选清单内有效（失效时前端打警告）。"""
        if not val:
            return True
        if val.upper() == GEN_DISABLED:
            return cat != "chat"
        return val in {p["key"] for p in available_blocks(cat)}

    role_rows = []
    for r in sorted(roles, key=lambda x: (_ROLE_ORDER.get(x.role_code, _CUSTOM_TIER), x.id)):
        cfg = rows.get(r.role_code)
        role_rows.append({
            "roleCode": r.role_code,
            "roleName": r.role_name,
            "chatBlockKey": cfg.chat_block_key if cfg else None,
            "imageBlockKey": cfg.image_block_key if cfg else None,
            "videoBlockKey": cfg.video_block_key if cfg else None,
            "chatBlockValid": _block_valid("chat", cfg.chat_block_key if cfg else None),
            "imageBlockValid": _block_valid("image", cfg.image_block_key if cfg else None),
            "videoBlockValid": _block_valid("video", cfg.video_block_key if cfg else None),
        })
    # OTHER 兜底行恒在末尾（无论表里有没有行都展示，供超管配置）
    cfg = rows.get(OTHER_ROLE_CODE)
    role_rows.append({
        "roleCode": OTHER_ROLE_CODE,
        "roleName": "其他用户",
        "chatBlockKey": cfg.chat_block_key if cfg else None,
        "imageBlockKey": cfg.image_block_key if cfg else None,
        "videoBlockKey": cfg.video_block_key if cfg else None,
        "chatBlockValid": _block_valid("chat", cfg.chat_block_key if cfg else None),
        "imageBlockValid": _block_valid("image", cfg.image_block_key if cfg else None),
        "videoBlockValid": _block_valid("video", cfg.video_block_key if cfg else None),
    })

    return Success(
        data={
            "roles": role_rows,
            "chatOptions": available_blocks("chat"),
            "imageOptions": available_blocks("image"),
            "videoOptions": available_blocks("video"),
            "globalKeys": {cat: get_active_block(cat, DEFAULT_BLOCKS[cat]) for cat in ("chat", "image", "video")},
        }
    )


@router.put("", summary="保存按角色模型配置（仅超管）")
async def set_role_model_config(payload: RoleModelConfigUpdate):
    if (fail := await _ensure_super()) is not None:
        return fail

    role_code = (payload.role_code or "").strip()
    if role_code != OTHER_ROLE_CODE:
        if not await Role.filter(role_code=role_code).exists():
            return Fail(code="4000", msg=f"角色不存在：{role_code}")

    # 三态校验（业务错误一律 4000，绝不复用鉴权码）
    chat = payload.chat_block_key or None
    image = payload.image_block_key or None
    video = payload.video_block_key or None
    if chat:
        if chat.upper() == GEN_DISABLED:
            return Fail(code="4000", msg="对话模型不支持禁用，只能跟随全局或指定预设块")
        if chat not in {p["key"] for p in available_blocks("chat")}:
            return Fail(code="4000", msg=f"对话模型块未配置或不可选：{chat}")
    for cat, val in (("image", image), ("video", video)):
        if val and val.upper() != GEN_DISABLED and val not in {p["key"] for p in available_blocks(cat)}:
            return Fail(code="4000", msg=f"{cat} 模型块未配置或不可选：{val}")
    # DISABLED 统一大写存储
    image = GEN_DISABLED if image and image.upper() == GEN_DISABLED else image
    video = GEN_DISABLED if video and video.upper() == GEN_DISABLED else video

    uid = CTX_USER_ID.get() or None
    if chat is None and image is None and video is None:
        # 三字段全跟随全局 → 删行（不存在也无妨）
        await AgentRoleModelConfig.filter(role_code=role_code).delete()
        clear_agent_instances()
        return Success(msg="已清除该角色的模型配置，恢复跟随全局")

    await AgentRoleModelConfig.update_or_create(
        role_code=role_code,
        defaults={"chat_block_key": chat, "image_block_key": image, "video_block_key": video, "updated_by": uid},
    )
    # 只清 agent 实例缓存（块定义 / llm 单例没变）：新配置在下一条消息重建时生效
    clear_agent_instances()
    return Success(msg="已保存，对该角色用户的下一条消息生效")
