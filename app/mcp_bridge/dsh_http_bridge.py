"""
dsh 标准工具桥（streamable-http 版）：内置于 FastAPI 进程，替代 stdio 子进程桥。

相比 stdio 桥（app/mcp_bridge/dsh_tools_server.py，保留作 DSH_BRIDGE_MODE=stdio 回退）：
- 拿得到「当前活跃回合」上下文（ACTIVE_TURNS 表，qa.py/scheduler/sediment 注册）：
  register_artifact 等工具能关联到具体 session/message → 前端产物下载块恢复
- 计费恢复：WebSearch 类 MCP 工具按原口径记 mcp_call
- WebSearch 恢复：FastAPI 进程可直连 DashScope SSE MCP（dsh 的 mcp-client 不支持
  SSE，本桥代为转发为普通工具）

挂载路径：/mcp-bridge/stdtools/{uid}/mcp（uid 走 URL，loopback + MCP_BRIDGE_TOKEN 鉴权，
与 app/mcp_bridge 同一套 _BearerGuardMiddleware）。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Optional

import mcp.types as types
from loguru import logger
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

# ── 活跃回合登记（产物联动核心） ──────────────────────────────────────────────
#
# dsh 桥请求是独立 HTTP 请求，拿不到发起回合的 ContextVar；qa.py 等消费端在
# 回合开始时登记 {uid: {token: {session_id/session_key/message_id/workspace}}}，
# 桥内工具调用前据此重建 AgentCallContext。
# 并发口径：同一用户可能有多个回合同时在途（多会话、定时任务与聊天并行——
# 回合不做串行化），表按 uid → {token: 条目} 组织：
#   - set 返回唯一 token，调用方持有；
#   - clear 只删自己 token 的条目，短回合结束不会抹掉并发长回合的上下文；
#   - get 返回最近登记的一条（归属口径与原「后写覆盖」一致）；
#   - 回合条目即便全部缺失，桥内也会按 uid 推导用户工作区兜底
#     （workspace 对 uid 是纯函数），相对路径不会再误解析到项目根工作区。

_ACTIVE_TURNS: dict[int, dict[str, dict]] = {}
_TURN_DEFAULTS: dict[int, dict] = {}  # uid → {is_super, is_admin, gen_override}（agent 构建期登记）

_CTX_BRIDGE_UID: ContextVar[Optional[int]] = ContextVar("dsh_bridge_uid", default=None)


def register_turn_defaults(
    uid: Optional[int], *, is_super: bool = False, is_admin: bool = False, gen_override: Optional[dict] = None
) -> None:
    """agent 构建期登记用户级默认（超管标记、管理员标记、生成能力角色覆盖）。

    is_admin（R_SUPER/R_ADMIN）门控向量库管理工具组——普通用户不挂载。
    """
    if uid is None:
        return
    _TURN_DEFAULTS[uid] = {
        "is_super": bool(is_super),
        "is_admin": bool(is_admin),
        "gen_override": gen_override or None,
    }


def set_active_turn(
    uid: Optional[int],
    *,
    session_id: Optional[int] = None,
    session_key: Optional[str] = None,
    message_id: Optional[int] = None,
    workspace: Optional[Path] = None,
) -> Optional[str]:
    """回合开始：登记产物联动上下文，返回回合 token（收尾清理时原样传回）。"""
    if uid is None:
        return None
    token = uuid.uuid4().hex
    _ACTIVE_TURNS.setdefault(uid, {})[token] = {
        "session_id": session_id,
        "session_key": session_key,
        "message_id": message_id,
        "workspace": workspace,
    }
    return token


def clear_active_turn(uid: Optional[int], token: Optional[str] = None) -> None:
    """回合结束：只清自己 token 对应的条目。

    token 缺失时不删任何东西（旧「按 uid 整删」语义会误清并发回合的上下文，
    是本函数 token 化的原因）；条目清空后删除空桶。
    """
    if uid is None or token is None:
        if token is None and uid is not None:
            logger.warning(f"[dsh-http-bridge] clear_active_turn 缺少 token，跳过清理 uid={uid}")
        return
    entries = _ACTIVE_TURNS.get(uid)
    if not entries:
        return
    entries.pop(token, None)
    if not entries:
        _ACTIVE_TURNS.pop(uid, None)


def get_active_turn(uid: Optional[int]) -> dict:
    """活跃回合表公共读取口：数据集桥（app/mcp_bridge/datasets.py）重建工具调用上下文用。

    多个回合并发时返回最近登记的一条（归属口径与原实现一致）。
    """
    if uid is None:
        return {}
    entries = _ACTIVE_TURNS.get(uid)
    if not entries:
        return {}
    return next(reversed(entries.values()))


# ── 工具集构建（按 uid + 形态签名缓存） ───────────────────────────────────────

_user_tools_cache: dict[tuple, list] = {}
_websearch_tools: Optional[list] = None
_websearch_lock = asyncio.Lock()
_BILLED_TOOL_NAMES: set[str] = set()  # 记 mcp_call 计费的工具名（WebSearch 等）


async def _get_websearch_tools() -> list:
    """DashScope WebSearch（SSE 转发路径）——仅回退模式使用。

    dsh 侧现已原生直连 streamable-http 端点（见 qa_agent 的 websearch 连接器行），
    桥内此路径默认关闭（避免重复工具 + 首次 list_tools 的惰性 SSE 握手延迟）；
    设 DSH_BRIDGE_WEBSEARCH=1 可在 stdio 回退桥等场景重新启用。
    """
    global _websearch_tools
    if os.environ.get("DSH_BRIDGE_WEBSEARCH") != "1":
        return []
    if _websearch_tools is not None:
        return _websearch_tools
    async with _websearch_lock:
        if _websearch_tools is not None:
            return _websearch_tools
        tools: list = []
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            from app.langchain.config import langchain_config

            key = langchain_config.DASHSCOPE_API_KEY
            if key:
                client = MultiServerMCPClient({
                    "websearch": {
                        "transport": "sse",
                        "url": "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/sse",
                        "headers": {"Authorization": f"Bearer {key}"},
                    }
                })
                tools = await asyncio.wait_for(client.get_tools(), timeout=20)
                for t in tools:
                    _BILLED_TOOL_NAMES.add(t.name)
                logger.info(f"[dsh-http-bridge] WebSearch 工具就绪：{[t.name for t in tools]}")
            else:
                logger.info("[dsh-http-bridge] DASHSCOPE_API_KEY 未配置，WebSearch 不加载")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dsh-http-bridge] WebSearch 加载失败（联网搜索降级）: {e}")
        _websearch_tools = tools
        return tools


def _build_user_tools(uid: Optional[int], is_super: bool, is_admin: bool, gen_override: Optional[dict]) -> list:
    """同步构建工具集（与 stdio 桥同口径 + WebSearch 由异步侧追加）。

    标准数据工具（db 四件 + 语义搜索）已迁出本桥，统一走系统数据集 MCP 服务
    （app/mcp_bridge/datasets.py，用户在技能面板添加启用后才挂进会话，
    与连接器同口径）——本桥只保留图表 / 产物 / 生成 / 视觉 / 个人侧工具。
    """
    from app.langchain.tools.chart_tool import create_chart
    from app.services.agent_runtime.artifact_tools import register_artifact

    tools = [create_chart, register_artifact]

    # 生成工具：has_capability 门控读 CTX_GEN_BLOCK_OVERRIDE，按本回合角色覆盖临时设置
    try:
        from app.core.ctx import CTX_GEN_BLOCK_OVERRIDE
        from app.langchain.tools.image_tools import get_image_tools
        from app.langchain.tools.video_tools import get_video_tools

        tk = CTX_GEN_BLOCK_OVERRIDE.set(gen_override or {})
        try:
            tools += list(get_image_tools()) + list(get_video_tools())
        finally:
            CTX_GEN_BLOCK_OVERRIDE.reset(tk)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dsh-http-bridge] 生成工具加载失败（跳过）: {e}")

    # 视觉兜底 / 视频通道：VISION 角色模型读图/读视频并返回文字描述。
    # 定位（图片通道路径由系统提示词「图片与视频理解通道」节规定，见
    # qa_agent._vision_section）：① 纯文本主模型用户的图片/视频理解兜底；
    # ② 视觉块用户看视频的唯一通道（dsh 无视频通道）。视觉块看图片走原生
    # read_image，提示词已明令不走本工具
    try:
        from app.langchain.config import has_role

        if has_role("VISION"):
            from app.langchain.tools.vision_tools import vision_inspect

            tools.append(vision_inspect)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dsh-http-bridge] vision_inspect 加载失败（跳过）: {e}")

    if uid is not None:
        from app.langchain.tools.history_tools import make_history_tools
        from app.langchain.tools.kb_tools import make_kb_tools
        from app.langchain.tools.task_tools import make_task_tools
        from app.langchain.tools.workflow_tools import make_workflow_tools
        from app.services.agent_runtime.edit_tools import SKILL_TOOLS

        # 工作流画板（read_workflow/edit_workflow_board/publish_html_board 等，
        # 应用制作与流程编排的核心工具）+ 定时任务，deepagents 时代即挂主 agent
        tools += list(make_workflow_tools(uid)) + list(make_task_tools(uid))
        tools += list(make_kb_tools(uid)) + list(make_history_tools(uid)) + list(SKILL_TOOLS)
        # 向量库管理（统一向量库体系对话侧入口：列表/搜索/建库/增删条目）——仅管理员挂载
        if is_admin:
            from app.langchain.tools.vector_lib_tools import make_vector_lib_tools

            tools += list(make_vector_lib_tools(uid, is_super))
        if is_super:
            from app.langchain.tools.admin_tools import make_admin_tools

            tools += list(make_admin_tools(uid))

    # 去重保序
    seen: set = set()
    deduped = []
    for t in tools:
        if t.name not in seen:
            seen.add(t.name)
            deduped.append(t)
    return deduped


async def _tools_for_request(uid: Optional[int]) -> list:
    """按当前请求的 uid + 形态签名取工具集（缓存）。"""
    defaults = _TURN_DEFAULTS.get(uid or -1) or {}
    is_super = bool(defaults.get("is_super"))
    is_admin = bool(defaults.get("is_admin"))
    gen_override = defaults.get("gen_override")
    # is_admin 编进键：角色变更（管理员↔普通）时工具集形态不同，必须重建
    cache_key = (uid, is_super, is_admin, repr(gen_override))
    cached = _user_tools_cache.get(cache_key)
    if cached is None:
        cached = _build_user_tools(uid, is_super, is_admin, gen_override)
        _user_tools_cache[cache_key] = cached
    # WebSearch 异步追加（不进缓存结构，独立失败域）
    ws = await _get_websearch_tools()
    if ws:
        names = {t.name for t in cached}
        cached = cached + [t for t in ws if t.name not in names]
    return cached


def _tool_schema(t) -> dict:
    # tool_json_schema：给 bool / array / object 参数加 string 备选——MCP SDK 在
    # call_tool 前按 inputSchema 做 jsonschema 校验，不加备选字符串化入参会被拦死
    from app.langchain.tools._tool_args import tool_json_schema

    return tool_json_schema(t)


# ── MCP Server（低层，list/call 动态按 uid 解析） ─────────────────────────────

_server = Server("stdtools")


@_server.list_tools()
async def _list_tools() -> list[types.Tool]:
    uid = _CTX_BRIDGE_UID.get()
    tools = await _tools_for_request(uid)
    return [types.Tool(name=t.name, description=(t.description or "")[:2000], inputSchema=_tool_schema(t)) for t in tools]


@_server.call_tool()
async def _call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    uid = _CTX_BRIDGE_UID.get()
    tools = await _tools_for_request(uid)
    tool = next((t for t in tools if t.name == name), None)
    if tool is None:
        return [types.TextContent(type="text", text=f"未知工具：{name}")]

    # 逐消息调用上下文：从活跃回合表重建（产物/落盘类工具依赖）
    turn = get_active_turn(uid)
    try:
        from app.services.agent_runtime.call_context import AgentCallContext, default_user_workspace, set_agent_call_context

        # 工作区兜底：回合条目缺失/不全时按 uid 推导（workspace 对 uid 是纯函数），
        # 相对路径不会再误解析到项目根工作区
        _ws = turn.get("workspace") or (default_user_workspace(uid) if uid is not None else None)
        set_agent_call_context(
            AgentCallContext(
                session_id=turn.get("session_id"),
                session_key=turn.get("session_key"),
                message_id=turn.get("message_id"),
                workspace_dir=_ws,
            )
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dsh-http-bridge] AgentCallContext 重建失败: {e}")

    # 用户身份：skill_save 等静态工具调用时读 CTX_USER_ID（stdio 桥在进程启动时
    # 一次性设置；HTTP 桥是请求级的，必须每次调用设置），计费归属同样依赖它
    _uid_token = None
    try:
        if uid is not None:
            from app.core.ctx import CTX_USER_ID

            _uid_token = (CTX_USER_ID, CTX_USER_ID.set(uid))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dsh-http-bridge] CTX_USER_ID 设置失败: {e}")

    try:
        try:
            result = await tool.ainvoke(dict(arguments or {}))
        except NotImplementedError:
            result = await asyncio.to_thread(tool.invoke, dict(arguments or {}))
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[dsh-http-bridge] 工具 {name} 执行失败")
        return [types.TextContent(type="text", text=f"工具执行失败：{e}")]
    finally:
        if _uid_token is not None:
            try:
                _uid_token[0].reset(_uid_token[1])
            except Exception:  # noqa: BLE001
                pass
        try:
            from app.services.agent_runtime.call_context import clear_agent_call_context

            clear_agent_call_context()
        except Exception:  # noqa: BLE001
            pass

    # 计费：WebSearch 等外部 MCP 工具按原口径记 mcp_call（业务工具不计费，与旧行为一致）
    if name in _BILLED_TOOL_NAMES:
        try:
            from app.langchain.billing.pricing import Billing

            await Billing.record(module="mcp", provider="dashscope", model=name, units={"mcp_call": 1})
        except Exception:
            logger.exception("[dsh-http-bridge] Billing.record 异常（已吞掉）")

    text = result if isinstance(result, str) else str(result)
    if len(text) > 60000:
        text = text[:60000] + "\n...[内容过长已截断，请缩小查询范围或使用分页参数]"
    return [types.TextContent(type="text", text=text)]


# ── ASGI 挂载 ─────────────────────────────────────────────────────────────────

_bridge_manager: Optional[StreamableHTTPSessionManager] = None
_BRIDGE_STACK: Optional["contextlib.AsyncExitStack"] = None


class _UidMCPHandler:
    """ASGI 入口：从 URL 解析 uid 注入 contextvar，再交给 session_manager。"""

    async def __call__(self, scope, receive, send) -> None:
        uid_raw = (scope.get("path_params") or {}).get("uid")
        try:
            uid = int(uid_raw) if uid_raw is not None else None
        except (TypeError, ValueError):
            uid = None
        token = _CTX_BRIDGE_UID.set(uid)
        try:
            assert _bridge_manager is not None
            await _bridge_manager.handle_request(scope, receive, send)
        finally:
            _CTX_BRIDGE_UID.reset(token)


def mount_dsh_http_bridge(app: Any) -> None:
    """create_app 里调用：挂载 /mcp-bridge/stdtools/{uid}/mcp。"""
    global _bridge_manager
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from app.mcp_bridge import _BearerGuardMiddleware

    manager = StreamableHTTPSessionManager(
        app=_server,
        event_store=None,
        json_response=False,
        stateless=True,
    )
    _bridge_manager = manager

    token = os.getenv("MCP_BRIDGE_TOKEN", "")
    sub = Starlette(routes=[Mount("/{uid:int}/mcp", app=_UidMCPHandler())])
    if token:
        sub.add_middleware(_BearerGuardMiddleware, token=token)
    else:
        logger.warning("[dsh-http-bridge] MCP_BRIDGE_TOKEN 未配置，/mcp-bridge/stdtools 端点暂无鉴权（仅适合本机开发）")
    app.mount("/mcp-bridge/stdtools", sub, name="mcp-bridge-stdtools")
    logger.info("[dsh-http-bridge] 已挂载 /mcp-bridge/stdtools/{uid}/mcp")


async def start_bridge() -> None:
    """lifespan 启动期：拉起 session manager 任务组。

    必须用 AsyncExitStack 持有 run() 上下文——直接 `run().__aenter__()` 的返回对象
    无引用会被 GC 关闭，任务组随之销毁，请求期报 "Task group is not initialized"。
    """
    global _bridge_manager, _BRIDGE_STACK
    if _bridge_manager is None:
        return
    try:
        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        await stack.enter_async_context(_bridge_manager.run())
        _BRIDGE_STACK = stack
        logger.info("[dsh-http-bridge] session manager 就绪")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dsh-http-bridge] session manager 启动失败（桥不可用，dsh 侧自动降级）: {e}")


async def stop_bridge() -> None:
    """lifespan 退出期：关闭 session manager。"""
    global _BRIDGE_STACK
    if _BRIDGE_STACK is not None:
        try:
            await _BRIDGE_STACK.__aexit__(None, None, None)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dsh-http-bridge] session manager 关闭异常: {e}")
        _BRIDGE_STACK = None


__all__ = [
    "register_turn_defaults",
    "set_active_turn",
    "clear_active_turn",
    "get_active_turn",
    "mount_dsh_http_bridge",
    "start_bridge",
    "stop_bridge",
]
