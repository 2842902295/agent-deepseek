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

                重写 aget_delta_channel_history（状态恢复时回放 DeltaChannel）。

                历史教训（两代实现的实测数据）：
                - 上游默认实现：沿 parent chain 逐个 aget_tuple（json_table + 两个
                  关联子查询 + to_base64，OB 上 ~136ms/次）。294 checkpoint ≈ 40s。
                - 第一代优化：逐 checkpoint 简单 PK 查询 + 每 channel 全量 blob。
                  但 **parent chain 仍是逐条串行查询，且对"没有任何快照的 channel
                  （如极少写入的 files）必须走到链根"** —— 长会话依旧 O(N) 次 OB
                  往返：812 checkpoint 的会话每轮对话要串行查 800+ 次（几十秒），
                  且随轮数线性恶化（表现为"token 不多却越聊越慢"）。

                本实现把 DB 往返压到常数级（约 5~7 次查询，与链长无关）：
                  0. 全 thread 无 writes 的 channel 直接返回空历史（消费端按"无
                     seed 从空重建"处理，结果等价）——纯聊天会话的 files 通道
                     由此直接跳过，不再拖全链
                  1. 一次查询取回整条 parent chain（只要 checkpoint_id /
                     parent / channel_versions，JSON_EXTRACT 裁剪掉大 JSON），
                     在内存里走链
                  2. 一次存在性查询找出各 channel 最近的快照版本（只取版本号，
                     不传 blob 字节），再只为命中的那 **一个** 版本取 blob——
                     不再加载/反序列化该 channel 的全部历史快照
                  3. 一次批量 writes 查询
                语义与 InMemorySaver.get_delta_channel_history 对齐：
                  _DeltaSnapshot 快照 → seed + 收集该 cp 自身 writes
                  plain value（旧式迁移数据）→ seed，其值已含 writes 不重复收集
                  无快照 channel → 走到根，收集全部 writes（内存走链，成本常数）
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
                    ch_ph = ",".join(["%s"] * len(channels))

                    # ── Step 0：全 thread 无 writes 的 channel 直接空重建 ──
                    # 无 seed 时消费端本就"从空开始回放 writes"；writes 也没有 →
                    # 结果必为空。纯聊天会话的 files 通道由此免走全链。
                    async with self._cursor() as cur:
                        await cur.execute(
                            f"SELECT DISTINCT channel FROM checkpoint_writes "
                            f"WHERE thread_id = %s AND checkpoint_ns_hash = UNHEX(MD5(%s)) "
                            f"AND channel IN ({ch_ph})",
                            (thread_id, checkpoint_ns, *channels),
                        )
                        channels_with_writes = {r["channel"] for r in await cur.fetchall()}

                    result: dict[str, DeltaChannelHistory] = {}
                    todo: list[str] = []
                    for ch in channels:
                        if ch in channels_with_writes:
                            todo.append(ch)
                        else:
                            result[ch] = {"writes": []}
                    if not todo:
                        logger.info(
                            f"[OBAIOMySQLSaver] delta history：thread={thread_id[:16]}… "
                            f"channels={list(channels)} 均无 writes，空重建 "
                            f"耗时={(_time.perf_counter() - _t0) * 1000:.0f}ms"
                        )
                        return result

                    # ── Step 1：一次查询取回整条链，内存走 parent chain ──
                    # uuid6 checkpoint_id 字典序即时间序；只取 channel_versions
                    #（JSON_EXTRACT 裁剪，避免传输 versions_seen 等大字段）。
                    try:
                        async with self._cursor() as cur:
                            await cur.execute(
                                "SELECT checkpoint_id, parent_checkpoint_id, "
                                "JSON_EXTRACT(checkpoint, '$.channel_versions') AS cv "
                                "FROM checkpoints "
                                "WHERE thread_id = %s AND checkpoint_ns_hash = UNHEX(MD5(%s))",
                                (thread_id, checkpoint_ns),
                            )
                            cp_rows = await cur.fetchall()
                        id_to_node: dict[str, tuple[str | None, dict[str, Any]]] = {}
                        for r in cp_rows:
                            cv = r["cv"]
                            if isinstance(cv, (bytes, bytearray)):
                                cv = cv.decode("utf-8")
                            id_to_node[r["checkpoint_id"]] = (
                                r["parent_checkpoint_id"],
                                json.loads(cv) if cv else {},
                            )
                    except Exception:
                        # JSON_EXTRACT 不可用时退回整列 checkpoint
                        async with self._cursor() as cur:
                            await cur.execute(
                                "SELECT checkpoint_id, parent_checkpoint_id, checkpoint "
                                "FROM checkpoints "
                                "WHERE thread_id = %s AND checkpoint_ns_hash = UNHEX(MD5(%s))",
                                (thread_id, checkpoint_ns),
                            )
                            cp_rows = await cur.fetchall()
                        id_to_node = {}
                        for r in cp_rows:
                            ckpt = json.loads(r["checkpoint"]) if isinstance(r["checkpoint"], str) else r["checkpoint"]
                            id_to_node[r["checkpoint_id"]] = (r["parent_checkpoint_id"], ckpt.get("channel_versions", {}))

                    # walk 起点与 base class 一致：target 的 parent；无 target 取最新
                    target_cp_id = get_checkpoint_id(config)
                    if target_cp_id:
                        cursor_id: str | None = id_to_node.get(target_cp_id, (None, {}))[0]
                    elif id_to_node:
                        latest = max(id_to_node)  # uuid6 字典序 = 时间序
                        cursor_id = id_to_node[latest][0]
                    else:
                        cursor_id = None

                    # chain: [(cp_id, channel_versions), ...]  target → root 顺序
                    chain: list[tuple[str, dict[str, Any]]] = []
                    _seen: set[str] = set()
                    while cursor_id is not None and cursor_id in id_to_node and cursor_id not in _seen:
                        _seen.add(cursor_id)
                        parent, versions = id_to_node[cursor_id]
                        chain.append((cursor_id, versions))
                        cursor_id = parent

                    if not chain:
                        for ch in todo:
                            result[ch] = {"writes": []}
                        return result

                    # ── Step 2：沿链收集候选版本 → 存在性查询定位各 channel 最近快照 ──
                    # channel 的 seed = 距 target 最近、且存有非空 blob 的那个版本。
                    # 先收集候选（walk 序、去重），一次查询只取"哪些版本有 blob"
                    # （不传 blob 字节），再按 walk 序取第一个命中的版本。
                    cand_versions: dict[str, list[str]] = {ch: [] for ch in todo}
                    _seen_v: dict[str, set[str]] = {ch: set() for ch in todo}
                    for _cp_id, versions in chain:
                        for ch in todo:
                            v = versions.get(ch)
                            if v is not None and v not in _seen_v[ch]:
                                _seen_v[ch].add(v)
                                cand_versions[ch].append(v)

                    all_cand = [v for ch in todo for v in cand_versions[ch]]
                    todo_ph = ",".join(["%s"] * len(todo))
                    has_blob: set[tuple[str, str]] = set()
                    if all_cand:
                        v_ph = ",".join(["%s"] * len(all_cand))
                        async with self._cursor() as cur:
                            await cur.execute(
                                f"SELECT channel, version FROM checkpoint_blobs "
                                f"WHERE thread_id = %s AND checkpoint_ns_hash = UNHEX(MD5(%s)) "
                                f"AND channel IN ({todo_ph}) AND version IN ({v_ph}) "
                                f"AND type <> 'empty' AND `blob` IS NOT NULL",
                                (thread_id, checkpoint_ns, *todo, *all_cand),
                            )
                            has_blob = {(r["channel"], r["version"]) for r in await cur.fetchall()}

                    seed_version: dict[str, str] = {}
                    for ch in todo:
                        for v in cand_versions[ch]:  # walk 序 = target → root
                            if (ch, v) in has_blob:
                                seed_version[ch] = v
                                break

                    # ── Step 3：只加载命中版本的 blob 值（每 channel 至多一个）──
                    seed_value: dict[str, Any] = {}
                    for ch, v in seed_version.items():
                        async with self._cursor() as cur:
                            await cur.execute(
                                "SELECT type, `blob` FROM checkpoint_blobs "
                                "WHERE thread_id = %s AND checkpoint_ns_hash = UNHEX(MD5(%s)) "
                                "AND channel = %s AND version = %s",
                                (thread_id, checkpoint_ns, ch, v),
                            )
                            brow = await cur.fetchone()
                        if brow is not None and brow["blob"] is not None:
                            seed_value[ch] = self.serde.loads_typed((brow["type"], brow["blob"]))

                    # ── Step 4：内存走链定终止点，收集需要 writes 的 checkpoint ──
                    remaining = set(todo)
                    term_at_cp: dict[str, dict[str, Any]] = {}
                    inspect_cps: list[str] = []  # target → root 顺序
                    for cp_id, versions in chain:
                        if not remaining:
                            break
                        inspect_cps.append(cp_id)
                        terminated_here: dict[str, Any] = {}
                        for ch in list(remaining):
                            v = versions.get(ch)
                            if v is not None and seed_version.get(ch) == v and ch in seed_value:
                                terminated_here[ch] = seed_value[ch]
                        if terminated_here:
                            term_at_cp[cp_id] = terminated_here
                            for ch in terminated_here:
                                remaining.discard(ch)
                    # remaining 非空 = 存量旧数据里"无快照"的 channel：链已走到根，
                    # 其全部 writes 都在 inspect_cps 内，回放语义不变。

                    # ── Step 5：批量加载 writes（1 次查询）──
                    # 排序对齐 InMemorySaver：cp 间 DESC（最后整体反转回 oldest→newest），
                    # cp 内 (task_id, idx) DESC 后反转 = 升序。
                    collected_by_ch: dict[str, list[tuple[str, str, Any]]] = {c: [] for c in todo}
                    if inspect_cps:
                        cp_ph = ",".join(["%s"] * len(inspect_cps))
                        async with self._cursor() as cur:
                            await cur.execute(
                                f"SELECT checkpoint_id, task_id, channel, type, `blob` "
                                f"FROM checkpoint_writes "
                                f"WHERE thread_id = %s AND checkpoint_ns_hash = UNHEX(MD5(%s)) "
                                f"AND checkpoint_id IN ({cp_ph}) AND channel IN ({todo_ph}) "
                                f"ORDER BY checkpoint_id DESC, task_id DESC, idx DESC",
                                (thread_id, checkpoint_ns, *inspect_cps, *todo),
                            )
                            wrows = await cur.fetchall()

                        writes_by_cp: dict[str, list[dict]] = {}
                        for wrow in wrows:  # 查询序：cp DESC、cp 内 (task_id, idx) DESC
                            writes_by_cp.setdefault(wrow["checkpoint_id"], []).append(wrow)

                        remaining = set(todo)
                        for cp_id in inspect_cps:
                            if not remaining:
                                break
                            terminated_here = term_at_cp.get(cp_id, {})
                            # 注意：按查询序（cp 间 DESC、cp 内 (task_id,idx) DESC）追加，
                            # 与 InMemorySaver 一致——末尾整体 reversed 后才同时得到
                            # "cp 间 oldest→newest、cp 内 (task_id,idx) 升序"。
                            # 若此处先 reversed，末尾再反转会把 cp 内顺序倒回去。
                            for wrow in writes_by_cp.get(cp_id, []):
                                ch = wrow["channel"]
                                if ch not in remaining:
                                    continue
                                tv = terminated_here.get(ch)
                                if tv is not None and not isinstance(tv, _DeltaSnapshot):
                                    continue  # plain value 已含本 cp 的 writes
                                collected_by_ch[ch].append(
                                    (wrow["task_id"], ch, self.serde.loads_typed((wrow["type"], wrow["blob"])))
                                )
                            for ch in terminated_here:
                                remaining.discard(ch)

                    # ── Step 6：组装结果（writes oldest→newest）──
                    for ch in todo:
                        entry: DeltaChannelHistory = {"writes": list(reversed(collected_by_ch[ch]))}  # type: ignore[typeddict-item]
                        if ch in seed_value:
                            entry["seed"] = seed_value[ch]
                        result[ch] = entry
                    logger.info(
                        f"[OBAIOMySQLSaver] delta history：thread={thread_id[:16]}… "
                        f"链长={len(chain)} 回放cp={len(inspect_cps)} "
                        f"seeds={{{', '.join(f'{c}:{seed_version[c][:8]}' for c in todo if c in seed_value) or '-'}}} "
                        f"writes={{{', '.join(f'{c}:{len(collected_by_ch[c])}' for c in todo)}}} "
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
