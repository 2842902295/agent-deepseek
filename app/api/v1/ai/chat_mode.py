"""
对话模式 API：用户偏好（所有登录用户）+ 模式配置 CRUD（仅超管）。

两组端点（数据层与解析见 app/langchain/chat_mode.py，模式清单真相源 =
agent_chat_mode_config 表行，可增删改；删除只保底「至少留一个模式」）：

- GET    /ai/chat-mode         当前用户的模式 + 思考强度偏好，附模式清单元数据
                               （每模式带 levels/levelDefault——按该模式有效块解析，
                               档位值即 wire 值 none/low/medium/high/max）。
                               模型名脱敏：blockKey/blockLabel 仅超管可见。
                               QA 页面挂载即调本端点：无热实例时顺带后台预热 agent
- PUT    /ai/chat-mode         保存自己的偏好（模式 ∈ DB 行、档位 ∈ 目标模式有效块
                               白名单）；写成功后弹出该用户的 agent 缓存并后台预热
- GET    /ai/chat-mode/config  仅超管：模式行清单（label/note/sortOrder/块指派）
                               + chat 预设块选项（含 levels）+ 全局兜底块
- PUT    /ai/chat-mode/config  仅超管：改单个模式（label/note/chatBlockKey/sortOrder，
                               key 不可改）
- POST   /ai/chat-mode/config  仅超管：新增模式（key 可省略自动生成）
- DELETE /ai/chat-mode/config/{key}  仅超管：删除模式（最后一个拒绝；用户偏好
                               指向被删模式由 resolve 自动回落默认模式，无需清数据）

兜底链：模式块（配置且有效）→ 角色模型配置 chat 块 → 全局激活块。
思考强度默认与迁移规则见 chat_mode.py（默认不开；档位随块切换就近平滑迁移）。
业务错误一律 4000 / 权限 4032，绝不复用鉴权码（4001/4002/4003/4010）。
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.v1.ai.agent_skill import get_current_role_codes
from app.core.ctx import CTX_USER_ID
from app.langchain.chat_mode import (
    DEFAULT_MODE,
    LEVEL_LABELS,
    fallback_mode,
    invalidate_modes_cache,
    load_modes,
    normalize_level,
)
from app.langchain.config import get_active_block
from app.langchain.model_selection import (
    DEFAULT_BLOCKS,
    _block_configured,
    available_blocks,
    block_label,
    block_reasoning_levels,
)
from app.langchain.role_model_profile import clear_agent_instances, resolve_user_model_profile
from app.models.standard import AgentChatModeConfig, AgentUserChatPref
from app.schemas.base import Fail, Success

router = APIRouter(prefix="/chat-mode", tags=["AI-对话模式"])

# 自定义模式 key 格式（与 AgentUserChatPref.mode CharField(16) 对齐）
_MODE_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{1,15}$")


async def _ensure_super() -> Optional[Fail]:
    """非超管直接返回 Fail；超管返回 None。"""
    _, _, is_super = await get_current_role_codes()
    if not is_super:
        return Fail(code="4032", msg="forbidden: 仅管理员可访问")
    return None


async def _effective_block_for_mode(row: Optional[AgentChatModeConfig], fallback_block: Optional[str]) -> str:
    """某模式行对当前用户的有效 chat 块：模式块（有效）→ 角色兜底块 → 全局激活块。"""
    if row is not None and row.chat_block_key and _block_configured("chat", row.chat_block_key):
        return row.chat_block_key
    if fallback_block and _block_configured("chat", fallback_block):
        return fallback_block
    return get_active_block("chat", DEFAULT_BLOCKS["chat"])


def _levels_payload(block_key: str) -> list[dict[str, str]]:
    """块档位白名单 → 前端滑块 marks 数据（配置序 = 滑块序）。"""
    return [{"key": lv, "label": LEVEL_LABELS.get(lv, lv)} for lv in block_reasoning_levels(block_key)]


# ── 用户偏好（所有登录用户） ────────────────────────────────────────────────


class ChatModePrefUpdate(BaseModel):
    """保存入参：各字段均可省略（省略 = 不改该项）。"""

    mode: Optional[str] = Field(None, description="对话模式 key（agent_chat_mode_config 行）")
    thinking_level: Optional[str] = Field(None, alias="thinkingLevel", description="思考强度 wire 档位值（none/low/medium/high/max，须在目标模式有效块白名单内）")
    tool_process_expand: Optional[bool] = Field(None, alias="toolProcessExpand", description="流式输出时自动展开工具调用过程（纯前端展示偏好，不触发 agent 重建）")

    class Config:
        populate_by_name = True


@router.get("", summary="当前用户的对话模式偏好（含模式清单与档位元数据）")
async def get_chat_mode_pref():
    uid = CTX_USER_ID.get() or None
    if uid is None:
        return Fail(code="4000", msg="无法识别当前用户")

    pref = await AgentUserChatPref.get_or_none(user_id=uid)
    rows = await load_modes()
    row_map = {r.mode: r for r in rows}
    mode = (pref.mode if pref else None) or DEFAULT_MODE
    if mode not in row_map:
        mode = fallback_mode(rows)

    # 角色兜底块（每模式 levels 解析用）：角色模型配置 chat 块
    profile = await resolve_user_model_profile(uid)
    fallback_block = profile.chat_block_key

    chat_options = {p["key"]: p.get("label") or p["key"] for p in available_blocks("chat")}
    # 模型名脱敏：具体用哪个块只有超管可见，普通用户只拿到模式语义（blockKey/blockLabel 置空）
    _, _, is_super = await get_current_role_codes()

    raw_level = pref.thinking_level if pref else None
    modes = []
    for r in rows:
        block_key = r.chat_block_key
        # effective：该模式当前是否指派了仍在可选清单内的有效块（否则走兜底链）
        effective = bool(block_key and block_key in chat_options)
        eff_block = await _effective_block_for_mode(r, fallback_block)
        modes.append({
            "key": r.mode,
            "label": r.label or r.mode,
            "note": r.note or "",
            "blockKey": block_key if (effective and is_super) else None,
            # label 供 composer 弹层小字（仅超管）；普通用户前端展示模式说明文案
            "blockLabel": (chat_options.get(block_key) or block_label(block_key)) if (effective and is_super) else None,
            "effective": effective,
            # 该模式有效块的档位白名单（空 = 滑块隐藏）+ 存量偏好平滑迁移后的当前生效档
            "levels": _levels_payload(eff_block),
            "levelDefault": normalize_level(raw_level, eff_block),
        })

    cur = next((m for m in modes if m["key"] == mode), None)
    level = (cur or {}).get("levelDefault")

    # 页面打开即预热：QA 页挂载时前端会调本端点，无热实例（后端刚重启 / 缓存被弹出）
    # 就后台重建 agent（dsh spawn + MCP 握手实测 8~14s）——用户打字的时间里完成，
    # 首条消息不再付冷启动。已有热实例则跳过（不重复查 DB / 重算缓存键）。
    from app.api.v1.ai.qa import prewarm_user_agent, uid_has_cached_agent

    if not uid_has_cached_agent(uid):
        prewarm_user_agent(uid)

    return Success(data={"mode": mode, "thinkingLevel": level, "toolProcessExpand": bool(pref.tool_process_expand) if pref else False, "modes": modes})


@router.put("", summary="保存当前用户的对话模式偏好")
async def set_chat_mode_pref(payload: ChatModePrefUpdate):
    uid = CTX_USER_ID.get() or None
    if uid is None:
        return Fail(code="4000", msg="无法识别当前用户")

    pref = await AgentUserChatPref.get_or_none(user_id=uid)
    rows = await load_modes()
    row_map = {r.mode: r for r in rows}
    cur_mode = (pref.mode if pref else None) or DEFAULT_MODE
    if cur_mode not in row_map:
        cur_mode = fallback_mode(rows)

    mode = payload.mode if payload.mode is not None else cur_mode
    if mode not in row_map:
        return Fail(code="4000", msg=f"未知的对话模式：{mode}")

    level = payload.thinking_level
    if level is not None:
        # 档位校验对准「目标模式的有效块」：无档位白名单的块不接受强度设置
        profile = await resolve_user_model_profile(uid)
        eff_block = await _effective_block_for_mode(row_map[mode], profile.chat_block_key)
        levels = block_reasoning_levels(eff_block)
        if not levels:
            return Fail(code="4000", msg="当前模式的模型不支持思考强度调节")
        if level not in levels:
            return Fail(code="4000", msg=f"该模型不支持的思考强度：{level}（可选 {'/'.join(levels)}）")
    # 只切模式不传档位：存量值原样保留（读时按新块平滑迁移，切回原模式自动还原）
    cur_level = pref.thinking_level if pref else None
    merged_level = level if level is not None else cur_level

    # 过程展示开关：纯前端偏好，不参与 agent 构建，省略即不改
    cur_expand = bool(pref.tool_process_expand) if pref else False
    merged_expand = payload.tool_process_expand if payload.tool_process_expand is not None else cur_expand
    expand_changed = payload.tool_process_expand is not None and merged_expand != cur_expand

    # 是否需要重建 agent：仅当模式/思考强度的有效值真的变了（新建行但值等于默认不算变）
    mode_level_changed = mode != cur_mode or merged_level != cur_level

    if not mode_level_changed and not expand_changed:
        return Success(msg="偏好未变化")

    await AgentUserChatPref.update_or_create(
        user_id=uid,
        defaults={"mode": mode, "thinking_level": merged_level, "tool_process_expand": 1 if merged_expand else 0},
    )

    # 仅模式/档位变化才需要重建 agent：弹出该用户全部形态的缓存 agent（含 dsh 子进程关闭），
    # 随即后台预热新形态（dsh spawn + MCP 握手 + 技能同步实测 8~12s）：下一条消息直接命中热实例。
    # 过程展示开关变更不触碰 agent（前端即时生效），避免无谓的冷启动。
    if mode_level_changed:
        from app.api.v1.ai.qa import _evict_user_agents, prewarm_user_agent

        _evict_user_agents(uid)
        prewarm_user_agent(uid)
        return Success(msg="已保存，下一条消息生效")

    return Success(msg="已保存")


# ── 模式配置 CRUD（仅超管） ─────────────────────────────────────────────────


class ChatModeConfigUpdate(BaseModel):
    """保存入参：mode 定位行，其余字段省略 = 不改该项（key 不可改）。"""

    mode: str = Field(..., description="模式 key（定位行，不可改）")
    label: Optional[str] = Field(None, max_length=32, description="显示名")
    note: Optional[str] = Field(None, max_length=128, description="弹层小字说明")
    chat_block_key: Optional[str] = Field(None, alias="chatBlockKey", description="chat 预设块名；null=兜底（角色配置→全局）")
    sort_order: Optional[int] = Field(None, alias="sortOrder", ge=0, description="展示顺序，小的在前")

    class Config:
        populate_by_name = True


class ChatModeConfigCreate(BaseModel):
    """新增模式入参：label 必填；key 省略自动生成 mode_{n}。"""

    key: Optional[str] = Field(None, description="模式 key（^[a-z][a-z0-9_-]{1,15}$；省略自动生成）")
    label: str = Field(..., max_length=32, description="显示名")
    note: Optional[str] = Field(None, max_length=128, description="弹层小字说明")
    chat_block_key: Optional[str] = Field(None, alias="chatBlockKey", description="chat 预设块名；null=兜底")

    class Config:
        populate_by_name = True


@router.get("/config", summary="对话模式配置清单（仅超管）")
async def get_chat_mode_config():
    if (fail := await _ensure_super()) is not None:
        return fail

    rows = await load_modes()
    chat_options = available_blocks("chat")
    valid_keys = {p["key"] for p in chat_options}

    modes = []
    for r in rows:
        block_key = r.chat_block_key
        modes.append({
            "key": r.mode,
            "label": r.label or r.mode,
            "note": r.note or "",
            "sortOrder": r.sort_order,
            "chatBlockKey": block_key,
            # null 恒有效（走兜底链）；块名仅当仍在可选清单内有效（失效前端打警告）
            "blockValid": (not block_key) or block_key in valid_keys,
        })

    return Success(
        data={
            "modes": modes,
            "chatOptions": chat_options,
            "globalChatKey": get_active_block("chat", DEFAULT_BLOCKS["chat"]),
        }
    )


@router.put("/config", summary="保存对话模式配置（仅超管；label/note/块指派/排序）")
async def set_chat_mode_config(payload: ChatModeConfigUpdate):
    if (fail := await _ensure_super()) is not None:
        return fail

    mode = (payload.mode or "").strip()
    row = await AgentChatModeConfig.get_or_none(mode=mode)
    if row is None:
        return Fail(code="4000", msg=f"未知的对话模式：{mode}")

    if payload.chat_block_key is not None:
        block_key = payload.chat_block_key.strip() or None
        if block_key and block_key not in {p["key"] for p in available_blocks("chat")}:
            return Fail(code="4000", msg=f"对话模型块未配置或不可选：{block_key}")
        row.chat_block_key = block_key
    if payload.label is not None:
        label = payload.label.strip()
        if not label:
            return Fail(code="4000", msg="模式名称不能为空")
        row.label = label
    if payload.note is not None:
        row.note = payload.note.strip() or None
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order

    row.updated_by = CTX_USER_ID.get() or None
    await row.save()

    invalidate_modes_cache()
    # 所有用户的该模式形态都可能变化 → 清全部 agent 实例缓存，下一条消息重建生效
    clear_agent_instances()
    return Success(msg="已保存，对使用该模式用户的下一条消息生效")


@router.post("/config", summary="新增对话模式（仅超管）")
async def create_chat_mode_config(payload: ChatModeConfigCreate):
    if (fail := await _ensure_super()) is not None:
        return fail

    label = (payload.label or "").strip()
    if not label:
        return Fail(code="4000", msg="模式名称不能为空")

    block_key = (payload.chat_block_key or "").strip() or None
    if block_key and block_key not in {p["key"] for p in available_blocks("chat")}:
        return Fail(code="4000", msg=f"对话模型块未配置或不可选：{block_key}")

    existing = {r.mode for r in await AgentChatModeConfig.all()}
    key = (payload.key or "").strip()
    if key:
        if not _MODE_KEY_RE.match(key):
            return Fail(code="4000", msg="模式 key 格式不合法（小写字母开头，可含数字/下划线/中划线，2~16 位）")
        if key in existing:
            return Fail(code="4000", msg=f"模式 key 已存在：{key}")
    else:
        n = len(existing) + 1
        while f"mode_{n}" in existing:
            n += 1
        key = f"mode_{n}"

    max_sort = max((r.sort_order for r in await load_modes()), default=-1)
    uid = CTX_USER_ID.get() or None
    await AgentChatModeConfig.create(
        mode=key, label=label, note=(payload.note or "").strip() or None,
        sort_order=max_sort + 1, chat_block_key=block_key, updated_by=uid,
    )

    invalidate_modes_cache()
    clear_agent_instances()
    return Success(data={"key": key}, msg="已新增模式，用户弹层下一条消息即见")


@router.delete("/config/{key}", summary="删除对话模式（仅超管；至少保留一个模式）")
async def delete_chat_mode_config(key: str):
    if (fail := await _ensure_super()) is not None:
        return fail

    row = await AgentChatModeConfig.get_or_none(mode=key)
    if row is None:
        return Fail(code="4000", msg=f"未知的对话模式：{key}")
    if await AgentChatModeConfig.all().count() <= 1:
        return Fail(code="4000", msg="至少需要保留一个对话模式，无法删除最后一个")
    await row.delete()

    # 用户偏好指向被删模式无需清数据：resolve_user_chat_pref 读时自动回落默认模式
    invalidate_modes_cache()
    clear_agent_instances()
    return Success(msg="已删除，使用该模式的用户自动回落默认模式")
