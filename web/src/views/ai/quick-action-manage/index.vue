<script setup lang="ts">
import { nextTick, ref } from 'vue';
import SvgIcon from '@/components/custom/svg-icon.vue';
import ActionStudio from './modules/action-studio.vue';
import ProfessionStudio from './modules/profession-studio.vue';
import { fetchManageQuickActions, fetchProfessions } from '@/service/api';

type Workspace = 'actions' | 'professions';

const workspace = ref<Workspace>('actions');
const professionStudioRef = ref<InstanceType<typeof ProfessionStudio>>();

const actionCount = ref(0);
const professionCount = ref(0);
const onboardedCount = ref(0);

async function loadOverview() {
  const [a, p] = await Promise.all([fetchManageQuickActions(), fetchProfessions()]);
  if (!a.error && a.data) actionCount.value = a.data.actions.length;
  if (!p.error && p.data) {
    professionCount.value = p.data.length;
    onboardedCount.value = p.data.reduce((sum, x) => sum + (x.userCount ?? 0), 0);
  }
}

function switchWorkspace(ws: Workspace) {
  if (workspace.value === ws) return;
  workspace.value = ws;
  loadOverview(); // 任一侧可能增删过数据，顶栏计数保持新鲜
}

/** 功能详情里的职业 chip 直达：切换工作区并选中目标职业 */
function onOpenProfession(id: number) {
  switchWorkspace('professions');
  nextTick(() => professionStudioRef.value?.openProfession(id));
}

loadOverview();
</script>

<template>
  <div class="qac-root">
    <!-- aurora 背景（整个控制台唯一一份，两个工作台共享） -->
    <div class="aurora" aria-hidden="true">
      <div class="aurora-orb aurora-1" />
      <div class="aurora-orb aurora-2" />
      <div class="aurora-orb aurora-3" />
      <div class="aurora-orb aurora-4" />
      <div class="aurora-grain" />
    </div>

    <!-- ── 顶栏：品牌 + 工作区切换 + 总览 ── -->
    <header class="qac-topbar">
      <div class="qac-brand">
        <div class="qac-brand-mark">
          <span class="qac-bm-glyph">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4">
              <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </span>
          <span class="qac-bm-halo" />
        </div>
        <div class="qac-brand-text">
          <span class="qac-brand-zh">快捷功能中心</span>
          <span class="qac-brand-en">QUICK ACTIONS CONSOLE</span>
        </div>
      </div>

      <span class="qac-spacer" />

      <nav class="qac-seg" role="tablist" aria-label="工作区切换">
        <span class="qac-seg-thumb" :class="{ 'is-right': workspace === 'professions' }" aria-hidden="true" />
        <button
          type="button"
          class="qac-seg-btn"
          :class="{ 'is-on': workspace === 'actions' }"
          role="tab"
          :aria-selected="workspace === 'actions'"
          @click="switchWorkspace('actions')"
        >
          <SvgIcon icon="mdi:lightning-bolt-outline" class="qac-seg-icon" />
          功能库
          <span class="qac-seg-count">{{ actionCount }}</span>
        </button>
        <button
          type="button"
          class="qac-seg-btn"
          :class="{ 'is-on': workspace === 'professions' }"
          role="tab"
          :aria-selected="workspace === 'professions'"
          @click="switchWorkspace('professions')"
        >
          <SvgIcon icon="mdi:account-details-outline" class="qac-seg-icon" />
          职业体系
          <span class="qac-seg-count">{{ professionCount }}</span>
        </button>
      </nav>

      <span class="qac-spacer" />

      <div class="qac-overview" title="已完成引导（选择了职业）的用户数">
        <b>{{ onboardedCount }}</b>
        <span>人已选职业</span>
      </div>
    </header>

    <!-- ── 工作区面板：淡入淡出切换，双面板常驻保持各自编辑状态 ── -->
    <div class="qac-body">
      <Transition name="qac-fade">
        <div v-show="workspace === 'actions'" class="qac-pane">
          <ActionStudio @open-profession="onOpenProfession" @goto-professions="switchWorkspace('professions')" />
        </div>
      </Transition>
      <Transition name="qac-fade">
        <div v-show="workspace === 'professions'" class="qac-pane">
          <ProfessionStudio ref="professionStudioRef" @goto-actions="switchWorkspace('actions')" />
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.qac-root {
  --bg: #f5f7fb;

  --surface: rgba(255, 255, 255, 0.42);
  --surface-strong: rgba(255, 255, 255, 0.62);
  --surface-deep: rgba(255, 255, 255, 0.78);
  --highlight: rgba(255, 255, 255, 0.95);

  --border: rgba(30, 64, 175, 0.1);
  --border-glow: rgba(30, 64, 175, 0.25);

  --ink: #0f172a;
  --ink-soft: #334155;
  --ink-mute: #64748b;
  --ink-faint: #94a3b8;

  --c-blue: #1e40af;
  --c-blue-2: #2563eb;
  --c-sky: #0ea5e9;
  --c-cyan: #0891b2;
  --c-violet: #4f46e5;

  --aurora: linear-gradient(110deg, var(--c-blue) 0%, var(--c-blue-2) 35%, var(--c-sky) 70%, var(--c-cyan) 100%);

  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 16px -8px rgba(30, 64, 175, 0.12);
  --shadow-glow: 0 8px 32px -10px rgba(30, 64, 175, 0.45);

  --ease: cubic-bezier(0.32, 0.72, 0, 1);

  position: relative;
  display: flex;
  flex-direction: column;
  height: 100vh;
  max-width: 1240px;
  margin: 0 auto;
  padding: 14px 24px 24px;
  gap: 14px;
  background: transparent;
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, 'PingFang SC', 'Noto Sans SC', sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: var(--ink);
  overflow: hidden;
}

/* ─── aurora background ─── */
.aurora {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
}

.aurora-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
  will-change: transform;
}

.aurora-1 {
  width: 620px; height: 620px;
  top: -180px; right: -120px;
  background: radial-gradient(circle, var(--c-blue) 0%, transparent 65%);
  opacity: 0.3;
  animation: qac-drift-1 26s ease-in-out infinite;
}
.aurora-2 {
  width: 680px; height: 680px;
  bottom: -240px; left: -160px;
  background: radial-gradient(circle, var(--c-cyan) 0%, transparent 65%);
  opacity: 0.28;
  animation: qac-drift-2 32s ease-in-out infinite;
}
.aurora-3 {
  width: 440px; height: 440px;
  top: 38%; right: 18%;
  background: radial-gradient(circle, var(--c-sky) 0%, transparent 65%);
  opacity: 0.25;
  animation: qac-drift-3 28s ease-in-out infinite;
}
.aurora-4 {
  width: 380px; height: 380px;
  top: 15%; left: -40px;
  background: radial-gradient(circle, var(--c-violet) 0%, transparent 65%);
  opacity: 0.18;
  animation: qac-drift-1 30s ease-in-out infinite reverse;
}

.aurora-grain {
  position: absolute;
  inset: 0;
  background-image: radial-gradient(circle at 1px 1px, rgba(15, 23, 42, 0.05) 1px, transparent 0);
  background-size: 3px 3px;
  opacity: 0.35;
  mix-blend-mode: multiply;
}

@keyframes qac-drift-1 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50%      { transform: translate(60px, 40px) scale(1.08); }
}
@keyframes qac-drift-2 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50%      { transform: translate(-40px, -60px) scale(1.05); }
}
@keyframes qac-drift-3 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33%      { transform: translate(-50px, 30px) scale(0.96); }
  66%      { transform: translate(40px, -25px) scale(1.04); }
}

/* ─── 顶栏 ─── */
.qac-topbar {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
  padding: 10px 16px;
  border-radius: 16px;
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    var(--shadow-sm);
}

.qac-spacer {
  flex: 1;
}

/* 品牌 */
.qac-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.qac-brand-mark {
  position: relative;
  width: 38px; height: 38px;
  border-radius: 12px;
  background: var(--aurora);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 4px 14px -4px rgba(30, 64, 175, 0.55);
}

.qac-bm-glyph {
  position: relative;
  z-index: 1;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.18));
}

.qac-bm-halo {
  position: absolute;
  inset: -2px;
  border-radius: 14px;
  background: var(--aurora);
  filter: blur(12px);
  opacity: 0.5;
  z-index: 0;
  animation: qac-halo 3s ease-in-out infinite;
}

@keyframes qac-halo {
  0%, 100% { opacity: 0.4; transform: scale(0.95); }
  50%      { opacity: 0.7; transform: scale(1.1); }
}

.qac-brand-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1;
}

.qac-brand-zh {
  font-size: 16px;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--ink);
}

.qac-brand-en {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.14em;
  color: var(--ink-faint);
  text-transform: uppercase;
}

/* 工作区分段切换 */
.qac-seg {
  position: relative;
  display: flex;
  flex-shrink: 0;
  padding: 4px;
  border-radius: 14px;
  background: var(--surface-deep);
  border: 1px solid var(--border);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
}

.qac-seg-thumb {
  position: absolute;
  top: 4px;
  left: 4px;
  width: calc(50% - 4px);
  height: calc(100% - 8px);
  border-radius: 11px;
  background: var(--aurora);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 4px 14px -4px rgba(30, 64, 175, 0.5);
  transition: transform 0.32s var(--ease);
}

.qac-seg-thumb.is-right {
  transform: translateX(100%);
}

.qac-seg-btn {
  position: relative;
  z-index: 1;
  flex: 1 1 0;
  min-width: 128px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  height: 36px;
  padding: 0 14px;
  border: none;
  background: transparent;
  border-radius: 11px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.01em;
  color: var(--ink-mute);
  cursor: pointer;
  transition: color 0.24s ease;
  white-space: nowrap;
}

.qac-seg-btn:hover {
  color: var(--c-blue);
}

.qac-seg-btn.is-on {
  color: #fff;
}

.qac-seg-icon {
  font-size: 16px;
}

.qac-seg-count {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 7px;
  border-radius: 999px;
  background: rgba(30, 64, 175, 0.08);
  color: var(--c-blue);
  font-variant-numeric: tabular-nums;
  transition: all 0.24s ease;
}

.qac-seg-btn.is-on .qac-seg-count {
  background: rgba(255, 255, 255, 0.22);
  color: #fff;
}

/* 总览 */
.qac-overview {
  display: flex;
  align-items: baseline;
  gap: 5px;
  flex-shrink: 0;
  padding-right: 4px;
}

.qac-overview b {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--c-blue);
  font-variant-numeric: tabular-nums;
}

.qac-overview span {
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-faint);
}

/* ─── 工作区面板 ─── */
.qac-body {
  position: relative;
  z-index: 1;
  flex: 1;
  min-height: 0;
}

.qac-pane {
  position: absolute;
  inset: 0;
}

.qac-fade-enter-active,
.qac-fade-leave-active {
  transition: opacity 0.26s var(--ease), transform 0.26s var(--ease);
}

.qac-fade-enter-from {
  opacity: 0;
  transform: translateY(10px);
}

.qac-fade-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* ─── Responsive ─── */
@media (max-width: 900px) {
  .qac-root { padding: 10px 14px 14px; }
  .qac-overview { display: none; }
  .qac-brand-en { display: none; }
}

@media (max-width: 640px) {
  .qac-seg-btn { min-width: 0; }
  .qac-seg-count { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .aurora-orb, .qac-bm-halo { animation: none !important; }
  .qac-seg-thumb { transition: none !important; }
}
</style>
