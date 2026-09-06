<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue';
import VectorLibDrawer from './VectorLibDrawer.vue';
import { useAuthStore } from '@/store/modules/auth';
import { fetchVectorLibList } from '@/service/api';

/**
 * 向量库管理入口（仅管理员可见：R_SUPER / R_ADMIN，普通用户不渲染）。
 * 由 QATopBar 引入，与「模型」按钮并列。
 * 点击打开向量库管理抽屉：系统库 + 用户库合并展示（建库 / 条目 / 上传 / 构建 / 检索验证）。
 *
 * 状态徽标：拉一次列表摘要——有库在构建中 → 图标旁旋转小圈；
 * 有库陈旧（向量模型已切换、语义搜索被拒）→ 橙色圆点提醒。
 */
const authStore = useAuthStore();
const isAdmin = computed(() => (authStore.userInfo?.roles || []).some(r => r === 'R_SUPER' || r === 'R_ADMIN'));

const drawerShow = ref(false);
const anyBuilding = ref(false);
const anyStale = ref(false);

let pollTimer: ReturnType<typeof setTimeout> | null = null;

function clearPoll() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

/** 拉列表摘要刷新徽标；构建中则 10s 后再查一次，直到安静 */
async function refreshBadges() {
  if (!isAdmin.value) return;
  const { data, error } = await fetchVectorLibList();
  if (error || !data) return;
  anyBuilding.value = data.records.some(l => l.isBuilding);
  anyStale.value = data.records.some(l => l.state === 'stale');
  clearPoll();
  if (anyBuilding.value) {
    pollTimer = setTimeout(refreshBadges, 10000);
  }
}

/** 抽屉关闭后刷新一次（刚触发的构建 / 重建结果要反映到徽标） */
function onDrawerClose(v: boolean) {
  drawerShow.value = v;
  if (!v) refreshBadges();
}

if (isAdmin.value) refreshBadges();

onUnmounted(clearPoll);
</script>

<template>
  <div v-if="isAdmin" class="vector-lib-entry">
    <button class="vl-trigger" title="向量库管理（系统库 + 用户库）" @click="drawerShow = true">
      <span class="vl-icon">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14a9 3 0 0 0 18 0V5" /><path d="M3 12a9 3 0 0 0 18 0" /></svg>
      </span>
      <span class="vl-text">向量库</span>
      <span v-if="anyBuilding" class="vl-badge is-building" title="有向量库正在构建 / 同步" />
      <span v-else-if="anyStale" class="vl-badge is-stale" title="有向量库已陈旧：向量模型已切换，需重建后才能语义搜索" />
    </button>

    <VectorLibDrawer :show="drawerShow" @update:show="onDrawerClose" />
  </div>
</template>

<style scoped>
.vector-lib-entry {
  position: relative;
  display: inline-flex;
  flex-shrink: 0;
}

/* ─── 触发按钮（与模型入口同款玻璃语言） ─────────────────────── */
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
/* 构建中：旋转小圈（跟在文字后） */
.vl-badge.is-building {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
  border: 1.5px solid rgba(30, 64, 175, 0.75);
  border-top-color: transparent;
  border-radius: 50%;
  animation: vl-rotate 0.7s linear infinite;
}

/* 陈旧：橙色呼吸圆点 */
.vl-badge.is-stale {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #d97706;
  box-shadow: 0 0 0 2px rgba(217, 119, 6, 0.18);
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
    box-shadow: 0 0 0 2px rgba(217, 119, 6, 0.18);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(217, 119, 6, 0.08);
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
