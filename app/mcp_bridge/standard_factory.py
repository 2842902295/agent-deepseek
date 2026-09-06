"""
标准加工厂桥接 —— 未上架占位实现（供自家 qa_agent 经连接器挂载）。

用户问及/想使用「标准加工厂」时，主 agent 调用唯一工具拿到官方口径
（请等待正式上架），转达给用户即可。

正式上架后：删掉占位工具，按 demo.py 模板补真实接口工具，重启服务。
挂载 key：standard-factory（连接器 URL http://<本服务>/mcp-bridge/standard-factory/mcp）
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("standard-factory")


@mcp.tool()
async def standard_factory_service() -> str:
    """标准加工厂服务入口。用户问及或想使用「标准加工厂」时调用本工具，把返回内容如实转达给用户。"""
    return (
        "感谢您关注标准加工厂！加工厂目前正在进行最后的打磨，暂未对外开放，"
        "请您再等等，稍后会正式上架，届时即可在对话中直接使用，敬请期待。"
    )
