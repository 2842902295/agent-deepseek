"""
通用标准问答 API

支持多轮流式对话，Agent 可自主查询数据库回答用户问题。
会话与消息持久化到 agent_session / agent_message。
"""

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import time
from collections import OrderedDict
from pathlib import Path
from typing import AsyncGenerator, Optional, Union

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from tortoise.expressions import Q

from app.api.v1.ai.agent_skill import build_skill_injection, get_current_role_codes, resolve_skills_from_text
from app.api.v1.ai.role_tier import resolve_user_tier_codes, tier_allows, tier_cache_part
from app.core.ctx import CTX_USER_ID
from app.langchain.agents.qa_agent import create_qa_agent
from app.models.standard.agent import AgentMessage, AgentSession
from app.schemas.base import Fail, Success
from app.services.agent_runtime.call_context import (
    AgentCallContext,
    clear_agent_call_context,
    set_agent_call_context,
)
from app.services.agent_runtime.timeline_sanitize import sanitize_tool_args, sanitize_tool_result
from app.services.agent_runtime.tool_display_names import get_tool_display_name

router = APIRouter(prefix="/qa", tags=["标准问答"])

# 图片后缀集合：用于识别附件是否是图片
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp")
# 光栅图片扩展名：多模态模型真正能「看」的位图格式（svg 是文本格式，走 read_file）
_RASTER_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")
# 过大的视频不直传（读进内存 + 上传 + 模型拉取都会超时）
_MAX_VIDEO_INLINE_BYTES = 100 * 1024 * 1024
# 图片预解读：单图超时（秒）与原图大小上限（超过不解读，防 413/超时）
_IMAGE_PRIME_TIMEOUT_S = 90.0
_IMAGE_PRIME_MAX_BYTES = 20 * 1024 * 1024

# 活跃任务取消令牌：session_key -> asyncio.Event（用于停止后台任务）
_active_task_cancellations: dict[str, asyncio.Event] = {}

# 后台回合 task 的强引用表：asyncio 只在事件循环里持弱引用，create_task 后不存
# 结果的 task 可能被 GC 中途回收（官方文档明确警告）。回合 task 生命周期长达
# 数十分钟，绝不能冒这个险——启动时 add，done 回调自动 discard。
_background_tasks: set[asyncio.Task] = set()

# ── Workspace 与 Skill 路径 ───────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
_WORKSPACE_ROOT = _PROJECT_ROOT / ".agent_workspace"
_GLOBAL_SKILLS_DIR = _WORKSPACE_ROOT / ".agent_skills"
_PROJECT_SKILLS_SUBDIR = "_project"
_SESSIONS_ROOT = _WORKSPACE_ROOT / "sessions"  # 兼容旧路径，仅用于 get_session_workspace 兜底


def _user_workspace(user_id: Optional[int]) -> Path:
    """用户持久工作目录：users/{user_id}/，沙箱根，跨 session 共享。

    口径唯一真相源：call_context.default_user_workspace（HTTP 桥兜底同用）。
    """
    from app.services.agent_runtime.call_context import default_user_workspace

    ws = default_user_workspace(user_id)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _session_tmp_dir(user_workspace: Path, session_key: str) -> Path:
    """session 临时目录：users/{user_id}/sessions/{session_key}/，存本次会话中间产物。"""
    d = user_workspace / "sessions" / session_key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _iter_global_skill_dirs() -> dict[str, Path]:
    """扫两类 skill 目录，返回 {skill_key: 源目录}：
    - 顶层 .agent_skills/<key>/         portable skill（可跨项目移植）
    - .agent_skills/_project/<key>/     project-bound skill（耦合本项目工具）

    skill_key 不带 _project/ 前缀——agent metadata 看到的就是 key 本身。
    若两类 skill 同名，project-bound 优先（因为耦合方需求更具体）。
    """
    out: dict[str, Path] = {}
    if not _GLOBAL_SKILLS_DIR.is_dir():
        return out

    # 1) 顶层 portable
    for entry in _GLOBAL_SKILLS_DIR.iterdir():
        if entry.is_dir() and not entry.name.startswith(".") and entry.name != _PROJECT_SKILLS_SUBDIR:
            out[entry.name] = entry

    # 2) _project/ 下 project-bound（覆盖同名顶层）
    project_dir = _GLOBAL_SKILLS_DIR / _PROJECT_SKILLS_SUBDIR
    if project_dir.is_dir():
        for entry in project_dir.iterdir():
            if entry.is_dir() and not entry.name.startswith("."):
                out[entry.name] = entry
    return out


def _ensure_session_workspace(session_key: str, user_id: Optional[int] = None) -> Path:
    """返回用户持久工作目录（沙箱根）。session_key 仅用于创建临时子目录，不作为根。"""
    return _user_workspace(user_id)


def get_session_workspace(session_key: str, user_id: Optional[int] = None) -> Path:
    """外部模块拿 session 工作目录的入口。"""
    return _ensure_session_workspace(session_key, user_id)


async def _get_visible_skill_keys(user_id: Optional[int], user_codes: frozenset = frozenset()) -> set[str]:
    """当前用户「我的技能」里应加载的 skill_key 集合（商店模型，与 @ 调用口径一致）：
    - 内置 skill：直接落在 .agent_skills/ 或 .agent_skills/_project/ 下、未登记到 DB 的目录，所有用户默认可用
    - DB 登记的：「已上架（is_enabled=1）或本人创建」且 档位足够（builtin 豁免、作者恒见自己实体），
      再按个人偏好过滤：
      有记录 → 禁用（is_enabled=0）或未添加（is_added=0）的剔除；
      无记录 → 默认未添加，仅本人创建的技能与 builtin（如「编辑」）保留

    user_codes：调用方已算好的用户档位集合（管理员为 MANAGER_CODES 哨兵）。
    """
    # 函数级惰性 import：agent_skill 顶层导入了 qa，不能反向顶层引用
    from app.api.v1.ai.agent_skill import _default_added
    from app.models.standard.agent import AgentSkill

    # 1) 磁盘上所有 skill 目录（顶层 portable + _project/ 下 project-bound）
    builtin: set[str] = set(_iter_global_skill_dirs().keys())

    # 2) DB 里有记录的：已上架或本人创建（统一尺子，与列表/@ 弹层同一把）
    q = Q(is_enabled=1)
    if user_id is not None:
        q = q | Q(user_id=user_id)
    db_visible: dict[str, AgentSkill] = {}
    db_known: set[str] = set()  # 所有 DB 中有记录的 key（无论可见与否）
    rows = await AgentSkill.filter(q).all()
    for r in rows:
        db_known.add(r.skill_key)
        db_visible[r.skill_key] = r  # 保留行对象：判「已添加」需要 user_id / source

    # 2.5) 个人偏好与缺省口径（与 resolve_skills_from_text 完全一致）：
    # 有记录按记录来；无记录默认未添加，仅本人创建的技能与 builtin 保留。
    # 磁盘内置技能无 DB 记录，不受个人偏好影响。
    pref_map: dict = {}
    if user_id is not None:
        from app.models.standard.agent import AgentSkillUserPref

        prefs = await AgentSkillUserPref.filter(user_id=user_id).all()
        pref_map = {p.skill_key: p for p in prefs}
    added_keys: set[str] = set()
    for key, r in db_visible.items():
        # 档位过滤：不够档的技能不加载（builtin 豁免；作者恒见自己实体在 tier_allows 内）
        if r.source != "builtin" and not await tier_allows(r.min_tier_code, r.user_id, user_id, user_codes):
            continue
        p = pref_map.get(key)
        if p is not None:
            if p.is_enabled and p.is_added:
                added_keys.add(key)
        elif r.source == "builtin" or _default_added(r, user_id):
            added_keys.add(key)

    # 3) 内置 skill = 磁盘存在但 DB 里完全没记录的；这部分所有人可见
    builtin_only = builtin - db_known

    return builtin_only | added_keys


def _sync_session_skills(workspace: Path, allowed_keys: set[str], target_dirname: str = ".agent_skills") -> None:
    """把全局 .agent_skills/ 增量硬链到 workspace/<target_dirname>/，按 allowed_keys 过滤。

    target_dirname 两态：
    - ".agent_skills"          用户手动添加 + 磁盘内置（主技能面）
    - ".agent_expert_skills"   会话召唤专家的绑定技能（运行时并集，不落个人偏好）

    - 已是同一 inode 的文件直接跳过
    - 源文件被改/被替换：重建硬链
    - 源文件不存在但目标还在：删除目标
    - allowed_keys 之外的本地 skill 目录：整个删除
    """
    target_root = workspace / target_dirname
    # 防御：旧版本可能在这里建了 symlink，必须先 unlink，否则下方 iterdir/rmtree 会
    # 顺着软链操作到全局 skill 目录，造成误删！
    if target_root.is_symlink() or (target_root.exists() and not target_root.is_dir()):
        try:
            target_root.unlink()
        except OSError as e:
            logger.warning(f"清理旧 {target_dirname} 软链失败：{e}")
    target_root.mkdir(parents=True, exist_ok=True)

    # 1) 删除不再允许的 skill 目录
    for entry in target_root.iterdir():
        if entry.is_dir() and entry.name not in allowed_keys:
            shutil.rmtree(entry, ignore_errors=True)

    # 2) 对每个允许的 skill，从全局源（顶层或 _project/ 下）硬链同步
    src_map = _iter_global_skill_dirs()
    for key in allowed_keys:
        src_dir = src_map.get(key)
        if src_dir is None or not src_dir.is_dir():
            # 全局目录里不存在 → 可能是 DB 物化技能，不动它（由 _materialize_from_db 管理）
            continue
        _hardlink_tree(src_dir, target_root / key)


def _hardlink_tree(src: Path, dst: Path) -> None:
    """递归硬链：把 src 下所有文件硬链到 dst（按 inode 比对，增量同步）。"""
    dst.mkdir(parents=True, exist_ok=True)

    src_files: dict[Path, Path] = {}
    for p in src.rglob("*"):
        if p.is_file():
            src_files[p.relative_to(src)] = p

    # 删掉目标里源已不存在的文件
    for p in list(dst.rglob("*")):
        if p.is_file() and p.relative_to(dst) not in src_files:
            try:
                p.unlink()
            except OSError:
                pass

    # 建/更新硬链
    for rel, src_file in src_files.items():
        dst_file = dst / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        if dst_file.exists():
            try:
                if os.path.samefile(dst_file, src_file):
                    continue
            except OSError:
                pass
            try:
                dst_file.unlink()
            except OSError:
                pass
        try:
            os.link(src_file, dst_file)
        except OSError as e:
            logger.warning(f"硬链失败 {src_file} -> {dst_file}: {e}，回退复制")
            try:
                shutil.copy2(src_file, dst_file)
            except OSError as ce:
                logger.error(f"复制也失败：{ce}")


# ── 共享资源（模块级懒加载，所有会话共用） ────────────────────────────────────

_shared_resources: Optional[dict] = None
_shared_lock = asyncio.Lock()


async def _get_shared_resources() -> dict:
    """MCP tools / memory store — 重资源，全局共享一份。"""
    global _shared_resources
    if _shared_resources is None:
        async with _shared_lock:
            if _shared_resources is None:
                from app.langchain.mcp_client import get_mcp_tools
                from app.langchain.memory_store import get_memory_store

                _shared_resources = {
                    "mcp_tools": await get_mcp_tools(),
                    "store": await get_memory_store(),
                }
    return _shared_resources


# ── 按用户缓存 Agent（LRU，maxsize=50） ───────────────────────────────────────
#
# agent 实例对 session 完全无状态：workspace 是用户级（users/{uid}/），对话状态在
# checkpointer（按 thread_id 索引，经 config 传入），工具上下文走 per-request 的
# ContextVar。因此按用户缓存——同一用户的全部会话（案例加载、新对话、切会话）共享
# 一个实例；此前按 session_key 缓存，每个新会话首次请求都要全量重建（数秒级）。
#
# 注意：_agent_cache 用 per-key lock，避免「A 用户在新建 agent 时全局阻塞 B 用户读缓存」。
# 全局只在做 LRU 维护时短暂持锁。

_AGENT_CACHE_MAX = 50
_agent_cache: "OrderedDict[str, object]" = OrderedDict()
_agent_lru_lock = asyncio.Lock()
_agent_build_locks: dict[str, asyncio.Lock] = {}
_agent_build_locks_lock = asyncio.Lock()

# skill 同步触发（全量硬链遍历实测秒级~十余秒，绝不能常态化卡在消息路径上）：
# - 24h 周期兜底：只覆盖「绕过系统直接改磁盘文件」的低频场景（开发调试）；
#   后端重启后内存表清空，首条消息必命中代数判定，天然全量重对一遍
# - 显式失效（invalidate_skill_sync）：本人操作技能/专家绑定 → 下一条消息 await 就地同步
# - 全局代数（bump_skill_gen）：已上架技能的内容/可见性变化 → 所有用户下一条消息
#   后台重同步（不阻塞消息；对他人有一条消息的滞后窗口，可见性是软约束可接受）
_SKILL_SYNC_INTERVAL = 86400.0
_skill_last_sync: dict[Path, float] = {}
# 显式失效集：技能保存/删除/偏好变更/专家换绑等端点在此登记——这些是低频事件，
# 下一条消息必须看到最新技能面，同步在消息路径上 await（一次性等待可接受）。
_skill_force_sync: set[Path] = set()
# 后台同步任务登记表（str(ws) → task）：同一 workspace 同时只允许一个遍历在跑
_skill_sync_tasks: dict[str, "asyncio.Task[None]"] = {}
# 已开始走目录树的后台同步（str(ws)）：仍在延迟睡眠里的任务可被强制同步直接取消，
# 已开走的只能 shield 等它收尾（避免两轮遍历并发操作同一棵树）
_skill_sync_started: set[str] = set()
# 后台分派的延迟起跑秒数：让冷启动构建（dsh spawn + MCP 握手）与首回合先跑完，
# 弱服务器上目录遍历与构建抢 IO 会把首 token 明显拖长（实测 38s 遍历与构建重叠）
_BG_SYNC_DELAY = 15.0
# 全局技能面代数：任何「他人可见技能」的写入 +1；每 workspace 记录上次同步时的代数，
# 不一致即触发后台重同步。进程内存计数（run.py workers=1 单进程部署成立）。
_skill_global_gen = 0
_skill_ws_gen: dict[Path, int] = {}


def bump_skill_gen() -> None:
    """全局失效技能面：已上架技能的内容 / 可见性 / 激活文件集变化后调用。

    所有用户的下一条消息触发后台重同步（不阻塞）；操作者本人请配合
    invalidate_skill_sync（面板路径 force await / 对话路径 bg 均可）。
    """
    global _skill_global_gen
    _skill_global_gen += 1


def invalidate_skill_sync(ws: Path, force: bool = True) -> None:
    """失效某 workspace 的技能同步节流。

    force=True：下一条消息在路径上 await 同步完成（面板操作/专家换绑——本人必须
    立即看到结果）；force=False：仅弹出时间戳，下一条消息触发后台同步（对话中
    agent 存删技能——新文件已由 edit_tools 即时物化，不值得让流畅对话挨一刀 await）。
    """
    _skill_last_sync.pop(ws, None)
    if force:
        _skill_force_sync.add(ws)


def notify_skill_changed(uid: Optional[int], *, affects_others: bool = False, force: bool = True) -> None:
    """技能写操作后的统一同步失效钩子（面板端点 / agent 工具共用）。

    - affects_others=True：已上架技能的内容/可见性/激活文件集变了 → 全局代数 +1，
      其他所有用户下一条消息后台重同步（不阻塞消息）
    - uid：操作者本人失效自己的 workspace（force 语义见 invalidate_skill_sync）
    兜底周期已拉到 24h，「改动实时到位」完全靠本钩子覆盖全部写入口。
    """
    try:
        if affects_others:
            bump_skill_gen()
        if uid:
            invalidate_skill_sync(_user_workspace(uid), force=force)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[qa] 技能同步失效失败（不影响写入）: {e}")


async def _get_or_create_build_lock(cache_key: str) -> asyncio.Lock:
    async with _agent_build_locks_lock:
        lock = _agent_build_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            _agent_build_locks[cache_key] = lock
        return lock


# ── MCP 连接器：生效集 + 缓存签名 ────────────────────────────────────────────


async def _get_effective_connectors(user_id: Optional[int], user_codes: frozenset = frozenset()):
    """用户的 MCP 连接器生效集（缓存签名与工具加载共用此函数，口径不漂移）：

    （已上架 or 本人创建）且 档位足够 且（有偏好行 → is_added 且 is_enabled；
    无行 → 缺省未添加，本人创建除外默认已添加）。返回 (rows, pref_map, cred_map)；
    cred_map = {connector_key: 个人凭据行}（凭据一人一份，加载时本人凭据 > 共享凭据）。

    user_codes：调用方已算好的用户档位集合（管理员为 MANAGER_CODES 哨兵）。
    """
    from app.models.standard.agent import AgentConnector, AgentConnectorCredential, AgentConnectorUserPref

    if user_id is None:
        return [], {}, {}
    rows = await AgentConnector.filter(Q(is_enabled=1) | Q(user_id=user_id))
    pref_map = {p.connector_key: p for p in await AgentConnectorUserPref.filter(user_id=user_id)}
    cred_map = {r.connector_key: r for r in await AgentConnectorCredential.filter(user_id=user_id)}
    out = []
    for c in rows:
        p = pref_map.get(c.connector_key)
        if p is not None:
            if not (p.is_added and p.is_enabled):
                continue
        elif c.user_id != user_id:
            continue  # 无偏好行 = 未添加（本人创建的连接器默认已添加）
        # 档位过滤：不够档的连接器不加载（作者恒见自己实体在 tier_allows 内）
        if not await tier_allows(c.min_tier_code, c.user_id, user_id, user_codes):
            continue
        out.append(c)
    return out, pref_map, cred_map


async def _connector_cache_part(user_id: Optional[int], user_codes: frozenset = frozenset()) -> str:
    """生效连接器集签名（编进 agent cache_key）：增删改/加减移除/启停/凭据变化都换 key → 自动重建。"""
    rows, pref_map, cred_map = await _get_effective_connectors(user_id, user_codes)
    if not rows:
        return "_x0"
    parts = []
    for c in rows:
        ct = int(c.update_time.timestamp()) if c.update_time else 0
        p = pref_map.get(c.connector_key)
        pt = int(p.update_time.timestamp()) if (p is not None and p.update_time) else 0
        cred = cred_map.get(c.connector_key)
        kt = int(cred.update_time.timestamp()) if (cred is not None and cred.update_time) else 0
        parts.append(f"{c.connector_key}:{ct}:{pt}:{kt}")
    parts.sort()
    return "_x" + hashlib.md5("|".join(parts).encode()).hexdigest()[:10]


# ── 会话专家：@召唤驻留绑定（agent_session.expert_key） ─────────────────────


_EXPERT_SKILLS_DIRNAME = ".agent_expert_skills"


async def _get_session_expert(session_key: Optional[str], user_id: Optional[int], user_codes: Optional[frozenset] = None):
    """解析会话当前驻留的专家行（不存在/被删/被本人禁用/档位不足 → None，优雅降级为通用会话）。

    user_codes：调用方已算好的用户档位集合；None 时由 get_effective_expert 内部兜底现算。
    """
    if not session_key:
        return None
    s = await AgentSession.get_or_none(session_key=session_key, is_deleted=0)
    if s is None or not s.expert_key:
        return None
    from app.api.v1.ai.agent_expert import get_effective_expert

    return await get_effective_expert(user_id, s.expert_key, user_codes)


def _expert_cache_part(expert_row) -> str:
    """会话专家签名（编进 agent cache_key）：切换/移除专家、专家定义变化都换 key → 自动重建。"""
    if expert_row is None:
        return "_e0"
    ts = int(expert_row.update_time.timestamp()) if expert_row.update_time else 0
    return f"_e{expert_row.id}_{ts}"


# fire-and-forget 的 agent 关闭任务引用（防 GC 提前回收 task）
_agent_close_tasks: "set[asyncio.Task]" = set()


def _schedule_agent_close(agents: list) -> None:
    """best-effort 关闭被弹出的 dsh agent 子进程（fire-and-forget）。

    同步函数，可在任意上下文调用：有事件循环则丢线程池异步关；无循环（同步脚本）
    静默跳过（与旧行为一致，靠 GC 兜底）。关闭失败只记日志不抛出。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for a in agents:
        _close = getattr(a, "close", None)
        if not callable(_close):
            continue

        async def _do_close(_c=_close):
            try:
                await asyncio.to_thread(_c)
            except Exception as _ce:  # noqa: BLE001
                logger.warning(f"关闭被弹出 agent 失败（忽略）: {_ce}")

        t = loop.create_task(_do_close())
        _agent_close_tasks.add(t)
        t.add_done_callback(_agent_close_tasks.discard)


def _evict_user_agents(uid: Optional[int]) -> None:
    """弹出该用户全部形态的缓存 agent（连接器写端点 / 对话偏好切换调用）。

    cache_key 形如 u{uid}[_s]{profile}_x{sig}：按 `u{uid}` 精确或 `u{uid}_` 前缀匹配，
    不会误伤 u{uid}0/ u{uid}1 等更长 uid（边界是下划线）。
    弹出的实例 best-effort 关闭 dsh 子进程（对话模式切换是用户高频操作，只 pop
    靠 GC 会泄漏运行时子进程）。
    """
    if uid is None:
        return
    try:
        exact = f"u{uid}"
        keys = [k for k in _agent_cache if k == exact or k.startswith(exact + "_")]
        evicted = []
        for k in keys:
            a = _agent_cache.pop(k, None)
            if a is not None:
                evicted.append(a)
            _agent_build_locks.pop(k, None)
        _schedule_agent_close(evicted)
    except Exception as e:
        logger.warning(f"弹出用户 agent 缓存失败（不影响写入）: {e}")


def uid_has_cached_agent(uid: Optional[int]) -> bool:
    """该用户是否有任一形态的缓存 agent 实例（cache_key 前缀口径同 _evict_user_agents）。

    页面打开预热用：已有热实例就不必再触发预热循环（省一次 DB 查询与缓存键重算）。
    """
    if uid is None:
        return False
    exact = f"u{uid}"
    return any(k == exact or k.startswith(exact + "_") for k in _agent_cache)


# ── 对话配置切换后的后台预热 ────────────────────────────────────────────────
#
# PUT /ai/chat-mode 会弹出该用户全部 agent 形态（dsh 子进程随之关闭）。不预热的话，
# 下一条消息要付完整冷启动（dsh spawn + MCP 握手 + 技能同步，实测 8~12s）。预热 =
# 后台按用户最近会话的形态重建；消息到达直接命中热实例。代数计数处理「连续切换」：
# 构建完成后发现配置又变了，弹出陈旧形态再来一轮（不 cancel——to_thread 构建中途
# 取消会把已 spawn 的 dsh 子进程变成没人关的孤儿）。

_prewarm_gen: dict[int, int] = {}
_prewarm_tasks: dict[int, "asyncio.Task[None]"] = {}


def prewarm_user_agent(uid: Optional[int]) -> None:
    """后台重建该用户的 agent（best-effort，失败静默）。

    两个触发口：① PUT /ai/chat-mode 弹出缓存后；② GET /ai/chat-mode（QA 页面打开）
    发现无热实例时——后端重启后用户打开页面即预热，首条消息不再付冷启动
    （dsh spawn + MCP 握手，实测 8~14s）。
    """
    if not uid:
        return
    gen = _prewarm_gen[uid] = _prewarm_gen.get(uid, 0) + 1
    t = _prewarm_tasks.get(uid)
    if t is not None and not t.done():
        return  # 在跑的预热结束后会发现代数变化，自行弹出陈旧形态并重建
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_prewarm_loop(uid, gen))
    _prewarm_tasks[uid] = task
    task.add_done_callback(lambda _t: _prewarm_tasks.pop(uid, None) if _prewarm_tasks.get(uid) is _t else None)


async def _prewarm_loop(uid: int, gen: int) -> None:
    while True:
        try:
            # 按最近活跃会话的形态预热（含其驻留专家）；无会话则预热通用形态
            sess = await AgentSession.filter(user_id=uid, is_deleted=0).order_by("-update_time").first()
            session_key = sess.session_key if sess else f"prewarm_u{uid}"
            t0 = time.monotonic()
            await _get_agent_for_session(session_key, uid)
            dt = (time.monotonic() - t0) * 1000
            if _prewarm_gen.get(uid) == gen:
                logger.info(f"[perf] agent 预热完成 uid={uid} session={session_key} 耗时 {dt:.0f}ms")
                return
            # 预热期间用户又切换了：刚建的形态已陈旧。无在途回合时弹出全部形态
            # （陈旧 dsh 子进程随之关闭）；有在途回合则跳过——evict 的 close 会杀掉
            # 回合正在使用的运行时，孤儿形态留给下次切换/LRU 回收
            _busy = False
            try:
                from app.mcp_bridge.dsh_http_bridge import get_active_turn

                _busy = bool(get_active_turn(uid))
            except Exception:  # noqa: BLE001
                _busy = False
            if not _busy:
                _evict_user_agents(uid)
            else:
                logger.info(f"[qa] 预热被取代但该用户有在途回合，跳过弹出 uid={uid}")
            gen = _prewarm_gen.get(uid, gen)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qa] agent 预热失败 uid={uid}（忽略，消息到达时冷启动兜底）: {e}")
            return


async def _run_skill_sync(ws: Path, user_id: Optional[int], _tier_codes, session_expert, delay: float = 0.0) -> None:
    """全量技能面同步一轮：可见技能硬链进 workspace + DB 技能物化 + 专家绑定面。

    开销实测秒级~数十秒（数万文件双 stat 遍历），因此只有「显式失效 / 首次」
    才允许 await 在消息路径上；周期性到期由调用方丢后台（见 _skill_force_sync 注释）。
    delay>0：后台分派先睡再走——避免目录树遍历与冷启动构建（dsh spawn + MCP 握手）
    抢 CPU/磁盘（弱服务器上实测把首 token 拖长）。睡眠期间强制同步可直接取消本任务。
    失败不更新时间戳与失效标记 → 下一条消息重试。
    """
    if delay > 0:
        await asyncio.sleep(delay)
    _skill_sync_started.add(str(ws))
    t0 = time.monotonic()
    # 开始时的全局代数：同步中途有人 bump（allowed 已算完、改动不在本轮）→
    # 结束时记旧代数 → 下一条消息代数不等再跑一轮，不漏改动
    gen0 = _skill_global_gen
    try:
        allowed = await _get_visible_skill_keys(user_id, user_codes=_tier_codes)
        await asyncio.to_thread(_sync_session_skills, ws, allowed)
        # DB 技能物化到同一目录（agent_skill_file → .agent_skills/<key>/）
        from app.api.v1.ai.agent_skill import _materialize_from_db

        db_keys = [k for k in allowed if k not in _iter_global_skill_dirs()]
        if db_keys:
            await _materialize_from_db(db_keys, ws)
        # 专家绑定技能 → 独立的每专家目录（运行时并集，不落个人 is_added；
        # 专家换绑时 _ensure_session / 会话端点已失效节流，这里即时重算）。
        # 并入前做可见性复核：绑定关系不回溯校验（与「绑定未上架技能」同政策），
        # 运行时对不够档/已下架的用户不生效；全被滤掉时传空集清旧目录
        if session_expert is not None and session_expert.skill_keys:
            from app.api.v1.ai.agent_skill import filter_visible_skill_keys

            exp_keys = await filter_visible_skill_keys(session_expert.skill_keys, user_id, _tier_codes)
            await asyncio.to_thread(_sync_session_skills, ws, exp_keys, _EXPERT_SKILLS_DIRNAME)
            exp_db_keys = [k for k in exp_keys if k not in _iter_global_skill_dirs()]
            if exp_db_keys:
                await _materialize_from_db(exp_db_keys, ws, target_dirname=_EXPERT_SKILLS_DIRNAME)
        _skill_last_sync[ws] = time.monotonic()
        _skill_ws_gen[ws] = gen0
        _skill_force_sync.discard(ws)
        logger.info(f"[perf] 技能同步完成 uid={user_id} 耗时 {(time.monotonic() - t0) * 1000:.0f}ms")
    except Exception as e:
        logger.warning(f"同步 skill 失败（{e}），继续用现有状态")
    finally:
        _skill_sync_started.discard(str(ws))


async def _get_agent_for_session(session_key: str, user_id: Optional[int]):
    """按用户取/建 agent。workspace 为用户持久目录，skill 增量同步到其中（节流）。

    agent 形态由「用户级配置 + 会话驻留专家」共同决定：专家编进 cache_key，
    同一用户不同专家的会话各缓存一个实例。
    """
    ws = _user_workspace(user_id)
    _session_tmp_dir(ws, session_key)  # 确保 session 临时目录存在

    # 超管判定 + 角色预取（上移到一切判定之前：档位集合贯穿专家生效判定 /
    # 技能面同步 / 连接器生效集 / cache_key）：
    # - profile 编进 cache_key（配置 / 角色变化 → key 变 → agent 自动重建对应形态）
    # - 档位集合（tier_cache_part）同样编进 key：用户挂/摘标记角色 → key 变 → 自动重建，
    #   可见性收紧/放开即时生效
    _is_super = False
    _role_objs: list = []
    if user_id is not None:
        from app.models.system.admin import User

        _u = await User.get_or_none(id=user_id).prefetch_related("by_user_roles")
        if _u:
            _role_objs = list(_u.by_user_roles)
            _is_super = any(r.role_code == "R_SUPER" for r in _role_objs)
    # 管理员（R_SUPER/R_ADMIN）：向量库管理工具组门控（普通用户的 dsh 实例不挂载）
    _is_admin = any(r.role_code in ("R_SUPER", "R_ADMIN") for r in _role_objs)
    _tier_codes = await resolve_user_tier_codes([r.role_code for r in _role_objs], _is_admin)

    # 会话驻留专家（@召唤绑定 agent_session.expert_key）：
    # 决定专家技能面同步、cache_key 与构建期人设注入
    session_expert = await _get_session_expert(session_key, user_id, user_codes=_tier_codes)

    # skill 同步触发分流（三档语义见 _SKILL_SYNC_INTERVAL / _skill_force_sync /
    # bump_skill_gen 注释）：显式失效或首次 → await；全局代数变化 / 24h 兜底 → 后台
    _first_sync = not (ws / ".agent_skills").is_dir()
    _due = ws not in _skill_last_sync or (time.monotonic() - _skill_last_sync[ws]) > _SKILL_SYNC_INTERVAL
    if _first_sync or ws in _skill_force_sync or _due or _skill_ws_gen.get(ws, -1) != _skill_global_gen:
        _bg = _skill_sync_tasks.get(str(ws))
        if _first_sync or ws in _skill_force_sync:
            if _bg is not None and not _bg.done():
                if str(ws) not in _skill_sync_started:
                    # 后台任务还在延迟睡眠里、没碰目录树 → 直接取消，本条消息立即同步
                    _bg.cancel()
                else:
                    # 已在走树：等它收尾，避免两轮同步并发操作同一棵目录树
                    try:
                        await asyncio.wait_for(asyncio.shield(_bg), timeout=60)
                    except Exception:  # noqa: BLE001
                        pass
            await _run_skill_sync(ws, user_id, _tier_codes, session_expert)
        elif _bg is None or _bg.done():
            _skill_sync_tasks[str(ws)] = asyncio.create_task(_run_skill_sync(ws, user_id, _tier_codes, session_expert, delay=_BG_SYNC_DELAY))

    # 按角色模型配置 profile 解析，放在缓存查找之前（角色预取已上移到函数开头）：
    # - profile 编进 cache_key（配置 / 角色变化 → key 变 → agent 自动重建对应形态）
    # - 同步 set 请求级上下文（CTX_PROFILE + CTX_GEN_BLOCK_OVERRIDE）：本次请求的
    #   运行期读取（附件视觉判定 / 生成能力门卫）与 agent 构建（CTX 随
    #   asyncio.to_thread 拷贝进构建线程）都依赖它。命中缓存也必须每次设置。
    from app.langchain.role_model_profile import apply_gen_override, resolve_profile_for_roles, set_current_profile

    _profile = await resolve_profile_for_roles(_role_objs)

    # 对话模式（用户偏好，见 chat_mode.py）：所选模式配了有效 chat 块则覆盖 profile
    # 的 chat 块与视觉判定；思考强度经 create_qa_agent → 回环代理 level 路由生效。
    # ⚠️ replace 必须在 set_current_profile 之前——否则 effective_chat_supports_vision
    # 读到旧块，图片直传 / vision_inspect 分流出错。
    # 兜底链：模式块（配置且有效）→ 角色 profile.chat_block_key → 全局激活块；
    # fallback_block 传角色块，level 归一（块档位白名单 + 平滑迁移）对准真实生效块。
    # 匿名/系统路径 resolve 返回不干预（mode=""，block/level=None），现状不变。
    from app.langchain.chat_mode import resolve_user_chat_pref

    _chat_mode, _mode_block, _chat_level = await resolve_user_chat_pref(user_id, fallback_block=_profile.chat_block_key)
    if _mode_block:
        import dataclasses

        from app.langchain.config import chat_block_supports_vision

        _profile = dataclasses.replace(_profile, chat_block_key=_mode_block, supports_vision=chat_block_supports_vision(_mode_block))

    set_current_profile(_profile)
    apply_gen_override(_profile)
    # dsh HTTP 桥：登记用户级默认（超管标记 / 生成覆盖），供桥内工具集形态缓存
    try:
        from app.core.ctx import CTX_GEN_BLOCK_OVERRIDE
        from app.mcp_bridge.dsh_http_bridge import register_turn_defaults

        register_turn_defaults(user_id, is_super=_is_super, is_admin=_is_admin, gen_override=CTX_GEN_BLOCK_OVERRIDE.get())
    except Exception as _e:  # noqa: BLE001
        logger.warning(f"[qa] register_turn_defaults 失败（忽略）: {_e}")

    # 连接器生效集签名也编进 key：添加/移除/启停连接器 → key 变 → agent 自动重建；
    # 会话驻留专家同样编进 key：召唤/移除专家、专家定义变化 → key 变 → 自动重建；
    # 档位集合编进 key：挂/摘标记角色 → key 变 → 可见面即时收紧/放开；
    # 对话模式/思考强度编进 key（_d{mode}_{level}）：切换 → key 变 → 重建对应形态
    # （块覆盖已进 _profile.cache_key_part，此处再编入 level 与 mode 语义值）
    cache_key = (
        f"u{user_id or 0}"
        + ("_s" if _is_super else "")
        + ("_a" if _is_admin else "")
        + tier_cache_part(_tier_codes)
        + _profile.cache_key_part
        + await _connector_cache_part(user_id, _tier_codes)
        + _expert_cache_part(session_expert)
        + f"_d{_chat_mode or 'g'}_{_chat_level or 'g'}"
    )

    # 先无锁读：99% 走这条路径
    cached = _agent_cache.get(cache_key)
    if cached is not None:
        async with _agent_lru_lock:
            _agent_cache.move_to_end(cache_key, last=True)
        return cached

    # 单 cache_key 串行构建；其他 key 不受影响
    build_lock = await _get_or_create_build_lock(cache_key)
    async with build_lock:
        cached = _agent_cache.get(cache_key)
        if cached is not None:
            return cached

        # ── dsh 内核（main-dsh 分支）────────────────────────────────────────
        # 对话状态由 dsh 运行时自己维护（进程内多轮 + JSONL 会话日志），不再需要
        # langgraph checkpointer；业务工具/连接器经 qa_agent 的 MCP 桥接入。
        _skills_dir = ws / ".agent_skills"
        user_skills = [".agent_skills"] if _skills_dir.is_dir() else None
        # 专家绑定技能面：独立的每专家父目录与主技能面并列注入（运行时并集）
        if session_expert is not None and session_expert.skill_keys and (ws / _EXPERT_SKILLS_DIRNAME).is_dir():
            user_skills = (user_skills or []) + [_EXPERT_SKILLS_DIRNAME]

        # 用户 MCP 连接器生效集 → dsh cordis mcp-client 行（仅 streamable_http 可直挂，
        # SSE 连接器由 qa_agent 侧跳过并记录）。凭据解析口径与 deepagents 时代一致：
        # 本人个人凭据优先 → shared 共享凭据 → personal 无凭据跳过。
        conn_configs: list[dict] = []
        try:
            conn_rows, _conn_prefs, conn_creds = await _get_effective_connectors(user_id, _tier_codes)
            if session_expert is not None and session_expert.connector_keys:
                from app.api.v1.ai.agent_expert import _listed_or_own as _expert_listed
                from app.models.standard.agent import AgentConnector as _AgentConnectorRow

                _existing = {c.connector_key for c in conn_rows}
                _extra_keys = [k for k in session_expert.connector_keys if k not in _existing]
                if _extra_keys:
                    for _c in await _AgentConnectorRow.filter(connector_key__in=_extra_keys):
                        # 绑定连接器并入前复核：统一尺子 + 档位（绑定关系不回溯校验）
                        if _expert_listed(_c, user_id) and await tier_allows(_c.min_tier_code, _c.user_id, user_id, _tier_codes):
                            conn_rows.append(_c)
            for c in conn_rows:
                cred = conn_creds.get(c.connector_key)
                if cred is not None:
                    api_key = cred.api_key
                elif c.credential_mode == "shared":
                    api_key = c.api_key
                elif c.credential_mode == "none":
                    api_key = None
                else:
                    continue  # personal 且无凭据 → 不加载
                conn_configs.append({"key": c.connector_key, "transport": c.transport, "url": c.url, "api_key": api_key})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[qa] 连接器配置加载失败（跳过）: {e}")

        # 会话驻留专家人设：构建期注入系统提示词（无条件则不注入，通用会话形态不变）
        _expert_payload = None
        if session_expert is not None and (session_expert.instructions or "").strip():
            _expert_payload = {
                "name": session_expert.name,
                "description": session_expert.description or "",
                "instructions": session_expert.instructions,
            }

        # create_qa_agent 是同步函数，丢线程池跑
        def _build():
            return create_qa_agent(
                root_dir=str(ws),
                user_id=user_id,
                is_super=_is_super,
                is_admin=_is_admin,
                skills=user_skills,
                chat_block_key=_profile.chat_block_key,
                thinking_level=_chat_level,
                expert=_expert_payload,
                connectors=conn_configs,
            )

        agent = await asyncio.to_thread(_build)

        async with _agent_lru_lock:
            _agent_cache[cache_key] = agent
            while len(_agent_cache) > _AGENT_CACHE_MAX:
                evict_key, evicted_agent = _agent_cache.popitem(last=False)
                logger.info(f"LRU 弹出 agent: {evict_key}")
                # dsh 内核：弹出的实例关掉其运行时子进程，防泄漏
                try:
                    _close = getattr(evicted_agent, "close", None)
                    if callable(_close):
                        await asyncio.to_thread(_close)
                except Exception as _ce:  # noqa: BLE001
                    logger.warning(f"关闭被弹出 agent 失败（忽略）: {_ce}")
                # 顺手清理它的 build lock（再用就再建）
                async with _agent_build_locks_lock:
                    _agent_build_locks.pop(evict_key, None)
        return agent


def _session_key_from_thread(thread_id: str) -> str:
    """thread_id 形如 `qa-sess_xxxxxx`，截掉 `qa-` 前缀得到 session_key；其他形式归为匿名桶。"""
    if thread_id.startswith("qa-"):
        return thread_id[3:]
    return f"anon_{thread_id}"


# ── Schemas ───────────────────────────────────────────────────────────────────


class QARequest(BaseModel):
    """问答请求"""

    message: str = Field(..., description="用户消息")
    session_key: Optional[str] = Field(None, description="会话 key；留空则自动新建会话")
    expert_key: Optional[str] = Field(None, alias="expertKey", description="会话召唤的专家 key：新建会话时绑定；已有会话且与现绑定不同时改绑（@专家名 走同一改绑路径）")
    thread_id: Optional[str] = Field(None, description="兼容老接口：直接指定 thread_id（不持久化）")
    files: Optional[list[str]] = Field(None, description="已上传文件的相对路径列表")
    workflow_key: Optional[str] = Field(None, description="用户当前在工作流画板打开的工作流 key（注入上下文用）")
    scope_node_ids: Optional[list[str]] = Field(None, description="迷你协作可编辑范围（焦点卡 + 一跳邻居，首元素为焦点卡；空=全板模式）")
    selected_node_ids: Optional[list[str]] = Field(None, description="用户当前在画板选中的节点 id 列表（随消息的上下文，非可编辑范围）")
    sustained_work: bool = Field(False, description="持续精造模式（仅管理员）：复杂创作任务多轮自我精进直到拿得出手")

    class Config:
        populate_by_name = True


class QAResetRequest(BaseModel):
    """重置对话请求"""

    thread_id: str = Field(..., description="要清除的会话线程 ID")


# 持续精造模式（仅管理员）：前端 composer「持续精造」按钮点亮后随消息带上 sustained_work，
# 这里做消息级指令注入（协议本体在 _project/sustained-build 技能里，由 agent 现场加载——
# 与 workflow-board / office-cli 技能的"先读 SKILL.md 再动手"同模式）。
# 非管理员携带标志位在端点入口静默降级为普通消息（不返回 Fail JSON，避免破坏 SSE 流解析）。
_SUSTAINED_WORK_RULES = """[持续精造模式已开启]
用户为本次任务明确要求持续精造：他已预先接受更长的耗时与更高的消耗，换一件不需要他来回挑毛病就打磨到位的交付。
动手前必须先 read_file 加载 `.agent_skills/sustained-build/SKILL.md`（持续精造工作协议），然后严格按协议节律工作：
计划先行 → 初版 → [深度工作 ↔ 侦察兵灵感] 多轮循环 → 定期整理，直到有确凿证据（侦察兵裁决 / 轮次上限 / 用户喊停）才收工。
本条消息禁止一轮交差，也禁止以"如需调整请告诉我"搪塞收尾。

"""


# ── SSE 工具 ──────────────────────────────────────────────────────────────────


def _sse(data: dict) -> str:
    """构造 SSE 事件"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# 图片预解读提示词：一次性把图「翻译」成文字，覆盖后续回合可能用到的各类细节
_IMAGE_PRIME_PROMPT = (
    "请完整、忠实、详细地描述这张图片，让看不到图片的人仅凭你的描述就能完整掌握图片内容：\n"
    "1. 整体内容与场景；2. 关键细节（主体、物体、人物、颜色、布局、相对位置）；\n"
    "3. 图中出现的所有文字必须逐字转写；表格 / 图表 / 单据 / 代码等结构化内容逐条写出（可用 markdown 表格）。\n"
    "直接输出描述本身，不要输出『这张图片展示了』之类的开场白或评价。"
)


async def _describe_one_image(llm, abs_path: Path) -> str:
    """调多模态 LLM 一次，获取单张图片的详细文字描述；失败抛异常由调方兜底。"""
    import mimetypes

    from langchain_core.messages import HumanMessage

    from app.langchain.tools.vision_tools import _maybe_resize_sync, _to_data_url

    data = await asyncio.to_thread(abs_path.read_bytes)
    if len(data) > _IMAGE_PRIME_MAX_BYTES:
        raise ValueError(f"图片过大（{len(data) // 1048576}MB > 20MB）")
    mime = mimetypes.guess_type(abs_path.name)[0] or "image/png"
    # PIL 缩放是 CPU 密集，必须丢线程池（复用 vision_tools 的缩放逻辑）
    data, mime = await asyncio.to_thread(_maybe_resize_sync, data, mime)
    msg = HumanMessage(
        content=[
            {"type": "text", "text": _IMAGE_PRIME_PROMPT},
            {"type": "image_url", "image_url": {"url": _to_data_url(data, mime)}},
        ]
    )
    resp = await asyncio.wait_for(llm.ainvoke([msg]), timeout=_IMAGE_PRIME_TIMEOUT_S)
    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    if not content.strip():
        raise ValueError("视觉模型返回为空")
    return content.strip()


async def _prime_image_descriptions(request, workspace: Path) -> str:
    """
    图片理解预注入（dsh rc1 时代「图片进不了模型上下文」的过渡方案，待原生链路实测确认后拆除）。

    rc1 运行时捆绑里没有 attachments 持久服务的实现（08-22/08-25 二进制解剖 +
    真实运行时实测坐实）：内联图片块、read_image 工具、MCP 图片结果全被运行时拒绝，
    图片字节无法进入模型上下文。因此在回合开始前，用当前生效 chat 块（支持视觉，
    调用方已判定）的原生多模态能力把每张光栅附件预先「看」一遍，以文字注入用户
    消息 —— 主回合零工具调用，agent 直接拿到图片内容。解读失败的图如实注入失败说明，
    不静默丢图。

    0.1.2-alpha.5 现状：attachments 服务已补齐，且 cordis.patch.yml 的 models 条目
    按块视觉能力声明了 image 输入模态（见 qa_agent._build_cordis_patch_yml），
    原生 read_image 通道已打开——预解读文字不够用时 agent 可再 read_image 看原图。
    待真实回合确认原生链路完整可用后，本层整体拆除、上传改走原生内联。
    """
    from app.langchain.llm_providers import get_llm

    raster = [f for f in (request.files or []) if Path(f).suffix.lower() in _RASTER_IMAGE_EXTS]
    if not raster:
        return ""
    llm = get_llm()

    def _abs(rel: str) -> Path:
        p = Path(rel)
        return p.resolve() if p.is_absolute() else (workspace / rel.lstrip("/").lstrip("\\")).resolve()

    results = await asyncio.gather(*[_describe_one_image(llm, _abs(f)) for f in raster], return_exceptions=True)
    blocks: list[str] = []
    for rel, res in zip(raster, results):
        if isinstance(res, BaseException):
            logger.warning(f"[image_prime] 图片预解读失败 {rel}: {type(res).__name__}: {res}")
            blocks.append(f"图片：{rel}\n（预解读失败，无法查看该图内容，请向用户如实说明）")
        else:
            blocks.append(f"图片：{rel}\n内容描述：\n{res}")
    return "[用户上传的图片已由多模态模型预解读，内容描述如下，直接使用即可；回复中提及图片时用文件名指代；若描述信息不够、需要核对细节，可再用 read_image 工具直接查看原图]\n" + "\n\n".join(blocks)


# ── 交互式问卷解析 ────────────────────────────────────────────────────────────
# 与 [artifact:<ID>] 同一模式：模型在正文中自主输出 ```questionnaire 围栏（严格 JSON），
# 后端流中解析成问卷卡片事件，展示文本里只留 [questionnaire:<qid>] 占位符——
# 前端按占位符定位渲染卡片，历史回放从 process_json 的 questionnaire 条目取载荷。
# 未闭合的围栏（JSON 还在流式输出中）从展示文本里截掉，用户看不到 JSON 过程。

_QN_MARKER = "```questionnaire"
_QN_MAX_QUESTIONS = 5
_QN_MAX_OPTIONS = 6


def _scan_questionnaire_segments(raw: str) -> list[tuple[bool, str]]:
    """扫描文本中的问卷围栏，返回 [(是否问卷块, 内容)] 段列表。

    已闭合围栏 → (True, 围栏内 JSON 文本)；其余文本 → (False, 原文)。
    尾部未闭合围栏（开始标记已出现、结束围栏未流到）不进段列表 = 展示截断。
    """
    segments: list[tuple[bool, str]] = []
    pos = 0
    while True:
        start = raw.find(_QN_MARKER, pos)
        if start < 0:
            segments.append((False, raw[pos:]))
            return segments
        segments.append((False, raw[pos:start]))
        nl = raw.find("\n", start)
        if nl < 0:
            return segments  # 开始行尚未流完整 = 未闭合
        close = raw.find("\n```", nl)
        if close < 0:
            return segments  # 未闭合
        seg_end = raw.find("\n", close + 1)
        segments.append((True, raw[nl + 1 : close]))
        pos = len(raw) if seg_end < 0 else seg_end + 1


def _parse_questionnaire(body: str) -> Optional[dict]:
    """校验并规整问卷 JSON；合法返回 {"questions": [...]}，否则 None（降级按原文展示）。"""
    try:
        data = json.loads(body.strip())
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions or len(questions) > _QN_MAX_QUESTIONS:
        return None
    cleaned: list[dict] = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            return None
        question_text = str(q.get("question") or "").strip()
        options = q.get("options")
        if not question_text or not isinstance(options, list) or not (2 <= len(options) <= _QN_MAX_OPTIONS):
            return None
        opts: list[dict] = []
        for opt in options:
            if not isinstance(opt, dict):
                return None
            label = str(opt.get("label") or "").strip()
            if not label:
                return None
            item = {"label": label}
            desc = str(opt.get("description") or "").strip()
            if desc:
                item["description"] = desc
            opts.append(item)
        cleaned.append({
            "id": str(q.get("id") or "").strip() or f"q{i + 1}",
            "title": str(q.get("title") or "").strip()[:20] or f"问题 {i + 1}",
            "question": question_text,
            "multiSelect": bool(q.get("multiSelect")),
            "options": opts,
        })
    return {"questions": cleaned}


async def _save_msg_resilient(
    msg: AgentMessage,
    *,
    update_fields: Optional[list[str]] = None,
    attempts: int = 3,
    delay: float = 0.4,
    what: str = "",
) -> bool:
    """消息落库的连接瞬断重试（修复「一次 2013 杀死整个回合」）。

    高思考强度长回合期间，到内网 OceanBase 的空闲连接可能被中间设备回收
    （日志实证：UPDATE agent_message 中途 2013 Lost connection，2026-09-04 单日 46 次），
    旧实现异常直接穿透 astream 消费循环 → 整个回合被中止（LLM 明明还在正常输出）。
    OperationalError（2013/2006/2003 均归入此类）指数退避重试；非连接类错误不重试。
    最终失败返回 False 不抛异常——进度落库本就是尽力而为，不能拖垮回合；
    调用方对终态落库失败仅告警（前端已有全量内容，刷新/回放可纠偏）。
    """
    from tortoise.exceptions import OperationalError

    last: Optional[Exception] = None
    for i in range(attempts):
        try:
            if update_fields:
                await msg.save(update_fields=update_fields)
            else:
                await msg.save()
            if i:
                logger.info(f"[db-resilient] {what or 'save'} 第 {i + 1} 次重试成功 msg_id={msg.id}")
            return True
        except OperationalError as e:
            last = e
            logger.warning(f"[db-resilient] {what or 'save'} 连接异常（第 {i + 1}/{attempts} 次）msg_id={msg.id}: {e}")
            if i < attempts - 1:
                await asyncio.sleep(delay * (2**i))
        except Exception as e:  # noqa: BLE001
            last = e
            break
    logger.error(f"[db-resilient] {what or 'save'} 最终失败 msg_id={msg.id}: {last}")
    return False


async def _execute_agent_in_background(
    agent,
    agent_input: Union[str, list],  # str=纯文本；list=多模态 content blocks（旁路消费方）
    config: dict,
    session: Optional[AgentSession],
    user_msg: Optional[AgentMessage],
    assistant_msg: Optional[AgentMessage],
    request,
    event_queue: asyncio.Queue,
    workspace: Path,
    cancellation_event: asyncio.Event,
    expert_summoned: bool = False,  # 本条消息是否 @召唤改绑了专家（session 事件回显用）
    timeline_details: bool = False,  # True=超管/管理员：时间线保留原始入参细节（排查用）
):
    """
    后台任务：执行 Agent 推理并将事件推送到队列。
    无论前端是否断开连接，都会完整执行并存库（除非收到取消信号）。
    """
    step = 0  # SSE 事件序号（前端排序/调试用）
    # 正文累积：提升（promote）前 = 已流式输出的全部文本（中间态，供刷新恢复）；
    # 收到 answer_promote 后替换为被提升的尾部文本（= 最终答案）
    collected_content: list[str] = []
    # 过程时间线：落库形状（不含被提升为答案的尾部 text 条目）
    process_items: list[dict] = []
    # 各 text/reasoning 条目的全量文本（item_id → 累积文本）
    item_text: dict[str, str] = {}
    # 未闭合的尾部 text 条目（有序）：tool_call 到达即清空（定性叙述）；
    # aborted 时尽力提升为答案（用户看到的即答案）
    open_text_ids: list[str] = []
    # 已被提升为答案的条目 id（done/aborted 落库时从 process_json 剔除）
    promoted_ids: list[str] = []
    # 问卷解析状态：围栏扫描以原始全文为准（item_text 存占位符替换后的展示文本）
    _raw_item_text: dict[str, str] = {}
    _qn_assigned: dict[str, list[Optional[str]]] = {}  # text 条目 id → 已分配 qid（None=JSON 非法，降级原文）
    _qn_seq = 0  # 全局问卷序号（qid = qn1/qn2…，占位符 [questionnaire:qnN]）
    _text_item_order: list[str] = []  # text 条目首现顺序（重建模式下 collected_content 的依据）
    _collected_rebuild = False  # 问卷出现后，collected_content 改为按展示文本全量重建（占位符语义进落库内容）
    session_key = session.session_key if session else None
    _first_process_token = False  # 首个过程事件（思考/正文）计时用

    # 节流落库：执行期间把已产出进度写回 DB（status 保持 streaming），用户刷新页面后
    # 前端轮询 GET messages 即可增量恢复中间进度，而非只看到「思考中...」占位。
    # text 增量是 token 级高频事件走时间节流；tool_call/tool_result/todo/promote
    # 低频且是进度骨架，强制立即落库。终态落库（done/error/aborted）不受影响。
    _FLUSH_INTERVAL = 2.0
    _last_flush = time.monotonic()

    # reasoning 攒批：dsh 的思考流 delta 频率极高，逐条发 SSE 浪费带宽；
    # 缓冲 ≥0.4s 或任何非 reasoning 事件到达即 flush（DB 条目不受攒批影响，始终全量累积）
    _REASONING_BATCH_S = 0.4
    _reasoning_buf: dict = {"item_id": None, "text": "", "since": 0.0}

    def _upsert_process_item(item: dict) -> None:
        """按 id 原位更新或追加时间线条目（text/todo 为可重入条目）。"""
        for i, existing in enumerate(process_items):
            if existing.get("id") == item.get("id"):
                process_items[i] = item
                return
        process_items.append(item)

    def _sync_collected_content() -> None:
        """重建模式下按展示文本（占位符语义）全量重算 collected_content。"""
        collected_content.clear()
        collected_content.extend(item_text.get(i, "") for i in _text_item_order)

    async def _emit_questionnaire_text(item_id: str, raw: str) -> None:
        """含问卷围栏的 text 条目：重扫原始全文 → 新闭合围栏发问卷事件，展示文本替换为占位符。

        展示文本 = 原文把已闭合围栏换成 [questionnaire:qnN]、尾部未闭合围栏截掉；
        与上一轮展示不同即以 replace 事件整块下发（前端 text replace 语义已有）。
        """
        nonlocal step, _qn_seq, _collected_rebuild, _first_process_token
        _collected_rebuild = True
        assigned = _qn_assigned.setdefault(item_id, [])
        segments = _scan_questionnaire_segments(raw)
        display_parts: list[str] = []
        qn_index = 0
        for is_qn, body in segments:
            if not is_qn:
                display_parts.append(body)
                continue
            qn_index += 1
            if qn_index <= len(assigned):
                qid = assigned[qn_index - 1]
                display_parts.append(f"[questionnaire:{qid}]" if qid else f"```questionnaire\n{body.strip()}\n```")
                continue
            payload = _parse_questionnaire(body)
            if payload is None:
                assigned.append(None)  # 非法 JSON：降级按原文展示，记占位防重扫重复告警
                display_parts.append(f"```questionnaire\n{body.strip()}\n```")
                logger.warning(f"[questionnaire] 围栏 JSON 非法，降级原文展示 session={session_key} item={item_id}")
                continue
            _qn_seq += 1
            qid = f"qn{_qn_seq}"
            assigned.append(qid)
            display_parts.append(f"[questionnaire:{qid}]")
            _upsert_process_item({"id": qid, "kind": "questionnaire", **payload})
            step += 1
            await event_queue.put(_sse({"type": "process", "step": step, "kind": "questionnaire", "item_id": qid, **payload}))
            await _flush_progress(force=True)
            logger.info(f"[questionnaire] 发出 {qid}（{len(payload['questions'])} 题）session={session_key}")
        display = "".join(display_parts)
        prev = item_text.get(item_id)
        if item_id not in _text_item_order:
            _text_item_order.append(item_id)
        if item_id not in open_text_ids:
            open_text_ids.append(item_id)
        item_text[item_id] = display
        _sync_collected_content()
        _upsert_process_item({"id": item_id, "kind": "text", "content": display})
        if prev != display:
            step += 1
            if not _first_process_token:
                _first_process_token = True
                logger.info(f"[perf] first process token: {(time.monotonic() - _t_stream_start) * 1000:.0f}ms after astream()")
            await event_queue.put(_sse({"type": "process", "step": step, "kind": "text", "item_id": item_id, "content": display, "replace": True}))
            await _flush_progress()

    async def _flush_reasoning() -> None:
        """把 reasoning 攒批缓冲发成一条 SSE process 事件。"""
        nonlocal step
        if not _reasoning_buf["text"]:
            return
        item_id = _reasoning_buf["item_id"]
        text = _reasoning_buf["text"]
        _reasoning_buf["item_id"] = None
        _reasoning_buf["text"] = ""
        _reasoning_buf["since"] = 0.0
        step += 1
        await event_queue.put(_sse({"type": "process", "step": step, "kind": "reasoning", "item_id": item_id, "content": text}))

    def _persist_process_items() -> list[dict]:
        """落库用的时间线：剔除已提升为答案的条目（它们活在 content 列）。"""
        return [it for it in process_items if it.get("id") not in promoted_ids]

    async def _flush_progress(force: bool = False) -> None:
        nonlocal _last_flush
        if assistant_msg is None:
            return
        if not force and time.monotonic() - _last_flush < _FLUSH_INTERVAL:
            return
        _last_flush = time.monotonic()
        assistant_msg.content = "".join(collected_content).strip()
        assistant_msg.process_json = _persist_process_items()
        # 落库失败不再杀回合（历史 bug：一次 2013 瞬断从 save 穿透 astream 消费循环
        # → 整个回合中止，LLM 明明还在正常输出）。内存字段已更新，
        # 下次节流 flush 自然补齐；helper 内部已重试 + 告警。
        await _save_msg_resilient(
            assistant_msg,
            update_fields=["content", "process_json", "update_time"],
            what="进度落库",
        )

    async def _finalize_aborted() -> None:
        """用户主动停止不算异常：走独立的 aborted 状态，不写 error。
        尽力提升：已流式输出的尾部 text 落为答案（用户看到什么，落库就是什么）。"""
        await _flush_reasoning()
        if open_text_ids:
            promoted_ids.extend(i for i in open_text_ids if i in item_text and i not in promoted_ids)
            collected_content.clear()
            collected_content.extend(item_text[i] for i in promoted_ids)
            open_text_ids.clear()
        if assistant_msg is not None:
            assistant_msg.content = "".join(collected_content).strip()
            assistant_msg.process_json = _persist_process_items()
            assistant_msg.status = "aborted"
            assistant_msg.error = None
            # 终态落库失败也不阻断 aborted 事件（前端已收到全量内容）
            await _save_msg_resilient(assistant_msg, attempts=5, delay=0.5, what="aborted 落库")
        await event_queue.put(_sse({"type": "aborted"}))

    # dsh HTTP 桥回合登记：uid/token 用局部变量持有，收尾清理时原样传回
    # （只清自己的条目，避免误清同用户并发回合的上下文）
    _turn_uid = CTX_USER_ID.get() or None
    _turn_token: Optional[str] = None

    try:
        # 用户输入内容审核
        from app.utils.content_moderation import moderate

        hit_keyword = await moderate(request.message)
        if hit_keyword:
            logger.warning(f"用户输入审核拦截, session={session.session_key if session else '-'}, keyword={hit_keyword!r}")
            if assistant_msg is not None:
                assistant_msg.content = "[内容审核未通过，已拦截]"
                assistant_msg.status = "done"
                await _save_msg_resilient(assistant_msg, what="审核拦截落库")
            await event_queue.put(_sse({"type": "moderated", "message": "内容已被审核拦截"}))
            await event_queue.put(_sse({"type": "done", "steps": 0}))
            await event_queue.put(None)  # 结束标记
            return

        # 积分余额检查（generic 模式下；standard 模式自动放行）
        from app.langchain.billing.quota import check_quota

        quota_status = await check_quota(CTX_USER_ID.get() or None)
        if not quota_status.allowed:
            if assistant_msg is not None:
                assistant_msg.content = "积分余额不足，请联系管理员充值"
                assistant_msg.status = "error"
                assistant_msg.error = "quota_exceeded"
                await _save_msg_resilient(assistant_msg, what="quota 落库")
            await event_queue.put(
                _sse({
                    "type": "quota_exceeded",
                    "quota": quota_status.quota,
                    "used": quota_status.used,
                    "remaining": quota_status.remaining,
                    "message": "积分余额不足，请联系管理员充值",
                })
            )
            await event_queue.put(None)  # 结束标记
            return

        # 会话驻留专家回显：前端据此刷新专家徽标（@召唤改绑也在这里随首事件送达）
        _sess_expert = None
        if session is not None and session.expert_key:
            from app.api.v1.ai.agent_expert import get_effective_expert as _get_eff_expert

            _sess_expert = await _get_eff_expert(CTX_USER_ID.get() or None, session.expert_key)
        await event_queue.put(
            _sse({
                "type": "session",
                "sessionKey": session.session_key if session else None,
                "threadId": config["configurable"]["thread_id"],
                "assistantMessageId": assistant_msg.id if assistant_msg else None,
                "userMessageId": user_msg.id if user_msg else None,
                "expertKey": _sess_expert.expert_key if _sess_expert else None,
                "expertName": _sess_expert.name if _sess_expert else None,
                "expertIcon": _sess_expert.icon if _sess_expert else None,
                "expertSummoned": bool(expert_summoned and _sess_expert),
            })
        )

        # 设置 Agent 调用上下文
        # workflow_scope：画布底部迷你输入栏的「选中节点协作」作用域，透传给 edit_workflow_board 软强制
        _wf_scope = None
        if request.workflow_key and request.scope_node_ids:
            _wf_scope = {
                "workflow_key": request.workflow_key,
                "focus_id": request.scope_node_ids[0],
                "scope_ids": list(request.scope_node_ids),
            }
        set_agent_call_context(
            AgentCallContext(
                session_id=session.id if session else None,
                session_key=session.session_key if session else None,
                message_id=assistant_msg.id if assistant_msg else None,
                workspace_dir=workspace,
                workflow_scope=_wf_scope,
            )
        )

        # dsh HTTP 桥：登记本回合逐消息上下文（产物联动/工作区根）
        try:
            from app.mcp_bridge.dsh_http_bridge import set_active_turn

            _turn_token = set_active_turn(
                _turn_uid,
                session_id=session.id if session else None,
                session_key=session.session_key if session else None,
                message_id=assistant_msg.id if assistant_msg else None,
                workspace=workspace,
            )
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"[bg_task] set_active_turn 失败（产物联动降级）: {_e}")

        # 计费上下文
        from app.core.ctx import CTX_BILLING_BIZ_ENTRY, CTX_BILLING_SESSION_ID

        CTX_BILLING_BIZ_ENTRY.set("qa")
        CTX_BILLING_SESSION_ID.set(session.id if session else None)

        # 图片预注入：dsh rc1 运行时无图片通道（无 attachments 服务，内联图片块 /
        # read_image / MCP 图片结果全被拒），回合开始前先用生效 chat 块的多模态能力
        # 预解读注入文字，恢复「零工具调用直接看图」体验（计费已挂上下文，预解读调用同样记账）
        try:
            from app.langchain.role_model_profile import effective_chat_supports_vision

            if isinstance(agent_input, str) and request.files and effective_chat_supports_vision():
                _primed = await _prime_image_descriptions(request, workspace)
                if _primed:
                    agent_input = _primed + "\n\n" + agent_input
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"[bg_task] 图片预解读失败（本轮降级为看不到图）: {_e}")

        # subgraphs=True：子 Agent 的事件也会冒泡上来，以三元组 (namespace, mode, data) 形式
        # cancel_event：停止信号即时生效（≤0.5s），不必等下一个流事件；
        # astream 退出时会写带外取消信号中止 dsh 回合本身（见 DshQaAgent._signal_cancel）
        _t_stream_start = time.monotonic()
        async for namespace, stream_mode, chunk in agent.astream(
            {"messages": [{"role": "user", "content": agent_input}]},
            config=config,
            stream_mode=["messages", "updates"],
            subgraphs=True,
            cancel_event=cancellation_event,
        ):
            # 检查取消信号
            if cancellation_event.is_set():
                logger.info(f"[bg_task] 检测到取消信号，中止执行 session={session_key}")
                await _finalize_aborted()
                return

            # dsh 内核下 namespace 恒为空元组；messages/updates 模式仍会产出
            # （供五条旁路消费方），本端点只消费 process 模式。
            # 子代理子会话的工具事件已由适配层翻译成带 in_subagent 标记的 process 条目。
            if stream_mode != "process":
                continue

            kind = chunk.get("kind")

            # reasoning 攒批：非 reasoning 事件到达先 flush，保证时间线顺序
            if kind != "reasoning":
                await _flush_reasoning()

            if kind == "reasoning":
                item_id = chunk.get("item_id") or ""
                content = chunk.get("content") or ""
                if not item_id or not content:
                    continue
                item_text[item_id] = item_text.get(item_id, "") + content
                _upsert_process_item({"id": item_id, "kind": "reasoning", "content": item_text[item_id]})
                if _reasoning_buf["item_id"] != item_id:
                    # 条目切换：旧缓冲先发（跨条目不合并）
                    await _flush_reasoning()
                    _reasoning_buf["item_id"] = item_id
                _reasoning_buf["text"] += content
                if not _reasoning_buf["since"]:
                    _reasoning_buf["since"] = time.monotonic()
                if not _first_process_token:
                    _first_process_token = True
                    logger.info(f"[perf] first process token: {(time.monotonic() - _t_stream_start) * 1000:.0f}ms after astream()")
                if time.monotonic() - _reasoning_buf["since"] >= _REASONING_BATCH_S:
                    await _flush_reasoning()

            elif kind == "text":
                item_id = chunk.get("item_id") or ""
                content = chunk.get("content") or ""
                if not item_id:
                    continue
                # 原始全文累积（问卷扫描基准；replace=block-end 权威整块，防 delta 丢失）
                if chunk.get("replace"):
                    _raw_item_text[item_id] = content
                else:
                    _raw_item_text[item_id] = _raw_item_text.get(item_id, "") + content
                raw = _raw_item_text[item_id]

                if _QN_MARKER in raw or item_id in _qn_assigned:
                    # 问卷路径：重扫原文，闭合围栏发问卷事件，展示文本只留占位符（replace 整块下发）
                    await _emit_questionnaire_text(item_id, raw)
                    continue

                # 常规路径：与问卷无涉的增量/整块转发
                if chunk.get("replace"):
                    item_text[item_id] = content
                else:
                    item_text[item_id] = item_text.get(item_id, "") + content
                    if not _collected_rebuild:
                        collected_content.append(content)
                if item_id not in _text_item_order:
                    _text_item_order.append(item_id)
                if _collected_rebuild:
                    _sync_collected_content()
                if item_id not in open_text_ids:
                    open_text_ids.append(item_id)
                _upsert_process_item({"id": item_id, "kind": "text", "content": item_text[item_id]})
                step += 1
                if not _first_process_token:
                    _first_process_token = True
                    logger.info(f"[perf] first process token: {(time.monotonic() - _t_stream_start) * 1000:.0f}ms after astream()")
                await event_queue.put(
                    _sse({
                        "type": "process",
                        "step": step,
                        "kind": "text",
                        "item_id": item_id,
                        "content": content,
                        "replace": bool(chunk.get("replace")),
                    })
                )
                await _flush_progress()

            elif kind == "tool_call":
                # 工具调用定性了此前的流式文本为叙述（留在时间线，不进答案）
                open_text_ids.clear()
                tool_name = chunk.get("tool") or ""
                item_id = chunk.get("item_id") or ""
                # 入参脱敏：SQL / shell 命令 / 代码内容等实现细节不出后端
                # （落库过程项与 SSE 用同一份，历史回放同样干净）；
                # 超管/管理员（timeline_details=True）保留原始入参便于排查
                raw_args = chunk.get("args") or {}
                safe_args = raw_args if timeline_details else sanitize_tool_args(tool_name, raw_args)
                in_subagent = bool(chunk.get("in_subagent"))
                _upsert_process_item({
                    "id": item_id,
                    "kind": "tool_call",
                    "tool": tool_name,
                    "tool_display": get_tool_display_name(tool_name),
                    "args": safe_args,
                    "is_subagent": bool(chunk.get("is_subagent")),
                    "in_subagent": in_subagent,
                })
                step += 1
                await event_queue.put(
                    _sse({
                        "type": "process",
                        "step": step,
                        "kind": "tool_call",
                        "item_id": item_id,
                        "tool": tool_name,
                        "tool_display": get_tool_display_name(tool_name),
                        "args": safe_args,
                        "is_subagent": bool(chunk.get("is_subagent")),
                        "in_subagent": in_subagent,
                    })
                )
                await _flush_progress(force=True)

            elif kind == "tool_result":
                tool_name = chunk.get("tool") or ""
                item_id = chunk.get("item_id") or ""
                content = chunk.get("content") or ""
                is_error = bool(chunk.get("is_error"))
                in_subagent = bool(chunk.get("in_subagent"))
                # 返回值脱敏：技能加载内容（SKILL.md 全文）与技术型输出不出后端；
                # 业务数据原样保留。超管/管理员（timeline_details=True）看原始返回
                if not timeline_details:
                    content = sanitize_tool_result(tool_name, content, is_error)
                _upsert_process_item({
                    "id": item_id,
                    "kind": "tool_result",
                    "tool": tool_name,
                    "tool_display": get_tool_display_name(tool_name),
                    "content": content,
                    "is_error": is_error,
                    "in_subagent": in_subagent,
                })
                step += 1
                await event_queue.put(
                    _sse({
                        "type": "process",
                        "step": step,
                        "kind": "tool_result",
                        "item_id": item_id,
                        "tool": tool_name,
                        "tool_display": get_tool_display_name(tool_name),
                        "content": content,
                        "is_error": is_error,
                        "in_subagent": in_subagent,
                    })
                )
                await _flush_progress(force=True)

            elif kind == "todo":
                todos = chunk.get("todos") or []
                # 固定 id "todo"：整快照原位替换，避免长任务清单刷屏
                _upsert_process_item({"id": "todo", "kind": "todo", "todos": todos})
                step += 1
                await event_queue.put(_sse({"type": "process", "step": step, "kind": "todo", "item_id": "todo", "todos": todos}))
                await _flush_progress(force=True)

            elif kind == "compaction":
                item_id = chunk.get("item_id") or ""
                _upsert_process_item({"id": item_id, "kind": "compaction"})
                step += 1
                await event_queue.put(_sse({"type": "process", "step": step, "kind": "compaction", "item_id": item_id}))

            elif kind == "reset":
                # in-band 重试 / 升代续跑：清空一切已累积，前端同步清场
                collected_content.clear()
                process_items.clear()
                item_text.clear()
                open_text_ids.clear()
                promoted_ids.clear()
                _raw_item_text.clear()
                _qn_assigned.clear()
                _qn_seq = 0
                _text_item_order.clear()
                _collected_rebuild = False
                _reasoning_buf["item_id"] = None
                _reasoning_buf["text"] = ""
                _reasoning_buf["since"] = 0.0
                step += 1
                await event_queue.put(_sse({"type": "process", "step": step, "kind": "reset"}))
                await _flush_progress(force=True)

            elif kind == "answer_promote":
                # 回合收尾：尾部未闭合 text 条目定性为答案
                ids = [i for i in (chunk.get("item_ids") or []) if i in item_text]
                if ids:
                    promoted_ids.extend(i for i in ids if i not in promoted_ids)
                    collected_content.clear()
                    collected_content.extend(item_text[i] for i in promoted_ids)
                    open_text_ids.clear()
                await _flush_progress(force=True)

        # astream 可能因 cancel_event 自行结束（停止响应），循环体内的检查没机会执行 → 这里兜底
        if cancellation_event.is_set():
            logger.info(f"[bg_task] 检测到取消信号（流结束后），中止执行 session={session_key}")
            await _finalize_aborted()
            return

        # 收尾：flush 残余攒批 → 内容审核 → 落库
        await _flush_reasoning()
        final_content = "".join(collected_content).strip()
        hit_keyword = await moderate(final_content)
        if hit_keyword:
            logger.warning(f"内容审核拦截, session={session.session_key if session else '-'}, keyword={hit_keyword!r}")
            await event_queue.put(_sse({"type": "moderated", "message": "内容已被审核拦截"}))
            final_content = "[内容审核未通过，已拦截]"

        if assistant_msg is not None:
            assistant_msg.content = final_content
            assistant_msg.process_json = _persist_process_items()
            assistant_msg.status = "done"
            # 终态落库放宽重试次数（失败=答案内容丢）；仍失败也不阻断 done 事件——
            # 前端已有全量内容，DB 不一致由后续消息/刷新回放纠偏
            await _save_msg_resilient(assistant_msg, attempts=5, delay=0.5, what="终态落库")

        await event_queue.put(_sse({"type": "done", "steps": step, "promoted": promoted_ids}))

    except Exception as e:
        logger.exception("后台 Agent 执行异常")
        if assistant_msg is not None:
            try:
                assistant_msg.content = "".join(collected_content).strip()
                assistant_msg.process_json = _persist_process_items()
                assistant_msg.status = "error"
                assistant_msg.error = str(e)[:2000]
                await _save_msg_resilient(assistant_msg, what="error 落库")
            except Exception:  # noqa: BLE001
                # 落库二次异常绝不能吞掉 error 事件的发送——否则前端收不到任何终态
                # 事件、SSE 干净 EOF，气泡永久停在「生成中」，只能刷新页面（本次事故根因之一）
                logger.exception("error 落库也失败，跳过落库仅发事件")
        await event_queue.put(_sse({"type": "error", "message": str(e)}))
    finally:
        clear_agent_call_context()
        try:
            from app.mcp_bridge.dsh_http_bridge import clear_active_turn

            clear_active_turn(_turn_uid, _turn_token)
        except Exception:  # noqa: BLE001
            pass
        await event_queue.put(None)  # 结束标记
        # 清理取消事件：同一性判断——「停止后马上再发」会用新事件覆盖同 key 的注册，
        # 旧任务收尾时不能误删新任务的取消事件（否则后续点停止提示「无运行中的任务」）
        if session_key and _active_task_cancellations.get(session_key) is cancellation_event:
            del _active_task_cancellations[session_key]
        logger.info(f"后台 Agent 执行完成 session={session_key}")


@router.post("/stop", summary="停止对话流")
async def qa_stop(session_key: str):
    """
    停止指定会话的 Agent 执行（用户主动点击停止按钮时调用）。
    session_key 作为 query param 传入：POST /qa/stop?session_key=xxx
    """
    logger.info(f"[stop] 收到停止请求 session_key={session_key!r}, 当前活跃任务keys={list(_active_task_cancellations.keys())}")
    if session_key in _active_task_cancellations:
        _active_task_cancellations[session_key].set()
        logger.info(f"[stop] 已触发取消事件 session={session_key}")
        return Success(data=None, msg="已发送停止信号")
    else:
        logger.info(f"[stop] 未找到活跃任务 session={session_key}")
        return Success(data=None, msg="该会话无运行中的任务")


# ── 会话管理辅助 ──────────────────────────────────────────────────────────────
async def _build_kb_context(query: str, uid: Optional[int]) -> str:
    """从个人知识库召回 top-k，拼成 system prompt 片段；失败或空命中返回空串。"""
    if not uid or not query.strip():
        return ""
    try:
        from app.services.seekdb import kb_hybrid_search
    except Exception:
        return ""
    try:
        # kb_hybrid_search 是同步的（pyseekdb RPC + 同步 embedding 桥接），扔线程池
        hits = await asyncio.to_thread(kb_hybrid_search, query, 5)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[KB] qa 注入检索失败: {e}")
        return ""
    own = [h for h in hits if (h.get("metadata") or {}).get("user_id") == uid]
    if not own:
        return ""
    parts = ["以下是用户个人知识库中与本次提问相关的条目，可作为先验经验参考；引用时请在回复末尾用 `[参考: <标题>]` 标注："]
    for i, h in enumerate(own[:3], 1):
        meta = h.get("metadata") or {}
        title = meta.get("title", f"条目{i}")
        summary = meta.get("summary") or ""
        content = (meta.get("content") or "")[:500]
        parts.append(f"### 条目{i}: {title}\n摘要：{summary}\n正文片段：{content}")
    return "\n\n".join(parts) + "\n\n---\n\n"


async def _ensure_session(session_key: Optional[str], workflow_key: Optional[str] = None, expert_key: Optional[str] = None) -> tuple[Optional[AgentSession], str]:
    """根据 session_key 取或建会话，返回 (session, thread_id)。thread_id 用 session.thread_id。

    新建会话时按 workflow_key 落来源标签与工作流归属：工作流画板页发起的记 workflow 并绑定该工作流，其余记 qa。
    命中已有会话时归属跟随最近一次带板消息：本次消息带 workflow_key 且与现归属不同，
    即改绑到该工作流（QA 页伴侣面板挂板/换板、画板悬浮窗里切老会话聊别的板都走这里；
    改绑无副作用——上下文注入只读 request.workflow_key，checkpointer 按 thread_id 不受影响）。

    expert_key 同款改绑语义：专家为会话级驻留召唤，本次带专家且与现绑定不同即改绑
    （@专家名 解析命中后也走这里）；改绑后失效技能同步节流，专家技能面随本条消息重算。
    """
    uid = CTX_USER_ID.get() or None
    if session_key:
        s = await AgentSession.get_or_none(session_key=session_key, is_deleted=0)
        if s is not None:
            changed_fields: list[str] = []
            if workflow_key and s.workflow_key != workflow_key:
                s.workflow_key = workflow_key
                s.source = "workflow"
                changed_fields.extend(["workflow_key", "source"])
            if expert_key and s.expert_key != expert_key:
                s.expert_key = expert_key
                changed_fields.append("expert_key")
            if changed_fields:
                await s.save(update_fields=changed_fields)
                if "expert_key" in changed_fields:
                    invalidate_skill_sync(_user_workspace(uid))
            return s, s.thread_id
    # 新建会话
    key = f"sess_{secrets.token_hex(6)}"
    s = await AgentSession.create(
        session_key=key,
        user_id=uid,
        title="新任务",
        thread_id=f"qa-{key}",
        source="workflow" if workflow_key else "qa",
        workflow_key=workflow_key,
        expert_key=expert_key,
    )
    if expert_key:
        invalidate_skill_sync(_user_workspace(uid))  # 带专家建会话：专家技能面即时重算
    return s, s.thread_id


# ── 接口：流式问答（SSE） ──────────────────────────────────────────────────────


def _describe_wf_node(n: dict) -> str:
    """生成工作流节点的一行描述（画板全貌注入与「选中节点协作」共用）。"""
    nd = n.get("data") or {}
    ntype = str(n.get("type") or "textNode")
    if ntype == "fileNode":
        desc = f"附件: {nd.get('name', '')}"
    elif ntype in ("startNode", "endNode"):
        desc = str(nd.get("label") or ("开始" if ntype == "startNode" else "结束"))
    elif ntype == "reviewNode":
        q = str(nd.get("question") or "")[:40]
        ans = nd.get("answer")
        desc = f"人工核查: {q}（{'已回答: ' + str(ans) if ans else '待用户回答'}）"
    elif ntype == "taskNode":
        subs = nd.get("subs")
        subs_s = f"（任务 {len(subs)}）" if isinstance(subs, list) and subs else ""
        desc = f"工作项: {nd.get('title') or '(工作项)'}{subs_s}"
    elif ntype == "dataNode":
        metric = nd.get("metric")
        metric_s = f"{metric}{nd.get('unit') or ''}" if metric not in (None, "") else ""
        desc = f"数据: {nd.get('title') or '(数据)'}{(' ' + metric_s) if metric_s else ''}"
    elif ntype == "conclusionNode":
        desc = f"结论: {str(nd.get('claim') or '')[:40]}"
    elif ntype == "segNode":  # 分镜段卡（一卡一场戏）
        head = " ".join(x for x in [str(nd.get("seg") or ""), str(nd.get("duration") or "")] if x)
        shots = nd.get("shots")
        shots_s = f"（分镜 {len(shots)}）" if isinstance(shots, list) and shots else ""
        desc = f"分镜段: {head or '(分镜段)'}{shots_s}"
    else:
        desc = str(nd.get("text") or "")[:40]
    tag = {"startNode": "开始节点", "endNode": "结束节点", "fileNode": "附件节点"}.get(ntype, "")
    return f"{desc} | {tag}" if tag else desc


@router.post("/chat/stream", summary="流式问答（SSE）")
async def qa_chat_stream(request: QARequest):
    """
    流式返回 Agent 的逐步推理过程和最终回答（Server-Sent Events）。

    如果传了 session_key，用户消息与 assistant 最终消息会落到 agent_message。
    首个 SSE 事件固定为 `session`，告知前端本次使用的 sessionKey（便于自动建会话时回填）。

    Agent 执行在独立 asyncio 任务中进行，即使前端断开连接也会完整执行并存库。
    """
    agent = None  # 真正取在确定 session_key 之后

    # 角色判定（两处消费）：① 时间线脱敏豁免——超管/管理员保留原始工具入参细节便于排查，
    # 普通用户的入参经 sanitize_tool_args 脱敏；② 持续精造模式仅管理员
    _rc_uid, _rc_codes, _rc_is_super = await get_current_role_codes()
    timeline_details = _rc_is_super or "R_ADMIN" in _rc_codes
    # 持续精造模式管理员校验：按钮本就只对管理员渲染；非管理员携带标志位时静默降级
    # 为普通消息（不返回 Fail JSON——SSE 端点返回 JSON 会破坏前端流解析，且无提权面）
    sustained_work_on = bool(request.sustained_work)
    if sustained_work_on and not timeline_details:
        logger.warning(f"[qa] 非管理员尝试开启持续精造模式（uid={_rc_uid}），已降级为普通消息")
        sustained_work_on = False

    session, thread_id = await _ensure_session(request.session_key, request.workflow_key, (request.expert_key or "").strip() or None)
    if request.thread_id and not request.session_key:
        thread_id = request.thread_id  # 兼容老用法

    # @召唤解析（技能加载 + 专家驻留）：必须在取 agent 之前——专家改绑影响 agent 形态（cache_key）。
    # 同 token 既命中技能又命中专家时优先技能（@技能键 与 @专家名 天然不同形，冲突极少）。
    uid = CTX_USER_ID.get() or None
    hit_skills = await resolve_skills_from_text(request.message, uid)
    summoned_expert = None
    if session is not None and "@" in request.message:
        from app.api.v1.ai.agent_expert import resolve_experts_from_text

        _skill_tokens = {sk.skill_key for sk in hit_skills}
        _hit_experts = await resolve_experts_from_text(request.message, uid)
        summoned_expert = next(
            (e for e in _hit_experts if e.name not in _skill_tokens and e.expert_key not in _skill_tokens),
            None,
        )
        if summoned_expert is not None and session.expert_key != summoned_expert.expert_key:
            # @专家名 → 会话驻留改绑（直到移除或被 @ 另一专家覆盖）
            session.expert_key = summoned_expert.expert_key
            await session.save(update_fields=["expert_key", "update_time"])
            invalidate_skill_sync(_user_workspace(uid))  # 专家技能面随本条消息即时重算

    session_key_for_agent = session.session_key if session else _session_key_from_thread(thread_id)
    uid_for_mem = CTX_USER_ID.get() or 0
    agent = await _get_agent_for_session(session_key_for_agent, CTX_USER_ID.get() or None)
    config = {"configurable": {"thread_id": thread_id, "user_id": str(uid_for_mem)}}

    # 先落 user 消息
    user_msg: Optional[AgentMessage] = None
    if session is not None:
        attachments_data = None
        if request.files:
            attachments_data = [{"name": Path(f).name, "path": f, "size": 0, "isImage": Path(f).suffix.lower() in _IMAGE_EXTS} for f in request.files]
            # 尝试补充真实文件大小
            for att in attachments_data:
                try:
                    real_path = _user_workspace(CTX_USER_ID.get() or None) / att["path"]
                    if real_path.exists():
                        att["size"] = real_path.stat().st_size
                except Exception:
                    pass
        user_msg = await AgentMessage.create(
            session_id=session.id,
            role="user",
            content=request.message,
            status="done",
            attachments_json=attachments_data,
        )
        session.message_count = session.message_count + 1
        if (not session.title or session.title in ("新对话", "新任务")) and request.message.strip():
            session.title = request.message.strip()[:36]
        await session.save()

    # 预留 assistant 消息占位
    assistant_msg: Optional[AgentMessage] = None
    if session is not None:
        assistant_msg = await AgentMessage.create(
            session_id=session.id,
            role="assistant",
            content="",
            status="streaming",
        )
        session.message_count = session.message_count + 1
        await session.save(update_fields=["message_count", "update_time"])

    # 命中 @skill：注入到 agent 输入（hit_skills 已在取 agent 前解析，供 @专家判定共用）
    workspace = _user_workspace(uid)
    injected_prompt = await build_skill_injection(hit_skills, uid, workspace_dir=workspace)

    agent_input = f"{injected_prompt}{request.message}" if injected_prompt else request.message

    # 持续精造模式指令注入（仅管理员，入口已校验；与 workflow 画板规则同层前置）
    if sustained_work_on:
        agent_input = _SUSTAINED_WORK_RULES + agent_input

    # 注入「当前打开的工作流」上下文（工作流画板页面）：让 Agent 直接操作该 key，无需 list_workflows
    if request.workflow_key:
        from app.models.standard.agent import AgentWorkflow

        wf = await AgentWorkflow.get_or_none(workflow_key=request.workflow_key, user_id=uid, is_deleted=0)
        if wf and (wf.board_type or "board") == "html":
            # 应用制作任务：注入开发者模式规则 + 任务目录上下文（通用 prompt 保持克制，场景规则随消息注入）。
            # 节点 / 选中 / scope 等流程编排概念对 html 型不适用，全部跳过。
            from app.langchain.tools.workflow_tools import HTML_BOARD_RULES

            app_dir = workspace / "apps" / wf.workflow_key
            # 跨机惰性物化：已发布版本的文件在 DB（agent_app_file），本机目录缺入口就从 DB 捞回来落盘，
            # 再让 agent 开工——agent 看到的永远是文件齐全的工作目录，入库机制对它完全透明。
            # 失败不阻塞（目录实况照常注入，agent 至多看到空目录）
            try:
                from app.api.v1.ai.agent_workflow import ensure_app_files

                await ensure_app_files(uid, wf.workflow_key)
            except Exception as e:  # noqa: BLE001 —— 物化是增强路径，失败保持原行为
                logger.warning(f"[qa] 应用制作文件物化失败 {wf.workflow_key}: {e!r}")

            def _list_app_files() -> list:
                out: list = []
                upload_count = 0
                if not app_dir.is_dir():
                    return out
                archive_files = 0
                for p in sorted(app_dir.rglob("*")):
                    if not p.is_file() or p.name.endswith(".tmp"):
                        continue
                    rel = p.relative_to(app_dir).as_posix()
                    if rel.startswith("uploads/"):  # 页面上传的文件只计数，避免把应用文件挤出上下文
                        upload_count += 1
                        continue
                    if rel.startswith(".versions/"):  # 发布存档只计数（回滚由 rollback_workflow_version 负责）
                        archive_files += 1
                        continue
                    out.append(f"- {rel}（{p.stat().st_size} B）")
                    if len(out) >= 50:
                        break
                if upload_count:
                    out.append(f"- uploads/（{upload_count} 个用户上传文件，需要时可用 ls / read_file 查看）")
                if archive_files:
                    out.append(f"- .versions/（{archive_files} 个发布存档文件，回滚用，勿读写；回滚调 rollback_workflow_version）")
                return out

            file_lines = await asyncio.to_thread(_list_app_files)
            files_desc = "\n".join(file_lines) if file_lines else "（目录为空，请先写入口 index.html）"
            if wf.share_on:
                _share_desc = "已开启·免登录模式（访客全部匿名，whoami 拿不到访客身份）" if wf.share_public else "已开启·仅登录用户模式（whoami 可识别访客身份）"
            else:
                _share_desc = "未开启（访客无法打开；多用户应用需用户在画板顶栏「分享」开启并选「仅登录用户」）"
            html_ctx = (
                "[用户当前打开了一个「应用制作」任务]\n"
                f"workflow_key: {wf.workflow_key}\n"
                f"标题: {wf.title}\n"
                f"分享状态: {_share_desc}\n"
                f"应用目录: apps/{wf.workflow_key}/（相对工作目录）\n"
                f"当前文件：\n{files_desc}\n"
                f"用户提到的「看板」「页面」「应用」即指它。写完/改完文件后务必调用 "
                f"publish_html_board(workflow_key={wf.workflow_key}) 发布，用户画布才会更新。\n\n"
            )
            agent_input = HTML_BOARD_RULES + html_ctx + agent_input
        elif wf:
            node_lines = [f"- id={n.get('id')} | {_describe_wf_node(n)}" for n in wf.nodes or []]
            nodes_desc = "\n".join(node_lines) if node_lines else "（暂无节点）"
            # 仅当用户从工作流画板页面发起对话时，才注入详细协作规则（通用 prompt 保持克制）
            from app.langchain.tools.workflow_tools import WORKFLOW_BOARD_RULES

            workflow_ctx = (
                "[用户当前在「工作流画板」页面打开了一个工作流]\n"
                f"workflow_key: {wf.workflow_key}\n"
                f"标题: {wf.title}\n"
                f"当前节点：\n{nodes_desc}\n"
                f"用户提到的「这个工作流」「当前工作流」即指它，请直接用 workflow_key={wf.workflow_key} "
                "调用工作流工具读写，不要再用 list_workflows 查找。\n\n"
            )
            # 选中节点协作（画布底部迷你输入栏）：把可编辑范围随消息注入；
            # 真正的越界拦截在 edit_workflow_board（读 AgentCallContext.workflow_scope 软强制）。
            by_id = {str(n.get("id")): n for n in wf.nodes or []}
            scope_ids = request.scope_node_ids or []
            if scope_ids:
                focus = scope_ids[0]
                focus_desc = _describe_wf_node(by_id[focus]) if focus in by_id else "（该卡）"
                scope_lines = [f"- id={sid} | {_describe_wf_node(by_id[sid])}" for sid in scope_ids if sid in by_id]
                workflow_ctx += (
                    "[选中节点协作]\n"
                    f"用户从板子底部针对单张卡发起局部协作。焦点卡: id={focus}（{focus_desc}）。\n"
                    "可编辑范围 = 该卡 + 与之直接相连的卡：\n" + "\n".join(scope_lines) + "\n"
                    "- 改动只落在这个范围内：改这些卡、在它们周围增卡/撤卡、增删与它们相连的连线；"
                    "范围外的卡只读（越界改动会被自动跳过，并在工具返回的 skipped_scope 中列出，"
                    "照报告向用户说明即可，别反复重试）。\n"
                    "- read_workflow 看的仍是全板：先看清整体结构，再决定范围内怎么改。\n"
                    "- 回复简短：改了什么、一两句交代即可，细节用户直接在板上看。\n\n"
                )
            # 画板选中的节点：用户框选/点选的卡随消息告知（是「当前看着的卡」的上下文，不是可编辑范围约束）
            selected_ids = request.selected_node_ids or []
            if selected_ids:
                sel_lines = [f"- id={sid} | {_describe_wf_node(by_id[sid])}" for sid in selected_ids if sid in by_id]
                if sel_lines:
                    workflow_ctx += "[用户选中的节点]\n用户当前在画板选中了以下卡（这是 ta 正看着的卡，回答时优先围绕它们，涉及改动时也优先处理这些卡）：\n" + "\n".join(sel_lines) + "\n\n"
            agent_input = WORKFLOW_BOARD_RULES + workflow_ctx + agent_input
    elif session is not None and session.workflow_key:
        # 会话已归属一块板但本条消息没带（用户收起了面板 / 从历史会话恢复）：
        # 告知 agent 这块既有板的存在——当前话题相关时直接复用该 key 继续上板干活，别重复建板。
        from app.models.standard.agent import AgentWorkflow

        _sess_wf = await AgentWorkflow.get_or_none(workflow_key=session.workflow_key, user_id=uid, is_deleted=0)
        if _sess_wf:
            _bt = _sess_wf.board_type or "board"
            if _bt == "html":
                # 应用制作物化文件（与打开面板同款机制），防 agent 复用开工时读到空目录；失败不阻塞
                try:
                    from app.api.v1.ai.agent_workflow import ensure_app_files

                    await ensure_app_files(uid, _sess_wf.workflow_key)
                except Exception as e:  # noqa: BLE001 —— 物化是增强路径，失败保持原行为
                    logger.warning(f"[qa] 应用制作文件物化失败（会话归属板）{_sess_wf.workflow_key}: {e!r}")
            _board_kind = (
                "应用制作（html 页面型，文件在 apps/<key>/ 目录，改完用 publish_html_board 发布）" if _bt == "html" else "流程编排板（卡片+连线型，用 read_workflow / edit_workflow_board 读写）"
            )
            agent_input = (
                "[本会话已归属一块板]\n"
                f"workflow_key: {_sess_wf.workflow_key}，标题: {_sess_wf.title}，板型: {_board_kind}（用户可能收起了面板）。\n"
                "当前话题与这块板相关时，直接用该 workflow_key 调工作流工具继续上板干活；话题明确不搭时才考虑 create_workflow_board 另建。\n\n"
            ) + agent_input

    # 注入上传文件路径提示（dsh 内核：无图片通道，光栅图走后台预解读注入，见 _prime_image_descriptions）
    if request.files:
        from app.langchain.config import has_role
        from app.langchain.role_model_profile import effective_chat_supports_vision

        # 本次请求生效的 chat 块是否支持视觉（决定图片走哪条理解通道，跟随角色模型配置）
        supports_vision = effective_chat_supports_vision()

        hint_parts: list[str] = []
        _media_exts = _IMAGE_EXTS + _VIDEO_EXTS
        other_files = [f for f in request.files if Path(f).suffix.lower() not in _media_exts]
        image_files = [f for f in request.files if Path(f).suffix.lower() in _IMAGE_EXTS]
        video_files = [f for f in request.files if Path(f).suffix.lower() in _VIDEO_EXTS]
        if other_files:
            file_list = "\n".join(f"- {f}" for f in other_files)
            hint_parts.append(f"[用户上传了以下文件（路径已是相对工作目录），可直接用 read_file 工具读取]\n{file_list}")
        if image_files:
            if supports_vision:
                # 光栅图由后台任务用多模态主模型预解读后注入上下文（_prime_image_descriptions），
                # 此处不留提示；svg 是文本格式不走预解读，留给 read_file
                svg_files = [f for f in image_files if Path(f).suffix.lower() == ".svg"]
                if svg_files:
                    svg_list = "\n".join(f"- {f}" for f in svg_files)
                    hint_parts.append(f"[用户上传了以下 SVG 图片（文本格式，可直接用 read_file 读取）]\n{svg_list}")
            elif has_role("VISION"):
                # 主模型纯文本但配置了 VISION 角色：走 vision_inspect 工具（deepagents 时代的兜底通道）
                img_list = "\n".join(f"- {f}" for f in image_files)
                hint_parts.append(f"[用户上传了 {len(image_files)} 张图片（相对工作目录路径）。当前主模型不支持视觉，请调用 vision_inspect 工具查看图片内容（传文件路径和想问的问题）]\n{img_list}")
            else:
                img_list = "\n".join(f"- {f}" for f in image_files)
                hint_parts.append(f"[用户上传了以下图片，但当前主模型不支持视觉、无法查看图片内容，请在回复中如实告知用户（不要尝试读取）：]\n{img_list}")
        if video_files:
            # dsh 阶段暂无视频理解通道（deepagents 时代的 video_url 直传已下线）
            video_list = "\n".join(f"- {Path(f).name}" for f in video_files)
            hint_parts.append(f"[用户上传了以下视频，当前系统暂不支持分析视频内容，请在回复中如实告知用户（不要尝试读取）：]\n{video_list}")
        if hint_parts:
            agent_input = "\n\n".join(hint_parts) + "\n\n" + agent_input

    # workspace 已在上方 skill 注入时提前创建（_user_workspace(uid)）

    # 创建事件队列用于后台任务和 SSE 流之间通信
    event_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

    # 创建取消事件
    cancellation_event = asyncio.Event()
    if session:
        _prev_event = _active_task_cancellations.get(session.session_key)
        if _prev_event is not None and not _prev_event.is_set():
            # 旧任务仍在跑（dsh 回合无法取消）：新旧回合短暂并存，
            # agent 层会检测到同会话占用并为新消息升代换新 dsh 会话
            logger.warning(f"[chat/stream] session={session.session_key} 上一回合尚未结束即收到新消息，新回合将另起 dsh 会话")
        _active_task_cancellations[session.session_key] = cancellation_event
        logger.info(f"[chat/stream] 注册取消事件 session={session.session_key}, 当前活跃tasks={list(_active_task_cancellations.keys())}")

    # 用 asyncio.create_task 立即启动，与 event_generator 并发执行
    # （BackgroundTasks 在 response 结束后才跑，会死锁）
    _bg_task = asyncio.create_task(
        _execute_agent_in_background(
            agent=agent,
            agent_input=agent_input,
            config=config,
            session=session,
            user_msg=user_msg,
            assistant_msg=assistant_msg,
            request=request,
            event_queue=event_queue,
            workspace=workspace,
            cancellation_event=cancellation_event,
            expert_summoned=summoned_expert is not None,
            timeline_details=timeline_details,
        )
    )
    _background_tasks.add(_bg_task)
    _bg_task.add_done_callback(_background_tasks.discard)

    async def event_generator() -> AsyncGenerator[str, None]:
        """SSE 事件生成器：从事件队列读取并推送给前端"""
        try:
            while True:
                try:
                    # 等待事件，超时 30 秒发送心跳
                    event = await asyncio.wait_for(event_queue.get(), timeout=30.0)
                    if event is None:  # 结束标记
                        break
                    yield event
                except asyncio.TimeoutError:
                    # 发送心跳保持连接
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            # 前端断开连接，但后台任务会继续执行
            logger.info(f"SSE 连接断开 session={session.session_key if session else '-'}, 后台任务继续执行")
            raise

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── 接口：同步问答（备用） ────────────────────────────────────────────────────


@router.post("/chat", summary="同步问答")
async def qa_chat(request: QARequest):
    """
    同步返回最终回答（等待 Agent 完成后一次性返回）。
    注意：响应时间较长，建议使用流式接口。
    """
    try:
        from langchain_core.messages import AIMessage

        agent = await _get_agent_for_session(
            _session_key_from_thread(request.thread_id or "qa-anonymous"),
            CTX_USER_ID.get() or None,
        )
        thread_id = request.thread_id or "qa-anonymous"
        uid_for_mem = CTX_USER_ID.get() or 0
        config = {"configurable": {"thread_id": thread_id, "user_id": str(uid_for_mem)}}

        answer = ""
        steps = 0

        # 持续精造模式（与流式路径同款注入；管理员校验同样生效，防旁路行为分裂）
        _msg = request.message
        if request.sustained_work:
            _uid_c, _codes_c, _is_super_c = await get_current_role_codes()
            if _is_super_c or "R_ADMIN" in _codes_c:
                _msg = _SUSTAINED_WORK_RULES + _msg

        # 用 astream 避免同步 stream 阻塞 event loop
        async for chunk in agent.astream(
            {"messages": [{"role": "user", "content": _msg}]},
            config=config,
            stream_mode="messages",
        ):
            msg = chunk[0] if isinstance(chunk, tuple) else chunk
            if isinstance(msg, AIMessage):
                steps += 1
                content = msg.content
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                text = (content or "").strip()
                if text and not msg.tool_calls:
                    answer = text

        if not answer:
            return Fail(code="5001", msg="Agent 未生成有效回答")

        return Success(data={"answer": answer, "steps": steps, "thread_id": thread_id}, msg="回答成功")

    except Exception as e:
        logger.exception(f"同步问答失败：{request.message[:50]}")
        return Fail(code="5000", msg=f"问答失败：{str(e)}")


# ── 接口：重置对话 ────────────────────────────────────────────────────────────


@router.post("/reset", summary="清除对话历史")
async def qa_reset(request: QAResetRequest):
    """
    清除指定 thread_id 的对话历史，开启全新对话。
    """
    try:
        agent = await _get_agent_for_session(
            _session_key_from_thread(request.thread_id),
            CTX_USER_ID.get() or None,
        )
        config = {"configurable": {"thread_id": request.thread_id}}
        # update_state 内部走 SQLite 写盘，扔线程池避免阻塞
        await asyncio.to_thread(agent.update_state, config, {"messages": []})
        return Success(data=None, msg=f"会话 {request.thread_id} 已重置")
    except Exception as e:
        logger.exception(f"重置对话失败：thread_id={request.thread_id}")
        return Fail(code="5000", msg=f"重置失败：{str(e)}")


# ── 接口：每日简报 ────────────────────────────────────────────────────────────

# 进程内锁：key = f"{user_id}_{date}"，防止同一用户同一天并发重复触发
_daily_brief_locks: dict[str, asyncio.Lock] = {}


def _get_brief_lock(user_id: int, today: "date") -> asyncio.Lock:
    key = f"{user_id}_{today}"
    if key not in _daily_brief_locks:
        _daily_brief_locks[key] = asyncio.Lock()
    return _daily_brief_locks[key]


@router.post("/daily-brief/stream", summary="生成每日简报（SSE）")
async def daily_brief_stream():
    """
    每日简报：Agent 自动分析用户历史会话，识别持续关注话题，主动搜索最新信息，生成今日简报。

    防重复机制：同一用户同一天只生成一次，后续请求直接返回已生成内容。
    """
    from datetime import date

    from app.models.standard.agent import AgentDailyBrief

    user_id = CTX_USER_ID.get()
    if not user_id:
        return StreamingResponse(
            _sse_error("需要登录"),
            media_type="text/event-stream",
        )

    today = date.today()

    # 进程内锁：确保同一用户同一天的 check-and-create 是原子操作，防止并发重复触发
    brief_lock = _get_brief_lock(user_id, today)

    import json as _json

    logger.info(f"daily brief: user_id={user_id} date={today} acquiring lock")
    async with brief_lock:
        existing = await AgentDailyBrief.filter(user_id=user_id, brief_date=today).first()
        logger.info(f"daily brief: existing={existing} status={getattr(existing, 'generation_status', None) if existing else None}")

        # ── 已成功生成，返回缓存 ──────────────────────────────────────────────
        if existing and existing.generation_status == "done" and existing.content_html:
            cached_data: dict = {}
            if existing.content_json:
                try:
                    cached_data = _json.loads(existing.content_json) if isinstance(existing.content_json, str) else existing.content_json
                except Exception:
                    pass

            if not cached_data.get("top_html") and not cached_data.get("middle_html"):
                import re as _re

                def _extract_cached(marker: str, content: str) -> str:
                    m = _re.search(marker + r"\s*```html\s*(.*?)\s*```", content, _re.DOTALL)
                    if m:
                        return m.group(1).strip()
                    m2 = _re.search(marker + r"\s*(.*?)(?=<!--\s*DAILY_BRIEF_|\Z)", content, _re.DOTALL)
                    if m2:
                        raw = m2.group(1).strip()
                        raw = _re.sub(r"^```\w*\s*|\s*```$", "", raw).strip()
                        return raw
                    return ""

                top_html = _extract_cached("<!-- DAILY_BRIEF_TOP -->", existing.content_html)
                middle_html = _extract_cached("<!-- DAILY_BRIEF_MIDDLE -->", existing.content_html)
                sm = _re.search(r"<!-- DAILY_BRIEF_SKILLS -->\s*```json\s*(.*?)\s*```", existing.content_html, _re.DOTALL)
                if not sm:
                    sm = _re.search(r"<!-- DAILY_BRIEF_SKILLS -->.*?<script[^>]*>(.*?)</script>", existing.content_html, _re.DOTALL)
                if not sm:
                    sm = _re.search(r"<!-- DAILY_BRIEF_SKILLS -->\s*(\[.*?\])", existing.content_html, _re.DOTALL)
                skills_raw = sm.group(1).strip() if sm else "[]"
                try:
                    parsed = _json.loads(skills_raw)
                    sl = next((v for v in parsed.values() if isinstance(v, list)), []) if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else [])
                    skills_list = [{"display": s.get("display") or s.get("name") or "", "prompt": s.get("prompt", "")} for s in sl if isinstance(s, dict) and (s.get("display") or s.get("name"))]
                except Exception:
                    skills_list = []
                cached_data = {"top_html": top_html, "middle_html": middle_html, "skills": skills_list}
                existing.content_json = _json.dumps(cached_data, ensure_ascii=False)
                await existing.save()

            _cached = dict(cached_data)

            async def _cached_generator():
                yield _sse({"type": "cached", "brief_date": str(today)})
                yield _sse({"type": "section", "name": "top", "html": _cached.get("top_html", "")})
                if _cached.get("middle_html"):
                    yield _sse({"type": "section", "name": "middle", "html": _cached["middle_html"]})
                yield _sse({"type": "skills", "items": _cached.get("skills", [])})
                yield _sse({"type": "done"})

            return StreamingResponse(_cached_generator(), media_type="text/event-stream")

        # ── 正在生成中且未超时，拒绝重复触发 ────────────────────────────────
        if existing and existing.generation_status == "generating":
            from datetime import datetime, timezone

            ct = existing.create_time
            if ct:
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=timezone.utc)
                age_min = (datetime.now(timezone.utc) - ct).total_seconds() / 60
            else:
                age_min = 0
            if age_min < 30:

                async def _generating_generator():
                    yield _sse({"type": "generating", "brief_date": str(today)})
                    yield _sse({"type": "done"})

                return StreamingResponse(_generating_generator(), media_type="text/event-stream")
            # 超时：视为失败，允许重新生成

        # ── 新建或重试失败/超时的记录 ────────────────────────────────────────
        prev_brief = await AgentDailyBrief.filter(user_id=user_id, generation_status="done").order_by("-brief_date").first()
        prev_brief_id = prev_brief.id if prev_brief else None

        if existing:
            existing.generation_status = "generating"
            existing.error = None
            await existing.save()
            brief_record_init: AgentDailyBrief = existing
        else:
            brief_record_init = await AgentDailyBrief.create(
                user_id=user_id,
                brief_date=today,
                prev_brief_id=prev_brief_id,
                generation_status="generating",
            )

    # 锁外：异步流式生成（可能耗时数分钟，不能持有锁）
    async def event_generator() -> AsyncGenerator[str, None]:
        brief_record: Optional[AgentDailyBrief] = brief_record_init

        try:
            yield _sse({"type": "start", "brief_date": str(today)})

            thread_id = f"daily-brief-{user_id}-{today}"
            workspace = _user_workspace(user_id)
            # 用户对话偏好（模式块 + 思考强度）同样作用于每日简报；未配置模式块时
            # _b_block=None → create_qa_agent 走全局激活块，现状不变
            from app.langchain.chat_mode import resolve_user_chat_pref

            _b_mode, _b_block, _b_level = await resolve_user_chat_pref(user_id)

            # dsh 内核：brief 专属工具（get_prev_daily_brief 等）暂未接入（阶段 2 走 MCP 桥），
            # 共享资源加载一并跳过，避免构建期外部 MCP 握手阻塞
            def _build():
                return create_qa_agent(
                    root_dir=str(workspace),
                    user_id=user_id,
                    chat_block_key=_b_block,
                    thinking_level=_b_level,
                )

            agent = await asyncio.to_thread(_build)

            config = {"configurable": {"thread_id": thread_id, "user_id": str(user_id)}}

            agent_input = """你是每日简报助手。请利用可用工具充分了解用户近期的对话历史和关注重点（包括 get_prev_daily_brief、get_recent_sessions 等），深度分析用户真正想解决的问题或达成的目标（不是话题标签，而是意图：用户在研究什么决策？在解决什么困难？在追踪什么机会？），结合 WebSearch 获取最新进展，生成简报。

**若用户没有对话记录**：不要输出空内容，改为根据当天日期和当前热点话题（通过 WebSearch 获取），生成一份通用的科技/市场早报，并在简报顶部用一句话告知用户"还没有对话记录，这是今日精选"。

## 输出格式（必须严格执行）

<!-- DAILY_BRIEF_TOP -->
```html
<!DOCTYPE html>...完整简报内容...
```

## 设计原则

**图表优先**：凡是有数据的地方，优先用图表展示，大字数字卡片次之，纯文字描述数字是最后选择。每个话题至少有一个图表或数字卡片。

**结论前置，分析隐藏**：结论是针对用户具体目标的直接判断（"你现在应该做X，因为Y"），不是新闻摘要。分析过程默认折叠，用户点击展开。

**每天样式不同**：在规范内自由发挥，给用户新鲜感。

## HTML 技术规范

遵循本系统已有的 HTML 输出规范（内嵌展示、去 AI 味原则等），额外注意：
- 数据必须真实，不能伪造
- 风格以冷色系为主，**禁止暖黄/土色/咖啡/纯黑色系**
- 布局按宽屏设计，body 不设 max-width 限制，内容横向铺满
- **禁用 Emoji**：简报里不用任何 emoji / 表情符号，需要图标时用内联 SVG
- 直接返回html文本内容，不需要提交artifact注册

"""

            final_ai_content = ""
            collected_stream: list[str] = []
            async for namespace, stream_mode, chunk in agent.astream(
                {"messages": [{"role": "user", "content": agent_input}]},
                config=config,
                stream_mode=["messages", "updates"],
                subgraphs=True,
            ):
                is_subagent = bool(namespace)
                if stream_mode == "messages":
                    token, metadata = chunk
                    # 过滤 LangGraph 对话历史压缩产物（如 "## SESSION INTENT"），避免混入简报输出
                    if metadata.get("lc_source") == "summarization":
                        continue
                    node = metadata.get("langgraph_node", "")
                    content = getattr(token, "content", "") or ""
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    if not is_subagent and node == "model" and content:
                        collected_stream.append(content)
                        yield _sse({"type": "chunk", "content": content})
                elif stream_mode == "updates" and not is_subagent:
                    for node, node_update in chunk.items():
                        if node != "model" or not node_update:
                            continue
                        for msg in node_update.get("messages") or []:
                            msg_content = getattr(msg, "content", "") or ""
                            if isinstance(msg_content, list):
                                msg_content = " ".join(c.get("text", "") for c in msg_content if isinstance(c, dict))
                            if msg_content.strip() and not (getattr(msg, "tool_calls", None) or []):
                                final_ai_content = msg_content.strip()

            stream_content = "".join(collected_stream)
            full_content = final_ai_content or stream_content
            logger.info(f"daily brief: collected {len(full_content)} chars (updates={len(final_ai_content)}, stream={len(stream_content)})")

            import re

            def _extract_section(marker: str, content: str) -> str:
                m = re.search(marker + r"\s*```html\s*(.*?)\s*```", content, re.DOTALL)
                if m:
                    return m.group(1).strip()
                m2 = re.search(marker + r"\s*(.*?)(?=<!--\s*DAILY_BRIEF_|\Z)", content, re.DOTALL)
                if m2:
                    raw = m2.group(1).strip()
                    raw = re.sub(r"^```\w*\s*|\s*```$", "", raw).strip()
                    return raw
                return ""

            def _parse_sections(content: str):
                top = _extract_section("<!-- DAILY_BRIEF_TOP -->", content)
                mid = _extract_section("<!-- DAILY_BRIEF_MIDDLE -->", content)
                sk = re.search(r"<!-- DAILY_BRIEF_SKILLS -->\s*```json\s*(.*?)\s*```", content, re.DOTALL)
                if not sk:
                    sk = re.search(r"<!-- DAILY_BRIEF_SKILLS -->.*?<script[^>]*>(.*?)</script>", content, re.DOTALL)
                if not sk:
                    sk = re.search(r"<!-- DAILY_BRIEF_SKILLS -->\s*(\[.*?\])", content, re.DOTALL)
                return top, mid, sk

            top_html, middle_html, skills_match = _parse_sections(full_content)

            # 如果 top 或 skills 在 updates 里找不到，再试完整 stream（HTML 可能生成在更早的轮次）
            if (not top_html or not skills_match) and stream_content and stream_content != full_content:
                logger.info("daily brief: updates 解析不完整，尝试 stream 内容")
                s_top, s_mid, s_sk = _parse_sections(stream_content)
                if s_top:
                    top_html = s_top
                if s_mid:
                    middle_html = s_mid
                if s_sk:
                    skills_match = s_sk
                if s_top or s_sk:
                    full_content = stream_content

            # middle 若为空占位（<div></div> 之类），直接置空，避免前端渲染白板
            if middle_html and not re.search(r"<(?:p|h[1-6]|div[^>]*class|section|article|table|ul|ol|img|svg)", middle_html, re.I):
                middle_html = ""

            skills_json_raw = skills_match.group(1).strip() if skills_match else "[]"

            # 清理模型把两段放进同一个 HTML 文档时产生的残留闭合标签
            # top_html 可能是开了 <!DOCTYPE html> 但没有 </body></html> 的半截文档——补全
            if top_html and re.search(r"<!doctype\s+html", top_html, re.I) and not re.search(r"</body>", top_html, re.I):
                top_html = top_html + "\n</body></html>"
            # middle_html 可能带着孤立的 </body></html> 结尾——strip 掉
            if middle_html and not re.search(r"<body[\s>]", middle_html, re.I):
                middle_html = re.sub(r"\s*</body>\s*</html>\s*$", "", middle_html, flags=re.I).strip()

            if not top_html and not middle_html:
                logger.warning("daily brief: 三段解析全部失败，使用原始内容 fallback")
                top_html = f'<div style="white-space:pre-wrap;font-family:sans-serif;font-size:14px;line-height:1.8;color:#334155;padding:8px 0">{full_content}</div>'

            import json

            try:
                parsed = json.loads(skills_json_raw)
                if isinstance(parsed, dict):
                    skills_list = next((v for v in parsed.values() if isinstance(v, list)), [])
                else:
                    skills_list = parsed if isinstance(parsed, list) else []
                normalized = []
                for s in skills_list:
                    if isinstance(s, dict):
                        display = s.get("display") or s.get("name") or s.get("title") or s.get("skill") or ""
                        prompt = s.get("prompt") or (f"@{display} " if display else "")
                        if display:
                            normalized.append({"display": display, "prompt": prompt})
                skills_list = normalized
            except Exception:
                skills_list = []

            brief_record.content_html = full_content
            brief_record.content_json = json.dumps({"top_html": top_html, "middle_html": middle_html, "skills": skills_list}, ensure_ascii=False)
            brief_record.generation_status = "done"
            await brief_record.save()
            logger.info(f"daily brief: saved id={brief_record.id} status=done top={len(top_html)} middle={len(middle_html)}")

            yield _sse({"type": "section", "name": "top", "html": top_html})
            if middle_html:
                yield _sse({"type": "section", "name": "middle", "html": middle_html})
            yield _sse({"type": "skills", "items": skills_list})
            yield _sse({"type": "done"})

        except Exception as e:
            logger.exception("生成每日简报失败")
            if brief_record:
                brief_record.generation_status = "error"
                brief_record.error = str(e)[:2000]
                await brief_record.save()
            yield _sse({"type": "error", "message": str(e)})
        finally:
            if brief_record and brief_record.generation_status == "generating":

                async def _fix_status():
                    try:
                        brief_record.generation_status = "error"
                        brief_record.error = "generation_interrupted"
                        await brief_record.save()
                    except Exception:
                        pass

                asyncio.ensure_future(_fix_status())

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def _sse_error(msg: str) -> AsyncGenerator[str, None]:
    """SSE 错误流"""
    yield _sse({"type": "error", "message": msg})
