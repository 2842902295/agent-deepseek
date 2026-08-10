#!/usr/bin/env python3
"""
search.py — 在 skills.sh 公共索引里搜可装的 agent skill。

用法：
    python search.py <query> [--limit N] [--official-only] [--json]

示例：
    python search.py react performance
    python search.py changelog --limit 3
    python search.py "" --limit 10        # 留空 query 出 leaderboard top N
    python search.py react --json          # 喂给上层程序

为什么这样写：
- skills.sh 没公开 API，但主页 RSC payload 里塞着完整 leaderboard（600+ 条），
  一次抓全表，本地匹配 + 排序。
- 仅依赖标准库，任何能跑 python3 的环境都能直接用，不需要 npm / Node。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

_BASE = "https://skills.sh"
_RSC_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[\d+,\s*"((?:[^"\\]|\\.)*)"\]\)')
_SKILL_OBJ_RE = re.compile(r'\{"source":"[^"]+","skillId":"[^"]+"[^}]+\}')


def _http_get(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "skill-discovery/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _decode_rsc(html: str) -> str:
    chunks = _RSC_CHUNK_RE.findall(html)
    if not chunks:
        return ""
    return "".join(c.encode("utf-8", "replace").decode("unicode_escape", "replace") for c in chunks)


def _parse_rows(rsc_text: str) -> list[dict]:
    rows: list[dict] = []
    for m in _SKILL_OBJ_RE.finditer(rsc_text):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        rows.append(obj)
    return rows


def fetch_leaderboard() -> list[dict]:
    """抓 skills.sh 主页 → 解 RSC payload → 返回 600+ 条原始字段。"""
    try:
        html = _http_get(_BASE + "/")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"⚠ 抓取 skills.sh 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return []
    return _parse_rows(_decode_rsc(html))


def search(query: str, rows: list[dict], limit: int, official_only: bool) -> list[dict]:
    q = (query or "").strip().lower()
    terms = [t for t in q.split() if t]

    def _match(r: dict) -> bool:
        if official_only and not r.get("isOfficial"):
            return False
        if not terms:
            return True
        hay = f"{r.get('source','')} {r.get('skillId','')} {r.get('name','')}".lower()
        return all(t in hay for t in terms)

    hits = [r for r in rows if _match(r)]
    hits.sort(key=lambda r: int(r.get("installs") or 0), reverse=True)
    return hits[: max(1, min(limit, 50))]


def fmt_installs(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)


def raw_url(source: str, skill_id: str) -> str:
    """SKILL.md 的 GitHub raw URL；可交给 skill-management 子 agent 用 skill_install(url=...) 安装。"""
    return f"https://raw.githubusercontent.com/{source}/main/skills/{skill_id}/SKILL.md"


def render_markdown(hits: list[dict]) -> str:
    if not hits:
        return "未找到匹配的 skill。"
    lines = ["| # | name | source | installs | official | source_url |",
             "|---|------|--------|----------|----------|-----------|"]
    for i, r in enumerate(hits, 1):
        src = r.get("source", "?")
        sid = r.get("skillId", "?")
        nm = r.get("name", sid)
        inst = fmt_installs(int(r.get("installs") or 0))
        off = "✓" if r.get("isOfficial") else ""
        url = raw_url(src, sid)
        lines.append(f"| {i} | {nm} | {src} | {inst} | {off} | `{url}` |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="在 skills.sh 索引里搜 agent skill")
    ap.add_argument("query", nargs="*", help="搜索关键词；多个词 AND，留空出 leaderboard top N")
    ap.add_argument("--limit", "-n", type=int, default=5, help="返回多少条（默认 5，最多 50）")
    ap.add_argument("--official-only", action="store_true", help="只看 isOfficial=true 的官方 skill")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机器读用）")
    args = ap.parse_args()

    rows = fetch_leaderboard()
    if not rows:
        print("⚠ leaderboard 为空（网络或上游变更）；可改用 web_search 兜底。", file=sys.stderr)
        return 2

    query = " ".join(args.query)
    hits = search(query, rows, args.limit, args.official_only)

    if args.json:
        out = [{
            "name": r.get("name"),
            "source": r.get("source"),
            "skill_id": r.get("skillId"),
            "installs": int(r.get("installs") or 0),
            "is_official": bool(r.get("isOfficial")),
            "source_url": raw_url(r.get("source", ""), r.get("skillId", "")),
            "detail_url": f"{_BASE}/{r.get('source')}/{r.get('skillId')}",
        } for r in hits]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"# skills.sh · query={query or '(top)'} · {len(hits)}/{len(rows)} 命中\n")
        print(render_markdown(hits))
        if hits:
            print("\n> 把上表中某条的 `source_url` 交给 skill-management 子 agent，用 `skill_install(url=..., is_public=False)` 安装。")
            print("> 想看某条详情，跑 `python show.py <source>/<skill_id>`。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
