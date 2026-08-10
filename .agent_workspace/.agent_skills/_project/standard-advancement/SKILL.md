---
name: standard-advancement
description: >
  分析一篇**具体标准**的先进性。当用户输入标准编号（如 GB/T 1234）或标准名称，需要对该标准做先进性分析、评估技术水平、与国际/相关标准对比、挖掘相关专利布局和政策依据时，触发本 skill。
  触发词举例："分析 XX 标准的先进性"、"这个标准技术是否先进"、"帮我做先进性分析"、"标准先进性评估"、"XX 和国际标准差距"。
  本 skill 覆盖单篇标准的深度分析，与批量领域分析（先进性分析 skill）互补。
type: project-bound
contract_version: 3
requires:
  tools:
    - standard_query
    - get_standard_chapters
    - search_candidate_standards
  models:
    - CHAT
---

# 标准先进性分析

> **Project-bound skill**，依赖本项目的 qa_agent 工具集和可选的 WebSearch MCP。

## 整体结构

对一篇给定标准，从四个**独立视角**各自找出需要关注的问题点，最终汇总呈现。不打分，不评级。

| 视角 | 关注问题 | 详细规范 |
|---|---|---|
| A. 标准自身 | 这篇标准本身写得怎么样、时效如何 | `references/dim-a-self.md` |
| B. 相关标准对比 | 在同领域标准体系中处于什么位置 | `references/dim-b-standards.md` |
| C. 相关专利 | 背后的专利布局是否支撑其技术地位 | `references/dim-c-patents.md` |
| D. 相关政策 | 政策层面对这篇标准的支撑与要求 | `references/dim-d-policy.md` |

每个视角的**检查要点、数据采集方式、报告结构**均在各自的 reference 文件中定义。执行时必须读取对应文件，不能靠主文件的概要自行展开。

---

## 工作流

### Step 0 — 解析输入

从用户输入提取：
- **标准编号**（如 `GB/T 38661`）：直接用于 DB 查询
- **标准名称**（如"电动汽车用电池管理系统"）：先 `search_candidate_standards` 定位标准号再继续
- 两者都没有：请用户补充

告知用户将从四个独立视角检查，每个视角列出需要关注的问题点。

---

### Step 1 — 获取标准基础信息（所有视角共享）

执行一次，结果供四个视角复用：

```sql
SELECT standard_no, cname, ename, std_nature, std_type, state,
       issue_date, act_date, annul_date,
       lead_unit, draft_unit_main, draft_unit, chief_unit,
       adopt_type, adopt_no, adopt_level, adopt_name,
       replace_stds, target_stds, replace_description,
       industry, std_domain, intl_cat, use_range
FROM standard_base_info
WHERE standard_no = '目标标准号' AND deleted = 0
LIMIT 1
```

若 DB 无结果，后续所有视角均切换为 WebSearch 模式，报告顶部注明"标准库中未找到，基于网络公开信息"。

---

### Step 2 — 并行执行四个视角分析

读取各视角的 reference 文件后，按文件中的指引采集数据、评分：

- 读 `references/dim-a-self.md` → 执行视角 A 分析
- 读 `references/dim-b-standards.md` → 执行视角 B 分析
- 读 `references/dim-c-patents.md` → 执行视角 C 分析
- 读 `references/dim-d-policy.md` → 执行视角 D 分析

每个视角独立输出需要关注的问题点列表。若某视角无问题，注明"无明显关注点"。

---

### Step 3 — 汇总报告

```
## 【标准先进性分析报告】
标准：{编号} {中文名称}
分析日期：{date}

### 一、标准概况
（状态、时效、起草机构、采标情况一句话摘要）

---

### A. 标准自身
（按 dim-a-self.md 报告结构输出关注点）

### B. 相关标准对比
（按 dim-b-standards.md 报告结构输出关注点）

### C. 专利支撑
（按 dim-c-patents.md 报告结构输出关注点；数据来源受限时注明）

### D. 政策支撑
（按 dim-d-policy.md 报告结构输出关注点；数据来源受限时注明）

---

### 汇总关注点
（整合四视角，列出所有需要关注的问题点，注明来自哪个视角）
```

---

## 工具契约

| 工具 | 用途 |
|---|---|
| `standard_query` | 查 `standard_base_info` 元数据；查相关标准列表 |
| `get_standard_chapters` | 读标准正文目录和关键章节（视角 A 用） |
| `search_candidate_standards` | 搜索领域相关标准（视角 B 用） |
| WebSearch（MCP，可选） | 专利检索（视角 C）、政策检索（视角 D）；不可用时降级 |

**通用约束：**
- 所有 `standard_query` 必须含 `LIMIT`
- WebSearch 结果引用时注明"来源：网络搜索"
- WebSearch 不可用时：视角 C/D 在报告中注明"数据来源受限，评分仅供参考"，分数单独标注置信度
