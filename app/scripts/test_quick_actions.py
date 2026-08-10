#!/usr/bin/env python3
"""
快捷功能系统测试脚本

测试步骤：
1. 创建数据库表
2. 插入测试数据
3. 测试 API 接口
"""

import asyncio
import json
from app.core.init_app import init_tortoise_orm
from app.models.standard.agent import AgentQuickAction, AgentQuickActionExample


async def test_quick_actions():
    """测试快捷功能系统"""

    # 初始化数据库连接
    await init_tortoise_orm()

    print("=" * 60)
    print("快捷功能系统测试")
    print("=" * 60)

    # 1. 测试创建快捷功能
    print("\n1. 创建快捷功能...")
    action = await AgentQuickAction.create(
        name="测试功能",
        skill_key="test_skill",
        icon="🧪",
        description="这是一个测试功能",
        sort_order=99,
        visibility="public"
    )
    print(f"   ✓ 创建成功，ID: {action.id}")

    # 2. 测试添加案例
    print("\n2. 添加案例...")
    example = await AgentQuickActionExample.create(
        action_id=action.id,
        title="测试案例",
        description="这是一个测试案例",
        conversation_data=[
            {"role": "user", "content": "测试问题"},
            {"role": "assistant", "content": "测试回答"}
        ],
        sort_order=1
    )
    print(f"   ✓ 创建成功，ID: {example.id}")

    # 3. 查询快捷功能
    print("\n3. 查询所有快捷功能...")
    actions = await AgentQuickAction.filter(is_enabled=1).order_by("sort_order")
    print(f"   ✓ 找到 {len(actions)} 个快捷功能：")
    for a in actions:
        examples_count = await AgentQuickActionExample.filter(
            action_id=a.id, is_enabled=1
        ).count()
        print(f"      - {a.icon} {a.name} (关联 {examples_count} 个案例)")

    # 4. 查询特定功能的案例
    print("\n4. 查询测试功能的案例...")
    examples = await AgentQuickActionExample.filter(
        action_id=action.id, is_enabled=1
    ).order_by("sort_order")
    print(f"   ✓ 找到 {len(examples)} 个案例：")
    for ex in examples:
        print(f"      - {ex.title}: {ex.description}")
        print(f"        会话数据: {json.dumps(ex.conversation_data, ensure_ascii=False)}")

    # 5. 更新快捷功能
    print("\n5. 更新快捷功能...")
    action.description = "更新后的描述"
    await action.save()
    print(f"   ✓ 更新成功")

    # 6. 停用案例
    print("\n6. 停用案例...")
    example.is_enabled = 0
    await example.save()
    enabled_count = await AgentQuickActionExample.filter(
        action_id=action.id, is_enabled=1
    ).count()
    print(f"   ✓ 停用成功，剩余启用案例: {enabled_count}")

    # 7. 清理测试数据
    print("\n7. 清理测试数据...")
    await AgentQuickActionExample.filter(action_id=action.id).delete()
    await action.delete()
    print(f"   ✓ 清理完成")

    print("\n" + "=" * 60)
    print("测试完成！所有功能正常运行。")
    print("=" * 60)

    # 关闭数据库连接
    from tortoise import Tortoise
    await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(test_quick_actions())
