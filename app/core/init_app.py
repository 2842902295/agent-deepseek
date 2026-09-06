import os
import re
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware
from tortoise.contrib.fastapi import register_tortoise
from tortoise.exceptions import MultipleObjectsReturned

from app.api import api_router
from app.controllers import role_controller
from app.controllers.user import UserCreate, user_controller
from app.core.exceptions import (
    DoesNotExist,
    DoesNotExistHandle,
    HTTPException,
    HttpExcHandle,
    IntegrityError,
    IntegrityHandle,
    RequestValidationError,
    RequestValidationHandle,
    ResponseValidationError,
    ResponseValidationHandle,
)
from app.core.middlewares import APILoggerAddResponseMiddleware, APILoggerMiddleware, BackGroundTaskMiddleware
from app.log import log
from app.models.system import Api, Button, IconType, Menu, MenuType, Role, StatusType, User
from app.settings import APP_SETTINGS


def make_middlewares():
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=APP_SETTINGS.CORS_ORIGINS,
            allow_credentials=APP_SETTINGS.CORS_ALLOW_CREDENTIALS,
            allow_methods=APP_SETTINGS.CORS_ALLOW_METHODS,
            allow_headers=APP_SETTINGS.CORS_ALLOW_HEADERS,
        ),
        Middleware(BackGroundTaskMiddleware),
        Middleware(APILoggerMiddleware),
        Middleware(APILoggerAddResponseMiddleware),
    ]
    return middleware


def register_db(app: FastAPI):
    # 桌面单机版纯客户端模式（DESKTOP_CLIENT_MODE）：共享远程库的表结构由服务端部署保证，
    # 跳过启动建表，省掉每次启动几十次 CREATE 远程往返（服务器部署 / Docker 无此变量，不受影响）
    client_mode = os.getenv("DESKTOP_CLIENT_MODE", "").lower() in ("1", "true", "yes")
    register_tortoise(
        app,
        config=APP_SETTINGS.TORTOISE_ORM,
        generate_schemas=not client_mode,
    )


def register_exceptions(app: FastAPI):
    app.add_exception_handler(DoesNotExist, DoesNotExistHandle)
    app.add_exception_handler(HTTPException, HttpExcHandle)  # type: ignore
    app.add_exception_handler(IntegrityError, IntegrityHandle)
    app.add_exception_handler(RequestValidationError, RequestValidationHandle)
    app.add_exception_handler(ResponseValidationError, ResponseValidationHandle)


def register_routers(app: FastAPI, prefix: str = "/api"):
    app.include_router(api_router, prefix=prefix)


# ── 统一向量库：vec_item 维度解析与漂移自愈 ────────────────────────────────────
# vec_item 的 VECTOR 列维度不可 ALTER。启动期按三级回退解析「应采用的维度」，
# 与现存表不一致时 RENAME 保留旧表 + 新维度重建 + 全部库快照清零（数据已随旧表进备份，
# 库状态派生为 empty，需显式重建）。
async def _resolve_vec_item_dim(conn_std) -> int:
    """三级回退解析 vec_item 建表维度。

    ① SQL 直查激活 embed 块的 dimension 列（modify_db 时内存映射尚未从 DB 加载，直查最准）；
    ② import 期已物化进 env 的 {激活块}_DIMENSION（model_selection 播种基线）；
    ③ 旧 .env 通用字段 EMBED_DIMENSION。
    """
    try:
        rows = await conn_std.execute_query_dict(
            "SELECT b.dimension AS dimension FROM agent_model_config c "
            "JOIN agent_model_block b ON b.block_key = c.selected_key AND b.is_deleted = 0 "
            "WHERE c.category = 'embed' LIMIT 1"
        )
        if rows and rows[0].get("dimension"):
            return int(rows[0]["dimension"])
    except Exception:
        pass  # 新装库表尚未建 / 缺字段 → 走下一级
    try:
        from app.langchain.config import get_active_block

        _v = os.getenv(f"{get_active_block('embed', 'EMBED_DASHSCOPE')}_DIMENSION")
        if _v:
            return int(_v)
    except Exception:
        pass
    return int(os.getenv("EMBED_DIMENSION", "1024") or 1024)


async def _probe_vec_item_dim(conn_std):
    """读现存 vec_item 表的 VECTOR 维度；表不存在返回 None。"""
    try:
        rows = await conn_std.execute_query_dict("SHOW CREATE TABLE vec_item")
    except Exception:
        return None
    if not rows:
        return None
    ddl = rows[0].get("Create Table") or ""
    m = re.search(r"`embedding`\s+VECTOR\((\d+)\)", ddl, re.IGNORECASE)
    return int(m.group(1)) if m else None


async def _ensure_vec_item_tables(conn_std, vec_dim: int):
    """保证 vec_item / vec_item_failed 存在且维度正确（维度漂移则自愈）。"""
    cur_dim = await _probe_vec_item_dim(conn_std)
    if cur_dim is not None and cur_dim != vec_dim:
        _bak = f"vec_item_bak_{cur_dim}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        await conn_std.execute_script(f"RENAME TABLE vec_item TO {_bak};")
        try:
            await conn_std.execute_script(
                "UPDATE vec_library SET embed_block=NULL, embed_dim=NULL, item_count=0, status='empty';"
            )
        except Exception:
            pass  # vec_library 尚未建（首启时序兜底）
        log.warning(
            f"[modify_db] vec_item 维度漂移 {cur_dim}→{vec_dim}：旧表改名 {_bak} 保留，"
            "新表已按新维度重建，所有向量库需显式重建"
        )
        cur_dim = None
    if cur_dim is None:
        await conn_std.execute_script(
            f"""
            CREATE TABLE IF NOT EXISTS vec_item (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                library_id BIGINT NOT NULL,
                item_key VARCHAR(191) NOT NULL,
                content MEDIUMTEXT NOT NULL,
                content_hash CHAR(32) NOT NULL,
                payload JSON NULL,
                ref_key VARCHAR(128) NULL,
                embedding VECTOR({vec_dim}) NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uk_lib_item (library_id, item_key),
                KEY idx_lib_ref (library_id, ref_key),
                KEY idx_lib_hash (library_id, content_hash),
                VECTOR INDEX vidx_vec_item(embedding) WITH (DISTANCE=cosine, TYPE=HNSW, M=16, EF_CONSTRUCTION=200)
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
        log.info(f"[modify_db] vec_item 已按维度 {vec_dim} 建表")
    # 失败重试表（无向量列，维度无关，幂等）
    await conn_std.execute_script(
        """
        CREATE TABLE IF NOT EXISTS vec_item_failed (
            library_id BIGINT NOT NULL,
            item_key VARCHAR(191) NOT NULL,
            error TEXT NULL,
            retry_count INT NOT NULL DEFAULT 0,
            last_attempt_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (library_id, item_key),
            KEY idx_retry (library_id, retry_count)
        ) DEFAULT CHARSET=utf8mb4;
        """
    )


async def modify_db():
    from tortoise import connections

    conn_std = connections.get("conn_standard")
    try:
        await conn_std.execute_script("ALTER TABLE standard_duplicate_batch ADD COLUMN pool_id INT NULL;")
    except Exception:
        pass  # 列已存在则忽略
    try:
        await conn_std.execute_script("ALTER TABLE standard_duplicate_batch ADD COLUMN mode VARCHAR(20) NOT NULL DEFAULT 'deep';")
    except Exception:
        pass  # 列已存在则忽略

    # agent_connector 补凭据类型列（none/shared/personal；旧行默认 shared 与建表初期语义一致）
    try:
        await conn_std.execute_script("ALTER TABLE agent_connector ADD COLUMN credential_mode VARCHAR(16) NOT NULL DEFAULT 'shared';")
    except Exception:
        pass  # 列已存在则忽略

    # agent_connector 补图标列（svg 源码或图片 data URI，同 agent_skill.icon）
    try:
        await conn_std.execute_script("ALTER TABLE agent_connector ADD COLUMN icon LONGTEXT NULL;")
    except Exception:
        pass  # 列已存在则忽略

    # agent_connector 补类别列（connector/dataset；旧行默认 connector 行为不变）
    try:
        await conn_std.execute_script("ALTER TABLE agent_connector ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'connector';")
    except Exception:
        pass  # 列已存在则忽略

    # 手动补齐 agent_skill 的 visibility / tags 字段
    for sql in (
        "ALTER TABLE agent_skill ADD COLUMN visibility VARCHAR(16) NOT NULL DEFAULT 'private';",
        "ALTER TABLE agent_skill ADD COLUMN allowed_role_codes JSON NULL;",
        "ALTER TABLE agent_skill ADD COLUMN tags JSON NULL;",
    ):
        try:
            await conn_std.execute_script(sql)
        except Exception:
            pass  # 列已存在

    # users 补技能专属 code（仅用于拼技能 key，防手机号泄露）
    for sql in (
        "ALTER TABLE users ADD COLUMN skill_code VARCHAR(16) NULL;",
        "ALTER TABLE users ADD UNIQUE KEY uk_users_skill_code (skill_code);",
    ):
        try:
            await conn_std.execute_script(sql)
        except Exception:
            pass  # 列/索引已存在
    # 旧数据迁移：原"公共"（user_id IS NULL）转 public
    try:
        await conn_std.execute_script("UPDATE agent_skill SET visibility='public' WHERE user_id IS NULL AND visibility='private';")
    except Exception:
        pass

    # 「官方」分类废除（2026-08-31）：存量 official 行统一转 curated（幂等；
    # 「是否官方」语义改由「已上架」表达，上架技能 key 不带专属 code 后缀）
    try:
        await conn_std.execute_script("UPDATE agent_skill SET source='curated' WHERE source='official';")
    except Exception:
        pass

    # 技能统一：agent_skill 吸收 pkg 字段（version / source_url）
    for _sql in (
        "ALTER TABLE agent_skill ADD COLUMN version VARCHAR(32) NULL;",
        "ALTER TABLE agent_skill ADD COLUMN source_url VARCHAR(512) NULL;",
    ):
        try:
            await conn_std.execute_script(_sql)
        except Exception:
            pass

    # 技能规范统一：prompt 列改名 skill_md（内容即 SKILL.md 主文件全文）
    try:
        await conn_std.execute_script("ALTER TABLE agent_skill CHANGE COLUMN prompt skill_md LONGTEXT NOT NULL;")
    except Exception:
        pass  # 已改名则忽略

    # 技能文件表（BLOB 存储 + 版本管理）
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS agent_skill_file (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                skill_key VARCHAR(64) NOT NULL,
                path VARCHAR(512) NOT NULL,
                content LONGBLOB NOT NULL,
                size INT NOT NULL DEFAULT 0,
                is_binary TINYINT(1) NOT NULL DEFAULT 0,
                version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
                is_active TINYINT(1) NOT NULL DEFAULT 1,
                create_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                update_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                UNIQUE KEY uk_skill_path_ver (skill_key, path, version),
                INDEX idx_skill_active (skill_key, is_active)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # 应用制作行数据层（一行=一条业务记录；与 AgentAppRow 模型一致，显式建表保证唯一键/索引落地）
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS agent_app_row (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                workflow_key VARCHAR(64) NOT NULL,
                tbl VARCHAR(64) NOT NULL,
                row_key VARCHAR(191) NOT NULL,
                data JSON NOT NULL,
                updated_by INT NULL,
                create_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                update_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                UNIQUE KEY uk_wf_tbl_key (workflow_key, tbl, row_key),
                INDEX idx_wf_tbl (workflow_key, tbl)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # 商店模型：agent_skill_user_pref 增加 is_added（是否已添加到「我的技能」，与 is_enabled 正交）
    try:
        await conn_std.execute_script("ALTER TABLE agent_skill_user_pref ADD COLUMN is_added TINYINT NOT NULL DEFAULT 1;")
    except Exception:
        pass  # 列已存在则忽略

    # 技能图标：agent_skill 增加 icon（单个 <svg> 源码，agent 创建技能时生成 / 存量统一补录）
    try:
        await conn_std.execute_script("ALTER TABLE agent_skill ADD COLUMN icon LONGTEXT NULL;")
    except Exception:
        pass  # 列已存在则忽略

    # 商店分类导航：category（词表见 agent_skill.py::SKILL_CATEGORIES）+ is_featured（管理员精选）
    for _sql in (
        "ALTER TABLE agent_skill ADD COLUMN category VARCHAR(32) NULL;",
        "ALTER TABLE agent_skill ADD COLUMN is_featured TINYINT NOT NULL DEFAULT 0;",
    ):
        try:
            await conn_std.execute_script(_sql)
        except Exception:
            pass  # 列已存在则忽略

    # 专家体系（取代旧职业体系）：agent_session 补 expert_key（@专家名 会话级驻留绑定）
    try:
        await conn_std.execute_script("ALTER TABLE agent_session ADD COLUMN expert_key VARCHAR(64) NULL;")
    except Exception:
        pass  # 列已存在则忽略

    # 专家体系：废弃旧职业表（agent_expert 完全取代，数据不迁移，直接删除）
    try:
        _, rows = await conn_std.execute_query(
            "SELECT COUNT(*) AS c FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'agent_profession'"
        )
        if rows and int(rows[0]["c"]) > 0:
            await conn_std.execute_script("DROP TABLE agent_profession;")
    except Exception:
        pass

    # 专家体系：users 表移除 profession_id（专家为会话级召唤，不再有用户级职业绑定）
    try:
        _, rows = await conn_std.execute_query(
            "SELECT COUNT(*) AS c FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'profession_id'"
        )
        if rows and int(rows[0]["c"]) > 0:
            await conn_std.execute_script("ALTER TABLE users DROP COLUMN profession_id;")
    except Exception:
        pass

    # 起草单位字段合并：draft_unit_main（第一家）并入 draft_unit（全部），源表同改，删列避免口径争议
    try:
        _, rows = await conn_std.execute_query(
            "SELECT COUNT(*) AS c FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'standard_base_info' AND COLUMN_NAME = 'draft_unit_main'"
        )
        if rows and int(rows[0]["c"]) > 0:
            await conn_std.execute_script("ALTER TABLE standard_base_info DROP COLUMN draft_unit_main;")
    except Exception:
        pass

    # 案例回归橱窗：案例归属从 action_id 迁到 skill_key（存量由脚本经旧功能回填）
    for _sql in (
        "ALTER TABLE agent_quick_action_example ADD COLUMN skill_key VARCHAR(64) NULL;",
        "ALTER TABLE agent_quick_action_example ADD INDEX idx_example_skill (skill_key, is_enabled, sort_order);",
    ):
        try:
            await conn_std.execute_script(_sql)
        except Exception:
            pass  # 列/索引已存在则忽略

    # 补齐 agent_message.attachments_json 字段
    try:
        await conn_std.execute_script("ALTER TABLE agent_message ADD COLUMN attachments_json JSON NULL;")
    except Exception:
        pass

    # 补齐 agent_message.process_json 字段（过程时间线，dsh 三段式改造）
    try:
        await conn_std.execute_script("ALTER TABLE agent_message ADD COLUMN process_json JSON NULL;")
    except Exception:
        pass

    # 补齐 agent_session.is_starred 字段
    try:
        await conn_std.execute_script("ALTER TABLE agent_session ADD COLUMN is_starred TINYINT NOT NULL DEFAULT 0;")
    except Exception:
        pass

    # 补齐 agent_session.branch_from_thread_id 字段
    try:
        await conn_std.execute_script("ALTER TABLE agent_session ADD COLUMN branch_from_thread_id VARCHAR(96) NULL;")
    except Exception:
        pass

    # 补齐 agent_session.source 字段（会话来源：qa / workflow）
    try:
        await conn_std.execute_script("ALTER TABLE agent_session ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'qa';")
    except Exception:
        pass

    # 补齐 agent_session.workflow_key 字段（会话归属的工作流；画板内默认加载本工作流最近会话用）
    try:
        await conn_std.execute_script("ALTER TABLE agent_session ADD COLUMN workflow_key VARCHAR(64) NULL;")
    except Exception:
        pass

    # 扩展 agent_quick_action.icon 字段长度（iconify 图标名可能很长）
    try:
        await conn_std.execute_script("ALTER TABLE agent_quick_action MODIFY COLUMN icon VARCHAR(128) NULL;")
    except Exception:
        pass

    # agent_quick_action_example: 新增 preview_images 列（多图支持）
    try:
        await conn_std.execute_script("ALTER TABLE agent_quick_action_example ADD COLUMN preview_images JSON NULL;")
    except Exception:
        pass  # 列已存在则忽略

    # agent_quick_action: 新增 categories 列（展示类型，字符串数组，可多选）
    try:
        await conn_std.execute_script("ALTER TABLE agent_quick_action ADD COLUMN categories JSON NULL;")
    except Exception:
        pass  # 列已存在则忽略

    # 补齐 standard_cache_ai 的试验关联字段
    for _sql in (
        "ALTER TABLE standard_cache_ai ADD COLUMN source_test_names JSON NULL;",
        "ALTER TABLE standard_cache_ai ADD COLUMN target_test_names JSON NULL;",
    ):
        try:
            await conn_std.execute_script(_sql)
        except Exception:
            pass  # 列已存在则忽略

    # 补齐 agent_workflow 的人机协作信号字段（人改动简报 + 上一次写入者 + 节点徽标）
    for _sql in (
        "ALTER TABLE agent_workflow ADD COLUMN human_edit JSON NULL;",
        "ALTER TABLE agent_workflow ADD COLUMN editor VARCHAR(8) NULL;",
        "ALTER TABLE agent_workflow ADD COLUMN marks JSON NULL;",
    ):
        try:
            await conn_std.execute_script(_sql)
        except Exception:
            pass  # 列已存在则忽略

    # 补齐 agent_workflow 的板型字段（board 流程编排 / html 应用制作；NOT NULL DEFAULT 自动回填存量行）
    try:
        await conn_std.execute_script("ALTER TABLE agent_workflow ADD COLUMN board_type VARCHAR(16) NOT NULL DEFAULT 'board';")
    except Exception:
        pass  # 列已存在则忽略

    # 补齐 agent_workflow 的入口就绪标志（应用制作 index.html 是否发布过；跨机部署空板判定的 DB 真相源）
    try:
        await conn_std.execute_script("ALTER TABLE agent_workflow ADD COLUMN entry_ready TINYINT NOT NULL DEFAULT 0;")
    except Exception:
        pass  # 列已存在则忽略
    # 补齐 agent_workflow 的应用制作预览素材列（发布时从 index.html 提取，列表卡个性化预览的 DB 真相源）
    try:
        await conn_std.execute_script("ALTER TABLE agent_workflow ADD COLUMN html_preview JSON NULL;")
    except Exception:
        pass  # 列已存在则忽略
    # 补齐 agent_workflow 的分享开关（应用制作分享链接：访客可查看使用，不能编辑板本身）
    try:
        await conn_std.execute_script("ALTER TABLE agent_workflow ADD COLUMN share_on TINYINT NOT NULL DEFAULT 0;")
    except Exception:
        pass  # 列已存在则忽略
    # 补齐分享模式列：share_on 开启前提下 0=仅登录用户可打开 1=免登录公开（存量默认 0 = 仅登录，与旧行为一致）
    try:
        await conn_std.execute_script("ALTER TABLE agent_workflow ADD COLUMN share_public TINYINT NOT NULL DEFAULT 0;")
    except Exception:
        pass  # 列已存在则忽略
    # 补齐应用制作「当前查看的存档版本号」指针：版本语义改造后存档只在用户点发布时产生，
    # NULL=未固化工作态（agent 发布后的状态），非 NULL=正在查看该存档版本
    try:
        await conn_std.execute_script("ALTER TABLE agent_workflow ADD COLUMN app_version INT NULL;")
    except Exception:
        pass  # 列已存在则忽略
    # 补齐应用文件存档的固化者标记（版本列表里区分「你 / Agent」；存量存档为 NULL 显示为系统）
    try:
        await conn_std.execute_script("ALTER TABLE agent_app_file ADD COLUMN editor VARCHAR(8) NULL;")
    except Exception:
        pass  # 列已存在则忽略

    # 补齐用户对话偏好的过程展示开关（流式时自动展开工具调用过程；默认 0=一直收折）
    try:
        await conn_std.execute_script("ALTER TABLE agent_user_chat_pref ADD COLUMN tool_process_expand TINYINT NOT NULL DEFAULT 0;")
    except Exception:
        pass  # 列已存在则忽略

    # 存量回填：本机 apps 目录里已有 index.html 的看板把标志立起来（上线前发布的板也认账）
    try:
        from app.api.v1.ai.agent_workflow import backfill_entry_ready

        await backfill_entry_ready()
    except Exception:
        pass  # 回填失败不影响启动（读取侧还有本机文件兜底判定）

    # 存量回填：本机已发布的应用制作把应用文件补进 DB（agent_app_file），
    # 含历史 .versions/ 磁盘存档导入；入库后跨机物化与回滚才有真相源
    try:
        from app.api.v1.ai.agent_workflow import backfill_app_files

        await backfill_app_files()
    except Exception:
        pass  # 回填失败不影响启动（请求侧还有惰性物化兜底）

    # 知识库：每日 feed 排序结果表（夜间 agent 写入）
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS nian_daily_feed (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                feed_date DATE NOT NULL,
                entry_id VARCHAR(64) NOT NULL,
                `rank` INT NOT NULL,
                reason VARCHAR(80) NOT NULL DEFAULT '',
                confidence FLOAT NULL,
                create_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                update_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                UNIQUE KEY uk_user_date_entry (user_id, feed_date, entry_id),
                KEY idx_user_date_rank (user_id, feed_date, `rank`)
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # 知识库：夜间 agent 运行日志表
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS nian_feed_run_log (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                feed_date DATE NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'ok',
                items_written INT NOT NULL DEFAULT 0,
                brief TEXT NULL,
                error TEXT NULL,
                duration_ms INT NOT NULL DEFAULT 0,
                create_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                update_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                KEY idx_user_date (user_id, feed_date),
                KEY idx_status (status)
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # ── 计费 / 积分系统 ────────────────────────────────────────────────────
    # 1) 单价表（版本化，effective_to IS NULL 表示当前生效）
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS agent_pricing (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                provider VARCHAR(32) NOT NULL,
                model VARCHAR(96) NOT NULL,
                unit_type VARCHAR(32) NOT NULL,
                price_yuan DECIMAL(14,10) NOT NULL,
                effective_from DATETIME(6) NOT NULL,
                effective_to DATETIME(6) NULL,
                note VARCHAR(255) NULL,
                create_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                update_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                KEY idx_lookup (provider, model, unit_type, effective_to)
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # 2) 给 agent_usage_log 加列（generate_schemas 建过表的，需要 ALTER 补；新表不会触发）
    for sql in (
        "ALTER TABLE agent_usage_log ADD COLUMN biz_entry VARCHAR(32) NULL;",
        "ALTER TABLE agent_usage_log ADD COLUMN pricing_snapshot_json JSON NULL;",
        "ALTER TABLE agent_usage_log ADD COLUMN credits DECIMAL(16,4) NULL;",
        "ALTER TABLE agent_usage_log ADD INDEX idx_biz_entry (biz_entry, create_time);",
    ):
        try:
            await conn_std.execute_script(sql)
        except Exception:
            pass  # 列已存在 / 索引已存在则忽略

    # 3) agent_model_block 加 dimension 列（embed 块专用：向量输出维度）
    try:
        await conn_std.execute_script("ALTER TABLE agent_model_block ADD COLUMN dimension INT NULL;")
    except Exception:
        pass  # 列已存在则忽略

    # 4) 统一向量库条目表（vec_library 由 Tortoise generate_schemas 建；
    #    vec_item / vec_item_failed 带 VECTOR 列走裸 SQL，含维度漂移自愈）
    try:
        _vec_dim = await _resolve_vec_item_dim(conn_std)
        await _ensure_vec_item_tables(conn_std, _vec_dim)
    except Exception as _e:
        log.warning(f"[modify_db] vec_item 建表/维度自愈失败（向量库功能不可用）: {_e}")

    # 用户积分配额表（generic 模式下生效，standard 模式下不检查）
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS agent_user_credit_quota (
                user_id BIGINT NOT NULL PRIMARY KEY,
                quota BIGINT NOT NULL DEFAULT 200000,
                create_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                update_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # 图片文本回填任务日志表（由调度器写入，generate_schemas 会自动建表，此处保底）
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS standard_image_fill_log (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                run_date DATE NOT NULL COMMENT '执行日期',
                source_table VARCHAR(64) NOT NULL COMMENT '来源表名',
                source_id BIGINT NOT NULL COMMENT '来源记录ID',
                file_name TEXT NULL COMMENT '图片文件名',
                status VARCHAR(16) NOT NULL COMMENT 'ok/failed/dead',
                error_msg TEXT NULL COMMENT '失败原因',
                elapsed_ms INT NULL COMMENT '处理耗时(ms)',
                create_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                KEY idx_run_date (run_date),
                KEY idx_source (source_table, source_id),
                KEY idx_status (status)
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # 每日简报表
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS agent_daily_brief (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                brief_date DATE NOT NULL,
                content_html LONGTEXT NULL,
                content_json JSON NULL,
                prev_brief_id BIGINT NULL,
                ref_session_keys JSON NULL,
                topics_json JSON NULL,
                generation_status VARCHAR(16) NOT NULL DEFAULT 'done',
                error TEXT NULL,
                create_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                update_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                UNIQUE KEY uq_user_date (user_id, brief_date),
                KEY idx_user_date (user_id, brief_date)
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # agent_scheduled_task：Agent 定时任务表
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS agent_scheduled_task (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                task_key VARCHAR(64) NOT NULL,
                user_id INT NOT NULL,
                title VARCHAR(200) NOT NULL,
                prompt TEXT NOT NULL,
                cron_expr VARCHAR(100) NOT NULL,
                timezone VARCHAR(32) NOT NULL DEFAULT 'Asia/Shanghai',
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                last_run_at DATETIME(6) NULL,
                last_session_key VARCHAR(64) NULL,
                run_count INT NOT NULL DEFAULT 0,
                is_deleted INT NOT NULL DEFAULT 0,
                create_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                update_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                UNIQUE KEY uq_task_key (task_key),
                KEY idx_user_status (user_id, is_deleted, status)
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # agent_scheduled_task：补 last_fire_slot 列（多实例共用同一 DB 时的触发执行权抢占去重）
    try:
        await conn_std.execute_script("ALTER TABLE agent_scheduled_task ADD COLUMN last_fire_slot VARCHAR(12) NULL;")
    except Exception:
        pass  # 列已存在则忽略

    # agent_scheduled_task_run：定时任务执行记录表
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS agent_scheduled_task_run (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                task_id BIGINT NOT NULL,
                user_id INT NOT NULL,
                session_key VARCHAR(64) NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'done',
                result_summary TEXT NULL,
                error TEXT NULL,
                duration_ms INT NULL,
                create_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                update_time DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                KEY idx_task_time (task_id, create_time),
                KEY idx_user_time (user_id, create_time)
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # 用户新手引导：users 表补引导完成时间字段
    try:
        await conn_std.execute_script("ALTER TABLE users ADD COLUMN onboarded_at DATETIME(6) NULL;")
    except Exception:
        pass  # 列已存在则忽略

    # 专家体系：官方专家种子（仅当 agent_expert 为空时插入；改专家请直接 SQL 或面板管理）
    # 绑定技能沿用原职业推荐技能 ID 的策展结果：按 agent_skill.id 解析为 skill_key 落库
    try:
        _, rows = await conn_std.execute_query("SELECT COUNT(*) AS c FROM agent_expert")
        if rows and int(rows[0]["c"]) == 0:
            import json as _json

            _, skill_rows = await conn_std.execute_query("SELECT id, skill_key FROM agent_skill")
            _id2key = {int(r["id"]): r["skill_key"] for r in (skill_rows or [])}

            def _skill_keys_json(ids: list) -> str:
                ks = [_id2key[i] for i in ids if i in _id2key]
                return "'" + _json.dumps(ks, ensure_ascii=False) + "'" if ks else "NULL"

            _seed_experts = [
                # (expert_key, name, icon, description, instructions, welcome_message, skill_ids, category, sort_order)
                (
                    "exp_standard_drafter",
                    "标准编制专家",
                    "mdi:file-document-edit-outline",
                    "标准立项、起草、征求意见与审查修订全流程专家",
                    "你是一位资深的标准编制专家，长期从事国家标准、行业标准与团体标准的立项、起草、征求意见与审查修订工作。"
                    "工作方法：严格遵循 GB/T 1.1 的起草规则——结构层次（章/条/段/列项）清晰编号，术语与定义先行，规范性引用文件准确列出；"
                    "技术要求用可验证的表述（shall/should/may 对应的中文规范用语），指标给出数值、单位与试验方法；前后条文交叉引用必须一致。"
                    "交付风格：输出的标准文稿格式规范、条款编号完整；主动指出缺少的要素（范围、术语、验证方法）并给出补齐建议；"
                    "涉及与现行标准的协调性时，先用平台的标准查询工具核实再下结论。",
                    "你好，我是标准编制专家。无论是立项论证、框架搭建、条文起草还是征求意见处理，把材料交给我，我们按 GB/T 1.1 的规矩一步步来。",
                    [7, 6, 11, 12, 1],
                    "标准工程",
                    0,
                ),
                (
                    "exp_standard_reviewer",
                    "标准审查专家",
                    "mdi:check-decagram-outline",
                    "标准检索、核查、合规判定与实施落地专家",
                    "你是一位严谨的标准审查专家，负责标准符合性核查、合规判定与实施落地评估。"
                    "工作方法：审查任何标准或技术方案前，先明确审查依据（对应的标准编号与条款）；逐条核对时区分「不符合」「部分符合」与「无法判定」，"
                    "每个结论都标注依据条款号；引用标准先核实其现行有效状态（是否被替代或作废）；给出结论时同步给出整改建议与优先级。"
                    "交付风格：审查意见用表格化、条目化呈现（问题描述/依据条款/严重程度/整改建议四要素齐全）；不做没有依据的主观判断；"
                    "标准检索一律使用平台的标准查询与语义搜索工具，不凭空回忆标准内容。",
                    "你好，我是标准审查专家。把需要审查的标准文本、技术方案或合规问题交给我，我会逐条核对、给出有依据的审查意见。",
                    [13, 8, 10, 9, 14, 2],
                    "标准工程",
                    1,
                ),
                (
                    "exp_research_analyst",
                    "科研分析专家",
                    "mdi:flask-outline",
                    "标准相关研究、数据分析与文档处理专家",
                    "你是一位科研分析专家，擅长标准相关课题研究、技术数据分析与研究报告撰写。"
                    "工作方法：接到研究任务先拆解研究问题与证据需求；数据检索优先使用平台的标准查询工具与知识库，明确区分事实、数据与推断；"
                    "对比分析用统一维度列表格；结论必须能回溯到证据来源；涉及统计口径时先说明口径定义。"
                    "交付风格：研究报告结构完整（背景/方法/数据/分析/结论/建议）；图表优先于大段文字；主动说明数据局限性与结论适用边界；"
                    "引用标准、文献时给出编号或出处，不虚构来源。",
                    "你好，我是科研分析专家。研究选题、标准比对、数据分析还是报告撰写，告诉我你的研究问题，我们一起把证据链搭起来。",
                    [2, 13, 4, 3, 16],
                    "科研分析",
                    2,
                ),
                (
                    "exp_office_general",
                    "综合办公专家",
                    "mdi:briefcase-outline",
                    "日常办公、会议纪要、文档处理与翻译润色专家",
                    "你是一位高效的综合办公专家，处理日常办公事务：会议纪要、公文写作、文档整理、翻译润色与日程规划。"
                    "工作方法：会议纪要按「议题—讨论要点—决议—责任人与时限」结构整理，遗漏信息主动询问而不是脑补；"
                    "公文写作遵循对应文体规范（通知/报告/函的格式要求不同）；翻译先确认用途与受众再定文体，润色保留原意不改变事实；"
                    "文档处理优先用平台的 Office 文档技能直接产出可交付文件。"
                    "交付风格：交付物格式规整、可直接使用；长文档先给摘要再给全文；时间节点类任务主动核对日期与星期是否一致。",
                    "你好，我是综合办公专家。会议纪要、公文材料、翻译润色还是文档整理，直接把素材发给我，我给你能直接用的成果。",
                    [3, 15, 16, 4, 1],
                    "综合办公",
                    3,
                ),
            ]
            for _ek, _name, _icon, _desc, _instr, _welcome, _sids, _cat, _sort in _seed_experts:
                await conn_std.execute_query(
                    "INSERT INTO agent_expert(expert_key, name, icon, description, instructions, welcome_message,"
                    " skill_keys, category, user_id, sort_order, is_enabled, created_by, create_time, update_time)"
                    " VALUES (%s, %s, %s, %s, %s, %s, " + _skill_keys_json(_sids) + ", %s, NULL, %s, 1, NULL, NOW(), NOW())",
                    [_ek, _name, _icon, _desc, _instr, _welcome, _cat, _sort],
                )
    except Exception:
        pass

    # 3) 种价（仅当 agent_pricing 为空时插入；改价请直接 SQL，不要在这里改）
    try:
        _, rows = await conn_std.execute_query("SELECT COUNT(*) AS c FROM agent_pricing")
        if rows and int(rows[0]["c"]) == 0:
            await conn_std.execute_script(
                """
                INSERT INTO agent_pricing(provider, model, unit_type, price_yuan, effective_from, note) VALUES
                  ('dashscope', 'qwen-max',            'token_in',       0.0000200, NOW(), '初始定价(估)'),
                  ('dashscope', 'qwen-max',            'token_out',      0.0000600, NOW(), '初始定价(估)'),
                  ('dashscope', 'qwen-max',            'token_cached',   0.0000050, NOW(), '初始定价(估)'),
                  ('dashscope', 'qwen3.7-max',         'token_in',       0.0000200, NOW(), '初始定价(估)'),
                  ('dashscope', 'qwen3.7-max',         'token_out',      0.0000600, NOW(), '初始定价(估)'),
                  ('dashscope', 'qwen3.7-max',         'token_cached',   0.0000050, NOW(), '初始定价(估)'),
                  ('dashscope', 'qwen3.8-max',         'token_in',       0.0000200, NOW(), '初始定价(估，暂沿用3.7-max)'),
                  ('dashscope', 'qwen3.8-max',         'token_out',      0.0000600, NOW(), '初始定价(估，暂沿用3.7-max)'),
                  ('dashscope', 'qwen3.8-max',         'token_cached',   0.0000050, NOW(), '初始定价(估，暂沿用3.7-max)'),
                  ('dashscope', 'text-embedding-v4',   'token_in',       0.0000005, NOW(), '初始定价(估)'),
                  ('dashscope', 'happyhorse-1.0-t2v',       'video_sec_720',  0.9000000, NOW(), '初始定价'),
                  ('dashscope', 'happyhorse-1.0-t2v',       'video_sec_1080', 1.6000000, NOW(), '初始定价'),
                  ('dashscope', 'happyhorse-1.0-i2v',       'video_sec_720',  0.9000000, NOW(), '初始定价'),
                  ('dashscope', 'happyhorse-1.0-i2v',       'video_sec_1080', 1.6000000, NOW(), '初始定价'),
                  ('dashscope', 'happyhorse-1.0-r2v',       'video_sec_720',  0.9000000, NOW(), '初始定价'),
                  ('dashscope', 'happyhorse-1.0-r2v',       'video_sec_1080', 1.6000000, NOW(), '初始定价'),
                  ('dashscope', 'happyhorse-1.0-video-edit','video_sec_720',  0.9000000, NOW(), '初始定价'),
                  ('dashscope', 'happyhorse-1.0-video-edit','video_sec_1080', 1.6000000, NOW(), '初始定价'),
                  ('dashscope', 'wan3.0-video-prime', 'video_sec_480',  0.4500000, NOW(), '初始定价'),
                  ('dashscope', 'wan3.0-video-prime', 'video_sec_720',  0.9000000, NOW(), '初始定价'),
                  ('dashscope', 'wan3.0-video-prime', 'video_sec_1080', 1.8000000, NOW(), '初始定价'),
                  ('dashscope', 'wan3.0-video',       'video_sec_480',  0.3000000, NOW(), '初始定价(原价，未计限时7折)'),
                  ('dashscope', 'wan3.0-video',       'video_sec_720',  0.6000000, NOW(), '初始定价(原价，未计限时7折)'),
                  ('dashscope', 'wan3.0-video',       'video_sec_1080', 1.2000000, NOW(), '初始定价(原价，未计限时7折)'),
                  ('dashscope', 'websearch',           'mcp_call',       0.0500000, NOW(), '初始定价(估)'),
                  ('ark',       'doubao-seedance-2-0-260128', 'video_sec_480',  0.5000000, NOW(), 'Ark Seedance 2.0 480p(估)'),
                  ('ark',       'doubao-seedance-2-0-260128', 'video_sec_720',  1.0000000, NOW(), 'Ark Seedance 2.0 720p(估)'),
                  ('ark',       'doubao-seedance-2-0-260128', 'video_sec_1080', 1.8000000, NOW(), 'Ark Seedance 2.0 1080p(估)'),
                  ('ark',       'doubao-seedance-2-0-fast-260128', 'video_sec_480',  0.3000000, NOW(), 'Ark Seedance 2.0 Fast 480p(估)'),
                  ('ark',       'doubao-seedance-2-0-fast-260128', 'video_sec_720',  0.6000000, NOW(), 'Ark Seedance 2.0 Fast 720p(估)'),
                  ('ark',       'doubao-seedance-2-0-fast-260128', 'video_sec_1080', 1.1000000, NOW(), 'Ark Seedance 2.0 Fast 1080p(估)');
                """
            )
    except Exception:
        pass

    # agent_skill_category：补 icon 列（分类图标：svg 源码/图片 data URI，橱窗 pill 与引导弹窗分组头渲染）
    try:
        await conn_std.execute_script("ALTER TABLE agent_skill_category ADD COLUMN icon LONGTEXT NULL;")
    except Exception:
        pass  # 列已存在则忽略

    # agent_model_block：补 context_window 列（chat 块上下文窗口，dsh cordis contextWindow 的块级真相源；
    # 新装库建表自带，此语句兜底极早期手工改过的库）
    try:
        await conn_std.execute_script("ALTER TABLE agent_model_block ADD COLUMN context_window INT NULL DEFAULT NULL;")
    except Exception:
        pass  # 列已存在则忽略

    # 思考强度滑块化 + 对话模式可配置：
    # agent_model_block 补 reasoning_levels/reasoning_default（chat 块档位白名单与默认档）；
    # agent_chat_mode_config 补 label/note/sort_order（模式行 = 清单真相源，可增删改）
    for _sql in (
        "ALTER TABLE agent_model_block ADD COLUMN reasoning_levels VARCHAR(128) NULL DEFAULT NULL;",
        "ALTER TABLE agent_model_block ADD COLUMN reasoning_default VARCHAR(16) NULL DEFAULT NULL;",
        "ALTER TABLE agent_chat_mode_config ADD COLUMN label VARCHAR(32) NULL DEFAULT NULL;",
        "ALTER TABLE agent_chat_mode_config ADD COLUMN note VARCHAR(128) NULL DEFAULT NULL;",
        "ALTER TABLE agent_chat_mode_config ADD COLUMN sort_order INT NOT NULL DEFAULT 0;",
    ):
        try:
            await conn_std.execute_script(_sql)
        except Exception:
            pass  # 列已存在则忽略

    # 清理已废弃的 agent batch 表（多任务功能重构）
    for tbl in ("agent_batch_item", "agent_batch"):
        try:
            _, rows = await conn_std.execute_query(
                "SELECT COUNT(*) AS c FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                [tbl],
            )
            if rows and int(rows[0]["c"]) > 0:
                await conn_std.execute_script(f"DROP TABLE {tbl};")
        except Exception:
            pass

    # 卡片快捷提问：技能/专家/连接器各补 example_questions 列（JSON 字符串数组，卡片展示一键提问）
    for _sql in (
        "ALTER TABLE agent_skill ADD COLUMN example_questions JSON NULL;",
        "ALTER TABLE agent_expert ADD COLUMN example_questions JSON NULL;",
        "ALTER TABLE agent_connector ADD COLUMN example_questions JSON NULL;",
    ):
        try:
            await conn_std.execute_script(_sql)
        except Exception:
            pass  # 列已存在则忽略

    # 实体可见性档位：技能/专家/连接器各补 min_tier_code 列。
    # 2026-08-27 起语义由「单值最低档」改为「可见档位 code 数组（JSON）」，
    # 列名保留；旧库已建 VARCHAR 列的 MODIFY 成 json（存量全 NULL，安全）。
    for _table in ("agent_skill", "agent_expert", "agent_connector"):
        for _sql in (
            f"ALTER TABLE {_table} ADD COLUMN min_tier_code JSON NULL;",
            f"ALTER TABLE {_table} MODIFY COLUMN min_tier_code JSON NULL;",
        ):
            try:
                await conn_std.execute_script(_sql)
            except Exception:
                pass  # 列已存在 / 已是 json 则忽略

    # 标准库指纹增量同步状态表（单行，id 恒为 1；见 app/services/std_sync.py）
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS standard_data_sync_state (
                id INT NOT NULL PRIMARY KEY,
                status VARCHAR(16) NOT NULL DEFAULT 'idle',
                trigger_by VARCHAR(16) NULL,
                started_at DATETIME NULL,
                finished_at DATETIME NULL,
                duration_sec DOUBLE NULL,
                stats_json JSON NULL,
                last_error TEXT NULL
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # standard_jgh_pdf_term：与源库口径对齐补 ngram 全文索引（表由 Tortoise 建，索引走裸 SQL；已存在则忽略）
    for _sql in (
        "ALTER TABLE standard_jgh_pdf_term ADD FULLTEXT KEY ft_index_title (`title`) WITH PARSER ngram;",
        "ALTER TABLE standard_jgh_pdf_term ADD FULLTEXT KEY ft_index_title_word (`word`) WITH PARSER ngram;",
    ):
        try:
            await conn_std.execute_script(_sql)
        except Exception:
            pass  # 索引已存在则忽略


async def init_menus():
    menus = await Menu.exists()
    if menus:
        return

    constant_menu = [
        Menu(
            status=StatusType.enable,
            parent_id=0,
            menu_type=MenuType.catalog,
            menu_name="login",
            route_name="login",
            route_path="/login",
            component="layout.blank$view.login",
            order=1,
            i18n_key="route.login",
            props=True,
            constant=True,
            hide_in_menu=True,
        ),
        Menu(
            status=StatusType.enable,
            parent_id=0,
            menu_type=MenuType.catalog,
            menu_name="403",
            route_name="403",
            route_path="/403",
            component="layout.blank$view.403",
            order=2,
            i18n_key="route.403",
            constant=True,
            hide_in_menu=True,
        ),
        Menu(
            status=StatusType.enable,
            parent_id=0,
            menu_type=MenuType.catalog,
            menu_name="404",
            route_name="404",
            route_path="/404",
            component="layout.blank$view.404",
            order=3,
            i18n_key="route.404",
            constant=True,
            hide_in_menu=True,
        ),
        Menu(
            status=StatusType.enable,
            parent_id=0,
            menu_type=MenuType.catalog,
            menu_name="500",
            route_name="500",
            route_path="/500",
            component="layout.blank$view.500",
            order=4,
            i18n_key="route.500",
            constant=True,
            hide_in_menu=True,
        ),
    ]
    await Menu.bulk_create(constant_menu)

    # 1
    await Menu.create(
        status=StatusType.enable,
        parent_id=0,
        menu_type=MenuType.menu,
        menu_name="首页",
        route_name="home",
        route_path="/home",
        component="layout.base$view.home",
        order=1,
        i18n_key="route.home",
        icon="mdi:monitor-dashboard",
        icon_type=IconType.iconify,
    )
    await Menu.create(
        status_type=StatusType.enable,
        parent_id=0,
        menu_type=MenuType.menu,
        menu_name="关于",
        route_name="about",
        route_path="/about",
        component="layout.base$view.about",
        order=99,
        i18n_key="route.about",
        icon="fluent:book-information-24-regular",
        icon_type=IconType.iconify,
    )

    # 2
    root_menu = await Menu.create(
        status=StatusType.enable,
        parent_id=0,
        menu_type=MenuType.catalog,
        menu_name="功能",
        route_name="function",
        route_path="/function",
        component="layout.base",
        order=2,
        i18n_key="route.function",
        icon="icon-park-outline:all-application",
        icon_type=IconType.iconify,
    )

    parent_menu = await Menu.create(
        status=StatusType.enable,
        parent_id=root_menu.id,
        menu_type=MenuType.menu,
        menu_name="切换权限",
        route_name="function_toggle-auth",
        route_path="/function/toggle-auth",
        component="view.function_toggle-auth",
        order=4,
        i18n_key="route.function_toggle-auth",
        icon="ic:round-construction",
        icon_type=IconType.iconify,
    )

    button_code1 = await Button.create(button_code="B_CODE1", button_desc="超级管理员可见")
    await parent_menu.by_menu_buttons.add(button_code1)
    button_code2 = await Button.create(button_code="B_CODE2", button_desc="管理员可见")
    await parent_menu.by_menu_buttons.add(button_code2)
    button_code3 = await Button.create(button_code="B_CODE3", button_desc="管理员和用户可见")
    await parent_menu.by_menu_buttons.add(button_code3)
    await parent_menu.save()

    children_menu = [
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="请求",
            route_name="function_request",
            route_path="/function/request",
            component="view.function_request",
            order=3,
            i18n_key="route.function_request",
            icon="carbon:network-overlay",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="超级管理员可见",
            route_name="function_super-page",
            route_path="/function/super-page",
            component="view.function_super-page",
            order=5,
            i18n_key="route.function_super-page",
            icon="ic:round-supervisor-account",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="标签页",
            route_name="function_tab",
            route_path="/function/tab",
            component="view.function_tab",
            order=2,
            i18n_key="route.function_tab",
            icon="ic:round-tab",
            icon_type=IconType.iconify,
        ),
    ]
    await Menu.bulk_create(children_menu)
    await Menu.create(
        status_type=StatusType.enable,
        parent_id=root_menu.id,
        menu_type=MenuType.menu,
        menu_name="多标签页",
        route_name="function_multi-tab",
        route_path="/function/multi-tab",
        component="view.function_multi-tab",
        order=1,
        i18n_key="route.function_multi-tab",
        icon="ic:round-tab",
        icon_type=IconType.iconify,
        multi_tab=True,
        hide_in_menu=True,
        active_menu=await Menu.get(route_name="function_tab"),
    )

    parent_menu = await Menu.create(
        status_type=StatusType.enable,
        parent_id=root_menu.id,
        menu_type=MenuType.catalog,
        menu_name="隐藏子菜单",
        route_name="function_hide-child",
        route_path="/function/hide-child",
        redirect="/function/hide-child/one",
        order=2,
        i18n_key="route.function_hide-child",
        icon="material-symbols:filter-list-off",
        icon_type=IconType.iconify,
    )

    children_menu = [
        Menu(
            status_type=StatusType.enable,
            parent_id=parent_menu.id,
            menu_type=MenuType.menu,
            menu_name="隐藏子菜单1",
            route_name="function_hide-child_one",
            route_path="/function/hide-child/one",
            component="view.function_hide-child_one",
            order=1,
            i18n_key="route.function_hide-child_one",
            icon="material-symbols:filter-list-off",
            icon_type=IconType.iconify,
            hide_in_menu=True,
            active_menu=parent_menu,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=parent_menu.id,
            menu_type=MenuType.menu,
            menu_name="隐藏子菜单2",
            route_name="function_hide-child_two",
            route_path="/function/hide-child/two",
            component="view.function_hide-child_two",
            order=2,
            i18n_key="route.function_hide-child_two",
            hide_in_menu=True,
            active_menu=parent_menu,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=parent_menu.id,
            menu_type=MenuType.menu,
            menu_name="隐藏子菜单3",
            route_name="function_hide-child_three",
            route_path="/function/hide-child/three",
            component="view.function_hide-child_three",
            order=3,
            i18n_key="route.function_hide-child_three",
            hide_in_menu=True,
            active_menu=parent_menu,
        ),
    ]
    await Menu.bulk_create(children_menu)

    # 5
    root_menu = await Menu.create(
        status_type=StatusType.enable,
        parent_id=0,
        menu_type=MenuType.catalog,
        menu_name="异常页",
        route_name="exception",
        route_path="/exception",
        component="layout.base",
        order=3,
        i18n_key="route.exception",
        icon="ant-design:exception-outlined",
        icon_type=IconType.iconify,
    )
    children_menu = [
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="403",
            route_name="exception_403",
            route_path="/exception/403",
            component="view.403",
            order=1,
            i18n_key="route.exception_403",
            icon="ic:baseline-block",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="404",
            route_name="exception_404",
            route_path="/exception/404",
            component="view.404",
            order=2,
            i18n_key="route.exception_404",
            icon="ic:baseline-web-asset-off",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="500",
            route_name="exception_500",
            route_path="/exception/500",
            component="view.500",
            order=3,
            i18n_key="route.exception_500",
            icon="ic:baseline-wifi-off",
            icon_type=IconType.iconify,
        ),
    ]
    await Menu.bulk_create(children_menu)

    # 6
    root_menu = await Menu.create(
        status_type=StatusType.enable,
        parent_id=0,
        menu_type=MenuType.catalog,
        menu_name="alova示例",
        route_name="alova",
        route_path="/alova",
        component="layout.base",
        order=7,
        i18n_key="route.alova",
        icon="carbon:http",
        icon_type=IconType.iconify,
    )
    children_menu = [
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="alova_request",
            route_name="alova_request",
            route_path="/alova/request",
            component="view.alova_request",
            order=1,
            i18n_key="route.alova_request",
            icon="ic:baseline-block",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="alova_scenes",
            route_name="alova_scenes",
            route_path="/alova/scenes",
            component="view.alova_scenes",
            order=2,
            i18n_key="route.alova_scenes",
            icon="cbi:scene-dynamic",
            icon_type=IconType.iconify,
        ),
    ]
    await Menu.bulk_create(children_menu)

    # 插件示例1

    # 7
    root_menu = await Menu.create(
        status_type=StatusType.enable,
        parent_id=0,
        menu_type=MenuType.catalog,
        menu_name="插件示例",
        route_name="plugin",
        route_path="/plugin",
        component="layout.base",
        order=7,
        i18n_key="route.plugin",
        icon="clarity:plugin-line",
        icon_type=IconType.iconify,
    )

    children_menu = [
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_barcode",
            route_name="plugin_barcode",
            route_path="/plugin/barcode",
            component="view.plugin_barcode",
            order=1,
            i18n_key="route.plugin_barcode",
            icon="ic:round-barcode",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_charts",
            route_name="plugin_charts",
            route_path="/plugin/charts",
            component=None,  # No component specified for the parent
            order=2,
            i18n_key="route.plugin_charts",
            icon="mdi:chart-areaspline",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_copy",
            route_name="plugin_copy",
            route_path="/plugin/copy",
            component="view.plugin_copy",
            order=3,
            i18n_key="route.plugin_copy",
            icon="mdi:clipboard-outline",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_editor",
            route_name="plugin_editor",
            route_path="/plugin/editor",
            component=None,  # No component specified for the parent
            order=4,
            i18n_key="route.plugin_editor",
            icon="icon-park-outline:editor",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_excel",
            route_name="plugin_excel",
            route_path="/plugin/excel",
            component="view.plugin_excel",
            order=5,
            i18n_key="route.plugin_excel",
            icon="ri:file-excel-2-line",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_gantt",
            route_name="plugin_gantt",
            route_path="/plugin/gantt",
            component=None,  # No component specified for the parent
            order=6,
            i18n_key="route.plugin_gantt",
            icon="ant-design:bar-chart-outlined",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_icon",
            route_name="plugin_icon",
            route_path="/plugin/icon",
            component="view.plugin_icon",
            order=7,
            i18n_key="route.plugin_icon",
            icon="custom-icon",
            icon_type=IconType.local,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_map",
            route_name="plugin_map",
            route_path="/plugin/map",
            component="view.plugin_map",
            order=8,
            i18n_key="route.plugin_map",
            icon="mdi:map",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_pdf",
            route_name="plugin_pdf",
            route_path="/plugin/pdf",
            component="view.plugin_pdf",
            order=9,
            i18n_key="route.plugin_pdf",
            icon="uiw:file-pdf",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_pinyin",
            route_name="plugin_pinyin",
            route_path="/plugin/pinyin",
            component="view.plugin_pinyin",
            order=10,
            i18n_key="route.plugin_pinyin",
            icon="entypo-social:google-hangouts",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_print",
            route_name="plugin_print",
            route_path="/plugin/print",
            component="view.plugin_print",
            order=11,
            i18n_key="route.plugin_print",
            icon="mdi:printer",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_swiper",
            route_name="plugin_swiper",
            route_path="/plugin/swiper",
            component="view.plugin_swiper",
            order=12,
            i18n_key="route.plugin_swiper",
            icon="simple-icons:swiper",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_tables",
            route_name="plugin_tables",
            route_path="/plugin/tables",
            component=None,  # No component specified for the parent
            order=13,
            i18n_key="route.plugin_tables",
            icon="icon-park-outline:table",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_typeit",
            route_name="plugin_typeit",
            route_path="/plugin/typeit",
            component="view.plugin_typeit",
            order=14,
            i18n_key="route.plugin_typeit",
            icon="mdi:typewriter",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_video",
            route_name="plugin_video",
            route_path="/plugin/video",
            component="view.plugin_video",
            order=15,
            i18n_key="route.plugin_video",
            icon="mdi:video",
            icon_type=IconType.iconify,
        ),
    ]

    # Bulk create all child menus
    await Menu.bulk_create(children_menu)

    # Now, handle the nested children for 'plugin_charts' and 'plugin_editor' separately

    plugin_charts_menu = await Menu.get(route_name="plugin_charts")
    plugin_charts_children = [
        Menu(
            status_type=StatusType.enable,
            parent_id=plugin_charts_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_charts_antv",
            route_name="plugin_charts_antv",
            route_path="/plugin/charts/antv",
            component="view.plugin_charts_antv",
            order=1,
            i18n_key="route.plugin_charts_antv",
            icon="hugeicons:flow-square",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=plugin_charts_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_charts_echarts",
            route_name="plugin_charts_echarts",
            route_path="/plugin/charts/echarts",
            component="view.plugin_charts_echarts",
            order=2,
            i18n_key="route.plugin_charts_echarts",
            icon="simple-icons:apacheecharts",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=plugin_charts_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_charts_vchart",
            route_name="plugin_charts_vchart",
            route_path="/plugin/charts/vchart",
            component="view.plugin_charts_vchart",
            order=3,
            i18n_key="route.plugin_charts_vchart",
            icon="visactor",
            icon_type=IconType.local,
        ),
    ]

    await Menu.bulk_create(plugin_charts_children)

    # Nested children for 'plugin_editor'
    plugin_editor_menu = await Menu.get(route_name="plugin_editor")
    plugin_editor_children = [
        Menu(
            status_type=StatusType.enable,
            parent_id=plugin_editor_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_editor_markdown",
            route_name="plugin_editor_markdown",
            route_path="/plugin/editor/markdown",
            component="view.plugin_editor_markdown",
            order=1,
            i18n_key="route.plugin_editor_markdown",
            icon="ri:markdown-line",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=plugin_editor_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_editor_quill",
            route_name="plugin_editor_quill",
            route_path="/plugin/editor/quill",
            component="view.plugin_editor_quill",
            order=2,
            i18n_key="route.plugin_editor_quill",
            icon="mdi:file-document-edit-outline",
            icon_type=IconType.iconify,
        ),
    ]

    # Bulk create editor children
    await Menu.bulk_create(plugin_editor_children)

    # Nested children for 'plugin_gantt'
    plugin_gantt_menu = await Menu.get(route_name="plugin_gantt")
    plugin_gantt_children = [
        Menu(
            status_type=StatusType.enable,
            parent_id=plugin_gantt_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_gantt_dhtmlx",
            route_name="plugin_gantt_dhtmlx",
            route_path="/plugin/gantt/dhtmlx",
            component="view.plugin_gantt_dhtmlx",
            order=1,
            i18n_key="route.plugin_gantt_dhtmlx",
            icon=None,  # No icon specified
            icon_type=None,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=plugin_gantt_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_gantt_vtable",
            route_name="plugin_gantt_vtable",
            route_path="/plugin/gantt/vtable",
            component="view.plugin_gantt_vtable",
            order=2,
            i18n_key="route.plugin_gantt_vtable",
            icon="visactor",
            icon_type=IconType.local,
        ),
    ]

    # Bulk create gantt children
    await Menu.bulk_create(plugin_gantt_children)

    # Nested children for 'plugin_tables'
    plugin_tables_menu = await Menu.get(route_name="plugin_tables")
    plugin_tables_children = [
        Menu(
            status_type=StatusType.enable,
            parent_id=plugin_tables_menu.id,
            menu_type=MenuType.menu,
            menu_name="plugin_tables_vtable",
            route_name="plugin_tables_vtable",
            route_path="/plugin/tables/vtable",
            component="view.plugin_tables_vtable",
            order=1,
            i18n_key="route.plugin_tables_vtable",
            icon="visactor",
            icon_type=IconType.local,
        ),
    ]

    # Bulk create tables children
    await Menu.bulk_create(plugin_tables_children)

    # 插件示例2

    # 9
    root_menu = await Menu.create(
        status_type=StatusType.enable,
        parent_id=0,
        menu_type=MenuType.catalog,
        menu_name="多级菜单",
        route_name="multi-menu",
        route_path="/multi-menu",
        component="layout.base",
        order=4,
        i18n_key="route.multi-menu",
        icon="mdi:menu",
        icon_type=IconType.iconify,
    )
    parent_menu = await Menu.create(
        status_type=StatusType.enable,
        parent_id=root_menu.id,
        menu_type=MenuType.catalog,
        menu_name="一级子菜单1",
        route_name="multi-menu_first",
        route_path="/multi-menu/first",
        order=1,
        i18n_key="route.multi-menu_first",
        icon="mdi:menu",
        icon_type=IconType.iconify,
    )
    await Menu.create(
        status_type=StatusType.enable,
        parent_id=parent_menu.id,
        menu_type=MenuType.menu,
        menu_name="二级子菜单",
        route_name="multi-menu_first_child",
        route_path="/multi-menu/first/child",
        component="view.multi-menu_first_child",
        order=1,
        i18n_key="route.multi-menu_first_child",
        icon="mdi:menu",
        icon_type=IconType.iconify,
    )

    parent_menu = await Menu.create(
        status_type=StatusType.enable,
        parent_id=root_menu.id,
        menu_type=MenuType.catalog,
        menu_name="一级子菜单2",
        route_name="multi-menu_second",
        route_path="/multi-menu/second",
        order=13,
        i18n_key="route.multi-menu_second",
        icon="mdi:menu",
        icon_type=IconType.iconify,
    )

    parent_menu = await Menu.create(
        status_type=StatusType.enable,
        parent_id=parent_menu.id,
        menu_type=MenuType.catalog,
        menu_name="二级子菜单2",
        route_name="multi-menu_second_child",
        route_path="/multi-menu/second/child",
        order=1,
        i18n_key="route.multi-menu_second_child",
        icon="mdi:menu",
        icon_type=IconType.iconify,
    )

    await Menu.create(
        status_type=StatusType.enable,
        parent_id=parent_menu.id,
        menu_type=MenuType.menu,
        menu_name="三级菜单",
        route_name="multi-menu_second_child_home",
        route_path="/multi-menu/second/child/home",
        component="view.multi-menu_second_child_home",
        order=1,
        i18n_key="route.multi-menu_second_child_home",
        icon="mdi:menu",
        icon_type=IconType.iconify,
    )

    # 16
    root_menu = await Menu.create(
        status_type=StatusType.enable,
        parent_id=0,
        menu_type=MenuType.catalog,
        menu_name="系统管理",
        route_name="manage",
        route_path="/manage",
        component="layout.base",
        order=5,
        i18n_key="route.manage",
        icon="carbon:cloud-service-management",
        icon_type=IconType.iconify,
    )

    parent_menu = await Menu.create(
        status_type=StatusType.enable,
        parent_id=root_menu.id,
        menu_type=MenuType.menu,
        menu_name="日志管理",
        route_name="manage_log",
        route_path="/manage/log",
        component="view.manage_log",
        order=1,
        i18n_key="route.manage_log",
        icon="material-symbols:list-alt-outline",
        icon_type=IconType.iconify,
    )
    button_add_del_batch_del = await Button.create(button_code="B_Add_Del_Batch-del", button_desc="新增_删除_批量删除")

    await parent_menu.by_menu_buttons.add(button_add_del_batch_del)
    await parent_menu.save()

    parent_menu = await Menu.create(
        status_type=StatusType.enable,
        parent_id=root_menu.id,
        menu_type=MenuType.menu,
        menu_name="API管理",
        route_name="manage_api",
        route_path="/manage/api",
        component="view.manage_api",
        order=2,
        i18n_key="route.manage_api",
        icon="ant-design:api-outlined",
        icon_type=IconType.iconify,
    )
    button_refreshAPI = await Button.create(button_code="B_refreshAPI", button_desc="刷新API")

    await parent_menu.by_menu_buttons.add(button_refreshAPI)
    await parent_menu.save()

    children_menu = [
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="用户管理",
            route_name="manage_user",
            route_path="/manage/user",
            component="view.manage_user",
            order=3,
            i18n_key="route.manage_user",
            icon="ic:round-manage-accounts",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="角色管理",
            route_name="manage_role",
            route_path="/manage/role",
            component="view.manage_role",
            order=4,
            i18n_key="route.manage_role",
            icon="carbon:user-role",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="菜单管理",
            route_name="manage_menu",
            route_path="/manage/menu",
            component="view.manage_menu",
            order=5,
            i18n_key="route.manage_menu",
            icon="material-symbols:route",
            icon_type=IconType.iconify,
        ),
        Menu(
            status_type=StatusType.enable,
            parent_id=root_menu.id,
            menu_type=MenuType.menu,
            menu_name="用户详情",
            route_name="manage_user-detail",
            route_path="/manage/user-detail/:id",
            component="view.manage_user-detail",
            order=6,
            i18n_key="route.manage_user-detail",
            hide_in_menu=True,
        ),
    ]
    await Menu.bulk_create(children_menu)


async def insert_role(children_role: list[Role], role_apis: list[tuple[str, str]] = None, role_menus: list[str] = None, role_buttons: list[str] = None):
    if role_apis is None:
        role_apis = []
    if role_menus is None:
        role_menus = []
    if role_buttons is None:
        role_buttons = []

    on_conflict = ("role_code",)
    update_fields = ("role_name", "role_desc")

    await Role.bulk_create(children_role, on_conflict=on_conflict, update_fields=update_fields)

    for role_zs in children_role:
        role_obj = await Role.get(role_code=role_zs.role_code)
        for api_method, api_path in role_apis:
            try:
                api_obj: Api = await Api.get(api_method=api_method, api_path=api_path)
                await role_obj.by_role_apis.add(api_obj)
            except DoesNotExist:
                print("不存在API", api_method, api_path)
                return False

        for route_name in role_menus:
            try:
                menu_obj: Menu = await Menu.get(route_name=route_name)
                await role_obj.by_role_menus.add(menu_obj)
            except MultipleObjectsReturned:
                print("多个菜单", route_name)
                return False

        for button_code in role_buttons:
            button_obj: Button = await Button.get(button_code=button_code)
            await role_obj.by_role_buttons.add(button_obj)

        await role_obj.save()
    return True


async def init_users():
    role_exist = await role_controller.model.exists()
    if not role_exist:
        role_home_menu = await Menu.get(route_name="home")
        # 超级管理员拥有所有菜单 所有按钮
        super_role_obj = await Role.create(role_name="超级管理员", role_code="R_SUPER", role_desc="超级管理员", by_role_home=role_home_menu)
        role_super_menu_objs = await Menu.filter(constant=False)  # 过滤常量路由(公共路由)
        for menu_obj in role_super_menu_objs:
            await super_role_obj.by_role_menus.add(menu_obj)
        for button_obj in await Button.all():
            await super_role_obj.by_role_buttons.add(button_obj)

        # 管理员拥有 首页 关于 系统管理-API管理 系统管理-用户管理
        role_admin = await Role.create(role_name="管理员", role_code="R_ADMIN", role_desc="管理员", by_role_home=role_home_menu)

        role_admin_apis = [
            ("post", "/api/v1/system-manage/logs/all/"),
            ("post", "/api/v1/system-manage/apis/all/"),
            ("post", "/api/v1/system-manage/users/all/"),
            ("get", "/api/v1/system-manage/roles"),
            ("post", "/api/v1/system-manage/users"),  # 新增用户
            ("patch", "/api/v1/system-manage/users/{user_id}"),  # 修改用户
            ("delete", "/api/v1/system-manage/users/{user_id}"),  # 删除用户
            ("delete", "/api/v1/system-manage/users"),  # 批量删除用户
        ]
        role_admin_menus = ["home", "about", "function_toggle-auth", "manage_log", "manage_api", "manage_user"]
        role_admin_buttons = ["B_CODE2", "B_CODE3"]
        await insert_role([role_admin], role_admin_apis, role_admin_menus, role_admin_buttons)

        # 普通用户拥有 首页 关于 系统管理-API管理
        role_user = await Role.create(role_name="普通用户", role_code="R_USER", role_desc="普通用户", by_role_home=role_home_menu)
        role_user_apis = [("post", "/api/v1/system-manage/logs/all/"), ("post", "/api/v1/system-manage/apis/all/")]
        role_user_menus = ["home", "about", "function_toggle-auth", "manage_log", "manage_api"]
        role_user_buttons = ["B_CODE3"]
        await insert_role([role_user], role_user_apis, role_user_menus, role_user_buttons)

    user = await user_controller.model.exists()
    if not user:
        super_role_obj: Role | None = await role_controller.get_by_code("R_SUPER")
        user_super_obj: User = await user_controller.create(
            UserCreate(
                userName="Soybean",  # type: ignore
                userEmail="admin@admin.com",  # type: ignore
                password="123456",
            )
        )
        await user_super_obj.by_user_roles.add(super_role_obj)

        user_super_obj: User = await user_controller.create(
            UserCreate(
                userName="Super",  # type: ignore
                userEmail="admin1@admin.com",  # type: ignore
                password="123456",
            )
        )
        await user_super_obj.by_user_roles.add(super_role_obj)

        admin_role_obj: Role | None = await role_controller.get_by_code("R_ADMIN")
        user_admin_obj = await user_controller.create(
            UserCreate(
                userName="Admin",  # type: ignore
                userEmail="admin2@admin.com",  # type: ignore
                password="123456",
            )
        )
        await user_admin_obj.by_user_roles.add(admin_role_obj)

        user_role_obj: Role | None = await role_controller.get_by_code("R_USER")
        user_user_obj = await user_controller.create(
            UserCreate(
                userName="User",  # type: ignore
                userEmail="user@user.com",  # type: ignore
                password="123456",
            )
        )
        await user_user_obj.by_user_roles.add(user_role_obj)


# 用户档位基线（agent_role_tier）：三档包含关系 all=普通用户(rank 0) / paid=付费用户(rank 1) / gov=主管部门(rank 2)。
# 只补缺失档、不回写存量行（admin agent 改过的名称/映射不被启动覆盖，与 model_selection 播种同款纪律）
_ROLE_TIER_BASELINE = [
    ("all", "普通用户", 0, []),
    ("paid", "付费用户", 1, ["R_PAID"]),
    ("gov", "主管部门", 2, ["R_GOV"]),
]

# 档位标记角色：无菜单/按钮/API 授权（用户界面零感知），仅用于实体可见性档位判定
_MARKER_ROLES = [
    ("R_PAID", "付费用户"),
    ("R_GOV", "主管部门"),
]


async def ensure_role_tiers():
    """幂等播种档位基线：仅缺失时创建，不回写已存在的档位行。"""
    from app.models.standard.agent import AgentRoleTier

    for code, name, rank, codes in _ROLE_TIER_BASELINE:
        if await AgentRoleTier.get_or_none(tier_code=code) is None:
            await AgentRoleTier.create(tier_code=code, tier_name=name, tier_rank=rank, role_codes=codes or None)


async def ensure_marker_roles():
    """幂等播种档位标记角色（R_PAID/R_GOV）：不授任何菜单/按钮/API。

    by_role_home 为非空 FK，仿 init_users 传首页菜单；用户在界面上感知不到这些角色，
    挂上即达到对应档位（用户↔角色走现有角色管理页 / admin agent 的 users.role_codes）。
    """
    home_menu = await Menu.get_or_none(route_name="home")
    for code, name in _MARKER_ROLES:
        if not await Role.filter(role_code=code).exists():
            await Role.create(
                role_name=name,
                role_code=code,
                role_desc="档位标记角色：无菜单权限，仅用于专家/技能/连接器/数据集的可见性档位判定",
                by_role_home=home_menu,
            )
