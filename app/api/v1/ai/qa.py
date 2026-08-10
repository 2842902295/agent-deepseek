"""
通用标准问答 API

支持多轮流式对话，Agent 可自主查询数据库回答用户问题。
会话与消息持久化到 agent_session / agent_message。
"""

import asyncio
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

from app.api.v1.ai.agent_skill import build_skill_injection, resolve_skills_from_text
from app.core.ctx import CTX_USER_ID
from app.langchain.agents.qa_agent import create_qa_agent
from app.models.standard.agent import AgentMessage, AgentSession
from app.schemas.base import Fail, Success
from app.services.agent_runtime.call_context import (
    AgentCallContext,
    clear_agent_call_context,
    set_agent_call_context,
)
from app.services.agent_runtime.tool_display_names import get_tool_display_name

router = APIRouter(prefix="/qa", tags=["标准问答"])

# 图片后缀集合：用于识别附件是否是图片
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp")
_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")
# 过大的视频不直传（读进内存 + 上传 + 模型拉取都会超时）
_MAX_VIDEO_INLINE_BYTES = 100 * 1024 * 1024

# 「呈现型」工具：主 agent 写完答案正文后，顺手调用它们来配图 / 登记产物，
# 不会取回任何新信息。与这类工具同条消息出现的文本就是答案本身，
# reclassify（正文转 thinking）时必须跳过，否则实质答案会被整段丢进思考过程。
# 相对地，task / 查库 / 联网搜索等「干活型」工具会取新信息，与它们同条消息的
# 文本是"我去干活了"的铺垫，照常转 thinking。
_PRESENTATION_TOOLS = {"create_chart", "register_artifact"}

# 活跃任务取消令牌：session_key -> asyncio.Event（用于停止后台任务）
_active_task_cancellations: dict[str, asyncio.Event] = {}

# ── Workspace 与 Skill 路径 ───────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
_WORKSPACE_ROOT = _PROJECT_ROOT / ".agent_workspace"
_GLOBAL_SKILLS_DIR = _WORKSPACE_ROOT / ".agent_skills"
_PROJECT_SKILLS_SUBDIR = "_project"
_USERS_ROOT = _WORKSPACE_ROOT / "users"  # 持久层：users/{user_id}/
_SESSIONS_ROOT = _WORKSPACE_ROOT / "sessions"  # 兼容旧路径，仅用于 get_session_workspace 兜底


def _user_workspace(user_id: Optional[int]) -> Path:
    """用户持久工作目录：users/{user_id}/，沙箱根，跨 session 共享。"""
    uid = str(user_id) if user_id else "anonymous"
    ws = _USERS_ROOT / uid
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


async def _get_visible_skill_keys(user_id: Optional[int]) -> set[str]:
    """当前用户可见的 skill_key 集合：
    - 内置 skill：直接落在 .agent_skills/ 或 .agent_skills/_project/ 下、未登记到 DB 的目录，所有用户默认可用
    - DB 登记的：公共（user_id=null）+ 自己创建的
    """
    from app.models.standard.agent import AgentSkill

    # 1) 磁盘上所有 skill 目录（顶层 portable + _project/ 下 project-bound）
    builtin: set[str] = set(_iter_global_skill_dirs().keys())

    # 2) DB 里有记录的：取所有，再过滤可见性
    db_visible: set[str] = set()
    db_known: set[str] = set()  # 所有 DB 中有记录的 key（无论可见与否）
    rows = await AgentSkill.filter(is_enabled=1).all()
    for r in rows:
        db_known.add(r.skill_key)
        if r.user_id is None or r.user_id == user_id:
            db_visible.add(r.skill_key)

    # 3) 内置 skill = 磁盘存在但 DB 里完全没记录的；这部分所有人可见
    builtin_only = builtin - db_known

    return builtin_only | db_visible


def _sync_session_skills(workspace: Path, allowed_keys: set[str]) -> None:
    """把全局 .agent_skills/ 增量硬链到 workspace/.agent_skills/，按 allowed_keys 过滤。

    - 已是同一 inode 的文件直接跳过
    - 源文件被改/被替换：重建硬链
    - 源文件不存在但目标还在：删除目标
    - allowed_keys 之外的本地 skill 目录：整个删除
    """
    target_root = workspace / ".agent_skills"
    # 防御：旧版本可能在这里建了 symlink，必须先 unlink，否则下方 iterdir/rmtree 会
    # 顺着软链操作到全局 skill 目录，造成误删！
    if target_root.is_symlink() or (target_root.exists() and not target_root.is_dir()):
        try:
            target_root.unlink()
        except OSError as e:
            logger.warning(f"清理旧 .agent_skills 软链失败：{e}")
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

# skill 同步节流：全量硬链同步开销大（本地实测约数秒），每用户 workspace 最多 60s 一次。
# 代价：新发布/撤销的 skill 最长 60s 后生效。
_SKILL_SYNC_INTERVAL = 60.0
_skill_last_sync: dict[Path, float] = {}


async def _get_or_create_build_lock(cache_key: str) -> asyncio.Lock:
    async with _agent_build_locks_lock:
        lock = _agent_build_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            _agent_build_locks[cache_key] = lock
        return lock


async def _get_agent_for_session(session_key: str, user_id: Optional[int]):
    """按用户取/建 agent。workspace 为用户持久目录，skill 增量同步到其中（节流）。"""
    ws = _user_workspace(user_id)
    _session_tmp_dir(ws, session_key)  # 确保 session 临时目录存在

    # skill 同步节流：可见性变更是低频事件，不必每次请求都做全量硬链同步。
    # 文件硬链/拷贝/rmtree 都是阻塞 IO，丢到线程池避免卡死 event loop。
    if time.monotonic() - _skill_last_sync.get(ws, 0.0) > _SKILL_SYNC_INTERVAL:
        try:
            allowed = await _get_visible_skill_keys(user_id)
            await asyncio.to_thread(_sync_session_skills, ws, allowed)
            # DB 技能物化到同一目录（agent_skill_file → .agent_skills/<key>/）
            from app.api.v1.ai.agent_skill import _materialize_from_db

            db_keys = [k for k in allowed if k not in _iter_global_skill_dirs()]
            if db_keys:
                await _materialize_from_db(db_keys, ws)
            _skill_last_sync[ws] = time.monotonic()
        except Exception as e:
            logger.warning(f"同步 skill 失败（{e}），继续用现有状态")

    # 超管判定 + 按角色模型配置 profile 解析，都放在缓存查找之前：
    # - profile 编进 cache_key（配置 / 角色变化 → key 变 → agent 自动重建对应形态）
    # - 同步 set 请求级上下文（CTX_PROFILE + CTX_GEN_BLOCK_OVERRIDE）：本次请求的
    #   运行期读取（附件视觉判定 / 生成能力门卫）与 agent 构建（CTX 随
    #   asyncio.to_thread 拷贝进构建线程）都依赖它。命中缓存也必须每次设置。
    _is_super = False
    _role_objs: list = []
    if user_id is not None:
        from app.models.system.admin import User

        _u = await User.get_or_none(id=user_id).prefetch_related("by_user_roles")
        if _u:
            _role_objs = list(_u.by_user_roles)
            _is_super = any(r.role_code == "R_SUPER" for r in _role_objs)

    from app.langchain.role_model_profile import apply_gen_override, resolve_profile_for_roles, set_current_profile

    _profile = await resolve_profile_for_roles(_role_objs)
    set_current_profile(_profile)
    apply_gen_override(_profile)

    cache_key = f"u{user_id or 0}" + ("_s" if _is_super else "") + _profile.cache_key_part

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

        shared = await _get_shared_resources()
        from app.langchain.checkpointers import get_async_checkpointer

        # kb_* / 历史回溯工具均不直挂主 agent：已分别下沉到 knowledge-base / chat-history
        # 子 agent（省每轮工具 schema token），统一经 task() 委派
        checkpointer = await get_async_checkpointer()

        # 动态构建 skills 列表：传父目录，SkillsMiddleware 会 ls 它并发现所有子目录里的 SKILL.md
        _skills_dir = ws / ".agent_skills"
        user_skills = [".agent_skills"] if _skills_dir.is_dir() else None

        # create_qa_agent 是同步函数，丢线程池跑
        def _build():
            return create_qa_agent(
                extra_tools=list(shared["mcp_tools"]),
                store=shared["store"],
                root_dir=str(ws),
                checkpointer=checkpointer,
                user_id=user_id,
                is_super=_is_super,
                skills=user_skills,
                chat_block_key=_profile.chat_block_key,
            )

        agent = await asyncio.to_thread(_build)

        async with _agent_lru_lock:
            _agent_cache[cache_key] = agent
            while len(_agent_cache) > _AGENT_CACHE_MAX:
                evict_key, _ = _agent_cache.popitem(last=False)
                logger.info(f"LRU 弹出 agent: {evict_key}")
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
    thread_id: Optional[str] = Field(None, description="兼容老接口：直接指定 thread_id（不持久化）")
    files: Optional[list[str]] = Field(None, description="已上传文件的相对路径列表")
    workflow_key: Optional[str] = Field(None, description="用户当前在工作流画板打开的工作流 key（注入上下文用）")
    scope_node_ids: Optional[list[str]] = Field(None, description="迷你协作可编辑范围（焦点卡 + 一跳邻居，首元素为焦点卡；空=全板模式）")
    selected_node_ids: Optional[list[str]] = Field(None, description="用户当前在画板选中的节点 id 列表（随消息的上下文，非可编辑范围）")


class QAResetRequest(BaseModel):
    """重置对话请求"""

    thread_id: str = Field(..., description="要清除的会话线程 ID")


# ── SSE 工具 ──────────────────────────────────────────────────────────────────


def _sse(data: dict) -> str:
    """构造 SSE 事件"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _execute_agent_in_background(
    agent,
    agent_input: Union[str, list],  # str=纯文本；list=多模态 content blocks（文本+内联图片）
    config: dict,
    session: Optional[AgentSession],
    user_msg: Optional[AgentMessage],
    assistant_msg: Optional[AgentMessage],
    request,
    event_queue: asyncio.Queue,
    workspace: Path,
    cancellation_event: asyncio.Event,
):
    """
    后台任务：执行 Agent 推理并将事件推送到队列。
    无论前端是否断开连接，都会完整执行并存库（除非收到取消信号）。
    """
    step = 0
    collected_content: list[str] = []
    collected_thinking: list[str] = []
    tool_steps: list[dict] = []
    session_key = session.session_key if session else None
    _first_main_token = False  # 主 agent 首个 token 计时用

    try:
        # 用户输入内容审核
        from app.utils.content_moderation import moderate

        hit_keyword = await moderate(request.message)
        if hit_keyword:
            logger.warning(f"用户输入审核拦截, session={session.session_key if session else '-'}, keyword={hit_keyword!r}")
            if assistant_msg is not None:
                assistant_msg.content = "[内容审核未通过，已拦截]"
                assistant_msg.status = "done"
                await assistant_msg.save()
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
                await assistant_msg.save()
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

        await event_queue.put(
            _sse({
                "type": "session",
                "sessionKey": session.session_key if session else None,
                "threadId": config["configurable"]["thread_id"],
                "assistantMessageId": assistant_msg.id if assistant_msg else None,
                "userMessageId": user_msg.id if user_msg else None,
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

        # 计费上下文
        from app.core.ctx import CTX_BILLING_BIZ_ENTRY, CTX_BILLING_SESSION_ID

        CTX_BILLING_BIZ_ENTRY.set("qa")
        CTX_BILLING_SESSION_ID.set(session.id if session else None)

        # subgraphs=True：子 Agent 的事件也会冒泡上来，以三元组 (namespace, mode, data) 形式
        _t_stream_start = time.monotonic()
        async for namespace, stream_mode, chunk in agent.astream(
            {"messages": [{"role": "user", "content": agent_input}]},
            config=config,
            stream_mode=["messages", "updates"],
            subgraphs=True,
        ):
            # 检查取消信号
            if cancellation_event.is_set():
                logger.info(f"[bg_task] 检测到取消信号，中止执行 session={session_key}")
                if assistant_msg is not None:
                    assistant_msg.content = "".join(collected_content).strip()
                    assistant_msg.thinking = "\n\n".join(collected_thinking).strip() or None
                    assistant_msg.tool_steps_json = tool_steps
                    # 用户主动停止不算异常：走独立的 aborted 状态，不写 error
                    assistant_msg.status = "aborted"
                    assistant_msg.error = None
                    await assistant_msg.save()
                await event_queue.put(_sse({"type": "aborted"}))
                return

            # namespace 为空元组表示主 Agent；非空表示子 Agent（如 ("tools:abc",)）
            is_subagent = bool(namespace)

            if stream_mode == "messages":
                token, metadata = chunk
                # 过滤 LangGraph 对话历史压缩产物（如 "## SESSION INTENT"），避免混入正常输出
                if metadata.get("lc_source") == "summarization":
                    continue
                node = metadata.get("langgraph_node", "")
                content = getattr(token, "content", "") or ""
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                if node == "model" and content:
                    msg_id = getattr(token, "id", "") or ""
                    step += 1
                    if is_subagent:
                        # 子 Agent 的流式 token 不推前端：
                        # 其最终输出已通过 tool_result 事件进入执行痕迹，无需重复展示中间过程
                        pass
                    else:
                        if not _first_main_token:
                            _first_main_token = True
                            logger.info(f"[perf] first main-agent token: {(time.monotonic() - _t_stream_start) * 1000:.0f}ms after astream()")
                        collected_content.append(content)
                        await event_queue.put(
                            _sse({
                                "type": "answer_chunk",
                                "step": step,
                                "msg_id": msg_id,
                                "content": content,
                            })
                        )

            elif stream_mode == "updates":
                for node, node_update in chunk.items():
                    if node_update is None:
                        continue

                    if node == "model":
                        # 子 Agent 的 model 节点：tool_call 带 subagent 标记，不做 reclassify
                        for msg in node_update.get("messages") or []:
                            msg_id = getattr(msg, "id", "") or ""
                            tool_calls = getattr(msg, "tool_calls", None) or []
                            msg_content = getattr(msg, "content", "") or ""
                            if isinstance(msg_content, list):
                                msg_content = " ".join(c.get("text", "") for c in msg_content if isinstance(c, dict))
                            # 与呈现型工具（create_chart / register_artifact）同条消息的文本
                            # 是答案正文，跳过 reclassify；与干活型工具同条消息的文本才是铺垫。
                            _tc_names = {tc.get("name") for tc in tool_calls}
                            _presentation_only = bool(_tc_names) and _tc_names <= _PRESENTATION_TOOLS
                            if not is_subagent and tool_calls and msg_content.strip() and not _presentation_only:
                                step += 1
                                thinking_text = "".join(collected_content).strip()
                                if thinking_text:
                                    collected_thinking.append(thinking_text)
                                    collected_content = []
                                await event_queue.put(
                                    _sse({
                                        "type": "reclassify",
                                        "step": step,
                                        "msg_id": msg_id,
                                        "to": "thinking",
                                        "content": msg_content.strip(),
                                    })
                                )
                            for tc in tool_calls:
                                step += 1
                                tool_name = tc.get("name", "")
                                tc_event: dict = {
                                    "type": "tool_call",
                                    "step": step,
                                    "tool": tool_name,
                                    "tool_display": get_tool_display_name(tool_name),
                                    "args": tc.get("args", {}),
                                }
                                if is_subagent:
                                    tc_event["subagent"] = True
                                else:
                                    tool_steps.append({
                                        "id": step,
                                        "type": "tool_call",
                                        "tool": tool_name,
                                        "tool_display": get_tool_display_name(tool_name),
                                        "args": tc.get("args", {}),
                                    })
                                await event_queue.put(_sse(tc_event))
                    elif node == "tools":
                        for msg in node_update.get("messages") or []:
                            tool_name = getattr(msg, "name", "") or ""
                            tool_content = getattr(msg, "content", "") or ""
                            if isinstance(tool_content, list):
                                tool_content = " ".join(c.get("text", "") for c in tool_content if isinstance(c, dict))
                            # task 工具通过 Command 返回 ToolMessage，name 字段不会被 LangGraph 填充，
                            # 需要兜底推断为 "task"；否则执行痕迹里看不到子 agent 的完整返回
                            if not tool_name and not is_subagent:
                                if tool_content:
                                    tool_name = "task"
                                    logger.debug(
                                        "[qa stream] task tool result captured: content_len=%d",
                                        len(tool_content),
                                    )
                                else:
                                    logger.warning("[qa stream] unnamed ToolMessage with empty content in main agent tools node (subagent may have returned empty result)")
                            if tool_name:
                                step += 1
                                tr_event: dict = {
                                    "type": "tool_result",
                                    "step": step,
                                    "tool": tool_name,
                                    "tool_display": get_tool_display_name(tool_name),
                                    "content": tool_content,
                                }
                                if is_subagent:
                                    tr_event["subagent"] = True
                                else:
                                    tool_steps.append({
                                        "id": step,
                                        "type": "tool_result",
                                        "tool": tool_name,
                                        "tool_display": get_tool_display_name(tool_name),
                                        "content": tool_content,
                                    })
                                await event_queue.put(_sse(tr_event))

        # 收尾：内容审核 → 落库
        final_content = "".join(collected_content).strip()
        hit_keyword = await moderate(final_content)
        if hit_keyword:
            logger.warning(f"内容审核拦截, session={session.session_key if session else '-'}, keyword={hit_keyword!r}")
            await event_queue.put(_sse({"type": "moderated", "message": "内容已被审核拦截"}))
            final_content = "[内容审核未通过，已拦截]"

        if assistant_msg is not None:
            assistant_msg.content = final_content
            assistant_msg.thinking = "\n\n".join(collected_thinking).strip() or None
            assistant_msg.tool_steps_json = tool_steps
            assistant_msg.status = "done"
            await assistant_msg.save()

        await event_queue.put(_sse({"type": "done", "steps": step}))

    except Exception as e:
        logger.exception("后台 Agent 执行异常")
        if assistant_msg is not None:
            assistant_msg.content = "".join(collected_content).strip()
            assistant_msg.thinking = "\n\n".join(collected_thinking).strip() or None
            assistant_msg.tool_steps_json = tool_steps
            assistant_msg.status = "error"
            assistant_msg.error = str(e)[:2000]
            await assistant_msg.save()
        await event_queue.put(_sse({"type": "error", "message": str(e)}))
    finally:
        clear_agent_call_context()
        await event_queue.put(None)  # 结束标记
        # 清理取消事件
        if session_key and session_key in _active_task_cancellations:
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


async def _ensure_session(session_key: Optional[str], workflow_key: Optional[str] = None) -> tuple[Optional[AgentSession], str]:
    """根据 session_key 取或建会话，返回 (session, thread_id)。thread_id 用 session.thread_id。

    新建会话时按 workflow_key 落来源标签与工作流归属：工作流画板页发起的记 workflow 并绑定该工作流，其余记 qa。
    命中已有会话不改其归属（会话绑定首次发起时的工作流）。
    """
    uid = CTX_USER_ID.get() or None
    if session_key:
        s = await AgentSession.get_or_none(session_key=session_key, is_deleted=0)
        if s is not None:
            return s, s.thread_id
    # 新建会话
    key = f"sess_{secrets.token_hex(6)}"
    s = await AgentSession.create(
        session_key=key,
        user_id=uid,
        title="新对话",
        thread_id=f"qa-{key}",
        source="workflow" if workflow_key else "qa",
        workflow_key=workflow_key,
    )
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
    elif ntype == "segNode":  # 品牌板型：分镜段卡（generic，一卡一场戏）
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
    session, thread_id = await _ensure_session(request.session_key, request.workflow_key)
    if request.thread_id and not request.session_key:
        thread_id = request.thread_id  # 兼容老用法
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
        if (not session.title or session.title == "新对话") and request.message.strip():
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

    # 命中 @skill：注入到 agent 输入
    uid = CTX_USER_ID.get() or None
    hit_skills = await resolve_skills_from_text(request.message, uid)
    workspace = _user_workspace(uid)
    injected_prompt = await build_skill_injection(hit_skills, uid, workspace_dir=workspace)

    agent_input = f"{injected_prompt}{request.message}" if injected_prompt else request.message

    # 注入「当前打开的工作流」上下文（工作流画板页面）：让 Agent 直接操作该 key，无需 list_workflows
    if request.workflow_key:
        from app.models.standard.agent import AgentWorkflow

        wf = await AgentWorkflow.get_or_none(workflow_key=request.workflow_key, user_id=uid, is_deleted=0)
        if wf and (wf.board_type or "board") == "html":
            # HTML 看板任务：注入开发者模式规则 + 任务目录上下文（通用 prompt 保持克制，场景规则随消息注入）。
            # 节点 / 选中 / scope 等流程板概念对 html 型不适用，全部跳过。
            from app.langchain.tools.workflow_tools import HTML_BOARD_RULES

            app_dir = workspace / "apps" / wf.workflow_key

            def _list_app_files() -> list:
                out: list = []
                if not app_dir.is_dir():
                    return out
                for p in sorted(app_dir.rglob("*")):
                    if not p.is_file() or p.name.endswith(".tmp"):
                        continue
                    out.append(f"- {p.relative_to(app_dir).as_posix()}（{p.stat().st_size} B）")
                    if len(out) >= 50:
                        break
                return out

            file_lines = await asyncio.to_thread(_list_app_files)
            files_desc = "\n".join(file_lines) if file_lines else "（目录为空，请先写入口 index.html）"
            html_ctx = (
                "[用户当前打开了一个「HTML 看板」任务]\n"
                f"workflow_key: {wf.workflow_key}\n"
                f"标题: {wf.title}\n"
                f"应用目录: apps/{wf.workflow_key}/（相对工作目录）\n"
                f"当前文件：\n{files_desc}\n"
                f"用户提到的「应用」「看板」即指它。写完/改完文件后务必调用 "
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

    # 注入上传文件路径提示
    inline_media_blocks: list[dict] = []
    if request.files:
        from app.langchain.config import has_role
        from app.langchain.role_model_profile import effective_chat_supports_vision

        # 按角色模型配置：跟随本次请求生效的 chat 块（_get_agent_for_session 已解析并
        # set CTX_PROFILE），而非全局激活块——否则文本块角色用户会被塞多模态直传
        supports_vision = effective_chat_supports_vision()
        usable_files = list(request.files)
        skipped_media: list[str] = []
        # 主模型无原生视觉时：视频没有任何兜底（VISION 角色只认图），直接跳过；
        # 未配置 VISION 角色时，图片也一并跳过
        if not supports_vision:
            usable_files = [f for f in usable_files if Path(f).suffix.lower() not in _VIDEO_EXTS]
            skipped_media.extend(f for f in request.files if Path(f).suffix.lower() in _VIDEO_EXTS)
            if not has_role("VISION"):
                usable_files = [f for f in usable_files if Path(f).suffix.lower() not in _IMAGE_EXTS]
                skipped_media.extend(f for f in request.files if Path(f).suffix.lower() in _IMAGE_EXTS)

        hint_parts: list[str] = []
        _media_exts = _IMAGE_EXTS + _VIDEO_EXTS
        other_files = [f for f in usable_files if Path(f).suffix.lower() not in _media_exts]
        image_files = [f for f in usable_files if Path(f).suffix.lower() in _IMAGE_EXTS]
        video_files = [f for f in usable_files if Path(f).suffix.lower() in _VIDEO_EXTS]
        if other_files:
            file_list = "\n".join(f"- {f}" for f in other_files)
            hint_parts.append(f"[用户上传了以下文件（路径已是相对工作目录），可直接用 read_file 工具读取]\n{file_list}")
        if image_files:
            if supports_vision:
                # 原生视觉模型：多模态消息直传（OpenAI 标准 image_url 块），零工具调用。
                # url 用 PUBLIC_BASE_URL 公网链接（publish_conversation_media 统一发布）：
                # 消息/checkpoint 里只留一行 URL，避免 base64 永久驻留导致上下文膨胀。
                import mimetypes as _mimetypes
                from app.api.v1.ai.upload import publish_conversation_media
                from app.langchain.tools.vision_tools import _maybe_resize_sync

                unreadable: list[str] = []
                img_count = 0
                for f in image_files:
                    try:
                        fp = (workspace / f.lstrip("/").lstrip("\\")).resolve()
                        data = await asyncio.to_thread(fp.read_bytes)
                        mime = _mimetypes.guess_type(fp.name)[0] or "image/png"
                        data, mime = await asyncio.to_thread(_maybe_resize_sync, data, mime)
                        url = await publish_conversation_media(data, mime, ext=fp.suffix.lower())
                        if url:
                            inline_media_blocks.append({"type": "image_url", "image_url": {"url": url}})
                            img_count += 1
                        else:
                            logger.warning(f"[chat/stream] 图片发布公网 URL 失败，跳过该图：{f}")
                            unreadable.append(f)
                    except Exception as e:
                        logger.warning(f"[chat/stream] 内联上传图片失败 {f}: {e}")
                        unreadable.append(f)
                if img_count:
                    hint_parts.append(f"[用户上传了 {img_count} 张图片，已作为多模态内容直接附在本条消息中，可直接看到图片内容，无需任何工具]")
                if unreadable:
                    hint_parts.append(f"[以下图片附件读取失败，已忽略：{', '.join(Path(f).name for f in unreadable)}]")
            else:
                img_list = "\n".join(f"- {f}" for f in image_files)
                hint_parts.append(f"[用户上传了以下图片（相对工作目录路径），用 vision_inspect(file=路径, question=具体问题) 查看，不要用 read_file]\n{img_list}")
        if video_files:
            # 原生视觉模型：视频多模态直传（OpenAI/DashScope 标准 video_url 块），与图片同一套公网上传机制
            import mimetypes as _mimetypes
            from app.api.v1.ai.upload import publish_conversation_media

            unreadable_v: list[str] = []
            video_count = 0
            for f in video_files:
                try:
                    fp = (workspace / f.lstrip("/").lstrip("\\")).resolve()
                    size = (await asyncio.to_thread(fp.stat)).st_size
                    if size > _MAX_VIDEO_INLINE_BYTES:
                        logger.warning(f"[chat/stream] 视频过大无法直传（{size // 1048576}MB），跳过：{f}")
                        unreadable_v.append(f)
                        continue
                    data = await asyncio.to_thread(fp.read_bytes)
                    mime = _mimetypes.guess_type(fp.name)[0] or "video/mp4"
                    url = await publish_conversation_media(data, mime, ext=fp.suffix.lower())
                    if url:
                        inline_media_blocks.append({"type": "video_url", "video_url": {"url": url}})
                        video_count += 1
                    else:
                        logger.warning(f"[chat/stream] 视频发布公网 URL 失败，跳过该视频：{f}")
                        unreadable_v.append(f)
                except Exception as e:
                    logger.warning(f"[chat/stream] 内联上传视频失败 {f}: {e}")
                    unreadable_v.append(f)
            if video_count:
                hint_parts.append(f"[用户上传了 {video_count} 个视频，已作为多模态内容直接附在本条消息中，可直接观看视频内容，无需任何工具]")
            if unreadable_v:
                hint_parts.append(f"[以下视频附件无法直接观看（过大或发布失败），已忽略：{', '.join(Path(f).name for f in unreadable_v)}]")
        if skipped_media:
            media_list = "\n".join(f"- {Path(f).name}" for f in skipped_media)
            hint_parts.append(f"[当前主模型不支持视觉（或未配置视觉模型），以下图片/视频附件已忽略，请勿尝试读取，可在回复中告知用户暂时无法分析]\n{media_list}")
        if hint_parts:
            agent_input = "\n\n".join(hint_parts) + "\n\n" + agent_input
    if inline_media_blocks:
        # 多模态直传：content 从纯文本变为「文本 + 图片/视频块」列表
        agent_input = [{"type": "text", "text": agent_input}, *inline_media_blocks]

    # workspace 已在上方 skill 注入时提前创建（_user_workspace(uid)）

    # 创建事件队列用于后台任务和 SSE 流之间通信
    event_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

    # 创建取消事件
    cancellation_event = asyncio.Event()
    if session:
        _active_task_cancellations[session.session_key] = cancellation_event
        logger.info(f"[chat/stream] 注册取消事件 session={session.session_key}, 当前活跃tasks={list(_active_task_cancellations.keys())}")

    # 用 asyncio.create_task 立即启动，与 event_generator 并发执行
    # （BackgroundTasks 在 response 结束后才跑，会死锁）
    asyncio.create_task(
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
        )
    )

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

        # 用 astream 避免同步 stream 阻塞 event loop
        async for chunk in agent.astream(
            {"messages": [{"role": "user", "content": request.message}]},
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

    from app.langchain.tools.daily_brief_tools import make_daily_brief_tools
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
            brief_tools = await make_daily_brief_tools(user_id)
            shared = await _get_shared_resources()

            def _build():
                return create_qa_agent(
                    extra_tools=list(shared["mcp_tools"]) + brief_tools,
                    store=shared["store"],
                    root_dir=str(workspace),
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
