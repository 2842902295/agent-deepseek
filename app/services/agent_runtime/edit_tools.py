"""
统一技能管理工具集（5 个工具）：skill_read / skill_save / skill_delete / skill_install / skill_list。

平台架构（所有工具的共同前提）：
- **OceanBase 是唯一事实源**：用户技能存 agent_skill（元数据+skill_md）+ agent_skill_file（文件 BLOB+版本）
- 磁盘 `.agent_workspace/.agent_skills/` 只放系统内置技能（只读）；用户 workspace 里的
  `.agent_skills/<key>/` 是运行时物化缓存，随时会被同步逻辑重建/清理，**不是交付物**
- builtin 来源不可删改；skill_key 全局唯一，冲突自动加后缀

设计原则：
- agent 只表达意图（读/写/删/装/列），工具内部处理"动哪张表、碰不碰磁盘"
- 工具内部自捕获异常并返回错误文本，绝不把异常抛回 agent 图（避免整轮对话崩掉）
- 多用户：skill_key 全局唯一，upsert 时检查归属，冲突自动加后缀
"""

from __future__ import annotations

import asyncio
import io
import re
import zipfile
from pathlib import Path
from typing import Annotated, Any, Optional

import httpx
from langchain.tools import tool
from loguru import logger

from app.models.standard.agent import AgentSkill, AgentSkillFile

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_WORKSPACE_ROOT = _PROJECT_ROOT / ".agent_workspace"
_USERS_ROOT = _WORKSPACE_ROOT / "users"

# 二进制文件扩展名（命中时 is_binary=True，skill_read 不返回内容）
_BINARY_EXTS = frozenset(
    ".png .jpg .jpeg .gif .bmp .ico .webp .svg".split()
    + ".zip .tar .gz .bz2 .7z .rar".split()
    + ".pdf .doc .docx .xls .xlsx .ppt .pptx".split()
    + ".exe .dll .so .dylib .bin .dat".split()
    + ".mp3 .mp4 .avi .mov .wav .flac".split()
    + ".ttf .otf .woff .woff2 .eot".split()
)


def _is_binary_path(path: str) -> bool:
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return ext in _BINARY_EXTS


def _validate_key(key: str) -> Optional[str]:
    """校验 skill_key 合法性。返回 None=通过，否则返回错误原因。

    与 @ 召唤正则 `@([^\\s@{}]+)` 对齐：不含空白/@/{}；长度 2~64（DB 上限，
    兼容「专属code_技能名」规范 key）；禁止 . 开头（避免与隐藏目录/相对路径混淆）。
    """
    key = (key or "").strip()
    if not key:
        return "skill_key 不能为空"
    if len(key) < 2 or len(key) > 64:
        return f"skill_key 长度须为 2~64 字，当前 {len(key)}"
    if re.search(r"[\s@{}]", key):
        return "skill_key 不能包含空白字符、@、{、}（会影响 @召唤）"
    if key.startswith("."):
        return "skill_key 不能以 . 开头"
    return None


def _slugify_key_part(s: str) -> str:
    """清洗 key 组成片段：@ 召唤不允许的字符（空白/@/{}）与路径危险字符统一换成 -。"""
    s = re.sub(r"[\\/:*?\"<>|@{}\s]+", "-", (s or "").strip())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s.lstrip(".")


# 技能专属 code 字符集：去掉易混淆字符（i/l/o/0/1）
_SKILL_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


async def _get_or_create_skill_code(uid: int) -> Optional[str]:
    """取用户的技能专属 code；首次使用时自动生成（随机短码、全局唯一、与登录名/手机号完全无关）。"""
    import secrets

    from app.models.system import User

    user = await User.get_or_none(id=uid)
    if user is None:
        return None
    if user.skill_code:
        return user.skill_code
    while True:
        code = "".join(secrets.choice(_SKILL_CODE_ALPHABET) for _ in range(6))
        if not await User.filter(skill_code=code).exists():
            break
    user.skill_code = code
    await user.save(update_fields=["skill_code"])
    return code


async def _build_user_skill_key(uid: Optional[int], name: str) -> str:
    """按规范生成 skill_key：「技能专属code_技能名」，不允许自由命名。

    专属 code 由系统自动生成（随机短码，与登录名/手机号解耦，防隐私泄露）；
    无登录态时退化为纯名称。
    """
    code = await _get_or_create_skill_code(uid) if uid is not None else None
    name_part = _slugify_key_part(name)[:43] or "skill"
    base = f"{code}_{name_part}" if code else name_part
    return base[:64]


async def _alloc_skill_key(base: str, exclude_id: Optional[int] = None) -> str:
    """key 冲突时自动加后缀（-2、-3…）；exclude_id 排除自身（重算场景）。"""
    final_key = base
    suffix = 2
    while True:
        qs = AgentSkill.filter(skill_key=final_key)
        if exclude_id is not None:
            qs = qs.exclude(id=exclude_id)
        if not await qs.exists():
            return final_key
        final_key = f"{base}-{suffix}"
        suffix += 1


async def apply_key_convention(skill: AgentSkill) -> Optional[str]:
    """按「专属code_技能名」规范重算 skill_key，实现存储 key 与规范的实时一致。

    名称变化后调用：key 有变 → 级联更新文件表 + 清理旧 workspace 物化目录，返回新 key；
    未变化返回 None。builtin 不适用。
    """
    if skill.source == "builtin":
        return None
    # 官方技能 key 不带专属 code 前缀；其余按「专属code_技能名」
    if skill.source == "official":
        base = _slugify_key_part(skill.name)[:64] or "skill"
    else:
        base = await _build_user_skill_key(skill.user_id, skill.name)
    new_key = await _alloc_skill_key(base, exclude_id=skill.id)
    if new_key == skill.skill_key:
        return None
    old_key = skill.skill_key
    await AgentSkillFile.filter(skill_key=old_key).update(skill_key=new_key)
    skill.skill_key = new_key
    await skill.save(update_fields=["skill_key", "update_time"])
    await _purge_workspace_skill(old_key, skill.user_id)
    logger.info(f"技能 key 规范同步：@{old_key} → @{new_key}")
    return new_key


def _parse_skill_md(md: str) -> dict[str, Any]:
    """从 SKILL.md 抠 YAML frontmatter。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", md, re.S)
    if not m:
        return {}
    out: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip("\"'")
    return out


def _extract_pkg_keys(skill_md: str) -> list[str]:
    """从 SKILL.md 的 YAML frontmatter 提取 skills: [k1, k2]。"""
    if not skill_md:
        return []
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", skill_md, re.S)
    if not m:
        return []
    for line in m.group(1).splitlines():
        line = line.strip()
        if line.startswith("skills:"):
            val = line.split(":", 1)[1].strip()
            if val.startswith("[") and val.endswith("]"):
                return [x.strip().strip("\"'") for x in val[1:-1].split(",") if x.strip()]
    return []


def _build_skill_md(name: str, description: Optional[str], version: Optional[str], body: str) -> str:
    """按 skill 规范生成 SKILL.md：YAML frontmatter + 正文。body 已含 frontmatter 时视为完整 SKILL.md，原样返回。"""
    body = (body or "").strip()
    if body.startswith("---"):
        return body + "\n"
    lines = ["---", f"name: {name or 'skill'}"]
    if description:
        lines.append(f"description: {description}")
    lines.append(f"version: {version or '1.0.0'}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body + "\n"


def _update_frontmatter(md: str, name: Optional[str] = None, description: Optional[str] = None) -> str:
    """更新 SKILL.md frontmatter 里的 name / description，保持字段与文件一致。

    None = 保留原行；"" = 删除该行；其他值 = 覆盖（原 frontmatter 没有的键追加）。
    无 frontmatter 时原样返回。
    """
    m = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)", md or "", re.S)
    if not m:
        return md
    head, body, tail = m.group(1), m.group(2), m.group(3)
    updates = {"name": name, "description": description}
    out: list[str] = []
    handled: set[str] = set()
    for line in body.splitlines():
        key = line.strip().split(":", 1)[0].strip() if ":" in line else ""
        if key in updates:
            handled.add(key)
            v = updates[key]
            if v is None:
                out.append(line)
            elif v:
                out.append(f"{key}: {v}")
            # v == "" → 删除该行
        else:
            out.append(line)
    for key, v in updates.items():
        if key not in handled and v:
            out.append(f"{key}: {v}")
    return head + "\n".join(out) + tail


async def sync_skill_md_file(skill: AgentSkill) -> None:
    """skill 规范：每个技能必须有一份与 skill_md 字段一致的激活 SKILL.md（字段内容即主文件内容）。"""
    md = _build_skill_md(skill.name, skill.description, skill.version, skill.skill_md)
    content = md.encode("utf-8")
    version = skill.version or "1.0.0"
    # 兼容历史大小写（zip 内可能叫 skill.md）
    rows = await AgentSkillFile.filter(skill_key=skill.skill_key, version=version)
    for f in rows:
        if f.path.lower() == "skill.md":
            if bytes(f.content) != content:
                f.content = content
                f.size = len(content)
                f.is_binary = False
                f.is_active = True
                await f.save()
            return
    await AgentSkillFile.create(
        skill_key=skill.skill_key,
        path="SKILL.md",
        content=content,
        size=len(content),
        is_binary=False,
        version=version,
        is_active=True,
    )


def _can_manage(skill: AgentSkill, uid: Optional[int], role_codes: list[str]) -> bool:
    """当前用户是否有权管理该技能（超管或管理员可管理所有技能）。"""
    if "R_SUPER" in role_codes or "R_ADMIN" in role_codes:
        return True
    if skill.source == "builtin":
        return False
    return skill.user_id == uid


async def _get_role_codes(uid: Optional[int]) -> list[str]:
    if uid is None:
        return []
    from app.models.system import User

    user = await User.get_or_none(id=uid).prefetch_related("by_user_roles")
    if user is None:
        return []
    return [r.role_code for r in user.by_user_roles]


def _builtin_skill_dir(key: str) -> Optional[Path]:
    """磁盘内置技能目录（全局 .agent_skills/ 顶层或 _project/ 下，含 SKILL.md 才算）；不存在返回 None。"""
    if not key or "/" in key or "\\" in key or ".." in key:
        return None
    for d in (_WORKSPACE_ROOT / ".agent_skills" / key, _WORKSPACE_ROOT / ".agent_skills" / "_project" / key):
        if (d / "SKILL.md").is_file():
            return d
    return None


def _iter_builtin_skill_dirs() -> dict[str, Path]:
    """扫全局磁盘内置技能，返回 {key: 目录}；_project 下同名覆盖顶层。"""
    out: dict[str, Path] = {}
    root = _WORKSPACE_ROOT / ".agent_skills"
    if not root.is_dir():
        return out
    for entry in root.iterdir():
        if entry.is_dir() and not entry.name.startswith(".") and entry.name != "_project" and (entry / "SKILL.md").is_file():
            out[entry.name] = entry
    project = root / "_project"
    if project.is_dir():
        for entry in project.iterdir():
            if entry.is_dir() and not entry.name.startswith(".") and (entry / "SKILL.md").is_file():
                out[entry.name] = entry
    return out


# ── workspace 物化（best effort，失败只记日志不影响落库） ────────────────────


def _user_skills_dir(uid: Optional[int]) -> Optional[Path]:
    """当前操作用户的 workspace 技能目录；uid 为空返回 None。"""
    if uid is None:
        return None
    return _USERS_ROOT / str(uid) / ".agent_skills"


async def _materialize_active_files(skill_key: str, uid: Optional[int]) -> None:
    """把刚写入 DB 的激活文件物化到操作用户的 workspace，让 SkillsMiddleware 立即可见。

    与 agent_skill._materialize_from_db 同构（直接写 users/{uid}/.agent_skills/<key>/）。
    60s 节流同步之后也会由它接管维护；这里只是消除空窗。
    """
    target_root = _user_skills_dir(uid)
    if target_root is None:
        return

    def _write() -> None:
        dst = target_root / skill_key
        dst.mkdir(parents=True, exist_ok=True)

    try:
        files = await AgentSkillFile.filter(skill_key=skill_key, is_active=True)
        if not files:
            return
        if not target_root.parent.exists():  # workspace 尚未创建的用户不主动建
            return
        await asyncio.to_thread(_write)
        for f in files:
            target = target_root / skill_key / f.path
            content = bytes(f.content)

            def _write_file(t=target, c=content) -> None:
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_bytes(c)

            await asyncio.to_thread(_write_file)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"物化技能到 workspace 失败（不影响落库）{skill_key}: {e}")


async def _purge_workspace_skill(skill_key: str, uid: Optional[int]) -> None:
    """删除操作用户 workspace 里的技能物化目录（删除技能后的磁盘清理）。"""
    target_root = _user_skills_dir(uid)
    if target_root is None:
        return
    dst = target_root / skill_key
    if not dst.exists():
        return

    import shutil

    try:
        await asyncio.to_thread(shutil.rmtree, dst, True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"清理 workspace 技能目录失败 {skill_key}: {e}")


# ── 工具 1：skill_read ─────────────────────────────────────────────────────


@tool
async def skill_read(
    key: Annotated[str, "技能 key（@ 召唤用的关键字）"],
    file_path: Annotated[Optional[str], "要读取的文件路径（如 scripts/run.py）；不传则返回技能概览"] = None,
) -> str:
    """读取技能信息（DB 技能 + 磁盘内置技能均可）。不传 file_path 返回概览（元数据+文件列表），传了返回该文件内容。"""
    try:
        skill = await AgentSkill.get_or_none(skill_key=key)

        if file_path:
            # 读具体文件：先 DB，未命中再回退磁盘内置技能目录
            f = await AgentSkillFile.get_or_none(skill_key=key, path=file_path, is_active=True)
            if f is not None:
                if f.is_binary:
                    return f"[二进制文件] {file_path}（{f.size} 字节）"
                return f.content.decode("utf-8", errors="replace")
            bd = _builtin_skill_dir(key)
            if bd is not None and ".." not in file_path.split("/"):
                target = (bd / file_path.lstrip("/")).resolve()
                try:
                    target.relative_to(bd.resolve())
                except ValueError:
                    return f"非法路径（越出技能目录）：{file_path}"
                if target.is_file():
                    if _is_binary_path(file_path):
                        return f"[二进制文件] {file_path}（{target.stat().st_size} 字节）"
                    return await asyncio.to_thread(target.read_text, "utf-8", "replace")
            return f"文件不存在：{file_path}（技能 {key}）"

        # 概览：DB 技能
        if skill is not None:
            files = await AgentSkillFile.filter(skill_key=key, is_active=True).order_by("path")
            file_list = "\n".join(f"  {f.path} ({f.size}B)" for f in files) or "  (无文件)"
            parts = [
                f"skill_key: {skill.skill_key}",
                f"name: {skill.name}",
                f"description: {skill.description or ''}",
                f"source: {skill.source}",
                f"version: {skill.version or '-'}",
                f"is_enabled: {bool(skill.is_enabled)}",
                f"visibility: {skill.visibility}",
                f"--- SKILL.md ---",
                skill.skill_md,
                f"--- files ({len(files)}) ---",
                file_list,
            ]
            return "\n".join(parts)

        # 概览：磁盘内置技能（不在 DB，如 skill-creator / office-cli）
        bd = _builtin_skill_dir(key)
        if bd is not None:
            skill_md = await asyncio.to_thread((bd / "SKILL.md").read_text, "utf-8", "replace")
            fm = _parse_skill_md(skill_md)

            def _walk() -> list[str]:
                out: list[str] = []
                for p in sorted(bd.rglob("*")):
                    if p.is_file() and not any(part.startswith(".") for part in p.relative_to(bd).parts):
                        out.append(f"  {p.relative_to(bd).as_posix()} ({p.stat().st_size}B)")
                return out

            fl = await asyncio.to_thread(_walk)
            parts = [
                f"skill_key: {key}",
                f"name: {fm.get('name') or key}",
                f"description: {fm.get('description') or ''}",
                "source: builtin（磁盘内置，未入库；只读，不可 skill_save/skill_delete）",
                "--- SKILL.md ---",
                skill_md,
                f"--- files ({len(fl)}) ---",
                "\n".join(fl) or "  (无文件)",
            ]
            return "\n".join(parts)

        return f"技能不存在：{key}"
    except Exception as e:  # noqa: BLE001
        logger.exception(f"skill_read 失败 key={key}")
        return f"skill_read 执行失败：{type(e).__name__}: {e}"


# ── 工具 2：skill_save ─────────────────────────────────────────────────────


@tool
async def skill_save(
    key: Annotated[
        str,
        "技能 key。更新已有技能时传目标技能的现有 key（@ 召唤用的关键字）；"
        "新建时 key 由平台自动按「专属code_技能名」生成，此参数只作名称参考",
    ],
    name: Annotated[Optional[str], "显示名；不传则不改（更新时）或取 key（创建时）"] = None,
    description: Annotated[Optional[str], "简短描述（≤60 字，写明何时触发）"] = None,
    skill_md: Annotated[Optional[str], "SKILL.md 主文件全文（含 YAML frontmatter）；不传则不改"] = None,
    files: Annotated[
        Any,
        "文件列表（JSON 或 list）：[{path, content}]。path 如 scripts/run.py；content 为文本内容。"
        "传入时会覆盖同路径旧文件；旧版本中不在列表里的文件将转为历史版本。不传则不动文件。",
    ] = None,
    is_public: Annotated[bool, "true=全员可见；false=仅当前用户私有（默认）。公开前请先征得用户同意"] = False,
) -> str:
    """创建或更新技能（upsert），写入数据库。key 已存在且有权管理 → 更新；
    无权编辑（他人技能）→ 拒绝并返回提示，由 agent 引导用户换个技能名新建。
    新建时 key 固定由平台按「专属code_技能名」自动生成（不接受自由命名），冲突自动加后缀。
    这是技能持久化的唯一途径——不要把技能写成 workspace 里的文件当交付物。"""
    from app.core.ctx import CTX_USER_ID

    try:
        uid = CTX_USER_ID.get() or None
        role_codes = await _get_role_codes(uid)

        # key 合法性校验
        key = (key or "").strip()
        key_err = _validate_key(key)
        if key_err:
            return f"skill_save 失败：{key_err}"

        # 解析 files 参数
        if isinstance(files, str):
            import json

            try:
                files = json.loads(files)
            except Exception:
                return "files 参数解析失败：不是合法 JSON"
        if files is not None and not isinstance(files, list):
            return f"files 必须是 list，实际：{type(files).__name__}"

        # ── upsert 逻辑 ──
        existing = await AgentSkill.get_or_none(skill_key=key)
        final_key = key

        if existing:
            # builtin 全量保护：任何写入（含仅 files）都拦截
            if existing.source == "builtin":
                return f"技能 {key} 是内置（builtin），不可编辑"
            if not _can_manage(existing, uid, role_codes):
                # 无权编辑他人技能：直接拒绝并给出正确提示，不做任何自动转换
                return (
                    f"无权编辑技能 @{key}：仅技能创建者或超管可修改，原技能未做任何改动。"
                    f"如用户希望在此基础上修改，请引导其创建一个自己的新技能："
                    f"先用 skill_read 读取 @{key} 的内容（含文件），再用 skill_save 换个技能名保存"
                    f"（key 由平台自动按「专属code_技能名」生成）。"
                )

        _pre_name: Optional[str] = existing.name if existing else None

        if existing:
            # ── 更新 ──
            changed: list[str] = []
            if name is not None and name != existing.name:
                existing.name = name[:64]
                changed.append("name")
            if description is not None:
                existing.description = description[:1000] or None
                changed.append("description")
            if skill_md is not None and skill_md != existing.skill_md:
                existing.skill_md = skill_md
                changed.append("skill_md")
            if changed:
                await existing.save()
        else:
            # ── 创建 ──
            if not skill_md and not files:
                return "创建技能至少需要 skill_md 或 files 之一"
            if uid is None:
                return "创建技能需要登录态"
            # key 规范：固定「专属code_技能名」，传入的 key 仅作名称参考，冲突自动加后缀
            final_key = await _alloc_skill_key(await _build_user_skill_key(uid, name or key or "skill"))
            existing = await AgentSkill.create(
                skill_key=final_key,
                name=(name or final_key)[:64],
                description=(description or "")[:1000] or None,
                skill_md=skill_md or "",
                source="derived",
                user_id=None if is_public else uid,
                is_enabled=1,
                visibility="public" if is_public else "private",
            )
            changed = ["created"]

        # ── 文件写入 ──
        files_written = 0
        skill_md_content: Optional[str] = None
        new_paths: list[str] = []
        if files:
            for item in files:
                if not isinstance(item, dict) or "path" not in item:
                    continue
                fpath = item["path"].strip().lstrip("/")
                content_str = item.get("content", "")
                if not fpath or ".." in fpath.split("/"):
                    continue
                content_bytes = content_str.encode("utf-8") if isinstance(content_str, str) else content_str
                is_bin = _is_binary_path(fpath)

                # 如果 files 里有 SKILL.md，解析 frontmatter 同步元数据
                if fpath.lower() == "skill.md" and not is_bin:
                    skill_md_content = content_str if isinstance(content_str, str) else content_str.decode("utf-8", errors="replace")
                    fm = _parse_skill_md(skill_md_content)
                    if fm.get("name"):
                        existing.name = fm["name"][:64]
                    if fm.get("description"):
                        existing.description = fm["description"][:1000]
                    if fm.get("version"):
                        existing.version = fm["version"][:32]

                # upsert 文件行（当前激活版本）
                current_version = existing.version or "1.0.0"
                await AgentSkillFile.update_or_create(
                    skill_key=final_key,
                    path=fpath,
                    version=current_version,
                    defaults={
                        "content": content_bytes,
                        "size": len(content_bytes),
                        "is_binary": is_bin,
                        "is_active": True,
                    },
                )
                new_paths.append(fpath)
                files_written += 1

            # 旧激活文件中不在新列表里的 → 转历史版本（与上传升级语义对齐）
            if new_paths:
                await AgentSkillFile.filter(skill_key=final_key, is_active=True).exclude(path__in=new_paths).update(is_active=False)

            await existing.save()

        # ── skill_md ↔ SKILL.md 文件同步（规范：字段内容即主文件内容）──
        if skill_md_content is not None:
            # 传了 SKILL.md 文件 → 文件为准，回写 skill_md 字段
            if existing.skill_md != skill_md_content:
                existing.skill_md = skill_md_content
                existing.skill_pkg_keys = _extract_pkg_keys(skill_md_content) or None
                await existing.save()
        else:
            # 没传 SKILL.md 文件 → 用 skill_md 字段补一份，保证技能始终有主文件
            await sync_skill_md_file(existing)

        # ── 名称有变 → 实时重算 key（专属code_技能名），保持存储与规范一致 ──
        if _pre_name is not None and existing.name != _pre_name:
            new_key = await apply_key_convention(existing)
            if new_key:
                final_key = new_key

        # ── 立即物化到操作用户 workspace（best effort，消除 60s 同步空窗）──
        await _materialize_active_files(final_key, uid)

        summary = f"技能 @{final_key}"
        if "created" in changed:
            summary += f" 已创建（id={existing.id}）"
        else:
            summary += f" 已更新字段：{', '.join(changed)}" if changed else " 无变更"
        if files_written:
            summary += f"；写入 {files_written} 个文件"
        return summary
    except Exception as e:  # noqa: BLE001
        logger.exception(f"skill_save 失败 key={key}")
        return f"skill_save 执行失败：{type(e).__name__}: {e}"


# ── 工具 3：skill_delete ───────────────────────────────────────────────────


@tool
async def skill_delete(
    key: Annotated[str, "要删除的技能 key"],
) -> str:
    """删除技能（DB 记录 + 所有文件）。builtin 不可删。"""
    from app.core.ctx import CTX_USER_ID

    try:
        uid = CTX_USER_ID.get() or None
        role_codes = await _get_role_codes(uid)

        skill = await AgentSkill.get_or_none(skill_key=key)
        if skill is None:
            return f"技能不存在：{key}"
        if skill.source == "builtin":
            return f"技能 {key} 是内置（builtin），不可删除"
        if not _can_manage(skill, uid, role_codes):
            return f"无权删除技能 {key}（非创建者且非超管）"

        # 删文件
        deleted_files = await AgentSkillFile.filter(skill_key=key).delete()
        # 删主记录
        await skill.delete()

        # 清理磁盘：用户 workspace 物化目录 + 全局目录历史残留
        await _purge_workspace_skill(key, uid)
        legacy_dir = _WORKSPACE_ROOT / ".agent_skills" / key
        if legacy_dir.exists():
            import shutil

            try:
                await asyncio.to_thread(shutil.rmtree, legacy_dir, True)
            except Exception:
                pass

        return f"已删除技能 @{key}（含 {deleted_files} 个文件）"
    except Exception as e:  # noqa: BLE001
        logger.exception(f"skill_delete 失败 key={key}")
        return f"skill_delete 执行失败：{type(e).__name__}: {e}"


# ── 工具 4：skill_install ──────────────────────────────────────────────────


@tool
async def skill_install(
    url: Annotated[
        str,
        "技能源地址：zip 包/SKILL.md 的 http(s) URL；或 workspace 内的相对路径"
        "（如 ./out/my-skill.zip，指向本地打包好的技能 zip 或 SKILL.md）",
    ],
    key: Annotated[Optional[str], "可选：技能名参考（最终 key 由平台按「专属code_技能名」规范自动生成，冲突自动加后缀）"] = None,
    is_public: Annotated[bool, "true=全员可见；false=仅当前用户私有（默认）"] = False,
) -> str:
    """安装技能（下载/读取 → 解析 → 入库）。支持 zip 包和单 SKILL.md。"""
    from app.core.ctx import CTX_USER_ID

    try:
        uid = CTX_USER_ID.get() or None

        if not url:
            return "url 必填"

        url = url.strip()
        is_remote = url.lower().startswith(("http://", "https://"))

        if is_remote:
            try:
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    content_type = resp.headers.get("content-type", "")
                    body = resp.content
            except Exception as e:  # noqa: BLE001
                return f"下载失败：{type(e).__name__}: {e}"
            source_url = url
        else:
            # 本地路径：限定在当前操作用户的 workspace 内，防越权读取
            if uid is None:
                return "本地路径安装需要登录态"
            ws_root = (_USERS_ROOT / str(uid)).resolve()
            candidate = (ws_root / url.lstrip("./")).resolve()
            try:
                candidate.relative_to(ws_root)
            except ValueError:
                return f"非法路径（越出工作区）：{url}"
            if not candidate.is_file():
                return f"本地文件不存在：{url}"
            body = await asyncio.to_thread(candidate.read_bytes)
            content_type = ""
            source_url = f"local://{url.lstrip('./')}"

        # 判断 zip 还是 raw SKILL.md
        is_zip = url.lower().endswith(".zip") or "zip" in content_type
        if is_zip:
            msg, _change = await _install_from_zip(body, source_url, key, is_public, uid)
            return msg
        else:
            text = body.decode("utf-8", errors="ignore")
            return await _install_from_md(text, source_url, key, is_public, uid)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"skill_install 失败 url={url}")
        return f"skill_install 执行失败：{type(e).__name__}: {e}"


async def _install_from_zip(
    zip_bytes: bytes,
    source_url: str,
    suggested_key: Optional[str],
    is_public: bool,
    uid: Optional[int],
    allow_upgrade: bool = False,
    source: str = "curated",
) -> tuple[str, dict]:
    """从 zip 字节流安装：在内存中解压，全部存入 DB。

    返回 (message, change)。change: {action, skillKey, name, oldVersion, newVersion, fileCount}；
    allow_upgrade=True 时，key 已存在且当前用户可管理（非 builtin）→ 原地升级（旧文件转历史版本），
    否则沿用加后缀新建的行为。source 标记来源（discovered=URL 发现安装 / uploaded=用户上传）。
    """

    def _extract() -> tuple[str, list[tuple[str, bytes]]]:
        """返回 (skill_md_content, [(path, content_bytes), ...])"""
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            # 找 SKILL.md
            skill_md_name = next(
                (n for n in names if n.lower().endswith("/skill.md") or n.lower() == "skill.md"),
                None,
            )
            if not skill_md_name:
                raise ValueError("zip 内未找到 SKILL.md")
            # 确定路径前缀（zip 可能有一层顶级目录）
            prefix = skill_md_name.rsplit("/", 1)[0] + "/" if "/" in skill_md_name else ""
            skill_md = zf.read(skill_md_name).decode("utf-8", errors="ignore")
            file_list: list[tuple[str, bytes]] = []
            for n in names:
                if n.endswith("/"):
                    continue
                rel = n[len(prefix):] if prefix and n.startswith(prefix) else n
                if not rel or ".." in rel.split("/"):
                    continue
                # 跳过隐藏目录（.versions, .bak 等）
                if any(p.startswith(".") for p in rel.split("/")):
                    continue
                file_list.append((rel, zf.read(n)))
            return skill_md, file_list

    try:
        skill_md, file_list = await asyncio.to_thread(_extract)
    except Exception as e:  # noqa: BLE001
        return f"解压失败：{e}", {"action": "failed", "skillKey": None, "name": None, "oldVersion": None, "newVersion": None, "fileCount": 0}

    from app.core.ctx import CTX_USER_ID

    fm = _parse_skill_md(skill_md)
    # key 规范：固定「专属code_技能名」，suggested_key 仅作名称参考
    base_key = await _build_user_skill_key(CTX_USER_ID.get() or uid, fm.get("name") or suggested_key or "skill")

    # ── 升级分支：allow_upgrade 且 key 已存在、可管理、非 builtin → 原地升级 ──
    existing = await AgentSkill.get_or_none(skill_key=base_key) if allow_upgrade else None
    if existing is not None and existing.source != "builtin" and _can_manage(existing, uid, await _get_role_codes(uid)):
        old_version = existing.version
        new_version = fm.get("version") or existing.version or "1.0.0"
        final_key = base_key

        existing.name = (fm.get("name") or final_key)[:64]
        if fm.get("description"):
            existing.description = fm["description"][:1000]
        existing.skill_md = skill_md
        existing.skill_pkg_keys = _extract_pkg_keys(skill_md) or None
        existing.version = new_version
        existing.source_url = source_url
        await existing.save()

        # 旧激活文件转历史版本，再写入新版本文件（同版本重传走 update 防唯一键冲突）
        await AgentSkillFile.filter(skill_key=final_key, is_active=True).update(is_active=False)
        for fpath, content_bytes in file_list:
            await AgentSkillFile.update_or_create(
                skill_key=final_key,
                path=fpath,
                version=new_version,
                defaults={
                    "content": content_bytes,
                    "size": len(content_bytes),
                    "is_binary": _is_binary_path(fpath),
                    "is_active": True,
                },
            )

        await _materialize_active_files(final_key, uid)

        change = {
            "action": "upgraded",
            "skillKey": final_key,
            "name": existing.name,
            "oldVersion": old_version,
            "newVersion": new_version,
            "fileCount": len(file_list),
        }
        msg = (
            f"已升级 @{final_key}（{existing.name}）· v{old_version or '?'} → v{new_version}\n"
            f"含 {len(file_list)} 个文件。现在可在对话中 @{final_key} 召唤。"
        )
        return msg, change

    # ── 新建分支：防冲突加后缀 ──
    final_key = await _alloc_skill_key(base_key)

    version = fm.get("version") or "1.0.0"

    # 创建主记录（skill_md 即 SKILL.md 全文）
    await AgentSkill.create(
        skill_key=final_key,
        name=(fm.get("name") or final_key)[:64],
        description=(fm.get("description") or "")[:1000] or None,
        skill_md=skill_md,
        version=version,
        source_url=source_url,
        source=source,
        user_id=None if is_public else uid,
        is_enabled=1,
        visibility="public" if is_public else "private",
    )

    # 写入文件
    for fpath, content_bytes in file_list:
        await AgentSkillFile.create(
            skill_key=final_key,
            path=fpath,
            content=content_bytes,
            size=len(content_bytes),
            is_binary=_is_binary_path(fpath),
            version=version,
            is_active=True,
        )

    await _materialize_active_files(final_key, uid)

    vis = "全员可见" if is_public else "仅你可见"
    msg = (
        f"已安装 @{final_key}（{fm.get('name') or final_key}）· v{version} · {vis}\n"
        f"含 {len(file_list)} 个文件。现在可在对话中 @{final_key} 召唤。"
    )
    change = {
        "action": "created",
        "skillKey": final_key,
        "name": fm.get("name") or final_key,
        "oldVersion": None,
        "newVersion": version,
        "fileCount": len(file_list),
    }
    return msg, change


async def _install_from_md(
    text: str,
    source_url: str,
    suggested_key: Optional[str],
    is_public: bool,
    uid: Optional[int],
    source: str = "curated",
) -> str:
    """从单个 SKILL.md 文本安装（无附属文件）。"""
    from app.core.ctx import CTX_USER_ID

    fm = _parse_skill_md(text)
    # key 规范：固定「专属code_技能名」，suggested_key 仅作名称参考
    base_key = await _build_user_skill_key(CTX_USER_ID.get() or uid, fm.get("name") or suggested_key or "skill")
    final_key = await _alloc_skill_key(base_key)

    await AgentSkill.create(
        skill_key=final_key,
        name=(fm.get("name") or final_key)[:64],
        description=(fm.get("description") or "")[:1000] or None,
        skill_md=text,
        source_url=source_url,
        source=source,
        user_id=None if is_public else uid,
        is_enabled=1,
        visibility="public" if is_public else "private",
    )

    # SKILL.md 本身也存一份到文件表（方便物化时重建目录结构）
    content_bytes = text.encode("utf-8")
    await AgentSkillFile.create(
        skill_key=final_key,
        path="SKILL.md",
        content=content_bytes,
        size=len(content_bytes),
        is_binary=False,
        version=fm.get("version") or "1.0.0",
        is_active=True,
    )

    await _materialize_active_files(final_key, uid)

    vis = "全员可见" if is_public else "仅你可见"
    return f"已安装 @{final_key}（{fm.get('name') or final_key}）· {vis}\n现在可在对话中 @{final_key} 召唤。"


# ── 工具 5：skill_list ─────────────────────────────────────────────────────


@tool
async def skill_list(
    keyword: Annotated[Optional[str], "可选：按 key/名称/描述模糊过滤"] = None,
) -> str:
    """列出当前用户可见的已启用技能（DB 技能 + 磁盘内置技能），用于创建前查重、更新前找 key、了解可用能力。"""
    from app.core.ctx import CTX_USER_ID

    try:
        uid = CTX_USER_ID.get() or None
        role_codes = await _get_role_codes(uid)
        is_super = "R_SUPER" in role_codes

        rows = await AgentSkill.filter(is_enabled=1).order_by("id")
        lines: list[str] = []
        seen_keys: set[str] = set()
        kw = (keyword or "").strip().lower()
        for s in rows:
            if s.source == "builtin":
                continue
            if not is_super:
                vis = (s.visibility or "private").lower()
                if vis == "private" and s.user_id != uid:
                    continue
                if vis == "role":
                    allowed = s.allowed_role_codes or []
                    if not any(rc in role_codes for rc in allowed) and s.user_id != uid:
                        continue
            if kw and kw not in f"{s.skill_key} {s.name} {s.description or ''}".lower():
                continue
            owner = "公共" if s.user_id is None else ("我的" if s.user_id == uid else "他人")
            has_files = await AgentSkillFile.filter(skill_key=s.skill_key, is_active=True).exists()
            lines.append(
                f"- @{s.skill_key} | {s.name} | {owner}/{s.visibility or 'private'} | "
                f"source={s.source} | v{s.version or '-'} | {'含文件' if has_files else '纯prompt'} | "
                f"{(s.description or '')[:60]}"
            )
            seen_keys.add(s.skill_key)

        # 磁盘内置技能（未入库，如 skill-creator / office-cli）：对所有人可见、只读
        def _builtin_lines() -> list[str]:
            out: list[str] = []
            for key, d in _iter_builtin_skill_dirs().items():
                if key in seen_keys:
                    continue
                try:
                    fm = _parse_skill_md((d / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
                except Exception:  # noqa: BLE001
                    fm = {}
                name = fm.get("name") or key
                desc = fm.get("description") or ""
                if kw and kw not in f"{key} {name} {desc}".lower():
                    continue
                out.append(f"- @{key} | {name} | 系统内置 | source=builtin(disk) | 只读 | {desc[:60]}")
            return out

        lines.extend(await asyncio.to_thread(_builtin_lines))

        if not lines:
            return f"没有可见的技能{f'（关键词：{keyword}）' if kw else ''}"
        return f"共 {len(lines)} 个可见技能：\n" + "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        logger.exception("skill_list 失败")
        return f"skill_list 执行失败：{type(e).__name__}: {e}"


# ── 导出 ───────────────────────────────────────────────────────────────────

SKILL_TOOLS = [skill_read, skill_save, skill_delete, skill_install, skill_list]

__all__ = ["SKILL_TOOLS"]
