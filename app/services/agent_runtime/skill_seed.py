"""内置技能的初始化 seed。"""

from __future__ import annotations

from pathlib import Path

from app.models.standard.agent import AgentSkill

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SKILLS_ROOT = _PROJECT_ROOT / ".agent_workspace" / ".agent_skills"


_ARTIFACT_GUIDE = """

【产物输出】
- 当结果适合用图表表达（占比、对比、排名、趋势等）时，请在 markdown 答复中嵌入 fenced block：
```chart
{"type": "bar", "title": "...", "xField": "name", "yField": "value", "data": [{"name": "A", "value": 10}]}
```
type 取 bar / line / pie / scatter；data 是对象数组，xField / yField 给字段名。
- 当用户需要可下载的报告（md / xlsx / pdf 等），用 write_file 把内容写到 workspace，再调用 register_artifact(name, artifact_type, relative_path, description) 登记，前端会显示下载按钮。
- 不要把图表 JSON 写成代码块以外的形式；不要把答复完全替换成 JSON。
"""

# 内置 skill 包（磁盘 .agent_workspace/.agent_skills/<key>/）现由运行时按需挂载，不再登记到 DB。


BUILTIN_SKILLS: list[dict] = [
    {
        "skill_key": "编辑",
        "name": "编辑技能",
        "description": "用对话方式修改已凝练的技能",
        "skill_md": (
            "你现在处于【编辑】模式。用户希望通过对话修改已凝练的技能。\n\n"
            "## 工作流程（必须遵守）\n"
            "1. 用户还没说清要改什么时，先问清楚（改哪部分、改成什么样）。\n"
            "2. 需求明确后，用 task 委派 skill-management 子 agent，description 写清：\n"
            "   「修改技能 <key>：<用户的修改要求原文>。先 skill_read 读现状，改完用 skill_save 落库，"
            "把改动摘要和工具返回原文告诉我」。\n"
            "3. 子 agent 返回后，把「改了什么、改前 → 改后」用简短文字呈现给用户。\n"
            "4. 用户对改动不满意 → 带着反馈再次委派修改；满意则结束。\n"
            "5. 子 agent 反馈无权修改（他人技能 / 内置技能）→ 不要反复尝试，如实转告用户，并主动建议："
            "「可以基于这个技能创建一个属于你自己的新技能，在原内容上随便改」。用户同意后再次委派："
            "子 agent 先用 skill_read 读取原技能的内容与文件（有文件时逐一把各文件也读出来），"
            "再用 skill_save 换个技能名保存为用户自己的新技能（落库 key 由平台自动按「专属code_技能名」生成），"
            "然后把新 @key 告知用户。\n\n"
            "## 注意\n"
            "- 技能读写工具（skill_read/skill_save/skill_delete）只挂在 skill-management 子 agent 上，"
            "你自己没有这些工具，必须委派。\n"
            "- 一次只改用户明确同意的内容，不要附带『顺手优化』。\n"
            "- builtin 技能不可改；子 agent 拒绝时如实转告用户。\n"
            "- 无权限时绝不尝试绕过或反复重试修改他人技能，只能走「基于它新建」路径。"
        ),
    },
]

# 这三个作为"初始数据"seed 到 DB（source=curated，可编辑可排序可删），只在不存在时创建
INITIAL_CAPABILITIES: list[dict] = [
    # {
    #     "skill_key": "标准去重",
    #     "name": "标准去重",
    #     "description": "标准池内查找疑似重复 / 近似标准",
    #     "skill_md": (
    #             "---\nskills: [standard_dedup]\n---\n"
    #             "用户要做标准去重。从用户消息里抽出待查的标准编号，按 SKILL.md 的说明调用脚本；\n"
    #             "执行后用一段话向用户说明：找到了哪些疑似重复标准、为什么需要关注、是否建议合并。\n"
    #             + _ARTIFACT_GUIDE
    #     ),
    #     "skill_pkg_keys": ["standard_dedup"],
    # },
    # {
    #     "skill_key": "指标提取",
    #     "name": "指标提取",
    #     "description": "从标准章节中抽取结构化指标",
    #     "skill_md": (
    #             "---\nskills: [indicator_extract]\n---\n"
    #             "用户要做指标提取。从用户消息里抽出标准编号（可选名称），按 SKILL.md 的说明调用脚本；\n"
    #             "执行后用一段话向用户说明：提取到多少条指标、多少条试验、覆盖的主要要素组。\n"
    #             + _ARTIFACT_GUIDE
    #     ),
    #     "skill_pkg_keys": ["indicator_extract"],
    # },
    # {
    #     "skill_key": "AI比对",
    #     "name": "AI 比对",
    #     "description": "按指标维度生成差异报告",
    #     "skill_md": (
    #             "---\nskills: [ai_comparison]\n---\n"
    #             "用户要做 AI 比对。从用户消息里抽出 source / target 两个标准编号，按 SKILL.md 的说明调用脚本；\n"
    #             "若脚本提示某一边没有缓存指标，**先用 @指标提取 对该标准跑一遍**，再回到比对。\n"
    #             "执行后用一段话总结：两标准的整体关系、关键差异、是否可互为替代。\n"
    #             + _ARTIFACT_GUIDE
    #     ),
    #     "skill_pkg_keys": ["ai_comparison"],
    # },
]


async def seed_builtin_skills() -> None:
    # 1. builtin capability（仅"编辑"，不在前端展示）
    for sk in BUILTIN_SKILLS:
        await AgentSkill.update_or_create(
            skill_key=sk["skill_key"],
            defaults={
                "name": sk["name"],
                "description": sk["description"],
                "skill_md": sk["skill_md"],
                "source": "builtin",
                "user_id": None,
                "is_enabled": 1,
            },
        )

    # 2. 初始能力（source=curated，可编辑可排序）
    #    - 不存在：创建
    #    - 已存在且 source=builtin：转为 curated（从老版本迁移过来的）
    #    - 已存在且 source!=builtin：不动（用户改过的保留）
    for cap in INITIAL_CAPABILITIES:
        existing = await AgentSkill.get_or_none(skill_key=cap["skill_key"])
        if existing is None:
            await AgentSkill.create(
                skill_key=cap["skill_key"],
                name=cap["name"],
                description=cap["description"],
                skill_md=cap["skill_md"],
                skill_pkg_keys=cap.get("skill_pkg_keys"),
                source="curated",
                user_id=None,
                is_enabled=1,
            )
        elif existing.source == "builtin":
            existing.source = "curated"
            existing.skill_md = cap["skill_md"]
            existing.skill_pkg_keys = cap.get("skill_pkg_keys")
            await existing.save()

    # 3. 清理已废弃的 builtin（全文相似度、标准评估、对象关系）
    _DEPRECATED = ["全文相似度", "标准评估", "对象关系"]
    await AgentSkill.filter(skill_key__in=_DEPRECATED, source="builtin").delete()
