<script setup lang="ts">
import { computed, ref } from 'vue';
import type { ProcessStep } from '@/service/api/ai';
import { argsSummary, argsToParams, parseResultContent, resultSummary } from '../modules/tool-format';

/**
 * 过程时间线（dsh 三段式改造：过程时间线 + 结果 两段式）
 *
 * 渲染后端 process_json / SSE process 事件累积的条目序列：
 * reasoning 思考 / text 叙述 / tool_call + tool_result 工具卡 / todo 清单 / compaction 压缩。
 * 折叠态为一行摘要（「过程 · N 次调用 · …」），展开态为带竖线节点的时间线。
 *
 * 折叠由父组件通过 v-model:collapsed 控制：
 * - 流式中强制展开（live=true 时摘要行仍可点，但父组件会在 done 时自动收起）
 * - aborted/error 回合默认展开
 */
const props = defineProps<{
  items: ProcessStep[];
  /** 流式进行中 */
  live?: boolean;
  /** 折叠态（v-model） */
  collapsed?: boolean;
  /** 回合时长（毫秒，父组件记录；缺省不显示） */
  durationMs?: number | null;
  /**
   * 当前「打开中」的 text 条目 id（D2 无跳动设计）。
   * 打开中的文本正由结果区流式渲染，时间线不重复展示；
   * 直到 tool_call 定性为叙述（父组件清空 openTextId）才进入时间线。
   */
  openTextId?: string | null;
}>();

const emit = defineEmits<{
  (e: 'update:collapsed', v: boolean): void;
}>();

const innerCollapsed = computed({
  get: () => props.collapsed ?? false,
  set: v => emit('update:collapsed', v)
});

// 工具卡展开态（条目对象会被原位替换，按 id 记录）
const expanded = ref<Record<string, boolean>>({});

function toggleItem(id: string) {
  expanded.value[id] = !expanded.value[id];
}

/**
 * 时间线可见条目（D2 无跳动设计）：
 * 打开中的 text 条目正由结果区流式渲染（父组件按 openTextId 驱动 msg.content），
 * 时间线不重复展示它；定性后才可见——
 * tool_call 到达 → 父组件清空 openTextId，叙述条目就地出现在时间线尾；
 * done 到达 → 父组件按 promoted 从 msg.process 剔除答案条目，时间线自然不再含它。
 */
const visibleItems = computed(() => {
  const openId = props.openTextId;
  // 问卷条目（kind=questionnaire）由父组件按正文占位符在结果区渲染卡片，时间线不展示
  return props.items.filter(i => i.kind !== 'questionnaire' && i.id !== openId);
});

const stats = computed(() => {
  const calls = visibleItems.value.filter(i => i.kind === 'tool_call');
  return {
    toolCount: calls.length,
    subagentCount: calls.filter(i => i.is_subagent).length,
    textCount: visibleItems.value.filter(i => i.kind === 'text').length,
    hasReasoning: visibleItems.value.some(i => i.kind === 'reasoning'),
    hasTodo: visibleItems.value.some(i => i.kind === 'todo')
  };
});

function formatDuration(ms: number): string {
  const s = Math.max(1, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m${r}s` : `${m}m`;
}

const summaryText = computed(() => {
  const parts: string[] = [];
  if (stats.value.toolCount) {
    parts.push(`${stats.value.toolCount} 次调用`);
    if (stats.value.subagentCount) parts.push(`${stats.value.subagentCount} 子代理`);
  }
  if (stats.value.textCount) parts.push(`${stats.value.textCount} 段叙述`);
  if (stats.value.hasReasoning) parts.push('含思考');
  if (props.durationMs && props.durationMs > 0) parts.push(formatDuration(props.durationMs));
  return parts.join(' · ');
});

/** 子代理委派壳：标题取 args.description（截断） */
function subagentDesc(item: ProcessStep): string {
  const desc = item.args?.description;
  if (typeof desc !== 'string' || !desc) return '';
  return desc.length > 60 ? `${desc.slice(0, 60)}…` : desc;
}

function todoIcon(status: string): string {
  if (status === 'completed') return '✓';
  if (status === 'in_progress') return '◐';
  return '○';
}
</script>

<template>
  <div class="ptl">
    <div class="ptl-box">
      <!-- 摘要行（折叠态唯一可见部分） -->
      <div
        class="ptl-summary"
        role="button"
        tabindex="0"
        @click="innerCollapsed = !innerCollapsed"
        @keydown.enter.prevent="innerCollapsed = !innerCollapsed"
      >
        <span class="ptl-icon" :class="{ live }">⌬</span>
        <span class="ptl-label">{{ innerCollapsed ? '过程' : '过程时间线' }}</span>
        <span v-if="live" class="ptl-live">进行中</span>
        <span v-if="summaryText" class="ptl-stats">{{ summaryText }}</span>
        <span class="ptl-toggle">{{ innerCollapsed ? '展开' : '收起' }}<i>{{ innerCollapsed ? '↓' : '↑' }}</i></span>
      </div>

      <!-- 时间线主体 -->
      <ol v-if="!innerCollapsed" class="ptl-list">
        <li v-for="item in visibleItems" :key="item.id" :class="[`ptl-${item.kind}`, { 'ptl-sub': item.in_subagent }]" class="ptl-item">
          <span class="ptl-node" />

          <!-- reasoning：淡色斜体纯文本（不走 marked） -->
          <template v-if="item.kind === 'reasoning'">
            <div class="ptl-body">
              <div class="ptl-head-row">
                <span class="ptl-tag tag-reasoning">思考</span>
              </div>
              <div class="ptl-reasoning">{{ item.content }}</div>
            </div>
          </template>

          <!-- text：叙述（被后续工具调用定性为中间话语，留时间线不进答案） -->
          <template v-else-if="item.kind === 'text'">
            <div class="ptl-body">
              <div class="ptl-head-row">
                <span class="ptl-tag tag-text">叙述</span>
              </div>
              <div class="ptl-narration">{{ item.content }}</div>
            </div>
          </template>

          <!-- tool_call：工具调用卡 -->
          <template v-else-if="item.kind === 'tool_call'">
            <div class="ptl-body">
              <div class="ptl-head-row" @click="toggleItem(item.id)">
                <span class="ptl-tag tag-call">调用</span>
                <span v-if="item.is_subagent && !item.in_subagent" class="ptl-sub-badge">子代理</span>
                <span v-else-if="item.in_subagent" class="ptl-sub-badge ptl-sub-badge--child">子代理内</span>
                <span class="ptl-tool">{{ item.tool_display || item.tool }}</span>
                <span class="ptl-arrow">{{ expanded[item.id] ? '▴' : '▾' }}</span>
              </div>
              <div v-if="!expanded[item.id]" class="ptl-summary-line">
                {{ item.is_subagent && subagentDesc(item) ? subagentDesc(item) : argsSummary(item.args || {}) }}
              </div>
              <div v-else class="ptl-detail ptl-detail-call">
                <template v-if="argsToParams(item.args || {}).length">
                  <div v-for="p in argsToParams(item.args || {})" :key="p.key" class="ptl-param-row">
                    <span class="ptl-param-key">{{ p.key }}</span>
                    <span class="ptl-param-val">{{ p.value }}</span>
                  </div>
                </template>
                <div v-else class="ptl-plain-text">(无参数)</div>
              </div>
            </div>
          </template>

          <!-- tool_result：工具返回卡（is_error 红描边） -->
          <template v-else-if="item.kind === 'tool_result'">
            <div class="ptl-body">
              <div class="ptl-head-row" @click="toggleItem(item.id)">
                <span class="ptl-tag tag-result">{{ item.is_error ? '失败' : '返回' }}</span>
                <span v-if="item.in_subagent" class="ptl-sub-badge ptl-sub-badge--child">子代理内</span>
                <span class="ptl-tool">{{ item.tool_display || item.tool }}</span>
                <span class="ptl-arrow">{{ expanded[item.id] ? '▴' : '▾' }}</span>
              </div>
              <div v-if="!expanded[item.id]" class="ptl-summary-line">{{ resultSummary(item.content ?? '') }}</div>
              <div v-else class="ptl-detail" :class="item.is_error ? 'ptl-detail-error' : 'ptl-detail-result'">
                <template v-if="parseResultContent(item.content || '').kind === 'kv'">
                  <div
                    v-for="p in (parseResultContent(item.content || '') as any).pairs"
                    :key="p.key"
                    class="ptl-param-row ptl-result-row"
                  >
                    <span class="ptl-param-key ptl-result-key">{{ p.key }}</span>
                    <span class="ptl-param-val">{{ p.value }}</span>
                  </div>
                </template>
                <template v-else-if="parseResultContent(item.content || '').kind === 'list'">
                  <div
                    v-for="li in (parseResultContent(item.content || '') as any).items"
                    :key="li.label"
                    class="ptl-list-item"
                  >
                    <span class="ptl-list-seq">{{ li.label }}</span>
                    <span class="ptl-list-line">{{ li.line }}</span>
                  </div>
                </template>
                <div v-else class="ptl-plain-text">{{ (parseResultContent(item.content || '') as any).value }}</div>
              </div>
            </div>
          </template>

          <!-- todo：任务清单（整快照原位替换） -->
          <template v-else-if="item.kind === 'todo'">
            <div class="ptl-body">
              <div class="ptl-head-row">
                <span class="ptl-tag tag-todo">清单</span>
              </div>
              <div class="ptl-todos">
                <div
                  v-for="(t, ti) in item.todos || []"
                  :key="ti"
                  class="ptl-todo-row"
                  :class="`todo-${t.status}`"
                >
                  <span class="ptl-todo-icon">{{ todoIcon(t.status) }}</span>
                  <span class="ptl-todo-text">{{ t.content }}</span>
                </div>
              </div>
            </div>
          </template>

          <!-- compaction：上下文压缩一行灰字 -->
          <template v-else-if="item.kind === 'compaction'">
            <div class="ptl-body">
              <div class="ptl-compaction">上下文压缩（历史过长，运行时自动整理）</div>
            </div>
          </template>
        </li>
      </ol>
    </div>
  </div>
</template>

<style scoped>
.ptl {
  margin: 10px 0 6px;
}

.ptl-box {
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.42);
  backdrop-filter: blur(20px) saturate(180%);
  overflow: hidden;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
}

.ptl-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  cursor: pointer;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.06em;
  color: var(--ink-3);
  user-select: none;
  transition: all 0.15s;
  background: linear-gradient(90deg, var(--accent-soft) 0%, transparent 70%);
}

.ptl-summary:hover {
  color: var(--ink);
  background: linear-gradient(90deg, rgba(30, 64, 175, 0.1) 0%, transparent 70%);
}

.ptl-icon {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1.5px solid var(--accent);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 9px;
  color: var(--accent);
  flex-shrink: 0;
  background: var(--accent-soft);
}

.ptl-icon.live {
  animation: ptl-pulse 1.6s ease-in-out infinite;
}

@keyframes ptl-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(30, 64, 175, 0.25);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(30, 64, 175, 0.08);
  }
}

.ptl-label {
  font-weight: 600;
}

.ptl-live {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--accent);
  background: var(--accent-soft);
  border: 1px solid rgba(30, 64, 175, 0.18);
  padding: 1px 7px;
  border-radius: 99px;
}

.ptl-stats {
  flex: 1;
  font-size: 10px;
  color: var(--ink-4);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ptl-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--ink-4);
  font-size: 9px;
  letter-spacing: 0.1em;
  flex-shrink: 0;
}

.ptl-toggle i {
  font-style: normal;
  display: inline-block;
}

.ptl-list {
  list-style: none;
  padding: 6px 0;
  margin: 0;
  position: relative;
  border-top: 1px solid var(--rule);
}

.ptl-list::before {
  content: '';
  position: absolute;
  left: 32px;
  top: 18px;
  bottom: 18px;
  width: 1px;
  background: repeating-linear-gradient(to bottom, var(--rule) 0 5px, transparent 5px 9px);
}

.ptl-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 7px 14px 7px 18px;
  transition: background 0.1s;
  position: relative;
}

.ptl-item:hover {
  background: rgba(30, 64, 175, 0.025);
}

.ptl-node {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 4px;
  position: relative;
  z-index: 1;
  background: var(--ink-4);
  box-shadow: 0 0 0 3px rgba(148, 163, 184, 0.12);
}

.ptl-tool_call .ptl-node {
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.ptl-tool_result .ptl-node {
  background: #2a9d8f;
  box-shadow: 0 0 0 3px rgba(42, 157, 143, 0.12);
}

.ptl-reasoning .ptl-node,
.ptl-item.ptl-reasoning > .ptl-node {
  background: #8e7cc3;
  box-shadow: 0 0 0 3px rgba(142, 124, 195, 0.12);
}

.ptl-todo .ptl-node {
  background: #e9c46a;
  box-shadow: 0 0 0 3px rgba(233, 196, 106, 0.15);
}

.ptl-body {
  flex: 1;
  min-width: 0;
}

.ptl-head-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-mono);
  margin-bottom: 3px;
}

.ptl-tool_call .ptl-head-row,
.ptl-tool_result .ptl-head-row {
  cursor: pointer;
}

.ptl-tag {
  font-size: 9px;
  font-weight: 700;
  padding: 1px 7px;
  letter-spacing: 0.1em;
  border-radius: 99px;
  flex-shrink: 0;
}

.tag-call {
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid rgba(30, 64, 175, 0.18);
}

.tag-result {
  background: rgba(42, 157, 143, 0.1);
  color: #2a9d8f;
  border: 1px solid rgba(42, 157, 143, 0.2);
}

.tag-reasoning {
  background: rgba(142, 124, 195, 0.1);
  color: #7a66ad;
  border: 1px solid rgba(142, 124, 195, 0.25);
}

.tag-text {
  background: rgba(148, 163, 184, 0.12);
  color: var(--ink-3);
  border: 1px solid rgba(148, 163, 184, 0.25);
}

.tag-todo {
  background: rgba(233, 196, 106, 0.14);
  color: #a07d2a;
  border: 1px solid rgba(233, 196, 106, 0.35);
}

.ptl-sub-badge {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 1px 7px;
  border-radius: 99px;
  color: #fff;
  background: var(--accent);
  flex-shrink: 0;
}

/* 子代理内部条目：更浅的徽标色，区分「委派动作」与「子代理内部的工具」 */
.ptl-sub-badge--child {
  background: #64748b;
}

/* 子代理子会话条目：整体缩进，表达「嵌套在委派之内」的层级 */
.ptl-item.ptl-sub {
  margin-left: 26px;
}

.ptl-item.ptl-sub .ptl-node {
  width: 8px;
  height: 8px;
  margin-top: 5px;
  background: #64748b;
  box-shadow: 0 0 0 3px rgba(100, 116, 139, 0.12);
}

.ptl-tool {
  flex: 1;
  color: var(--ink);
  font-weight: 600;
  font-size: 11.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ptl-arrow {
  color: var(--ink-4);
  font-size: 9px;
  flex-shrink: 0;
}

.ptl-summary-line {
  font-family: var(--font-body);
  font-size: 11.5px;
  color: var(--ink-3);
  line-height: 1.5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ptl-detail {
  margin: 6px 0 2px;
  border: 1px solid var(--rule-soft);
  border-left: 2px solid var(--accent);
  border-radius: 10px;
  overflow: hidden;
}

.ptl-detail-result {
  border-left-color: #2a9d8f;
}

.ptl-detail-error {
  border-color: rgba(220, 38, 38, 0.35);
  border-left-color: #dc2626;
}

.ptl-param-row {
  display: grid;
  grid-template-columns: minmax(80px, auto) 1fr;
  align-items: baseline;
  gap: 0;
  border-bottom: 1px solid var(--rule-soft);
}

.ptl-param-row:last-child {
  border-bottom: none;
}

.ptl-param-key {
  padding: 6px 10px;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  color: var(--accent);
  letter-spacing: 0.04em;
  background: var(--accent-soft);
  white-space: nowrap;
  border-right: 1px solid var(--rule-soft);
  align-self: stretch;
  display: flex;
  align-items: flex-start;
  padding-top: 8px;
}

.ptl-param-val {
  padding: 6px 12px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.6;
  max-height: 200px;
  overflow-y: auto;
  background: var(--paper);
}

.ptl-result-row .ptl-result-key {
  background: rgba(42, 157, 143, 0.08);
  color: #2a9d8f;
}

.ptl-list-item {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 5px 12px;
  border-bottom: 1px solid var(--rule-soft);
  background: var(--paper);
}

.ptl-list-item:last-child {
  border-bottom: none;
}

.ptl-list-seq {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  color: var(--ink-4);
  min-width: 16px;
  flex-shrink: 0;
}

.ptl-list-line {
  font-family: var(--font-body);
  font-size: 11px;
  color: var(--ink-2);
  line-height: 1.5;
  word-break: break-all;
}

.ptl-plain-text {
  padding: 8px 14px;
  font-family: var(--font-body);
  font-size: 11.5px;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.65;
  max-height: 200px;
  overflow-y: auto;
  background: var(--paper);
}

/* reasoning：淡色斜体纯文本 */
.ptl-reasoning {
  font-family: var(--font-body);
  font-size: 11.5px;
  font-style: italic;
  color: var(--ink-4);
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
}

/* text：叙述，左竖线 */
.ptl-narration {
  font-family: var(--font-body);
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  border-left: 2px solid var(--rule);
  padding: 2px 0 2px 10px;
  margin: 2px 0;
}

/* todo 清单 */
.ptl-todos {
  margin: 4px 0 2px;
  border: 1px solid var(--rule-soft);
  border-left: 2px solid #e9c46a;
  border-radius: 10px;
  background: var(--paper);
  overflow: hidden;
}

.ptl-todo-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 5px 12px;
  border-bottom: 1px solid var(--rule-soft);
  font-size: 11.5px;
  line-height: 1.5;
}

.ptl-todo-row:last-child {
  border-bottom: none;
}

.ptl-todo-icon {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--ink-4);
}

.todo-completed .ptl-todo-icon {
  color: #2a9d8f;
}

.todo-in_progress .ptl-todo-icon {
  color: var(--accent);
}

.todo-completed .ptl-todo-text {
  color: var(--ink-4);
  text-decoration: line-through;
  text-decoration-color: rgba(148, 163, 184, 0.5);
}

.ptl-todo-text {
  color: var(--ink-2);
  word-break: break-word;
}

/* compaction */
.ptl-compaction {
  font-size: 10.5px;
  color: var(--ink-4);
  letter-spacing: 0.04em;
  padding: 2px 0;
}
</style>
