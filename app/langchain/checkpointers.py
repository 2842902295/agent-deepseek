"""
LangGraph checkpointer 工厂（OceanBase / MySQL 兼容）

历史：原本走 langgraph-checkpoint-sqlite + .agent_workspace/<id>/checkpoints.sqlite，
现统一切到 langgraph-checkpoint-mysql 的 AIOMySQLSaver，所有对话状态都进 OB 的
fast_main 库（4 张表：checkpoints / checkpoint_blobs / checkpoint_writes /
checkpoint_migrations，由 saver.setup() 自动建/升级）。

设计：
- 全进程单例。首次访问时 lazy 创建 aiomysql.Pool，构造 AIOMySQLSaver 并执行 setup()。
- 复用 STANDARD_MYSQL_* 配置，与 standard_pool 同实例同库（独立连接池避免互相阻塞）。
- 出错降级到 MemorySaver，保证业务不中断（仅会话状态丢失）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any, Optional

import aiomysql
from loguru import logger

from app.settings.config import settings

_lock = asyncio.Lock()
_saver: Optional[object] = None
_pool: Optional[aiomysql.Pool] = None


def _patch_runtime_sql_for_oceanbase() -> None:
    """
    OB MySQL 模式在 `json_array(<BLOB>)` 时不会像 MySQL 8.x 那样自动 base64 编码 BLOB，
    会把原始二进制塞进 JSON 字符串里，导致客户端 utf-8 解码炸掉。

    上游通过 `mysql_mariadb_branch(mysql_frag, mariadb_frag)` 用版本注释做分支：
        /*!50700 mysql */ /*M! mariadb */
    OB 与 MySQL 一样会"识别"`/*!50700 ...*/` 这段（取 mysql_frag），但行为却更接近
    MariaDB（不自动 base64 BLOB）。所以这里覆写 utils.mysql_mariadb_branch，让它
    在 OB 上始终选 MariaDB 分支（显式 to_base64 + VALUE() 语法），从根上避开
    BLOB 编码 / ON DUPLICATE KEY UPDATE 的两类不兼容。

    必须在 base.py 中以 module-level f-string 已经评估过的 SQL 之前调用过——但
    base 模块的 SELECT_SQL / UPSERT_*_SQL 在 import 时就已经用旧分支拼好了，
    所以仅 patch 函数还不够，要把已生成的 SQL 字符串也替换。
    """
    from langgraph.checkpoint.mysql import base as _base
    from langgraph.checkpoint.mysql import utils as _utils

    def _force_mariadb_branch(mysql_fragment: str, mariadb_fragment: str) -> str:
        return mariadb_fragment

    _utils.mysql_mariadb_branch = _force_mariadb_branch
    _base.mysql_mariadb_branch = _force_mariadb_branch  # type: ignore[attr-defined]

    # 替换 import 时已用旧分支拼好的常量 SQL：
    # 把 /*!50700 X*//*M! Y*/ 整段替换为 Y
    import re
    branch_re = re.compile(r"/\*!\d+\s*([^*]*)\*/\s*/\*M!\s*([^*]*)\*/", re.DOTALL)

    patched_attrs: list[str] = []
    for attr in (
        "SELECT_SQL",
        "SELECT_PENDING_SENDS_SQL",
        "UPSERT_CHECKPOINT_BLOBS_SQL",
        "UPSERT_CHECKPOINTS_SQL",
        "UPSERT_CHECKPOINT_WRITES_SQL",
        "INSERT_CHECKPOINT_WRITES_SQL",
    ):
        if not hasattr(_base, attr):
            continue
        old = getattr(_base, attr)
        if not isinstance(old, str):
            continue
        new = branch_re.sub(lambda m: m.group(2), old)
        if new != old:
            setattr(_base, attr, new)
            patched_attrs.append(attr)

    logger.info(
        f"[checkpointer] OB 运行时 SQL patch：强制走 MariaDB 分支；"
        f"已替换常量 {patched_attrs}"
    )


def _patch_migrations_for_oceanbase() -> None:
    """
    针对 OceanBase MySQL 模式做兼容性补丁。直接改写 langgraph-checkpoint-mysql
    的模块级 MIGRATIONS 列表，处理三类不兼容：

    1) JSON 列默认值：`metadata JSON NOT NULL DEFAULT ('{}')`
       —— OB 不支持给 JSON/TEXT/BLOB 设默认表达式。去掉默认值，运行时 INSERT 都显式带值。

    2) 单条 ALTER 同时含多个 PK 操作（DROP PK + ADD PK [+ MODIFY]）
       —— OB 报 1235 "Multiple complex DDLs about primary in single stmt not supported"。
       拆成多条独立 ALTER。

    3) 含 STORED 生成列 + 版本注释（/*!50700 ... STORED,*/ /*M! ... ,*/）的 ALTER
       —— OB 对版本注释解析有差异，且 STORED 生成列在 OB 上不稳定。
       由于上游后面的迁移会把 checkpoint_ns_hash 改成普通 BINARY，这里直接一步到位
       建成普通 BINARY(16)，跳过生成列阶段；后续 MODIFY 那条会变成 no-op。

    saver.setup() 按 MIGRATIONS 顺序逐条 execute；fresh 库场景上述改写都是等价的。
    """
    import re

    from langgraph.checkpoint.mysql import base as _mig_mod

    bad_default = "metadata JSON NOT NULL DEFAULT ('{}')"
    good_default = "metadata JSON NOT NULL"

    # 已知带 STORED + 版本注释的三条迁移：用 checkpoint_ns_hash 关键字定位
    # 用正则提取目标表名 + 新 PK 列表，重写为 OB 友好序列
    HASH_BLOCK_RE = re.compile(
        r"ALTER\s+TABLE\s+(?P<table>\w+)\s+"
        r"/\*!\d+\s+ADD\s+COLUMN\s+checkpoint_ns_hash[^*]*STORED,?\s*\*/"
        r"\s*/\*M!\s+ADD\s+COLUMN\s+checkpoint_ns_hash[^*]*\*/"
        r"\s*DROP\s+PRIMARY\s+KEY\s*,\s*"
        r"ADD\s+PRIMARY\s+KEY\s*\((?P<pk>[^)]+)\)\s*;?",
        re.IGNORECASE | re.DOTALL,
    )

    new_migrations: list = []
    json_default_patched = 0
    pk_alter_split = 0
    hash_block_rewritten = 0

    for sql in _mig_mod.MIGRATIONS:
        if not isinstance(sql, str):
            new_migrations.append(sql)
            continue

        if bad_default in sql:
            sql = sql.replace(bad_default, good_default)
            json_default_patched += 1

        # 处理含 STORED + 版本注释的 hash 列迁移：整段重写
        m = HASH_BLOCK_RE.search(sql)
        if m:
            table = m.group("table")
            pk_cols = m.group("pk").strip()
            new_migrations.extend([
                f"ALTER TABLE {table} ADD COLUMN checkpoint_ns_hash BINARY(16);",
                f"ALTER TABLE {table} DROP PRIMARY KEY;",
                f"ALTER TABLE {table} ADD PRIMARY KEY ({pk_cols});",
            ])
            hash_block_rewritten += 1
            continue

        # 简单的 DROP PK + ADD PK 复合 ALTER：按顶层逗号拆条（适配 12/13/14 这类）
        normalized = re.sub(r"\s+", " ", sql.strip(), flags=re.IGNORECASE).upper()
        if (
            normalized.startswith("ALTER TABLE")
            and "DROP PRIMARY KEY" in normalized
            and "ADD PRIMARY KEY" in normalized
        ):
            split_stmts = _split_alter_top_level(sql)
            if len(split_stmts) > 1:
                new_migrations.extend(split_stmts)
                pk_alter_split += 1
                continue

        new_migrations.append(sql)

    _mig_mod.MIGRATIONS[:] = new_migrations
    logger.info(
        f"[checkpointer] OB 兼容性 patch 完成："
        f"去 JSON 默认值 {json_default_patched} 条，"
        f"重写 STORED hash 块 {hash_block_rewritten} 条，"
        f"拆复合 ALTER {pk_alter_split} 条；总迁移 {len(new_migrations)} 条"
    )


def _split_alter_top_level(sql: str) -> list[str]:
    """
    `ALTER TABLE t op1, op2, ...;` 按顶层逗号拆成多条独立 ALTER。
    会跳过 (...) 与 /* ... */ 注释中的逗号。
    """
    import re
    m = re.match(r"\s*ALTER\s+TABLE\s+(\S+)\s+(.+?);?\s*$", sql, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return [sql]
    table = m.group(1)
    body = m.group(2)

    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    while i < len(body):
        ch = body[i]
        # 跳过 /* ... */ 整段
        if ch == "/" and i + 1 < len(body) and body[i + 1] == "*":
            end = body.find("*/", i + 2)
            if end == -1:
                buf.append(body[i:])
                break
            buf.append(body[i:end + 2])
            i = end + 2
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            piece = "".join(buf).strip()
            if piece:
                parts.append(piece)
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)

    if len(parts) <= 1:
        return [sql]
    return [f"ALTER TABLE {table} {p};" for p in parts]


async def get_async_checkpointer():
    """获取全局 AIOMySQLSaver 实例（首次调用会建池 + setup 建表）。失败时返回 MemorySaver 兜底。"""
    global _saver, _pool
    if _saver is not None:
        return _saver

    async with _lock:
        if _saver is not None:
            return _saver

        try:
            from langchain_core.runnables import RunnableConfig
            from langgraph.checkpoint.base import (
                DeltaChannelHistory,
                get_checkpoint_id,
            )
            from langgraph.checkpoint.mysql.aio import AIOMySQLSaver
            from langgraph.checkpoint.serde.types import _DeltaSnapshot

            _patch_runtime_sql_for_oceanbase()
            _patch_migrations_for_oceanbase()

            class OBAIOMySQLSaver(AIOMySQLSaver):  # type: ignore[misc]
                """AIOMySQLSaver 的 OceanBase 优化子类。

                重写 aget_delta_channel_history，将默认的"N 次串行 aget_tuple"
                （每次含 json_table + 两个关联子查询 + to_base64，OB 上 ~136ms/次）
                替换为：
                  1. N 次简单 PK 查询走 parent chain（~5ms/次）
                  2. 每 channel 1 次批量 blob 查询
                  3. 1 次批量 writes 查询
                294 个 checkpoint 的场景：~40s → ~1.5s。
                """

                async def aget_delta_channel_history(  # type: ignore[override]
                    self,
                    *,
                    config: RunnableConfig,
                    channels: Sequence[str],
                ) -> Mapping[str, DeltaChannelHistory]:
                    if not channels:
                        return {}

                    import time as _time
                    _t0 = _time.perf_counter()

                    thread_id: str = config["configurable"]["thread_id"]
                    checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")

                    # ── Step 1：走 parent chain（简单 PK 查询，不走 json_table/子查询）──
                    # 与 base class 行为一致：walk 从 target 的 parent 开始
                    target_cp_id = get_checkpoint_id(config)
                    if target_cp_id:
                        async with self._cursor() as cur:
                            await cur.execute(
                                "SELECT parent_checkpoint_id FROM checkpoints "
                                "WHERE thread_id = %s AND checkpoint_ns_hash = UNHEX(MD5(%s)) "
                                "AND checkpoint_id = %s",
                                (thread_id, checkpoint_ns, target_cp_id),
                            )
                            row = await cur.fetchone()
                        cursor_id: str | None = row["parent_checkpoint_id"] if row else None
                    else:
                        # 无 checkpoint_id：取最新 checkpoint，从其 parent 开始走
                        async with self._cursor() as cur:
                            await cur.execute(
                                "SELECT parent_checkpoint_id FROM checkpoints "
                                "WHERE thread_id = %s AND checkpoint_ns_hash = UNHEX(MD5(%s)) "
                                "ORDER BY checkpoint_id DESC LIMIT 1",
                                (thread_id, checkpoint_ns),
                            )
                            row = await cur.fetchone()
                        cursor_id = row["parent_checkpoint_id"] if row else None

                    # chain: [(cp_id, channel_versions_dict), ...]  parent → root 顺序
                    chain: list[tuple[str, dict[str, Any]]] = []
                    while cursor_id is not None:
                        async with self._cursor() as cur:
                            await cur.execute(
                                "SELECT checkpoint_id, parent_checkpoint_id, checkpoint "
                                "FROM checkpoints "
                                "WHERE thread_id = %s AND checkpoint_ns_hash = UNHEX(MD5(%s)) "
                                "AND checkpoint_id = %s",
                                (thread_id, checkpoint_ns, cursor_id),
                            )
                            row = await cur.fetchone()
                        if row is None:
                            break
                        ckpt: dict = json.loads(row["checkpoint"])
                        chain.append((row["checkpoint_id"], ckpt.get("channel_versions", {})))
                        cursor_id = row["parent_checkpoint_id"]

                    if not chain:
                        return {ch: {"writes": []} for ch in channels}

                    # ── Step 2：批量加载 blobs（每 channel 1 次查询）──
                    # 建立 {(channel, version): deserialized_value} 映射
                    blob_vals: dict[tuple[str, str], Any] = {}
                    for ch in channels:
                        async with self._cursor() as cur:
                            await cur.execute(
                                "SELECT version, type, `blob` FROM checkpoint_blobs "
                                "WHERE thread_id = %s AND checkpoint_ns_hash = UNHEX(MD5(%s)) "
                                "AND channel = %s",
                                (thread_id, checkpoint_ns, ch),
                            )
                            for brow in await cur.fetchall():
                                if brow["type"] != "empty" and brow["blob"] is not None:
                                    blob_vals[(ch, brow["version"])] = self.serde.loads_typed(
                                        (brow["type"], brow["blob"])
                                    )

                    # ── Step 3：走 chain 找 seed + 收集需要加载 writes 的 cp_ids ──
                    # 逻辑与 InMemorySaver.get_delta_channel_history 完全一致：
                    #   _DeltaSnapshot blob → 记录 seed，收集该 cp 的 writes
                    #   plain value blob   → 记录 seed，不收集该 cp 的 writes（值已含 writes）
                    #   无 blob            → 继续向上，收集该 cp 的 writes
                    collected_by_ch: dict[str, list[tuple[str, str, Any]]] = {c: [] for c in channels}
                    seed_by_ch: dict[str, Any] = {}
                    remaining: set[str] = set(channels)
                    write_cp_ids: list[str] = []  # 保持 parent→root 顺序，去重

                    for cp_id, versions in chain:
                        if not remaining:
                            break
                        # 找该 checkpoint 的非空 blob
                        terminated_here: dict[str, Any] = {}
                        for ch in remaining:
                            ver = versions.get(ch)
                            if ver is not None:
                                blob_val = blob_vals.get((ch, ver))
                                if blob_val is not None:
                                    terminated_here[ch] = blob_val
                        # 收集 writes（plain value 跳过）
                        for ch in remaining:
                            blob_val = terminated_here.get(ch)
                            if blob_val is not None and not isinstance(blob_val, _DeltaSnapshot):
                                continue  # plain value 已含 writes，不重复收集
                            write_cp_ids.append(cp_id)
                            break  # 同一 cp_id 只需记录一次
                        # 记录 seed，停止该 channel 的遍历
                        for ch, blob_val in terminated_here.items():
                            seed_by_ch[ch] = blob_val
                            remaining.discard(ch)

                    # ── Step 4：批量加载 writes（1 次查询）──
                    if write_cp_ids:
                        placeholders = ",".join(["%s"] * len(write_cp_ids))
                        async with self._cursor() as cur:
                            await cur.execute(
                                f"SELECT checkpoint_id, task_id, channel, type, `blob`, idx "
                                f"FROM checkpoint_writes "
                                f"WHERE thread_id = %s AND checkpoint_ns_hash = UNHEX(MD5(%s)) "
                                f"AND checkpoint_id IN ({placeholders}) "
                                f"ORDER BY checkpoint_id DESC, idx DESC",
                                (thread_id, checkpoint_ns, *write_cp_ids),
                            )
                            for wrow in await cur.fetchall():
                                ch = wrow["channel"]
                                if ch in collected_by_ch:
                                    collected_by_ch[ch].append(
                                        (wrow["task_id"], ch, self.serde.loads_typed((wrow["type"], wrow["blob"])))
                                    )

                    # ── Step 5：组装结果（writes oldest→newest）──
                    result: dict[str, DeltaChannelHistory] = {}
                    for ch in channels:
                        entry: DeltaChannelHistory = {"writes": list(reversed(collected_by_ch[ch]))}  # type: ignore[typeddict-item]
                        if ch in seed_by_ch:
                            entry["seed"] = seed_by_ch[ch]
                        result[ch] = entry
                    logger.info(
                        f"[OBAIOMySQLSaver] aget_delta_channel_history 完成："
                        f"thread={thread_id[:16]}… chain={len(chain)} "
                        f"writes_cps={len(write_cp_ids)} "
                        f"耗时={(_time.perf_counter() - _t0) * 1000:.0f}ms"
                    )
                    return result

            _pool = await aiomysql.create_pool(
                host=settings.STANDARD_MYSQL_HOST,
                port=settings.STANDARD_MYSQL_PORT,
                user=settings.STANDARD_MYSQL_USER,
                password=settings.STANDARD_MYSQL_PASSWORD,
                db=settings.STANDARD_MYSQL_DB,
                charset="utf8mb4",
                autocommit=True,
                minsize=1,
                maxsize=10,
                pool_recycle=3600,
            )
            saver = OBAIOMySQLSaver(_pool)  # type: ignore[arg-type]
            await saver.setup()
            _saver = saver
            logger.info(
                f"[checkpointer] OBAIOMySQLSaver 已就绪 → "
                f"{settings.STANDARD_MYSQL_USER}@{settings.STANDARD_MYSQL_HOST}:"
                f"{settings.STANDARD_MYSQL_PORT}/{settings.STANDARD_MYSQL_DB}"
            )
        except Exception as e:
            from langgraph.checkpoint.memory import MemorySaver
            # 降级会让重启后丢失对话上下文，必须显眼报错而不是静默吞掉
            logger.exception(
                f"[checkpointer] OB checkpointer 初始化失败！降级到 MemorySaver "
                f"（重启后多轮对话上下文将丢失）。原因：{e}"
            )
            _saver = MemorySaver()

        return _saver


async def close_checkpointer() -> None:
    """lifespan 退出时调用，释放 saver 持有的连接池。"""
    global _saver, _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
    _saver = None


__all__ = ["get_async_checkpointer", "close_checkpointer"]
