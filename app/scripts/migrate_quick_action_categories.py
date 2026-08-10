"""
快捷功能「类型」数据迁移脚本（一次性）

背景：旧版功能用 agent_quick_action.categories（JSON 字符串数组）表达展示类型，
排序只有一个全局 sort_order。用户页橱窗按类型分章节时：
  - 章节顺序 = 类型首次出现顺序（全局排序的副产品，不稳定）
  - 组内顺序 = 全局排序（多功能跨类型时无法分别排序）

本脚本把旧数据迁到新结构：
  - agent_quick_action_category：类型表（章节顺序 = 按全局排序的首次出现顺序）
  - agent_quick_action_link：功能 ↔ 类型 关联（组内顺序 = 全局排序在组内的相对顺序）

用法：
    python -m app.scripts.migrate_quick_action_categories

选库：默认读 .env；指向测试/部署库时加 ENV_FILE，如：
    ENV_FILE=.env.test python -m app.scripts.migrate_quick_action_categories

幂等：类型按 name 匹配，link 按 (action_id, category_id) 匹配，重跑安全。
"""

import asyncio
import json

from tortoise import Tortoise, connections

from app.models.standard.agent import AgentQuickActionCategory, AgentQuickActionLink
from app.settings.config import settings


async def _init_orm() -> None:
    """直连 ORM 配置初始化（不走 app.core.init_app，避免拉起整条 API 导入链）"""
    settings._build_tortoise_orm()
    await Tortoise.init(config=settings.TORTOISE_ORM)


def _parse_categories(raw) -> list[str]:
    """兼容 JSON 列驱动返回 str / list 两种情况。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return []
        if isinstance(parsed, list):
            return [str(c).strip() for c in parsed if str(c).strip()]
    return []


async def migrate() -> None:
    await _init_orm()
    conn = connections.get("conn_standard")

    # 旧字段已从 ORM 移除，直接读原始列；按全局排序遍历，保证首次出现顺序稳定
    _, rows = await conn.execute_query("SELECT id, categories FROM agent_quick_action ORDER BY sort_order, id")

    # 1) 类型表：按全局排序中的首次出现顺序创建
    cat_ids: dict[str, int] = {}
    next_cat_sort = await AgentQuickActionCategory.all().count()
    created_cats = 0
    for row in rows:
        for name in _parse_categories(row.get("categories")):
            if name in cat_ids:
                continue
            cat = await AgentQuickActionCategory.get_or_none(name=name)
            if cat is None:
                cat = await AgentQuickActionCategory.create(name=name, sort_order=next_cat_sort)
                next_cat_sort += 1
                created_cats += 1
                print(f"  + 新类型 [{cat.id}] {name}（章节序 {cat.sort_order}）")
            cat_ids[name] = cat.id

    # 2) 关联表：组内顺序 = 全局排序在组内的相对顺序（遍历计数器）
    group_counter: dict[int, int] = {}
    created_links = 0
    for row in rows:
        action_id = row["id"]
        for name in _parse_categories(row.get("categories")):
            cat_id = cat_ids.get(name)
            if cat_id is None:
                continue
            exists = await AgentQuickActionLink.get_or_none(action_id=action_id, category_id=cat_id)
            if exists is not None:
                continue
            sort_in_group = group_counter.get(cat_id, 0)
            group_counter[cat_id] = sort_in_group + 1
            await AgentQuickActionLink.create(action_id=action_id, category_id=cat_id, sort_order=sort_in_group)
            created_links += 1
            print(f"  + 关联 功能[{action_id}] → {name}（组内序 {sort_in_group}）")

    print()
    print(f"完成：类型 +{created_cats}，关联 +{created_links}。")
    if not created_cats and not created_links:
        print("（无新增，数据已迁移过或旧表无 categories 数据）")

    await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(migrate())
