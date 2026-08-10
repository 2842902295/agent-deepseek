"""
Skill 包通用工具：SKILL.md frontmatter 解析、slug、磁盘根路径。

历史上这里还承载 create_skill_package / install_skill_pkg 等 agent 工具与
agent_skill_pkg 表的读写；技能文件改存 DB（agent_skill_file）后，那些逻辑已迁移到
`app/services/agent_runtime/edit_tools.py`（skill_read/save/delete/install 四个工具），
本模块只保留与 DB 无关的纯函数供可能的复用。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
SKILLS_ROOT = _PROJECT_ROOT / ".agent_workspace" / ".agent_skills"

# SKILL.md frontmatter 解析
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def _slugify(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[\\/:*?\"<>|]+", "-", s)
    s = re.sub(r"\s+", "-", s)
    return s[:64]


def parse_skill_md(md: str) -> dict[str, Any]:
    """从 SKILL.md 抠 frontmatter，返回 {name, description, version, ...}。失败返回空 dict。"""
    m = _FM_RE.match(md)
    if not m:
        return {}
    body = m.group(1)
    out: dict[str, Any] = {}
    # 简单 yaml 行解析（足以处理 name/description/version 等单行字段）
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip("\"'")
    return out


__all__ = [
    "SKILLS_ROOT",
    "parse_skill_md",
]
