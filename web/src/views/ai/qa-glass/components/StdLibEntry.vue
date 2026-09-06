<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue';
import StdLibDrawer from './StdLibDrawer.vue';
import { useAuthStore } from '@/store/modules/auth';
import { fetchStdSyncStatus } from '@/service/api';

/**
 * 标准库同步入口（仅管理员可见：R_SUPER / R_ADMIN，普通用户不渲染）。
 * 由 QATopBar 引入，与「向量库」按钮并列。
 * 点击打开同步管理抽屉（源库信息 / 最近一次结果 / 立即同步）。
 *
 * 状态徽标：同步运行中 → 旋转小圈；最近一次失败 → 红色圆点提醒。
 */
const authStore = useAuthStore();
const isAdmin = computed(() => (authStore.userInfo?.roles || []).some(r => r === 'R_SUPER' || r === 'R_ADMIN'));

const drawerShow = ref(false);
const running = ref(false);
const lastError = ref(false);

let pollTimer: ReturnType<typeof setTimeout> | null = null;

function clearPoll() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

/** 拉状态刷新徽标；运行中则 10s 后再查一次，直到安静 */
async function refreshBadges() {
  if (!isAdmin.value) return;
  const { data, error } = await fetchStdSyncStatus();
  if (error || !data) return;
  running.value = data.running;
  lastError.value = !data.running && data.status === 'error';
  clearPoll();
  if (data.running) {
    pollTimer = setTimeout(refreshBadges, 10000);
  }
}

/** 抽屉关闭后刷新一次（刚触发的同步结果要反映到徽标） */
function onDrawerClose(v: boolean) {
  drawerShow.value = v;
  if (!v) refreshBadges();
}

if (isAdmin.value) refreshBadges();

onUnmounted(clearPoll);
</script>

<template>
  <div v-if="isAdmin" class="stdlib-entry">
    <button class="vl-trigger" title="标准库同步管理（源库 → 本地业务库，指纹增量）" @click="drawerShow = true">
      <span class="vl-icon">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /><path d="M3 3v5h5" /><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" /><path d="M16 16h5v5" /></svg>
      </span>
      <span class="vl-text">标准库</span>
      <span v-if="running" class="vl-badge is-building" title="标准库正在同步" />
      <span v-else-if="lastError" class="vl-badge is-error" title="最近一次同步失败，请打开查看" />
    </button>

    <StdLibDrawer :show="drawerShow" @update:show="onDrawerClose" />
  </div>
</template>

<style scoped>
.stdlib-entry {
  position: relative;
  display: inline-flex;
  flex-shrink: 0;
}

/* ─── 触发按钮（与向量库入口同款玻璃语言） ───────────────────── */
.vl-trigger {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  margin-left: 4px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.18);
  color: #1e40af;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.005em;
  cursor: pointer;
  border-radius: 9px;
  transition: all 0.2s ease;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
}
.vl-trigger:hover {
  border-color: rgba(30, 64, 175, 0.35);
  background: rgba(255, 255, 255, 0.78);
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 8px 24px -6px rgba(30, 64, 175, 0.28);
}

.vl-icon {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}

/* ─── 状态徽标 ───────────────────────────────────────────────── */
.vl-badge.is-building {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
  border: 1.5px solid rgba(30, 64, 175, 0.75);
  border-top-color: transparent;
  border-radius: 50%;
  animation: vl-rotate 0.7s linear infinite;
}

.vl-badge.is-error {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #d03050;
  box-shadow: 0 0 0 2px rgba(208, 48, 80, 0.18);
  animation: vl-pulse 1.6s ease-in-out infinite;
}

@keyframes vl-rotate {
  to {
    transform: rotate(360deg);
  }
}

@keyframes vl-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 2px rgba(208, 48, 80, 0.18);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(208, 48, 80, 0.08);
  }
}

/* ─── 响应式：手机端隐藏文字，仅留图标 ───────────────────────── */
@media (max-width: 960px) {
  .vl-trigger {
    border-radius: 999px;
    padding: 6px 9px;
    margin-left: 0;
  }
  .vl-text {
    display: none;
  }
}
</style>
