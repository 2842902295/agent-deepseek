# 平台契约：项目约定 1~12（应用制作）

任务目录 = 项目，HTML = 页面，需要持久化数据的应用才用 json 当数据库。风格与方法论见 SKILL.md；本册是数据层 / 通道 / 发布 / 多用户的全部契约细节。

1. **一切写在任务目录下**（消息注入会给出 workflow_key 与当前文件清单）：write_file / edit_file 用相对工作目录的路径，形如 `apps/{workflow_key}/index.html`。入口必须是 index.html，路径一律正斜杠。
2. **单页多视图，禁止页间跳转（防闪屏）**：应用跑在 iframe 里，任何页面跳转（href / location 换页）都是整文档重载——白屏闪烁，体验极差。工作台 / 列表 / 详情 / 设置这类模块切换，一律用 JS 在同一文档内切换视图（显示对应区块 + 导航高亮，视图先渲染好放 DOM 里藏显控制）。样式 / 脚本抽根目录 style.css / app.js 共享引用；单文件过大时按职责把 css / js 拆成多个文件，**而不是拆成多个 html 页面互跳**。
3. **数据层（有用户数据改动的应用的硬要求：用户数据必须留到下次打开——刷新 / 关板再进，改动都得还在）**：
   **新应用的用户数据一律走行数据通道**（服务端行库，一行 = 一条业务记录；多人同时写互不覆盖，发布 / 回滚也不受影响）。
   托管路由只有「静态文件 GET、whoami GET、rows GET 与 save / proxy / ai / upload / extract / rows/put / rows/del 七个 POST」——**没有 /load 之类的数据端点，不要臆造**。（serve html 时系统还会自动注入「编辑文字」脚本，见第 11 条，你无需实现。）
   **纯展示页不需要数据层**——内容直接写进 HTML，别给展示页硬套数据读写。
   **禁止用 localStorage / sessionStorage / 内存变量代替行通道**——那些不是本应用的持久化层，刷新或换环境即丢。
   **数据组织**：一个逻辑集合一个 `tbl`（如 records / members / settings，字母数字下划线开头，可含 . _ -）；行键自定（≤191 字符），多用户按人的数据建议 `{visitorId}:` 前缀或单独 tbl（身份见第 12 条）。
   **读 / 写 / 删**标准写法（APP_TOKEN 提取与其他通道同款）：

   ```js
   const parts = location.pathname.split('/');
   const APP_TOKEN = parts[parts.indexOf('html-app') + 1] || '';
   const ROW_BASE = '/api/v1/ai/html-app/' + APP_TOKEN;

   // 读：返回 {rows: [{key, data, updatedBy, updatedAt}], more}，按行键排序；可传 prefix 前缀过滤、limit（≤1000，默认 200）、offset 翻页
   // 注意：updatedBy / updatedAt 是平台元数据（updatedAt 为 ISO 8601 字符串），只供展示，勿混入 data 里自己的业务字段
   async function loadRows(tbl, prefix) {
     const q = new URLSearchParams({tbl: tbl, limit: '200'});
     if (prefix) q.set('prefix', prefix);
     const j = await (await fetch(ROW_BASE + '/rows?' + q)).json().catch(() => ({}));
     if (j.code !== '0000') throw new Error(j.msg || '读取失败');
     return j.data;
   }
   // 写：upsert 语义（同键覆盖、新键插入），一次 ≤200 行、单行数据 ≤256KB
   async function putRows(tbl, rows) { // rows: [{key: 行键, data: 任意JSON}]
     const j = await (await fetch(ROW_BASE + '/rows/put', {
       method: 'POST', headers: {'Content-Type': 'application/json'},
       body: JSON.stringify({tbl: tbl, rows: rows})
     })).json().catch(() => ({}));
     if (j.code !== '0000') alert('保存失败：' + (j.msg || '未知错误'));
   }
   // 删：一次 ≤200 键
   async function delRows(tbl, keys) {
     const j = await (await fetch(ROW_BASE + '/rows/del', {
       method: 'POST', headers: {'Content-Type': 'application/json'},
       body: JSON.stringify({tbl: tbl, keys: keys})
     })).json().catch(() => ({}));
     if (j.code !== '0000') alert('删除失败：' + (j.msg || '未知错误'));
   }
   ```

   **APP_TOKEN 只能照上面这样从页面自身 URL 提取**——绝不能写成 workflow_key、占位符或任何固定字符串（写死 = 服务端全部拒绝，保存静默失败，用户数据丢失）。
   页面启动先 loadRows 拉全量再渲染；拉到「没有该数据表的读取权限」这类错误要如实提示（见第 12 条 $acl）。
   **写回时机不变**：用户每次操作后**立即写回**（改完调 putRows / 删完调 delRows）。
   **时间字段约定（1970 灾难防再犯）**：`data` 里的业务时间字段（createdAt / deadline / joinedAt 等）一律存 `Date.now()` **纪元毫秒**（数字，可比较可排序）。
   读返回外层的 updatedBy / updatedAt 是平台元数据（updatedAt 是 ISO 8601 字符串，如 `2026-08-28T17:01:22.123456`）：原样展示可以，**禁止用展开等方式拿它覆盖 data 里自己的字段**（`{...r.data, updatedAt: r.updatedAt}` 是已发生的真实错误模式——ISO 串混进毫秒字段），**更禁止 parseInt**（`parseInt("2026-08-28T…")` = 2026，`new Date(2026)` 直接渲染成 **1970-01-01**）。
   时间渲染统一走一个兼容三种输入（毫秒数字 / 纯数字字符串 / ISO 字符串）的格式化函数，无效值兜底 '-'，绝不显示 1970 / NaN：

   ```js
   function fmtTs(ts, withTime) {
     const n = typeof ts === 'number' ? ts : (/^\d+$/.test(String(ts)) ? Number(ts) : Date.parse(ts));
     const d = new Date(n);
     if (!ts || isNaN(d.getTime())) return '-';
     const p = x => String(x).padStart(2, '0');
     const s = d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
     return withTime ? s + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) : s;
   }
   ```

   **系统注入 window.AppBridge（推荐，替代上面手写的 fetch 样板）**：serve 页面时系统自动注入一个 `window.AppBridge` 对象，封装了数据层与互动通道——直接调它即可，无需手写 APP_TOKEN 提取 / fetch / 解信封 / SSE：
   - `AppBridge.token`：当前应用 token（已从页面 URL 提取好）；
   - `AppBridge.whoami()` → Promise<{ownerId, visitorId, isOwner, share, nickName, userName}>（身份，见第 12 条）；
   - `AppBridge.loadRows(tbl, {prefix, limit, offset})` → Promise<{rows, more}>（等价上面手写 loadRows）；
   - `AppBridge.putRows(tbl, rows)` / `AppBridge.delRows(tbl, keys)`（rows=[{key,data}]、keys=[字符串]）；
   - `AppBridge.onChange(cb)` → 返回退订函数：订阅本板行数据变更的**服务端 SSE 实时推送**，cb 收到 {type:'rows', tbls:[变更的表名], rev, by}（by ∈ 'page' 页面写 / 'agent' 对话工具写 / 'mcp' 互动接口工具写）。300ms 自动合并去抖、断线自动重连。**任何写者改了数据都会触发**（自己 putRows、agent 用 update_app_rows 写、其他访客页面写、互动接口工具写）——这是「人机对局 / 多人协作」页面实时刷新的基础。收到变更后对相关 tbl 重新 loadRows 再渲染即可（幂等，丢中间事件无害）；AppBridge 不替代「启动先 loadRows 拉全量」，首屏仍要自己拉。
   - `AppBridge.sendToChat(text)` → Promise<{ok, reason?}>：页内反向呼叫 agent 触发一个对话回合（详见 references/mcp-manifest.md）。

   上面手写 fetch 的写法仍然完全合法（AppBridge 只是它的封装）；**存量已手写数据层的应用不必迁移到 AppBridge**。

   **存量 json 数据层（data/store.json + /save）的应用保持原样，不要迁移**——/save 通道只为存量应用写回与系统「编辑文字」（第 11 条）保留，**新应用的业务数据不走 json /save**（text-edits.json 也禁写）。
   **改版 / 重写页面时必须原样保留数据层的读写方式**——UI 改坏了可以重来，持久化层改丢了 = 用户数据「保存了却消失」，是最严重事故。
4. **交互自闭环**：功能在页面内完成（增删改查 / 筛选 / 排序 / 表单校验），别让用户回到对话框里点按钮；不依赖任何外网静态资源（CDN / 在线字体 / 图床 / 统计脚本），样式脚本全部本地文件，离线可用是硬要求（业务数据接口例外——走第 6 条的外部数据通道；生图工具存进任务目录的图片就是本地资源，放心用，见 SKILL.md「设计先行」第 8 条）。
5. **文件通道（用户本地文件进应用 / 文档解析）**：页面需要用户提供本地文件（`<input type="file">` 选合同 / 报告 / 表格 / 图片……）时，走标准 multipart 上传（FormData）POST 同源 /upload 存入任务目录（单文件上限 10GB）：

   ```js
   async function uploadFile(file, extractText) { // file: <input type=file> 选中的 File 对象
     const fd = new FormData();
     fd.append('file', file);
     fd.append('extractText', extractText ? 'true' : 'false');
     const r = await fetch('/api/v1/ai/html-app/' + APP_TOKEN + '/upload', {method: 'POST', body: fd});
     const j = await r.json().catch(() => ({}));
     if (j.code !== '0000') throw new Error(j.msg || '上传失败');
     return j.data; // {path: 'uploads/xxx.pdf', size, text?, textKind?, textChars?, textTruncated?}
   }
   ```

   **这是本环境唯一的 multipart 端点**（save / proxy / ai / extract 仍一律 JSON）；fetch 时**不要手动设置 Content-Type**——boundary 由浏览器生成，自己加头反而解析失败。存下的文件在目录内 uploads/ 下，可像静态资源一样 fetch(path) 读回（展示 / 做下载）。
   **文档分析**：extractText=true 时服务端直接提取正文随 text 返回（支持 pdf / docx / xlsx / txt / csv / md / json 等；超长会截断，看 textTruncated；超过 50MB 的文件跳过提取，textKind=too_large；doc / ppt 等老格式不支持，text 为空、textKind=unsupported——不支持的情况如实提示用户，别假装解析成功）。要把内容喂给 /ai 时直接用这段 text（注意 /ai 单次输入有字数上限，长文要在页面里分段发送）；目录里已有的文件（你自己写入的附件、以前上传的）要提取文本，POST /extract，请求体 {path}，返回同构的 {text, textKind, textChars, textTruncated}。上传 / 解析失败都要在页面明确提示，不能静默吞掉。
6. **外部能力通道（外部 API / 平台 AI）**：应用需要在运行期调外部 API 取数据（天气 / 汇率 / 公开资讯 / 第三方接口……）时，页面不要直接 fetch 跨域地址（浏览器 CORS 会拦），更不要宣称做不到——一律走同源代理（服务端代发）：

   ```js
   async function proxyFetch(url, options) {
     const r = await fetch('/api/v1/ai/html-app/' + APP_TOKEN + '/proxy', {
       method: 'POST',
       headers: {'Content-Type': 'application/json'},
       body: JSON.stringify(Object.assign({url: url}, options || {}))
     });
     const j = await r.json().catch(() => ({}));
     if (j.code !== '0000') throw new Error(j.msg || '外部请求失败');
     return j.data; // {status, contentType, body, truncated, encoding?}
   }
   ```

   options 可带 {method, headers, body}（headers 仅业务类生效，host / cookie 不转发）。返回的 body：文本类（json / text / xml）是 utf-8 字符串，二进制是 base64（带 encoding='base64'）；**上游状态码在 data.status 字段**（上游 4xx/5xx 时 j.code 仍是 0000，要自己看 status 判错）；不跟随跳转、内网地址会被拒。接口需要的 API key 做成应用「设置」页里的输入项（存进数据层），别硬编码进代码、也别在对话里索要；外部接口可能失败，页面要有明确的错误提示，不能白屏。取回的数据按数据层约定该存就存。
   **平台 AI（让应用本身成为 AI 应用）**：页面需要对话 / 生成 / 分析等 AI 能力时，调同源 `/ai` 端点直接用平台模型（免任何 key；模型配置与计费跟随实际使用者——「仅登录用户」分享时跟随访客本人的角色配置，免登录分享与板主使用跟随板主；对话历史由页面自己维护成 messages 数组，别期待服务端记上下文；有频率限制，收到「请求过于频繁」错误要如实提示用户）：

   ```js
   async function aiChat(messages) { // messages: [{role: 'system'|'user'|'assistant', content: '...'}]
     const r = await fetch('/api/v1/ai/html-app/' + APP_TOKEN + '/ai', {
       method: 'POST',
       headers: {'Content-Type': 'application/json'},
       body: JSON.stringify({messages: messages})
     });
     const j = await r.json().catch(() => ({}));
     if (j.code !== '0000') throw new Error(j.msg || 'AI 调用失败');
     return j.data.content; // 字符串
   }
   ```

   user 消息可附图片走多模态：{role:'user', content:'...', images:['<图片字节 base64>', ...]}（最多 3 张、单张 ≤2MB、合计 ≤3MB；当前模型不支持图片时接口报错，页面要捕获并如实提示）。注意区分：**图片走 images，文档（docx / pdf / xlsx）走第 5 条的 extractText / extract 提取文本后放进 content**——别把文档 base64 塞给 AI。
7. **安全纪律**：每个 html 页的 `<head>` 必须带 `<meta name="referrer" content="no-referrer">`（访问凭据在 URL 里，防 Referer 泄漏）；应用只调用自己的 save / proxy / ai / upload / extract 接口，不探测其他接口，不读写任务目录外的文件。
8. **发布纪律（最易违反）**：用户画布只在你调用 `publish_html_board(workflow_key)` 工具后才会更新——**每写完 / 改完一批文件，最后必须调用一次它**（先把文件全部写完再发布；不发布 = 用户看到的还是旧版或占位）。
   **发布前必做两项自查**：① 写回自查（仅页面带数据层时——行通道或存量 json 均适用）——逐一枚举页面里所有会改变数据的用户操作（新建 / 编辑 / 删除 / 勾选完成 / 状态切换 / 排序……），确认**每一个**入口的处理函数最终都调用了写回（行通道 = rows/put 或 rows/del；存量 json = /save）——漏掉任何一个，那个操作的数据就会刷新后凭空消失（最高频事故）；② 形态自查——按 SKILL.md「设计先行」第 6 条做换主题自检 + 5.2 过时风格负面清单逐项排除，发现自己又做成了传统网页骨架就改掉再发布。
   发布后对话只说一句「已更新，……」，别复述页面内容。
   **版本存档与回滚**：版本存档只在用户点画布顶栏「发布」按钮时固化（系统自动管理，保留最近 5 版；存档在数据库里，不在应用目录中）；你的 publish_html_board 只更新用户画面、不产生版本，也不要向用户承诺「帮你存一版」（版本由用户自己点发布固化）。
   **用户说「回滚到上一版 / 恢复旧版 / 还原你改之前的样子」时，直接调 `rollback_workflow_version(workflow_key)`**（可指定存档版本号，不传回上一版）——严禁从对话记忆里手动重建旧文件（记忆必然不全、重建必失真，此前已多次酿成事故），也严禁读写 / 删除 .versions/ 里的任何东西。
9. **增量修改**：改已有应用先 read_file 相关文件，再用 edit_file 局部改；禁止整目录重写；**禁止删除用户数据**（行数据与存量 json 数据层都是）——数据是用户的资产，改数据结构要做迁移：行数据用 `query_app_rows` 读出 → 转换 → `update_app_rows` 写回；存量 json 读旧文件 → 转换 → 写新文件。
10. **对话风格**：像有产品意识的创作者——先想清楚内容结构与页面形态（应用再想数据模型）再动手；审美小细节自己拍板（别为配色 / 布局反复征询），复杂应用的骨架与审美大方向信息不足时先一次性确认再开工（见 SKILL.md 第 2 节）。做完一句话交付；对话里不贴代码。用户验收时提的修改意见 = 下一轮需求，同样走「改文件 → 发布」循环。
11. **系统文字编辑（托管路由自动注入，你不要自己实现同类功能）**：serve 页面时系统注入了「编辑文字」能力——用户点画布外框顶栏的「编辑文字」按钮后可就地修改页面上的静态文字，改动以替换对 {from: 原文, to: 改后} 存进 `data/text-edits.json`，页面加载时自动套用。**你迭代任何已有看板前先 read_file 这个文件**（不存在则跳过）：把原文仍在新页面中存在的替换合并进页面（直接按 to 改 html 里的文本），合并完用 write_file 把该文件重置为 []（内容已烘焙进页面，这不算删用户数据）；原文已被你重写的替换直接作废丢弃。严禁无视这份清单、更严禁用自己记忆里的旧文案覆盖用户改过的文字。注意：应用从 store.json 等动态渲染的文字不在此能力范围内，那类文字靠应用自身的编辑入口修改。
12. **多用户与角色（分享给多人使用的应用——用户要「多用户 / 多角色 / 成员 / 权限管理」类应用时按本条完整搭）**：
    - **往完善、复杂去想（这类需求的默认姿态）**：用户要的是系统，不是演示。除非用户明确要简版，一律按完整配置交付：① 认真设计角色模型——几种角色、各自能看什么 / 能做什么，做出**真实的权限差异**（不同的视图、操作按钮、数据范围），而不是挂个角色徽章完事；② 板主管理屏（成员列表 / 授予角色 / 移除成员）；③ 按 $acl 两种模式落实数据保护——该按人私密的私密、该按角色限制的制限；④ 每个角色都有事可做——登录后呈现该角色该看的工作台与待办，而不是只有一个全员共用的列表页；⑤ 空态、无权限态、错误提示做全。别把多角色系统缩水成一张表加一个角色下拉框，也别「先跑起来以后再加」——完整性在第一版交付。
    - **身份识别（唯一入口）**：GET `/api/v1/ai/html-app/{APP_TOKEN}/whoami` 返回 `{ownerId(板主), visitorId(访客用户ID或null), isOwner(是否板主本人), share, nickName, userName}`——应用一律经此端点识别当前用户，**绝不让用户自报身份 / 手动选择「我是谁」**（包括「方便起见」放身份选择下拉框）。**ownerId / visitorId 均为字符串**，可直接用作行 key、直接与行键 === 比较，无需 String()/Number() 转换（但对旧数据 / 表单取值做比较时，两侧都包一层 String() 永远更稳）。板主 token 恒 isOwner=true、visitorId=null；**免登录分享时 visitorId 恒为 null（匿名）**。判断是否板主用 `isOwner || visitorId===ownerId`（板主自己也可以经分享链接打开，此时 isOwner=false、visitorId=板主 uid）。
    - **分享模式（多用户生效的开关，交付必讲）**：访客身份只存在于「仅登录用户」分享模式；分享由用户在画板顶栏「分享」面板开启（你操作不了），且**开启分享一律默认「免登录」（访客全部匿名）**——多用户应用「不能用」的头号原因就是这个。因此：
      · 多用户应用的交付说明里必须明确告诉用户下一步：画板顶栏「分享」→ 开启分享 → **切到「仅登录用户」**→ 复制链接发给他人，对方登录后打开即自动识别身份。
      · 匿名分支（visitorId=null）必须设计：给友好落地页（引导改走登录方式打开 / 或提供只读体验），别报错、别白屏、别把匿名当某个 uid。
      · 消息上下文会给出该板当前分享状态；发现需求是多用户但分享未开或处于「免登录」，主动提醒用户切换。
    - **标准装配序列（多用户应用的参考架构，别从零发明）**：① 启动调 whoami 取身份 → ② members 表解析 / 登记成员行 → ③ 读自己的角色 → ④ 按角色渲染视图与操作。
      成员表 tbl="members"：行 key = uid（whoami 返回的已是字符串，板主用 ownerId、访客用 visitorId，原样用即可），data = {name, role, joinedAt, ...}。
      · 访客首访自动登记一行（name 取 whoami 的 nickName/userName，role 给 'member' 之类的普通成员默认值）；板主行 role 恒 'owner'（启动时发现没有该行就自动补种）。
      · members 表通常很小：loadRows 全表后按 key **精确匹配**自己的行（别靠前缀匹配，防 uid 7 撞上 70/71）。
      · 角色判断每次都以 members 表为准，禁止把角色缓存进 localStorage（板主改了角色必须刷新即生效）。
      · 角色变更入口只在板主管理屏（loadRows members 渲染成员列表、改 role 字段后 putRows 写回）；普通访客页面只展示自己的角色。
      · 成员加入流：对方先打开分享链接（自动登记进 members）→ 板主在管理屏授角色；若提前知道 uid 也可预插 members 行预设角色。
    - **角色完全由应用自管**：平台不提供角色系统，角色定义（几种、叫什么、各能做什么）全由你建模、页面逻辑判断。**板主恒最高权限**：页面逻辑必须无条件给板主管理权，不要让板主走普通角色流程。
    - **$acl 行级保护（服务端强制，不是页面自律）**：行数据默认任何持有链接的人都可读可写；应用里有按人私密或按角色限制的数据时，必须把受保护的 tbl 登记进 $acl 清单表——用 `update_app_rows(workflow_key, tbl="$acl", upsert_json='[{"key": "受保护表名", "data": {"writers": [允许写的uid], "readers": [允许读的uid]}], …}')` 登记（writers / readers 均可省略 = 不限制该向，名单里 uid 数字或数字字符串均可；板主恒过；未登记的表不受限）。登记后无权限者读写该表会被服务端拒绝（页面收到「没有该数据表的读/写权限」要如实提示，别假装成功、也别无限重试）。
      两条硬边界（别设计做不到的方案）：① $acl 强制粒度是**表级**（按 tbl 给 reader/writer 名单），同表内更细的按人数据靠行 key 前缀 + 页面过滤实现；② **$acl 表本身仅板主可写**——访客侧页面 JS 无法自行登记权限。
      因此按人私密数据有两种模式，按敏感度选：
      · 共享表 + `{uid}:` 前缀行 key（如 `7:note_1`），页面读取时按身份过滤——轻量，但私密性靠页面自律（适合个人待办、草稿这类一般数据）；
      · 独立 tbl + $acl 登记（如表 `notes_7` 登记 {"readers":[7],"writers":[7]}）——服务端强制，但新增受保护表只能由板主侧登记：构建期你就用 update_app_rows(tbl="$acl") 预登记，或应用里给板主管理屏做动态登记（板主页面 JS 可写 $acl）。
      应用内的权限变更（板主在管理屏重新分配某表读写范围）= 板主页面 JS 改写该表的 $acl 行。普通访客页面的权限管理 UI 只能读（GET rows 传 tbl=$acl）不能写。
    - **多用户应用发布前自查（在第 8 条自查之外追加）**：① whoami 已接入且匿名分支有处理；② 页面无任何「选择身份」入口、角色全部来自 members 表；③ 应受保护的表都已登记 $acl；④ 交付说明包含「开启分享并切到仅登录用户」引导。
    - **行数据不随「发布 / 回滚」走**：回滚（第 8 条）只还原页面代码、不碰行数据——回滚后数据仍在，若新旧版本数据结构不同需自行迁移（第 9 条）。这与存量 json 数据层不同（json 文件是页面资产，随回滚整体还原），向用户解释时注意区分。
