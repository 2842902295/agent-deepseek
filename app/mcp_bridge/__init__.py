"""
MCP 桥接（内置胶水层）：把自家外部应用的 MCP 服务挂载进本服务进程。

架构：
- 每个外部应用 = 一个 FastMCP 实例（一文件一应用，见 demo.py 模板），
  工具函数体内用 async httpx 调该应用自己的 HTTP 接口，外部应用零改动
- mount_mcp_bridges() 统一挂到 /mcp-bridge/<key>，连接器 URL 即
  http://<本服务>/mcp-bridge/<key>/mcp（transport=streamable_http）
- 平台侧走现有「连接器」（agent_connector）登记：列表可见、可试连、可启停，
  体验与外部 MCP 服务完全一致（回环连接：agent 拨回本进程）

新增应用：复制 demo.py → 补工具 → 在下面 _all_bridges() 登记一行 → 重启生效。

鉴权：env MCP_BRIDGE_TOKEN 配置后，所有桥接端点强制 Bearer 校验
（连接器 api_key 填同一个 token）；未配置则放行（仅适合本机开发）。

实现要点：不能用 FastMCP.streamable_http_app() 的现成封装——它依赖自身 lifespan
初始化 session_manager 任务组，而被 FastAPI mount 的子应用 lifespan 不会执行，
请求会报 "Task group is not initialized"。因此手动持有 StreamableHTTPSessionManager，
由主应用 lifespan 调 start/stop_mcp_session_managers() 拉起/关闭（参数与官方
streamable_http_app 内部一致：stateless=True、json_response=False——平台客户端
按次拨号，无需会话保持）。
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, List, Optional, Tuple

from loguru import logger
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send

# (挂载 key, FastMCP 实例, session_manager) —— mount_mcp_bridges() 填充，
# start/stop_mcp_session_managers() 消费。模块级持有，跨 lifespan 可达。
_BRIDGES: List[Tuple[str, FastMCP, StreamableHTTPSessionManager]] = []
_MANAGER_STACK: Optional[contextlib.AsyncExitStack] = None


class _BearerGuardMiddleware:
    """ASGI 级 Bearer 校验：Authorization 头与 MCP_BRIDGE_TOKEN 不符一律 401。"""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            if headers.get("authorization") != f"Bearer {self.token}":
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                await send({"type": "http.response.body", "body": "unauthorized".encode()})
                return
        await self.app(scope, receive, send)


class _MCPHandler:
    """ASGI 端点：把请求交给对应桥接的 session_manager（需先 run() 初始化任务组）。"""

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.manager.handle_request(scope, receive, send)


def _all_bridges() -> List[Tuple[str, Any]]:
    """桥接登记表：(挂载 key, FastMCP 实例)。新增应用在这里加一行。"""
    from app.mcp_bridge.demo import mcp as demo_mcp
    from app.mcp_bridge.standard_factory import mcp as factory_mcp
    from app.mcp_bridge.standard_hall import mcp as hall_mcp

    return [
        ("demo", demo_mcp),
        ("standard-factory", factory_mcp),
        ("standard-hall", hall_mcp),
    ]


def mount_mcp_bridges(app: Any) -> None:
    """把所有桥接 FastMCP 实例挂载进主 FastAPI（create_app 里调用）。

    每个桥接 = 一个 Starlette 子应用（单路由 /mcp → session_manager），
    session_manager 的任务组由主应用 lifespan 统一拉起（见下方 start/stop）。
    """
    global _BRIDGES
    _BRIDGES = []
    token = os.getenv("MCP_BRIDGE_TOKEN", "")
    for key, mcp_inst in _all_bridges():
        manager = StreamableHTTPSessionManager(
            app=mcp_inst._mcp_server,
            event_store=None,
            json_response=False,
            stateless=True,
        )
        sub = Starlette(routes=[Mount("/mcp", app=_MCPHandler(manager))])
        if token:
            sub.add_middleware(_BearerGuardMiddleware, token=token)
        else:
            logger.warning(f"[mcp_bridge] MCP_BRIDGE_TOKEN 未配置，/mcp-bridge/{key} 端点暂无鉴权（生产部署前请配置）")
        app.mount(f"/mcp-bridge/{key}", sub, name=f"mcp-bridge-{key}")
        _BRIDGES.append((key, mcp_inst, manager))
        logger.info(f"[mcp_bridge] 已挂载 /mcp-bridge/{key}/mcp（{mcp_inst.name}）")


async def start_mcp_session_managers() -> None:
    """lifespan 启动期调用：拉起所有桥接的 session manager 任务组。

    用 AsyncExitStack 统一持有（而非逐个 async with——后者要求"最后启动的
    先退出"，与启动顺序相反，多桥接时会抛 RuntimeError）。
    """
    global _MANAGER_STACK
    if not _BRIDGES:
        return
    try:
        stack = contextlib.AsyncExitStack()
        await stack.__aenter__()
        for key, _, manager in _BRIDGES:
            await stack.enter_async_context(manager.run())
            logger.info(f"[mcp_bridge] session manager 就绪：{key}")
        _MANAGER_STACK = stack
    except Exception as e:
        logger.warning(f"[mcp_bridge] session manager 启动失败（桥接不可用，不影响主服务）: {e}")


async def stop_mcp_session_managers() -> None:
    """lifespan 退出期调用：关闭所有桥接的 session manager。"""
    global _MANAGER_STACK
    if _MANAGER_STACK is not None:
        try:
            await _MANAGER_STACK.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"[mcp_bridge] session manager 关闭异常: {e}")
        _MANAGER_STACK = None
