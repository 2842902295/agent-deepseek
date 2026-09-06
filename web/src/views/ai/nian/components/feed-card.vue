<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref} from 'vue';
import {marked} from 'marked';
import type {NianFeedItem} from '@/service/api';
import {dayDeltaLabel, fmtMonthDay, fmtTime, getTodoTiming} from '../composables/useTodoTiming';
import {getServiceBaseURL} from '@/utils/service';

const props = defineProps<{
  item: NianFeedItem;
}>();

const emit = defineEmits<{
  (e: 'delete', id: string): void;
  (e: 'open', id: string): void;
}>();

const menuOpen = ref(false);

const typeLabel: Record<string, string> = {
  knowledge: 'Knowledge',
  idea: 'Spark',
  todo: 'Task'
};

const typeLabelZh: Record<string, string> = {
  knowledge: '知识',
  idea: '灵感',
  todo: '待办'
};

function resolveUrl(url?: string | null): string {
  if (!url) return '';
  if (url.startsWith('http')) return url;
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const {baseURL} = getServiceBaseURL(import.meta.env, isHttpProxy);
  return `${baseURL}${url.startsWith('/') ? url : '/' + url}`;
}

const rankLabel = computed(() => {
  if (props.item.lastFeedRank == null) return '';
  return String(props.item.lastFeedRank + 1).padStart(2, '0');
});

// ── todo 时间逻辑 ─────────────────────────────────────────────────
type TodoUrgency = 'overdue' | 'today' | 'soon' | 'normal' | 'done';

const todoUrgency = computed<TodoUrgency>(() => {
  if (props.item.entryType !== 'todo') return 'normal';
  if (props.item.todoStatus === 'done') return 'done';
  const due = props.item.dueAt;
  if (!due) return 'normal';
  const t = getTodoTiming(due);
  if (t.bucket === 'overdue') return 'overdue';
  if (t.bucket === 'today') return 'today';
  if (t.bucket === 'tomorrow' || t.bucket === 'day-after') return 'soon';
  return 'normal';
});

const todoCountdown = computed(() => {
  if (props.item.entryType !== 'todo') return {primary: '', secondary: ''};
  if (props.item.todoStatus === 'done') {
    return {primary: '已完成', secondary: ''};
  }
  const due = props.item.dueAt;
  if (!due) return {primary: '待安排', secondary: ''};
  const t = getTodoTiming(due);
  const timeStr = fmtTime(t.date);
  const dateStr = fmtMonthDay(t.date);

  if (t.bucket === 'overdue') {
    // 自然日逾期：今天 00:00 之前的算「逾期 N 天」，今天的算「逾期 N 小时」
    const overdueDays = -t.dayDelta;
    return {
      primary: overdueDays === 0 ? `逾期 ${-t.hoursDelta} 小时` : `逾期 ${overdueDays} 天`,
      secondary: `${dateStr} ${timeStr}`,
    };
  }
  if (t.bucket === 'today') {
    return {
      primary: t.hoursDelta <= 0 ? '即将到期' : `今日 · ${timeStr}`,
      secondary: t.hoursDelta > 0 ? `还有 ${t.hoursDelta} 小时` : '',
    };
  }
  return {primary: dayDeltaLabel(t.dayDelta), secondary: `${dateStr} ${timeStr}`};
});

// ── idea 捕获时间 ─────────────────────────────────────────────────
const ideaTimeLabel = computed(() => {
  if (props.item.entryType !== 'idea') return '';
  const ts = props.item.createdAt;
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const time = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (isToday) return `今日 ${time}`;
  return `${d.getMonth() + 1}.${d.getDate()} ${time}`;
});

// ── idea 正文：渲染 markdown 给卡片用（只读，所以 marked 直出 HTML 即可） ─
const ideaContentHtml = computed(() => {
  if (props.item.entryType !== 'idea') return '';
  const md = props.item.content || props.item.title || '';
  if (!md.trim()) return '<span class="i-quote-empty">（空灵感）</span>';
  return marked.parse(md) as string;
});

// ── 交互 ──────────────────────────────────────────────────────────
function onCardClick() {
  emit('open', props.item.id);
}

function onMenuClick(action: 'delete', e: Event) {
  e.stopPropagation();
  menuOpen.value = false;
  emit(action, props.item.id);
}

function toggleMenu(e: Event) {
  e.stopPropagation();
  menuOpen.value = !menuOpen.value;
}

function onDocClick() {
  if (menuOpen.value) menuOpen.value = false;
}

onMounted(() => window.addEventListener('click', onDocClick));
onBeforeUnmount(() => window.removeEventListener('click', onDocClick));
</script>

<template>
  <article
    :class="[
      'card',
      `card-${item.entryType}`,
      item.entryType === 'todo' && `card-todo-${todoUrgency}`,
      item.entryType === 'idea' && item.ideaStatus === 'digested' && 'card-idea-digested'
    ]"
    @click="onCardClick"
  >
    <!-- ============ 共享：右上角更多菜单 ============ -->
    <button class="menu-btn" :aria-label="'更多操作'" @click="toggleMenu">
      <svg viewBox="0 0 14 14" fill="currentColor" width="12" height="12">
        <circle cx="2.5" cy="7" r="1.2" /><circle cx="7" cy="7" r="1.2" /><circle cx="11.5" cy="7" r="1.2" />
      </svg>
    </button>
    <Transition name="menu-pop">
      <div v-if="menuOpen" class="menu" @click.stop>
        <button class="menu-danger" @click="(e) => onMenuClick('delete', e)">
          <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4" width="11" height="11">
            <path d="M2 3h8M4 3V1.5h4V3M3 3l.7 7.5h4.6L9 3" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          删除
        </button>
      </div>
    </Transition>

    <!-- ============ KNOWLEDGE：干净卡片 + 摘要 + 多 tag ============ -->
    <template v-if="item.entryType === 'knowledge'">
      <div class="k-accent" />
      <div class="card-meta">
        <span class="type-pill">
          <span class="tp-dot" /><span class="tp-en">KNOWLEDGE</span>
        </span>
        <span v-if="rankLabel" class="rank">#{{ rankLabel }}</span>
      </div>
      <h3 class="card-title">{{ item.title || '（无题）' }}</h3>
      <p v-if="item.summary" class="k-summary">{{ item.summary }}</p>
      <div class="card-foot">
        <span v-for="t in item.tags.slice(0, 4)" :key="t" class="chip chip-tag">#{{ t }}</span>
        <span v-if="item.tags.length > 4" class="chip chip-more">+{{ item.tags.length - 4 }}</span>
      </div>
    </template>

    <!-- ============ IDEA：引语风格 + 捕获时间 ============ -->
    <template v-else-if="item.entryType === 'idea'">
      <div class="i-spark-bar" />
      <div class="i-bolt" aria-hidden="true">
        <svg viewBox="0 0 16 18" fill="currentColor" width="18" height="20">
          <path d="M9 0L1 10h5l-3 8 9-11H7L11 0z" />
        </svg>
      </div>
      <div class="card-meta">
        <span class="type-pill type-pill-idea">
          <span class="tp-dot" /><span class="tp-en">SPARK</span>
        </span>
        <span v-if="item.ideaStatus === 'digested'" class="i-digested">已消化</span>
        <span v-if="ideaTimeLabel" class="i-time">{{ ideaTimeLabel }}</span>
      </div>
      <blockquote class="i-quote">
        <span class="i-quote-mark">"</span>
        <span class="i-quote-text" v-html="ideaContentHtml" />
        <span class="i-quote-mark i-quote-mark-end">"</span>
      </blockquote>
      <div v-if="item.title && item.content" class="i-title">— {{ item.title }}</div>
      <div v-if="item.tags.length" class="card-foot">
        <span v-for="t in item.tags.slice(0, 3)" :key="t" class="chip chip-tag">#{{ t }}</span>
      </div>
    </template>

    <!-- ============ TODO：截止时间放最大 ============ -->
    <template v-else-if="item.entryType === 'todo'">
      <div class="t-stripe" />
      <div class="t-due-block">
        <div class="t-due-primary">{{ todoCountdown.primary }}</div>
        <div v-if="todoCountdown.secondary" class="t-due-secondary">{{ todoCountdown.secondary }}</div>
      </div>
      <div class="card-meta t-meta">
        <span class="type-pill type-pill-todo">
          <span class="tp-dot" /><span class="tp-en">TASK</span>
        </span>
        <span
          v-if="item.todoStatus === 'done'"
          class="t-status t-status-done"
        >已完成</span>
        <span v-else class="t-status t-status-pending">待办</span>
      </div>
      <h3 class="card-title t-title">{{ item.title || '（无题）' }}</h3>
      <div v-if="item.tags.length" class="card-foot">
        <span v-for="t in item.tags.slice(0, 3)" :key="t" class="chip chip-tag">#{{ t }}</span>
      </div>
    </template>
  </article>
</template>

<style scoped>
/* ─── 卡片基底 ─── */
.card {
  --accent: #1e40af;
  --accent-2: #0891b2;
  --accent-glow: rgba(30, 64, 175, 0.18);

  position: relative;
  display: block;
  padding: 14px 16px;
  /* 性能约定：卡片数量多，底色保持接近不透明、不上 backdrop-filter，
     否则每张卡每帧都要对背后内容做实时模糊，滚动帧率从 160 掉到 100 */
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 16px;
  cursor: pointer;
  font-family: 'Plus Jakarta Sans', sans-serif;
  color: #0f172a;
  /* 性能约定：单层软阴影。多层 box-shadow（尤其 inset 层）是滚动重绘大头 */
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
  /* 性能约定：不过渡 box-shadow——阴影过渡期间整卡逐帧重绘，
     鼠标扫过一排卡片会形成重绘风暴；只过渡 transform 和 border-color */
  transition: transform 0.24s cubic-bezier(0.32, 0.72, 0, 1), border-color 0.2s ease;
  overflow: hidden;
  /* 视口外的卡片跳过布局/绘制，feed 再长也不掉帧 */
  content-visibility: auto;
  contain-intrinsic-size: auto 220px;
}

.card:hover {
  transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--accent) 35%, transparent);
  box-shadow: 0 10px 28px -14px var(--accent-glow);
}

.card:active { transform: translateY(0); }

/* ─── 通用 meta 行 / pill ─── */
.card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}

.type-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px 2px 6px;
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 999px;
}

.tp-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
}

.tp-en {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 9.5px;
  letter-spacing: 0.06em;
  color: var(--accent);
  text-transform: uppercase;
}

.rank {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  color: var(--accent);
  opacity: 0.7;
}

.card-title {
  margin: 0 0 6px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 14.5px;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.005em;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ─── 共享：menu 按钮（绝对定位） ─── */
.menu-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 5;
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  background: rgba(255, 255, 255, 0.7);
  color: #64748b;
  cursor: pointer;
  border-radius: 7px;
  transition: all 0.18s ease;
}

.menu-btn:hover {
  border-color: rgba(30, 64, 175, 0.16);
  color: var(--accent);
}

.menu {
  position: absolute;
  top: 36px;
  right: 8px;
  z-index: 10;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(28px) saturate(200%);
  -webkit-backdrop-filter: blur(28px) saturate(200%);
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  min-width: 132px;
  padding: 4px;
  box-shadow: 0 16px 40px -8px rgba(15, 23, 42, 0.18);
}

.menu button {
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
  padding: 7px 10px;
  background: transparent;
  border: none;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12.5px;
  font-weight: 500;
  color: #0f172a;
  cursor: pointer;
  border-radius: 7px;
}

.menu button svg { color: #64748b; }
.menu button:hover { background: rgba(30, 64, 175, 0.08); color: var(--accent); }
.menu button:hover svg { color: var(--accent); }
.menu-danger { color: #b91c1c !important; }
.menu-danger svg { color: #b91c1c !important; }
.menu-danger:hover { background: rgba(185, 28, 28, 0.08) !important; }

.menu-pop-enter-from, .menu-pop-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.96);
}

.menu-pop-enter-active, .menu-pop-leave-active {
  transition: opacity 0.16s ease, transform 0.18s cubic-bezier(0.32, 0.72, 0, 1);
  transform-origin: top right;
}

/* ─── 共享：chips + pin-mark ─── */
.card-foot {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  margin-top: 6px;
}

.chip {
  display: inline-flex;
  align-items: center;
  padding: 1px 8px;
  font-size: 10.5px;
  font-weight: 600;
  border-radius: 999px;
  border: 1px solid transparent;
  line-height: 1.6;
}

.chip-tag {
  color: #64748b;
  background: rgba(255, 255, 255, 0.5);
  border-color: rgba(15, 23, 42, 0.08);
}

.chip-more { color: #94a3b8; }

/* ============ KNOWLEDGE：干净卡片 + 左侧 accent + 摘要 ============ */
.card-knowledge {
  --accent: #1e40af;
  --accent-2: #2563eb;
  --accent-glow: rgba(30, 64, 175, 0.18);
  background: rgba(255, 255, 255, 0.88);
  border-color: rgba(30, 64, 175, 0.14);
  padding-left: 18px;
}

.card-knowledge .k-accent {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: linear-gradient(180deg, var(--accent), var(--accent-2));
  opacity: 0.85;
}

.k-summary {
  margin: 0 0 8px;
  font-size: 12.5px;
  font-weight: 500;
  color: #475569;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ============ IDEA：引语风格 + 捕获时间 ============ */
.card-idea {
  --accent: #7c3aed;
  --accent-2: #a855f7;
  --accent-glow: rgba(124, 58, 237, 0.32);
  background: linear-gradient(180deg, rgba(216, 180, 254, 0.35), rgba(245, 243, 255, 0.94) 70%);
  border-color: rgba(124, 58, 237, 0.24);
  padding-top: 18px;
}

/* 性能约定：彩虹条保持静态。流动的 background-position 动画无法走合成器，
   会逼每张灵感卡每帧重绘自己（165Hz 下是掉帧主凶之一） */
.card-idea .i-spark-bar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, #7c3aed 0%, #a855f7 30%, #ec4899 60%, #f59e0b 100%);
}

.card-idea .i-bolt {
  position: absolute;
  top: 28px;
  right: 14px;
  color: var(--accent);
  opacity: 0.16;
}

.type-pill-idea {
  background: linear-gradient(135deg, rgba(124, 58, 237, 0.14), rgba(168, 85, 247, 0.14));
  border-color: rgba(124, 58, 237, 0.3);
}

.i-time {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  color: var(--accent);
  margin-left: auto;
  margin-right: 30px;
  letter-spacing: 0.04em;
}

.i-digested {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: #475569;
  background: rgba(148, 163, 184, 0.18);
  border: 1px solid rgba(100, 116, 139, 0.28);
  border-radius: 999px;
}

/* 已消化的灵感整体褪色：去掉 spark 渐变条、闪电图标、强调色，全部转灰 */
.card-idea-digested {
  --accent: #64748b;
  --accent-2: #94a3b8;
  --accent-glow: rgba(100, 116, 139, 0.18);
  background: linear-gradient(180deg, rgba(148, 163, 184, 0.18), rgba(248, 250, 252, 0.94) 70%);
  border-color: rgba(100, 116, 139, 0.22);
  opacity: 0.78;
}

.card-idea-digested .i-spark-bar {
  background: linear-gradient(90deg, #94a3b8, #cbd5e1);
}

.card-idea-digested .i-bolt {
  opacity: 0.08;
}

.card-idea-digested .i-quote,
.card-idea-digested .i-title {
  color: #64748b;
}

.card-idea-digested .i-quote-text,
.card-idea-digested .i-quote-text :deep(*) {
  text-decoration: line-through;
  text-decoration-color: rgba(100, 116, 139, 0.45);
  text-decoration-thickness: 1px;
}

.i-quote {
  position: relative;
  margin: 6px 0 6px;
  padding: 4px 6px 4px 18px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 14px;
  font-weight: 500;
  font-style: italic;
  color: #1e1b4b;
  line-height: 1.55;
  letter-spacing: 0.005em;
}

.i-quote-mark {
  position: absolute;
  font-family: Georgia, serif;
  font-size: 36px;
  font-weight: 800;
  color: var(--accent);
  opacity: 0.3;
  line-height: 1;
  font-style: normal;
}

.i-quote-mark:not(.i-quote-mark-end) {
  top: -6px;
  left: 0;
}

.i-quote-mark-end {
  display: none; /* 结尾引号去掉，保持简洁 */
}

.i-quote-text {
  display: -webkit-box;
  -webkit-line-clamp: 5;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 卡片里的 markdown 预览：尽量紧凑，且让 line-clamp 能正常生效。
   marked 会把段落包成 <p>，需要去掉 margin；标题/列表/代码也压低权重。 */
.i-quote-text :deep(p) {
  margin: 0;
  display: inline;
}
.i-quote-text :deep(p + p) {
  display: block;
  margin-top: 4px;
}
.i-quote-text :deep(h1),
.i-quote-text :deep(h2),
.i-quote-text :deep(h3),
.i-quote-text :deep(h4),
.i-quote-text :deep(h5),
.i-quote-text :deep(h6) {
  margin: 0;
  font-size: inherit;
  font-weight: 700;
  letter-spacing: -0.005em;
}
.i-quote-text :deep(ul),
.i-quote-text :deep(ol) {
  margin: 4px 0 0;
  padding-left: 1.2em;
}
.i-quote-text :deep(li) { margin: 0; }
.i-quote-text :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.9em;
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(124, 58, 237, 0.1);
  color: var(--accent);
}
.i-quote-text :deep(pre) {
  display: none; /* 卡片预览里不渲染代码块，太占空间 */
}
.i-quote-text :deep(blockquote) {
  margin: 0;
  padding-left: 8px;
  border-left: 2px solid color-mix(in srgb, var(--accent) 30%, transparent);
  color: inherit;
}
.i-quote-text :deep(a) { color: var(--accent); text-decoration: none; }
.i-quote-text :deep(strong) { font-weight: 700; }
.i-quote-text :deep(em) { font-style: italic; }
.i-quote-text :deep(hr) { display: none; }
.i-quote-text :deep(img) { display: none; }
.i-quote-empty { font-style: italic; opacity: 0.6; }

.i-title {
  font-size: 11px;
  font-weight: 600;
  color: #6b21a8;
  text-align: right;
  margin-top: 4px;
  letter-spacing: 0.02em;
}

/* ============ TODO：截止时间放最大 + 紧迫度色彩 ============ */
.card-todo {
  --accent: #d97706;
  --accent-2: #f59e0b;
  --accent-glow: rgba(217, 119, 6, 0.28);
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.22) 0%, rgba(255, 251, 235, 0.94) 60%);
  border-color: rgba(217, 119, 6, 0.22);
  padding-left: 18px;
}

.card-todo-overdue {
  --accent: #dc2626;
  --accent-2: #ef4444;
  --accent-glow: rgba(220, 38, 38, 0.32);
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.26) 0%, rgba(254, 242, 242, 0.94) 60%);
  border-color: rgba(220, 38, 38, 0.3);
}

.card-todo-today {
  --accent: #ea580c;
  --accent-2: #f97316;
  --accent-glow: rgba(234, 88, 12, 0.3);
  background: linear-gradient(135deg, rgba(249, 115, 22, 0.24) 0%, rgba(255, 247, 237, 0.94) 60%);
  border-color: rgba(234, 88, 12, 0.26);
}

.card-todo-soon {
  --accent: #ca8a04;
  --accent-2: #eab308;
  --accent-glow: rgba(202, 138, 4, 0.26);
}

.card-todo-normal {
  --accent: #65a30d;
  --accent-2: #84cc16;
  --accent-glow: rgba(101, 163, 13, 0.24);
  background: linear-gradient(135deg, rgba(132, 204, 22, 0.2) 0%, rgba(247, 254, 231, 0.94) 60%);
  border-color: rgba(101, 163, 13, 0.22);
}

.card-todo-done {
  --accent: #64748b;
  --accent-2: #94a3b8;
  --accent-glow: rgba(100, 116, 139, 0.2);
  background: linear-gradient(135deg, rgba(148, 163, 184, 0.2) 0%, rgba(248, 250, 252, 0.94) 60%);
  border-color: rgba(100, 116, 139, 0.22);
  opacity: 0.78;
}

.card-todo-done .card-title,
.card-todo-done .t-due-primary {
  text-decoration: line-through;
  text-decoration-color: rgba(100, 116, 139, 0.6);
}

.card-todo .t-stripe {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 6px;
  background: linear-gradient(180deg, var(--accent), var(--accent-2));
}

.t-due-block {
  position: relative;
  padding: 6px 10px 8px 12px;
  margin: -2px 0 8px 0;
  border-bottom: 1px dashed color-mix(in srgb, var(--accent) 30%, transparent);
}

.t-due-primary {
  font-family: 'JetBrains Mono', monospace;
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--accent);
  text-shadow: 0 1px 2px var(--accent-glow);
  line-height: 1.1;
}

.t-due-secondary {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 600;
  color: #94a3b8;
  letter-spacing: 0.02em;
  margin-top: 3px;
}

.t-meta { padding-left: 4px; }

.type-pill-todo {
  background: linear-gradient(135deg, rgba(217, 119, 6, 0.14), rgba(245, 158, 11, 0.14));
  border-color: rgba(217, 119, 6, 0.28);
}

.t-status {
  margin-left: auto;
  margin-right: 30px;
  padding: 2px 8px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  border-radius: 999px;
}

.t-status-pending {
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 24%, transparent);
}

.t-status-done {
  color: #fff;
  background: linear-gradient(135deg, #10b981, #34d399);
  border: 1px solid transparent;
}

.t-title {
  padding-left: 4px;
}
</style>
