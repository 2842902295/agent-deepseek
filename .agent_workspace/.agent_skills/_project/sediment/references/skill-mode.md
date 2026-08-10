# 模式 B：凝练为技能

把"这次对话里展现出的可复用工作方式"抽象成一个技能（agent_skill 记录）。技能由用户/系统在对话里显式 `@<key>` 触发；SKILL.md 主文件描述做什么、怎么做，附属文件（脚本/模板/参考资料）一并入库。

> 在用之前请先读过主 `SKILL.md` 的"共同心法"和"输出纪律"——这里只展开 B 模式的细节。

## 概念边界（必须分清）

- **技能 = 一条 DB 记录**：`skill_md`（SKILL.md 全文）+ 可选附属文件；落库唯一途径是 `skill_save` 工具。
- **SKILL.md 正文 = 做事的方法**：触发场景、固定 SQL/规则/清洗/流程、脚本怎么调、输出含义，都写在 SKILL.md 里。
- **不要往 workspace 写文件当交付物**：`.agent_skills/<key>/` 只是运行时缓存，不进 DB 的东西用户会丢。

## 工作流

1. **判断对话是否值得凝练**：用户做了一件**有泛化潜力**的事（不是一次性闲聊），才值得抽。如果对话本身就是杂谈、单次查问、或没有可复用工作方式，输出 `{"skill_key": null}` 表示不凝练。
2. **查重**：`skill_read(key)` 查目标 key 是否已存在；命中相关旧技能时优先更新（skill_save 传变更字段）而非新建。
3. **起草 SKILL.md**：
   - 开头 YAML frontmatter：`name` / `description`（必须，≤60 字，写清何时触发）。
   - 正文：用第二人称（"你"）写做事方法；不要泄露具体编号、个人数据、本次对话的偶然细节。能 1 句说清的别写 3 句。
   - 有可固化的脚本/模板时，作为附属文件一并准备（scripts/xxx.py、templates/xxx.md）。
4. **落库**：调 `skill_save(key, name, description, skill_md, files?)`。key 冲突时工具自动加 `-2/-3` 后缀——以工具返回的 `@<最终key>` 为准。
5. **报告**：输出 marker JSON。

## 写作要点

- `skill_key`：2~32 字、不含空白与 @ 符号；简短易懂，能让用户一眼看懂这是什么能力（例如"标准去重"、"会议纪要"）。
- `name`：显示名，可以和 skill_key 相同或更长。
- `description`：≤60 字，说清楚什么时候用。description 是系统选技能的主依据，要有触发线索（参考 skill-creator 的"description 优化"思路）。

## 输出格式

> ⚠️ **复杂凝练的高频失败点**：流程跑得越久（多轮工具调用、长 prompt、嵌套 frontmatter），越容易在最后忘了写 marker、或把 marker 写成口头报告替代物。后端只认 marker——**没 marker = 整个凝练失败 = 用户看到弹窗"未拿到结构化报告"**。哪怕你刚刚向用户口头汇报过了，**最后一条 AI 消息**还得**重新**输出一份 marker。

硬性约定：

1. marker 必须是**最后一条 AI 消息**的最后一段。前面的自然语言总结可有可无，但 marker 不能省。
2. 标签**严格小写、无空格**：`<sediment-report>` 和 `</sediment-report>`。
3. JSON 内的 `skill_key` 必须填 `skill_save` 工具返回文本里**实际出现**的 key（形如「技能 @xxx 已创建（id=..）」；key 冲突时工具会自动加 -2/-3 后缀，别用你最初建议的那个）。
4. JSON 必须合法：双引号、无尾逗号、字符串里的 `"` 要 `\"` 转义。

```
（可选的自然语言总结，告诉用户凝练出了什么）

<sediment-report>
{
  "type": "skill",
  "skill_key": "...",
  "name": "...",
  "description": "...",
  "has_files": true,
  "summary": "已凝练为技能「...」..."
}
</sediment-report>
```

对话不足以凝练时：

```
<sediment-report>
{"type": "skill", "skill_key": null, "summary": "本次对话不足以凝练为可复用技能：..."}
</sediment-report>
```

## 工具契约

> frontmatter `requires.tools` 是机器可读版；本表给人看。任一签名变化时，主 SKILL.md 的 `contract_version` 必须 +1。

| 工具 | 关键参数 | 用途 / 返回 |
|---|---|---|
| `skill_read(key)` | key: 技能 key | 查技能现状（存在与否、SKILL.md 与文件列表） |
| `skill_save(key, name?, description?, skill_md?, files?, is_public=False)` | skill_md: 完整 SKILL.md 全文（含 frontmatter）；files: `[{path, content}]` 附属文件 | 新建或更新技能并落库；新建返回「技能 @<key> 已创建（id=..）」 |

### 共用约束

- 工具从登录态取 user_id，agent 不能伪造别人的身份越权
- 创建出来的技能默认归当前用户私有（visibility=private），除非显式传 `is_public=true`
- builtin 来源的技能不可改动；命中时换一个 key 新建
