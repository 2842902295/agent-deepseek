---
name: sediment
description: 把刚刚的对话沉淀下来——可以是整理成知识条目落入个人知识库（库），也可以是把可复用的工作方式抽象成能力（capability，可选附带 skill 包）。当用户或系统说「整理进库」「整理进念念」「沉淀到库」「凝练为技能」「沉淀为技能」「记下来」「沉淀这次会话」「把这段记下来」「把刚才的留下」「沉淀成可复用能力」之类时使用。也用于库收件箱的自由文本整理（"把这段文本整理进库：xxx"）。重要:你拿到的对话历史就在 checkpoint 里，不需要外部传 transcript，凭记忆直接做整理。
type: project-bound
contract_version: 5
requires:
  tools:
    - task
    # kb_* 由 knowledge-base 子 agent 持有（经 task 委派，不直挂主 agent）
    - kb_search
    - kb_create
    - kb_update
    - kb_merge
    - kb_split
    - kb_delete
    - skill_read
    - skill_save
  models:
    - CHAT
---

# 沉淀（Sediment）

> **本 skill 是 project-bound**——直接调用本项目 qa_agent 运行时挂载的工具，无法跨项目移植。规范见 `_project/README.md`。

把刚才的对话凝练成长期价值。两种产物形态：**知识条目**（落入库）或 **能力 capability**（可复用工作方式，可选附带 skill 包）。共同心法相同，工作流分开放在 references/ 下，按需读。

## 路由：识别 mode → 读对应 reference

按指令措辞判断走哪个模式：

| 触发线索 | 模式 | 必读文件 |
|---|---|---|
| 整理进库 / 整理进念念 / 沉淀到库 / 记下来 / 沉淀这次会话 / 把这段记下来 / 收件箱自由文本 | **A：沉淀到库** | `references/kb-mode.md` |
| 凝练为技能 / 沉淀为技能 / 沉淀成 capability / 抽象成能力 / 做成可复用 skill | **B：凝练为技能** | `references/skill-mode.md` |

**确定 mode 后第一件事**：用 `read_file` 读对应的 reference 文件——里面有完整工作流、工具契约、输出 schema。**不要凭主 SKILL.md 的概要自行展开 mode 细节**，细节都在 reference 里。

如果指令同时含两类词（少见），优先按出现的第一个动词走；不确定就当模式 A。

## 共同心法（两个模式都适用）

理解这些原则比记规则重要：

### 看时序、辨最终结论

一段对话里 USER 和 ASSISTANT 都可能中途改主意——澄清需求、推翻假设、修正错误、补充约束。沉淀只留**最终达成的版本**。被覆盖的中间产物、走错的弯路、被纠正的错误结论，都不进库。

判定方法：当同一主题在对话里出现多次，按**消息时间先后**取最后一版；前面那些只用来理解上下文，不入库。

### 寒暄不沉淀

"好的"、"谢谢"、"我看看"、单纯问候和确认性回复都不是知识。如果一整段对话只有这些，candidates=0 / skill_key=null 直接报告"没什么可沉淀的"。

### 凭据是知识，要沉淀（但分开存）

服务器 IP、数据库连接信息、API key、账号密码、token 等**值得入库**——用户记不住、下次还要用。**不要**因为"是临时凭据 / 含密码"而判定不沉淀，那是误判。

但**不要混进脚本/工具/方法论条目**：方法论条目讲*怎么做*（保持泛化、可复用），凭据条目讲*连什么*（具体值、会变更）。两类条目各自独立成条，更新各管各的。具体拆分规则见 `references/kb-mode.md` 的"主题拆分纪律"。

### 一段对话往往能拆多条

不要硬塞成一条。如果对话横跨几个主题（聊了 A 又聊了 B），分别处理；每条候选都要先防重复（A：委派时要求 knowledge-base 子 agent 先搜后写；B：`skill_read`），再决定写入动作。

### 写之前先搜

入库 / 落技能前必须先查重——模式 A 在委派 description 里明确要求 knowledge-base 子 agent 先 kb_search 后写；模式 B 用 `skill_read`。命中相关旧条目时，**优先升级旧的而非新建**：旧条目重写得更好才是正确动作；只有真的是全新主题才新建。

### 解释 why，不要光罗列动作

每条 result 的 `note` 写为什么这么处理（为什么 merge、为什么 update、为什么 skip），不是动作的同义反复。这对将来回看历史、调优心法都重要。

## 输出纪律（两个模式都适用）

- marker 必须严格写成 `<sediment-report>...</sediment-report>`，全小写、无空格。后端用正则抠。
- JSON 必须是合法 JSON（双引号、无尾逗号）。
- marker 段之外的自然语言可以有；但 marker 段**只能出现一次**，且在最后。
- 任何模式下都要给出 `summary` 字段——后端用它弹 toast 给用户看。

## 失败兜底

- 工具调用失败、内容为空、不足以沉淀——别硬凑，老老实实在 summary 写明原因，candidates=0 / skill_key=null。
- 不要为了"看起来有产出"而创建空壳条目或意义不明的 capability。
