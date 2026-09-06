"""临时探针：检查目标 OB 库已存在的系统表"""
import asyncio
import os
from pathlib import Path

import aiomysql
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
v = dotenv_values(ROOT / os.getenv("ENV_FILE", ".env.test"))


async def go():
    pool = await aiomysql.create_pool(
        host=v["STANDARD_MYSQL_HOST"],
        port=int(v["STANDARD_MYSQL_PORT"]),
        user=v["STANDARD_MYSQL_USER"],
        password=v["STANDARD_MYSQL_PASSWORD"],
        db=v["STANDARD_MYSQL_DB"],
        charset="utf8mb4",
        autocommit=True,
        minsize=1,
        maxsize=2,
    )
    async with pool.acquire() as c:
        async with c.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME"
            )
            tables = [r["TABLE_NAME"] for r in await cur.fetchall()]
            target = {
                "users", "roles", "menus", "apis", "buttons", "logs", "api_logs",
                "users_roles", "roles_menus", "roles_apis", "roles_buttons", "menus_buttons",
            }
            print(f"DB has {len(tables)} tables total")
            existed = sorted(target & set(tables))
            missing = sorted(target - set(tables))
            print(f"system tables EXISTED ({len(existed)}): {existed}")
            print(f"system tables MISSING ({len(missing)}): {missing}")
            for t in existed:
                await cur.execute(f"SELECT COUNT(*) AS c FROM `{t}`")
                cnt = (await cur.fetchone())["c"]
                print(f"  {t}: {cnt} rows")
    pool.close()
    await pool.wait_closed()


asyncio.run(go())
