"""
SSE MCP 连接器代理：dsh 的 mcp-client 只支持 streamable-http，用户自添加的
SSE 连接器由本模块在 FastAPI 进程内代为转发（上游 SSE ↔ 下游 streamable-http）。

挂载：/mcp-proxy/{uid}/{key}/mcp。代理条目在 qa_agent 构建期注册
（register_sse_proxy，按「用户+连接器」为键——同一连接器不同用户的凭据不同）。

转发策略：每次操作拨号一次上游（initialize → list/call → 断开）。SSE 握手
约半秒/次，preview 阶段可接受；后续可优化为常驻连接池。

计费：与 deepagents 时代一致——连接器工具调用记 mcp_call（provider=connector:<key>）。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from contextvars import ContextVar
from typing import Any, Optional

import mcp.types as types
from loguru import logger
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

# (uid, connector_key) → {url, api_key}
_PROXY_REGISTRY: dict[tuple, dict] = {}
_CTX_PROXY_KEY: ContextVar[Optional[tuple]] = ContextVar("dsh_sse_proxy_key", default=None)

_UPSTREAM_TIMEOUT_S = 60.0


def register_sse_proxy(uid: Optional[int], key: str, url: str, api_key: Optional[str]) -> None:
    """qa_agent 构建期登记代理条目（重复登记覆盖，凭据随连接器保存更新）。"""
    if uid is None or not key or not url:
        return
    _PROXY_REGISTRY[(uid, key)] = {"url": url, "api_key": api_key}


async def _open_upstream(entry: dict):
    """拨号上游 SSE MCP：返回 (退出栈上下文, ClientSession)。调用方负责关闭。"""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    headers = {"Authorization": f"Bearer {entry['api_key']}"} if entry.get("api_key") else None
    cm = sse_client(entry["url"], headers=headers, timeout=30, sse_read_timeout=30)
    read_stream, write_stream = await cm.__aenter__()
    session = ClientSession(read_stream, write_stream)
    try:
        await session.__aenter__()
        await asyncio.wait_for(session.initialize(), timeout=30)
    except BaseException:
        try:
            await session.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        await cm.__aexit__(None, None, None)
        raise
    return cm, session


async def _close_upstream(cm, session) -> None:
    for closer in (session, cm):
        try:
            await closer.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


_server = Server("sse-proxy")


def _resolve_entry() -> Optional[dict]:
    pk = _CTX_PROXY_KEY.get()
    if pk is None:
        return None
    return _PROXY_REGISTRY.get(pk)


@_server.list_tools()
async def _list_tools() -> list[types.Tool]:
    entry = _resolve_entry()
    if entry is None:
        logger.warning("[sse-proxy] 未登记的代理请求（list_tools 返回空）")
        return []
    try:
        cm, session = await _open_upstream(entry)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[sse-proxy] 上游连接失败 url={entry['url']}: {e}")
        return []
    try:
        res = await asyncio.wait_for(session.list_tools(), timeout=_UPSTREAM_TIMEOUT_S)
        return list(res.tools)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[sse-proxy] list_tools 转发失败: {e}")
        return []
    finally:
        await _close_upstream(cm, session)


@_server.call_tool()
async def _call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    pk = _CTX_PROXY_KEY.get()
    entry = _resolve_entry()
    if entry is None:
        return [types.TextContent(type="text", text="代理未登记，无法调用")]
    try:
        cm, session = await _open_upstream(entry)
    except Exception as e:  # noqa: BLE001
        return [types.TextContent(type="text", text=f"上游连接器连接失败：{e}")]
    try:
        res = await asyncio.wait_for(
            session.call_tool(name, dict(arguments or {})),
            timeout=_UPSTREAM_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[sse-proxy] call_tool 转发失败 tool={name}: {e}")
        return [types.TextContent(type="text", text=f"工具调用失败：{e}")]
    finally:
        await _close_upstream(cm, session)

    # 计费：与 deepagents 时代一致（connector:<key> provider 记 mcp_call）
    try:
        from app.langchain.billing.pricing import Billing

        conn_key = pk[1] if pk else "unknown"
        await Billing.record(module="mcp", provider=f"connector:{conn_key}", model=name, units={"mcp_call": 1})
    except Exception:
        logger.exception("[sse-proxy] Billing.record 异常（已吞掉）")

    return list(res.content) if res and res.content else [types.TextContent(type="text", text="（无返回内容）")]


# ── ASGI 挂载 ─────────────────────────────────────────────────────────────────

_proxy_manager: Optional[StreamableHTTPSessionManager] = None
_PROXY_STACK: Optional[contextlib.AsyncExitStack] = None


class _UidKeyProxyHandler:
    """ASGI 入口：从 URL 解析 uid/connector_key 注入 contextvar，再交给 manager。"""

    async def __call__(self, scope, receive, send) -> None:
        params = scope.get("path_params") or {}
        uid_raw, key = params.get("uid"), params.get("key")
        try:
            pk = (int(uid_raw), str(key)) if uid_raw is not None and key else None
        except (TypeError, ValueError):
            pk = None
        token = _CTX_PROXY_KEY.set(pk)
        try:
            assert _proxy_manager is not None
            await _proxy_manager.handle_request(scope, receive, send)
        finally:
            _CTX_PROXY_KEY.reset(token)


def mount_sse_proxy(app: Any) -> None:
    """create_app 里调用：挂载 /mcp-proxy/{uid}/{key}/mcp。"""
    global _proxy_manager
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from app.mcp_bridge import _BearerGuardMiddleware

    manager = StreamableHTTPSessionManager(
        app=_server,
        event_store=None,
        json_response=False,
        stateless=True,
    )
    _proxy_manager = manager

    token = os.getenv("MCP_BRIDGE_TOKEN", "")
    sub = Starlette(routes=[Mount("/{uid:int}/{key}/mcp", app=_UidKeyProxyHandler())])
    if token:
        sub.add_middleware(_BearerGuardMiddleware, token=token)
    app.mount("/mcp-proxy", sub, name="mcp-sse-proxy")
    logger.info("[sse-proxy] 已挂载 /mcp-proxy/{uid}/{key}/mcp")


async def start_proxy() -> None:
    """lifespan 启动期：拉起 session manager 任务组（AsyncExitStack 持有防 GC 关闭）。"""
    global _proxy_manager, _PROXY_STACK
    if _proxy_manager is None:
        return
    try:
        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        await stack.enter_async_context(_proxy_manager.run())
        _PROXY_STACK = stack
        logger.info("[sse-proxy] session manager 就绪")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[sse-proxy] session manager 启动失败（SSE 连接器不可用）: {e}")


async def stop_proxy() -> None:
    """lifespan 退出期：关闭 session manager。"""
    global _PROXY_STACK
    if _PROXY_STACK is not None:
        try:
            await _PROXY_STACK.__aexit__(None, None, None)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[sse-proxy] session manager 关闭异常: {e}")
        _PROXY_STACK = None


__all__ = ["register_sse_proxy", "mount_sse_proxy", "start_proxy", "stop_proxy"]
