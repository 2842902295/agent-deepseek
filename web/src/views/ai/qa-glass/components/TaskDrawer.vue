<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue';
import { useMessage } from 'naive-ui';
import type { ScheduledTask } from '@/service/api';
import {
  fetchScheduledTasks,
  fetchPauseScheduledTask,
  fetchResumeScheduledTask,
  fetchDeleteScheduledTask
} from '@/service/api';

const props = defineProps<{ show: boolean }>();
const emit = defineEmits<{
  'update:show': [value: boolean];
  loadSession: [key: string];
  /** 引导到会话框：关闭抽屉并把起手文案填入输入框 */
  fill: [text: string];
}>();

const message = useMessage();
const loading = ref(false);
const tasks = ref<ScheduledTask[]>([]);
const expandedTask = ref<number | null>(null);
let pollTimer: ReturnType<typeof setInterval> | null = null;

const WEEK_NAMES = ['日', '一', '二', '三', '四', '五', '六'];
const isCronNum = (s: string) => /^\d{1,2}$/.test(s);

/** 周字段 → 人性化文本，无法识别返回 null */
function dowToText(dow: string): string | null {
  if (dow === '1-5') return '工作日';
  if (dow === '0,6' || dow === '6,0') return '周末';
  if (dow.split(',').every(isCronNum)) {
    const names = [...new Set(dow.split(',').map(n => WEEK_NAMES[Number(n) % 7]))];
    return `每周${names.join('、')}`;
  }
  const range = dow.match(/^(\d)-(\d)$/);
  if (range) return `每周${WEEK_NAMES[Number(range[1]) % 7]}至周${WEEK_NAMES[Number(range[2]) % 7]}`;
  return null;
}

/** cron 表达式 → 人性化文本；无法识别的组合原样返回（hover 可看原文） */
function cronToText(cron: string): string {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [min, hour, dom, mon, dow] = parts;
  const fixedTime =
    isCronNum(hour) && isCronNum(min) ? `${hour.padStart(2, '0')}:${min.padStart(2, '0')}` : null;

  // 间隔类（日期部分全为 *）
  if (dom === '*' && mon === '*' && dow === '*') {
    if (min === '*' && hour === '*') return '每分钟';
    const stepMin = min.match(/^\*\/(\d+)$/);
    if (stepMin && hour === '*') return Number(stepMin[1]) === 1 ? '每分钟' : `每 ${stepMin[1]} 分钟`;
    if (isCronNum(min) && hour === '*') return `每小时第 ${min} 分钟`;
    const stepHour = hour.match(/^\*\/(\d+)$/);
    if (stepHour && isCronNum(min)) {
      const n = Number(stepHour[1]);
      const base = n === 1 ? '每小时' : `每 ${n} 小时`;
      return Number(min) === 0 ? base : `${base}（第 ${min} 分）`;
    }
    // 一天多个时间点：0 9,18 * * *
    if (isCronNum(min) && hour.split(',').every(isCronNum)) {
      return (
        '每天 ' + hour.split(',').map(h => `${h.padStart(2, '0')}:${min.padStart(2, '0')}`).join('、')
      );
    }
  }

  // 日期前缀
  let dayText: string | null = null;
  if (dom === '*' && mon === '*' && dow === '*') dayText = '每天';
  else if (mon === '*' && dom === '*') dayText = dowToText(dow);
  else if (mon === '*' && dow === '*' && dom.split(',').every(isCronNum)) {
    dayText = `每月 ${dom.split(',').join('、')}日`;
  }

  if (dayText && fixedTime) return `${dayText} ${fixedTime}`;
  return cron;
}

function formatTime(ts: number | null): string {
  if (!ts) return '—';
  const d = new Date(ts);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  if (isToday) return `今天 ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
}

function formatDuration(ms: number | null): string {
  if (!ms) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

const statusMap: Record<string, { label: string; cls: string }> = {
  active: { label: '运行中', cls: 'st-active' },
  paused: { label: '已暂停', cls: 'st-paused' },
  canceled: { label: '已取消', cls: 'st-canceled' }
};

async function loadTasks() {
  loading.value = true;
  try {
    const { data, error } = await fetchScheduledTasks({ size: 50 });
    tasks.value = !error && data ? (data.items || []) : [];
  } catch {
    tasks.value = [];
  } finally {
    loading.value = false;
  }
}

async function handlePause(task: ScheduledTask) {
  try {
    const { error } = await fetchPauseScheduledTask(task.id);
    if (!error) { message.success('已暂停'); await loadTasks(); }
  } catch {
    message.error('操作失败');
  }
}

async function handleResume(task: ScheduledTask) {
  try {
    const { error } = await fetchResumeScheduledTask(task.id);
    if (!error) { message.success('已恢复'); await loadTasks(); }
  } catch {
    message.error('操作失败');
  }
}

async function handleDelete(task: ScheduledTask) {
  try {
    const { error } = await fetchDeleteScheduledTask(task.id);
    if (!error) { message.success('已删除'); await loadTasks(); }
  } catch {
    message.error('操作失败');
  }
}

function handleViewSession(sessionKey: string) {
  emit('loadSession', sessionKey);
  emit('update:show', false);
}

function toggleExpand(taskId: number) {
  expandedTask.value = expandedTask.value === taskId ? null : taskId;
}

function close() {
  emit('update:show', false);
}

/** 到会话中创建：关闭抽屉并把起手文案填入输入框 */
function goCreate() {
  emit('fill', '帮我创建一个定时任务：');
  close();
}

watch(
  () => props.show,
  open => {
    if (open) {
      loadTasks();
      pollTimer = setInterval(loadTasks, 10000);
    } else {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }
  }
);

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<template>
  <Teleport to="body">
    <Transition name="td-mask">
      <div v-if="show" class="td-mask" @click="close" />
    </Transition>

    <Transition name="td-panel">
      <div v-if="show" class="td-panel" @click.stop>
        <header class="td-head">
          <span class="td-head-tag">SCHEDULED</span>
          <span class="td-head-line" />
          <span class="td-head-title">定时任务</span>
          <button class="td-close" @click="close">×</button>
        </header>

        <div class="td-body">
          <!-- 创建提示 -->
          <div class="td-hint">
            <div class="td-hint-text">
              在对话中告诉 Agent 你想定时做的事，如：<br>
              <span class="td-hint-ex">「每天早上 9 点帮我整理行业新闻」</span>
              <span class="td-hint-ex">「每周五下午生成本周工作周报」</span>
            </div>
            <button class="td-hint-btn" @click="goCreate">
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
              到会话中创建
            </button>
          </div>

          <div v-if="loading && tasks.length === 0" class="td-loading">
            <span class="td-spin" />
          </div>

          <div v-else-if="tasks.length === 0" class="td-empty">
            <div class="td-empty-icon">⏱</div>
            <div class="td-empty-text">还没有定时任务</div>
          </div>

          <div v-else class="td-list">
            <div v-for="task in tasks" :key="task.id" class="td-card">
              <!-- 任务头部 -->
              <div class="td-card-row">
                <span class="td-card-title">{{ task.title }}</span>
                <span class="td-card-status" :class="statusMap[task.status]?.cls || 'st-canceled'">
                  {{ statusMap[task.status]?.label || task.status }}
                </span>
              </div>

              <div class="td-card-meta">
                <span class="td-cron" :title="task.cronExpr">{{ cronToText(task.cronExpr) }}</span>
                <span class="td-sep">·</span>
                <span>执行 {{ task.runCount }} 次</span>
              </div>

              <!-- 操作按钮 -->
              <div class="td-card-actions">
                <button
                  v-if="task.status === 'active'"
                  class="td-btn td-btn-ghost"
                  @click="handlePause(task)"
                >暂停</button>
                <button
                  v-if="task.status === 'paused'"
                  class="td-btn td-btn-primary"
                  @click="handleResume(task)"
                >恢复</button>
                <button
                  v-if="task.status !== 'canceled'"
                  class="td-btn td-btn-danger"
                  @click="handleDelete(task)"
                >删除</button>
                <button
                  v-if="task.recentRuns && task.recentRuns.length > 0"
                  class="td-btn td-btn-ghost td-btn-expand"
                  @click="toggleExpand(task.id)"
                >
                  {{ expandedTask === task.id ? '收起记录' : `执行记录 (${task.runCount})` }}
                  <span class="td-expand-arrow" :class="{ 'td-expand-arrow-open': expandedTask === task.id }">▾</span>
                </button>
              </div>

              <!-- 执行记录展开区 -->
              <Transition name="td-runs">
                <div v-if="expandedTask === task.id && task.recentRuns" class="td-runs">
                  <div
                    v-for="run in task.recentRuns"
                    :key="run.id"
                    class="td-run"
                  >
                    <div class="td-run-head">
                      <span class="td-run-dot" :class="run.status === 'done' ? 'td-run-ok' : 'td-run-err'" />
                      <span class="td-run-time">{{ formatTime(run.createTime) }}</span>
                      <span class="td-run-dur">{{ formatDuration(run.durationMs) }}</span>
                      <a
                        v-if="run.sessionKey"
                        class="td-run-link"
                        @click="handleViewSession(run.sessionKey)"
                      >查看对话 →</a>
                    </div>
                    <div v-if="run.resultSummary" class="td-run-summary">{{ run.resultSummary }}</div>
                    <div v-if="run.error" class="td-run-error">⚠ {{ run.error }}</div>
                  </div>
                </div>
              </Transition>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.td-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.18);
  backdrop-filter: blur(2px);
  z-index: 2100;
}
.td-mask-enter-active, .td-mask-leave-active { transition: opacity 0.32s ease; }
.td-mask-enter-from, .td-mask-leave-to { opacity: 0; }

.td-panel {
  position: fixed;
  top: 0; right: 0;
  width: min(480px, 90vw);
  height: 100dvh;
  background: var(--surface, rgba(255, 255, 255, 0.42));
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border-left: 1px solid rgba(30, 64, 175, 0.12);
  box-shadow: -4px 0 32px -4px rgba(30, 64, 175, 0.10);
  z-index: 2101;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: 'Plus Jakarta Sans', 'Inter', system-ui, sans-serif;
  color: var(--ink, #0f172a);
}
.td-panel-enter-active { transition: transform 0.42s cubic-bezier(0.22, 1, 0.36, 1); }
.td-panel-leave-active { transition: transform 0.28s cubic-bezier(0.4, 0, 1, 1); }
.td-panel-enter-from, .td-panel-leave-to { transform: translateX(100%); }

.td-head {
  display: flex; align-items: center; gap: 10px;
  padding: 18px 20px 14px; flex-shrink: 0;
}
.td-head-tag {
  font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--ink-3, #64748b);
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  padding: 3px 8px; border-radius: 5px;
  border: 1px solid rgba(30, 64, 175, 0.08);
}
.td-head-line { flex: 1; height: 1px; background: rgba(30, 64, 175, 0.08); }
.td-head-title { font-size: 15px; font-weight: 600; }
.td-close {
  width: 28px; height: 28px; display: flex; align-items: center; justify-content: center;
  border: none; background: transparent; color: var(--ink-3, #64748b);
  font-size: 20px; cursor: pointer; border-radius: 6px; transition: background 0.15s;
}
.td-close:hover { background: rgba(30, 64, 175, 0.06); }

.td-body { flex: 1; overflow-y: auto; padding: 0 16px 20px; }
.td-body::-webkit-scrollbar { width: 5px; }
.td-body::-webkit-scrollbar-track { background: transparent; }
.td-body::-webkit-scrollbar-thumb { background: rgba(30, 64, 175, 0.12); border-radius: 3px; }

/* ─── 创建提示 ─── */
.td-hint {
  margin-bottom: 14px;
  padding: 12px 14px;
  background: rgba(37, 99, 235, 0.04);
  border: 1px solid rgba(37, 99, 235, 0.10);
  border-radius: 10px;
}
.td-hint-text {
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ink-3, #64748b);
}
.td-hint-ex {
  display: inline-block;
  margin-top: 2px;
  margin-right: 8px;
  font-size: 12px;
  color: #2563eb;
  background: rgba(37, 99, 235, 0.06);
  padding: 2px 8px;
  border-radius: 4px;
}
.td-hint-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  padding: 7px 15px;
  border-radius: 8px;
  border: transparent;
  background: linear-gradient(135deg, #2563eb, #1e40af);
  color: #fff;
  cursor: pointer;
  box-shadow: 0 4px 14px -4px rgba(37, 99, 235, 0.4);
  transition: all 0.15s;
}
.td-hint-btn:hover {
  background: linear-gradient(135deg, #1d4ed8, #1e3a8a);
  transform: translateY(-1px);
  box-shadow: 0 6px 18px -4px rgba(37, 99, 235, 0.5);
}

.td-loading { display: flex; justify-content: center; padding: 60px 0; }
.td-spin {
  width: 24px; height: 24px;
  border: 2px solid rgba(30, 64, 175, 0.12); border-top-color: #2563eb;
  border-radius: 50%; animation: td-rotate 0.7s linear infinite;
}
@keyframes td-rotate { to { transform: rotate(360deg); } }

.td-empty { text-align: center; padding: 60px 20px; }
.td-empty-icon { font-size: 32px; margin-bottom: 12px; opacity: 0.4; }
.td-empty-text { font-size: 14px; font-weight: 500; color: var(--ink-3, #64748b); }
.td-empty-hint { font-size: 12px; color: var(--ink-4, #94a3b8); margin-top: 6px; }

.td-list { display: flex; flex-direction: column; gap: 10px; }

/* ─── 卡片 ─── */
.td-card {
  padding: 14px 16px;
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  border: 1px solid rgba(30, 64, 175, 0.08);
  border-radius: 10px; transition: border-color 0.2s;
}
.td-card:hover { border-color: rgba(30, 64, 175, 0.18); }

.td-card-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.td-card-title {
  font-size: 13.5px; font-weight: 600; line-height: 1.3;
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.td-card-status {
  font-size: 11px; font-weight: 600; padding: 2px 8px;
  border-radius: 20px; white-space: nowrap;
}
.st-active { color: #059669; background: rgba(5, 150, 105, 0.10); }
.st-paused { color: #d97706; background: rgba(217, 119, 6, 0.10); }
.st-canceled { color: var(--ink-4, #94a3b8); background: rgba(148, 163, 184, 0.10); }

.td-card-meta {
  display: flex; align-items: center; gap: 6px;
  margin-top: 8px; font-size: 12px; color: var(--ink-3, #64748b);
}
.td-cron {
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  font-size: 11.5px; background: rgba(30, 64, 175, 0.04);
  padding: 1px 6px; border-radius: 4px;
}
.td-sep { color: var(--ink-4, #94a3b8); }

.td-card-actions { display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap; }
.td-btn {
  font-family: inherit; font-size: 12px; font-weight: 500;
  padding: 4px 12px; border-radius: 6px; border: 1px solid transparent;
  cursor: pointer; transition: all 0.15s;
}
.td-btn-ghost { background: transparent; color: var(--ink-3, #64748b); border-color: rgba(30, 64, 175, 0.12); }
.td-btn-ghost:hover { background: rgba(30, 64, 175, 0.04); color: var(--ink-2, #334155); }
.td-btn-primary { background: rgba(37, 99, 235, 0.08); color: #2563eb; border-color: rgba(37, 99, 235, 0.15); }
.td-btn-primary:hover { background: rgba(37, 99, 235, 0.14); }
.td-btn-danger { background: transparent; color: #dc2626; border-color: rgba(220, 38, 38, 0.12); }
.td-btn-danger:hover { background: rgba(220, 38, 38, 0.06); }

.td-btn-expand { display: flex; align-items: center; gap: 4px; margin-left: auto; }
.td-expand-arrow { font-size: 10px; transition: transform 0.2s; display: inline-block; }
.td-expand-arrow-open { transform: rotate(180deg); }

/* ─── 执行记录 ─── */
.td-runs-enter-active { transition: all 0.25s ease; }
.td-runs-leave-active { transition: all 0.2s ease; }
.td-runs-enter-from, .td-runs-leave-to { opacity: 0; max-height: 0; }
.td-runs-enter-to, .td-runs-leave-from { opacity: 1; max-height: 600px; }

.td-runs {
  margin-top: 10px; padding-top: 10px;
  border-top: 1px solid rgba(30, 64, 175, 0.06);
  display: flex; flex-direction: column; gap: 8px;
  overflow: hidden;
}

.td-run {
  padding: 8px 10px;
  background: rgba(30, 64, 175, 0.02);
  border-radius: 6px;
  border: 1px solid rgba(30, 64, 175, 0.04);
}

.td-run-head {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px;
}

.td-run-dot {
  width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
}
.td-run-ok { background: #059669; }
.td-run-err { background: #dc2626; }

.td-run-time { color: var(--ink-2, #334155); font-weight: 500; }
.td-run-dur { color: var(--ink-4, #94a3b8); font-size: 11px; }
.td-run-link {
  margin-left: auto; color: #2563eb; cursor: pointer;
  font-size: 11px; white-space: nowrap;
}
.td-run-link:hover { text-decoration: underline; }

.td-run-summary {
  margin-top: 4px; font-size: 12px; color: var(--ink-3, #64748b);
  line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
  overflow: hidden;
}

.td-run-error {
  margin-top: 4px; font-size: 12px; color: #dc2626;
  line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
