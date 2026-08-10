# 模式 A：沉淀到库

把对话里值得长期保留的事实/结论/方法论整理成知识条目，落入个人知识库（库）。

> 在用之前请先读过主 `SKILL.md` 的"共同心法"和"输出纪律"——这里只展开 A 模式的细节。
> 库的读写工具（kb_*）挂在 `knowledge-base` 子 agent 上——本模式不直接接触这些工具，落库一律通过 `task(subagent_type='knowledge-base')` 委派。

## 分工（重要）

- **你（主 agent）负责初步整理**：从对话记忆里提炼候选主题、整理每条的正文素材、识别产物 ID、起草 tags。**素材必须完整**——子 agent 看不到对话，内容只能来自你传的 description。
- **子 agent 负责怎么入库**：查重、动作选择（update / merge / split / delete / create）、与旧条目融合重写、结构组织、标签定稿，都由它自主决策。你传的条目形态只是初稿，不必要求它照搬；除"产物引用必须保留"外，不要微管理它的写入方式。

## 工作流

1. **盘点候选**：根据对话内容，列出 N 条候选主题（脑子里列就行，不用调工具）。每条想清楚标题、什么类型、属于谁。先按下面"主题拆分纪律"检查一下，看看一段对话该被拆成几条。随后为每条候选起草数据：title、content 全文素材、entry_type、tags 初稿、summary（字段语义见"五种条目类型"）。
2. **检查产物**：扫一遍对话里 ASSISTANT 的回复，看有没有 `register_artifact` 工具调用及其返回的 `artifact#<ID>`。如果有，记住这些 ID——后面的知识条目**必须引用它们**（写进委派 payload，见"产物保留"）。
3. **委派落库**：一次 `task(subagent_type='knowledge-base')` 处理全部候选。description 里写清：
   - 每条候选的完整素材：title、content（**全文，不许省略**）、entry_type、tags 初稿、summary、due_at / primary_artifact_id 等类型字段
   - 产物引用要求：primary_artifact_id 与 content 中的 `[artifact:<ID>]` 占位符必须保留
   - 说明决策权归它：查重、动作选择、与旧条目融合、结构调整、标签定稿由它自主判断；完成后逐条回报 action + entry_id + title + note
   **严禁**只写"把刚才的内容存进去"——子 agent 看不到对话，素材不传全等于沉淀失败。
4. **报告**：按子 agent 回报的每条实际动作组织总结，并在最后追加 marker 包裹的结构化 JSON（见"输出格式"）。子 agent 报 skipped（命中旧条目且无新增信息）的，results 里如实记 skipped。

## ⚠️ 产物保留（强制规则，违反即沉淀失败）

> **对话中 ASSISTANT 生成的所有产物（HTML 页面、图表、图片、文件等），在沉淀时必须保留引用。丢掉产物等于丢掉了对话最核心的产出。**

### 如何识别对话中的产物

扫描对话历史里 ASSISTANT 消息中的：
- `已登记 artifact#<ID>` — 来自 `register_artifact` 工具的返回
- `[artifact:<ID>]` — ASSISTANT 在回复正文中插入的产物占位符

把找到的所有 artifact ID 记下来。

### 如何在知识条目中保留产物

交给子 agent 的条目素材里，**必须同时做到以下两点**（并在 description 里明确要求保留）：

1. **`primary_artifact_id` 字段**：条目数据中带上 `primary_artifact_id=<ID>`（取对话中最重要的那个产物）
2. **content 中嵌入占位符**：在正文的合适位置（通常在描述段落之后）单独写一行 `[artifact:<ID>]`，前后各留一个空行

**错误示范（禁止）**：
```
content: "## 项目概述\n用户要求制作一个小游戏...\n产物信息\n文件：game.html（约 26KB）\nartifact ID: 7000136"
```
↑ 纯文字描述产物，前端无法渲染。

**正确示范**：
```
content: "## 项目概述\n用户要求制作一个武侠小游戏，水墨暗色调视觉风格。\n\n[artifact:7000136]\n\n## 操作说明\n- WASD 移动\n- J 轻攻击\n- K 重攻击"
primary_artifact_id: 7000136
```
↑ 前端会在此处内嵌渲染 HTML 游戏。

### 多条产物

如果对话中有多个产物（比如一张图 + 一个 HTML），全都要保留：
- `primary_artifact_id` 放最重要的那个
- content 中为每个产物各写一行 `[artifact:<ID>]`

### 没有产物的对话

如果对话里确实没有 `register_artifact` 或 `[artifact:ID]`，正常写 markdown 即可。但**只要对话中有产物，就必须引用**——不能只写文字描述。

## 主题拆分纪律

> 同一段对话里出现"方法论 + 凭据/具体值"是常态，必须**拆成两条独立条目**，不要混。

判定要点：

- **方法论条目**（脚本、工具、SOP、SQL 模板）——回答"怎么做"。保持泛化、参数化、可复用，不写具体 IP/账号/密码/token。命中旧条目时优先 update 而非 create。
- **凭据/连接信息条目**（服务器、数据库、API、账号）——回答"连什么 / 是什么具体值"。每个独立环境/服务一条，title 形如「XX 服务器连接信息」「XX 数据库凭据」。新增机器/凭据轮换时是 update 这条凭据条目，不去动方法论条目。

常见误区（务必避免）：

- ❌ "这次只是换了 IP 和密码，已有脚本条目覆盖了，无需更新" —— 错。方法论条目本来就不该写具体 IP/密码；新的服务器凭据应该独立成条入库。
- ❌ 把凭据塞进方法论条目的"使用示例"里 —— 错。凭据会变，会污染方法论条目。
- ❌ "含密码的内容是临时凭据，不入库" —— 错。用户记不住才需要入库；库是个人知识库不是公开发布渠道。

## 标签初稿（你起草，子 agent 定稿）

> 标签是**目录维度**——回答"这条该归到哪一类，让我以后能从这一类找回它"。不是描述维度。

你起草 tags 时遵守：

- 优先**对象 / 主题**标签（`#客户管理系统` `#服务器凭据`），避开动作（`#调研`）/ 状态（`#待办`）/ 描述（`#长文`）/ 时间（`#今日`）
- 每条通常 **1~3 个**，最多 5 个
- 发现本次候选与近期某条老条目讲的是同一对象（哪怕字面没明说），起草同一个标签，并在 description 里提示子 agent 给缺该标签的老条目补 tags

canonical 形态沿用、同义词归一、补标签串联由子 agent 在写库时用 kb_search 校验执行——你拿不准库里已有什么形态时，直接用你觉得最合理的，它会纠偏。

## 五种条目类型

| 类型 | 字段语义 |
|---|---|
| **knowledge** 知识 | 长文。`content` 必填（markdown），可有 `summary`。 |
| **idea** 灵感 | 短文。`content` 必填（一两句），不写 summary。 |
| **todo** 待办 | 重点是时间。`due_at` 必填**毫秒时间戳**（用户说"明天 18:00"自己换算成绝对毫秒）；`todo_status` 默认 pending；content 写补充描述，title 写任务一句话。 |
| **file** 文件 | 以一份文件为主体。`primary_artifact_id` 必填（附件列表里非图类文件的 id）；**content 不允许写**；title=文件展示名，summary=一句话用途。 |
| **diagram** 图 | 以一张图为主体。`primary_artifact_id` 必填（附件里 artifactType=excalidraw 的那条 id）；如有同名 SVG 把它的 id 填到 `svg_artifact_id`；**content 不允许写**；title=图主题，summary=一句话总结。 |

判别优先级：附件含 excalidraw → diagram；附件含非图文件且条目主体就是它 → file；语境含"截止/由谁完成/提醒我" → todo；一两句灵光 → idea；其余有正文价值的 → knowledge。

## 输出格式

最后一段必须是 marker 包裹的 JSON，前后可以有自然语言对用户说人话：

```
（可选的自然语言总结，给用户看）

<sediment-report>
{
  "type": "knowledge",
  "candidates": 3,
  "summary": "已记下 3 条：……",
  "results": [
    {"action": "created", "entry_id": "kb_xxx", "title": "...", "entry_type": "knowledge", "tags": [...], "note": "全新主题，无相关旧条目"},
    {"action": "updated", "entry_id": "kb_yyy", "title": "...", "note": "重写整段以融合新增结论"},
    {"action": "skipped", "title": "...", "note": "命中已有条目且无新增信息"}
  ]
}
</sediment-report>
```

`action` 取值：`created` / `updated` / `merged` / `split` / `deleted` / `skipped`——以子 agent 回报的实际动作为准，不要自行美化。

## 委派契约

> frontmatter `requires.tools` 是机器可读版；本节给人看。契约变化时，主 SKILL.md 的 `contract_version` 必须 +1。

| 项 | 说明 |
|---|---|
| 委派方式 | `task(subagent_type='knowledge-base')`，一次委派带上全部候选，description 写全素材与要求 |
| 主 agent 职责 | 从对话记忆提炼候选（素材全文、拆分、产物 ID、tags 初稿），组装 payload，按回报组装 `<sediment-report>` |
| 子 agent 职责与决策权 | kb_search 查重、动作选择（update > append > merge > split > delete > create）、与旧条目融合重写、结构组织、标签定稿与补标签、执行写库 |
| 返回 | 逐条 action + entry_id + title + note；失败原因如实回报 |

### 共用约束

- 所有 kb_* 工具通过闭包绑定 user_id，子 agent 不能伪造别人的 user_id 越权
- 附件 id（primary_artifact_id / svg_artifact_id）必须用对话上下文里 register_artifact 给出过的真实 id
