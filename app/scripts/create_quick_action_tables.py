"""
创建快捷功能相关表的迁移脚本

运行方式：
python -m app.scripts.create_quick_action_tables
"""

import asyncio
from app.core.init_app import init_tortoise_orm
from app.settings.config import settings


async def create_tables():
    """创建快捷功能相关表"""
    # 初始化 Tortoise ORM
    await init_tortoise_orm()

    from tortoise import Tortoise

    # 获取连接
    conn = Tortoise.get_connection("conn_standard")

    # 创建 agent_quick_action 表
    create_quick_action_sql = """
    CREATE TABLE IF NOT EXISTS `agent_quick_action` (
        `id` INT NOT NULL PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
        `name` VARCHAR(64) NOT NULL COMMENT '功能名称',
        `skill_key` VARCHAR(64) NULL COMMENT '关联的技能 key',
        `icon` VARCHAR(32) NULL COMMENT '图标标识',
        `description` VARCHAR(500) NULL COMMENT '功能描述',
        `sort_order` INT NOT NULL DEFAULT 0 COMMENT '排序',
        `is_enabled` INT NOT NULL DEFAULT 1 COMMENT '1 启用 0 停用',
        `visibility` VARCHAR(16) NOT NULL DEFAULT 'public' COMMENT '可见性',
        `allowed_role_codes` JSON NULL COMMENT '指定角色 code 列表',
        `created_by` INT NULL COMMENT '创建者用户ID',
        `create_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
        `update_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
        INDEX `idx_enabled_sort` (`is_enabled`, `sort_order`),
        INDEX `idx_skill_key` (`skill_key`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent 快捷功能';
    """

    # 创建 agent_quick_action_example 表
    create_example_sql = """
    CREATE TABLE IF NOT EXISTS `agent_quick_action_example` (
        `id` INT NOT NULL PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
        `action_id` INT NOT NULL COMMENT '所属快捷功能ID',
        `title` VARCHAR(128) NOT NULL COMMENT '案例标题',
        `description` VARCHAR(500) NULL COMMENT '案例描述',
        `conversation_data` JSON NOT NULL COMMENT '会话数据 JSON',
        `preview_image` VARCHAR(512) NULL COMMENT '预览图片路径',
        `preview_html` TEXT NULL COMMENT '预览 HTML 片段',
        `source_session_id` INT NULL COMMENT '来源会话ID',
        `source_message_ids` JSON NULL COMMENT '来源消息ID列表',
        `sort_order` INT NOT NULL DEFAULT 0 COMMENT '排序',
        `is_enabled` INT NOT NULL DEFAULT 1 COMMENT '1 启用 0 停用',
        `created_by` INT NULL COMMENT '创建者用户ID',
        `create_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
        `update_time` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间',
        INDEX `idx_action_enabled_sort` (`action_id`, `is_enabled`, `sort_order`),
        INDEX `idx_source_session` (`source_session_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent 快捷功能案例';
    """

    print("开始创建表...")

    try:
        # 执行创建表 SQL
        await conn.execute_script(create_quick_action_sql)
        print("✓ 创建 agent_quick_action 表成功")

        await conn.execute_script(create_example_sql)
        print("✓ 创建 agent_quick_action_example 表成功")

        # 插入初始数据
        insert_quick_actions = """
        INSERT INTO `agent_quick_action` (`name`, `skill_key`, `icon`, `description`, `sort_order`, `visibility`, `created_by`)
        VALUES
            ('流程图生成', '流程图生成', '📊', '快速生成流程图、架构图等可视化内容', 1, 'public', NULL),
            ('智能问数', NULL, '🔢', '智能数据查询与分析', 2, 'public', NULL),
            ('文档处理', '办公文档处理', '📄', '快速处理 Word、Excel、PDF 等文档', 3, 'public', NULL)
        ON DUPLICATE KEY UPDATE `name`=`name`;
        """

        await conn.execute_script(insert_quick_actions)
        print("✓ 插入初始快捷功能数据成功")

        print("\n表创建完成！")

    except Exception as e:
        print(f"❌ 创建表失败: {e}")
        raise

    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(create_tables())
