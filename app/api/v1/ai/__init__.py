"""
AI 模块 API

提供 LangChain、LangGraph 相关的 API 接口（完全使用 LangServe）
"""

from fastapi import APIRouter

from app.api.v1.ai.agent import router as agent_router
from app.api.v1.ai.agent_skill import router as agent_skill_router
from app.api.v1.ai.quick_action import router as quick_action_router
from app.api.v1.ai.dashboard import router as dashboard_router
from app.api.v1.ai.model_config import router as model_config_router
from app.api.v1.ai.role_model_config import router as role_model_config_router
from app.api.v1.ai.ai_comparison_v2 import router as ai_comparison_router
from app.api.v1.ai.data_migration import router as data_migration_router
from app.api.v1.ai.deduplication import router as deduplication_router
from app.api.v1.ai.deduplication_agent import router as deduplication_agent_router
from app.api.v1.ai.deduplication_pool import router as deduplication_pool_router
from app.api.v1.ai.full_text_similarity import router as full_text_similarity_router
from app.api.v1.ai.kb import router as kb_router
from app.api.v1.ai.langserve_routes import router as langserve_router, register_langserve_routes
from app.api.v1.ai.qa import router as qa_router
from app.api.v1.ai.standard_base_info import router as standard_base_info_router
from app.api.v1.ai.standard_evaluation import router as standard_evaluation_router
from app.api.v1.ai.standard_ind import router as standard_ind_router
from app.api.v1.ai.langextract_indicator import router as langextract_indicator_router
from app.api.v1.ai.standard_obj import router as standard_obj_router
from app.api.v1.ai.standard_obj_rel import router as standard_obj_rel_router
from app.api.v1.ai.standard_vec import router as standard_vec_router
from app.api.v1.ai.upload import router as upload_router
from app.api.v1.ai.upload import public_router as upload_public_router
from app.api.v1.ai.mineru_test import router as mineru_test_router
from app.api.v1.ai.task import router as task_router
from app.api.v1.ai.agent_workflow import router as agent_workflow_router

# 创建 AI 模块路由
ai_router = APIRouter(prefix="/ai", tags=["AI"])

# 只注册 LangServe 路由
ai_router.include_router(langserve_router)


# 注册查重池管理路由
ai_router.include_router(deduplication_pool_router)

# 注册查重路由
ai_router.include_router(deduplication_router)

# 注册全文相似度路由
ai_router.include_router(full_text_similarity_router)

# 注册AI比对路由 (V2 - 表格形式)
ai_router.include_router(ai_comparison_router)

# 注册标准基础信息路由
ai_router.include_router(standard_base_info_router)

# 注册标准整合评估路由
ai_router.include_router(standard_evaluation_router)

# 注册通用问答路由
ai_router.include_router(qa_router)

# 注册 Agent 工作台路由（会话）
ai_router.include_router(agent_router)
ai_router.include_router(agent_skill_router)

# 注册快捷功能路由
ai_router.include_router(quick_action_router)

# 注册 AI 用户使用看板路由（仅 R_SUPER 可见）
ai_router.include_router(dashboard_router)

# 注册模型切换路由（仅 R_SUPER 可用，全局生效）
ai_router.include_router(model_config_router)

# 注册按角色模型配置路由（仅 R_SUPER 可用，角色设定 > 全局设定）
ai_router.include_router(role_model_config_router)

# 注册个人知识库路由
ai_router.include_router(kb_router)

# 注册基于 StructuredQAAgent 的查重路由
ai_router.include_router(deduplication_agent_router)

# 注册数据迁移路由
ai_router.include_router(data_migration_router)

# 注册标准指标查询路由
ai_router.include_router(standard_ind_router)

# 注册 LangExtract 指标提取路由
ai_router.include_router(langextract_indicator_router)

# 注册标准化对象视角路由
ai_router.include_router(standard_obj_router)

# 注册标准化对象关系路由
ai_router.include_router(standard_obj_rel_router)

# 注册标准向量库路由（status / build / cancel）
ai_router.include_router(standard_vec_router)

# 注册文件上传路由
ai_router.include_router(upload_router)

# 注册 MinerU 单条测试路由
ai_router.include_router(mineru_test_router)

# 注册定时任务管理路由
ai_router.include_router(task_router)

# 注册共享工作流路由
ai_router.include_router(agent_workflow_router)

# 注册 LangServe 自动生成的路由
register_langserve_routes(ai_router)

# ── 公开 AI 路由：不挂全局 auth，按需自己鉴权（如 token 校验） ───────────────────
from app.api.v1.ai.agent_public import router as agent_public_router
from app.api.v1.ai.html_app import router as html_app_router

ai_public_router = APIRouter(prefix="/ai", tags=["AI"])
ai_public_router.include_router(agent_public_router)
ai_public_router.include_router(upload_public_router)
# HTML 看板应用托管（URL 内嵌 htmlAppToken 门控，token 与登录态双向隔离）
ai_public_router.include_router(html_app_router)

__all__ = ["ai_router", "ai_public_router"]
