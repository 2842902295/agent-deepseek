"""
将 app_system.sqlite3 中的系统管理数据迁移到 OceanBase（MySQL 兼容）。

通用化要点：
- SQLite 路径 / 目标 env file 通过 CLI 参数指定，不写死
- 默认 dry-run（只对账不写入），需显式加 --yes 才执行
- 写入完成后做逐表 COUNT 对账
- 支持 --truncate（默认）或 --keep-target（基于 PK 跳过已存在行，便于增量补数据）

典型用法：
    # 测试库（先看一下差异，不写入）
    .venv/bin/python scripts/migrate_system_to_ob.py --env-file .env.test

    # 测试库正式迁移（清空目标后灌入）
    .venv/bin/python scripts/migrate_system_to_ob.py --env-file .env.test --yes

    # 生产库迁移
    .venv/bin/python scripts/migrate_system_to_ob.py --env-file .env.prod --yes

    # 增量灌入（不清空目标）
    .venv/bin/python scripts/migrate_system_to_ob.py --env-file .env.prod --keep-target --yes

前置：目标 OB 库必须已存在 12 张系统表（先正常启动一次应用让 Tortoise 建表）。
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

import aiomysql
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent

# 灌库顺序：先无外键 → 再 menus → roles → users → m2m → 日志
ORDERED_TABLES: list[str] = [
    "buttons",
    "apis",
    "menus",
    "roles",
    "users",
    "users_roles",
    "roles_menus",
    "roles_apis",
    "roles_buttons",
    "menus_buttons",
    "api_logs",
    "logs",
]

# 每张表的 "唯一识别键"：增量模式用它判断目标是否已存在该行
PK_COLUMNS: dict[str, list[str]] = {
    "buttons": ["id"],
    "apis": ["id"],
    "menus": ["id"],
    "roles": ["id"],
    "users": ["id"],
    "logs": ["id"],
    "api_logs": ["id"],
    "users_roles": ["users_id", "role_id"],
    "roles_menus": ["roles_id", "menu_id"],
    "roles_apis": ["roles_id", "api_id"],
    "roles_buttons": ["roles_id", "button_id"],
    "menus_buttons": ["menus_id", "button_id"],
}

AUTO_INCREMENT_TABLES = ("users", "roles", "menus", "apis", "buttons", "logs", "api_logs")


# ── 读端 ──────────────────────────────────────────────────────────────────────

def fetch_all(conn: sqlite3.Connection, table: str) -> list[dict]:
    cur = conn.execute(f'SELECT * FROM "{table}"')
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def load_snapshots(sqlite_path: Path) -> dict[str, list[dict]]:
    sconn = sqlite3.connect(str(sqlite_path))
    sconn.row_factory = sqlite3.Row
    snapshots: dict[str, list[dict]] = {}
    try:
        for t in ORDERED_TABLES:
            snapshots[t] = fetch_all(sconn, t)
    finally:
        sconn.close()
    return snapshots


# ── 写端 ──────────────────────────────────────────────────────────────────────

async def count_target(pool: aiomysql.Pool) -> dict[str, int]:
    out: dict[str, int] = {}
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for t in ORDERED_TABLES:
                await cur.execute(f"SELECT COUNT(*) FROM `{t}`")
                (cnt,) = await cur.fetchone()
                out[t] = cnt
    return out


async def fetch_existing_keys(pool: aiomysql.Pool, table: str) -> set[tuple]:
    keys = PK_COLUMNS[table]
    col_sql = ",".join(f"`{k}`" for k in keys)
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT {col_sql} FROM `{table}`")
            rows = await cur.fetchall()
    return {tuple(r) for r in rows}


async def truncate_target(pool: aiomysql.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SET FOREIGN_KEY_CHECKS = 0;")
            try:
                for t in reversed(ORDERED_TABLES):
                    await cur.execute(f"TRUNCATE TABLE `{t}`;")
                    print(f"  truncated {t}")
            finally:
                await cur.execute("SET FOREIGN_KEY_CHECKS = 1;")
        await conn.commit()


async def insert_rows(pool: aiomysql.Pool, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    col_sql = ",".join(f"`{c}`" for c in cols)
    placeholders = ",".join(["%s"] * len(cols))
    sql = f"INSERT INTO `{table}` ({col_sql}) VALUES ({placeholders})"

    BATCH = 500
    total = 0
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for i in range(0, len(rows), BATCH):
                chunk = rows[i : i + BATCH]
                params = [tuple(r.get(c) for c in cols) for r in chunk]
                await cur.executemany(sql, params)
                total += len(chunk)
                print(f"    {table}: {total}/{len(rows)}")
        await conn.commit()
    return total


async def fix_menu_self_ref(pool: aiomysql.Pool, menu_rows: list[dict]) -> None:
    pending = [(r["id"], r["active_menu_id"]) for r in menu_rows if r.get("active_menu_id")]
    if not pending:
        return
    print(f"  fixing {len(pending)} menus.active_menu_id ...")
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "UPDATE `menus` SET `active_menu_id`=%s WHERE `id`=%s",
                [(ref, mid) for mid, ref in pending],
            )
        await conn.commit()


async def reset_auto_increment(pool: aiomysql.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for t in AUTO_INCREMENT_TABLES:
                await cur.execute(f"SELECT COALESCE(MAX(id),0) FROM `{t}`")
                (max_id,) = await cur.fetchone()
                await cur.execute(f"ALTER TABLE `{t}` AUTO_INCREMENT = {max_id + 1}")
                print(f"  {t}: AUTO_INCREMENT -> {max_id + 1}")
        await conn.commit()


# ── 编排 ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--sqlite", type=Path, default=ROOT / "app_system.sqlite3",
        help="SQLite 源文件路径（默认 ./app_system.sqlite3）",
    )
    p.add_argument(
        "--env-file", type=Path, default=ROOT / ".env.test",
        help="目标环境的 env 文件，从中读 STANDARD_MYSQL_*（默认 .env.test）",
    )
    p.add_argument(
        "--keep-target", action="store_true",
        help="不 TRUNCATE 目标表，按 PK 跳过已存在行（增量模式）",
    )
    p.add_argument(
        "--yes", action="store_true",
        help="确认执行写入。不加这个参数则只 dry-run，打印对账信息。",
    )
    return p.parse_args()


def load_db_config(env_file: Path) -> dict:
    if not env_file.exists():
        print(f"ERROR: env 文件不存在: {env_file}")
        sys.exit(1)
    v = dotenv_values(env_file)
    required = ["STANDARD_MYSQL_HOST", "STANDARD_MYSQL_PORT", "STANDARD_MYSQL_USER", "STANDARD_MYSQL_DB"]
    missing = [k for k in required if not v.get(k)]
    if missing:
        print(f"ERROR: {env_file} 缺少配置: {missing}")
        sys.exit(1)
    return {
        "host": v["STANDARD_MYSQL_HOST"],
        "port": int(v["STANDARD_MYSQL_PORT"]),
        "user": v["STANDARD_MYSQL_USER"],
        "password": v.get("STANDARD_MYSQL_PASSWORD", "") or "",
        "db": v["STANDARD_MYSQL_DB"],
    }


async def main() -> None:
    args = parse_args()

    if not args.sqlite.exists():
        print(f"ERROR: SQLite 源文件不存在: {args.sqlite}")
        sys.exit(1)

    cfg = load_db_config(args.env_file)

    print("=" * 60)
    print(f"源 SQLite : {args.sqlite}")
    print(f"目标 OB   : {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['db']}")
    print(f"环境文件  : {args.env_file}")
    print(f"模式      : {'TRUNCATE+INSERT' if not args.keep_target else 'KEEP+INCREMENT'}")
    print(f"执行      : {'WRITE' if args.yes else 'DRY-RUN'}")
    print("=" * 60)

    print("\n读取 SQLite 快照...")
    snapshots = load_snapshots(args.sqlite)
    for t in ORDERED_TABLES:
        print(f"  {t:>16}: {len(snapshots[t]):>6} rows")

    pool = await aiomysql.create_pool(
        host=cfg["host"], port=cfg["port"],
        user=cfg["user"], password=cfg["password"],
        db=cfg["db"], charset="utf8mb4",
        autocommit=False, minsize=1, maxsize=4,
    )

    try:
        print("\n目标库当前行数：")
        before = await count_target(pool)
        for t in ORDERED_TABLES:
            print(f"  {t:>16}: {before[t]:>6} rows")

        if not args.yes:
            print("\n[DRY-RUN] 未执行写入。加 --yes 确认后再跑一次。")
            return

        if not args.keep_target:
            print("\n清空目标库系统管理表 ...")
            await truncate_target(pool)
            rows_to_write = snapshots
        else:
            print("\n增量模式：拉取目标库已存在的 PK ...")
            rows_to_write = {}
            for t in ORDERED_TABLES:
                existing = await fetch_existing_keys(pool, t)
                keys = PK_COLUMNS[t]
                kept = [r for r in snapshots[t] if tuple(r.get(k) for k in keys) not in existing]
                rows_to_write[t] = kept
                print(f"  {t:>16}: 源 {len(snapshots[t])} - 已有 {len(existing)} = 待写 {len(kept)}")

        # menus 自引用：第一遍灌入时把 active_menu_id 全置 NULL
        if rows_to_write.get("menus"):
            menu_rows_for_insert = [{**r, "active_menu_id": None} for r in rows_to_write["menus"]]
        else:
            menu_rows_for_insert = []

        print("\n按依赖顺序写入目标库 ...")
        for t in ORDERED_TABLES:
            rows = menu_rows_for_insert if t == "menus" else rows_to_write[t]
            print(f"  {t} ({len(rows)} rows)")
            await insert_rows(pool, t, rows)

        # 回填 active_menu_id：用源数据中的所有 menu 行（不只是新增的，因为引用可能跨批次）
        all_menu_rows_with_ref = [r for r in snapshots["menus"] if r.get("active_menu_id")]
        if all_menu_rows_with_ref:
            print("\n回填 menus.active_menu_id 自引用 ...")
            await fix_menu_self_ref(pool, snapshots["menus"])

        print("\n重置自增 AUTO_INCREMENT ...")
        await reset_auto_increment(pool)

        print("\n对账：")
        after = await count_target(pool)
        ok = True
        for t in ORDERED_TABLES:
            src = len(snapshots[t])
            dst = after[t]
            mark = "✓" if (args.keep_target or src == dst) else "✗"
            if not args.keep_target and src != dst:
                ok = False
            print(f"  {mark} {t:>16}: 源 {src:>6} → 目标 {dst:>6}")

        print("\n✅ 数据迁移完成" if ok else "\n⚠️  对账存在差异，请检查上面行数")

    finally:
        pool.close()
        await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
