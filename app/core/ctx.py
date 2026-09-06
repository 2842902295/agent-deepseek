import contextvars

from starlette.background import BackgroundTasks

CTX_USER_ID: contextvars.ContextVar[int] = contextvars.ContextVar("user_id", default=0)
CTX_X_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("x_request_id", default="")
CTX_BG_TASKS: contextvars.ContextVar[BackgroundTasks | None] = contextvars.ContextVar("bg_task", default=None)

# 计费上下文：业务入口在请求开始时 set，BillingCallbackHandler 读取后写入 agent_usage_log。
# - module 已经由调用方根据 LLM 角色 / 工具类型决定（chat/vision/embed/mcp/video），不在这里设
# - biz_entry：qa / nian / workbench / qa-batch / ...，用于看板按业务维度切片
# - session_id：冗余到 usage_log，便于按会话查询消费
CTX_BILLING_BIZ_ENTRY: contextvars.ContextVar[str | None] = contextvars.ContextVar("billing_biz_entry", default=None)
CTX_BILLING_SESSION_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar("billing_session_id", default=None)

# 按角色模型配置：请求级生成能力覆盖（{"IMAGE": 块名或"DISABLED", "VIDEO": ...}）。
# 业务入口（qa.py / scheduler.py）解析用户角色 profile 后 set；
# config.py::load_provider / has_capability 读取，全链路（6 个 client + 工具门卫）零改动跟随。
# 未设置（每日简报等系统路径）→ 走全局激活块，语义不变。
CTX_GEN_BLOCK_OVERRIDE: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar("gen_block_override", default=None)
