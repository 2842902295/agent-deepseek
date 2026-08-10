#!/usr/bin/env python3
"""
show.py — 取 skills.sh 上某个具体 skill 的详细信息（description + 安装命令）。

用法：
    python show.py <source>/<skill_id>
    python show.py vercel-labs/agent-skills/vercel-react-best-practices

输出 markdown：标题 + description + raw URL + skill_install 的安装模板。
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request

_BASE = "https://skills.sh"
_OG_DESC_RE = re.compile(
    r'<meta\s+(?:name|property)="(?:og:)?description"\s+content="([^"]+)"',
    re.I,
)


def _http_get(url: str, timeout: float = 10.0) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": "skill-discovery/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                return None
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"⚠ HTTP {url}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _unescape(s: str) -> str:
    return (s.replace("&quot;", '"').replace("&amp;", "&")
             .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'"))


def parse_path(arg: str) -> tuple[str, str] | None:
    """
    接受三段或四段路径：
      vercel-labs/agent-skills/vercel-react-best-practices  → ('vercel-labs/agent-skills', 'vercel-react-best-practices')
      anthropics/skills/frontend-design                      → ('anthropics/skills', 'frontend-design')
    """
    parts = [p for p in arg.strip().strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    skill_id = parts[-1]
    source = "/".join(parts[:-1])
    return source, skill_id


def main() -> int:
    ap = argparse.ArgumentParser(description="查看 skills.sh 上某个 skill 的详情")
    ap.add_argument("path", help="skill 路径，例如 vercel-labs/agent-skills/vercel-react-best-practices")
    args = ap.parse_args()

    parsed = parse_path(args.path)
    if not parsed:
        print(f"⚠ 路径格式错误：{args.path}\n  期望：<owner>/<repo>/<skill_id>", file=sys.stderr)
        return 2
    source, skill_id = parsed

    detail_url = f"{_BASE}/{source}/{skill_id}"
    raw_url = f"https://raw.githubusercontent.com/{source}/main/skills/{skill_id}/SKILL.md"

    html = _http_get(detail_url)
    desc = None
    if html:
        m = _OG_DESC_RE.search(html)
        if m:
            desc = _unescape(m.group(1)).strip()

    print(f"# {skill_id}")
    print(f"\n**来源**：`{source}`")
    print(f"**详情页**：{detail_url}")
    print(f"**SKILL.md raw**：`{raw_url}`")
    print()
    if desc:
        print("## 描述\n")
        print(desc)
    else:
        print("> ⚠ 未拿到 description（页面结构可能变了，但 raw URL 仍可用）")
    print()
    print("## 安装到本系统")
    print("```")
    print(f"task(subagent_type='skill-management', description='安装技能：请调用 skill_install(url=\"{raw_url}\", is_public=false)，把工具返回原文告诉我')")
    print("```")
    print("> `is_public=true` 让全员可见。装完用 `@" + skill_id + "` 召唤。")
    return 0 if desc else 1


if __name__ == "__main__":
    sys.exit(main())
