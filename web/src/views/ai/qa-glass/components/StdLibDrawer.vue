<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue';
import { fetchStdSyncStatus, fetchStdSyncTrigger } from '@/service/api';

/**
 * 标准库同步管理抽屉（纯自绘，零 naive-ui 依赖，复刻向量库管理面的玻璃视觉语言）。
 *
 * 数据源：后端指纹增量同步引擎（/ai/std-sync，源库 → 本地业务库，见 app/services/std_sync.py）。
 * 打开即拉一次状态；运行中 3s 轮询，结束自动停轮询。
 */
const props = defineProps<{ show: boolean }>();
const emit = defineEmits<{ (e: 'update:show', v: boolean): void }>();

const visible = computed({
  get: () => props.show,
  set: (v: boolean) => emit('update:show', v)
});

const loading = ref(false);
const triggering = ref(false);
const status = ref<Api.AI.StdSyncStatus | null>(null);

let pollTimer: ReturnType<typeof setInterval> | null = null;

function stopPoll() {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function refresh(silent = false) {
  if (!silent) loading.value = true;
  const { data, error } = await fetchStdSyncStatus();
  if (!error && data) {
    status.value = data;
    if (!data.running) stopPoll();
  }
  if (!silent) loading.value = false;
}

function startPollIfNeeded() {
  if (status.value?.running && pollTimer === null) {
    pollTimer = setInterval(() => refresh(true), 3000);
  }
}

watch(
  () => props.show,
  async v => {
    if (v) {
      await refresh();
      startPollIfNeeded();
      window.addEventListener('keydown', onKeydown);
    } else {
      stopPoll();
      window.removeEventListener('keydown', onKeydown);
    }
  }
);

onUnmounted(() => {
  stopPoll();
  window.removeEventListener('keydown', onKeydown);
});

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') visible.value = false;
}

async function triggerSync() {
  if (triggering.value || status.value?.running) return;
  triggering.value = true;
  const { error } = await fetchStdSyncTrigger();
  triggering.value = false;
  if (!error) {
    await refresh(true);
    startPollIfNeeded();
  }
}

// ── 展示辅助 ────────────────────────────────────────────────────────────────

const STATUS_META: Record<string, { label: string; cls: string }> = {
  idle: { label: '未运行过', cls: 'is-empty' },
  running: { label: '同步中', cls: 'is-building' },
  done: { label: '已完成', cls: 'is-ready' },
  error: { label: '同步失败', cls: 'is-error' }
};

const PHASE_LABEL: Record<string, string> = {
  diff: '指纹比对',
  upsert: '回写',
  delete: '清理',
  done: '完成'
};

const statusMeta = computed(() => STATUS_META[status.value?.status || 'idle'] || STATUS_META.idle);

const runningTableLabel = computed(() => {
  const st = status.value;
  if (!st?.running || !st.progress) return '';
  return st.tables.find(t => t.name === st.progress!.table)?.label || st.progress!.table;
});

const tableRows = computed(() => {
  const st = status.value;
  if (!st) return [];
  const runningIdx = st.progress ? st.tables.findIndex(t => t.name === st.progress!.table) : -1;
  return st.tables.map((t, i) => {
    const s = st.stats?.[t.name];
    let phase: 'pending' | 'running' | 'done' = 'done';
    if (st.running) {
      if (runningIdx >= 0) phase = i < runningIdx ? 'done' : i === runningIdx ? 'running' : 'pending';
      else phase = 'pending';
    }
    return { ...t, stats: s || null, phase };
  });
});

const totals = computed(() => {
  const st = status.value?.stats;
  if (!st) return null;
  const sum = { added: 0, updated: 0, deleted: 0, unchanged: 0 };
  for (const v of Object.values(st)) {
    sum.added += v.added;
    sum.updated += v.updated;
    sum.deleted += v.deleted;
    sum.unchanged += v.unchanged;
  }
  return sum;
});

function fmtNum(n: number | null | undefined): string {
  return n == null ? '—' : n.toLocaleString('en-US');
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="sls-root">
      <div class="sls-mask" @click="visible = false" />
      <aside class="sls-panel">
        <header class="sls-head">
          <div class="sls-head-txt">
            <div class="sls-title">标准库同步</div>
            <div class="sls-sub">源库 → 本地业务库 · 指纹增量（只写差异行，可重复执行）</div>
          </div>
          <button class="sls-close" title="关闭（Esc）" @click="visible = false">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </header>

        <div class="sls-body">
          <!-- 源库参照系 -->
          <div class="sls-ref">
            <div class="sls-ref-line">
              <span class="sls-ref-k">源库</span>
              <span v-if="status" class="sls-ref-v">{{ status.source.host }}:{{ status.source.port }} / {{ status.source.database }}</span>
              <span v-else class="sls-ref-v">加载中…</span>
              <span v-if="status" class="sls-ref-chip" :class="status.dailyEnabled ? 'is-ready' : 'is-empty'">
                {{ status.dailyEnabled ? '每日 02:30 自动同步' : '定时同步已关闭' }}
              </span>
            </div>
            <div class="sls-ref-tip">同步六张 standard_* 表；源端删除的行本地物理删除，与源库保持镜像</div>
          </div>

          <!-- 最近一次同步 -->
          <article class="sls-card" :class="statusMeta.cls">
            <div class="sls-top">
              <span class="sls-chip" :class="statusMeta.cls">
                <span v-if="status?.running" class="sls-chip-spin" />{{ statusMeta.label }}
              </span>
              <span v-if="status?.trigger && !status.running" class="sls-chip is-plain">{{ status.trigger === 'cron' ? '定时触发' : '手工触发' }}</span>
              <span class="sls-top-actions">
                <button class="sls-btn" :disabled="loading" @click="refresh()">
                  <span v-if="loading" class="sls-btn-spin" />刷新
                </button>
                <button class="sls-btn is-primary" :disabled="status?.running || triggering" @click="triggerSync">
                  <span v-if="triggering || status?.running" class="sls-btn-spin" />{{ status?.running ? '同步中…' : '立即同步' }}
                </button>
              </span>
            </div>

            <!-- 实时进度（运行中） -->
            <div v-if="status?.running && status.progress" class="sls-progress">
              <div class="sls-progress-track">
                <div class="sls-progress-bar ind" />
              </div>
              <span class="sls-progress-txt">
                {{ runningTableLabel }}
                · {{ PHASE_LABEL[status.progress.phase] || status.progress.phase }}
                <template v-if="status.progress.detail"> · {{ status.progress.detail }}</template>
              </span>
            </div>

            <!-- 时间信息 -->
            <div v-if="status?.startedAt || status?.finishedAt" class="sls-kvs">
              <div class="sls-kv">
                <span class="sls-kv-k">开始</span>
                <span class="sls-kv-v">{{ status?.startedAt || '—' }}</span>
              </div>
              <div class="sls-kv">
                <span class="sls-kv-k">结束</span>
                <span class="sls-kv-v">{{ status?.finishedAt || '—' }}</span>
              </div>
              <div class="sls-kv">
                <span class="sls-kv-k">耗时</span>
                <span class="sls-kv-v">{{ status?.durationSec != null ? `${status.durationSec}s` : '—' }}</span>
              </div>
            </div>

            <div v-if="status?.status === 'error' && status.error" class="sls-banner is-error">{{ status.error }}</div>
          </article>

          <!-- 分表明细（自绘表格） -->
          <div class="sls-table">
            <div class="sls-thead">
              <span>表</span>
              <span class="num" title="源端有、本地无 → 写入">新增</span>
              <span class="num" title="指纹不一致 → 按源端覆盖">更新</span>
              <span class="num" title="本地有、源端无 → 物理删除">删除</span>
              <span class="num">未变</span>
              <span />
            </div>
            <div v-if="!tableRows.length" class="sls-empty">{{ loading ? '加载中…' : '暂无同步记录' }}</div>
            <template v-else>
              <div v-for="row in tableRows" :key="row.name" class="sls-tr" :class="{ 'is-running': row.phase === 'running' }">
                <span class="sls-td-name">
                  {{ row.label }}
                  <span class="sls-td-key">{{ row.name }}</span>
                </span>
                <span class="num">{{ fmtNum(row.stats?.added) }}</span>
                <span class="num">{{ fmtNum(row.stats?.updated) }}</span>
                <span class="num">{{ fmtNum(row.stats?.deleted) }}</span>
                <span class="num">{{ fmtNum(row.stats?.unchanged) }}</span>
                <span class="sls-td-state">
                  <span v-if="row.phase === 'running'" class="sls-state-run"><span class="sls-chip-spin" />进行中</span>
                  <span v-else-if="row.phase === 'pending'" class="sls-state-pend">待执行</span>
                </span>
              </div>
              <div v-if="totals" class="sls-tr is-total">
                <span>合计</span>
                <span class="num">{{ fmtNum(totals.added) }}</span>
                <span class="num">{{ fmtNum(totals.updated) }}</span>
                <span class="num">{{ fmtNum(totals.deleted) }}</span>
                <span class="num">{{ fmtNum(totals.unchanged) }}</span>
                <span />
              </div>
            </template>
          </div>

          <div class="sls-tip">
            全量一轮约 7 分钟（千万级章节表指纹扫描为主）。同步期间本地库可正常读写，写入按批进行；
            源端「软删除」的基础信息行会在本地物理删除，请确认后再手工触发。
          </div>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
/* ── 遮罩 + 面板骨架（自绘抽屉） ────────────────────────────── */
.sls-root {
  position: fixed;
  inset: 0;
  z-index: 2050;
}

.sls-mask {
  position: absolute;
  inset: 0;
  background: rgba(15, 23, 42, 0.32);
  backdrop-filter: blur(2px);
}

.sls-panel {
  position: absolute;
  top: 0;
  right: 0;
  height: 100%;
  width: min(720px, 100vw);
  display: flex;
  flex-direction: column;
  background: rgba(255, 255, 255, 0.94);
  backdrop-filter: blur(18px);
  border-left: 1px solid rgba(30, 64, 175, 0.12);
  box-shadow: -16px 0 48px -16px rgba(15, 23, 42, 0.25);
  animation: sls-in 0.22s ease;
}

@keyframes sls-in {
  from {
    transform: translateX(40px);
    opacity: 0;
  }
  to {
    transform: translateX(0);
    opacity: 1;
  }
}

.sls-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px 12px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}

.sls-head-txt {
  flex: 1;
  min-width: 0;
}

.sls-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink, #0f172a);
}

.sls-sub {
  margin-top: 2px;
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
}

.sls-close {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 8px;
  border: 1px solid rgba(100, 116, 139, 0.2);
  background: rgba(255, 255, 255, 0.7);
  color: var(--ink-3, #64748b);
  cursor: pointer;
  transition: all 0.15s ease;
}

.sls-close:hover {
  color: #d03050;
  border-color: rgba(208, 48, 80, 0.4);
}

.sls-body {
  flex: 1;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，滚动条出现/消失不挤压内容、不抖动 */
  padding: 16px 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ── 源库参照系 ─────────────────────────────────────────────── */
.sls-ref {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 14px;
  border-radius: 12px;
  background: linear-gradient(120deg, rgba(30, 64, 175, 0.07), rgba(30, 64, 175, 0.02));
  border: 1px solid rgba(30, 64, 175, 0.12);
}

.sls-ref-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.sls-ref-k {
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
}

.sls-ref-v {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 700;
  color: #1e40af;
}

.sls-ref-chip {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 999px;
}

.sls-ref-chip.is-ready {
  background: rgba(22, 163, 74, 0.1);
  color: #15803d;
}

.sls-ref-chip.is-empty {
  background: rgba(100, 116, 139, 0.12);
  color: #64748b;
}

.sls-ref-tip {
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
}

/* ── 按钮（自绘，与向量库管理面同款语言） ───────────────────── */
.sls-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 11px;
  border-radius: 8px;
  border: 1px solid rgba(100, 116, 139, 0.28);
  background: rgba(255, 255, 255, 0.7);
  color: var(--ink-3, #475569);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.5;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.18s ease;
}

.sls-btn:hover:not(:disabled) {
  border-color: rgba(30, 64, 175, 0.4);
  color: #1e40af;
  background: rgba(255, 255, 255, 0.95);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px -4px rgba(30, 64, 175, 0.25);
}

.sls-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.sls-btn.is-primary {
  border-color: rgba(30, 64, 175, 0.35);
  background: rgba(30, 64, 175, 0.09);
  color: #1e40af;
}

.sls-btn.is-primary:hover:not(:disabled) {
  background: rgba(30, 64, 175, 0.16);
}

.sls-btn-spin {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
  border: 1.5px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: sls-rotate 0.7s linear infinite;
}

.sls-chip-spin {
  width: 9px;
  height: 9px;
  flex-shrink: 0;
  border: 1.5px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: sls-rotate 0.7s linear infinite;
}

@keyframes sls-rotate {
  to {
    transform: rotate(360deg);
  }
}

/* ── 状态卡片 ───────────────────────────────────────────────── */
.sls-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 14px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.6),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 6px 20px -10px rgba(30, 64, 175, 0.12);
}

.sls-card.is-error {
  border-color: rgba(208, 48, 80, 0.25);
}

.sls-top {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}

.sls-top-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}

.sls-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 600;
  line-height: 1.7;
}

.sls-chip.is-ready {
  background: rgba(22, 163, 74, 0.1);
  color: #15803d;
}

.sls-chip.is-building {
  background: rgba(30, 64, 175, 0.1);
  color: #1e40af;
}

.sls-chip.is-error {
  background: rgba(208, 48, 80, 0.1);
  color: #d03050;
}

.sls-chip.is-empty {
  background: rgba(100, 116, 139, 0.12);
  color: #64748b;
}

.sls-chip.is-plain {
  background: rgba(100, 116, 139, 0.08);
  color: var(--ink-3, #64748b);
  font-weight: 500;
}

/* ── 实时进度 ───────────────────────────────────────────────── */
.sls-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(30, 64, 175, 0.05);
  border: 1px solid rgba(30, 64, 175, 0.1);
}

.sls-progress-track {
  flex: 1;
  height: 6px;
  border-radius: 999px;
  background: rgba(30, 64, 175, 0.12);
  overflow: hidden;
}

.sls-progress-bar.ind {
  width: 35%;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #3b82f6, #1e40af);
  animation: sls-slide 1.3s ease-in-out infinite;
}

@keyframes sls-slide {
  0% {
    transform: translateX(-110%);
  }
  100% {
    transform: translateX(320%);
  }
}

.sls-progress-txt {
  font-size: 11px;
  color: #1e40af;
  white-space: nowrap;
}

/* ── 时间 kv ────────────────────────────────────────────────── */
.sls-kvs {
  display: flex;
  align-items: stretch;
  padding: 4px 2px 0;
}

.sls-kv {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 16px;
}

.sls-kv:first-child {
  padding-left: 0;
}

.sls-kv + .sls-kv {
  border-left: 1px dashed rgba(30, 64, 175, 0.14);
}

.sls-kv-k {
  font-size: 10px;
  color: var(--ink-4, #94a3b8);
}

.sls-kv-v {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink, #0f172a);
  font-family: 'JetBrains Mono', monospace;
}

.sls-banner {
  padding: 7px 11px;
  border-radius: 9px;
  font-size: 11px;
  line-height: 1.6;
  word-break: break-all;
}

.sls-banner.is-error {
  background: rgba(208, 48, 80, 0.06);
  border: 1px solid rgba(208, 48, 80, 0.15);
  color: #d03050;
}

/* ── 分表明细（自绘表格） ───────────────────────────────────── */
.sls-table {
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.55);
  overflow: hidden;
}

.sls-thead,
.sls-tr {
  display: grid;
  grid-template-columns: minmax(180px, 1.4fr) repeat(4, minmax(70px, 0.8fr)) 70px;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
}

.sls-thead {
  font-size: 10px;
  font-weight: 600;
  color: var(--ink-4, #94a3b8);
  background: rgba(30, 64, 175, 0.04);
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}

.sls-thead .num,
.sls-tr .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.sls-tr {
  font-size: 12px;
  color: var(--ink, #0f172a);
  border-bottom: 1px solid rgba(30, 64, 175, 0.05);
}

.sls-tr:last-child {
  border-bottom: none;
}

.sls-tr.is-running {
  background: rgba(30, 64, 175, 0.045);
}

.sls-tr.is-total {
  font-weight: 700;
  background: rgba(30, 64, 175, 0.03);
  border-top: 1px solid rgba(30, 64, 175, 0.1);
}

.sls-td-name {
  display: flex;
  align-items: baseline;
  gap: 7px;
  min-width: 0;
  font-weight: 600;
}

.sls-td-key {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 400;
  color: var(--ink-4, #94a3b8);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sls-td-state {
  font-size: 10px;
  color: var(--ink-4, #94a3b8);
}

.sls-state-run {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #1e40af;
  font-weight: 600;
}

.sls-empty {
  padding: 18px 14px;
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
  text-align: center;
}

.sls-tip {
  font-size: 11px;
  line-height: 1.7;
  color: var(--ink-4, #94a3b8);
  padding: 0 2px;
}

/* ── 响应式 ─────────────────────────────────────────────────── */
@media (max-width: 640px) {
  .sls-thead,
  .sls-tr {
    grid-template-columns: minmax(120px, 1.4fr) repeat(4, minmax(52px, 0.8fr)) 56px;
    gap: 4px;
    padding: 8px 10px;
  }

  .sls-kvs {
    flex-wrap: wrap;
  }
}
</style>
