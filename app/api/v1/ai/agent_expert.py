"""
Agent 专家 API（商店模型，取代旧职业体系）

专家 = 「人设 + 方法论 + 技能包 + 连接器」的封装体，**会话级召唤**：
消息里 @专家名 → 当前会话绑定该专家并驻留（agent_session.expert_key），
人设 instructions 注入系统提示词，绑定技能/连接器在该会话运行时并入生效集。

- 统一可见性尺子（与技能/连接器一致，无 visibility 机制）：可见 = is_enabled=1（已上架）OR user_id==uid（本人创建）
- 新建默认 is_enabled=0（未上架仅创建者可见），上下架仅管理员（否则 4032）
- 个人偏好（agent_expert_user_pref）：is_added / is_enabled 正交，与技能/连接器偏好同口径
- 绑定校验：skill_keys / connector_keys 只保留库里存在的 key；专家删除时引用会话置空、偏好行清除
- 生效即时性：agent cache_key 含专家 update_ts，定义一变 key 自动变，无需逐用户 evict
"""

from __future__ import annotations

import re
import secrets
from typing import List, Optional, Union

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field
from tortoise.expressions import Q

from app.api.v1.ai.role_tier import norm_tier_codes, resolve_user_tier_codes, tier_allows
from app.core.ctx import CTX_USER_ID
from app.models.standard.agent import (
    AgentConnector,
    AgentExpert,
    AgentExpertUserPref,
    AgentSession,
    AgentSkill,
    AgentSkillUserPref,
)
from app.models.system import User
from app.schemas.base import Fail, Success, SuccessExtra

router = APIRouter(prefix="/agent/experts", tags=["Agent 专家"])

_AT_PATTERN = re.compile(r"@([^\s@{}]+)")


# ── Helpers（权限口径复用技能侧，函数级惰性 import 防循环依赖） ──────────────


async def get_current_role_codes():
    from app.api.v1.ai.agent_skill import get_current_role_codes as _impl

    return await _impl()


def _is_manager(is_super: bool, role_codes: list[str]) -> bool:
    from app.api.v1.ai.agent_skill import _is_manager as _impl

    return _impl(is_super, role_codes)


def _can_manage(obj, uid: Optional[int], is_manager: bool) -> bool:
    from app.api.v1.ai.agent_skill import _can_manage as _impl

    return _impl(obj, uid, is_manager)


def _listed_or_own(obj, uid: Optional[int]) -> bool:
    from app.api.v1.ai.agent_skill import _listed_or_own as _impl

    return _impl(obj, uid)


def _default_added(e: AgentExpert, uid: Optional[int]) -> bool:
    """无偏好记录时的缺省 is_added 口径（与技能/连接器一致）：仅本人创建默认已添加。"""
    return uid is not None and e.user_id is not None and e.user_id == uid


def _invalidate_skill_sync(uid: Optional[int]) -> None:
    """偏好/绑定有变 → 失效该用户 workspace 的技能同步节流（下次对话请求即重算技能面）。"""
    if uid is None:
        return
    try:
        from app.api.v1.ai import qa as _qa

        _qa.invalidate_skill_sync(_qa._user_workspace(uid))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[expert] 技能同步节流失效失败（不影响写入）: {e}")


async def _replace_added_skills(uid: int, skill_ids: list[int]) -> None:
    """全量替换「已添加」技能：给定技能置 is_added=1，其余已有偏好行置 0（引导完成时用）。"""
    valid_ids = {s.id for s in await AgentSkill.filter(id__in=skill_ids, is_enabled=1)} if skill_ids else set()
    if valid_ids:
        keys = [k for (k,) in await AgentSkill.filter(id__in=list(valid_ids)).values_list("skill_key")]
        for key in keys:
            await AgentSkillUserPref.update_or_create(defaults={"is_added": 1}, user_id=uid, skill_key=key)
        await AgentSkillUserPref.filter(user_id=uid).exclude(skill_key__in=keys).update(is_added=0)
    _invalidate_skill_sync(uid)


async def _filter_existing_skill_keys(keys: Optional[list]) -> list[str]:
    """绑定技能只留库里存在（且未软删）的 skill_key，去重保序。"""
    ks = [k for k in dict.fromkeys(keys or []) if isinstance(k, str) and k]
    if not ks:
        return []
    rows = await AgentSkill.filter(skill_key__in=ks).values_list("skill_key", flat=True)
    valid = set(rows)
    return [k for k in ks if k in valid]


async def _filter_existing_connector_keys(keys: Optional[list]) -> list[str]:
    ks = [k for k in dict.fromkeys(keys or []) if isinstance(k, str) and k]
    if not ks:
        return []
    rows = await AgentConnector.filter(connector_key__in=ks).values_list("connector_key", flat=True)
    valid = set(rows)
    return [k for k in ks if k in valid]


def _expert_to_dict(
    e: AgentExpert,
    user_enabled: Optional[bool] = None,
    user_added: Optional[bool] = None,
    user_added_at: Optional[int] = None,
    author: Optional[str] = None,
) -> dict:
    uid = CTX_USER_ID.get() or None
    created_at = int(e.create_time.timestamp() * 1000) if e.create_time else None
    added_at = user_added_at
    if added_at is None and user_added:
        added_at = created_at
    return {
        "id": e.id,
        "expertKey": e.expert_key,
        "name": e.name,
        "icon": e.icon,
        "description": e.description,
        "instructions": e.instructions,
        "welcomeMessage": e.welcome_message,
        "skillKeys": list(e.skill_keys) if e.skill_keys else [],
        "connectorKeys": list(e.connector_keys) if e.connector_keys else [],
        "category": e.category,
        "userId": e.user_id,
        "author": author,
        "sortOrder": e.sort_order,
        "isEnabled": bool(e.is_enabled),
        "minTierCode": getattr(e, "min_tier_code", None),
        "exampleQuestions": list(getattr(e, "example_questions", None) or []),
        "userEnabled": True if user_enabled is None else bool(user_enabled),
        "isAdded": _default_added(e, uid) if user_added is None else bool(user_added),
        "addedAt": added_at,
        "createdAt": created_at,
        "updatedAt": int(e.update_time.timestamp() * 1000) if e.update_time else None,
    }


async def _experts_to_records(rows: list, uid: Optional[int]) -> list[dict]:
    """把本页 AgentExpert 行批量序列化为出参（偏好三 map + 作者名一次性预取）。"""
    enabled_map: dict[str, bool] = {}
    added_map: dict[str, bool] = {}
    added_at_map: dict[str, int] = {}
    keys = [e.expert_key for e in rows]
    if uid and keys:
        prefs = await AgentExpertUserPref.filter(user_id=uid, expert_key__in=keys).all()
        enabled_map = {p.expert_key: bool(p.is_enabled) for p in prefs}
        added_map = {p.expert_key: bool(p.is_added) for p in prefs}
        added_at_map = {p.expert_key: int(p.update_time.timestamp() * 1000) for p in prefs if p.update_time}
    from app.api.v1.ai.agent_skill import _author_label_map

    author_map = await _author_label_map([e.user_id for e in rows if e.user_id is not None])
    return [
        _expert_to_dict(
            e,
            enabled_map.get(e.expert_key, True),
            added_map.get(e.expert_key, _default_added(e, uid)),
            added_at_map.get(e.expert_key),
            author_map.get(e.user_id),
        )
        for e in rows
    ]


async def _fallback_user_codes(uid: Optional[int]) -> frozenset:
    """用户档位集合兜底：按当前登录用户现算；上下文用户与 uid 不一致时保守落空集（仅全员可见实体）。"""
    _ctx_uid, _codes, _sup = await get_current_role_codes()
    if _ctx_uid is not None and (uid is None or _ctx_uid == uid):
        return await resolve_user_tier_codes(_codes, _is_manager(_sup, _codes))
    return frozenset()


async def get_effective_expert(uid: Optional[int], expert_key: Optional[str], user_codes: Optional[frozenset] = None) -> Optional[AgentExpert]:
    """会话绑定专家的生效判定（qa.py 构建期 / 前端回显共用，口径不漂移）：
    存在 + （上架 or 本人创建）+ 档位足够 + 本人未禁用（偏好行 is_enabled=0 则失效）。

    user_codes：调用方已算好的用户档位集合（qa.py 链路）；None 时兜底现算。
    """
    if not expert_key:
        return None
    e = await AgentExpert.get_or_none(expert_key=expert_key)
    if e is None or not _listed_or_own(e, uid):
        return None
    codes = user_codes
    if codes is None:
        codes = await _fallback_user_codes(uid)
    if not await tier_allows(e.min_tier_code, e.user_id, uid, codes):
        return None
    if uid is not None:
        p = await AgentExpertUserPref.get_or_none(user_id=uid, expert_key=expert_key)
        if p is not None and not p.is_enabled:
            return None
    return e


async def resolve_experts_from_text(message: str, uid: Optional[int], user_codes: Optional[frozenset] = None) -> list[AgentExpert]:
    """@专家召唤解析：抓 @token，按专家名或 expert_key 命中（口径同 get_effective_expert）。

    与技能 @ 解析同款模式；同 token 既命中技能又命中专家时由调用方（qa.py）优先技能。
    返回按消息出现顺序去重后的专家行。

    user_codes：调用方已算好的用户档位集合；None 时兜底现算（不够档的专家 @ 不到）。
    """
    if not message or "@" not in message:
        return []
    tokens = [t for t in _AT_PATTERN.findall(message) if t]
    if not tokens:
        return []
    codes = user_codes
    if codes is None:
        codes = await _fallback_user_codes(uid)
    from tortoise.expressions import Q

    rows = await AgentExpert.filter(Q(is_enabled=1) | Q(user_id=uid if uid is not None else -1))
    pref_map = {p.expert_key: p for p in await AgentExpertUserPref.filter(user_id=uid)} if uid is not None else {}
    by_token: dict[str, AgentExpert] = {}
    for e in rows:
        p = pref_map.get(e.expert_key)
        if p is not None and not p.is_enabled:
            continue  # 本人禁用：不可召唤
        if not await tier_allows(e.min_tier_code, e.user_id, uid, codes):
            continue  # 不在可见白名单：不可召唤（作者恒见自己实体在 tier_allows 内）
        by_token.setdefault(e.name, e)
        by_token.setdefault(e.expert_key, e)
    out: list[AgentExpert] = []
    seen: set[str] = set()
    for t in tokens:
        e = by_token.get(t)
        if e is not None and e.expert_key not in seen:
            seen.add(e.expert_key)
            out.append(e)
    return out


# ── Req 模型 ─────────────────────────────────────────────────────────────────


class ExpertCreateReq(BaseModel):
    name: str = Field(..., description="专家名（1~32 字）")
    icon: Optional[str] = Field(None, description="图标标识（iconify 图标名）")
    description: Optional[str] = Field(None, description="卡片一句话简介（≤200 字）")
    instructions: Optional[str] = Field(None, description="人设与方法论提示词（注入系统提示词）")
    welcome_message: Optional[str] = Field(None, alias="welcomeMessage", description="绑定会话首屏欢迎语")
    skill_keys: Optional[List[str]] = Field(None, alias="skillKeys", description="绑定技能 key 列表")
    connector_keys: Optional[List[str]] = Field(None, alias="connectorKeys", description="绑定连接器 key 列表")
    category: Optional[str] = Field(None, description="专家中心分组（≤32 字）")
    example_questions: Optional[List[str]] = Field(None, alias="exampleQuestions", description="快捷提问（字符串数组）：卡片展示，用户点击即添加该专家并把问题填入输入框")

    class Config:
        populate_by_name = True


class ExpertUpdateReq(BaseModel):
    name: Optional[str] = Field(None, description="专家名（1~32 字）")
    icon: Optional[str] = Field(None, description="图标；空串=清除")
    description: Optional[str] = Field(None, description="简介；空串=清除")
    instructions: Optional[str] = Field(None, description="人设与方法论提示词；空串=清除")
    welcome_message: Optional[str] = Field(None, alias="welcomeMessage", description="欢迎语；空串=清除")
    skill_keys: Optional[List[str]] = Field(None, alias="skillKeys", description="绑定技能（整体替换）")
    connector_keys: Optional[List[str]] = Field(None, alias="connectorKeys", description="绑定连接器（整体替换）")
    category: Optional[str] = Field(None, description="分组；空串=清除")
    sort_order: Optional[int] = Field(None, alias="sortOrder", description="排序（仅管理员）")
    is_enabled: Optional[bool] = Field(None, alias="isEnabled", description="商店上架/下架（仅管理员）")
    min_tier_code: Optional[Union[List[str], str]] = Field(
        None, alias="minTierCode", description="可见档位白名单（档位 code 数组，如 ['paid','gov']）；[]/'all'=全员可见；不传=不改；显式多选无包含关系（仅管理员）"
    )
    example_questions: Optional[List[str]] = Field(None, alias="exampleQuestions", description="快捷提问（字符串数组）；空数组=清除；不传不改")

    class Config:
        populate_by_name = True


class ExpertPrefsReq(BaseModel):
    expert_keys: List[str] = Field(..., alias="expertKeys", description="专家 key 列表（≤200）")
    is_enabled: Optional[bool] = Field(None, alias="isEnabled", description="个人启用/禁用")
    is_added: Optional[bool] = Field(None, alias="isAdded", description="是否已添加到我的专家")

    class Config:
        populate_by_name = True


class ExpertManageBatchReq(BaseModel):
    expert_keys: List[str] = Field(..., alias="expertKeys", description="专家 key 列表（≤200）")
    is_enabled: Optional[bool] = Field(None, alias="isEnabled", description="批量上架/下架")

    class Config:
        populate_by_name = True


class ExpertKeysReq(BaseModel):
    expert_keys: List[str] = Field(..., alias="expertKeys", description="专家 key 列表（≤200）")

    class Config:
        populate_by_name = True


class OnboardingCompleteReq(BaseModel):
    """完成引导：勾选的专家（多选，进「我的专家」）+ 勾选的技能（落 is_added）"""
    expert_keys: List[str] = Field(default_factory=list, alias="expertKeys")
    action_ids: List[int] = Field(default_factory=list, alias="actionIds")

    class Config:
        populate_by_name = True


# ── CRUD ─────────────────────────────────────────────────────────────────────


@router.get("", summary="专家列表")
async def list_experts(
    include_disabled: bool = False,
    current: Optional[int] = None,
    size: Optional[int] = None,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
    user_id: Optional[int] = None,
):
    """商店模型（与技能/连接器同口径）：默认只回上架中；include_disabled=true 附带未上架行
    （仅管理者或作者本人可见）——上架管理视图用。

    上架管理分页：管理员 + include_disabled + 同时传 current/size 时走 DB 分页，返回
    SuccessExtra({records}, total, current, size)；支持 keyword（名称/key/描述/分组模糊）、
    status（all/enabled/disabled）、user_id（0=官方）。专家无精选/分类词表维度。
    其余情形逐字保留全量逻辑（兼容既有调用方）。
    """
    from app.api.v1.ai.agent_skill import manage_paging_enabled, paging_bounds, q_author

    uid, role_codes, is_super = await get_current_role_codes()
    is_mgr = _is_manager(is_super, role_codes)

    # ── 分页分支（上架管理页，管理员专属） ──
    if manage_paging_enabled(is_mgr, include_disabled, current, size):
        cur, siz = paging_bounds(current, size)
        qs = AgentExpert.all()
        kw = (keyword or "").strip()
        if kw:
            qs = qs.filter(
                Q(name__icontains=kw) | Q(expert_key__icontains=kw) | Q(description__icontains=kw) | Q(category__icontains=kw)
            )
        st = (status or "all").strip() or "all"
        if st == "enabled":
            qs = qs.filter(is_enabled=1)
        elif st == "disabled":
            qs = qs.filter(is_enabled=0)
        qa = q_author(user_id)
        if qa is not None:
            qs = qs.filter(qa)
        total = await qs.count()
        # 确定性排序（跨页不重不漏）：在架优先，保留组内 sort_order 语义，id 收尾
        rows = await qs.order_by("-is_enabled", "sort_order", "id").offset((cur - 1) * siz).limit(siz)
        records = await _experts_to_records(rows, uid)
        return SuccessExtra(data={"records": records}, total=total, current=cur, size=siz)

    # ── 全量分支（原有逻辑，保持不变） ──
    tier_codes = await resolve_user_tier_codes(role_codes, is_mgr)
    qs = AgentExpert.all()
    if not include_disabled:
        qs = qs.filter(is_enabled=1)
    rows = await qs.order_by("sort_order", "id")
    visible = []
    for e in rows:
        if not is_mgr and not _listed_or_own(e, uid):
            continue
        if not e.is_enabled and not (is_mgr or _can_manage(e, uid, is_mgr)):
            continue
        # 档位过滤：不在可见白名单的用户看不到（作者恒见自己实体，在 tier_allows 内）
        if not await tier_allows(e.min_tier_code, e.user_id, uid, tier_codes):
            continue
        visible.append(e)
    records = await _experts_to_records(visible, uid)
    return Success(data=records)


@router.get("/manage-authors", summary="上架管理：专家作者清单（仅管理员）")
async def manage_authors():
    """作者筛选下拉源：按作者分组计数 [{userId, author, count}]（userId=None 为「官方」桶）。"""
    from app.api.v1.ai.agent_skill import _author_facets

    _uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅超管/管理员可见")
    return Success(data=await _author_facets(AgentExpert.all()))


@router.post("", summary="新建专家（默认未上架，仅创建者可见）")
async def create_expert(req: ExpertCreateReq):
    from app.api.v1.ai.agent_skill import _norm_example_questions

    uid = CTX_USER_ID.get() or None
    if uid is None:
        return Fail(msg="未登录，无法创建专家")
    name = (req.name or "").strip()
    if not name or len(name) > 32:
        return Fail(msg="专家名称需为 1~32 字")
    if await AgentExpert.get_or_none(name=name):
        return Fail(code="4009", msg="专家名已存在")
    for _ in range(3):
        key = f"exp_{secrets.token_hex(3)}"
        if not await AgentExpert.get_or_none(expert_key=key):
            break
    e = await AgentExpert.create(
        expert_key=key,
        name=name,
        icon=(req.icon or "").strip() or None,
        description=(req.description or "").strip()[:200] or None,
        instructions=(req.instructions or "").strip() or None,
        welcome_message=(req.welcome_message or "").strip() or None,
        skill_keys=await _filter_existing_skill_keys(req.skill_keys) or None,
        connector_keys=await _filter_existing_connector_keys(req.connector_keys) or None,
        category=(req.category or "").strip()[:32] or None,
        user_id=uid,
        is_enabled=0,
        created_by=uid,
        example_questions=_norm_example_questions(req.example_questions) or None,
    )
    logger.info(f"[expert] 新建专家 id={e.id} key={e.expert_key} name={e.name} uid={uid}")
    return Success(data=_expert_to_dict(e, True, True, author=None))


async def _get_expert_by_id(expert_id: int) -> Optional[AgentExpert]:
    return await AgentExpert.get_or_none(id=expert_id)


@router.patch("/{expert_id}", summary="更新专家（创建者/管理员；上下架仅管理员）")
async def update_expert(expert_id: int, req: ExpertUpdateReq):
    uid, role_codes, is_super = await get_current_role_codes()
    is_mgr = _is_manager(is_super, role_codes)
    e = await _get_expert_by_id(expert_id)
    if e is None:
        return Fail(msg="专家不存在")
    if not _can_manage(e, uid, is_mgr):
        return Fail(code="4032", msg="forbidden: 仅创建者或管理员可编辑该专家")

    if req.is_enabled is not None and bool(e.is_enabled) != req.is_enabled:
        if not is_mgr:
            return Fail(code="4032", msg="forbidden: 仅管理员可上下架专家")
        e.is_enabled = 1 if req.is_enabled else 0
    if req.sort_order is not None:
        if not is_mgr:
            return Fail(code="4032", msg="forbidden: 仅管理员可调整专家排序")
        e.sort_order = req.sort_order
    # 可见档位白名单：仅管理员可设置；[]/'all' 归一为 NULL（全员可见）；非法档位 code → 4000；不传不改
    if req.min_tier_code is not None:
        if not is_mgr:
            return Fail(code="4032", msg="forbidden: 仅管理员可设置可见档位")
        norm, terr = await norm_tier_codes(req.min_tier_code)
        if terr:
            return Fail(msg=terr)
        e.min_tier_code = norm
    if req.name is not None:
        name = req.name.strip()
        if not name or len(name) > 32:
            return Fail(msg="专家名称需为 1~32 字")
        dup = await AgentExpert.get_or_none(name=name)
        if dup and dup.id != expert_id:
            return Fail(code="4009", msg="专家名已存在")
        e.name = name
    if req.icon is not None:
        e.icon = req.icon.strip() or None
    if req.description is not None:
        e.description = req.description.strip()[:200] or None
    if req.instructions is not None:
        e.instructions = req.instructions.strip() or None
    if req.welcome_message is not None:
        e.welcome_message = req.welcome_message.strip() or None
    if req.skill_keys is not None:
        e.skill_keys = await _filter_existing_skill_keys(req.skill_keys) or None
    if req.connector_keys is not None:
        e.connector_keys = await _filter_existing_connector_keys(req.connector_keys) or None
    if req.category is not None:
        e.category = req.category.strip()[:32] or None
    if req.example_questions is not None:
        from app.api.v1.ai.agent_skill import _norm_example_questions

        e.example_questions = _norm_example_questions(req.example_questions) or None

    await e.save()
    # cache_key 含专家 update_ts：定义一变，相关会话下一条消息自动重建 agent，无需 evict
    from app.api.v1.ai.agent_skill import _author_label_map

    author_map = await _author_label_map([e.user_id] if e.user_id is not None else [])
    return Success(data=_expert_to_dict(e, author=author_map.get(e.user_id)))


@router.delete("/{expert_id}", summary="删除专家（创建者/管理员）")
async def delete_expert(expert_id: int):
    uid, role_codes, is_super = await get_current_role_codes()
    is_mgr = _is_manager(is_super, role_codes)
    e = await _get_expert_by_id(expert_id)
    if e is None:
        return Fail(msg="专家不存在")
    if not _can_manage(e, uid, is_mgr):
        return Fail(code="4032", msg="forbidden: 仅创建者或管理员可删除该专家")
    # 引用会话优雅降级为通用会话；偏好行清除
    await AgentSession.filter(expert_key=e.expert_key).update(expert_key=None)
    await AgentExpertUserPref.filter(expert_key=e.expert_key).delete()
    await e.delete()
    logger.info(f"[expert] 删除专家 id={expert_id} key={e.expert_key}")
    return Success(data={"deleted": expert_id})


@router.put("/prefs", summary="批量设置专家个人偏好（添加/移除、启用/禁用，只影响当前用户）")
async def batch_expert_prefs(req: ExpertPrefsReq):
    uid, role_codes, is_super = await get_current_role_codes()
    if uid is None:
        return Fail(msg="未登录，无法设置专家偏好")
    if req.is_enabled is None and req.is_added is None:
        return Fail(msg="is_enabled 与 is_added 至少传一个")
    keys = list(dict.fromkeys(req.expert_keys))
    if not keys:
        return Fail(msg="expert_keys 不能为空")
    if len(keys) > 200:
        return Fail(msg="单次最多设置 200 个专家")
    tier_codes = await resolve_user_tier_codes(role_codes, _is_manager(is_super, role_codes))
    # 只处理「已上架或本人创建 且 在可见白名单内」的专家；其余静默过滤（堵 API 直调绕过）
    rows = await AgentExpert.filter(expert_key__in=keys)
    valid_keys = [
        e.expert_key
        for e in rows
        if _listed_or_own(e, uid) and await tier_allows(e.min_tier_code, e.user_id, uid, tier_codes)
    ]
    defaults: dict = {}
    if req.is_enabled is not None:
        defaults["is_enabled"] = 1 if req.is_enabled else 0
    if req.is_added is not None:
        defaults["is_added"] = 1 if req.is_added else 0
    for k in valid_keys:
        await AgentExpertUserPref.update_or_create(defaults=defaults, user_id=uid, expert_key=k)
    if valid_keys and req.is_enabled is not None:
        # 禁用可能使存量绑定会话的专家失效 → 失效技能同步节流（agent 侧按 get_effective_expert 自动降级）
        _invalidate_skill_sync(uid)
    return Success(data={"updated": valid_keys})


# ── 上架管理批量操作（管理员）；路径纪律同连接器侧 ───────────────────────────


@router.put("/manage", summary="批量管理专家（上架/下架，仅管理员）")
async def batch_manage_experts(req: ExpertManageBatchReq):
    uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅管理员可批量管理专家")
    if req.is_enabled is None:
        return Fail(msg="is_enabled 必传")
    keys = list(dict.fromkeys(req.expert_keys))
    if not keys:
        return Fail(msg="expert_keys 不能为空")
    if len(keys) > 200:
        return Fail(msg="单次最多管理 200 个专家")
    rows = await AgentExpert.filter(expert_key__in=keys)
    updated: list[str] = []
    skipped: list[str] = []
    by_key = {e.expert_key: e for e in rows}
    for k in keys:
        e = by_key.get(k)
        if e is None:
            skipped.append(k)
            continue
        e.is_enabled = 1 if req.is_enabled else 0
        await e.save()
        updated.append(k)
    return Success(data={"updated": updated, "skipped": skipped})


@router.post("/manage-delete", summary="批量删除专家（管理员可删全部；作者可删自己的）")
async def batch_delete_experts(req: ExpertKeysReq):
    from tortoise.transactions import in_transaction

    uid, role_codes, is_super = await get_current_role_codes()
    is_mgr = _is_manager(is_super, role_codes)
    keys = list(dict.fromkeys(req.expert_keys))
    if not keys:
        return Fail(msg="expert_keys 不能为空")
    if len(keys) > 200:
        return Fail(msg="单次最多删除 200 个专家")
    rows = await AgentExpert.filter(expert_key__in=keys)
    by_key = {e.expert_key: e for e in rows}
    updated: list[str] = []
    skipped: list[str] = []
    for k in keys:
        e = by_key.get(k)
        if e is None or not _can_manage(e, uid, is_mgr):
            skipped.append(k)
            continue
        try:
            async with in_transaction("conn_standard"):
                await AgentSession.filter(expert_key=k).update(expert_key=None)
                await AgentExpertUserPref.filter(expert_key=k).delete()
                await e.delete()
            updated.append(k)
        except Exception:  # noqa: BLE001
            logger.exception(f"批量删除专家失败 {k}")
            skipped.append(k)
    return Success(data={"updated": updated, "skipped": skipped})


# ── 新手引导（取代旧 quick-actions 的 onboarding） ───────────────────────────


@router.get("/onboarding", summary="新手引导数据")
async def get_expert_onboarding():
    """引导数据：是否需要引导 + 上架专家列表 + 全量可见技能（供勾选）+ 当前状态回显。

    返回 {needOnboarding, experts, actions, categories, groups, current}：
    - experts：上架中的专家（含 isAdded 回显）
    - actions/categories/groups：快捷功能橱窗同源（技能派生），第二步勾选用
    - current：已添加技能 id + 我的专家 key（设置模式回显）
    """
    from app.api.v1.ai.quick_action import _build_quick_action_list

    uid, role_codes, is_super = await get_current_role_codes()
    tier_codes = await resolve_user_tier_codes(role_codes, _is_manager(is_super, role_codes))
    full = await _build_quick_action_list(include_all=False)

    experts_qs = await AgentExpert.filter(is_enabled=1).order_by("sort_order", "id")
    enabled_map: dict[str, bool] = {}
    added_map: dict[str, bool] = {}
    if uid:
        prefs = await AgentExpertUserPref.filter(user_id=uid).all()
        enabled_map = {p.expert_key: bool(p.is_enabled) for p in prefs}
        added_map = {p.expert_key: bool(p.is_added) for p in prefs}
    expert_dicts = []
    for e in experts_qs:
        if uid is not None and not enabled_map.get(e.expert_key, True):
            continue  # 本人禁用的专家不进引导
        if not await tier_allows(e.min_tier_code, e.user_id, uid, tier_codes):
            continue  # 不在可见白名单的专家不进引导
        expert_dicts.append(_expert_to_dict(
            e,
            enabled_map.get(e.expert_key, True),
            added_map.get(e.expert_key, _default_added(e, uid)),
        ))

    need = True
    current = {"expertKeys": [], "actionIds": []}
    if uid:
        user = await User.get_or_none(id=uid)
        need = user is not None and user.onboarded_at is None
        current = {
            "expertKeys": [k for k, v in added_map.items() if v],
            "actionIds": [a["id"] for a in full["actions"] if a.get("isAdded")],
        }

    return Success(data={
        "needOnboarding": need,
        "experts": expert_dicts,
        "actions": full["actions"],
        "categories": full["categories"],
        "groups": full["groups"],
        "current": current,
    })


@router.post("/onboarding/complete", summary="完成新手引导")
async def complete_expert_onboarding(req: OnboardingCompleteReq):
    """写 onboarded_at；勾选专家进「我的专家」（is_added=1，多选）；勾选技能落 is_added。"""
    uid = CTX_USER_ID.get() or None
    if not uid:
        return Fail(code="4000", msg="请先登录")

    from datetime import datetime

    _uid, _role_codes, _is_super = await get_current_role_codes()
    _tier_codes = await resolve_user_tier_codes(_role_codes, _is_manager(_is_super, _role_codes))
    added_keys: List[str] = []
    if req.expert_keys:
        rows = await AgentExpert.filter(expert_key__in=req.expert_keys, is_enabled=1)
        # 不在可见白名单的专家视同不存在（堵 API 直调绕过）
        rows = [e for e in rows if await tier_allows(e.min_tier_code, e.user_id, uid, _tier_codes)]
        if len(rows) != len(set(req.expert_keys)):
            return Fail(code="4004", msg="部分专家不存在或已下架")
        for e in rows:
            await AgentExpertUserPref.update_or_create(
                defaults={"is_added": 1, "is_enabled": 1}, user_id=uid, expert_key=e.expert_key
            )
            added_keys.append(e.expert_key)

    await _replace_added_skills(uid, req.action_ids)
    await User.filter(id=uid).update(onboarded_at=datetime.now())

    return Success(msg="引导完成", data={"expertKeys": added_keys, "actionIds": req.action_ids})
