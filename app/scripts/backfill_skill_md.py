"""
一次性迁移脚本：技能规范统一 —— 给缺少激活 SKILL.md 的技能回填主文件。

用法：
    python -m app.scripts.backfill_skill_md

逻辑：
0. 先把 agent_skill.prompt 列改名 skill_md（与启动期 modify_db 同一 ALTER，幂等）
1. 遍历 agent_skill 全部记录（builtin 跳过：不在前端展示，来源是 seed）
2. 若该技能当前激活版本已有 SKILL.md（大小写不敏感）→ 跳过
3. 否则按 skill 规范用 skill_md 字段生成 SKILL.md 写入 agent_skill_file
   （skill_md 缺 frontmatter 时自动补 name/description/version）
4. 幂等：重跑不会重复写入
"""

import asyncio
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


async def main():
    from tortoise import Tortoise

    from app.settings.config import settings

    settings._build_tortoise_orm()
    await Tortoise.init(config=settings.TORTOISE_ORM)

    from tortoise import connections

    # 与启动期 modify_db 同一 ALTER：prompt 列改名 skill_md（已改过则忽略）
    conn_std = connections.get("conn_standard")
    try:
        await conn_std.execute_script("ALTER TABLE agent_skill CHANGE COLUMN prompt skill_md LONGTEXT NOT NULL;")
        print("agent_skill.prompt → skill_md 列改名完成")
    except Exception:
        print("agent_skill.skill_md 列已存在，跳过改名")

    from app.models.standard.agent import AgentSkill, AgentSkillFile
    from app.services.agent_runtime.edit_tools import sync_skill_md_file

    skills = await AgentSkill.all()
    filled = 0
    skipped = 0

    for s in skills:
        if s.source == "builtin":
            skipped += 1
            continue
        rows = await AgentSkillFile.filter(skill_key=s.skill_key, is_active=True)
        if any(f.path.lower() == "skill.md" for f in rows):
            skipped += 1
            continue
        await sync_skill_md_file(s)
        filled += 1
        print(f"  [@{s.skill_key}] 已回填 SKILL.md（skill_md {len(s.skill_md or '')} 字符）")

    print(f"\n完成：回填 {filled} 个，跳过 {skipped} 个（builtin / 已有 SKILL.md）")
    await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
