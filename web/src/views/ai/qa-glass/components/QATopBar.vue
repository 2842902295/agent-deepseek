<script setup lang="ts">
import { computed } from 'vue';
import { brand } from '@/constants/brand';
import { useAuthStore } from '@/store/modules/auth';
import ModelSwitcher from './ModelSwitcher.vue';
import StdLibEntry from './StdLibEntry.vue';
import VectorLibEntry from './VectorLibEntry.vue';

interface Props {
  currentSessionTitle: string;
  messagesLength: number;
  sedimentingKb: boolean;
  distilling: boolean;
  hasSessionKey: boolean;
  sedimentMenuOpen: boolean;
  exportingImage: boolean;
  streaming: boolean;
  /** 侧栏是否收起：收起时顶栏左侧展示 展开 + 新建会话 两钮（原 topbar-toggle 已移除） */
  sidebarCollapsed: boolean;
}

const props = defineProps<Props>();

const emit = defineEmits<{
  toggleSidebar: [];
  newSession: [];
  goToNian: [];
  sedimentSession: [];
  distill: [];
  toggleSedimentMenu: [];
  closeSedimentMenu: [];
  pickSediment: [kind: 'kb' | 'skill'];
  exportImage: [];
}>();

// 知识库入口暂时隐藏（含「沉淀到库」按钮与手机端菜单中的同名项），恢复时改回 true 即可
const showKbEntry = false;

const authStore = useAuthStore();

// 是否超管（模型切换仅 R_SUPER 可用）
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
    <!--
 侧栏收起时：顶栏左侧只留「展开 + 新建会话」两钮（不带搜索；会话泡 + 加号图标），
         外层小间距成组，与顶栏其余元素的 12px gap 区分开
-->
    <span v-if="sidebarCollapsed" class="topbar-side-group">
      <button class="topbar-side-btn" title="展开侧栏" @click="emit('toggleSidebar')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
          <rect x="3" y="4" width="18" height="16" rx="3" />
          <path d="M9.5 4v16" />
        </svg>
      </button>
      <button class="topbar-side-btn" title="新建任务" @click="emit('newSession')">
        <!-- 圆润会话泡 + 居中加号（lucide message-circle-plus 风格，比方泡更现代） -->
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />
          <path d="M8 12h8" />
          <path d="M12 8v8" />
        </svg>
      </button>
    </span>
    <div class="topbar-meta">
      <span class="meta-title">{{ currentSessionTitle }}</span>
    </div>
    <button
      v-if="showKbEntry"
      class="topbar-kb"
      title="打开知识库"
      @click="emit('goToNian')"
    >
      <svg class="td-icon-kb" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m16 6 4 14" /><path d="M12 6v14" /><path d="M8 8v12" /><path d="M4 4v16" /></svg>
      <span class="td-text-nian">知识库</span>
    </button>

    <!-- 向量库管理（仅管理员可见，入口内部按 R_SUPER/R_ADMIN 门控） -->
    <VectorLibEntry />

    <!-- 标准库同步（仅管理员可见，入口内部按 R_SUPER/R_ADMIN 门控） -->
    <StdLibEntry />

    <!-- 全局模型切换（仅超管） -->
    <ModelSwitcher v-if="isSuper" />

    <!-- 导出分享图（桌面 / 手机均显示，手机端仅图标） -->
    <button
      v-if="messagesLength > 0"
      :disabled="exportingImage || streaming"
      class="topbar-distill topbar-export"
      :title="exportingImage ? '分享图生成中…' : streaming ? '请等待 AI 回复完成' : '把当前任务导出为一张图片'"
      @click="emit('exportImage')"
    >
      <span :class="{ 'is-spin': exportingImage }" class="td-icon td-icon-export">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="9" cy="9" r="2" /><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" /></svg>
      </span>
      <span class="td-text">{{ exportingImage ? '生成中' : '导出图片' }}</span>
    </button>

    <!-- 桌面端：两个独立按钮 -->
    <button
      v-if="showKbEntry && messagesLength >= 2"
      :disabled="sedimentingKb || !hasSessionKey"
      class="topbar-distill topbar-sediment-kb topbar-only-desktop"
      :title="sedimentingKb ? '正在整理…' : '把本次任务沉淀到库'"
      @click="emit('sedimentSession')"
    >
      <span class="td-icon">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1" /><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" /><path d="M10 12h4" /></svg>
      </span>
      <span class="td-text">{{ sedimentingKb ? '沉淀中' : '沉淀到库' }}</span>
    </button>
    <button
      v-if="messagesLength >= 2"
      :disabled="distilling || !hasSessionKey"
      class="topbar-distill topbar-distill-skill topbar-only-desktop"
      :title="distilling ? '凝练中…' : '把本次任务凝练为可复用技能'"
      @click="emit('distill')"
    >
      <span class="td-icon">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" /><path d="M20 3v4" /><path d="M22 5h-4" /><path d="M4 17v2" /><path d="M5 18H3" /></svg>
      </span>
      <span class="td-text">{{ distilling ? '凝练中' : '凝练为技能' }}</span>
    </button>

    <!-- 手机端：合并为一个"沉淀"按钮，点开后弹出二选一 -->
    <div
      v-if="messagesLength >= 2"
      v-click-outside="() => emit('closeSedimentMenu')"
      class="topbar-sediment-mobile"
    >
      <button
        :disabled="(sedimentingKb || distilling) || !hasSessionKey"
        class="topbar-distill topbar-sediment-trigger"
        :class="{ 'is-open': sedimentMenuOpen }"
        :title="sedimentingKb || distilling ? '沉淀中…' : '沉淀本次任务'"
        @click="emit('toggleSedimentMenu')"
      >
        <span class="td-text-keep">{{ sedimentingKb ? '沉淀中' : (distilling ? '凝练中' : '沉淀') }}</span>
        <svg class="td-caret" :class="{ 'td-caret--open': sedimentMenuOpen }" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6" /></svg>
      </button>
      <div v-if="sedimentMenuOpen" class="sediment-menu">
        <button
          v-if="showKbEntry"
          :disabled="sedimentingKb || !hasSessionKey"
          class="sm-item"
          @click="emit('pickSediment', 'kb')"
        >
          <svg class="sm-mark" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="5" rx="1" /><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8" /><path d="M10 12h4" /></svg>
          <span class="sm-body">
            <span class="sm-title">沉淀到库</span>
            <span class="sm-sub">把本次任务整理进知识库</span>
          </span>
        </button>
        <button
          :disabled="distilling || !hasSessionKey"
          class="sm-item"
          @click="emit('pickSediment', 'skill')"
        >
          <svg class="sm-mark" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" /><path d="M20 3v4" /><path d="M22 5h-4" /><path d="M4 17v2" /><path d="M5 18H3" /></svg>
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
  height: 56px;
  padding: 0 20px;
  margin: 0;
  flex-shrink: 0;
  background: transparent;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
  border: 1px solid transparent;
  /* 底部细分隔线（原由 index.vue 重复定义，2026-08-21 收归组件基线；
     ink 主题在 index.vue 覆写 border-bottom-color） */
  border-bottom-color: var(--rule, rgba(30, 64, 175, 0.1));
  border-radius: 0;
  box-shadow: none;
}

/* ─── 侧栏收起时顶栏左侧「展开 / 新建」钮组（与侧栏头部 side-collapse-btn 同款语言、同尺寸） ─── */
.topbar-side-group {
  display: inline-flex;
  align-items: center;
  gap: 4px; /* 组内紧凑，与顶栏其余元素的 12px gap 拉开层次 */
}

.topbar-side-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  padding: 0;
  border: none;
  border-radius: 9px;
  background: transparent;
  color: var(--ink-3);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.topbar-side-btn:hover {
  background: var(--fill-hover);
  color: var(--ink);
}

/* ─── Meta 标题区 ─────────────────────────────────────────────── */
.topbar-meta {
  flex: 1;
  /* 标题区是唯一可压缩项：min-width:0 允许缩到内容宽度以下（flex 默认 min-width:auto 会顶着不放，把两侧按钮挤变形） */
  min-width: 0;
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
  font-family: var(--font-body);
  font-size: 14px;
  font-style: normal;
}
.meta-title {
  font-family: var(--font-body);
  font-weight: 600;
  font-size: 14px;
  color: var(--ink);
  font-style: normal;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  letter-spacing: -0.005em;
}

/* ─── 知识库按钮（浅蓝 pill 语言，全顶栏唯一彩色） ───────────── */
.topbar-kb {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 13px 7px 11px;
  margin-left: 4px;
  background: var(--blue-bg);
  border: 1px solid transparent;
  color: var(--blue);
  cursor: pointer;
  border-radius: 999px;
  position: relative;
  box-shadow: none;
  transition: filter 0.15s ease;
  flex-shrink: 0;
  white-space: nowrap;
}
.topbar-kb:hover {
  filter: brightness(0.97);
}
.topbar-kb:active {
  filter: brightness(0.94);
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
  color: var(--blue);
  text-transform: none;
}

.topbar-kb-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 16px;
  padding: 0 5px;
  background: var(--blue);
  color: #fff;
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
  border-radius: 999px;
  margin-left: 2px;
  box-shadow: none;
}

/* ─── 沉淀按钮（桌面端独立按钮） ──────────────────────────────── */
.topbar-distill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  margin-left: 4px;
  background: transparent;
  border: 1px solid var(--card-border);
  color: var(--ink-2);
  font-family: var(--font-body);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.005em;
  cursor: pointer;
  border-radius: 999px;
  transition: all 0.16s ease;
  text-transform: none;
  box-shadow: none;
  flex-shrink: 0;
  white-space: nowrap;
}
.topbar-distill:hover:not(:disabled) {
  background: var(--grad-brand);
  color: var(--on-primary);
  border-color: transparent;
  box-shadow: none;
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
  flex-shrink: 0;
}

.topbar-sediment-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.topbar-sediment-trigger.is-open {
  background: var(--grad-brand);
  color: var(--on-primary);
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
  background: var(--popup-bg);
  backdrop-filter: var(--popup-blur);
  -webkit-backdrop-filter: var(--popup-blur);
  border: 1px solid var(--popup-border);
  border-radius: 14px;
  box-shadow: var(--popup-shadow);
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
  background: var(--fill-hover);
}
.sm-item:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.sm-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--ink-2);
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
  .qa-topbar .topbar-side-btn,
  .qa-topbar .topbar-distill,
  .qa-topbar .topbar-kb {
    border-radius: 999px;
  }

  .qa-topbar {
    padding: 10px 14px;
    gap: 8px;
  }

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

  /* 手机端展开/收起等左侧按钮调大，避免误触 */
  .topbar-side-btn {
    width: 40px;
    height: 40px;
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
