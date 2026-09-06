"""
dsh MCP 工具桥（stdio）：把 Python 侧业务工具暴露给 dsh 运行时。

dsh-jsonrpc-agent 运行时经内置 dsh-mcp-client 以 stdio 方式挂载本进程
（见 qa_agent.py::_build_mcp_servers / _build_cordis_yml）。工具实现直接复用
app.langchain.tools 下的现成工厂——与 deepagents 时代同一份代码（SQL 安全门、
pool 过滤等原样生效），仅把载体从 langchain tool 换成 MCP。

进程是「每 dsh 实例一个」（dsh 实例按用户缓存），用户上下文经 env 注入：
  - DSH_BRIDGE_USER_ID     → 绑定 kb/history/skill/admin 工具的用户闭包 + CTX_USER_ID
  - DSH_BRIDGE_IS_SUPER=1  → 追加 system-admin 工具组
  - DSH_BRIDGE_IS_ADMIN=1  → 追加 vector_lib 工具组（管理员专属：R_SUPER/R_ADMIN）
  - DSH_BRIDGE_WORKSPACE   → AgentCallContext.workspace_dir（产物/落盘类工具的根）

工具分组：
  - 基础（始终）：create_chart（标准数据工具已迁出，统一走系统数据集 MCP 服务
                  app/mcp_bridge/datasets.py，由用户在技能面板添加启用后生效，与桥形态无关）
  - 用户态（有 uid）：kb_*×6（知识库）/ history×4（历史回溯）/ skill_*×5（技能管理）
  - 管理员（uid+admin）：vector_lib×5（统一向量库体系对话侧入口）
  - 超管（uid+super）：admin_*×6

DB 依赖：skill/admin/history 工具走 Tortoise ORM——启动时尽力初始化，失败仅告警
（对应工具在调用时报错，不拖垮整个桥）。

⚠️ stdio 纪律：stdout 只能走 JSON-RPC。app.log 会在 import 期给 loguru 挂 stdout
sink，本模块在启动前一律摘除并改走 stderr（_purge_stdout_logging）。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import mcp.types as types
from loguru import logger
from mcp.server import Server
from mcp.server.stdio import stdio_server


def _purge_stdout_logging() -> None:
    """保持 stdout 纯净：摘掉 app.log 挂的 stdout sink，日志统一走 stderr。"""
    logger.remove()
    logger.add(sys.stderr, level="INFO")


_purge_stdout_logging()

app = Server("stdtools")

_tools_cache: list | None = None


def _bridge_user_id() -> int | None:
    v = os.environ.get("DSH_BRIDGE_USER_ID")
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _bridge_is_super() -> bool:
    return os.environ.get("DSH_BRIDGE_IS_SUPER") == "1"


def _bridge_is_admin() -> bool:
    """管理员（R_SUPER/R_ADMIN）：向量库管理工具组的门控。"""
    return os.environ.get("DSH_BRIDGE_IS_ADMIN") == "1"


def _setup_context() -> None:
    """进程级静态上下文：用户身份 + 工作区 + 生成能力角色覆盖（本进程只服务一个用户）。"""
    uid = _bridge_user_id()
    if uid is not None:
        try:
            from app.core.ctx import CTX_USER_ID

            CTX_USER_ID.set(uid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dsh-mcp] CTX_USER_ID 设置失败: {e}")
    # 按角色模型配置的生成能力覆盖（IMAGE/VIDEO 块名或 DISABLED 哨兵）：
    # dsh 实例按用户+profile 缓存，profile 变化即重建进程，env 烘焙是安全的
    gen = os.environ.get("DSH_BRIDGE_GEN_OVERRIDE")
    if gen:
        try:
            import json as _json

            from app.core.ctx import CTX_GEN_BLOCK_OVERRIDE

            CTX_GEN_BLOCK_OVERRIDE.set(_json.loads(gen))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dsh-mcp] CTX_GEN_BLOCK_OVERRIDE 设置失败: {e}")
    ws = os.environ.get("DSH_BRIDGE_WORKSPACE")
    if ws:
        try:
            from app.services.agent_runtime.call_context import AgentCallContext, set_agent_call_context

            set_agent_call_context(AgentCallContext(workspace_dir=Path(ws)))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dsh-mcp] AgentCallContext 设置失败: {e}")


async def _ensure_tortoise() -> None:
    """尽力初始化 Tortoise（skill/admin/history 工具需要）；失败仅告警不致命。"""
    try:
        from tortoise import Tortoise

        from app.settings.config import settings

        if not getattr(settings, "TORTOISE_ORM", None):
            settings._build_tortoise_orm()
        await Tortoise.init(config=settings.TORTOISE_ORM)
        logger.info("[dsh-mcp] Tortoise 初始化完成")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dsh-mcp] Tortoise 初始化失败（skill/admin/history 工具调用时会报错）: {e}")


def _tools() -> list:
    """懒加载工具集（首次 list/call 时才 import，避免空跑进程加载全套配置）。"""
    global _tools_cache
    if _tools_cache is None:
        from app.langchain.tools.chart_tool import create_chart

        # 标准数据工具（db 四件 + 语义搜索）已迁出：统一走系统数据集 MCP 服务
        # （app/mcp_bridge/datasets.py，用户在技能面板添加启用后生效，与 stdio/HTTP 桥形态无关）
        tools = [create_chart]

        # 生图/生视频：工具工厂内部按 has_capability 门控（跟随上面烘焙的角色覆盖），
        # 禁用/未配置时返回 []；加载异常仅告警跳过
        try:
            from app.langchain.tools.image_tools import get_image_tools
            from app.langchain.tools.video_tools import get_video_tools

            tools += list(get_image_tools()) + list(get_video_tools())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dsh-mcp] 生成工具加载失败（跳过）: {e}")

        # 视觉兜底 / 视频通道（与 HTTP 桥同口径）：VISION 角色模型读图/读视频返回文字描述；
        # 视觉块看图片走原生 read_image（系统提示词节已规定），本工具对视觉块只剩视频通道
        try:
            from app.langchain.config import has_role

            if has_role("VISION"):
                from app.langchain.tools.vision_tools import vision_inspect

                tools.append(vision_inspect)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dsh-mcp] vision_inspect 加载失败（跳过）: {e}")

        uid = _bridge_user_id()
        if uid is not None:
            from app.langchain.tools.history_tools import make_history_tools
            from app.langchain.tools.kb_tools import make_kb_tools
            from app.services.agent_runtime.edit_tools import SKILL_TOOLS

            tools += list(make_kb_tools(uid)) + list(make_history_tools(uid)) + list(SKILL_TOOLS)
            # 向量库管理（统一向量库体系对话侧入口）——仅管理员挂载
            if _bridge_is_admin():
                from app.langchain.tools.vector_lib_tools import make_vector_lib_tools

                tools += list(make_vector_lib_tools(uid, _bridge_is_super()))
            if _bridge_is_super():
                from app.langchain.tools.admin_tools import make_admin_tools

                tools += list(make_admin_tools(uid))
        # 去重保序（防同名工具撞车）
        seen: set = set()
        deduped = []
        for t in tools:
            if t.name not in seen:
                seen.add(t.name)
                deduped.append(t)
        _tools_cache = deduped
        logger.info(f"[dsh-mcp] 工具桥就绪（uid={uid}, super={_bridge_is_super()}）：{[t.name for t in _tools_cache]}")
    return _tools_cache


@app.list_tools()
async def _list_tools() -> list[types.Tool]:
    # tool_json_schema：给 bool / array / object 参数加 string 备选——MCP SDK 在
    # call_tool 前按 inputSchema 做 jsonschema 校验，不加备选字符串化入参会被拦死
    from app.langchain.tools._tool_args import tool_json_schema

    out: list[types.Tool] = []
    for t in _tools():
        out.append(types.Tool(name=t.name, description=(t.description or "")[:2000], inputSchema=tool_json_schema(t)))
    return out


@app.call_tool()
async def _call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    tool = next((t for t in _tools() if t.name == name), None)
    if tool is None:
        return [types.TextContent(type="text", text=f"未知工具：{name}")]
    try:
        # 工具多为 async-only StructuredTool（coroutine 实现）：优先 ainvoke；
        # 纯同步工具回退 to_thread(invoke)
        try:
            result = await tool.ainvoke(dict(arguments or {}))
        except NotImplementedError:
            result = await asyncio.to_thread(tool.invoke, dict(arguments or {}))
    except Exception as e:  # noqa: BLE001 - 工具异常物化为结果文本，回合不中断
        logger.exception(f"[dsh-mcp] 工具 {name} 执行失败")
        return [types.TextContent(type="text", text=f"工具执行失败：{e}")]
    text = result if isinstance(result, str) else str(result)
    # 单条 MCP 文本内容过大时截断（防 dsh 上下文爆炸；章节分页工具本身已有分页）
    if len(text) > 60000:
        text = text[:60000] + "\n...[内容过长已截断，请缩小查询范围或使用分页参数]"
    return [types.TextContent(type="text", text=text)]


async def _main() -> None:
    _purge_stdout_logging()  # 再摘一次：懒加载工具链的 import 可能重新挂 stdout sink
    _setup_context()
    await _ensure_tortoise()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(_main())
