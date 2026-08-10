"""
MinerU 文档解析工具（LangChain tool 封装）

供 Agent 调用，将文档 URL 转换为 Markdown 文本。
支持 PDF、图片、Word、PPT、Excel 等格式。
"""

from __future__ import annotations

import asyncio
import time
from typing import Annotated

from langchain.tools import tool
from loguru import logger

from app.settings.config import settings

_IMAGE_BASE_URL = settings.JGH_IMAGE_BASE_URL
_CACHE_TTL = 1800  # 30 分钟

# {url: (timestamp, result)}
_cache: dict[str, tuple[float, str]] = {}


@tool
async def parse_with_mineru(
        filename: Annotated[str, "图片或文档的文件名，如 abc123.jpg 或 report.pdf，不含路径和域名"],
) -> str:
    """
    调用 MinerU 解析图片或文档，返回 Markdown 文本。
    遇到表格、公式、图片等复杂内容时优先使用此工具，比模型直接阅读更稳定准确。
    传入文件名即可，不需要拼接 URL。
    """
    from app.utils.mineru import convert_to_markdown, MinerUError
    try:
        url = _IMAGE_BASE_URL + filename.lstrip("/")

        cached = _cache.get(url)
        if cached and time.time() - cached[0] < _CACHE_TTL:
            logger.debug(f"MinerU 命中缓存: {url}")
            return cached[1]

        logger.debug(f"MinerU 解析 URL: {url}")
        # convert_to_markdown 是 async：直接 await，无需新建 loop
        result = await convert_to_markdown(url)
        _cache[url] = (time.time(), result)
        return result
    except MinerUError as e:
        logger.warning(f"MinerU 转换失败：{e}")
        return f"[MinerU 转换失败：{e}]"
    except Exception as e:
        logger.warning(f"MinerU 调用异常：{e}")
        return f"[MinerU 调用异常：{e}]"
