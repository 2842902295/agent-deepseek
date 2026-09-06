<script setup lang="ts">
import {computed, onUnmounted, reactive, ref, watch} from 'vue';
import type {QuestionnaireQuestion} from '@/service/api/ai';

/**
 * 交互式问卷卡片（dsh 问卷协议的前端渲染端）
 *
 * 后端（qa.py::_emit_questionnaire_text）把模型正文里的 ```questionnaire 围栏解析成
 * SSE process kind=questionnaire 事件，正文只留 [questionnaire:<qid>] 占位符；
 * 父组件按占位符把卡片嵌进答案分段（与 [artifact:<ID>] 同一模式）。
 *
 * 交互（单选/多选可混合出现，但页面逻辑不同）：
 * - 一次只展示一题，问题即顶栏；右上角 ‹ n/N › 与键盘左右方向键切题
 * - 单选：点选即自动进入下一题；已选后未选中项变灰（仍可点击换选）；
 *   切回已答过的题清掉可见选中态（不置灰不打勾），答案记在 committed 里不丢
 * - 多选：自由勾选不跳题，切回保留选中样式；底栏左为已选数、右为跳过 + 下一题箭头
 *   （最后一题箭头变向上 = 提交）
 * - 跳过 = 视为已答（记「跳过」）并立即前进（单向、无恢复态）：非末题切下一题；末题单选走停顿自动提交、多选直接提交
 * - 自填输入会撤销跳过态（防多选切回后输入被静默丢成「跳过」）
 * - 自填输入框不参与「选中」：输入内容原样成为答案（单选下与选项互斥，多选追加为一项）
 * - 单选自填行尾按钮随状态变形：无字 = 跳过；有字 = 前进箭头（末题变向上 = 提交）
 * - 单选最后一题：点选/跳过后稍作停顿自动提交（无提交按钮）；自填输入不自动提交（用户可能还要改），由用户点 ↑ 箭头提交
 *
 * 作答不新增接口：提交 = 按约定格式拼一条用户消息走普通发送链路回流
 * （提示词契约见 qa_agent.py「交互式问卷」节：`问卷回答：- q1 (标题): 选项…`）；
 * 回流消息属功能性消息，由父组件隐藏、聊天里不展示。
 */
const props = defineProps<{
  /** 问卷条目 id（qn1/qn2…；占位符同源） */
  qid: string;
  questions: QuestionnaireQuestion[];
  /** 历史回放：各题作答值（问题 id → 值，含「跳过」）；存在即视为已作答 */
  answers?: Record<string, string>;
  /** 会话流式进行中：禁止提交（后端同回合不可并发作答） */
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (e: 'submit', text: string): void;
}>();

const tab = ref(0);

// 当前可见作答状态（按问题 id）：切题离开时单选清空（切回呈现未选中），多选保留选中样式
const picks = reactive<Record<string, string[]>>({}); // 选中的选项 label
const otherText = reactive<Record<string, string>>({}); // 自填内容（原样即答案，无前缀）
const skipped = reactive<Record<string, boolean>>({}); // 跳过
// 该题当前答案是否来自自填输入（跨切题保留）：自填答案不自动提交，交给用户点箭头
const typedAnswered = reactive<Record<string, boolean>>({});

// 已确认答案（问题 id → 答案文本，与 answerValue 同格式）：切题离开时落记，提交时合并
const committed = reactive<Record<string, string | undefined>>({});

const submitted = ref(false);
const submittedAnswers = ref<Record<string, string>>({});

/** 单选选定/跳过后稍作停顿（让用户看到选中态）再前进 */
const ADVANCE_MS = 260;
/** 单选最后一题作答后停顿多久自动提交（留出看到选中态 / 反悔改选的时间） */
const SUBMIT_HOLD_MS = 1000;

let advanceTimer: ReturnType<typeof setTimeout> | null = null;
let submitTimer: ReturnType<typeof setTimeout> | null = null;
onUnmounted(() => {
  if (advanceTimer) clearTimeout(advanceTimer);
  if (submitTimer) clearTimeout(submitTimer);
});

const current = computed(() => props.questions[tab.value] ?? null);
const isLast = computed(() => tab.value >= props.questions.length - 1);
const readonlyMode = computed(() => submitted.value || !!props.answers);

function hasText(q: QuestionnaireQuestion): boolean {
  return (otherText[q.id] || '').trim() !== '';
}

/** 当前可见状态是否构成作答（不含跳过） */
function hasAnswer(q: QuestionnaireQuestion): boolean {
  return !skipped[q.id] && ((picks[q.id]?.length ?? 0) > 0 || hasText(q));
}

/** 当前可见状态是否已处置（作答或跳过） */
function displayAddressed(q: QuestionnaireQuestion): boolean {
  return hasAnswer(q) || !!skipped[q.id];
}

/** 每题作答值（跳过 / 选项 + 自填文本顿号连接） */
function answerValue(q: QuestionnaireQuestion): string {
  const items: string[] = [...(picks[q.id] || [])];
  const t = (otherText[q.id] || '').trim();
  if (t) items.push(t);
  return skipped[q.id] || items.length === 0 ? '跳过' : items.join('、');
}

/** 每题最终值：当前可见状态优先，其次已确认值（切回未重新作答时） */
function finalValue(q: QuestionnaireQuestion): string | null {
  if (displayAddressed(q)) return answerValue(q);
  return committed[q.id] ?? null;
}

// 每题都已处置（作答 或 显式跳过）才可提交——跳过也算数，保证全跳过也有出路
const canSubmit = computed(
  () => !submitted.value && !props.disabled && props.questions.length > 0 && props.questions.every(q => finalValue(q) !== null)
);

/** 已选数（多选底栏左侧）：勾选项 + 自填文本（有字算一项） */
function selectedCount(q: QuestionnaireQuestion): number {
  return (picks[q.id]?.length ?? 0) + (hasText(q) ? 1 : 0);
}

/** 单选选定/跳过后稍作停顿再进入下一题 */
function scheduleAdvance() {
  if (isLast.value) return;
  if (advanceTimer) clearTimeout(advanceTimer);
  advanceTimer = setTimeout(() => {
    advanceTimer = null;
    goTo(tab.value + 1);
  }, ADVANCE_MS);
}

/** 单选最后一题自动提交：全部已处置时挂起倒计时，期间任何新交互重置倒计时（重挂） */
function armAutoSubmit() {
  if (submitTimer) {
    clearTimeout(submitTimer);
    submitTimer = null;
  }
  if (!isLast.value || readonlyMode.value || props.disabled || !canSubmit.value) return;
  if (current.value?.multiSelect) return; // 多选题走底栏 ↑ 显式提交，不自动
  if (current.value && typedAnswered[current.value.id]) return; // 自填答案不自动提交，交给用户点箭头
  submitTimer = setTimeout(() => {
    submitTimer = null;
    handleSubmit();
  }, SUBMIT_HOLD_MS);
}

// 流式结束（disabled 变 false）后若恰好停在已全部处置的单选末题，补挂自动提交
watch(() => props.disabled, () => armAutoSubmit());

/** 切题离开：可见状态落进已确认答案；单选清空可见态（切回呈现未选中），多选保留选中样式 */
function commitOnLeave(q: QuestionnaireQuestion | undefined) {
  if (!q) return;
  if (displayAddressed(q)) committed[q.id] = answerValue(q);
  else if (q.multiSelect) committed[q.id] = undefined; // 多选可见态即真相：取消全部勾选 = 视为未作答
  if (!q.multiSelect) {
    picks[q.id] = [];
    otherText[q.id] = '';
    skipped[q.id] = false;
  }
}

/** 切题统一入口：清挂着的定时器，落记并处理离开的题；到达末题时按需挂起自动提交 */
function goTo(i: number) {
  if (advanceTimer) {
    clearTimeout(advanceTimer);
    advanceTimer = null;
  }
  if (submitTimer) {
    clearTimeout(submitTimer);
    submitTimer = null;
  }
  const clamped = Math.max(0, Math.min(i, props.questions.length - 1));
  if (clamped === tab.value) return;
  commitOnLeave(props.questions[tab.value]);
  tab.value = clamped;
  armAutoSubmit();
}

function next() {
  if (tab.value < props.questions.length - 1) goTo(tab.value + 1);
}

function prev() {
  if (tab.value > 0) goTo(tab.value - 1);
}

function pickOption(q: QuestionnaireQuestion, label: string) {
  if (readonlyMode.value || props.disabled) return;
  skipped[q.id] = false;
  typedAnswered[q.id] = false; // 点选覆盖自填
  if (q.multiSelect) {
    const cur = picks[q.id] || [];
    picks[q.id] = cur.includes(label) ? cur.filter(x => x !== label) : [...cur, label];
    return; // 多选自由勾选，导航/提交走底栏，不自动前进
  }
  picks[q.id] = [label];
  otherText[q.id] = ''; // 单选下选项与自填互斥
  if (isLast.value) armAutoSubmit();
  else scheduleAdvance();
}

function isPicked(q: QuestionnaireQuestion, label: string): boolean {
  return !!picks[q.id]?.includes(label);
}

/** 单选已有选择（选项或自填）时，未选中项变灰（仍可点击换选） */
function optDim(q: QuestionnaireQuestion, label: string): boolean {
  if (q.multiSelect) return false;
  const hasSel = (picks[q.id]?.length ?? 0) > 0 || hasText(q);
  return hasSel && !isPicked(q, label);
}

/** 单选已有选择但无自填时，自填行同样变灰（仍可点击输入） */
function otherDim(q: QuestionnaireQuestion): boolean {
  if (q.multiSelect) return false;
  const hasSel = (picks[q.id]?.length ?? 0) > 0 || hasText(q);
  return hasSel && !hasText(q);
}

/** 点击自填行 = 聚焦输入框（不改选中状态，打字才改） */
function focusOtherInput(e: MouseEvent) {
  (e.currentTarget as HTMLElement).querySelector('input')?.focus();
}

/** 跳过 = 视为已答并立即前进（单选多选一致，单向动作、无「恢复」态）：
 * 非末题切下一题；末题单选走停顿自动提交、多选直接提交（尚不可提交则仅落跳过态，用户可导航回其它题补齐） */
function skipQuestion(q: QuestionnaireQuestion) {
  if (readonlyMode.value || props.disabled) return;
  skipped[q.id] = true;
  typedAnswered[q.id] = false;
  picks[q.id] = [];
  otherText[q.id] = '';
  if (isLast.value) {
    if (q.multiSelect) {
      if (canSubmit.value) handleSubmit();
    } else armAutoSubmit();
  } else scheduleAdvance();
}

/** 键盘左右方向键切题（自填输入框内方向键仍归光标移动，不劫持） */
function onCardKeydown(e: KeyboardEvent) {
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  const tag = (e.target as HTMLElement)?.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  e.preventDefault();
  if (e.key === 'ArrowLeft') prev();
  else next();
}

/** 自填输入：打字即撤销跳过（多选切回时跳过态会残留，不清会把输入静默丢成「跳过」）；
 * 单选下另与选项互斥（打字即清选中）。
 * 自填答案绝不自动提交——用户可能还要改：撤销任何已挂起的自动提交倒计时，提交一律由用户点箭头 */
function onOtherInput() {
  const q = current.value;
  if (!q) return;
  if (skipped[q.id]) skipped[q.id] = false;
  typedAnswered[q.id] = true;
  if (submitTimer) {
    clearTimeout(submitTimer);
    submitTimer = null;
  }
  if (q.multiSelect) return;
  if (hasText(q) && (picks[q.id]?.length ?? 0) > 0) picks[q.id] = [];
}

/** 自填输入框回车：单选前进（末题立即提交）；多选回车只是输入习惯，不跳题（多选导航一律走底栏） */
function onOtherEnter() {
  const q = current.value;
  if (!q || q.multiSelect) return;
  if (!isLast.value) next();
  else handleSubmit();
}

function handleSubmit() {
  if (!canSubmit.value) return;
  if (submitTimer) {
    clearTimeout(submitTimer);
    submitTimer = null;
  }
  if (advanceTimer) {
    clearTimeout(advanceTimer);
    advanceTimer = null;
  }
  const per: Record<string, string> = {};
  const lines: string[] = [];
  for (const q of props.questions) {
    const v = finalValue(q) ?? '跳过';
    per[q.id] = v;
    lines.push(`- ${q.id} (${q.title}): ${v}`);
  }
  submittedAnswers.value = per;
  submitted.value = true;
  emit('submit', `问卷回答：\n${lines.join('\n')}`);
}

/** 只读回显：本地点选提交的值 优先，其次历史回放回流值 */
function displayValue(q: QuestionnaireQuestion): string {
  if (submitted.value) return submittedAnswers.value[q.id] ?? '已作答';
  return props.answers?.[q.id] ?? '已作答';
}
</script>

<template>
  <div class="qst" :data-qid="qid">
    <div class="qst-box" tabindex="0" @keydown="onCardKeydown">
      <!-- 载荷缺失（异常兜底：process 条目被清但占位符还在正文） -->
      <template v-if="!questions.length">
        <div class="qst-ro-head">
          <span class="qst-ro-done"><svg viewBox="0 0 16 16" width="9" height="9" aria-hidden="true"><path d="M3 8.5l3.2 3.5L13 4.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" /></svg></span>
          <span class="qst-ro-muted">问卷内容不可用</span>
        </div>
      </template>

      <template v-else>
        <!-- 已作答（本地提交 或 历史回流）：只读回显实际答案 -->
        <template v-if="readonlyMode">
          <div class="qst-ro-head">
            <span class="qst-ro-done"><svg viewBox="0 0 16 16" width="9" height="9" aria-hidden="true"><path d="M3 8.5l3.2 3.5L13 4.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" /></svg></span>
            <span class="qst-ro-label">已作答</span>
          </div>
          <div class="qst-ro">
            <div v-for="q in questions" :key="q.id" class="qst-ro-line">
              <span class="qst-ro-title">{{ q.title }}</span>
              <span class="qst-ro-val" :class="{skip: displayValue(q) === '跳过'}">{{ displayValue(q) }}</span>
            </div>
          </div>
        </template>

        <!-- 作答态：一次一题，问题即顶栏，右上角 ‹ n/N › 顺序切换 -->
        <template v-else>
          <Transition name="qst-swap" mode="out-in">
            <div v-if="current" :key="current.id" class="qst-body">
              <div class="qst-head">
                <div class="qst-question">
                  {{ current.question }}
                  <span v-if="current.multiSelect" class="qst-multi-hint">可多选</span>
                </div>
                <div class="qst-nav">
                  <button type="button" class="qst-nav-btn" :disabled="tab === 0" aria-label="上一题" @click="prev">
                    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M10 3L5 8l5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
                  </button>
                  <span class="qst-nav-count">{{ tab + 1 }}/{{ questions.length }}</span>
                  <button type="button" class="qst-nav-btn" :disabled="isLast" aria-label="下一题" @click="next">
                    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M6 3l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
                  </button>
                </div>
              </div>

              <div class="qst-options">
                <button
                  v-for="opt in current.options"
                  :key="opt.label"
                  type="button"
                  class="qst-opt"
                  :class="{picked: isPicked(current, opt.label), dim: optDim(current, opt.label), multi: current.multiSelect}"
                  :disabled="disabled"
                  @click="pickOption(current, opt.label)"
                >
                  <span class="qst-opt-mark">
                    <svg v-if="current.multiSelect && isPicked(current, opt.label)" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="1.5" y="1.5" width="13" height="13" rx="3.5" fill="currentColor" /><path d="M4.8 8.4l2.1 2.1 4.3-4.8" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>
                    <svg v-else-if="current.multiSelect" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="1.5" y="1.5" width="13" height="13" rx="3.5" fill="none" stroke="currentColor" stroke-width="1.5" /></svg>
                    <svg v-else-if="isPicked(current, opt.label)" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5" /><circle cx="8" cy="8" r="3.2" fill="currentColor" /></svg>
                    <svg v-else viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5" /></svg>
                  </span>
                  <span class="qst-opt-text">
                    <span class="qst-opt-label">{{ opt.label }}</span>
                    <span v-if="opt.description" class="qst-opt-desc">{{ opt.description }}</span>
                  </span>
                </button>

                <!-- 自填：样式与上方选项对齐；不参与选中，输入内容原样成为答案；单选派行尾按钮随状态变形（无字=跳过；有字=前进，末题向上=提交） -->
                <div
                  class="qst-opt qst-other"
                  :class="{picked: hasText(current), dim: otherDim(current), multi: current.multiSelect}"
                  @click="focusOtherInput"
                >
                  <span class="qst-opt-mark">
                    <svg v-if="current.multiSelect && hasText(current)" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="1.5" y="1.5" width="13" height="13" rx="3.5" fill="currentColor" /><path d="M4.8 8.4l2.1 2.1 4.3-4.8" fill="none" stroke="#fff" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>
                    <svg v-else-if="current.multiSelect" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="1.5" y="1.5" width="13" height="13" rx="3.5" fill="none" stroke="currentColor" stroke-width="1.5" /></svg>
                    <svg v-else-if="hasText(current)" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5" /><circle cx="8" cy="8" r="3.2" fill="currentColor" /></svg>
                    <svg v-else viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5" /></svg>
                  </span>
                  <input
                    v-model="otherText[current.id]"
                    type="text"
                    class="qst-other-input"
                    :disabled="disabled"
                    placeholder="其他想法，直接输入…"
                    maxlength="200"
                    @keydown.enter.prevent="onOtherEnter"
                    @input="onOtherInput"
                  />
                  <template v-if="!current.multiSelect">
                    <button
                      v-if="hasText(current)"
                      type="button"
                      class="qst-go"
                      :disabled="isLast ? !canSubmit : disabled"
                      :title="isLast ? '提交回答' : '下一题'"
                      :aria-label="isLast ? '提交回答' : '下一题'"
                      @click="isLast ? handleSubmit() : next()"
                    >
                      <svg v-if="isLast" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M8 13.5v-11M3.5 7L8 2.5 12.5 7" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>
                      <svg v-else viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M2.5 8h11M9 3.5L13.5 8 9 12.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>
                    </button>
                    <button
                      v-else
                      type="button"
                      class="qst-skip"
                      :disabled="disabled"
                      @click="skipQuestion(current)"
                    >
                      跳过
                    </button>
                  </template>
                </div>
              </div>

              <!-- 多选底栏：左已选数；右跳过 + 下一题（最后一题箭头向上 = 提交） -->
              <div v-if="current.multiSelect" class="qst-foot">
                <span class="qst-count">已选 {{ selectedCount(current) }} 项</span>
                <button
                  type="button"
                  class="qst-skip"
                  :disabled="disabled"
                  @click="skipQuestion(current)"
                >
                  跳过
                </button>
                <button
                  type="button"
                  class="qst-go"
                  :disabled="isLast ? !canSubmit : disabled"
                  :title="isLast ? '提交回答' : '下一题'"
                  :aria-label="isLast ? '提交回答' : '下一题'"
                  @click="isLast ? handleSubmit() : next()"
                >
                  <svg v-if="isLast" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M8 13.5v-11M3.5 7L8 2.5 12.5 7" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>
                  <svg v-else viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M2.5 8h11M9 3.5L13.5 8 9 12.5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>
                </button>
              </div>
            </div>
          </Transition>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.qst {
  margin: 10px 0;
}

.qst-box {
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.42);
  backdrop-filter: blur(20px) saturate(180%);
  overflow: hidden;
  outline: none;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
}

/* 键盘可达（左右方向键切题）：仅键盘聚焦时描边提示 */
.qst-box:focus-visible {
  border-color: rgba(30, 64, 175, 0.35);
}

/* ── 顶栏：问题 + 右上角切换 ──────────────────────── */
.qst-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 8px;
}

.qst-nav {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  margin-top: 2px;
}

.qst-nav-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 1px solid var(--rule);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.5);
  color: var(--ink-3);
  cursor: pointer;
  transition: all 0.12s;
}

.qst-nav-btn:hover:not(:disabled) {
  border-color: rgba(30, 64, 175, 0.3);
  color: var(--accent);
}

.qst-nav-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.qst-nav-count {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-4);
  min-width: 26px;
  text-align: center;
}

/* ── 题目区 ──────────────────────────────────────── */
.qst-body {
  padding: 10px 12px 12px;
}

.qst-question {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  line-height: 1.6;
}

.qst-multi-hint {
  display: inline-block;
  margin-left: 6px;
  font-size: 10px;
  font-weight: 500;
  color: var(--ink-4);
  border: 1px solid var(--rule);
  border-radius: 99px;
  padding: 0 7px;
  vertical-align: 1px;
}

/* 选项列表：轻量风格——不逐项描边，行间一条分隔线 */
.qst-options {
  display: flex;
  flex-direction: column;
}

.qst-opt {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  text-align: left;
  border: none;
  border-bottom: 1px solid var(--rule);
  border-radius: 0;
  background: transparent;
  padding: 9px 6px;
  cursor: pointer;
  transition: background 0.15s;
}

.qst-opt:last-child {
  border-bottom: none;
}

.qst-opt:hover:not(:disabled):not(.dim) {
  background: rgba(30, 64, 175, 0.05);
}

/* 选中态不加背景框：标记变 accent + 文案加粗着色即可 */
.qst-opt.picked .qst-opt-label {
  color: var(--accent);
  font-weight: 600;
}

.qst-opt.dim {
  opacity: 0.45;
}

.qst-opt:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

/* 选中标记：内联 SVG（单选 radio / 多选 checkbox）；未选中灰，选中态随整行 picked 变 accent */
.qst-opt-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 1px;
  color: var(--ink-4);
}

/* 自填行垂直居中布局下不需要首行对齐补偿 */
.qst-other .qst-opt-mark {
  margin-top: 0;
}

.qst-opt.picked .qst-opt-mark {
  color: var(--accent);
}

.qst-opt-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.qst-opt-label {
  font-size: 12.5px;
  color: var(--ink);
  line-height: 1.5;
}

.qst-opt-desc {
  font-size: 11px;
  color: var(--ink-4);
  line-height: 1.5;
}

/* 自填行：与上方选项同款分隔线样式，内容垂直居中；聚焦时底线加深 + 轻底色提示 */
.qst-other {
  align-items: center;
}

.qst-other:focus-within {
  background: rgba(30, 64, 175, 0.05);
  border-bottom-color: rgba(30, 64, 175, 0.35);
}

.qst-other-input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  font-size: 12.5px;
  color: var(--ink);
  outline: none;
  padding: 0;
}

.qst-other-input::placeholder {
  color: var(--ink-4);
}

.qst-other-input:disabled {
  cursor: not-allowed;
}

/* 跳过（单选挂自填行尾；多选挂底栏）：与箭头按钮同款矩形框，白底 */
.qst-skip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--rule);
  border-radius: 8px;
  background: #fff;
  font-size: 11px;
  color: var(--ink-3);
  cursor: pointer;
  flex-shrink: 0;
  white-space: nowrap;
  transition: all 0.12s;
}

.qst-skip:hover:not(:disabled) {
  border-color: rgba(30, 64, 175, 0.25);
  color: var(--accent);
}

.qst-skip:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

/* ── 多选底栏（与选项区以一条分隔线相隔） ──────────── */
.qst-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  padding-top: 10px;
  border-top: 1px solid var(--rule);
}

.qst-count {
  flex: 1;
  font-size: 11px;
  color: var(--ink-4);
}

.qst-go {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  flex-shrink: 0;
  transition: filter 0.12s, opacity 0.12s;
}

.qst-go:hover:not(:disabled) {
  filter: brightness(1.08);
}

.qst-go:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ── 只读回显 ───────────────────────────────────── */
.qst-ro-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 12px 0;
}

.qst-ro-done {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  border-radius: 99px;
  background: rgba(42, 157, 143, 0.14);
  color: #2a9d8f;
}

.qst-ro-label {
  font-size: 11px;
  font-weight: 600;
  color: #2a9d8f;
  letter-spacing: 0.04em;
}

.qst-ro-muted {
  font-size: 11px;
  color: var(--ink-4);
}

.qst-ro {
  padding: 8px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.qst-ro-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  line-height: 1.6;
}

.qst-ro-title {
  color: var(--ink-4);
  flex-shrink: 0;
}

.qst-ro-title::after {
  content: '·';
  margin-left: 8px;
  color: var(--rule);
}

.qst-ro-val {
  color: var(--ink);
  word-break: break-word;
}

.qst-ro-val.skip {
  color: var(--ink-4);
}

/* ── 切题过渡 ───────────────────────────────────── */
.qst-swap-enter-active,
.qst-swap-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.qst-swap-enter-from {
  opacity: 0;
  transform: translateX(10px);
}

.qst-swap-leave-to {
  opacity: 0;
  transform: translateX(-10px);
}
</style>
