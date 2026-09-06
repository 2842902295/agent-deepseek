<!-- markdownlint-disable MD033 MD041 -->

<p align="center">
  <img src="web/public/xiaonian-logo.png" width="160" height="160" alt="Agent DeepSeek">
</p>

<div align="center">

# Agent DeepSeek

> 开源、可私有部署的全栈 Agent web 平台：对话 Agent 内核全面采用 DeepSeek 官方运行时 **DeepSeek Harness（dsh）**，以 DeepSeek 为基础模型、兼容任意 OpenAI 协议模型；多层子代理编排 × 技能凝练 × 流程编排，另有一堆丰富细节，完全兼容手机端；Docker 一键部署、Electron 桌面单机版，前后端一体轻量化，是一个完美契合 vibe coding 时代的项目。

[![license](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
![python](https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=edb641)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?logo=fastapi&logoColor=white)
![Vue3](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)
![DeepSeek Harness](https://img.shields.io/badge/Agent%20Runtime-DeepSeek%20Harness-000)
![LangGraph](https://img.shields.io/badge/LangGraph-Task%20Agents-000?logo=langchain&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-enabled-7c3aed)
![Docker](https://img.shields.io/badge/Docker-one--click%20deploy-2496ED?logo=docker&logoColor=white)
![Electron](https://img.shields.io/badge/Electron-Desktop-47848F?logo=electron&logoColor=white)
[![visits](https://visitor-badge.laobi.icu/badge?page_id=2842902295.agent-deepseek&title=visits)](https://github.com/2842902295/agent-deepseek)

🔗 **在线预览**：[agent-deepseek.com](https://agent-deepseek.com)

</div>

---

## 简介

**Agent DeepSeek** 是一个端到端、可私有部署的完整 Agent 平台。它的对话 Agent 内核**全面采用 DeepSeek 官方发布的 Agent 运行时 DeepSeek Harness（dsh）**，结构化任务 Agent 则由 **LangChain / LangGraph** 驱动，把一个对话框背后串起了 **LLM 推理 / 多层 Agent 编排 / 虚拟文件系统 / Shell 执行 / 联网搜索 / 图像视频生成 / 持久记忆 / 可凝练技能体系 / AI 托管知识库**——你说一句话，它自己规划、自己调工具、自己写答案；你做完一段对话，可以一键把整段流程凝练成一位新的 "AI 助理"，下次 `@` 即可召唤。

整套系统基于 **FastAPI + Vue3** 全栈构建，以 **DeepSeek 为基础模型**，同时兼容任意 OpenAI 协议的大模型（DeepSeek 官方、DashScope、Ollama、vLLM、自建网关、OpenAI 官方均可），**纯 Web 部署、完全兼容手机端、Docker 一键拉起**。内置一键打包脚本，支持 **在线源码部署** 与 **离线内网部署** 两种交付形态；Windows 桌面单机版基于成熟的 **Electron** 技术已完成适配，一键即可打包出安装程序。

<!-- 截图占位：产品整体截图，建议 1440px 宽，放到 screenshots/overview.png -->
<p align="center">
  <img src="screenshots/overview.png" alt="产品整体截图" width="100%" />
</p>

## ✨ 核心亮点

### 1. 🐳 Agent 内核：全面采用 DeepSeek Harness（dsh）官方运行时

这是本项目最核心的一次升级：对话 Agent 的运行内核已**全面切换到 DeepSeek 官方发布的 Agent 运行时——DeepSeek Harness（dsh）**，不再依赖自研或第三方拼接的 agent loop。Agent 的规划、工具调用、子代理编排、上下文压缩、会话留档，全部跑在 DeepSeek 自家的官方内核上，与 DeepSeek 官方 Agent 产品同源同构。

- **官方运行时，同源同构**：dsh 是 DeepSeek 官方发布的 Agent Harness，平台通过其官方 Python SDK（`deepseek-harness-sdk`）以「子进程 + JSON-RPC over stdio」方式驱动，运行时与业务后端进程相互隔离，稳定、可恢复
- **内核自带完整工具栈**：bash 执行、文件读写、todo 规划、子代理（subagent）编排、上下文自动压缩（compaction）等均由 dsh 原生提供，平台只做能力组合与参数覆写，不重复造轮子
- **原生技能体系**：dsh 内置 skill 加载机制，平台技能直接挂进官方运行时，按需加载、零额外胶水
- **MCP 协议原生接入**：dsh 内置 MCP 客户端，业务工具经 MCP 桥挂载，联网搜索与第三方连接器即插即用
- **原生多模态读图**：支持视觉的模型可经 dsh 原生 `read_image` 通道把图片直接读进上下文
- **真实可中断**：「停止」经带外取消直达运行时自带的 `agent.cancel()`，秒级终止在途 LLM 请求与工具循环，不再空烧后续 token
- **开放兼容**：内核虽为 DeepSeek 官方运行时，模型层仍兼容任意 OpenAI 协议模型（DeepSeek / DashScope / Ollama / vLLM / 自建网关均可），不锁死单一厂商

> 标准比对、指标提取、对象关系推断等**结构化任务 Agent** 仍由 LangChain / LangGraph 驱动，与 dsh 对话内核分工协作、各司其职。

### 2. 🤖 比肩一线国产 Agent 的完整平台

以 DeepSeek 官方运行时 **DeepSeek Harness（dsh）** 为对话内核、LangChain 生态协同实现的完整 Agent 平台，能力对齐 **WorkBuddy、扣子（Coze）、Dify、FastGPT、RagFlow** 等国产优秀 Agent 产品——而它们之中，要么闭源托管、要么偏工作流编排、要么只做 RAG 问答。本项目提供的是**一整套可私有部署、可二次开发的完整 Agent 底座**：

- **自主任务执行**：一句话扔进去，AI 自己拆解步骤、规划子任务、调用工具、整理产物
- **专家 · 技能 · 连接器三位一体**：`@专家名` 即可召唤一位专家（人设与方法论 + 绑定技能 + 绑定连接器）驻留当前会话——专家中心提供预设专家，也可自建；技能按需 `@` 加载，MCP 连接器即插即用
- **多层 Subagent 编排**：复杂任务自动分派给子 Agent 并行处理
- **虚拟文件系统 + 隔离 Shell**：AI 拥有自己的工作目录，读文件、写代码、跑脚本，全程沙箱隔离
- **MCP 协议接入**：联网搜索、第三方服务即插即用
- **流式全程可见**：每一步思考、工具调用、生成产物实时推送，可中途打断
- **持久会话记忆**：重启后凭 session 完整恢复历史对话与产物
- **纯 Web 部署**：Docker Compose 一键拉起，浏览器即用；Windows 桌面单机版基于 **Electron** 完成适配，打包出安装程序即用
- **完全兼容手机端**：手机、平板浏览器打开即用，对话、知识库、技能管理全部适配，无需安装 App

<!-- 截图占位：对话页截图（展示流式输出/工具调用过程），放到 screenshots/chat.png -->
<p align="center">
  <img src="screenshots/chat.png" alt="全能助理对话页" width="100%" />
</p>

<!-- 截图占位：手机端对话页截图（竖屏，宽建议 360px 左右展示），放到 screenshots/mobile.png -->
<p align="center">
  <img src="screenshots/mobile.png" alt="手机端对话页" width="360" />
</p>

### 3. 🔬 流程编排：不是黑盒报告，是人机深度协作

各平台都有 "流程编排 / Deep Research"，但几乎都是同一种形态：提问 → 漫长等待 → 一份无法干预的长报告，过程是黑盒，方向偏了只能推倒重来。

我们的流程编排核心是**人与 AI 的深度协作**——分析过程本身就是一块双方都能编辑的白板：

1. **你提出问题**：例如 "帮我深度对比这两套技术方案的优劣"
2. **AI 构建画板**：自动把问题拆解成一张可视化的工作流画板，任务卡片、执行顺序一目了然
3. **人机协作调整**：你可以直接在画板上增删卡片、调整顺序、修改任务描述、补充上下文——**即使在 AI 响应期间也能随时编辑**
4. **AI 按画板执行**：逐任务推进，每个卡片的中间结果实时回流画板，进展全程可见
5. **多轮迭代收敛**：不满意就继续调整画板，AI 接着干，直到得到你要的结果

画板跨会话持久保存、可复用——同类问题二次分析，直接套用上次的工作流。

<!-- 截图占位：流程编排画板截图（展示任务卡片与执行状态），放到 screenshots/deep-analysis-board.png -->
<p align="center">
  <img src="screenshots/deep-analysis-board.png" alt="流程编排工作流画板" width="100%" />
</p>

<p align="center">
  <img src="screenshots/deep-analysis-board-detail.png" alt="流程编排工作流画板" width="100%" />
</p>

<p align="center">
  <img src="screenshots/deep-analysis-board-detail-html.png" alt="流程编排工作流画板" width="100%" />
</p>

### 4. 🧩 技能管理：零经验构建自己的 Skill

技能（Skill）是 Agent 的 "操作手册 + 工具脚本" 组合，遵循 [Anthropic Agent Skills 规范](https://agentskills.io)。围绕它我们做了一整套零门槛的生产与消费闭环：

- **零经验构建**：用一句话描述你想要的技能，AI 自动生成完整技能包（说明文档 + 脚本 + 依赖），无需懂任何规范细节
- **可视化编辑**：写一段 prompt 也能定义新助理，即改即生效
- **全网发现、自主安装**：让 AI 帮你从外部网络搜索、评估并一键安装社区技能
- **上传与原地升级**：zip 拖拽上传，已有技能可原地升级、保留数据
- **一键凝练**：每跑通一次任务，可一键凝练成新的 AI 助理 / 技能——下次 `@` 一下就能召唤，一次教会、永久可用
- **业务经验资产化**：经验从 "老同事脑子里" 变成 "团队随时调取的资产"

> 平台已筛选集成一批开箱即用的内置技能（设计、文档演示、浏览器自动化、开发工程、CAD 制造等），完整分类清单见下文「🧰 平台已筛选集成的 Agent 技能」一节。

<!-- 截图占位：技能管理/上传页截图，放到 screenshots/skill-manage.png -->
<p align="center">
  <img src="screenshots/skill-manage.png" alt="技能管理" width="100%" />
</p>

### 5. 🧠 知识库管理：由 AI 组织，而非笨蛋堆叠

传统笔记 / 知识库工具的通病：收藏一时爽，堆成山后再也不翻。本项目的个人知识库**全部由 AI 组织管理**：

- **多种内容形式**：不止知识碎片——灵感、待办、经验总结、结论沉淀，统统收纳
- **双入口沉淀**：对话消息上一键「📌 记住」；或在对话中自然说出 "记住这个"，AI 自动识别沉淀意图，无需打断当前工作
- **AI 全权整理**：每条新内容入库前，AI 先做语义检索，自主决定——重复则只记来源、相关则合并进已有篇章、新主题则开新篇并自动归类。**永远不产生零散碎片的无脑堆叠**
- **对话即资产**：对话内容不再阅后即焚，有价值的部分被 AI 组织进知识库，成为可二次翻阅的积累
- **双向服务**：人把它当笔记随时翻阅检索；AI 把它当长期记忆，在后续对话中自动检索复用——知识库真正 "越用越聪明"

<!-- 截图占位：知识库工作台截图，放到 screenshots/knowledge-base.png -->
<p align="center">
  <img src="screenshots/knowledge-base.png" alt="知识库工作台" width="100%" />
</p>

### 6. 🧬 AI 时代的代码库：一体性设计，AI 上手即改

这个项目本身就是「人机协作开发」的产物，也是为 AI 编程时代设计的代码库：

- **仓库自带 `CLAUDE.md` 工程手册**——不是写给人看的泛泛介绍，而是写给 AI coding agent 的完整上下文：架构分层、接口契约、错误码红线、缓存陷阱、部署注意事项，全部成文。用 Claude Code 打开项目，AI 立刻拥有专家级项目认知
- **全栈高度一体的约定**：响应码契约、模型配置块、工具注册、SSE 流式协议、前端请求层，处处同一套范式——AI 看懂一处即可举一反三改遍全局，几乎不存在"每个模块各写各的"的理解成本
- **一句话完成全栈修改**：描述需求，Claude Code / Codex 等工具可以直接完成「后端接口 + 前端页面 + 配置 + 权限数据」的整条链路修改
- 项目的绝大多数功能正是这样完成的：人提需求、把关验收，AI 负责实现——这套工作流本身也沉淀在 `CLAUDE.md` 里，可以直接复用

### 7. 🚀 更多能力

| 能力 | 说明 |
|------|------|
| **专家召唤** | `@专家名` 驻留式召唤：人设与方法论注入系统提示词，绑定技能/连接器随会话运行时生效；专家中心可自建、可上架共享 |
| **多模型热切换** | 内置 Qwen / Grok / Claude / Kimi 等对话模型 + GPT-image / Qwen-Image 图像生成 + Seedance / Grok Imagine 视频生成，超管页面一键全局切换，无需重启、凭据绝不出服务端 |
| **多模态理解** | 图片直接拖进对话，支持视觉的模型多模态直传，纯文本模型自动走视觉兜底 |
| **图像 / 视频生成** | 对话中直接生成图片、图生视频，产物以卡片形式在对话流中预览 |
| **文件协作** | 拖拽上传任意文件（10 GB 也行）：Word / Excel / PDF / 压缩包 / 代码 / 图像，看得懂、改得了、产得出可下载文件 |
| **浏览器自动化** | 内置 browser-use 技能，AI 可自主打开网页、点击、截图、抓取 |
| **联网搜索** | MCP 协议接入外部搜索（DashScope WebSearch 等） |
| **批次任务** | 一条指令对多条数据并发执行，前台实时看进度 |
| **图表渲染** | 内嵌柱图 / 折线 / 饼图 / 散点，AI 直接输出 chart JSON |
| **运行追踪** | 每一步工具调用、思考过程、产物登记全程留痕，可追溯、可审计 |
| **企业级底座** | 完整 RBAC 权限体系、JWT 双 Token 鉴权、操作审计、数据库驱动菜单——既能做 AI 平台，也能直接当中后台脚手架 |
| **极简部署** | 两条命令完成打包上线，源码即交付物；另支持离线全量包 / 仅代码更新包 |

<!-- 截图占位（可选）：模型切换面板截图，放到 screenshots/model-switcher.png -->

## 🧰 平台已筛选集成的 Agent 技能

平台从社区与官方技能中**筛选并集成了一批开箱即用的 Agent 技能（Skills）**——每一项都让 Agent 获得一类专业能力，在对话中按需 `@` 加载即可触发，无需任何额外配置。技能遵循 [Anthropic Agent Skills 规范](https://agentskills.io)，独立、可移植、可二次开发；你也可以用「技能管理」一句话自建、上传升级或让 AI 全网发现安装。

以下为当前平台已集成的内置技能，按用途分类：

### 🎨 设计与创意视觉

| 技能 | 用途 |
|------|------|
| `canvas-design` | 平面 / 海报视觉设计，运用设计哲学产出 PNG、PDF 静态作品（坚持原创、不抄袭现有艺术家） |
| `algorithmic-art` | 基于 p5.js 的生成式 / 算法艺术（流场、粒子系统等），带种子随机与可交互参数探索 |
| `brand-guidelines` | 套用 Anthropic 官方品牌色与字体规范，让产物具备统一、专业的视觉调性 |
| `theme-factory` | 10 套预设主题（配色 / 字体），给幻灯片、文档、报告、落地页一键换肤，也可即时生成新主题 |
| `ui-ux-pro-max` | Web / 移动端 UI/UX 设计智能：50+ 风格、161 配色、57 字体搭配、99 条 UX 准则，覆盖 React / Vue / Flutter 等 10 套技术栈 |
| `gpt-image-2-style-library` | GPT-Image2 视觉风格库与工业级提示词模板，用于创建 / 改写 / 分类 / 优化生图提示词 |
| `slack-gif-creator` | 制作适配 Slack 的动画 GIF，含尺寸约束、校验工具与动画概念 |

### 📑 文档 · 演示 · 图表

| 技能 | 用途 |
|------|------|
| `ppt-master` | AI 驱动的演示文稿工作流：生成可编辑 PPTX、重建页面视觉、套用品牌 / 版式模板、美化增强、加旁白与动画 |
| `visiomaster` | 把流程图 / 架构图 / 论文模块图重建为**可编辑** Visio `.vsdx`，并导出 SVG、PNG（而非贴一张截图） |
| `office-cli` | 用 officecli 创建、分析、校对、修改 Office 文档（Word `.docx` / Excel `.xlsx` / PPT `.pptx`），支持加图表、查格式问题 |
| `pdf` | PDF 全套处理：读取 / 提取文本表格、合并拆分、旋转、加水印、填表单、加解密、提取图片、扫描件 OCR |
| `excalidraw-diagram-generator` | 自然语言生成 Excalidraw 图表（流程图、关系图、思维导图、系统架构图），输出可直接打开的 `.excalidraw` |
| `doc-coauthoring` | 结构化文档协作写作工作流：高效传递上下文、迭代精修、验证可读性（提案 / 技术规范 / 决策文档等） |
| `internal-comms` | 撰写各类企业内部沟通文案：状态报告、领导层更新、公司简报、FAQ、事故报告、项目进展等 |

### 🌐 浏览器 · 网页 · 自动化

| 技能 | 用途 |
|------|------|
| `browser-use` | 经 CDP 直控浏览器：网页交互、自动化、抓取、测试、截图 |
| `browser-skill` | 基于用户**已登录浏览器**做自动化：访问 / 阅读页面、填表、抓数据、点击流程、回归测试 UI（需 bsk CLI + 浏览器扩展） |
| `crawl4ai` | 网页爬取与结构化数据提取完整工具集，擅长 JS 重页面、多 URL 批量抓取、自动化数据管道（开源官方包，保持出厂原样） |
| `webapp-testing` | 用 Playwright 与本地 Web 应用交互并测试：验证前端功能、调试 UI 行为、截图、查看浏览器日志 |

### 🛠️ 开发 · 工程 · 制造

| 技能 | 用途 |
|------|------|
| `claude-api` | 构建 / 调试 / 优化 Claude API、Anthropic SDK 应用，含提示词缓存与跨模型版本迁移 |
| `mcp-builder` | 构建高质量 MCP（Model Context Protocol）Server，对接外部 API / 服务（Python FastMCP 或 Node / TS SDK） |
| `skill-creator` | 创建、改进、评测技能本身：从零写技能、跑 eval、做性能基准、优化触发描述 |
| `web-artifacts-builder` | 构建复杂多组件 Web artifact（React + Tailwind + shadcn/ui），含状态管理与路由 |
| `threejs-skills` | Three.js / WebGL 3D 场景开发全套：建模、材质、光照、动画、着色器、后处理、渲染优化，含 Rapier 物理 |
| `text-to-cad` | CAD / 机器人 / 制造全套：AutoCAD、中望 CAD、绘图制图、3D 建模、STEP / DXF、CNC / G-code、3D 打印、URDF / SRDF、钣金、标准件 |

### ✍️ 文本润色

| 技能 | 用途 |
|------|------|
| `humanizer-zh` | 去除中文文本里的 AI 生成痕迹：依据维基百科「AI 写作特征」修复夸张象征、宣传性语言、破折号滥用、三段式、AI 词汇等，使行文更自然、更像人写 |

> 以上为平台内置的**便携技能**（portable skills，自给自足、可跨项目移植）；另有一组与本项目业务深度耦合的 `_project` 专属技能（标准库查重、指标提取、对象关系推断等），随行业版交付、不在通用清单内。

## 🏗️ 技术架构

<!-- 架构图占位：整体架构图（建议展示：浏览器 / 手机 → Nginx → FastAPI → Agent 运行时 → OceanBase / Redis / 外部模型 API），放到 docs/screenshots/architecture.png -->
<p align="center">
  <img src="screenshots/architecture.png" alt="整体架构图" width="100%" />
</p>

### 后端（`/app`）

```
Router → Controller → CRUD / Model
                ↓
        Agent Runtime
           ├── DeepSeek Harness (dsh)   ← 对话 Agent 官方运行时内核
           │     ├── 子进程 + JSON-RPC over stdio 驱动（与后端进程隔离）
           │     ├── 原生工具栈：bash / 文件 / todo / subagent / compaction
           │     ├── 原生 skill 加载 + MCP 客户端（外部工具接入）
           │     ├── 原生 read_image 多模态读图
           │     └── 带外取消（真实可中断）+ 会话 JSONL 留档
           └── LangChain / LangGraph    ← 结构化任务 Agent（比对 / 提取 / 推断）
```

- **Agent 内核**：对话 Agent 跑在 DeepSeek 官方运行时 **DeepSeek Harness（dsh）** 上，SDK 以子进程 + JSON-RPC 驱动、与后端进程隔离；结构化任务 Agent 仍由 LangChain / LangGraph 驱动
- **FastAPI** + **Tortoise ORM**：异步优先，单 worker 事件循环严格无阻塞
- **SSE 流式协议**：实时推送 token、工具调用、推理痕迹
- **JWT 鉴权**：access token（12h）+ refresh token（7d）双 Token 机制，前端静默刷新
- **API 自注册**：启动时把路由元信息写入 DB，权限系统即插即用
- **模型配置两层架构**：角色块（对话/视觉/Embedding）+ 能力块（图像/视频），激活块重定向机制实现消费方零改动的全局热切换

### 前端（`/web`）

- **Vue 3** + **Vite 7** + **TypeScript** + **Naive UI** + **Pinia**
- **Elegant Router**：基于文件结构自动生成路由，菜单由数据库驱动
- **pnpm monorepo**：HTTP / hooks / utils 等内部包独立维护
- **SSE 流式渲染**：边收 token 边渲染 Markdown / 工具调用 / 思考过程
- **工作流画板**：可视化拖拽编辑，AI 响应期间不锁死编辑权

### Skill 体系

每个技能独立、可移植、可跨项目复用，结构遵循 Anthropic Agent Skills 规范，并经 dsh 原生 skill 机制按需加载进运行时：

```
.agent_skills/<skill-name>/
├── SKILL.md           # YAML frontmatter（name + description）+ 操作说明
├── scripts/           # 可执行脚本
├── references/        # 按需加载的详细文档
└── assets/            # 模板/资源文件
```

## 🚀 快速开始

### 方式一：Docker 部署（快速体验）

```bash
git clone <repo>
cd <project>

# 配置环境变量
cp .env.example .env       # 填 LLM API Key 等
cp web/.env.example web/.env

# 启动（Nginx :1880 + FastAPI :9999 + Redis）
docker compose up -d --build

# 查看日志
docker compose logs -f app
```

访问 `http://localhost:1880`。

### 方式二：本地开发

环境要求：

| 工具 | 版本 |
|------|------|
| Python | ≥ 3.12 |
| Node.js | ≥ 20.19 |
| pnpm | ≥ 10.5 |
| MySQL / OceanBase | 任意（业务数据库） |
| Redis | 任意（缓存可选） |

```bash
# 后端依赖
pdm install

# 前端依赖
cd web && pnpm install && cd ..

# 启动后端（端口 9999）
python run.py

# 新开终端，启动前端（端口 9527）
cd web && pnpm dev
```

### 关键环境变量

```bash
# 主对话 LLM（OpenAI 兼容协议；默认块为 CHAT_DASHSCOPE，可再配多个预设块供超管切换）
CHAT_DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
CHAT_DASHSCOPE_API_KEY=sk-xxxx
CHAT_DASHSCOPE_MODEL=qwen-plus
CHAT_DASHSCOPE_VISION_SUPPORTED=true

# 可选：视觉理解 / Embedding
VISION_BASE_URL=... / VISION_API_KEY=... / VISION_MODEL=...
EMBED_BASE_URL=... / EMBED_API_KEY=... / EMBED_MODEL=...

# 可选：图像 / 视频生成
IMAGE_PROVIDER=apipod / IMAGE_API_KEY=... / IMAGE_MODEL=...
VIDEO_PROVIDER=ark    / VIDEO_API_KEY=... / VIDEO_MODEL=...

# 可选：联网搜索（DashScope MCP）
DASHSCOPE_API_KEY=...

# 业务数据库
STANDARD_MYSQL_HOST=... / STANDARD_MYSQL_PORT=... / STANDARD_MYSQL_USER=...
STANDARD_MYSQL_PASSWORD=... / STANDARD_MYSQL_DB=...

# JWT
SECRET_KEY=<生成一段随机串>
```

完整配置见 `.env.example`。

## 📦 部署与打包

**极简部署**是本项目的一大特色：不依赖 CI、不依赖镜像仓库，**源码即交付物**——日常更新全链路只有两条命令：本地 `python pack_deploy.py` 一键打包，服务器 `bash restart_cesi-fast-admin.sh` 一键解压并拉起，从改完代码到线上生效一气呵成。

正式交付走内置的一键打包脚本，覆盖从在线服务器到离线内网的部署场景：

| 脚本 | 产物 | 适用场景 |
|------|------|------|
| `pack_deploy.py` | `deploy_package_<时间戳>.zip` | 在线源码部署：整包源码上传服务器，Docker 构建启动 |
| `pack_deploy_offline.py` | `offline_package_*.zip` / `update_package_*.zip` | 离线内网部署：镜像 + 前端全量包，以及仅代码的日常更新包 |
| `pack_desktop.py` | `<应用名>安装包.exe`（Electron / NSIS）+ 绿色免安装目录 | Windows 桌面单机版（Electron 壳，已完成适配） |

### 在线源码部署

```bash
python pack_deploy.py
# 交互式选择品牌变体（standard 内网 1880 / generic 外网 80+443）
# 本地预构建前端（web/dist 随包分发，服务器零 node 编译）→ 产出 deploy_package_<时间戳>.zip
```

上传至服务器后：

```bash
bash restart_cesi-fast-admin.sh   # 一键解压（保留服务器 .env.prod）+ 自动选择最快部署路径
```

- 脚本自动检测依赖变更：`pdm.lock` / `pyproject.toml` 未变 → **跳过镜像重建，仅重启 app**；有变更或首次部署 → 自动 `--build`
- 前端产物由 nginx 直接挂载：**前端更新无需重建、无需重启**
- 依赖在构建期烘焙进镜像、源码运行时挂载；手动强制重建：`docker compose up -d --build`

### 离线内网部署（目标机器无网络）

```bash
python pack_deploy_offline.py
# 完整打包（首次部署）：构建 app 镜像 → docker save 导出 tar → 构建前端
#   → offline_package_<时间戳>.zip（含全部镜像与 deploy_offline.sh）
# 仅打包代码（日常更新）：→ update_package_<时间戳>.zip + update.sh
```

目标机器上：

```bash
# 首次部署
unzip -O UTF-8 offline_package_*.zip && cd cesi-fast-admin && bash deploy_offline.sh

# 日常更新
bash update.sh update_package_<时间戳>.zip   # 覆盖代码 + restart app，前端自动生效
```

### Windows 桌面单机版

桌面端采用成熟的 **Electron** 技术（`electron-builder` 编译、NSIS 中文安装向导），已完成适配：

```bash
python pack_desktop.py   # 产出可双击安装的 <应用名>安装包.exe（Electron / NSIS）+ 绿色免安装目录
```

嵌入式 Python + 便携 Redis 全部打进安装包，数据库与大模型可保持远程，终端用户装完即用。Electron 壳负责单图标生命周期管理：启动即拉起后端 + Redis，关窗即优雅退出，无需手动启停。

## 📁 项目结构

```
.
├── CLAUDE.md                  # AI coding agent 工程手册（项目约定 / 红线 / 陷阱）
├── app/                       # 后端
│   ├── api/v1/                # FastAPI 路由（auth / system-manage / ai/…）
│   ├── controllers/           # 业务逻辑层
│   ├── models/                # ORM 模型（系统表 + 业务表）
│   ├── schemas/               # Pydantic schema（camelCase 出参）
│   ├── core/                  # 应用工厂、鉴权依赖、中间件、CRUD 基类
│   ├── langchain/             # AI 子系统：Agent / Tool / 模型配置 / MCP
│   └── services/              # Agent 运行时、知识库、外部生成 API client
├── web/                       # 前端 monorepo
│   ├── src/views/ai/          # 对话工作台 / 知识库 / 流程编排画板 / 技能管理
│   ├── src/views/system/      # 用户 / 角色 / 菜单 / API 管理
│   └── packages/              # 内部工具包
├── .agent_workspace/          # Agent 隔离工作目录（运行时生成）
│   └── .agent_skills/         # 技能包目录，每个技能独立可分发
├── deploy/                    # Docker / Nginx / Electron 桌面壳配置
├── pack_deploy.py             # 在线源码部署一键打包
├── pack_deploy_offline.py     # 离线内网部署一键打包（全量 / 增量）
├── pack_desktop.py            # Windows 桌面单机版打包（Electron / NSIS）
├── tests/
└── docker-compose.yml
```

## 🗺️ 路线图

- [x] Agent 内核切换至 **DeepSeek Harness（dsh）** 官方运行时 + 多层 subagent 编排
- [x] 流程编排工作流画板（人机协作编辑）
- [x] 技能体系：内置 / 凝练 / 自建 / 全网发现 / 上传升级
- [x] AI 托管个人知识库（碎片 / 灵感 / 待办）
- [x] 图像 / 视频生成、浏览器自动化、联网搜索
- [x] 超管多模型全局热切换
- [x] **桌面客户端**（Windows 单机版基于成熟 **Electron** 技术完成适配，`pack_desktop.py` 一键打包 NSIS 安装程序）
- [ ] 技能市场（在线分享、一键安装）
- [ ] 更多内置技能（音频生成等）

## 🤝 参与贡献

欢迎提交 Pull Request 或 Issue。Bug 修复、新技能、新工具、文档完善都非常欢迎。

<a href="https://github.com/2842902295/agent-deepseek/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=2842902295/agent-deepseek" alt="contributors" />
</a>

## 🙏 致谢

本项目站在以下优秀开源项目的肩膀上：

- [DeepSeek Harness (dsh)](https://www.npmjs.com/package/@deepseek-ai/dsh) — 对话 Agent 官方运行时内核
- [FastAPI](https://fastapi.tiangolo.com/) / [Pydantic](https://docs.pydantic.dev/)
- [LangChain](https://github.com/langchain-ai/langchain) / [LangGraph](https://github.com/langchain-ai/langgraph) — 结构化任务 Agent
- [Anthropic Agent Skills](https://agentskills.io)
- [Electron](https://www.electronjs.org/) / [electron-builder](https://www.electron.build/) — Windows 桌面单机版
- [SoybeanAdmin](https://github.com/soybeanjs/soybean-admin)
- [Tortoise ORM](https://tortoise.github.io)
- [Naive UI](https://www.naiveui.com/)

## ⭐ Star 趋势

<a href="https://www.star-history.com/?repos=2842902295%2Fagent-deepseek&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=2842902295/agent-deepseek&type=date&theme=dark&legend=top-left&sealed_token=AEZkII86VqzcTsJ9v8cXRnqgPm1DW8PiSjqb3MRuzjD2UgVS_y5WA3mqExPaeIZ-y7uf9H7uW-G4tgZYr-B0BH-3aoSLpmGNWg6kGT79VVgkavyGZGAKMzNEg4OjbbmvJ5xU7EFAa62EFZKAmIbEmgHMyuyFXhRiZnzsTbXrKLhr2FxYYMD8XtUHzAvK" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=2842902295/agent-deepseek&type=date&legend=top-left&sealed_token=AEZkII86VqzcTsJ9v8cXRnqgPm1DW8PiSjqb3MRuzjD2UgVS_y5WA3mqExPaeIZ-y7uf9H7uW-G4tgZYr-B0BH-3aoSLpmGNWg6kGT79VVgkavyGZGAKMzNEg4OjbbmvJ5xU7EFAa62EFZKAmIbEmgHMyuyFXhRiZnzsTbXrKLhr2FxYYMD8XtUHzAvK" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=2842902295/agent-deepseek&type=date&legend=top-left&sealed_token=AEZkII86VqzcTsJ9v8cXRnqgPm1DW8PiSjqb3MRuzjD2UgVS_y5WA3mqExPaeIZ-y7uf9H7uW-G4tgZYr-B0BH-3aoSLpmGNWg6kGT79VVgkavyGZGAKMzNEg4OjbbmvJ5xU7EFAa62EFZKAmIbEmgHMyuyFXhRiZnzsTbXrKLhr2FxYYMD8XtUHzAvK" />
 </picture>
</a>

## 📄 开源协议

[MIT License](./LICENSE) © 2026

可自由使用与修改，商业使用请保留作者版权信息。
