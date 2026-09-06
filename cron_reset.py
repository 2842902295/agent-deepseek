import time

from app import refresh_api_list
from app.core.exceptions import SettingNotFound
from app.core.init_app import init_menus, init_users

try:
    from app.settings import APP_SETTINGS
except ImportError:
    raise SettingNotFound("Can not import settings")

from tortoise import Tortoise, run_async
from loguru import logger


async def init():
    await Tortoise.init(
        config=APP_SETTINGS.TORTOISE_ORM,
    )
    await Tortoise.generate_schemas()

    conn = Tortoise.get_connection("conn_standard")

    # 收集 app_system 应用下所有模型对应的表（含 m2m through 表），仅清这些表
    from tortoise import Tortoise as _T
    system_table_names: set[str] = set()
    for model in _T.apps.get("app_system", {}).values():
        meta = model._meta
        system_table_names.add(meta.db_table)
        for m2m in meta.m2m_fields:
            field = meta.fields_map[m2m]
            through = getattr(field, "through", None)
            if through:
                system_table_names.add(through)

    await conn.execute_query("SET FOREIGN_KEY_CHECKS = 0;")
    try:
        for table_name in system_table_names:
            print("table_name", table_name)
            await conn.execute_query(f"TRUNCATE TABLE `{table_name}`;")
    finally:
        await conn.execute_query("SET FOREIGN_KEY_CHECKS = 1;")

    await init_menus()
    await refresh_api_list()
    await init_users()

    await Tortoise.close_connections()


while True:
    run_async(init())
    logger.info("Reset all tables")
    time.sleep(60 * 10)
