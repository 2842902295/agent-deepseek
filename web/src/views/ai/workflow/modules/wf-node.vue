<script setup lang="ts">
import {computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref} from 'vue';
import {Handle, Position} from '@vue-flow/core';
import {getServiceBaseURL} from '@/utils/service';
import {extractExt, fileExt, fileExtGroup, formatFileSize, isCsvFile, isHtmlFile, isMarkdownFile, isOfficePreviewable} from '@/utils/attachment';
import {marked} from 'marked';
import HtmlRender from '@/views/ai/qa-glass/components/html-render.vue';
import type {BoardMarks} from '@/service/api/ai';
import {caretFromPoint} from './caret-at-point';
import WfIcon from './wf-icon.vue';
import WfNodeBadge from './wf-node-badge.vue';

const props = defineProps<{
  id: string;
  type?: string;
  data: Record<string, any>;
}>();

/** 父页面（index.vue）通过 provide 注入的编辑 / 上传接口 */
const api = inject<{
  beginEdit: () => void;
  updateData: (id: string, patch: Record<string, any>) => void;
  editSignal: Ref<{id: string; tick: number} | null>;
  /** 响应期编辑锁：Agent 回应中为 true，各编辑入口据此短路返回 */
  editLocked: Ref<boolean>;
  uploadingNodeId: Ref<string | null>;
  requestNodeUpload: (nodeId: string) => void;
  uploadOneToNode: (nodeId: string, file: File) => Promise<void>;
  /** 附件全屏预览：打开页面级共享弹层（workflow/index.vue 承载） */
  previewAttachment: (att: {name: string; src: string}) => void;
  /** 节点卡全屏预览：弹窗挂该卡的真实节点组件，保持可编辑（workflow/index.vue 实现） */
  previewNode?: (nodeId: string) => void;
  /** 人工核查作答后通知页面：立即落库并自动向对话窗发消息触发 Agent 响应（workflow/index.vue 实现） */
  notifyReviewAnswered?: (nodeId: string, question: string, answer: string) => void;
}>('wfNodeApi');

// 节点形态：start（开始）/ end（结束）/ file（附件）/ review（人工核查）/ task（工作项）/ data（数据）/ conclusion（结论）/
// seg（分镜段，品牌板型：分镜板，见 modules/brand-boards.ts）/ text（叙述，默认最轻）
const variant = computed<'start' | 'end' | 'file' | 'review' | 'task' | 'data' | 'conclusion' | 'seg' | 'text'>(() => {
  if (props.type === 'startNode') return 'start';
  if (props.type === 'endNode') return 'end';
  if (props.type === 'fileNode') return 'file';
  if (props.type === 'reviewNode') return 'review';
  if (props.type === 'taskNode') return 'task';
  if (props.type === 'dataNode') return 'data';
  if (props.type === 'conclusionNode') return 'conclusion';
  if (props.type === 'segNode') return 'seg';
  return 'text';
});

// handle 旁的「＋」：按住拖动 = 从该 handle 拖出连线（n8n 式出线，松开到目标节点即连线、松开在空白弹类型菜单）；
// 原地按下松开（点击）= 弹菜单并自动避让摆放。实现：把 mousedown 转发给同侧 handle，
// 让 Vue Flow 自己的 handlePointerDown 跑完整流程（连线跟手 / autoPan / connect-start/end 事件），不维护任何私有拖拽状态
function onAddMouseDown(e: MouseEvent, handleType: 'source' | 'target') {
  if (api?.editLocked?.value) return; // 响应期锁编辑：卡内「＋」拖线 / 加节点停用
  if (e.button !== 0) return;
  const nodeRoot = (e.currentTarget as HTMLElement).closest('.vue-flow__node');
  // Handle 渲染 data-handlepos = Position 字符串（right=source / left=target）；附件节点 handle 在内层 .file 里，统一从节点外壳查
  const handle = nodeRoot?.querySelector<HTMLElement>(`.vue-flow__handle[data-handlepos="${handleType === 'source' ? 'right' : 'left'}"]`);
  if (!handle) return;
  e.preventDefault();
  handle.dispatchEvent(
    new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window, button: 0, buttons: 1, clientX: e.clientX, clientY: e.clientY})
  );
}

/** 任务行「＋」：同 onAddMouseDown——把 mousedown 转发给该行自己的 handle（data-handleid=sid），
 *  从这条任务拖出连线（edge 带 sourceHandle，起点对齐该行） */
function onSubAddMouseDown(e: MouseEvent, sid: string) {
  if (api?.editLocked?.value) return;
  if (e.button !== 0) return;
  const nodeRoot = (e.currentTarget as HTMLElement).closest('.vue-flow__node');
  const handle = nodeRoot?.querySelector<HTMLElement>(`.vue-flow__handle[data-handleid="${sid}"]`);
  if (!handle) return;
  handle.dispatchEvent(
    new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window, button: 0, buttons: 1, clientX: e.clientX, clientY: e.clientY})
  );
}

// ── 文本节点：默认拖拽；单击落焦点、再点一下进入编辑，双击开全屏预览 ──────
const textEditing = ref(false);
const taEl = ref<HTMLTextAreaElement | null>(null);
let origText = '';

function autoResize() {
  const el = taEl.value;
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight}px`;
}
/**
 * 新加入的节点在被 Vue Flow 测量完成前是 visibility:hidden（dist 里 `visibility: isInit ? 'visible' : 'hidden'`），
 * 对隐藏元素调 focus() 会被浏览器静默忽略 → 等可见后再聚焦，编辑态已取消则放弃。
 * at = 入口点击的视口坐标：光标直接落在用户点的那个字上（点哪编辑哪，不再粗暴全选）；
 * 缺省（程序化进入，如新建节点自动开打）光标置于文末
 */
function focusWhenReady(el: HTMLTextAreaElement | HTMLInputElement, editing: () => boolean, at?: {x: number; y: number} | null, tries = 12) {
  if (!editing()) return;
  const cs = getComputedStyle(el);
  if (cs.visibility === 'hidden' || cs.display === 'none') {
    if (tries > 0) requestAnimationFrame(() => focusWhenReady(el, editing, at, tries - 1));
    return;
  }
  el.focus();
  try {
    const hit = at ? caretFromPoint(el, at.x, at.y) : null;
    if (hit) {
      el.setSelectionRange(hit.index, hit.index);
      // 长文编辑框有限高内滚：把光标滚进可视区（focus 不会滚到指定位置）
      if (el instanceof HTMLTextAreaElement && (hit.top < el.scrollTop + 4 || hit.top > el.scrollTop + el.clientHeight - 24)) {
        el.scrollTop = Math.max(0, hit.top - el.clientHeight / 2);
      }
    } else {
      el.setSelectionRange(el.value.length, el.value.length);
    }
  } catch {
    /* 个别 input 类型不支持 setSelectionRange，忽略 */
  }
}
function startTextEdit(at?: {x: number; y: number} | null) {
  if (api?.editLocked?.value) return; // 响应期锁编辑：双击进编辑停用
  if (textEditing.value) return;
  origText = props.data?.text || '';
  textEditing.value = true;
  nextTick(() => {
    const el = taEl.value;
    if (!el) return;
    autoResize();
    focusWhenReady(el, () => textEditing.value, at);
  });
}
function commitTextEdit() {
  textEditing.value = false;
}
function cancelTextEdit() {
  textEditing.value = false;
  if ((props.data?.text || '') !== origText) api?.updateData(props.id, {text: origText});
}
function onTextInput(e: Event) {
  api?.beginEdit();
  api?.updateData(props.id, {text: (e.target as HTMLTextAreaElement).value});
  nextTick(autoResize);
}
/**
 * 滚轮落在文本卡上 = 滚动卡内文本，而不是缩放画布（用户约定）。
 * 仅当文本确实溢出时拦截；内容没溢出则放行，画布照常缩放。
 * 编辑态 textarea 自带 nowheel 走原生滚动；ctrl+滚轮（触控板捏合）与横向滑动始终放行给画布。
 */
function onTextWheel(e: WheelEvent) {
  if (textEditing.value || e.ctrlKey) return;
  if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
  const el = taEl.value;
  if (!el) return;
  const max = el.scrollHeight - el.clientHeight;
  if (max <= 1) return;
  e.preventDefault();
  e.stopPropagation(); // 不让事件冒泡到 viewport 的 d3-zoom wheel 监听
  const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY; // Firefox 按行滚
  el.scrollTop = Math.min(Math.max(0, el.scrollTop + dy), max);
}
// textarea 常驻不换元素：展示态必须**直接回显全文**——高度重算不能等双击进编辑才发生（挂载 / 字体加载 /
// 卡宽变化的重算钩子注册在下方 syncHeights 定义处）。这里先挂内容变化监听：data 任何字段变化 → 重算
watch(
  () => props.data,
  () => nextTick(syncHeights),
  {deep: true}
);
// 程序化进入编辑（如：工具栏新建节点后自动开打）
watch(
  () => api?.editSignal.value,
  sig => {
    if (!sig || sig.id !== props.id) return;
    if (variant.value === 'text') startTextEdit();
    else if (['task', 'data', 'conclusion', 'seg'].includes(variant.value)) startCardEdit();
  }
);

// ── 开始 / 结束节点：单击落焦点、再点改标签；双击开全屏预览 ──────────────
const capLabel = computed(() => props.data?.label || (variant.value === 'start' ? '开始' : '结束'));
const capEditing = ref(false);
const capInputEl = ref<HTMLInputElement | null>(null);
let origLabel = '';

function startCapEdit(at?: {x: number; y: number} | null) {
  if (api?.editLocked?.value) return;
  if (capEditing.value) return;
  origLabel = props.data?.label ?? '';
  capEditing.value = true;
  nextTick(() => {
    const el = capInputEl.value;
    if (el) focusWhenReady(el, () => capEditing.value, at);
  });
}
function commitCapEdit() {
  capEditing.value = false;
}
function cancelCapEdit() {
  capEditing.value = false;
  if ((props.data?.label ?? '') !== origLabel) api?.updateData(props.id, {label: origLabel});
}
function onCapInput(e: Event) {
  api?.beginEdit();
  api?.updateData(props.id, {label: (e.target as HTMLInputElement).value});
}

// ── 附件节点：空态点击 / 拖拽上传；有文件则回显 ─────────────────────────
const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
const fileName = computed(() => String(props.data?.name || ''));
const hasFile = computed(() => Boolean(String(props.data?.url || '').trim()));
// data.url 约定为后端相对路径（/ai/agent/uploads/download?... 或 /ai/agent/artifacts/123/download?inline=1）；
// 容错 agent 写绝对 URL / 漏前导斜杠的情况，不再无脑前拼 baseURL（拼出双前缀 = 坏链）
const mediaSrc = computed(() => {
  const u = String(props.data?.url || '').trim();
  if (!u) return '';
  if (/^https?:\/\//i.test(u)) return u;
  return `${baseURL}${u.startsWith('/') ? u : `/${u}`}`;
});
// 下载链接：去掉 inline=1 → 后端下发 Content-Disposition: attachment，真下载而非新标签页内联打开
const downloadSrc = computed(() => {
  const u = mediaSrc.value;
  if (!u) return '';
  const qi = u.indexOf('?');
  if (qi < 0) return u;
  const kept = u.slice(qi + 1).split('&').filter(p => p && p !== 'inline=1');
  return kept.length ? `${u.slice(0, qi)}?${kept.join('&')}` : u.slice(0, qi);
});
const isUploading = computed(() => api?.uploadingNodeId.value === props.id);
const dragHover = ref(0);
// 图片加载失败（产物链接失效 / 文件丢失 / ID 编错）→ 显示说明态而非破图
const imgError = ref(false);
watch(mediaSrc, () => {
  imgError.value = false;
});

function pickForThisNode() {
  if (api?.editLocked?.value) return;
  if (isUploading.value) return;
  api?.requestNodeUpload(props.id);
}
function onFileDrop(e: DragEvent) {
  e.preventDefault();
  if (api?.editLocked?.value) return;
  dragHover.value = 0;
  const f = e.dataTransfer?.files?.[0];
  if (f && !isUploading.value) void api?.uploadOneToNode(props.id, f);
}

// 类型判断：mime 优先、扩展名兜底（与 qa-glass 同构，判断函数共享 utils）
const fileKind = computed<'image' | 'video' | 'audio' | 'markdown' | 'office' | 'csv' | 'html' | 'file'>(() => {
  const mime = String(props.data?.mime || '').toLowerCase();
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('video/')) return 'video';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime === 'text/html') return 'html';
  const name = String(props.data?.name || '');
  if (isHtmlFile(name)) return 'html';
  if (isMarkdownFile(name)) return 'markdown';
  if (isOfficePreviewable(name)) return 'office';
  if (isCsvFile(name)) return 'csv';
  const ext = extractExt(name);
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico'].includes(ext)) return 'image';
  if (['mp4', 'webm', 'mov', 'ogg', 'mkv', 'avi'].includes(ext)) return 'video';
  if (['mp3', 'wav', 'flac', 'aac', 'm4a'].includes(ext)) return 'audio';
  return 'file';
});

// ── HTML 附件：卡内联渲染（与 qa-glass 同一个 HtmlRender 组件），内容拉一次缓存；全屏预览走共享弹层 ──
const htmlText = ref('');
const htmlLoading = ref(false);
const htmlError = ref(false);
// 互动模式：iframe 会吞掉鼠标事件导致节点拖不动——默认透明 shield 罩住 iframe 保住拖拽，
// 用户点「互动」才摘掉 shield 进 iframe 交互态（滚动 / 点击页面内容），再点退出恢复可拖
const htmlInteractive = ref(false);
watch(
  () => (fileKind.value === 'html' ? mediaSrc.value : ''),
  async (src, _old, onCleanup) => {
    htmlText.value = '';
    htmlError.value = false;
    htmlInteractive.value = false;
    if (!src) return;
    htmlLoading.value = true;
    let stale = false;
    onCleanup(() => {
      stale = true;
    });
    try {
      const resp = await fetch(src, {credentials: 'include'});
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const text = await resp.text();
      if (stale) return;
      htmlText.value = text;
    } catch {
      if (!stale) htmlError.value = true;
    } finally {
      if (!stale) htmlLoading.value = false;
    }
  },
  {immediate: true}
);

// ── Markdown 附件：卡内联渲染（与预览弹层 / artifact-list 同用 marked），内容拉一次缓存；全屏预览走共享弹层 ──
const mdHtml = ref('');
const mdLoading = ref(false);
const mdError = ref(false);
watch(
  () => (fileKind.value === 'markdown' ? mediaSrc.value : ''),
  async (src, _old, onCleanup) => {
    mdHtml.value = '';
    mdError.value = false;
    if (!src) return;
    mdLoading.value = true;
    let stale = false;
    onCleanup(() => {
      stale = true;
    });
    try {
      const resp = await fetch(src, {credentials: 'include'});
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const text = await resp.text();
      if (stale) return;
      mdHtml.value = (await marked.parse(text, {breaks: true})) as string;
    } catch {
      if (!stale) mdError.value = true;
    } finally {
      if (!stale) mdLoading.value = false;
    }
  },
  {immediate: true}
);

/** 全屏预览（图片 / markdown / office / csv / html）：复用页面级共享弹层（workflow/index.vue 提供 previewAttachment） */
function openFullPreview() {
  const src = mediaSrc.value;
  if (!src) return;
  api?.previewAttachment({name: String(props.data?.name || '附件'), src});
}

/** 卡片全屏预览（附件卡的 ⛶ 模式推广到所有卡型）：弹窗渲染该卡真实节点组件，保持可编辑 */
// 预览弹窗环境标记（wf-node-preview.vue provide 的普通 boolean，不是 ref——别按 .value 取！）
const inPreviewMode = inject('wfPreview', false);
function openCardPreview() {
  if (inPreviewMode) return; // 弹窗内不再套娃开预览
  api?.previewNode?.(props.id);
}

/** 板级徽标通道（workflow/index.vue provide）：临时协作态，不进 node.data、不进 agent 上下文；
 *  预览弹窗与画布同组件树，inject 自然继承 */
const wfMarks = inject<Ref<BoardMarks> | null>('wfMarks', null);
// 本节点的标：new（Agent 本轮新增）/ human（人工编辑）/ agent（Agent 编辑），优先级由打标端保证（new 覆盖 human/agent）
const nodeMark = computed(() => wfMarks?.value?.nodes?.[props.id]?.t);

// ── 点击手势：单击 = 落焦点（选中），再点一下 = 进就地编辑；双击 = 开全屏预览 ──
// click / dblclick 区分：单击动作延迟 ~230ms，窗口内来了 dblclick 就取消挂起的单击、走预览。
// 「落焦点」用 vue-flow 节点选中态承载（pointerdown 时抢在本次点击选中前读 .vue-flow__node.selected）：
// 第一击选中卡片，点别处取消选中自动复位——节奏天然是「点一下、再点一下」。
let gestureTimer: ReturnType<typeof setTimeout> | null = null;
let downWasSelected = false;
let gestureDownX = 0;
let gestureDownY = 0;
function onGesturePointerDown(e: PointerEvent) {
  const wrap = (e.currentTarget as HTMLElement)?.closest?.('.vue-flow__node');
  downWasSelected = !!wrap?.classList.contains('selected');
  gestureDownX = e.clientX;
  gestureDownY = e.clientY;
}
function onGestureClick(e: MouseEvent, startEdit: (at?: {x: number; y: number} | null) => void) {
  if (api?.editLocked?.value) return;
  // 按钮 / 链接（＋ 连线、⛶ 预览、下载等）与拖拽位移不触发卡片手势
  if ((e.target as HTMLElement)?.closest?.('button, a')) return;
  if (Math.hypot(e.clientX - gestureDownX, e.clientY - gestureDownY) > 6) return;
  // 入口点击点：进编辑后光标直接落在这个点对应的字上（点哪编辑哪，不再全选 / 跳文末）
  const at = {x: e.clientX, y: e.clientY};
  // 预览弹窗里就是来编辑的：单击直接进编辑，不走两步节奏（也没有双击预览要区分）
  if (inPreviewMode) {
    startEdit(at);
    return;
  }
  const rootEl = e.currentTarget as HTMLElement | null;
  if (gestureTimer) {
    clearTimeout(gestureTimer); // 双击序列的第二击：不做单击动作，交给 dblclick
    gestureTimer = null;
    return;
  }
  const wasSelected = downWasSelected;
  gestureTimer = setTimeout(() => {
    gestureTimer = null;
    if (api?.editLocked?.value) return;
    if (wasSelected) {
      startEdit(at);
    } else {
      // 落焦点：焦点放到卡内第一个字段（没有就放卡本身），光标同步落在点击位置——可见光标 = 「已落下」的明确信号
      const field = rootEl?.querySelector<HTMLInputElement | HTMLTextAreaElement>('input, textarea');
      if (field) {
        field.focus();
        try {
          const hit = caretFromPoint(field, at.x, at.y);
          if (hit) field.setSelectionRange(hit.index, hit.index);
        } catch {
          /* 个别 input 类型不支持 setSelectionRange，忽略 */
        }
      } else {
        rootEl?.focus();
      }
    }
  }, 230);
}
function onGestureDblClick() {
  if (gestureTimer) {
    clearTimeout(gestureTimer);
    gestureTimer = null;
  }
  // 编辑中的双击是文本操作（选词等），不触发预览
  if (cardEditing.value || textEditing.value || capEditing.value) return;
  openCardPreview();
}
/** 人工核查卡双击：作答控件（选项 / 输入框）上的双击不劫持，其余区域开预览 */
function onReviewDblClick(e: MouseEvent) {
  if ((e.target as HTMLElement)?.closest?.('input, button')) return;
  onGestureDblClick();
}

// 预览入口统一收到卡内右上角全屏按钮（图片 / 文档 / HTML 一致）：卡面其余区域不触发预览，
// 拖拽 / 选中节点不再误开弹层——板子编辑顺手，预览是显式动作

// ── 人工核查节点：agent 在质量关键点提问，用户作答写入 data.answer，agent 读到后继续流程 ──
// data = {question, options?: string[], answer?: string, disabled?: boolean}；answer 空 = 待回答；
// disabled=true = agent 处理完作答后的收口锁定（撤卡 or 锁禁用二选一），作答不可再改
const reviewQuestion = computed(() => String(props.data?.question || ''));
const reviewOptions = computed<string[]>(() => (Array.isArray(props.data?.options) ? props.data.options.map(String).filter(Boolean) : []));
const reviewAnswer = computed(() => (props.data?.answer == null ? '' : String(props.data.answer)));
const reviewAnswered = computed(() => reviewAnswer.value !== '');
const reviewDisabled = computed(() => props.data?.disabled === true);
const reviewInput = ref(reviewAnswer.value);
watch(reviewAnswer, v => {
  reviewInput.value = v;
});
/** 点选选项即作答（写入 data.answer 随自动保存落库，并通知页面自动触发 Agent 响应）；再次点已选项 = 撤回作答（不通知） */
function pickReviewOption(opt: string) {
  if (api?.editLocked?.value || reviewDisabled.value) return; // 响应期锁编辑 / 已收口锁定：核查作答停用
  const next = reviewAnswer.value === opt ? '' : opt;
  api?.beginEdit();
  api?.updateData(props.id, {answer: next});
  if (next) api?.notifyReviewAnswered?.(props.id, reviewQuestion.value, next);
}
/** 自由输入：回车 / 失焦提交（去首尾空白，内容未变则不触发保存；新答案同样通知页面触发 Agent 响应） */
function commitReviewInput() {
  if (api?.editLocked?.value || reviewDisabled.value) return;
  const val = reviewInput.value.trim();
  if (val === reviewAnswer.value) return;
  api?.beginEdit();
  api?.updateData(props.id, {answer: val});
  if (val) api?.notifyReviewAnswered?.(props.id, reviewQuestion.value, val);
}

// ── 工作项节点：核心就是「一张卡装一块工作的多条任务」，不维护待办/已办——进度看连线与产出，不看勾选 ──
// data = {title, subs?: [文字, sid?][], summary?, note?}
// sid = 任务稳定 id：edge.sourceHandle 锚在它上面（任务级出线），改名/重排都不能让它漂走
const taskTitle = computed(() => String(props.data?.title || ''));
// subs 形状：[文字, sid?]（sid 可省，agent 写的纯字符串行由 normalizeNode 下轮补齐 id）
// t 保留原值不 trim：行级编辑逐键写回时 :value 与输入值一致光标不跳；空行判定才用 trim
const taskSubs = computed(() => {
  const raw = Array.isArray(props.data?.subs) ? props.data.subs : [];
  return raw
    .map((s: any) => (Array.isArray(s) ? {t: String(s[0] ?? ''), sid: typeof s[1] === 'string' ? s[1] : ''} : {t: String(s ?? ''), sid: ''}))
    .filter((s: {t: string; sid: string}) => s.t.trim());
});
/** 任务稳定 id（短、仅小写字母数字，可直接拼进 edge id / 选择器） */
let subSeq = 0;
function genSubId(): string {
  return `s${Date.now().toString(36).slice(-6)}${(subSeq++).toString(36)}`;
}
const taskSummary = computed(() => String(props.data?.summary || ''));
const taskNote = computed(() => String(props.data?.note || ''));

// ── 数据节点：一组量化事实。大号主数值 + attrs 键值对（值全为数值时自动生比例条）+ 口径 note + 证据 samples ──
// data = {title, metric?, unit?, attrs?: [string, string|number][], note?, samples?: string[]}
const dataTitle = computed(() => String(props.data?.title || ''));
const dataMetric = computed(() => (props.data?.metric == null ? '' : String(props.data.metric)));
const dataUnit = computed(() => String(props.data?.unit || ''));
const dataNote = computed(() => String(props.data?.note || ''));
// 标签保留原值不 trim（同行级编辑其它清单）：逐键写回 :value 一致光标不跳；空行判定才用 trim
const dataAttrs = computed<Array<[string, string]>>(() => {
  const raw = props.data?.attrs;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter(p => Array.isArray(p) && String(p[0] ?? '').trim())
    .map(p => [String(p[0] ?? ''), String(p[1] ?? '')] as [string, string]);
});
// attrs 值全为数值 → 自动渲染卡内比例条（相对最大值，不给独立开关字段，agent 少填一样）
const dataBars = computed<Array<{pct: number}> | null>(() => {
  const pairs = dataAttrs.value;
  if (pairs.length < 2) return null;
  const nums = pairs.map(([, v]) => Number(String(v).replace(/[,%\s]/g, '')));
  if (nums.some(n => !Number.isFinite(n) || n < 0)) return null;
  const max = Math.max(...nums);
  if (max <= 0) return null;
  return nums.map(n => ({pct: Math.max(3, Math.round((n / max) * 100))}));
});
const dataSamples = computed<string[]>(() => (Array.isArray(props.data?.samples) ? props.data.samples.map(String).filter(Boolean) : []));

/** input 宽度按内容字符数给（CJK 记 2）：现代浏览器由 CSS field-sizing: content 直接按内容自适应，size 属性作旧内核兜底 */
function contentSize(s: string, min = 1): number {
  let n = 0;
  for (const ch of s) n += /[^\x00-\xff]/.test(ch) ? 2 : 1;
  return Math.max(min, n);
}

// ── 结论节点：项目给出的答案，全板最重的一张卡（深色）。data = {claim, points?: string[], caveat?} ──
const conClaim = computed(() => String(props.data?.claim || ''));
const conPoints = computed<string[]>(() => (Array.isArray(props.data?.points) ? props.data.points.map(String).filter(Boolean) : []));
const conCaveat = computed(() => String(props.data?.caveat || ''));

// ── 分镜段节点（品牌板型：分镜板，generic）：一卡一场戏（SEG），单场承载多分镜 ──
// data = {seg(段位 SEG01…), duration?(本场时长), emotion?(情绪), scene?(场景), state?(状态/调度),
//          shots?: [分镜文字, sid?][]（与工作项 subs 同构：sid 是分镜级出线锚点）, note?}
const segNo = computed(() => String(props.data?.seg || ''));
const segDuration = computed(() => String(props.data?.duration || ''));
const segEmotion = computed(() => String(props.data?.emotion || ''));
const segScene = computed(() => String(props.data?.scene || ''));
const segState = computed(() => String(props.data?.state || ''));
const segNote = computed(() => String(props.data?.note || ''));
// shots 形状同 subs：[文字, sid?]（sid 可省，agent 写的纯字符串行由 normalizeNode 下轮补齐 id）
// t 保留原值不 trim（同 taskSubs）：行级编辑逐键写回时 :value 与输入值一致光标不跳；空行判定才用 trim
const segShots = computed(() => {
  const raw = Array.isArray(props.data?.shots) ? props.data.shots : [];
  return raw
    .map((s: any) => (Array.isArray(s) ? {t: String(s[0] ?? ''), sid: typeof s[1] === 'string' ? s[1] : ''} : {t: String(s ?? ''), sid: ''}))
    .filter((s: {t: string; sid: string}) => s.t.trim());
});

/** 写行清单（工作项卡 subs / 分镜段卡 shots 同一机制）。每条补齐稳定 sid——缺 id 的行（老数据 / 新增行）在此落 id，行级连线才锚得住 */
function writeRows(rows: Array<[string, string?]>) {
  const withIds: Array<[string, string]> = rows.map(([t, sid]) => [t, sid || genSubId()]);
  const field = variant.value === 'seg' ? 'shots' : 'subs';
  api?.beginEdit();
  api?.updateData(props.id, {[field]: withIds});
}
/** 当前卡的行清单（task→subs / seg→shots），缓冲区读写共用 */
function cardRows(): Array<{t: string; sid: string}> {
  return variant.value === 'seg' ? segShots.value : taskSubs.value;
}

// ── 卡片就地编辑（task / data / conclusion / seg 多字段卡，人机协作：单击落焦点、再点一下即可改任何字段）──
// 与文本节点同源的零形变思路：字段元素常驻，展示态 readonly + 对事件透明 → 整卡可拖拽；
// 进编辑焦点落在用户点击的那个字段、光标落在点击的那个字（点哪编辑哪），Esc 整卡快照恢复、焦点移出卡片 = 编辑完成。
// 所有字段一律逐键实时写库——包括列表字段：行级就地编辑，展示的每一行原地换成可编辑元素
// （行几何不变 → 进出编辑零抖动），**不做「列表 ↔ 多行文本域」的整块互换**（那是抖动之源）。
const cardEditing = ref(false);
const cardRootEl = ref<HTMLElement | null>(null);
let origCardData: Record<string, any> = {};

function resizeTa(el: HTMLTextAreaElement) {
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight}px`;
}
/** 本节点全部常驻 textarea 的高度自适应：展示态直接回显全文，双击只是进编辑的入口、不是看全文的前提。
 *  文本节点 = 唯一 textarea；卡片类 = summary / claim / 分镜等常驻 textarea（行级编辑的 textarea 一并覆盖） */
function syncHeights() {
  if (variant.value === 'text') {
    autoResize();
    return;
  }
  if (['task', 'data', 'conclusion', 'seg'].includes(variant.value)) {
    cardRootEl.value?.querySelectorAll<HTMLTextAreaElement>('textarea').forEach(resizeTa);
  }
}
// 高度重算的另外两个时机：① 网页字体加载完（Plus Jakarta Sans 加载前后字面宽度不同会改变换行，
// 首测高度会裁切）② 卡宽变化（同理改变换行）——都重算常驻 textarea 高度，展示态全文始终可见
let heightRO: ResizeObserver | null = null;
onMounted(() => {
  nextTick(syncHeights);
  void document.fonts?.ready
    ?.then(() => {
      syncHeights();
    })
    .catch(() => {});
  const target = variant.value === 'text' ? taEl.value : cardRootEl.value;
  if (target && typeof ResizeObserver !== 'undefined') {
    let lastW = target.getBoundingClientRect().width;
    heightRO = new ResizeObserver(entries => {
      const w = entries[entries.length - 1]?.contentRect.width ?? 0;
      if (Math.abs(w - lastW) > 0.5) {
        lastW = w;
        nextTick(syncHeights);
      }
    });
    heightRO.observe(target);
  }
});
onBeforeUnmount(() => {
  heightRO?.disconnect();
  heightRO = null;
});

function startCardEdit(at?: {x: number; y: number} | null) {
  if (api?.editLocked?.value) return; // 响应期锁编辑：卡片双击编辑停用
  if (cardEditing.value || !['task', 'data', 'conclusion', 'seg'].includes(variant.value)) return;
  origCardData = JSON.parse(JSON.stringify(props.data || {}));
  // 行级编辑以 sid 为稳定 key（行级连线锚点）：老数据缺 sid 的行进编辑前先一次性补齐落库
  if (['task', 'seg'].includes(variant.value) && cardRows().some(r => !r.sid)) {
    writeRows(cardRows().map(r => [r.t, r.sid]));
  }
  cardEditing.value = true;
  api?.beginEdit();
  nextTick(() => {
    cardRootEl.value?.querySelectorAll<HTMLTextAreaElement>('textarea').forEach(resizeTa);
    // 点哪编辑哪：编辑态字段恢复指针事件，elementFromPoint 直接命中用户点的那个字段；
    // 点在字段之外（头部 / 清单区空白等）退回第一个字段，光标置于文末
    let target: HTMLInputElement | HTMLTextAreaElement | null = null;
    let point: {x: number; y: number} | null = null;
    if (at) {
      const hitEl = document.elementFromPoint(at.x, at.y);
      if ((hitEl instanceof HTMLInputElement || hitEl instanceof HTMLTextAreaElement) && cardRootEl.value?.contains(hitEl)) {
        target = hitEl;
        point = at;
      }
    }
    if (!target) target = cardRootEl.value?.querySelector('input, textarea') as HTMLInputElement | HTMLTextAreaElement | null;
    if (target) focusWhenReady(target, () => cardEditing.value, point);
  });
}
/** Esc：取消本次编辑（整卡数据恢复进编辑前的快照；未提交的 ghost 行一并丢弃） */
function cancelCardEdit() {
  if (!cardEditing.value) return;
  cardEditing.value = false;
  newChildText.value = '';
  newPointText.value = '';
  newAttrLabel.value = '';
  newAttrValue.value = '';
  newSampleText.value = '';
  api?.updateData(props.id, JSON.parse(JSON.stringify(origCardData)));
}
/** 焦点移出整卡（点了画布 / 别的卡）= 编辑完成；所有字段均逐键写库，这里只退出编辑态 */
function onCardFocusOut(e: FocusEvent) {
  if (!cardEditing.value) return;
  if (e.relatedTarget && (e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) return;
  cardEditing.value = false;
}
/** 标量字段输入：按 data-f 认字段，逐键实时写（同文本节点 onTextInput） */
function onFieldInput(e: Event) {
  const el = e.target as HTMLInputElement | HTMLTextAreaElement;
  const f = el.dataset.f;
  if (!f) return;
  api?.beginEdit();
  api?.updateData(props.id, {[f]: el.value});
  if (el instanceof HTMLTextAreaElement) resizeTa(el);
}

// ── 行级就地编辑：展示的每一行原地换成可编辑元素（行几何不变 → 进出编辑零抖动），逐键写库 ──
// sid / 行索引保住连线锚点；新增走清单末尾的 ghost 行（回车 / 失焦提交）；删除走行尾悬浮 ×。
/** 工作项卡 subs / 分镜段卡 shots 共用：按行写回 [文字, sid] 清单 */
function onChildInput(i: number, e: Event) {
  const el = e.target as HTMLTextAreaElement;
  resizeTa(el);
  const field = variant.value === 'seg' ? 'shots' : 'subs';
  const rows = cardRows().map((r, j) => (j === i ? [el.value, r.sid] : [r.t, r.sid])) as Array<[string, string]>;
  api?.beginEdit();
  api?.updateData(props.id, {[field]: rows});
}
function delChild(i: number) {
  if (api?.editLocked?.value) return;
  const field = variant.value === 'seg' ? 'shots' : 'subs';
  const rows = cardRows().filter((_, j) => j !== i).map(r => [r.t, r.sid] as [string, string]);
  api?.beginEdit();
  api?.updateData(props.id, {[field]: rows});
}
const newChildText = ref('');
/** ghost 行提交新任务 / 分镜：输入后回车或失焦落库（空输入忽略） */
function commitNewChild() {
  const t = newChildText.value.trim();
  newChildText.value = '';
  if (!t) return;
  const field = variant.value === 'seg' ? 'shots' : 'subs';
  const rows = [...cardRows().map(r => [r.t, r.sid] as [string, string]), [t, genSubId()] as [string, string]];
  api?.beginEdit();
  api?.updateData(props.id, {[field]: rows});
}
/** 结论卡要点 */
function onPointInput(i: number, e: Event) {
  const el = e.target as HTMLTextAreaElement;
  resizeTa(el);
  const points = conPoints.value.map((p, j) => (j === i ? el.value : p));
  api?.beginEdit();
  api?.updateData(props.id, {points});
}
function delPoint(i: number) {
  if (api?.editLocked?.value) return;
  api?.beginEdit();
  api?.updateData(props.id, {points: conPoints.value.filter((_, j) => j !== i)});
}
const newPointText = ref('');
function commitNewPoint() {
  const t = newPointText.value.trim();
  newPointText.value = '';
  if (!t) return;
  api?.beginEdit();
  api?.updateData(props.id, {points: [...conPoints.value, t]});
}
/** 数据卡 attrs（[标签, 值] 对）：part=0 标签 / part=1 值 */
function onAttrInput(i: number, part: 0 | 1, e: Event) {
  const v = (e.target as HTMLInputElement).value;
  const attrs = dataAttrs.value.map(([l, val], j) => (j === i ? (part === 0 ? [v, val] : [l, v]) : [l, val])) as Array<[string, string]>;
  api?.beginEdit();
  api?.updateData(props.id, {attrs});
}
function delAttr(i: number) {
  if (api?.editLocked?.value) return;
  api?.beginEdit();
  api?.updateData(props.id, {attrs: dataAttrs.value.filter((_, j) => j !== i)});
}
const newAttrLabel = ref('');
const newAttrValue = ref('');
function commitNewAttr() {
  const l = newAttrLabel.value.trim();
  const v = newAttrValue.value.trim();
  newAttrLabel.value = '';
  newAttrValue.value = '';
  if (!l) return;
  api?.beginEdit();
  api?.updateData(props.id, {attrs: [...dataAttrs.value, [l, v] as [string, string]]});
}
/** ghost 行失焦提交；焦点在同一 ghost 行的标签↔值之间切换（Tab）不提交，否则切格子的瞬间内容被吞 */
function onAttrGhostBlur(e: FocusEvent) {
  const rt = e.relatedTarget as HTMLElement | null;
  const myGhost = (e.currentTarget as HTMLElement | null)?.closest('.dt-attr-new');
  if (rt && myGhost && rt.closest('.dt-attr-new') === myGhost) return;
  commitNewAttr();
}
/** 数据卡 samples（证据行） */
function onSampleInput(i: number, e: Event) {
  const el = e.target as HTMLTextAreaElement;
  resizeTa(el);
  const samples = dataSamples.value.map((s, j) => (j === i ? el.value : s));
  api?.beginEdit();
  api?.updateData(props.id, {samples});
}
function delSample(i: number) {
  if (api?.editLocked?.value) return;
  api?.beginEdit();
  api?.updateData(props.id, {samples: dataSamples.value.filter((_, j) => j !== i)});
}
const newSampleText = ref('');
function commitNewSample() {
  const t = newSampleText.value.trim();
  newSampleText.value = '';
  if (!t) return;
  api?.beginEdit();
  api?.updateData(props.id, {samples: [...dataSamples.value, t]});
}
</script>

<template>
  <!-- 开始 / 结束：胶囊节点（单击落焦点、再点改标签；双击开全屏预览；编辑框浮在标签上，尺寸不变） -->
  <div
    v-if="variant === 'start' || variant === 'end'"
    class="wf-node cap"
    :class="`cap-${variant}`"
    tabindex="-1"
    @pointerdown="onGesturePointerDown"
    @click="onGestureClick($event, startCapEdit)"
    @dblclick.stop="onGestureDblClick"
  >
    <button type="button" class="wf-card-fs nodrag" title="全屏预览（保持可编辑）" @click.stop="openCardPreview"><WfIcon name="expand" :size="12" /></button>
    <WfNodeBadge v-if="nodeMark" :type="nodeMark" />
    <Handle v-if="variant === 'end'" type="target" :position="Position.Left" class="wf-handle" />
    <button v-if="variant === 'end'" class="wf-add tgt nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'target')">+</button>
    <WfIcon class="cap-ic" :name="variant === 'start' ? 'play' : 'stop'" :size="10" />
    <span class="cap-wrap">
      <span class="cap-label" :class="{ghost: capEditing}" title="单击落焦点，再点一下修改；双击全屏预览">{{ capLabel }}</span>
      <input
        v-if="capEditing"
        ref="capInputEl"
        class="cap-input nodrag nowheel"
        :value="capLabel"
        spellcheck="false"
        @input="onCapInput"
        @blur="commitCapEdit"
        @keydown.enter.stop="commitCapEdit"
        @keydown.esc.stop="cancelCapEdit"
      />
    </span>
    <Handle v-if="variant === 'start'" type="source" :position="Position.Right" class="wf-handle" />
    <button v-if="variant === 'start'" class="wf-add src nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'source')">+</button>
  </div>

  <!-- 文本节点：textarea 常驻（展示态对事件透明 → 整体可拖拽），单击落焦点、再点进入编辑，双击开全屏预览，切换不换元素；滚轮在卡内滚文本 -->
  <div
    v-else-if="variant === 'text'"
    class="wf-node text"
    @pointerdown="onGesturePointerDown"
    @click="onGestureClick($event, startTextEdit)"
    @dblclick.stop="onGestureDblClick"
    @wheel="onTextWheel"
  >
    <button type="button" class="wf-card-fs nodrag" title="全屏预览（保持可编辑）" @click.stop="openCardPreview"><WfIcon name="expand" :size="12" /></button>
    <WfNodeBadge v-if="nodeMark" :type="nodeMark" />
    <Handle type="target" :position="Position.Left" class="wf-handle" />
    <button class="wf-add tgt nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'target')">+</button>
    <textarea
      ref="taEl"
      class="text-area"
      :class="textEditing ? 'nodrag nowheel' : 'view'"
      :value="data?.text || ''"
      :placeholder="textEditing ? '输入文本…' : '点击两次编辑…'"
      :readonly="!textEditing"
      rows="1"
      spellcheck="false"
      @input="onTextInput"
      @blur="commitTextEdit"
      @keydown.esc.stop="cancelTextEdit"
    />
    <Handle type="source" :position="Position.Right" class="wf-handle" />
    <button class="wf-add src nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'source')">+</button>
  </div>

  <!-- 人工核查节点：agent 在「有人工参与质量更高」的位置提问，用户作答（点选 / 自由输入）写入 data.answer，agent 读到后继续流程 -->
  <div v-else-if="variant === 'review'" class="wf-node review" :class="{answered: reviewAnswered, locked: reviewDisabled}" @dblclick.stop="onReviewDblClick">
    <button type="button" class="wf-card-fs nodrag" title="全屏预览（保持可编辑）" @click.stop="openCardPreview"><WfIcon name="expand" :size="12" /></button>
    <WfNodeBadge v-if="nodeMark" :type="nodeMark" />
    <Handle type="target" :position="Position.Left" class="wf-handle" />
    <button class="wf-add tgt nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'target')">+</button>
    <div class="rv-head">
      <WfIcon class="rv-ic" name="review" :size="13" />
      <span class="rv-title">人工核查</span>
      <span class="rv-badge" :class="reviewDisabled ? 'locked' : reviewAnswered ? 'done' : 'wait'">
        <WfIcon v-if="reviewDisabled" name="lock" :size="9" />{{ reviewDisabled ? '已锁定' : reviewAnswered ? '已回答' : '待回答' }}
      </span>
    </div>
    <div class="rv-q">{{ reviewQuestion || '（问题待 agent 填写）' }}</div>
    <!-- 已收口锁定：选项 / 答案只读回显，作答入口整体停用 -->
    <div v-if="reviewOptions.length" class="rv-opts nodrag" :class="{locked: reviewDisabled}">
      <button v-for="opt in reviewOptions" :key="opt" class="rv-opt" :class="{sel: reviewAnswer === opt}" @click="pickReviewOption(opt)">
        {{ opt }}
      </button>
    </div>
    <div v-else-if="reviewDisabled" class="rv-ans">{{ reviewAnswer || '（未作答）' }}</div>
    <input
      v-else
      v-model="reviewInput"
      class="rv-input nodrag nowheel"
      placeholder="输入回答，回车确认"
      spellcheck="false"
      @keydown.enter.stop="commitReviewInput"
      @blur="commitReviewInput"
    />
    <Handle type="source" :position="Position.Right" class="wf-handle" />
    <button class="wf-add src nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'source')">+</button>
  </div>

  <!-- 工作项节点：一张卡装一块工作，核心是卡内编号任务清单；双击卡片就地编辑各字段；每条任务可各自拖出一条连线（任务级 sourceHandle） -->
  <div
    v-else-if="variant === 'task'"
    ref="cardRootEl"
    class="wf-node task"
    :class="{editing: cardEditing}"
    @pointerdown="onGesturePointerDown"
    @click="onGestureClick($event, startCardEdit)"
    @dblclick.stop="onGestureDblClick"
    @focusout="onCardFocusOut"
  >
    <button type="button" class="wf-card-fs nodrag" title="全屏预览（保持可编辑）" @click.stop="openCardPreview"><WfIcon name="expand" :size="12" /></button>
    <WfNodeBadge v-if="nodeMark" :type="nodeMark" />
    <Handle type="target" :position="Position.Left" class="wf-handle" />
    <button class="wf-add tgt nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'target')">+</button>
    <div class="tk-head">
      <input
        class="tk-title"
        :class="cardEditing ? 'nodrag nowheel' : 'view'"
        data-f="title"
        :value="taskTitle"
        :placeholder="cardEditing ? '工作项名称…' : '（未命名工作项）'"
        :readonly="!cardEditing"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
      <span v-if="taskSubs.length" class="tk-count" :title="`这张卡拆成了 ${taskSubs.length} 条任务`">{{ taskSubs.length }} 项</span>
    </div>
    <textarea
      v-if="cardEditing || taskSummary"
      class="tk-summary"
      :class="cardEditing ? 'nodrag nowheel' : 'view'"
      data-f="summary"
      :value="taskSummary"
      :placeholder="cardEditing ? '这一项在做什么 / 结果如何…' : ''"
      :readonly="!cardEditing"
      rows="1"
      spellcheck="false"
      @input="onFieldInput"
      @keydown.esc.stop="cancelCardEdit"
    />
    <!-- 卡内任务清单是这张卡的主角：编号行逐条罗列；行级就地编辑——进编辑每行文字原地换成 textarea，
         行几何（编号 / 字号 / 行高 / 内边距）两态完全一致，进出编辑零抖动；
         行右「＋」可从这条任务拖出连线（edge.sourceHandle = 行 sid）；行不挡卡片拖拽 -->
    <div class="tk-subs">
      <div v-if="!taskSubs.length && !cardEditing" class="tk-empty">点击进入编辑，把这块工作拆成一条条任务…</div>
      <div v-for="(s, i) in taskSubs" :key="s.sid || `${i}-${s.t}`" class="tk-sub">
        <span class="tk-sub-no">{{ String(i + 1).padStart(2, '0') }}</span>
        <textarea
          v-if="cardEditing"
          class="tk-sub-input nodrag nowheel"
          :value="s.t"
          placeholder="任务描述…"
          rows="1"
          spellcheck="false"
          @input="onChildInput(i, $event)"
          @keydown.esc.stop="cancelCardEdit"
        />
        <span v-else class="tk-sub-t">{{ s.t }}</span>
        <button v-if="cardEditing" class="tk-sub-del nodrag" title="删除这条任务" @click.stop="delChild(i)" @dblclick.stop>
          <WfIcon name="x" :size="8" />
        </button>
        <Handle v-if="s.sid" :id="s.sid" type="source" :position="Position.Right" class="wf-sub-handle" />
        <button
          v-if="s.sid"
          class="tk-sub-add nodrag"
          title="从这条任务拖出连线 / 点击加节点"
          @mousedown.stop.prevent="onSubAddMouseDown($event, s.sid)"
          @click.stop
          @dblclick.stop
        >
          +
        </button>
      </div>
      <!-- 编辑态末尾 ghost 行：回车 / 失焦提交新任务（不占展示态版面） -->
      <div v-if="cardEditing" class="tk-sub tk-sub-new">
        <span class="tk-sub-no">{{ String(taskSubs.length + 1).padStart(2, '0') }}</span>
        <input
          v-model="newChildText"
          class="tk-sub-input nodrag nowheel"
          placeholder="＋ 新任务（回车添加）"
          spellcheck="false"
          @keydown.enter.stop.prevent="commitNewChild"
          @blur="commitNewChild"
          @keydown.esc.stop="cancelCardEdit"
        />
      </div>
    </div>
    <div v-if="!cardEditing && taskNote" class="tk-note"><WfIcon class="tk-note-ic" name="flag" :size="11" />{{ taskNote }}</div>
    <!-- 编辑态复用展示态容器（图标与虚线分隔原位保留），textarea 顶替展示文字的位置 → 注记文字列两态对齐 -->
    <div v-if="cardEditing" class="tk-note tk-note-editing">
      <WfIcon class="tk-note-ic" name="flag" :size="11" />
      <textarea
        class="tk-note-edit nodrag nowheel"
        data-f="note"
        :value="taskNote"
        placeholder="注记（存疑 / 原因 / 边界…，可留空）"
        rows="1"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
    </div>
    <span class="wf-card-edit-ic"><WfIcon name="pencil" :size="10" /></span>
    <Handle type="source" :position="Position.Right" class="wf-handle" />
    <button class="wf-add src nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'source')">+</button>
  </div>

  <!-- 数据节点：量化事实——大号主数值 + attrs（值全为数值时自动生比例条）+ 口径 + 证据；双击卡片就地编辑 -->
  <div
    v-else-if="variant === 'data'"
    ref="cardRootEl"
    class="wf-node data"
    :class="{editing: cardEditing}"
    @pointerdown="onGesturePointerDown"
    @click="onGestureClick($event, startCardEdit)"
    @dblclick.stop="onGestureDblClick"
    @focusout="onCardFocusOut"
  >
    <button type="button" class="wf-card-fs nodrag" title="全屏预览（保持可编辑）" @click.stop="openCardPreview"><WfIcon name="expand" :size="12" /></button>
    <WfNodeBadge v-if="nodeMark" :type="nodeMark" />
    <Handle type="target" :position="Position.Left" class="wf-handle" />
    <button class="wf-add tgt nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'target')">+</button>
    <div class="dt-head">
      <WfIcon class="dt-ic" name="data" :size="13" />
      <input
        class="dt-title"
        :class="cardEditing ? 'nodrag nowheel' : 'view'"
        data-f="title"
        :value="dataTitle"
        :placeholder="cardEditing ? '数据名称…' : '（未命名数据）'"
        :readonly="!cardEditing"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
    </div>
    <div v-if="cardEditing || dataMetric" class="dt-metric">
      <input
        class="dt-num"
        :class="cardEditing ? 'nodrag nowheel' : 'view'"
        data-f="metric"
        :value="dataMetric"
        :size="contentSize(dataMetric, 1)"
        :placeholder="cardEditing ? '主数值' : ''"
        :readonly="!cardEditing"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
      <input
        v-if="cardEditing || dataUnit"
        class="dt-unit"
        :class="cardEditing ? 'nodrag nowheel' : 'view'"
        data-f="unit"
        :value="dataUnit"
        :size="contentSize(dataUnit, 1)"
        :placeholder="cardEditing ? '单位' : ''"
        :readonly="!cardEditing"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
    </div>
    <!-- attrs 行级就地编辑：每行 [标签, 值] 原地换成两个 input，行几何两态一致；值全为数值时比例条随输入实时重算 -->
    <div v-if="dataAttrs.length || cardEditing" class="dt-attrs" :class="{'has-bars': dataBars}">
      <div v-for="([label, val], i) in dataAttrs" :key="i" class="dt-attr">
        <input
          v-if="cardEditing"
          class="dt-alabel dt-alabel-in nodrag nowheel"
          :value="label"
          placeholder="标签"
          spellcheck="false"
          @input="onAttrInput(i, 0, $event)"
          @keydown.esc.stop="cancelCardEdit"
        />
        <span v-else class="dt-alabel" :title="label">{{ label }}</span>
        <span v-if="dataBars" class="dt-bar"><i :style="{width: `${dataBars[i].pct}%`}" /></span>
        <input
          v-if="cardEditing"
          class="dt-aval dt-aval-in nodrag nowheel"
          :value="val"
          placeholder="值"
          spellcheck="false"
          @input="onAttrInput(i, 1, $event)"
          @keydown.esc.stop="cancelCardEdit"
        />
        <span v-else class="dt-aval">{{ val }}</span>
        <button v-if="cardEditing" class="dt-attr-del nodrag" title="删除这条属性" @click.stop="delAttr(i)" @dblclick.stop>
          <WfIcon name="x" :size="8" />
        </button>
      </div>
      <!-- 编辑态末尾 ghost 行：标签非空时回车 / 失焦提交 -->
      <div v-if="cardEditing" class="dt-attr dt-attr-new">
        <input
          v-model="newAttrLabel"
          class="dt-alabel dt-alabel-in nodrag nowheel"
          placeholder="＋ 标签（回车添加）"
          spellcheck="false"
          @keydown.enter.stop.prevent="commitNewAttr"
          @blur="onAttrGhostBlur"
          @keydown.esc.stop="cancelCardEdit"
        />
        <input
          v-model="newAttrValue"
          class="dt-aval dt-aval-in nodrag nowheel"
          placeholder="值"
          spellcheck="false"
          @keydown.enter.stop.prevent="commitNewAttr"
          @blur="onAttrGhostBlur"
          @keydown.esc.stop="cancelCardEdit"
        />
      </div>
    </div>
    <div v-if="!cardEditing && dataNote" class="dt-note">口径 · {{ dataNote }}</div>
    <!-- 编辑态复用展示态容器：保留「口径 ·」前缀，值文字列与展示态对齐 -->
    <div v-if="cardEditing" class="dt-note dt-note-editing">
      <span class="dt-note-prefix">口径 · </span>
      <textarea
        class="dt-note-edit nodrag nowheel"
        data-f="note"
        :value="dataNote"
        placeholder="这组数怎么统计的…（可留空）"
        rows="1"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
    </div>
    <!-- samples 行级就地编辑：「└ 样例」行原地换成 input，前缀保留 -->
    <div v-if="dataSamples.length || cardEditing" class="dt-samples">
      <div v-for="(s, i) in dataSamples" :key="i" class="dt-sample">
        <span class="dt-sample-prefix">└</span>
        <textarea
          v-if="cardEditing"
          class="dt-sample-input nodrag nowheel"
          :value="s"
          placeholder="代表样例…"
          rows="1"
          spellcheck="false"
          @input="onSampleInput(i, $event)"
          @keydown.esc.stop="cancelCardEdit"
        />
        <template v-else>{{ s }}</template>
        <button v-if="cardEditing" class="dt-sample-del nodrag" title="删除这条样例" @click.stop="delSample(i)" @dblclick.stop>
          <WfIcon name="x" :size="8" />
        </button>
      </div>
      <div v-if="cardEditing" class="dt-sample dt-sample-new">
        <span class="dt-sample-prefix">└</span>
        <input
          v-model="newSampleText"
          class="dt-sample-input nodrag nowheel"
          placeholder="＋ 代表样例（回车添加，可留空）"
          spellcheck="false"
          @keydown.enter.stop.prevent="commitNewSample"
          @blur="commitNewSample"
          @keydown.esc.stop="cancelCardEdit"
        />
      </div>
    </div>
    <span class="wf-card-edit-ic"><WfIcon name="pencil" :size="10" /></span>
    <Handle type="source" :position="Position.Right" class="wf-handle" />
    <button class="wf-add src nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'source')">+</button>
  </div>

  <!-- 结论节点：项目给出的答案——全板最重的一张卡（深色底白字）；双击卡片就地编辑 claim / 要点 / caveat -->
  <div
    v-else-if="variant === 'conclusion'"
    ref="cardRootEl"
    class="wf-node conclusion"
    :class="{editing: cardEditing}"
    @pointerdown="onGesturePointerDown"
    @click="onGestureClick($event, startCardEdit)"
    @dblclick.stop="onGestureDblClick"
    @focusout="onCardFocusOut"
  >
    <button type="button" class="wf-card-fs nodrag" title="全屏预览（保持可编辑）" @click.stop="openCardPreview"><WfIcon name="expand" :size="12" /></button>
    <WfNodeBadge v-if="nodeMark" :type="nodeMark" />
    <Handle type="target" :position="Position.Left" class="wf-handle" />
    <button class="wf-add tgt nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'target')">+</button>
    <div class="cc-head"><WfIcon class="cc-ic" name="conclusion" :size="10" />结论</div>
    <textarea
      class="cc-claim"
      :class="cardEditing ? 'nodrag nowheel' : 'view'"
      data-f="claim"
      :value="conClaim"
      :placeholder="cardEditing ? '项目最终答案 / 结论…' : '（结论待填写）'"
      :readonly="!cardEditing"
      rows="1"
      spellcheck="false"
      @input="onFieldInput"
      @keydown.esc.stop="cancelCardEdit"
    />
    <!-- 要点行级就地编辑：li 原地换成内嵌 textarea，「▸」前缀（li::before）两态保留，行几何一致 -->
    <ul v-if="conPoints.length || cardEditing" class="cc-points">
      <li v-for="(p, i) in conPoints" :key="i" class="cc-point">
        <textarea
          v-if="cardEditing"
          class="cc-point-input nodrag nowheel"
          :value="p"
          placeholder="要点…"
          rows="1"
          spellcheck="false"
          @input="onPointInput(i, $event)"
          @keydown.esc.stop="cancelCardEdit"
        />
        <template v-else>{{ p }}</template>
        <button v-if="cardEditing" class="cc-point-del nodrag" title="删除这条要点" @click.stop="delPoint(i)" @dblclick.stop>
          <WfIcon name="x" :size="8" />
        </button>
      </li>
      <li v-if="cardEditing" class="cc-point cc-point-new">
        <input
          v-model="newPointText"
          class="cc-point-input nodrag nowheel"
          placeholder="＋ 新要点（回车添加，可留空）"
          spellcheck="false"
          @keydown.enter.stop.prevent="commitNewPoint"
          @blur="commitNewPoint"
          @keydown.esc.stop="cancelCardEdit"
        />
      </li>
    </ul>
    <div v-if="!cardEditing && conCaveat" class="cc-caveat"><WfIcon class="cc-caveat-ic" name="warning" :size="12" />{{ conCaveat }}</div>
    <!-- 编辑态复用展示态容器（警示图标与分隔线原位保留），textarea 顶替展示文字的位置 -->
    <div v-if="cardEditing" class="cc-caveat cc-caveat-editing">
      <WfIcon class="cc-caveat-ic" name="warning" :size="12" />
      <textarea
        class="cc-caveat-edit nodrag nowheel"
        data-f="caveat"
        :value="conCaveat"
        placeholder="注意 / 适用边界（可留空）"
        rows="1"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
    </div>
    <span class="wf-card-edit-ic"><WfIcon name="pencil" :size="10" /></span>
    <Handle type="source" :position="Position.Right" class="wf-handle" />
    <button class="wf-add src nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'source')">+</button>
  </div>

  <!--
    分镜段节点（品牌板型：分镜板，generic）：一卡一场戏（SEG），单场承载多分镜——
    结构仿工作项卡：头部（段位 + 时长 + 分镜数徽章）+ 情绪/场景/状态三段 + 分镜清单（行级出线）；双击就地编辑
  -->
  <div
    v-else-if="variant === 'seg'"
    ref="cardRootEl"
    class="wf-node seg"
    :class="{editing: cardEditing}"
    @pointerdown="onGesturePointerDown"
    @click="onGestureClick($event, startCardEdit)"
    @dblclick.stop="onGestureDblClick"
    @focusout="onCardFocusOut"
  >
    <button type="button" class="wf-card-fs nodrag" title="全屏预览（保持可编辑）" @click.stop="openCardPreview"><WfIcon name="expand" :size="12" /></button>
    <WfNodeBadge v-if="nodeMark" :type="nodeMark" />
    <Handle type="target" :position="Position.Left" class="wf-handle" />
    <button class="wf-add tgt nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'target')">+</button>
    <div class="sg-head">
      <input
        class="sg-seg"
        :class="cardEditing ? 'nodrag nowheel' : 'view'"
        data-f="seg"
        :value="segNo"
        :placeholder="cardEditing ? '段位（如 SEG01）' : '（未命名段）'"
        :readonly="!cardEditing"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
      <input
        v-if="cardEditing || segDuration"
        class="sg-dur"
        :class="cardEditing ? 'nodrag nowheel' : 'view'"
        data-f="duration"
        :value="segDuration"
        :size="Math.max(3, segDuration.length + 1)"
        :placeholder="cardEditing ? '时长（如 10s）' : ''"
        :readonly="!cardEditing"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
      <span v-if="segShots.length" class="sg-count" :title="`本场拆成 ${segShots.length} 个分镜`">{{ segShots.length }} 镜</span>
    </div>
    <div v-if="cardEditing || segEmotion" class="sg-field">
      <span class="sg-tag">情绪</span>
      <textarea
        class="sg-text"
        :class="cardEditing ? 'nodrag nowheel' : 'view'"
        data-f="emotion"
        :value="segEmotion"
        :placeholder="cardEditing ? '本场的情绪基调…' : ''"
        :readonly="!cardEditing"
        rows="2"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
    </div>
    <div v-if="cardEditing || segScene" class="sg-field">
      <span class="sg-tag">场景</span>
      <textarea
        class="sg-text"
        :class="cardEditing ? 'nodrag nowheel' : 'view'"
        data-f="scene"
        :value="segScene"
        :placeholder="cardEditing ? '时间地点 / 环境 / 在场人物…' : ''"
        :readonly="!cardEditing"
        rows="2"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
    </div>
    <div v-if="cardEditing || segState" class="sg-field">
      <span class="sg-tag">状态</span>
      <textarea
        class="sg-text"
        :class="cardEditing ? 'nodrag nowheel' : 'view'"
        data-f="state"
        :value="segState"
        :placeholder="cardEditing ? '各人物开场时的姿态 / 位置 / 接续上一场的状态…' : ''"
        :readonly="!cardEditing"
        rows="2"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
    </div>
    <!-- 分镜清单：本场的主角——与工作项卡任务清单同构；行级就地编辑（行几何两态一致，进出编辑零抖动）；
         行右「＋」可从这条分镜拖出连线（edge.sourceHandle = 行 sid，挂生成的画面/视频素材） -->
    <div class="sg-shots">
      <div v-if="!segShots.length && !cardEditing" class="sg-empty">点击进入编辑，把这场戏拆成一条条分镜…</div>
      <div v-for="(s, i) in segShots" :key="s.sid || `${i}-${s.t}`" class="sg-shot">
        <span class="sg-shot-no">{{ String(i + 1).padStart(2, '0') }}</span>
        <textarea
          v-if="cardEditing"
          class="sg-shot-input nodrag nowheel"
          :value="s.t"
          placeholder="时间范围：（景别，运镜）画面描述 [音效]"
          rows="1"
          spellcheck="false"
          @input="onChildInput(i, $event)"
          @keydown.esc.stop="cancelCardEdit"
        />
        <span v-else class="sg-shot-t">{{ s.t }}</span>
        <button v-if="cardEditing" class="sg-shot-del nodrag" title="删除这条分镜" @click.stop="delChild(i)" @dblclick.stop>
          <WfIcon name="x" :size="8" />
        </button>
        <Handle v-if="s.sid" :id="s.sid" type="source" :position="Position.Right" class="wf-sub-handle" />
        <button
          v-if="s.sid"
          class="sg-shot-add nodrag"
          title="从这条分镜拖出连线 / 点击加节点"
          @mousedown.stop.prevent="onSubAddMouseDown($event, s.sid)"
          @click.stop
          @dblclick.stop
        >
          +
        </button>
      </div>
      <!-- 编辑态末尾 ghost 行：回车 / 失焦提交新分镜 -->
      <div v-if="cardEditing" class="sg-shot sg-shot-new">
        <span class="sg-shot-no">{{ String(segShots.length + 1).padStart(2, '0') }}</span>
        <input
          v-model="newChildText"
          class="sg-shot-input nodrag nowheel"
          placeholder="＋ 新分镜（回车添加）"
          spellcheck="false"
          @keydown.enter.stop.prevent="commitNewChild"
          @blur="commitNewChild"
          @keydown.esc.stop="cancelCardEdit"
        />
      </div>
    </div>
    <div v-if="!cardEditing && segNote" class="sg-note"><WfIcon class="sg-note-ic" name="flag" :size="11" />{{ segNote }}</div>
    <!-- 编辑态复用展示态容器（图标与虚线分隔原位保留），textarea 顶替展示文字的位置 → 备注文字列两态对齐 -->
    <div v-if="cardEditing" class="sg-note sg-note-editing">
      <WfIcon class="sg-note-ic" name="flag" :size="11" />
      <textarea
        class="sg-note-edit nodrag nowheel"
        data-f="note"
        :value="segNote"
        placeholder="备注（存疑 / 衔接要点…，可留空）"
        rows="1"
        spellcheck="false"
        @input="onFieldInput"
        @keydown.esc.stop="cancelCardEdit"
      />
    </div>
    <span class="wf-card-edit-ic"><WfIcon name="pencil" :size="10" /></span>
    <Handle type="source" :position="Position.Right" class="wf-handle" />
    <button class="wf-add src nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'source')">+</button>
  </div>

  <!-- 附件节点（.file 带 overflow:hidden，「＋」与徽标放在外层不裁剪容器；Handle 留在节点内不影响连线坐标计算） -->
  <div v-else class="wf-node-wrap">
    <WfNodeBadge v-if="nodeMark" :type="nodeMark" />
    <div class="wf-node file" :class="{'file-is-doc': fileKind === 'html' || fileKind === 'markdown'}">
      <Handle type="target" :position="Position.Left" class="wf-handle" />

      <!--
        空态：点击或拖拽上传。有文件名但无 url（agent 占位卡 / 产物未登记）→ 占位态：
        明示文件名与「产物未关联」，仍保留点击上传作为补救手段，不再显示无信息的上传框
      -->
      <div
        v-if="!hasFile"
        class="file-drop"
        :class="{hover: dragHover > 0, busy: isUploading, placeholder: !!fileName}"
        @click="pickForThisNode"
        @dragenter.prevent="dragHover++"
        @dragover.prevent
        @dragleave="dragHover = Math.max(0, dragHover - 1)"
        @drop.prevent="onFileDrop"
      >
        <template v-if="isUploading">
          <span class="fd-spinner" />
          <span class="fd-t1">上传中…</span>
        </template>
        <template v-else-if="fileName">
          <span :class="'af-ext-' + fileExtGroup(fileName)" class="af-ext fd-ext">{{ fileExt(fileName) }}</span>
          <span class="fd-t1 fd-name" :title="fileName">{{ fileName }}</span>
          <span class="fd-t2">产物未关联 · 点击上传替代，或等 Agent 补齐链接</span>
        </template>
        <template v-else>
          <svg class="fd-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4.4 15.5A7 7 0 1 1 15.7 8.5h1.8a4.5 4.5 0 0 1 2.3 8.4" />
            <path d="M12 12.5V21" />
            <path d="m15.5 16-3.5-3.5L8.5 16" />
          </svg>
          <span class="fd-t1">{{ dragHover > 0 ? '松开即上传' : '点击上传' }}</span>
          <span class="fd-t2">或把文件拖到这里</span>
        </template>
      </div>

      <!-- 有文件：按类型回显 -->
      <template v-else>
        <!-- 图片：缩略图常驻回显，按住即拖节点；预览只走右上角全屏按钮，不干扰板子编辑 -->
        <div v-if="fileKind === 'image' && mediaSrc && !imgError" class="file-media file-media-img" title="按住拖动 · 右上角全屏预览">
          <img :src="mediaSrc" :alt="fileName || '图片'" draggable="false" @error="imgError = true" />
          <button type="button" class="file-fs-btn nodrag" title="全屏预览" @click.stop="openFullPreview"><WfIcon name="expand" :size="14" /></button>
        </div>
        <!-- 图片加载失败：产物链接失效的说明态（破图无信息，用户不知道该找谁修） -->
        <div v-else-if="fileKind === 'image' && imgError" class="file-chip file-chip-broken">
          <span class="af-ext af-ext-img">img</span>
          <span class="file-chip-note">图片加载失败 · 产物链接可能已失效</span>
        </div>
        <!-- 视频 / 音频：节点内直接播放（controls 含拖拽语义，保留 nodrag 不被节点拖拽劫持） -->
        <div v-else-if="fileKind === 'video' && mediaSrc" class="file-media nodrag">
          <video :src="mediaSrc" controls preload="metadata" />
        </div>
        <div v-else-if="fileKind === 'audio' && mediaSrc" class="file-media nodrag">
          <audio :src="mediaSrc" controls />
        </div>
        <!-- HTML 页面：卡内 iframe 渲染（同 qa-glass 的 HtmlRender），固定高预览窗内滚动，全屏走共享弹层。
             外层不带 nodrag：默认 shield 罩住 iframe（iframe 吞鼠标事件会拖不动节点），事件经 shield 冒泡到节点即可拖拽；
             点「互动」摘掉 shield 进交互态（操作页面内容），此时不可拖、点「退出互动」恢复 -->
        <div v-else-if="fileKind === 'html'" class="file-media file-media-html nowheel" :class="{live: htmlInteractive}">
          <div v-if="htmlLoading" class="file-html-state">页面载入中…</div>
          <div v-else-if="htmlError" class="file-html-state file-html-err">加载失败</div>
          <template v-else-if="htmlText">
            <HtmlRender :html="htmlText" scrollable />
            <div v-if="!htmlInteractive" class="file-html-shield" title="按住拖动卡片 · 点「互动」操作页面内容" />
            <button type="button" class="file-fs-btn nodrag" title="全屏预览" @click.stop="openFullPreview"><WfIcon name="expand" :size="14" /></button>
            <button
              type="button"
              class="file-live-btn nodrag"
              :title="htmlInteractive ? '退出互动（恢复可拖动）' : '进入互动模式（滚动 / 点击页面内容）'"
              @click.stop="htmlInteractive = !htmlInteractive"
            >
              {{ htmlInteractive ? '退出互动' : '互动' }}
            </button>
          </template>
        </div>
        <!-- Markdown 文档：卡内联渲染（同预览弹层的 marked 渲染），固定高预览窗内滚动，全屏走共享弹层。
             nodrag：阅览要划字 / 滚动，内容区不参与拖拽（同视频 / 音频约定），拖卡走底部文件名栏等区域 -->
        <div v-else-if="fileKind === 'markdown'" class="file-media file-media-md nodrag nowheel">
          <div v-if="mdLoading" class="file-md-state">载入中…</div>
          <div v-else-if="mdError" class="file-md-state file-md-err">加载失败</div>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div v-else-if="mdHtml" class="file-md-body" v-html="mdHtml" />
          <button type="button" class="file-fs-btn nodrag" title="全屏预览" @click.stop="openFullPreview"><WfIcon name="expand" :size="14" /></button>
        </div>
        <!-- office / csv：被动展示 chip + 右上角全屏按钮打开预览（chip 本体不响应点击，不干扰板子编辑） -->
        <div v-else-if="fileKind === 'office' || fileKind === 'csv'" class="file-doc-chip">
          <span :class="'af-ext-' + fileExtGroup(fileName)" class="af-ext">{{ fileExt(fileName) }}</span>
          <span class="file-chip-name">{{ fileName || '文档' }}</span>
          <button type="button" class="file-fs-btn nodrag" title="预览" @click.stop="openFullPreview"><WfIcon name="expand" :size="14" /></button>
        </div>
        <!-- 其他文件：彩色扩展名徽章（纯展示，可作拖拽把手） -->
        <div v-else class="file-chip">
          <span :class="'af-ext-' + fileExtGroup(fileName)" class="af-ext">{{ fileExt(fileName) }}</span>
          <span class="file-chip-note">该类型暂不支持预览</span>
        </div>
        <div class="file-foot">
          <span class="file-name" :title="fileName">{{ fileName || '附件' }}</span>
          <span v-if="data?.size != null" class="file-size">{{ formatFileSize(data.size) }}</span>
          <a v-if="mediaSrc" class="file-dl nodrag" :href="downloadSrc" :download="fileName || undefined" target="_blank" rel="noopener noreferrer" title="下载">
            <WfIcon name="download" :size="13" />
          </a>
        </div>
      </template>

      <Handle type="source" :position="Position.Right" class="wf-handle" />
    </div>
    <button class="wf-add tgt nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'target')">+</button>
    <button class="wf-add src nodrag" title="拖出连线 / 点击加节点" @mousedown="onAddMouseDown($event, 'source')">+</button>
  </div>
</template>

<style scoped>
/* ── 设计语言与 nian 页对齐：QA 蓝玻璃风 ──
   性能约定（同 nian）：节点数量可多，禁 backdrop-filter / 无限动画，
   阴影单层柔化，过渡只动 border / shadow */
.wf-node {
  position: relative;
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
  color: #0f172a;
  transition: border-color 0.18s, box-shadow 0.18s;
}
/* 附件节点外层容器：不裁剪，供「＋」绝对定位 */
.wf-node-wrap {
  position: relative;
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
}

/* ── handle「＋」快捷入口：鼠标靠近节点（父页 proximity 检测打 .wf-near）时浮现，按住拖出连线 / 点击弹菜单 ── */
.wf-add {
  position: absolute;
  top: 50%;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 1px solid rgba(30, 64, 175, 0.25);
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.95);
  color: #1e40af;
  font-size: 13px;
  font-weight: 700;
  line-height: 1;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 2px 8px -2px rgba(30, 64, 175, 0.3);
  cursor: pointer;
  opacity: 0;
  transform: translate(0, -50%) scale(0.4);
  transition:
    opacity 0.16s,
    transform 0.16s cubic-bezier(0.34, 1.56, 0.64, 1),
    background 0.14s,
    border-color 0.14s,
    color 0.14s,
    box-shadow 0.14s;
}
.wf-add.src {
  right: -26px;
}
.wf-add.tgt {
  left: -26px;
}
.vue-flow__node.wf-near .wf-add {
  opacity: 1;
  transform: translate(0, -50%) scale(1);
}
.vue-flow__node.wf-near .wf-add:hover {
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 55%, #0ea5e9 100%);
  border-color: transparent;
  color: #fff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.35),
    0 4px 14px -2px rgba(30, 64, 175, 0.5);
  transform: translate(0, -50%) scale(1.18);
}
.vue-flow__node.wf-near .wf-add:active {
  transform: translate(0, -50%) scale(1.02);
}

/* ── 开始 / 结束胶囊：aurora 渐变（开始=蓝青极光，结束=薄荷青） ── */
.cap {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 22px;
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: 999px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.01em;
  color: #fff;
  white-space: nowrap;
  cursor: grab;
}
.cap-start {
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 45%, #0ea5e9 100%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.35),
    0 4px 16px -4px rgba(30, 64, 175, 0.5);
}
.cap-start:hover {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 8px 24px -6px rgba(30, 64, 175, 0.6);
}
.cap-end {
  background: linear-gradient(110deg, #0e7490 0%, #10b981 100%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.35),
    0 4px 16px -4px rgba(16, 185, 129, 0.45);
}
.cap-end:hover {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 8px 24px -6px rgba(16, 185, 129, 0.55);
}
.vue-flow__node.selected .cap {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 0 0 3px rgba(30, 64, 175, 0.14),
    0 8px 24px -6px rgba(30, 64, 175, 0.4);
}
.cap-ic {
  flex-shrink: 0;
  opacity: 0.95;
}
.cap-label {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 标签永远在位撑开胶囊；编辑时隐藏（保留占位），输入框浮在其上 → 切换零形变 */
.cap-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
  max-width: 220px;
}
.cap-label.ghost {
  visibility: hidden;
}
.cap-input {
  position: absolute;
  left: -8px;
  right: -8px;
  top: 50%;
  transform: translateY(-50%);
  min-width: calc(100% + 16px);
  border: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.75);
  background: rgba(255, 255, 255, 0.18);
  border-radius: 4px 4px 0 0;
  color: #fff;
  font: inherit;
  font-weight: 700;
  text-align: center;
  outline: none;
  padding: 2px 6px;
  box-sizing: border-box;
  cursor: text;
}
.cap-input::placeholder {
  color: rgba(255, 255, 255, 0.6);
}

/* ── 文本节点：叙述卡，全板最轻的一级（安静退到背景层，前景视觉重量让给工作项/数据/结论卡） ── */
.text {
  background: rgba(255, 255, 255, 0.3);
  border: 1px solid rgba(15, 23, 42, 0.06);
  border-radius: 14px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
  min-width: 168px;
  max-width: 320px;
  padding: 4px 5px;
  cursor: grab;
  transition: background 0.15s, border-color 0.15s, box-shadow 0.15s;
}
.text:hover {
  background: rgba(255, 255, 255, 0.55);
  border-color: rgba(15, 23, 42, 0.12);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px -16px rgba(15, 23, 42, 0.14);
}
.vue-flow__node.selected .text {
  background: rgba(255, 255, 255, 0.72);
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1), 0 8px 24px -16px rgba(15, 23, 42, 0.16);
}
/* textarea 常驻：展示 / 编辑共用同一元素与盒模型，切换不产生任何尺寸变化 */
.text-area {
  display: block;
  width: 100%;
  min-height: 36px;
  padding: 6px 10px;
  border: none;
  background: transparent;
  outline: none;
  resize: none;
  overflow-y: auto;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.6;
  color: #475569;
  box-sizing: border-box;
  cursor: text;
}
/* 两态都不限高：高度始终随内容撑开（JS autoResize 写死 scrollHeight），进出编辑高度零变化。
   曾经编辑态限高 240px——长文卡一进编辑整卡骤缩、出编辑又弹回，是典型的抖动源，故移除 */
.text-area::placeholder {
  color: #94a3b8;
}
/* 卡内滚动：细滚动条提示「还有内容」，悬停加深 */
.text-area {
  scrollbar-width: thin;
  scrollbar-color: rgba(30, 64, 175, 0.24) transparent;
}
.text-area::-webkit-scrollbar {
  width: 5px;
}
.text-area::-webkit-scrollbar-track {
  background: transparent;
}
.text-area::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.2);
  border-radius: 3px;
}
.text-area::-webkit-scrollbar-thumb:hover {
  background: rgba(30, 64, 175, 0.42);
}
/* 展示态：对指针事件透明 → 事件落到节点根元素，整体可拖拽；占位提示灰斜体 */
.text-area.view {
  pointer-events: none;
}
.text-area.view::placeholder {
  font-style: italic;
  font-weight: 400;
}

/* ── 附件节点：紫色调玻璃卡片（idea 紫 #7c3aed，同 nian 灵感色系） ── */
.file {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(124, 58, 237, 0.16);
  border-radius: 14px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 8px 24px -12px rgba(124, 58, 237, 0.18);
  overflow: hidden;
  width: 224px;
}
.file:hover {
  border-color: rgba(124, 58, 237, 0.34);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 1px 2px rgba(15, 23, 42, 0.05),
    0 12px 32px -12px rgba(124, 58, 237, 0.26);
}
.vue-flow__node.selected .file {
  border-color: #7c3aed;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 0 0 3px rgba(124, 58, 237, 0.12),
    0 12px 32px -12px rgba(124, 58, 237, 0.26);
}

/* 空态：点击 / 拖拽上传 */
.file-drop {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 128px;
  margin: 8px;
  border: 1.5px dashed rgba(124, 58, 237, 0.35);
  border-radius: 10px;
  background: rgba(124, 58, 237, 0.03);
  color: #7c3aed;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.file-drop:hover {
  border-color: rgba(124, 58, 237, 0.6);
  background: rgba(124, 58, 237, 0.06);
}
.file-drop.hover {
  border-style: solid;
  border-color: #7c3aed;
  background: rgba(124, 58, 237, 0.1);
}
.fd-ic {
  width: 27px;
  height: 27px;
}
.fd-t1 {
  font-size: 12.5px;
  font-weight: 700;
  line-height: 1;
}
.fd-t2 {
  font-size: 10.5px;
  color: #a78bfa;
  line-height: 1;
}
.fd-spinner {
  width: 22px;
  height: 22px;
  border: 2.5px solid rgba(124, 58, 237, 0.2);
  border-top-color: #7c3aed;
  border-radius: 50%;
  animation: fd-spin 0.7s linear infinite;
}
@keyframes fd-spin {
  to {
    transform: rotate(360deg);
  }
}
.file-drop.busy {
  cursor: progress;
  border-style: solid;
  border-color: rgba(124, 58, 237, 0.4);
}
/* 占位态：agent 已给文件名但产物链接未登记——降饱和的虚线框，明示文件名与缺失原因 */
.file-drop.placeholder {
  border-color: rgba(124, 58, 237, 0.22);
  background: rgba(124, 58, 237, 0.02);
  color: #94a3b8;
}
.file-drop.placeholder:hover {
  border-color: rgba(124, 58, 237, 0.5);
  background: rgba(124, 58, 237, 0.05);
}
.fd-ext {
  margin-bottom: 2px;
}
.fd-name {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #64748b;
}

/* 回显区 */
.file-media {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f8fafc;
  min-height: 56px;
}
.file-media img {
  display: block;
  width: 100%;
  max-height: 200px;
  object-fit: contain;
  background: #fff;
}
.file-media video {
  display: block;
  width: 100%;
  max-height: 200px;
  background: #0f172a;
}
.file-media audio {
  width: 100%;
  padding: 14px 10px;
  box-sizing: border-box;
}
/* 可阅览文档（HTML / markdown）：卡内联渲染，加宽卡身承载固定高预览窗 */
.file-is-doc {
  width: 300px;
}
.file-media-html {
  position: relative;
  display: block;
  height: 300px;
  overflow: hidden;
  background: #fff;
}
.file-html-state {
  padding: 30px 0;
  font-size: 11px;
  color: #94a3b8;
  text-align: center;
}
.file-html-err {
  color: #b91c1c;
}
/* 拖动 shield：默认罩住 iframe（iframe 吞鼠标事件会拖不动节点）——透明罩层，事件冒泡到节点即可按住拖动；
   互动模式（.live）摘掉，iframe 内容可滚动 / 点击 */
.file-html-shield {
  position: absolute;
  inset: 0;
  z-index: 1;
  cursor: grab;
}
.file-html-shield:active {
  cursor: grabbing;
}
/* 互动模式切换：HTML 预览窗右下角浮现，深色玻璃同 file-fs-btn 语言；互动中常驻（要能退出） */
.file-live-btn {
  position: absolute;
  right: 8px;
  bottom: 8px;
  z-index: 2;
  padding: 4px 10px;
  border: none;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.55);
  color: #fff;
  font-family: inherit;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.18s, background 0.18s;
}
.file:hover .file-live-btn,
.file-media-html.live .file-live-btn {
  opacity: 1;
}
.file-live-btn:hover {
  background: rgba(15, 23, 42, 0.78);
}

/* Markdown 文档：卡内联渲染，固定高预览窗自滚动（nodrag + nowheel：划字 / 滚动不打扰画布） */
.file-media-md {
  position: relative;
  display: block;
  height: 300px;
  overflow-y: auto;
  padding: 12px 14px;
  background: #fff;
}
.file-md-state {
  padding: 30px 0;
  font-size: 11px;
  color: #94a3b8;
  text-align: center;
}
.file-md-err {
  color: #b91c1c;
}
/* 卡内 md 排版（预览弹层 apm-md 的小一号变体，适配 300px 卡宽） */
.file-md-body {
  font-size: 12.5px;
  line-height: 1.75;
  color: #0f172a;
  word-break: break-word;
}
.file-md-body :deep(h1),
.file-md-body :deep(h2),
.file-md-body :deep(h3),
.file-md-body :deep(h4) {
  margin: 14px 0 8px;
  font-weight: 700;
  line-height: 1.4;
  color: #0f172a;
}
.file-md-body :deep(h1) {
  font-size: 17px;
  padding-bottom: 6px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.12);
}
.file-md-body :deep(h2) {
  font-size: 15px;
  padding-bottom: 4px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}
.file-md-body :deep(h3) {
  font-size: 13.5px;
}
.file-md-body :deep(h4) {
  font-size: 12.5px;
}
.file-md-body :deep(p) {
  margin: 0 0 10px;
}
.file-md-body :deep(strong) {
  font-weight: 700;
  color: #0f172a;
}
.file-md-body :deep(ul),
.file-md-body :deep(ol) {
  margin: 0 0 10px;
  padding-left: 20px;
}
.file-md-body :deep(li) {
  margin: 3px 0;
}
.file-md-body :deep(blockquote) {
  margin: 0 0 10px;
  padding: 2px 10px;
  border-left: 3px solid rgba(124, 58, 237, 0.35);
  color: #475569;
}
.file-md-body :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.06);
  font-size: 11.5px;
}
.file-md-body :deep(pre) {
  margin: 0 0 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.05);
  overflow-x: auto;
}
.file-md-body :deep(pre code) {
  padding: 0;
  background: none;
}
.file-md-body :deep(table) {
  width: 100%;
  margin: 0 0 10px;
  border-collapse: collapse;
  font-size: 11.5px;
}
.file-md-body :deep(th),
.file-md-body :deep(td) {
  padding: 5px 8px;
  border: 1px solid rgba(15, 23, 42, 0.1);
  text-align: left;
}
.file-md-body :deep(th) {
  background: rgba(15, 23, 42, 0.04);
  font-weight: 600;
}
.file-md-body :deep(a) {
  color: #2563eb;
  text-decoration: none;
}
.file-md-body :deep(hr) {
  margin: 12px 0;
  border: none;
  border-top: 1px solid rgba(15, 23, 42, 0.1);
}
.file-md-body > :deep(:first-child) {
  margin-top: 0;
}
.file-md-body > :deep(:last-child) {
  margin-bottom: 0;
}

/* 卡片全屏预览入口：与附件卡 file-fs-btn 完全同款（卡内右上角、hover 卡片浮现、深色玻璃白图标）——
   附件卡的 ⛶ 开的是文件内容预览，其余卡型由此入口开节点预览弹窗 */
.wf-card-fs {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 6;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.55);
  color: #fff;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.18s, background 0.18s;
}
.wf-node:hover .wf-card-fs {
  opacity: 1;
}
.wf-card-fs:hover {
  background: rgba(15, 23, 42, 0.78);
}
/* 深色底（结论卡）与渐变胶囊（开始 / 结束）上深色按钮会糊进背景 → 白玻璃，按卡底自适应对比度 */
.conclusion .wf-card-fs,
.cap .wf-card-fs {
  background: rgba(255, 255, 255, 0.24);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.28);
}
.conclusion .wf-card-fs:hover,
.cap .wf-card-fs:hover {
  background: rgba(255, 255, 255, 0.4);
}
/* 全屏预览按钮：卡内右上角，hover 卡片浮现——图片 / 文档 / HTML 统一的唯一预览入口
   （预览是显式动作，卡面其余区域不触发，拖拽 / 选中节点不会误开弹层） */
.file-fs-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.55);
  color: #fff;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, background 0.15s;
}
.file:hover .file-fs-btn {
  opacity: 1;
}
.file-fs-btn:hover {
  background: rgba(15, 23, 42, 0.78);
}
/* 图片缩略图：常驻回显，按住拖节点（预览走右上角全屏按钮） */
.file-media-img {
  position: relative;
  cursor: grab;
}
/* 其他文件：徽章 chip（移植 qa-glass 彩色扩展名徽章，缩小适配节点） */
.file-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 12px;
  background: #f8fafc;
}
.file-chip-note {
  font-size: 11px;
  color: #94a3b8;
}
/* 产物链接失效的说明态：琥珀色提示，区别于普通灰字 */
.file-chip-broken .file-chip-note {
  color: #b45309;
}
.af-ext {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 26px;
  height: 16px;
  padding: 0 4px;
  border-radius: 3px;
  font-size: 8.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  color: #fff;
  background: #94a3b8;
  flex-shrink: 0;
}
.af-ext-pdf {
  background: #dc2626;
}
.af-ext-doc {
  background: #2563eb;
}
.af-ext-sheet {
  background: #16a34a;
}
.af-ext-ppt {
  background: #ea580c;
}
.af-ext-img {
  background: #7c3aed;
}
.af-ext-zip {
  background: #854d0e;
}
.af-ext-media {
  background: #0891b2;
}
.af-ext-code {
  background: #475569;
}
.af-ext-text {
  background: #64748b;
}
.af-ext-other {
  background: #94a3b8;
}
/* markdown / office / csv 展示 chip（qa-glass q-att-md 风格，紫色文件卡语境）：
   被动展示不响应点击（可作拖拽把手），预览走右上角全屏按钮 */
.file-doc-chip {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 14px 44px 14px 12px;
  background: #f8fafc;
  text-align: left;
  box-sizing: border-box;
}
.file-chip-name {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  font-weight: 600;
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 底栏作为拖拽把手（移动节点），下载链接单独 nodrag 保证可点 */
.file-foot {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 11px;
  border-top: 1px solid rgba(124, 58, 237, 0.1);
  cursor: grab;
}
.file-name {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  font-weight: 600;
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-size {
  flex-shrink: 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #94a3b8;
  font-variant-numeric: tabular-nums;
}
.file-dl {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 6px;
  font-size: 13px;
  color: #7c3aed;
  text-decoration: none;
  transition: background 0.15s;
}
.file-dl:hover {
  background: rgba(124, 58, 237, 0.1);
}

/* ── 人工核查节点：琥珀色玻璃卡（「需要人注意」的专属色，区别于文本白 / 附件紫） ── */
.review {
  background: rgba(255, 251, 235, 0.95);
  border: 1px solid rgba(217, 119, 6, 0.22);
  border-radius: 14px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 8px 24px -12px rgba(217, 119, 6, 0.22);
  width: 240px;
  padding: 10px 12px;
  box-sizing: border-box;
  cursor: grab;
}
.review:hover {
  border-color: rgba(217, 119, 6, 0.42);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 1px 2px rgba(15, 23, 42, 0.05),
    0 12px 32px -12px rgba(217, 119, 6, 0.3);
}
.vue-flow__node.selected .review {
  border-color: #d97706;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 0 0 3px rgba(217, 119, 6, 0.12),
    0 12px 32px -12px rgba(217, 119, 6, 0.3);
}
.rv-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 7px;
}
.rv-ic {
  flex-shrink: 0;
  color: #d97706;
}
.rv-title {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.02em;
  color: #b45309;
}
.rv-badge {
  margin-left: auto;
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 999px;
}
.rv-badge.wait {
  background: rgba(217, 119, 6, 0.14);
  color: #b45309;
}
.rv-badge.done {
  background: rgba(16, 185, 129, 0.16);
  color: #047857;
}
/* 已收口锁定：中性石板灰，徽章内小锁与文字紧排 */
.rv-badge.locked {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  background: rgba(100, 116, 139, 0.14);
  color: #475569;
}
.rv-q {
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.55;
  color: #0f172a;
  margin-bottom: 9px;
  word-break: break-word;
}
.rv-opts {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.rv-opt {
  text-align: left;
  padding: 7px 10px;
  border: 1px solid rgba(217, 119, 6, 0.25);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.75);
  color: #334155;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, color 0.15s;
}
.rv-opt:hover {
  border-color: #d97706;
  background: #fff;
}
.rv-opt.sel {
  border-color: #d97706;
  background: linear-gradient(110deg, rgba(217, 119, 6, 0.16), rgba(245, 158, 11, 0.1));
  color: #92400e;
}
/* 锁定后的选项只读：不再响应点击（pickReviewOption 内也有兜底） */
.rv-opts.locked .rv-opt {
  pointer-events: none;
  opacity: 0.78;
}
/* 锁定后自由输入的答案改为静态回显 */
.rv-ans {
  padding: 7px 10px;
  border: 1px solid rgba(217, 119, 6, 0.18);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.6);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.5;
  color: #334155;
  word-break: break-word;
}
.rv-input {
  display: block;
  width: 100%;
  padding: 7px 10px;
  border: 1px solid rgba(217, 119, 6, 0.25);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.8);
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: #0f172a;
  outline: none;
  box-sizing: border-box;
  transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
}
.rv-input:focus {
  border-color: #d97706;
  background: #fff;
  box-shadow: 0 0 0 3px rgba(217, 119, 6, 0.1);
}
.rv-input::placeholder {
  color: #94a3b8;
}

/* ── 工作项节点：白底卡片，核心是卡内编号任务清单（淡蓝列表区 + 计数徽章） ── */
.task {
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(15, 23, 42, 0.1);
  border-radius: 14px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 10px 28px -14px rgba(15, 23, 42, 0.18);
  width: 232px;
  padding: 11px 13px;
  box-sizing: border-box;
  cursor: grab;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.task:hover {
  border-color: rgba(37, 99, 235, 0.35);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 14px 34px -14px rgba(37, 99, 235, 0.22);
}
.vue-flow__node.selected .task {
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12), 0 14px 34px -14px rgba(37, 99, 235, 0.22);
}
.tk-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}
/* 计数徽章：这张卡拆成了几条任务——工作项卡「多任务」定位的视觉锚 */
.tk-count {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: #2563eb;
  color: #fff;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  line-height: 1.5;
  letter-spacing: 0.03em;
  white-space: nowrap;
}
.tk-title {
  flex: 1;
  min-width: 0;
  font-size: 13.5px;
  font-weight: 800;
  line-height: 1.35;
  color: #0f172a;
  word-break: break-word;
}
.tk-summary {
  margin-top: 6px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.55;
  color: #475569;
  word-break: break-word;
}
.tk-note {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed rgba(15, 23, 42, 0.08);
  font-size: 11px;
  line-height: 1.5;
  color: #94a3b8;
  word-break: break-word;
}
.tk-note-ic {
  flex-shrink: 0;
  margin-top: 1.5px;
  color: #b6c2d4;
}

/* ── 数据节点：青色系（#0891b2），数字是主角——大号等宽主数值 + 属性行（全数值时自动比例条） ── */
.data {
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(8, 145, 178, 0.2);
  border-radius: 14px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 10px 28px -14px rgba(8, 145, 178, 0.26);
  width: 252px;
  padding: 11px 13px;
  box-sizing: border-box;
  cursor: grab;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.data:hover {
  border-color: rgba(8, 145, 178, 0.42);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 14px 34px -14px rgba(8, 145, 178, 0.32);
}
.vue-flow__node.selected .data {
  border-color: #0891b2;
  box-shadow: 0 0 0 3px rgba(8, 145, 178, 0.12), 0 14px 34px -14px rgba(8, 145, 178, 0.32);
}
.dt-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.02em;
  color: #155e75;
  word-break: break-word;
}
.dt-ic {
  flex-shrink: 0;
  color: #0891b2;
}
.dt-metric {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 2px 6px;
  margin: 2px 0 8px;
}
.dt-num {
  font-family: 'JetBrains Mono', monospace;
  font-size: 30px;
  font-weight: 800;
  line-height: 1;
  letter-spacing: -0.02em;
  color: #0f172a;
  font-variant-numeric: tabular-nums;
  word-break: break-word;
}
.dt-unit {
  font-size: 12px;
  font-weight: 700;
  color: #64748b;
}
.dt-attrs {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
/* 属性行：标签/值都可换行——短内容保持一行，长内容值折到下一行占满，绝不溢出卡外 */
.dt-attr {
  position: relative; /* 行级编辑的删除浮层锚在行上 */
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 2px 8px;
  font-size: 11.5px;
}
.dt-alabel {
  flex: 0 1 auto;
  min-width: 0;
  color: #64748b;
  font-weight: 600;
  white-space: normal;
  word-break: break-word;
}
.dt-bar {
  flex: 1;
  min-width: 20px;
  height: 5px;
  border-radius: 3px;
  background: rgba(8, 145, 178, 0.1);
  overflow: hidden;
}
.dt-bar i {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: linear-gradient(90deg, #0891b2, #22d3ee);
  transition: width 0.3s;
}
.dt-aval {
  flex: 1 1 auto;
  min-width: 48px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11.5px;
  font-weight: 700;
  line-height: 1.5;
  color: #0f172a;
  font-variant-numeric: tabular-nums;
  white-space: normal;
  word-break: break-word;
}
/* 全数值行（有比例条）：保持原来的单行紧致布局——标签定宽省略、条撑开、值贴右 */
.dt-attrs.has-bars .dt-attr {
  flex-wrap: nowrap;
  align-items: center;
}
.dt-attrs.has-bars .dt-alabel {
  flex: 0 0 auto;
  min-width: 44px;
  max-width: 88px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dt-attrs.has-bars .dt-aval {
  flex: 0 0 auto;
  min-width: 0;
  white-space: nowrap;
}
.dt-note {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed rgba(8, 145, 178, 0.18);
  font-size: 11px;
  line-height: 1.5;
  color: #94a3b8;
  word-break: break-word;
}
.dt-samples {
  margin-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.dt-sample {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  line-height: 1.5;
  color: #64748b;
  word-break: break-all;
}

/* ── 结论节点：全板最重的一张卡（深色底白字），一眼锁定「答案在这」 ── */
.conclusion {
  background: linear-gradient(150deg, #0f172a 0%, #1e3a8a 135%);
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 14px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.1), 0 14px 40px -12px rgba(15, 23, 42, 0.5);
  width: 284px;
  padding: 12px 15px;
  box-sizing: border-box;
  cursor: grab;
  color: #fff;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.conclusion:hover {
  border-color: rgba(148, 163, 184, 0.45);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12), 0 18px 48px -12px rgba(15, 23, 42, 0.6);
}
.vue-flow__node.selected .conclusion {
  border-color: #60a5fa;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12), 0 0 0 3px rgba(96, 165, 250, 0.22), 0 18px 48px -12px rgba(15, 23, 42, 0.6);
}
.cc-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 7px;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.14em;
  color: rgba(255, 255, 255, 0.55);
}
.cc-ic {
  flex-shrink: 0;
  color: #38bdf8;
}
.cc-claim {
  font-size: 15px;
  font-weight: 800;
  line-height: 1.5;
  color: #fff;
  word-break: break-word;
}
.cc-points {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.cc-points li {
  position: relative;
  padding-left: 14px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.55;
  color: rgba(255, 255, 255, 0.85);
  word-break: break-word;
}
.cc-points li::before {
  content: '▸';
  position: absolute;
  left: 0;
  color: #38bdf8;
  font-size: 11px;
}
.cc-caveat {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  margin-top: 9px;
  padding-top: 7px;
  border-top: 1px solid rgba(255, 255, 255, 0.12);
  font-size: 11px;
  line-height: 1.5;
  color: #fcd34d;
  word-break: break-word;
}
.cc-caveat-ic {
  flex-shrink: 0;
  margin-top: 1px;
}

/* ── 卡片就地编辑（task / data / conclusion）：字段元素常驻，展示态对事件透明 → 整卡可拖；双击进编辑 ── */
.task input,
.task textarea,
.data input,
.data textarea,
.conclusion input,
.conclusion textarea {
  display: block;
  width: 100%;
  margin: 0;
  padding: 0;
  border: none;
  background: transparent;
  outline: none;
  resize: none;
  overflow: hidden; /* 高度随内容自动撑开（resizeTa），不出内部滚动条 */
  font-family: inherit;
  font-size: inherit;
  color: inherit;
  box-sizing: border-box;
}
.task .view,
.data .view,
.conclusion .view {
  pointer-events: none; /* 展示态：事件穿透到卡片 → 整体可拖拽 */
}
/* hover 浮现小编辑提示（SVG 铅笔）；编辑中隐藏 */
.task,
.data,
.conclusion {
  position: relative;
}
.wf-card-edit-ic {
  position: absolute;
  top: 8px;
  right: 10px;
  display: flex;
  color: #94a3b8;
  opacity: 0;
  transition: opacity 0.15s;
  pointer-events: none;
}
/* 工作项卡右上角有任务数徽章，编辑提示避让到右下角 */
.task .wf-card-edit-ic {
  top: auto;
  bottom: 8px;
}
/* 结论卡深色底，提示色调和 */
.conclusion .wf-card-edit-ic {
  color: rgba(255, 255, 255, 0.5);
}
.task:hover .wf-card-edit-ic,
.data:hover .wf-card-edit-ic,
.conclusion:hover .wf-card-edit-ic,
.seg:hover .wf-card-edit-ic {
  opacity: 0.85;
}
.task.editing .wf-card-edit-ic,
.data.editing .wf-card-edit-ic,
.conclusion.editing .wf-card-edit-ic,
.seg.editing .wf-card-edit-ic {
  opacity: 0;
}
/* 编辑态：边框提亮 + 取消抓取光标，暗示「正在输入」 */
.task.editing {
  border-color: rgba(37, 99, 235, 0.45);
  cursor: auto;
}
.data.editing {
  border-color: rgba(8, 145, 178, 0.55);
  cursor: auto;
}
.conclusion.editing {
  border-color: rgba(96, 165, 250, 0.6);
  cursor: auto;
}
/* 占位提示：展示态灰斜体（同文本卡）；编辑态正常灰 */
.task .view::placeholder,
.data .view::placeholder,
.conclusion .view::placeholder {
  font-style: italic;
  font-weight: 400;
  color: #94a3b8;
}
.task input::placeholder,
.task textarea::placeholder,
.data input::placeholder,
.data textarea::placeholder {
  color: #b8c3d5;
}
.conclusion input::placeholder,
.conclusion textarea::placeholder {
  color: rgba(255, 255, 255, 0.45);
}
.conclusion input,
.conclusion textarea {
  caret-color: #fff;
}
/* 工作项字段（input/textarea 复用展示排版） */
.tk-summary {
  word-break: break-word;
}
/* ── 编辑态同槽位对齐：注记 / caveat / 口径字段编辑态复用展示态容器（图标、前缀、分隔线原位保留），
   textarea 顶替展示文字的位置（flex:1）→ 进出编辑文字列与块位置不偏移 ── */
.tk-note-editing textarea,
.sg-note-editing textarea,
.cc-caveat-editing textarea,
.dt-note-editing textarea {
  flex: 1;
  min-width: 0;
  width: auto; /* 基础 reset 给了 width:100%，flex 行内收回剩余槽位 */
}
.dt-note-editing {
  display: flex;
  align-items: flex-start;
}
.dt-note-prefix {
  flex-shrink: 0;
  white-space: pre; /* 尾随空格不塌缩，值文字列对齐展示态「口径 · 值」 */
}
/* 主数值 / 单位：size 属性两态统一随内容，编辑态最小宽兜住占位符，避免宽度跳变 */
.data.editing .dt-num {
  min-width: 6ch;
}
.data.editing .dt-unit {
  min-width: 4ch;
}
/* 卡内任务清单（subs）：这张卡的主角——淡蓝区块 + 等宽编号；行不响应点击（可从行上拖卡片），连线入口只在行右「＋」 */
.tk-subs {
  margin-top: 8px;
  padding: 6px 7px;
  border-radius: 9px;
  background: rgba(37, 99, 235, 0.05);
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.tk-empty {
  padding: 4px 4px;
  font-size: 10.5px;
  line-height: 1.5;
  color: #94a3b8;
}
.tk-sub {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 3.5px 20px 3.5px 5px;
  border-radius: 6px;
  font-size: 11.5px;
  line-height: 1.45;
  color: #334155;
  transition: background 0.15s;
}
.tk-sub:hover {
  background: rgba(37, 99, 235, 0.07);
}
.tk-sub-no {
  flex: none;
  margin-top: 1px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  line-height: 1.5;
  color: #2563eb;
  font-variant-numeric: tabular-nums;
}
.tk-sub-t {
  word-break: break-word;
}
/* 任务行级 handle：同 wf-handle 约定——视觉隐藏但保留几何，连线从该行右侧出发；出线入口统一在行右「＋」 */
.wf-sub-handle {
  width: 6px;
  height: 6px;
  opacity: 0;
  pointer-events: none;
}
/* 任务行「＋」：hover 该行浮现（比节点级 wf-add 小一号，贴着行右侧） */
.tk-sub-add {
  position: absolute;
  top: 50%;
  right: 2px;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  padding: 0;
  border: 1px solid rgba(30, 64, 175, 0.25);
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.95);
  color: #1e40af;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  box-shadow: 0 1px 5px -1px rgba(30, 64, 175, 0.3);
  cursor: pointer;
  opacity: 0;
  transform: translateY(-50%) scale(0.5);
  transition:
    opacity 0.14s,
    transform 0.14s cubic-bezier(0.34, 1.56, 0.64, 1),
    background 0.14s,
    border-color 0.14s,
    color 0.14s;
}
.tk-sub:hover .tk-sub-add {
  opacity: 1;
  transform: translateY(-50%) scale(1);
}
.tk-sub .tk-sub-add:hover {
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 55%, #0ea5e9 100%);
  border-color: transparent;
  color: #fff;
  transform: translateY(-50%) scale(1.15);
}
.tk-sub .tk-sub-add:active {
  transform: translateY(-50%) scale(1);
}
/* ── 行级就地编辑（工作项卡任务行 / 分镜卡分镜行共用思路）：展示行文字原地换成 textarea，
   行几何（编号 / 字号 / 行高 / 内边距）两态完全一致 → 进出编辑零抖动。
   可编辑元素只补 flex 槽位与指针样式，排版全部继承行容器 ── */
.tk-sub-input,
.sg-shot-input {
  flex: 1;
  min-width: 0;
  width: auto; /* 基础 reset 给了 width:100%（task 卡），flex 行内收回 */
  line-height: inherit;
  cursor: text;
}
/* 行级删除：绝对定位浮层（不占行内几何 → 行宽两态一致），编辑态 + 悬停该行才浮现 */
.tk-sub-del,
.sg-shot-del,
.dt-attr-del,
.dt-sample-del,
.cc-point-del {
  position: absolute;
  top: 50%;
  z-index: 4;
  display: none;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  padding: 0;
  border-radius: 50%;
  transform: translateY(-50%);
  cursor: pointer;
  transition: color 0.14s, border-color 0.14s, box-shadow 0.14s;
}
.tk-sub-del,
.sg-shot-del,
.dt-attr-del,
.dt-sample-del {
  right: 20px; /* 避让行右「＋」连线入口 */
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(15, 23, 42, 0.16);
  color: #94a3b8;
  box-shadow: 0 1px 4px -1px rgba(15, 23, 42, 0.25);
}
.tk-sub-del:hover,
.sg-shot-del:hover,
.dt-attr-del:hover,
.dt-sample-del:hover {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.35);
}
.cc-point-del {
  right: 0;
  background: rgba(2, 6, 23, 0.5);
  border: 1px solid rgba(255, 255, 255, 0.22);
  color: rgba(255, 255, 255, 0.6);
}
.cc-point-del:hover {
  color: #fda4af;
  border-color: rgba(251, 113, 133, 0.45);
}
.task.editing .tk-sub:hover .tk-sub-del,
.seg.editing .sg-shot:hover .sg-shot-del,
.data.editing .dt-attr:hover .dt-attr-del,
.data.editing .dt-sample:hover .dt-sample-del,
.conclusion.editing .cc-point:hover .cc-point-del {
  display: flex;
}
/* ghost 行（编辑态末尾新增入口）：不占展示态版面；placeholder 自带提示，行本身不再加底色 */
.tk-sub-new,
.sg-shot-new {
  opacity: 0.92;
}
/* 数据字段 */
.dt-head input {
  flex: 1;
  min-width: 0;
}
/* 主数值与单位紧贴：宽度随内容（field-sizing 自适应，size 属性字符数兜底），单位允许多字符；过长最多占满一行不撑破卡 */
.dt-metric input.dt-num {
  flex: 0 1 auto;
  min-width: 0;
  width: auto;
  max-width: 100%;
  field-sizing: content;
}
.dt-metric input.dt-unit {
  flex: 0 1 auto;
  min-width: 0;
  width: auto;
  max-width: 100%;
  field-sizing: content;
}
/* attrs 行级就地编辑：input 复用展示 span 的类（dt-alabel / dt-aval 的字号字重与 flex 分配），
   只覆盖掉 input 默认宽度 → 行几何两态一致；比例条随输入实时重算（dataBars 由 attrs 派生） */
.dt-alabel-in,
.dt-aval-in {
  width: auto; /* 基础 reset 给了 width:100%，flex 行内由各自 flex 规则接管 */
  cursor: text;
}
/* ghost 行提示色 */
.dt-attr-new .dt-alabel-in::placeholder,
.dt-attr-new .dt-aval-in::placeholder {
  font-style: normal;
}
/* samples 行级就地编辑：flex 布局两态共用——「└」前缀 + 1ch 间距（等宽字体下与原「└ 文本」的空格等宽） */
.dt-sample {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 1ch;
}
.dt-sample-prefix {
  flex-shrink: 0;
}
.dt-sample-input {
  flex: 1;
  min-width: 0;
  width: auto;
  line-height: inherit;
  cursor: text;
}
.dt-sample-del {
  right: 0; /* samples 区没有「＋」入口，贴右即可 */
}
/* 结论字段（深色卡） */
.cc-claim {
  word-break: break-word;
}
/* 要点行级就地编辑：li 内嵌 textarea/input，「▸」（li::before）与 padding-left 两态保留，行几何一致 */
.cc-point-input {
  line-height: inherit;
  cursor: text;
}

/* ── 分镜段节点（品牌板型：分镜板）：白卡 + 青色系（#0e7490），一卡一场戏，单场承载多分镜（结构仿工作项卡） ── */
.seg {
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(14, 116, 144, 0.2);
  border-radius: 14px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 10px 28px -14px rgba(14, 116, 144, 0.26);
  width: 292px;
  padding: 11px 13px;
  box-sizing: border-box;
  cursor: grab;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.seg:hover {
  border-color: rgba(14, 116, 144, 0.42);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 14px 34px -14px rgba(14, 116, 144, 0.32);
}
.vue-flow__node.selected .seg {
  border-color: #0e7490;
  box-shadow: 0 0 0 3px rgba(14, 116, 144, 0.12), 0 14px 34px -14px rgba(14, 116, 144, 0.32);
}
/* 字段重置（同 task/data 卡：无边框透明底，样式由各字段类自己给） */
.seg input,
.seg textarea {
  display: block;
  margin: 0;
  padding: 0;
  border: none;
  background: transparent;
  outline: none;
  resize: none;
  overflow: hidden;
  font-family: inherit;
  font-size: inherit;
  color: inherit;
  box-sizing: border-box;
  cursor: text;
}
.seg .view {
  pointer-events: none; /* 展示态：事件穿透到卡片 → 整体可拖拽 */
}
.seg .view::placeholder {
  font-style: italic;
  font-weight: 400;
  color: #9db6c0;
}
.seg input::placeholder,
.seg textarea::placeholder {
  color: #9db6c0;
}
.sg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}
.sg-seg {
  flex: 1;
  min-width: 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 13.5px;
  font-weight: 800;
  line-height: 1.35;
  letter-spacing: 0.01em;
  color: #0f172a;
  word-break: break-word;
}
.sg-dur {
  flex: 0 0 auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11.5px;
  font-weight: 700;
  color: #0e7490;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
/* 分镜数徽章：本场拆成了几个分镜（同工作项卡 tk-count 的视觉锚位，换青色系） */
.sg-count {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  background: #0e7490;
  color: #fff;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  line-height: 1.5;
  letter-spacing: 0.03em;
  white-space: nowrap;
}
/* 情绪 / 场景 / 状态：标签 + 段落文本 */
.sg-field {
  margin-top: 7px;
}
.sg-tag {
  display: block;
  margin-bottom: 2px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: #0e7490;
  opacity: 0.85;
}
.sg-text {
  display: block;
  width: 100%;
  padding: 6px 9px;
  border-radius: 8px;
  background: rgba(8, 145, 178, 0.04);
  font-size: 11.5px;
  font-weight: 500;
  line-height: 1.55;
  color: #475569;
  word-break: break-word;
}
/* 分镜清单（同工作项卡 tk-subs 的淡色区块 + 等宽编号，青色系） */
.sg-shots {
  margin-top: 8px;
  padding: 6px 7px;
  border-radius: 9px;
  background: rgba(14, 116, 144, 0.05);
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.sg-empty {
  padding: 4px 4px;
  font-size: 10.5px;
  line-height: 1.5;
  color: #94a3b8;
}
.sg-shot {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 3.5px 20px 3.5px 5px;
  border-radius: 6px;
  font-size: 11.5px;
  line-height: 1.45;
  color: #334155;
  transition: background 0.15s;
}
.sg-shot:hover {
  background: rgba(14, 116, 144, 0.07);
}
.sg-shot-no {
  flex: none;
  margin-top: 1px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  line-height: 1.5;
  color: #0e7490;
  font-variant-numeric: tabular-nums;
}
.sg-shot-t {
  word-break: break-word;
}
/* 分镜行「＋」：hover 该行浮现（同任务行 tk-sub-add） */
.sg-shot-add {
  position: absolute;
  top: 50%;
  right: 2px;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  padding: 0;
  border: 1px solid rgba(14, 116, 144, 0.3);
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.95);
  color: #0e7490;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  box-shadow: 0 1px 5px -1px rgba(14, 116, 144, 0.3);
  cursor: pointer;
  opacity: 0;
  transform: translateY(-50%) scale(0.5);
  transition:
    opacity 0.14s,
    transform 0.14s cubic-bezier(0.34, 1.56, 0.64, 1),
    background 0.14s,
    border-color 0.14s,
    color 0.14s;
}
.sg-shot:hover .sg-shot-add {
  opacity: 1;
  transform: translateY(-50%) scale(1);
}
.sg-shot .sg-shot-add:hover {
  background: linear-gradient(110deg, #0e7490 0%, #0891b2 55%, #22d3ee 100%);
  border-color: transparent;
  color: #fff;
  transform: translateY(-50%) scale(1.15);
}
.sg-shot .sg-shot-add:active {
  transform: translateY(-50%) scale(1);
}
.sg-note {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed rgba(14, 116, 144, 0.18);
  font-size: 11px;
  line-height: 1.5;
  color: #94a3b8;
  word-break: break-word;
}
.sg-note-ic {
  flex-shrink: 0;
  margin-top: 1.5px;
  color: #a8b5c8;
}
/* 分镜清单的行级就地编辑样式复用任务卡的行级规则（.sg-shot-input / .sg-shot-del，见「行级就地编辑」段） */
.seg.editing {
  border-color: rgba(14, 116, 144, 0.55);
  cursor: auto;
}
/* 编辑提示铅笔：头部右侧有时长/分镜数徽章，避让到右下角 */
.seg .wf-card-edit-ic {
  top: auto;
  bottom: 8px;
}

/* handle 仅作为「＋ 转发 mousedown」与连线落点锚位：视觉隐藏（连线入口统一在 ＋），保留几何尺寸供命中计算 */
.wf-handle {
  width: 7px;
  height: 7px;
  opacity: 0;
  pointer-events: none;
}
</style>
