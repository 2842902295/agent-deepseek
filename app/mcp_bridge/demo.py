"""
MCP 桥接模板（demo）—— 新接入一个自家应用时，复制本文件改名即可。

步骤：
1. `mcp = FastMCP("应用名")` 改成你的应用名
2. 目标应用地址 / 鉴权填进 TARGET_BASE_URL / TARGET_HEADERS
3. 要暴露的能力各写一个 @mcp.tool() 函数——函数体用 httpx 调该应用的 HTTP 接口；
   **描述写清"何时调用"**（LLM 靠它选工具，写法同 skill description 纪律）
4. 去 app/mcp_bridge/__init__.py::_all_bridges() 登记一行，重启服务生效

连接器登记（平台「连接器」页面添加）：
- URL：http://<本服务地址>/mcp-bridge/<key>/mcp （key 即 _all_bridges 里登记的键）
- transport：streamable_http
- api_key：MCP_BRIDGE_TOKEN 配置了就填它（本端 Bearer 校验），没配置则留空
"""

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("bridge-demo")

# ── 目标应用连接信息（补真实值）─────────────────────────────────────────────
TARGET_BASE_URL = "http://192.168.8.1:8000"
TARGET_HEADERS: dict = {
    # "Authorization": "Bearer <目标应用的凭据>",
}

_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


@mcp.tool()
async def bridge_ping(text: str = "hello") -> str:
    """连通性自检：原样返回传入文本。试连 / 排查桥接是否正常时调用。"""
    return f"pong: {text}"


# ── 模板：调用目标应用 HTTP 接口的完整写法（补真实接口后取消注释）────────────
# 纪律：
# - 必须 async httpx（本项目单 worker，同步阻塞调用会卡死事件循环）
# - 返回值给文本 / JSON 字符串，别塞二进制大 blob
# - 报错直接 raise 带人话的异常，平台会把错误文本转给 LLM 自行处置，不会崩 agent
#
# @mcp.tool()
# async def query_work_order(order_id: str) -> str:
#     """按工单号查询 OA 工单详情。用户问工单进度 / 状态时调用。"""
#     async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
#         resp = await client.get(
#             f"{TARGET_BASE_URL}/api/orders/{order_id}",
#             headers=TARGET_HEADERS,
#         )
#         resp.raise_for_status()
#         return resp.text
