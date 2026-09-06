"""
Agent 技能 API（商店模型）

- /agent/skills CRUD + 凝练
- 两个正交状态：商店上架/下架（AgentSkill.is_enabled，全局，仅管理员可改）、
  个人「已添加/未添加」与「启用/禁用」（AgentSkillUserPref.is_added / is_enabled）
- 统一可见性尺子（无 visibility 机制）：可见/可用 = is_enabled=1（已上架）OR user_id==uid（本人创建）
- 个人偏好缺省口径：**无记录 = 未添加**——技能上架不会自动进他人「我的技能」，
  仅本人创建的技能默认已添加（_default_added）；builtin（如「编辑」）不进商店、不受此口径约束
- 匹配逻辑：消息里出现 @<skill_key> 时，仅当技能「（已上架或本人创建）且 已添加 且 启用」
  才把对应 skill_md（SKILL.md 全文）注入到 agent 调用（resolve_skills_from_text）
"""

from __future__ import annotations

import re
from typing import Optional, Union

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from tortoise.expressions import Q
from tortoise.functions import Count

from app.api.v1.ai.role_tier import norm_tier_codes, resolve_user_tier_codes, tier_allows
from app.core.ctx import CTX_USER_ID
from app.models.standard.agent import AgentSession, AgentSkill, AgentSkillFile, AgentSkillUserPref
from app.models.system import User
from app.schemas.base import Fail, Success, SuccessExtra

router = APIRouter(prefix="/agent/skills", tags=["Agent 技能"])

# 商店分类默认词表——仅首启播种基线（skill_seed.seed_skill_categories，表为空才插入）。
# **运行时真相源是 DB 表 agent_skill_category**：超管/管理员在「上架管理」页维护，
# system-admin 子 agent 也可直接读写该表。NULL/不在词表内按「其他」展示
SKILL_CATEGORIES = [
    "标准工作",
    "办公文档",
    "数据分析",
    "内容创作",
    "视听媒体",
    "开发编程",
    "工具效率",
    "生活娱乐",
    "其他",
]


async def _skill_categories() -> list[str]:
    """当前生效的分类词表（DB 排序）。"""
    from app.models.standard.agent import AgentSkillCategory

    rows = await AgentSkillCategory.all().order_by("sort_order", "id")
    return [r.name for r in rows]


# ── Helpers ──────────────────────────────────────────────────────────────────

_AT_PATTERN = re.compile(r"@([^\s@{}]+)")


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



def _listed_or_own(obj, uid: Optional[int]) -> bool:
    """统一可见性尺子（取代 visibility 机制）：已上架（is_enabled=1）或本人创建。"""
    return bool(obj.is_enabled) or (obj.user_id is not None and obj.user_id == uid)


def _is_manager(is_super: bool, role_codes: list[str]) -> bool:
    """超管或管理员：拥有技能管理权限（编辑/删除/上下架/精选/档位设置）。"""
    return is_super or "R_ADMIN" in role_codes


def _can_manage(obj, uid: Optional[int], is_super: bool) -> bool:
    """是否可以编辑/删除/改可见性（is_super 参数传入「超管或管理员」的合成结果）。"""
    if is_super:
        return True
    return obj.user_id is not None and obj.user_id == uid


def _notify_skill_changed(uid: Optional[int], *, affects_others: bool = False, force: bool = True) -> None:
    """技能写操作后的统一同步失效钩子（qa.py 顶层导入了本模块，只能函数级惰性反向 import）。

    兜底同步周期已拉到 24h，「改动实时到位」靠本钩子覆盖全部写入口：
    - affects_others=True：已上架技能的可见性/内容/激活文件集变化 → 全局代数 +1，
      其他所有用户下一条消息后台重同步（不阻塞其消息路径）
    - uid 操作者本人：force=True → 下一条消息 await 同步（面板操作立即见效）；
      force=False → 仅后台同步（对话中 agent 工具路径，文件已即时物化）
    """
    try:
        from app.api.v1.ai import qa as _qa

        _qa.notify_skill_changed(uid, affects_others=affects_others, force=force)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[skill] 同步失效失败（不影响写入）: {e}")


async def _norm_category(v: Optional[str]) -> Optional[str]:
    """分类归一：只认 DB 词表（agent_skill_category）内取值（去空白），
    词表外/空一律 None（前端按「其他」展示），防止自由输入造成商店导航出现孤儿分类。"""
    v = (v or "").strip()
    if not v:
        return None
    return v if v in await _skill_categories() else None


# ── 上架管理：服务端分页 + 筛选（连接器/专家端点经惰性 import 复用） ──────────
# 分页路径启用条件（唯一入口）：管理员 + include_disabled + 同时传 current/size。
# 该路径无逐行内存剔除（_listed_or_own / tier_allows 对管理员恒真），DB 分页语义安全；
# 其余情形（含非管理员传分页参数）一律回落全量逻辑，避免「逐行剔除 + DB 分页」叠加漏行。

_MANAGE_STATUS_SKILL = ("all", "enabled", "disabled", "featured")
_MANAGE_STATUS_PLAIN = ("all", "enabled", "disabled")
_PAGE_SIZE_MAX = 200


def manage_paging_enabled(is_mgr: bool, include_disabled: bool, current, size) -> bool:
    """四条件全真才走 DB 分页；否则回落全量（非管理员/未开 include_disabled 传参静默回落，防漏行）。"""
    return is_mgr and include_disabled and current is not None and size is not None


def paging_bounds(current, size) -> tuple[int, int]:
    """分页参数钳制：current >= 1，size 收敛到 1.._PAGE_SIZE_MAX。"""
    cur = max(1, int(current or 1))
    siz = min(_PAGE_SIZE_MAX, max(1, int(size or 1)))
    return cur, siz


def q_author(user_id: Optional[int]) -> Optional[Q]:
    """作者筛选：0 = 「官方」桶（user_id IS NULL，对齐 _skill_to_dict 的官方展示），其余精确匹配；None 不加条件。"""
    if user_id is None:
        return None
    if user_id == 0:
        return Q(user_id__isnull=True)
    return Q(user_id=user_id)


async def q_category_other() -> Q:
    """「其他」分类桶的服务端等价式（对齐前端 skill-categories.ts::displayCategory）。

    NULL / 空串 / 字面量「其他」/ 词表外取值都归入「其他」。注意 SQL 的 NOT IN 对 NULL 行
    不命中，故必须显式并上 isnull/空串；「其他」字面量恒并入（无论是否在词表内都归本桶）。
    """
    vocab = await _skill_categories()
    q = Q(category__isnull=True) | Q(category="") | Q(category="其他")
    if vocab:
        q = q | ~Q(category__in=vocab)
    return q


async def _author_facets(qs) -> list[dict]:
    """作者清单（管理页作者筛选下拉源）：按 user_id 分组计数 + 展示名拼接。

    user_id=None 归并为「官方」桶（userId:None）。排序：官方桶恒首，其余按 count 降序、作者名升序。
    """
    rows = await qs.annotate(cnt=Count("id")).group_by("user_id").values("user_id", "cnt")
    official = 0
    uids: list[int] = []
    counts: dict[int, int] = {}
    for r in rows:
        uid_v = r.get("user_id")
        n = int(r.get("cnt") or 0)
        if uid_v is None:
            official += n
        else:
            uids.append(uid_v)
            counts[uid_v] = counts.get(uid_v, 0) + n
    label_map = await _author_label_map(uids)
    out: list[dict] = []
    if official:
        out.append({"userId": None, "author": "官方", "count": official})
    rest = [{"userId": u, "author": label_map.get(u, "未命名"), "count": counts[u]} for u in counts]
    rest.sort(key=lambda x: (-x["count"], x["author"]))
    out.extend(rest)
    return out


def _norm_example_questions(raw) -> list[str]:
    """卡片快捷提问归一（技能/专家/连接器共用）：逐条 strip、去空白项、去重（保序）、
    单条截 100 字、最多 6 条；空结果返回 []。"""
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, (list, tuple)) else [raw]:
        text = str(item or "").strip()[:100]
        if text and text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) >= 6:
            break
    return out


def _default_added(s, uid: Optional[int]) -> bool:
    """个人偏好「无记录」时的缺省 is_added 口径（商店模型）：

    默认**未添加**——技能上架只进商店，不自动进他人「我的技能」；
    仅本人创建的技能（user_id == uid）默认已添加，保证作者建完即可用。
    builtin（如「编辑」）不进商店，由调用方单独豁免，不走这里。
    """
    return uid is not None and s.user_id is not None and s.user_id == uid


def _mask_login(s: str) -> str:
    """脱敏登录名（手机号）：前 3 位 + **** + 后 4 位；太短原样返回。"""
    s = s or ""
    return f"{s[:3]}****{s[-4:]}" if len(s) >= 7 else s


def _author_label(row: dict) -> str:
    """作者展示名：nick_name 优先；缺失回落脱敏 user_name（是手机号，禁止裸显）；再缺失「未命名」。

    nick_name 与登录名相同或为纯数字（注册默认值即手机号）视为未起昵称，同样走脱敏回落。
    """
    nick = (row.get("nick_name") or "").strip()
    login = (row.get("user_name") or "").strip()
    if nick and nick != login and not nick.isdigit():
        return nick
    return _mask_login(login) if login else "未命名"


async def _author_label_map(user_ids: list[int]) -> dict[int, str]:
    """批量预取作者展示名（避免逐条 N+1）：{user_id: author}。"""
    if not user_ids:
        return {}
    rows = await User.filter(id__in=user_ids).values("id", "user_name", "nick_name")
    return {r["id"]: _author_label(r) for r in rows}


async def _skill_to_dict(
    s: AgentSkill,
    user_enabled: Optional[bool] = None,
    author: Optional[str] = None,
    user_added: Optional[bool] = None,
    user_added_at: Optional[int] = None,
) -> dict:
    from app.services.agent_runtime.edit_tools import _parse_skill_md

    # 激活文件数（agent_skill_file is_active=True）
    file_count = await AgentSkillFile.filter(skill_key=s.skill_key, is_active=True).count()
    # skill 规范：SKILL.md 是唯一事实源，展示用的 name/description 优先取其 frontmatter
    fm = _parse_skill_md(s.skill_md or "")
    pref = None
    if user_enabled is None or user_added is None:
        # 单条场景未预取：现查当前用户的个人偏好
        # （无记录/未登录：默认启用；「已添加」仅本人创建的技能默认成立，见 _default_added）
        uid = CTX_USER_ID.get() or None
        pref = await AgentSkillUserPref.filter(user_id=uid, skill_key=s.skill_key).first() if uid else None
        if user_enabled is None:
            user_enabled = bool(pref.is_enabled) if pref else True
        if user_added is None:
            user_added = bool(pref.is_added) if pref else _default_added(s, uid)
    if author is None:
        # 单条场景未预取：惰查作者展示名；user_id=None 的无属主公共技能显示「官方」
        if s.user_id is None:
            author = "官方"
        else:
            u = await User.filter(id=s.user_id).only("id", "user_name", "nick_name").first()
            author = _author_label({"user_name": u.user_name, "nick_name": u.nick_name}) if u else "未命名"
    created_at = int(s.create_time.timestamp() * 1000) if s.create_time else None
    # 「加入我的技能」时间（ms 时间戳）：「我的技能」页按它倒序（新创建/新添加在前）。
    # 列表场景批量预取传入（pref.update_time）；单条场景回落现查的偏好行；
    # 缺省添加（本人创建、无偏好行）回落技能创建时间；未添加 → None
    added_at = user_added_at
    if added_at is None and user_added:
        if pref is not None and pref.is_added and pref.update_time:
            added_at = int(pref.update_time.timestamp() * 1000)
        else:
            added_at = created_at
    return {
        "id": s.id,
        "skillKey": s.skill_key,
        "name": fm.get("name") or s.name,
        "description": fm.get("description") or s.description,
        "skillMd": s.skill_md,
        "skillPkgKeys": s.skill_pkg_keys or [],
        "hasFiles": file_count > 0,
        "fileCount": file_count,
        "version": s.version,
        "sourceUrl": s.source_url,
        # 「官方」分类已废除：存量 official 行对外一律按 curated 展示
        "source": "curated" if s.source == "official" else s.source,
        "originSessionId": s.origin_session_id,
        "userId": s.user_id,
        "author": author,
        "isEnabled": bool(s.is_enabled),
        "userEnabled": user_enabled,
        "isAdded": user_added,
        "tags": getattr(s, "tags", None) or [],
        "icon": getattr(s, "icon", None) or None,
        "category": getattr(s, "category", None) or None,
        "isFeatured": bool(getattr(s, "is_featured", 0)),
        "minTierCode": getattr(s, "min_tier_code", None),
        "exampleQuestions": list(getattr(s, "example_questions", None) or []),
        "addedAt": added_at,
        "createdAt": created_at,
        "updatedAt": int(s.update_time.timestamp() * 1000) if s.update_time else None,
    }


def build_skill_injection_empty() -> str:
    return ""


def extract_pkg_keys_from_skill_md(skill_md: str) -> list[str]:
    """
    capability.skill_md 若以 YAML frontmatter 开头，并且包含 `skills: [k1, k2]`，返回这个列表。
    不支持时返回空列表。
    """
    if not skill_md:
        return []
    import re as _re
    m = _re.match(r"^---\s*\n(.*?)\n---\s*\n", skill_md, _re.S)
    if not m:
        return []
    body = m.group(1)
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("skills:"):
            val = line.split(":", 1)[1].strip()
            # 形如 [k1, k2]
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1]
                return [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
    return []


async def resolve_skills_from_text(text: str, user_id: Optional[int], tier_codes: Optional[frozenset] = None) -> list[AgentSkill]:
    """从一段文本里抓 @name，返回可调用的技能列表（去重，保留出现顺序）。

    商店模型下 @ 只能调用「（已上架或本人创建）且 档位足够 且 已添加 且 启用」的技能：
    - 已下架（全局 is_enabled=0）：从商店撤下，**仅作者本人**仍可 @ 自己的技能
    - 档位不符（用户档位集合与实体可见白名单无交集）：剔除（作者恒见自己实体；builtin 豁免）
    - 已禁用 / 未添加（个人偏好 is_enabled=0 或 is_added=0）：完全剔除，@ 不到
    - 无偏好记录 = 默认未添加（仅本人创建的技能默认已添加，见 _default_added）；
      builtin（如「编辑」）不进商店，豁免此口径
    与默认加载面（qa.py::_get_visible_skill_keys）的过滤口径保持一致。

    tier_codes：调用方已算好的用户档位集合（qa.py 链路）；None 时按当前登录用户兜底现算，
    上下文用户与 user_id 不一致时保守落空集（只能看全员可见，宁可少露不多露）。
    """
    if not text:
        return []
    if tier_codes is None:
        _ctx_uid, _codes, _sup = await get_current_role_codes()
        if _ctx_uid is not None and (user_id is None or _ctx_uid == user_id):
            tier_codes = await resolve_user_tier_codes(_codes, _is_manager(_sup, _codes))
        else:
            tier_codes = frozenset()
    names = list(dict.fromkeys(_AT_PATTERN.findall(text)))
    if not names:
        return []
    # 统一尺子：已上架或本人创建（不在 SQL 里只查 is_enabled=1，否则本人未上架技能 @ 不到）。
    # 行级 _listed_or_own 会挡住「他人未上架」；user_id 为空时退化为仅已上架。
    q = Q(is_enabled=1)
    if user_id is not None:
        q = q | Q(user_id=user_id)
    skills = await AgentSkill.filter(Q(skill_key__in=names) & q)
    # 个人偏好：显式禁用（is_enabled=0）或未添加（is_added=0）的一律剔除
    pref_map: dict[str, AgentSkillUserPref] = {}
    if user_id is not None:
        prefs = await AgentSkillUserPref.filter(user_id=user_id, skill_key__in=names).all()
        pref_map = {p.skill_key: p for p in prefs}
    # 按统一尺子（已上架或本人创建）+ 已添加 过滤
    visible: list[AgentSkill] = []
    by_key: dict[str, AgentSkill] = {}
    for sk in skills:
        p = pref_map.get(sk.skill_key)
        if p is not None:
            if not p.is_enabled or not p.is_added:
                continue
        elif sk.source != "builtin" and not _default_added(sk, user_id):
            # 无记录默认未添加：非本人技能 @ 不到（builtin 豁免）
            continue
        if not _listed_or_own(sk, user_id):
            continue
        # 档位过滤：不在可见白名单的 @ 不到（作者恒见自己实体在 tier_allows 内；builtin 豁免）
        if sk.source != "builtin" and not await tier_allows(sk.min_tier_code, sk.user_id, user_id, tier_codes):
            continue
        by_key[sk.skill_key] = sk
    # 按原顺序输出
    for n in names:
        if n in by_key:
            visible.append(by_key[n])
    return visible


async def filter_visible_skill_keys(keys, uid: Optional[int], tier_codes: frozenset) -> set[str]:
    """技能 key 批量可见性复核（专家绑定技能并入运行时用）：
    统一尺子（已上架或本人创建）+ 档位白名单判定，builtin 豁免档位。

    绑定关系本身不回溯校验（与「绑定未上架技能」同政策）——这里只在运行时
    对不在白名单的用户不生效，授权变化后自动恢复。
    """
    keys = [k for k in (keys or []) if k]
    if not keys:
        return set()
    rows = await AgentSkill.filter(skill_key__in=list(dict.fromkeys(keys)))
    out: set[str] = set()
    for s in rows:
        if s.source == "builtin":
            out.add(s.skill_key)
            continue
        if not _listed_or_own(s, uid):
            continue
        if not await tier_allows(s.min_tier_code, s.user_id, uid, tier_codes):
            continue
        out.add(s.skill_key)
    return out


def _mount_skill_pkgs(pkgs: list, workspace_dir) -> dict[str, str]:
    """
    把磁盘上的 skill 包目录挂到 workspace/.skills/<key>/（内置 skill 走这条路）。
    Linux/Mac 走 symlink；Windows 走 copytree。已存在则跳过。
    返回 {skill_key: 相对 workspace 的路径}。
    """
    import os
    import shutil
    from pathlib import Path as _P

    if workspace_dir is None:
        return {}
    ws = _P(workspace_dir)
    mount_root = ws / ".skills"
    mount_root.mkdir(parents=True, exist_ok=True)

    mounted: dict[str, str] = {}
    for p in pkgs:
        try:
            src = (_P(__file__).parent.parent.parent.parent.parent / p.pkg_path).resolve()
            if not src.exists() or not src.is_dir():
                continue
            dst = mount_root / p.skill_key
            # 目标存在：symlink 指向正确就复用，否则重建
            if dst.is_symlink():
                try:
                    if dst.resolve() == src:
                        mounted[p.skill_key] = f".skills/{p.skill_key}"
                        continue
                except OSError:
                    pass
                dst.unlink(missing_ok=True)
            elif dst.exists():
                shutil.rmtree(dst)
            # 尝试软链；失败则拷贝
            try:
                os.symlink(src, dst, target_is_directory=True)
            except (OSError, NotImplementedError):
                shutil.copytree(src, dst)
            mounted[p.skill_key] = f".skills/{p.skill_key}"
        except Exception:
            logger.exception(f"挂载 skill 包失败: {p.skill_key}")
    return mounted


async def _materialize_from_db(skill_keys: list[str], workspace_dir, target_dirname: str = ".agent_skills") -> dict[str, str]:
    """
    从 agent_skill_file 表物化文件到 workspace/<target_dirname>/<key>/（与内置 skill 同族）。
    target_dirname 两态：".agent_skills" 主技能面；".agent_expert_skills" 会话专家绑定技能面。
    已存在且文件数匹配则跳过。返回 {skill_key: 相对路径}。
    """
    from pathlib import Path as _P

    from app.models.standard.agent import AgentSkillFile

    if workspace_dir is None or not skill_keys:
        return {}
    ws = _P(workspace_dir)
    mount_root = ws / target_dirname
    mount_root.mkdir(parents=True, exist_ok=True)

    mounted: dict[str, str] = {}
    for key in skill_keys:
        files = await AgentSkillFile.filter(skill_key=key, is_active=True)
        if not files:
            continue
        dst = mount_root / key
        # 跳过条件：磁盘 (相对路径, 大小) 集合与 DB 激活集完全一致。
        # 旧实现只比文件数：升级后「同名不同内容」或「残留多余文件」都会被误判跳过
        want = {f"{f.path}|{f.size}" for f in files}
        if dst.exists() and dst.is_dir():
            have = set()
            for p in dst.rglob("*"):
                if p.is_file():
                    have.add(f"{p.relative_to(dst).as_posix()}|{p.stat().st_size}")
            if have == want:
                mounted[key] = f"{target_dirname}/{key}"
                continue
        # 物化：先清掉激活集之外的残留文件，再写入当前版本
        dst.mkdir(parents=True, exist_ok=True)
        active_paths = {f.path for f in files}
        for p in dst.rglob("*"):
            if p.is_file() and p.relative_to(dst).as_posix() not in active_paths:
                p.unlink(missing_ok=True)
        for f in files:
            target = dst / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f.content)
        mounted[key] = f"{target_dirname}/{key}"
    return mounted


def _load_builtin_skill_objs(keys: list[str]) -> list:
    """
    为未入库的内置 skill（磁盘 .agent_workspace/.agent_skills/<key>/）构造轻量对象，
    供 _mount_skill_pkgs 做 symlink/copy。返回带 .skill_key/.pkg_path/.skill_md 的对象列表。
    同时兼容 _project/<key>/ 下的 project-bound skill。
    """
    from types import SimpleNamespace
    from pathlib import Path as _P

    root = _P(__file__).parent.parent.parent.parent.parent
    skills_root = root / ".agent_workspace" / ".agent_skills"
    out: list = []
    for key in keys:
        candidates = [
            skills_root / key,
            skills_root / "_project" / key,
        ]
        for d in candidates:
            if d.is_dir():
                pkg_path = str(d.relative_to(root)).replace("\\", "/")
                skill_md = ""
                md_file = d / "SKILL.md"
                if md_file.exists():
                    try:
                        skill_md = md_file.read_text(encoding="utf-8")
                    except Exception:
                        skill_md = ""
                out.append(SimpleNamespace(skill_key=key, pkg_path=pkg_path, skill_md=skill_md))
                break
    return out


async def build_skill_injection(skills: list[AgentSkill], user_id: Optional[int], workspace_dir=None) -> str:
    """
    拼成一段指令注入 agent：
    - 技能自身的 skill_md（SKILL.md 全文）
    - 物化技能文件到工作区（DB → .skills/<key>/，内置 → symlink）
    - 在提示里指明工作区路径，方便 agent 直接 execute 脚本
    """
    if not skills:
        return ""

    # 收集所有要物化的 skill keys
    mount_keys: list[str] = []
    for sk in skills:
        if sk.skill_key and sk.skill_key not in mount_keys:
            mount_keys.append(sk.skill_key)

    # workspace_dir 优先用调用方传入的，回退到 context
    if workspace_dir is None:
        from app.services.agent_runtime.call_context import get_agent_call_context

        ctx = get_agent_call_context()
        workspace_dir = ctx.workspace_dir if ctx else None

    # 优先从 DB 物化（用户技能 / 已入库技能）
    db_mounted: dict[str, str] = {}
    if mount_keys and workspace_dir:
        db_mounted = await _materialize_from_db(mount_keys, workspace_dir)

    # 剩余未物化的 key 走磁盘 symlink（内置 skill：.agent_workspace/.agent_skills/<key>/）
    remaining_keys = [k for k in mount_keys if k not in db_mounted]
    disk_mounted: dict[str, str] = {}
    if remaining_keys:
        import asyncio as _asyncio

        builtin_objs = await _asyncio.to_thread(_load_builtin_skill_objs, remaining_keys)
        if builtin_objs and workspace_dir:
            disk_mounted = await _asyncio.to_thread(_mount_skill_pkgs, builtin_objs, workspace_dir)

    all_mounted = {**db_mounted, **disk_mounted}

    # 组装注入文本
    parts: list[str] = ["以下是本次需要应用的能力说明（请严格遵循）：", ""]
    for sk in skills:
        parts.append(f"【能力：{sk.name}】")
        parts.append(sk.skill_md)
        rel = all_mounted.get(sk.skill_key)
        if rel:
            parts.append("")
            parts.append(f"技能文件已挂载到工作区：`{rel}/`（脚本在 `{rel}/scripts/`，模板在 `{rel}/templates/`）")
            parts.append("可直接用 execute 执行脚本、read_file 读取文件。")
        parts.append("")

    return "\n".join(parts) + "\n"


# ── Schemas ──────────────────────────────────────────────────────────────────

class SkillCreateReq(BaseModel):
    skill_key: Optional[str] = Field(None, description="已废弃：key 由平台按「技能名_专属code」自动生成，传入将被忽略")
    name: str
    description: Optional[str] = None
    skill_md: str = Field(..., description="SKILL.md 正文（可带 YAML frontmatter，后端自动补齐）")
    icon: Optional[str] = Field(None, description="图标：单个 <svg> 元素源码或图片 data URI；不传则前端显示兜底图标")
    category: Optional[str] = Field(None, description="分类：SKILL_CATEGORIES 词表内取值；不传按「其他」展示")
    example_questions: Optional[list[str]] = Field(None, description="快捷提问（字符串数组）：卡片展示，用户点击即添加该技能并把问题填入输入框")


class SkillUpdateReq(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    skill_md: Optional[str] = None
    is_enabled: Optional[bool] = None
    icon: Optional[str] = Field(None, description="图标：单个 <svg> 元素源码或图片 data URI；传空串清除；不传不改")
    category: Optional[str] = Field(None, description="分类：SKILL_CATEGORIES 词表内取值；传空串清除；不传不改")
    is_featured: Optional[bool] = Field(None, description="精选技能（商店顶部精选区）；仅管理员可设置")
    min_tier_code: Optional[Union[list[str], str]] = Field(
        None, description="可见档位白名单（档位 code 数组，如 ['paid','gov']）；传 []/'all'=全员可见；不传=不改；显式多选无包含关系；仅管理员可设置"
    )
    example_questions: Optional[list[str]] = Field(None, description="快捷提问（字符串数组）；传空数组清除；不传不改")


class SkillFromSessionReq(BaseModel):
    session_key: str
    suggested_key: Optional[str] = Field(None, description="可选，覆盖 LLM 建议的 key")


class SkillPrefsReq(BaseModel):
    skill_keys: list[str] = Field(..., description="技能 key 列表（单个开关也走这里，长度 1）")
    is_enabled: Optional[bool] = Field(None, description="个人启用/禁用（禁用=完全不加载、@ 不可调用）；不传则不改")
    is_added: Optional[bool] = Field(None, description="是否已添加到我的技能（False=移除，退回商店未添加态）；不传则不改")


class SkillManageBatchReq(BaseModel):
    skill_keys: list[str] = Field(..., description="技能 key 列表")
    is_enabled: Optional[bool] = Field(None, description="批量上架/下架（全局商店可见性）；传哪个改哪个")
    is_featured: Optional[bool] = Field(None, description="批量精选/取消精选；传哪个改哪个")


class SkillKeysReq(BaseModel):
    skill_keys: list[str] = Field(..., description="技能 key 列表")


# ── CRUD ─────────────────────────────────────────────────────────────────────

async def _skill_pref_maps(uid: Optional[int], skill_keys: list[str]) -> tuple[dict[str, bool], dict[str, bool], dict[str, int]]:
    """个人偏好三 map 预取（启用/已添加/添加时间），限定本页 skill_key，避免逐条 N+1。

    无记录缺省：默认启用；「已添加」仅本人创建的技能默认成立（_default_added）。
    """
    enabled_map: dict[str, bool] = {}
    added_map: dict[str, bool] = {}
    added_at_map: dict[str, int] = {}
    if uid and skill_keys:
        prefs = await AgentSkillUserPref.filter(user_id=uid, skill_key__in=skill_keys).all()
        enabled_map = {p.skill_key: bool(p.is_enabled) for p in prefs}
        added_map = {p.skill_key: bool(p.is_added) for p in prefs}
        # 「加入我的技能」时间=偏好行最后更新时间：前端「我的技能」按它倒序（新添加在前）
        added_at_map = {p.skill_key: int(p.update_time.timestamp() * 1000) for p in prefs if p.update_time}
    return enabled_map, added_map, added_at_map


async def _skills_to_records(rows: list, uid: Optional[int]) -> list[dict]:
    """把本页 AgentSkill 行批量序列化为出参（偏好三 map + 作者名一次性预取）。"""
    enabled_map, added_map, added_at_map = await _skill_pref_maps(uid, [s.skill_key for s in rows])
    # 作者展示名批量预取；user_id=None 的官方/公共技能走惰查兜底显示「官方」
    author_map = await _author_label_map([s.user_id for s in rows if s.user_id is not None])
    return [
        await _skill_to_dict(
            s,
            enabled_map.get(s.skill_key, True),
            author_map.get(s.user_id),
            added_map.get(s.skill_key, _default_added(s, uid)),
            added_at_map.get(s.skill_key),
        )
        for s in rows
    ]


@router.get("", summary="技能列表")
async def list_skills(
    include_disabled: bool = False,
    current: Optional[int] = None,
    size: Optional[int] = None,
    keyword: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    user_id: Optional[int] = None,
):
    """商店模型：
    - 默认为「已上架（is_enabled=1）或本人创建」的技能（统一尺子，与 @ 调用口径一致）：
      本人未上架的技能（上传/自建）对作者自己可见可用，对他人不可见
    - include_disabled=true 附带其余已下架行，但下架行仅对管理者（超管/管理员）
      或可管理人（作者本人）返回——商店管理视图用
    - builtin 不展示给前端（如"编辑"），但 @ 匹配时仍生效

    上架管理分页：管理员 + include_disabled + 同时传 current/size 时走 DB 分页，返回
    SuccessExtra({records}, total, current, size)；支持 keyword（名称/描述/key 模糊）、
    category（词表名或「其他」）、status（all/enabled/disabled/featured）、user_id（0=官方）。
    其余情形逐字保留全量逻辑（兼容既有调用方）。
    """
    uid, role_codes, is_super = await get_current_role_codes()
    is_mgr = _is_manager(is_super, role_codes)

    # ── 分页分支（上架管理页，管理员专属） ──
    if manage_paging_enabled(is_mgr, include_disabled, current, size):
        cur, siz = paging_bounds(current, size)
        qs = AgentSkill.all().exclude(source="builtin")
        kw = (keyword or "").strip()
        if kw:
            # 展示名来自 _parse_skill_md frontmatter 覆盖，可能与 DB name 列略有出入——管理页搜索以 DB 列为准
            qs = qs.filter(Q(name__icontains=kw) | Q(description__icontains=kw) | Q(skill_key__icontains=kw))
        cat = (category or "").strip()
        if cat:
            vocab = await _skill_categories()
            if cat == "其他":
                qs = qs.filter(await q_category_other())
            elif cat in vocab:
                qs = qs.filter(category=cat)
            else:
                qs = qs.filter(id=-1)  # 词表外取值无对应行，恒空集
        st = (status or "all").strip() or "all"
        if st == "enabled":
            qs = qs.filter(is_enabled=1)
        elif st == "disabled":
            qs = qs.filter(is_enabled=0)
        elif st == "featured":
            # 精选不附加在架约束：管理员可见带精选标的全部行
            qs = qs.filter(is_featured=1)
        qa = q_author(user_id)
        if qa is not None:
            qs = qs.filter(qa)
        total = await qs.count()
        # 确定性排序（跨页不重不漏）：在架优先（对齐管理页前端排序语义），id 收尾
        rows = await qs.order_by("-is_enabled", "id").offset((cur - 1) * siz).limit(siz)
        records = await _skills_to_records(rows, uid)
        return SuccessExtra(data={"records": records}, total=total, current=cur, size=siz)

    # ── 全量分支（原有逻辑，保持不变） ──
    # 用户档位集合（显式白名单口径）：管理员哨兵恒全可见
    tier_codes = await resolve_user_tier_codes(role_codes, is_mgr)
    qs = AgentSkill.all()
    if not include_disabled:
        # 统一尺子：已上架或本人创建（只查 is_enabled=1 会让本人未上架技能从 @ 候选消失）
        q = Q(is_enabled=1)
        if uid is not None:
            q = q | Q(user_id=uid)
        qs = qs.filter(q)
    rows = await qs.order_by("id")
    visible = []
    for s in rows:
        if s.source == "builtin":
            continue
        # 管理者（超管/管理员）可见全部行——上架管理页「管理员看全部」的前提；
        # 普通用户只保留「已上架或本人创建」（统一尺子，无 visibility 机制）
        if not is_mgr and not _listed_or_own(s, uid):
            continue
        # 档位过滤：不在可见白名单的用户看不到（作者恒见自己实体，在 tier_allows 内）
        if not await tier_allows(s.min_tier_code, s.user_id, uid, tier_codes):
            continue
        # 已下架行只对管理者/作者本人可见（普通用户视角等同 include_disabled=false）
        if not s.is_enabled and not (is_mgr or _can_manage(s, uid, is_mgr)):
            continue
        visible.append(s)
    records = await _skills_to_records(visible, uid)
    return Success(data=records)


@router.post("", summary="新建技能")
async def create_skill(req: SkillCreateReq):
    from app.services.agent_runtime.edit_tools import _build_skill_md, sync_skill_md_file

    from app.services.agent_runtime.edit_tools import _alloc_skill_key, _build_user_skill_key, _update_frontmatter

    uid = CTX_USER_ID.get() or None
    if uid is None:
        return Fail(msg="未登录，无法创建技能")
    # key 规范：固定「技能名_专属code」，忽略请求中的 skill_key，冲突自动加后缀
    final_key = await _alloc_skill_key(await _build_user_skill_key(uid, req.name))
    # skill 规范：skill_md 即 SKILL.md 主文件全文（缺 frontmatter 时自动补齐；
    # 已有 frontmatter 时用本次提交的 name/description 覆盖，保证字段与文件一致）
    skill_md = _build_skill_md(req.name, req.description, None, req.skill_md)
    skill_md = _update_frontmatter(skill_md, req.name, req.description)
    s = await AgentSkill.create(
        skill_key=final_key,
        name=req.name,
        description=req.description,
        skill_md=skill_md,
        skill_pkg_keys=extract_pkg_keys_from_skill_md(skill_md) or None,
        source="curated",
        user_id=uid,
        is_enabled=0,  # 默认未上架：仅创建者可见，管理员上架后全员可见
        icon=(req.icon or "").strip() or None,
        category=await _norm_category(req.category),
        example_questions=_norm_example_questions(req.example_questions) or None,
    )
    await sync_skill_md_file(s)
    # 新建默认未上架，仅本人可见 → 只需失效本人同步节流
    _notify_skill_changed(uid)
    return Success(data=await _skill_to_dict(s))


@router.put("/prefs", summary="批量设置技能个人偏好（添加/移除、启用/禁用，只影响当前用户）")
async def batch_skill_prefs(req: SkillPrefsReq):
    """商店模型的个人偏好开关（两个正交状态，传哪个改哪个）：

    - is_added：是否已添加到「我的技能」（False=移除，退回商店未添加态）
    - is_enabled：启用/禁用。**禁用=完全剔除**——不加载进 workspace、@ 不可调用、
      @ 候选弹层不出现（resolve_skills_from_text / _get_visible_skill_keys 同口径过滤）
    - 全局 is_enabled（商店上架/下架）不动
    - 路径注意：将来勿加单段 PATCH/GET 变体，会撞 PATCH /{skill_id}
    """
    uid, role_codes, is_super = await get_current_role_codes()
    if uid is None:
        return Fail(msg="未登录，无法设置技能偏好")
    if req.is_enabled is None and req.is_added is None:
        return Fail(msg="is_enabled 与 is_added 至少传一个")
    keys = list(dict.fromkeys(req.skill_keys))
    if not keys:
        return Fail(msg="skill_keys 不能为空")
    if len(keys) > 200:
        return Fail(msg="单次最多设置 200 个技能")
    tier_codes = await resolve_user_tier_codes(role_codes, _is_manager(is_super, role_codes))
    # 只处理存在且对当前用户「已上架或本人创建 且 在可见白名单内」的技能；其余静默过滤
    # （堵 API 直调/气泡点击给不可见的实体写偏好）
    rows = await AgentSkill.filter(skill_key__in=keys)
    valid_keys = [
        s.skill_key
        for s in rows
        if _listed_or_own(s, uid) and await tier_allows(s.min_tier_code, s.user_id, uid, tier_codes)
    ]
    defaults: dict = {}
    if req.is_enabled is not None:
        defaults["is_enabled"] = 1 if req.is_enabled else 0
    if req.is_added is not None:
        defaults["is_added"] = 1 if req.is_added else 0
    for k in valid_keys:
        await AgentSkillUserPref.update_or_create(
            defaults=defaults, user_id=uid, skill_key=k
        )
    # 失效该用户的 workspace 同步节流：下一条消息即按新偏好重建 .agent_skills/ 目录
    #（个人偏好只影响本人，不 bump 全局代数）
    if valid_keys:
        _notify_skill_changed(uid)
    return Success(data={"updated": valid_keys})


# ── 上架管理批量操作（管理员） ───────────────────────────────────────────────
# 路径纪律同 /prefs：单段字面路由（manage/manage-delete）用 PUT/POST，勿改 DELETE/PATCH
# 单段变体——会被先注册的 /{skill_id} 参数路由抢先匹配，"manage" 转 int 直接 422。


@router.put("/manage", summary="批量管理技能（上架/下架/精选，仅管理员）")
async def batch_manage_skills(req: SkillManageBatchReq):
    """上架管理页的批量操作（单条语义同 PATCH /{skill_id}，权限同：仅管理员）。

    is_enabled / is_featured 传哪个改哪个；builtin 行跳过（计入 skipped）。
    """
    uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅管理员可批量管理技能")
    if req.is_enabled is None and req.is_featured is None:
        return Fail(msg="is_enabled 与 is_featured 至少传一个")
    keys = list(dict.fromkeys(req.skill_keys))
    if not keys:
        return Fail(msg="skill_keys 不能为空")
    if len(keys) > 200:
        return Fail(msg="单次最多管理 200 个技能")
    rows = {s.skill_key: s for s in await AgentSkill.filter(skill_key__in=keys)}
    updated: list[str] = []
    skipped: list[str] = []
    # 上下架联动 key 规范（上架=纯名称、未上架=技能名_专属code），save 后逐条重算
    from app.services.agent_runtime.edit_tools import apply_key_convention

    for k in keys:
        s = rows.get(k)
        if s is None or s.source == "builtin":
            skipped.append(k)
            continue
        if req.is_enabled is not None:
            s.is_enabled = 1 if req.is_enabled else 0
        if req.is_featured is not None:
            s.is_featured = 1 if req.is_featured else 0
        await s.save()
        if req.is_enabled is not None:
            new_key = await apply_key_convention(s)
            if new_key:
                k = new_key  # key 已联动变化，回报新 key
        updated.append(k)
    # 上/下架改变全员可见性 → bump 全局代数，其他用户下一条消息后台重同步；
    # 执行者本人强制失效，下一条消息 await 即见
    if updated:
        _notify_skill_changed(uid, affects_others=req.is_enabled is not None)
    return Success(data={"updated": updated, "skipped": skipped})


@router.post("/manage-delete", summary="批量删除技能（管理员可删全部；作者可删自己的）")
async def batch_delete_skills(req: SkillKeysReq):
    """上架管理页批量删除：主记录 + 全部版本文件 + 所有用户的个人偏好行一并删除。

    builtin 不可删、非管理员只能删本人创建的，其余进 skipped。
    """
    from tortoise.transactions import in_transaction

    uid, role_codes, is_super = await get_current_role_codes()
    is_mgr = _is_manager(is_super, role_codes)
    keys = list(dict.fromkeys(req.skill_keys))
    if not keys:
        return Fail(msg="skill_keys 不能为空")
    if len(keys) > 200:
        return Fail(msg="单次最多删除 200 个技能")
    rows = {s.skill_key: s for s in await AgentSkill.filter(skill_key__in=keys)}
    updated: list[str] = []
    skipped: list[str] = []
    for k in keys:
        s = rows.get(k)
        if s is None or s.source == "builtin" or not _can_manage(s, uid, is_mgr):
            skipped.append(k)
            continue
        try:
            # 事务内级联删除，避免「删了文件丢主记录」的中间态孤儿
            async with in_transaction("conn_standard"):
                await AgentSkillFile.filter(skill_key=k).delete()
                await AgentSkillUserPref.filter(skill_key=k).delete()
                await s.delete()
            updated.append(k)
        except Exception:  # noqa: BLE001
            logger.exception(f"批量删除技能失败 {k}")
            skipped.append(k)
    # 批量删除可能移除已上架技能 → 一律 bump 全局代数（他人下一条消息后台重同步）
    if updated:
        _notify_skill_changed(uid, affects_others=True)
    return Success(data={"updated": updated, "skipped": skipped})


@router.get("/manage-authors", summary="上架管理：技能作者清单（仅管理员）")
async def manage_authors():
    """作者筛选下拉源：按作者分组计数 [{userId, author, count}]（userId=None 为「官方」桶）。"""
    _uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅超管/管理员可见")
    qs = AgentSkill.all().exclude(source="builtin")
    return Success(data=await _author_facets(qs))


# ── 商店分类词表（运行时真相源 = agent_skill_category 表） ─────────────────
# 读：所有登录用户（商店导航）；写：仅超管/管理员（4032 守卫）。
# system-admin 子 agent 也可经其通用表工具直接读写该表。


class CategoryReq(BaseModel):
    name: str = Field(..., description="分类名（1~32 字，重名拒绝）")
    sort_order: Optional[int] = Field(None, description="排序值（小在前）；新建不传默认追加到最后")
    icon: Optional[str] = Field(None, description="分类图标：单个 <svg> 源码或图片 data URI；空串=清除")


@router.get("/categories", summary="分类词表（商店导航）")
async def list_categories():
    """按排序返回 [{id, name, sortOrder, icon}]；商店导航取 name，橱窗/管理页用全量。"""
    from app.models.standard.agent import AgentSkillCategory

    rows = await AgentSkillCategory.all().order_by("sort_order", "id")
    return Success(data=[{"id": r.id, "name": r.name, "sortOrder": r.sort_order, "icon": r.icon} for r in rows])


@router.post("/categories", summary="新增分类（仅管理员）")
async def add_category(req: CategoryReq):
    from app.models.standard.agent import AgentSkillCategory

    _uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅超管/管理员可管理技能分类")
    name = (req.name or "").strip()
    if not name or len(name) > 32:
        return Fail(msg="分类名需为 1~32 字")
    if await AgentSkillCategory.filter(name=name).exists():
        return Fail(msg=f"分类已存在：{name}")
    if req.sort_order is None:
        last = await AgentSkillCategory.all().order_by("-sort_order", "-id").first()
        req.sort_order = (last.sort_order + 1) if last else 0
    row = await AgentSkillCategory.create(name=name, sort_order=req.sort_order, icon=(req.icon or "").strip() or None)
    return Success(data={"id": row.id, "name": row.name, "sortOrder": row.sort_order, "icon": row.icon})


@router.patch("/categories/{cat_id}", summary="重命名/排序分类（仅管理员）")
async def update_category(cat_id: int, req: CategoryReq):
    from app.models.standard.agent import AgentSkillCategory

    _uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅超管/管理员可管理技能分类")
    row = await AgentSkillCategory.get_or_none(id=cat_id)
    if row is None:
        return Fail(msg="分类不存在")
    name = (req.name or "").strip()
    if not name or len(name) > 32:
        return Fail(msg="分类名需为 1~32 字")
    if name != row.name:
        if await AgentSkillCategory.filter(name=name).exclude(id=cat_id).exists():
            return Fail(msg=f"分类已存在：{name}")
        # 重命名级联：引用旧分类的技能跟随改名，不产生孤儿取值
        await AgentSkill.filter(category=row.name).update(category=name)
        row.name = name
    if req.sort_order is not None:
        row.sort_order = req.sort_order
    # icon 传 None=不改；传空串=清除；其余=原样写入（svg 源码 / data URI）
    if req.icon is not None:
        row.icon = req.icon.strip() or None
    await row.save()
    return Success(data={"id": row.id, "name": row.name, "sortOrder": row.sort_order, "icon": row.icon})


@router.delete("/categories/{cat_id}", summary="删除分类（仅管理员；该分类下技能回落「其他」）")
async def delete_category(cat_id: int):
    from app.models.standard.agent import AgentSkillCategory

    _uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅超管/管理员可管理技能分类")
    row = await AgentSkillCategory.get_or_none(id=cat_id)
    if row is None:
        return Fail(msg="分类不存在")
    # 引用该分类的技能置空 → 前端落「其他」兜底桶
    await AgentSkill.filter(category=row.name).update(category=None)
    await row.delete()
    return Success(data=None, msg="已删除")


# ── 技能案例（技能整合后案例按 skill_key 挂载；仅管理员维护） ──────────────


class SkillExampleFromSessionReq(BaseModel):
    session_key: str = Field(..., alias="sessionKey")
    title: Optional[str] = None
    description: Optional[str] = None
    preview_images: Optional[list] = Field(None, alias="previewImages")
    sort_order: int = Field(0, alias="sortOrder")

    class Config:
        populate_by_name = True


class SkillExampleUpdateReq(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    preview_images: Optional[list] = Field(None, alias="previewImages")
    sort_order: Optional[int] = Field(None, alias="sortOrder")
    is_enabled: Optional[int] = Field(None, alias="isEnabled")

    class Config:
        populate_by_name = True


@router.get("/{skill_id}/examples", summary="技能的案例列表（管理员）")
async def list_skill_examples(skill_id: int):
    from app.api.v1.ai.quick_action import _example_to_dict
    from app.models.standard.agent import AgentQuickActionExample

    _uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅超管/管理员可管理技能案例")
    s = await AgentSkill.get_or_none(id=skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    rows = await AgentQuickActionExample.filter(skill_key=s.skill_key).order_by("sort_order", "id")
    return Success(data=[_example_to_dict(ex) for ex in rows])


@router.post("/{skill_id}/examples/from-session", summary="从会话提取案例（管理员）")
async def create_skill_example_from_session(skill_id: int, payload: SkillExampleFromSessionReq):
    """把一段现成会话提取为技能案例（对话全文 + artifacts 序列化，fork 时可重建）。"""
    from app.api.v1.ai.quick_action import _example_to_dict
    from app.models.standard.agent import (
        AgentArtifact,
        AgentMessage,
        AgentQuickActionExample,
        AgentSession,
    )

    _uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅超管/管理员可管理技能案例")
    s = await AgentSkill.get_or_none(id=skill_id)
    if s is None:
        return Fail(msg="技能不存在")

    session = await AgentSession.get_or_none(session_key=payload.session_key)
    if not session:
        return Fail(code="4004", msg="会话不存在")
    messages = await AgentMessage.filter(session_id=session.id).order_by("id")
    if not messages:
        return Fail(msg="会话没有消息")

    # 批量取 artifacts 序列化进案例数据（fork 时重建用）
    msg_ids = [m.id for m in messages]
    artifacts_by_msg: dict[int, list] = {}
    if msg_ids:
        for a in await AgentArtifact.filter(message_id__in=msg_ids).order_by("id"):
            artifacts_by_msg.setdefault(a.message_id, []).append(a)

    conversation_messages = []
    message_ids = []
    for msg in messages:
        msg_data: dict = {"role": msg.role, "content": msg.content or ""}
        if msg.thinking:
            msg_data["thinking"] = msg.thinking
        if msg.attachments_json:
            msg_data["attachments"] = msg.attachments_json
        if msg.tool_steps_json:
            msg_data["toolSteps"] = msg.tool_steps_json
        if msg.process_json:
            msg_data["processSteps"] = msg.process_json
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

    uid = CTX_USER_ID.get() or None
    preview_images = payload.preview_images or None
    example = await AgentQuickActionExample.create(
        action_id=0,  # 技能整合后归属看 skill_key；action_id 仅兼容旧表非空约束
        skill_key=s.skill_key,
        title=payload.title or session.title,
        description=payload.description or f"从会话「{session.title}」提取",
        conversation_data={"messages": conversation_messages, "source_user_id": session.user_id},
        source_session_id=session.id,
        source_message_ids=message_ids,
        sort_order=payload.sort_order or 0,
        preview_image=preview_images[0] if preview_images else None,
        preview_images=preview_images,
        created_by=uid,
    )
    return Success(data=_example_to_dict(example))


@router.put("/examples/{example_id}", summary="更新案例（管理员）")
async def update_skill_example(example_id: int, payload: SkillExampleUpdateReq):
    from app.api.v1.ai.quick_action import _example_to_dict
    from app.models.standard.agent import AgentQuickActionExample

    _uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅超管/管理员可管理技能案例")
    example = await AgentQuickActionExample.get_or_none(id=example_id)
    if not example:
        return Fail(code="4004", msg="案例不存在")
    update_data = payload.model_dump(exclude_unset=True, by_alias=False)
    # preview_images ↔ preview_image 联动（旧字段兼容）
    if update_data.get("preview_images"):
        update_data["preview_image"] = update_data["preview_images"][0]
    if update_data:
        await example.update_from_dict(update_data).save()
    return Success(data=_example_to_dict(example))


@router.delete("/examples/{example_id}", summary="删除案例（管理员）")
async def delete_skill_example(example_id: int):
    from app.models.standard.agent import AgentQuickActionExample

    _uid, role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, role_codes):
        return Fail(code="4032", msg="forbidden: 仅超管/管理员可管理技能案例")
    example = await AgentQuickActionExample.get_or_none(id=example_id)
    if not example:
        return Fail(code="4004", msg="案例不存在")
    await example.delete()
    return Success(msg="已删除")


@router.patch("/{skill_id}", summary="更新技能")
async def update_skill(skill_id: int, req: SkillUpdateReq):
    s = await AgentSkill.get_or_none(id=skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    if not _can_manage(s, uid, _is_manager(is_super, _role_codes)):
        return Fail(msg="无权修改：仅创建者或管理员可改")
    # 内置能力：只允许切换启停与设置快捷提问，其余字段不可改
    if s.source == "builtin":
        non_enable_fields = {
            k: v for k, v in req.model_dump(exclude_unset=True).items() if k not in ("is_enabled", "example_questions")
        }
        if non_enable_fields:
            return Fail(msg="内置能力不可编辑，仅允许启用/停用与设置快捷提问")
        changed = False
        if req.is_enabled is not None:
            s.is_enabled = 1 if req.is_enabled else 0
            changed = True
        if req.example_questions is not None:
            s.example_questions = _norm_example_questions(req.example_questions) or None
            changed = True
        if changed:
            await s.save()
            # 内置技能启停影响全员可见性
            _notify_skill_changed(uid, affects_others=req.is_enabled is not None)
        return Success(data=await _skill_to_dict(s))
    from app.services.agent_runtime.edit_tools import _parse_skill_md, _update_frontmatter

    if req.name is not None:
        s.name = req.name[:64]
    if req.description is not None:
        s.description = req.description[:1000]
    if req.skill_md is not None:
        s.skill_md = req.skill_md
        s.skill_pkg_keys = extract_pkg_keys_from_skill_md(req.skill_md) or None
        # SKILL.md 为事实源：frontmatter 里的 name/description 回写字段，避免漂移
        fm = _parse_skill_md(req.skill_md)
        if fm.get("name"):
            s.name = fm["name"][:64]
        if fm.get("description"):
            s.description = fm["description"][:1000]
    elif req.name is not None or req.description is not None:
        # 仅改字段时同步更新 skill_md 的 frontmatter（展示以 SKILL.md 为准）
        s.skill_md = _update_frontmatter(
            s.skill_md or "",
            s.name if req.name is not None else None,
            s.description if req.description is not None else None,
        )
    if req.is_enabled is not None:
        # 上下架（商店可见性）仅管理员可改，普通作者只能编辑内容字段
        if bool(s.is_enabled) != req.is_enabled and not _is_manager(is_super, _role_codes):
            return Fail(code="4032", msg="forbidden: 仅管理员可上下架技能")
        s.is_enabled = 1 if req.is_enabled else 0
    # 图标：传什么存什么；传空串=清除（回落前端兜底图标）；不传不改
    if req.icon is not None:
        s.icon = req.icon.strip() or None
    # 分类：只认 DB 词表内取值；传空串=清除；不传不改
    if req.category is not None:
        s.category = await _norm_category(req.category)
    # 精选：仅管理员可设置（业务拒绝用 4032，不复用鉴权码）
    if req.is_featured is not None:
        if not _is_manager(is_super, _role_codes):
            return Fail(code="4032", msg="forbidden: 仅管理员可设置精选技能")
        s.is_featured = 1 if req.is_featured else 0
    # 可见档位白名单：仅管理员可设置；[]/'all' 归一为 NULL（全员可见）；非法档位 code → 4000；不传不改
    if req.min_tier_code is not None:
        if not _is_manager(is_super, _role_codes):
            return Fail(code="4032", msg="forbidden: 仅管理员可设置可见档位")
        norm, terr = await norm_tier_codes(req.min_tier_code)
        if terr:
            return Fail(msg=terr)
        s.min_tier_code = norm
    # 快捷提问：传空数组=清除；不传不改
    if req.example_questions is not None:
        s.example_questions = _norm_example_questions(req.example_questions) or None
    await s.save()
    # skill 规范：name / description / skill_md 任一变化都同步到 SKILL.md 主文件
    if req.name is not None or req.description is not None or req.skill_md is not None:
        from app.services.agent_runtime.edit_tools import sync_skill_md_file

        await sync_skill_md_file(s)
    # 名称有变或上下架 → 实时重算 key（上架=纯名称，未上架=技能名_专属code，
    # 级联文件表/偏好/快捷功能等），无需人工维护
    if req.name is not None or req.skill_md is not None or req.is_enabled is not None:
        from app.services.agent_runtime.edit_tools import apply_key_convention

        await apply_key_convention(s)
    # 已上架技能的内容/可见性变化影响其他用户 → bump 全局代数（下一条消息后台重同步）
    _notify_skill_changed(uid, affects_others=bool(s.is_enabled) or req.is_enabled is not None)
    return Success(data=await _skill_to_dict(s))


@router.delete("/{skill_id}", summary="删除技能")
async def delete_skill(skill_id: int):
    s = await AgentSkill.get_or_none(id=skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    if s.source == "builtin":
        return Fail(msg="内置技能不可删除，请改为停用")
    uid, _role_codes, is_super = await get_current_role_codes()
    if not _can_manage(s, uid, _is_manager(is_super, _role_codes)):
        return Fail(msg="无权删除：仅创建者或管理员可删")
    # 与 skill_delete 工具对齐：主记录 + 全部版本文件一起删，不留孤儿行
    was_listed = bool(s.is_enabled)  # 删除前记录上架态（已上架 → 影响其他用户）
    await AgentSkillFile.filter(skill_key=s.skill_key).delete()
    await s.delete()
    _notify_skill_changed(uid, affects_others=was_listed)
    return Success(data=None, msg="已删除")


class TagsReq(BaseModel):
    tags: list[str] = Field(default_factory=list, description="用户自定义标签，去重后保存")


@router.patch("/{skill_id}/tags", summary="修改技能标签")
async def update_skill_tags(skill_id: int, req: TagsReq):
    s = await AgentSkill.get_or_none(id=skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    if not _can_manage(s, uid, _is_manager(is_super, _role_codes)):
        return Fail(msg="无权修改：仅创建者或管理员可改")
    # 去重 + 去空 + 单 tag 长度上限
    cleaned = []
    seen = set()
    for t in req.tags:
        t = (t or "").strip()
        if t and t not in seen:
            cleaned.append(t[:32])
            seen.add(t)
    s.tags = cleaned or None
    await s.save(update_fields=["tags", "update_time"])
    return Success(data=await _skill_to_dict(s))


# ── 版本 / 文件 / 安装 / 上传 / 下载 / 发现 ─────────────────────────────────


async def _get_skill_by_id(skill_id: int) -> Optional[AgentSkill]:
    return await AgentSkill.get_or_none(id=skill_id)


@router.get("/{skill_id}/versions", summary="列出技能的所有版本（创建者/管理员）")
async def list_skill_versions(skill_id: int):
    s = await _get_skill_by_id(skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    if not _can_manage(s, uid, _is_manager(is_super, _role_codes)):
        return Fail(code="4032", msg="forbidden: 仅创建者或管理员可查看技能版本")
    rows = await AgentSkillFile.filter(skill_key=s.skill_key)
    # 按 version 聚合：fileCount / size / isActive
    agg: dict[str, dict] = {}
    for f in rows:
        v = f.version or "1.0.0"
        item = agg.setdefault(v, {"version": v, "fileCount": 0, "size": 0})
        item["fileCount"] += 1
        item["size"] += f.size or 0
    data = []
    for v, item in sorted(agg.items()):
        data.append({
            "version": v,
            "fileCount": item["fileCount"],
            "size": item["size"],
            "isActive": v == (s.version or "1.0.0"),
        })
    return Success(data=data)


@router.post("/{skill_id}/versions/{version}/activate", summary="切换激活版本（创建者/管理员）")
async def activate_skill_version(skill_id: int, version: str):
    s = await _get_skill_by_id(skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    if not _can_manage(s, uid, _is_manager(is_super, _role_codes)):
        return Fail(code="4032", msg="forbidden: 仅创建者或管理员可切换技能版本")
    exists = await AgentSkillFile.exists(skill_key=s.skill_key, version=version)
    if not exists:
        return Fail(msg=f"版本不存在：{version}")
    # 先把该 skill 的所有文件置为非激活，再激活目标版本
    await AgentSkillFile.filter(skill_key=s.skill_key).update(is_active=False)
    await AgentSkillFile.filter(skill_key=s.skill_key, version=version).update(is_active=True)
    s.version = version
    await s.save(update_fields=["version", "update_time"])
    # 激活文件集变了：已上架 → 影响其他用户（bump 全局代数）
    _notify_skill_changed(uid, affects_others=bool(s.is_enabled))
    return Success(data=await _skill_to_dict(s))


@router.delete("/{skill_id}/versions/{version}", summary="删除某历史版本（创建者/管理员）")
async def delete_skill_version(skill_id: int, version: str):
    s = await _get_skill_by_id(skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    if not _can_manage(s, uid, _is_manager(is_super, _role_codes)):
        return Fail(code="4032", msg="forbidden: 仅创建者或管理员可删除技能版本")
    if version == (s.version or "1.0.0"):
        return Fail(msg="当前激活版本不可删除，请先切换到其他版本")
    deleted = await AgentSkillFile.filter(skill_key=s.skill_key, version=version).delete()
    if not deleted:
        return Fail(msg=f"版本不存在：{version}")
    return Success(data=None, msg="已删除")


class InstallReq(BaseModel):
    source_url: str
    suggested_key: Optional[str] = None


@router.post("/install", summary="从 URL 安装技能")
async def install_skill(req: InstallReq):
    import httpx

    from app.services.agent_runtime.edit_tools import _install_from_md, _install_from_zip

    uid = CTX_USER_ID.get() or None
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(req.source_url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            body = resp.content
    except Exception as e:
        logger.exception("下载技能源失败")
        return Fail(msg=f"下载失败：{type(e).__name__}: {e}")

    is_zip = req.source_url.lower().endswith(".zip") or "zip" in content_type
    installed_key: Optional[str] = None
    try:
        if is_zip:
            msg, change = await _install_from_zip(body, req.source_url, req.suggested_key, uid)
            installed_key = change.get("skillKey") if change.get("action") != "failed" else None
        else:
            text = body.decode("utf-8", errors="ignore")
            msg = await _install_from_md(text, req.source_url, req.suggested_key, uid)
    except Exception as e:
        logger.exception("安装技能失败")
        return Fail(msg=f"安装失败：{e}")

    # 反查刚创建的技能：zip 优先按本次返回的 key 精确反查（按 source_url 反查无 user_id
    # 过滤，并发下可能误拿他人同 URL 安装的行）；md 分支无 key，回落 source_url + 本人
    s = None
    if installed_key:
        s = await AgentSkill.get_or_none(skill_key=installed_key)
    if s is None:
        qs = AgentSkill.filter(source_url=req.source_url)
        if uid is not None:
            qs = qs.filter(user_id=uid)
        s = await qs.order_by("-id").first()
    if s is None:
        _notify_skill_changed(uid)  # 新装默认未上架，仅本人可见
        return Success(data={"message": msg})
    _notify_skill_changed(uid, affects_others=bool(s.is_enabled))
    return Success(data={"skill": await _skill_to_dict(s), "message": msg})


@router.post("/upload", summary="上传 zip 安装技能")
async def upload_skill(
    file: UploadFile = File(...),
):
    from app.services.agent_runtime.edit_tools import _install_from_zip

    if not file.filename or not file.filename.lower().endswith(".zip"):
        return Fail(msg="只接受 .zip 文件")
    # uid 恒取真实用户：升级权限判断需要本人身份；新建技能默认未上架（仅本人可见）
    uid = CTX_USER_ID.get() or None
    try:
        zip_bytes = await file.read()
        if not zip_bytes:
            return Fail(msg="zip 内容为空")
        source_url = f"upload://{file.filename}"
        msg, change = await _install_from_zip(zip_bytes, source_url, None, uid, allow_upgrade=True, source="curated")
    except Exception as e:
        logger.exception("上传安装失败")
        return Fail(msg=f"上传失败：{e}")
    if change.get("action") == "failed" or not change.get("skillKey"):
        return Fail(msg=msg)

    # 按本次安装返回的 key 精确反查（旧的 source_url startswith 会误拿别人/上次的上传）
    s = await AgentSkill.get_or_none(skill_key=change["skillKey"])
    # allow_upgrade 可原地升级已上架技能 → 已上架则影响其他用户
    _notify_skill_changed(uid, affects_others=bool(s and s.is_enabled))
    data: dict = {"message": msg, "change": change}
    if s is not None:
        data["skill"] = await _skill_to_dict(s)
    return Success(data=data)


@router.get("/{skill_id}/download", summary="下载当前激活版本 zip（创建者/管理员）")
async def download_skill(skill_id: int):
    import io
    import zipfile
    from urllib.parse import quote

    s = await _get_skill_by_id(skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    # 技能文件本体仅创建者/管理员可得：上架只是「可添加使用」，不构成文件分发授权
    uid, _role_codes, is_super = await get_current_role_codes()
    if not _can_manage(s, uid, _is_manager(is_super, _role_codes)):
        return Fail(code="4032", msg="forbidden: 仅创建者或管理员可下载技能包")
    files = await AgentSkillFile.filter(skill_key=s.skill_key, is_active=True)
    if not files:
        return Fail(msg="该技能没有可下载的文件")

    def _build_zip() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.writestr(f.path, f.content)
        return buf.getvalue()

    import asyncio as _asyncio

    zip_bytes = await _asyncio.to_thread(_build_zip)
    fname = f"{s.skill_key}-{s.version or 'latest'}.zip"
    ascii_fallback = fname.encode("ascii", errors="replace").decode("ascii").replace("?", "_")
    encoded = quote(fname, safe="")
    disposition = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"

    def gen():
        yield zip_bytes

    return StreamingResponse(gen(), media_type="application/zip", headers={"Content-Disposition": disposition})


# ── 发现（find-skills）：LLM + GitHub 搜索 / 联网搜索，SSE 返回候选 ──────────

class DiscoverReq(BaseModel):
    query: str = Field(..., description="想要发现的技能主题描述")


_DISCOVER_INSTRUCTION = """\
你是 skill 发现助手。用户给你一段"我想要什么样的 skill"的描述，你的任务：
**找到 1~5 个公开生态里真实存在、可以下载的 skill 包候选**。

操作纪律（严格遵守）：

1. 优先搜 GitHub（绝大多数 skill 只存在于 GitHub 项目）：用 `execute` 请求 GitHub 搜索 API
   （如 `https://api.github.com/search/repositories?q=<英文关键词>+claude+skill&sort=stars&per_page=10`，
   urllib/json 标准库即可）。中文需求要翻成英文搜（索引以英文为主）。
2. GitHub 搜不到或无法访问时，用 `WebSearch` 工具上网找（如 "<query> claude skill github"、
   "<query> agent skill SKILL.md" 等模板），从搜索结果里拿真实仓库链接，再用 `execute` 核实可达。
3. **不要调用 `npx skills add` 安装**——本流程只负责发现，安装由系统的另一条路径执行。
4. 每个候选必须给：name、source_url（GitHub 仓库链接或其 zip 下载链接）、description；
   可选：version。**source_url 必须是真实可达的链接**——只用搜索返回的链接，禁止臆造。

输出纪律：

- 只输出一个 JSON 对象 {"candidates": [{...}, ...]}，**没有任何前后缀、解释、markdown 代码块标记**。
- 找不到合适的就返回 {"candidates": []}，宁可空也不要凑数。
"""


def _sse(d: dict) -> str:
    import json

    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


async def _ask_discover(query: str) -> list[dict]:
    """调用 qa_agent 产出候选，解析 JSON。"""
    import json

    from app.api.v1.ai.qa import _get_agent_for_session

    agent = await _get_agent_for_session("skill-discover", None)
    thread = f"skill-discover-{abs(hash(query)) % 10 ** 8}"
    config = {"configurable": {"thread_id": thread}}
    input_msg = f"{_DISCOVER_INSTRUCTION}\n\n用户描述：{query}"
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": input_msg}]},
            config=config,
        )
    except Exception:
        logger.exception("find-skills 调用失败")
        return []
    answer = ""
    for msg in reversed(result.get("messages", [])):
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if content and getattr(msg, "type", "") == "ai":
            answer = content.strip()
            break
    if not answer:
        return []
    bs = answer.find("{")
    be = answer.rfind("}")
    if bs < 0 or be <= bs:
        return []
    try:
        data = json.loads(answer[bs: be + 1])
    except Exception:
        logger.warning(f"find-skills JSON 解析失败：{answer[:200]}")
        return []
    cands = data.get("candidates")
    if not isinstance(cands, list):
        return []
    out = []
    for c in cands:
        if not isinstance(c, dict):
            continue
        if not c.get("source_url") or not c.get("name"):
            continue
        out.append({
            "name": str(c.get("name"))[:128],
            "description": str(c.get("description", ""))[:400] or None,
            "source_url": str(c.get("source_url"))[:512],
            "version": c.get("version"),
        })
    return out


@router.post("/discover/stream", summary="发现技能（SSE）")
async def discover_stream(req: DiscoverReq):
    import asyncio

    async def gen():
        yield _sse({"type": "started"})
        task = asyncio.create_task(_ask_discover(req.query))
        try:
            while not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=10)
                except asyncio.TimeoutError:
                    yield _sse({"type": "heartbeat"})
                except Exception:
                    break
            try:
                candidates = task.result()
            except Exception as e:
                yield _sse({"type": "error", "message": str(e)[:500]})
                return
            yield _sse({"type": "candidates", "items": candidates})
            yield _sse({"type": "done"})
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── 会话凝练为技能（系统触发路径：内联轻量指令，不经 sediment skill 文件） ────
#
# 对话素材由 sediment_runner.run_sediment 从 DB 重建后拼在触发文本之后（自包含）。
# 指令刻意写成内联而非「加载 sediment skill 再读 reference」：系统触发路径每次都走，
# 省掉技能加载 + read_file 两轮往返与 ~10KB 提示词负担（对话内用户主动触发仍走 skill）。

_DISTILL_TRIGGER = (
    "[系统触发] 把附在本消息末尾的对话历史凝练成一个可复用技能。步骤：\n"
    "1. 判断：对话须展现出可泛化、可复用的工作方式才值得凝练；一次性闲聊、单点查询、纯问答"
    "不足以凝练——此时不要落库，直接输出 skill_key=null 的 marker 并在 summary 说明原因。\n"
    "2. 查重：用 skill_read 查是否已有同类技能；命中则用 skill_save 传该技能的现有 key 做更新，不新建。\n"
    "3. 起草 SKILL.md：开头 YAML frontmatter（name；description ≤60 字，写清何时触发）；"
    "正文用第二人称写做法（泛化可复用，不带本次对话的偶然细节与凭据数据）。\n"
    "4. 落库：调 skill_save(key=技能名, name=..., description=..., skill_md=..., category=从工具参数说明的分类词表选一个)；"
    "正式 key 由平台自动生成，以工具返回文本里的 @key 为准。\n"
    "5. 收尾：最后一条消息必须以下面这个 marker 结尾（后端正则抠取，缺失即整体失败）：\n"
    '<sediment-report>{"type":"skill","skill_key":"skill_save 返回的实际 key","name":"...","description":"...","has_files":false,"summary":"一句话结果说明"}</sediment-report>'
)


async def _resolve_distilled_skill(report: dict) -> Optional[AgentSkill]:
    """凝练跑完后，按 marker 里的 skill_key 反查那条 AgentSkill。"""
    skill_key = (report or {}).get("skill_key")
    if not skill_key:
        return None
    return await AgentSkill.get_or_none(skill_key=skill_key)


@router.post("/from-session", summary="把会话凝练为技能")
async def skill_from_session(req: SkillFromSessionReq):
    """同步版：让 qa_agent 把这次对话凝练成技能（对话素材由 run_sediment 从 DB 注入）。"""
    from app.api.v1.ai.sediment_runner import run_sediment

    uid = CTX_USER_ID.get() or None
    if uid is None:
        return Fail(msg="未登录")
    session = await AgentSession.get_or_none(session_key=req.session_key, is_deleted=0)
    if session is None:
        return Fail(msg="会话不存在")

    trigger = _DISTILL_TRIGGER
    if req.suggested_key:
        trigger += f"\n建议的技能名称：{req.suggested_key}（落库 key 由平台自动按「技能名_专属code」生成）"

    outcome = await run_sediment(
        trigger_text=trigger, session_key=session.session_key, user_id=uid
    )
    report = outcome["report"]
    if not report.get("skill_key"):
        return Fail(msg=report.get("summary") or "此次对话不足以凝练为技能")

    s = await _resolve_distilled_skill(report)
    if s is None:
        return Fail(msg=f"未能定位到凝练产物（skill_key={report.get('skill_key')}）")
    # 凝练产物默认未上架，仅本人可见；skill_save 已即时物化，这里再强制失效一次
    # 确保下一条消息目录树与 DB 一致（force=True 走面板口径）
    _notify_skill_changed(uid)
    return Success(data={"skill": await _skill_to_dict(s), "draft": report})


# ── SSE 流式凝练（避免长耗时 HTTP 超时） ─────────────────────────────────────


@router.post("/from-session/stream", summary="把会话凝练为技能（SSE）")
async def skill_from_session_stream(req: SkillFromSessionReq):
    """SSE 版：done 事件保持原 schema（{type, skill, draft}），前端无感。"""
    from app.api.v1.ai.sediment_runner import sse

    uid = CTX_USER_ID.get() or None

    async def gen():
        if uid is None:
            yield sse({"type": "error", "message": "未登录"})
            return
        session = await AgentSession.get_or_none(session_key=req.session_key, is_deleted=0)
        if session is None:
            yield sse({"type": "error", "message": "会话不存在"})
            return

        trigger = _DISTILL_TRIGGER
        if req.suggested_key:
            trigger += f"\n建议的技能名称：{req.suggested_key}（落库 key 由平台自动按「技能名_专属code」生成）"

        async def _to_done(report: dict) -> dict:
            if not report.get("skill_key"):
                return {"type": "error", "message": report.get("summary") or "此次对话不足以凝练为技能"}
            s = await _resolve_distilled_skill(report)
            if s is None:
                return {"type": "error", "message": f"未能定位到凝练产物（skill_key={report.get('skill_key')}）"}
            return {"skill": await _skill_to_dict(s), "draft": report}

        # stream_sediment 的 on_done 是同步回调；这里要异步反查 DB，所以接管循环
        import asyncio as _asyncio

        from app.api.v1.ai.sediment_runner import run_sediment

        yield sse({"type": "started"})
        task = _asyncio.create_task(
            run_sediment(trigger_text=trigger, session_key=session.session_key, user_id=uid)
        )
        try:
            while not task.done():
                try:
                    await _asyncio.wait_for(_asyncio.shield(task), timeout=10)
                except _asyncio.TimeoutError:
                    yield sse({"type": "heartbeat"})
                except Exception:
                    break
            try:
                outcome = task.result()
            except Exception as e:  # noqa: BLE001
                logger.exception("凝练技能任务异常")
                yield sse({"type": "error", "message": str(e)[:500]})
                return

            done_payload = await _to_done(outcome["report"])
            if done_payload.get("type") == "error":
                yield sse(done_payload)
            else:
                # 同 /from-session 同步版口径：凝练成功 → 强制失效本人同步节流
                _notify_skill_changed(uid)
                yield sse({"type": "done", **done_payload})
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")
