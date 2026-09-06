"""
本地关键词内容审核。

词表路径由 settings.CONTENT_MODERATION_KEYWORDS_FILE 指定（绝对路径或相对项目根的路径）。
CONTENT_MODERATION=keyword 时生效，其他值（包括默认的 none）直接放行。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_DEFAULT_BLOCKLIST = Path(__file__).parent / "keywords_blocklist.txt"


@lru_cache(maxsize=1)
def _load_keywords(path: str) -> tuple[str, ...]:
    """加载词表，结果缓存。进程内词表文件变更不会自动热更新（重启生效）。"""
    p = Path(path)
    if not p.exists():
        return ()
    words = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    return tuple(words)


def _get_keywords() -> tuple[str, ...]:
    from app.settings.config import settings
    kw_file = getattr(settings, "CONTENT_MODERATION_KEYWORDS_FILE", None) or str(_DEFAULT_BLOCKLIST)
    return _load_keywords(kw_file)


def moderate_sync(text: str) -> str | None:
    """
    同步检查文本。
    返回 None 表示通过；返回命中的关键词字符串表示拦截。
    """
    from app.settings.config import settings
    provider = getattr(settings, "CONTENT_MODERATION", "none")
    if provider != "keyword":
        return None

    keywords = _get_keywords()
    if not keywords:
        return None

    lower = text.lower()
    for kw in keywords:
        if kw.lower() in lower:
            return kw
    return None


async def moderate(text: str) -> str | None:
    """异步版本，供 async 上下文调用。"""
    import asyncio
    return await asyncio.to_thread(moderate_sync, text)
