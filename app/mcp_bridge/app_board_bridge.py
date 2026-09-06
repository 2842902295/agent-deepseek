"""
应用制作「互动接口」MCP 桥：把 apps/{wk}/mcp.json manifest 托管成真实 MCP 服务。

用户在对话中与「应用制作」应用直接互动（五子棋落子、多 NPC 社区发言……）的运行时载体：
agent 开发应用时写一份声明式 manifest（列出对外开放的工具 + 每个工具对 agent_app_row
行数据的效果），本模块据 manifest 动态暴露工具面、并在 call_tool 时**在服务端执行效果**
（读写行数据），页面是否打开都能调。执行后 publish SSE 事件，订阅该板的页面即时刷新。

挂载：`/mcp-bridge/app-board/{uid}/{wf_key}/mcp`（per-(uid,wk) 端点，qa.py 把它作为
连接器 `app_{wk}` 挂进会话；dsh mcp-client 拨回本进程）。**一个 Server + 一个 manager
服务所有 (uid,wk)**——工具面是请求级的：list_tools / call_tool 从 ContextVar 取 (uid,wk)，
按对应 manifest 动态生成 / 执行（handler 从 URL path_params 注入 ContextVar，仿 datasets）。

身份口径（与 workflow_tools.update_app_rows 完全一致）：以**板主身份**执行，updated_by 恒 =
板主 uid，**不查 $acl**（权限治理面只归 update_app_rows；manifest 里 $acl 表已被校验期拒绝）。
actor 区分靠 manifest 工具的显式入参（如 `actor`）落进 data，平台侧不区分主/子 agent。

红线：
- 磁盘 I/O（manifest 读盘）走 app_manifest.aload_manifest（内部 to_thread）。
- DB 全走 Tortoise async。
- put-merge / append 的「读-改-写」在 per-(wk,tbl,key) asyncio.Lock 内串行（多 actor 并发核心）。
- call_tool 失败返回 {ok:false,...} 文本，绝不抛异常拖垮 manager 任务组。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Optional

import mcp.types as types
from loguru import logger
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from app.models.standard.agent import AgentAppRow, AgentWorkflow
from app.services.agent_runtime import app_events, app_manifest
from app.services.agent_runtime.app_manifest import TemplateError, render_template

# 桥层文本兜底上限（与 datasets 同口径）
_BRIDGE_TEXT_CAP = 1400000

# 请求级 (uid, wf_key) 上下文（handler 从 URL path_params 注入）
_CTX_APP_UID: ContextVar[Optional[int]] = ContextVar("app_board_uid", default=None)
_CTX_APP_WK: ContextVar[Optional[str]] = ContextVar("app_board_wk", default=None)

# 工具显示名注册表：wk → {tool_name: displayName}（tool_display_names 动态查它做时间线中文化）
_DISPLAY_NAMES: dict[str, dict[str, str]] = {}


# ── html_app 校验常量（延迟导入防循环：桥经 create_app 挂载，早于 api 包加载） ──────
_html_consts_cache: Optional[tuple] = None


def _html_consts() -> tuple:
    """(_valid_tbl, _valid_row_key, _ROW_DATA_MAX_BYTES, _ACL_TBL, _ROWS_QUERY_LIMIT_MAX)。"""
    global _html_consts_cache
    if _html_consts_cache is None:
        from app.api.v1.ai.html_app import (
            _ACL_TBL,
            _ROW_DATA_MAX_BYTES,
            _ROWS_QUERY_LIMIT_MAX,
            _valid_row_key,
            _valid_tbl,
        )

        _html_consts_cache = (_valid_tbl, _valid_row_key, _ROW_DATA_MAX_BYTES, _ACL_TBL, _ROWS_QUERY_LIMIT_MAX)
    return _html_consts_cache


# ── 属主校验（TTL 60s 缓存） ───────────────────────────────────────────────────
_owner_cache: dict[tuple[int, str], tuple[float, bool]] = {}
_OWNER_TTL = 60.0


async def _owner_ok(uid: Optional[int], wk: Optional[str]) -> bool:
    """校验 (uid, wk) 是该用户名下未删除的 html 应用制作板。失败 → 工具面空 / 调用拒绝。"""
    if uid is None or not wk:
        return False
    now = time.monotonic()
    hit = _owner_cache.get((uid, wk))
    if hit is not None and now - hit[0] < _OWNER_TTL:
        return hit[1]
    ok = False
    try:
        wf = await AgentWorkflow.get_or_none(workflow_key=wk, user_id=uid, is_deleted=0)
        ok = wf is not None and (wf.board_type or "board") == "html"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[app-board] 属主校验失败 uid={uid} wk={wk}: {e!r}")
        ok = False
    _owner_cache[(uid, wk)] = (now, ok)
    return ok


# ── per-(wk,tbl,key) 行锁（put-merge / append 的 RMW 串行化，引用计数用完即清） ──────
_row_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
_row_lock_refs: dict[tuple[str, str, str], int] = {}


@asynccontextmanager
async def _row_lock(wk: str, tbl: str, key: str):
    k = (wk, tbl, key)
    lock = _row_locks.get(k)
    if lock is None:
        lock = asyncio.Lock()
        _row_locks[k] = lock
    _row_lock_refs[k] = _row_lock_refs.get(k, 0) + 1
    await lock.acquire()
    try:
        yield
    finally:
        lock.release()
        _row_lock_refs[k] -= 1
        if _row_lock_refs[k] <= 0 and not lock.locked():
            _row_locks.pop(k, None)
            _row_lock_refs.pop(k, None)


class _EffectError(Exception):
    """效果执行失败（携带面向 agent 的中文原因）。"""


# ── 效果执行器 ────────────────────────────────────────────────────────────────
async def _eff_read(wk: str, eff: dict, args: dict, ctx: dict) -> Any:
    _, _, _, _, limit_max = _html_consts()
    tbl = eff["tbl"]
    qs = AgentAppRow.filter(workflow_key=wk, tbl=tbl)
    if eff.get("prefix"):
        qs = qs.filter(row_key__startswith=eff["prefix"])
    if eff.get("keys"):
        rendered_keys = []
        for kt in eff["keys"]:
            rk = render_template(kt, args, None, ctx)
            rk = "" if rk is None else str(rk)
            rendered_keys.append(rk)
        qs = qs.filter(row_key__in=rendered_keys)
    lim = eff.get("limit") or 200
    lim = max(1, min(int(lim), limit_max))
    rows = await qs.order_by("row_key").limit(lim + 1)
    more = len(rows) > lim
    return {
        "rows": [
            {
                "key": r.row_key,
                "data": r.data,
                "updatedBy": str(r.updated_by) if r.updated_by is not None else None,
                "updatedAt": r.update_time.isoformat() if r.update_time else None,
            }
            for r in rows[:lim]
        ],
        "more": more,
    }


def _check_row_size(data: Any, key: str) -> None:
    _, _, size_max, _, _ = _html_consts()
    if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > size_max:
        raise _EffectError(f"行数据过大（上限 {size_max // 1024}KB）：{key[:32]}")


def _render_key(kt: Any, args: dict, ctx: dict) -> str:
    _, valid_row_key, _, _, _ = _html_consts()
    rk = render_template(kt, args, None, ctx)
    rk = "" if rk is None else str(rk)
    if not rk or not valid_row_key(rk):
        raise _EffectError(f"渲染出的行键非法或为空：{rk[:32]!r}")
    return rk


async def _eff_put(wk: str, uid: int, eff: dict, args: dict, ctx: dict) -> Any:
    tbl = eff["tbl"]
    key = _render_key(eff["key"], args, ctx)
    data = render_template(eff["data"], args, None, ctx)
    if not isinstance(data, dict):
        raise _EffectError("put.data 渲染结果必须是 JSON 对象")
    if eff.get("merge"):
        async with _row_lock(wk, tbl, key):
            cur = await AgentAppRow.get_or_none(workflow_key=wk, tbl=tbl, row_key=key)
            base = dict(cur.data) if (cur and isinstance(cur.data, dict)) else {}
            # 浅合并，渲染值为 null 的键跳过（可选参数缺省不改字段的关键语义）
            for k, v in data.items():
                if v is not None:
                    base[k] = v
            _check_row_size(base, key)
            await AgentAppRow.update_or_create(
                workflow_key=wk, tbl=tbl, row_key=key,
                defaults={"data": base, "updated_by": uid},
            )
    else:
        _check_row_size(data, key)
        await AgentAppRow.update_or_create(
            workflow_key=wk, tbl=tbl, row_key=key,
            defaults={"data": data, "updated_by": uid},
        )
    return {"key": key}


async def _eff_append(wk: str, uid: int, eff: dict, args: dict, ctx: dict) -> Any:
    tbl = eff["tbl"]
    key = _render_key(eff["key"], args, ctx)
    field = eff["field"]
    value = render_template(eff["value"], args, None, ctx)
    max_len = eff.get("maxLen")
    async with _row_lock(wk, tbl, key):
        cur = await AgentAppRow.get_or_none(workflow_key=wk, tbl=tbl, row_key=key)
        base = dict(cur.data) if (cur and isinstance(cur.data, dict)) else {}
        arr = base.get(field)
        if not isinstance(arr, list):
            arr = []
        arr.append(value)
        if max_len:
            arr = arr[-int(max_len):]
        base[field] = arr
        _check_row_size(base, key)
        await AgentAppRow.update_or_create(
            workflow_key=wk, tbl=tbl, row_key=key,
            defaults={"data": base, "updated_by": uid},
        )
    return {"key": key, "len": len(arr)}


async def _eff_delete(wk: str, eff: dict, args: dict, ctx: dict) -> Any:
    tbl = eff["tbl"]
    qs = AgentAppRow.filter(workflow_key=wk, tbl=tbl)
    if eff.get("keys"):
        rendered_keys = []
        for kt in eff["keys"]:
            rk = render_template(kt, args, None, ctx)
            rk = "" if rk is None else str(rk)
            rendered_keys.append(rk)
        qs = qs.filter(row_key__in=rendered_keys)
    elif eff.get("prefix") is not None:
        qs = qs.filter(row_key__startswith=eff["prefix"])
    else:
        raise _EffectError("delete 必须给 keys 或 prefix")
    deleted = await qs.delete()
    return {"deleted": deleted}


async def _run_tool(wk: str, uid: int, tool_def: dict, args: dict) -> dict:
    """顺序执行一个工具的全部 effects。任一失败中止后续（不回滚），返回 {ok,...}。"""
    session = ""
    try:
        from app.mcp_bridge.dsh_http_bridge import get_active_turn

        session = get_active_turn(uid).get("session_key") or ""
    except Exception:  # noqa: BLE001 —— 拿不到 session 不阻塞执行
        session = ""
    ctx = {"uid": uid, "session": session, "ts": int(time.time() * 1000), "tool": tool_def["name"]}

    results: list[Any] = []
    changed_tbls: list[str] = []
    effects = tool_def.get("effects") or []
    for i, eff in enumerate(effects):
        op = eff.get("op")
        try:
            if op == "read":
                results.append(await _eff_read(wk, eff, args, ctx))
            elif op == "put":
                results.append(await _eff_put(wk, uid, eff, args, ctx))
                changed_tbls.append(eff["tbl"])
            elif op == "append":
                results.append(await _eff_append(wk, uid, eff, args, ctx))
                changed_tbls.append(eff["tbl"])
            elif op == "delete":
                results.append(await _eff_delete(wk, eff, args, ctx))
                changed_tbls.append(eff["tbl"])
            else:
                raise _EffectError(f"未知效果 op：{op}")
        except (TemplateError, _EffectError) as e:
            return {"ok": False, "error": str(e), "doneEffects": i, "results": results}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[app-board] 效果执行异常 wk={wk} tool={tool_def['name']} eff[{i}]: {e!r}")
            return {"ok": False, "error": f"效果执行失败：{e}", "doneEffects": i, "results": results}

    # 有写效果 → SSE 广播（by=mcp），订阅该板的页面 onChange 刷新
    if changed_tbls:
        app_events.publish(wk, list(dict.fromkeys(changed_tbls)), "mcp")
    return {"ok": True, "results": results}


# ── MCP Server 工厂（单实例服务所有 (uid,wk)，工具面请求级动态） ──────────────────
def _make_app_board_server() -> Server:
    server = Server("app-board")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        uid, wk = _CTX_APP_UID.get(), _CTX_APP_WK.get()
        if not await _owner_ok(uid, wk):
            return []
        manifest, _err, _fp = await app_manifest.aload_manifest(uid, wk)
        if not manifest:
            return []  # 缺失 / 非法 → 空工具面（连接器挂着无工具，dsh 不炸）
        tools: list[types.Tool] = []
        names: dict[str, str] = {}
        for t in manifest.get("tools", []):
            nm = t["name"]
            schema = t.get("inputSchema") or {"type": "object", "properties": {}}
            tools.append(types.Tool(name=nm, description=(t.get("description") or "")[:2000], inputSchema=schema))
            dn = t.get("displayName")
            if dn:
                names[nm] = dn
        if wk:
            _DISPLAY_NAMES[wk] = names
        return tools

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        uid, wk = _CTX_APP_UID.get(), _CTX_APP_WK.get()
        if not await _owner_ok(uid, wk):
            return [types.TextContent(type="text", text=json.dumps({"ok": False, "error": "应用不存在或无权访问"}, ensure_ascii=False))]
        manifest, err, _fp = await app_manifest.aload_manifest(uid, wk)
        if not manifest:
            return [types.TextContent(type="text", text=json.dumps({"ok": False, "error": err or "互动接口未配置"}, ensure_ascii=False))]
        tool_def = next((t for t in manifest.get("tools", []) if t.get("name") == name), None)
        if tool_def is None:
            return [types.TextContent(type="text", text=json.dumps({"ok": False, "error": f"未知工具：{name}"}, ensure_ascii=False))]

        args = dict(arguments or {})
        try:
            result = await _run_tool(wk, uid, tool_def, args)
        except Exception as e:  # noqa: BLE001 —— 兜底：绝不抛异常拖垮 manager 任务组
            logger.exception(f"[app-board] 工具 {name} 执行崩溃 wk={wk}")
            result = {"ok": False, "error": f"工具执行失败：{e}"}
        text = json.dumps(result, ensure_ascii=False)
        if len(text) > _BRIDGE_TEXT_CAP:
            text = text[:_BRIDGE_TEXT_CAP] + "\n...[内容过长已截断]"
        return [types.TextContent(type="text", text=text)]

    return server


# ── ASGI 挂载 ─────────────────────────────────────────────────────────────────
_APP_BOARD_SERVER: Optional[Server] = None
_APP_BOARD_MANAGER: Optional[StreamableHTTPSessionManager] = None
_APP_BOARD_STACK: Optional[contextlib.AsyncExitStack] = None


class _AppBoardHandler:
    """ASGI 入口：从 URL path_params 解析 (uid, wf_key) 注入 ContextVar。"""

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope, receive, send) -> None:
        params = scope.get("path_params") or {}
        uid_raw = params.get("uid")
        try:
            uid = int(uid_raw) if uid_raw is not None else None
        except (TypeError, ValueError):
            uid = None
        wk = params.get("wf_key")
        wk = str(wk) if wk is not None else None
        t_uid = _CTX_APP_UID.set(uid)
        t_wk = _CTX_APP_WK.set(wk)
        try:
            await self.manager.handle_request(scope, receive, send)
        finally:
            _CTX_APP_UID.reset(t_uid)
            _CTX_APP_WK.reset(t_wk)


def mount_app_board_bridge(app: Any) -> None:
    """create_app 里调用：挂载 /mcp-bridge/app-board/{uid}/{wf_key}/mcp。"""
    global _APP_BOARD_SERVER, _APP_BOARD_MANAGER
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from app.mcp_bridge import _BearerGuardMiddleware

    _APP_BOARD_SERVER = _make_app_board_server()
    _APP_BOARD_MANAGER = StreamableHTTPSessionManager(
        app=_APP_BOARD_SERVER,
        event_store=None,
        json_response=False,
        stateless=True,
    )
    sub = Starlette(routes=[Mount("/{uid:int}/{wf_key}/mcp", app=_AppBoardHandler(_APP_BOARD_MANAGER))])
    token = os.getenv("MCP_BRIDGE_TOKEN", "")
    if token:
        sub.add_middleware(_BearerGuardMiddleware, token=token)
    else:
        logger.warning("[app-board] MCP_BRIDGE_TOKEN 未配置，/mcp-bridge/app-board 端点暂无鉴权（仅适合本机开发）")
    app.mount("/mcp-bridge/app-board", sub, name="mcp-bridge-app-board")
    logger.info("[app-board] 已挂载 /mcp-bridge/app-board/{uid}/{wf_key}/mcp")


async def start_app_board_bridge() -> None:
    """lifespan 启动期：拉起 session manager 任务组（AsyncExitStack 持有，防 GC 杀任务组）。"""
    global _APP_BOARD_STACK
    if _APP_BOARD_MANAGER is None:
        return
    try:
        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        await stack.enter_async_context(_APP_BOARD_MANAGER.run())
        _APP_BOARD_STACK = stack
        logger.info("[app-board] session manager 就绪")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[app-board] session manager 启动失败（互动接口不可用，不影响主服务）: {e}")


async def stop_app_board_bridge() -> None:
    """lifespan 退出期：关闭 session manager。"""
    global _APP_BOARD_STACK
    if _APP_BOARD_STACK is not None:
        try:
            await _APP_BOARD_STACK.__aexit__(None, None, None)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[app-board] session manager 关闭异常: {e}")
        _APP_BOARD_STACK = None
