<script setup lang="ts">
import { computed, ref } from 'vue';
import { NSwitch } from 'naive-ui';
import type { AgentConnector } from '@/service/api';
import { fetchBatchAgentConnectorPrefs } from '@/service/api';
import { customIconHtml } from './skill/skill-icon';

/**
 * 在用连接器指示（composer-actions 内）：图标叠加堆展示（圆片只含启用态），
 * 点击弹出层可直接启用/禁用（禁用=agent 不加载其工具；禁用态不进圆片堆，但在弹层里可重新启用）。
 * 全部禁用时圆片堆塌成一个纯插头小图标入口，点击同样打开弹层。
 */
const props = withDefaults(defineProps<{
  /** 我已添加的连接器（含禁用态，弹层里可重新启用） */
  connectors: AgentConnector[];
  /** 维度标识：connector=连接器（默认） / dataset=数据集；控制弹层文案与图标 */
  variant?: 'connector' | 'dataset';
}>(), {
  variant: 'connector',
});

const emit = defineEmits<{
  /** 启停变更后通知外层刷新（agent 工具集随签名自动重建） */
  changed: [];
  /** 「管理连接器」→ 打开技能面板连接器页（商店） */
  manage: [];
}>();

const open = ref(false);
const MAX = 5;
const enabled = computed(() => props.connectors.filter(c => c.userEnabled));
const visible = computed(() => enabled.value.slice(0, MAX));
const extra = computed(() => Math.max(0, enabled.value.length - MAX));

const label = computed(() => (props.variant === 'dataset' ? '数据集' : '连接器'));

const triggerTitle = computed(() => {
  const lb = label.value;
  if (!enabled.value.length) return `${lb}均已禁用（${props.connectors.length} 个，点击管理）`;
  return `在用的${lb}（${enabled.value.length} 个启用 / 共 ${props.connectors.length} 个，点击管理）`;
});

function iconHtmlOf(c: AgentConnector): string {
  return customIconHtml(c.icon);
}

async function toggle(c: AgentConnector) {
  const { data, error } = await fetchBatchAgentConnectorPrefs([c.connectorKey], { isEnabled: !c.userEnabled });
  if (!error && data) {
    c.userEnabled = !c.userEnabled;
    emit('changed');
  } else {
    window.$message?.error('切换失败');
  }
}
</script>

<template>
  <div v-if="connectors.length" class="cstack">
    <button class="cstack-trigger" :title="triggerTitle" @click="open = !open">
      <span class="cstack-chips">
        <template v-if="visible.length">
          <span
            v-for="c in visible"
            :key="c.connectorKey"
            class="cstack-chip"
          >
            <span v-if="iconHtmlOf(c)" v-html="iconHtmlOf(c)"></span>
            <svg v-else-if="variant === 'dataset'" width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <ellipse cx="8" cy="4" rx="5.5" ry="2.1" />
              <path d="M2.5 4v8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1V4" />
              <path d="M2.5 8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1" />
            </svg>
            <svg v-else width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M6 2v3M10 2v3" />
              <path d="M4 5h8v2.5a4 4 0 0 1-4 4 4 4 0 0 1-4-4V5z" />
              <path d="M8 11.5V14" />
            </svg>
          </span>
          <span v-if="extra" class="cstack-more">+{{ extra }}</span>
        </template>
        <!-- 无启用项：按维度显示对应图标入口 -->
        <svg v-else-if="variant === 'dataset'" width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <ellipse cx="8" cy="4" rx="5.5" ry="2.1" />
              <path d="M2.5 4v8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1V4" />
              <path d="M2.5 8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1" />
            </svg>
        <svg v-else width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M6 2v3M10 2v3" />
              <path d="M4 5h8v2.5a4 4 0 0 1-4 4 4 4 0 0 1-4-4V5z" />
              <path d="M8 11.5V14" />
            </svg>
      </span>
    </button>

    <template v-if="open">
      <div class="cstack-backdrop" @click="open = false" />
      <div class="cstack-pop">
        <div class="cstack-pop-head">
          <span class="cstack-pop-title">{{ label }}</span>
          <span class="cstack-pop-hint">{{ variant === 'dataset' ? '启用后为任务注入相关知识数据' : '启用后任务中自动加载其工具' }}</span>
        </div>
        <div class="cstack-pop-list">
          <div v-for="c in connectors" :key="c.connectorKey" class="cstack-item">
            <span class="cstack-item-icon">
              <span v-if="iconHtmlOf(c)" v-html="iconHtmlOf(c)"></span>
              <svg v-else-if="variant === 'dataset'" width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <ellipse cx="8" cy="4" rx="5.5" ry="2.1" />
              <path d="M2.5 4v8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1V4" />
              <path d="M2.5 8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1" />
            </svg>
              <svg v-else width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M6 2v3M10 2v3" />
              <path d="M4 5h8v2.5a4 4 0 0 1-4 4 4 4 0 0 1-4-4V5z" />
              <path d="M8 11.5V14" />
            </svg>
            </span>
            <span class="cstack-item-name" :title="c.name">{{ c.name }}</span>
            <NSwitch :value="c.userEnabled" size="small" @update:value="toggle(c)" />
          </div>
        </div>
        <button
          class="cstack-manage"
          @click="
            open = false;
            emit('manage');
          "
        >
          管理{{ label }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cstack {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.cstack-trigger {
  display: inline-flex;
  align-items: center;
  border: none;
  background: transparent;
  padding: 2px;
  cursor: pointer;
  border-radius: 20px;
  color: var(--ink-3);
  transition: background 0.15s;
}
.cstack-trigger:hover {
  background: var(--fill-hover);
}
/* 图标叠加堆：负外边距叠放，hover 时轻微散开 */
.cstack-chips {
  display: inline-flex;
  align-items: center;
  padding-right: 4px;
}
.cstack-chip {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--surface-strong);
  border: 1.5px solid var(--paper, #f5f7fb);
  box-shadow: 0 0 0 1px var(--border);
  color: var(--accent, #1e40af);
  overflow: hidden;
  transition: transform 0.16s;
}
.cstack-chip + .cstack-chip {
  margin-left: -8px;
}
.cstack-trigger:hover .cstack-chip + .cstack-chip {
  transform: translateX(2px);
}
.cstack-chip :deep(svg) {
  width: 13px;
  height: 13px;
}
.cstack-chip :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.cstack-more {
  margin-left: -6px;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 24px;
  padding: 0 7px;
  border-radius: 12px;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  color: var(--ink-3);
  background: var(--fill-hover);
  border: 1px solid var(--border);
}

/* ── 弹层：向上展开，结构/令牌照 skill-popup ── */
.cstack-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1499;
}
.cstack-pop {
  position: absolute;
  bottom: calc(100% + 10px);
  left: 0;
  z-index: 1500;
  width: 280px;
  max-height: 340px;
  display: flex;
  flex-direction: column;
  background: var(--surface-strong);
  border: 1px solid var(--border-strong);
  border-radius: 12px;
  box-shadow: var(--shadow-md);
  backdrop-filter: blur(14px);
  overflow: hidden;
}
.cstack-pop-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 10px 13px 8px;
  border-bottom: 1px solid var(--line-hair);
}
.cstack-pop-title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink);
}
.cstack-pop-hint {
  font-size: 10.5px;
  color: var(--ink-4);
}
.cstack-pop-list {
  flex: 1;
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding: 4px 6px;
}
.cstack-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 8px;
  border-radius: 9px;
  transition: background 0.12s;
}
.cstack-item:hover {
  background: var(--fill-hover);
}
.cstack-item-icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 7px;
  background: var(--fill-hover);
  color: var(--accent, #1e40af);
  overflow: hidden;
}
.cstack-item-icon :deep(svg) {
  width: 13px;
  height: 13px;
}
.cstack-item-icon :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.cstack-item-name {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cstack-manage {
  flex-shrink: 0;
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-3);
  background: transparent;
  border: none;
  border-top: 1px solid var(--line-hair);
  padding: 8px;
  cursor: pointer;
  transition: color 0.12s, background 0.12s;
}
.cstack-manage:hover {
  color: var(--accent);
  background: var(--fill-hover);
}
</style>
