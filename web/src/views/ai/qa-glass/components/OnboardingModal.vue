<script setup lang="ts">
import {computed, ref, watch} from 'vue';
import {NModal} from 'naive-ui';
import SvgIcon from '@/components/custom/svg-icon.vue';
import type {Profession, QuickAction, QuickActionGroup} from '@/service/api/ai';

const props = withDefaults(
  defineProps<{
    show: boolean;
    /** onboarding=首次强制引导（不可关闭）；settings=功能设置（可关闭） */
    mode: 'onboarding' | 'settings';
    professions: Profession[];
    actions: QuickAction[];
    groups: QuickActionGroup[];
    initialProfessionId?: number | null;
    initialActionIds?: number[];
  }>(),
  {
    initialProfessionId: null,
    initialActionIds: () => []
  }
);

const emit = defineEmits<{
  'update:show': [value: boolean];
  confirm: [payload: {professionId: number; actionIds: number[]; professionChanged: boolean}];
}>();

const step = ref<1 | 2>(1);
const selectedProfessionId = ref<number | null>(null);
const checked = ref<Set<number>>(new Set());
const submitting = ref(false);

/** 第二步按橱窗章节分组展示全部功能 */
const groupedActions = computed(() => {
  const byId = new Map(props.actions.map(a => [a.id, a]));
  const list: Array<{cat: string; actions: QuickAction[]}> = [];
  const grouped = new Set<number>();
  for (const g of props.groups) {
    const members = g.actionIds.map(id => byId.get(id)).filter((a): a is QuickAction => Boolean(a));
    if (!members.length) continue;
    list.push({cat: g.name, actions: members});
    members.forEach(a => grouped.add(a.id));
  }
  const rest = props.actions.filter(a => !grouped.has(a.id));
  if (rest.length) list.push({cat: '更多能力', actions: rest});
  return list;
});

const selectedProfession = computed(() => props.professions.find(p => p.id === selectedProfessionId.value) || null);

function applyRecommended(professionId: number | null) {
  const prof = props.professions.find(p => p.id === professionId);
  const ids = prof?.recommendedActionIds ?? [];
  checked.value = new Set(ids);
}

function pickProfession(id: number) {
  selectedProfessionId.value = id;
}

function goStep2() {
  if (!selectedProfessionId.value) return;
  // 进入第二步：按所选职业预勾选推荐功能
  applyRecommended(selectedProfessionId.value);
  step.value = 2;
}

function backStep1() {
  step.value = 1;
}

function toggleAction(id: number) {
  const next = new Set(checked.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  checked.value = next;
}

function isRecommended(id: number): boolean {
  return selectedProfession.value?.recommendedActionIds.includes(id) ?? false;
}

function confirm() {
  if (!selectedProfessionId.value || checked.value.size === 0) return;
  submitting.value = true;
  emit('confirm', {
    professionId: selectedProfessionId.value,
    actionIds: [...checked.value],
    professionChanged: props.mode === 'onboarding' ? false : selectedProfessionId.value !== props.initialProfessionId
  });
}

function close() {
  if (props.mode === 'onboarding') return; // 引导不可跳过
  emit('update:show', false);
}

// 打开时初始化：settings 回显当前职业与订阅；onboarding 从第一步开始
watch(
  () => props.show,
  v => {
    if (!v) return;
    submitting.value = false;
    if (props.mode === 'settings') {
      selectedProfessionId.value = props.initialProfessionId;
      checked.value = new Set(props.initialActionIds);
      step.value = props.initialProfessionId ? 2 : 1;
    } else {
      selectedProfessionId.value = null;
      checked.value = new Set();
      step.value = 1;
    }
  }
);

// 提交完成后由父组件关闭；此处监听 submitting 复位
watch(
  () => props.show,
  v => {
    if (!v) submitting.value = false;
  }
);
</script>

<template>
  <NModal :show="show" :mask-closable="mode === 'settings'" :closable="false" @update:show="v => !v && close()">
    <div class="obm-card" role="dialog" :aria-label="mode === 'onboarding' ? '新手引导' : '功能设置'">
      <div class="obm-glow" aria-hidden="true" />

      <header class="obm-head">
        <span class="obm-mark" aria-hidden="true">
          <SvgIcon :icon="step === 1 ? 'mdi:account-details-outline' : 'mdi:format-list-checks'" />
        </span>
        <div class="obm-titles">
          <h3 class="obm-title">{{ mode === 'onboarding' ? '欢迎使用' : '功能设置' }}</h3>
          <p class="obm-sub">
            <template v-if="step === 1">STEP 1 · 选择你的职业</template>
            <template v-else>STEP 2 · 勾选常用功能</template>
          </p>
        </div>
        <button v-if="mode === 'settings'" class="obm-close" title="关闭" @click="close">×</button>
      </header>

      <!-- 步骤指示 -->
      <div class="obm-steps">
        <span class="obm-step" :class="{active: step === 1, done: step > 1}">1 选择职业</span>
        <span class="obm-step-line" :class="{active: step > 1}" />
        <span class="obm-step" :class="{active: step === 2}">2 勾选功能</span>
      </div>

      <!-- ─── 第一步：职业卡片 ─── -->
      <div v-if="step === 1" class="obm-body">
        <div class="obm-prof-grid">
          <button
            v-for="prof in professions"
            :key="prof.id"
            type="button"
            class="obm-prof"
            :class="{selected: selectedProfessionId === prof.id}"
            @click="pickProfession(prof.id)"
          >
            <span class="obm-prof-icon">
              <SvgIcon :icon="prof.icon || 'mdi:account-outline'" />
            </span>
            <span class="obm-prof-name">{{ prof.name }}</span>
            <span class="obm-prof-desc">{{ prof.description }}</span>
            <span class="obm-prof-count">{{ prof.recommendedActionIds.length }} 项推荐</span>
            <span v-if="selectedProfessionId === prof.id" class="obm-prof-check" aria-hidden="true">
              <SvgIcon icon="mdi:check" />
            </span>
          </button>
        </div>
      </div>

      <!-- ─── 第二步：功能勾选 ─── -->
      <div v-else class="obm-body obm-body--actions">
        <div class="obm-actions-tip">
          <SvgIcon icon="mdi:lightbulb-on-outline" />
          <span>已按「{{ selectedProfession?.name }}」为你预选，可自行增删</span>
          <span class="obm-actions-count">已选 {{ checked.size }} 项</span>
        </div>
        <div class="obm-actions-scroll">
          <section v-for="group in groupedActions" :key="group.cat" class="obm-group">
            <h4 class="obm-group-title">{{ group.cat }}</h4>
            <div class="obm-action-grid">
              <button
                v-for="action in group.actions"
                :key="action.id"
                type="button"
                class="obm-action"
                :class="{checked: checked.has(action.id)}"
                @click="toggleAction(action.id)"
              >
                <span class="obm-action-box" aria-hidden="true">
                  <SvgIcon v-if="checked.has(action.id)" icon="mdi:check" />
                </span>
                <span class="obm-action-icon">
                  <SvgIcon :icon="action.icon || 'mdi:lightning-bolt'" />
                </span>
                <span class="obm-action-text">
                  <span class="obm-action-name">
                    {{ action.name }}
                    <em v-if="isRecommended(action.id)" class="obm-rec">荐</em>
                  </span>
                  <span v-if="action.description" class="obm-action-desc">{{ action.description }}</span>
                </span>
              </button>
            </div>
          </section>
        </div>
      </div>

      <footer class="obm-foot">
        <button v-if="step === 2" type="button" class="obm-btn obm-btn--ghost" @click="backStep1">上一步</button>
        <span class="obm-foot-spacer" />
        <button
          v-if="step === 1"
          type="button"
          class="obm-btn obm-btn--primary"
          :disabled="!selectedProfessionId"
          @click="goStep2"
        >
          下一步
        </button>
        <button
          v-else
          type="button"
          class="obm-btn obm-btn--primary"
          :disabled="checked.size === 0 || submitting"
          @click="confirm"
        >
          {{ submitting ? '保存中…' : mode === 'onboarding' ? '开始使用' : '保存设置' }}
        </button>
      </footer>
    </div>
  </NModal>
</template>

<style scoped>
/* 弹窗 teleport 到 body，脱离 .qa-shell 作用域，复刻其玻璃设计变量（与 SessionSearchModal 同款） */
.obm-card {
  --paper: #f5f7fb;
  --surface: rgba(255, 255, 255, 0.55);
  --surface-strong: rgba(255, 255, 255, 0.72);
  --ink: #0f172a;
  --ink-2: #334155;
  --ink-3: #64748b;
  --ink-4: #94a3b8;
  --accent: #1e40af;
  --accent-2: #2563eb;
  --accent-soft: rgba(30, 64, 175, 0.08);
  --aurora: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  --font-display: 'Plus Jakarta Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;

  position: relative;
  width: 720px;
  max-width: 94vw;
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 20px;
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  font-family: var(--font-display);
  color: var(--ink);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.06),
    0 24px 64px -20px rgba(30, 64, 175, 0.32);
  animation: obm-pop 0.32s cubic-bezier(0.32, 0.72, 0, 1);
}

@keyframes obm-pop {
  from {
    opacity: 0;
    transform: translateY(14px) scale(0.975);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@keyframes obm-check-pop {
  from {
    opacity: 0;
    transform: scale(0.5);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.obm-glow {
  position: absolute;
  top: -70px;
  left: 50%;
  width: 340px;
  height: 160px;
  transform: translateX(-50%);
  background: var(--aurora);
  opacity: 0.16;
  filter: blur(48px);
  pointer-events: none;
}

/* ─── 头部 ─── */
.obm-head {
  position: relative;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 22px 24px 14px;
}

.obm-mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--aurora);
  color: #fff;
  font-size: 20px;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 4px 14px -4px rgba(30, 64, 175, 0.55);
}

.obm-titles {
  flex: 1;
  min-width: 0;
}

.obm-title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  background: var(--aurora);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.obm-sub {
  margin: 3px 0 0;
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  color: var(--ink-3);
}

.obm-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--ink-3);
  font-size: 19px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.18s ease;
  flex-shrink: 0;
}

.obm-close:hover {
  background: rgba(185, 28, 28, 0.08);
  color: #b91c1c;
  transform: rotate(90deg);
}

/* ─── 步骤指示 ─── */
.obm-steps {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 26px 14px;
}

.obm-step {
  padding: 3px 10px;
  border-radius: 99px;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--ink-4);
  transition: all 0.2s ease;
}

.obm-step.active {
  color: var(--accent);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px rgba(30, 64, 175, 0.14);
}

.obm-step.done {
  color: var(--accent-2);
}

.obm-step-line {
  flex: 0 0 34px;
  height: 2px;
  border-radius: 2px;
  background: rgba(30, 64, 175, 0.14);
  transition: background 0.2s ease;
}

.obm-step-line.active {
  background: var(--aurora);
}

/* ─── 主体 ─── */
.obm-body {
  flex: 1;
  overflow-y: auto;
  padding: 4px 24px 16px;
  border-top: 1px solid rgba(30, 64, 175, 0.08);
}

.obm-body::-webkit-scrollbar {
  width: 6px;
}
.obm-body::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.14);
  border-radius: 4px;
}

/* 第一步：职业卡片网格 */
.obm-prof-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
  padding-top: 18px;
}

.obm-prof {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding: 18px;
  border: 1.5px solid rgba(30, 64, 175, 0.12);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.55);
  text-align: left;
  cursor: pointer;
  transition: all 0.2s ease;
}

.obm-prof:hover {
  border-color: rgba(30, 64, 175, 0.32);
  background: rgba(255, 255, 255, 0.72);
  transform: translateY(-2px);
  box-shadow: 0 10px 28px -14px rgba(30, 64, 175, 0.4);
}

.obm-prof.selected {
  border-color: rgba(37, 99, 235, 0.75);
  background: linear-gradient(160deg, rgba(219, 234, 254, 0.92), rgba(224, 242, 254, 0.66));
  box-shadow:
    0 0 0 3px rgba(37, 99, 235, 0.13),
    0 12px 30px -16px rgba(30, 64, 175, 0.5);
}

.obm-prof.selected:hover {
  border-color: rgba(37, 99, 235, 0.85);
  box-shadow:
    0 0 0 3px rgba(37, 99, 235, 0.16),
    0 14px 32px -16px rgba(30, 64, 175, 0.55);
}

.obm-prof.selected .obm-prof-name {
  color: var(--accent);
}

.obm-prof.selected .obm-prof-desc {
  color: var(--ink-2);
}

.obm-prof.selected .obm-prof-count {
  background: var(--accent-2);
  color: #fff;
}

.obm-prof-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 22px;
}

.obm-prof.selected .obm-prof-icon {
  background: var(--aurora);
  color: #fff;
  box-shadow: 0 6px 16px -6px rgba(30, 64, 175, 0.55);
}

.obm-prof-name {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
}

.obm-prof-desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink-3);
}

.obm-prof-count {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--accent);
  padding: 2px 8px;
  border-radius: 99px;
  background: var(--accent-soft);
}

.obm-prof-check {
  position: absolute;
  top: 12px;
  right: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--aurora);
  color: #fff;
  font-size: 14px;
  box-shadow:
    0 0 0 3px rgba(255, 255, 255, 0.6),
    0 3px 10px -3px rgba(30, 64, 175, 0.6);
  animation: obm-check-pop 0.24s cubic-bezier(0.34, 1.56, 0.64, 1);
}

/* 第二步：功能勾选 */
.obm-actions-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 14px 0 6px;
  padding: 9px 12px;
  border-radius: 12px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
}

.obm-actions-count {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
}

.obm-actions-scroll {
  padding-bottom: 4px;
}

.obm-group {
  margin-top: 14px;
}

.obm-group-title {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--ink-2);
}

.obm-action-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

.obm-action {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1.5px solid rgba(30, 64, 175, 0.1);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.55);
  text-align: left;
  cursor: pointer;
  transition: all 0.16s ease;
}

.obm-action:hover {
  border-color: rgba(30, 64, 175, 0.28);
  background: rgba(255, 255, 255, 0.74);
}

.obm-action.checked {
  border-color: rgba(37, 99, 235, 0.55);
  background: linear-gradient(160deg, rgba(219, 234, 254, 0.78), rgba(224, 242, 254, 0.52));
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.09);
}

.obm-action-box {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  margin-top: 1px;
  border: 1.5px solid rgba(30, 64, 175, 0.35);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.7);
  color: #fff;
  font-size: 14px;
  flex-shrink: 0;
  transition: all 0.16s ease;
}

.obm-action.checked .obm-action-box {
  background: var(--aurora);
  border-color: transparent;
  box-shadow: 0 2px 8px -2px rgba(30, 64, 175, 0.5);
  animation: obm-check-pop 0.22s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.obm-action.checked .obm-action-icon {
  background: rgba(37, 99, 235, 0.14);
  color: var(--accent-2);
}

.obm-action-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 16px;
  flex-shrink: 0;
}

.obm-action-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.obm-action-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  display: flex;
  align-items: center;
  gap: 5px;
}

.obm-rec {
  font-style: normal;
  font-size: 9px;
  font-weight: 700;
  color: #fff;
  background: var(--accent-2);
  border-radius: 4px;
  padding: 0 4px;
  line-height: 14px;
}

.obm-action-desc {
  font-size: 11px;
  line-height: 1.4;
  color: var(--ink-3);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ─── 底部 ─── */
.obm-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 24px 20px;
  border-top: 1px solid rgba(30, 64, 175, 0.08);
}

.obm-foot-spacer {
  flex: 1;
}

.obm-btn {
  height: 38px;
  padding: 0 22px;
  border: none;
  border-radius: 12px;
  font-family: var(--font-display);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.18s ease;
}

.obm-btn--primary {
  background: var(--aurora);
  color: #fff;
  box-shadow: 0 6px 18px -8px rgba(30, 64, 175, 0.6);
}

.obm-btn--primary:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 10px 24px -8px rgba(30, 64, 175, 0.65);
}

.obm-btn--primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  box-shadow: none;
}

.obm-btn--ghost {
  background: var(--surface-strong);
  color: var(--ink-2);
  border: 1px solid rgba(30, 64, 175, 0.14);
}

.obm-btn--ghost:hover {
  border-color: rgba(30, 64, 175, 0.3);
  color: var(--accent);
}

@media (max-width: 640px) {
  .obm-prof-grid,
  .obm-action-grid {
    grid-template-columns: 1fr;
  }
}
</style>
