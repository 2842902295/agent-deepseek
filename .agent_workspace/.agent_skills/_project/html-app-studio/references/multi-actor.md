# 多角色 / 多 NPC 协作应用（多个子 agent + 用户并发读写同一 app）

用户要「多 NPC 社区」「多角色协作」「多智能体模拟」这类时按本册做——主 agent **并行委派**多个子 agent 扮演各角色，与用户页面一起并发读写同一 app 的共享状态。互动接口基础规范（manifest 格式 / 效果操作 / 占位符）见 references/mcp-manifest.md。dsh 未启用 toolFilter → **spawn 出的子 agent 与主 agent 共享全局工具注册表**，互动接口工具（mcp__app_*__*）对子 agent 天然可见可调。

- **并发安全三律（违反任一条都会数据覆盖错乱）**：
  ① **消息 / 事件流 append-only、每条一行**：多方发言 / 行动流，**首选一条行动一行、行 key = `${ctx.ts}_${args.actor}`**（零读改写、天然并发安全，多 actor 同时写也不撞）；或用 append 尾追进单流数组行（单行锁保序，记得配 maxLen 裁剪）。**不要**让多 actor 都 put 到同一个「消息列表」行（整行覆盖会丢消息）。
  ② **每个角色独立状态各占一行**：每 NPC 的状态单独一行（如 tbl="npcs"、key=npc_id），并行写零冲突；**不要**把所有 NPC 塞进一个大行。
  ③ **禁止整表读出再整体写回**；共享状态行（如棋盘 board/state）天然 last-writer-wins——这类行要**单一角色串行维护**或拆成多行，避免多 actor 同时写同一行。回合类博弈靠 turn 字段 + 页面裁判兜底（mcp-manifest.md「两种裁判模式」）。
- **actor 身份必须显式传参（硬要求）**：平台侧无法区分是哪个子 agent / 哪个 NPC 写的（updated_by 恒为板主 uid）。因此 **manifest 的协作工具必须声明一个 `actor` 字符串入参**（如「谁在发言」），**委派子 agent 时把该角色身份写进委派描述**，让 LLM 调用时显式传，`${args.actor}` 落进 data。`${ctx.*}` 只作审计兜底，不能区分角色。红利：actor 是标量入参，过程时间线里原样可见（「阿珍 发言」级业务感）。
- **委派子 agent 的描述要素**（主 agent 并行委派时每个都要讲清）：角色名 + 人设 + 该角色的独立行 key + 可用工具（mcp__app_{wk}__<name>）+ 本次 actor 传什么值。兜底写法：若子 agent 因故调不到工具，**让子 agent 把行动结果用文本带回、主 agent 统一写入**（串行但永远可用）。
- **页面多 actor 渲染**：页面订阅 onChange，多个 actor 的发言 / 行动行流入时按 data.actor 区分渲染（不同头像 / 颜色 / 名字）。
- **NPC 自主性边界如实告知（交付必讲）**：dsh 是**回合制**，NPC **不会自发行动**（没有自发轮次）。社区的「活」靠三种驱动：
  ① 用户发消息 → 主 agent 并行委派多 NPC 推进一个「社区时间片」；② 页面定时器 sendToChat（「社区心跳」，间隔 ≥60s，尊重 AppBridge 3s 冷却与 busy 退避）；③ 平台调度任务（create_scheduled_task）周期驱动。
  **交付时如实告诉用户「NPC 不会在无人触发时自己动」**，别承诺自主运转。
