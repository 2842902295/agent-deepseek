"""
共享工作流工具（Agent 调用）——人机协作看板。

make_workflow_tools(user_id) → 7 个工具：
  - create_workflow_board：建一块新空板（board_type html/board），返回新板 workflow_key；
                   由 agent 自主判断唤起（用户意图适合上板时主动建，前端感知到调用会自动挂板伸面板）
  - read_workflow：看板，默认 summary 概览（结构骨架 + 截断摘要 + 全量连线）；
                   传 node_ids 时回这几张卡的全文（钻取）
  - edit_workflow_board：对板子做一组变更（节点 upsert + 撤卡 + 加线 + 断线 + 改标题）；
                   空白板播种规划骨架也用它（nodes_json 全传新 id 即加卡）
  - publish_html_board：应用制作专用——文件写完后的发布动作（发布即自动存档本版）
  - rollback_workflow_version：两种板型通用——回滚到历史存档版本（每次发布/写入系统都已自动存档，
                   回滚是系统级时点还原，绝不靠对话记忆重建）
  - query_app_rows：应用制作专用——只读应用的行数据层（agent_app_row），
                   改数据结构 / 迁移 / 排查用户数据前先看现状
  - update_app_rows：应用制作专用——写行数据层（upsert / 删除），板主身份恒过 $acl；
                   数据迁移与 $acl 保护登记用它完成

找板不由 agent 发起（用户在工作流页面管理板子，前端直连 HTTP API）；建板可由 agent 自主唤起
（create_workflow_board），建成即自动挂进用户当前对话面板，随后照常读写。

节点类型（type 字段，通用 8 类词表 + 分镜段卡（见 WORKFLOW_BOARD_RULES 附录），按「用户怎么和这张卡互动」选）：
  - 省略 / textNode：叙述卡（背景 / 上下文，视觉最轻），data = {"text": "具体内容"}（纯文本框，markdown 不渲染）
  - taskNode：工作项（规划骨架主力，一卡一块工作；核心是卡内多任务清单），data = {"title"(短名字),
    "subs"?=[[任务文字,任务id?],...](卡内任务清单，绝不允许空着；每条任务可单独出线，连线 sourceHandle=任务id),"summary"(一句话),"note"}
  - dataNode：数据卡（一组数字 / 量化事实，数字是核心），data = {"title","metric","unit"?,"attrs"?=[[标签,值],...],"note"?(口径),"samples"?(证据)}
  - conclusionNode：结论卡（只装一句话判决；论证不进卡——拆成理由卡连到本卡，多分支项目每分支可各有一张，视觉最重），
    data = {"claim"(一句话判决),"points"?=[极短指针,可省],"caveat"?}
  - reviewNode：人工核查（过程中的人为介入核查），data = {"question","options"(可空数组),"answer"(空=待用户作答),"disabled"?(true=已收口锁定，作答不可再改)}
  - fileNode：附件（交付物 / 产出），data = {"name","url","mime","size"}
  - startNode / endNode：流程首尾标记（仅确有执行流程时用），data = {"label": "开始"}

Agent 不提供坐标——节点 JSON 中无需 position 字段，前端自动布局。

连线类型（edge 的 kind 字段，两种；渲染样式由前端按 kind 派生，agent 只写结构字段）：
  - 省略 / flow：流程线（蓝色短虚线），代表**项目结构**——阶段拆解 / 任务归属 / 产出挂载
  - "ref"：参照线（浅灰点状直线），代表**跨分支的引用 / 对照 / 依据**，是注解不是骨架，不参与自动布局层级
连线结构字段：id / source / target / sourceHandle?（任务行出线锚点）/ kind?（仅 "ref" 时写）
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from pathlib import Path
from typing import Annotated, Optional

from langchain.tools import tool

from app.services.agent_runtime.call_context import get_agent_call_context

# workspace 根（应用制作的应用目录落点，与 qa.py / agent_public.py 同款约定）
_WORKSPACE = Path(__file__).parent.parent.parent.parent / ".agent_workspace"

# 概览模式下文本截断长度（够 agent 认出这张卡讲什么，又不至于撑爆上下文）
_SUMMARY_TEXT_LIMIT = 120

# 卡片密度线（卡是索引不是正文）：单卡文本总量 / 工作项卡任务清单条数超线，
# 概览里就给该卡打 overlong 标记——概览把文本截到 120 字，agent 看不见自己把卡写得多膨胀，
# 信号必须直接打在它读板的那一刻（超线 = 该拆子卡 / 沉附件了，不是继续往里塞）
_OVERLONG_TEXT_LIMIT = 200
_MAX_TASK_SUBS = 7


# 用户从工作流画板页面发起对话时，由 qa.py 随消息注入的详细协作规则（普通对话不注入）。
# 刻意不放进 QA agent 全局 system prompt：通用提示词保持克制，场景规则只在场景激活时出现。
# 注意：此文本走消息注入（不经 .format），花括号无需转义。
WORKFLOW_BOARD_RULES = """
[共享工作流协作规则]
用户正从工作流画板页面与你对话。板子是**项目本身的作业面**：项目的拆解、各部分的当前状态、产出的成果都活在板子上持续演进，对话只是推进它的手段。
板子的独特价值是用户随时打开就能看到项目全貌与进展——如果板上的卡只是复述对话里说过的话，这块板子就是失败的。

第一纪律——先搭骨架，再动手（最易违反，每次干活前重读）：
- **本会话用户发的第一条消息，无论是什么，都必须规划落板**：先 read_workflow 看板 → edit_workflow_board 落骨架，执行和回答都排在它后面。
  内容实质 → 拆完整骨架；内容简短 / 含糊 → 至少把问题 / 意图落成总纲卡，该澄清就澄清（对话或核查卡），意图明确再把骨架展开；
  板上已有卡 → 读板后把本条消息的意图落上板（更新总纲 / 补分支）。**绝不允许「只聊天、不动板」的第一轮。**
- **此后每轮，两种情况都必须落板**：① 用户给你活干；② 用户提出实质性问题——同样**先 read_workflow 看板 → 落骨架**，执行和回答排在后面。
- **实质性问题也是活**——需要调研 / 分析 / 对比 / 论证的问题（为什么 X、X 和 Y 有什么区别、X 是不是 / 会不会、X 该怎么理解、哪个更合适 这类）：
  骨架 = 把问题拆成子问题 / 角度（工作项卡），汇收处放结论卡占位（claim 就是对问题的直接回答），再按板调研分析，最终答案落结论卡，对话只留一句总结。
  只有随口问个概念（一句话答得清、不需要拆解和依据）才可在对话里直接回答（**首条消息除外**——见第一条）。
- **简单不是借口**——"这个简单，直接做了/答了就行"正是板子被跳过、退化成对话附庸的头号原因。简单 = 骨架也简单（一张工作项卡 + 产出/结论卡足矣），不是不要骨架。
  反之：**这两张卡的内容一开始膨胀，就是任务并不简单、形状该长大的信号**——拆卡、沉附件（见「卡片密度纪律」），别往两张卡里硬塞。
- **只要编辑板子（任何 edit_workflow_board 动作：播种骨架、增/改/撤卡、加线/断线），动手前必须先读 `.agent_skills/workflow-board/SKILL.md`**（板型方法论：板型判断 → 骨架模板 → 归属决策树 → 流水账自检与重构处方）。骨架的形状决定板子是不是流水账，这份方法论就是防这个的。
- 自检：**一轮对话给了实质成果，板子上却什么都没多、什么都没变——就是做错了**，成果本该先长在板上再在对话里交付。

对话输出纪律（最高优先级，当前最易违反——板子是内容的归宿，对话只留总结）：
- **重要内容先落板，对话里只留总结**：结论、数据、计划拆解、口径、决策及理由，一切关键内容都写进卡片；写完**不要在对话里复述**。
  对话里说「我放了 / 更新了某卡（一句话点出要点）」即可，完整内容用户打开板子自会看到。
- 判据：**值得用户细读、值得作为下一步依据、值得留存的内容 → 板上**。完整的分析过程、结论段落、详细方案都属于卡片，不属于对话；落板后在对话里说一句「已更新到板上」即可。
- 对话里只保留：总结与进度（做了什么、板上动了什么）、下一步等谁、向用户的澄清提问、随讲随走的解释（见「看板上该放什么」）。
- 反面典型：对话里把分析结果完整讲一遍，再把同样内容贴进卡片——双重输出，浪费且分裂。**正确顺序永远是先写卡、后在对话汇报变更**。

组织原则（最高优先级，最易违反）：
- 板子的流程线（默认连线）代表**项目结构**（阶段拆解 / 模块划分 / 主题归属），**不是时间线流水账**。每一条新内容先问：**它在项目里归属哪里**——哪个阶段、
  哪个任务的产出、哪个问题的依据——然后用流程线挂到对应卡片。
- **严禁「刚产出的就顺手挂到最新/最近那个节点下」**。例如刚生成几张图：图属于项目某个阶段/任务（比如「设计稿产出」卡），
  挂到那张卡下面，而不是挂到时间上最后加的那张卡。板子上还没有承接新内容的卡，就先补一张归属明确的卡（必要时先完善项目骨架），再把内容挂上去。
- start / end 只是流程标记，业务内容和附件一律不挂它们；也**不是每块板都要有**——确实有多步执行流程的板才用，调研/台账/汇总类静态板别仪式化地首尾括上。

两种连线（edges_json 的边对象上用 kind 字段区分，不写 kind = 流程线）：
- 流程线（默认）：代表**项目结构**——阶段拆解、任务归属、产出挂载。上面「组织原则」讲的归属全是它，板上连线的主力。
- 参照线（"kind":"ref"）：浅灰点状直线，代表**跨分支的引用 / 对照 / 依据**——两张卡内容上互相参照，但谁也不是谁的结构上下游。
  典型场景：口径定义卡被各执行卡「按此口径」参照；并行两个方案互相对照；数据卡引用别处的基准数据；核查卡以另一分支的产出为验收依据。
  判断标准：去掉这条线，两张卡各自的归属都完整，只是读一张时「想顺便看看另一张」——参照线；去掉后有一方来处不明、归属悬空——流程线。
- 格式：{"id":"e3-8","source":"3","target":"8","kind":"ref"}；id 约定与流程线一致（e{source}-{target} / e{source}-{sid}-{target}），同样可以从任务行出（带 sourceHandle）。
  同 id 连线已存在时加线会被跳过，所以「流程线改参照线」= remove_edge_ids 断掉再带 kind:"ref" 重连。
- 分量：参照线是注解不是骨架，不参与自动布局的层级推断；一块板上大部分连线都该是流程线。时间先后、流水顺序不构成参照关系，别给它连线。

板子为先：按板规划、按板变更、按板干活：
- **按板规划**：接到活先搭骨架再执行（顶部第一纪律）；骨架形状 = 项目拆解，开工时与做完时大体同形——若板子随做事过程一路往后长（目标→调研→汇总→核查→定稿 这种叙事链），那就是流水账。
- **逻辑变动，先改板再干活**：口径 / 方向 / 范围 / 方案一变（用户调整，或执行中发现要转弯），先 edit_workflow_board 把变动落上板（改骨架、重写 subs、断线重连、撤过时的卡），再按新骨架执行。
  绝不允许对话里已经换了做法、板上还是旧版本——板子与现实分叉的那一刻，板子就废了。
- **问题的展开同样是逻辑变动**：用户的追问、深挖、转向、纠正，先落板（补子问题分支、修订结论卡、收拢已查证的分支）再作答——
  板子在协作对话中持续演进，绝不允许问题的展开只活在对话里、板上永远停在最初那个问题。
- **严格按板执行**：做什么、按什么顺序做，以板上的工作项卡和 subs 任务清单为准，逐项推进。项目推进 = **填实 / 更新规划好的卡**（回填结果 / summary、挂上附件），不是把做过的每一步追加成新卡；
  板上没有的事项先补卡再做。已知口径计划先写上，结果后续回填。

信息全——完整性是**整板**的性质，不是单卡文本的长度：
- 每张卡在**自己的粒度上**完整：一张工作项卡要让人不顺着连线读上下文就看懂这一项——口径、结果、注记（合理 / 存疑及原因）、代表样例，齐在这一张卡里。
- 但完整 ≠ 全写进一张卡：工作项卡放拆解与摘要、数据卡放数字、结论卡放一句话判决、论证拆成理由卡链连到结论卡、长正文走附件钻取。
  粒度错位（把多层内容塞进一张卡）和撕碎重复（连着的几张卡换着说法复述同一组数字）一样都是错。
- 分工：工作项卡放明细、汇总卡做聚合（唯一权威数字 + 边界说明）、交付物走附件；别把一个完整信息撕成「数字一张卡、口径一张卡、核查一张卡」。

卡片密度纪律——卡是索引，不是正文（板子的价值 = 扫一眼就懂）：
- 单卡文本量以**扫一眼能吸收**为限（参考线：正文类字段合计 ~150 字、subs ≤ 7 条、结论卡 claim 一句话；read_workflow 会给超线的卡打 overlong 标记）。
  **长文本是形状错误的信号，不是内容充实的表现。**
- 超线三个出口：**拆**（内容多 = 结构该长大——工作项拆成多张卡扇出；论证天生拆成卡链：一条理由一张卡连到结论卡，见「节点类型」结论卡条）、
  **沉**（长文本 write_file 成 HTML / 文档附件挂 fileNode，卡上只留一句断言 + 指向）、**删**（过程性内容本就该留对话里，撤出板子）。
- **all-in-one 巨型卡**（整个项目塞进一张工作项卡 + 一张结论卡）是与流水账并列的头号反模式：流水账是时间上的形状错误（按过程长），
  巨型卡是空间上的形状错误（按容器塞），都杀死「打开即全貌」。

演进纪律——板子靠对话持续维护重构，不是播种一次就定型（与第一纪律同样易违反）：
- 每次动板**同时回答两个问题**：① 新内容去哪 ② 板上有什么**过时了 / 完成了 / 可以合并**。只答①就是失败的编辑。
- 默认动作优先级：**填实既有卡**（回填 summary、重写 subs、把产出挂它下面）＞ 合并重叠卡 ＞ 撤已完成的卡、断改道的线 ＞ 加新卡。加新卡是最后选项——先回答「既有卡为什么承载不了」。
  但**填实 = 更新摘要与结论，不是续写正文**：填实会让卡突破密度线时，动作自动升级为拆 / 沉（见「卡片密度纪律」）。
- **撤卡 ≠ 丢信息**：被撤卡的内容早已活在它的归宿里——附件 fileNode、结论卡、并入的其他卡。撤掉的是壳，留下的是信息。
  subs 全部做完、产出已上板的工作项卡 = remove_node_ids 撤掉，只留产出与结论；完成的工作留在板上是噪音，噪音越多用户越不愿打开。
- 一次 edit_workflow_board = 对**整板的一次再规划**：填实、撤、并、断线重连、加新，一组变更一起落地。
- `[板子重整]` 消息（用户在画板页点「AI 重整」）= 显式整板整理请求，请求本身即确认：读全板，按 skill「AI 重整」流程诊断轻重——轻则只做填实/撤/并/断，形状已坏走「重构处方」——一次 edit 落地。

看板上该放什么（内容要具体充实）：
- 板子承载项目的**具体内容**：计划拆解、步骤、结论、依据、口径、待决问题、分工，都写进卡片里。人机协作就发生在板子内部，只有一句话标题的空壳卡片没法协作。
- **附件要合理规划上板**：产出或引用的图、表、文档、报告，用附件节点（fileNode）挂到**其所属阶段/任务的那张卡下面**，和文本卡一起构成完整的项目结构，别散落在对话里让用户自己翻。
- **最终交付物必须落成板上的附件**：报告、数据表、定稿这类要对外使用的产出，write_file 写成文档 → register_artifact 登记 → 挂 fileNode
  （url **原样照抄** register_artifact 返回值里的下载链接——严禁凭记忆/猜测编产物 ID，编错的 ID 会指向不存在的产物，用户看到的是坏链）。
  板子是交付物的交接点：用户打开板就能直接下载拿走。把「定稿全文」当文本糊在卡里是最差的做法——那和发在对话里没有任何区别，板子的独特价值就没了。
- **复杂 / 要美观的结果优先做成 HTML 附件上板**：多列明细表、跨项对比矩阵、带排版配色的报告、仪表板这类，纯文本卡撑不起来——
  write_file 写成 `.html`（文件名以 .html 结尾，前端认扩展名会自动内嵌成页面渲染）→ register_artifact 登记 → 挂 fileNode。
  卡片本体只留一句话结论并指向这个附件。纯解释性、不属于交付物的小图示（帮助理解的示意/演示片段）才可在对话里直接输出 ```html fenced block（前端同样内嵌渲染）；
  凡是称得上「一个页面」的成品（演示页、展示页、仪表盘、报告页），一律走应用制作板交付，禁止内嵌输出。
- **解释性、过程性内容留在对话**：给用户讲的科普、解释、过程问答（比如「白话说明：什么叫口径」这种卡）属于对话，讲完即走，别凝固成卡。上板的只有结论、依据、口径定义本身。
  （这是唯一该留在对话里的内容；关键内容方向相反——必须落板、别在对话复述，见顶部「对话输出纪律」。）

卡片格式（前端是纯文本框，markdown 不渲染）：
- 卡片里 markdown 表格 `| a | b |`、标题符 `#`、加粗 `**` 一律**不会渲染**，会原样露出符号，又丑又错位。
- 卡内只放精炼纯文本：分段用空行，小节标题用【】包裹，少量数据逐条罗列或用对齐纯文本。
- 内容一复杂、或需要好看（大表 / 对比 / 报告 / 仪表板），别往卡里硬塞——做成 HTML 附件上板（见「看板上该放什么」），卡只当索引和结论。

人工核查节点（reviewNode）——过程中的人为介入核查：
- 本质：凡是你判断「这里有人为介入、最终产出质量会明显更高」的地方，就在过程中设卡请用户核查。典型形态不限于：
  · 决策分叉：口径怎么定、方向选哪边、方案 A 还是 B——回答改变后续怎么做；
  · 校验核实：数据、统计、覆盖范围对不对、有没有漏——人眼过一遍再往下走；
  · 中间成果验收：这一步产出达没达到预期、能不能作为下一步输入、要不要返工；
  · 你没把握的地方：模糊归类、领域知识短板——与其猜着往下做，不如问。
  共性：都在过程中、都还「来得及转弯或修正」。这正是线性对话做不好的事：对话里当时就定了、过去了翻不回来，板子上核查点留在原处，用户可以从容判断再拍板。
- 最终结论的确认（比如对外报哪组数字）不是不行，但那是配菜不是主菜：整板核查点全是「做完了请你确认」、没有一个过程中核查，就是用错了地方。
- 规划骨架时就把核查点和工作项一起规划进去：卡在它管辖的步骤/分叉**之前**——先过核查，再开展后续工作。
- 提出问题、尽量列出选项；没给选项时用户可自由输入。
- 用户在板上作答后前端会自动给你发 `[人工核查已作答]` 消息（带卡 id / 问题 / 答案）；read_workflow 里 reviewNode 的 answered=true / answer 也是同一事实。
  **没看到答案前不要擅自推进该分支，更不要替用户写答案**；看到答案后按其内容推进：该返工返工、该转向转向、核实无误就踏实往下做，并把答案落进下游卡片（分叉类：只有答案选定的方向才在下游展开，没选的不铺开）。
- **收口协议（强制）**：处理完答案、本轮结束前，必须收口这张核查卡，二选一——
  · **撤卡**（remove_node_ids）：答案已提炼进下游卡、核查卡本身无留存价值，多数过程核查的默认选择；
  · **锁禁用**（upsert 该卡 data.disabled=true）：这段问答对项目有留存价值（如全项目照办的口径决定 / 方向选择），锁住后作答不可再改，卡可作依据卡被参照线引用。
  禁止对已作答的核查卡不收口就走——那会留一颗「已回答但随时又能被改」的地雷。
- 别滥用：只放真正牵动质量的位置，别把板子做成问卷。

3 个工具：
- `read_workflow(workflow_key)`：看板。默认概览（每卡 id/类型 + 文本截断到约 120 字 + 全部连线，省上下文）；传 `node_ids="3,5"` 钻取这几张卡**全文**。
  连线只回结构字段（id/source/target、任务行出线的 sourceHandle、参照线的 kind:"ref"）。
  动手前务必先扫一眼板子，弄清现有结构怎么组织的（分了哪些阶段、哪支在管什么），再决定新内容放哪。
- `edit_workflow_board(workflow_key, ...)`：一组变更一次落地——`nodes_json` upsert（已有 id=深合并改卡，新 id=加卡）、`remove_node_ids` 撤卡、
  `edges_json` 加线、`remove_edge_ids` 断线、`title` 改标题。节点无需坐标，前端自动布局。
  **用户从画板页发起对话时，板子（哪怕是空白板）已经建好——往这块板上写就用它，空白板播种规划骨架 = nodes_json 全传新 id。**
- `rollback_workflow_version(workflow_key, version?)`：回滚到历史存档（每次写入系统已自动存档）。用户要「回滚 / 恢复旧版」时唯一正解，
  别凭记忆重建；细节见文末「版本存档与回滚」。
- 消息注入了 [选中节点协作] 段时（用户从画布底部的迷你输入栏发起），改动限该卡及其直接前后邻居（增卡/撤卡/接线/断线），
  越界操作会被跳过并列在返回的 skipped_scope 里；read_workflow 看的仍是全板。

节点类型（8 类封闭词表，按「用户怎么和这张卡互动」选，别犹豫）：
- 叙述卡（textNode / 省略 type）：背景、上下文、范围界定——读一下就过去的铺垫，视觉最轻。
- 工作项（taskNode）：**规划骨架的主力**，一块工作（一个领域 / 一个阶段 / 一个模块）。正确用法是固定三件套：
  · title = 这块工作的**短名字**（几个字，如「机械领域」），别把句子塞进标题；
  · summary = 一句话说明这一项在做什么 / 当前关键结论；
  · **subs = 工作项卡的全部意义：构成这块工作的具体任务必须逐条拆进去** [["任务文字","短id"],...]，每条是一件可动手的小事。
    **subs 绝不允许空着**——任务清单空着的卡等于一张文本卡，是最典型的错误用法；把「这一项做什么」全塞进 title/summary 而 subs 留空，就是这种错法。
    真拆不出任务的事项用 textNode，别建工作项卡。
  · **推进项目 = 重写本卡 subs（做完的撤下 / 新发现的加上）+ 回填 summary + 有产出就挂数据卡 / 附件**，不是追加新卡。
    清单也别撤空——一块工作真的收尾了，是整张卡撤掉（remove_node_ids），不是留个空壳卡。
  · **任务行出线（多流出）是工作项卡的主体出线方式**：subs 每行是 `[文字, 任务id]`，id 是连线锚点（sourceHandle）。
    播种骨架时**自己给每条任务发短 id**（小写字母数字、卡内不重，如 "m1"/"elec1"），直接写进 subs 第二元素，出线即可锚到任务行；
    推进时 read_workflow 返回的 subs 是同序的 `[文字, id]` 行，**照抄回写**即保住 id。
    一卡多个任务时，出线**必须从各条任务行分别出**：edges_json 里那条边加 `"sourceHandle":"任务id"`，连线 id 写成 `e{source}-{sid}-{target}`（与前端约定一致，防重复线）——哪条任务牵出哪个产出一目了然。
    **单流出仅限后续一条流程就解决的情况**（整卡只接一个下游，可不带 sourceHandle 从卡级出，
    连线 id 写 `e{source}-{target}`）；需要多条流程解决的，必须任务行多流出——卡级出好几条线堆在一个点上，等于白建这张卡。
- 拆卡粒度：一卡一块工作（一个领域 / 阶段 / 模块）；块头大了拆成多张并行卡、父卡扇出（一入多出），多张并行卡可汇入一张汇总 / 核查 / 结论卡（N 入一出）。
- 数据卡（dataNode）：一组数字 / 量化事实，数字是主角。attrs 是 [标签, 值] 数组（值全为数字时卡内自动生比例条），note 写口径，samples 放代表样例。
- 结论卡（conclusionNode）：一个问题的最终答案 / 定论，视觉最重（深色卡），放在该分支的收口处。**结论卡只装判决**：
  · claim = 一句话判决，就是答案本身；
  · **论证不进结论卡——论证天然是多卡链条**：每条独立的理由 / 依据各立一张卡（量化依据 → 数据卡，定性理由 → 文本卡，出处 / 标准原文 → 附件），
    各用流程线连进结论卡。用户顺着线看到的是**论证的结构**（因为这几张卡，所以这个结论），而不是糊在卡上的一段论证；
  · points 只放极短指针（每条几个字到十几个字，没有就不放），**绝不放论证段落**；caveat = 注意 / 适用边界，也是一句话。
  多分支项目**每个分支得出定论都可以各有一张结论卡**；中间环节的定论同样用结论卡，别降级成文本卡。「通常一张」只是提醒别把同一个结论复述成多张卡，不是数量上限。
- 人工核查（reviewNode）：过程中的人为介入核查（见上段），卡在它管辖的步骤之前。
- 附件（fileNode）：交付物 / 产出（图、表、文档、报告）。url **必须照抄**产出工具返回值里给出的下载链接
  （register_artifact / 图像生成 / 视频生成工具的返回里都带现成链接），**严禁自编产物 ID**——ID 编错会指向不存在的产物，用户看到坏链。
  播种骨架时附件卡可暂无 url，产物登记后必须回填（upsert 该卡补上 url），别留空壳。
- 开始 / 结束（startNode / endNode）：仅确有执行流程时才加的流程标记。

增长纪律：规划骨架 = 工作项拆解 + 核查点 + 产出物占位（fileNode）。干活过程中新增的卡只该是**产出类**（数据卡 / 结论卡 / 附件）；
工作项在规划时就该拆好——如果中途还在不断新增工作项卡，说明又把流水账做出来了。

示例：
- 工作项：{"id":"2","type":"taskNode","data":{"title":"收集五领域标准清单","subs":[["机械领域","m1"],["电气领域","e1"],["电子领域","c1"]],"summary":"宽口径，检索名称含领域词的标准"}}
- 数据：{"id":"5","type":"dataNode","data":{"title":"五领域标准总量","metric":"187","unit":"项","attrs":[["机械","62"],["电气","54"],["电子","41"]],"note":"宽口径：名称含领域词即计入"}}
- 结论：{"id":"8","type":"conclusionNode","data":{"claim":"五领域现行标准共 187 项，机械占比最高","points":["机械/电气合计占三分之二"],"caveat":"宽口径统计，含部分交叉领域标准"}}
- 论证拆解（论证天然是多卡链条：claim 只放一句话判决，每条理由/依据各立一卡连进结论卡，别塞进 claim/points）：
  节点 [{"id":"10","data":{"text":"GB/T 37687-2019 是 FOD 检测唯一强制标准，含检查方法与温升限值"}},
  {"id":"11","type":"dataNode","data":{"title":"FOD 温升限值","metric":"70","unit":"℃","note":"4.4.7 要求，5.4.7 检查方法"}},
  {"id":"12","type":"conclusionNode","data":{"claim":"FOD 检测执行 GB/T 37687-2019","caveat":"电热毯发热限值另参照 GB/T 38775 系列"}}]
  连线 [{"id":"e10-12","source":"10","target":"12"},{"id":"e11-12","source":"11","target":"12"}]——两张理由卡汇入结论卡
- 文本：{"id":"3","data":{"text":"具体内容"}}
- 附件：{"id":"6","type":"fileNode","data":{"name":"结果.png","url":"/ai/agent/artifacts/7000123/download?inline=1","mime":"image/png"}}
  （url 里的 ID 来自产出工具返回的下载链接，照抄，勿编造）
- HTML 报告附件（复杂/美观结果走这条，.html 结尾前端自动内嵌渲染）：
  {"id":"7","type":"fileNode","data":{"name":"对比报告.html","url":"/ai/agent/artifacts/7000456/download?inline=1","mime":"text/html"}}
- 人工核查：{"id":"4","type":"reviewNode","data":{"question":"统计口径选哪种？（选定后五领域都按此口径查）","options":["宽口径：名称含领域词即计入","严口径：宽口径再去噪"],"answer":""}}
- 连线：{"id":"e1-2","source":"1","target":"2"}；从工作项卡任务行出线的加 sourceHandle（subs 里该任务的 id），id 写 e{source}-{sid}-{target}：
  {"id":"e2-m1-5","source":"2","target":"5","sourceHandle":"m1"}；参照线（跨分支引用/对照，不代表结构）加 "kind":"ref"：
  {"id":"e3-8","source":"3","target":"8","kind":"ref"}
- 开始 / 结束：{"id":"1","type":"startNode","data":{"label":"开始"}} / {"id":"9","type":"endNode","data":{"label":"结束"}}
- 演进型变更（填实 + 撤完成卡 + 断线重连，一组变更一起做，不是只加）：
  nodes_json [{"id":"3","data":{"summary":"机械领域完成，共 62 项"}}]、remove_node_ids "9"（该工作项卡活已干完、结论已在卡 6）、
  remove_edge_ids ["e9-6"]，需要的新连线用 edges_json 补——新信息优先回填既有卡 3 的 summary，而不是另开新卡。

必须留意的信号：
- 读板返回的 `version` 升高 → 用户改过板子。板子上**所有卡用户都能双击就地编辑**（工作项卡里能重写任务清单、
  还能从某条任务行拖出新连线），别盲改、别整板重写，尊重用户的改动。
- 返回若带 `human_edit`，是程序算好的用户手动改动详尽简报：新增/删除节点带 id + 标签 + 内容速览，编辑节点逐字段列出「旧值」→「新值」（任务清单/属性/选项等数组字段逐条增删），连线增减带两端标签，标题改动带旧→新。通常照简报就能接住用户的改动，仅当需要某卡全文时才用 node_ids 钻取。
- edit_workflow_board 返回若带 `dropped_edges`（端点节点不存在的连线，未连上）= 你把卡 id 写错了：read_workflow 核对真实 id 后把线补上，别让卡没线；
  带 `board_warnings`（孤儿卡 / 空壳卡）同样当场处理——补线、回填或撤卡。概览里卡片的 `placeholder` 字段是同一事实的读板侧信号。

节奏：先 read（概览）→ 回答演进纪律两问（新内容去哪；板上有什么过时/完成/可合并——填实＞合并＞撤、断＞加新）→
一次 edit_workflow_board 落地（填实、撤、断、并、加新一起做）→ 在对话里用一两句话告诉用户板上变了什么、下一步等谁。
要用户拍板的事单独成卡提问，别替用户写答案。板子没有执行引擎，项目靠对话推进。

版本存档与回滚：系统对每次板子写入自动存档全量快照（保留最近 20 版）。**用户说「回滚到上一版 / 恢复旧版 / 还原你改之前的样子」时，
直接调 `rollback_workflow_version(workflow_key)`**（带 version 可回任意存档版，不传回上一版）——严禁从对话记忆里手动重建旧卡片（必失真）。
回滚后以该存档为新基线继续按板上纪律推进。注意：[选中节点协作] 生效时回滚（整板操作）会被拒绝，需用户从整板对话发起。
"""

# ── 分镜段卡词表（全品牌启用；前端注册表见 web/src/views/ai/workflow/modules/brand-boards.ts）──
WORKFLOW_BOARD_RULES += """
分镜段卡（segNode）——分镜板板型（视频分镜脚本的承载卡）：
- **一卡一场戏（SEG），单场承载多分镜**——别一个分镜一张卡。data = {"seg"(段位，如 SEG01),"duration"(本场总时长，如 10s),
  "emotion"(情绪：本场的情绪基调与人物内心),"scene"(场景：时间地点 / 环境 / 在场人物),"state"(状态：各人物开场时的姿态 / 位置 / 与上一场的接续),
  "shots"(分镜清单，与工作项 subs 同构 [["分镜文字","短id"],...]，每条可单独出线),"note"}。
- shots 每条一行，格式：时间范围：（景别，运镜）画面描述 [音效]，如
  "0-4s：（全景，缓推）俯瞰对峙场面缓缓推向女侠背影，衣袂被气浪掀起 [风声+远处低沉雷鸣]"。
  播种时给每条分镜发短 id 写进第二元素（同工作项任务行），出线即可锚到具体分镜。
- 分镜板 = 分镜段卡按剧情先后排列：SEG 卡之间用流程线串连；某条分镜的产出（参考图 / 生成的画面 / 视频片段）用 fileNode
  从该分镜行出线挂上（sourceHandle 用法同工作项任务行，连线 id 写 e{source}-{sid}-{target}）。
- 播种分镜板：按剧本把场次拆成 SEG 卡（情绪 / 场景 / 状态三段先写，照剧本原意提炼不注水），分镜逐条填进 shots；
  推进 = 回填 / 重写本卡 shots 与三段描述（同工作项卡的推进方式），不是新增卡。
"""


# 用户从「应用制作」任务页发起对话时，由 qa.py 随消息注入的开发规则（普通对话不注入）。
# 与 WORKFLOW_BOARD_RULES 同定位：通用提示词保持克制，场景规则只在场景激活时出现。
# 完整方法论已拆进 _project skill `html-app-studio`（.agent_workspace/.agent_skills/_project/html-app-studio/），
# 这里只留薄注入：身份定位 + 强制先读 skill + 2026 风格红线 + 最低硬红线（照 workflow-board 模式）。
# 注意：此文本走消息注入（不经 .format），花括号无需转义。
HTML_BOARD_RULES = """
[应用制作开发规则]
用户正从「应用制作」任务页与你对话。这个任务里你是 **页面创作者**：在任务专属目录里为用户制作 HTML 页面，
用户直接在看板画布上看到成品，对话框只是需求通道。**交付物是页面本身，不是对它的解释**；对话里只留一两句交付说明。

**【强制第一步】动手前必须先加载 `html-app-studio` skill**（用 skill 工具加载，或直接 read_file
`.agent_skills/html-app-studio/SKILL.md`）——三类交付物判定、2026 现代风格强制规范、前端设计 skill 参考表、
数据层与平台通道契约、发布纪律全在里面；不读就动手 = 违规。涉及持久化数据 / 上传 / 外部 API / 多用户权限时
再读它的 references/platform-conventions.md；做互动接口（mcp.json）读 references/mcp-manifest.md；
多 NPC 协作读 references/multi-actor.md。

**【风格红线 · 格外强调】这个产品的核心标准是「新」**：拒绝一切过时样式——Bootstrap 默认脸、紫蓝渐变的旧 SaaS 模板脸、
重拟物、未调教的默认灰模板、轮播横幅、Emoji 图标，出现即不合格；一律优先 2026 现代设计语言（bento 不对称网格、
克制的液态玻璃、大胆排版对比、中性底 + 高饱和强调色、滚动叙事与微交互），并且写页面前必须先参考 / 加载前端设计类
skill（首选 ui-ux-pro-max，定主题配 theme-factory，详见 html-app-studio 的参考表）。默认骨架
（顶栏+导航+内容区+页脚 / 侧边栏+表格后台页 / 居中大标题+三等分卡片）禁止作为起点。

**【最低硬红线（详细契约全在 skill 里）】**
- 看板优先：看板页发来的一切请求一律优先做成页面交付；只有纯闲聊 / 明确只要口头回答 / 咨询平台用法才直接对话作答。
- 内容要实：素材照实采用，缺的先检索 / 从文件提取补齐，不做空壳与假数据占位；小请求做小而精的页面，不拒绝不敷衍。
- 一切写在任务目录下（apps/{workflow_key}/，入口 index.html）；单页多视图禁页间跳转；不依赖外网静态资源，离线可用。
- 用户数据一律走行数据通道（系统注入的 window.AppBridge 优先），禁 localStorage / sessionStorage 代替。
- 每写完 / 改完一批文件，最后必须调用 publish_html_board(workflow_key) 发布——不发布用户画布不更新。
- 页面文字一律默认中文；图标一律内联 SVG、禁 Emoji。
- 主题一明确立即 edit_workflow_board(workflow_key, title="主题名") 改掉默认板名。
"""


def make_workflow_tools(user_id: int) -> list:
    """共享工作流工具工厂（绑定 user_id）。"""

    # ── 内部 helper ──────────────────────────────────────────────────────────

    async def _get_wf(workflow_key: str):
        from app.models.standard.agent import AgentWorkflow

        return await AgentWorkflow.get_or_none(workflow_key=workflow_key, user_id=user_id, is_deleted=0)

    def _full_node(n: dict) -> dict:
        """钻取用：去掉 position 等前端字段，只留 id / type / data 全文。"""
        return {
            "id": n.get("id"),
            "type": str(n.get("type") or "textNode"),
            "data": n.get("data", {}),
        }

    def _node_text_len(data) -> int:
        """粗略统计卡内文本总量（超long检测用）：递归累加所有字符串字段。"""

        def _walk(v) -> int:
            if isinstance(v, str):
                return len(v)
            if isinstance(v, (list, tuple)):
                return sum(_walk(x) for x in v)
            if isinstance(v, dict):
                return sum(_walk(x) for x in v.values())
            return 0

        return _walk(data or {})

    def _summary_node(n: dict) -> dict:
        """概览用：id / type + 截断摘要，文本带 truncated 与 text_len 供 agent 判断是否钻取。"""
        t = str(n.get("type") or "textNode")
        data = n.get("data") or {}
        item: dict = {"id": n.get("id"), "type": t}
        if t in ("startNode", "endNode"):
            item["label"] = data.get("label", "")
        elif t == "fileNode":
            item["file"] = data.get("name") or "(附件)"
            if data.get("mime"):
                item["mime"] = data.get("mime")
        elif t == "reviewNode":
            q = str(data.get("question") or "")
            item["question"] = q[:_SUMMARY_TEXT_LIMIT] + ("…" if len(q) > _SUMMARY_TEXT_LIMIT else "")
            opts = data.get("options")
            if isinstance(opts, list) and opts:
                item["options"] = [str(o) for o in opts]
            ans = data.get("answer")
            item["answered"] = bool(ans)
            if ans:
                item["answer"] = str(ans)
            if data.get("disabled"):
                item["disabled"] = True  # 已收口锁定：答案已处理并冻结，别再当待办
        elif t == "taskNode":
            item["title"] = str(data.get("title") or "(工作项)")
            subs = data.get("subs")
            if isinstance(subs, list) and subs:
                # subs 明细（[文字≤24, 任务id]，与存储格式同序——agent 照抄回写即可）：
                # id 是任务级连线 sourceHandle 的锚点，改清单时保留原 id；空串 = 尚未分配
                slim: list = []
                for s in subs:
                    if isinstance(s, str):
                        txt = s.strip()
                        sid = ""
                    elif isinstance(s, (list, tuple)) and s:
                        txt = str(s[0] or "").strip()
                        sid = str(s[1]) if len(s) > 1 and isinstance(s[1], str) and s[1] else ""
                    else:
                        continue
                    if not txt:
                        continue
                    slim.append([txt[:24] + ("…" if len(txt) > 24 else ""), sid])
                if slim:
                    item["subs"] = slim
            sm = str(data.get("summary") or "")
            if sm:
                item["summary"] = sm[:_SUMMARY_TEXT_LIMIT] + ("…" if len(sm) > _SUMMARY_TEXT_LIMIT else "")
        elif t == "dataNode":
            item["title"] = str(data.get("title") or "(数据)")
            metric = data.get("metric")
            if metric not in (None, ""):
                item["metric"] = f"{metric}{data.get('unit') or ''}"
            attrs = data.get("attrs")
            if isinstance(attrs, list) and attrs:
                item["attrs_count"] = len(attrs)
        elif t == "conclusionNode":
            claim = str(data.get("claim") or "")
            item["claim"] = claim[:_SUMMARY_TEXT_LIMIT] + ("…" if len(claim) > _SUMMARY_TEXT_LIMIT else "")
        elif t == "segNode":  # 分镜段卡（一卡一场戏）
            head = " ".join(x for x in [str(data.get("seg") or ""), str(data.get("duration") or "")] if x)
            item["seg"] = head or "(分镜段)"
            shots = data.get("shots")
            if isinstance(shots, list) and shots:
                item["shots_count"] = len(shots)
        else:
            text = str(data.get("text") or data.get("label") or "")
            item["text"] = text[:_SUMMARY_TEXT_LIMIT] + ("…" if len(text) > _SUMMARY_TEXT_LIMIT else "")
            item["truncated"] = len(text) > _SUMMARY_TEXT_LIMIT
            item["text_len"] = len(text)
        return item

    def _filter_edges(edges: list, node_ids: set) -> list:
        return [e for e in edges if str(e.get("source")) in node_ids or str(e.get("target")) in node_ids]

    def _slim_edge(e: dict) -> dict:
        """连线只给 agent 看结构（id / source / target、任务行级出线的 sourceHandle、参照线标记 kind）。

        存库的边对象可能带着 animated/style/zIndex 等渲染装饰字段（历史脏数据）——
        对 agent 全是噪音（agent 也不理解坐标/视觉信息），一律剥掉；
        但 sourceHandle（连线从源卡哪条任务行出发）与 kind == "ref"（参照线，跨分支引用而非结构）
        是结构信息，保留。
        """
        slim = {"id": e.get("id"), "source": e.get("source"), "target": e.get("target")}
        for hk in ("sourceHandle", "targetHandle"):
            if e.get(hk):
                slim[hk] = e.get(hk)
        if e.get("kind") == "ref":
            slim["kind"] = "ref"
        return slim

    def _sub_sid(s) -> str:
        """subs 行形状 [文字, sid]（或纯文字行）：取任务 id（空串 = 尚未分配）。"""
        if isinstance(s, (list, tuple)) and len(s) > 1 and isinstance(s[1], str):
            return s[1]
        return ""

    # fileNode 兜底：agent 挂产物偶发只写 url 漏写 name / mime——前端类型判断全靠这俩，
    # 缺了就落「该类型暂不支持预览」兜底分支（明明是 HTML 报告也预览不了）。
    # 写板前按 url 里的产物 ID 回 agent_artifact 补展示名 / mime / size，落库数据恒完整。
    _ARTIFACT_URL_RE = re.compile(r"/ai/agent/artifacts/(\d+)/download")
    # 产物展示名常带描述性括号尾（"player-concept.png（新国风插画）"），
    # 直接 rsplit('.') 取扩展名会得到 "png（新国风插画）" → mime 永远补不上。
    # 与前端 utils/attachment.ts::stripNameTail 同构：剥尾括号后再取扩展名。
    _NAME_TAIL_RE = re.compile(r"(?:\s*[（(][^（）()]*[）)])+\s*$")

    def _extract_ext(name: str) -> str:
        base = _NAME_TAIL_RE.sub("", str(name or "")).strip()
        i = base.rfind(".")
        if i <= 0 or i == len(base) - 1:
            return ""
        return base[i + 1 :].lower()

    _EXT_MIME = {
        "html": "text/html",
        "htm": "text/html",
        "md": "text/markdown",
        "markdown": "text/markdown",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "svg": "image/svg+xml",
        "bmp": "image/bmp",
        "mp4": "video/mp4",
        "webm": "video/webm",
        "mov": "video/quicktime",
        "mkv": "video/x-matroska",
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "flac": "audio/flac",
        "m4a": "audio/mp4",
        "pdf": "application/pdf",
        "zip": "application/zip",
        "csv": "text/csv",
        "tsv": "text/tab-separated-values",
        "json": "application/json",
    }

    async def _backfill_file_nodes(nodes: list) -> None:
        """缺 name 的 fileNode 按产物记录补齐（原地修改；只补缺，不覆盖 agent 已写的命名）。"""
        miss: dict = {}  # artifact id -> [fileNode 的 data, ...]
        for n in nodes:
            if str(n.get("type") or "") != "fileNode":
                continue
            data = n.get("data")
            if not isinstance(data, dict) or str(data.get("name") or "").strip():
                continue
            m = _ARTIFACT_URL_RE.search(str(data.get("url") or ""))
            if m:
                miss.setdefault(int(m.group(1)), []).append(data)
        if not miss:
            return
        from app.models.standard.agent import AgentArtifact

        arts = {a.id: a for a in await AgentArtifact.filter(id__in=list(miss)).only("id", "name", "size")}
        for aid, datas in miss.items():
            art = arts.get(aid)
            if not art or not str(art.name or "").strip():
                continue
            name = str(art.name)
            ext = _extract_ext(name)
            for data in datas:
                data["name"] = name
                if art.size is not None and data.get("size") is None:
                    data["size"] = art.size
                if not data.get("mime") and ext in _EXT_MIME:
                    data["mime"] = _EXT_MIME[ext]

    def _merge_subs(old_subs, new_subs) -> list:
        """合并 subs 时保住已锚定的任务 id（按文字匹配继承）。

        前端任务级连线的 sourceHandle 锚在 id 上：agent 重写清单若丢 id，既有连线全变悬空。
        新行没有 id 没关系，前端加载时会补。行形状统一写成 [文字, id]（纯文字行也归一成此形）。
        """
        id_by_text: dict = {}
        if isinstance(old_subs, list):
            for s in old_subs:
                if not (isinstance(s, (list, tuple)) and s):
                    continue
                sid = _sub_sid(s)
                if sid:
                    txt = str(s[0] or "").strip()
                    if txt:
                        id_by_text.setdefault(txt, sid)
        merged: list = []
        for s in new_subs if isinstance(new_subs, list) else []:
            if isinstance(s, str):
                txt = s.strip()
            elif isinstance(s, (list, tuple)) and s:
                txt = str(s[0] or "").strip()
            else:
                continue
            if not txt:
                continue
            sid = (_sub_sid(s) if not isinstance(s, str) else "") or id_by_text.get(txt, "")
            merged.append([txt, sid])
        return merged

    def _node_label(n: dict) -> str:
        """卡的短标签（告警可读性用）：按类型取最表意的字段，截断到 18 字。"""
        t = str(n.get("type") or "textNode")
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        if t in ("startNode", "endNode"):
            raw = data.get("label", "")
        elif t == "fileNode":
            raw = data.get("name") or "附件"
        elif t == "reviewNode":
            raw = data.get("question", "")
        elif t == "conclusionNode":
            raw = data.get("claim", "")
        elif t == "segNode":
            raw = data.get("seg", "")
        else:
            raw = data.get("title") or data.get("text") or ""
        raw = str(raw or "").strip().replace("\n", " ")
        return raw[:18] + ("…" if len(raw) > 18 else "")

    def _placeholder_reason(n: dict) -> str:
        """空壳检测：卡的核心字段还是占位状态（未回填）时返回原因，否则空串。

        与 overlong 标记对称——一个是「写太多」的信号，一个是「没写完」的信号。
        播种骨架允许占位，但占位必须在产出后回填；没有任何机制追缴时，
        占位卡就永久留在板上当空框（LLM 不会主动意识到自己留了壳）。
        """
        t = str(n.get("type") or "textNode")
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        if t == "taskNode":
            subs = data.get("subs")
            if isinstance(subs, list):
                for s in subs:
                    if isinstance(s, str) and s.strip():
                        return ""
                    if isinstance(s, (list, tuple)) and s and str(s[0] or "").strip():
                        return ""
            return "任务清单（subs）为空"
        if t == "dataNode":
            return "" if str(data.get("metric") or "").strip() else "metric 没有数值"
        if t == "conclusionNode":
            claim = str(data.get("claim") or "").strip()
            if not claim:
                return "claim 为空"
            if "待汇收" in claim or "待回填" in claim:
                return "claim 仍是占位（待汇收/待回填）"
            return ""
        if t == "fileNode":
            return "" if str(data.get("url") or "").strip() else "附件没有下载链接（url）"
        if t == "reviewNode":
            return "" if str(data.get("question") or "").strip() else "question 为空"
        if t == "segNode":
            shots = data.get("shots")
            if isinstance(shots, list):
                for s in shots:
                    if isinstance(s, str) and s.strip():
                        return ""
                    if isinstance(s, (list, tuple)) and s and str(s[0] or "").strip():
                        return ""
            return "分镜清单（shots）为空"
        if t in ("startNode", "endNode"):
            return ""
        return "" if str(data.get("text") or "").strip() else "文本内容为空"

    # ── 工具 ─────────────────────────────────────────────────────────────────

    @tool
    async def create_workflow_board(
        board_type: Annotated[
            str,
            "板型：'board'=流程编排（卡片+连线的协作画布，规划骨架、推进任务、挂产出）；"
            "'html'=应用制作（你像开发者一样在 apps/<key>/ 目录写页面文件，写完用 publish_html_board 发布）",
        ],
        title: Annotated[str, "板子标题（≤128 字，按用户主题起名，如「XX 竞品分析」「XX 数据看板」）"],
    ) -> str:
        """建一块新空板，返回新板 workflow_key。建成即自动挂进用户当前对话面板（用户无需任何操作）。
        **返回值 rules 字段是该板型的完整工作规则，建完板必须逐条遵照执行**——那就是你接下来干活的全部纪律。
        你应紧接着在新板上开工，别建完就停、别停下来等用户确认。
        一次对话通常只建一块板；消息上下文提示已有可复用板时优先复用，别重复建。"""
        bt = (board_type or "").strip().lower()
        if bt not in ("board", "html"):
            return json.dumps({"ok": False, "message": "board_type 只能是 'board'（流程编排板）或 'html'（应用制作）"}, ensure_ascii=False)

        from app.models.standard.agent import AgentSession, AgentWorkflow

        _ctx = get_agent_call_context()
        wf = await AgentWorkflow.create(
            workflow_key="wf_" + secrets.token_hex(12),
            session_key=_ctx.session_key if _ctx else None,
            user_id=user_id,
            title=(title or "新看板")[:128],
            board_type=bt,
            nodes=[],
            edges=[],
            version=1,
            editor="agent",
        )
        # 会话归属回填：agent 建板即本会话归属该板——后续消息（哪怕面板收起 / 从历史恢复）
        # 能走会话级板注入与挂板恢复；与 _ensure_session「归属跟随最近一次带板消息」语义一致，
        # 之后用户若从别的板发消息，_ensure_session 照常改绑。
        if _ctx and _ctx.session_key:
            await AgentSession.filter(session_key=_ctx.session_key, user_id=user_id).update(workflow_key=wf.workflow_key)
        resp: dict = {
            "ok": True,
            "workflow_key": wf.workflow_key,
            "board_type": bt,
            "title": wf.title,
        }
        if bt == "html":
            # 预建应用目录（与 HTTP 建板端点同款）：文件工具与托管路由的共同落点
            await asyncio.to_thread((_WORKSPACE / "users" / str(user_id) / "apps" / wf.workflow_key).mkdir, parents=True, exist_ok=True)
            resp["app_dir"] = f"apps/{wf.workflow_key}/"
            # 建板回合的规则书注入：与 qa.py 消息级注入同源（用户从板页发消息时同样注入这份），
            # 保证 agent 自建板的当回合就拿到完整开发纪律，不裸奔。
            resp["rules"] = HTML_BOARD_RULES + (
                "[你刚建好了这块「应用制作」板，已自动挂上用户面板]\n"
                f"workflow_key: {wf.workflow_key}\n"
                f"标题: {wf.title}\n"
                f"应用目录: apps/{wf.workflow_key}/（相对工作目录，当前为空，先写入口 index.html）\n"
                f"写完/改完文件后务必调用 publish_html_board(workflow_key={wf.workflow_key}) 发布，用户画布才会更新。\n"
                "现在先加载 html-app-studio skill 读完方法论，再按上面的开发规则直接开工。\n"
            )
        else:
            resp["rules"] = WORKFLOW_BOARD_RULES + (
                "[你刚建好了这块「流程编排」板，已自动挂上用户面板]\n"
                f"workflow_key: {wf.workflow_key}\n"
                f"标题: {wf.title}\n"
                "当前节点：（暂无节点）\n"
                f"直接用 workflow_key={wf.workflow_key} 调工作流工具读写；"
                "先读 .agent_skills/workflow-board/SKILL.md 方法论，再播种规划骨架——现在开工。\n"
            )
        return json.dumps(resp, ensure_ascii=False)

    @tool
    async def read_workflow(
        workflow_key: Annotated[str, "工作流 key（wf_ 开头）"],
        summary: Annotated[
            bool,
            "True=概览：每张卡只回 id/类型+截断摘要+全量连线，省上下文，适合每轮扫一眼看用户改了什么；False=全文。默认 True。要细看/改某几张卡请改用 node_ids。",
        ] = True,
        node_ids: Annotated[
            Optional[str],
            "要钻取全文的节点 ID，逗号分隔（如 '1,3,5'）。一旦传入，这几张卡回**全文**（忽略 summary），其余不返回。留空则按 summary 回全部。",
        ] = None,
    ) -> str:
        """看板。先读懂项目结构怎么组织的（分了哪些阶段/分支，新内容该挂哪），再动手。
        默认概览（结构骨架+截断摘要+全量连线）；传 node_ids 则钻取这几张卡的全文。
        返回的节点/连线只有内容与结构（连线仅 id/source/target、任务行出线的 sourceHandle、参照线的 kind:"ref"），不含任何坐标，布局由前端负责，你无需关心。"""
        wf = await _get_wf(workflow_key)
        if not wf:
            return json.dumps({"ok": False, "message": "工作流不存在或不属于你"}, ensure_ascii=False)

        all_nodes: list = wf.nodes or []
        all_edges: list = wf.edges or []

        if node_ids:
            wanted = {x.strip() for x in node_ids.split(",") if x.strip()}
            nodes = [_full_node(n) for n in all_nodes if str(n.get("id")) in wanted]
            edges = [_slim_edge(e) for e in _filter_edges(all_edges, wanted)]
            mode = "full"
        elif summary:
            nodes = [_summary_node(n) for n in all_nodes]
            edges = [_slim_edge(e) for e in all_edges]
            mode = "summary"
        else:
            nodes = [_full_node(n) for n in all_nodes]
            edges = [_slim_edge(e) for e in all_edges]
            mode = "full"

        # 超long检测（仅概览）：概览把文本截到 120 字，agent 看不见自己把卡写得多膨胀，
        # 把信号直接打在它读板的决策时点——超线 = 该拆卡 / 沉附件了
        # 空壳检测（仅概览，与超long对称）：占位卡不回填就永久留成空框，
        # agent 读板时必须直接看到「这几张卡还没填」，占位才有追缴闭环
        overlong_ids: list = []
        placeholder_ids: list = []
        if mode == "summary":
            for n, item in zip(all_nodes, nodes):
                data = n.get("data") or {}
                flags = []
                ln = _node_text_len(data)
                if ln > _OVERLONG_TEXT_LIMIT:
                    flags.append(f"文本 {ln} 字")
                subs = data.get("subs")
                if isinstance(subs, list) and len(subs) > _MAX_TASK_SUBS:
                    flags.append(f"任务清单 {len(subs)} 条")
                if flags:
                    item["overlong"] = "、".join(flags)
                    overlong_ids.append(str(n.get("id")))
                ph = _placeholder_reason(n)
                if ph:
                    item["placeholder"] = ph
                    placeholder_ids.append(str(n.get("id")))

        resp: dict = {
            "ok": True,
            "workflow_key": wf.workflow_key,
            "title": wf.title,
            "version": wf.version,
            "mode": mode,
            "nodes": nodes,
            "edges": edges,
            "total_nodes": len(all_nodes),
            "total_edges": len(all_edges),
        }
        # html 应用板的分享状态（第 12 条多用户规则靠它判断：免登录模式访客全部匿名）
        if getattr(wf, "board_type", None) == "html":
            resp["shareOn"] = bool(wf.share_on)
            resp["sharePublic"] = bool(wf.share_public)
        # 人机协作信号：仅当上一次写入者是人时，才把「人的改动简报」带给 agent
        # （agent 自己改板会清空它，避免重复提示）
        if wf.editor == "human" and wf.human_edit:
            resp["human_edit"] = wf.human_edit
            resp["human_edit_hint"] = "用户在你上次离开后手动改过看板（详尽简报见 human_edit，含字段级旧→新）。通常据此直接回应即可，需要某卡全文再用 node_ids 钻取。"
        if mode == "summary":
            resp["hint"] = "概览：text 已截断。需某卡全文或要改某卡，再调 read_workflow(node_ids=...) 钻取。"
            if overlong_ids:
                resp["hint"] += (
                    f"⚠ 卡片 {'、'.join(overlong_ids)} 超long（明细见各卡 overlong 字段）：卡是索引不是正文——"
                    "拆成子卡，或 write_file 沉成附件挂 fileNode（卡上留一句断言），别继续往里塞。"
                )
            if placeholder_ids:
                resp["hint"] += (
                    f"⚠ 卡片 {'、'.join(placeholder_ids)} 是空壳（明细见各卡 placeholder 字段）："
                    "占位卡产出后必须回填（metric / claim / url / subs 等），别长期晾着；已不需要就撤卡。"
                )
        return json.dumps(resp, ensure_ascii=False)

    @tool
    async def edit_workflow_board(
        workflow_key: Annotated[str, "工作流 key（wf_ 开头）"],
        nodes_json: Annotated[
            Optional[str],
            "upsert 节点 JSON 数组：带**已有 id**=改那张卡（data 深合并，只动你传的字段），带**新 id**=加新卡。无需 position。"
            "卡片是纯文本，别写 markdown（表格 |、标题 #、加粗 ** 都不渲染，会露出原始符号）；"
            "复杂 / 要美观的结果（大表 / 对比 / 报告 / 仪表板）做成 HTML 附件上板，别塞进卡里。"
            '推进工作项优先更新 taskNode 的 subs 任务清单（如 {"id":"2","data":{"subs":[["机械领域","s1a2b3"],["电气领域"]]}}；'
            "重写清单照 read_workflow 返回的行写、**保留原任务 id**，id 是任务级连线的锚点，丢了连线会断）或回填 summary，别另开新卡。"
            "**加新卡/附件前先想好它在项目结构里归属哪个阶段/任务**，再用 edges_json 连到对应节点，严禁按时间顺序挂到最新节点下做流水账。"
            '示例：[{"id":"2","data":{"summary":"已收集完，共 187 项"}},{"id":"5","type":"dataNode","data":{"title":"总量","metric":"187","unit":"项"}}]',
        ] = None,
        remove_node_ids: Annotated[Optional[str], "要撤掉的节点 ID，逗号分隔（关联连线自动清掉）。如 '4,7'"] = None,
        edges_json: Annotated[
            Optional[str],
            "要新增的连线 JSON 数组（端点必须存在；已存在的连线 id 会被跳过）。"
            "连线 id 写 e{source}-{target} / e{source}-{sid}-{target}（与前端约定一致，防重复线）。"
            '工作项卡有多个任务时出线应从各任务行分别出："sourceHandle":"任务id"（取自 read_workflow 的 subs 各行第二元素，或你播种时自发的短 id；锚点不存在会退回整卡出线）。'
            '默认不加线型字段 = 流程线（代表项目结构）；跨分支的引用/对照/依据关系加 "kind":"ref" 画成参照线（浅灰点状直线，不参与结构布局），'
            "改线型 = remove_edge_ids 断掉再带 kind 重连。"
            '示例：[{"id":"e3-5","source":"3","target":"5"},{"id":"e2-m1-5","source":"2","target":"5","sourceHandle":"m1"},{"id":"e3-8","source":"3","target":"8","kind":"ref"}]',
        ] = None,
        remove_edge_ids: Annotated[Optional[str], "要断开的连线 ID，逗号分隔。如 'e2,e4'"] = None,
        title: Annotated[Optional[str], "若需同时改标题，传入新标题"] = None,
    ) -> str:
        """对看板做一组变更 = 对整板做一次再规划。先想填实/撤/断/并，再想加新：加/改卡（upsert）、撤卡、加线、断线，一次调用一起落地。

        **卡是索引不是正文**：单卡文本以扫一眼能吸收为限（正文 ~150 字、subs ≤ 7 条、claim 一句话），超了就拆子卡或沉附件（write_file → fileNode），别硬塞。
        **论证天然是多卡链条**：结论卡只装一句话判决，每条理由/依据各立一卡（数据卡/文本卡/附件）连进结论卡，别把论证塞进 claim/points。
        **调用前必读 `.agent_skills/workflow-board/SKILL.md`**（板型方法论，任何编辑动作都适用；本轮已读过则不必重读；仅改 title 的改名调用除外）。
        **画板页协作的第一动作往往就是它**：会话里用户第一条消息无论是什么都必须规划落板；此后给活干、提实质性问题（哪怕很简单）也是先落骨架（工作项卡 + 产出/结论位）再执行——别直接在对话里干完交差、也别只在对话里把问题答了。
        每次编辑都是对**整板结构的一次再规划**，不只是往上补：**新内容优先回填既有卡**（summary / subs / 挂附件）而不是加新卡；
        已完成/过时的卡要撤（remove_node_ids——撤卡≠丢信息，内容已在附件/结论卡里）、过时/改道的连线要断（remove_edge_ids）再重连、重叠的卡要合并——加、改、撤、断线在一组变更里一起做完，别让板子只增不减越长越乱。
        新内容按项目结构归属放置（挂到所属阶段/任务的卡下），不做时间流水账；别为做过的每一步追加新卡。
        作用域模式（消息注入了 [选中节点协作] 时）：改动限焦点卡及其前后邻居，越界操作自动跳过并在返回的 skipped_scope 中逐条列出，照单说明即可。
        返回若带 `dropped_edges`（端点节点不存在的连线，未连上）必须核对 id 后补线；带 `board_warnings`（孤儿卡 / 空壳卡）应当场处理：补线、回填或撤卡。"""
        wf = await _get_wf(workflow_key)
        if not wf:
            return json.dumps({"ok": False, "message": "工作流不存在或不属于你"}, ensure_ascii=False)

        nodes: list = list(wf.nodes or [])
        edges: list = list(wf.edges or [])

        # 作用域模式（选中节点协作）：qa.py 经 AgentCallContext.workflow_scope 透传可编辑范围。
        # allowed=None 是全板模式，下方各处分支不生效，行为与原来一字不差；
        # 有作用域时，越界操作逐条跳过并记入 skipped，最后随返回报告（软强制，不报错）。
        _ctx = get_agent_call_context()
        _scope = _ctx.workflow_scope if _ctx else None
        allowed: Optional[set] = None
        if _scope and _scope.get("workflow_key") == workflow_key and _scope.get("scope_ids"):
            allowed = {str(x) for x in _scope["scope_ids"]}
        skipped: list = []

        # 0) 先校验两段 JSON 合法性（LLM 偶尔会吐出带未转义引号 / 截断的 JSON）。
        #    解析失败就原样返回错误让 agent 重试，绝不抛异常炸掉整轮工具任务。
        nodes_payload: list = []
        if nodes_json:
            try:
                nodes_payload = json.loads(nodes_json)
            except json.JSONDecodeError as exc:
                return json.dumps(
                    {"ok": False, "message": f"nodes_json 不是合法 JSON（{exc}）。请检查卡片文字里的引号/换行是否转义，重新传完整数组。"},
                    ensure_ascii=False,
                )
            if not isinstance(nodes_payload, list):
                return json.dumps({"ok": False, "message": "nodes_json 必须是 JSON 数组（[...]），请重新传。"}, ensure_ascii=False)
        edges_payload: list = []
        if edges_json:
            try:
                edges_payload = json.loads(edges_json)
            except json.JSONDecodeError as exc:
                return json.dumps(
                    {"ok": False, "message": f"edges_json 不是合法 JSON（{exc}）。请重新传完整数组。"},
                    ensure_ascii=False,
                )
            if not isinstance(edges_payload, list):
                return json.dumps({"ok": False, "message": "edges_json 必须是 JSON 数组（[...]），请重新传。"}, ensure_ascii=False)

        # 1) 撤卡：删节点 + 清关联连线
        removed_ids: list = []
        if remove_node_ids:
            to_remove = {x.strip() for x in remove_node_ids.split(",") if x.strip()}
            if allowed is not None and to_remove:
                out_of_scope = sorted(to_remove - allowed)
                if out_of_scope:
                    skipped.extend(f"撤卡 {x}：超出选中范围，未撤除" for x in out_of_scope)
                    to_remove = to_remove & allowed
            if to_remove:
                nodes = [n for n in nodes if str(n.get("id")) not in to_remove]
                edges = [e for e in edges if str(e.get("source")) not in to_remove and str(e.get("target")) not in to_remove]
                removed_ids = sorted(to_remove)

        # 2) upsert 节点：已有 id 深合并，新 id 追加
        upserted: list = []
        added: list = []
        if nodes_json:
            idx = {str(n.get("id")): i for i, n in enumerate(nodes)}
            for item in nodes_payload:
                nid = str(item.get("id", ""))
                if not nid:
                    continue
                if nid in idx:
                    if allowed is not None and nid not in allowed:
                        skipped.append(f"节点 {nid}：超出选中范围，未修改")
                        continue
                    old = nodes[idx[nid]]
                    for k, v in item.items():
                        if k == "data" and isinstance(v, dict) and isinstance(old.get("data"), dict):
                            if "subs" in v:
                                v = {**v, "subs": _merge_subs(old["data"].get("subs"), v.get("subs"))}
                            old["data"].update(v)
                        else:
                            old[k] = v
                    upserted.append(nid)
                else:
                    nodes.append(item)
                    idx[nid] = len(nodes) - 1
                    added.append(nid)
                    if allowed is not None:
                        allowed.add(nid)  # 本轮新增卡纳入作用域，后续连线可引用

        # 3) 断线
        removed_edges: list = []
        if remove_edge_ids:
            drop = {x.strip() for x in remove_edge_ids.split(",") if x.strip()}
            if allowed is not None and drop:
                ok_drop = {
                    str(e.get("id"))
                    for e in edges
                    if str(e.get("id")) in drop
                    and (str(e.get("source")) in allowed or str(e.get("target")) in allowed)
                }
                out_of_scope = sorted(drop - ok_drop)
                if out_of_scope:
                    skipped.extend(f"断线 {x}：与选中范围无关，未断开" for x in out_of_scope)
                drop = ok_drop
            if drop:
                removed_edges = [str(e.get("id")) for e in edges if str(e.get("id")) in drop]
                edges = [e for e in edges if str(e.get("id")) not in drop]

        # 4) 加线：跳过已存在 id；端点不存在的悬空线**逐条记入 dropped_edges 随返回上报**，
        #    让 agent 知道哪条线没连上、去核对 id 补线（此前静默丢弃：卡建了线没了，
        #    返回却报成功——板上「没连线的独立框框」就是这么来的）。
        #    sourceHandle（任务级出线）锚点失效时退回整卡出线。
        #    只落结构字段（id/source/target/sourceHandle/kind）——样式由前端按 kind 派生，渲染字段不进库
        added_edges = 0
        added_edge_ids: list = []
        dropped_edges: list = []
        if edges_json:
            final_ids = {str(n.get("id")) for n in nodes}
            node_by_id = {str(n.get("id")): n for n in nodes}
            existing_edge_ids = {str(e.get("id")) for e in edges}
            for e in edges_payload:
                eid = str(e.get("id", ""))
                if eid and eid in existing_edge_ids:
                    continue
                src, tgt = str(e.get("source")), str(e.get("target"))
                if src not in final_ids or tgt not in final_ids:
                    miss = sorted({x for x in (src, tgt) if x not in final_ids})
                    dropped_edges.append(
                        f"连线 {eid or (src + '→' + tgt)}：端点节点 {'、'.join(miss)} 不存在，未连上"
                        "（read_workflow 核对真实卡 id 后用 edges_json 补这条线，别让卡没线）"
                    )
                    continue
                if allowed is not None and (src not in allowed or tgt not in allowed):
                    skipped.append(f"连线 {eid or (src + '-' + tgt)}：端点超出选中范围，未连接")
                    continue
                clean: dict = {"id": eid, "source": src, "target": tgt}
                sh = e.get("sourceHandle")
                if sh:
                    sdata = (node_by_id.get(src) or {}).get("data") or {}
                    ssubs = sdata.get("subs")
                    anchored = isinstance(ssubs, list) and any(_sub_sid(s) == str(sh) for s in ssubs)
                    if anchored:
                        clean["sourceHandle"] = str(sh)
                if e.get("kind") == "ref":
                    clean["kind"] = "ref"  # 参照线标记；其余 kind 值一律不认
                edges.append(clean)
                if eid:
                    existing_edge_ids.add(eid)
                    added_edge_ids.append(eid)
                added_edges += 1

        # 5) 整板体检（软告警随返回上报，不阻断）：孤儿卡（无任何连线）与空壳卡（核心字段未回填）
        #    是「拆板拆不干净」的两大症状，给 agent 显式纠错信号，而不是留给用户肉眼发现。
        #    作用域模式下只报选中范围内的卡（范围外的它本来也无权处理，报了只会诱导越界操作）
        board_warnings: list = []
        connected: set = set()
        for e in edges:
            connected.add(str(e.get("source")))
            connected.add(str(e.get("target")))
        for n in nodes:
            nid = str(n.get("id"))
            t = str(n.get("type") or "textNode")
            if t in ("startNode", "endNode"):
                continue
            if allowed is not None and nid not in allowed:
                continue
            if nid not in connected:
                board_warnings.append(
                    f"孤儿卡 {nid}（{_node_label(n)}）：没有任何连线——用 edges_json 把流程线补到归属卡下，"
                    "不该存在的卡用 remove_node_ids 撤掉"
                )
            ph = _placeholder_reason(n)
            if ph:
                board_warnings.append(
                    f"空壳卡 {nid}（{_node_label(n)}）：{ph}——播种占位的，产出后及时回填，别长期留壳；不再需要就撤卡"
                )

        if title:
            wf.title = title[:128]
        await _backfill_file_nodes(nodes)  # 缺 name / mime 的附件卡按产物记录补齐（防前端落「不支持预览」兜底）
        wf.nodes = nodes
        wf.edges = edges
        wf.version += 1
        wf.editor = "agent"
        wf.human_edit = None  # agent 已接管，消费掉人的改动简报
        # 节点徽标（临时协作态）：agent 每次编辑全量重建——上一轮的徽标（含人编辑标）此刻清零，
        # 只记本轮增量：新卡=new、改过的卡=agent、新连线=new。这是「agent 这轮动了什么」的视觉信号，
        # 不进 read_workflow、不入 agent 上下文；人端的 human 标由前端随保存写回。
        node_marks = {nid: {"t": "new"} for nid in added}
        node_marks.update({nid: {"t": "agent"} for nid in upserted if nid not in node_marks})
        edge_marks = {eid: {"t": "new"} for eid in added_edge_ids}
        wf.marks = {"nodes": node_marks, "edges": edge_marks} if (node_marks or edge_marks) else None
        await wf.save()
        # 存档本次写入（回滚的真相源）；存档失败不阻塞编辑
        try:
            from app.api.v1.ai.agent_workflow import archive_wf_version  # 延迟导入：ai 包 __init__ 经 qa.py 反过来依赖本模块

            await archive_wf_version(wf)
        except Exception:
            pass

        resp: dict = {
            "ok": True,
            "version": wf.version,
            "upserted_node_ids": upserted,
            "added_node_ids": added,
            "removed_node_ids": removed_ids,
            "added_edges": added_edges,
            "removed_edge_ids": removed_edges,
        }
        if skipped:
            resp["skipped_scope"] = skipped
        if dropped_edges:
            resp["dropped_edges"] = dropped_edges
        if board_warnings:
            resp["board_warnings"] = board_warnings
        return json.dumps(resp, ensure_ascii=False)

    @tool
    async def publish_html_board(
        workflow_key: Annotated[str, "应用制作任务的工作流 key（wf_ 开头）"],
    ) -> str:
        """发布应用制作：在任务目录写完/改完页面（展示页或应用）文件后调用，用户画布才会刷新到最新版。

        相当于「部署」动作——不发布，用户看到的就还是旧版或占位页。
        调用前提：index.html 已写入目录，且本批文件已全部写完（发布会立即触发用户端重载，半途发布看到的是半成品）。
        注意：本工具只更新用户画面，**不产生版本存档**——版本由用户在看板顶栏点「发布」按钮自行固化，
        你不需要也无法替用户建版本。仅用于应用制作任务；节点连线板的改动请用 edit_workflow_board。"""
        wf = await _get_wf(workflow_key)
        if not wf:
            return json.dumps({"ok": False, "message": "工作流不存在或不属于你"}, ensure_ascii=False)
        if (wf.board_type or "board") != "html":
            return json.dumps(
                {"ok": False, "message": "这是节点连线板，不是应用制作：板子改动请用 edit_workflow_board"},
                ensure_ascii=False,
            )

        app_dir = _WORKSPACE / "users" / str(user_id) / "apps" / workflow_key

        def _entry_exists() -> bool:
            return (app_dir / "index.html").is_file()

        if not await asyncio.to_thread(_entry_exists):
            return json.dumps(
                {"ok": False, "message": f"入口文件 apps/{workflow_key}/index.html 不存在，请先用 write_file 写好入口再发布"},
                ensure_ascii=False,
            )

        def _preflight_problems() -> list:
            """发布前体检：拦截最常见的「用户数据静默丢失」与「切换闪屏」坏法。
            只查必然错误的形态（误报率≈0），不强制应用必须有持久化（纯展示页合法）：
            ① APP_TOKEN 写死成 workflow_key（托管路由验签必拒，保存全部静默失败）
            ② 调用不存在的 /load 端点（托管路由只有静态文件 GET、whoami GET、rows GET 与 save / proxy / ai / upload / extract / rows/put / rows/del 七个 POST）
            ③ 跳到其他 html 页面的链接（应用跑在 iframe 里，换页 = 整文档重载白屏闪烁，
               模块切换应做同文档内的视图切换）"""
            problems: list = []
            for p in sorted(app_dir.rglob("*.html")):
                if not p.is_file():
                    continue
                rel_parts = p.relative_to(app_dir).parts
                if any(seg.startswith(".") for seg in rel_parts):
                    continue  # .versions/ 发布存档里的旧版 html 不参与体检（否则旧版问题会误拦新版发布）
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                rel = p.relative_to(app_dir).as_posix()
                if re.search(r"APP_TOKEN\s*=\s*[\"'`]wf_", text):
                    problems.append(f"{rel} 把 APP_TOKEN 写死成了 workflow_key——必须改为从页面 URL 提取：parts[parts.indexOf('html-app') + 1]")
                if re.search(r"html-app[^\"'`]{0,80}/load", text):
                    problems.append(f"{rel} 调用了不存在的 /load 端点——新应用读数据请用行通道 GET rows（见开发规则第 3 条），存量 json 数据层用相对路径 fetch('data/store.json')")
                m = re.search(r"""href\s*=\s*["'](?!https?:|mailto:|#|/api/)[^"']+\.html["']""", text, re.IGNORECASE)
                if m:
                    problems.append(f"{rel} 存在页间跳转 {m.group(0)}——iframe 内换页是整文档重载，白屏闪烁；模块切换改用同一文档内的视图切换")
                if re.search(r"""location(?:\.href)?\s*=\s*["'][^"'#][^"']*\.html""", text):
                    problems.append(f"{rel} 存在 JS 页间跳转（location 赋值 .html）——同上，改用同文档视图切换")
            return problems

        problems = await asyncio.to_thread(_preflight_problems)
        if problems:
            return json.dumps(
                {
                    "ok": False,
                    "message": "检出以下问题，拒绝发布：" + "；".join(problems) + "。请修正后再调用 publish_html_board。",
                },
                ensure_ascii=False,
            )

        def _list_files() -> list:
            out: list = []
            upload_count = 0
            archive_files = 0
            for p in sorted(app_dir.rglob("*")):
                if not p.is_file() or p.name.endswith(".tmp"):
                    continue
                rel = p.relative_to(app_dir).as_posix()
                if rel.startswith("uploads/"):  # 页面上传的文件只计数，避免把应用文件挤出清单
                    upload_count += 1
                    continue
                if rel.startswith(".versions/"):  # 发布存档只计数，不进文件清单
                    archive_files += 1
                    continue
                out.append({"path": rel, "bytes": p.stat().st_size})
                if len(out) >= 50:
                    break
            if upload_count:
                out.append({"path": "uploads/", "note": f"{upload_count} 个用户上传文件"})
            if archive_files:
                out.append({"path": ".versions/", "note": f"{archive_files} 个发布存档文件（回滚用，勿读写）"})
            return out

        files = await asyncio.to_thread(_list_files)
        wf.version += 1
        wf.editor = "agent"
        wf.human_edit = None
        # 发布即入口就绪，落库成为跨机真相源：DB 共享而工作目录不共享的部署里，
        # 另一台实例靠这个标志认出「此板已发布」，不会把它误判为空板清掉
        wf.entry_ready = 1
        # agent 改动后当前是未固化工作态：app_version 指针清空（版本列表里不再有「当前」标记）。
        # 版本存档只在用户点「发布」按钮时产生——agent 更新画面不复制新版本
        wf.app_version = None
        # 活动集入库：本次目录全量写进 DB 活动集（agent_app_file version=0，uploads/ 除外）——
        # 跨机真相源（其他实例按需把文件物化回本机磁盘）。入库失败不阻塞发布（最坏退回单机可见）
        synced_info = None
        try:
            from app.api.v1.ai.agent_workflow import db_sync_live_from_disk  # 延迟导入：ai 包 __init__ 经 qa.py 反过来依赖本模块

            synced_info = await db_sync_live_from_disk(wf.workflow_key, app_dir)
        except Exception:
            pass
        # 顺手提取列表卡预览素材（应用标题 / 首行标语 / 主题色）落库：任务库里的应用制作卡
        # 据此渲染各自的真实预览，不再是清一色占位图。提取失败不阻塞发布（卡片退回占位样式）
        try:
            from app.api.v1.ai.agent_workflow import extract_html_preview  # 延迟导入：ai 包 __init__ 经 qa.py 反过来依赖本模块

            wf.html_preview = await asyncio.to_thread(extract_html_preview, app_dir)
        except Exception:
            pass
        await wf.save()
        resp: dict = {"ok": True, "version": wf.version, "files": files, "hint": "已发布，用户画布将自动更新"}
        if synced_info and synced_info.get("skipped"):
            resp["hint"] += f"；注意：{'、'.join(synced_info['skipped'])} 超过 16MB 未入库，只在发布它的机器上可见，其他机器打开会缺这些文件"
        return json.dumps(resp, ensure_ascii=False)

    @tool
    async def rollback_workflow_version(
        workflow_key: Annotated[str, "工作流 key（wf_ 开头）"],
        version: Annotated[Optional[int], "要回滚到的存档版本号；不传默认回「上一版」（= 次新存档）"] = None,
    ) -> str:
        """把工作流回滚到历史存档版本——时点全量还原，系统自动存档，绝不需要你凭记忆重建。

        两种板型都支持，存档链路各自闭环：
        - 应用制作（html 板）：版本存档由用户点「发布」固化（当时应用目录的全量快照）；回滚把除 uploads/
          （用户上传文件原样保留）外的全部文件还原到那个版本的时刻。若当前有未固化改动会先自动备份一版。
        - 节点连线板（board 板）：每次板子写入自动存档全量快照（节点 / 连线 / 标题）；回滚整板还原。

        version 不传 = 回「上一版」：节点板取次新存档（最新一版即当前状态）；应用制作按当前查看状态判断——
        正查看最新版取次新版，处于未固化工作态（你刚改完）则取最新存档（即你改之前的样子）。
        **用户说「回滚到上一版 / 恢复旧版 / 还原你改之前的样子」时，直接调用本工具就是唯一正解——
        严禁从对话记忆里手动重建旧文件或旧卡片**（记忆必然不全，重建必失真，此前已多次酿成事故）。
        返回里带全部存档版本清单（versions），需要更早的版本照清单带 version 再调一次。"""
        from app.api.v1.ai.agent_workflow import list_wf_versions, rollback_wf_to_version

        wf = await _get_wf(workflow_key)
        if not wf:
            return json.dumps({"ok": False, "message": "工作流不存在或不属于你"}, ensure_ascii=False)

        # 作用域模式（选中节点协作）：回滚是整板操作，与「改动限选中范围」冲突，拒绝执行
        _ctx = get_agent_call_context()
        _scope = _ctx.workflow_scope if _ctx else None
        if _scope and _scope.get("workflow_key") == workflow_key and _scope.get("scope_ids"):
            return json.dumps(
                {"ok": False, "message": "当前是选中节点协作模式，回滚是整板操作无法执行；请用户从整板对话发起"},
                ensure_ascii=False,
            )

        versions = await list_wf_versions(wf, user_id)
        if version is None:
            if (wf.board_type or "board") == "html":
                # html 板「上一版」语义：正查看最新版 → 次新版；工作态（agent 刚改完未固化）
                # 或正在看更早版本 → 最新存档（= 用户说「还原你改之前的样子」时的正确目标）
                if not versions:
                    return json.dumps(
                        {"ok": False, "message": "还没有任何版本存档（版本由用户点「发布」固化），没有可回的「上一版」", "versions": versions},
                        ensure_ascii=False,
                    )
                newest = versions[0]["version"]
                if wf.app_version is not None and int(wf.app_version) == int(newest):
                    if len(versions) < 2:
                        return json.dumps(
                            {"ok": False, "message": "只有一版存档且正在查看它，没有可回的「上一版」", "versions": versions},
                            ensure_ascii=False,
                        )
                    version = versions[1]["version"]
                else:
                    version = newest
            else:
                if len(versions) < 2:
                    return json.dumps(
                        {"ok": False, "message": "存档不足两版（最新一版即当前状态），没有可回的「上一版」", "versions": versions},
                        ensure_ascii=False,
                    )
                version = versions[1]["version"]
        new_version = await rollback_wf_to_version(wf, int(version), editor="agent", user_id=user_id)
        if new_version is None:
            return json.dumps(
                {"ok": False, "message": f"存档版本 v{version} 不存在或已损坏", "versions": versions},
                ensure_ascii=False,
            )
        is_html = (wf.board_type or "board") == "html"
        return json.dumps(
            {
                "ok": True,
                "rolledBackTo": int(version),
                "newVersion": new_version,
                "hint": "时点全量还原完成，用户画布将自动更新" if is_html else "整板已还原到该存档状态",
                "versions": versions,
            },
            ensure_ascii=False,
        )

    @tool
    async def query_app_rows(
        workflow_key: Annotated[str, "应用制作任务的工作流 key（wf_ 开头）"],
        tbl: Annotated[str, "应用内逻辑集合名（如 records / members / settings；$acl 是权限登记清单）"],
        prefix: Annotated[Optional[str], "只回行键以该前缀开头的行（如按人数据 '123:'）；不传 = 全量"] = None,
        limit: Annotated[Optional[int], "最多回多少行，默认 100；需要更多时直接传更大的值"] = None,
    ) -> str:
        """读应用制作应用的行数据层（用户数据，一行 = 一条业务记录）——改数据结构 / 迁移 / 排查数据问题前先看现状。

        返回 {ok, rows: [{key, data, updatedBy, updatedAt}]（按行键排序）, more: 是否还有更多}。
        updatedBy / updatedAt 是平台元数据（updatedAt 为 ISO 8601 字符串），勿与应用 data 内的业务时间字段（纪元毫秒）混用。
        只读；写入 / 删除 / 迁移请用 update_app_rows。仅自己的应用制作（html 板）可用。"""
        wf = await _get_wf(workflow_key)
        if not wf:
            return json.dumps({"ok": False, "message": "工作流不存在或不属于你"}, ensure_ascii=False)
        if (wf.board_type or "board") != "html":
            return json.dumps({"ok": False, "message": "这是节点连线板，不是应用制作"}, ensure_ascii=False)

        from app.api.v1.ai.html_app import _valid_tbl

        if tbl != "$acl" and not _valid_tbl(tbl):
            return json.dumps({"ok": False, "message": "tbl 名不合法（字母/数字/下划线开头，可含 . _ -，最长 64 字符）"}, ensure_ascii=False)
        lim = max(1, int(limit or 100))

        from app.models.standard.agent import AgentAppRow

        qs = AgentAppRow.filter(workflow_key=workflow_key, tbl=tbl)
        if prefix:
            qs = qs.filter(row_key__startswith=prefix)
        rows = await qs.order_by("row_key").limit(lim + 1)
        more = len(rows) > lim
        return json.dumps(
            {
                "ok": True,
                "rows": [
                    {
                        "key": r.row_key,
                        "data": r.data,
                        "updatedBy": r.updated_by,
                        "updatedAt": r.update_time.isoformat() if r.update_time else None,
                    }
                    for r in rows[:lim]
                ],
                "more": more,
            },
            ensure_ascii=False,
        )

    @tool
    async def update_app_rows(
        workflow_key: Annotated[str, "应用制作任务的工作流 key（wf_ 开头）"],
        tbl: Annotated[str, '应用内逻辑集合名（如 records / members）；传 "$acl" = 写行级权限登记清单（行键为受保护表名，data 形如 {"writers": [uid], "readers": [uid]}）'],
        upsert_json: Annotated[
            Optional[str],
            '要新增/覆盖的行，JSON 字符串数组：[{"key": "行键", "data": {…}}, …]（同 (tbl,key) 已有则覆盖）。数组一律传 JSON 字符串',
        ] = None,
        delete_keys_json: Annotated[Optional[str], '要删除的行键，JSON 字符串数组：["key1", "key2"]。数组一律传 JSON 字符串'] = None,
    ) -> str:
        """写应用制作应用的行数据层（upsert 和/或删除，可同时传，先写后删）——用户要求修数据、改数据结构做迁移、登记 $acl 权限保护时用。

        你以板主身份执行（$acl 权限名单对你不生效；页面端访客仍受限）。
        **严禁用本工具删除用户数据**——改数据结构必须迁移：query_app_rows 读出 → 转换 → 本工具写回。
        仅自己的应用制作（html 板）可用；一次各 ≤200 行/键、单行数据 ≤256KB。"""
        wf = await _get_wf(workflow_key)
        if not wf:
            return json.dumps({"ok": False, "message": "工作流不存在或不属于你"}, ensure_ascii=False)
        if (wf.board_type or "board") != "html":
            return json.dumps({"ok": False, "message": "这是节点连线板，不是应用制作"}, ensure_ascii=False)

        upserts: list = []
        if upsert_json:
            try:
                upserts = json.loads(upsert_json)
            except Exception as exc:
                return json.dumps({"ok": False, "message": f"upsert_json 不是合法 JSON（{exc}）。请重新传完整数组。"}, ensure_ascii=False)
            if not isinstance(upserts, list):
                return json.dumps({"ok": False, "message": 'upsert_json 必须是 JSON 数组（[{"key": …, "data": …}]），请重新传。'}, ensure_ascii=False)
        del_keys: list = []
        if delete_keys_json:
            try:
                del_keys = json.loads(delete_keys_json)
            except Exception as exc:
                return json.dumps({"ok": False, "message": f"delete_keys_json 不是合法 JSON（{exc}）。请重新传完整数组。"}, ensure_ascii=False)
            if not isinstance(del_keys, list):
                return json.dumps({"ok": False, "message": 'delete_keys_json 必须是行键的 JSON 数组（["k1", …]），请重新传。'}, ensure_ascii=False)
        if not upserts and not del_keys:
            return json.dumps({"ok": False, "message": "upsert_json 与 delete_keys_json 至少传一个"}, ensure_ascii=False)

        from app.api.v1.ai.html_app import _ROW_DATA_MAX_BYTES, _ROWS_BATCH_MAX, _valid_row_key, _valid_tbl

        if tbl != "$acl" and not _valid_tbl(tbl):
            return json.dumps({"ok": False, "message": "tbl 名不合法（字母/数字/下划线开头，可含 . _ -，最长 64 字符）"}, ensure_ascii=False)
        if len(upserts) > _ROWS_BATCH_MAX or len(del_keys) > _ROWS_BATCH_MAX:
            return json.dumps({"ok": False, "message": f"单次写入 / 删除各不能超过 {_ROWS_BATCH_MAX} 行"}, ensure_ascii=False)
        for item in upserts:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not _valid_row_key(item["key"]) or "data" not in item:
                return json.dumps({"ok": False, "message": 'upsert_json 每项必须是 {"key": 行键(≤191字符), "data": 任意JSON}'}, ensure_ascii=False)
            if len(json.dumps(item["data"], ensure_ascii=False).encode("utf-8")) > _ROW_DATA_MAX_BYTES:
                return json.dumps({"ok": False, "message": f"行数据过大（上限 256KB）：{item['key'][:32]}"}, ensure_ascii=False)
        if any(not isinstance(k, str) or not _valid_row_key(k) for k in del_keys):
            return json.dumps({"ok": False, "message": "delete_keys_json 元素必须是行键（≤191 字符字符串）"}, ensure_ascii=False)
        if tbl == "$acl":
            from app.api.v1.ai.html_app import _validate_acl_data

            for item in upserts:
                if not _valid_tbl(item["key"]):
                    return json.dumps({"ok": False, "message": f"$acl 的行键必须是合法的受保护表名：{item['key'][:32]}"}, ensure_ascii=False)
                err = _validate_acl_data(item["data"])
                if err:
                    return json.dumps({"ok": False, "message": f"$acl 行数据不合法：{err}"}, ensure_ascii=False)

        from tortoise.transactions import in_transaction

        from app.models.standard.agent import AgentAppRow

        async with in_transaction():
            for item in upserts:
                await AgentAppRow.update_or_create(
                    workflow_key=workflow_key,
                    tbl=tbl,
                    row_key=item["key"],
                    defaults={"data": item["data"], "updated_by": user_id},
                )
            deleted = 0
            if del_keys:
                deleted = await AgentAppRow.filter(workflow_key=workflow_key, tbl=tbl, row_key__in=del_keys).delete()
        # 行数据变更 → SSE 广播（by=agent）：订阅该板的页面（板主 + 分享访客）onChange 即时刷新。
        # 延迟 import 防循环依赖；推送失败绝不影响写主链路（app_events.publish 内部吞异常）
        try:
            from app.services.agent_runtime import app_events

            app_events.publish(workflow_key, [tbl], "agent")
        except Exception:  # noqa: BLE001
            pass
        return json.dumps({"ok": True, "written": len(upserts), "deleted": deleted}, ensure_ascii=False)

    return [
        create_workflow_board,
        read_workflow,
        edit_workflow_board,
        publish_html_board,
        rollback_workflow_version,
        query_app_rows,
        update_app_rows,
    ]
