"""
Deep Agents × SQLite 示例

使用项目封装好的 create_sqlite_agent + run_agent_stream。

运行方式：python -m app.langchain.examples.deepagents_sqlite_example
"""

import io
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from app.langchain.agents import create_sqlite_agent, run_agent_stream

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "app_system.sqlite3"

_TASKS = [
    "查询 standard_base_info 表的前 10 条记录，展示主要字段内容。",
]


def print_separator(title: str = ""):
    line = "─" * 60
    if title:
        print(f"\n┌{line}┐")
        print(f"│  {title:<58}│")
        print(f"└{line}┘")
    else:
        print(f"{'─' * 62}")


def main():
    agent = create_sqlite_agent(_DB_PATH)
    config = {"configurable": {"thread_id": "sqlite-demo-001"}}

    for task in _TASKS:
        print_separator(task[:56])
        run_agent_stream(agent, task, config)


if __name__ == "__main__":
    main()
