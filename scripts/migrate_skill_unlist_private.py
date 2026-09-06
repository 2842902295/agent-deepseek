"""
一次性数据迁移：把存量 private/role 上架技能归一为「默认未上架」口径。

背景：2026-08-19 连接器改造移除了技能 visibility（可见范围）机制，共享口径统一为
is_enabled（上架=全员可见）。存量的 private/role 技能若仍挂着 is_enabled=1，
去掉 visibility 过滤后会瞬间全员可见——必须先把它们下架。

幂等：条件本身带 is_enabled=1，重复执行影响行数为 0。

用法（内网库 + 公网库都要执行一次）：
    python scripts/migrate_skill_unlist_private.py --env-file .env          # dry-run 看数量
    python scripts/migrate_skill_unlist_private.py --env-file .env --yes    # 执行
    # 公网库（env 文件里被注释掉了）显式传连接：
    python scripts/migrate_skill_unlist_private.py --host <公网host> --port <公网port> \
        --user <user> --password <pwd> --db fast_main --yes

⚠️ 必须先于「去 visibility」的新代码部署执行，否则旧私有技能会泄漏进商店。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent

WHERE = "is_enabled=1 AND visibility IN ('private','role') AND source<>'builtin'"
SQL_COUNT = f"SELECT COUNT(*) FROM agent_skill WHERE {WHERE}"
SQL_UPDATE = f"UPDATE agent_skill SET is_enabled=0 WHERE {WHERE}"


def main() -> int:
    ap = argparse.ArgumentParser(description="存量 private/role 上架技能下架归一（幂等）")
    ap.add_argument("--env-file", default=".env", help="读取 STANDARD_MYSQL_* 的 env 文件（相对项目根）")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--db")
    ap.add_argument("--yes", action="store_true", help="实际执行（默认 dry-run 只看数量）")
    args = ap.parse_args()

    env = dotenv_values(ROOT / args.env_file)
    host = args.host or env.get("STANDARD_MYSQL_HOST")
    port = args.port or int(env.get("STANDARD_MYSQL_PORT") or 3306)
    user = args.user or env.get("STANDARD_MYSQL_USER")
    password = args.password if args.password is not None else env.get("STANDARD_MYSQL_PASSWORD")
    db = args.db or env.get("STANDARD_MYSQL_DB")
    if not host or not user or not db:
        print("缺少连接信息：请用 --env-file 或 --host/--user/--password/--db 指定", file=sys.stderr)
        return 2

    conn = pymysql.connect(
        host=host, port=port, user=user, password=password, database=db,
        charset="utf8mb4", connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_COUNT)
            n = int(cur.fetchone()[0])
            print(f"目标库 {host}:{port}/{db}：{n} 个上架中的 private/role 技能将被下架")
            if not args.yes:
                print("dry-run 模式，未执行。确认无误后加 --yes 落库。")
                return 0
            if n == 0:
                print("无需迁移（幂等）。")
                return 0
            cur.execute(SQL_UPDATE)
            conn.commit()
            print(f"完成：已下架 {cur.rowcount} 个技能。")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
