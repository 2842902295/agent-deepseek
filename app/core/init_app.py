import os

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

from app.core.middlewares import BackGroundTaskMiddleware, APILoggerMiddleware, APILoggerAddResponseMiddleware
from app.models.system import Menu, Role, User, Button, Api
from app.models.system import StatusType, IconType, MenuType
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
    register_tortoise(
        app,
        config=APP_SETTINGS.TORTOISE_ORM,
        generate_schemas=True,
    )


def register_exceptions(app: FastAPI):
    app.add_exception_handler(DoesNotExist, DoesNotExistHandle)
    app.add_exception_handler(HTTPException, HttpExcHandle)  # type: ignore
    app.add_exception_handler(IntegrityError, IntegrityHandle)
    app.add_exception_handler(RequestValidationError, RequestValidationHandle)
    app.add_exception_handler(ResponseValidationError, ResponseValidationHandle)


def register_routers(app: FastAPI, prefix: str = "/api"):
    app.include_router(api_router, prefix=prefix)


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

    # 补齐 agent_message.attachments_json 字段
    try:
        await conn_std.execute_script("ALTER TABLE agent_message ADD COLUMN attachments_json JSON NULL;")
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

    # 补齐 agent_workflow 的板型字段（board 流程板 / html HTML看板；NOT NULL DEFAULT 自动回填存量行）
    try:
        await conn_std.execute_script("ALTER TABLE agent_workflow ADD COLUMN board_type VARCHAR(16) NOT NULL DEFAULT 'board';")
    except Exception:
        pass  # 列已存在则忽略

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

    # ── 标准向量库（standard_vec_*）─────────────────────────────────────────
    # 标准级 + 章节级双层语义检索；HNSW + cosine。
    # 不同 EMBED_PROVIDER 使用不同的表（后缀隔离），避免切换 provider 后向量空间冲突。
    # EMBED_PROVIDER 必填：web-embed（EMBED_*）或 local-embed（LOCAL_EMBED_*）。
    # 嵌入由 app/services/standard_vec/builder.py 异步落库，本处只负责保证表存在。
    try:
        from app.langchain.embedding_providers import get_embed_provider, vec_table_suffix

        _ep = get_embed_provider()
        _vs = vec_table_suffix()
        if _ep == "web-embed":
            embed_dim = int(os.getenv("EMBED_DIMENSION", "1024") or 1024)
        else:
            embed_dim = int(os.getenv("LOCAL_EMBED_DIMENSION") or os.getenv("EMBED_DIMENSION", "1024") or 1024)
    except Exception:
        _vs = ""
        embed_dim = 1024

    # 维度编入表名（_vs 包含 provider + dimension，如 _web_2048），
    # 切换维度时旧表保留不动，切回来直接用，无需清理或重建。
    try:
        await conn_std.execute_script(
            f"""
            CREATE TABLE IF NOT EXISTS standard_vec_meta{_vs} (
                standard_no VARCHAR(64) NOT NULL PRIMARY KEY,
                cname VARCHAR(512) NULL,
                use_range TEXT NULL,
                embedding VECTOR({embed_dim}) NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                VECTOR INDEX vidx_meta(embedding) WITH (DISTANCE=cosine, TYPE=HNSW, M=16, EF_CONSTRUCTION=200)
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    try:
        await conn_std.execute_script(
            f"""
            CREATE TABLE IF NOT EXISTS standard_vec_chapter{_vs} (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                standard_no VARCHAR(64) NOT NULL,
                chapter_id BIGINT NULL,
                title_no VARCHAR(64) NULL,
                title VARCHAR(512) NULL,
                word_excerpt TEXT NULL,
                embedding VECTOR({embed_dim}) NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_standard_no (standard_no),
                KEY idx_chapter_id (chapter_id),
                UNIQUE KEY uk_standard_chapter (standard_no, chapter_id),
                VECTOR INDEX vidx_chapter(embedding) WITH (DISTANCE=cosine, TYPE=HNSW, M=16, EF_CONSTRUCTION=200)
            ) DEFAULT CHARSET=utf8mb4;
            """
        )
    except Exception:
        pass

    # 失败重试表：embed/写入失败的章节按 chapter_id 落库，下次跑时优先重试 retry_count<3
    try:
        await conn_std.execute_script(
            """
            CREATE TABLE IF NOT EXISTS standard_vec_chapter_failed{_vs} (
                chapter_id BIGINT PRIMARY KEY,
                standard_no VARCHAR(64) NULL,
                error TEXT NULL,
                retry_count INT NOT NULL DEFAULT 0,
                last_attempt_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_retry (retry_count, last_attempt_at)
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
                status VARCHAR(16) NOT NULL COMMENT 'ok/failed',
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

    # 用户新手引导：users 表补职业 / 引导完成时间字段
    for _sql in (
        "ALTER TABLE users ADD COLUMN profession_id INT NULL;",
        "ALTER TABLE users ADD COLUMN onboarded_at DATETIME(6) NULL;",
    ):
        try:
            await conn_std.execute_script(_sql)
        except Exception:
            pass  # 列已存在则忽略

    # 用户职业表：默认种子（仅当 agent_profession 为空时插入；改推荐请直接 SQL 或管理端）
    try:
        _, rows = await conn_std.execute_query("SELECT COUNT(*) AS c FROM agent_profession")
        if rows and int(rows[0]["c"]) == 0:
            await conn_std.execute_script(
                """
                INSERT INTO agent_profession(name, icon, description, recommended_action_ids, sort_order, is_enabled, create_time, update_time) VALUES
                  ('标准编制人员', 'mdi:file-document-edit-outline', '负责标准立项、起草、征求意见与审查修订', '[7, 6, 11, 12, 1]', 0, 1, NOW(), NOW()),
                  ('标准审查人员', 'mdi:check-decagram-outline',   '负责标准检索、核查、合规判定与实施落地', '[13, 8, 10, 9, 14, 2]', 1, 1, NOW(), NOW()),
                  ('科研分析人员', 'mdi:flask-outline',             '从事标准相关研究、数据分析与文档处理',   '[2, 13, 4, 3, 16]', 2, 1, NOW(), NOW()),
                  ('综合办公人员', 'mdi:briefcase-outline',         '日常办公、会议纪要、文档处理与翻译润色', '[3, 15, 16, 4, 1]', 3, 1, NOW(), NOW());
                """
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
