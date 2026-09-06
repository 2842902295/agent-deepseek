"""
一次性迁移脚本：把 .agent_skills/ 下非内置技能包的文件内容写入 agent_skill_file 表。

用法：
    python -m app.scripts.migrate_skill_files_to_db

逻辑：
1. 扫描 .agent_workspace/.agent_skills/ 下所有目录
2. 跳过内置 skill（硬编码列表）
3. 对每个目录，把所有文件（排除 .versions/, .bak/）写入 agent_skill_file
4. 同时把 agent_skill_pkg 的 version 同步到 agent_skill（如果同 key 有 capability 记录）
5. 幂等：已存在的 (skill_key, path, version) 跳过
"""

import asyncio
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 内置 skill key 列表（不迁移）——迁移时点的历史快照，直接内联
BUILTIN_KEYS = frozenset(
    [
        # 主 agent 通用类
        "ai-video-gen", "algorithmic-art", "brand-guidelines", "browser-use", "crawl4ai",
        "canvas-design", "doc-coauthoring", "doc-review-enhance",
        "excalidraw-diagram-generator",
        "humanizer-zh", "internal-comms", "pdf", "slack-gif-creator",
        "theme-factory", "ui-ux-pro-max", "hallmark", "visiomaster", "web-artifacts-builder",
        "音乐跨平台下载与链接查找", "text-to-cad", "office-cli", "browser-skill",
        # standard sub-agent
        "standards-db",
        # skill-management sub-agent
        "skill-creator",
        # stock sub-agent
        "a-share-data", "stock-analysis",
    ]
    + ["_project"]  # _project 是内置 skill 的父目录
)

_SKIP_DIRS = frozenset([".versions", ".bak", ".git", "__pycache__", "node_modules"])

_BINARY_EXTS = frozenset(
    ".png .jpg .jpeg .gif .bmp .ico .webp".split()
    + ".zip .tar .gz .bz2 .7z .rar".split()
    + ".pdf .doc .docx .xls .xlsx .ppt .pptx".split()
    + ".exe .dll .so .dylib .bin .dat".split()
    + ".mp3 .mp4 .avi .mov .wav .flac".split()
    + ".ttf .otf .woff .woff2 .eot".split()
)


async def main():
    from tortoise import Tortoise

    from app.settings.config import settings

    settings._build_tortoise_orm()
    await Tortoise.init(config=settings.TORTOISE_ORM)
    await Tortoise.generate_schemas(safe=True)

    from app.models.standard.agent import AgentSkill, AgentSkillFile

    skills_root = _PROJECT_ROOT / ".agent_workspace" / ".agent_skills"
    if not skills_root.exists():
        print(f"目录不存在：{skills_root}，无需迁移")
        return

    migrated = 0
    skipped = 0

    for pkg_dir in sorted(skills_root.iterdir()):
        if not pkg_dir.is_dir():
            continue
        key = pkg_dir.name
        if key in BUILTIN_KEYS or key.startswith("."):
            skipped += 1
            continue

        # 确定版本号（从 agent_skill 记录读取，无则默认 1.0.0）
        skill_row_for_ver = await AgentSkill.get_or_none(skill_key=key)
        version = (skill_row_for_ver.version if skill_row_for_ver else None) or "1.0.0"

        # 扫描文件
        files_to_write: list[tuple[str, bytes, bool]] = []
        for f in pkg_dir.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(pkg_dir).as_posix()
            # 跳过隐藏/缓存目录
            if any(p in _SKIP_DIRS or p.startswith(".") for p in rel.split("/")):
                continue
            is_bin = ("." + rel.rsplit(".", 1)[-1].lower()) in _BINARY_EXTS if "." in rel else False
            content = f.read_bytes()
            files_to_write.append((rel, content, is_bin))

        if not files_to_write:
            skipped += 1
            continue

        # 写入 DB（幂等）
        written = 0
        for rel, content, is_bin in files_to_write:
            exists = await AgentSkillFile.exists(skill_key=key, path=rel, version=version)
            if exists:
                continue
            await AgentSkillFile.create(
                skill_key=key,
                path=rel,
                content=content,
                size=len(content),
                is_binary=is_bin,
                version=version,
                is_active=True,
            )
            written += 1

        # 同步 version 到 agent_skill（如果有对应记录）
        skill_row = await AgentSkill.get_or_none(skill_key=key)
        if skill_row and not skill_row.version:
            skill_row.version = version
            await skill_row.save()

        migrated += 1
        print(f"  [{key}] v{version}: {written} 个文件入库（共 {len(files_to_write)} 个）")

    print(f"\n完成：迁移 {migrated} 个技能包，跳过 {skipped} 个（内置/空）")
    await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
