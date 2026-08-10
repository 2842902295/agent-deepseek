"""
Agent 技能 API

- /agent/skills CRUD + 凝练
- 匹配逻辑：消息里出现 @<skill_key> 时，对应 skill_md（SKILL.md 全文）注入到 agent 调用
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from app.core.ctx import CTX_USER_ID
from app.models.standard.agent import AgentSession, AgentSkill, AgentSkillFile
from app.models.system import User
from app.schemas.base import Fail, Success

router = APIRouter(prefix="/agent/skills", tags=["Agent 技能"])


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


def _visible_to(obj, uid: Optional[int], role_codes: list[str], is_super: bool) -> bool:
    """visibility 过滤通用判断（适用于 AgentSkill）。"""
    if is_super:
        return True
    vis = (getattr(obj, "visibility", None) or "private").lower()
    if vis == "public":
        return True
    if vis == "private":
        return obj.user_id is not None and obj.user_id == uid
    if vis == "role":
        allowed = getattr(obj, "allowed_role_codes", None) or []
        return any(rc in role_codes for rc in allowed)
    # 兜底：兼容历史数据（user_id is null 视为公共）
    return obj.user_id is None or obj.user_id == uid


def _is_manager(is_super: bool, role_codes: list[str]) -> bool:
    """超管或管理员：拥有技能管理权限（编辑/删除/可见性/标签/来源设置）。"""
    return is_super or "R_ADMIN" in role_codes


def _can_manage(obj, uid: Optional[int], is_super: bool) -> bool:
    """是否可以编辑/删除/改可见性（is_super 参数传入「超管或管理员」的合成结果）。"""
    if is_super:
        return True
    return obj.user_id is not None and obj.user_id == uid


async def _skill_to_dict(s: AgentSkill) -> dict:
    from app.services.agent_runtime.edit_tools import _parse_skill_md

    # 激活文件数（agent_skill_file is_active=True）
    file_count = await AgentSkillFile.filter(skill_key=s.skill_key, is_active=True).count()
    # skill 规范：SKILL.md 是唯一事实源，展示用的 name/description 优先取其 frontmatter
    fm = _parse_skill_md(s.skill_md or "")
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
        "source": s.source,
        "originSessionId": s.origin_session_id,
        "userId": s.user_id,
        "isEnabled": bool(s.is_enabled),
        "visibility": getattr(s, "visibility", "private") or "private",
        "allowedRoleCodes": getattr(s, "allowed_role_codes", None) or [],
        "tags": getattr(s, "tags", None) or [],
        "createdAt": int(s.create_time.timestamp() * 1000) if s.create_time else None,
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


async def resolve_skills_from_text(text: str, user_id: Optional[int]) -> list[AgentSkill]:
    """从一段文本里抓 @name，返回可见的已启用技能列表（去重，保留出现顺序）。"""
    if not text:
        return []
    names = list(dict.fromkeys(_AT_PATTERN.findall(text)))
    if not names:
        return []
    query = AgentSkill.filter(skill_key__in=names, is_enabled=1)
    skills = await query
    # 按 visibility 过滤
    _, role_codes, is_super = await get_current_role_codes()
    visible: list[AgentSkill] = []
    by_key: dict[str, AgentSkill] = {}
    for sk in skills:
        if not _visible_to(sk, user_id, role_codes, is_super):
            continue
        by_key[sk.skill_key] = sk
    # 按原顺序输出
    for n in names:
        if n in by_key:
            visible.append(by_key[n])
    return visible


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


async def _materialize_from_db(skill_keys: list[str], workspace_dir) -> dict[str, str]:
    """
    从 agent_skill_file 表物化文件到 workspace/.agent_skills/<key>/（与内置 skill 同目录）。
    已存在且文件数匹配则跳过。返回 {skill_key: 相对路径}。
    """
    from pathlib import Path as _P

    from app.models.standard.agent import AgentSkillFile

    if workspace_dir is None or not skill_keys:
        return {}
    ws = _P(workspace_dir)
    mount_root = ws / ".agent_skills"
    mount_root.mkdir(parents=True, exist_ok=True)

    mounted: dict[str, str] = {}
    for key in skill_keys:
        files = await AgentSkillFile.filter(skill_key=key, is_active=True)
        if not files:
            continue
        dst = mount_root / key
        # 简单判断：目录存在且文件数匹配则跳过（避免每次重物化）
        if dst.exists() and dst.is_dir():
            existing_count = sum(1 for _ in dst.rglob("*") if _.is_file())
            if existing_count >= len(files):
                mounted[key] = f".agent_skills/{key}"
                continue
        # 物化
        dst.mkdir(parents=True, exist_ok=True)
        for f in files:
            target = dst / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f.content)
        mounted[key] = f".agent_skills/{key}"
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
    skill_key: Optional[str] = Field(None, description="已废弃：key 由平台按「专属code_技能名」自动生成，传入将被忽略")
    name: str
    description: Optional[str] = None
    skill_md: str = Field(..., description="SKILL.md 正文（可带 YAML frontmatter，后端自动补齐）")
    is_public: bool = False


class SkillUpdateReq(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    skill_md: Optional[str] = None
    is_enabled: Optional[bool] = None


class SkillFromSessionReq(BaseModel):
    session_key: str
    suggested_key: Optional[str] = Field(None, description="可选，覆盖 LLM 建议的 key")
    is_public: bool = False


class SkillSourceReq(BaseModel):
    source: str = Field(..., description="仅超管可设置：official 官方 / curated 收录")


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("", summary="技能列表")
async def list_skills(include_disabled: bool = False):
    uid, role_codes, is_super = await get_current_role_codes()
    qs = AgentSkill.all()
    if not include_disabled:
        qs = qs.filter(is_enabled=1)
    rows = await qs.order_by("id")
    # builtin 不展示给前端（如"编辑"），但 @popup 匹配时仍生效
    visible = [
        s for s in rows
        if s.source != "builtin" and _visible_to(s, uid, role_codes, is_super)
    ]
    return Success(data=[await _skill_to_dict(s) for s in visible])


@router.post("", summary="新建技能")
async def create_skill(req: SkillCreateReq):
    from app.services.agent_runtime.edit_tools import _build_skill_md, sync_skill_md_file

    from app.services.agent_runtime.edit_tools import _alloc_skill_key, _build_user_skill_key, _update_frontmatter

    uid = CTX_USER_ID.get() or None
    if uid is None:
        return Fail(msg="未登录，无法创建技能")
    # key 规范：固定「专属code_技能名」，忽略请求中的 skill_key，冲突自动加后缀
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
        user_id=None if req.is_public else uid,
        is_enabled=1,
    )
    await sync_skill_md_file(s)
    return Success(data=await _skill_to_dict(s))


@router.patch("/{skill_id}", summary="更新技能")
async def update_skill(skill_id: int, req: SkillUpdateReq):
    s = await AgentSkill.get_or_none(id=skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    if not _can_manage(s, uid, _is_manager(is_super, _role_codes)):
        return Fail(msg="无权修改：仅创建者或管理员可改")
    # 内置能力：只允许切换启停，其余字段不可改
    if s.source == "builtin":
        non_enable_fields = {
            k: v for k, v in req.model_dump(exclude_unset=True).items() if k != "is_enabled"
        }
        if non_enable_fields:
            return Fail(msg="内置能力不可编辑，仅允许启用/停用")
        if req.is_enabled is not None:
            s.is_enabled = 1 if req.is_enabled else 0
            await s.save()
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
        s.is_enabled = 1 if req.is_enabled else 0
    await s.save()
    # skill 规范：name / description / skill_md 任一变化都同步到 SKILL.md 主文件
    if req.name is not None or req.description is not None or req.skill_md is not None:
        from app.services.agent_runtime.edit_tools import sync_skill_md_file

        await sync_skill_md_file(s)
    # 名称有变 → 实时重算 key（专属code_技能名，级联文件表），无需人工维护
    if req.name is not None or req.skill_md is not None:
        from app.services.agent_runtime.edit_tools import apply_key_convention

        await apply_key_convention(s)
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
    await AgentSkillFile.filter(skill_key=s.skill_key).delete()
    await s.delete()
    return Success(data=None, msg="已删除")


class VisibilityReq(BaseModel):
    visibility: str = Field(..., description="private / role / public")
    allowed_role_codes: Optional[list[str]] = Field(None, description="visibility=role 时使用")


class TagsReq(BaseModel):
    tags: list[str] = Field(default_factory=list, description="用户自定义标签，去重后保存")


@router.patch("/{skill_id}/visibility", summary="修改技能可见性")
async def update_skill_visibility(skill_id: int, req: VisibilityReq):
    s = await AgentSkill.get_or_none(id=skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    uid, _role_codes, is_super = await get_current_role_codes()
    if not _can_manage(s, uid, _is_manager(is_super, _role_codes)):
        return Fail(msg="无权修改可见性：仅创建者或管理员可改")
    if req.visibility not in ("private", "role", "public"):
        return Fail(msg="visibility 必须是 private / role / public 之一")
    s.visibility = req.visibility
    s.allowed_role_codes = req.allowed_role_codes if req.visibility == "role" else None
    await s.save(update_fields=["visibility", "allowed_role_codes", "update_time"])
    return Success(data=await _skill_to_dict(s))


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


@router.put("/{skill_id}/source", summary="设置技能来源（仅超管：官方/收录）")
async def set_skill_source(skill_id: int, req: SkillSourceReq):
    """超管把技能标记为「官方」（key 去掉用户名前缀），或取消标记回到「收录」。"""
    from app.services.agent_runtime.edit_tools import apply_key_convention

    _uid, _role_codes, is_super = await get_current_role_codes()
    if not _is_manager(is_super, _role_codes):
        return Fail(code="4032", msg="forbidden: 仅超管/管理员可设置技能来源")
    if req.source not in ("official", "curated"):
        return Fail(msg="source 只能是 official（官方）或 curated（收录）")
    s = await AgentSkill.get_or_none(id=skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    if s.source == "builtin":
        return Fail(msg="内置技能不可修改来源")
    if s.source != req.source:
        s.source = req.source
        await s.save(update_fields=["source", "update_time"])
        # key 规范随来源实时联动：官方去掉用户名前缀，取消官方则加回
        await apply_key_convention(s)
    return Success(data=await _skill_to_dict(s))


# ── 版本 / 文件 / 安装 / 上传 / 下载 / 发现 ─────────────────────────────────


async def _get_skill_by_id(skill_id: int) -> Optional[AgentSkill]:
    return await AgentSkill.get_or_none(id=skill_id)


@router.get("/{skill_id}/versions", summary="列出技能的所有版本")
async def list_skill_versions(skill_id: int):
    s = await _get_skill_by_id(skill_id)
    if s is None:
        return Fail(msg="技能不存在")
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


@router.post("/{skill_id}/versions/{version}/activate", summary="切换激活版本")
async def activate_skill_version(skill_id: int, version: str):
    s = await _get_skill_by_id(skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    exists = await AgentSkillFile.exists(skill_key=s.skill_key, version=version)
    if not exists:
        return Fail(msg=f"版本不存在：{version}")
    # 先把该 skill 的所有文件置为非激活，再激活目标版本
    await AgentSkillFile.filter(skill_key=s.skill_key).update(is_active=False)
    await AgentSkillFile.filter(skill_key=s.skill_key, version=version).update(is_active=True)
    s.version = version
    await s.save(update_fields=["version", "update_time"])
    return Success(data=await _skill_to_dict(s))


@router.delete("/{skill_id}/versions/{version}", summary="删除某历史版本")
async def delete_skill_version(skill_id: int, version: str):
    s = await _get_skill_by_id(skill_id)
    if s is None:
        return Fail(msg="技能不存在")
    if version == (s.version or "1.0.0"):
        return Fail(msg="当前激活版本不可删除，请先切换到其他版本")
    deleted = await AgentSkillFile.filter(skill_key=s.skill_key, version=version).delete()
    if not deleted:
        return Fail(msg=f"版本不存在：{version}")
    return Success(data=None, msg="已删除")


class InstallReq(BaseModel):
    source_url: str
    suggested_key: Optional[str] = None
    is_public: bool = False


@router.post("/install", summary="从 URL 安装技能")
async def install_skill(req: InstallReq):
    import httpx

    from app.services.agent_runtime.edit_tools import _install_from_md, _install_from_zip

    uid = None if req.is_public else (CTX_USER_ID.get() or None)
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
    try:
        if is_zip:
            msg, _change = await _install_from_zip(body, req.source_url, req.suggested_key, req.is_public, uid)
        else:
            text = body.decode("utf-8", errors="ignore")
            msg = await _install_from_md(text, req.source_url, req.suggested_key, req.is_public, uid)
    except Exception as e:
        logger.exception("安装技能失败")
        return Fail(msg=f"安装失败：{e}")

    # 反查刚创建的技能（source_url 唯一标识本次安装）
    s = await AgentSkill.filter(source_url=req.source_url).order_by("-id").first()
    if s is None:
        return Success(data={"message": msg})
    return Success(data={"skill": await _skill_to_dict(s), "message": msg})


@router.post("/upload", summary="上传 zip 安装技能")
async def upload_skill(
    file: UploadFile = File(...),
    is_public: bool = Form(False),
):
    from app.services.agent_runtime.edit_tools import _install_from_zip

    if not file.filename or not file.filename.lower().endswith(".zip"):
        return Fail(msg="只接受 .zip 文件")
    # uid 恒取真实用户：升级权限判断需要本人身份；新建时的公开归属由 is_public 控制
    uid = CTX_USER_ID.get() or None
    try:
        zip_bytes = await file.read()
        if not zip_bytes:
            return Fail(msg="zip 内容为空")
        source_url = f"upload://{file.filename}"
        msg, change = await _install_from_zip(zip_bytes, source_url, None, is_public, uid, allow_upgrade=True, source="curated")
    except Exception as e:
        logger.exception("上传安装失败")
        return Fail(msg=f"上传失败：{e}")
    if change.get("action") == "failed" or not change.get("skillKey"):
        return Fail(msg=msg)

    # 按本次安装返回的 key 精确反查（旧的 source_url startswith 会误拿别人/上次的上传）
    s = await AgentSkill.get_or_none(skill_key=change["skillKey"])
    data: dict = {"message": msg, "change": change}
    if s is not None:
        data["skill"] = await _skill_to_dict(s)
    return Success(data=data)


@router.get("/{skill_id}/download", summary="下载当前激活版本 zip")
async def download_skill(skill_id: int):
    import io
    import zipfile
    from urllib.parse import quote

    s = await _get_skill_by_id(skill_id)
    if s is None:
        return Fail(msg="技能不存在")
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


# ── 发现（find-skills）：LLM + skills.sh 搜索，SSE 返回候选 ──────────────────

class DiscoverReq(BaseModel):
    query: str = Field(..., description="想要发现的技能主题描述")


_DISCOVER_INSTRUCTION = """\
你是 skill 发现助手。用户给你一段"我想要什么样的 skill"的描述，你的任务：
**找到 1~5 个公开生态里真实存在、可以下载的 skill 包候选**。

操作纪律（严格遵守）：

1. 必须读 `skill-discovery` skill（_project 下的 project-bound skill），按里面写的流程发现：
   用 `execute` 跑 `python $AGENT_SKILLS/_project/skill-discovery/scripts/search.py <query> --limit 5 --json`
   走 skills.sh 真实索引。中文需求要翻成英文搜（skills.sh 索引以英文为主）。
2. 脚本失败时（网络问题、leaderboard 为空），fallback 用 `bailian_web_search` MCP 工具搜
   "<query> agent skill SKILL.md github"、"skills.sh <query>" 等模板。
3. **不要调用 `npx skills add` 安装**——本流程只负责发现，安装由系统的另一条路径执行。
4. 每个候选必须给：name、source_url（search.py 输出的 raw URL 直接用）、description；
   可选：version。**source_url 必须是真实可达的链接**——search.py 给出的 URL 直接用即可。

输出纪律：

- 只输出一个 JSON 对象 {"candidates": [{...}, ...]}，**没有任何前后缀、解释、markdown 代码块标记**。
- 找不到合适的就返回 {"candidates": []}，宁可空也不要凑数。
"""


def _sse(d: dict) -> str:
    import json

    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


async def _ask_discover(query: str) -> list[dict]:
    """调用 qa_agent（带 skill-discovery skill）产出候选，解析 JSON。"""
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


# ── 会话凝练为技能（走 qa_agent + sediment skill 模式 B） ────────────────────


async def _resolve_distilled_skill(report: dict) -> Optional[AgentSkill]:
    """sediment skill 模式 B 跑完后，按 marker 里的 skill_key 反查那条 AgentSkill。"""
    skill_key = (report or {}).get("skill_key")
    if not skill_key:
        return None
    return await AgentSkill.get_or_none(skill_key=skill_key)


@router.post("/from-session", summary="把会话凝练为技能")
async def skill_from_session(req: SkillFromSessionReq):
    """同步版：让 qa_agent 走 sediment skill（模式 B）把这次对话凝练成 capability。"""
    from app.api.v1.ai.sediment_runner import run_sediment

    uid = CTX_USER_ID.get() or None
    if uid is None:
        return Fail(msg="未登录")
    session = await AgentSession.get_or_none(session_key=req.session_key, is_deleted=0)
    if session is None:
        return Fail(msg="会话不存在")

    trigger = (
        "[系统触发] 把刚才的整段对话凝练成可复用的技能。"
        "按 sediment skill 模式 B 走（先读 _project/sediment/references/skill-mode.md），"
        "用 skill_save 工具落库，最后输出 <sediment-report> marker（type=skill）。"
    )
    if req.suggested_key:
        trigger += f"\n建议的技能名称：{req.suggested_key}（落库 key 由平台自动按「专属code_技能名」生成）"
    if req.is_public:
        trigger += "\n调用 skill_save 创建技能时传 is_public=true。"

    outcome = await run_sediment(
        trigger_text=trigger, session_key=session.session_key, user_id=uid
    )
    report = outcome["report"]
    if not report.get("skill_key"):
        return Fail(msg=report.get("summary") or "此次对话不足以凝练为技能")

    s = await _resolve_distilled_skill(report)
    if s is None:
        return Fail(msg=f"未能定位到凝练产物（skill_key={report.get('skill_key')}）")
    return Success(data={"skill": await _skill_to_dict(s), "draft": report})


# ── SSE 流式凝练（避免长耗时 HTTP 超时） ─────────────────────────────────────


@router.post("/from-session/stream", summary="把会话凝练为技能（SSE）")
async def skill_from_session_stream(req: SkillFromSessionReq):
    """SSE 版：done 事件保持原 schema（{type, skill, draft}），前端无感。"""
    from app.api.v1.ai.sediment_runner import sse, stream_sediment

    uid = CTX_USER_ID.get() or None

    async def gen():
        if uid is None:
            yield sse({"type": "error", "message": "未登录"})
            return
        session = await AgentSession.get_or_none(session_key=req.session_key, is_deleted=0)
        if session is None:
            yield sse({"type": "error", "message": "会话不存在"})
            return

        trigger = (
            "[系统触发] 把刚才的整段对话凝练成可复用的技能。"
            "按 sediment skill 模式 B 走（先读 _project/sediment/references/skill-mode.md），"
            "用 skill_save 工具落库，最后输出 <sediment-report> marker（type=skill）。"
        )
        if req.suggested_key:
            trigger += f"\n建议的 skill_key：{req.suggested_key}（冲突时自动加后缀）"
        if req.is_public:
            trigger += "\n调用 skill_save 创建技能时传 is_public=true。"

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
                yield sse({"type": "done", **done_payload})
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")
