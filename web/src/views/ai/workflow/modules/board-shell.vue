<script lang="ts">
import type {Ref} from 'vue';

/** 宿主对话桥：画板所有「找对话」的动作都经此桥，board-shell 不直接持有 QAGlass 实例 */
export interface QaBridge {
  /** 对话是否响应中（画布编辑锁数据源） */
  running: Ref<boolean>;
  /** 发消息（内部自带就绪等待；返回是否发出） */
  sendMessage(text: string): Promise<boolean> | boolean;
  /** 当前会话 key（节点内上传用） */
  currentSessionKey?: () => string;
}
</script>

<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, provide, ref, unref, watch, watchEffect} from 'vue';
import {VueFlow, useVueFlow} from '@vue-flow/core';
import {Controls} from '@vue-flow/controls';
import {MiniMap} from '@vue-flow/minimap';
import dagre from '@dagrejs/dagre';
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';
import '@vue-flow/controls/dist/style.css';
import '@vue-flow/minimap/dist/style.css';
import {
  fetchListWorkflows,
  fetchPublishWorkflowVersion,
  fetchRollbackWorkflowVersion,
  fetchSetWorkflowShare,
  fetchUpdateWorkflow,
  fetchUploadFile,
  fetchWorkflow,
  fetchWorkflowMeta,
  fetchWorkflowVersions,
  type BoardMarks,
  type WorkflowData,
  type WorkflowListItem,
  type WorkflowVersionItem
} from '@/service/api';
import {useAuthStore} from '@/store/modules/auth';
import {brandNodeTypes} from './brand-boards';
import WfIcon from './wf-icon.vue';
import WfNode from './wf-node.vue';
import WfNodePreview from './wf-node-preview.vue';
import HtmlBoard from './html-board.vue';
import {fmtRel} from './fmt-rel';

/**
 * 画板成品组件：给定 workflowKey 的完整画板体验（两种板型 + 顶栏 + 回显 + 版本/分享 + 轮询）。
 * 从 workflow/index.vue 原样抽出（纯搬家），专页与 QA 页伴侣面板共挂同一成品。
 * 与宿主的边界：wk 由宿主以 prop 驱动（route/attached 的读写权在宿主）；
 * 所有「找对话 / 建板 / 回退」动作经 emits 上报；对话能力经 qaBridge 注入。
 */
const props = withDefaults(
  defineProps<{
    workflowKey: string | null;
    qaBridge?: QaBridge;
    /** 顶栏全屏按钮开关（全屏 = fixed 覆盖层占满视口，面板/专页宿主均可用） */
    allowFullscreen?: boolean;
    /** 面板场景隐藏顶栏「对话」钮 */
    showChatToggle?: boolean;
    /** 顶栏「对话」钮激活态（页面宿主传 chatOpen） */
    chatActive?: boolean;
    /** 宿主在插槽里展示任务库时置 true：画布/引导条整体让位 */
    libShown?: boolean;
    /** 面板场景：MiniMap 钳小一档，别占掉半幅面板 */
    compact?: boolean;
  }>(),
  {qaBridge: undefined, allowFullscreen: true, showChatToggle: true, chatActive: false, libShown: false, compact: false}
);

const emit = defineEmits<{
  (e: 'not-found', wk: string): void;
  (e: 'board-switched', wk: string): void;
  (e: 'create-requested', type: 'board' | 'html'): void;
  (e: 'back'): void;
  (e: 'request-chat'): void;
  (e: 'toggle-chat'): void;
  (e: 'preview-attachment', att: {name: string; src: string}): void;
  (e: 'fullscreen-enter'): void;
}>();

const authStore = useAuthStore();
const {fitView, screenToFlowCoordinate, findNode, updateNodeInternals, getViewport, setViewport, dimensions, viewport} = useVueFlow();

// wk 由宿主持有：壳只读（建板/切板/清空都在宿主侧改 prop）
const workflowKey = computed(() => props.workflowKey || '');

// 对话桥兼容层：原有代码一律经 qaRef.value?.xxx 消费（editLocked / 就绪轮询 / 上传取会话 key），
// 搬家零改动的关键——宿主没有桥时为 null：editLocked 恒 false、程序化发消息链路整体哑火
const qaRef = computed(() => {
  const b = props.qaBridge;
  if (!b) return null;
  return {
    running: unref(b.running),
    sendMessage: (t: string) => b.sendMessage(t),
    currentSessionKey: b.currentSessionKey ? b.currentSessionKey() : ''
  };
});

// 附件全屏预览：弹层实例在宿主（页面宿主与悬浮对话窗共享同一实例；QA 页宿主复用自家实例），这里只上报
function previewAttachment(att: {name: string; src: string}) {
  emit('preview-attachment', att);
}

const title = ref('');
const nodes = ref<any[]>([]);
const edges = ref<any[]>([]);
const version = ref(0);
const saveState = ref<'idle' | 'saving' | 'saved'>('idle');
const loading = ref(true);
// 板型：board=节点连线流程编排（默认）/ html=应用制作（iframe 渲染 agent 在任务目录开发的 HTML 应用）
const boardType = ref<'board' | 'html'>('board');
// html 型专用：入口 index.html 是否已就绪（agent 首次写入/发布后翻 true；空任务清理以此为准）
const entryReady = ref(false);
// 真·全屏：浏览器 Fullscreen API 占满整个屏幕 + 隐藏顶栏（返回/标题/新建/切换/对话入口），
// 只留画布 + 右上悬浮退出按钮（Esc 同效）。纯视图态不持久化：返回列表 / 重进页面自动复位
const fullscreen = ref(false);
// 应用制作「编辑文字」：顶栏按钮经 HtmlBoard postMessage 驱动页内注入脚本（后端 serve 时注入），
// 状态由 iframe 回报（edit-state）。切换板 / 切板型时复位——HtmlBoard 整体重建，页内编辑态必然消失
const htmlBoardRef = ref<InstanceType<typeof HtmlBoard> | null>(null);
const htmlEditing = ref(false);
watch([workflowKey, boardType], () => {
  htmlEditing.value = false;
});

// ── 节点徽标（临时协作态）─────────────────────────────────────────────────
// nodes: new=agent 本轮新增 / agent=agent 改过 / human=人改过；edges: new=新连线高亮。
// agent 端每次 edit_workflow_board 全量重建（旧徽标清零，生命周期天然闭合）；
// 人端的标在本地打、随保存原样回传。广播给 WfNode（含预览弹窗子树）渲染角标。
const marks = ref<BoardMarks>({nodes: {}, edges: {}});
provide('wfMarks', marks);
function normalizeMarks(m: any): BoardMarks {
  return {nodes: m?.nodes && typeof m.nodes === 'object' ? m.nodes : {}, edges: m?.edges && typeof m.edges === 'object' ? m.edges : {}};
}
/** 人编辑打标：new 优先保留（agent 刚加的卡被人改了仍显示 NEW——「本轮新增」是更强的信号） */
function markNodeHuman(id: string) {
  const cur = marks.value.nodes?.[id];
  if (cur?.t === 'new' || cur?.t === 'human') return;
  marks.value = {...marks.value, nodes: {...marks.value.nodes, [id]: {t: 'human'}}};
}
function markEdgeNew(id: string) {
  marks.value = {...marks.value, edges: {...marks.value.edges, [String(id)]: {t: 'new'}}};
}
/** 删节点 / 连线时同步清徽标，不留幽灵标 */
function clearMarksFor(nodeIds?: string[], edgeIds?: string[]) {
  const nm = {...marks.value.nodes};
  const em = {...marks.value.edges};
  let changed = false;
  for (const id of nodeIds || []) {
    if (delete nm[id]) changed = true;
  }
  for (const id of edgeIds || []) {
    if (delete em[id]) changed = true;
  }
  if (changed) marks.value = {nodes: nm, edges: em};
}

// 画布编辑：历史栈 / 右键菜单 / 附件上传
const canvasEl = ref<HTMLElement | null>(null);
const history = ref<string[]>([]);
const redoStack = ref<string[]>([]);
interface CtxMenuState {
  x: number;
  y: number;
  nodeId?: string;
  edgeId?: string;
  panePos?: {x: number; y: number};
}
const ctxMenu = ref<CtxMenuState | null>(null);
const fileInputEl = ref<HTMLInputElement | null>(null);
let addSeq = 0;

// 连线操作后的「选择节点类型」弹层（＋ 按住拖动松开 / ＋ 点击 / handle 拖线松开）
// sourceNode 非空 = 添加节点并连线；autoPlace = 无位移的点击式添加，新节点自动避开源节点摆放（拖线松开尊重落点）
// sourceHandle 非空 = 从工作项卡某条任务出线（工作项卡每行一个 handle），新边锚到该行
const connectMenu = ref<
  {x: number; y: number; flowX: number; flowY: number; sourceNode: string; handleType: string; sourceHandle: string; autoPlace?: boolean} | null
>(null);

// 节点类型：通用 8 类词表（开始 / 结束 / 文本叙述 / 工作项 / 数据 / 结论 / 附件 / 人工核查）
// + 品牌专属板型（brand-boards.ts 注册表按 BRAND_VARIANT 供给，含全品牌共用的分镜段卡 segNode），统一由 wf-node.vue 按 type 渲染
const brandNodeDefs = brandNodeTypes();
const nodeTypes = {
  startNode: WfNode,
  endNode: WfNode,
  textNode: WfNode,
  taskNode: WfNode,
  dataNode: WfNode,
  conclusionNode: WfNode,
  fileNode: WfNode,
  reviewNode: WfNode,
  ...Object.fromEntries(brandNodeDefs.map(t => [t.nodeType, WfNode]))
};

// ── 全局预览块（MiniMap）：整板等比例缩小 + 当前视口框，节点多了找方位 / 点击跳转 / 拖动平移 ──
// 缩略语言 = 真实卡片的简化镜像，不再是饱和色块：白底卡 + 类型色细描边（色值与 wf-node.vue 各卡
// 边框一致），结论卡深色、开始/结束渐变胶囊照旧；连线也画出（细虚线），一眼就是"一块缩小的板"
const MINI_CARD_STYLE: Record<string, {fill: string; stroke: string}> = {
  // 叙述卡：真板里最轻的一级，缩略里也最淡（半透明白 + 极浅描边）
  textNode: {fill: 'rgba(255,255,255,0.55)', stroke: 'rgba(15,23,42,0.2)'},
  taskNode: {fill: '#ffffff', stroke: 'rgba(37,99,235,0.45)'},
  dataNode: {fill: '#ffffff', stroke: 'rgba(8,145,178,0.5)'},
  // 结论卡真身是深色渐变，缩略用深色实底锚住视觉重心
  conclusionNode: {fill: '#1e3a8a', stroke: 'rgba(148,163,184,0.55)'},
  fileNode: {fill: '#ffffff', stroke: 'rgba(124,58,237,0.45)'},
  // 核查卡真身是米黄底，照搬
  reviewNode: {fill: 'rgba(255,251,235,0.95)', stroke: 'rgba(217,119,6,0.5)'},
  // 分镜段卡（与注册表色一致；即使注册表未命中也兜底走这条）
  segNode: {fill: '#ffffff', stroke: 'rgba(14,116,144,0.5)'}
};
function miniCardStyle(type?: string): {fill: string; stroke: string} {
  const s = MINI_CARD_STYLE[type || 'textNode'];
  if (s) return s;
  // 未来品牌卡型：注册表色加透明度当描边，卡身照旧白底
  const brand = brandNodeDefs.find(b => b.nodeType === type)?.color;
  return brand ? {fill: '#ffffff', stroke: `${brand}8c`} : {fill: '#ffffff', stroke: 'rgba(100,116,139,0.45)'};
}

// ── 拖拽期冻结 MiniMap 装饰计算（性能）──────────────────────────────────
// MiniMap 缩略节点跟随拖拽实时移动是库内置行为，但页面自定义的两块装饰会跟着每帧全板重算，
// 开销随节点/连线数线性增长，是节点多时拖拽掉帧的主因：
//   ① miniMapGeomLive：每帧遍历全部节点算包围盒（还会改 MiniMap 的 width/height 触发重建 viewBox）
//   ② miniEdgesBySource：读了所有节点 position，拖拽即失效 → 每帧重建全部缩略连线 SVG path
// 因此拖拽期间：几何读上一帧缓存（尺寸不抖）、缩略连线整段停画（松手即回）；
// 缩略节点方块本身继续实时跟随（库内置，是拖拽时看方位的核心信息）。
const nodeDragging = ref(false);
function onNodeDragStart() {
  pushHistory();
  nodeDragging.value = true;
}
function onNodeDragStop() {
  nodeDragging.value = false;
}
// 视口平移/缩放期间同拖拽：MiniMap 每帧 O(N) 重渲是平移掉帧的独立瓶颈之一， move 结束即回
const paneMoving = ref(false);
function onPaneMoveStart() {
  paneMoving.value = true;
}
function onPaneMoveEnd() {
  paneMoving.value = false;
}

// ── 合成层点阵背景（替代 @vue-flow/background）─────────────────────────────
// 官方 Background 每帧改 SVG pattern 属性 = 全画布 pattern 重光栅，平移掉帧的独立瓶颈之一。
// 自实现：点阵画在超大 div 的 background 上，平移只改 transform（合成，零光栅；mod 一个 tile 保证无限平移覆盖），
// 缩放只改 background-size（仅缩放那一帧重光栅一次）。网格随画布 1:1 移动
const dotGridStyle = computed(() => {
  const z = viewport.value.zoom || 1;
  const tile = 20 * z;
  const mx = ((viewport.value.x % tile) + tile) % tile;
  const my = ((viewport.value.y % tile) + tile) % tile;
  return {
    backgroundSize: `${tile}px ${tile}px`,
    transform: `translate3d(${mx}px, ${my}px, 0)`
  };
});

// ── 预览块尺寸自适应板型：竖长链板给竖框、宽板给横框（固定横比例会把竖板压成亚像素竖线，"粗糙"观感的主因）──
const miniMapGeomLive = computed(() => {
  const ns = nodes.value;
  if (!ns.length) return {w: 168, h: 168, px: 0.12};
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const n of ns) {
    const s = getNodeSize(n);
    const x = n.position?.x ?? 0;
    const y = n.position?.y ?? 0;
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x + s.w > maxX) maxX = x + s.w;
    if (y + s.h > maxY) maxY = y + s.h;
  }
  const bw = Math.max(maxX - minX, 1);
  const bh = Math.max(maxY - minY, 1);
  const MAXS = props.compact ? 176 : 320;
  const MINS = 120;
  const k = MAXS / Math.max(bw, bh);
  const w = Math.round(Math.min(Math.max(bw * k, MINS), MAXS));
  const h = Math.round(Math.min(Math.max(bh * k, MINS), MAXS));
  // 屏幕像素/板面单位（组件内部另加 offsetScale 内边距，按 0.9 系数估），供缩略"最小可见尺寸"用
  const px = Math.min(w / bw, h / bh) * 0.9;
  return {w, h, px};
});
// 拖拽冻结：watchEffect 动态依赖——拖拽中短路不读 live（O(N) 包围盒计算整个拖拽期只发生在松手那一刻）；
// 非拖拽期 live 变化即刷缓存。miniMapGeom 才是模板唯一消费入口
let miniGeomCache: {w: number; h: number; px: number} = {w: 168, h: 168, px: 0.12};
watchEffect(() => {
  if (!nodeDragging.value) miniGeomCache = miniMapGeomLive.value;
});
const miniMapGeom = computed(() => (nodeDragging.value ? miniGeomCache : miniMapGeomLive.value));
// 缩略节点最小可见尺寸（板面单位）：超长竖链板（2 万+ 单位）缩到几百 px 后真实尺寸只剩 1-3px，
// 看着像散点"虚线"= 简陋的直观来源；钳到屏上约 6×4.5px 的小卡片才能读出"一列卡片"的结构；放大时居中补偿不偏位
const miniMin = computed(() => ({w: 6 / miniMapGeom.value.px, h: 4.5 / miniMapGeom.value.px}));
// 插槽类型集 = 样式表 ∪ 注册表 ∪ 板上实际出现的类型（segNode 等注册表卡型由此命中插槽）
const miniMapTypes = computed(() => {
  const set = new Set<string>([...Object.keys(MINI_CARD_STYLE), 'startNode', 'endNode']);
  for (const b of brandNodeDefs) set.add(b.nodeType);
  for (const n of nodes.value) set.add(String(n.type || 'textNode'));
  return [...set];
});
/** 预览块插槽渲染：最小尺寸钳制 + 居中补偿；圆角与真卡等比（真卡 14px 圆角 / ~232px 宽 ≈ 6%），开始/结束画胶囊。
 *  返回 rect 全套属性，模板一次 v-bind 消费（此前每卡 4 次调用，缩略全量重渲染时是无谓重复） */
function miniNodeRect(p: {position: {x: number; y: number}; dimensions: {width: number; height: number}; type?: string}) {
  const mw = Math.max(p.dimensions.width, miniMin.value.w);
  const mh = Math.max(p.dimensions.height, miniMin.value.h);
  const cap = p.type === 'startNode' || p.type === 'endNode';
  return {
    x: p.position.x + (p.dimensions.width - mw) / 2,
    y: p.position.y + (p.dimensions.height - mh) / 2,
    width: mw,
    height: mh,
    rx: cap ? mh / 2 : Math.min(mw * 0.06, mh * 0.3)
  };
}
/** 缩略连线：按源节点分组的细虚线路径（svg viewBox 就是板面坐标系，直接画在 MiniMap 的节点插槽里）。
 *  几何仿主画布：flow 从源卡右缘中点贝塞尔到目标卡左缘中点；ref 是直线。细节（行级出线口）省略 */
const miniEdgesBySource = computed(() => {
  const map = new Map<string, {d: string; kind: 'flow' | 'ref'}[]>();
  for (const e of edges.value) {
    const s = findNode(String(e.source));
    const t = findNode(String(e.target));
    if (!s || !t) continue;
    const sw = s.dimensions?.width, sh = s.dimensions?.height, tw = t.dimensions?.width, th = t.dimensions?.height;
    if (!sw || !sh || !tw || !th) continue;
    const sx = s.position.x + sw, sy = s.position.y + sh / 2;
    const tx = t.position.x, ty = t.position.y + th / 2;
    const kind: 'flow' | 'ref' = e.kind === 'ref' ? 'ref' : 'flow';
    const dx = Math.max(Math.abs(tx - sx) * 0.45, 24);
    const d = kind === 'ref' ? `M${sx},${sy} L${tx},${ty}` : `M${sx},${sy} C${sx + dx},${sy} ${tx - dx},${ty} ${tx},${ty}`;
    const key = String(e.source);
    const list = map.get(key);
    if (list) list.push({d, kind});
    else map.set(key, [{d, kind}]);
  }
  return map;
});

/** 指针位置 → 板面坐标：svg 的 viewBox 就是板面坐标系，getScreenCTM 逆矩阵直接换算（任意缩放都准） */
function miniBoardPoint(svg: SVGSVGElement | null, clientX: number, clientY: number): {x: number; y: number} | null {
  const ctm = svg?.getScreenCTM?.();
  if (!ctm) return null;
  const p = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse());
  return {x: p.x, y: p.y};
}
/** 把主画布视口中心对准板面上某点（zoom 不变，只平移） */
function miniCenterOn(bp: {x: number; y: number}) {
  const vp = getViewport();
  setViewport({
    x: dimensions.value.width / 2 - bp.x * vp.zoom,
    y: dimensions.value.height / 2 - bp.y * vp.zoom,
    zoom: vp.zoom
  });
}
let miniDragRaf = 0;
let miniDragPending: {x: number; y: number; svg: SVGSVGElement} | null = null;
function flushMiniDrag() {
  miniDragRaf = 0;
  const p = miniDragPending;
  miniDragPending = null;
  if (!p) return;
  const bp = miniBoardPoint(p.svg, p.x, p.y);
  if (bp) miniCenterOn(bp);
}
function onMiniPointerDown(e: PointerEvent) {
  if (e.button !== 0) return;
  e.preventDefault();
  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  const svg = (e.currentTarget as HTMLElement).querySelector<SVGSVGElement>('svg');
  const bp = miniBoardPoint(svg, e.clientX, e.clientY);
  if (bp) miniCenterOn(bp); // 点哪跳哪；随后按住拖动持续跟随
}
function onMiniPointerMove(e: PointerEvent) {
  const el = e.currentTarget as HTMLElement;
  if (!el.hasPointerCapture?.(e.pointerId)) return;
  const svg = el.querySelector<SVGSVGElement>('svg');
  if (!svg) return;
  miniDragPending = {x: e.clientX, y: e.clientY, svg};
  if (!miniDragRaf) miniDragRaf = requestAnimationFrame(flushMiniDrag);
}
function onMiniPointerUp(e: PointerEvent) {
  (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
  if (miniDragRaf) {
    cancelAnimationFrame(miniDragRaf);
    miniDragRaf = 0;
  }
  miniDragPending = null;
}

// ── 连线两种形态（kind 是唯一真相，type / style 全是渲染派生，不落库） ──────
// flow（默认，省略 kind）= 流程线：蓝色短虚线，代表项目结构（阶段拆解 / 任务归属 / 产出挂载）
// ref = 参照线：浅灰点状直线，代表跨分支的引用 / 对照 / 依据，是注解不是骨架，不参与自动布局层级
const EDGE_KIND_STYLE: Record<'flow' | 'ref', {type?: string; style: Record<string, string | number>}> = {
  flow: {style: {stroke: '#2563eb', strokeWidth: 1.5, strokeDasharray: '5 3'}},
  ref: {type: 'straight', style: {stroke: '#94a3b8', strokeWidth: 1.5, strokeDasharray: '1.5 5', strokeLinecap: 'round'}}
};
/** 按 kind 给边对象补上渲染用的 type / style（加载 / 撤销重做 / 手建连线统一走这里） */
function decorateEdge(e: any): any {
  const kind = e.kind === 'ref' ? 'ref' : 'flow';
  const spec = EDGE_KIND_STYLE[kind];
  // animated 是纯渲染派生，唯一来源是下方的 edges 徽标：先剥掉入参可能携带的历史值，
  // 否则曾被标记过的边经 {...e} 扩散后永久流动（库里的历史脏数据加载时也就地洗掉）
  const {animated: _animated, ...base} = e;
  const out: any = {...base, kind, style: {...spec.style}};
  if (spec.type) out.type = spec.type;
  else delete out.type;
  // 新连线高亮（徽标 edges.new）：加粗实线 + 流动动画——比常规流程线（细虚线）明显一档，
  // 与节点 NEW 徽标同色（紫），agent 下次编辑随徽标一起清除
  if (marks.value.edges?.[String(e.id)]) {
    out.animated = true;
    out.style = {...out.style, stroke: '#7c3aed', strokeWidth: 2.4};
    delete out.style.strokeDasharray;
  }
  return out;
}

let lastLocalEdit = 0;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let saveTimer: ReturnType<typeof setTimeout> | null = null;

// ── 人机协作改动基线 ──────────────────────────────────────────────────────
// 「上一次与服务器同步的板子状态」快照。人保存时与之 diff 出本次改动的详尽简报：
// 字段级旧→新、节点内容速览、连线两端标签、标题改动——页面回显面板与随 PUT 上报的 humanEdit 共用这份 diff
// （程序算好，agent 直接照简报回应，不用凭记忆猜；不记拖拽坐标）。
let baselineNodes: {id: string; type: string; dataKey: string; label: string}[] = [];
let baselineEdges: {id: string; source: string; target: string; sourceHandle: string}[] = [];
let baselineTitle = '';

// 节点可读标签（与后端 agent_workflow.py::_node_label / 列表预览同契约：按类型取当家字段）
const NODE_TYPE_ZH: Record<string, string> = {
  textNode: '文本',
  taskNode: '工作项',
  dataNode: '数据卡',
  conclusionNode: '结论',
  fileNode: '附件',
  reviewNode: '人工核查',
  startNode: '开始',
  endNode: '结束',
  ...Object.fromEntries(brandNodeDefs.map(t => [t.nodeType, t.zh]))
};
function nodeLabel(n: {type?: string; data?: any}): string {
  const t = String(n.type || 'textNode');
  const d = n.data || {};
  const bt = brandNodeDefs.find(x => x.nodeType === t);
  let name = '';
  if (bt) name = String(bt.labelOf?.(d) || '');
  else if (t === 'taskNode' || t === 'dataNode') name = String(d.title || '');
  else if (t === 'conclusionNode') name = String(d.claim || '');
  else if (t === 'fileNode') name = String(d.name || '');
  else if (t === 'reviewNode') name = String(d.question || '');
  else if (t === 'startNode' || t === 'endNode') name = String(d.label || '');
  else name = String(d.text || '').slice(0, 20);
  const zh = NODE_TYPE_ZH[t] || '节点';
  return name ? `${zh}「${name.slice(0, 18)}」` : zh;
}

function takeBaseline() {
  baselineNodes = nodes.value.map(n => ({
    id: String(n.id),
    type: String(n.type || 'textNode'),
    dataKey: JSON.stringify(n.data || {}),
    label: nodeLabel(n)
  }));
  baselineEdges = edges.value.map(e => ({
    id: String(e.id),
    source: String(e.source),
    target: String(e.target),
    sourceHandle: e.sourceHandle ? String(e.sourceHandle) : ''
  }));
  baselineTitle = title.value;
}

// ── 字段级 diff：结构化净值 + 文本派生 ─────────────────────────────────────
// 关键设计：字段改动以结构化净值（scalar 的 from/to、array 的 add/rem 行集合）存进累积改动集，
// 跨保存按字段归并——逐字输入触发的连环保存（s→s6→s6 其他）最终只剩一行净变化「s」→「s6 其他」，
// 不留中间态。人读的文本与上报 agent 的文本都由 fieldChangeText 从同一结构派生，保证一字不差。
const FIELD_ZH: Record<string, string> = {
  title: '标题',
  text: '正文',
  claim: '结论',
  summary: '摘要',
  note: '注记',
  metric: '主数值',
  unit: '单位',
  attrs: '属性',
  points: '要点',
  samples: '证据',
  subs: '任务清单',
  question: '提问',
  options: '选项',
  answer: '回答',
  label: '名称',
  name: '文件名',
  disabled: '收口锁定',
  seg: '段位',
  duration: '时长',
  emotion: '情绪',
  scene: '场景',
  state: '状态',
  shots: '分镜清单'
};
// 附件伴生字段：name 已说明换了什么，url/mime/size 不单独列（减噪）
const FIELD_SKIP = new Set(['url', 'mime', 'size']);
const ARRAY_FIELDS = new Set(['subs', 'attrs', 'points', 'samples', 'options', 'shots']);

/** 数组字段的行文本：attrs 是 [标签,值] 拼接，subs 是 [文字,sid] 取文字，其余为纯字符串 */
function rowText(key: string, x: any): string {
  if (Array.isArray(x)) {
    if (key === 'attrs' && x.length >= 2) return `${x[0]}：${x[1]}`;
    return String(x[0] ?? '');
  }
  return String(x ?? '');
}

const arrayTexts = (key: string, v: any) => (Array.isArray(v) ? v : []).map((x: any) => rowText(key, x).trim()).filter(Boolean);

/** 一个字段的结构化改动净值（完整不截断；面板靠 CSS 省略、hover 看全） */
interface FieldChange {
  f: string; // 字段 key
  zh: string; // 中文名
  arr?: boolean; // 数组字段
  from?: string; // 标量最初值（空 = 新填入）
  to?: string; // 标量当前值（空 = 已清空）
  add?: string[]; // 数组新增行（净值：撤回的已剔除）
  rem?: string[]; // 数组删除行（净值：加回来的已剔除）
  c0?: number; // 数组最初条数
  c1?: number; // 数组当前条数
}

/** 从结构化净值派人/agent 可读的文本行（面板渲染与 humanEdit 上报共用此函数——同一份文本） */
function fieldChangeText(fc: FieldChange): string {
  if (fc.arr) {
    const parts: string[] = [];
    if (fc.add?.length) parts.push(`+${fc.add.map(t => `「${t}」`).join('')}`);
    if (fc.rem?.length) parts.push(`-${fc.rem.map(t => `「${t}」`).join('')}`);
    if (!parts.length) return `${fc.zh} ${fc.c0}→${fc.c1}项`;
    return `${fc.zh} ${parts.join(' ')}`;
  }
  if (!fc.from) return `${fc.zh} 填入「${fc.to}」`;
  if (!fc.to) return `${fc.zh} 清空（原「${fc.from}」）`;
  return `${fc.zh} 「${fc.from}」→「${fc.to}」`;
}

/** 两版节点 data 的字段级 diff：逐字段返回结构化净值（不截断） */
function diffFields(oldData: any, newData: any): FieldChange[] {
  const out: FieldChange[] = [];
  const keys = new Set([...Object.keys(oldData || {}), ...Object.keys(newData || {})]);
  for (const k of keys) {
    if (FIELD_SKIP.has(k)) continue;
    const o = oldData?.[k];
    const n = newData?.[k];
    if (JSON.stringify(o) === JSON.stringify(n)) continue;
    const zh = FIELD_ZH[k] || k;
    if (ARRAY_FIELDS.has(k)) {
      const oldT = arrayTexts(k, o);
      const newT = arrayTexts(k, n);
      const oldSet = new Set(oldT);
      const newSet = new Set(newT);
      out.push({
        f: k,
        zh,
        arr: true,
        add: newT.filter(t => !oldSet.has(t)),
        rem: oldT.filter(t => !newSet.has(t)),
        c0: oldT.length,
        c1: newT.length
      });
    } else {
      const os = o === undefined || o === null ? '' : String(o);
      const ns = n === undefined || n === null ? '' : String(n);
      out.push({f: k, zh, from: os, to: ns});
    }
  }
  return out;
}

/** 两轮字段净值按字段归并成最终净值：标量保留最早 from、更新 to；数组按行文本对消（删了又加回 / 加了又删 = 没变）；净值归零的字段整条剔除 */
function mergeFieldChanges(base: FieldChange[], inc: FieldChange[]): FieldChange[] {
  const clone = (f: FieldChange): FieldChange => ({...f, add: f.add && [...f.add], rem: f.rem && [...f.rem]});
  const map = new Map<string, FieldChange>();
  for (const f of base) map.set(f.f, clone(f));
  for (const f of inc) {
    const ex = map.get(f.f);
    if (!ex) {
      map.set(f.f, clone(f));
      continue;
    }
    if (f.arr) {
      const added = new Set(ex.add || []);
      const removed = new Set(ex.rem || []);
      for (const t of f.add || []) {
        if (removed.has(t)) removed.delete(t); // 又加回来了：抵消
        else added.add(t);
      }
      for (const t of f.rem || []) {
        if (added.has(t)) added.delete(t); // 又删掉了：抵消
        else removed.add(t);
      }
      ex.add = [...added];
      ex.rem = [...removed];
      ex.c1 = f.c1; // c0 保留最初条数
    } else {
      ex.to = f.to; // from 保留最初值
    }
  }
  return [...map.values()].filter(f => (f.arr ? (f.add?.length || 0) + (f.rem?.length || 0) > 0 || f.c0 !== f.c1 : f.from !== f.to));
}

/** 节点内容速览（新增 / 删除时让人与 agent 一眼看到这张卡装了什么；完整不截断） */
function nodeBrief(n: {type?: string; data?: any}): string {
  const t = String(n.type || 'textNode');
  const d = n.data || {};
  const bt = brandNodeDefs.find(x => x.nodeType === t);
  if (bt) return String(bt.briefOf?.(d) || '');
  if (t === 'textNode') return String(d.text || '');
  if (t === 'taskNode') {
    const subs = arrayTexts('subs', d.subs);
    return subs.length ? `${subs.length}项任务：${subs.join('、')}` : '';
  }
  if (t === 'dataNode') {
    const head = [d.metric, d.unit].filter(Boolean).join(' ');
    const attrCount = Array.isArray(d.attrs) ? d.attrs.length : 0;
    return [head, attrCount ? `${attrCount}项属性` : ''].filter(Boolean).join(' · ');
  }
  if (t === 'conclusionNode') return String(d.claim || '');
  if (t === 'reviewNode') return d.question ? `提问「${String(d.question)}」` : '';
  return '';
}

interface DiffEntry {
  id: string;
  type?: string;
  label: string;
  /** 新增 / 删除节点的内容速览（全文） */
  brief?: string;
  /** 编辑节点的结构化字段净值（渲染 / 上报时才派生成文本行） */
  fields?: FieldChange[];
  /** 服务端播种带来的旧改动文本行（上个会话的净值，无法结构化合并，原样展示与上报） */
  seeded?: string[];
}
interface DiffEdgeRef {
  id: string;
  label: string;
}
interface RichDiff {
  added: DiffEntry[];
  edited: DiffEntry[];
  removed: DiffEntry[];
  edgesAdded: DiffEdgeRef[];
  edgesRemoved: DiffEdgeRef[];
  /** 工作流标题改动 */
  title?: {from: string; to: string};
}

/** 计算人本次相对基线的详尽改动简报（字段级旧→新 + 连线两端标签 + 标题）；无改动返回 null */
function diffBaseline(): RichDiff | null {
  const oldMap = new Map(baselineNodes.map(n => [n.id, n]));
  // 当前节点按 id 索引（节点多时逐基线 find 是 O(n²)，保存随板子变大越来越卡）
  const curById = new Map(nodes.value.map(n => [String(n.id), n]));
  const curLabel = new Map(nodes.value.map(n => [String(n.id), nodeLabel(n)]));
  const labelOf = (id: string) => curLabel.get(id) || oldMap.get(id)?.label || `节点${id}`;
  const baseData = (id: string): any => {
    const key = oldMap.get(id)?.dataKey;
    if (!key) return null;
    try {
      return JSON.parse(key);
    } catch {
      return null;
    }
  };
  // 任务行锚点文字（sourceHandle sid → 行文字）：先查当前卡，删过的卡查基线 data
  const subTextOf = (nodeId: string, sid: string): string => {
    const subs = curById.get(nodeId)?.data?.subs ?? baseData(nodeId)?.subs;
    if (!Array.isArray(subs)) return '';
    const hit = subs.find((s: any) => Array.isArray(s) && String(s[1]) === sid);
    return hit ? String(hit[0] ?? '') : '';
  };
  /** 连线可读标签：源卡 → 目标卡；任务行级出线标到具体那行（行文字完整不截断） */
  const edgeLabelOf = (e: {source: string; target: string; sourceHandle?: string}) => {
    const src = labelOf(e.source);
    const row = e.sourceHandle ? subTextOf(e.source, e.sourceHandle) : '';
    return row ? `${src} · 「${row}」→ ${labelOf(e.target)}` : `${src} → ${labelOf(e.target)}`;
  };

  const added: DiffEntry[] = [];
  const edited: DiffEntry[] = [];
  const removed: DiffEntry[] = [];
  for (const n of nodes.value) {
    const id = String(n.id);
    if (!oldMap.has(id)) added.push({id, type: String(n.type || 'textNode'), label: nodeLabel(n), brief: nodeBrief(n) || undefined});
  }
  for (const b of baselineNodes) {
    const cur = curById.get(b.id);
    if (!cur) {
      removed.push({id: b.id, type: b.type, label: b.label, brief: nodeBrief({type: b.type, data: baseData(b.id)}) || undefined});
    } else if (JSON.stringify(cur.data || {}) !== b.dataKey) {
      const fields = diffFields(baseData(b.id) || {}, cur.data || {});
      // JSON key 顺序差异会过 stringify 但无实际字段变化 → fields 为空，不算编辑
      if (fields.length) edited.push({id: b.id, type: String(cur.type || 'textNode'), label: curLabel.get(b.id) || b.label, fields});
    }
  }
  const curEdges = edges.value.map(e => ({
    id: String(e.id),
    source: String(e.source),
    target: String(e.target),
    sourceHandle: e.sourceHandle ? String(e.sourceHandle) : ''
  }));
  const oldEdgeSet = new Set(baselineEdges.map(e => e.id));
  const curEdgeSet = new Set(curEdges.map(e => e.id));
  const edgesAdded = curEdges.filter(e => !oldEdgeSet.has(e.id)).map(e => ({id: e.id, label: edgeLabelOf(e)}));
  const edgesRemoved = baselineEdges.filter(e => !curEdgeSet.has(e.id)).map(e => ({id: e.id, label: edgeLabelOf(e)}));
  const titleDiff = baselineTitle !== title.value ? {from: baselineTitle, to: title.value} : undefined;
  if (!added.length && !removed.length && !edited.length && !edgesAdded.length && !edgesRemoved.length && !titleDiff) return null;
  return {added, edited, removed, edgesAdded, edgesRemoved, title: titleDiff};
}

// ── 人机协作回显：人的待办清单 ────────────────────────────────────────────
// pendingDiff = 累积的未回应改动集（RichDiff），是**唯一数据源**：
// 保存时原样作为 humanEdit 上报给 agent，面板也从它派生渲染——页面上看到的与交给 agent 的是同一份完整文本。
// 累积语义直到 Agent 更新板子（editor='agent'）视为回应后清空。
const pendingDiff = ref<RichDiff | null>(null);
const echoHidden = ref(false); // 手动收起过：不再自动冒出，直到出现新改动
// waiting = 有改动待 Agent 回应；responded = Agent 已更新板子（短驻提示后自动收起）
const echoState = ref<'idle' | 'waiting' | 'responded'>('idle');
let echoHideTimer: ReturnType<typeof setTimeout> | null = null;

/** 面板内截断行的 hover 完整信息浮层：深板岩玻璃 + 等宽字，与明细行同气质 */
const echoTipStyle = {
  maxWidth: '340px',
  maxHeight: '280px',
  overflowY: 'auto',
  padding: '8px 11px',
  background: 'rgba(15, 23, 42, 0.94)',
  color: '#e2e8f0',
  border: '1px solid rgba(56, 189, 248, 0.18)',
  borderRadius: '9px',
  boxShadow: '0 10px 28px rgba(15, 23, 42, 0.3)',
  fontFamily: "'JetBrains Mono', 'Plus Jakarta Sans', monospace",
  fontSize: '11.5px',
  lineHeight: '1.65'
} as const;

function hasDiffContent(d: RichDiff | null): boolean {
  return !!d && !!(d.added.length || d.edited.length || d.removed.length || d.edgesAdded.length || d.edgesRemoved.length || d.title);
}

function refreshEchoState() {
  if (hasDiffContent(pendingDiff.value)) {
    if (echoHideTimer) {
      clearTimeout(echoHideTimer);
      echoHideTimer = null;
    }
    echoHidden.value = false; // 新改动突破手动收起
    echoState.value = 'waiting';
  } else if (echoState.value !== 'responded') {
    echoState.value = 'idle';
  }
}

/** 面板整体可见：有选中节点（实时块）或有待回应的改动/已回应提示（改动块） */
const echoPanelVisible = computed(() => selectedNodeIds.value.length > 0 || (echoState.value !== 'idle' && !echoHidden.value));

/** 明细行合并去重（仅去重不封顶——页面展示与给 agent 的信息量一致） */
function unionLines(a: string[], b: string[]): string[] {
  const out = [...a];
  const set = new Set(a);
  for (const x of b) {
    if (!set.has(x)) {
      out.push(x);
      set.add(x);
    }
  }
  return out;
}

/** 一个条目的全部明细行：速览 + 播种旧文本 + 字段净值派生文本（与 exportHumanEdit 同一派生路径） */
const entryDetail = (e: DiffEntry): string[] => [...(e.brief ? [e.brief] : []), ...(e.seeded || []), ...(e.fields || []).map(fieldChangeText)];

/** 从累积改动集派生上报载荷：字段净值 → changes 文本行（与面板渲染共用 fieldChangeText——页面看到的与 agent 拿到的一字不差） */
function exportHumanEdit(d: RichDiff) {
  return {
    added: d.added.map(n => ({id: n.id, type: n.type, label: n.label, brief: n.brief})),
    edited: d.edited.map(n => ({id: n.id, label: n.label, changes: [...(n.seeded || []), ...(n.fields || []).map(fieldChangeText)]})),
    removed: d.removed.map(n => ({id: n.id, label: n.label, brief: n.brief})),
    edgesAdded: d.edgesAdded,
    edgesRemoved: d.edgesRemoved,
    title: d.title
  };
}

/** 把一次保存的增量 diff 合并进累积改动集（含抵消：加了又删 = 没加；新增后再编辑仍算新增并追加字段变更；连上的线又删 = 没连；标题保留最早 from） */
function mergeIntoPending(diff: RichDiff) {
  const cur: RichDiff = pendingDiff.value
    ? JSON.parse(JSON.stringify(pendingDiff.value))
    : {added: [], edited: [], removed: [], edgesAdded: [], edgesRemoved: []};
  const added = new Map(cur.added.map(n => [n.id, n]));
  const edited = new Map(cur.edited.map(n => [n.id, n]));
  const removed = new Map(cur.removed.map(n => [n.id, n]));
  for (const n of diff.added) {
    removed.delete(n.id);
    edited.delete(n.id);
    added.set(n.id, n);
  }
  for (const n of diff.edited) {
    const inAdded = added.get(n.id);
    if (inAdded) {
      // 新增后又编辑：卡本身是新的，内部演变没有意义——刷新内容速览（brief 即最终内容），不保留字段历史
      inAdded.label = n.label;
      const curNode = nodes.value.find(x => String(x.id) === n.id);
      if (curNode) inAdded.brief = nodeBrief(curNode) || inAdded.brief;
    } else {
      removed.delete(n.id);
      const ex = edited.get(n.id);
      if (ex) {
        ex.label = n.label;
        // 同一字段的多轮改动归并成净值（s→s6→s6 其他 最终只剩「s」→「s6 其他」一行）
        ex.fields = mergeFieldChanges(ex.fields || [], n.fields || []);
      } else edited.set(n.id, n);
    }
  }
  for (const n of diff.removed) {
    if (added.delete(n.id)) continue; // 与之前的「新增」抵消
    edited.delete(n.id);
    removed.set(n.id, n);
  }
  cur.added = [...added.values()];
  cur.edited = [...edited.values()];
  cur.removed = [...removed.values()];
  // 连线按 id 同样抵消
  const eAdded = new Map(cur.edgesAdded.map(e => [e.id, e]));
  const eRemoved = new Map(cur.edgesRemoved.map(e => [e.id, e]));
  for (const e of diff.edgesAdded) {
    eRemoved.delete(e.id);
    if (!eAdded.has(e.id)) eAdded.set(e.id, e);
  }
  for (const e of diff.edgesRemoved) {
    eAdded.delete(e.id);
    if (!eRemoved.has(e.id)) eRemoved.set(e.id, e);
  }
  cur.edgesAdded = [...eAdded.values()];
  cur.edgesRemoved = [...eRemoved.values()];
  // 标题：保留最早 from、更新 to；改回原值 = 没改
  if (diff.title) {
    const from = cur.title?.from ?? diff.title.from;
    const to = diff.title.to;
    cur.title = from === to ? undefined : {from, to};
  }
  pendingDiff.value = hasDiffContent(cur) ? cur : null;
  refreshEchoState();
}

/** Agent 已改板 → 待办消解，短驻「已更新」提示后自动收起 */
function onAgentResponded() {
  if (echoHideTimer) clearTimeout(echoHideTimer);
  pendingDiff.value = null;
  echoHidden.value = false;
  echoState.value = 'responded';
  echoHideTimer = setTimeout(() => {
    echoState.value = 'idle';
    echoHideTimer = null;
  }, 4000);
}

/** 手动收起：只藏起展示（服务端 human_edit 不动，agent 读板仍可见）；下次出现新改动会重新冒出 */
function dismissEcho() {
  if (echoHideTimer) clearTimeout(echoHideTimer);
  echoHideTimer = null;
  echoHidden.value = true;
  echoState.value = 'idle';
}

/** 打开板子时，若上次写入者是人且 Agent 还没回应 → 用服务端存的改动集播种（详尽格式直接还原；旧 id 数组格式降级查名） */
function seedPendingFromServer(he: any) {
  const labelById = new Map(nodes.value.map(n => [String(n.id), nodeLabel(n)]));
  const normItem = (x: any): DiffEntry => {
    // 旧格式是纯 id 字符串，降级：名字从当前板上查（删掉的卡只能显示「节点」）
    if (typeof x === 'string') return {id: x, label: labelById.get(x) || '节点'};
    const id = String(x?.id ?? '');
    return {
      id,
      type: x?.type ? String(x.type) : undefined,
      label: x?.label || labelById.get(id) || '节点',
      brief: x?.brief ? String(x.brief) : undefined,
      // 服务端存的是派生好的文本行（上个会话的净值）：无法还原结构，原样展示与再上报
      seeded: Array.isArray(x?.changes) ? (x.changes as any[]).map(String) : undefined
    };
  };
  const normEdge = (x: any): DiffEdgeRef => (typeof x === 'string' ? {id: x, label: '连线'} : {id: String(x?.id ?? ''), label: x?.label || '连线'});
  const d: RichDiff = {
    added: (he.added || []).map(normItem),
    edited: (he.edited || []).map(normItem),
    removed: (he.removed || []).map(normItem),
    // 旧格式 edgesAdded/edgesRemoved 是计数（数字），无法还原端点，丢弃
    edgesAdded: Array.isArray(he.edgesAdded) ? he.edgesAdded.map(normEdge) : [],
    edgesRemoved: Array.isArray(he.edgesRemoved) ? he.edgesRemoved.map(normEdge) : [],
    title: he.title && he.title.from !== he.title.to ? {from: String(he.title.from ?? ''), to: String(he.title.to ?? '')} : undefined
  };
  pendingDiff.value = hasDiffContent(d) ? d : null;
  refreshEchoState();
}

/** 回显状态机（每次应用服务端数据后调）：editor='agent' 且有挂起改动集 → 消解（即使面板被手动收起也要清数据，否则下次保存会把旧待办混报给 agent）；首载 editor='human' 带简报 → 播种（手动收起过则不打扰） */
function applyEchoSignal(data: WorkflowData) {
  if (data.editor === 'agent') {
    if (pendingDiff.value || echoState.value === 'waiting') onAgentResponded();
    return;
  }
  if (data.editor === 'human' && data.humanEdit && echoState.value === 'idle' && !echoHidden.value) seedPendingFromServer(data.humanEdit);
}

/** 面板清单：从 pendingDiff 派生——同标签聚合计数、明细合并，按 新增 → 编辑 → 删除 排序 */
const OP_ORDER: Record<string, number> = {add: 0, edit: 1, remove: 2};
const pendingDisplay = computed(() => {
  const d = pendingDiff.value;
  if (!d) return [];
  const groups = new Map<string, {op: 'add' | 'edit' | 'remove'; label: string; count: number; detail: string[]}>();
  const push = (op: 'add' | 'edit' | 'remove', label: string, detail: string[]) => {
    const key = `${op}|${label}`;
    const g = groups.get(key);
    if (g) {
      g.count += 1;
      g.detail = unionLines(g.detail, detail);
    } else groups.set(key, {op, label, count: 1, detail: [...detail]});
  };
  for (const n of d.added) push('add', n.label, entryDetail(n));
  for (const n of d.edited) push('edit', n.label, entryDetail(n));
  for (const n of d.removed) push('remove', n.label, entryDetail(n));
  if (d.title) push('edit', '流程编排标题', [`「${d.title.from}」→「${d.title.to}」`]);
  return [...groups.values()].sort((a, b) => OP_ORDER[a.op] - OP_ORDER[b.op]);
});

// ── 节点就地编辑 / 上传接口（注入给自定义节点组件） ─────────────────────
let lastEditPush = 0;
function beginEdit() {
  if (editLocked.value) return; // 响应期锁编辑（兜底：任何入口漏守也进不了历史栈）
  // 连续输入只在开始时入一次历史栈（2s 内视为同一次编辑）
  const now = Date.now();
  if (now - lastEditPush > 2000) {
    pushHistory();
    lastEditPush = now;
  }
}
function onNodeDataUpdate(id: string, patch: Record<string, any>) {
  if (editLocked.value) return; // 响应期锁编辑：本地数据写入的兜底闸（applyData 整包替换不走这里，agent 改板不受影响）
  const n = nodes.value.find(x => x.id === id);
  if (!n) return;
  n.data = {...n.data, ...patch};
  markNodeHuman(id);
  // 行清单被重写（行级编辑删行等）：锚在已消失行（subs 任务 / shots 分镜）上的连线一并清掉，下一次保存落库
  for (const field of ['subs', 'shots'] as const) {
    const rows = patch[field];
    if (Array.isArray(rows) && edges.value.some(e => e.source === id && e.sourceHandle)) {
      const alive = new Set(rows.filter((s: any) => Array.isArray(s) && s[1]).map((s: any) => String(s[1])));
      const kept = edges.value.filter(e => !(e.source === id && e.sourceHandle && !alive.has(String(e.sourceHandle))));
      if (kept.length !== edges.value.length) {
        clearMarksFor(undefined, edges.value.filter(e => !kept.includes(e)).map(e => e.id));
        edges.value = kept;
      }
    }
  }
  scheduleSave();
}
// 程序化进入编辑（新建文本节点后自动开打）
const editSignal = ref<{id: string; tick: number} | null>(null);
let editTick = 0;
function triggerEdit(id: string) {
  editSignal.value = {id, tick: ++editTick};
}
// 文件节点内上传：uploadingNodeId 驱动节点内「上传中」态
const uploadingNodeId = ref<string | null>(null);
let uploadTargetNodeId: string | null = null;
function requestNodeUpload(nodeId: string) {
  if (editLocked.value) return;
  uploadTargetNodeId = nodeId;
  fileInputEl.value?.click();
}
async function uploadOneToNode(nodeId: string, file: File) {
  if (editLocked.value) return;
  if (!workflowKey.value) return;
  uploadingNodeId.value = nodeId;
  try {
    const sk = qaRef.value?.currentSessionKey || workflowKey.value || 'workflow';
    const res = await fetchUploadFile(file, sk);
    const uid = authStore.userInfo?.userId;
    const uidPart = uid ? `&user_id=${encodeURIComponent(uid)}` : '';
    // url 存后端相对路径，回显时由节点组件拼 baseURL（同 qa-glass 附件回显约定）
    const url = `/ai/agent/uploads/download?path=${encodeURIComponent(res.path)}${uidPart}`;
    onNodeDataUpdate(nodeId, {name: res.filename, url, mime: file.type || '', size: res.size});
  } catch (err) {
    window.$message?.error?.(`上传失败：${(err as Error).message || err}`);
  } finally {
    uploadingNodeId.value = null;
  }
}

// 人工核查作答通知：用户在画布上作答 → 立即落库（防与 Agent 写板竞态）→ 对话窗自动发一条消息触发 Agent 响应。
// Agent 按协作规则的收口协议处理：推进分支后要么撤卡、要么锁 disabled（前端据此渲染只读锁定态）
async function notifyReviewAnswered(nodeId: string, question: string, answer: string) {
  if (!workflowKey.value) return;
  // 1) 冲刷保存：答案必须先落库再让 Agent 读板，否则 Agent 写板与本地延迟保存会互相覆盖
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
  await doSave();
  // 2) 确保对话窗打开并等待 QAGlass 就绪（首次可能还在 Suspense 挂载中）
  emit('request-chat'); // 请宿主确保对话在场（页面宿主=打开悬浮窗；QA 伴侣面板宿主=对话本就在旁）
  const start = Date.now();
  while (typeof qaRef.value?.sendMessage !== 'function' && Date.now() - start < 3000) {
    await new Promise(r => setTimeout(r, 60));
  }
  if (typeof qaRef.value?.sendMessage !== 'function') return;
  // 3) 程序化发送：只带事实（卡 id / 问题 / 答案）——推进与收口协议后端随板子上下文注入，不在消息里复述
  qaRef.value.sendMessage(`[人工核查已作答] 板上卡 id=${nodeId} 问题「${question}」→ 用户回答：「${answer}」`);
}
// 编辑锁：问答悬浮窗当前会话响应中（agent 可能正在改板）→ 画布全部编辑入口禁用；选中节点保持可用（选中态要随消息告知 agent）
// 注：下方 provide('wfNodeApi') 的对象字面量立即求值会引用它，声明必须先于 provide（仅依赖 qaRef，可安全前置）
const editLocked = computed(() => !!qaRef.value?.running);

provide('wfNodeApi', {
  beginEdit,
  updateData: onNodeDataUpdate,
  editSignal,
  editLocked,
  uploadingNodeId,
  requestNodeUpload,
  uploadOneToNode,
  previewAttachment,
  previewNode: openNodePreview,
  notifyReviewAnswered
});

// ── AI 重整：用户一键 → 代发预设消息让 Agent 语义级梳理整板 → 响应结束后整板自动对齐 ──
// 触发链路照 notifyReviewAnswered：冲刷保存 → 开对话窗 → 等 QAGlass 就绪 → sendMessage（带 [板子重整] marker，
// 处理流程由 workflow-board skill 的「AI 重整」章节定义）；差别是重整收尾时再补一次整板对齐——Agent 的变更不含坐标
let reorgSentKey = '';
let reorgSentVersion = 0;
const reorgPending = ref(false);

async function reorgBoard() {
  if (!workflowKey.value || editLocked.value || !nodes.value.length) return;
  // 1) 冲刷保存：重整前的板子状态必须先落库，防本地延迟保存与 Agent 写板互相覆盖
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
  await doSave();
  // 2) 记下点击时的板与版本：防等待响应期间用户切板整错板；版本用于判断 Agent 是否真动过板
  reorgSentKey = workflowKey.value;
  reorgSentVersion = version.value;
  reorgPending.value = true;
  // 3) 确保对话窗打开并等待 QAGlass 就绪（首次可能还在 Suspense 挂载中）
  emit('request-chat'); // 请宿主确保对话在场（页面宿主=打开悬浮窗；QA 伴侣面板宿主=对话本就在旁）
  const start = Date.now();
  while (typeof qaRef.value?.sendMessage !== 'function' && Date.now() - start < 3000) {
    await new Promise(r => setTimeout(r, 60));
  }
  if (typeof qaRef.value?.sendMessage !== 'function') {
    reorgPending.value = false;
    return;
  }
  // 4) 程序化发送：marker + 最小指令，具体诊断与处理流程随 WORKFLOW_BOARD_RULES 注入并指向 skill
  qaRef.value.sendMessage('[板子重整] 用户点击了「AI 重整」，请通读全板后按 workflow-board skill 的「AI 重整」流程诊断处理，一次 edit_workflow_board 完成。');
}

// 响应结束（Agent 全部工具调用已落库）→ 同步最新板，若 Agent 动过板则整板对齐 + 适应视图，完成「一键」收尾
watch(editLocked, (locked, wasLocked) => {
  if (!wasLocked || locked || !reorgPending.value) return;
  reorgPending.value = false;
  void finishReorgRelayout();
});

async function finishReorgRelayout() {
  if (!workflowKey.value || workflowKey.value !== reorgSentKey) return;
  // 先同步服务端最新板再动（同 relayout 响应期模式），避免拿本地滞后快照覆盖 Agent 改动
  const {data} = await fetchWorkflow(workflowKey.value);
  if (data && data.version !== version.value) applyData(data);
  // 仅当 Agent 确实动过板才对齐；它判定板已整齐没动板时，用户手摆的坐标也不打扰
  if (version.value !== reorgSentVersion) {
    relayout();
    fitAll();
  }
}

// ── 自动布局 ────────────────────────────────────────────────────────────────
// 各类型节点初始尺寸估值：新节点落点居中 / 未测量时的布局兜底（渲染后以 Vue Flow 实测 dimensions 为准）
const NODE_SIZE_HINTS: Record<string, [number, number]> = {
  textNode: [182, 46],
  taskNode: [230, 120],
  dataNode: [250, 170],
  conclusionNode: [280, 140],
  fileNode: [224, 144],
  reviewNode: [240, 150],
  startNode: [86, 38],
  endNode: [80, 38],
  ...Object.fromEntries(brandNodeDefs.map(t => [t.nodeType, t.sizeHint]))
};

/** 节点尺寸：优先 Vue Flow 实测值（dimensions），未测量时按类型估值兜底 */
function getNodeSize(n: any): {w: number; h: number} {
  const d = n.dimensions || n.measured;
  if (d?.width && d?.height) return {w: d.width, h: d.height};
  const [w, h] = NODE_SIZE_HINTS[String(n.type || 'textNode')] || [182, 46];
  return {w, h};
}

// 布局间距：LAYER_GAP = 层与层（横向，沿流向留出连线空间）；SIBLING_GAP = 同列卡片纵向间距
const LAYER_GAP = 96;
const SIBLING_GAP = 48;

function autoLayout(rawNodes: any[], rawEdges: any[]): any[] {
  // 给没有 position 的节点分配坐标：dagre（Sugiyama）分层布局——最长路径分层 + 交叉消减，多个无关联通量自动分离，环由 acyclicer 自动处理
  const free = rawNodes.filter(n => !n.position);
  if (!free.length) return rawNodes;

  const g = new dagre.graphlib.Graph();
  // LR = 每层一列左→右；ranksep = 层间距（沿流向留连线空间），nodesep = 同列卡片纵向间距
  g.setGraph({rankdir: 'LR', ranksep: LAYER_GAP, nodesep: SIBLING_GAP, marginx: 0, marginy: 0});
  g.setDefaultEdgeLabel(() => ({}));
  const freeIds = new Set(free.map(n => String(n.id)));
  for (const n of free) {
    const s = getNodeSize(n);
    g.setNode(String(n.id), {width: s.w, height: s.h});
  }
  for (const e of rawEdges || []) {
    // 参照线是跨分支注解，不参与层级推断；自环不进布局；悬空边（端点不在自由集）跳过，防 dagre 凭空造节点
    if (e.kind === 'ref') continue;
    const s = String(e.source);
    const t = String(e.target);
    if (s !== t && freeIds.has(s) && freeIds.has(t)) g.setEdge(s, t);
  }
  dagre.layout(g);

  // dagre 输出节点中心点、包围盒从原点开始；板上已有用户摆好的卡时，新批次整体平移到包围盒右侧、纵向居中对齐，不覆盖旧卡
  let offX = 0;
  let offY = 0;
  const placed = rawNodes.filter(n => n.position);
  if (placed.length) {
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const n of placed) {
      const s = getNodeSize(n);
      maxX = Math.max(maxX, n.position.x + s.w);
      minY = Math.min(minY, n.position.y);
      maxY = Math.max(maxY, n.position.y + s.h);
    }
    let fMinX = Infinity;
    let fMaxX = -Infinity;
    let fMinY = Infinity;
    let fMaxY = -Infinity;
    for (const n of free) {
      const p = g.node(String(n.id));
      const s = getNodeSize(n);
      fMinX = Math.min(fMinX, (p?.x ?? 0) - s.w / 2);
      fMaxX = Math.max(fMaxX, (p?.x ?? 0) + s.w / 2);
      fMinY = Math.min(fMinY, (p?.y ?? 0) - s.h / 2);
      fMaxY = Math.max(fMaxY, (p?.y ?? 0) + s.h / 2);
    }
    offX = maxX + LAYER_GAP - fMinX;
    offY = (minY + maxY) / 2 - (fMinY + fMaxY) / 2;
  }

  return rawNodes.map(n => {
    if (n.position) return n;
    const p = g.node(String(n.id));
    const s = getNodeSize(n);
    return {
      ...n,
      type: n.type || 'textNode',
      position: {x: Math.round((p?.x ?? 0) - s.w / 2 + offX), y: Math.round((p?.y ?? 0) - s.h / 2 + offY)}
    };
  });
}

// ── 二段式布局：先估值摆位让板子立刻可见，实测尺寸回来后对「估值摆放」的卡重排一次 ──
// Vue Flow 通过 ResizeObserver 异步测量节点（dimensions）。首摆用类型估值，长卡会偏挤；
// 测量完成后只重排 hintPlaced 记录的新卡（用户拖好的坐标绝不动），并落库持久化。
let hintPlaced = new Set<string>();
let relayoutTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleMeasuredRelayout(tries = 8) {
  if (relayoutTimer) clearTimeout(relayoutTimer);
  relayoutTimer = setTimeout(() => {
    if (!hintPlaced.size) return;
    const list = nodes.value;
    const targets = list.filter(n => hintPlaced.has(String(n.id)));
    if (!targets.length) return;
    // 主动强制实测，不干等 ResizeObserver 回调：后台标签页里 RO 不触发，dimensions 会一直是 0；
    // updateNodeInternals 走 offsetWidth/Height 同步测量（全板都测——下方 autoLayout 算旧卡包围盒
    // 时也要真实尺寸，否则新批次会错位压到旧卡上）。NodeWrapper 还没挂载时本次强制无效，落到下方重试
    updateNodeInternals();
    // 还没量完 → 稍等再试
    if (targets.some(n => !n.dimensions?.width)) {
      if (tries > 0) scheduleMeasuredRelayout(tries - 1);
      return;
    }
    const stripIds = hintPlaced;
    hintPlaced = new Set();
    nodes.value = autoLayout(
      list.map(n => (stripIds.has(String(n.id)) ? {...n, position: null} : n)),
      edges.value
    );
    scheduleSave();
    // 不再 fitView：实测重排是后台自动对齐，保留用户当前视口，不强制视图回中
  }, 90);
}

// 任务稳定 id（edge.sourceHandle 的锚点）：agent 写的 subs 不带 id，加载 / 轮询归一化时补齐，随下一次人工保存落库
let subSeq = 0;
function genSubId(): string {
  return `s${Date.now().toString(36).slice(-6)}${(subSeq++).toString(36)}`;
}

/** 行清单（[文字, sid?]）逐行补齐稳定 sid——行级出线的 sourceHandle 锚点；agent 写的纯文字行也在此物化，已有 sid 原样保留 */
function withRowIds(rows: any[]): any[] {
  return rows.map((s: any) => {
    const t = String(Array.isArray(s) ? (s[0] ?? '') : (s ?? '')).trim();
    if (!t) return s;
    const sid = Array.isArray(s) && typeof s[1] === 'string' && s[1] ? s[1] : genSubId();
    return [t, sid];
  });
}

// taskNode / segNode 归一化：行清单（subs / shots）缺 sid 补齐（幂等）
function normalizeNode(n: any) {
  const type = n.type || 'textNode';
  if (type === 'taskNode') {
    const d = n.data || {};
    let data: Record<string, any> = {
      ...d, // 保留 subs 任务清单等既有字段——整体重建 data 会把 subs 吞掉，每次加载/轮询刷新都凭空消失
      title: d.title ?? '',
      summary: d.summary ?? '',
      note: d.note ?? ''
    };
    delete data.status; // 清掉旧数据残留的 status 字段
    if (Array.isArray(data.subs) && data.subs.length) {
      data = {...data, subs: withRowIds(data.subs)};
    }
    return {...n, type, data};
  }
  if (type === 'segNode') {
    // 分镜段卡——shots 与 subs 同构，sid 是分镜级出线锚点
    const d = n.data || {};
    let data: Record<string, any> = {...d, seg: d.seg ?? ''};
    if (Array.isArray(data.shots) && data.shots.length) {
      data = {...data, shots: withRowIds(data.shots)};
    }
    return {...n, type, data};
  }
  return {...n, type};
}

function ensureNodeType(list: any[]): any[] {
  return list.map(n => ({...n, type: n.type || 'textNode'}));
}

// ── 加载 ──────────────────────────────────────────────────────────────────
async function loadWorkflow(wk: string) {
  loading.value = true;
  const {data, error} = await fetchWorkflow(wk);
  if (error || !data) {
    // 板子已不存在（多为空板被自动清理）：本地复位并交由宿主处理（页面回列表 / 面板收起）
    loading.value = false;
    stopPolling();
    leaveFullscreen();
    emit('not-found', wk);
    return;
  }
  // 切换工作流：清空上一个工作流的编辑态
  history.value = [];
  redoStack.value = [];
  ctxMenu.value = null;
  connectMenu.value = null;
  pendingAppChat = null;
  dismissEcho();
  boardType.value = data.boardType === 'html' ? 'html' : 'board';
  if (boardType.value === 'html') {
    // 应用制作：画布是 iframe（HtmlBoard），没有节点/连线数据，只同步标题 / 版本 / 入口就绪态 / 分享态
    title.value = data.title;
    baselineTitle = data.title;
    version.value = data.version;
    entryReady.value = !!data.entryReady;
    shareOn.value = !!data.shareOn;
    sharePublic.value = !!data.sharePublic;
    nodes.value = [];
    edges.value = [];
  } else {
    entryReady.value = false;
    shareOn.value = false;
    sharePublic.value = false;
    applyData(data, {fit: true});
  }
  loading.value = false;
  emit('request-chat');
}

function applyData(data: WorkflowData, opts: {fit?: boolean} = {}) {
  title.value = data.title;
  version.value = data.version;
  // 徽标先于连线装饰落位：decorateEdge 按 edges 徽标派生高亮样式
  marks.value = normalizeMarks(data.marks);
  const rawNodes = (data.nodes || []).map(normalizeNode);
  // 任务级连线（带 sourceHandle）：锚的那条任务已不在源卡 subs 里（被删 / 被换）→ 悬空丢弃，别留看不见的幽灵线
  const subIdsByNode = new Map<string, Set<string>>();
  for (const n of rawNodes) {
    if (n.type === 'taskNode' && Array.isArray(n.data?.subs)) {
      subIdsByNode.set(
        String(n.id),
        new Set(n.data.subs.filter((s: any) => Array.isArray(s) && s[1]).map((s: any) => String(s[1])))
      );
    }
  }
  edges.value = (data.edges || [])
    .filter((e: any) => !e.sourceHandle || subIdsByNode.get(String(e.source))?.has(String(e.sourceHandle)))
    .map(decorateEdge);
  nodes.value = autoLayout(rawNodes, data.edges || []);
  // 无坐标的新卡先用估值尺寸摆位；记下它们，等 Vue Flow 实测尺寸回来后按真实大小重排一次
  hintPlaced = new Set(rawNodes.filter(n => !n.position).map(n => String(n.id)));
  if (hintPlaced.size) scheduleMeasuredRelayout();
  takeBaseline();
  // 回显状态机：Agent 改过板 → 消解待办；首载时人的改动还没被 Agent 回应 → 播种待办
  applyEchoSignal(data);
  // 仅显式打开板子（首载 / 切换 / 新建）时适配视图；轮询刷新等自动场景保留用户当前视口
  if (opts.fit) nextTick(() => fitView({padding: 0.2, duration: 300}));
}

/** 空板引导条示例（板已建好，只欠第一句话）：流程编排 = 拆任务落板；应用制作 = 描述要开发的应用 */
const BOARD_EXAMPLES: string[] = ['帮我梳理 GB 4943.1 新旧版本的技术差异清单', '帮我整理电动自行车相关的现行有效国标清单，并核对每份的实施状态'];
const HTML_EXAMPLES: string[] = ['帮我制作一份电动自行车标准族谱 · 交互式研究报告', '类 PPT 演示页。完整，全面的介绍下你自己'];

/** 等 QAGlass 挂载就绪（首次打开可能还在 Suspense 挂载中），与 notifyReviewAnswered 同款轮询 */
async function waitQaReady(): Promise<boolean> {
  const start = Date.now();
  while (typeof qaRef.value?.sendMessage !== 'function' && Date.now() - start < 3000) {
    await new Promise(r => setTimeout(r, 60));
  }
  return typeof qaRef.value?.sendMessage === 'function';
}

/** 空板引导条示例：板已在，只差把话说给 Agent */
async function sendExample(prompt: string) {
  if (!workflowKey.value || editLocked.value) return;
  if (await waitQaReady()) qaRef.value.sendMessage(prompt);
}

/** 应用页内 AppBridge.sendToChat 呼叫 agent（反向通道）：把页内文本作为一条用户消息触发回合，
 *  完成后经 respond 回执给页内（页内据此提示 / 退避）。照 notifyReviewAnswered 先例：
 *  开对话窗 → 等就绪 → sendMessage。
 *  忙时口径（对局场景核心）：agent 回合进行中（刚应完一手还在输出）用户又落子，旧口径
 *  「拒绝 busy」会丢掉这一手——回合结束后没人补发，agent 永远不响应。改为排队：只保留
 *  最新一条（对局呼叫幂等，旧的被覆盖无害），editLocked 落回 false 时自动补发；
 *  排队即向页内回执 ok（别让页面误报失败或盲目重试）。 */
let pendingAppChat: {text: string; wk: string} | null = null;

watch(editLocked, (locked, wasLocked) => {
  if (locked || !wasLocked || !pendingAppChat) return;
  const p = pendingAppChat;
  pendingAppChat = null;
  if (p.wk !== workflowKey.value) return; // 已切板：作废
  void deliverAppChat(p.text);
});

/** 实际投递：开对话窗 → 等 QAGlass 就绪 → sendMessage。失败（就绪超时）返回 false。
 *  注意不做 doSave 冲刷——html 板没有本地节点/连线编辑态，此前无条件 doSave 会 PUT 相同
 *  title 把 version bump 起来 → 轮询命中 → iframe 假重载 /「新版本已就绪」条莫名冒出。 */
async function deliverAppChat(text: string): Promise<boolean> {
  emit('request-chat');
  const ok = await waitQaReady();
  if (!ok) return false;
  qaRef.value?.sendMessage(text);
  return true;
}

async function onAppChat(p: {text: string; respond: (ok: boolean, reason?: string) => void}) {
  const {text, respond} = p;
  if (!workflowKey.value || boardType.value !== 'html') {
    respond(false, 'no-board');
    return;
  }
  if (editLocked.value) {
    pendingAppChat = {text, wk: workflowKey.value};
    respond(true);
    return;
  }
  respond(await deliverAppChat(text) ? true : false, 'no-channel');
}

// ── 保存 ──────────────────────────────────────────────────────────────────
function scheduleSave() {
  lastLocalEdit = Date.now();
  saveState.value = 'saving';
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(doSave, 900);
}

async function doSave() {
  if (!workflowKey.value) return;
  // 应用制作：数据在任务目录文件里（agent 维护），人端只有标题可编辑——只发 {title}，
  // 绝不带 nodes/edges（空数组会 bump version 并把 editor 打成 human，污染发布信号）；
  // 标题没变就整体 no-op 不 PUT（后端已加同值不 bump 的双保险，这里再省一次请求）
  if (boardType.value === 'html') {
    if (title.value === baselineTitle) return;
    await fetchUpdateWorkflow(workflowKey.value, {title: title.value});
    baselineTitle = title.value; // 标题已落库：更新基线，轮询跟随 agent 改名才有正确参照
    saveState.value = 'saved';
    setTimeout(() => {
      if (saveState.value === 'saved') saveState.value = 'idle';
    }, 2000);
    return;
  }
  // 保存时去掉 style / type / animated 渲染派生字段（DB 只存 id/source/target/sourceHandle/kind 结构字段，样式加载时按 kind 派生）
  const cleanEdges = edges.value.map(({style: _style, type: _type, animated: _animated, ...rest}: any) => rest);
  // 人本次相对上次保存的增量改动：先并入累积改动集 pendingDiff——它是唯一数据源，
  // 字段级改动在其中按净值归并（逐字输入的连环保存不留中间态）；上报时从它派生 humanEdit，
  // 面板也照它渲染：页面看到的与 agent 拿到的是同一份完整文本
  const diff = diffBaseline();
  if (diff) mergeIntoPending(diff);
  // PUT 响应即含最新 version，不再为它多打一次全量读接口（板子大时每次保存省一整板 JSON 往返）
  const {data: saved} = await fetchUpdateWorkflow(workflowKey.value, {
    title: title.value,
    nodes: nodes.value,
    edges: cleanEdges,
    humanEdit: pendingDiff.value ? exportHumanEdit(pendingDiff.value) : undefined,
    // 徽标原样回传（含本地新打的 human / 新连线标）；agent 端写入会全量重建覆盖
    marks: marks.value
  });
  takeBaseline();
  if (saved) version.value = saved.version;
  saveState.value = 'saved';
  setTimeout(() => {
    if (saveState.value === 'saved') saveState.value = 'idle';
  }, 2000);
}

// ── 轮询 Agent 修改 ──────────────────────────────────────────────────────
/** 应用制作轮询 / 重查共用：跟随 entryReady / version / 标题。
 * 标题实时跟随 agent 改名（如按「板名同步」规则把默认名改成应用名），免刷新页面；
 * 人有未保存的标题改动时不覆盖（title 与 baselineTitle 不等 = 人正在改，以人的输入为准） */
function syncHtmlFromServer(data: any) {
  entryReady.value = !!data.entryReady;
  if (data.version === version.value) return;
  version.value = data.version;
  if (data.title && title.value === baselineTitle && data.title !== title.value) {
    title.value = data.title;
    baselineTitle = data.title;
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    if (!workflowKey.value) return;
    // 最近 1.5s 有本地编辑则跳过，避免覆盖拖拽
    if (Date.now() - lastLocalEdit < 1500) return;
    // 轮询只拿轻量 meta（不含 nodes/edges）：板子大了之后每 3.5s 全量读一次是持续重负载，
    // 只有 version 变了（Agent 改过板）才值得拉全量
    const {data} = await fetchWorkflowMeta(workflowKey.value);
    if (!data) return;
    if (boardType.value === 'html') {
      // 应用制作：跟随 entryReady / version（publish 信号 → HtmlBoard 重载 iframe）与标题，不跑 VueFlow 布局
      syncHtmlFromServer(data);
      return;
    }
    if (data.version !== version.value) {
      const full = await fetchWorkflow(workflowKey.value);
      if (full.data) applyData(full.data);
    }
  }, 3500);
}

/** 应用制作占位态「再看看」：重拉工作流，刷新 entryReady / version / 标题 */
async function recheckHtmlTask() {
  if (!workflowKey.value) return;
  const {data} = await fetchWorkflow(workflowKey.value);
  if (!data) return;
  syncHtmlFromServer(data);
}

/** 空任务判定（返回 / 卸载时的即时清理用）：board 型看节点数；html 型看是否发布过 index.html（目录里仅中间文件不算交付） */
function isCurrentTaskEmpty() {
  return boardType.value === 'html' ? !entryReady.value : !nodes.value.length;
}

// ── Vue Flow 事件 ─────────────────────────────────────────────────────────
const SAVE_SKIP = new Set(['select', 'reset', 'dimensions']);
function onNodesChange(changes: any[]) {
  if (Array.isArray(changes) && changes.some(c => !SAVE_SKIP.has(c?.type))) scheduleSave();
}
function onEdgesChange(changes: any[]) {
  if (Array.isArray(changes) && changes.some(c => c?.type !== 'select')) scheduleSave();
}
function onConnect(params: any) {
  if (editLocked.value) return;
  pushHistory();
  addStyledEdge(String(params.source), String(params.target), params.sourceHandle ? String(params.sourceHandle) : undefined);
  scheduleSave();
}

// 「＋」靠近浮现：指针进入节点外扩区域（比节点体 hover 更宽松）就给该节点打 wf-near，CSS 据此显示 ＋
const NEAR_PAD = 30;
let nearRaf = 0;
let nearLast: {x: number; y: number} | null = null;
let nearLastRun = 0;
// 指针按住（拖卡 / 平移 / 拖线）期间计数：就近检测每帧对全部节点 getBoundingClientRect，
// 拖拽时 DOM 脏、强制回流 = layout thrash，是拖动掉帧的真凶之一——按住期间完全停掉
let canvasPresses = 0;
function onCanvasPressStart() {
  canvasPresses++;
  clearNearNodes();
}
function onCanvasPressEnd() {
  canvasPresses = Math.max(0, canvasPresses - 1);
}
function updateNearNodes() {
  nearRaf = 0;
  const pt = nearLast;
  const scope = canvasEl.value;
  if (!pt || !scope || canvasPresses) return;
  scope.querySelectorAll<HTMLElement>('.vue-flow__node').forEach(w => {
    const r = w.getBoundingClientRect();
    const near = pt.x >= r.left - NEAR_PAD && pt.x <= r.right + NEAR_PAD && pt.y >= r.top - NEAR_PAD && pt.y <= r.bottom + NEAR_PAD;
    w.classList.toggle('wf-near', near);
  });
}
function onCanvasMouseMove(e: MouseEvent) {
  if (canvasPresses) return; // 拖拽中不做就近检测（见 canvasPresses 注释）
  // 悬停也限流 ~12Hz：就近浮现是装饰，不需要每帧精度，节点多时省每帧全量 rect 读取
  const now = performance.now();
  if (now - nearLastRun < 80) return;
  nearLastRun = now;
  nearLast = {x: e.clientX, y: e.clientY};
  if (!nearRaf) nearRaf = requestAnimationFrame(updateNearNodes);
}
function clearNearNodes() {
  nearLast = null;
  if (nearRaf) {
    cancelAnimationFrame(nearRaf);
    nearRaf = 0;
  }
  document.querySelectorAll('.vue-flow__node.wf-near').forEach(w => w.classList.remove('wf-near'));
}

// 从 handle 拖出连线：Vue Flow 1.48 的 @connect-start 载荷是单个对象 {event, nodeId, handleId, handleType}
// handleId = 子任务级出线时的行 sid（卡级默认 handle 无 id）
let connectStartInfo: {nodeId: string; handleType: string; handleId: string; x: number; y: number} | null = null;
function onConnectStart(params: any) {
  if (editLocked.value) return; // 锁定时不记录起点 → onConnectEnd 不会弹「拖线加节点」菜单
  const ev = params?.event;
  connectStartInfo = {
    nodeId: String(params?.nodeId ?? ''),
    handleType: params?.handleType || 'source',
    handleId: String(params?.handleId ?? ''),
    x: ev?.clientX ?? 0,
    y: ev?.clientY ?? 0
  };
}
function onConnectEnd(event: any) {
  const start = connectStartInfo;
  connectStartInfo = null;
  connectMenu.value = null;
  if (!start || !event || !start.nodeId) return;
  const clientX = event.clientX ?? event.changedTouches?.[0]?.clientX ?? 0;
  const clientY = event.clientY ?? event.changedTouches?.[0]?.clientY ?? 0;
  const dist = Math.hypot(clientX - start.x, clientY - start.y);
  const target: HTMLElement | null = event.target as HTMLElement | null;
  // 落在控件 / 工具栏 / 对话窗 / 全局预览块上 → 不弹
  if (target?.closest?.('.vue-flow__controls, .vue-flow__minimap, .wf-toolbar, .wf-float')) return;
  const onNode = Boolean(target?.closest?.('.vue-flow__node'));
  // 真正拖到了另一个节点上（有位移）→ 已由 onConnect 建立连线
  if (onNode && dist > 6) return;
  // 在 handle 上原地按下又松开（无位移、落点仍在节点）→ 视为「点击 handle 加节点」
  // 拖线后松手在空白处（有位移）→ 弹菜单建节点并连线
  const flow = screenToFlowCoordinate({x: clientX, y: clientY});
  connectMenu.value = {
    x: clientX,
    y: clientY,
    flowX: flow.x,
    flowY: flow.y,
    sourceNode: start.nodeId,
    handleType: start.handleType,
    sourceHandle: start.handleType === 'source' ? start.handleId : '',
    autoPlace: dist <= 6
  };
}
/** 沿连线方向与源节点隔开一个间距摆放新节点（查不到源节点时返回 null）。
 *  sourceHandle 非空 = 任务级出线：新节点纵轴对齐那条任务行（多行任务各线齐整），找不到行 handle 回退卡中线 */
function placeBesideNode(nodeId: string, handleType: string, type: string, sourceHandle = ''): {x: number; y: number} | null {
  const src = findNode(nodeId);
  if (!src) return null;
  const GAP = 70;
  const sw = src.dimensions?.width || 120;
  const sh = src.dimensions?.height || 40;
  const [nw, nh] = NODE_SIZE_HINTS[type] || [182, 46];
  if (handleType === 'target') return {x: src.position.x - GAP - nw, y: src.position.y + sh / 2 - nh / 2};
  let cy = src.position.y + sh / 2;
  if (sourceHandle) {
    const hel = document.querySelector<HTMLElement>(`.vue-flow__handle[data-handleid="${CSS.escape(sourceHandle)}"]`);
    const r = hel?.getBoundingClientRect();
    if (hel && r) cy = screenToFlowCoordinate({x: 0, y: r.top + r.height / 2}).y;
  }
  return {x: src.position.x + sw + GAP, y: cy - nh / 2};
}
/** 建连线：sourceHandle 非空 = 从源卡某条任务出发（edge id 带 handle 段避免同卡多线撞 id）；手动拖出 = 流程线 */
function addStyledEdge(source: string, target: string, sourceHandle?: string) {
  const edge: any = {
    id: sourceHandle ? `e${source}-${sourceHandle}-${target}` : `e${source}-${target}`,
    source,
    target
  };
  if (sourceHandle) edge.sourceHandle = sourceHandle;
  markEdgeNew(edge.id); // 人手建线同样吃「新连线高亮」，agent 下次编辑统一清除
  edges.value = [...edges.value, decorateEdge(edge)];
}

/** 切换连线类型：flow（流程线）⇄ ref（参照线）。kind 随保存落库，线型只是渲染派生 */
function setEdgeKind(id: string, kind: 'flow' | 'ref') {
  if (editLocked.value) return;
  if (!edges.value.some(e => e.id === id)) return;
  pushHistory();
  edges.value = edges.value.map(e => (e.id === id ? decorateEdge({...e, kind}) : e));
  scheduleSave();
}
function connectAdd(type: string) {
  const m = connectMenu.value;
  connectMenu.value = null;
  if (!m || editLocked.value) return;
  // 点击式添加（handle 点击）：自动避让摆放；拖线松开：尊重用户松手的位置（节点中心对齐落点）
  const pos = (m.autoPlace && m.sourceNode && placeBesideNode(m.sourceNode, m.handleType, type, m.sourceHandle)) || centeredAt({x: m.flowX, y: m.flowY}, type);
  // addNodeAt 内部已入历史栈（快照在新节点+新边之前），一次撤销即可同时移除两者
  const id = addNodeAt(pos, type);
  if (m.sourceNode) {
    // 从 source handle 拖出：源 → 新节点；从 target handle 拖出：新节点 → 源
    if (m.handleType === 'target') addStyledEdge(id, m.sourceNode);
    else addStyledEdge(m.sourceNode, id, m.sourceHandle || undefined);
  }
  scheduleSave();
  if (type === 'textNode') nextTick(() => triggerEdit(id));
}
const connectMenuStyle = computed(() => {
  if (!connectMenu.value) return {};
  const W = 190;
  const H = 196 + brandNodeDefs.length * 34;
  const x = Math.min(connectMenu.value.x, window.innerWidth - W - 8);
  const y = Math.min(connectMenu.value.y, window.innerHeight - H - 8);
  return {left: `${x}px`, top: `${y}px`};
});

// ── 历史栈（撤销 / 重做） ─────────────────────────────────────────────────
function snapGraph() {
  return JSON.stringify({nodes: nodes.value, edges: edges.value});
}
function pushHistory() {
  history.value.push(snapGraph());
  if (history.value.length > 60) history.value.shift();
  redoStack.value = [];
}
function restoreGraph(json: string) {
  const s = JSON.parse(json);
  nodes.value = ensureNodeType(s.nodes || []);
  edges.value = (s.edges || []).map(decorateEdge);
  scheduleSave();
}
function undo() {
  if (editLocked.value) return;
  if (!history.value.length) return;
  redoStack.value.push(snapGraph());
  restoreGraph(history.value.pop() as string);
}
function redo() {
  if (editLocked.value) return;
  if (!redoStack.value.length) return;
  history.value.push(snapGraph());
  restoreGraph(redoStack.value.pop() as string);
}

// ── 节点增删改 ────────────────────────────────────────────────────────────
function nextNodeId() {
  const nums = nodes.value.map(n => Number(n.id)).filter(x => Number.isFinite(x));
  const max = nums.length ? Math.max(...nums) : 0;
  return String(Math.max(max + 1, 1000 + addSeq++));
}

function centerPos() {
  if (canvasEl.value) {
    const r = canvasEl.value.getBoundingClientRect();
    const center = screenToFlowCoordinate({x: r.left + r.width / 2, y: r.top + r.height / 2});
    const jitter = (nodes.value.length % 6) * 40;
    return {x: center.x + jitter, y: center.y + jitter};
  }
  return {x: 120, y: 120};
}

/** 「点击坐标」→ 节点摆放坐标（左上角）：按类型估值尺寸把节点中心对到光标处 */
function centeredAt(p: {x: number; y: number}, type: string): {x: number; y: number} {
  const [nw, nh] = NODE_SIZE_HINTS[type] || [182, 46];
  return {x: p.x - nw / 2, y: p.y - nh / 2};
}

// 各类型新建时的默认 data（各卡字段契约，见 wf-node.vue 各形态 computed）
const NEW_NODE_DATA: Record<string, Record<string, any>> = {
  startNode: {label: '开始'},
  endNode: {label: '结束'},
  fileNode: {}, // 空文件节点：节点内点击 / 拖拽上传
  reviewNode: {question: '', options: [], answer: ''}, // 人工核查：agent 填问题 / 选项，用户作答写 answer
  taskNode: {title: '', subs: [], summary: '', note: ''}, // 工作项：核心是多任务清单 subs = [[文字, sid], ...]
  dataNode: {title: '', metric: '', unit: '', attrs: [], note: '', samples: []}, // 数据卡：数字 / 属性 / 口径 / 证据
  conclusionNode: {claim: '', points: [], caveat: ''}, // 结论卡：答案 / 要点 / 注意
  textNode: {text: ''},
  ...Object.fromEntries(brandNodeDefs.map(t => [t.nodeType, t.newData])) // 品牌专属板型（注册表）
};

function addNodeAt(pos: {x: number; y: number}, type: string = 'textNode'): string {
  if (editLocked.value) return '';
  pushHistory();
  const id = nextNodeId();
  const data = {...(NEW_NODE_DATA[type] || NEW_NODE_DATA.textNode)};
  nodes.value = [
    ...nodes.value.map(n => ({...n, selected: false})),
    {
      id,
      type,
      position: {x: Math.round(pos.x), y: Math.round(pos.y)},
      data,
      selected: true
    }
  ];
  scheduleSave();
  // 新建内容类节点自动进入编辑态，直接开打（文本 / 工作项 / 数据 / 结论 / 品牌板型卡）
  if (['textNode', 'taskNode', 'dataNode', 'conclusionNode'].includes(type) || brandNodeDefs.some(t => t.nodeType === type))
    nextTick(() => triggerEdit(id));
  return id;
}

function addNode(type: string = 'textNode') {
  addNodeAt(centeredAt(centerPos(), type), type);
}

// ── 附件上传（节点内点击选择文件 → 全局隐藏 input） ──────────────────────
async function onFileInputChange(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file || !uploadTargetNodeId || editLocked.value) return;
  await uploadOneToNode(uploadTargetNodeId, file);
}

function deleteNode(id: string) {
  if (editLocked.value) return;
  if (!nodes.value.some(n => n.id === id)) return;
  pushHistory();
  clearMarksFor([id], edges.value.filter(e => e.source === id || e.target === id).map(e => e.id));
  edges.value = edges.value.filter(e => e.source !== id && e.target !== id);
  nodes.value = nodes.value.filter(n => n.id !== id);
  scheduleSave();
}

function deleteEdge(id: string) {
  if (editLocked.value) return;
  if (!edges.value.some(e => e.id === id)) return;
  pushHistory();
  clearMarksFor(undefined, [id]);
  edges.value = edges.value.filter(e => e.id !== id);
  scheduleSave();
}

function deleteSelected() {
  if (editLocked.value) return;
  const selNodeIds = new Set(nodes.value.filter(n => n.selected).map(n => n.id));
  const hasSelEdge = edges.value.some(e => e.selected);
  if (!selNodeIds.size && !hasSelEdge) return;
  pushHistory();
  clearMarksFor(
    [...selNodeIds],
    edges.value.filter(e => e.selected || selNodeIds.has(e.source) || selNodeIds.has(e.target)).map(e => e.id)
  );
  edges.value = edges.value.filter(e => !e.selected && !selNodeIds.has(e.source) && !selNodeIds.has(e.target));
  nodes.value = nodes.value.filter(n => !selNodeIds.has(n.id));
  scheduleSave();
}

function duplicateNode(id: string) {
  if (editLocked.value) return;
  const src = nodes.value.find(n => n.id === id);
  if (!src) return;
  pushHistory();
  const copy: any = JSON.parse(JSON.stringify(src));
  copy.id = nextNodeId();
  copy.selected = true;
  copy.position = {x: (src.position?.x || 0) + 48, y: (src.position?.y || 0) + 48};
  copy.data = {...src.data};
  nodes.value = [...nodes.value.map(n => ({...n, selected: false})), copy];
  scheduleSave();
}

// ── 右键菜单 ──────────────────────────────────────────────────────────────
function onNodeContextMenu({event, node}: any) {
  event?.preventDefault?.();
  if (editLocked.value) return; // 响应期锁编辑：右键菜单全是编辑动作，整体不弹
  ctxMenu.value = {x: event.clientX, y: event.clientY, nodeId: node.id};
}
function onEdgeContextMenu({event, edge}: any) {
  event?.preventDefault?.();
  if (editLocked.value) return;
  ctxMenu.value = {x: event.clientX, y: event.clientY, edgeId: edge.id};
}
function onPaneContextMenu(event: any) {
  event?.preventDefault?.();
  if (editLocked.value) return;
  const clientX = event?.clientX ?? 0;
  const clientY = event?.clientY ?? 0;
  ctxMenu.value = {
    x: clientX,
    y: clientY,
    panePos: screenToFlowCoordinate({x: clientX, y: clientY})
  };
}

const ctxMenuStyle = computed(() => {
  if (!ctxMenu.value) return {};
  const W = 176;
  // 连线菜单 2 项 / 节点菜单 2 项 / 空白菜单 8 项（+ 品牌板型项）
  const H = ctxMenu.value.edgeId ? 84 : ctxMenu.value.nodeId ? 84 : 236 + brandNodeDefs.length * 34;
  const x = Math.min(ctxMenu.value.x, window.innerWidth - W - 8);
  const y = Math.min(ctxMenu.value.y, window.innerHeight - H - 8);
  return {left: `${x}px`, top: `${y}px`};
});

// 右键的这条连线当前是流程线还是参照线（决定菜单项文案与线型样例）
const ctxEdgeKind = computed(() => {
  const id = ctxMenu.value?.edgeId;
  if (!id) return 'flow';
  return edges.value.find(e => e.id === id)?.kind === 'ref' ? 'ref' : 'flow';
});

function ctxAction(act: string) {
  const m = ctxMenu.value;
  ctxMenu.value = null;
  if (!m || editLocked.value) return;
  if (act === 'dup' && m.nodeId) duplicateNode(m.nodeId);
  else if (act === 'del' && m.nodeId) deleteNode(m.nodeId);
  else if (act === 'del-edge' && m.edgeId) deleteEdge(m.edgeId);
  else if (act === 'toggle-edge-kind' && m.edgeId) {
    // 注意此时 ctxMenu 已清空，当前线型直接从边对象取（ctxEdgeKind 依赖菜单状态，不能在这用）
    const cur = edges.value.find(e => e.id === m.edgeId)?.kind === 'ref' ? 'ref' : 'flow';
    setEdgeKind(m.edgeId, cur === 'ref' ? 'flow' : 'ref');
  }
  else if (act === 'add' && m.panePos) addNodeAt(centeredAt(m.panePos, 'textNode'), 'textNode');
  else if (act === 'add-work' && m.panePos) addNodeAt(centeredAt(m.panePos, 'taskNode'), 'taskNode');
  else if (act === 'add-data' && m.panePos) addNodeAt(centeredAt(m.panePos, 'dataNode'), 'dataNode');
  else if (act === 'add-conclusion' && m.panePos) addNodeAt(centeredAt(m.panePos, 'conclusionNode'), 'conclusionNode');
  else if (act === 'add-start' && m.panePos) addNodeAt(centeredAt(m.panePos, 'startNode'), 'startNode');
  else if (act === 'add-end' && m.panePos) addNodeAt(centeredAt(m.panePos, 'endNode'), 'endNode');
  else if (act === 'add-file' && m.panePos) addNodeAt(centeredAt(m.panePos, 'fileNode'), 'fileNode');
  else if (act === 'add-review' && m.panePos) addNodeAt(centeredAt(m.panePos, 'reviewNode'), 'reviewNode');
  else if (act.startsWith('add-brand-') && m.panePos) {
    // 品牌专属板型卡（注册表）
    const nt = act.slice('add-brand-'.length);
    addNodeAt(centeredAt(m.panePos, nt), nt);
  }
}

// ── 布局 / 视图 ───────────────────────────────────────────────────────────
// 画布交互模式：pan=拖拽（默认，左键拖空白=平移画布，Shift+拖=框选）；select=框选（左键拖空白=框选）
// 记住用户的选择，选中哪个用哪个
const canvasMode = ref<'pan' | 'select'>(localStorage.getItem('SOY_wf_canvas_mode') === 'select' ? 'select' : 'pan');
watch(canvasMode, m => localStorage.setItem('SOY_wf_canvas_mode', m));

// 「添加节点」折叠菜单（点击菜单外 / Esc 关闭）
const addMenuOpen = ref(false);
function addFromMenu(type: string) {
  addMenuOpen.value = false;
  if (editLocked.value) return;
  addNode(type);
}
function closeAddMenuOutside(e: PointerEvent) {
  if (!(e.target as HTMLElement)?.closest?.('.wf-addmenu-wrap')) addMenuOpen.value = false;
}
watch(addMenuOpen, open => {
  if (open) setTimeout(() => document.addEventListener('pointerdown', closeAddMenuOutside), 0);
  else document.removeEventListener('pointerdown', closeAddMenuOutside);
});


async function relayout() {
  if (!nodes.value.length) return;
  // 响应期也允许整理板子（纯排版不碰卡片内容）：先同步服务端最新板子——Agent 可能刚写过板，
  // 若直接拿本地滞后快照（轮询 3.5s 间隔）整包保存，会把 Agent 的改动覆盖掉
  if (editLocked.value && workflowKey.value) {
    const {data} = await fetchWorkflow(workflowKey.value);
    if (data && data.version !== version.value) applyData(data);
  }
  if (!nodes.value.length) return;
  pushHistory();
  // 手动整板重排：取消待执行的实测重排（否则稍后又会动一次刚摆好的卡）
  hintPlaced = new Set();
  if (relayoutTimer) clearTimeout(relayoutTimer);
  // 对齐前强制同步实测一遍尺寸，dagre 必须拿真实大小排，否则卡片叠压：
  // dimensions 是 RO 异步测的缓存值，三种场景会失真——① 卡片刚长高（Agent 加行 / 刚 applyData，
  // 渲染还没落地缓存还是旧高度）② 后台标签页 RO 不回调，缓存停在旧值 ③ 新增卡从没被测过（0x0 被当点节点摆）。
  // 先 nextTick 等挂起的渲染落地，再 updateNodeInternals 走 offsetWidth/Height 同步测量（不依赖 RO，前后台都准）
  await nextTick();
  updateNodeInternals();
  const stripped = nodes.value.map(n => ({...n, position: null}));
  nodes.value = autoLayout(stripped, edges.value);
  scheduleSave();
  // 自动对齐后保留用户当前视口，不强制视图回中（需要看全貌时手动点「视图」）
}

function fitAll() {
  fitView({padding: 0.2, duration: 300});
}

// ── 键盘快捷键 ────────────────────────────────────────────────────────────
function onKeyDown(e: KeyboardEvent) {
  // Esc 关闭节点全屏预览弹窗（视图动作，不受编辑锁限制；输入框打字时不触发）
  if (e.key === 'Escape' && previewNodeId.value) {
    const el = e.target as HTMLElement | null;
    if (!el?.closest?.('input, textarea, [contenteditable="true"]')) {
      closeNodePreview();
      return;
    }
  }
  // Esc 退出全屏浏览（同为视图动作，不受编辑锁限制；原生全屏下浏览器自己消费 Esc，这里兜软全屏）
  if (e.key === 'Escape' && fullscreen.value) {
    const el = e.target as HTMLElement | null;
    if (!el?.closest?.('input, textarea, [contenteditable="true"]')) {
      leaveFullscreen();
      return;
    }
  }
  if (editLocked.value) return; // 响应期锁编辑：Delete / 撤销重做等画布快捷键整体停用
  const el = e.target as HTMLElement | null;
  // 文本节点正在输入时不触发画布快捷键
  if (el?.closest?.('input, textarea, [contenteditable="true"]')) return;
  const k = e.key.toLowerCase();
  if (e.key === 'Delete' || e.key === 'Backspace') {
    e.preventDefault();
    deleteSelected();
  } else if ((e.ctrlKey || e.metaKey) && k === 'z' && !e.shiftKey) {
    e.preventDefault();
    undo();
  } else if ((e.ctrlKey || e.metaKey) && (k === 'y' || (k === 'z' && e.shiftKey))) {
    e.preventDefault();
    redo();
  } else if (e.key === 'Escape') {
    addMenuOpen.value = false;
  }
}

const selectedCount = computed(
  () => nodes.value.filter(n => n.selected).length + edges.value.filter(e => e.selected).length
);

// 当前选中的节点（与 selectedCount 同源）：id 列表随 QA 消息发给 agent；echo 派生标签用于面板「选中节点」块
const selectedNodeIds = computed(() => nodes.value.filter(n => n.selected).map(n => String(n.id)));
const selectedNodesEcho = computed(() => nodes.value.filter(n => n.selected).map(n => ({id: String(n.id), label: nodeLabel(n)})));

// ── 顶栏快捷入口：新建 / 切换画板（对话按钮旁） ────────────────────────────
const boardMenuOpen = ref(false);
const boardList = ref<WorkflowListItem[]>([]);
const boardListLoading = ref(false);
const boardKeyword = ref('');
/** 菜单内搜索：列表打开时已全量拉取，本地过滤即输即得 */
const filteredBoardList = computed(() => {
  const k = boardKeyword.value.trim().toLowerCase();
  if (!k) return boardList.value;
  return boardList.value.filter(b => String(b.title || '').toLowerCase().includes(k));
});
async function toggleBoardMenu() {
  boardMenuOpen.value = !boardMenuOpen.value;
  if (!boardMenuOpen.value) return;
  boardKeyword.value = '';
  boardListLoading.value = true;
  const {data} = await fetchListWorkflows();
  boardList.value = data || [];
  boardListLoading.value = false;
}
function closeBoardMenu() {
  boardMenuOpen.value = false;
}
function closeBoardMenuOutside(e: PointerEvent) {
  if (!(e.target as HTMLElement)?.closest?.('.wf-boardmenu-wrap')) boardMenuOpen.value = false;
}
watch(boardMenuOpen, open => {
  if (open) setTimeout(() => document.addEventListener('pointerdown', closeBoardMenuOutside), 0);
  else document.removeEventListener('pointerdown', closeBoardMenuOutside);
});
function pickBoard(wk: string) {
  boardMenuOpen.value = false;
  if (wk === workflowKey.value) return;
  emit('board-switched', wk);
}

// ── 应用制作分享：顶栏「分享」按钮 → 面板（开关 + 模式选择 + 复制链接）。仅 html 型且已发布（entryReady）时显示。
//    开启后访客可经 /share/{workflowKey} 打开使用（数据层写回 / 页内 AI 照常，不能编辑板本身）。
//    两种模式：免登录公开（sharePublic=true，默认）/ 仅登录用户可打开（匿名访客由分享页引导去登录）。
//    每次开启分享一律默认免登录；开启后可随时经面板分段按钮切换模式
const shareMenuOpen = ref(false);
const shareOn = ref(false);
const sharePublic = ref(true);
const shareBusy = ref(false);
const shareCopied = ref(false);
let shareCopiedTimer: ReturnType<typeof setTimeout> | null = null;

const shareLink = computed(() => `${location.origin}/share/${workflowKey.value}`);

function toggleShareMenu() {
  shareMenuOpen.value = !shareMenuOpen.value;
}
function closeShareMenu() {
  shareMenuOpen.value = false;
}
function closeShareMenuOutside(e: PointerEvent) {
  if (!(e.target as HTMLElement)?.closest?.('.wf-sharemenu-wrap')) shareMenuOpen.value = false;
}
watch(shareMenuOpen, open => {
  if (open) setTimeout(() => document.addEventListener('pointerdown', closeShareMenuOutside), 0);
  else document.removeEventListener('pointerdown', closeShareMenuOutside);
});

async function toggleShare() {
  if (shareBusy.value || !workflowKey.value) return;
  const next = !shareOn.value;
  shareBusy.value = true;
  // 开启分享默认免登录（public=true）；关闭时后端一并清模式
  const {data, error} = await fetchSetWorkflowShare(workflowKey.value, next, next);
  shareBusy.value = false;
  if (error || !data) {
    window.$message?.error(error?.msg || (next ? '开启分享失败' : '关闭分享失败'));
    return;
  }
  shareOn.value = data.shareOn;
  sharePublic.value = data.sharePublic;
  window.$message?.success?.(next ? (sharePublic.value ? '分享已开启，任何人可经链接免登录打开' : '分享已开启，仅登录用户可经链接打开') : '分享已关闭');
}

/** 切换分享模式（已开启时生效）：false=仅登录用户可打开 / true=免登录公开 */
async function setShareMode(pub: boolean) {
  if (shareBusy.value || !workflowKey.value || !shareOn.value || pub === sharePublic.value) return;
  shareBusy.value = true;
  const {data, error} = await fetchSetWorkflowShare(workflowKey.value, true, pub);
  shareBusy.value = false;
  if (error || !data) {
    window.$message?.error(error?.msg || '切换分享模式失败');
    return;
  }
  sharePublic.value = data.sharePublic;
  window.$message?.success?.(pub ? '已切换为「免登录可打开」' : '已切换为「仅登录用户可打开」');
}

async function copyShareLink() {
  // navigator.clipboard 仅在安全上下文（HTTPS / localhost）可用；
  // 部署后走 HTTP 时降级为 textarea + execCommand('copy')
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(shareLink.value);
    } else {
      const ta = document.createElement('textarea');
      ta.value = shareLink.value;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    shareCopied.value = true;
    if (shareCopiedTimer) clearTimeout(shareCopiedTimer);
    shareCopiedTimer = setTimeout(() => (shareCopied.value = false), 2000);
  } catch {
    window.$message?.error('复制失败，请手动选择链接复制');
  }
}

// ── 顶栏快捷入口：版本（应用制作 = 用户点「发布」固化的版本，可直接切换查看；节点板 = 每次写入自动存档，可回滚）──
// html 板：切换 = 目标版文件还原 + 指针改指（仅未固化改动才自动备份一版，不无脑复制新版本）；
// 节点板：回滚 = 时点全量还原（当前态先自动存档，因此回滚可再回滚）。
const versionMenuOpen = ref(false);
const versionList = ref<WorkflowVersionItem[]>([]);
const versionLoading = ref(false);
const rollbackBusy = ref(false);
const publishBusy = ref(false);
async function toggleVersionMenu() {
  versionMenuOpen.value = !versionMenuOpen.value;
  boardMenuOpen.value = false;
  if (!versionMenuOpen.value) return;
  await loadVersions();
}
async function loadVersions() {
  if (!workflowKey.value) return;
  versionLoading.value = true;
  const {data} = await fetchWorkflowVersions(workflowKey.value);
  versionList.value = data || [];
  versionLoading.value = false;
}
function closeVersionMenuOutside(e: PointerEvent) {
  // 回滚确认气泡（NPopconfirm）teleport 到 body，不在 .wf-vermenu-wrap 子树里——
  // 气泡内的点击不能关菜单，否则按下「回滚」确认的瞬间菜单先被收掉、气泡随之销毁，positive-click 永远不发出
  const t = e.target as HTMLElement;
  if (t?.closest?.('.wf-vermenu-wrap') || t?.closest?.('.n-popconfirm')) return;
  versionMenuOpen.value = false;
}
watch(versionMenuOpen, open => {
  if (open) setTimeout(() => document.addEventListener('pointerdown', closeVersionMenuOutside), 0);
  else document.removeEventListener('pointerdown', closeVersionMenuOutside);
});
// 与「切换」菜单互斥：顶栏两个下拉不叠加（反向由 toggleVersionMenu 关闭切换菜单）
watch(boardMenuOpen, open => {
  if (open) versionMenuOpen.value = false;
});
function versionMeta(v: WorkflowVersionItem): string {
  if (boardType.value === 'html') return `${v.fileCount ?? 0} 个文件`;
  return `${v.nodeCount ?? 0} 节点 · ${v.edgeCount ?? 0} 连线`;
}
/** 应用制作：把当前状态固化为新版本（版本只在用户点「发布」时产生；内容没变不重复建版） */
async function doPublish() {
  if (publishBusy.value || !workflowKey.value) return;
  publishBusy.value = true;
  const {data, error} = await fetchPublishWorkflowVersion(workflowKey.value);
  publishBusy.value = false;
  if (error || !data) {
    window.$message?.error(error?.msg || '发布失败，请重试');
    return;
  }
  if (data.unchanged) {
    window.$message?.info(`内容与版本 ${data.version} 相同，未重复建版`);
  } else {
    window.$message?.success(`已发布为版本 ${data.version}`);
  }
  if (versionMenuOpen.value) await loadVersions(); // 菜单开着就地把新版本刷进列表
}
/** 节点板回滚到指定存档版本（经 NPopconfirm 确认；整板重载） */
async function doRollback(v: WorkflowVersionItem) {
  if (rollbackBusy.value || !workflowKey.value) return;
  rollbackBusy.value = true;
  const {data, error} = await fetchRollbackWorkflowVersion(workflowKey.value, v.version);
  rollbackBusy.value = false;
  if (error || !data) {
    window.$message?.error(error?.msg || '回滚失败，请重试');
    return;
  }
  versionMenuOpen.value = false;
  window.$message?.success(`已回滚到版本 ${v.version}`);
  await loadWorkflow(workflowKey.value); // 节点板整板替换：重拉全量数据
}
/** 应用制作：直接切换到指定版本查看（无确认弹窗——未固化改动后端会自动备份，随时可切回来） */
async function switchVersion(v: WorkflowVersionItem) {
  if (rollbackBusy.value || v.current || !workflowKey.value) return;
  rollbackBusy.value = true;
  const {data, error} = await fetchRollbackWorkflowVersion(workflowKey.value, v.version);
  rollbackBusy.value = false;
  if (error || !data) {
    window.$message?.error(error?.msg || '切换失败，请重试');
    return;
  }
  versionMenuOpen.value = false;
  window.$message?.success(`已切换到版本 ${v.version}`);
  await recheckHtmlTask(); // 后端 version 递增 → HtmlBoard 侦测到变化自动重载 iframe
}

// ── 全屏：浏览器 Fullscreen API 占满整个屏幕 + 隐藏顶栏只看画布（Esc 或右上悬浮按钮退出）──
function enterFullscreen() {
  // 请宿主收起对话窗；顶栏悬浮菜单就地收掉，别把打开的面板冻在隐藏态
  emit('fullscreen-enter');
  boardMenuOpen.value = false;
  versionMenuOpen.value = false;
  addMenuOpen.value = false;
  ctxMenu.value = null;
  connectMenu.value = null;
  fullscreen.value = true;
  // 浏览器级全屏（拒绝/不支持时静默退回「仅隐藏顶栏」的软全屏，不打断用户）
  document.documentElement.requestFullscreen?.().catch(() => {});
}
function leaveFullscreen() {
  fullscreen.value = false;
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}
// 用户按 Esc 退出浏览器全屏时同步收掉软全屏态（浏览器自行退出原生全屏后派发此事件）
function onFullscreenChange() {
  if (!document.fullscreenElement && fullscreen.value) fullscreen.value = false;
}
function newBoardFromMenu() {
  boardMenuOpen.value = false;
  emit('create-requested', 'board');
}

// ── 应用制作文字编辑：顶栏按钮 → HtmlBoard → iframe 内注入脚本（回报 edit-state 更新按钮形态）
function toggleHtmlTextEdit() {
  htmlBoardRef.value?.toggleTextEdit();
}
function onHtmlEditState(s: {editing: boolean; editableCount: number}) {
  htmlEditing.value = s.editing;
}

// ── 节点全屏预览弹窗（参照附件卡 ⛶ 预览的模式，推广到所有卡型）：
//    弹窗渲染在独立子组件 WfNodePreview 里——其内部小 VueFlow 的流程状态在自己子树创建并 provide，
//    与主画布状态天然隔离（本组件里绝不能出现第二个 useVueFlow，provide 覆盖会把主画布一起劫持）；
//    弹窗里是该卡的真实节点组件，不是快照，保持可编辑（wfNodeApi 沿组件树注入，直写主画布节点）──
const previewNodeId = ref<string | null>(null);
const previewNode = computed(() => (previewNodeId.value ? nodes.value.find(n => String(n.id) === previewNodeId.value) || null : null));
const previewNodeLabelText = computed(() => (previewNode.value ? nodeLabel(previewNode.value) : ''));
function openNodePreview(id: string) {
  boardMenuOpen.value = false;
  addMenuOpen.value = false;
  ctxMenu.value = null;
  connectMenu.value = null;
  previewNodeId.value = String(id);
}
function closeNodePreview() {
  previewNodeId.value = null;
}
// 预览开着时卡被 Agent 撤掉 → 自动关闭，别留一个空弹窗
watch(previewNode, n => {
  if (!n && previewNodeId.value) previewNodeId.value = null;
});

const saveLabel = computed(() => {
  if (saveState.value === 'saving') return '保存中…';
  if (saveState.value === 'saved') return '已保存 ✓';
  return '';
});

// ── 轮询 / 保存生命周期（宿主以 prop 换板；壳内自管启停，防双轮询与泄漏） ──
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

/** 冲刷保存：宿主返回 / 切板前的即时落盘（替代跨组件触碰 saveTimer） */
async function flushSave() {
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
    if (!isCurrentTaskEmpty()) await doSave();
  }
}

/** wk 清空时的本地复位（宿主返回列表 / 面板收起；板没了全屏也一并退出） */
function resetShellState() {
  if (fullscreen.value) leaveFullscreen();
  nodes.value = [];
  edges.value = [];
  title.value = '';
  version.value = 0;
  boardType.value = 'board';
  entryReady.value = false;
  shareOn.value = false;
  sharePublic.value = false;
  history.value = [];
  redoStack.value = [];
  ctxMenu.value = null;
  connectMenu.value = null;
  dismissEcho();
  htmlEditing.value = false;
  loading.value = false;
  baselineNodes = [];
  baselineEdges = [];
  baselineTitle = '';
}

watch(
  () => props.workflowKey,
  async (wk, old) => {
    if (wk === old) return;
    if (wk) {
      await loadWorkflow(wk);
      startPolling();
    } else {
      stopPolling();
      resetShellState();
    }
  },
  {immediate: true}
);

onMounted(() => {
  window.addEventListener('keydown', onKeyDown);
  document.addEventListener('fullscreenchange', onFullscreenChange);
});

onBeforeUnmount(() => {
  stopPolling();
  if (saveTimer) clearTimeout(saveTimer);
  if (relayoutTimer) clearTimeout(relayoutTimer);
  if (echoHideTimer) clearTimeout(echoHideTimer);
  if (shareCopiedTimer) clearTimeout(shareCopiedTimer);
  document.removeEventListener('pointerdown', closeAddMenuOutside);
  document.removeEventListener('fullscreenchange', onFullscreenChange);
  document.removeEventListener('pointerdown', closeBoardMenuOutside);
  document.removeEventListener('pointerdown', closeShareMenuOutside);
  document.removeEventListener('pointerdown', closeVersionMenuOutside);
  window.removeEventListener('keydown', onKeyDown);
});

defineExpose({
  flushSave,
  reload: loadWorkflow,
  isEmpty: isCurrentTaskEmpty,
  loading,
  selectedNodeIds,
  boardType,
  version,
  title
});
</script>

<template>
  <div class="wf-shell" :class="{'wf-shell--fs': fullscreen}">
    <header v-show="!fullscreen" class="wf-topbar" :class="{'wf-topbar--lift': boardMenuOpen || versionMenuOpen || shareMenuOpen}">
      <button class="wf-back" :title="workflowKey ? '返回流程编排列表' : '返回对话'" @click="emit('back')">
        <svg viewBox="0 0 20 20" fill="currentColor" width="18" height="18"><path fill-rule="evenodd" d="M17 10a.75.75 0 01-.75.75H5.612l4.158 3.96a.75.75 0 11-1.04 1.08l-5.5-5.25a.75.75 0 010-1.08l5.5-5.25a.75.75 0 111.04 1.08L5.612 9.25H16.25A.75.75 0 0117 10z" /></svg>
      </button>
      <template v-if="workflowKey">
        <input v-model="title" class="wf-title-input" spellcheck="false" :readonly="editLocked" :title="editLocked ? 'Agent 回应中，暂停编辑' : ''" @change="scheduleSave" />
        <span v-if="saveLabel" class="wf-save-hint">{{ saveLabel }}</span>
      </template>
      <span v-else class="wf-title-static">流程编排</span>
      <div class="wf-topbar-right">
        <template v-if="workflowKey">
          <!-- 快捷入口：全屏（浏览器全屏占满整个屏幕 + 隐藏顶栏；Esc 或右上悬浮按钮退出） -->
          <button v-if="allowFullscreen" class="wf-chat-btn" title="全屏：占满整个屏幕（Esc 退出）" @click="enterFullscreen">
            <WfIcon name="expand" :size="14" /><span>全屏</span>
          </button>
          <!-- 应用制作专属：就地编辑页面上的文字（页内注入脚本承接；改动自动存 data/text-edits.json） -->
          <button
            v-if="boardType === 'html' && entryReady"
            class="wf-chat-btn"
            :class="{active: htmlEditing}"
            :title="htmlEditing ? '完成并退出文字编辑（改动已自动保存）' : '直接修改看板页面上的文字'"
            @click="toggleHtmlTextEdit"
          >
            <WfIcon :name="htmlEditing ? 'check' : 'pencil'" :size="14" />
            <span>{{ htmlEditing ? '完成编辑' : '编辑文字' }}</span>
          </button>
          <!-- 应用制作专属：分享（访客经 /share/{workflowKey} 打开使用，不能编辑板本身；模式二选一：仅登录用户 / 免登录） -->
          <span v-if="boardType === 'html' && entryReady" class="wf-sharemenu-wrap">
            <button class="wf-chat-btn" :class="{active: shareMenuOpen || shareOn}" :title="shareOn ? '分享已开启，点击查看链接' : '分享给其他人（默认免登录打开）'" @click="toggleShareMenu">
              <WfIcon name="share" :size="14" />
              <span>分享</span>
            </button>
            <Transition name="wf-menu">
              <div v-if="shareMenuOpen" class="wf-share-panel">
                <div class="wf-share-head">
                  <span class="wf-share-title">分享这个看板</span>
                  <button
                    class="wf-share-switch"
                    :class="{on: shareOn}"
                    :disabled="shareBusy"
                    :title="shareOn ? '点击关闭分享' : '点击开启分享'"
                    @click="toggleShare"
                  ><i class="knob" /></button>
                </div>
                <template v-if="shareOn">
                  <!-- 打开方式二选一：仅登录用户（匿名访客由分享页引导登录）/ 免登录公开 -->
                  <div class="wf-share-modes">
                    <button class="wf-share-mode" :class="{on: !sharePublic}" :disabled="shareBusy" title="访客需登录后打开" @click="setShareMode(false)">仅登录用户</button>
                    <button class="wf-share-mode" :class="{on: sharePublic}" :disabled="shareBusy" title="任何人可经链接直接打开" @click="setShareMode(true)">免登录</button>
                  </div>
                  <div class="wf-share-linkrow">
                    <input class="wf-share-link" readonly :value="shareLink" @focus="($event.target as HTMLInputElement).select()" />
                    <button class="wf-share-copy" @click="copyShareLink">
                      <WfIcon :name="shareCopied ? 'check' : 'copy'" :size="13" />
                      <span>{{ shareCopied ? '已复制' : '复制' }}</span>
                    </button>
                  </div>
                  <p v-if="sharePublic" class="wf-share-note">任何人无需登录即可经链接打开；可以正常浏览使用（含页内数据操作与 AI 对话），但不能编辑看板本身。匿名访客在应用内没有身份；需要多用户 / 应用内角色时请选「仅登录用户」。</p>
                  <p v-else class="wf-share-note">仅登录用户可打开（未登录访客会先被引导登录）；可以正常浏览使用（含页内数据操作与 AI 对话），但不能编辑看板本身。应用可识别访客身份，支持应用内多用户与角色管理。</p>
                </template>
                <template v-else>
                  <p class="wf-share-note">开启后，访客可经链接打开使用这个看板；默认免登录可打开，也可切换为仅登录用户。</p>
                </template>
              </div>
            </Transition>
          </span>
          <!-- 快捷入口：新建任务（与当前任务同型：流程编排建流程编排，应用制作建应用制作） -->
          <button class="wf-chat-btn" :title="boardType === 'html' ? '新建应用制作' : '新建空白画板'" @click="emit('create-requested', boardType)">
            <WfIcon name="plus" :size="14" /><span>新建</span>
          </button>
          <!-- 快捷入口：切换画板（下拉列表，点谁切谁） -->
          <span class="wf-boardmenu-wrap">
            <button class="wf-chat-btn" :class="{active: boardMenuOpen}" title="切换到其他画板" @click="toggleBoardMenu">
              <WfIcon name="layout" :size="14" /><span>切换</span>
              <WfIcon class="wf-board-caret" :class="{up: boardMenuOpen}" name="chevron" :size="11" />
            </button>
            <Transition name="wf-menu">
              <div v-if="boardMenuOpen" class="wf-board-menu">
                <div class="wf-board-search">
                  <svg viewBox="0 0 20 20" fill="currentColor" width="14" height="14"><path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z" clip-rule="evenodd" /></svg>
                  <input v-model="boardKeyword" autofocus placeholder="搜索画板…" spellcheck="false" @keydown.esc.stop="closeBoardMenu" />
                </div>
                <button class="wf-board-new" @click="newBoardFromMenu">＋ 新建画板</button>
                <div class="wf-board-divider" />
                <div class="wf-board-list">
                  <button
                    v-for="b in filteredBoardList"
                    :key="b.workflowKey"
                    class="wf-board-item"
                    :class="{cur: b.workflowKey === workflowKey}"
                    :title="b.title"
                    @click="pickBoard(b.workflowKey)"
                  >
                    <span class="wf-board-item-name">
                      <em class="wf-board-item-type" :class="b.boardType === 'html' ? 't-html' : 't-board'">{{ b.boardType === 'html' ? '应' : '流' }}</em>{{ b.title || '未命名画板' }}
                    </span>
                    <span class="wf-board-item-meta">{{ b.nodeCount || 0 }} 节点 · {{ b.version || 0 }} 次编辑 · {{ fmtRel(b.updateTime) }}</span>
                  </button>
                  <div v-if="boardListLoading" class="wf-board-empty">加载中…</div>
                  <div v-else-if="!filteredBoardList.length" class="wf-board-empty">{{ boardKeyword.trim() ? '没有匹配的画板' : '暂无画板' }}</div>
                </div>
              </div>
            </Transition>
          </span>
          <!-- 应用制作专属：发布——把当前状态固化为新版本。版本只在这里产生：
               agent 改完只更新画面不建版本，用户觉得满意了点「发布」才存一版（内容没变不重复建） -->
          <button
            v-if="boardType === 'html' && entryReady"
            class="wf-chat-btn wf-publish-btn"
            :disabled="publishBusy"
            title="把当前看板状态固化为新版本（Agent 的修改只更新画面，点发布才存档成版本）"
            @click="doPublish"
          >
            <WfIcon :name="publishBusy ? 'history' : 'check'" :size="14" /><span>{{ publishBusy ? '发布中…' : '发布' }}</span>
          </button>
          <!-- 快捷入口：版本（应用制作 = 用户发布的版本，直接点击切换查看；节点板 = 每次写入自动存档，可回滚） -->
          <span class="wf-vermenu-wrap">
            <button class="wf-chat-btn" :class="{active: versionMenuOpen}" :title="boardType === 'html' ? '版本列表与切换' : '版本历史与回滚'" @click="toggleVersionMenu">
              <WfIcon name="history" :size="14" /><span>版本</span>
              <WfIcon class="wf-board-caret" :class="{up: versionMenuOpen}" name="chevron" :size="11" />
            </button>
            <Transition name="wf-menu">
              <div v-if="versionMenuOpen" class="wf-board-menu wf-ver-menu">
                <div class="wf-ver-head">
                  {{ boardType === 'html' ? '版本' : '版本历史' }}
                  <span class="wf-ver-sub">{{ boardType === 'html' ? '点「发布」固化新版本 · 点击任意版本直接切换' : '每次写入自动存档' }}</span>
                </div>
                <div class="wf-board-divider" />
                <div class="wf-board-list">
                  <div v-if="versionLoading" class="wf-board-empty">加载中…</div>
                  <div v-else-if="!versionList.length" class="wf-board-empty">
                    {{ boardType === 'html' ? '暂无版本（点顶栏「发布」把当前状态存为版本）' : '暂无存档（改动保存后自动存档）' }}
                  </div>
                  <template v-else>
                    <div
                      v-for="v in versionList"
                      :key="v.version"
                      class="wf-ver-item"
                      :class="{cur: v.current, clickable: boardType === 'html' && !v.current}"
                      :title="boardType === 'html' && !v.current ? `切换到版本 ${v.version}` : ''"
                      @click="boardType === 'html' && switchVersion(v)"
                    >
                      <div class="wf-ver-item-top">
                        <span class="wf-ver-item-name">
                          版本 {{ v.version }}<em v-if="v.current" class="wf-ver-cur">{{ boardType === 'html' ? '当前查看' : '当前' }}</em>
                        </span>
                        <!-- 节点板保留确认弹窗（整板替换）；应用制作点击行即切换，无需确认（未固化改动会自动备份） -->
                        <NPopconfirm
                          v-if="boardType !== 'html' && !v.current"
                          placement="left"
                          positive-text="回滚"
                          negative-text="取消"
                          :positive-button-props="{type: 'warning', loading: rollbackBusy}"
                          @positive-click="doRollback(v)"
                        >
                          <template #trigger>
                            <button class="wf-ver-rollback" :disabled="rollbackBusy" @click.stop>回滚</button>
                          </template>
                          回滚到版本 {{ v.version }}？当前状态会先自动存档，之后仍可再回滚回来。
                        </NPopconfirm>
                        <button
                          v-else-if="boardType === 'html' && !v.current"
                          class="wf-ver-rollback wf-ver-switch"
                          :disabled="rollbackBusy"
                          @click.stop="switchVersion(v)"
                        >切换</button>
                      </div>
                      <span class="wf-board-item-meta">
                        {{ versionMeta(v) }} · {{ fmtRel(v.archivedAt || undefined) }}
                        <template v-if="v.editor === 'agent'"> · Agent</template>
                        <template v-else-if="v.editor === 'human'"> · 你</template>
                      </span>
                    </div>
                  </template>
                </div>
              </div>
            </Transition>
          </span>
        </template>
        <button v-if="showChatToggle" class="wf-chat-btn" :class="{active: chatActive}" @click="emit('toggle-chat')">
          <svg viewBox="0 0 20 20" fill="currentColor" width="16" height="16"><path fill-rule="evenodd" d="M10 2c-2.236 0-4.43.18-6.57.524C1.993 2.755 1 4.014 1 5.426v5.148c0 1.413.993 2.67 2.43 2.902.848.137 1.705.248 2.57.331v3.443a.75.75 0 001.28.53l3.58-3.579a.78.78 0 01.527-.224 41.202 41.202 0 005.183-.5c1.437-.232 2.43-1.49 2.43-2.903V5.426c0-1.413-.993-2.67-2.43-2.902A41.289 41.289 0 0010 2z" /></svg>
          <span>对话</span>
        </button>
      </div>
    </header>

    <!-- 全屏浏览的退出按钮：右上悬浮小胶囊，平时半透明不打扰，hover 提亮；Esc 同效 -->
    <button v-if="fullscreen" class="wf-fs-exit" title="退出全屏（Esc）" @click="leaveFullscreen">
      <WfIcon name="expand" :size="12" /><span>退出全屏</span>
    </button>

    <!-- 宿主插槽：专页宿主在此放任务库列表（原 .wf-lib 位置）；QA 伴侣面板宿主留空 -->
    <slot />

    <!-- 应用制作画布：iframe 渲染 agent 在任务目录开发的多文件 HTML 应用（签名 token 托管路由）。
         :key=workflowKey 保证切换任务时 HtmlBoard 彻底重建（重新签发 token、断掉旧 iframe） -->
    <div v-if="!libShown && boardType === 'html'" class="wf-canvas wf-canvas-html">
      <div v-if="loading" class="wf-loading">
        <span class="wf-dot" /><span class="wf-dot" /><span class="wf-dot" />
      </div>
      <HtmlBoard
        v-else
        ref="htmlBoardRef"
        :key="workflowKey"
        :workflow-key="workflowKey"
        :version="version"
        :entry-ready="entryReady"
        @recheck="recheckHtmlTask"
        @edit-state="onHtmlEditState"
        @app-chat="onAppChat"
      />
    </div>

    <!-- Vue Flow 画布 -->
    <div
      v-else-if="!libShown"
      ref="canvasEl"
      class="wf-canvas"
      @mousemove="onCanvasMouseMove"
      @mouseleave="clearNearNodes"
      @pointerdown="onCanvasPressStart"
      @pointerup="onCanvasPressEnd"
      @pointercancel="onCanvasPressEnd"
    >
      <div v-if="loading" class="wf-loading">
        <span class="wf-dot" /><span class="wf-dot" /><span class="wf-dot" />
      </div>
      <!-- 点阵背景：合成层实现（见 dotGridStyle 注释），置于 VueFlow 之下 -->
      <div v-else class="wf-dots" :style="dotGridStyle" aria-hidden="true" />
      <VueFlow
        v-if="!loading"
        v-model:nodes="nodes"
        v-model:edges="edges"
        :node-types="nodeTypes"
        :fit-view-on-init="false"
        :min-zoom="0.15"
        :delete-key-code="null"
        connection-mode="loose"
        :nodes-draggable="!editLocked"
        :nodes-connectable="!editLocked"
        :edges-updatable="!editLocked"
        :pan-on-drag="canvasMode === 'pan' ? [0, 1] : [1]"
        :selection-key-code="canvasMode === 'pan' ? 'Shift' : true"
        selection-mode="partial"
        :multi-selection-key-code="['Meta', 'Control']"
        :default-edge-options="{style: {stroke: '#2563eb', strokeWidth: 1.5, strokeDasharray: '5 3'}}"
        @nodes-change="onNodesChange"
        @edges-change="onEdgesChange"
        @connect="onConnect"
        @connect-start="onConnectStart"
        @connect-end="onConnectEnd"
        @node-context-menu="onNodeContextMenu"
        @node-drag-start="onNodeDragStart"
        @node-drag-stop="onNodeDragStop"
        @move-start="onPaneMoveStart"
        @move-end="onPaneMoveEnd"
        @edge-context-menu="onEdgeContextMenu"
        @pane-context-menu="onPaneContextMenu"
      >
        <Controls position="bottom-right" :show-fit-view="true" :show-interactive="false" />
        <!-- 全局预览块：整板等比例缩小（白底细描边卡片 + 深色结论卡 + 渐变胶囊 + 细虚线连线，细节略去）
             + 当前视口框；点击跳转、按住拖平移视野。
             拖拽不用组件自带 pannable（movementX×max(1,zoom) 换算在低缩放档不准、不跟手），
             下方自实现指针捕获拖拽，视口框严格跟手 -->
        <!-- 拖拽期整块卸载：库内置渲染直接订阅全部节点 computedPosition，拖一卡 = 每帧 O(N) 重渲缩略块，
             板子越大越吃帧；缩略块只用于找方位，拖拽时消失、松手即回（2026-08-17 拖拽掉帧整治） -->
        <MiniMap
          v-if="!nodeDragging && !paneMoving"
          class="wf-minimap"
          :width="miniMapGeom.w"
          :height="miniMapGeom.h"
          position="bottom-left"
          mask-color="rgba(219, 231, 252, 0.66)"
          mask-stroke-color="rgba(37, 99, 235, 0.75)"
          :mask-stroke-width="1.5"
          :mask-border-radius="6"
          @pointerdown="onMiniPointerDown"
          @pointermove="onMiniPointerMove"
          @pointerup="onMiniPointerUp"
          @pointercancel="onMiniPointerUp"
        >
          <!-- 缩略自渲染：真实卡片的简化镜像——白底 + 类型色细描边（屏上恒定 1px），结论卡深色，
               开始/结束照真身画渐变胶囊；本卡的出边先画，线压在后绘制的卡片之下 -->
          <template v-for="t in miniMapTypes" :key="t" #[`node-${t}`]="p">
            <g>
              <!-- 缩略连线：拖拽期整段停画（此时 miniEdgesBySource 无人读 = 不重算），松手即回 -->
              <template v-if="!nodeDragging">
                <path v-for="(me, i) in miniEdgesBySource.get(String(p.id)) || []" :key="`e${i}`" :d="me.d" class="wf-mini-edge" :class="me.kind" />
              </template>
              <defs v-if="t === 'startNode' || t === 'endNode'">
                <linearGradient :id="`wf-mm-grad-${p.id}`" x1="0" y1="0" x2="1" y2="0.36">
                  <template v-if="t === 'startNode'">
                    <stop offset="0" stop-color="#1e40af" />
                    <stop offset="0.45" stop-color="#2563eb" />
                    <stop offset="1" stop-color="#0ea5e9" />
                  </template>
                  <template v-else>
                    <stop offset="0" stop-color="#0e7490" />
                    <stop offset="1" stop-color="#10b981" />
                  </template>
                </linearGradient>
              </defs>
              <rect
                v-bind="miniNodeRect(p)"
                :fill="t === 'startNode' || t === 'endNode' ? `url(#wf-mm-grad-${p.id})` : miniCardStyle(p.type).fill"
                :stroke="t === 'startNode' || t === 'endNode' ? 'rgba(255,255,255,0.55)' : miniCardStyle(p.type).stroke"
                class="wf-mini-node"
                :class="{selected: p.selected}"
              />
            </g>
          </template>
        </MiniMap>
      </VueFlow>

      <!-- 响应期编辑锁提示（顶部居中小横条，不拦截点击，响应结束自动消失） -->
      <Transition name="wf-lock-fade">
        <div v-if="editLocked" class="wf-lock-bar" title="对话响应中，Agent 可能正在更新板子，编辑暂时停用；选中节点与自动对齐不受影响">
          <WfIcon class="wf-lock-ic" name="lock" :size="12" />Agent 回应中 · 暂停编辑
        </div>
      </Transition>

      <!-- 画布工具栏 -->
      <div v-if="!loading" class="wf-toolbar">
        <!-- 交互模式：拖拽（默认）/ 框选，记住用户的选择 -->
        <div class="wf-mode">
          <button
            class="wf-mode-btn"
            :class="{active: canvasMode === 'pan'}"
            title="拖拽模式（默认）：左键拖空白 = 平移画布，Shift + 拖 = 框选"
            @click="canvasMode = 'pan'"
          >
            <WfIcon class="wf-mode-ic" name="move" :size="13" />拖拽
          </button>
          <button
            class="wf-mode-btn"
            :class="{active: canvasMode === 'select'}"
            title="框选模式：左键拖空白 = 框选多选，Ctrl/⌘ + 点击 = 加选"
            @click="canvasMode = 'select'"
          >
            <WfIcon class="wf-mode-ic" name="select" :size="13" />框选
          </button>
        </div>
        <span class="wf-tb-sep" />
        <!-- 添加节点（折叠菜单） -->
        <span class="wf-addmenu-wrap">
          <button class="wf-tb-btn primary" :class="{open: addMenuOpen}" :disabled="editLocked" title="添加节点" @click="addMenuOpen = !addMenuOpen">
            <WfIcon class="wf-tb-ic" name="plus" :size="13" />添加节点<span class="wf-tb-caret" />
          </button>
          <div v-if="addMenuOpen" class="wf-addmenu">
            <button class="wf-addmenu-item" @click="addFromMenu('textNode')">
              <WfIcon class="ami-ic tp-text" name="textNode" :size="16" />文本节点<span class="ami-tip">节点内直接输入</span>
            </button>
            <button class="wf-addmenu-item" @click="addFromMenu('taskNode')">
              <WfIcon class="ami-ic tp-work" name="taskNode" :size="16" />工作项<span class="ami-tip">一件事 + 任务清单</span>
            </button>
            <button class="wf-addmenu-item" @click="addFromMenu('dataNode')">
              <WfIcon class="ami-ic tp-data" name="dataNode" :size="16" />数据卡<span class="ami-tip">主数值 + 属性行</span>
            </button>
            <button class="wf-addmenu-item" @click="addFromMenu('conclusionNode')">
              <WfIcon class="ami-ic tp-conc" name="conclusionNode" :size="16" />结论卡<span class="ami-tip">深色卡，板上的答案</span>
            </button>
            <button class="wf-addmenu-item" @click="addFromMenu('fileNode')">
              <WfIcon class="ami-ic tp-file" name="fileNode" :size="16" />文件节点<span class="ami-tip">点击上传文件</span>
            </button>
            <button class="wf-addmenu-item" @click="addFromMenu('reviewNode')">
              <WfIcon class="ami-ic tp-review" name="reviewNode" :size="16" />人工核查<span class="ami-tip">提问，待人工作答</span>
            </button>
            <button class="wf-addmenu-item" @click="addFromMenu('startNode')">
              <WfIcon class="ami-ic tp-start" name="startNode" :size="16" />开始节点
            </button>
            <button class="wf-addmenu-item" @click="addFromMenu('endNode')">
              <WfIcon class="ami-ic tp-end" name="endNode" :size="16" />结束节点
            </button>
            <!-- 品牌专属板型卡（注册表按 BRAND_VARIANT 供给） -->
            <button v-for="bt in brandNodeDefs" :key="bt.nodeType" class="wf-addmenu-item" @click="addFromMenu(bt.nodeType)">
              <WfIcon class="ami-ic" :name="bt.icon" :size="16" :style="{color: bt.color}" />{{ bt.zh
              }}<span v-if="bt.tip" class="ami-tip">{{ bt.tip }}</span>
            </button>
          </div>
        </span>
        <!-- 响应期不禁用：纯排版动作，relayout 内部会先同步服务端最新板子再重排，不会覆盖 Agent 改动 -->
        <button class="wf-tb-btn" title="自动对齐（按连线关系重新排列卡片位置，不碰内容）" @click="relayout">
          <WfIcon class="wf-tb-ic" name="layout" :size="13" />自动对齐
        </button>
        <!-- 响应期禁用：AI 重整要触发 Agent 对话（sendSingle 对 running 中也直接忽略） -->
        <button
          class="wf-tb-btn"
          :disabled="editLocked || !nodes.length"
          title="AI 重整：助手通读板子重新梳理——并重复卡、撤过期卡、断失效线，必要时重塑结构"
          @click="reorgBoard"
        >
          <WfIcon class="wf-tb-ic" name="sparkle" :size="13" />AI 重整
        </button>
        <button class="wf-tb-btn" title="适应视图" @click="fitAll">
          <WfIcon class="wf-tb-ic" name="fit" :size="13" />视图
        </button>
        <span class="wf-tb-sep" />
        <button class="wf-tb-btn icon" :disabled="editLocked || !history.length" title="撤销 (Ctrl+Z)" @click="undo">
          <WfIcon name="undo" :size="15" />
        </button>
        <button class="wf-tb-btn icon" :disabled="editLocked || !redoStack.length" title="重做 (Ctrl+Y)" @click="redo">
          <WfIcon name="redo" :size="15" />
        </button>
        <span class="wf-tb-sep" />
        <button
          class="wf-tb-btn icon danger"
          :disabled="editLocked || !selectedCount"
          :title="selectedCount ? `删除选中的 ${selectedCount} 项 (Delete)` : '删除选中项 (Delete)'"
          @click="deleteSelected"
        >
          <WfIcon name="x" :size="13" />
        </button>
      </div>

      <!-- 附件上传（节点内点击触发，隐藏 input） -->
      <input ref="fileInputEl" type="file" class="wf-file-input" @change="onFileInputChange" />

      <!-- 人机协作回显面板：「选中节点」实时块 +「你的改动」待办块（Agent 待办） -->
      <Transition name="wf-echo">
        <div v-if="echoPanelVisible" class="wf-echo-panel">
          <!-- 选中节点：实时跟随选择态，发消息时随消息告知 Agent（Agent 会看到这些卡） -->
          <div v-if="selectedNodesEcho.length" class="wf-echo wf-sel">
            <div class="wf-echo-head">
              <span class="wf-echo-ic"><WfIcon name="target" :size="12" /></span>
              <span class="wf-echo-title">选中节点</span>
              <span class="wf-echo-badge sel">Agent 可见</span>
            </div>
            <div class="wf-echo-list">
              <n-tooltip
                v-for="s in selectedNodesEcho"
                :key="s.id"
                trigger="hover"
                placement="right"
                :show-arrow="false"
                :delay="180"
                :style="echoTipStyle"
              >
                <template #trigger>
                  <div class="wf-echo-row op-sel">
                    <span class="wf-echo-op"><WfIcon name="target" :size="11" /></span>
                    <span class="wf-echo-label">{{ s.label }}</span>
                  </div>
                </template>
                <span class="wf-echo-tip">{{ s.label }}</span>
              </n-tooltip>
            </div>
          </div>
          <!-- 你的改动：累积的未回应改动（原样上报给 Agent，Agent 更新板子后自动消解） -->
          <div v-if="echoState !== 'idle' && !echoHidden" class="wf-echo" :class="echoState">
            <div class="wf-echo-head">
            <span class="wf-echo-ic"><WfIcon :name="echoState === 'responded' ? 'check' : 'pencil'" :size="12" /></span>
            <span class="wf-echo-title">{{ echoState === 'responded' ? 'Agent 已更新板子' : '你的改动' }}</span>
            <span v-if="echoState === 'waiting'" class="wf-echo-badge">Agent 待办</span>
            <button class="wf-echo-close" title="收起" @click="dismissEcho">×</button>
          </div>
          <div v-if="echoState === 'waiting'" class="wf-echo-list">
            <div v-for="(g, gi) in pendingDisplay" :key="gi" class="wf-echo-item">
              <div class="wf-echo-row" :class="`op-${g.op}`">
                <span class="wf-echo-op"><WfIcon :name="g.op === 'add' ? 'plus' : g.op === 'edit' ? 'pencil' : 'minus'" :size="11" /></span>
                <!-- 面板内单行省略号截断，hover 弹完整内容（popup teleport 到 body，不被列表滚动区裁剪） -->
                <n-tooltip trigger="hover" placement="right" :show-arrow="false" :delay="180" :style="echoTipStyle">
                  <template #trigger>
                    <span class="wf-echo-label">{{ g.label }}</span>
                  </template>
                  <span class="wf-echo-tip">{{ g.label }}</span>
                </n-tooltip>
                <span v-if="g.count > 1" class="wf-echo-count">×{{ g.count }}</span>
              </div>
              <n-tooltip
                v-for="(d, di) in g.detail.slice(0, 2)"
                :key="di"
                trigger="hover"
                placement="right"
                :show-arrow="false"
                :delay="180"
                :style="echoTipStyle"
              >
                <template #trigger>
                  <div class="wf-echo-detail">{{ d }}</div>
                </template>
                <span class="wf-echo-tip">{{ d }}</span>
              </n-tooltip>
              <!-- 折叠的其余改动：hover 展开列出全部剩余条目 -->
              <n-tooltip v-if="g.detail.length > 2" trigger="hover" placement="right" :show-arrow="false" :delay="180" :style="echoTipStyle">
                <template #trigger>
                  <div class="wf-echo-detail more">还有 {{ g.detail.length - 2 }} 项改动</div>
                </template>
                <div class="wf-echo-tip-lines">
                  <div v-for="(d, di) in g.detail.slice(2)" :key="di" class="wf-echo-tip-line">{{ d }}</div>
                </div>
              </n-tooltip>
            </div>
            <n-tooltip
              v-for="(e, ei) in pendingDiff?.edgesAdded || []"
              :key="`ea-${ei}`"
              trigger="hover"
              placement="right"
              :show-arrow="false"
              :delay="180"
              :style="echoTipStyle"
            >
              <template #trigger>
                <div class="wf-echo-row op-eadd">
                  <span class="wf-echo-op"><WfIcon name="plus" :size="11" /></span>
                  <span class="wf-echo-label">{{ e.label }}</span>
                </div>
              </template>
              <span class="wf-echo-tip">{{ e.label }}</span>
            </n-tooltip>
            <n-tooltip
              v-for="(e, ei) in pendingDiff?.edgesRemoved || []"
              :key="`er-${ei}`"
              trigger="hover"
              placement="right"
              :show-arrow="false"
              :delay="180"
              :style="echoTipStyle"
            >
              <template #trigger>
                <div class="wf-echo-row op-erem">
                  <span class="wf-echo-op"><WfIcon name="minus" :size="11" /></span>
                  <span class="wf-echo-label">{{ e.label }}</span>
                </div>
              </template>
              <span class="wf-echo-tip">{{ e.label }}</span>
            </n-tooltip>
          </div>
          </div>
        </div>
      </Transition>
    </div>

    <!--
      空态引导条（页面级，两种板型共用，挂在画布分支之外）：
      流程编排 = 板上一张卡都没有时给出「第一句话」（Agent 播种骨架后 nodes 非空，自然消失）；
      应用制作 = 应用未发布时给出「想要什么应用」（entryReady 翻 true 后自然消失）。
      点示例句直接发给 Agent，无需手动关闭；与 lock-bar 条件互斥（editLocked 时隐藏）
    -->
    <div
      v-if="!libShown && !fullscreen && !loading && !editLocked && (boardType === 'html' ? !entryReady : !nodes.length)"
      class="wf-hint-bar"
    >
      <p class="wf-hint-title">
        {{ boardType === 'html' ? '把想要的应用描述给对话窗里的 Agent，它会开发并自动出现在这里' : '把想做的事说给对话窗里的 Agent，它会把任务拆解落到板上' }}；或点一句试试：
      </p>
      <div class="wf-hint-examples">
        <button v-for="ex in boardType === 'html' ? HTML_EXAMPLES : BOARD_EXAMPLES" :key="ex" class="wf-hint-example" @click="sendExample(ex)">{{ ex }}</button>
      </div>
    </div>

    <!-- 右键菜单 -->
    <template v-if="ctxMenu">
      <div class="wf-ctx-backdrop" @click="ctxMenu = null" @contextmenu.prevent="ctxMenu = null" />
      <div class="wf-ctx-menu" :style="ctxMenuStyle">
        <template v-if="ctxMenu.nodeId">
          <button class="wf-ctx-item" @click="ctxAction('dup')"><WfIcon class="ctx-ic" name="copy" :size="14" />复制节点</button>
          <div class="wf-ctx-sep" />
          <button class="wf-ctx-item danger" @click="ctxAction('del')"><WfIcon class="ctx-ic" name="trash" :size="14" />删除节点</button>
        </template>
        <template v-else-if="ctxMenu.edgeId">
          <button
            class="wf-ctx-item"
            :title="
              ctxEdgeKind === 'ref'
                ? '流程线：蓝色虚线，代表项目结构（阶段拆解 / 产出归属）'
                : '参照线：浅灰点状直线，代表跨分支的引用 / 对照 / 依据，不参与结构布局'
            "
            @click="ctxAction('toggle-edge-kind')"
          >
            <i class="wf-edge-ic" :class="ctxEdgeKind === 'ref' ? 'k-flow' : 'k-ref'" />
            {{ ctxEdgeKind === 'ref' ? '转为流程线' : '转为参照线' }}
          </button>
          <div class="wf-ctx-sep" />
          <button class="wf-ctx-item danger" @click="ctxAction('del-edge')"><WfIcon class="ctx-ic" name="trash" :size="14" />删除连线</button>
        </template>
        <template v-else>
          <button class="wf-ctx-item" @click="ctxAction('add')"><WfIcon class="ctx-ic tp-text" name="textNode" :size="14" />添加文本节点</button>
          <button class="wf-ctx-item" @click="ctxAction('add-work')"><WfIcon class="ctx-ic tp-work" name="taskNode" :size="14" />添加工作项</button>
          <button class="wf-ctx-item" @click="ctxAction('add-data')"><WfIcon class="ctx-ic tp-data" name="dataNode" :size="14" />添加数据卡</button>
          <button class="wf-ctx-item" @click="ctxAction('add-conclusion')"><WfIcon class="ctx-ic tp-conc" name="conclusionNode" :size="14" />添加结论卡</button>
          <button class="wf-ctx-item" @click="ctxAction('add-start')"><WfIcon class="ctx-ic tp-start" name="startNode" :size="14" />添加开始节点</button>
          <button class="wf-ctx-item" @click="ctxAction('add-end')"><WfIcon class="ctx-ic tp-end" name="endNode" :size="14" />添加结束节点</button>
          <button class="wf-ctx-item" @click="ctxAction('add-file')"><WfIcon class="ctx-ic tp-file" name="fileNode" :size="14" />添加文件节点</button>
          <button class="wf-ctx-item" @click="ctxAction('add-review')"><WfIcon class="ctx-ic tp-review" name="reviewNode" :size="14" />添加人工核查节点</button>
          <button v-for="bt in brandNodeDefs" :key="bt.nodeType" class="wf-ctx-item" @click="ctxAction(`add-brand-${bt.nodeType}`)">
            <WfIcon class="ctx-ic" :name="bt.icon" :size="14" :style="{color: bt.color}" />添加{{ bt.zh }}
          </button>
        </template>
      </div>
    </template>

    <!-- 点击空白 / 拖线到空白处：选择要创建的节点类型 -->
    <template v-if="connectMenu">
      <div class="wf-ctx-backdrop" @click="connectMenu = null" @contextmenu.prevent="connectMenu = null" />
      <div class="wf-ctx-menu wf-connect-menu" :style="connectMenuStyle">
        <div class="wf-connect-head">{{ connectMenu.sourceNode ? '添加节点并连线' : '添加节点' }}</div>
        <button class="wf-ctx-item" @click="connectAdd('textNode')"><WfIcon class="ctx-ic tp-text" name="textNode" :size="14" />文本节点</button>
        <button class="wf-ctx-item" @click="connectAdd('taskNode')"><WfIcon class="ctx-ic tp-work" name="taskNode" :size="14" />工作项</button>
        <button class="wf-ctx-item" @click="connectAdd('dataNode')"><WfIcon class="ctx-ic tp-data" name="dataNode" :size="14" />数据卡</button>
        <button class="wf-ctx-item" @click="connectAdd('conclusionNode')"><WfIcon class="ctx-ic tp-conc" name="conclusionNode" :size="14" />结论卡</button>
        <button class="wf-ctx-item" @click="connectAdd('startNode')"><WfIcon class="ctx-ic tp-start" name="startNode" :size="14" />开始节点</button>
        <button class="wf-ctx-item" @click="connectAdd('endNode')"><WfIcon class="ctx-ic tp-end" name="endNode" :size="14" />结束节点</button>
        <button class="wf-ctx-item" @click="connectAdd('fileNode')"><WfIcon class="ctx-ic tp-file" name="fileNode" :size="14" />文件节点</button>
        <button class="wf-ctx-item" @click="connectAdd('reviewNode')"><WfIcon class="ctx-ic tp-review" name="reviewNode" :size="14" />人工核查</button>
        <button v-for="bt in brandNodeDefs" :key="bt.nodeType" class="wf-ctx-item" @click="connectAdd(bt.nodeType)">
          <WfIcon class="ctx-ic" :name="bt.icon" :size="14" :style="{color: bt.color}" />{{ bt.zh }}
        </button>
      </div>
    </template>

    <!-- 节点全屏预览弹窗（参照附件卡 ⛶ 预览，推广到所有卡型）：弹窗里是该卡的真实节点组件，保持可编辑。
         弹窗必须是独立子组件：其内部 VueFlow 流程状态在自己的子树里创建并 provide，与主画布状态天然隔离
         （绝不能把两个 useVueFlow 放在本组件里——provide 会互相覆盖，弹窗播种单卡时主画布会被一起劫持） -->
    <WfNodePreview
      v-if="previewNodeId && previewNode"
      :node="previewNode"
      :label="previewNodeLabelText"
      :node-types="nodeTypes"
      @close="closeNodePreview"
    />
  </div>
</template>

<style scoped>
/* 壳根：设计令牌与 .wf-page 同源——QA 伴侣面板里没有 .wf-page，令牌必须自带一份（画板样式大量消费） */
.wf-shell {
  --bg: #f5f7fb;
  --surface: rgba(255, 255, 255, 0.42);
  --surface-strong: rgba(255, 255, 255, 0.66);
  --highlight: rgba(255, 255, 255, 0.9);
  --border: rgba(30, 64, 175, 0.1);
  --border-strong: rgba(30, 64, 175, 0.18);
  --border-glow: rgba(30, 64, 175, 0.28);
  --ink: #0f172a;
  --ink-soft: #334155;
  --ink-mute: #64748b;
  --ink-faint: #94a3b8;
  --c-blue: #1e40af;
  --c-blue-2: #2563eb;
  --c-sky: #0ea5e9;
  --c-cyan: #0891b2;
  --aurora: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px -12px rgba(30, 64, 175, 0.16);
  --shadow-md: 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px rgba(30, 64, 175, 0.22);
  --shadow-glow: 0 8px 32px -10px rgba(30, 64, 175, 0.45);
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg);
  overflow: hidden;
  position: relative;
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
  color: var(--ink);
  -webkit-font-smoothing: antialiased;
}
/* 全屏 = 固定覆盖层占满视口：专页宿主本就独占视口无差别，QA 伴侣面板宿主里板被限制在
   右侧面板列，必须提升为 fixed 覆盖层才能真正铺满全屏（浏览器 Fullscreen API 由脚本另行发起） */
.wf-shell--fs {
  position: fixed;
  inset: 0;
  z-index: 1500;
  flex: none;
}
.wf-topbar {
  position: relative;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  height: 56px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.5);
  border-bottom: 1px solid var(--border);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  box-shadow: inset 0 1px 0 var(--highlight), 0 1px 2px rgba(15, 23, 42, 0.03);
}
.wf-topbar--lift {
  z-index: 130;
}
.wf-back {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  border-radius: 11px;
  cursor: pointer;
  color: var(--ink-soft);
  box-shadow: inset 0 1px 0 var(--highlight), inset 0 0 0 1px rgba(255, 255, 255, 0.4);
  transition: all 0.18s;
}
.wf-back:hover {
  color: var(--c-blue);
  border-color: var(--border-glow);
  box-shadow: inset 0 1px 0 var(--highlight), var(--shadow-glow);
  transform: translateY(-1px);
}
.wf-title-input {
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--ink);
  outline: none;
  flex: 1;
  min-width: 0;
  padding: 4px 0;
  border-bottom: 1px solid transparent;
  transition: border-color 0.15s;
}
.wf-title-input:focus {
  border-bottom-color: var(--border-glow);
}
.wf-title-static {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--ink);
  flex: 1;
}
.wf-save-hint {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 600;
  color: #10b981;
  white-space: nowrap;
}
.wf-topbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.wf-fs-exit {
  position: absolute;
  top: 12px;
  right: 16px;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  border-radius: 11px;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-soft);
  cursor: pointer;
  opacity: 0.45;
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  box-shadow: inset 0 1px 0 var(--highlight), inset 0 0 0 1px rgba(255, 255, 255, 0.4);
  transition: all 0.18s;
}
.wf-fs-exit:hover {
  opacity: 1;
  color: var(--c-blue);
  border-color: var(--border-glow);
  box-shadow: inset 0 1px 0 var(--highlight), var(--shadow-glow);
  transform: translateY(-1px);
}
.wf-chat-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  border-radius: 11px;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-soft);
  cursor: pointer;
  box-shadow: inset 0 1px 0 var(--highlight), inset 0 0 0 1px rgba(255, 255, 255, 0.4);
  transition: all 0.18s;
}
.wf-chat-btn:hover {
  color: var(--c-blue);
  border-color: var(--border-glow);
  box-shadow: inset 0 1px 0 var(--highlight), var(--shadow-glow);
  transform: translateY(-1px);
}
.wf-chat-btn.active {
  color: #fff;
  background: var(--aurora);
  border-color: transparent;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), 0 4px 14px -2px rgba(30, 64, 175, 0.45);
}
.wf-canvas {
  flex: 1;
  position: relative;
  z-index: 1;
}
.wf-dots {
  position: absolute;
  left: -50%;
  top: -50%;
  width: 200%;
  height: 200%;
  background-image: radial-gradient(rgba(30, 64, 175, 0.09) 1px, transparent 1.1px);
  pointer-events: none;
  will-change: transform;
}
.wf-canvas-html {
  overflow: hidden;
  background: #fff;
}
.wf-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.wf-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #94a3b8;
  animation: wf-pulse 1.2s ease-in-out infinite;
}
.wf-dot:nth-child(2) { animation-delay: 0.15s; }
.wf-dot:nth-child(3) { animation-delay: 0.3s; }
.wf-file-input {
  display: none;
}
.wf-flow-pill.tp-startNode {
  border-color: rgba(30, 64, 175, 0.28);
  background: rgba(30, 64, 175, 0.05);
  color: #1e40af;
}
.wf-flow-pill.tp-startNode .wf-pill-dot {
  background: linear-gradient(110deg, #1e40af, #0ea5e9);
}
.wf-flow-pill.tp-endNode {
  border-color: rgba(16, 185, 129, 0.3);
  background: rgba(16, 185, 129, 0.05);
  color: #047857;
}
.wf-flow-pill.tp-endNode .wf-pill-dot { background: #10b981; }
.wf-flow-pill.tp-fileNode {
  border-color: rgba(124, 58, 237, 0.3);
  background: rgba(124, 58, 237, 0.05);
  color: #6d28d9;
}
.wf-flow-pill.tp-fileNode .wf-pill-dot { background: #7c3aed; }
.wf-flow-pill.tp-reviewNode {
  border-color: rgba(217, 119, 6, 0.32);
  background: rgba(217, 119, 6, 0.06);
  color: #b45309;
}
.wf-flow-pill.tp-reviewNode .wf-pill-dot { background: #d97706; }
.wf-flow-pill.tp-taskNode {
  border-color: rgba(37, 99, 235, 0.3);
  background: rgba(37, 99, 235, 0.05);
  color: #1d4ed8;
}
.wf-flow-pill.tp-taskNode .wf-pill-dot { background: #2563eb; }
.wf-flow-pill.tp-dataNode {
  border-color: rgba(8, 145, 178, 0.32);
  background: rgba(8, 145, 178, 0.05);
  color: #155e75;
}
.wf-flow-pill.tp-dataNode .wf-pill-dot { background: #0891b2; }
.wf-flow-pill.tp-conclusionNode {
  border-color: rgba(30, 58, 138, 0.4);
  background: #0f172a;
  color: #e2e8f0;
}
.wf-flow-pill.tp-conclusionNode .wf-pill-dot { background: #38bdf8; }
.wf-flow-pill.tp-segNode {
  border-color: rgba(14, 116, 144, 0.32);
  background: rgba(14, 116, 144, 0.05);
  color: #155e75;
}
.wf-flow-pill.tp-segNode .wf-pill-dot { background: #0e7490; }
.wf-toolbar {
  position: absolute;
  left: 50%;
  bottom: 20px;
  transform: translateX(-50%);
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 6px 8px;
  /* 叠在画布上：半透明底会让画布每帧重绘无法被遮挡裁剪剔除——不透明底（2026-08-17 掉帧整治） */
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 12px 32px -12px rgba(30, 64, 175, 0.28);
}
.wf-tb-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 7px 12px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 10px;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-soft);
  cursor: pointer;
  transition: all 0.18s;
  white-space: nowrap;
}
.wf-tb-btn:hover:not(:disabled) {
  color: var(--c-blue);
  background: rgba(255, 255, 255, 0.8);
  border-color: rgba(30, 64, 175, 0.15);
  box-shadow: inset 0 1px 0 var(--highlight), 0 4px 12px -4px rgba(30, 64, 175, 0.25);
  transform: translateY(-1px);
}
.wf-tb-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.wf-tb-btn.icon {
  padding: 7px 10px;
  font-size: 15px;
  line-height: 1;
}
.wf-tb-btn.primary {
  background: var(--aurora);
  border-color: transparent;
  color: #fff;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), 0 4px 14px -2px rgba(30, 64, 175, 0.45);
}
.wf-tb-btn.primary:hover:not(:disabled) {
  color: #fff;
  background: var(--aurora);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.45), 0 6px 20px -2px rgba(30, 64, 175, 0.55);
}
.wf-tb-btn.danger:hover:not(:disabled) {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.07);
  border-color: rgba(239, 68, 68, 0.2);
}
.wf-tb-ic {
  display: flex;
  align-items: center;
}
.wf-tb-sep {
  width: 1px;
  height: 18px;
  background: rgba(30, 64, 175, 0.12);
  margin: 0 4px;
  flex-shrink: 0;
}
.wf-mode {
  display: flex;
  gap: 2px;
  padding: 2px;
  background: rgba(30, 64, 175, 0.06);
  border: 1px solid rgba(30, 64, 175, 0.08);
  border-radius: 11px;
}
.wf-mode-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 11px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-soft);
  cursor: pointer;
  white-space: nowrap;
  transition: color 0.16s, background 0.16s, box-shadow 0.16s;
}
.wf-mode-btn:hover:not(.active) {
  color: var(--c-blue);
}
.wf-mode-btn.active {
  background: var(--aurora);
  color: #fff;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), 0 3px 10px -2px rgba(30, 64, 175, 0.5);
}
.wf-mode-ic {
  display: flex;
  align-items: center;
}
.wf-addmenu-wrap {
  position: relative;
  z-index: 12;
  display: inline-flex;
}
.wf-tb-caret {
  display: inline-block;
  margin-left: 2px;
  border-left: 4px solid transparent;
  border-right: 4px solid transparent;
  border-top: 5px solid currentColor;
  opacity: 0.75;
  transition: transform 0.18s;
}
.wf-tb-btn.open .wf-tb-caret {
  transform: rotate(180deg);
}
.wf-addmenu {
  position: absolute;
  bottom: calc(100% + 10px);
  left: 50%;
  min-width: 178px;
  padding: 6px;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    0 12px 32px -12px rgba(30, 64, 175, 0.35);
  animation: wf-pop 0.16s ease-out;
  transform: translateX(-50%);
}
.wf-addmenu-item {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-mute);
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.14s, color 0.14s;
}
.wf-addmenu-item:hover {
  background: rgba(30, 64, 175, 0.07);
  color: var(--c-blue);
}
.ami-ic {
  flex-shrink: 0;
}
.ami-tip {
  margin-left: auto;
  padding-left: 12px;
  font-size: 10.5px;
  font-weight: 400;
  color: var(--ink-faint);
}
.wf-echo-panel {
  position: absolute;
  top: 14px;
  left: 14px;
  z-index: 15;
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 254px;
}
.wf-echo {
  width: 100%;
  /* 叠在画布上，同工具栏：不透明底保遮挡裁剪（2026-08-17 掉帧整治） */
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: inset 0 1px 0 var(--highlight), var(--shadow-md);
  overflow: hidden;
}
.wf-echo.responded {
  border-color: rgba(16, 185, 129, 0.32);
}
.wf-echo-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 10px 9px 10px 12px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}
.wf-echo.responded .wf-echo-head {
  border-bottom-color: transparent;
}
.wf-echo-ic {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  border-radius: 7px;
  font-size: 11px;
  background: rgba(37, 99, 235, 0.1);
  color: var(--c-blue-2);
}
.wf-echo.responded .wf-echo-ic {
  background: rgba(16, 185, 129, 0.12);
  color: #059669;
}
.wf-echo-title {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--ink-soft);
}
.wf-echo-badge {
  flex-shrink: 0;
  padding: 2.5px 7px;
  border-radius: 999px;
  border: 1px solid rgba(217, 119, 6, 0.28);
  background: rgba(217, 119, 6, 0.09);
  font-size: 10px;
  font-weight: 600;
  color: #b45309;
  white-space: nowrap;
}
.wf-echo-badge.sel {
  border-color: rgba(37, 99, 235, 0.28);
  background: rgba(37, 99, 235, 0.09);
  color: var(--c-blue-2);
}
.wf-echo-row.op-sel .wf-echo-op {
  color: var(--c-blue-2);
}
.wf-echo-close {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border: none;
  background: transparent;
  border-radius: 7px;
  font-size: 14px;
  line-height: 1;
  color: var(--ink-faint);
  cursor: pointer;
  transition: all 0.15s;
}
.wf-echo-close:hover {
  background: rgba(30, 64, 175, 0.08);
  color: var(--c-blue);
}
.wf-echo-list {
  max-height: 260px;
  overflow-y: auto;
  padding: 6px;
  scrollbar-width: thin;
}
.wf-echo-item + .wf-echo-item {
  margin-top: 2px;
}
.wf-echo-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 7px;
  border-radius: 8px;
  font-size: 12px;
  color: var(--ink-soft);
  transition: background 0.12s;
}
.wf-echo-row:hover {
  background: rgba(30, 64, 175, 0.05);
}
.wf-echo-op {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 13px;
}
.wf-echo-row.op-add .wf-echo-op { color: #059669; }
.wf-echo-row.op-edit .wf-echo-op { color: var(--c-blue-2); }
.wf-echo-row.op-remove .wf-echo-op { color: #dc2626; }
.wf-echo-row.op-eadd .wf-echo-op { color: var(--c-blue-2); }
.wf-echo-row.op-erem .wf-echo-op { color: #dc2626; }
.wf-echo-detail {
  margin: 0 7px 2px 28px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  line-height: 1.55;
  color: var(--ink-mute);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wf-echo-detail.more {
  color: var(--ink-faint);
}
.wf-echo-detail:hover {
  color: var(--ink-soft);
}
.wf-echo-detail.more:hover {
  color: var(--ink-mute);
}
.wf-echo-tip {
  word-break: break-all;
  white-space: pre-wrap;
}
.wf-echo-tip-lines {
  display: flex;
  flex-direction: column;
  gap: 5px;
  max-height: 240px;
  overflow-y: auto;
  scrollbar-width: thin;
}
.wf-echo-tip-line {
  padding-left: 9px;
  border-left: 2px solid rgba(56, 189, 248, 0.5);
  word-break: break-all;
}
.wf-echo-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wf-echo-count {
  flex-shrink: 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--ink-faint);
}
.wf-echo-enter-active { transition: all 0.22s cubic-bezier(0.16, 1, 0.3, 1); }
.wf-echo-leave-active { transition: all 0.16s ease-in; }
.wf-echo-enter-from, .wf-echo-leave-to { opacity: 0; transform: translateY(-8px); }
.wf-lock-bar {
  position: absolute;
  top: 14px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 16;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 13px;
  border: 1px solid rgba(37, 99, 235, 0.22);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: inset 0 1px 0 var(--highlight), 0 4px 14px rgba(30, 64, 175, 0.1);
  font-size: 12px;
  font-weight: 600;
  color: var(--c-blue-2);
  white-space: nowrap;
  pointer-events: none;
}
.wf-lock-ic {
  display: flex;
  align-items: center;
}
.wf-lock-fade-enter-active { transition: all 0.2s ease-out; }
.wf-lock-fade-leave-active { transition: all 0.16s ease-in; }
.wf-lock-fade-enter-from, .wf-lock-fade-leave-to { opacity: 0; transform: translateX(-50%) translateY(-6px); }
.wf-hint-bar {
  position: absolute;
  top: 70px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 15;
  max-width: min(680px, calc(100% - 32px));
  padding: 10px 16px;
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-radius: 13px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: inset 0 1px 0 var(--highlight), 0 8px 24px -8px rgba(30, 64, 175, 0.25);
  text-align: center;
}
.wf-hint-title {
  margin: 0 0 8px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-soft);
}
.wf-hint-examples {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 6px;
}
.wf-hint-example {
  padding: 5px 12px;
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.8);
  font-size: 11.5px;
  font-weight: 500;
  color: var(--c-blue);
  cursor: pointer;
  transition: all 0.18s;
}
.wf-hint-example:hover {
  border-color: rgba(30, 64, 175, 0.45);
  background: rgba(30, 64, 175, 0.06);
  box-shadow: 0 2px 10px -4px rgba(30, 64, 175, 0.4);
}
.wf-boardmenu-wrap {
  position: relative;
}
.wf-board-caret {
  transition: transform 0.18s;
}
.wf-board-caret.up {
  transform: rotate(180deg);
}
.wf-board-menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 1001;
  width: 292px;
  max-height: 420px;
  display: flex;
  flex-direction: column;
  /* 接近不透明：菜单会盖在会话悬浮窗上，玻璃太透时底下文字透上来会干扰阅读 */
  background: rgba(255, 255, 255, 0.97);
  backdrop-filter: blur(32px) saturate(180%);
  -webkit-backdrop-filter: blur(32px) saturate(180%);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 13px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 24px 64px -20px rgba(30, 64, 175, 0.35);
  overflow: hidden;
  transform-origin: top right;
}
.wf-sharemenu-wrap {
  position: relative;
}
.wf-share-panel {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 1001;
  width: 320px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.97);
  backdrop-filter: blur(32px) saturate(180%);
  -webkit-backdrop-filter: blur(32px) saturate(180%);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 13px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 24px 64px -20px rgba(30, 64, 175, 0.35);
  transform-origin: top right;
}
.wf-share-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.wf-share-title {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
}
.wf-share-switch {
  position: relative;
  width: 38px;
  height: 21px;
  flex-shrink: 0;
  border: none;
  border-radius: 999px;
  background: #cbd5e1;
  cursor: pointer;
  transition: background 0.18s;
  padding: 0;
}
.wf-share-switch.on {
  background: #2563eb;
}
.wf-share-switch:disabled {
  opacity: 0.6;
  cursor: wait;
}
.wf-share-switch .knob {
  position: absolute;
  top: 2.5px;
  left: 2.5px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.25);
  transition: transform 0.18s;
}
.wf-share-switch.on .knob {
  transform: translateX(17px);
}
/* 打开方式二选一（仅登录用户 / 免登录）：分段按钮，选中态蓝染，与面板整体同气质 */
.wf-share-modes {
  display: flex;
  gap: 6px;
  margin-top: 10px;
}
.wf-share-mode {
  flex: 1;
  padding: 6px 8px;
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 8px;
  background: rgba(248, 250, 252, 0.9);
  color: #64748b;
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, color 0.15s;
}
.wf-share-mode:hover {
  border-color: rgba(30, 64, 175, 0.3);
  color: #334155;
}
.wf-share-mode.on {
  border-color: rgba(37, 99, 235, 0.45);
  background: rgba(37, 99, 235, 0.08);
  color: #1d4ed8;
  font-weight: 600;
}
.wf-share-mode:disabled {
  opacity: 0.6;
  cursor: wait;
}
.wf-share-linkrow {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
}
.wf-share-link {
  flex: 1;
  min-width: 0;
  padding: 7px 10px;
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 9px;
  background: rgba(248, 250, 252, 0.9);
  font-family: inherit;
  font-size: 12px;
  color: #334155;
  outline: none;
}
.wf-share-link:focus {
  border-color: rgba(30, 64, 175, 0.3);
}
.wf-share-copy {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  padding: 7px 12px;
  border: none;
  border-radius: 9px;
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  color: #fff;
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
  transition: filter 0.15s;
}
.wf-share-copy:hover {
  filter: brightness(1.06);
}
.wf-share-note {
  margin: 10px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: #64748b;
}
.wf-board-search {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 8px 8px 4px;
  padding: 7px 11px;
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.7);
  color: #94a3b8;
  flex-shrink: 0;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.wf-board-search:focus-within {
  border-color: rgba(30, 64, 175, 0.3);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.08);
}
.wf-board-search input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
  font-size: 12.5px;
  color: #334155;
}
.wf-board-search input::placeholder {
  color: #94a3b8;
}
.wf-board-new {
  padding: 11px 14px;
  border: none;
  background: transparent;
  text-align: left;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  color: #1e40af;
  cursor: pointer;
  transition: background 0.15s;
  flex-shrink: 0;
}
.wf-board-new:hover { background: rgba(30, 64, 175, 0.07); }
.wf-board-divider {
  height: 1px;
  background: rgba(30, 64, 175, 0.08);
  flex-shrink: 0;
}
.wf-board-list {
  overflow-y: auto;
  padding: 6px;
}
.wf-board-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 9px 10px;
  border: none;
  background: transparent;
  border-radius: 8px;
  text-align: left;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.15s;
}
.wf-board-item:hover { background: rgba(30, 64, 175, 0.07); }
.wf-board-item.cur { background: rgba(30, 64, 175, 0.09); }
.wf-board-item-name {
  font-size: 13px;
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wf-board-item-type {
  display: inline-block;
  margin-right: 6px;
  padding: 0 5px;
  border-radius: 5px;
  background: rgba(30, 64, 175, 0.08);
  color: #1e40af;
  font-family: 'JetBrains Mono', monospace;
  font-style: normal;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.04em;
  vertical-align: 1px;
}
/* 板型简写：应=应用制作（蓝）/ 流=流程编排（中性灰） */
.wf-board-item-type.t-board {
  background: rgba(100, 116, 139, 0.12);
  color: #64748b;
}
.wf-board-item.cur .wf-board-item-name { color: #1e40af; font-weight: 600; }
.wf-board-item-meta {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  color: #94a3b8;
}
.wf-board-empty {
  padding: 18px;
  text-align: center;
  font-size: 12px;
  color: #94a3b8;
}
.wf-vermenu-wrap {
  position: relative;
}
.wf-ver-menu {
  width: 268px;
}
.wf-ver-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 12px 14px 10px;
  font-size: 13px;
  font-weight: 600;
  color: #334155;
  flex-shrink: 0;
}
.wf-ver-sub {
  font-size: 10.5px;
  font-weight: 400;
  color: #94a3b8;
}
.wf-ver-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 10px;
  border-radius: 8px;
  transition: background 0.15s;
}
.wf-ver-item:hover { background: rgba(30, 64, 175, 0.07); }
.wf-ver-item.cur { background: rgba(30, 64, 175, 0.09); }
.wf-ver-item-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.wf-ver-item-name {
  font-size: 13px;
  color: #334155;
}
.wf-ver-item.cur .wf-ver-item-name { color: #1e40af; font-weight: 600; }
.wf-ver-cur {
  display: inline-block;
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 5px;
  background: rgba(5, 150, 105, 0.1);
  color: #059669;
  font-style: normal;
  font-size: 9.5px;
  font-weight: 700;
  vertical-align: 1px;
}
.wf-ver-rollback {
  padding: 2px 10px;
  border: 1px solid rgba(217, 119, 6, 0.35);
  border-radius: 7px;
  background: rgba(217, 119, 6, 0.06);
  color: #b45309;
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.wf-ver-rollback:hover:not(:disabled) { background: rgba(217, 119, 6, 0.14); border-color: rgba(217, 119, 6, 0.5); }
.wf-ver-rollback:disabled { opacity: 0.5; cursor: default; }
/* 应用制作的「切换」钮：非破坏性操作，用品牌蓝而非回滚的警示橙 */
.wf-ver-switch {
  border-color: rgba(30, 64, 175, 0.3);
  background: rgba(30, 64, 175, 0.05);
  color: #1e40af;
}
.wf-ver-switch:hover:not(:disabled) { background: rgba(30, 64, 175, 0.12); border-color: rgba(30, 64, 175, 0.5); }
/* 应用制作版本行可点击直切（节点板无此态，走回滚确认弹窗） */
.wf-ver-item.clickable { cursor: pointer; }
/* 顶栏「发布」钮：发布中禁用态反馈 */
.wf-publish-btn:disabled { opacity: 0.6; cursor: default; }
.wf-ctx-backdrop {
  position: fixed;
  inset: 0;
  z-index: 300;
}
.wf-ctx-menu {
  position: fixed;
  z-index: 301;
  min-width: 172px;
  padding: 6px;
  display: flex;
  flex-direction: column;
  background: rgba(255, 255, 255, 0.82);
  backdrop-filter: blur(32px) saturate(180%);
  -webkit-backdrop-filter: blur(32px) saturate(180%);
  border: 1px solid var(--border);
  border-radius: 13px;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    0 1px 2px rgba(15, 23, 42, 0.05),
    0 24px 64px -20px rgba(30, 64, 175, 0.35);
  animation: wf-ctx-in 0.14s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes wf-ctx-in {
  from { opacity: 0; transform: scale(0.96) translateY(-2px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}
.wf-ctx-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: none;
  background: transparent;
  border-radius: 8px;
  text-align: left;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-soft);
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
}
.wf-ctx-item:hover {
  background: rgba(30, 64, 175, 0.07);
  color: var(--c-blue);
}
.wf-ctx-item.danger:hover {
  background: rgba(239, 68, 68, 0.08);
  color: #ef4444;
}
.wf-ctx-sep {
  height: 1px;
  background: rgba(30, 64, 175, 0.08);
  margin: 4px 6px;
}
.wf-edge-ic {
  width: 18px;
  height: 0;
  flex-shrink: 0;
}
.wf-edge-ic.k-ref {
  border-top: 2px dotted #94a3b8;
}
.wf-edge-ic.k-flow {
  border-top: 2px dashed #2563eb;
}
.wf-connect-menu {
  min-width: 186px;
}
.wf-connect-head {
  padding: 7px 12px 5px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: var(--ink-faint);
  text-transform: uppercase;
}
.tp-text { color: #64748b; }
.tp-work { color: #2563eb; }
.tp-data { color: #0891b2; }
.tp-conc { color: #1e3a8a; }
.tp-file { color: #7c3aed; }
.tp-review { color: #d97706; }
.tp-start { color: #2563eb; }
.tp-end { color: #0e7490; }

/* ── 切割补丁：共享/漏归规则 ── */
@keyframes wf-pulse {
  0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
  40% { opacity: 1; transform: scale(1); }
}
@keyframes wf-pop {
  from {
    opacity: 0;
    transform: translateX(-50%) translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
}
/* 菜单项内的节点类型图标：与画布卡片 / 列表预览胶囊同一套色板（文本灰 / 工作项蓝 / 数据青 / 结论靛 / 附件紫 / 核查琥珀 / 结束薄荷） */
.ctx-ic {
  flex-shrink: 0;
}
</style>

<style>
.vue-flow__edge.animated path {
  animation: none;
}
.vue-flow__node {
  will-change: transform;
}
.vue-flow__edges {
  will-change: transform;
}
.wf-menu-enter-active { transition: all 0.16s cubic-bezier(0.16, 1, 0.3, 1); }
.wf-menu-leave-active { transition: all 0.12s ease-in; }
.wf-menu-enter-from, .wf-menu-leave-to { opacity: 0; transform: scale(0.96); }
.wf-canvas .vue-flow {
  background: transparent;
}
.wf-canvas .vue-flow__connection-path {
  stroke: #2563eb;
  stroke-width: 2;
  stroke-dasharray: 5 3;
}
.wf-canvas .vue-flow__edge.selected .vue-flow__edge-path {
  stroke: #1e40af;
  stroke-width: 2.5;
}
.wf-canvas .vue-flow__controls {
  border-radius: 12px;
  border: 1px solid rgba(30, 64, 175, 0.1);
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 8px 24px -8px rgba(30, 64, 175, 0.25);
  overflow: hidden;
}
.wf-canvas .vue-flow__minimap {
  padding: 6px;
  border-radius: 14px;
  border: 1px solid rgba(30, 64, 175, 0.12);
  /* 白底 + 与主画布同款点阵底纹，缩略图像一块"小画布"而非空白卡片 */
  background:
    radial-gradient(rgba(30, 64, 175, 0.07) 1px, transparent 1.2px) 0 0 / 9px 9px,
    rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 8px 24px -8px rgba(30, 64, 175, 0.25);
  overflow: hidden;
  touch-action: none; /* 触屏上指针拖拽不被浏览器手势打断 */
}
.wf-canvas .vue-flow__minimap svg {
  display: block;
}
.wf-canvas .vue-flow__node {
  will-change: transform;
  contain: layout style;
}
.wf-canvas .vue-flow__minimap-mask {
  vector-effect: non-scaling-stroke;
}
.wf-canvas .wf-mini-node {
  shape-rendering: geometricPrecision;
  vector-effect: non-scaling-stroke;
  stroke-width: 1px;
  fill-opacity: 0.95;
}
.wf-canvas .wf-mini-node.selected {
  stroke: #2563eb;
  stroke-width: 1.5px;
}
.wf-canvas .wf-mini-edge {
  fill: none;
  shape-rendering: geometricPrecision;
  vector-effect: non-scaling-stroke;
  stroke-width: 1px;
  pointer-events: none;
}
.wf-canvas .wf-mini-edge.flow {
  stroke: rgba(37, 99, 235, 0.5);
  stroke-dasharray: 4 3;
}
.wf-canvas .wf-mini-edge.ref {
  stroke: rgba(148, 163, 184, 0.85);
  stroke-dasharray: 0.1 4;
  stroke-linecap: round;
}
.wf-canvas .vue-flow__controls-button {
  background: transparent;
  border-bottom: 1px solid rgba(30, 64, 175, 0.06);
  fill: #475569;
  transition: background 0.15s, fill 0.15s;
}
.wf-canvas .vue-flow__controls-button:hover {
  background: rgba(30, 64, 175, 0.07);
  fill: #1e40af;
}
.wf-canvas .vue-flow__selection {
  background: rgba(37, 99, 235, 0.08);
  border: 1.5px dashed #2563eb;
  border-radius: 4px;
}
.wf-canvas .vue-flow__pane.selection {
  cursor: default;
}
</style>
