"""
MCP（Model Context Protocol）客户端：把外部 MCP server 提供的工具适配为 LangChain 工具。

当前接入：
  - 阿里云 DashScope WebSearch（联网搜索）
  - Playwright（浏览器操作，仅 BRAND_VARIANT=standard 内网版）

历史：QwenImage 曾走 MCP 接入，现已替换为 in-process 工具
（见 `app.langchain.tools.image_tools.generate_image`），从这里下掉。
"""

from __future__ import annotations

import functools

from loguru import logger

from app.langchain.config import langchain_config


def _wrap_tool_safe(tool) -> None:
    """
    包裹 tool.coroutine，捕获所有异常（包括 McpError）并转成字符串返回，
    避免连接异常冒泡导致 agent 崩溃。
    同时处理单文本内容的格式转换（修复 response_format='content_and_artifact' 错误）。
    """
    if not hasattr(tool, "coroutine") or tool.coroutine is None:
        return
    original_coro = tool.coroutine

    @functools.wraps(original_coro)
    async def safe_coro(*args, **kwargs):
        try:
            result = await original_coro(*args, **kwargs)

            # 修复：如果返回的是二元组，检查第一个元素是否为单文本内容
            # 这解决了 "response_format='content_and_artifact' expects two-tuple" 错误
            if isinstance(result, tuple) and len(result) == 2:
                tool_content, artifact = result
                # 如果内容是单个文本类型的列表，提取为纯字符串
                if (
                    isinstance(tool_content, list)
                    and len(tool_content) == 1
                    and isinstance(tool_content[0], dict)
                    and tool_content[0].get("type") == "text"
                ):
                    return tool_content[0]["text"], artifact

            return result
        except Exception as e:
            logger.warning(f"[MCP] 工具 {tool.name} 调用失败: {e}")
            # 返回二元组格式：(错误消息, None)
            return f"❌ 工具调用失败：{type(e).__name__}: {e}", None

    tool.coroutine = safe_coro


def _attach_billing_callback(tool, *, provider: str) -> None:
    """给工具挂一个 LangChain callback：调用结束时自动记 mcp_call。

    ⚠️ 不要 monkey-patch `_arun` / `coroutine`：StructuredTool 的 `_arun` 把 `config`
    标为必填关键字参，LangChain `BaseTool.arun` 会用 inspect 检查 `_arun` 的签名
    决定是否传 config——替换成 `*args, **kwargs` 的版本会被 inspect 漏掉 config，
    转发到原 `_arun` 时炸 TypeError。

    走 callback 才是稳定路径：BaseTool.ainvoke 内部会触发，无视工具签名。
    """
    from app.langchain.billing.callback import BillingToolCallbackHandler

    handler = BillingToolCallbackHandler(provider=provider, tool_name=tool.name)
    try:
        existing = list(getattr(tool, "callbacks", None) or [])
        existing.append(handler)
        tool.callbacks = existing
    except Exception:
        logger.exception(f"[Billing] 给 MCP 工具挂 callback 失败 tool={tool.name}")


async def get_mcp_tools() -> list:
    """
    异步获取所有已配置的 MCP server 工具列表。
    若依赖未装、env 未配置，或连接失败，返回空列表（不阻断 agent 启动）。
    """
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        logger.warning("langchain-mcp-adapters 未安装，跳过 MCP 工具加载")
        return []

    connections: dict = {}
    # provider 标记，用于计费时分辨上游平台
    provider_by_server: dict[str, str] = {}

    # ── Playwright MCP 服务器（仅 standard 内网版加载） ──────────────────────
    from app.settings import APP_SETTINGS
    # if APP_SETTINGS.BRAND_VARIANT.lower() == "standard":
    #     connections["playwright"] = {
    #         "url": "http://192.168.8.36:3000/mcp",
    #         "transport": "http"
    #     }
    #     provider_by_server["playwright"] = "playwright"

    # ── DashScope WebSearch（SSE） ────────────────────────────────────────────
    dashscope_key = langchain_config.DASHSCOPE_API_KEY
    if dashscope_key:
        connections["websearch"] = {
            "transport": "sse",
            "url": "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/sse",
            "headers": {"Authorization": f"Bearer {dashscope_key}"},
        }
        provider_by_server["websearch"] = "dashscope"
    else:
        logger.info("DASHSCOPE_API_KEY 未配置，跳过 DashScope MCP（WebSearch）")

    if not connections:
        return []

    try:
        client = MultiServerMCPClient(connections)
        tools = await client.get_tools()
        # 让 MCP 工具异常（限流 429、超时、远端错等）作为 ToolMessage 文本回传给 LLM，
        # 而不是让 ToolException 冒泡终止整个 agent 流——LLM 看到错误信息后会自主决定如何应对。
        # 同时给每个工具挂上计费 callback（默认 dashscope，未来多 provider 可改 by tool.name 路由）。
        default_provider = next(iter(provider_by_server.values()), "unknown")
        for t in tools:
            # handle_tool_error=True 只能捕 ToolException；McpError/连接异常发生在更底层，
            # 需要用 response_format="content_and_artifact" 或直接包裹 coroutine 来兜底。
            # 这里改用字符串模式：任何异常都把 str(e) 转成 ToolMessage 文本返给 LLM。
            try:
                t.handle_tool_error = True
            except Exception:
                pass
            _wrap_tool_safe(t)
            _attach_billing_callback(t, provider=default_provider)
        logger.info(f"已加载 {len(tools)} 个 MCP 工具：{[t.name for t in tools]}")
        return tools
    except Exception as e:
        logger.exception(f"MCP 工具加载失败：{e}")
        return []


__all__ = ["get_mcp_tools"]
