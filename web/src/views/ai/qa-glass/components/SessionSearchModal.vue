<script setup lang="ts">
import { ref, watch } from 'vue';
import { NDatePicker, NSpin } from 'naive-ui';
import type { AgentSession as ApiSession } from '@/service/api';
import { fetchAgentSessions } from '@/service/api';

const props = defineProps<{ show: boolean }>();

const emit = defineEmits<{
  'update:show': [value: boolean];
  select: [session: ApiSession];
}>();

const keyword = ref('');
const dateRange = ref<[number, number] | null>(null);
const results = ref<ApiSession[]>([]);
const loading = ref(false);
const loaded = ref(false);

function pad2(n: number) {
  return n < 10 ? `0${n}` : `${n}`;
}

function toDateString(ts: number) {
  const d = new Date(ts);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function formatCreatedAt(ts: number | null | undefined) {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  const md = `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()} ${md}`;
}

let debounceTimer: ReturnType<typeof setTimeout> | null = null;

async function runSearch() {
  loading.value = true;
  try {
    const search: { keyword?: string; startDate?: string; endDate?: string } = {};
    const kw = keyword.value.trim();
    if (kw) search.keyword = kw;
    if (dateRange.value) {
      search.startDate = toDateString(dateRange.value[0]);
      search.endDate = toDateString(dateRange.value[1]);
    }
    const { data, error } = await fetchAgentSessions(60, search);
    // 画板（workflow）会话归属工作流页面，不在问答页搜索结果中展示
    results.value = !error && data ? data.filter(s => s.source !== 'workflow') : [];
    loaded.value = true;
  } finally {
    loading.value = false;
  }
}

function scheduleSearch() {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(runSearch, 300);
}

function onKeywordInput() {
  scheduleSearch();
}

function onDateChange() {
  runSearch();
}

// 打开时：默认展示最近对话
watch(
  () => props.show,
  v => {
    if (v) {
      keyword.value = '';
      dateRange.value = null;
      results.value = [];
      loaded.value = false;
      runSearch();
    }
  }
);

function handleSelect(s: ApiSession) {
  emit('select', s);
  emit('update:show', false);
}

function close() {
  emit('update:show', false);
}
</script>

<template>
  <NModal :show="show" :mask-closable="true" @update:show="v => !v && close()">
    <div class="ssm-card" role="dialog" aria-label="搜索历史对话">
      <div class="ssm-glow" aria-hidden="true" />

      <header class="ssm-head">
        <span class="ssm-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        </span>
        <div class="ssm-titles">
          <h3 class="ssm-title">历史对话</h3>
          <p class="ssm-sub">SEARCH · 标题 / 时间</p>
        </div>
        <button class="ssm-close" title="关闭" @click="close">×</button>
      </header>

      <div class="ssm-filters">
        <label class="ssm-input-wrap">
          <span class="ssm-input-icon" aria-hidden="true">⌕</span>
          <input
            v-model="keyword"
            class="ssm-input"
            type="text"
            placeholder="搜索标题或对话内容，即时筛选…"
            @input="onKeywordInput"
          />
          <button v-if="keyword" class="ssm-input-clear" title="清空" @click="keyword = ''; scheduleSearch()">×</button>
        </label>
        <NDatePicker
          v-model:value="dateRange"
          type="daterange"
          clearable
          class="ssm-date"
          placement="bottom-start"
          @update:value="onDateChange"
        />
      </div>

      <div class="ssm-meta">
        <span class="ssm-meta-num">{{ loading ? '…' : results.length }}</span>
        <span class="ssm-meta-text">个对话</span>
        <span v-if="keyword.trim() || dateRange" class="ssm-meta-tag">已筛选</span>
      </div>

      <div class="ssm-list">
        <NSpin v-if="loading" size="small" class="ssm-spin" />

        <ul v-else-if="results.length" class="ssm-results">
          <li
            v-for="(s, i) in results"
            :key="s.sessionKey"
            class="ssm-item"
            :style="{ '--i': i }"
            @click="handleSelect(s)"
          >
            <span class="ssm-item-dot" aria-hidden="true" />
            <span class="ssm-item-title">{{ s.title }}</span>
            <span class="ssm-item-date">{{ formatCreatedAt(s.createdAt) }}</span>
            <span class="ssm-item-arrow" aria-hidden="true">→</span>
          </li>
        </ul>

        <p v-else-if="loaded" class="ssm-empty">
          {{ keyword.trim() || dateRange ? '没有找到匹配的对话' : '还没有历史对话' }}
        </p>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
/* 弹窗 teleport 到 body，脱离 .qa-shell 作用域，这里复刻其玻璃设计变量 */
.ssm-card {
  --paper: #f5f7fb;
  --surface: rgba(255, 255, 255, 0.55);
  --surface-strong: rgba(255, 255, 255, 0.72);
  --ink: #0f172a;
  --ink-2: #334155;
  --ink-3: #64748b;
  --ink-4: #94a3b8;
  --accent: #1e40af;
  --accent-2: #2563eb;
  --accent-soft: rgba(30, 64, 175, 0.08);
  --aurora: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  --font-display: 'Plus Jakarta Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;

  position: relative;
  width: 760px;
  max-width: 92vw;
  max-height: 84vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 20px;
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  font-family: var(--font-display);
  color: var(--ink);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.06),
    0 24px 64px -20px rgba(30, 64, 175, 0.32);
  animation: ssm-pop 0.32s cubic-bezier(0.32, 0.72, 0, 1);
}

@keyframes ssm-pop {
  from {
    opacity: 0;
    transform: translateY(14px) scale(0.975);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* 顶部极光的氛围光 */
.ssm-glow {
  position: absolute;
  top: -70px;
  left: 50%;
  width: 340px;
  height: 160px;
  transform: translateX(-50%);
  background: var(--aurora);
  opacity: 0.16;
  filter: blur(48px);
  pointer-events: none;
}

/* ─── 头部 ─── */
.ssm-head {
  position: relative;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 22px 24px 16px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}

.ssm-mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 12px;
  background: var(--aurora);
  color: #fff;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 4px 14px -4px rgba(30, 64, 175, 0.55);
}

.ssm-mark svg {
  width: 17px;
  height: 17px;
}

.ssm-titles {
  flex: 1;
  min-width: 0;
}

.ssm-title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  background: var(--aurora);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.ssm-sub {
  margin: 3px 0 0;
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  color: var(--ink-3);
}

.ssm-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--ink-3);
  font-size: 19px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.18s ease;
  flex-shrink: 0;
}

.ssm-close:hover {
  background: rgba(185, 28, 28, 0.08);
  color: #b91c1c;
  transform: rotate(90deg);
}

/* ─── 筛选区（两行竖排，宽度对齐）─── */
.ssm-filters {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 24px 4px;
}

.ssm-input-wrap {
  position: relative;
  width: 100%;
  display: flex;
  align-items: center;
}

.ssm-input-icon {
  position: absolute;
  left: 13px;
  font-size: 16px;
  color: var(--ink-4);
  pointer-events: none;
}

.ssm-input {
  width: 100%;
  height: 40px;
  padding: 0 36px;
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 12px;
  background: var(--surface-strong);
  font-family: var(--font-display);
  font-size: 13px;
  color: var(--ink);
  outline: none;
  transition: all 0.2s ease;
}

.ssm-input::placeholder {
  color: var(--ink-4);
}

.ssm-input:focus {
  border-color: rgba(30, 64, 175, 0.35);
  background: rgba(255, 255, 255, 0.85);
  box-shadow: 0 0 0 3px rgba(30, 64, 175, 0.12);
}

.ssm-input-clear {
  position: absolute;
  right: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 50%;
  background: rgba(30, 64, 175, 0.08);
  color: var(--ink-3);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.15s ease;
}

.ssm-input-clear:hover {
  background: rgba(185, 28, 28, 0.1);
  color: #b91c1c;
}

.ssm-date {
  width: 100%;
  /* 日期选择器根节点即 .n-date-picker，主题变量是内联样式，需 !important 覆盖 */
  --n-height: 40px !important;
  --n-border-radius: 12px !important;
  --n-border: 1px solid rgba(30, 64, 175, 0.14) !important;
  --n-border-hover: 1px solid rgba(30, 64, 175, 0.35) !important;
  --n-border-focus: 1px solid rgba(30, 64, 175, 0.35) !important;
  --n-border-active: 1px solid rgba(30, 64, 175, 0.35) !important;
  --n-box-shadow-focus: 0 0 0 3px rgba(30, 64, 175, 0.12) !important;
  --n-box-shadow-active: 0 0 0 3px rgba(30, 64, 175, 0.12) !important;
  --n-color: rgba(255, 255, 255, 0.72) !important;
  --n-color-active: rgba(255, 255, 255, 0.85) !important;
  --n-color-focus: rgba(255, 255, 255, 0.85) !important;
  --n-text-color: #0f172a !important;
  --n-placeholder-color: #94a3b8 !important;
  font-size: 13px;
}

/* ─── 计数 ─── */
.ssm-meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 10px 26px 8px;
}

.ssm-meta-num {
  font-family: var(--font-mono);
  font-size: 18px;
  font-weight: 700;
  color: var(--accent);
  letter-spacing: -0.02em;
}

.ssm-meta-text {
  font-size: 12px;
  color: var(--ink-3);
}

.ssm-meta-tag {
  margin-left: 4px;
  padding: 2px 8px;
  border-radius: 99px;
  background: var(--accent-soft);
  border: 1px solid rgba(30, 64, 175, 0.16);
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--accent);
}

/* ─── 结果列表 ─── */
.ssm-list {
  flex: 1;
  overflow-y: auto;
  padding: 2px 14px 16px;
}

.ssm-list::-webkit-scrollbar {
  width: 6px;
}
.ssm-list::-webkit-scrollbar-track {
  background: transparent;
}
.ssm-list::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.14);
  border-radius: 4px;
}

.ssm-spin {
  display: flex;
  justify-content: center;
  padding: 40px 0;
}

.ssm-results {
  list-style: none;
  margin: 0;
  padding: 0;
}

.ssm-item {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 10px 13px;
  border-radius: 12px;
  margin-bottom: 2px;
  cursor: pointer;
  color: var(--ink-2);
  font-size: 13px;
  border: 1px solid transparent;
  transition: all 0.18s ease;
  animation: ssm-rise 0.3s cubic-bezier(0.32, 0.72, 0, 1) both;
  animation-delay: calc(var(--i, 0) * 18ms);
}

@keyframes ssm-rise {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.ssm-item:hover {
  background: var(--surface-strong);
  border-color: rgba(30, 64, 175, 0.12);
  color: var(--ink);
  transform: translateX(3px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 4px 14px -6px rgba(30, 64, 175, 0.18);
}

.ssm-item-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: linear-gradient(110deg, #2563eb, #0ea5e9);
  flex-shrink: 0;
  box-shadow: 0 0 6px rgba(37, 99, 235, 0.35);
  transition: transform 0.18s ease;
}

.ssm-item:hover .ssm-item-dot {
  transform: scale(1.35);
}

.ssm-item-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.ssm-item-date {
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.04em;
  color: var(--ink-4);
}

.ssm-item-arrow {
  flex-shrink: 0;
  font-size: 14px;
  color: var(--accent);
  opacity: 0;
  transform: translateX(-4px);
  transition: all 0.18s ease;
}

.ssm-item:hover .ssm-item-arrow {
  opacity: 0.8;
  transform: translateX(0);
}

.ssm-empty {
  text-align: center;
  font-size: 13px;
  color: var(--ink-3);
  padding: 44px 16px;
  margin: 0;
}
</style>
