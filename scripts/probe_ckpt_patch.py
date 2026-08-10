"""临时验证：runtime SQL patch 后是否走 MariaDB 分支"""
import sys
import types
import importlib.util

# stub settings to avoid full app boot
fake = types.ModuleType("app.settings.config")


class _S:
    STANDARD_MYSQL_HOST = ""
    STANDARD_MYSQL_PORT = 0
    STANDARD_MYSQL_USER = ""
    STANDARD_MYSQL_PASSWORD = ""
    STANDARD_MYSQL_DB = ""


fake.settings = _S()
sys.modules["app"] = types.ModuleType("app")
sys.modules["app.settings"] = types.ModuleType("app.settings")
sys.modules["app.settings.config"] = fake

spec = importlib.util.spec_from_file_location("_cp", "app/langchain/checkpointers.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["_cp"] = mod
spec.loader.exec_module(mod)

mod._patch_runtime_sql_for_oceanbase()

from langgraph.checkpoint.mysql import base as B  # noqa: E402

print("=== SELECT_SQL blob/base64 lines ===")
for line in B.SELECT_SQL.splitlines():
    if "blob" in line.lower() or "base64" in line.lower():
        print("  ", line.strip())

print("\n=== UPSERT_CHECKPOINTS_SQL ===")
print(B.UPSERT_CHECKPOINTS_SQL)

print("\n=== still contains /*!50700 ?", "/*!50700" in B.SELECT_SQL or "/*!50700" in B.UPSERT_CHECKPOINTS_SQL)
