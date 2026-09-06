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

import yaml

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
SKILLS_ROOT = _PROJECT_ROOT / ".agent_workspace" / ".agent_skills"

# SKILL.md frontmatter 解析
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def _slugify(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[\\/:*?\"<>|]+", "-", s)
    s = re.sub(r"\s+", "-", s)
    return s[:64]


def _fm_scalar(v: Any) -> Any:
    """frontmatter 值规整：标量统一转 str（下游按字符串读 name/description/version），
    嵌套结构（requires 等）原样保留。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        return v.strip()
    return str(v)


def _parse_naive(body: str) -> dict[str, Any]:
    """兜底简单行解析（只处理单行字段）：PyYAML 解析失败时保住基本盘。"""
    out: dict[str, Any] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip("\"'")
    return out


def parse_skill_md(md: str) -> dict[str, Any]:
    """从 SKILL.md 抠 frontmatter，返回 {name, description, version, ...}。失败返回空 dict。

    用 PyYAML 真解析：支持块标量（`description: |` / `>`）、引号字符串、嵌套字段
    （requires 等）。历史教训：手写行解析遇到 `description: |` 只会把字面 "|" 当成描述。
    入口先 strip BOM/前导空白：Windows 记事本保存的 SKILL.md 常带 BOM 头或开头空行，
    会让 `^---` 正则失配、frontmatter 整个丢失（name 退化成默认 key）。
    """
    # strip UTF-8 BOM（引号内为不可见字符 ﻿）与前导空白
    md = (md or "").lstrip("﻿").lstrip()
    m = _FM_RE.match(md)
    if not m:
        return {}
    body = m.group(1)
    try:
        data = yaml.safe_load(body)
    except Exception:  # noqa: BLE001 — frontmatter 不是合法 YAML 时退化为行解析
        return _parse_naive(body)
    if not isinstance(data, dict):
        return _parse_naive(body)
    return {str(k): _fm_scalar(v) for k, v in data.items()}


__all__ = [
    "SKILLS_ROOT",
    "parse_skill_md",
]
