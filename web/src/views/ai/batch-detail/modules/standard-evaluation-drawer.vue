<script setup lang="ts">
import { ref, watch } from 'vue';
import { NDrawer, NDrawerContent, NButton, NEmpty, NSpin, NAlert, NDivider, NTag, NScrollbar } from 'naive-ui';
import { marked } from 'marked';
import { fetchStandardEvaluationStream, type StandardEvaluationEvent } from '@/service/api';

const props = defineProps<{
  show: boolean;
  standardNo: string;
}>();

const emit = defineEmits<{
  'update:show': [value: boolean];
}>();

// ── 状态 ──────────────────────────────────────────────────────────────────────

type Step = {
  id: number;
  type: 'tool_call' | 'tool_result' | 'thinking' | 'conclusion';
  icon: string;
  label: string;
  content: string;
};

const running = ref(false);
const steps = ref<Step[]>([]);
const conclusion = ref('');
const conclusionHtml = ref('');
const errorMsg = ref('');
const totalSteps = ref(0);
const stepsExpanded = ref(false);
const copySuccess = ref(false);
let abortController: AbortController | null = null;

function copyStepsText() {
  const text = steps.value
    .map(s => `[${s.label}]\n${s.content}`)
    .join('\n\n---\n\n');
  navigator.clipboard.writeText(text).then(() => {
    copySuccess.value = true;
    setTimeout(() => { copySuccess.value = false; }, 2000);
  });
}

marked.setOptions({ breaks: true });

// ── 核心逻辑 ──────────────────────────────────────────────────────────────────

function reset() {
  steps.value = [];
  conclusion.value = '';
  conclusionHtml.value = '';
  errorMsg.value = '';
  totalSteps.value = 0;
}

async function startEvaluation() {
  if (running.value) {
    abortController?.abort();
    running.value = false;
    return;
  }

  reset();
  running.value = true;
  abortController = new AbortController();

  try {
    await fetchStandardEvaluationStream(
      props.standardNo,
      (event: StandardEvaluationEvent) => {
        if (event.type === 'tool_call') {
          const argsText = Object.keys(event.args).length
            ? JSON.stringify(event.args, null, 2)
            : '';
          steps.value = [
            ...steps.value,
            { id: event.step, type: 'tool_call', icon: '🔧', label: `调用: ${event.tool}`, content: argsText }
          ];
        } else if (event.type === 'tool_result') {
          steps.value = [
            ...steps.value,
            { id: event.step, type: 'tool_result', icon: '📋', label: `结果: ${event.tool}`, content: event.content }
          ];
        } else if (event.type === 'thinking') {
          steps.value = [
            ...steps.value,
            { id: event.step, type: 'thinking', icon: '💭', label: '思考', content: event.content }
          ];
        } else if (event.type === 'conclusion') {
          conclusion.value = event.content;
          conclusionHtml.value = marked.parse(event.content) as string;
        } else if (event.type === 'done') {
          totalSteps.value = event.steps;
        } else if (event.type === 'error') {
          errorMsg.value = event.message;
        }
      },
      abortController.signal
    );
  } catch (err: any) {
    if (err?.name !== 'AbortError') {
      errorMsg.value = err?.message || '评估请求失败';
    }
  } finally {
    running.value = false;
  }
}

watch(
  () => props.show,
  show => {
    if (!show && running.value) {
      abortController?.abort();
      running.value = false;
    }
  }
);
</script>

<template>
  <NDrawer
    :show="props.show"
    width="100%"
    placement="right"
    @update:show="emit('update:show', $event)"
  >
    <NDrawerContent :title="`整合评估 · ${props.standardNo}`" closable>

      <!-- 操作栏 -->
      <div class="mb-4 flex items-center gap-3">
        <NButton
          :type="running ? 'default' : 'primary'"
          :loading="running"
          size="small"
          @click="startEvaluation"
        >
          {{ running ? '停止' : conclusion ? '重新评估' : '开始评估' }}
        </NButton>
        <div v-if="running" class="flex items-center gap-2 text-xs text-blue-500">
          <NSpin :size="14" />
          <span>Agent 分析中，已完成 {{ steps.length }} 步…</span>
        </div>
        <span v-else-if="totalSteps > 0" class="text-xs text-gray-400">
          共 {{ totalSteps }} 步完成
        </span>
      </div>

      <!-- 错误提示 -->
      <NAlert
        v-if="errorMsg"
        type="error"
        :title="errorMsg"
        closable
        class="mb-4"
        @close="errorMsg = ''"
      />

      <!-- 空状态 -->
      <div v-if="!running && steps.length === 0 && !conclusion" class="py-12">
        <NEmpty description="点击「开始评估」，Agent 将自动分析该标准并给出整合评估结论" />
      </div>

      <!-- ① 评估结论（置顶，最重要） -->
      <template v-if="conclusion">
        <NDivider title-placement="left">
          <span class="text-sm font-medium text-gray-700">整合评估结论</span>
        </NDivider>
        <div class="conclusion-box">
          <!-- eslint-disable-next-line vue/no-v-html -->
          <div class="md-body" v-html="conclusionHtml" />
        </div>
      </template>

      <!-- 结论加载中占位 -->
      <div v-else-if="running" class="conclusion-loading">
        <NSpin :size="16" />
        <span class="ml-2 text-sm text-gray-500">等待评估结论…</span>
      </div>

      <!-- ② 推理步骤（整体折叠） -->
      <template v-if="steps.length > 0">
        <NDivider title-placement="left" class="mt-4">
          <div class="steps-header">
            <button class="steps-toggle" @click="stepsExpanded = !stepsExpanded">
              <span class="text-sm font-medium text-gray-600">
                推理步骤 ({{ steps.length }})
              </span>
              <span class="toggle-arrow" :class="{ expanded: stepsExpanded }">▶</span>
            </button>
            <button class="copy-btn" @click.stop="copyStepsText">
              {{ copySuccess ? '✅ 已复制' : '📋 复制' }}
            </button>
          </div>
        </NDivider>
        <NScrollbar v-if="stepsExpanded" style="max-height: 560px;">
          <div class="steps-list">
            <div
              v-for="step in steps"
              :key="step.id"
              class="step-item"
              :class="`step-${step.type}`"
            >
              <div class="step-header">
                <span class="step-icon">{{ step.icon }}</span>
                <span class="step-label">{{ step.label }}</span>
                <NTag
                  size="tiny"
                  :bordered="false"
                  :type="step.type === 'tool_call' ? 'info' : step.type === 'tool_result' ? 'default' : 'warning'"
                  class="ml-2 flex-shrink-0"
                >
                  #{{ step.id }}
                </NTag>
              </div>
              <pre v-if="step.content" class="step-content">{{ step.content }}</pre>
            </div>
          </div>
        </NScrollbar>
      </template>

    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
/* ── 结论加载占位 ─────────────────────────────────────────────────────────── */
.conclusion-loading {
  display: flex;
  align-items: center;
  padding: 24px;
  background: #f9fafb;
  border-radius: 8px;
  margin-bottom: 12px;
}

/* ── 结论区域 ─────────────────────────────────────────────────────────────── */
.conclusion-box {
  background: linear-gradient(135deg, #f0fdf4 0%, #ecfdf5 100%);
  border: 1px solid #bbf7d0;
  border-radius: 8px;
  padding: 20px 24px;
  margin-top: 8px;
}

/* ── Markdown 正文 ────────────────────────────────────────────────────────── */
.md-body {
  font-size: 13px;
  line-height: 1.8;
  color: #1f2937;
}

.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4) {
  font-weight: 600;
  margin: 14px 0 6px;
  color: #111827;
}
.md-body :deep(h1) { font-size: 16px; }
.md-body :deep(h2) { font-size: 15px; }
.md-body :deep(h3) { font-size: 14px; }
.md-body :deep(h4) { font-size: 13px; }

.md-body :deep(p) { margin: 6px 0; }

.md-body :deep(ul),
.md-body :deep(ol) {
  padding-left: 20px;
  margin: 6px 0;
}
.md-body :deep(li) { margin: 3px 0; }

.md-body :deep(strong) { font-weight: 600; color: #111827; }
.md-body :deep(em) { font-style: italic; color: #374151; }

.md-body :deep(blockquote) {
  border-left: 3px solid #6ee7b7;
  padding-left: 12px;
  margin: 8px 0;
  color: #374151;
}

.md-body :deep(code) {
  background: rgba(0, 0, 0, 0.06);
  padding: 1px 5px;
  border-radius: 3px;
  font-family: 'Consolas', 'Menlo', monospace;
  font-size: 12px;
}

.md-body :deep(pre) {
  background: rgba(0, 0, 0, 0.04);
  padding: 10px 12px;
  border-radius: 4px;
  overflow-x: auto;
  margin: 8px 0;
}

.md-body :deep(hr) {
  border: none;
  border-top: 1px solid #d1fae5;
  margin: 12px 0;
}

/* ── 表格 ─────────────────────────────────────────────────────────────────── */
.md-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 12px;
}
.md-body :deep(th) {
  background: #d1fae5;
  color: #065f46;
  font-weight: 600;
  padding: 8px 12px;
  border: 1px solid #a7f3d0;
  text-align: left;
}
.md-body :deep(td) {
  padding: 7px 12px;
  border: 1px solid #d1fae5;
  color: #1f2937;
  vertical-align: top;
}
.md-body :deep(tr:nth-child(even) td) {
  background: rgba(209, 250, 229, 0.2);
}
.md-body :deep(tr:hover td) {
  background: rgba(167, 243, 208, 0.3);
}

/* ── 步骤整体折叠按钮 ─────────────────────────────────────────────────────── */
.steps-header {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}
.steps-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
}
.steps-toggle:hover span {
  color: #059669;
}
.copy-btn {
  background: none;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  cursor: pointer;
  padding: 1px 8px;
  font-size: 12px;
  color: #6b7280;
  transition: all 0.15s;
}
.copy-btn:hover {
  border-color: #059669;
  color: #059669;
}
.toggle-arrow {
  font-size: 10px;
  color: #9ca3af;
  transition: transform 0.2s;
  display: inline-block;
}
.toggle-arrow.expanded {
  transform: rotate(90deg);
}

/* ── 步骤列表 ─────────────────────────────────────────────────────────────── */
.steps-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 2px 8px;
}

.step-item {
  border-radius: 6px;
  padding: 10px 12px;
  border: 1px solid #f0f0f0;
}

.step-tool_call  { background: #eff6ff; border-color: #bfdbfe; }
.step-tool_result { background: #f9fafb; border-color: #e5e7eb; }
.step-thinking   { background: #fffbeb; border-color: #fde68a; }

.step-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.step-icon {
  font-size: 15px;
  line-height: 1;
  flex-shrink: 0;
}
.step-label {
  flex: 1;
  word-break: break-all;
  color: #374151;
}

/* 步骤内容：全量、等宽字体 */
.step-content {
  margin: 0;
  padding: 10px 12px;
  font-family: 'Consolas', 'Menlo', monospace;
  font-size: 11px;
  color: #4b5563;
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.6;
  background: #f9fafb;
  border-radius: 4px;
}
</style>
