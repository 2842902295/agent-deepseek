"""
用户档位定义与判定（实体可见性·显式多选白名单）

三档基线（agent_role_tier 表，启动幂等播种、只补缺失档不回写）：
all=普通用户(rank 0) / paid=付费用户(rank 1) / gov=主管部门(rank 2)。
tier_rank 仅作展示排序用，**不再参与包含关系比较**。

可见性语义（2026-08-27 由「单值最低档 + 包含关系」改为显式白名单）：
- 实体侧字段 `min_tier_code` 为**可见档位 code 数组**（JSON；历史列名保留）：
  NULL / 空数组 = 全员可见；勾选哪些档位，就只有所持这些档位的用户可见，
  **档位之间无包含关系**（只勾 paid 时 gov 用户也看不到，除非也勾 gov）。
- 用户档位集合 = 其所持角色（role_codes 命中）映射到的全部档位 code。
- R_SUPER / R_ADMIN 恒全可见（MANAGER_CODES 哨兵）；作者恒可见自己的实体。
- 实体里出现未知档位 code：运行期剔除后按剩余集合判；全是未知码则 fail-open 全员可见。

档位定义的增删改由 system-admin 经 admin_save_record 完成（agent_role_tier 在
admin 注册表内），写后钩子失效本模块缓存并清 agent 实例缓存。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from app.models.standard.agent import AgentRoleTier
from app.schemas.base import Success

router = APIRouter(prefix="/role-tiers", tags=["AI-用户档位"])

# 管理员哨兵：用户档位集合含 "*" 即管理员，一切可见性判定恒过。
# 用哨兵元素而非 None，保证 user_codes 类型统一为 frozenset，
# 各调用点 Optional[frozenset] 的 None 语义留给「未提供，现算」。
MANAGER_TOKEN = "*"
MANAGER_CODES = frozenset({MANAGER_TOKEN})

# 进程内缓存（tier_rank 升序）。失效点只有两个：admin agent 写钩子、启动播种。
# 表仅数行而热路径每消息多次触碰，做缓存但不上 TTL/Redis。
_TIERS_CACHE: Optional[list] = None


async def load_tiers(force_refresh: bool = False) -> list:
    """档位定义清单（tier_rank 升序，仅作展示排序）。"""
    global _TIERS_CACHE
    if _TIERS_CACHE is None or force_refresh:
        _TIERS_CACHE = list(await AgentRoleTier.all().order_by("tier_rank", "id"))
    return _TIERS_CACHE


def invalidate_tier_cache() -> None:
    """档位表写后失效（admin_tools 写钩子调用）。"""
    global _TIERS_CACHE
    _TIERS_CACHE = None


async def resolve_user_tier_codes(role_codes: list, is_manager: bool) -> frozenset:
    """用户持有的档位 code 集合：管理员 → MANAGER_CODES 哨兵（恒全可见）；
    否则所持角色命中的全部档位 code（可同时命中多档），无标记角色 = 空集。"""
    if is_manager:
        return MANAGER_CODES
    held = set(role_codes or [])
    if not held:
        return frozenset()
    codes: set = set()
    for t in await load_tiers():
        rc = t.role_codes or []
        if rc and held.intersection(rc):
            codes.add(t.tier_code)
    return frozenset(codes)


def _parse_visible_codes(visible_codes) -> set:
    """实体可见档位白名单归一为 set：None/空 → 空集（=全员）；
    兼容历史逗号分隔字符串与 JSON 数组；'all' 视为全员。"""
    if not visible_codes:
        return set()
    if isinstance(visible_codes, str):
        if visible_codes.strip().lower() == "all":
            return set()
        parts = [c.strip() for c in visible_codes.split(",")]
    else:
        parts = [str(c).strip() for c in visible_codes]
    out = {c for c in parts if c and c.lower() != "all"}
    return out


async def tier_ok(visible_codes, user_codes: frozenset) -> bool:
    """白名单判定：管理员恒过；白名单空 = 全员可见；
    否则要求用户档位集合与白名单交集非空。白名单中的未知 code 剔除后仍
    为空则 fail-open 全员可见（防配置漂移误隐藏；写入侧已校验）。"""
    if MANAGER_TOKEN in user_codes:
        return True
    vis = _parse_visible_codes(visible_codes)
    if not vis:
        return True
    known = {t.tier_code for t in await load_tiers()}
    vis &= known
    if not vis:
        return True
    return bool(vis & user_codes)


async def tier_allows(visible_codes, owner_uid: Optional[int], uid: Optional[int], user_codes: frozenset) -> bool:
    """合成判定：作者恒可见自己的实体；其余走白名单判定。"""
    if uid is not None and owner_uid is not None and owner_uid == uid:
        return True
    return await tier_ok(visible_codes, user_codes)


def tier_cache_part(user_codes: frozenset) -> str:
    """agent 缓存键的档位片段：档位集合变化 → 键变 → agent 自动重建。"""
    if MANAGER_TOKEN in user_codes:
        return "_tM"
    return "_t" + (".".join(sorted(user_codes)) if user_codes else "0")


async def norm_tier_codes(raw) -> tuple[Optional[list], Optional[str]]:
    """min_tier_code 入参归一（写侧）：接受数组或逗号分隔字符串；
    None/空/含 'all' → None（存 NULL = 全员）；逐项校验在档位词表内（'all' 除外）。
    返回 (归一后的 code 数组或 None, 错误消息)。与 _norm_category 同款「只认词表」纪律。"""
    if raw is None:
        return None, None
    if isinstance(raw, str):
        items = [c.strip() for c in raw.split(",")]
    elif isinstance(raw, (list, tuple, set)):
        items = [str(c).strip() for c in raw]
    else:
        items = [str(raw).strip()]
    codes: list = []
    for c in items:
        if not c or c.lower() == "all":
            continue  # 'all'/空 = 全员，等价于不选
        if c in codes:
            continue
        codes.append(c)
    if not codes:
        return None, None
    valid = {t.tier_code for t in await load_tiers()} - {"all"}
    bad = [c for c in codes if c not in valid]
    if bad:
        shown = "/".join(sorted(valid)) if valid else "（档位表为空）"
        return None, f"未知档位 code：{'、'.join(bad)}（可选值：{shown}；不选=全员可见）"
    return sorted(codes), None


@router.get("", summary="用户档位清单（登录可读）")
async def get_role_tiers():
    """档位选项清单（实体表单「可见范围」多选组件用）+ 当前用户所持档位。"""
    from app.api.v1.ai.agent_skill import _is_manager, get_current_role_codes  # 惰导防环

    _uid, role_codes, is_super = await get_current_role_codes()
    tiers = await load_tiers()
    my = await resolve_user_tier_codes(role_codes, _is_manager(is_super, role_codes))
    return Success(
        data={
            "tiers": [{"tierCode": t.tier_code, "tierName": t.tier_name, "tierRank": t.tier_rank} for t in tiers],
            "myCodes": [] if MANAGER_TOKEN in my else sorted(my),
        }
    )
