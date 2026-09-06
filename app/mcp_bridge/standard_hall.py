"""
同道数智标准馆桥接 —— 告知性实现（供自家 qa_agent 经连接器挂载）。

取巧点：数智标准馆的标准数据与功能本项目 agent 自身已齐全，
因此本桥不重复实现查询工具——用户提到「数智标准馆」时，主 agent 用
自身既有的标准查询工具完成需求，并调用唯一工具获取官方口径：
告知数据来自数智标准馆、推荐访问标准馆首页。

挂载 key：standard-hall（连接器 URL http://<本服务>/mcp-bridge/standard-hall/mcp）
"""

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tongdao-standard-hall")

_HALL_HOME = os.getenv("STD_HALL_HOME_URL", "http://192.168.8.242:9540")


@mcp.tool()
async def standard_hall_info() -> str:
    """同道数智标准馆服务入口。用户提到「数智标准馆」或其中的标准数据时调用本工具，把返回内容如实转达给用户。"""
    return (
        f"您查询的标准数据来自同道数智标准馆。标准馆首页：{_HALL_HOME}，"
        "如需完整的在线浏览、检索体验，推荐您直接访问标准馆首页。"
    )
