<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch} from 'vue';
import {NDatePicker, NPopover} from 'naive-ui';
import {fetchKbSearch, fetchKbArtifactsLookup, type AgentArtifact, type KBEntry, type NianFeedItem, type NianIdeaStatus, type NianTodoStatus} from '@/service/api';
import {getServiceBaseURL} from '@/utils/service';
import ExcalidrawDialog from '@/components/common/excalidraw-dialog.vue';
import {useNian} from '../composables/useNian';
import {dayDeltaLabel, fmtFullDate, fmtTime, getTodoTiming} from '../composables/useTodoTiming';
import MdBlockEditor from './md-block-editor.vue';

const props = defineProps<{
  show: boolean;
  item: NianFeedItem | null;
  /** 右侧 QA 抽屉是否打开，用于并排布局 */
  drawerOpen?: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:show', v: boolean): void;
  (e: 'delete', id: string): void;
  (e: 'open-related', id: string): void;
  (e: 'qa', title: string): void;
}>();

const {updateEntry, setIdeaStatus} = useNian();

const isEditing = ref(false);
const isLargeMode = ref(false);

const typeLabel: Record<string, string> = {knowledge: 'Knowledge', idea: 'Spark', todo: 'Task'};
const typeLabelZh: Record<string, string> = {knowledge: '知识', idea: '灵感', todo: '待办'};

function resolveUrl(url?: string | null): string {
  if (!url) return '';
  if (url.startsWith('http')) return url;
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  return `${baseURL}${url.startsWith('/') ? url : '/' + url}`;
}

// ── 编辑 buffer：item 切换时同步，避免直接修改 props.item ────────
const draft = reactive({
  title: '',
  summary: '',
  content: '',
  tags: [] as string[],
  dueAt: null as number | null,
  todoStatus: 'pending' as NianTodoStatus,
  ideaStatus: 'active' as NianIdeaStatus
});

// 防止跨条目脏写：每次保存都带上正在编辑的条目 id
let trackedId: string | null = null;
let pendingPatch: Record<string, unknown> = {};
let saveTimer: ReturnType<typeof setTimeout> | null = null;

const savingState = ref<'idle' | 'saving' | 'saved'>('idle');
let savedTimer: ReturnType<typeof setTimeout> | null = null;
function syncFromItem() {
  const it = props.item;
  if (!it) {
    trackedId = null;
    return;
  }
  trackedId = it.id;
  draft.title = it.title || '';
  draft.summary = it.summary || '';
  draft.content = it.content || '';
  draft.tags = [...(it.tags || [])];
  draft.dueAt = it.dueAt ?? null;
  draft.todoStatus = (it.todoStatus || 'pending') as NianTodoStatus;
  draft.ideaStatus = (it.ideaStatus || 'active') as NianIdeaStatus;
  pendingPatch = {};
  savingState.value = 'idle';
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
}

watch(() => props.item?.id, () => syncFromItem(), {immediate: true});

/** 从 markdown 中提取 ```html 和 ```chart fenced block，生成合成 artifact 并替换为 [artifact:ID] */
function extractInlineArtifacts(content: string): { stripped: string; artifacts: AgentArtifact[] } {
  const artifacts: AgentArtifact[] = [];
  let idx = 0;

  // 1. 提取 ```chart {...} ```
  let stripped = content.replace(/```chart[^\n]*\n([\s\S]*?)```/g, (_m, body: string) => {
    try {
      const spec = JSON.parse(body.trim());
      idx++;
      const id = -Date.now() - idx;
      artifacts.push({
        id, artifactType: 'chart', name: (typeof spec.title === 'string' ? spec.title : spec.title?.text) || `图表 ${idx}`,
        description: null, path: null, size: null, chartSpec: spec,
        messageId: null, downloadUrl: null, createdAt: Date.now()
      } as any);
      return `\n[artifact:${id}]\n`;
    } catch { return `\`\`\`chart\n${body}\n\`\`\``; }
  });

  // 2. 提取 ```html ... ```
  stripped = stripped.replace(/```html[^\n]*\n([\s\S]*?)```/g, (_m, body: string) => {
    idx++;
    const id = -Date.now() - 10000 - idx;
    artifacts.push({
      id, artifactType: 'html', name: `页面 ${idx}`,
      description: null, path: null, size: null, chartSpec: null,
      htmlContent: body, messageId: null, downloadUrl: null, createdAt: Date.now()
    } as any);
    return `\n[artifact:${id}]\n`;
  });

  return {stripped, artifacts};
}

/** 收集当前条目的所有产物（metadata + content 内联提取 + API 兜底） */
const entryArtifacts = ref<AgentArtifact[]>([]);
/** 经过内联提取后的正文（```html/chart 已被替换为 [artifact:ID]） */
const processedContent = ref('');

watch(() => props.item, async (it) => {
  if (!it) { entryArtifacts.value = []; processedContent.value = ''; return; }

  const content = it.content || '';
  processedContent.value = content;

  // 1. 从 metadata 取
  const list: AgentArtifact[] = [];
  if (it.primaryArtifact) list.push(it.primaryArtifact);
  if (it.attachments?.length) {
    for (const att of it.attachments) {
      if (att.artifact) list.push(att.artifact);
    }
  }

  // 2. 从 content 内联提取 ```html / ```chart 块
  const {stripped, artifacts: inlineArts} = extractInlineArtifacts(content);
  processedContent.value = stripped;
  for (const a of inlineArts) list.push(a);

  // 3. 从 content 解析 [artifact:ID] 标记，API 兜底取详情
  const knownIds = new Set(list.map(a => a.id));
  const MARKER_RE = /\[artifact:(-?\d+)\]/g;
  const extraIds: number[] = [];
  let m: RegExpExecArray | null;
  while ((m = MARKER_RE.exec(stripped))) {
    const id = Number(m[1]);
    if (!knownIds.has(id) && !extraIds.includes(id)) extraIds.push(id);
  }

  if (extraIds.length) {
    const {data, error} = await fetchKbArtifactsLookup(extraIds);
    if (!error && data) {
      for (const id of extraIds) {
        if (data[id]) list.push(data[id]);
      }
    }
  }

  entryArtifacts.value = list;
}, {immediate: true});

// ── 目录导航（knowledge 类型） ──────────────────────────────────────
const activeHeadingId = ref<string | null>(null);

const headings = computed(() => {
  if (props.item?.entryType !== 'knowledge') return [];
  const content = processedContent.value;
  if (!content) return [];
  const result: {id: string; text: string; level: number}[] = [];
  const lines = content.split('\n');
  let idx = 0;
  for (const line of lines) {
    const m = /^(#{1,6})\s+(.+)$/.exec(line);
    if (m) {
      result.push({id: `kh-${idx}`, text: m[2].replace(/[#*_`]/g, '').trim(), level: m[1].length});
      idx++;
    }
  }
  return result;
});

function scrollToHeading(headingId: string) {
  activeHeadingId.value = headingId;
  const idx = headings.value.findIndex(h => h.id === headingId);
  if (idx < 0) return;

  const tryScroll = (attempt: number) => {
    const mainEl = document.querySelector('.det-main') as HTMLElement | null;
    if (!mainEl) { if (attempt < 10) requestAnimationFrame(() => tryScroll(attempt + 1)); return; }
    const headingEls = mainEl.querySelectorAll('h1, h2, h3, h4, h5, h6');
    const target = headingEls[idx] as HTMLElement | undefined;
    if (!target) { if (attempt < 10) requestAnimationFrame(() => tryScroll(attempt + 1)); return; }

    // getBoundingClientRect 给视口坐标，不受 offsetParent 链影响
    const mainRect = mainEl.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const scrollTop = mainEl.scrollTop + (targetRect.top - mainRect.top) - 60;
    mainEl.scrollTo({top: Math.max(0, scrollTop), behavior: 'smooth'});
  };

  nextTick(() => tryScroll(0));
}

// 知识类型默认大屏，其他默认小屏
watch(() => props.item?.id, (id) => {
  if (!id) return;
  isLargeMode.value = props.item?.entryType === 'knowledge';
});

// 抽屉开关时按比例调整滚动位置（高度变化时防止 scrollTop 被截断）
watch(() => props.drawerOpen, (open, wasOpen) => {
  if (wasOpen && !open) {
    // 抽屉关闭：高度从 100vh → 88vh，按比例缩小 scrollTop
    const mainEl = document.querySelector('.det-main') as HTMLElement | null;
    if (mainEl) {
      mainEl.scrollTop = mainEl.scrollTop * 0.88;
    }
  }
});

// ── 相关条目（语义检索） ─────────────────────────────────────────
const relatedItems = ref<KBEntry[]>([]);
const relatedLoading = ref(false);
const relatedLoaded = ref(false); // 是否已经为当前 item 加载过
let relatedTrackedId: string | null = null;

// 移动端默认收折
const isRelatedMobile = ref(false);
const relatedShow = ref(false); // 移动端面板是否展开

function updateIsRelatedMobile() {
  isRelatedMobile.value = window.innerWidth <= 720;
}

async function loadRelated() {
  const it = props.item;
  if (!it) {
    relatedItems.value = [];
    return;
  }
  const reqId = it.id;
  relatedTrackedId = reqId;
  const q = [it.title, it.summary].filter(Boolean).join(' ').slice(0, 200);
  if (!q.trim()) {
    relatedItems.value = [];
    relatedLoaded.value = true;
    return;
  }
  relatedLoading.value = true;
  try {
    const {data, error} = await fetchKbSearch(q, 8);
    // 切换条目时丢弃旧请求结果
    if (relatedTrackedId !== reqId) return;
    if (!error && data) {
      relatedItems.value = data
        .filter((x) => x.id !== reqId && !x.isArchived)
        .slice(0, 5);
    } else {
      relatedItems.value = [];
    }
    relatedLoaded.value = true;
  } finally {
    if (relatedTrackedId === reqId) {
      relatedLoading.value = false;
    }
  }
}

// 切换条目：清空旧结果、关掉移动面板、PC 自动加载
watch(
  [() => props.item?.id, () => props.show],
  ([id, show]) => {
    if (!show) {
      relatedItems.value = [];
      relatedLoaded.value = false;
      relatedShow.value = false;
      return;
    }
    if (!id) return;
    relatedItems.value = [];
    relatedLoaded.value = false;
    relatedShow.value = false;
    if (!isRelatedMobile.value) {
      loadRelated();
    }
  },
  {immediate: true}
);

function onRelatedClick(id: string) {
  relatedShow.value = false;
  emit('open-related', id);
}

function toggleRelatedMobile() {
  if (relatedShow.value) {
    relatedShow.value = false;
    return;
  }
  relatedShow.value = true;
  if (!relatedLoaded.value && !relatedLoading.value) {
    loadRelated();
  }
}

// 关闭时如果还有未提交的 patch，立刻同步推一次
watch(
  () => props.show,
  async (v) => {
    if (!v && saveTimer) {
      clearTimeout(saveTimer);
      saveTimer = null;
      await flushPatch();
    }
  }
);

async function flushPatch() {
  if (!trackedId || Object.keys(pendingPatch).length === 0) return;
  const id = trackedId;
  const patch = pendingPatch;
  pendingPatch = {};
  savingState.value = 'saving';
  await updateEntry(id, patch as any);
  if (trackedId === id) {
    savingState.value = 'saved';
    if (savedTimer) clearTimeout(savedTimer);
    savedTimer = setTimeout(() => {
      if (savingState.value === 'saved') savingState.value = 'idle';
    }, 1400);
  }
}

function queuePatch(patch: Record<string, unknown>, immediate = false) {
  Object.assign(pendingPatch, patch);
  if (saveTimer) clearTimeout(saveTimer);
  if (immediate) {
    flushPatch();
  } else {
    saveTimer = setTimeout(() => {
      saveTimer = null;
      flushPatch();
    }, 700);
  }
}

// ── 字段 commit：差异判断后入队 ──────────────────────────────────
// 注意：判等参照系是 draft（用户视角的当前值），不是 props.item——
// props.item 在 modal 打开期间是固化引用，useNian 内部的 items 列表
// 替换不会回写到这里，用 props.item 当对照会让"改完再改回原值"被错误吞掉。
function commitTitle(next: string) {
  const v = next.trim();
  if (v === draft.title) return;
  draft.title = v;
  queuePatch({title: v});
}

function commitSummary(next: string) {
  const v = next.replace(/\s+$/g, '');
  if (v === draft.summary) return;
  draft.summary = v;
  queuePatch({summary: v});
}

function commitContent(next: string) {
  if (next === draft.content) return;
  draft.content = next;
  queuePatch({content: next});
}

function commitTags(text: string) {
  const next = text
    .split(/[,，]/)
    .map((s) => s.trim().replace(/^#/, ''))
    .filter(Boolean);
  const cur = draft.tags;
  if (next.length === cur.length && next.every((t, i) => t === cur[i])) return;
  draft.tags = next;
  queuePatch({tags: next});
}

function commitDueAt(next: number | null) {
  const v = typeof next === 'number' && Number.isFinite(next) ? next : null;
  if (v === draft.dueAt) return;
  draft.dueAt = v;
  queuePatch({dueAt: v}, true);
}

function toggleTodoStatus() {
  const next: NianTodoStatus = draft.todoStatus === 'done' ? 'pending' : 'done';
  draft.todoStatus = next;
  const patch: Record<string, unknown> = {todoStatus: next};
  if (next === 'done' && !props.item?.doneAt) patch.doneAt = Date.now();
  queuePatch(patch, true);
}

function toggleIdeaStatus() {
  const id = trackedId;
  if (!id) return;
  const next: NianIdeaStatus = draft.ideaStatus === 'digested' ? 'active' : 'digested';
  draft.ideaStatus = next;
  // 走 useNian.setIdeaStatus：后端不刷 updated_at（不顶到列表前），
  // 同时把响应合并回共享 items，让卡片立刻重渲染（灰色 + 删除线）。
  setIdeaStatus(id, next);
}

// ── contenteditable helpers：抓取 textContent 当成纯文本 ──────────
function onTextEditableBlur(e: FocusEvent, commit: (v: string) => void) {
  const el = e.target as HTMLElement;
  commit(el.innerText.replace(/ /g, ' '));
}

function onSingleLineKey(e: KeyboardEvent) {
  if (e.key === 'Enter') {
    e.preventDefault();
    (e.target as HTMLElement).blur();
  }
}

// 派生展示数据
const confPct = computed(() => {
  const c = props.item?.feedConfidence;
  if (c == null) return null;
  return Math.round(Math.max(0, Math.min(1, c)) * 100);
});

const rankLabel = computed(() => {
  if (props.item?.lastFeedRank == null) return '';
  return String(props.item.lastFeedRank + 1).padStart(2, '0');
});

// markdown 块级编辑：组件托管 block state 与编辑切换，
// 这里只在它吐出新 md 时同步到 draft + 入队保存。
function onContentBlocksUpdate(md: string) {
  if (md === draft.content) return;
  draft.content = md;
  queuePatch({content: md});
}

/** processedContent 编辑后同步回 draft.content（保留 [artifact:ID] 标记） */
function onProcessedContentUpdate(md: string) {
  if (md === processedContent.value) return;
  processedContent.value = md;
  draft.content = md;
  queuePatch({content: md});
}

// ── todo 时间面板 ─────────────────────────────────────────────────
const todoPanel = computed(() => {
  if (props.item?.entryType !== 'todo') return null;
  const due = draft.dueAt;
  const status = draft.todoStatus;
  if (status === 'done') {
    const dt = props.item?.doneAt ? new Date(props.item.doneAt) : null;
    return {
      primary: '已完成',
      secondary: dt ? `${fmtFullDate(dt)} ${fmtTime(dt)} 完成` : '',
      urgency: 'done'
    };
  }
  if (!due) {
    return {primary: '待安排', secondary: '没有截止时间', urgency: 'unscheduled'};
  }
  const t = getTodoTiming(due);
  const dateStr = fmtFullDate(t.date);
  const timeStr = fmtTime(t.date);

  if (t.bucket === 'overdue') {
    const overdueDays = -t.dayDelta;
    return {
      primary: overdueDays === 0 ? `逾期 ${-t.hoursDelta} 小时` : `逾期 ${overdueDays} 天`,
      secondary: `截止于 ${dateStr} ${timeStr}`,
      urgency: 'overdue'
    };
  }
  if (t.bucket === 'today') {
    return {
      primary: `今日 ${timeStr}`,
      secondary: t.hoursDelta > 0 ? `还有 ${t.hoursDelta} 小时` : '即将到期',
      urgency: 'today'
    };
  }
  // 未来：明天 / 后天 / 本周 / 之后
  const dayLabel = t.dayDelta <= 2 ? dayDeltaLabel(t.dayDelta) : `${dateStr.slice(5)} ${timeStr}`;
  const tail = t.dayDelta <= 2 ? timeStr : `还有 ${t.dayDelta} 天`;
  const urgency = t.dayDelta <= 2 ? 'soon' : 'normal';
  return {primary: dayLabel, secondary: tail, urgency};
});

function onDuePickerUpdate(v: number | null) {
  commitDueAt(v);
}

const duePopoverShow = ref(false);

function clearDue(e: Event) {
  e.stopPropagation();
  commitDueAt(null);
}

// ── file size ───────────────────────────────────────────────────
function fmtSize(bytes?: number | null): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// ── diagram excalidraw 编辑 ─────────────────────────────────────
const excalShow = ref(false);
function openExcalEditor() {
  if (!props.item?.primaryArtifact) return;
  excalShow.value = true;
}
const svgVersion = ref(0);
function onExcalSaved() {
  svgVersion.value = Date.now();
}
const svgPreviewUrl = computed(() => {
  const a = props.item?.svgArtifact;
  if (!a?.downloadUrl) return '';
  const base = resolveUrl(a.downloadUrl);
  return svgVersion.value > 0 ? `${base}${base.includes('?') ? '&' : '?'}v=${svgVersion.value}` : base;
});

function fmtTs(ts?: number | null): string {
  if (!ts) return '—';
  const d = new Date(ts);
  const yy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${yy}.${mm}.${dd} ${hh}:${mi}`;
}

async function close() {
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
    await flushPatch();
  }
  emit('update:show', false);
}

function emitAction(action: 'delete') {
  if (!props.item) return;
  emit(action, props.item.id);
  if (action === 'delete') emit('update:show', false);
}

// 标签输入：只在失焦/Enter 时提交
const tagsText = ref('');
watch(
  () => draft.tags,
  (v) => {
    tagsText.value = v.map((t) => `#${t}`).join(' ');
  },
  {immediate: true}
);
function onTagsBlur(e: FocusEvent) {
  commitTags((e.target as HTMLInputElement).value);
}

function onEscKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.show) close();
}

onMounted(() => {
  updateIsRelatedMobile();
  window.addEventListener('resize', updateIsRelatedMobile);
  document.addEventListener('keydown', onEscKey);
});
onBeforeUnmount(() => {
  window.removeEventListener('resize', updateIsRelatedMobile);
  document.removeEventListener('keydown', onEscKey);
});
</script>

<template>
  <Transition name="det-fade">
  <div
    v-if="show"
    class="det-overlay"
    :class="{ 'det-overlay-aside': drawerOpen }"
    @click.self="!drawerOpen && close()"
  >
    <div v-if="item" :class="['det-shell', isLargeMode && 'det-shell-large']">
      <div :class="['det', `det-${item.entryType}`]">
        <div class="det-glow" aria-hidden="true" />

      <!-- 标题区 -->
      <div class="det-head">
        <div class="head-left">
          <span class="type-pill">
            <span class="tp-dot" />
            <span class="tp-en">{{ typeLabel[item.entryType] || 'Knowledge' }}</span>
            <span class="tp-zh">{{ typeLabelZh[item.entryType] || '知识' }}</span>
          </span>
          <span class="agent-badge">
            <svg viewBox="0 0 14 14" fill="currentColor" width="9" height="9">
              <path d="M7 0l1.5 4L13 7l-4.5 1L7 14 5.5 8 0 7l4.5-1L7 0z" />
            </svg>
            AGENT · 自整理
          </span>
        </div>
        <h2
          class="head-title editable editable-title"
          contenteditable="plaintext-only"
          spellcheck="false"
          :data-empty="!draft.title"
          data-placeholder="（点此编辑标题）"
          @keydown="onSingleLineKey"
          @blur="onTextEditableBlur($event, commitTitle)"
        >{{ draft.title }}</h2>
        <span
          v-if="savingState !== 'idle'"
          class="save-indicator"
          :class="`save-${savingState}`"
        >
          <span v-if="savingState === 'saving'" class="save-spinner" />
          <svg v-else viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" width="10" height="10">
            <path d="M2 6l3 3 5-6" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          {{ savingState === 'saving' ? '保存中' : '已保存' }}
        </span>
        <button
          v-if="item.entryType === 'knowledge'"
          :class="['head-qa-btn', drawerOpen && 'head-qa-btn-active']"
          :title="drawerOpen ? '关闭问答抽屉' : '对此知识提问'"
          @click="emit('qa', draft.title || '（无题）')"
        >
          <svg v-if="!drawerOpen" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" width="13" height="13">
            <circle cx="8" cy="8" r="6" />
            <path d="M6.5 6.5c0-.8.7-1.5 1.5-1.5s1.5.7 1.5 1.5c0 .6-.4 1.1-.9 1.3-.3.1-.6.4-.6.7v.2" stroke-linecap="round" />
            <circle cx="8" cy="11" r=".5" fill="currentColor" />
          </svg>
          <svg v-else viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" width="13" height="13">
            <path d="M4 4l8 8M12 4l-8 8" stroke-linecap="round" />
          </svg>
          <span>{{ drawerOpen ? '收起' : '提问' }}</span>
        </button>
        <button
          class="head-expand-btn"
          :title="isLargeMode ? '收起' : '大窗模式'"
          @click="isLargeMode = !isLargeMode"
        >
          <!-- expand icon -->
          <svg v-if="!isLargeMode" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" width="13" height="13">
            <path d="M10 2h4v4M14 2l-5 5M6 14H2v-4M2 14l5-5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <!-- collapse icon -->
          <svg v-else viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" width="13" height="13">
            <path d="M14 10v4h-4M14 14l-5-5M2 6V2h4M2 2l5 5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
        <button class="head-close" @click="close" :aria-label="'关闭'">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" width="14" height="14">
            <path d="M3 3l10 10M13 3L3 13" stroke-linecap="round" />
          </svg>
        </button>
      </div>

      <!-- meta chips：tags 改成可编辑 -->
      <div class="det-meta">
        <input
          class="tags-input"
          :value="tagsText"
          placeholder="#标签 用空格或逗号分隔"
          @blur="onTagsBlur"
          @keydown.enter="(e) => (e.target as HTMLInputElement).blur()"
        />
        <span class="meta-flow" />
        <span class="meta-time">created {{ fmtTs(item.createdAt) }}</span>
      </div>

      <!-- 主体 ─────────────────────────────────── -->
      <main class="det-main">
        <!-- ===== KNOWLEDGE：左侧目录 + 右侧正文 ===== -->
        <template v-if="item.entryType === 'knowledge'">
          <div class="det-knowledge-wrap">
            <!-- 目录导航 -->
            <nav v-if="headings.length" class="det-toc">
              <div class="toc-label">目录</div>
              <a
                v-for="h in headings"
                :key="h.id"
                :class="['toc-item', `toc-h${h.level}`, activeHeadingId === h.id && 'toc-item-active']"
                @click="scrollToHeading(h.id)"
              >{{ h.text }}</a>
            </nav>
            <div class="det-content-area">
              <p
                class="det-summary editable"
                contenteditable="plaintext-only"
                spellcheck="false"
                :data-empty="!draft.summary"
                data-placeholder="（点此添加摘要）"
                @blur="onTextEditableBlur($event, commitSummary)"
              >{{ draft.summary }}</p>

              <MdBlockEditor
                :model-value="processedContent"
                :reset-key="item.id"
                :artifacts="entryArtifacts"
                empty-hint="点此撰写正文"
                add-label="+ 追加一段"
                @update:model-value="onProcessedContentUpdate"
              />
            </div>
          </div>
        </template>

        <!-- ===== IDEA：紫色引语 frame 包裹的同款块级 markdown ===== -->
        <template v-else-if="item.entryType === 'idea'">
          <div :class="['det-idea-frame', draft.ideaStatus === 'digested' && 'det-idea-frame-digested']">
            <span class="iq-mark">"</span>
            <MdBlockEditor
              class="det-idea-md"
              :model-value="draft.content"
              :reset-key="item.id"
              empty-hint="把灵感写下来…（支持 Markdown）"
              add-label="+ 续写一段"
              @update:model-value="onContentBlocksUpdate"
            />
          </div>
          <div v-if="draft.title" class="det-idea-byline">— {{ draft.title }}</div>
          <div class="det-idea-footer">
            <span class="det-idea-time">捕获于 {{ fmtTs(item.createdAt) }}</span>
            <button
              type="button"
              :class="['idea-status-btn', `idea-status-${draft.ideaStatus}`]"
              :title="draft.ideaStatus === 'digested' ? '点击：恢复为未消化' : '点击：标记为已消化（实现了 / 想通了）'"
              @click="toggleIdeaStatus"
            >
              <span class="isb-tick">
                <svg v-if="draft.ideaStatus === 'digested'" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13">
                  <path d="M2 6l3 3 5-6" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                <svg v-else viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" width="13" height="13">
                  <circle cx="6" cy="6" r="4.5" />
                </svg>
              </span>
              <span>{{ draft.ideaStatus === 'digested' ? '已消化' : '未消化' }}</span>
            </button>
          </div>
        </template>

        <!-- ===== TODO：截止 / 状态 / 描述 全部就地操作 ===== -->
        <template v-else-if="item.entryType === 'todo'">
          <div :class="['det-todo-panel', `dtp-${todoPanel?.urgency || 'normal'}`]">
            <div class="dtp-row">
              <div class="dtp-left">
                <div class="dtp-label">{{ draft.todoStatus === 'done' ? '完成情况' : '截止' }}</div>
                <NPopover
                  v-if="draft.todoStatus !== 'done'"
                  v-model:show="duePopoverShow"
                  trigger="click"
                  placement="bottom-start"
                  :show-arrow="false"
                  raw
                  class="dtp-popover"
                >
                  <template #trigger>
                    <button type="button" class="dtp-trigger">
                      <span class="dtp-primary">{{ todoPanel?.primary || '' }}</span>
                      <span v-if="todoPanel?.secondary" class="dtp-secondary">{{ todoPanel.secondary }}</span>
                      <span class="dtp-trigger-tail">
                        <span
                          v-if="draft.dueAt"
                          class="dtp-clear"
                          role="button"
                          :title="'清除截止'"
                          @click="clearDue"
                        >
                          <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" width="11" height="11">
                            <path d="M3 3l6 6M9 3l-6 6" stroke-linecap="round" />
                          </svg>
                        </span>
                        <svg class="dtp-caret" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" width="11" height="11">
                          <path d="M3 4.5L6 7.5l3-3" stroke-linecap="round" stroke-linejoin="round" />
                        </svg>
                      </span>
                    </button>
                  </template>
                  <NDatePicker
                    panel
                    type="datetime"
                    :value="draft.dueAt"
                    :default-calendar-start-time="draft.dueAt ?? Date.now()"
                    format="yyyy-MM-dd HH:mm"
                    @update:value="onDuePickerUpdate"
                    @confirm="duePopoverShow = false"
                  />
                </NPopover>
                <template v-else>
                  <div class="dtp-primary">{{ todoPanel?.primary || '' }}</div>
                  <div v-if="todoPanel?.secondary" class="dtp-secondary">{{ todoPanel.secondary }}</div>
                </template>
              </div>
              <button
                type="button"
                :class="['dtp-status-btn', `dtp-status-${draft.todoStatus}`]"
                @click="toggleTodoStatus"
              >
                <span class="dsb-tick">
                  <svg v-if="draft.todoStatus === 'done'" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                    <path d="M2 6l3 3 5-6" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                  <svg v-else viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" width="14" height="14">
                    <circle cx="6" cy="6" r="4.5" />
                  </svg>
                </span>
                <span>{{ draft.todoStatus === 'done' ? '已完成' : '待办中' }}</span>
              </button>
            </div>
          </div>
          <p
            class="det-summary editable"
            contenteditable="plaintext-only"
            spellcheck="false"
            :data-empty="!draft.summary"
            data-placeholder="（一句话摘要）"
            @blur="onTextEditableBlur($event, commitSummary)"
          >{{ draft.summary }}</p>
          <div
            class="det-content det-content-todo editable editable-multiline"
            contenteditable="plaintext-only"
            spellcheck="false"
            :data-empty="!draft.content"
            data-placeholder="补充描述…"
            @blur="onTextEditableBlur($event, commitContent)"
          >{{ draft.content }}</div>
        </template>
      </main>

      <!-- footer：删除 + 移动端"相关"触发 -->
      <footer class="det-foot">
        <button class="foot-btn foot-danger" @click="emitAction('delete')">
          <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" width="11" height="11">
            <path d="M2 3h8M4 3V1.5h4V3M3 3l.7 7.5h4.6L9 3" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          删除
        </button>
        <button
          v-if="isRelatedMobile"
          class="foot-btn foot-related"
          @click="toggleRelatedMobile"
        >
          <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.4" width="11" height="11">
            <circle cx="6" cy="6" r="4" />
            <path d="M9 9l4 4" stroke-linecap="round" />
          </svg>
          相关
          <span v-if="relatedLoaded && relatedItems.length" class="foot-related-count">{{ relatedItems.length }}</span>
        </button>
        <span class="foot-flow" />
        <span class="foot-hint">所有改动自动保存</span>
      </footer>
      </div>

      <!-- PC 端：右侧悬浮的相关条目卡片群（每条一个独立块，超出主条目高度的隐藏） -->
      <aside
        v-if="!isRelatedMobile && relatedItems.length"
        class="det-side-related"
      >
        <article
          v-for="r in relatedItems"
          :key="r.id"
          :class="['srel-card', `srel-card--${r.entryType}`]"
          @click="onRelatedClick(r.id)"
        >
          <div class="srel-type">{{ r.entryType }}</div>
          <h4 class="srel-title">{{ r.title || '（无题）' }}</h4>
          <p v-if="r.summary" class="srel-summary">{{ r.summary }}</p>
        </article>
      </aside>
    </div>
  </div>
  </Transition>

  <!-- 移动端：底部上滑面板 -->
  <Transition name="rel-sheet">
    <div
      v-if="isRelatedMobile && relatedShow"
      class="rel-sheet-overlay"
      @click.self="relatedShow = false"
    >
      <div class="rel-sheet">
        <div class="rel-sheet-handle" />
        <div class="rel-sheet-head">
          <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.4" width="12" height="12">
            <circle cx="6" cy="6" r="4" />
            <path d="M9 9l4 4" stroke-linecap="round" />
          </svg>
          <span class="rs-label">相关条目</span>
          <span v-if="relatedLoading" class="rs-loading">检索中…</span>
          <button class="rs-close" @click="relatedShow = false">
            <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="1.6" width="12" height="12">
              <path d="M3 3l8 8M11 3l-8 8" stroke-linecap="round" />
            </svg>
          </button>
        </div>
        <div v-if="relatedItems.length" class="rs-list">
          <article
            v-for="r in relatedItems"
            :key="r.id"
            :class="['rs-card', `rs-card--${r.entryType}`]"
            @click="onRelatedClick(r.id)"
          >
            <div class="rs-type">{{ r.entryType }}</div>
            <h4 class="rs-title">{{ r.title || '（无题）' }}</h4>
            <p v-if="r.summary" class="rs-summary">{{ r.summary }}</p>
          </article>
        </div>
        <div v-else-if="!relatedLoading" class="rs-empty">没有发现相关条目</div>
      </div>
    </div>
  </Transition>

  <ExcalidrawDialog
    v-if="item?.primaryArtifact?.artifactType === 'excalidraw'"
    v-model:show="excalShow"
    :title="draft.title || 'Excalidraw 编辑器'"
    :artifact-id="item.primaryArtifact.id"
    :source-url="resolveUrl(item.primaryArtifact.downloadUrl)"
    :z-index="3000"
    @saved="onExcalSaved"
  />
</template>

<style scoped>
.det-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(15, 23, 42, 0.35);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 4vh 4vw;
  transition: padding 0.3s ease;
}

/* 打开关闭动画 */
.det-fade-enter-active { transition: opacity 0.22s ease; }
.det-fade-enter-active .det-shell { transition: transform 0.28s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.22s ease; }
.det-fade-leave-active { transition: opacity 0.2s ease; }
.det-fade-leave-active .det-shell { transition: transform 0.18s ease, opacity 0.18s ease; }
.det-fade-enter-from { opacity: 0; }
.det-fade-enter-from .det-shell { transform: scale(0.96); opacity: 0; }
.det-fade-leave-to { opacity: 0; }
.det-fade-leave-to .det-shell { transform: scale(0.97); opacity: 0; }

/* 右侧 QA 抽屉打开时的样式由非 scoped style 块处理 */

.det {
  --accent: #1e40af;
  --accent-2: #0891b2;
  --accent-glow: rgba(30, 64, 175, 0.25);
  --ink: #0f172a;
  --ink-soft: #334155;
  --ink-mute: #64748b;
  --aurora: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);

  width: min(740px, 92vw);
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  position: relative;
  background: rgba(255, 255, 255, 0.82);
  backdrop-filter: blur(48px) saturate(220%);
  transition: width 0.3s cubic-bezier(0.16, 1, 0.3, 1), max-height 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  -webkit-backdrop-filter: blur(48px) saturate(220%);
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 24px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  color: var(--ink);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.55),
    0 1px 2px rgba(15, 23, 42, 0.05),
    0 32px 80px -20px rgba(15, 23, 42, 0.28),
    0 16px 48px -16px var(--accent-glow);
  overflow: hidden;
}

.det-knowledge { --accent: #1e40af; --accent-2: #2563eb; --accent-glow: rgba(30, 64, 175, 0.25); }
.det-idea      { --accent: #7c3aed; --accent-2: #a855f7; --accent-glow: rgba(124, 58, 237, 0.3); }
.det-todo      { --accent: #d97706; --accent-2: #f59e0b; --accent-glow: rgba(217, 119, 6, 0.3); }

.det-glow {
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
  opacity: 0.4;
  background:
    radial-gradient(ellipse 70% 50% at 0% 0%, var(--accent-glow), transparent 60%),
    radial-gradient(ellipse 60% 40% at 100% 0%, rgba(8, 145, 178, 0.18), transparent 60%);
}

/* ─── 通用：可编辑区域的 affordance ─── */
.editable {
  position: relative;
  border-radius: 8px;
  outline: none;
  transition: background 0.18s ease, box-shadow 0.18s ease;
  cursor: text;
}
.editable:hover {
  background: color-mix(in srgb, var(--accent) 5%, transparent);
}
.editable:focus {
  background: rgba(255, 255, 255, 0.85);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--accent) 35%, transparent),
    0 4px 14px -4px var(--accent-glow);
}
.editable[data-empty="true"]::before {
  content: attr(data-placeholder);
  color: var(--ink-mute);
  opacity: 0.65;
  pointer-events: none;
  font-style: italic;
}
.editable[data-empty="true"]:focus::before { display: none; }
.editable-multiline { white-space: pre-wrap; word-break: break-word; min-height: 1.6em; }

/* ─── save indicator (in head) ─── */
.save-indicator {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 9px 3px 7px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.06em;
  border-radius: 999px;
  border: 1px solid transparent;
  transition: all 0.2s ease;
  flex-shrink: 0; margin-top: 6px;
}
.save-saving {
  color: var(--accent, #1e40af);
  background: color-mix(in srgb, var(--accent, #1e40af) 10%, transparent);
  border-color: color-mix(in srgb, var(--accent, #1e40af) 22%, transparent);
}
.save-saved {
  color: #059669;
  background: rgba(16, 185, 129, 0.1);
  border-color: rgba(16, 185, 129, 0.25);
}
.save-spinner {
  width: 10px; height: 10px;
  border: 1.5px solid color-mix(in srgb, var(--accent, #1e40af) 22%, transparent);
  border-top-color: var(--accent, #1e40af);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ─── head ─── */
.det-head {
  position: relative; z-index: 1;
  display: flex; align-items: flex-start; gap: 12px;
  padding: 18px 22px 8px;
}
.head-left {
  display: flex; flex-direction: column; gap: 6px;
  flex-shrink: 0;
}
.type-pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 11px 4px 9px;
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 999px;
  font-size: 11px; font-weight: 600;
  flex-shrink: 0;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.85);
}
.tp-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 8px var(--accent-glow); }
.tp-en { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 10px; letter-spacing: 0.06em; color: var(--accent); }
.tp-zh { color: var(--ink-mute); font-weight: 500; font-size: 11px; }

.agent-badge {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 10px 3px 8px;
  background: var(--aurora); color: #fff;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px; font-weight: 700; letter-spacing: 0.08em;
  border-radius: 999px;
  box-shadow: 0 2px 8px -2px rgba(30, 64, 175, 0.4);
  flex-shrink: 0;
}
.agent-badge svg { animation: badge-spin 4s linear infinite; }
@keyframes badge-spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }

.head-title {
  flex: 1; margin: 0;
  padding: 4px 10px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 24px; font-weight: 800;
  color: var(--ink); letter-spacing: -0.02em;
  line-height: 1.3; word-break: break-word;
}
.editable-title { min-height: 1.3em; }
.head-qa-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 16px;
  border: none;
  background: linear-gradient(135deg, #ea580c, #f97316);
  color: #fff;
  cursor: pointer;
  border-radius: 10px;
  flex-shrink: 0;
  margin-top: 4px;
  font-size: 13px;
  font-weight: 700;
  font-family: 'Plus Jakarta Sans', sans-serif;
  transition: all 0.18s ease;
  box-shadow: 0 2px 8px rgba(234, 88, 12, 0.35);
}
.head-qa-btn:hover {
  background: linear-gradient(135deg, #c2410c, #ea580c);
  box-shadow: 0 4px 14px rgba(234, 88, 12, 0.45);
  transform: translateY(-1px);
}
.head-qa-btn svg { stroke: #fff; }
.head-qa-btn-active {
  background: linear-gradient(135deg, #475569, #64748b);
  box-shadow: 0 2px 8px rgba(71, 85, 105, 0.35);
}
.head-qa-btn-active:hover {
  background: linear-gradient(135deg, #334155, #475569);
  box-shadow: 0 4px 14px rgba(71, 85, 105, 0.45);
}
.head-expand-btn {
  width: 32px; height: 32px;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid rgba(30, 64, 175, 0.1);
  background: rgba(255, 255, 255, 0.6);
  color: var(--ink-soft); cursor: pointer;
  border-radius: 10px; flex-shrink: 0; margin-top: 4px;
  transition: all 0.18s ease;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.85);
}
.head-expand-btn:hover { background: rgba(30, 64, 175, 0.06); border-color: rgba(30, 64, 175, 0.2); color: var(--accent); }
.head-close {
  width: 32px; height: 32px;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid rgba(30, 64, 175, 0.1);
  background: rgba(255, 255, 255, 0.6);
  color: var(--ink-soft); cursor: pointer;
  border-radius: 10px; flex-shrink: 0; margin-top: 4px;
  transition: all 0.18s ease;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.85);
}
.head-close:hover { background: rgba(185, 28, 28, 0.08); border-color: rgba(185, 28, 28, 0.2); color: #b91c1c; }

/* ─── meta chips & tags input ─── */
.det-meta {
  position: relative; z-index: 1;
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 4px 22px 14px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}
.chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 9px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 11px; font-weight: 600; letter-spacing: 0.005em;
  border-radius: 999px; border: 1px solid transparent; line-height: 1.6;
}

.tags-input {
  flex: 1; min-width: 160px;
  padding: 4px 10px;
  border: 1px dashed rgba(30, 64, 175, 0.18);
  background: rgba(255, 255, 255, 0.45);
  border-radius: 8px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 11.5px; font-weight: 600;
  color: var(--ink-soft);
  outline: none;
  transition: all 0.18s ease;
}
.tags-input::placeholder { color: var(--ink-mute); opacity: 0.6; font-style: italic; font-weight: 500; }
.tags-input:hover { border-color: color-mix(in srgb, var(--accent) 30%, transparent); }
.tags-input:focus {
  border-style: solid;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 25%, transparent);
}

.meta-flow { flex: 0; }
.meta-time {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px; font-weight: 500;
  color: var(--ink-mute);
  margin-left: auto;
}

/* ─── main scroll ─── */
.det-main {
  position: relative; z-index: 1;
  padding: 18px 22px 24px;
  overflow-y: auto;
  flex: 1; min-height: 0;
}
.det-main::-webkit-scrollbar { width: 6px; }
.det-main::-webkit-scrollbar-thumb { background: rgba(30, 64, 175, 0.2); border-radius: 3px; }
.det-main::-webkit-scrollbar-thumb:hover { background: rgba(30, 64, 175, 0.4); }

.det-summary {
  margin: 0 0 16px;
  padding: 12px 16px;
  background: linear-gradient(135deg, rgba(30, 64, 175, 0.05), rgba(8, 145, 178, 0.05));
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 12px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 14px; font-weight: 500;
  color: var(--ink-soft); line-height: 1.65; letter-spacing: 0.005em;
}

/* ============ knowledge 两栏布局（目录 + 正文） ============ */
.det-knowledge-wrap {
  display: flex;
  gap: 0;
  min-height: 0;
}
.det-content-area {
  flex: 1;
  min-width: 0;
  padding: 0 4px 0 8px;
}

/* ============ 目录导航（左侧） ============ */
.det-toc {
  width: 180px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 4px 16px 16px 0;
  border-right: 1px solid rgba(30, 64, 175, 0.08);
  margin-right: 16px;
  overflow-y: auto;
  max-height: calc(88vh - 200px);
  position: sticky;
  top: 0;
}
.det-toc::-webkit-scrollbar { width: 3px; }
.det-toc::-webkit-scrollbar-thumb { background: rgba(30, 64, 175, 0.12); border-radius: 2px; }

.toc-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px; font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--ink-mute);
  padding: 0 0 8px 10px;
  opacity: 0.7;
}

.toc-item {
  display: block;
  padding: 4px 10px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12px; font-weight: 500;
  color: var(--ink-mute);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.5;
}
.toc-item:hover {
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 6%, transparent);
}
.toc-item-active {
  color: var(--accent);
  font-weight: 700;
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}
.toc-h1 { padding-left: 10px; font-weight: 700; font-size: 12.5px; }
.toc-h2 { padding-left: 10px; }
.toc-h3 { padding-left: 22px; font-size: 11.5px; }
.toc-h4 { padding-left: 34px; font-size: 11px; }
.toc-h5, .toc-h6 { padding-left: 46px; font-size: 10.5px; }

/* 小屏模式隐藏目录导航（只有大屏才显示） */
.det-shell:not(.det-shell-large) .det-toc {
  display: none;
}
.det-shell:not(.det-shell-large) .det-content-area {
  padding: 0;
}

/* ============ knowledge 块级编辑 ============ */
.det-content {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 14.5px; line-height: 1.75; color: var(--ink); letter-spacing: 0.005em;
}

/* ============ idea ============ */
.det-idea-frame {
  position: relative;
  margin: 12px 0;
  padding: 18px 22px 14px 40px;
  background: linear-gradient(135deg, rgba(168, 85, 247, 0.08), rgba(245, 243, 255, 0.5));
  border-left: 4px solid var(--accent);
  border-radius: 4px 12px 12px 4px;
  /* 灵感正文用稍大、稍倾斜的字号，跟知识区分开 */
  font-size: 16px;
  line-height: 1.7;
}
.iq-mark {
  position: absolute; top: 4px; left: 12px;
  font-family: Georgia, serif;
  font-size: 56px; font-weight: 800;
  color: var(--accent); opacity: 0.32; line-height: 1;
  font-style: normal;
  pointer-events: none;
}
/* 嵌入引语 frame 里的块编辑器：去掉自带的左侧 accent 条（外层 frame 已经有了），
   去掉额外背景，让 markdown 文字直接呈现在引语底色上 */
.det-idea-md {
  border-left: none !important;
  padding-left: 0 !important;
}
.det-idea-byline {
  text-align: right;
  font-size: 12.5px; font-weight: 600;
  color: #6b21a8;
  margin: 4px 8px 12px 0; letter-spacing: 0.02em;
}
.det-idea-time {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px; font-weight: 600;
  color: #94a3b8; text-align: right; letter-spacing: 0.04em;
}

.det-idea-footer {
  display: flex; align-items: center; gap: 12px;
  margin-top: 4px;
}
.det-idea-footer .det-idea-time {
  flex: 1; text-align: left;
}

.det-idea-frame-digested {
  background: linear-gradient(135deg, rgba(148, 163, 184, 0.1), rgba(248, 250, 252, 0.5));
  border-left-color: #94a3b8;
}
.det-idea-frame-digested .iq-mark { color: #94a3b8; opacity: 0.4; }
.det-idea-frame-digested .det-idea-md {
  color: #64748b;
  text-decoration: line-through;
  text-decoration-color: rgba(100, 116, 139, 0.45);
  text-decoration-thickness: 1px;
}

.idea-status-btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px;
  border: 1px solid transparent;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px; font-weight: 700; letter-spacing: 0.1em;
  cursor: pointer;
  border-radius: 999px;
  flex-shrink: 0;
  transition: all 0.2s cubic-bezier(0.32, 0.72, 0, 1);
}
.idea-status-active {
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  border-color: color-mix(in srgb, var(--accent) 28%, transparent);
}
.idea-status-active:hover {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  transform: translateY(-1px);
}
.idea-status-digested {
  color: #475569;
  background: rgba(148, 163, 184, 0.18);
  border-color: rgba(100, 116, 139, 0.3);
}
.idea-status-digested:hover {
  background: rgba(148, 163, 184, 0.28);
  transform: translateY(-1px);
}
.isb-tick { display: inline-flex; }

/* ============ todo ============ */
.det-todo-panel {
  position: relative;
  padding: 18px 22px;
  margin-bottom: 16px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid color-mix(in srgb, var(--accent) 24%, transparent);
  box-shadow: 0 2px 12px -2px var(--accent-glow);
}
.dtp-overdue     { --accent: #dc2626; --accent-2: #ef4444; background: linear-gradient(135deg, rgba(239, 68, 68, 0.14), rgba(254, 242, 242, 0.6)); }
.dtp-today       { --accent: #ea580c; --accent-2: #f97316; background: linear-gradient(135deg, rgba(249, 115, 22, 0.12), rgba(255, 247, 237, 0.6)); }
.dtp-soon        { --accent: #ca8a04; --accent-2: #eab308; }
.dtp-done        { --accent: #64748b; --accent-2: #94a3b8; background: linear-gradient(135deg, rgba(148, 163, 184, 0.1), rgba(248, 250, 252, 0.6)); }
.dtp-unscheduled { --accent: #475569; --accent-2: #64748b; background: linear-gradient(135deg, rgba(100, 116, 139, 0.08), rgba(248, 250, 252, 0.5)); }

.dtp-row { display: flex; align-items: flex-start; gap: 16px; }
.dtp-left { flex: 1; min-width: 0; }
.dtp-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.18em;
  color: var(--accent); text-transform: uppercase;
  margin-bottom: 6px;
}
.dtp-primary {
  font-family: 'JetBrains Mono', monospace;
  font-size: 30px; font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--accent); line-height: 1.1;
  text-shadow: 0 2px 4px var(--accent-glow);
}
.dtp-unscheduled .dtp-primary { font-style: italic; opacity: 0.85; }
.dtp-secondary {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px; font-weight: 600;
  color: #64748b; margin-top: 6px; letter-spacing: 0.02em;
}
.dtp-trigger {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  padding: 4px 8px 4px 10px;
  margin: -4px -8px -4px -10px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 10px;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: inherit;
  transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.dtp-trigger:hover {
  background: color-mix(in srgb, var(--accent) 8%, transparent);
  border-color: color-mix(in srgb, var(--accent) 22%, transparent);
}
.dtp-trigger:focus-visible {
  outline: none;
  border-color: color-mix(in srgb, var(--accent) 35%, transparent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 15%, transparent);
}
.dtp-trigger > .dtp-primary,
.dtp-trigger > .dtp-secondary { display: block; }
.dtp-trigger > .dtp-secondary { margin-top: 4px; }

.dtp-trigger-tail {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: stretch;
  padding-top: 6px;
  color: var(--accent);
  opacity: 0.7;
}
.dtp-trigger:hover .dtp-trigger-tail { opacity: 1; }

.dtp-clear {
  display: inline-flex;
  align-items: center; justify-content: center;
  width: 20px; height: 20px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.6);
  color: var(--ink-mute);
  cursor: pointer;
  transition: all 0.15s ease;
}
.dtp-clear:hover {
  background: rgba(185, 28, 28, 0.1);
  color: #b91c1c;
}

.dtp-caret { transition: transform 0.18s ease; }
.dtp-trigger[aria-expanded="true"] .dtp-caret,
.dtp-trigger:hover .dtp-caret { transform: translateY(1px); }

.dtp-status-btn {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 12px 18px;
  border: 1px solid transparent;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px; font-weight: 800; letter-spacing: 0.14em;
  cursor: pointer;
  border-radius: 12px;
  flex-shrink: 0;
  transition: all 0.2s cubic-bezier(0.32, 0.72, 0, 1);
}
.dtp-status-pending {
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  border-color: color-mix(in srgb, var(--accent) 30%, transparent);
}
.dtp-status-pending:hover {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  transform: translateY(-1px);
}
.dtp-status-done {
  color: #fff;
  background: linear-gradient(135deg, #10b981, #34d399);
  border-color: transparent;
  box-shadow: 0 6px 18px -4px rgba(16, 185, 129, 0.5);
}
.dtp-status-done:hover { transform: translateY(-1px); box-shadow: 0 10px 22px -4px rgba(16, 185, 129, 0.55); }
.dsb-tick { display: inline-flex; }

.det-content-todo {
  font-size: 13.5px; line-height: 1.7; color: #334155;
  white-space: pre-wrap;
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.45);
  border: 1px solid rgba(217, 119, 6, 0.12);
}

/* ─── 相关条目（PC 右侧栏 + 移动底部面板） ─── */
.det-shell {
  position: relative;
  display: block;
  /* 只让 .det 决定 NModal 的居中尺寸；aside 用绝对定位悬浮在右侧之外，
     避免出结果时整个 modal 重新居中产生水平抖动 */
}

/* 大窗模式 */
.det-shell-large .det {
  width: 92vw;
  max-height: 94vh;
}
.det-shell-large .det-side-related {
  display: none;
}

/* PC 端：右侧悬浮的相关条目卡片群——绝对定位不参与 shell 宽度计算 */
.det-side-related {
  position: absolute;
  top: 50%;
  left: calc(100% + 16px);
  transform: translateY(-50%);
  width: 280px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 2px;
  pointer-events: none; /* 卡片自身再开 auto，避免空 aside 区拦截到 mask 关闭 */
  animation: srel-fade 0.22s ease both;
}
@keyframes srel-fade {
  from { opacity: 0; transform: translate(-6px, -50%); }
  to   { opacity: 1; transform: translate(0, -50%); }
}
.srel-card {
  pointer-events: auto;
  flex-shrink: 0;
  display: block;
  padding: 14px 16px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 16px;
  cursor: pointer;
  transition: transform 0.22s cubic-bezier(0.32, 0.72, 0, 1),
              box-shadow 0.22s ease, border-color 0.18s ease,
              background 0.18s ease;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.5),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 6px 20px -10px rgba(30, 64, 175, 0.18);
}
/* ── 相关条目：类型配色（对齐 feed-card） ── */
.srel-card {
  --accent: #1e40af;
  --accent-2: #2563eb;
  --accent-glow: rgba(30, 64, 175, 0.18);
}
.srel-card--knowledge {
  background: rgba(255, 255, 255, 0.78);
  border-color: rgba(30, 64, 175, 0.14);
}
.srel-card--idea {
  --accent: #7c3aed;
  --accent-2: #a855f7;
  --accent-glow: rgba(124, 58, 237, 0.28);
  background: linear-gradient(180deg, rgba(168, 85, 247, 0.12), rgba(245, 243, 255, 0.78) 70%);
  border-color: rgba(124, 58, 237, 0.24);
}
.srel-card--todo {
  --accent: #d97706;
  --accent-2: #f59e0b;
  --accent-glow: rgba(217, 119, 6, 0.24);
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.10) 0%, rgba(255, 251, 235, 0.78) 60%);
  border-color: rgba(217, 119, 6, 0.22);
}
.srel-card:hover {
  border-color: color-mix(in srgb, var(--accent) 35%, transparent);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 16px 36px -12px var(--accent-glow);
}

.srel-type {
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px; font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  padding: 2px 7px;
  border-radius: 5px;
  margin-bottom: 8px;
}
.srel-title {
  margin: 0 0 4px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 13.5px; font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.005em;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.srel-summary {
  margin: 0;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 11.5px; font-weight: 500;
  color: #64748b;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* footer 上的"相关"触发按钮（移动端独占） */
.foot-related {
  color: #1e40af;
}
.foot-related:hover {
  background: rgba(30, 64, 175, 0.08);
  border-color: rgba(30, 64, 175, 0.2);
  color: #1e40af;
}
.foot-related-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 800;
  color: #fff;
  background: linear-gradient(135deg, #1e40af, #0891b2);
  padding: 1px 6px;
  border-radius: 999px;
  margin-left: 2px;
}

/* 移动端：底部上滑面板 */
.rel-sheet-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.42);
  backdrop-filter: blur(4px);
  z-index: 4000;
  display: flex;
  align-items: flex-end;
}
.rel-sheet {
  width: 100%;
  max-height: 76vh;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 8px 16px 24px;
  background: #fff;
  border-top-left-radius: 22px;
  border-top-right-radius: 22px;
  box-shadow: 0 -12px 40px -8px rgba(15, 23, 42, 0.32);
}
.rel-sheet-handle {
  width: 44px;
  height: 4px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.16);
  margin: 4px auto 6px;
}
.rel-sheet-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 4px 8px;
  border-bottom: 1px dashed rgba(30, 64, 175, 0.1);
  color: #64748b;
}
.rs-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: #1e40af;
  text-transform: uppercase;
}
.rs-loading {
  margin-left: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  color: #94a3b8;
}
.rs-close {
  margin-left: auto;
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(30, 64, 175, 0.12);
  background: rgba(255, 255, 255, 0.9);
  color: #64748b;
  cursor: pointer;
  border-radius: 9px;
}
.rs-close:hover {
  color: #1e40af;
  border-color: rgba(30, 64, 175, 0.28);
}
.rs-loading + .rs-close { margin-left: 8px; }
.rs-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  padding: 4px 0 0;
}
.rs-card {
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.42);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 12px;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.5),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 6px 20px -10px rgba(30, 64, 175, 0.18);
}
/* ── 移动端相关条目：类型配色（对齐 feed-card） ── */
.rs-card {
  --accent: #1e40af;
  --accent-2: #2563eb;
  --accent-glow: rgba(30, 64, 175, 0.18);
}
.rs-card--knowledge {
  background: rgba(255, 255, 255, 0.78);
  border-color: rgba(30, 64, 175, 0.14);
}
.rs-card--idea {
  --accent: #7c3aed;
  --accent-2: #a855f7;
  --accent-glow: rgba(124, 58, 237, 0.28);
  background: linear-gradient(180deg, rgba(168, 85, 247, 0.12), rgba(245, 243, 255, 0.78) 70%);
  border-color: rgba(124, 58, 237, 0.24);
}
.rs-card--todo {
  --accent: #d97706;
  --accent-2: #f59e0b;
  --accent-glow: rgba(217, 119, 6, 0.24);
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.10) 0%, rgba(255, 251, 235, 0.78) 60%);
  border-color: rgba(217, 119, 6, 0.22);
}
.rs-card:active {
  border-color: color-mix(in srgb, var(--accent) 35%, transparent);
  box-shadow: 0 2px 8px -4px var(--accent-glow);
}

.rs-type {
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px; font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  padding: 2px 7px;
  border-radius: 5px;
  margin-bottom: 8px;
}
.rs-title {
  margin: 0 0 4px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 14px; font-weight: 700;
  color: #0f172a;
  line-height: 1.4;
}
.rs-summary {
  margin: 0;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12px;
  color: #64748b;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.rs-empty {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 13px;
  color: #94a3b8;
  text-align: center;
  padding: 24px 0;
}

.rel-sheet-enter-from .rel-sheet,
.rel-sheet-leave-to .rel-sheet {
  transform: translateY(100%);
}
.rel-sheet-enter-from,
.rel-sheet-leave-to {
  background: rgba(15, 23, 42, 0);
  backdrop-filter: blur(0);
}
.rel-sheet-enter-active .rel-sheet,
.rel-sheet-leave-active .rel-sheet {
  transition: transform 0.28s cubic-bezier(0.32, 0.72, 0, 1);
}
.rel-sheet-enter-active,
.rel-sheet-leave-active {
  transition: background 0.22s ease, backdrop-filter 0.22s ease;
}

/* PC ≤ 720 让侧栏完全消失，腾给移动端面板 */
@media (max-width: 720px) {
  .det-shell {
    gap: 0;
  }
}

/* ─── footer ─── */
.det-foot {
  position: relative; z-index: 1;
  display: flex; align-items: center; gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid rgba(30, 64, 175, 0.08);
  background: rgba(255, 255, 255, 0.5);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.85);
}
.foot-btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 14px 8px 12px;
  border: 1px solid rgba(30, 64, 175, 0.1);
  background: rgba(255, 255, 255, 0.6);
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12.5px; font-weight: 600; letter-spacing: 0.005em;
  color: var(--ink);
  cursor: pointer;
  border-radius: 11px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
  transition: all 0.18s ease;
}
.foot-btn:hover {
  background: #fff;
  border-color: rgba(30, 64, 175, 0.2);
  transform: translateY(-1px);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9), 0 4px 12px -4px rgba(30, 64, 175, 0.18);
}
.foot-flow { flex: 1; }
.foot-hint {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 600;
  letter-spacing: 0.1em;
  color: var(--ink-mute);
  opacity: 0.7;
}
.foot-danger { color: #b91c1c; }
.foot-danger:hover { background: rgba(185, 28, 28, 0.08); border-color: rgba(185, 28, 28, 0.2); color: #b91c1c; }

/* ─── 移动端：缩小 padding，隐藏次要信息 ─── */
@media (max-width: 640px) {
  .det {
    width: 96vw;
    max-height: 92vh;
    border-radius: 18px;
  }
  .det-head {
    padding: 14px 16px 6px;
    gap: 8px;
  }
  /* 类型 pill 只保留英文，去掉中文 */
  .tp-zh { display: none; }
  .head-title {
    font-size: 20px;
    padding: 4px 6px;
  }
  .det-meta {
    padding: 4px 16px 12px;
    gap: 6px;
  }
  /* 隐藏：created 时间 */
  .meta-time { display: none; }
  .det-main {
    padding: 14px 16px 18px;
  }
  /* 知识类型：移动端隐藏目录，单栏布局 */
  .det-toc { display: none; }
  .det-content-area { padding: 0; }
  .det-summary {
    padding: 10px 12px;
    font-size: 13px;
  }
  /* todo 截止时间面板 */
  .det-todo-panel { padding: 14px 16px; margin-bottom: 12px; }
  .dtp-row { gap: 10px; }
  .dtp-primary { font-size: 22px; }
  .dtp-status-btn { padding: 10px 14px; font-size: 11px; }
  /* idea */
  .det-idea-frame { padding: 12px 16px 12px 32px; font-size: 16px; }
  /* footer：隐藏自动保存提示 */
  .det-foot {
    padding: 10px 14px;
    gap: 6px;
  }
  .foot-hint { display: none; }
  .foot-btn { padding: 7px 12px 7px 10px; font-size: 12px; }
}
</style>

<!-- 非 scoped：并排布局覆盖（避免 scoped 选择器问题） -->
<style>
/* 基础过渡：放在常态也生效 */
.det-overlay {
  transition: padding 0.35s cubic-bezier(0.16, 1, 0.3, 1), background 0.3s ease;
}
.det-overlay .det {
  transition: width 0.35s cubic-bezier(0.16, 1, 0.3, 1),
              max-height 0.35s cubic-bezier(0.16, 1, 0.3, 1),
              border-radius 0.35s cubic-bezier(0.16, 1, 0.3, 1);
}

.det-overlay-aside {
  justify-content: flex-start !important;
  background: transparent !important;
  backdrop-filter: none !important;
  padding-top: 0 !important;
  padding-bottom: 0 !important;
  padding-left: 0 !important;
  padding-right: min(860px, 80vw) !important;
  align-items: center !important;
}
.det-overlay-aside .det-shell {
  width: 100% !important;
  min-height: 100vh !important;
}
.det-overlay-aside .det {
  width: 100% !important;
  max-width: none !important;
  min-height: 100vh !important;
  max-height: 100vh !important;
  border-radius: 0 !important;
}
</style>

