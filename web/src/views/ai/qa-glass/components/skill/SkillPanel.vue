<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useAuthStore } from '@/store/modules/auth';
import {
  fetchAgentSkills,
  fetchUpdateAgentSkill,
  fetchDeleteAgentSkill,
  fetchDownloadAgentSkill
} from '@/service/api';
import type { AgentSkill } from '@/service/api';
import SkillListView from './SkillListView.vue';
import SkillDetailView from './SkillDetailView.vue';
import SkillCreateView from './SkillCreateView.vue';
import SkillDiscoverView from './SkillDiscoverView.vue';
import SkillUploadView from './SkillUploadView.vue';

type ViewName = 'list' | 'detail' | 'create' | 'discover' | 'upload';

const props = defineProps<{ show: boolean }>();
const emit = defineEmits<{
  'update:show': [value: boolean];
  use: [skillKey: string];
  /** 引导到会话框：关闭抽屉并把起手文案填入输入框 */
  fill: [text: string];
  /** 技能数据发生任何变更（启停/删除/上传/编辑保存）：通知外层刷新 @ 调用列表 */
  change: [];
}>();

const authStore = useAuthStore();
const myUserId = computed<number | null>(() => {
  const id = authStore.userInfo?.userId;
  return id != null && id !== '' ? Number(id) : null;
});
/** 超管或管理员：技能管理权限（编辑/他人技能删除/官方设置/指定角色筛选） */
const isAdmin = computed(() => (authStore.userInfo?.roles || []).some(r => r === 'R_SUPER' || r === 'R_ADMIN'));

const skills = ref<AgentSkill[]>([]);
const loading = ref(false);
const viewStack = ref<ViewName[]>(['list']);
const currentView = computed<ViewName>(() => viewStack.value[viewStack.value.length - 1]);
const selectedSkill = ref<AgentSkill | null>(null);
const highlightKey = ref<string | null>(null);

const viewMeta: Record<ViewName, { eyebrow: string; title: string }> = {
  list: { eyebrow: '技能库', title: '' },
  detail: { eyebrow: 'SKILL DETAIL', title: '技能详情' },
  create: { eyebrow: 'NEW SKILL', title: '创建技能' },
  discover: { eyebrow: 'DISCOVER', title: '发现技能' },
  upload: { eyebrow: 'UPLOAD PKG', title: '上传技能包' }
};
const currentMeta = computed(() => viewMeta[currentView.value]);

// 统计条（仅列表视图）
const statTotal = computed(() => skills.value.length);
const statEnabled = computed(() => skills.value.filter(s => s.isEnabled).length);
const statMine = computed(() => skills.value.filter(s => myUserId.value != null && s.userId === myUserId.value).length);

async function loadSkills() {
  loading.value = true;
  try {
    const { data, error } = await fetchAgentSkills(true);
    if (!error && data) skills.value = data;
  } finally {
    loading.value = false;
  }
}

function navigate(view: ViewName) {
  viewStack.value.push(view);
}
function goBack() {
  if (viewStack.value.length > 1) viewStack.value.pop();
  else selectedSkill.value = null;
}
function resetTo() {
  viewStack.value = ['list'];
}
function close() {
  emit('update:show', false);
}

// ── 卡片动作 ──────────────────────────────────────────────
function onUse(skill: AgentSkill) {
  emit('use', skill.skillKey);
  close();
}
function onAiEdit(skill: AgentSkill) {
  // 预填带引导话术的起手文案（无权限时 agent 会引导新建自己的技能）
  emit('fill', `@编辑 @${skill.skillKey} 帮我修改这个技能，我想调整的是：`);
  close();
}
function onDetail(skill: AgentSkill) {
  selectedSkill.value = skill;
  navigate('detail');
}
async function onDownload(skill: AgentSkill) {
  try {
    await fetchDownloadAgentSkill(skill.id, `${skill.skillKey}.zip`);
    window.$message?.success('已开始下载');
  } catch (e) {
    window.$message?.error((e as Error).message || '下载失败');
  }
}
/** 删除：二次确认由触发处的 NPopconfirm 完成，这里直接执行 */
async function onRemove(skill: AgentSkill) {
  const { error } = await fetchDeleteAgentSkill(skill.id);
  if (!error) {
    window.$message?.success('已删除');
    if (selectedSkill.value?.id === skill.id) goBack();
    await loadSkills();
    emit('change');
  } else {
    window.$message?.error('删除失败');
  }
}
async function onToggle(skill: AgentSkill) {
  const { data, error } = await fetchUpdateAgentSkill(skill.id, { is_enabled: !skill.isEnabled });
  if (!error && data) {
    Object.assign(skill, data);
    emit('change');
  } else {
    window.$message?.error('切换失败');
  }
}

// ── 子视图回调 ────────────────────────────────────────────
function onFill(text: string) {
  emit('fill', text);
  close();
}
/** 上传视图每完成一次上传：回列表并高亮该技能（视图自身保留，可连续上传） */
function onUploaded(skillKey: string) {
  highlightKey.value = skillKey;
  loadSkills();
  emit('change');
}
function onDetailChanged(skill: AgentSkill) {
  const idx = skills.value.findIndex(s => s.id === skill.id);
  if (idx >= 0) skills.value[idx] = skill;
  selectedSkill.value = skill;
  emit('change');
}

watch(
  () => props.show,
  open => {
    if (open) {
      resetTo();
      selectedSkill.value = null;
      highlightKey.value = null;
      loadSkills();
    }
  }
);
</script>

<template>
  <Teleport to="body">
    <Transition name="sk-mask">
      <div v-if="show" class="sk-mask" @click="close" />
    </Transition>

    <Transition name="sk-panel">
      <div v-if="show" class="sk-panel" @click.stop>
        <!-- 头部：点阵纹理 + 幽灵水印 + 渐变标题 -->
        <header class="sk-head">
          <span class="sk-head-dots" />
          <span class="sk-head-ghost">{{ currentView === 'list' ? '@' : '#' }}</span>

          <div class="sk-head-main">
            <div class="sk-head-eyebrow-row">
              <button v-if="currentView !== 'list'" class="sk-back" @click="goBack">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M10 3L5 8l5 5" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" /></svg>
                返回
              </button>
              <button v-else class="sk-close" title="关闭" @click="close">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.7" stroke-linecap="round" /></svg>
              </button>
              <span class="sk-eyebrow" :class="{ 'sk-eyebrow--lg': currentView === 'list' }">{{ currentMeta.eyebrow }}</span>
              <span class="sk-eyebrow-line" />
            </div>
            <h2 v-if="currentMeta.title" class="sk-head-title">{{ currentMeta.title }}</h2>
          </div>
        </header>

        <!-- 列表视图：统计 + 操作按钮同行 -->
        <template v-if="currentView === 'list'">
          <div class="sk-stats">
            <div class="sk-stat">
              <span class="sk-stat-num">{{ statTotal }}</span>
              <span class="sk-stat-label">全部技能</span>
            </div>
            <span class="sk-stat-sep" />
            <div class="sk-stat">
              <span class="sk-stat-num sk-stat-num--on">{{ statEnabled }}</span>
              <span class="sk-stat-label">已启用</span>
            </div>
            <span class="sk-stat-sep" />
            <div class="sk-stat">
              <span class="sk-stat-num sk-stat-num--mine">{{ statMine }}</span>
              <span class="sk-stat-label">我的技能</span>
            </div>

            <div class="sk-actions">
              <button class="sk-btn sk-btn--primary" @click="navigate('create')">
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
                创建技能
              </button>
              <button class="sk-btn" @click="navigate('discover')">
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 1.5l1.6 3.9 4.2.4-3.2 2.7.9 4.1L8 10.2l-3.5 2.4.9-4.1L2.2 5.8l4.2-.4L8 1.5z" stroke-width="1.3" stroke-linejoin="round" /></svg>
                发现技能
              </button>
              <button class="sk-btn" @click="navigate('upload')">
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 11V3M5 6l3-3 3 3" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 12.5h10" stroke-width="1.7" stroke-linecap="round" /></svg>
                上传技能包
              </button>
            </div>
          </div>
        </template>

        <!-- 视图主体：列表视图用 v-show 常驻（永不卸载）——进详情再返回时滚动位置与搜索/筛选状态由 DOM 原样保留；其余视图按需挂载 -->
        <div class="sk-body">
          <SkillListView
            v-show="currentView === 'list'"
            :skills="skills"
            :loading="loading"
            :my-user-id="myUserId"
            :is-admin="isAdmin"
            :highlight-key="highlightKey"
            @use="onUse"
            @detail="onDetail"
            @download="onDownload"
            @remove="onRemove"
            @toggle="onToggle"
            @refresh="loadSkills"
            @create="navigate('create')"
          />
          <SkillDetailView
            v-if="currentView === 'detail' && selectedSkill"
            :skill="selectedSkill"
            :my-user-id="myUserId"
            :is-admin="isAdmin"
            @changed="onDetailChanged"
            @use="onUse"
            @ai-edit="onAiEdit"
            @download="onDownload"
            @remove="onRemove"
          />
          <SkillCreateView v-else-if="currentView === 'create'" @fill="onFill" />
          <SkillDiscoverView v-else-if="currentView === 'discover'" @fill="onFill" />
          <SkillUploadView v-else-if="currentView === 'upload'" @uploaded="onUploaded" />
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* 层级须低于 naive-ui teleport 层（popover/popconfirm/tooltip/message 基准 2000+），
   否则抽屉内的气泡确认框会被抽屉自身挡住 */
.sk-mask {
  position: fixed;
  inset: 0;
  z-index: 1500;
  background: rgba(15, 23, 42, 0.18);
  backdrop-filter: blur(2px);
}
.sk-panel {
  position: fixed;
  top: 0;
  right: 0;
  z-index: 1501;
  width: min(1120px, 96vw);
  height: 100dvh;
  display: flex;
  flex-direction: column;
  background: var(--paper, #f5f7fb);
  background-image: linear-gradient(var(--paper, #f5f7fb), var(--paper-deep, #eaf0f9));
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border-left: 1px solid rgba(30, 64, 175, 0.12);
  box-shadow:
    -24px 0 64px -24px rgba(15, 23, 42, 0.24),
    inset 1px 0 0 rgba(255, 255, 255, 0.95);
}

/* 过渡 */
.sk-mask-enter-active,
.sk-mask-leave-active {
  transition: opacity 0.3s ease;
}
.sk-mask-enter-from,
.sk-mask-leave-to {
  opacity: 0;
}
.sk-panel-enter-active {
  transition: transform 0.42s cubic-bezier(0.22, 1, 0.36, 1);
}
.sk-panel-leave-active {
  transition: transform 0.28s cubic-bezier(0.4, 0, 1, 1);
}
.sk-panel-enter-from,
.sk-panel-leave-to {
  transform: translateX(100%);
}

/* ── 头部 ─────────────────────────────────────────── */
.sk-head {
  position: relative;
  flex-shrink: 0;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 22px 26px 16px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.1);
  overflow: hidden;
  background: linear-gradient(160deg, rgba(255, 255, 255, 0.6), rgba(255, 255, 255, 0.25));
}
/* 点阵纹理 */
.sk-head-dots {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 240px;
  background-image: radial-gradient(circle, rgba(30, 64, 175, 0.14) 1px, transparent 1px);
  background-size: 14px 14px;
  -webkit-mask-image: linear-gradient(to left, rgba(0, 0, 0, 0.7), transparent);
  mask-image: linear-gradient(to left, rgba(0, 0, 0, 0.7), transparent);
  pointer-events: none;
}
/* 幽灵水印大字 */
.sk-head-ghost {
  position: absolute;
  top: -34px;
  right: 18px;
  font-family: var(--font-display, 'Plus Jakarta Sans', sans-serif);
  font-size: 128px;
  font-weight: 800;
  letter-spacing: -0.04em;
  color: rgba(30, 64, 175, 0.06);
  pointer-events: none;
  user-select: none;
  line-height: 1;
}
.sk-head-main {
  position: relative;
  flex: 1;
  min-width: 0;
}
.sk-head-eyebrow-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.sk-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--accent, #1e40af);
  background: rgba(30, 64, 175, 0.08);
  border: 1px solid rgba(30, 64, 175, 0.16);
  border-radius: 7px;
  padding: 3px 10px 3px 7px;
  cursor: pointer;
  transition: background 0.15s;
}
.sk-back:hover {
  background: rgba(30, 64, 175, 0.15);
}
.sk-eyebrow {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.2em;
  color: var(--gold, #0891b2);
}
/* 列表视图：eyebrow 即主标题「技能库」，字号加大 */
.sk-eyebrow--lg {
  font-size: 26px;
  letter-spacing: 0.1em;
}
.sk-eyebrow-line {
  flex: 1;
  max-width: 60px;
  height: 1px;
  background: linear-gradient(90deg, rgba(8, 145, 178, 0.4), transparent);
}
.sk-head-title {
  margin: 0;
  font-family: var(--font-display, 'Plus Jakarta Sans', sans-serif);
  font-size: 26px;
  font-weight: 800;
  letter-spacing: -0.02em;
  line-height: 1.1;
  background: linear-gradient(110deg, #1e40af 0%, #0891b2 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
}
.sk-close {
  position: relative;
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ink-4, #94a3b8);
  background: rgba(255, 255, 255, 0.5);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 9px;
  cursor: pointer;
  transition: all 0.15s;
}
.sk-close:hover {
  background: #fff;
  color: var(--ink, #0f172a);
  border-color: rgba(30, 64, 175, 0.24);
  transform: rotate(90deg);
}

/* ── 统计条（左侧统计 + 右侧操作按钮） ─────────────── */
.sk-stats {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px 26px;
  padding: 16px 26px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
}
.sk-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.sk-stat-num {
  font-family: var(--font-display, 'Plus Jakarta Sans', sans-serif);
  font-size: 30px;
  font-weight: 800;
  line-height: 1;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  color: var(--ink, #0f172a);
}
.sk-stat-num--on {
  background: linear-gradient(135deg, #059669, #34d399);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
}
.sk-stat-num--mine {
  background: linear-gradient(135deg, #1e40af, #0891b2);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
}
.sk-stat-label {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ink-4, #94a3b8);
}
.sk-stat-sep {
  width: 1px;
  height: 34px;
  background: rgba(30, 64, 175, 0.1);
}

/* ── 操作按钮（统计条右侧） ─────────────────────────── */
.sk-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 9px;
}
.sk-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 15px;
  border-radius: 9px;
  border: 1px solid rgba(30, 64, 175, 0.15);
  background: rgba(255, 255, 255, 0.6);
  color: var(--ink-2, #334155);
  cursor: pointer;
  transition: all 0.16s;
}
.sk-btn:hover:not(:disabled) {
  background: #fff;
  border-color: rgba(30, 64, 175, 0.28);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px -4px rgba(30, 64, 175, 0.2);
}
.sk-btn:disabled {
  opacity: 0.65;
  cursor: not-allowed;
}
.sk-btn--primary {
  background: linear-gradient(135deg, #2563eb, #1e40af);
  border-color: transparent;
  color: #fff;
  box-shadow: 0 4px 14px -4px rgba(37, 99, 235, 0.45);
}
.sk-btn--primary:hover:not(:disabled) {
  background: linear-gradient(135deg, #1d4ed8, #1e3a8a);
  box-shadow: 0 6px 18px -4px rgba(37, 99, 235, 0.55);
}
.sk-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
</style>
