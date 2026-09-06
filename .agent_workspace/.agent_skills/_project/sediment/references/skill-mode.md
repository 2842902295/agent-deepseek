# 模式 B：凝练为技能

把"这次对话里展现出的可复用工作方式"抽象成一个技能（agent_skill 记录）。技能由用户在对话里 `@<key>` 触发；SKILL.md 主文件描述做什么、怎么做，附属文件（脚本/模板）一并入库。

> 在用之前请先读过主 `SKILL.md` 的"共同心法"和"输出纪律"——这里只展开 B 模式的细节。

## 概念边界

- **技能 = 一条 DB 记录**：`skill_md`（SKILL.md 全文）+ 可选附属文件；落库唯一途径是 `skill_save` 工具。
- **SKILL.md 正文 = 做事的方法**：触发场景、固定流程/规则、脚本怎么调、输出含义。
- **不要把技能写成 workspace 里的文件当交付物**——不进 DB 的东西用户会丢。

## 工作流

1. **判断是否值得凝练**：用户做了有泛化潜力的事才值得抽；杂谈、单次查问、纯问答 → 直接输出 `{"skill_key": null}` 的 marker，说明原因。
2. **查重**：`skill_read(key)` 查同类技能是否已存在；命中相关旧技能优先更新（skill_save 传该技能现有 key）而非新建。
3. **起草 SKILL.md**：
   - 开头 YAML frontmatter：`name` / `description`（必须，≤60 字，写清何时触发——description 是系统选技能的主依据）。
   - 正文：用第二人称写做事方法；泛化可复用，不泄露本次对话的偶然细节与凭据数据。能 1 句说清的别写 3 句。
   - 有可固化的脚本/模板时作为附属文件（`files` 参数，`[{path, content}]`）。
4. **落库**：调 `skill_save`。key 传技能名即可——正式 key 由平台自动生成（「技能名_专属code」），**以工具返回文本里的 @key 为准**。新建时尽量一并给 `category`（从工具参数说明的分类词表选）。
5. **报告**：输出 marker JSON。

## 工具契约

> frontmatter `requires.tools` 是机器可读版；本表给人看。任一签名变化时，主 SKILL.md 的 `contract_version` 必须 +1。

| 工具 | 关键参数 | 用途 / 返回 |
|---|---|---|
| `skill_read(key, file_path?)` | key: 技能 key | 查技能现状（存在与否、SKILL.md 与文件列表） |
| `skill_save(key, name?, description?, skill_md?, files?, category?, icon?)` | key: 新建时仅作名称参考（平台自动按「技能名_专属code」生成正式 key），更新时传现有 key；skill_md: 完整 SKILL.md 全文（含 frontmatter）；files: `[{path, content}]`（workspace 现有二进制文件用 `{path, workspace_path}`） | 新建或更新技能并落库；新建返回「技能 @<最终key> 已创建（id=..）」 |

### 共用约束

- 工具从登录态取 user_id，不能伪造他人身份越权
- 创建的技能默认未上架（仅本人可见可用）；`is_enabled` 上下架参数仅管理员可传，凝练场景**不要传**
- builtin 来源的技能不可改动；命中时换一个名字新建

## 输出格式

硬性约定：

1. marker 必须是**最后一条 AI 消息**的最后一段；前面可有自然语言总结，marker 不能省——后端只认 marker，缺失即整体失败。
2. 标签严格小写无空格：`<sediment-report>` / `</sediment-report>`。
3. `skill_key` 必须填 `skill_save` 返回文本里**实际出现**的 key（平台自动生成的那个，不是你最初起的名字）。
4. JSON 必须合法：双引号、无尾逗号、字符串里的 `"` 要转义。

```
（可选的自然语言总结）

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
