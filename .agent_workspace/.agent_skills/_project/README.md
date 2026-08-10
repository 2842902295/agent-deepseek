# `_project/` — Project-Bound Skills

本目录下的 skill 与本项目的 qa_agent 运行时**强耦合**。它们直接调用 in-process 工具，或经由专属子 agent 间接调用（如 kb_* 走 knowledge-base 子 agent、skill_save 走 skill-management 子 agent），无法跨项目移植。

> 顶层 `.agent_skills/<key>/` 是 portable skill（脚本自带、靠 CLI + env 解耦，可拷贝到任何项目）。两类 skill 用目录区分。

## 何时该写 project-bound skill

满足任一条就该放这里：

- 直接调宿主进程内的 Python 工具（langchain `@tool` 函数）
- 依赖宿主的运行时上下文（user_id、登录会话、ORM、checkpointer）
- 不能简单封成 CLI 脚本（起子进程开销不接受、或丢失 ReAct 流式语义）

否则——能写成自给自足的脚本（CLI 参数 + 环境变量）就放顶层，更值钱。

## 目录结构

```
_project/<skill-key>/
├── SKILL.md                    必填，含增强 frontmatter
├── tools.py                    可选，本 skill 私有工具（见下文）
├── scripts/                    可选，CLI 脚本（与 tools.py 同时存在不冲突）
└── references/ assets/ ...     按需
```

## 增强 frontmatter

在 skill-creator 标准字段之上追加这几项：

```yaml
---
name: <skill-key>
description: ...                 # 触发器，按 skill-creator 规范
type: project-bound              # 固定值
contract_version: 1              # 工具签名/契约变化必须 +1
requires:
  tools: [tool_a, tool_b, ...]   # 依赖的 in-process 工具名（必须在 qa_agent 工具集中存在）
  models: [CHAT, VISION, EMBED]  # 依赖的模型角色（按需）
  env: [STANDARD_MYSQL_HOST]     # 依赖的环境变量（按需，少见）
---
```

宿主在加载 skill 时会按 `requires.tools` 做静态校验：缺工具就报错或禁用该 skill。`contract_version` 是给迁移和回归测试用的——签名一变就 +1，老 capability 引用旧版本时能感知。

## SKILL.md 必备小节

除了 skill-creator 推荐的内容，还要有：

### `## 工具契约`

机器可读版在 frontmatter，本节给人看。每个工具一行，包含：

- 函数签名（关键参数）
- 用途和返回
- 隐含约束（比如 user_id 闭包绑定、必须先 search 再 write）

### `## 输出纪律`（如果输出要被外层程序解析）

如本 skill 输出要由后端正则抠 marker JSON，必须明确：

- marker 形式（如 `<sediment-report>{...}</sediment-report>`）
- JSON schema（必填字段、可选字段、字段含义）
- marker 出现次数和位置约束

## 自定义工具：tools.py（可选）

如果 skill 需要**仅本 skill 用的私有工具**（不该污染宿主级工具集），可以放在 `tools.py`：

```python
# tools.py
from langchain.tools import tool

def make_tools(ctx):
    """
    ctx: 宿主在加载时传入的上下文，至少含：
      - user_id: int | None
      - session_key: str | None
    返回 list[Tool]，宿主会把这些工具按需挂到 agent。
    """
    user_id = ctx.user_id

    @tool
    async def my_helper(arg: str) -> str:
        """这个 docstring 是 LLM 看到的工具说明。"""
        # ... 用 user_id 做事
        return ...

    return [my_helper]
```

> ⚠️ 这套机制目前**还没在宿主端落地**。如果你要写 tools.py，先在 PR 里同步加宿主侧的扫描 + 注入逻辑。
> 在那之前，私有工具仍然由宿主集中挂载（`app/services/agent_runtime/edit_tools.py` 等），SKILL.md 在 `requires.tools` 里声明依赖即可。

## 不要做的事

- ❌ 跨项目搬：搬不动。要搬就重写成 portable（用 scripts/）。
- ❌ 用 `requires.tools` 列宿主里压根没有的工具：启动校验会拒绝。
- ❌ 把跨多个 skill 共用的工具塞进某个 skill 的 tools.py：那是宿主级工具，应该挂在 qa_agent 全局工具集。
- ❌ 在 SKILL.md 里写"本 skill 自动加载"之类的话：触发还是靠 description 命中，agent 自己决定。

## 现有 project-bound skill

| Skill | 用途 |
|---|---|
| `sediment` | 沉淀对话入知识库 / 凝练为可复用 capability |
| `workflow-board` | 工作流画板板型方法论：板型判断 / 骨架模板 / 归属决策 / 流水账自检与重构 / 人工核査收口协议 / AI 重整流程 |
