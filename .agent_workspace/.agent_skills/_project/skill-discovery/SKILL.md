---
name: skill-discovery
description: 当用户想"找一个能 XX 的 skill"、"有没有现成的 skill"、"搜一下生态里的 skill"，或表达"希望能扩展某项能力"时使用。本 skill 教 qa_agent 从 skills.sh 公开生态**发现**合适的 skill 候选（search.py 检索 + 候选评估 + 降级搜索）。发现后的安装落库统一由 skill-management 子 agent 的 skill_install 完成。
type: project-bound
contract_version: 4
requires:
  tools: [execute, task]
---

# Skill Discovery

教 qa_agent 从公开生态里**发现**合适的 skill。职责边界要分清：

- **发现（本 skill 负责）**：跑本目录下的 `scripts/search.py`（纯 Python 标准库直查 [skills.sh](https://skills.sh) leaderboard，不需要 npx / Node），评估候选，交给用户挑。
- **安装（不归本 skill）**：技能落库工具 `skill_install` 只在 skill-management 子 agent 上。选定候选后用 `task(subagent_type='skill-management')` 委派，把 source_url 原样写进 description，让它执行 `skill_install(url=...)`。

> ⚠️ **不要用 `npx skills add`**——本项目环境多半没有 Node，且那条命令绕开 DB / 可见性 / 多版本，装完只对单机一个人有用。
> **只用本目录的 search.py 来发现 URL，安装永远委派 skill-management 子 agent 走 `skill_install`。**

## 何时启用

只在用户**明确**表达想找现成 skill 时：

- "帮我找个能 XX 的 skill" / "有没有现成的 skill 处理 XX"
- "搜一下生态里有没有 XX skill"
- 用户给出具体的 skill 名或 GitHub URL 想装（有 URL 时跳过发现，直接委派 skill-management 安装）

**不要**在以下场景启用：

- 用户问"你能做 XX 吗"——先试自己做，做不动再问要不要找 skill
- 用户在抱怨某能力弱——先确认要不要扩展
- 当前对话适合凝练成自己的 skill——走 sediment skill（最终由 skill_save 落库）

## 发现工作流

### Step 1. 用 search.py 检索

用 `execute` 跑：

```bash
python $AGENT_SKILLS/_project/skill-discovery/scripts/search.py <query> --limit 5
```

> `$AGENT_SKILLS` 已注入 shell 环境变量，等于 workspace 下的 `.agent_skills`。也可以直接相对路径 `python .agent_skills/_project/skill-discovery/scripts/search.py ...`。

**关键词技巧**：

- 关键词要具体：`react testing` 比 `testing` 好
- 试不同措辞：`deploy` 不行就试 `deployment` 或 `ci-cd`
- **中文需求要翻成英文搜**（skills.sh 索引以英文为主）：用户问 "前端设计" 就搜 `frontend design`
- 多词用空格，AND 匹配

输出是一张 markdown 表，每行：name / source / installs / official / source_url。**source_url 是 SKILL.md 的 GitHub raw URL，可直接交给 skill-management 子 agent 安装**。

附加用法（不常用，按需）：

- `--official-only` 只看 isOfficial=true 的官方 skill
- `--json` 出机器可读 JSON（如果你想在脚本里串接）
- `--limit N` 改条数（默认 5，最多 50）
- `python show.py <owner>/<repo>/<skill_id>` 取单条详情，含 description 和安装命令模板

### Step 2. 评估候选质量

每个候选核两件事：

1. **Installs**——优先 1K+ 的；< 100 谨慎对待
2. **isOfficial / 来源**——`vercel-labs`、`anthropics`、`microsoft`、`google-labs-code` 等知名组织远比无名作者可信

### Step 3. 把候选念给用户挑

**直接把表格贴给用户看**——这是 search.py 输出 markdown 的目的，不要复述、不要藏候选。

候选可能 0 / 1 / 多条：

- **0 条**：search.py 会显示"未找到匹配的 skill"。问要不要 (a) 改个措辞再试、(b) 走 fallback 检索（见下文 §search.py 跑不通怎么办 那段）、(c) 走技能创建流程自己写一个（委派 skill-management）
- **1 条 + installs 高 + 来源知名 + 用户语气明确**："装吧"、"就这个"——可以直接进 Step 4
- **多条 / 用户语气不明确**：让用户挑序号或贴 source_url。**不要替用户决定**

### Step 4. 安装（委派 skill-management）

```
task(subagent_type='skill-management',
     description='安装技能：请调用 skill_install(url="<上一步表格里原样的 source_url>", is_public=false)，把工具返回原文告诉我')
```

参数规则：

- `url` 用 search.py 表格里**原样**的 URL，不要改
- `is_public` 默认 `false`（仅当前用户私有）；用户明确说"给团队"/"全员可用"/"公开"才设 `true`
- key 一般不指定，让工具按 SKILL.md 自动取 key

装完子 agent 会回报 `@<skill_key>`，**告诉用户怎么用**：

> 已装好 `@vercel-react-best-practices`，现在你可以在对话里 `@vercel-react-best-practices` 调用它了。

## search.py 跑不通怎么办

少数极端场景下脚本会失败：

- 容器没装 `python3`（极罕见）
- 出网被防火墙截（skills.sh 抓不到 → stderr 显示"抓取 skills.sh 失败"）
- skills.sh 改了页面结构（leaderboard 解析为 0 条）

降级到 `bailian_web_search` MCP 工具（`DASHSCOPE_API_KEY` 配置时可用）：

1. 搜：`<query> agent skill SKILL.md github`、`skills.sh <query>`、`<query> claude skill site:github.com`
2. 拿搜索结果里的 GitHub raw `SKILL.md` URL 或 release zip URL
3. 这条路 LLM 幻觉率高，**装之前再问一次用户确认**

如果都没有，老实告诉用户："当前环境无法发现公开 skill，要不要我帮你写一个？"（写技能走 skill-management 子 agent：skill-creator 规范起草 + skill_save 落库）。

## 边界与禁忌

- **一次对话最多装 1 个 skill**。要装第二个先确认完上一个。
- **绝不用 `npx skills add` 安装**——见文档开头说明。
- **不要装短链 / IP 直连 / 看起来像 phishing 的 URL**。
- **装完不要主动改可见性**——`PATCH /visibility` 在前端有 UI，越权改容易出事。用户装完才反悔说"我要分享"，告诉他去前端改，不要再调一次工具。
- **遇到工具报错原话回给用户**，别加戏。`安装失败：HTTPError 403` 比"似乎遇到了一些问题"有用。

## 跟其它技能管理能力的关系

| 场景 | 用什么 |
|---|---|
| 公开生态有现成的 | 本 skill `search.py` 找 → 委派 skill-management 用 `skill_install(url=...)` 装 |
| 用户想把当前对话凝练成自己的 skill | sediment skill（最终 `skill_save` 落库） |
| 找了一圈生态里没有 → 想自己写 | 委派 skill-management 创建（skill-creator 规范起草 + `skill_save` 落库） |

## 工具契约

| 工具 / 脚本 | 用途 |
|---|---|
| `scripts/search.py <query> [--limit N] [--official-only] [--json]` | 在 skills.sh leaderboard 里搜，输出 markdown 表 / JSON |
| `scripts/show.py <source>/<skill_id>` | 取单条详情：description + 安装命令模板 |
| `task(subagent_type='skill-management')` → `skill_install(url, key?, is_public)` | 安装落库（从 URL 下载 zip 或 SKILL.md raw，走可见性体系） |
| `bailian_web_search` MCP | search.py 失败时的 fallback 检索 |

## 脚本实现要点（debug 用，正常忽略）

- `search.py` 抓 `https://skills.sh/` 主页，从内嵌的 RSC payload 里解析 600+ 条 leaderboard。skills.sh 的搜索是纯客户端的，server 不过滤——所以一次抓全表，本地 AND 匹配 + 按 installs 降序。
- `show.py` 取详情页 `<meta name="description">`，作为 og:description 抓一两句话简介。
- 两个脚本都只用 Python 标准库（`urllib`/`re`/`json`），任意 python3 都能跑，**不依赖 httpx 或项目内任何模块**——这意味着脚本本身是 portable 的，复制走也能用。
