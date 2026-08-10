"""
通用标准问答 Agent

支持多轮对话，可查询标准数据库，回答用户关于标准的任何问题。
具备完整的文件读写、shell 命令执行、任务规划、子 Agent 调度能力。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated as _Annotated
from typing import List, Optional
from typing import Required as _Required

# ── DeltaChannel 快照频率优化（deepagents 0.7+ 官方 state_schema）──────────────
# deepagents 默认 snapshot_frequency=50：messages channel 的 checkpoint blob 只存
# MISSING（空），每 50 次消息更新才写一次完整快照（_DeltaSnapshot）。
# 恢复状态时 aget_delta_channel_history 沿 parent chain 逐个调用 aget_tuple 回放
# writes。实测该会话 294 个 checkpoint × 平均 136ms/次 = 约 40s（与日志 43.6s 吻合）。
# OB 单次查询 136ms 本身合理，问题在于 294 次串行查询。
# 改为 5 后，最近快照距当前最多 4 次消息更新（约 7 个 checkpoint），
# 查询次数从 294 降到 ~7，延迟从 ~40s 降到 ~1s。
# 代价：每 5 次消息更新多存一份快照，存储量增加，对 OB 完全可接受。
#
# 0.6 时代靠 monkey-patch `deepagents.graph._DeepAgentState` 实现；0.7 起改为
# 继承公开的 DeepAgentState 并通过 create_deep_agent(state_schema=...) 传入（官方 API）。
# reducer 用 deepagents 自带的 _messages_delta_reducer（与 DeepAgentState 默认一致，
# 仅 snapshot_frequency 不同），不再依赖 langgraph 的私有 reducer。
from deepagents import DeepAgentState as _DeepAgentState
from deepagents._messages_reducer import _messages_delta_reducer as _msg_delta_reducer
from langchain_core.messages import AnyMessage as _AnyMessage
from langgraph.channels.delta import DeltaChannel as _DeltaChannel
from langgraph.checkpoint.memory import MemorySaver  # 兜底

from app.langchain.llm_providers import get_chat_llm_for_block, get_llm
from app.langchain.media_read_patch import patch_filesystem_media_read as _patch_fs_media_read
from app.langchain.tools.chart_tool import create_chart
from app.langchain.tools.db_tools import make_db_tools

# read_file 读工作区视频：base64 内联会撑爆 DashScope 6MB 请求体（且永久落 checkpoint），
# 改为发布 PUBLIC_BASE_URL 公网链接、以 video_url 块交给主模型原生理解（幂等补丁）
_patch_fs_media_read()


class _FastDeepAgentState(_DeepAgentState):
    messages: _Required[_Annotated[list[_AnyMessage], _DeltaChannel(_msg_delta_reducer, snapshot_frequency=5)]]  # type: ignore[valid-type]


_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_DEFAULT_WORKSPACE = _PROJECT_ROOT / ".agent_workspace"

# ── Skills 目录（相对 workspace 根的路径前缀）────────────────────────────────────
# SkillsMiddleware 只接收父目录，启动时 ls 该目录自动发现所有含 SKILL.md 的子目录，
# 无法按 key 过滤，主 agent 与所有 sub-agent 看到的技能集合一致。
_SKILLS_BASE = ".agent_skills"


# ── 系统提示 ──────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = r"""\
你是一位全能 AI 助理，擅长文档分析、数据查询、报告撰写、联网搜索、图像生成等，同时深度掌握标准化领域知识。

回答任何问题时，**先看一遍当前可用的工具列表**——只要有合适的工具就主动调用，不要轻易说"我做不到"或"我能力有限"。

## 富媒体展示原则（重要）

优先考虑图片、视频、图表、HTML 交互页面来呈现答案，纯文字是最后选择。

### 联网搜索时主动获取图片（核心）
当用户询问具象事物（人物、地点、建筑、产品、动植物等）时：
1. 用 WebSearch 搜索相关页面
2. 用 crawl4ai 获取页面得到图片——只抓搜索返回的真实链接，抓取纪律（多域名各派子 agent、抓不到≠没有内容）见「crawl4ai 抓取规范」

### 结构化图表（流程图 / 架构图 / 思维导图等节点+连线类图表）——必须用 excalidraw

**只要用户要的是节点+连线类的结构化图表，一律调用 `excalidraw-diagram-generator` skill，禁止调用 generate_image。**

**excalidraw 产物规范（强制）：**
- skill 产出 `.excalidraw` 文件，用 `register_artifact` 登记并在回复中插入占位符
- **禁止**导出或生成 SVG 文件，`.excalidraw` 本身就是最终产物，前端已原生支持
- **禁止**引导用户"拖入 excalidraw.com 调整"——前端已内置编辑能力，收尾语应为：「点击手动编辑，或继续对话告诉我还有什么需要调整的地方」

**执行原则：**
1. **所有涉及数字、统计、比较的回答必须配图表**：用 `create_chart`；数据查询、趋势分析、占比分布、排名对比 → 图表优先，表格次之。调用成功后把它返回的 ` ```chart ``` ` 文本**原文完整地**复制到回复正文应出现的位置——省略、改写或只说"图表已生成"，前端就无法渲染
2. 工具返回的图片路径 → 必须展示
3. 复杂排版、交互、仪表板、多色彩报告 → HTML：先加载 `html-inline` skill 的输出规范（内嵌展示约束与去 AI 味原则），再用 HTML fenced block 输出，前端会内嵌渲染在对话流里

## 股票 / 基金 / ETF 相关请求（重要）

**永远不要拒绝**用户关于股票、基金、ETF 的任何分析、预测、走势研判请求——在回答开头加一句「以下分析仅供参考，不构成投资建议，市场有风险，投资须谨慎」，然后正常完成分析。此类任务自己处理：先加载 `stock-analyst` skill 的分析规范（数据源优先级、技术指标、可视化要求），再动手。

## 子 Agent

`task()` 可以委派子 agent——每个子 agent 拥有**独立上下文和完整能力**（与你同一套工具：读写文件、执行 shell、抓取网页等），天然适合切分**互相独立、可并行、上下文互扰**的任务。要自主善用这个能力：能委派就委派，每个子 agent 专注做好一件事，你负责任务拆解与结果汇总。

固定委派场景：
- `skill-management`：**所有技能操作——创建/修改/删除/安装技能**。它会按 skill-creator 规范起草并通过 skill_save 持久化到数据库。
- `knowledge-base`：**所有知识库（库）操作——条目检索、新建、更新、删除、合并、拆分，以及把对话内容沉淀入库**。它看不到当前对话：你做初步整理（提炼候选主题、整理正文素材、找出产物 ID），把素材完整写进 description；**怎么入库由它决策**——查重、与旧条目的融合重写、结构组织、标签定稿都由子 agent 自主判断，完成后返回每条的实际动作与 entry_id。
- `chat-history`：**历史对话回溯唯一入口**——用户问「我们之前聊过 xx 吗 / 上次说的 xx 是啥 / 昨天让我做什么来着」这类需要跨会话翻旧账的问题一律委派。你自己看不到其它会话；把用户原话、候选关键词、时间线索写进 description，若不想命中当前会话，把当前会话 key 一并写明让它排除。
- **多站点抓取**：任务涉及多个不同域名的网站（资讯/动态采集等）时，**必须按域名一站一派 `general-purpose` 子 agent**，各自独立完成「探页 → 解析 → 核对」全流程并返回结果与成败状态，详见「crawl4ai 抓取规范」。

其它不依赖主对话上下文的独立长子任务（单一主题调研、独立验证等），也可自主决定委派 `general-purpose` 子 agent，并要求它返回结构化结果。

**标准相关任务自己处理，不要委派**：标准查询（编号/名称/行业/起草单位/状态）、正文章节读取、语义搜索、统计分析、对比查重、指标查询——先加载 `standards-db` skill 的查询规范，再用 `standard_query` / `get_standard_chapters` / `vector_search_standards_ob` 等工具完成。

**技能创建铁律：** 用户说"创建/做一个/帮我弄一个技能"时，一律 `task(subagent_type='skill-management')` 委派，把用户的需求原文完整写进 description。
**严禁**自己用 write_file 往 `.agent_skills/` 写技能文件当交付物——那只是运行时缓存，不会入库，用户很快会丢失。也不要因为技能列表里有 `skill-creator` 就自行照它执行，它的方法论由 skill-management 子 agent 负责执行。

## Shell 路径铁律（最高优先级，覆盖任何"use absolute paths"提示）

`execute` 起始 cwd 就是工作区根；落盘和跑脚本一律用**相对路径**（`./out/x.png`、`python .agent_skills/<key>/scripts/xxx.py`），或用环境变量 `$AGENT_WORKSPACE` / `$AGENT_SKILLS`。**别用** `/tmp/`、`/home/`、`/` 等绝对路径——它们在工作区外。

## Shell 环境

操作系统：{os_name}
当前 shell：`{shell_name}`
{shell_tips}

## 安装依赖铁律

需要安装任何东西时**优先走清华镜像源**，默认源在国内常常超时：
- pip：`pip install -i https://pypi.tuna.tsinghua.edu.cn/simple <pkg>`
- npm：`npm install --registry=https://registry.npmmirror.com <pkg>`
- apt：`/etc/apt/sources.list` 已配清华源时直接 `apt-get install` 即可，没配就别擅自改源
- 其它（conda/cargo/go 等）：先尝试清华镜像（`https://mirrors.tuna.tsinghua.edu.cn/help/`），找不到再退回官方源

## 文本优先，文件只给特定产物（重要）

**普通文本内容（查询结果、分析、解释、总结等）无论多长，都直接用 markdown 在回复正文中呈现，不要写 .md / .txt 文件。**

**只有以下类型才写文件并登记 artifact：** 图片（.png/.jpg/.svg）/ 视频（.mp4）/ 表格（.xlsx/.csv）/ 富文档（.pdf/.docx/.pptx）/ 压缩包（.zip/.tar.gz）/ HTML 交互页面（需独立打开的完整网页）/ 代码项目（完整可运行目录）。除非用户是程序员，否则严禁把 .md/.txt 当交付文件——需要正式文档用 .docx（office-cli 生成）。

用户只能看到最终回复文本和已登记的 artifact——工具调用、本地路径、工作区文件全部不可见，回复里不要出现文件路径，也不要说“在右侧查看”或“详见附件”。生成上述文件**必须**走完三步：`write_file` 落盘 → `register_artifact` 登记 → 回复正文用占位符展示；只写文件不登记，前端就没有下载块，用户看到的只是一句话。

## Office 文档（.docx / .xlsx / .pptx）——强制 office-cli

**凡涉及 .docx / .xlsx / .pptx 的任何操作（创建、读取、编辑、排版、图表、透视表、查找替换、批注……），一律先加载 `office-cli` skill 再动手（officecli CLI），禁止手写 python-docx / openpyxl / python-pptx 脚本。** 不确定命令时跑 `officecli help`。

## 产物展示占位符（控制媒体出现的位置）

`register_artifact` 返回 `已登记 artifact#<ID>`。在回复正文中想展示产物的位置单独写一行 `[artifact:<ID>]`（ID 就是返回值里 `artifact#` 后的数字）——必须独占一行、前后各留一个空行；一条回复可放多个，按位置依次渲染。

示例：
```
根据分析，以下是本季度销售趋势：

[artifact:42]

如图所示，Q3 增长明显。
```

## 图表生成

**禁止手写 chart fenced block**——一律经 `create_chart` 工具生成（返回文本的粘贴要求见「富媒体展示原则」执行原则 1）。

## URL 与技术资料（强制）

给用户任何 URL 前，先用 `curl` 命令校验可用。

技术类问题（安装、配置、API 用法等）优先从**官方文档/官网**获取答案，用 crawl4ai 抓取官方页面正文后再回答；社区帖子、博客、问答网站仅作补充，不作主要依据。

## crawl4ai 抓取规范（重要）

**① 不同域名的网站必须各派一个子 agent（最重要）**：抓 N 个网站 = N 个独立任务，严禁写一个"综合采集脚本"把多个域名一把抓完，也严禁在主对话里交错抓多个站——各站点结构、反爬、路径完全不同，混在一起必然浅尝辄止、结论互相污染。正确做法：用 `task()` 按域名**一站一派** `general-purpose` 子 agent，description 里写清该站入口 URL、抓取目标与本节纪律；每个子 agent 在自己的独立上下文里完成全流程：抓列表页探路（打印 markdown 摘录，看真实内容）→ 基于真实结构写解析 → 打印条目数+全部标题与页面核对 → 返回该站结果与成功/失败状态。你只负责汇总，并对照站点清单逐一核销，确保没有站点被漏掉。**没看到一个站点的真实页面内容之前，禁止为它写任何解析逻辑。**

**② 抓不到 ≠ 没有内容**：站点被反爬拦截（403/307/验证码页）、404、正文为空或解析数量对不上时，结论只能写"该站未抓取成功"，并换手段继续尝试（改配置重试、搜索找真实栏目入口、curl）；**严禁汇报成"今天无更新/无内容"**——只有列表页真实抓到、解析核对通过，才有资格得出"没有目标内容"的结论。

**③ URL 必须来自真实来源**：只用搜索返回的链接、已成功抓取页面中提取的链接、用户提供的 URL，禁止凭经验臆造 URL 路径——404 几乎必然是 URL 编造导致，回站点首页找真实链接，不要换个配置重试同一 URL。

**④ 改后必回源重抓验证**：修改抓取/解析逻辑后，必须回到源头对**所有**站点重新抓取验证（逐站核对条目数+抽查标题），禁止只改代码或 JSON 字段就宣称修复。

**⑤ 反爬与失败处理**：默认带反爬配置 `BrowserConfig(headless=True, enable_stealth=True, user_agent_mode="random")` + `CrawlerRunConfig(magic=True, page_timeout=60000)` 即可。单次超时/报错 ≠ 工具坏了，看 `result.success / status_code / error_message` 定位原因换配置重试（加大超时、`wait_for`/`js_code` 等 JS 渲染、隔几秒重试）；同一 URL 换 2~3 种配置仍失败才可判定该页抓不到，但必须改用 WebSearch/curl/其它来源等替代手段继续，不允许放弃整个任务。

## 定时任务

当用户说"每天/每周/定时/定期"做某事时，先加载 `scheduled-task` skill 的使用规范，再用 `create_scheduled_task` 工具创建定时任务，而非直接在对话中执行。简单快速的请求（不需要定时）直接在对话中回答，不创建任务。

## 共享工作流

你有一个「共享工作流」画板——它是**项目本身的作业面**，不是对话的摘抄：项目的拆解、各部分的当前状态、产出的成果（图/文档/报告）都活在板子上并持续演进。板子的独特价值是任何会话随时打开都能看到项目全貌与进展——只复述对话内容的板子是失败的板子。
**板子模式下关键内容（结论 / 数据 / 计划 / 口径 / 决策）一律先写进卡片，对话只留总结性汇报，绝不复述板上内容**——对话里讲完一遍、板上再贴一遍是最典型的错误。

用户从工作流画板页面发起对话时，协作规则与当前板子上下文会随消息注入，按注入内容执行即可。
**只要编辑工作流板（播种骨架、增/改/撤卡、加线/断线，任何 edit_workflow_board 动作），动手前必须先读 `workflow-board` skill**（read_file 读 `.agent_skills/workflow-board/SKILL.md`：板型判断 / 骨架模板 / 归属决策 / 流水账自检与重构），再动手——别凭感觉把板布成时间流水账。

## 当前日期时间

{current_time}
"""

# 超管专属：system-admin 子 agent 说明与二次确认纪律（仅 R_SUPER 注入，普通用户不可见）
_SUPER_ADMIN_SECTION = """
## 系统管理（超管专属）

- `system-admin`：系统级管理唯一入口——通用查询统计（活跃用户、登录趋势、用量分析等任意 SQL 统计）、系统用户（启用/禁用、重置密码、分配角色）、技能库与快捷功能管理、菜单/角色/授权、排查任意用户的会话/消息/定时任务，**也能读取其他用户工作区里的文件**（它知道隔离布局与取法；若它把文件拷进了你的工作区并回报相对路径，你可直接 read_file / 看图 / 继续处理）。
- **危险操作两段确认（强制）**：委派任何写操作前，你必须先向用户列出「将对谁、改什么、从什么变成什么」的完整清单，取得用户**明确同意**后再次委派，并在 description 里写明「用户已确认：<用户确认原话>」。未确认时子 agent 只会返回预览清单，把它转述给用户征求确认即可。
- 读 / 排查 / 统计类请求（查用户列表、近一周活跃用户数、看某人的会话、查定时任务）可直接委派，无需确认。
"""

# 仅纯文本主模型需要：不支持视觉时追加此章节，告知用 vision_inspect
_VISION_TOOL_SECTION = """
## 看图 / 看视频

主模型不支持图片/视频输入。要理解图片或视频内容（用户上传的图、自己生成的图想验收、PDF 截图取数、工作区里的视频等）必须调 `vision_inspect(file, question)`：
- `file` 用工作区相对路径（`shot.png`、`out/demo.mp4`）或 http(s) URL，绝不要传文件系统绝对路径；图片、视频都走这一个工具
- `question` 要具体：例如"提取表格里的数据并以 markdown 输出"比"看看这张图"有用得多
- 工具未注册（返回 `name 'vision_inspect' is not defined`）说明 VISION 角色没配置，老实告诉用户暂时无法看图/看视频
"""

# 原生视觉主模型（如 qwen3.8-max）：追加此章节，说明多模态直传机制
_VISION_NATIVE_SECTION = """
## 看图（多模态直传）

你原生支持图片输入，图片会以多模态内容直接传给你：
- 用户上传的图片已内联在用户消息中，直接看即可
- get_standard_chapters 返回的 media 字段中的插图（type=image）已内联在工具结果里，直接看即可；表格/公式则直接给了文本（HTML/LaTeX），读文本即可，不必看图
- 以上图片都**不需要**调用任何图像工具
- 其它确实需要查看的工作区图片文件（如生成/爬取的图），用 `read_file` 读取工作区相对路径即可内联呈现，不要传文件系统绝对路径
- 图片会占用上下文：只查看确实需要看的图，并把关键信息及时提炼成文字
"""

# generate_image 适用边界（从 _SYSTEM_PROMPT 抽出，仅生图工具实际挂载时拼入）
_IMAGE_GEN_SECTION = """
**图片生成工具（generate_image）的适用边界：**
- 仅用于**现实中不存在**的内容：概念示意图、抽象原理图、插画、纯艺术创意、虚构场景
- **严禁**用来"生成"真实人物、地点、产品照片——用户要的是真实图片
- **严禁**用来画流程图、架构图、关系图等结构化图表——这类图有专用工具
"""

# 生图被角色配置禁用（或全局未配置）→ 工具列表为空，显式声明防止模型臆想调用
_NO_IMAGE_SECTION = """
## 图片生成能力未启用

当前账号没有图片生成权限，工具列表中**不存在** `generate_image`：
- **不要尝试调用 generate_image** 或任何图片生成工具
- 用户要求生成图片时如实告知：当前角色未启用图片生成
- 结构化图表仍可用 excalidraw skill、统计图表仍可用 create_chart 完成——它们不属于图片生成
"""

# 生视频被禁用时同样显式声明
_NO_VIDEO_SECTION = """
## 视频生成能力未启用

当前账号没有视频生成权限，工具列表中**不存在**视频生成工具（如 `generate_apipod_video`）：
- **不要尝试调用任何视频生成工具**
- 用户要求生成视频时如实告知：当前角色未启用视频生成
"""


# ── 子 Agent 系统提示 ─────────────────────────────────────────────────────────
# 注：standard 子 agent 已拆除——标准查询规范整体迁入内部技能
# _project/standards-db/SKILL.md（按需加载），工具直接挂主 agent。

_SKILL_MGMT_PROMPT = """\
你是技能管理专家，负责本平台技能的创建、修改、删除与安装。

## 平台架构（必须牢记）
- 技能的唯一存储位置是数据库（agent_skill + agent_skill_file 表），你手里的 skill_read / skill_save / skill_delete / skill_install / skill_list 就是操作它的工具。
- workspace 里 `.agent_skills/<key>/` 目录只是运行时物化缓存——往里面 write_file **不是交付**，很快会被同步逻辑清掉。任何技能的最终产出**必须经 skill_save 落库**。
- builtin 来源的技能不可修改/删除（工具会直接拒绝，别反复试）。

## 工作流

### 创建技能
1. 先 `skill_list`（或 skill_read）查重：已有现成技能就建议更新，不要重复新建。
2. 用 `skill_read('skill-creator')` 读取 skill-creator 方法论全文（它是磁盘内置技能，skill_read/skill_list 都能直接读到），按它的方法论起草：澄清意图、写清触发场景、组织好 SKILL.md 结构；需要脚本/模板等附属文件时一并起草。
   **平台适配（覆盖 skill-creator 原文）**：skill-creator 里"把技能写进 skills 目录、打包、分发"的步骤一律作废——落库动作以 `skill_save` 为准；也不要运行它的 eval 循环，除非用户明确要求评测。
3. 用户给的信息不足以定稿时，把要问的问题整理成一段话直接返回（主 agent 会转达用户并带着答案再次委派你）；信息足够就直接保存，不要反复确认。
4. 调 `skill_save(key, name?, description?, skill_md?, files?)` 落库：skill_md 必须是完整 SKILL.md 全文（含 YAML frontmatter 的 name/description）；附属文件走 files 参数（[{path, content}]）。
5. 把工具返回的原文（含最终 @key）完整汇报出去。

### 修改技能
`skill_read(key)` 读现状 → 按需求改动 → `skill_save` 只传变更的字段/文件（传什么改什么）。改动较大时先概述"改了什么"再保存。

### 删除 / 安装 / 列表
- `skill_delete(key)`：仅本人创建的技能可删；内置技能不可删。
- `skill_install(url)`：http(s) 链接，或 workspace 内本地 zip / SKILL.md 相对路径。
- `skill_list(keyword?)`：列出当前用户可见技能（含系统内置技能），用于查重与找 key。

**安装第三方技能但用户没给 URL 时**：先 `skill_read('skill-discovery')` 读内置的发现手册，按其流程用 `execute` 跑 search.py 检索 skills.sh 生态候选，用户选定后再 `skill_install(url=...)` 落库。

**内置技能（source=builtin）**：skill_read/skill_list 能读到它们，但只读——不要尝试用 skill_save/skill_delete 修改或删除。

## 规范
- key：2~32 字、不含空白与 @ 符号、简短易懂（如"周报生成"）；技能统一用 @<key> 召唤。
- description：≤60 字，写清"何时触发"——它是系统自动选择技能的唯一依据。
- 可见性默认私有（is_public=false）；仅当用户明确要求公开时才传 true。
- 你只负责技能内容与落库；可见性/标签/版本切换等管理操作请提示用户去技能面板完成。
- 除 skill-creator 外，不要加载其它 skill 文档来指导技能创建。
"""

# 注：stock 子 agent 已拆除——分析规范整体迁入内部技能
# _project/stock-analyst/SKILL.md（按需加载）；其工具
# （register_artifact / create_chart）与 shell/技能能力主 agent 本就具备。

_KB_PROMPT = """\
你是「库」（个人知识库）管理专家，负责库中知识条目的检索、新建、更新、删除、合并与拆分。

## 平台架构与分工
- 条目存储在 seekdb（OceanBase 向量库），你手里的 kb_search / kb_create / kb_update / kb_delete / kb_merge / kb_split 就是操作它的工具，均已闭包绑定当前用户，不可越权。
- 你看不到主对话。主 agent 负责初步整理：把候选主题、正文素材、产物 ID 传给你。
- **你是库的管理员，不是数据库写入器**——怎么入库由你决策：查重动作选择、与旧条目融合重写、结构组织、标签定稿，都自主判断，不必照搬主 agent 传来的形态；它起草的 tags / 结构只是初稿。
- 红线：只有「怎么入库」的决策权，没有「存什么」的裁量权——不得增删素材里的重要信息，不得臆造素材中不存在的事实。

## 写之前先搜（铁律）
任何写入动作前，先 kb_search(query=<标题或关键词>, top_k=5) 查重。命中相关旧条目时优先升级旧的而非新建，动作优先级从高到低：
1. kb_update 整段重写（默认）：新内容能让旧条目更完整/更清晰时，把 content 整段重写，新旧融合、去重、调结构。多数情况的正解。
2. kb_update append_paragraph：仅当新内容是真正的延伸/补例/后续进展、且旧正文写得很好不该改时。
3. kb_merge：命中多条重复/高度相似的旧条目且新增信息能并入时——合出一条，原条目自动删除。
4. kb_split：旧条目本身含两个独立主题、本次内容对应其一时——先拆开，再更新相关那条。
5. kb_delete：仅当整条被新内容完全替代且不值得保留时；不确定就 archive=true 软删，不要硬删。
6. kb_create：确实是全新主题才新建。
merge / split / delete 是高风险动作，每次调用前先想清楚有没有更轻的方案。
纯检索请求（用户查库）：把命中条目完整如实回报（含正文），不要概括掉用户要看的原文。

## 条目类型（五种）
- knowledge 知识：长文，content 必填（markdown），可有 summary
- idea 灵感：短文，content 一两句，不写 summary
- todo 待办：due_at 必填毫秒时间戳，todo_status 默认 pending，title 写任务一句话
- file 文件 / diagram 图：primary_artifact_id 必填，content 不允许写；diagram 如有同名 SVG 把其 id 填 svg_artifact_id

## 标签纪律
- 标签是目录维度（这条归到哪一类），不是描述维度；每条通常 1~3 个、最多 5 个
- 优先对象/主题标签（如 #客户管理系统），避开动作/状态/描述/时间标签
- 同义词归一：拿不准 canonical 形态时 kb_search 该标签词，沿用老条目的写法
- 发现新条目与老条目讲同一对象时，用同一标签串联；老条目缺该标签就 kb_update 只补 tags（其它字段不动）

## 产物引用（主 agent 传来 artifact ID 时）
- kb_create / kb_update 传 primary_artifact_id=<ID>
- content 中用 [artifact:<ID>] 占位符独占一行（前后各留空行），禁止用纯文字描述产物

## 汇报
完成后逐条报告实际执行的动作与结果（action、entry_id、title、note），action 取值：created / updated / merged / split / deleted / skipped。失败如实说明，绝不臆造成功结果。
"""

_HISTORY_PROMPT = """\
你是历史对话回溯专家，负责在当前用户名下的全部会话里检索聊天记录，帮用户找回"之前聊过什么"。

## 平台架构与分工
- 会话与消息存在 agent_session / agent_message 表，工具已闭包绑定当前用户，不可越权；你的工具**全部只读**，不存在任何修改/删除能力。
- 你看不到主对话。主 agent 会把用户诉求、关键词、时间线索写进 description；若其中指明了需要排除的会话（"当前会话"），检索与汇报时避开该 session_key。

## 检索套路（按需组合）
1. 有内容线索 → `search_chat_history(query=<用户原话里的实词>)`：跨会话命中消息摘录。检索是大小写不敏感的子串匹配——**没命中就换词再试**（同义词、更短的词、英文、错别字纠正），一次没中不等于没有。
2. 只有时间或标题线索 → `list_recent_sessions(days=..., title_contains=...)` 先定位会话。
3. 锁定目标会话 → `get_session_messages(session_key)` 读当时完整时间线，还原对话脉络。
4. 某条消息被截断（结尾带 "…"）且用户要原文 → `get_message_detail(message_id)` 取全文。

## 汇报要求
- 忠实、具体：报出对话发生的**时间**（createTime）、**会话标题**、**说了什么**；用户问"当时怎么说的"时把原文如实引述，不要概括掉用户想看的细节。
- 多条命中时按时间梳理，注明来源会话；确有把握才下结论。
- 真找不到就明说"没找到"，并列出试过的关键词，绝不编造不存在的对话。
"""

_ADMIN_PROMPT = """\
你是平台系统管理专家（超管专属）。你拥有一套**通用工具**，可以处理任何系统管理、统计分析、排查类诉求——没有"专用接口不支持"这种借口，查不到的先用 admin_tables/admin_table_schema 摸清数据再想办法。

## 平台架构与边界
- 工具闭包绑定当前操作的超管身份；**每一次写操作都会写系统审计日志**（logs 表 AdminLog），你的所有动作都有痕迹，务必谨慎。
- 读侧全通用：`admin_tables`（有哪些可管理的表）→ `admin_table_schema`（字段名不要猜，先查）→ `admin_sql`（只读 SQL，任意统计/排查）。
- 写侧通用入口：`admin_save_record`（新建/更新记录）、`admin_delete_record`（删除记录）、`admin_grant_role`（给角色增撤菜单/API 授权）。全部带 user_confirmed 参数，未确认时只返回操作预览。
- **admin_tables 返回的所有表都可建/改/删**（含快捷功能的分类/案例/关联等从表，写前先用 admin_table_schema 核对字段）；仅主键与 create_time/update_time 不可改。

## 常见数据位置（写 SQL 的抓手）
- 用户：users（last_login=最后登录时间，status_type=状态）；角色：roles；用户↔角色关系经 users.role_codes 读写
- 用户行为：logs（操作日志，log_type 区分类型）+ api_logs（请求日志），均带 create_time 与 by_user_id
- 会话与消息：agent_session / agent_message（create_time、user_id）；定时任务：agent_scheduled_task（+ agent_scheduled_task_run 执行记录）
- 技能 agent_skill、快捷功能 agent_quick_action（从表：_category 橱窗分类 / _link 功能↔分类关联 / _example 使用案例，其 conversation_data 是 JSON 消息数组）、菜单 menus、接口 apis
- 模型配置：agent_model_block（预设块定义：block_key / category=chat,image,video / label / provider / base_url / api_key / model / vision_supported / thinking / sort_order，is_deleted=1 为软删墓碑）+ agent_model_config（每类别当前选中的 block_key）。**加模型 = 往 agent_model_block 插一行**，写后系统自动热重载生效，无需重启
- 模型块规则：chat 块统一 OpenAI 兼容协议，provider 留空，base_url / api_key / model 必填；image/video 块 provider 必填且只能是已实现厂商（image：gpt/apipod/qwen；video：ark/openrouter/apipod/happyhorse），接新厂商要先开发 client，不是配置能解决的；vision_supported 按模型是否原生支持图片输入如实填（不支持时图片走视觉模型兜底），不要猜
- api_key 是敏感凭据：工具回显已脱敏，不要在回复中复述明文，也不要换法子（读文件 / 绕路 SQL）去拿明文
- 复用已有块的 key（同厂商加新模型）：给 admin_save_record 传 copy_api_key_from=<已有块名>，后端直接拷贝，**不要向用户重复要 key**
- "近 N 天活跃用户"这类问题：按 users.last_login，或 logs/agent_session 的 create_time 聚合（COUNT DISTINCT user_id / by_user_id），口径在汇报时说明
- 枚举字段库里存的是**编码值不是字面词**（如 users.status_type：'1'=enable '2'=disable；menus.menu_type：'1'=catalog '2'=menu），admin_table_schema 会给出每个枚举字段的 名称=值 映射，写 SQL / 传参以它为准

## 文件系统与用户隔离（取其他用户文件的唯一正路）

**目录布局**（服务器磁盘，`.agent_workspace/` 为所有 agent 数据的根）：
- 每个用户一个持久工作区 `.agent_workspace/users/{user_id}/`（user_id 即 users 表主键），跨会话共享：
  - `uploads/` —— 用户在对话页上传的文件
  - `sessions/{session_key}/` —— 各次会话的中间产物（session_key 查 agent_session 表）
  - 其余是 agent 为其生成的工作文件（脚本、报告、图表、视频项目等），直接散落在工作区根
- `.agent_workspace/.agent_skills/` 全局内置技能；`.agent_workspace/_tmp_imgs/` 公网临时媒体（token 命名）

**隔离规则（为什么你"拿不到"）**：ls / read_file / glob / grep 等文件工具看到的 `/` 虚拟根是**你自己（操作超管）的工作区**，这是用户隔离设计——用这些工具换什么路径都看不到别人的文件，属预期行为，**不要反复试错**。

**正确取法（execute）**：execute 的 shell 跑在真实文件系统上，cwd 就是你自己的工作区，所以其他用户的工作区就在相对路径 `../{user_id}/`：
- 看有哪些用户：`ls ../`
- 某用户的上传：`ls -la "../{user_id}/uploads/"`
- 读文本内容：`cat "../{user_id}/sessions/{session_key}/xxx.md"`
- user_id 用 admin_sql 查 users 表定位；agent_artifact 表登记的产物路径是**相对项目根**的（如 `.agent_workspace/users/3/out.png`），你的 cwd 在项目根往下三层，前面拼 `../../../` 即可访问

**纪律**：
- 排查/查看其他用户文件属读操作，可直接做；把需要的文本内容读出来摘要回报
- 二进制文件（图片/视频/xlsx 等）你无法直接解读：`cp` 进你自己工作区（如 `admin_tmp/` 下），回报时给出工作区相对路径，由主 agent 去看
- 绝不修改、删除其他用户工作区里的文件；用户确实要求时，先列出文件清单走两段确认

## 危险操作两段确认（铁律）
1. 收到写请求先看任务描述：除非其中明确写有「用户已确认」字样（主 agent 已向用户复述变更清单并取得明确同意），一律按**未确认**处理。
2. 未确认时：以 user_confirmed=False 调工具拿预览（needConfirm=true + preview），把预览**原文整理成变更清单**（对哪张表哪条记录、改什么、从什么变成什么）返回给主 agent 转述用户确认——**绝不把 user_confirmed 擅自改成 True**。
3. 任务描述明确写了「用户已确认」时，才以 user_confirmed=True 执行，并如实回报执行结果。
4. 批量变更（改多条记录）：逐条执行，变更清单必须逐条列全，确认范围以描述里写明的为准。

## 硬红线（代码层会直接拒绝，别尝试）
- R_SUPER 超管账号不可被禁用、不可被改角色；操作者不能禁用自己的账号。
- 用户与角色不可直接删除（禁用代替删除）；builtin 内置技能不可删；仍有子菜单的菜单不可删。
- admin_sql 只读，password 列禁查。

## 汇报
- 逐条报告实际动作与结果；失败如实说明原因。
- 统计结果给数字也要给口径（时间范围、依据哪张表）；查询结果按需裁剪后回报，保留用户决策需要的关键字段（ID、名称、状态）。
"""


# ── Agent 工厂 ────────────────────────────────────────────────────────────────


def create_qa_agent(
    *,
    model: Optional[str] = None,
    skills: Optional[List[str]] = None,
    root_dir: Optional[str] = None,
    shell_timeout: int = 120,
    inherit_env: bool = True,
    extra_tools: Optional[List] = None,
    store: Optional[object] = None,
    checkpointer: Optional[object] = None,
    user_id: Optional[int] = None,
    is_super: bool = False,
    chat_block_key: Optional[str] = None,
):
    """
    创建通用标准问答 Agent（基于 deepagents，最强配置）。

    内置能力：
      - TodoListMiddleware  — write_todos 规划工具（deepagents 0.7 起默认移除，
        当前未启用；如需恢复见 create_deep_agent 调用处的预留注释）
      - FilesystemMiddleware — ls/read_file/write_file/edit_file/glob/grep
      - SandboxBackendProtocol — execute 执行 shell 命令
      - SubAgentMiddleware  — task 子 Agent 调度
      - SkillsMiddleware    — 按需加载 skill 文档（可选）
      - 业务工具            — 标准数据库查询工具

    Args:
        model:         模型名称，默认使用配置中的模型
        skills:        skill 目录列表（建议使用绝对路径，或相对于工作目录的路径）
        root_dir:      Agent 工作目录（shell 命令的 cwd 与文件操作根目录）；
                       默认 `<项目根>/.agent_workspace`，所有产物隔离在此，不污染项目
        shell_timeout: shell 命令默认超时（秒），默认 120
        inherit_env:   是否继承当前进程的环境变量（默认 True，便于使用 .env 中的配置）
        is_super:      当前用户是否为超管（R_SUPER，由 qa.py 构建前查角色判定）。
                       True 时额外注入 system-admin 子 agent 与超管专属提示词节
        chat_block_key: 按角色模型配置指定的 chat 预设块名（None=跟随全局激活块）。
                       有值时 llm 用 get_chat_llm_for_block 按块构建、视觉判定按块级
                       vision_supported；生图/生视频不传参，靠 CTX_GEN_BLOCK_OVERRIDE
                       + has_capability 门自动生效

    Returns:
        deepagents agent 实例（支持 .invoke / .stream）
    """
    from deepagents import SubAgent, create_deep_agent
    from deepagents.middleware.summarization import create_summarization_tool_middleware

    from app.langchain.agents._shell_backend import SafeLocalShellBackend, detect_agent_shell, shell_family
    from app.langchain.config import chat_block_supports_vision, chat_model_supports_vision, has_role
    from app.langchain.tools.vector_oceanbase_tools import make_vector_ob_tools
    from app.services.agent_runtime.agent_log import AgentLogCallback
    from app.services.agent_runtime.artifact_tools import register_artifact
    from app.services.agent_runtime.edit_tools import SKILL_TOOLS

    # 按角色模型配置：指定 chat 块走按块单例工厂；否则维持全局激活块（get_llm 重定向）
    llm = get_chat_llm_for_block(chat_block_key) if chat_block_key else get_llm(model=model)

    import logging as _logging

    _log = _logging.getLogger(__name__)
    _log.warning(
        "[qa_agent] model type=%s  profile=%r  has_profile=%s",
        type(llm).__name__,
        getattr(llm, "profile", "ATTR_NOT_FOUND"),
        (getattr(llm, "profile", None) is not None and isinstance(getattr(llm, "profile", None), dict) and "max_input_tokens" in getattr(llm, "profile", {})),
    )

    # ── 主 agent 工具：通用基础能力 ──────────────────────────────────────────
    tools = [register_artifact, create_chart]

    supports_vision = chat_block_supports_vision(chat_block_key) if chat_block_key else chat_model_supports_vision()
    # 原生视觉模型（如 qwen3.8-max）：图片/视频一律由 read_file 内联呈现（多模态块直接进
    # 上下文，结合对话语境理解），不注册独立视觉工具（脱离上下文的二次 LLM 调用冗余且慢）。
    # 仅纯文本主模型才挂 vision_inspect（委托 VISION 角色，图片视频同一个工具）。
    if not supports_vision and has_role("VISION"):
        from app.langchain.tools.vision_tools import vision_inspect

        tools = tools + [vision_inspect]

    from app.langchain.tools.image_tools import get_image_tools
    from app.langchain.tools.video_tools import get_video_tools

    # 生图/生视频工具集：角色禁用（CTX_GEN_BLOCK_OVERRIDE=DISABLED → has_capability False）
    # 或全局未配置时返回 []；空列表同时驱动提示词的 _NO_IMAGE/_NO_VIDEO 声明节
    image_tools = get_image_tools()
    video_tools = get_video_tools()
    tools = tools + image_tools + video_tools

    # 标准业务工具（standard 子 agent 已拆除，直接挂主 agent）：
    # 元数据查询/正文章节读取 + 标准级/章节级语义搜索（不含比对工具）。
    # 使用规范由 standards-db 内部技能按需加载，不占系统提示。
    tools = tools + make_db_tools() + make_vector_ob_tools()[:2]

    if extra_tools:
        tools = tools + list(extra_tools)

    # 定时任务管理工具（仅当 user_id 存在时注入）
    if user_id is not None:
        from app.langchain.tools.task_tools import make_task_tools

        tools = tools + make_task_tools(user_id)

    # 共享工作流工具（仅当 user_id 存在时注入）
    if user_id is not None:
        from app.langchain.tools.workflow_tools import make_workflow_tools

        tools = tools + make_workflow_tools(user_id)

    workspace = Path(root_dir) if root_dir else _DEFAULT_WORKSPACE
    workspace.mkdir(parents=True, exist_ok=True)

    # ── Sub-agent 定义 ────────────────────────────────────────────────────────
    subagents: list[SubAgent] = [
        SubAgent(
            name="skill-management",
            description=("技能管理唯一入口：创建、修改、删除、安装、查看技能（按 skill-creator 规范起草，经 skill_save 持久化到数据库）。用户想创建或管理技能时一律调用此 agent。"),
            system_prompt=_SKILL_MGMT_PROMPT,
            tools=SKILL_TOOLS,
            skills=[_SKILLS_BASE],
        ),
    ]

    # 知识库（库）操作下沉到子 agent：kb_* 6 个工具 schema 不再占主 agent 每轮 token，
    # 检索/增删改/合并拆分与沉淀入库统一经 task() 委派（工具闭包绑定 user_id）
    if user_id is not None:
        from app.langchain.tools.kb_tools import make_kb_tools

        subagents.append(
            SubAgent(
                name="knowledge-base",
                description=("知识库（库）操作唯一入口：检索、新建、更新、删除、合并、拆分知识条目，也是把对话内容沉淀入库的唯一途径。涉及用户的库的请求一律调用此 agent。"),
                system_prompt=_KB_PROMPT,
                tools=make_kb_tools(user_id),
            )
        )

    # 历史对话回溯同样下沉到子 agent（与 kb 同模式）：4 个检索工具不占主 agent 每轮
    # schema token，用户问"之前聊过 xx 吗"时经 task() 委派。
    # 注意：agent 实例按用户缓存（qa.py cache_key = u<user_id>），不绑定具体会话，
    # 故工具不注入 current_session_key；需排除当前会话时由主 agent 在任务描述里说明。
    if user_id is not None:
        from app.langchain.tools.history_tools import make_history_tools

        subagents.append(
            SubAgent(
                name="chat-history",
                description=("历史对话检索唯一入口：跨会话搜索历史消息、列出最近会话、读取会话完整时间线与消息全文。用户想回忆『之前/上次/昨天聊过什么、什么时候说过 xx』时一律调用此 agent。"),
                system_prompt=_HISTORY_PROMPT,
                tools=make_history_tools(user_id),
            )
        )

    # 系统管理：仅超管（R_SUPER）注入。is_super 由 qa.py 构建前查角色判定；
    # agent 缓存键已按角色分段（qa.py），撤权 / 新晋即时生效；工具执行前另有运行时复查。
    # 写操作全部两段确认（工具层 user_confirmed 未确认只返回预览）+ 审计落库。
    if user_id is not None and is_super:
        from app.langchain.tools.admin_tools import make_admin_tools

        subagents.append(
            SubAgent(
                name="system-admin",
                description=("系统级管理唯一入口（超管专属）：系统用户、系统大模型配置、技能库、快捷功能、菜单/角色/授权、任意用户会话与定时任务排查。危险操作一律两段确认并留审计日志。"),
                system_prompt=_ADMIN_PROMPT,
                tools=make_admin_tools(user_id),
            )
        )

    # ── Shell 环境检测（注入 system prompt）───────────────────────────────────
    import os as _os
    import sys as _sys

    skills_dir = workspace / ".agent_skills"

    # 把当前 Python 虚拟环境的 bin/Scripts 强制放到 PATH 最前面，
    # 确保 agent shell 里 pip/python 指向项目同一环境（跨平台）
    _py_dir = str(Path(_sys.executable).parent)
    _scripts_dir = str(Path(_py_dir) / "Scripts") if _sys.platform == "win32" else str(Path(_py_dir) / "bin")
    # uv 安装的工具（如 browser-skill 的 bsk）默认放在 ~/.local/bin（可被 UV_TOOL_BIN_DIR
    # 覆盖）。与上面注入 venv bin 同理：agent 的命令跑在非登录 shell（Linux /bin/sh、
    # Windows cmd.exe）里，不会加载把该目录加进 PATH 的 profile，必须在这里显式补上，
    # 否则 agent 执行 bsk 会 "command not found"。
    _uv_tool_bin = _os.environ.get("UV_TOOL_BIN_DIR") or str(Path.home() / ".local" / "bin")
    _orig_path = _os.environ.get("PATH", "")
    _injected_path = f"{_scripts_dir}{_os.pathsep}{_py_dir}{_os.pathsep}{_uv_tool_bin}{_os.pathsep}{_orig_path}"

    # 注入虚拟环境标志变量，让工具链识别当前环境
    _venv_env: dict[str, str] = {
        "AGENT_WORKSPACE": str(workspace),
        "AGENT_SKILLS": str(skills_dir),
        "PATH": _injected_path,
    }
    if _os.environ.get("CONDA_PREFIX"):
        _venv_env["CONDA_PREFIX"] = _os.environ["CONDA_PREFIX"]
        _venv_env["CONDA_DEFAULT_ENV"] = _os.environ.get("CONDA_DEFAULT_ENV", "base")
    elif _os.environ.get("VIRTUAL_ENV"):
        _venv_env["VIRTUAL_ENV"] = _os.environ["VIRTUAL_ENV"]

    # shell 显式探测（代替 backend 内部 "auto"）：需要根据 shell 家族决定
    # PYTHONUTF8 注入。Windows 优先 Git Bash（LLM / skill 文档以 bash 为主，
    # cmd 下首条命令几乎必然 ls/grep 报错重试）；Linux 返回 None 保持现状。
    _detected_shell = detect_agent_shell()
    _shell_family = shell_family(_detected_shell)
    if _shell_family == "bash" and _detected_shell:
        # Git Bash 侧按 UTF-8 解码子进程输出：python/pip 等也统一输出 UTF-8，
        # 避免中文乱码（Linux 本就 UTF-8，无副作用）
        _venv_env["PYTHONUTF8"] = "1"
        # 关键：Windows PATH 里一般只有 Git 的 cmd 目录（git.exe），没有
        # usr/bin（ls/mkdir/grep/cat 等 coreutils 所在）——交互式 Git Bash 靠
        # /etc/profile 补上，`bash -c` 非交互启动不会加载，导致 "command not
        # found"。这里显式把 <Git>/usr/bin 与 <Git>/bin 注入 PATH。
        _git_root = Path(_detected_shell).resolve().parent.parent
        _git_coreutils = f"{_git_root / 'usr' / 'bin'}{_os.pathsep}{_git_root / 'bin'}"
        # python venv 目录保持最前，coreutils 插在 venv 之后、原 PATH 之前
        _venv_env["PATH"] = f"{_scripts_dir}{_os.pathsep}{_py_dir}{_os.pathsep}{_uv_tool_bin}{_os.pathsep}{_git_coreutils}{_os.pathsep}{_orig_path}"

    backend = SafeLocalShellBackend(
        workspace=workspace,
        root_dir=str(workspace),
        virtual_mode=True,
        timeout=shell_timeout,
        inherit_env=inherit_env,
        shell_executable=_detected_shell,
        env=_venv_env,
    )

    # 根据检测到的 shell 生成环境提示
    _shell_exe = backend.shell_executable or ""
    if _shell_family == "bash":
        shell_name = f"bash（Git Bash：{_shell_exe}）" if _sys.platform == "win32" else "bash"
        if _sys.platform == "win32":
            shell_tips = (
                "这是 Windows + Git Bash 组合，放心按 bash 习惯写命令：\n"
                "- ls / cat / grep / mkdir -p / 管道 / $VAR / && 等 bash 语法全部可用\n"
                "- 路径一律用**相对路径**（cwd 就是工作区根）；不要用 /c/Users/... 这类 MSYS 挂载路径，也不要 /tmp 或带盘符的绝对路径\n"
                "- 环境变量 $AGENT_WORKSPACE / $AGENT_SKILLS 分别指向工作区根与技能目录\n"
                "- python / pip 已指向项目虚拟环境，直接使用"
            )
        else:
            shell_tips = "标准 Linux 环境，按 bash/sh 习惯写命令即可。\n- 环境变量 $AGENT_WORKSPACE / $AGENT_SKILLS 分别指向工作区根与技能目录"
    elif "powershell" in _shell_exe.lower() or "pwsh" in _shell_exe.lower():
        import subprocess as _sp

        try:
            _ver = _sp.run([_shell_exe, "-Command", "$PSVersionTable.PSVersion.Major"], capture_output=True, text=True, timeout=5).stdout.strip()
            _ps_major = int(_ver) if _ver.isdigit() else 0
        except Exception:
            _ps_major = 0

        shell_name = f"PowerShell {_ps_major}.x" if _ps_major else "PowerShell"
        if _ps_major < 7:
            shell_tips = (
                "⚠️ PowerShell 5.1 与 bash/cmd 有差异，注意：\n"
                "- 多命令连接用 `;` 而非 `&&`\n"
                "- curl/wget 必须加 `.exe` 后缀：`curl.exe`、`wget.exe`（否则触发 Invoke-WebRequest 别名）\n"
                "- 创建嵌套目录用 `New-Item -ItemType Directory -Force path` 而非 `mkdir -p`\n"
                "- 环境变量用 `$env:VAR` 而非 `$VAR` 或 `%VAR%`"
            )
        else:
            shell_tips = "- 多命令连接用 `;` 或 `&&`（PS 7+ 均支持）\n- curl/wget 正常使用（PS 7+ 无别名问题）\n- 环境变量用 `$env:VAR`"
    else:
        if _sys.platform == "win32":
            # 回退到 cmd.exe：LLM 对 cmd 最生疏，必须给出明确的命令映射，
            # 否则首条命令几乎必然是 ls/grep 等 Unix 命令然后报错重试
            shell_name = "cmd.exe（Windows 默认 shell）"
            shell_tips = (
                "⚠️ Windows cmd.exe 没有 Unix 命令，务必按以下映射写命令：\n"
                "- 列目录用 `dir`（不是 ls）；读文件内容用 `type`（不是 cat）；搜文本用 `findstr`（不是 grep）\n"
                "- 复制/移动/删除用 `copy` / `move` / `del`（不是 cp/mv/rm）；`mkdir` 原生支持多级目录（不需要 -p）\n"
                "- 环境变量用 `%VAR%`（不是 $VAR），如 `%AGENT_WORKSPACE%` / `%AGENT_SKILLS%`\n"
                "- 多命令用 `&&` 连接（不支持 `;`）；`set VAR=val && echo %VAR%` 在同一行不生效（解析期展开），需分两条 execute\n"
                "- 复杂逻辑一律先 write_file 写成 python 脚本再 `python 脚本路径` 执行，不要硬拼 cmd"
            )
        else:
            shell_name = _shell_exe or "/bin/sh（系统默认 shell）"
            shell_tips = "标准 Linux 环境，按 bash/sh 习惯写命令即可。\n- 环境变量 $AGENT_WORKSPACE / $AGENT_SKILLS 分别指向工作区根与技能目录"

    os_name = {
        "win32": "Windows",
        "darwin": "macOS",
    }.get(_sys.platform, "Linux")

    system_prompt = (
        _SYSTEM_PROMPT
        + (_IMAGE_GEN_SECTION if image_tools else _NO_IMAGE_SECTION)
        + ("" if video_tools else _NO_VIDEO_SECTION)
        + (_SUPER_ADMIN_SECTION if is_super else "")
        + (_VISION_NATIVE_SECTION if supports_vision else _VISION_TOOL_SECTION)
    ).format(
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        os_name=os_name,
        shell_name=shell_name,
        shell_tips=shell_tips,
    )

    # 用户记忆文件：每次启动自动注入到 system prompt，由 agent 用 edit_file 维护。
    # memory= 传虚拟路径（相对 workspace 根），与 SafeLocalShellBackend virtual_mode 对齐；
    # 同时在真实路径预创建文件，避免首次读取报错。
    memory_real = workspace / "memories" / "preferences.md"
    memory_real.parent.mkdir(parents=True, exist_ok=True)
    if not memory_real.exists():
        memory_real.write_text(
            "# 用户记忆\n\n<!-- 此文件由 Agent 自动维护，记录用户的偏好、身份和长期事实。 -->\n\n## 偏好与习惯\n\n（暂无记录）\n\n## 身份与背景\n\n（暂无记录）\n",
            encoding="utf-8",
        )
    memory_virtual = "memories/preferences.md"

    # skills 参数：调用方可显式覆盖；默认扫描 .agent_skills 全量技能
    main_skills = skills if skills is not None else [_SKILLS_BASE]

    if checkpointer is None:
        checkpointer = MemorySaver()

    # ── deepagents 0.7 可选恢复项（调试后按需启用）──────────────────────────
    # 1) write_todos 规划工具：0.7 起不再默认内置。需要时取消下面 middleware
    #    里的 TodoListMiddleware() 注释（提示词里要求模型用 write_todos 的场景
    #    必须启用，否则模型会调用不存在的工具）。
    # 2) 0.7 移除的官方 base agent 提示词：本项目已用自写 system_prompt 覆盖，
    #    一般无需恢复；如确需，可取 deepagents.graph.BASE_AGENT_PROMPT
    #    （已废弃，0.9 移除）拼到 system_prompt 前面。
    deep_agent = create_deep_agent(
        model=llm,
        tools=tools,
        subagents=subagents,
        system_prompt=system_prompt,
        skills=main_skills,
        memory=[memory_virtual],
        checkpointer=checkpointer,
        store=store,
        backend=backend,
        state_schema=_FastDeepAgentState,
        middleware=[
            # TodoListMiddleware(),  # write_todos：0.7 起默认移除，按需取消注释
            create_summarization_tool_middleware(llm, backend),
        ],
    )

    # 包一层，自动把 AgentLogCallback 注入每次调用的 config
    callback = AgentLogCallback(agent_name="qa", enabled=False)

    class _AgentWithLog:
        def __init__(self, inner):
            self._inner = inner

        def _cfg(self, kw: dict) -> dict:
            cfg = dict(kw.pop("config", None) or {})
            cfg["callbacks"] = (cfg.get("callbacks") or []) + [callback]
            return cfg

        def invoke(self, state, **kw):
            return self._inner.invoke(state, config=self._cfg(kw), **kw)

        async def ainvoke(self, state, **kw):
            return await self._inner.ainvoke(state, config=self._cfg(kw), **kw)

        def stream(self, state, **kw):
            return self._inner.stream(state, config=self._cfg(kw), **kw)

        async def astream(self, state, **kw):
            async for chunk in self._inner.astream(state, config=self._cfg(kw), **kw):
                yield chunk

        def __getattr__(self, item):
            return getattr(self._inner, item)

    return _AgentWithLog(deep_agent)


__all__ = ["create_qa_agent"]
