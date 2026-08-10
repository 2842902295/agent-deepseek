---
name: standards-db
description: 标准查询与正文阅读规范，处理任何标准相关任务前必读。适用：标准元数据查询（按编号/名称/起草单位/归口单位/行业/状态等）、标准统计分析、标准正文章节读取（含表格/公式/图片）、标准与章节的语义搜索定位。含 standard_base_info 全字段、get_standard_chapters 使用策略、media 图片展示流程。
---

# Standards DB — 标准查询与正文阅读规范

两类任务：**问数**（查元数据、做统计）和**正文查阅**（读章节、找内容）。

## 场景一：问数

**唯一数据源：`standard_base_info` 表**——所有标准元数据查询必须且只能从此表获取，禁止查 `standard_cache_*` 或其他表。

字段已内置如下，**禁止对这张表再调 standard_tables / standard_schema / read_skill**，直接写 SQL。

### `standard_base_info` 全字段（主键 `id`，查询键 `standard_no`）

**基本信息**

| 字段 | 含义 |
|---|---|
| `standard_no` | 标准编号 |
| `cname` | 标准名称 |
| `ename` | 标准英文名称 |
| `std_nature` | 标准性质（强制性/推荐性） |
| `std_type` | 标准类型（国家标准/行业标准/团体标准） |
| `state` | 标准状态（现行/废止/在研） |
| `std_year` | 标准年份 |
| `std_obj` | 标准对象 |
| `use_range` | 适用范围 |
| `is_secret` | 是否涉密 |
| `security_level` | 密级 |
| `mandatory_clause` | 强制性条款 |
| `patent_info` | 专利信息 |
| `remark` | 备注 |

**日期**

| 字段 | 含义 |
|---|---|
| `issue_date` | 发布日期 |
| `act_date` | 实施日期 |
| `annul_date` | 废止日期 |

**归口与起草**

| 字段 | 含义 |
|---|---|
| `lead_unit` | 归口单位 |
| `approval_unit` | 批准单位 |
| `put_unit` | 提出单位 |
| `draft_unit_main` | 主要起草单位 |
| `draft_unit` | 起草单位 |
| `draft_staff` | 起草人 |
| `chief_unit` | 主管部门 |
| `mgr_dept` | 行业管理部门 |

**分类与领域**

| 字段 | 含义 |
|---|---|
| `intl_cat` | 国际分类号（ICS） |
| `nat_cat` | 国内分类号（CCS） |
| `std_domain` | 标准领域 |
| `std_field` | 标准领域分类 |
| `industry` | 所属行业 |

**替代关系**

| 字段 | 含义 |
|---|---|
| `replace_stds` | 本标准替代的旧标准 |
| `target_stds` | 替代本标准的新标准 |
| `replace_description` | 替代说明 |
| `release_history` | 发布历史 |
| `release_std_no` | 发布标准号 |

**采标信息**

| 字段 | 含义 |
|---|---|
| `adopt_situation` | 采用情况 |
| `adopt_std_no` | 采用标准号（对应国际标准号） |
| `adopt_name` | 采用标准名称 |
| `adopt_level` | 采用程度（等同/修改/非等效） |
| `adopt_type` | 采用方式 |
| `adopt_no` | 采用编号 |
| `adopt_text` | 采用说明 |
| `gjb_no` | 国军标编号 |

**系统字段**

| 字段 | 含义 |
|---|---|
| `id` | 主键（MD5） |
| `deleted` | 软删除标记（查询有效数据须带 `deleted = 0`） |
| `creator` / `updater` | 创建人 / 更新人 |

### 常见查询模式

- 按编号精确查：`WHERE standard_no = 'GB/T XXXX-YYYY'`
- 模糊找标准：先用 `vector_search_standards_ob(query)` 语义召回，再用编号精确取详情
- 统计分析：SQL GROUP BY + COUNT，结合 `LIMIT`；**统计结果必须调用 `create_chart` 生成图表**（bar/pie/line），并将工具返回的 ` ```chart ... ``` ` 文本**原文完整复制**到回复正文中，不得省略

### SQL 纪律

- 所有 SQL 必须带 `LIMIT`
- 元数据只查 `standard_base_info`，并加 `deleted = 0` 过滤软删除；`standard_cache_*` 系列表禁止查询
- 统计正文情况（哪些标准有正文、章节数量等）可直查 `standard_jgh_pdf` / `standard_jgh_pdf_chapter`，两表用 `main_task_id` 关联（先按 standard_no 在 standard_jgh_pdf 取 main_task_id）；读取正文内容本身走场景二

## 场景二：正文查阅

用户要看某标准正文内容时，**怎么快怎么来，能一步到位就不要分两步**：

1. **用户点名了章节**（说出章节号或章节名，如"第4章"、"范围"、"规范性引用文件"）：
   直接 `get_standard_chapters(standard_no, title_no_prefix=...)` 一步取回正文，**不要先取目录**；
   `title_no_prefix` 章节号和章节名都接受，多章用逗号分隔（如 `"4,5,6"` 或 `"范围,规范性引用文件"`）。
   若未命中，返回结果会自动附带完整目录（toc 字段），据此改选后重试即可，不要再单独调 toc_only。
2. **用户没点名章节**：先 `get_standard_chapters(standard_no, toc_only=True)` 拿目录，了解章节结构和各章字数，再用 `title_no_prefix` 定向获取正文。
3. 章节内的表格/公式/图片：`get_standard_chapters` 默认已随正文一并还原、附在各章 `media` 字段——table/formula 条目的 `content` 是解析好的 HTML/LaTeX，**就是数据本身，直接用**；`path` 是截图/插图，仅在展示给用户时用。**不要再额外调工具**；遇到"见表X"这类跨章引用，直接 `keyword="表X"` 读取该表所在章节即可。
4. 用户问题模糊（未指定章节号、从目录也难判断）时，先用 `vector_search_chapters(query, scope_standard_nos=standard_no)` 语义定位章节，再取正文。
5. 数据库无正文数据（get_standard_chapters 返回空）时，告知用户该标准暂无电子正文。

## 图片展示（重要）

章节内容中经常包含图片（示意图、原理图、结构图等），这些图片**必须展示出来**，不能只描述。

**图片登记步骤（每张需要展示的图都要做）：**
1. 在章节 `media` 字段里找到对应条目的 `path`（如 `images/xxx.jpg`）：`type == "image"` 条目是插图，table/formula 条目的 path 是截图
2. 立即调 `register_artifact(name="图X-标题.jpg", artifact_type="image", relative_path="images/xxx.jpg")`
3. 在回答正文中对应位置插入 `[artifact:<ID>]` 占位符（独占一行，前后空行）

注意：`path` 字段已是 workspace 内的相对路径，直接传给 `register_artifact` 的 `relative_path` 参数。

回答时直接呈现章节内容，**不要把整个 word 字段原文抛出**，要提炼关键信息后组织回答，图片按位置穿插展示。

## 图片/表格阅读纪律

- `media` 里 table/formula 条目的 `content` 是解析好的 HTML/LaTeX，**就是数据本身，直接读文本取值**；`path` 截图仅用于展示给用户，不要读图取数
- `type=image` 插图条目：按系统提示中「看图」章节的方式查看（原生视觉模型能直接看到已内联在工具结果里的图片；纯文本模型调 `vision_inspect(file, question)`，file 用 workspace 相对路径）
- **严禁**用 `read_file` 读取图片文件（文本模型看不见，base64 还会永久占用大量上下文）；**严禁**用 shell（python / PIL / pytesseract 等）对图片做 OCR 或解码尝试，第一次失败就停止
- 图片占用上下文：只查看确实需要看的图，并把关键信息及时提炼成文字
