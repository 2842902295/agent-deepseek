"""
Deep Agents × seekdb-cli Skill 示例

展示 deepagents 加载 seekdb-cli skill，
分析 std_main 库中 standard_ 开头的表结构与整体架构。

运行方式：python -m app.langchain.examples.deepagents_seekdb_example
"""

import asyncio
import io
import subprocess
import sys
from pathlib import Path

# Windows 终端默认 GBK，强制 stdout/stderr 使用 UTF-8 避免 emoji 和中文乱码
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from langchain.tools import tool
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver

from app.langchain.llm_providers import get_llm, LLMProvider
from app.langchain.config import langchain_config


# ── 常量 ──────────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent  # app/langchain/examples → 项目根
_SKILLS_DIR = "app/langchain/skills"   # 相对于 FilesystemBackend root_dir（项目根）
_DSN = "seekdb://root:cesi%40172@112.126.76.193/std_main"


# ── 工具：执行 seekdb CLI 命令 ─────────────────────────────────────────────────

@tool
def run_command(command: str) -> str:
    """执行 shell 命令并返回 JSON 结构化结果。适用于 seekdb CLI 等命令行工具。"""
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,  # 捕获原始字节，避免 Windows GBK/UTF-8 冲突
    )

    def _decode(b: bytes) -> str:
        if not b:
            return ""
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return b.decode(enc)
            except UnicodeDecodeError:
                continue
        return b.decode("utf-8", errors="replace")

    output = _decode(result.stdout).strip()
    if result.returncode != 0:
        stderr = _decode(result.stderr).strip()
        if stderr:
            output += f"\n[stderr] {stderr}"
    return output or "(no output)"


# ── 创建 Agent ────────────────────────────────────────────────────────────────

_SKILL_CHEATSHEET = """\
seekdb-cli 常用命令速查（所有命令需加 --dsn 参数）：
  seekdb --dsn DSN schema tables                        # 列出所有表
  seekdb --dsn DSN schema describe <table>              # 表结构详情
  seekdb --dsn DSN table profile <table>                # 数据统计
  seekdb --dsn DSN relations infer                      # 推断表关联
  seekdb --dsn DSN sql "SELECT ..."                     # 只读 SQL
  seekdb --dsn DSN sql --write "INSERT/UPDATE/DELETE"   # 写入 SQL
  seekdb --dsn DSN collection list                      # 向量集合列表
  seekdb --dsn DSN collection info <name>               # 集合详情
  seekdb --dsn DSN query <collection> --text "..."      # 语义搜索
"""


def create_seekdb_agent():
    llm = get_llm()

    agent = create_deep_agent(
        model=llm,
        tools=[run_command],
        system_prompt=(
            "你是一个 seekdb 数据库助手，通过 seekdb-cli 工具操作数据库。\n"
            f"数据库连接：--dsn \"{_DSN}\"\n"
            "目标库：std_main，重点分析 standard_ 开头的表。\n"
            "写入操作需加 --write 标志。\n"
            f"{_SKILL_CHEATSHEET}"
            "操作完成后用中文简要汇报结果。"
        ),
        backend=FilesystemBackend(root_dir=str(_PROJECT_ROOT), virtual_mode=True),
        skills=[_SKILLS_DIR],
        checkpointer=MemorySaver(),
    )
    return agent


# ── 任务 ──────────────────────────────────────────────────────────────────────

_TASKS = [
    """
    分析 std_main 库中 standard_ 开头的表，给出整体架构说明。
    """,
]


# ── 主流程 ────────────────────────────────────────────────────────────────────

def print_separator(title: str = ""):
    line = "─" * 60
    if title:
        print(f"\n┌{line}┐")
        print(f"│  {title:<58}│")
        print(f"└{line}┘")
    else:
        print(f"{'─' * 62}")


def _text(content) -> str:
    if isinstance(content, list):
        return "".join(i["text"] if isinstance(i, dict) and "text" in i else str(i) for i in content)
    return str(content or "")


async def main():
    from langchain_core.messages import AIMessage, ToolMessage

    agent = create_seekdb_agent()
    config = {"configurable": {"thread_id": "seekdb-demo-001"}}

    for task in _TASKS:
        print_separator(task.split("\n")[0][:56])
        step = 0

        for msg, _ in agent.stream(
            {"messages": [{"role": "user", "content": task}]},
            config=config,
            stream_mode="messages",
        ):
            if isinstance(msg, AIMessage):
                for tc in msg.tool_calls or []:
                    step += 1
                    args = str(tc.get("args", {}))
                    print(f"\n[步骤 {step}] 🔧 {tc['name']}  {args[:120]}{'...' if len(args) > 120 else ''}")
                if text := _text(msg.content).strip():
                    step += 1
                    print(f"\n[步骤 {step}] {'💭' if msg.tool_calls else '💬'}  {text}")
            elif isinstance(msg, ToolMessage):
                step += 1
                result = _text(msg.content)
                print(f"\n[步骤 {step}] 📋 [{msg.name}]  {result[:300]}{'...' if len(result) > 300 else ''}")


if __name__ == "__main__":
    asyncio.run(main())
