"""
全局 aiomysql 连接池（standard 业务库）

用法：
    from app.services.mysql_pool import standard_pool

    async with standard_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT ...", params)
            rows = await cur.fetchall()

设计：
- 单进程一个池；池在首次 acquire 时懒加载，无需在 lifespan 中显式 init。
- lifespan 退出时调用 close_pool() 优雅关闭。
- 默认 minsize=1, maxsize=20；高并发场景调大 maxsize（aiomysql 受 MySQL max_connections 限制）。
- pool_recycle=3600 防止 wait_timeout 导致连接被服务端裁掉后下次报 "MySQL server has gone away"。
"""

from __future__ import annotations

import asyncio
from typing import Optional

import aiomysql
from loguru import logger

from app.settings.config import settings


class _StandardPool:
    """对 aiomysql.Pool 的薄包装，提供 lazy init 与 acquire 上下文。"""

    def __init__(self) -> None:
        self._pool: Optional[aiomysql.Pool] = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> aiomysql.Pool:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    logger.info(
                        f"[mysql_pool] 创建 standard 池 → "
                        f"{settings.STANDARD_MYSQL_USER}@{settings.STANDARD_MYSQL_HOST}:"
                        f"{settings.STANDARD_MYSQL_PORT}/{settings.STANDARD_MYSQL_DB}"
                    )
                    self._pool = await aiomysql.create_pool(
                        host=settings.STANDARD_MYSQL_HOST,
                        port=settings.STANDARD_MYSQL_PORT,
                        user=settings.STANDARD_MYSQL_USER,
                        password=settings.STANDARD_MYSQL_PASSWORD,
                        db=settings.STANDARD_MYSQL_DB,
                        charset="utf8mb4",
                        autocommit=True,
                        minsize=1,
                        maxsize=20,
                        pool_recycle=3600,
                    )
        return self._pool

    def acquire(self) -> "_AcquireCM":
        """async with standard_pool.acquire() as conn: ..."""
        return _AcquireCM(self)

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            logger.info("[mysql_pool] standard 池已关闭")


class _AcquireCM:
    """从池里租一个连接的 async context manager。"""

    def __init__(self, owner: _StandardPool) -> None:
        self._owner = owner
        self._pool: Optional[aiomysql.Pool] = None
        self._conn: Optional[aiomysql.Connection] = None

    async def __aenter__(self) -> aiomysql.Connection:
        self._pool = await self._owner._ensure()
        self._conn = await self._pool.acquire()
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None and self._pool is not None:
            self._pool.release(self._conn)


standard_pool = _StandardPool()


async def close_pool() -> None:
    """lifespan 关停时调用。"""
    await standard_pool.close()


__all__ = ["standard_pool", "close_pool"]
