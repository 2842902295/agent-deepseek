<script setup lang="ts">
import { computed } from 'vue';
import { useRouter } from 'vue-router';
import { brand } from '@/constants/brand';
import { useAuthStore } from '@/store/modules/auth';
import ModelSwitcher from './ModelSwitcher.vue';

interface Props {
  currentSessionTitle: string;
  messagesLength: number;
  sedimentingKb: boolean;
  distilling: boolean;
  hasSessionKey: boolean;
  sedimentMenuOpen: boolean;
  exportingImage: boolean;
  streaming: boolean;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  toggleSidebar: [];
  goToWorkbench: [];
  goToNian: [];
  sedimentSession: [];
  distill: [];
  toggleSedimentMenu: [];
  closeSedimentMenu: [];
  pickSediment: [kind: 'kb' | 'skill'];
  exportImage: [];
}>();

const router = useRouter();
const authStore = useAuthStore();

// 判断是否为管理员
const isAdmin = computed(() => {
  const roles = authStore.userInfo.roles || [];
  return roles.includes('R_SUPER') || roles.includes('R_ADMIN');
});

// 是否超管（模型切换仅 R_SUPER 可用，比 isAdmin 更严格，不含 R_ADMIN）
const isSuper = computed(() => (authStore.userInfo.roles || []).includes('R_SUPER'));

// v-click-outside 指令
const vClickOutside = {
  mounted(el: HTMLElement, binding: { value: () => void }) {
    (el as any).__clickOutsideHandler__ = (e: MouseEvent) => {
      if (!el.contains(e.target as Node)) binding.value();
    };
    document.addEventListener('click', (el as any).__clickOutsideHandler__);
  },
  unmounted(el: HTMLElement) {
    document.removeEventListener('click', (el as any).__clickOutsideHandler__);
  }
};
</script>

<template>
  <header class="qa-topbar">
    <button :title="'展开/收起'" class="topbar-toggle" @click="emit('toggleSidebar')">
      <span class="hamburger"/>
    </button>
    <button class="topbar-back" title="返回首页" @click="emit('goToWorkbench')">
      <svg class="tb-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5m7 7-7-7 7-7"/></svg>
      <span class="tb-text">首页</span>
    </button>
    <div class="topbar-meta">
      <span class="meta-title">{{ currentSessionTitle }}</span>
    </div>
    <button
      class="topbar-kb"
      title="打开知识库"
      @click="emit('goToNian')"
    >
      <svg class="td-icon-kb" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m16 6 4 14"/><path d="M12 6v14"/><path d="M8 8v12"/><path d="M4 4v16"/></svg>
      <span class="td-text-nian">知识库</span>
    </button>

    <!-- 管理员入口 -->
    <button
      v-if="isAdmin"
      class="topbar-admin"
      title="快捷功能管理"
      @click="router.push('/ai/quick-action-manage')"
    >
      <svg class="td-icon-admin" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
    </button>

    <!-- 全局模型切换（仅超管） -->
    <ModelSwitcher v-if="isSuper" />

    <!-- 导出分享图（桌面 / 手机均显示，手机端仅图标） -->
    <button
      v-if="messagesLength > 0"
      :disabled="exportingImage || streaming"
      class="topbar-distill topbar-export"
      :title="exportingImage ? '分享图生成中…' : streaming ? '请等待 AI 回复完成' : '把当前对话导出为一张图片'"
      @click="emit('exportImage')"
    >
      <span :class="{ 'is-spin': exportingImage }" class="td-icon td-icon-export">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></svg>
      </span>
      <span class="td-text">{{ exportingImage ? '生成中' : '导出图片' }}</span>
    </button>

    <!-- 桌面端：两个独立按钮 -->
    <button
      v-if="messagesLength >= 2"
      :disabled="sedimentingKb || !hasSessionKey"
      class="topbar-distill topbar-sediment-kb topbar-only-desktop"
      :title="sedimentingKb ? '正在整理…' : '把本次会话沉淀到库'"
      @click="emit('sedimentSession')"
    >
      <span class="td-icon">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/></svg>
      </span>
      <span class="td-text">{{ sedimentingKb ? '沉淀中' : '沉淀到库' }}</span>
    </button>
    <button
      v-if="messagesLength >= 2"
      :disabled="distilling || !hasSessionKey"
      class="topbar-distill topbar-distill-skill topbar-only-desktop"
      :title="distilling ? '凝练中…' : '把本次会话凝练为可复用技能'"
      @click="emit('distill')"
    >
      <span class="td-icon">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/></svg>
      </span>
      <span class="td-text">{{ distilling ? '凝练中' : '凝练为技能' }}</span>
    </button>

    <!-- 手机端：合并为一个"沉淀"按钮，点开后弹出二选一 -->
    <div
      v-if="messagesLength >= 2"
      class="topbar-sediment-mobile"
      v-click-outside="() => emit('closeSedimentMenu')"
    >
      <button
        :disabled="(sedimentingKb || distilling) || !hasSessionKey"
        class="topbar-distill topbar-sediment-trigger"
        :class="{ 'is-open': sedimentMenuOpen }"
        :title="sedimentingKb || distilling ? '沉淀中…' : '沉淀本次对话'"
        @click="emit('toggleSedimentMenu')"
      >
        <span class="td-text-keep">{{ sedimentingKb ? '沉淀中' : (distilling ? '凝练中' : '沉淀') }}</span>
        <svg class="td-caret" :class="{ 'td-caret--open': sedimentMenuOpen }" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>
      </button>
      <div v-if="sedimentMenuOpen" class="sediment-menu">
        <button
          :disabled="sedimentingKb || !hasSessionKey"
          class="sm-item"
          @click="emit('pickSediment', 'kb')"
        >
          <svg class="sm-mark" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/></svg>
          <span class="sm-body">
            <span class="sm-title">沉淀到库</span>
            <span class="sm-sub">把本次对话整理进知识库</span>
          </span>
        </button>
        <button
          :disabled="distilling || !hasSessionKey"
          class="sm-item"
          @click="emit('pickSediment', 'skill')"
        >
          <svg class="sm-mark" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/></svg>
          <span class="sm-body">
            <span class="sm-title">凝练为技能</span>
            <span class="sm-sub">提炼为可复用的 @ 技能</span>
          </span>
        </button>
      </div>
    </div>
  </header>
</template>

<style scoped>
/* ─── 浮动玻璃顶栏 ───────────────────────────────────────────── */
.qa-topbar {
  /* 顶栏整体提升层级：backdrop-filter 会形成层叠上下文，把内部下拉菜单
     （.ms-menu / .sediment-menu）困在顶栏内；顶栏自身若不定级，会被后续
     兄弟 .qa-stage（position:relative）及其内容（z-index ≤ 20）盖住。
     100 高于 stage 内容、低于全屏遮罩（distill 200 / md-preview 220）。 */
  position: relative;
  z-index: 100;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  margin: 12px 16px 0;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.42);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 16px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 8px 24px -8px rgba(30, 64, 175, 0.18);
}

/* ─── 展开/收起按钮 ────────────────────────────────────────────── */
.topbar-toggle {
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  cursor: pointer;
  font-size: 11px;
  color: var(--ink-2);
  border-radius: 9px;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 7px 9px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
}
.topbar-toggle:hover {
  border-color: rgba(30, 64, 175, 0.25);
  color: #1e40af;
  transform: translateY(-1px);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), 0 4px 12px -4px rgba(30,64,175,0.3);
}

.hamburger {
  display: inline-block;
  width: 14px;
  height: 1px;
  background: currentColor;
  position: relative;
}
.hamburger::before, .hamburger::after {
  content: '';
  position: absolute;
  left: 0;
  width: 14px;
  height: 1px;
  background: currentColor;
  transition: transform 0.2s;
}
.hamburger::before {
  top: -4px;
}
.hamburger::after {
  top: 4px;
}

/* ─── 返回首页按钮 ────────────────────────────────────────────── */
.topbar-back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  color: var(--ink-2);
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.005em;
  cursor: pointer;
  border-radius: 9px;
  transition: all 0.2s ease;
  text-transform: none;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
}
.topbar-back:hover {
  border-color: rgba(30, 64, 175, 0.25);
  color: #1e40af;
  transform: translateY(-1px);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), 0 4px 12px -4px rgba(30,64,175,0.3);
}

.tb-icon {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}

/* ─── Meta 标题区 ─────────────────────────────────────────────── */
.topbar-meta {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  overflow: hidden;
}
.meta-eyebrow {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--ink-3);
  text-transform: uppercase;
}
.meta-divider {
  color: var(--ink-4);
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 14px;
  font-style: normal;
}
.meta-title {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-weight: 600;
  font-size: 14.5px;
  color: var(--ink);
  font-style: normal;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  letter-spacing: -0.005em;
}

/* ─── 知识库按钮（蓝青渐变胶囊，全顶栏唯一有色按钮） ───────────── */
.topbar-kb {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 13px 7px 11px;
  margin-left: 4px;
  background: linear-gradient(110deg, #1e40af 0%, #0891b2 100%);
  border: 1px solid rgba(30, 64, 175, 0.35);
  color: #fff;
  cursor: pointer;
  border-radius: 10px;
  position: relative;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.28),
    0 1px 2px rgba(30, 64, 175, 0.14);
  transition: all 0.2s ease;
}
.topbar-kb:hover {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.28),
    0 8px 24px -6px rgba(30, 64, 175, 0.45);
}
.topbar-kb:active {
  transform: translateY(0);
}

.topbar-kb .td-icon-kb {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}

.topbar-kb .td-text-nian {
  font-family: var(--font-display);
  font-size: 13px;
  font-style: normal;
  font-weight: 600;
  line-height: 1;
  letter-spacing: 0.02em;
  color: #fff;
  text-transform: none;
}

.topbar-kb-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 16px;
  padding: 0 5px;
  background: linear-gradient(110deg, #1e40af 0%, #0891b2 100%);
  color: var(--paper);
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
  border-radius: 999px;
  margin-left: 2px;
  box-shadow: 0 1px 4px -1px rgba(30, 64, 175, 0.35);
}

/* ─── 管理员按钮 ──────────────────────────────────────────────── */
.topbar-admin {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.95), inset 0 0 0 1px rgba(255,255,255,0.4);
}
.topbar-admin:hover {
  border-color: rgba(30, 64, 175, 0.25);
  background: rgba(30, 64, 175, 0.08);
  color: #1e40af;
  transform: translateY(-1px);
}

.td-icon-admin {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

/* ─── 沉淀按钮（桌面端独立按钮） ──────────────────────────────── */
.topbar-distill {
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
  text-transform: none;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
}
.topbar-distill:hover:not(:disabled) {
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  color: #fff;
  border-color: transparent;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 4px 14px -2px rgba(30, 64, 175, 0.45);
  transform: translateY(-1px);
}
.topbar-distill:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.td-icon {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}

.td-icon-export {
  display: inline-block;
  font-size: 13px;
}
.td-icon-export.is-spin {
  animation: export-spin 1s linear infinite;
}
@keyframes export-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* ─── 沉淀按钮（手机端合并下拉） ──────────────────────────────── */
.topbar-sediment-mobile {
  display: none; /* 桌面隐藏，960 以下打开 */
  position: relative;
  margin-left: 8px;
}

.topbar-sediment-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.topbar-sediment-trigger.is-open {
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  color: #fff;
  border-color: transparent;
}

.td-caret {
  display: inline-block;
  flex-shrink: 0;
  transform: translateY(1px);
  transition: transform 0.18s ease;
}
.td-caret--open {
  transform: translateY(1px) rotate(180deg);
}

/* ─── 沉淀菜单（玻璃化下拉） ─────────────────────────────────── */
.sediment-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 30;
  min-width: 220px;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 14px;
  box-shadow: 0 16px 40px -8px rgba(15, 23, 42, 0.18);
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  animation: rise 0.18s ease-out;
}

.sm-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  background: transparent;
  border: none;
  border-radius: 10px;
  cursor: pointer;
  text-align: left;
  font-family: var(--font-body);
  color: var(--ink);
  transition: background 0.15s;
}
.sm-item:hover:not(:disabled) {
  background: rgba(30, 64, 175, 0.06);
}
.sm-item:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.sm-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--accent);
  flex-shrink: 0;
  margin-top: 1px;
}

.sm-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.sm-title {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--ink);
}

.sm-sub {
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.45;
}

/* ─── 响应式媒体查询 ───────────────────────────────────────────── */
@media (max-width: 960px) {
  .qa-topbar .topbar-back,
  .qa-topbar .topbar-toggle,
  .qa-topbar .topbar-distill,
  .qa-topbar .topbar-kb {
    border-radius: 999px;
  }

  .qa-topbar {
    padding: 10px 14px;
    gap: 8px;
  }

  .topbar-back .tb-text,
  .topbar-distill .td-text {
    display: none;
  }

  .topbar-only-desktop {
    display: none;
  }

  .topbar-sediment-mobile {
    display: inline-block;
    margin-left: 0;
  }

  .topbar-back,
  .topbar-distill {
    padding: 6px 9px;
    margin-left: 0;
  }

  .topbar-meta {
    gap: 6px;
    font-size: 12px;
  }

  .meta-eyebrow {
    display: none;
  }

  .meta-divider {
    display: none;
  }

  .meta-title {
    font-size: 14px;
  }
}

@media (max-width: 480px) {
  .qa-topbar {
    padding: 8px 10px;
  }

  /* 手机端隐藏返回首页按钮 */
  .topbar-back {
    display: none;
  }

  /* 手机端展开/收起按钮调大，避免误触 */
  .topbar-toggle {
    padding: 12px 14px;
  }

  .topbar-distill {
    padding: 6px 8px;
  }

  .topbar-kb .td-text-nian,
  .topbar-distill .td-text {
    display: none;
  }

  .topbar-kb {
    padding: 7px;
    margin-left: 4px;
    border-radius: 999px;
  }

  .topbar-meta {
    flex: 1;
    min-width: 0;
    overflow: hidden;
  }

  .meta-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(-8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
