# 互动接口（mcp.json）——让用户与 agent 在对话里直接与应用互动

人机对局 / 协作模拟等「agent 参与运行期互动」的完整规范。数据层契约见 references/platform-conventions.md 第 3 条；多 NPC 并发协作见 references/multi-actor.md。

- **何时做**：应用需要「agent 参与运行期互动」时——人机对局（五子棋 / 象棋 / 卡牌）、协作模拟（多 NPC 社区）、页面操作需 agent 响应的应用（页面点按钮 → agent 接手处理）。**纯展示页与纯 CRUD 应用不做**（用户自己操作数据层即可，agent 无用武之地）。
- **是什么**：写一份声明式 manifest `apps/{workflow_key}/mcp.json`，列出这个应用对 agent 开放的工具。平台据此挂一个**真实 MCP 服务**，工具名形如 `mcp__app_{workflow_key}__{工具名}`（对话里可直接调）。工具执行在**服务端**（读写行数据），**页面没打开也能调**（定时任务、子 agent 干活时都行）。执行身份 = 板主（updated_by 恒为板主 uid），**但 manifest 工具禁碰 $acl 表**（权限治理只归 update_app_rows，校验期直接拒绝）。
- **格式规范**：顶层 `{version: 1, tools: [...]}`（version 恒 1；tools 1~20 个；文件 ≤64KB）。每个 tool：
  - `name`：小写字母数字下划线、首字符小写字母（`^[a-z][a-z0-9_]{1,62}$`），manifest 内唯一；
  - `displayName`（可选）：中文显示名（≤32 字），过程时间线用（如「落子」）；
  - `description`：非空、≤2000 字——**LLM 靠它判断何时调用**，务必写清「做什么、何时用、每个参数含义」；
  - `inputSchema`：JSON Schema 子集——顶层 type 恒 "object"，properties 列各入参（type ∈ string/integer/number/boolean/array/object，可用 required/enum/description/default/minimum/maximum 等）；**禁 oneOf/anyOf/allOf/$ref 等高级关键字**（校验期拒绝）；
  - `effects`：1~10 个**顺序执行**的效果（对行数据的 4 种操作，见下）。
- **效果操作 4 种**（每个效果都带 `tbl`=表名，同 platform-conventions 第 3 条数据层规矩；`$acl` 一律拒绝）：
  - `read`：`{op:"read", tbl, prefix?|keys?, limit?(1~1000，默认200)}`——读出行并入工具返回（不触发页面刷新）。prefix 与 keys 互斥。
  - `put`：`{op:"put", tbl, key:<模板>, data:<模板对象>, merge?}`——upsert 一行。`merge:true` = 单行锁内先读现行 data 浅合并再写回（**渲染值为 null 的键跳过**——可选参数缺省不改字段的关键语义）；merge 省略/false = 整行替换。
  - `append`：`{op:"append", tbl, key:<模板>, field:<数组字段名>, value:<模板>, maxLen?(1~2000)}`——单行锁内把 value 尾追进现行 data[field] 数组（超 maxLen 从头裁剪），行不存在则创建。**多 actor 并发写的核心原语**（见 multi-actor.md）。
  - `delete`：`{op:"delete", tbl, keys?:[<模板>]|prefix?}`——删行（单次 ≤1000）；**必须给 keys 或 prefix 之一**（禁无条件全删）。

  任一效果失败即中止后续（不回滚已执行的），工具返回 {ok:false, error, doneEffects}；成功且有写返回 {ok:true, results:[各效果结果]}。**把 effects 设计成「先流水后状态」的可重放序列**（如 make_move 先 put moves 流水行、再 put board 状态行）——中途失败也有流水可复盘。
- **占位符**（模板里纯字符串替换，绝无表达式运算；三种命名空间）：
  - `${args.参数名}`：引用工具入参（该参数必须在 inputSchema.properties 声明）。**整串恰好一个占位符** → 保留原类型（`"${args.col}"` 单独作值 → 数字 col，不是字符串）；**内嵌**（`"${args.col}_${args.row}"`）→ 字符串拼接。
  - `${row.字段名}`：put-merge / append 执行前读到的现行行 data 的对应字段（行不存在 = null）。
  - `${ctx.uid|session|ts|tool}`：平台注入——板主 uid / 当前会话 key / 纪元毫秒 / 当前工具名。`${ctx.ts}` 常用来拼流水行 key 保唯一。
- **完整示例（五子棋，LLM 裁判模式，直接照此结构写）**：

  ```json
  {
    "version": 1,
    "tools": [
      {
        "name": "get_board", "displayName": "读取棋盘",
        "description": "读取当前五子棋棋盘状态与最近落子流水。落子前必须先调用它了解局面。",
        "inputSchema": {"type": "object", "properties": {}},
        "effects": [
          {"op": "read", "tbl": "board", "keys": ["state"]},
          {"op": "read", "tbl": "moves", "limit": 20}
        ]
      },
      {
        "name": "make_move", "displayName": "落子",
        "description": "在棋盘 (col,row) 落一子。side 为 black/white；newCells 为落子后的完整棋盘数组（LLM 裁判：由你算好整盘传回）；actor 标明落子方。",
        "inputSchema": {
          "type": "object",
          "properties": {
            "col": {"type": "integer", "description": "列 0-14"},
            "row": {"type": "integer", "description": "行 0-14"},
            "side": {"type": "string", "enum": ["black", "white"]},
            "newCells": {"type": "array", "description": "落子后完整棋盘"},
            "actor": {"type": "string", "description": "落子方，如 ai/user"}
          },
          "required": ["col", "row", "side", "newCells", "actor"]
        },
        "effects": [
          {"op": "put", "tbl": "moves", "key": "${ctx.ts}_${args.col}_${args.row}",
           "data": {"col": "${args.col}", "row": "${args.row}", "side": "${args.side}", "actor": "${args.actor}", "ts": "${ctx.ts}"}},
          {"op": "put", "tbl": "board", "key": "state", "merge": true,
           "data": {"cells": "${args.newCells}", "lastMove": {"col": "${args.col}", "row": "${args.row}", "side": "${args.side}"}}}
        ]
      },
      {
        "name": "reset_board", "displayName": "重置棋盘",
        "description": "清空棋盘与落子流水，开始新一局。",
        "inputSchema": {"type": "object", "properties": {}},
        "effects": [
          {"op": "put", "tbl": "board", "key": "state", "data": {"cells": [], "lastMove": null, "turn": "black"}},
          {"op": "delete", "tbl": "moves", "prefix": ""}
        ]
      }
    ]
  }
  ```

- **两种裁判模式（博弈类 manifest 设计时二选一）**：
  - **页面 JS 裁判（博弈类推荐）**：规则引擎（合法性校验、胜负判定、轮次切换）全在页面 JS 里。agent 的落子工具只传「意图」（如 col/row），页面 onChange 收到 agent 的写后校验合法性、再落子并写回权威棋盘。agent 只负责「下一步下哪」（策略），不算胜负——规则确定、可测，agent 不可能违规落子。
  - **LLM 裁判**：agent 读棋盘（get_board）、自己算合法性与胜负、写回整盘（make_move 传完整 newCells，即上例）。适合规则极简或回合制模拟；缺点是 agent 可能算错规则，页面要做容错。
- **页面侧配套四件套（做了互动接口，页面必须做到这四条，否则「agent 动了页面不知道」）**：
  ① 启动先 loadRows 拉全量；② AppBridge.onChange 订阅，收到 by='mcp'/'agent' 的变更就重新 loadRows 相关 tbl 重渲染（agent 用工具落的子才能即时出现在页面上）；③ 用户在页面操作后立即 putRows（页面自己落的子也要落库，并经 SSE 广播给 agent 侧的其他订阅者）；④ 用户走完一步需要 agent 回应时，AppBridge.sendToChat(text) 呼叫 agent（如「轮到你了」），**务必处理降级返回**：{ok:false, reason:'busy'}（agent 正在响应——宿主画板通常已自动排队、回合结束即补发并回执 ok，但分享页等场景仍可能收到 busy，退避稍后再试或提示用户）、'no-channel'（分享页无此通道，合法，静默即可）、'cooldown'（触发太快，AppBridge 内置 3s 冷却）。**收到 ok 即视为已送达，不要重复呼叫**（重复呼叫会让 agent 对同一手应两次）。
- **生效时序（重要，否则会误以为写完就生效）**：mcp.json 的读取与校验发生在发消息时，但**工具面在「下一条」消息才对 agent 可见**（agent 实例按 manifest 指纹重建，重建约 8~14s）。所以写完 / 改完 mcp.json：本轮回复里**告知用户「互动接口已配置，下一条消息起生效」**，别在本轮就自己调用它指望生效。下一条消息发出时系统会在消息里注入挂载状态（成功=「互动接口已挂载（指纹 xxx）：可用工具 mcp__app_..._<name>」；校验失败=「⚠ 互动接口未挂载：...校验失败——<原因>」）——**看到 ⚠ 就按原因自修**（错误消息带 tools[i].effects[j] 定位）。
- **发布纪律**：mcp.json 生效不依赖 publish（直接读盘），但它是普通应用文件，**仍须照常 publish_html_board**（跨机物化 + 版本存档一致性）。改 mcp.json 同样走上面的「下一条消息」时序。
