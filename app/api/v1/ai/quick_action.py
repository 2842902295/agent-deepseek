"""
快捷功能 API

管理快捷功能及其案例，支持：
- 获取所有启用的快捷功能及案例（用户端）
- 管理快捷功能 CRUD（管理员）
- 管理案例 CRUD（管理员）
"""

import asyncio
import os
import re
import secrets
import shutil
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel, Field

from app.core.ctx import CTX_USER_ID
from app.core.dependency import AuthControl, PermissionControl
from app.models.standard.agent import (
    AgentArtifact,
    AgentMessage,
    AgentQuickAction,
    AgentQuickActionCategory,
    AgentQuickActionExample,
    AgentQuickActionLink,
    AgentSession,
)
from app.models.system import User
from app.schemas.base import Fail, Success

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
_USERS_ROOT = _PROJECT_ROOT / ".agent_workspace" / "users"

router = APIRouter(prefix="/quick-actions", tags=["AI-快捷功能"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def get_current_role_codes() -> tuple[Optional[int], list[str], bool]:
    """从 CTX_USER_ID 取出 (uid, role_codes, is_super)。"""
    uid = CTX_USER_ID.get() or None
    if not uid:
        return None, [], False
    user = await User.get_or_none(id=uid).prefetch_related("by_user_roles")
    if user is None:
        return uid, [], False
    codes = [r.role_code for r in user.by_user_roles]
    return uid, codes, "R_SUPER" in codes


def _copy_user_uploads(src_uid: int, dst_uid: int) -> None:
    """将源用户上传目录中的文件硬链接（或复制）到目标用户目录。"""
    src_dir = _USERS_ROOT / str(src_uid) / "uploads"
    dst_dir = _USERS_ROOT / str(dst_uid) / "uploads"
    if not src_dir.exists():
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in src_dir.iterdir():
        if not f.is_file():
            continue
        dst = dst_dir / f.name
        if dst.exists():
            continue
        try:
            os.link(f, dst)
        except OSError:
            shutil.copy2(f, dst)


def _conv_messages(conv_data) -> list:
    """从 conversation_data 中提取消息列表（兼容 dict 新格式与 list 旧格式）。"""
    if isinstance(conv_data, list):
        return conv_data
    if isinstance(conv_data, dict):
        return conv_data.get("messages", [])
    return []


def _resolve_preview_images(ex) -> list[str]:
    """合并 preview_images 和 preview_image，返回完整的图片路径列表。"""
    images: list[str] = list(ex.preview_images) if ex.preview_images else []
    if not images and ex.preview_image:
        images = [ex.preview_image]
    return images


def _example_to_dict(ex) -> dict:
    """统一构建案例输出字典，保持 previewImage / previewImages 一致。"""
    images = _resolve_preview_images(ex)
    return {
        "id": ex.id,
        "actionId": ex.action_id,
        "title": ex.title,
        "description": ex.description,
        "conversationData": _conv_messages(ex.conversation_data),
        "previewImage": images[0] if images else ex.preview_image,
        "previewImages": images,
        "previewHtml": ex.preview_html,
        "sortOrder": ex.sort_order,
        "isEnabled": ex.is_enabled,
    }


def _example_to_summary_dict(ex: dict) -> dict:
    """用户端列表用的案例摘要：只含卡片展示字段。

    入参是 .values() 查出的行字典——查询时就没有 SELECT 巨大的 conversation_data
    （用户端橱窗 / @技能弹窗只消费管理页录入的标题、描述与预览图；
    完整对话数据在 fork 走 /examples/{id}/fork 时按 id 回库读）。
    conversationData 恒为空数组，仅为兼容前端既有类型与弹窗预览的兜底分支。
    """
    images: list[str] = list(ex["preview_images"]) if ex["preview_images"] else []
    if not images and ex["preview_image"]:
        images = [ex["preview_image"]]
    return {
        "id": ex["id"],
        "actionId": ex["action_id"],
        "title": ex["title"],
        "description": ex["description"],
        "conversationData": [],
        "previewImage": images[0] if images else ex["preview_image"],
        "previewImages": images,
        "previewHtml": ex["preview_html"],
        "sortOrder": ex["sort_order"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class QuickActionExampleOut(BaseModel):
    """案例输出"""
    id: int
    action_id: int = Field(alias="actionId")
    title: str
    description: Optional[str] = None
    conversation_data: List[dict] = Field(alias="conversationData")
    preview_image: Optional[str] = Field(None, alias="previewImage")
    preview_images: Optional[List[str]] = Field(None, alias="previewImages")
    preview_html: Optional[str] = Field(None, alias="previewHtml")
    sort_order: int = Field(alias="sortOrder")

    class Config:
        populate_by_name = True
        from_attributes = True


class QuickActionOut(BaseModel):
    """快捷功能输出"""
    id: int
    name: str
    skill_key: Optional[str] = Field(None, alias="skillKey")
    icon: Optional[str] = None
    description: Optional[str] = None
    category_ids: List[int] = Field(default_factory=list, alias="categoryIds")
    sort_order: int = Field(alias="sortOrder")
    examples: List[QuickActionExampleOut] = []

    class Config:
        populate_by_name = True
        from_attributes = True


class QuickActionCategoryOut(BaseModel):
    """类型输出"""
    id: int
    name: str
    sort_order: int = Field(alias="sortOrder")
    is_enabled: Optional[int] = Field(None, alias="isEnabled")
    action_count: Optional[int] = Field(None, alias="actionCount")

    class Config:
        populate_by_name = True


class QuickActionListOut(BaseModel):
    """快捷功能列表输出：类型（章节顺序）+ 功能（全局序）+ 分组（类型内序）"""
    categories: List[dict]
    actions: List[dict]
    groups: List[dict]


class QuickActionCreate(BaseModel):
    """创建快捷功能"""
    name: str = Field(..., min_length=1, max_length=64)
    skill_key: Optional[str] = Field(None, alias="skillKey")
    icon: Optional[str] = None
    description: Optional[str] = None
    category_ids: Optional[List[int]] = Field(None, alias="categoryIds")
    sort_order: int = Field(0, alias="sortOrder")
    visibility: str = "public"
    allowed_role_codes: Optional[List[str]] = Field(None, alias="allowedRoleCodes")

    class Config:
        populate_by_name = True


class QuickActionUpdate(BaseModel):
    """更新快捷功能"""
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    skill_key: Optional[str] = Field(None, alias="skillKey")
    icon: Optional[str] = None
    description: Optional[str] = None
    category_ids: Optional[List[int]] = Field(None, alias="categoryIds")
    sort_order: Optional[int] = Field(None, alias="sortOrder")
    is_enabled: Optional[int] = Field(None, alias="isEnabled")
    visibility: Optional[str] = None
    allowed_role_codes: Optional[List[str]] = Field(None, alias="allowedRoleCodes")

    class Config:
        populate_by_name = True


class QuickActionSortPayload(BaseModel):
    """排序入参：categoryId 为空表示未分组区（全局排序），否则为类型内排序"""
    category_id: Optional[int] = Field(None, alias="categoryId")
    action_ids: List[int] = Field(default_factory=list, alias="actionIds")

    class Config:
        populate_by_name = True


class QuickActionCategoryCreate(BaseModel):
    """创建类型"""
    name: str = Field(..., min_length=1, max_length=32)

    class Config:
        populate_by_name = True


class QuickActionCategoryUpdate(BaseModel):
    """更新类型"""
    name: Optional[str] = Field(None, min_length=1, max_length=32)
    is_enabled: Optional[int] = Field(None, alias="isEnabled")

    class Config:
        populate_by_name = True


class QuickActionCategorySortPayload(BaseModel):
    """类型排序入参：按给出的 id 顺序重排章节"""
    ids: List[int] = Field(default_factory=list)

    class Config:
        populate_by_name = True


class QuickActionExampleCreate(BaseModel):
    """创建案例"""
    title: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = None
    conversation_data: List[dict] = Field(..., alias="conversationData")
    preview_image: Optional[str] = Field(None, alias="previewImage")
    preview_images: Optional[List[str]] = Field(None, alias="previewImages")
    preview_html: Optional[str] = Field(None, alias="previewHtml")
    source_session_id: Optional[int] = Field(None, alias="sourceSessionId")
    source_message_ids: Optional[List[int]] = Field(None, alias="sourceMessageIds")
    sort_order: int = Field(0, alias="sortOrder")

    class Config:
        populate_by_name = True


class QuickActionExampleFromSession(BaseModel):
    """从会话创建案例"""
    session_key: str = Field(..., alias="sessionKey", min_length=1)
    title: Optional[str] = None
    description: Optional[str] = None
    preview_images: Optional[List[str]] = Field(None, alias="previewImages")
    sort_order: Optional[int] = Field(0, alias="sortOrder")

    class Config:
        populate_by_name = True


class QuickActionExampleUpdate(BaseModel):
    """更新案例"""
    title: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = None
    conversation_data: Optional[List[dict]] = Field(None, alias="conversationData")
    preview_image: Optional[str] = Field(None, alias="previewImage")
    preview_images: Optional[List[str]] = Field(None, alias="previewImages")
    preview_html: Optional[str] = Field(None, alias="previewHtml")
    sort_order: Optional[int] = Field(None, alias="sortOrder")
    is_enabled: Optional[int] = Field(None, alias="isEnabled")

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────────────────────────────────────
# 用户订阅 Schemas（新手引导 / 职业体系已迁至 agent_expert.py）
# ─────────────────────────────────────────────────────────────────────────────

class MyActionsPayload(BaseModel):
    """修改个人订阅功能"""
    action_ids: List[int] = Field(default_factory=list, alias="actionIds")

    class Config:
        populate_by_name = True


# ─────────────────────────────────────────────────────────────────────────────
# 用户端接口
# ─────────────────────────────────────────────────────────────────────────────

def _visibility_ok(action: AgentQuickAction, uid: Optional[int], role_codes: list[str], is_super: bool) -> bool:
    """判断功能对当前用户是否可见。"""
    if is_super:
        return True
    if action.visibility == "public":
        return True
    if action.visibility == "role" and action.allowed_role_codes:
        if any(code in role_codes for code in action.allowed_role_codes):
            return True
    if action.visibility == "private" and uid and action.created_by == uid:
        return True
    return False


async def _build_quick_action_list(*, include_all: bool) -> dict:
    """组装 {categories, actions, groups}——**数据源已统一为技能体系（agent_skill）**。

    技能整合（2026-08-14）：橱窗/引导不再维护独立的快捷功能表，直接派生自技能商店：
    - action = 一条技能（id/name/skillKey/icon/描述），examples 恒为空（案例机制退役）
    - categories/groups = 技能分类词表（agent_skill_category），未分类技能落「其他」虚拟组（id=0）
    - 用户端（include_all=False）：上架中（is_enabled=1，与技能商店同一把尺子）
    - 管理端（include_all=True）：全量技能（含下架），附 isEnabled
    排序：精选（is_featured）优先，其次按技能 id；每个分类组内同序。
    """
    from app.api.v1.ai.role_tier import resolve_user_tier_codes, tier_allows
    from app.models.standard.agent import AgentSkill, AgentSkillCategory, AgentSkillUserPref

    uid, _role_codes, _is_super = await get_current_role_codes()
    # 用户档位集合（管理员哨兵恒全可见）：用户端橱窗对不在可见白名单的技能不展示
    _tier_codes = await resolve_user_tier_codes(_role_codes, _is_super or "R_ADMIN" in _role_codes)
    # 按需取列：skill_md 大列（SKILL.md 全文，最大几十 KB/条）橱窗用不到，
    # SELECT * 会把它全拉回来——远程库上这是本接口最慢的一查询（~400ms → ~10ms）
    cats, all_skills = await asyncio.gather(
        AgentSkillCategory.all().order_by("sort_order", "id"),
        AgentSkill.all()
        .only(
            "id", "skill_key", "name", "description", "icon", "category",
            "source", "user_id", "is_enabled", "is_featured", "min_tier_code",
        )
        .order_by("id"),
    )
    cat_id_by_name = {c.name: c.id for c in cats}
    OTHER_CAT_ID = 0

    visible: list = []
    for s in all_skills:
        if s.source == "builtin":
            continue  # 内置能力不进橱窗
        if include_all:
            visible.append(s)  # 管理端不过滤档位（管理员恒全可见）
        elif s.is_enabled and await tier_allows(s.min_tier_code, s.user_id, uid, _tier_codes):
            visible.append(s)

    # 案例（2026-08-14 回归）：按 skill_key 挂回技能。
    # 用户端只取摘要字段（不 SELECT 巨大的 conversation_data；fork 时按案例 id 回库读全文）
    vis_keys = [s.skill_key for s in visible]
    examples_by_skill: dict[str, list[dict]] = {}
    if vis_keys:
        if include_all:
            ex_rows = await AgentQuickActionExample.filter(skill_key__in=vis_keys, is_enabled=1).order_by(
                "skill_key", "sort_order", "id"
            )
            for ex in ex_rows:
                if ex.skill_key:
                    examples_by_skill.setdefault(ex.skill_key, []).append(_example_to_dict(ex))
        else:
            ex_rows = await AgentQuickActionExample.filter(skill_key__in=vis_keys, is_enabled=1).order_by(
                "skill_key", "sort_order", "id"
            ).values(
                "id", "action_id", "skill_key", "title", "description",
                "preview_image", "preview_images", "preview_html", "sort_order",
            )
            for row in ex_rows:
                if row.get("skill_key"):
                    examples_by_skill.setdefault(row["skill_key"], []).append(_example_to_summary_dict(row))

    # 「已添加」回显：偏好表 + 缺省口径（本人创建的技能默认已添加），与技能面板同逻辑
    added_keys: set = set()
    if uid is not None:
        prefs = await AgentSkillUserPref.filter(user_id=uid).all()
        added_keys = {p.skill_key for p in prefs if p.is_added}
        for s in visible:
            if s.user_id is not None and s.user_id == uid:
                added_keys.add(s.skill_key)

    visible.sort(key=lambda s: (0 if getattr(s, "is_featured", 0) else 1, s.id))

    need_other = False
    action_dicts: list[dict] = []
    members_by_cat: dict[int, list[int]] = {}
    for i, s in enumerate(visible):
        cat_id = cat_id_by_name.get((s.category or "").strip())
        if cat_id is None:
            cat_id = OTHER_CAT_ID
            need_other = True
        item: dict = {
            "id": s.id,
            "name": s.name,
            "skillKey": s.skill_key,
            "icon": s.icon,
            "description": s.description,
            "categoryIds": [cat_id],
            "sortOrder": i,
            "examples": examples_by_skill.get(s.skill_key, []),
            "isFeatured": bool(getattr(s, "is_featured", 0)),
            "isAdded": s.skill_key in added_keys,
        }
        if include_all:
            item["isEnabled"] = s.is_enabled
        action_dicts.append(item)
        members_by_cat.setdefault(cat_id, []).append(s.id)

    categories: list[dict] = [
        {
            "id": c.id,
            "name": c.name,
            "sortOrder": c.sort_order,
            "icon": getattr(c, "icon", None),
            **({"isEnabled": 1} if include_all else {}),
        }
        for c in cats
    ]
    cat_name_by_id = {c.id: c.name for c in cats}
    if need_other:
        categories.append({"id": OTHER_CAT_ID, "name": "其他", "sortOrder": 9999, "icon": None})
        cat_name_by_id[OTHER_CAT_ID] = "其他"

    # groups 顺序 = 分类的 sort_order（cats 查询已按 sort_order, id 排好），
    # 「其他」虚拟组恒定殿后——不能按分类 id 排，否则后台调整分类顺序不生效
    groups: list[dict] = [
        {"id": c.id, "name": c.name, "icon": getattr(c, "icon", None), "actionIds": members_by_cat[c.id]}
        for c in cats
        if c.id in members_by_cat
    ]
    if OTHER_CAT_ID in members_by_cat:
        groups.append({"id": OTHER_CAT_ID, "name": "其他", "icon": None, "actionIds": members_by_cat[OTHER_CAT_ID]})

    return {
        "categories": categories,
        "actions": action_dicts,
        "groups": groups,
    }


@router.get("")
async def get_quick_actions():
    """
    获取所有启用的快捷功能及其案例（用户可见）

    返回 {categories, actions, groups}：
    - categories：启用的类型（章节顺序）
    - actions：功能平铺列表（全局排序，含 categoryIds 与案例）
    - groups：每个类型下的功能 id（类型内排序），前端橱窗按此渲染章节
    """
    return Success(data=await _build_quick_action_list(include_all=False))


@router.get("/manage")
async def manage_quick_actions(
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """
    管理端列表：全量数据（含停用功能 / 停用类型，附 isEnabled），结构同用户端
    """
    return Success(data=await _build_quick_action_list(include_all=True))


# ─────────────────────────────────────────────────────────────────────────────
# 用户订阅（新手引导 / 职业体系已迁至 agent_expert.py）
# ─────────────────────────────────────────────────────────────────────────────

async def _added_skill_ids(uid: int, actions: list[dict]) -> list[int]:
    """从橱窗 action 列表（已含 isAdded 回显）取当前用户「已添加」的技能 id，保持橱窗序。"""
    return [a["id"] for a in actions if a.get("isAdded")]


@router.get("/my")
async def get_my_actions():
    """
    当前用户「已添加」的技能（订阅语义已并入技能偏好 is_added）。

    首屏橱窗与对话框优先渲染此列表；actions 保持橱窗序。
    返回 {onboarded, actionIds, actions, categories}。
    """
    uid = CTX_USER_ID.get() or None
    if not uid:
        return Fail(code="4000", msg="请先登录")

    user = await User.get_or_none(id=uid)
    full = await _build_quick_action_list(include_all=False)
    my_ids = await _added_skill_ids(uid, full["actions"])
    by_id = {a["id"]: a for a in full["actions"]}
    my_actions = [by_id[i] for i in my_ids if i in by_id]

    return Success(data={
        "onboarded": bool(user and user.onboarded_at is not None),
        "actionIds": my_ids,
        "actions": my_actions,
        "categories": full["categories"],
    })


@router.put("/my/actions")
async def update_my_actions(payload: MyActionsPayload):
    """修改个人「已添加」技能。"""
    from app.api.v1.ai.agent_expert import _replace_added_skills

    uid = CTX_USER_ID.get() or None
    if not uid:
        return Fail(code="4000", msg="请先登录")

    await _replace_added_skills(uid, payload.action_ids)
    return Success(msg="已更新", data={"actionIds": payload.action_ids})


# ─────────────────────────────────────────────────────────────────────────────
# 管理员接口
# ─────────────────────────────────────────────────────────────────────────────

async def _sync_action_links(action_id: int, category_ids: list[int]) -> None:
    """重建功能的类型归属：已有 link 保留组内排序，新加入的追加到对应类型末尾。"""
    valid_ids = {
        c.id for c in await AgentQuickActionCategory.filter(id__in=category_ids)
    } if category_ids else set()
    existing = {
        lk.category_id
        for lk in await AgentQuickActionLink.filter(action_id=action_id)
    }
    to_remove = existing - valid_ids
    if to_remove:
        await AgentQuickActionLink.filter(
            action_id=action_id, category_id__in=list(to_remove)
        ).delete()
    for cid in category_ids:
        if cid not in valid_ids or cid in existing:
            continue
        last = await AgentQuickActionLink.filter(category_id=cid).order_by("-sort_order").first()
        next_sort = (last.sort_order + 1) if last else 0
        await AgentQuickActionLink.create(action_id=action_id, category_id=cid, sort_order=next_sort)


async def _action_category_ids(action_id: int) -> list[int]:
    links = await AgentQuickActionLink.filter(action_id=action_id).order_by("sort_order", "id")
    return [lk.category_id for lk in links]


# ── 类型（章节）管理 ──────────────────────────────────────────────────────────

@router.get("/categories")
async def list_categories(
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """类型列表（含每个类型下的功能数量，管理端）"""
    cats = await AgentQuickActionCategory.all().order_by("sort_order", "id")
    counts: dict[int, int] = {}
    for lk in await AgentQuickActionLink.all():
        counts[lk.category_id] = counts.get(lk.category_id, 0) + 1
    return Success(data=[
        {
            "id": c.id,
            "name": c.name,
            "sortOrder": c.sort_order,
            "isEnabled": c.is_enabled,
            "actionCount": counts.get(c.id, 0),
        }
        for c in cats
    ])


@router.post("/categories")
async def create_category(
    payload: QuickActionCategoryCreate,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """新建类型（追加到末尾）"""
    name = payload.name.strip()
    if await AgentQuickActionCategory.get_or_none(name=name):
        return Fail(code="4009", msg="类型名已存在")
    uid = CTX_USER_ID.get() or None
    last = await AgentQuickActionCategory.all().order_by("-sort_order").first()
    cat = await AgentQuickActionCategory.create(
        name=name,
        sort_order=(last.sort_order + 1) if last else 0,
        created_by=uid,
    )
    return Success(data={"id": cat.id, "name": cat.name, "sortOrder": cat.sort_order, "isEnabled": cat.is_enabled})


@router.put("/categories/sort")
async def sort_categories(
    payload: QuickActionCategorySortPayload,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """按给出的 id 顺序重排类型（章节顺序）"""
    for i, cid in enumerate(payload.ids):
        await AgentQuickActionCategory.filter(id=cid).update(sort_order=i)
    return Success(msg="类型排序已更新")


@router.put("/categories/{category_id}")
async def update_category(
    category_id: int,
    payload: QuickActionCategoryUpdate,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """更新类型（改名 / 启停）"""
    cat = await AgentQuickActionCategory.get_or_none(id=category_id)
    if not cat:
        return Fail(code="4004", msg="类型不存在")
    update_data = payload.model_dump(exclude_unset=True, by_alias=False)
    if "name" in update_data and update_data["name"]:
        update_data["name"] = update_data["name"].strip()
        dup = await AgentQuickActionCategory.get_or_none(name=update_data["name"])
        if dup and dup.id != category_id:
            return Fail(code="4009", msg="类型名已存在")
    if update_data:
        await cat.update_from_dict(update_data).save()
    return Success(data={"id": cat.id, "name": cat.name, "sortOrder": cat.sort_order, "isEnabled": cat.is_enabled})


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: int,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """删除类型（功能本身保留，仅解除归属）"""
    cat = await AgentQuickActionCategory.get_or_none(id=category_id)
    if not cat:
        return Fail(code="4004", msg="类型不存在")
    await AgentQuickActionLink.filter(category_id=category_id).delete()
    await cat.delete()
    return Success(msg="已删除")


# ── 排序 ─────────────────────────────────────────────────────────────────────

@router.put("/sort")
async def sort_quick_actions(
    payload: QuickActionSortPayload,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """
    功能排序：
    - categoryId 为空：更新全局排序（未分组区 / 快捷按钮列表）
    - categoryId 指定：更新功能在该类型内的排序（无 link 则自动建立归属）
    """
    if payload.category_id is None:
        for i, aid in enumerate(payload.action_ids):
            await AgentQuickAction.filter(id=aid).update(sort_order=i)
        return Success(msg="排序已更新")

    cat = await AgentQuickActionCategory.get_or_none(id=payload.category_id)
    if not cat:
        return Fail(code="4004", msg="类型不存在")
    for i, aid in enumerate(payload.action_ids):
        link = await AgentQuickActionLink.get_or_none(action_id=aid, category_id=payload.category_id)
        if link:
            link.sort_order = i
            await link.save()
        else:
            await AgentQuickActionLink.create(action_id=aid, category_id=payload.category_id, sort_order=i)
    return Success(msg="排序已更新")


# ── 功能 CRUD ─────────────────────────────────────────────────────────────────

@router.post("")
async def create_quick_action(
    payload: QuickActionCreate,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """
    创建快捷功能（管理员）
    """
    uid = CTX_USER_ID.get() or None
    action = await AgentQuickAction.create(
        name=payload.name,
        skill_key=payload.skill_key,
        icon=payload.icon,
        description=payload.description,
        sort_order=payload.sort_order,
        visibility=payload.visibility,
        allowed_role_codes=payload.allowed_role_codes,
        created_by=uid,
    )
    if payload.category_ids:
        await _sync_action_links(action.id, payload.category_ids)

    return Success(data={
        "id": action.id,
        "name": action.name,
        "skillKey": action.skill_key,
        "icon": action.icon,
        "description": action.description,
        "categoryIds": await _action_category_ids(action.id),
        "sortOrder": action.sort_order,
        "examples": [],
    })


@router.put("/{action_id}")
async def update_quick_action(
    action_id: int,
    payload: QuickActionUpdate,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """
    更新快捷功能（管理员）
    """
    action = await AgentQuickAction.get_or_none(id=action_id)
    if not action:
        return Fail(code="4004", msg="快捷功能不存在")

    update_data = payload.model_dump(exclude_unset=True, by_alias=False)
    category_ids = update_data.pop("category_ids", None)
    if update_data:
        await action.update_from_dict(update_data).save()
    if category_ids is not None:
        await _sync_action_links(action.id, category_ids)

    examples = await AgentQuickActionExample.filter(
        action_id=action.id, is_enabled=1
    ).order_by("sort_order", "id")

    return Success(data={
        "id": action.id,
        "name": action.name,
        "skillKey": action.skill_key,
        "icon": action.icon,
        "description": action.description,
        "categoryIds": await _action_category_ids(action.id),
        "sortOrder": action.sort_order,
        "examples": [_example_to_dict(ex) for ex in examples],
    })


@router.delete("/{action_id}")
async def delete_quick_action(
    action_id: int,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """
    删除快捷功能（管理员）
    """
    action = await AgentQuickAction.get_or_none(id=action_id)
    if not action:
        return Fail(code="4004", msg="快捷功能不存在")

    # 删除关联的案例与类型归属
    await AgentQuickActionExample.filter(action_id=action_id).delete()
    await AgentQuickActionLink.filter(action_id=action_id).delete()

    # 删除快捷功能
    await action.delete()

    return Success(msg="已删除")


@router.post("/{action_id}/examples")
async def create_example(
    action_id: int,
    payload: QuickActionExampleCreate,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """
    为快捷功能添加案例（管理员）
    """
    action = await AgentQuickAction.get_or_none(id=action_id)
    if not action:
        return Fail(code="4004", msg="快捷功能不存在")

    uid = CTX_USER_ID.get() or None
    # 合并 preview_images：优先用新字段，回退到 preview_image
    preview_images = payload.preview_images or None
    preview_image = payload.preview_image
    if preview_images:
        preview_image = preview_images[0]  # 首图同步到旧字段
    elif preview_image:
        preview_images = [preview_image]

    example = await AgentQuickActionExample.create(
        action_id=action_id,
        title=payload.title,
        description=payload.description,
        conversation_data=payload.conversation_data,
        preview_image=preview_image,
        preview_images=preview_images,
        preview_html=payload.preview_html,
        source_session_id=payload.source_session_id,
        source_message_ids=payload.source_message_ids,
        sort_order=payload.sort_order,
        created_by=uid,
    )

    return Success(data=_example_to_dict(example))


@router.post("/{action_id}/examples/from-session")
async def create_example_from_session(
    action_id: int,
    payload: QuickActionExampleFromSession,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """
    从会话创建案例（管理员）- 自动提取会话消息
    """
    from app.models.standard.agent import AgentMessage, AgentSession

    action = await AgentQuickAction.get_or_none(id=action_id)
    if not action:
        return Fail(code="4004", msg="快捷功能不存在")

    # 查询会话
    session = await AgentSession.get_or_none(session_key=payload.session_key)
    if not session:
        return Fail(code="4004", msg="会话不存在")

    # 查询会话消息
    messages = await AgentMessage.filter(
        session_id=session.id
    ).order_by("id")

    if not messages:
        return Fail(code="4000", msg="会话没有消息")

    # 转换为案例格式
    # 批量查询所有消息的 artifacts，用于序列化到案例数据中
    msg_ids = [m.id for m in messages]
    artifacts_by_msg: dict[int, list[AgentArtifact]] = {}
    if msg_ids:
        for a in await AgentArtifact.filter(message_id__in=msg_ids).order_by("id"):
            artifacts_by_msg.setdefault(a.message_id, []).append(a)

    conversation_messages = []
    message_ids = []
    for msg in messages:
        msg_data = {
            "role": msg.role,
            "content": msg.content or "",
        }
        if msg.thinking:
            msg_data["thinking"] = msg.thinking
        if msg.attachments_json:
            msg_data["attachments"] = msg.attachments_json
        if msg.tool_steps_json:
            msg_data["toolSteps"] = msg.tool_steps_json
        if msg.process_json:
            msg_data["processSteps"] = msg.process_json
        # 序列化 assistant 消息的 artifacts（fork 时重建用）
        if msg.role == "assistant" and msg.id in artifacts_by_msg:
            msg_data["artifacts"] = [
                {
                    "artifactType": a.artifact_type,
                    "name": a.name,
                    "description": a.description,
                    "path": a.path,
                    "size": a.size,
                    "chartSpec": a.chart_spec,
                    "oldId": a.id,
                }
                for a in artifacts_by_msg[msg.id]
            ]

        conversation_messages.append(msg_data)
        message_ids.append(msg.id)

    if not conversation_messages:
        return Fail(code="4000", msg="会话没有有效消息")

    # 新格式：dict 包装，附带 source_user_id（fork 时复制上传文件用）
    conversation_data = {
        "messages": conversation_messages,
        "source_user_id": session.user_id,
    }

    # 创建案例
    uid = CTX_USER_ID.get() or None
    preview_images = payload.preview_images or None
    preview_image = preview_images[0] if preview_images else None
    example = await AgentQuickActionExample.create(
        action_id=action_id,
        title=payload.title or session.title,
        description=payload.description or f"从会话「{session.title}」提取",
        conversation_data=conversation_data,
        source_session_id=session.id,
        source_message_ids=message_ids,
        sort_order=payload.sort_order or 0,
        preview_image=preview_image,
        preview_images=preview_images,
        created_by=uid,
    )

    return Success(data=_example_to_dict(example))


@router.put("/examples/{example_id}")
async def update_example(
    example_id: int,
    payload: QuickActionExampleUpdate,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """
    更新案例（管理员）
    """
    example = await AgentQuickActionExample.get_or_none(id=example_id)
    if not example:
        return Fail(code="4004", msg="案例不存在")

    update_data = payload.model_dump(exclude_unset=True, by_alias=False)

    # 合并 preview_images ↔ preview_image
    if "preview_images" in update_data and update_data["preview_images"]:
        update_data["preview_image"] = update_data["preview_images"][0]
    elif "preview_image" in update_data and update_data["preview_image"] and "preview_images" not in update_data:
        update_data["preview_images"] = [update_data["preview_image"]]

    if update_data:
        await example.update_from_dict(update_data).save()

    return Success(data=_example_to_dict(example))


@router.delete("/examples/{example_id}")
async def delete_example(
    example_id: int,
    _auth: AuthControl = Depends(),
    _perm: PermissionControl = Depends(),
):
    """
    删除案例（管理员）
    """
    example = await AgentQuickActionExample.get_or_none(id=example_id)
    if not example:
        return Fail(code="4004", msg="案例不存在")

    await example.delete()

    return Success(msg="已删除")



# ─────────────────────────────────────────────────────────────────────────────
# 用户端：案例 fork 为会话
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/examples/{example_id}/fork", summary="将案例 fork 为新会话")
async def fork_example_to_session(example_id: int):
    """
    将案例的对话数据复制为一个真实的 AgentSession + AgentMessage，
    前端可直接切换到该会话继续对话。

    同时重建 AgentArtifact 记录（更新 content 中的 [artifact:ID] 引用），
    并复制源用户的上传文件到当前用户目录。
    """
    example = await AgentQuickActionExample.get_or_none(id=example_id, is_enabled=1)
    if not example:
        return Fail(code="4004", msg="案例不存在")

    if not example.conversation_data:
        return Fail(code="4000", msg="案例没有对话数据")

    uid = CTX_USER_ID.get() or None

    # 兼容新旧 conversation_data 格式
    raw = example.conversation_data
    if isinstance(raw, list):
        conv_messages = raw
        source_user_id = None
    elif isinstance(raw, dict):
        conv_messages = raw.get("messages", [])
        source_user_id = raw.get("source_user_id")
    else:
        conv_messages = []
        source_user_id = None

    if not conv_messages:
        return Fail(code="4000", msg="案例没有对话数据")

    # 创建新会话
    key = f"sess_{secrets.token_hex(6)}"
    session = await AgentSession.create(
        session_key=key,
        user_id=uid,
        title=(example.title or "案例会话")[:128],
        thread_id=f"qa-{key}",
    )

    # 提前启动 agent 构建，与下面的 DB 写入并行：首次 fork 需全量构建 agent（慢），
    # 串行等它 = 纯浪费；已缓存时开销≈0。checkpoint 注入处 await 收口
    agent_task = None
    try:
        from app.api.v1.ai.qa import _get_agent_for_session

        agent_task = asyncio.create_task(_get_agent_for_session(key, uid))
    except Exception:
        logger.warning("[fork] agent 构建任务创建失败，checkpoint 注入时将重试", exc_info=True)

    # 写入 AgentMessage（+ 必要时重建 AgentArtifact）
    _ARTIFACT_MARKER_RE = re.compile(r"\[artifact:(-?\d+)\]")

    def _clean_stale_markers(text: str) -> str:
        """旧案例无 artifacts 数据：清除无效的 [artifact:ID] 标记"""
        text = re.sub(r"^\[artifact:-?\d+\]\s*$", "", text, flags=re.MULTILINE)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    # 规范化：只保留 user/assistant 消息
    norm_messages = [m for m in conv_messages if m.get("role", "user") in ("user", "assistant")]
    has_artifacts = any(m.get("role") == "assistant" and m.get("artifacts") for m in norm_messages)

    msg_count = 0
    rebuilt_messages: list[dict] = []  # 最终入库的 (role, content)，用于重建 checkpoint 上下文
    if not has_artifacts:
        # 快路径（绝大多数案例都是纯文本）：一次 bulk INSERT 写完全部消息。
        # 远程 OceanBase 单条往返 ~35ms，逐条 create = N 次往返（几十条消息就要 1s+）；
        # 无 artifacts 也就不用回填 message_id，连 id 都不需要取
        objs = []
        for msg_data in norm_messages:
            role = msg_data.get("role", "user")
            content = msg_data.get("content", "")
            if role == "assistant" and _ARTIFACT_MARKER_RE.search(content):
                content = _clean_stale_markers(content)
            objs.append(
                AgentMessage(
                    session_id=session.id,
                    role=role,
                    content=content,
                    thinking=msg_data.get("thinking"),
                    attachments_json=msg_data.get("attachments"),
                    tool_steps_json=msg_data.get("toolSteps"),
                    process_json=msg_data.get("processSteps"),
                    status="done",
                )
            )
            rebuilt_messages.append({"role": role, "content": content})
        if objs:
            await AgentMessage.bulk_create(objs)
        msg_count = len(objs)
    else:
        # 慢路径：重建 artifacts 需要拿到新 id 才能改写 content 里的 [artifact:旧ID]，
        # 只能逐条创建（有产物的案例占比很小，可接受）
        for msg_data in norm_messages:
            role = msg_data.get("role", "user")
            content = msg_data.get("content", "")
            artifacts_data = msg_data.get("artifacts")

            old_to_new_id: dict[int, int] = {}
            if role == "assistant" and artifacts_data:
                for art in artifacts_data:
                    new_art = await AgentArtifact.create(
                        artifact_type=art.get("artifactType", "other"),
                        name=(art.get("name") or "artifact")[:200],
                        description=(art.get("description") or "")[:1000] or None,
                        path=art.get("path"),
                        size=art.get("size"),
                        chart_spec=art.get("chartSpec"),
                        session_id=session.id,
                        # message_id 在消息创建后回填
                    )
                    old_id = art.get("oldId")
                    if old_id is not None:
                        old_to_new_id[old_id] = new_art.id

                # 替换 content 中的 [artifact:旧ID] → [artifact:新ID]
                if old_to_new_id:
                    def _replace_id(m, _map=old_to_new_id):
                        old = int(m.group(1))
                        new = _map.get(old)
                        return f"[artifact:{new}]" if new else ""
                    content = _ARTIFACT_MARKER_RE.sub(_replace_id, content)
            elif role == "assistant" and _ARTIFACT_MARKER_RE.search(content):
                content = _clean_stale_markers(content)

            new_msg = await AgentMessage.create(
                session_id=session.id,
                role=role,
                content=content,
                thinking=msg_data.get("thinking"),
                attachments_json=msg_data.get("attachments"),
                tool_steps_json=msg_data.get("toolSteps"),
                process_json=msg_data.get("processSteps"),
                status="done",
            )

            # 回填 artifact 的 message_id
            if old_to_new_id:
                for new_id in old_to_new_id.values():
                    await AgentArtifact.filter(id=new_id).update(message_id=new_msg.id)

            rebuilt_messages.append({"role": role, "content": content})
            msg_count += 1

    session.message_count = msg_count
    await session.save(update_fields=["message_count", "update_time"])

    # 重建 LangGraph checkpoint 上下文（关键）：
    # Agent 的多轮记忆存在 checkpointer（按 thread_id 索引）而非 agent_message 表，
    # 只写 DB 不注入 checkpoint 的话，用户加载案例后继续对话，agent 看到的上下文是空的。
    # 案例消息都是纯 user/assistant 文本（无 tool_calls），重建出的序列天然合法；
    # 新 thread 为空，aupdate_state 即写入，一次调用完成。
    try:
        from langchain_core.messages import AIMessage, HumanMessage

        lc_messages = [
            HumanMessage(content=m["content"]) if m["role"] == "user" else AIMessage(content=m["content"])
            for m in rebuilt_messages
            if m["content"]
        ]
        if lc_messages:
            if agent_task is not None:
                agent = await agent_task  # 消息写入期间已并行构建
            else:
                from app.api.v1.ai.qa import _get_agent_for_session

                agent = await _get_agent_for_session(key, uid)
            config = {
                "configurable": {
                    "thread_id": session.thread_id,
                    "user_id": str(uid) if uid else "0",
                }
            }
            await agent.aupdate_state(config, {"messages": lc_messages})
            logger.info(f"[fork] checkpoint 上下文已注入 example={example_id} session={key} msgs={len(lc_messages)}")
        elif agent_task is not None:
            # 没有可注入的消息时无需等 agent 构建完成；静默消费异常避免 never-retrieved 告警
            agent_task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
    except Exception:
        logger.warning("[fork] checkpoint 上下文注入失败，新会话 agent 将无历史记忆", exc_info=True)

    # 复制源用户的上传文件到当前用户目录
    if source_user_id and uid and source_user_id != uid:
        try:
            await asyncio.to_thread(_copy_user_uploads, source_user_id, uid)
        except Exception:
            logger.warning("[fork] 附件复制失败，新会话可能无法展示旧附件", exc_info=True)

    return Success(data={
        "sessionKey": session.session_key,
        "title": session.title,
        "threadId": session.thread_id,
        "messageCount": session.message_count,
        "isStarred": session.is_starred,
        "createdAt": int(session.create_time.timestamp() * 1000) if session.create_time else None,
        "updatedAt": int(session.update_time.timestamp() * 1000) if session.update_time else None,
    })
