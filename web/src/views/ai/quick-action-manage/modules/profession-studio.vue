<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { NInput, NPopconfirm, NSpin, useMessage } from 'naive-ui';
import SvgIcon from '@/components/custom/svg-icon.vue';
import {
  type Profession,
  type QuickAction,
  type QuickActionGroup,
  fetchCreateProfession,
  fetchDeleteProfession,
  fetchProfessions,
  fetchQuickActions,
  fetchSortProfessions,
  fetchUpdateProfession
} from '@/service/api';

const emit = defineEmits<{
  /** 跳转到功能库工作区 */
  gotoActions: [];
}>();

const message = useMessage();

const loading = ref(false);
const saving = ref(false);
const professions = ref<Profession[]>([]);
const allActions = ref<QuickAction[]>([]);
const allGroups = ref<QuickActionGroup[]>([]);

/* ── 编辑器表单 ── */
interface ProfessionForm {
  id: number; // 0 = 新建草稿
  name: string;
  icon: string;
  description: string;
  recommendedActionIds: number[];
  isEnabled: number;
}

function emptyForm(): ProfessionForm {
  return { id: 0, name: '', icon: '', description: '', recommendedActionIds: [], isEnabled: 1 };
}

const selectedId = ref<number | null>(null); // null=未选中；0=新建草稿
const form = ref<ProfessionForm>(emptyForm());
const snapshot = ref(JSON.stringify(emptyForm()));
/** 脏状态下点击其他职业时暂存目标，待用户确认 */
const pendingSwitch = ref<number | null>(null);
const pickerQuery = ref('');

const dirty = computed(() => JSON.stringify(form.value) !== snapshot.value);
const valid = computed(() => Boolean(form.value.name.trim()));
const canSave = computed(() => valid.value && dirty.value && !saving.value);

const selectedProfession = computed(() => professions.value.find(p => p.id === selectedId.value) || null);

const stats = computed(() => ({
  total: professions.value.length,
  enabled: professions.value.filter(p => p.isEnabled !== 0).length,
  users: professions.value.reduce((s, p) => s + (p.userCount ?? 0), 0)
}));

const recSet = computed(() => new Set(form.value.recommendedActionIds));

/** 推荐集中已失效的功能 id（停用 / 删除后不在启用列表里） */
const missingRecIds = computed(() => {
  const alive = new Set(allActions.value.map(a => a.id));
  return form.value.recommendedActionIds.filter(id => !alive.has(id));
});

/** 勾选器分组：与引导弹窗第二步一致的橱窗章节结构，支持搜索过滤 */
const pickerGroups = computed(() => {
  const q = pickerQuery.value.trim().toLowerCase();
  const match = (a: QuickAction) =>
    !q ||
    a.name.toLowerCase().includes(q) ||
    (a.description || '').toLowerCase().includes(q) ||
    (a.skillKey || '').toLowerCase().includes(q);
  const byId = new Map(allActions.value.map(a => [a.id, a]));
  const list: Array<{ cat: string; actions: QuickAction[] }> = [];
  const grouped = new Set<number>();
  for (const g of allGroups.value) {
    const members = g.actionIds
      .map(id => byId.get(id))
      .filter((a): a is QuickAction => Boolean(a))
      .filter(match);
    if (!members.length) continue;
    list.push({ cat: g.name, actions: members });
    g.actionIds.forEach(id => grouped.add(id));
  }
  const rest = allActions.value.filter(a => !grouped.has(a.id)).filter(match);
  if (rest.length) list.push({ cat: '更多能力', actions: rest });
  return list;
});

/* ── 数据加载 ── */
async function loadProfessions() {
  const { data, error } = await fetchProfessions();
  if (!error && data) professions.value = data;
}

async function loadActions() {
  const { data, error } = await fetchQuickActions();
  if (!error && data) {
    allActions.value = data.actions;
    allGroups.value = data.groups;
  }
}

/* ── 选中 / 切换 ── */
function selectProfession(id: number) {
  selectedId.value = id;
  if (id === 0) {
    form.value = emptyForm();
  } else {
    const p = professions.value.find(x => x.id === id);
    form.value = p
      ? {
          id: p.id,
          name: p.name,
          icon: p.icon || '',
          description: p.description || '',
          recommendedActionIds: [...p.recommendedActionIds],
          isEnabled: p.isEnabled ?? 1
        }
      : emptyForm();
  }
  snapshot.value = JSON.stringify(form.value);
  pendingSwitch.value = null;
  pickerQuery.value = '';
}

function clickProfession(id: number) {
  if (id === selectedId.value) return;
  if (dirty.value) {
    pendingSwitch.value = id;
    return;
  }
  selectProfession(id);
}

function confirmSwitch() {
  if (pendingSwitch.value !== null) selectProfession(pendingSwitch.value);
}

function cancelSwitch() {
  pendingSwitch.value = null;
}

function createDraft() {
  if (selectedId.value === 0) return;
  if (dirty.value) {
    pendingSwitch.value = 0;
    return;
  }
  selectProfession(0);
}

function restore() {
  form.value = JSON.parse(snapshot.value) as ProfessionForm;
}

/* ── 保存 / 启停 / 删除 ── */
async function saveProfession() {
  if (!canSave.value) return;
  saving.value = true;
  try {
    const payload = {
      name: form.value.name.trim(),
      icon: form.value.icon.trim() || undefined,
      description: form.value.description.trim() || undefined,
      recommendedActionIds: [...form.value.recommendedActionIds]
    };
    if (form.value.id === 0) {
      const { data, error } = await fetchCreateProfession({
        ...payload,
        sortOrder: professions.value.length,
        isEnabled: form.value.isEnabled
      });
      if (error) return;
      message.success(`职业「${payload.name}」已创建`);
      await loadProfessions();
      if (data) selectProfession(data.id);
    } else {
      const { error } = await fetchUpdateProfession(form.value.id, payload);
      if (error) return;
      message.success('已保存');
      await loadProfessions();
      selectProfession(form.value.id);
    }
  } finally {
    saving.value = false;
  }
}

async function toggleEnabled(p: Profession) {
  const next = p.isEnabled === 0 ? 1 : 0;
  const { error } = await fetchUpdateProfession(p.id, { isEnabled: next });
  if (error) return;
  message.success(next === 1 ? `已启用「${p.name}」` : `已停用「${p.name}」，引导弹窗不再展示`);
  await loadProfessions();
  if (form.value.id === p.id) {
    form.value.isEnabled = next;
    snapshot.value = JSON.stringify(form.value);
  }
}

async function removeProfession(p: Profession) {
  const { error } = await fetchDeleteProfession(p.id);
  if (error) return;
  message.success(`已删除「${p.name}」，已选该职业的用户仅清空职业标记，订阅功能保留`);
  if (selectedId.value === p.id) selectedId.value = null;
  await loadProfessions();
  if (selectedId.value === null && professions.value.length > 0) {
    selectProfession(professions.value[0].id);
  }
}

/* ── 拖拽排序（左列徽章卡） ── */
const dragIdx = ref<number | null>(null);
const dragOverIdx = ref<number | null>(null);

async function onProfDragEnd() {
  const from = dragIdx.value;
  const to = dragOverIdx.value;
  dragIdx.value = null;
  dragOverIdx.value = null;
  if (from === null || to === null || from === to) return;
  const ids = professions.value.map(p => p.id);
  const [moved] = ids.splice(from, 1);
  ids.splice(to, 0, moved);
  const { error } = await fetchSortProfessions(ids);
  if (!error) {
    message.success('排序已保存');
    await loadProfessions();
  }
}

/* ── 推荐功能勾选 ── */
function toggleRec(id: number) {
  const has = form.value.recommendedActionIds.includes(id);
  form.value.recommendedActionIds = has
    ? form.value.recommendedActionIds.filter(x => x !== id)
    : [...form.value.recommendedActionIds, id];
}

function clearRec() {
  form.value.recommendedActionIds = [];
}

/** 供外壳跨工作台导航调用：选中指定职业（有未保存修改时走既有确认条，不静默丢改动） */
function openProfession(id: number) {
  if (selectedId.value === id) return;
  if (dirty.value) {
    pendingSwitch.value = id;
    return;
  }
  selectProfession(id);
}

defineExpose({ openProfession });

onMounted(async () => {
  loading.value = true;
  try {
    await Promise.all([loadProfessions(), loadActions()]);
  } finally {
    loading.value = false;
  }
  if (professions.value.length > 0) selectProfession(professions.value[0].id);
});
</script>

<template>
  <div class="pfm-root">
    <!-- ========== 左侧栏：职业徽章列表 ========== -->
    <aside class="pfm-sidebar">
      <header class="pfm-sidebar-head">
        <div class="pfm-head-left">
          <div class="brand-mark">
            <span class="bm-glyph">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="5" width="18" height="14" rx="2" />
                <circle cx="9" cy="11" r="2" />
                <path d="M6 16c.5-1.6 1.6-2.4 3-2.4s2.5.8 3 2.4" />
                <path d="M14.5 9.5H18M14.5 13H18" />
              </svg>
            </span>
            <span class="bm-halo" />
          </div>
          <div class="brand-text">
            <span class="brand-zh">职业体系</span>
            <span class="brand-en">PROFESSIONS</span>
          </div>
        </div>
        <button class="glass-btn glass-btn--primary glass-btn--sm" title="新建职业" @click="createDraft">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M8 3v10M3 8h10" stroke-linecap="round" />
          </svg>
          <span>新建</span>
        </button>
      </header>

      <!-- 统计摘要 -->
      <div v-if="professions.length > 0" class="pfm-stats-bar">
        <div class="pfm-stat">
          <span class="pfm-stat-val">{{ stats.total }}</span>
          <span class="pfm-stat-label">职业</span>
        </div>
        <div class="pfm-stat-divider" />
        <div class="pfm-stat">
          <span class="pfm-stat-val">{{ stats.enabled }}</span>
          <span class="pfm-stat-label">启用</span>
        </div>
        <div class="pfm-stat-divider" />
        <div class="pfm-stat">
          <span class="pfm-stat-val">{{ stats.users }}</span>
          <span class="pfm-stat-label">已选用户</span>
        </div>
      </div>

      <NSpin v-if="loading && professions.length === 0" size="medium" class="pfm-sidebar-spin" />

      <nav v-else-if="professions.length > 0" class="pfm-sidebar-nav">
        <p class="pfm-nav-hint">拖拽徽章卡调整引导弹窗中的展示顺序</p>
        <button
          v-for="(p, idx) in professions"
          :key="p.id"
          type="button"
          class="pfm-card"
          :class="{
            'is-active': selectedId === p.id,
            'is-off': p.isEnabled === 0,
            'is-draft-anchor': selectedId === 0 && idx === 0 && false,
            'is-dragging': dragIdx === idx,
            'is-drop-target': dragOverIdx === idx && dragIdx !== null && dragIdx !== idx
          }"
          draggable="true"
          @dragstart="dragIdx = idx"
          @dragover="dragIdx !== null && dragIdx !== idx && ((dragOverIdx = idx), ($event.preventDefault()))"
          @dragend="onProfDragEnd"
          @click="clickProfession(p.id)"
        >
          <span class="pfm-drag" aria-hidden="true">
            <svg width="9" height="13" viewBox="0 0 10 14" fill="currentColor">
              <circle cx="3" cy="2" r="1.2" /><circle cx="7" cy="2" r="1.2" />
              <circle cx="3" cy="7" r="1.2" /><circle cx="7" cy="7" r="1.2" />
              <circle cx="3" cy="12" r="1.2" /><circle cx="7" cy="12" r="1.2" />
            </svg>
          </span>
          <span class="pfm-card-tile">
            <SvgIcon :icon="p.icon || 'mdi:account-outline'" class="pfm-card-icon" />
          </span>
          <span class="pfm-card-body">
            <span class="pfm-card-name">
              {{ p.name }}
              <span v-if="p.isEnabled === 0" class="pfm-off-badge">停</span>
            </span>
            <span class="pfm-card-desc">{{ p.description || '暂无描述' }}</span>
            <span class="pfm-card-meta">
              <span class="pfm-chip pfm-chip--rec">{{ p.recommendedActionIds.length }} 项推荐</span>
              <span class="pfm-chip pfm-chip--user">{{ p.userCount ?? 0 }} 用户</span>
            </span>
          </span>
        </button>
      </nav>

      <div v-else class="pfm-sidebar-empty">
        <div class="empty-orb" />
        <p class="pfm-empty-title">还没有职业</p>
        <p class="pfm-empty-hint">新用户进入问答页时需先选择职业</p>
        <button class="glass-btn glass-btn--primary" @click="createDraft">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M8 3v10M3 8h10" stroke-linecap="round" />
          </svg>
          <span>创建第一个</span>
        </button>
      </div>
    </aside>

    <!-- ========== 主内容区 ========== -->
    <main class="pfm-main">
      <div v-if="selectedId === null" class="pfm-placeholder">
        <div class="placeholder-orb" />
        <p class="placeholder-title">从左侧选择一个职业进行编辑</p>
        <p class="placeholder-hint">职业决定新用户引导弹窗的推荐功能集</p>
      </div>

      <div v-else :key="selectedId" class="pfm-editor">
        <!-- 编辑器头部 -->
        <header class="pfm-editor-head">
          <div class="pfm-editor-tile">
            <SvgIcon :icon="form.icon || 'mdi:account-outline'" class="pfm-editor-icon" />
          </div>
          <div class="pfm-editor-info">
            <h1 class="pfm-editor-title">
              {{ selectedId === 0 ? '新建职业' : form.name || '未命名职业' }}
              <span v-if="dirty" class="pfm-dirty-chip">未保存</span>
            </h1>
            <p class="pfm-editor-sub">
              <template v-if="selectedId === 0">DRAFT · 创建后展示在新手引导第一步</template>
              <template v-else>
                PROFESSION #{{ form.id }} · {{ selectedProfession?.userCount ?? 0 }} 位用户已选择
              </template>
            </p>
          </div>
          <div v-if="selectedProfession" class="pfm-editor-ops">
            <button
              class="glass-btn glass-btn--icon pfm-power"
              :class="{ 'is-off': selectedProfession.isEnabled === 0 }"
              :title="selectedProfession.isEnabled === 0 ? '已停用 · 点击启用（引导弹窗恢复展示）' : '停用该职业（引导弹窗隐藏，数据保留）'"
              @click="toggleEnabled(selectedProfession)"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M8 2v5.5" stroke-linecap="round" />
                <path d="M4.4 4.3a5.5 5.5 0 1 0 7.2 0" stroke-linecap="round" />
              </svg>
            </button>
            <NPopconfirm @positive-click="removeProfession(selectedProfession!)">
              <template #trigger>
                <button class="glass-btn glass-btn--icon glass-btn--danger" title="删除职业">
                  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                    <path d="M3 4h10M5.5 4V3a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1M6 7v5M10 7v5M4 4l.5 9a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1l.5-9" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                </button>
              </template>
              确定删除「{{ selectedProfession.name }}」？已选该职业的用户仅清空职业标记，订阅功能保留。
            </NPopconfirm>
          </div>
        </header>

        <!-- 脏状态切换确认条 -->
        <div v-if="pendingSwitch !== null" class="pfm-switchbar">
          <SvgIcon icon="mdi:alert-outline" class="pfm-switchbar-icon" />
          <span>有未保存的修改，切换将丢失这些更改。</span>
          <div class="pfm-switchbar-ops">
            <button class="glass-btn glass-btn--sm" @click="cancelSwitch">留下编辑</button>
            <button class="glass-btn glass-btn--sm glass-btn--danger" @click="confirmSwitch">放弃并切换</button>
          </div>
        </div>

        <!-- 基本信息 + 实时预览 -->
        <div class="pfm-cols">
          <section class="pfm-panel">
            <h3 class="pfm-panel-title">基本信息</h3>
            <div class="pfm-form-grid">
              <div class="pfm-field">
                <label class="pfm-label">职业名称 <em>*</em></label>
                <NInput v-model:value="form.name" :maxlength="32" placeholder="例：标准编制人员" />
              </div>
              <div class="pfm-field">
                <label class="pfm-label">图标</label>
                <div class="pfm-icon-input">
                  <NInput v-model:value="form.icon" placeholder="iconify 图标名，例：mdi:flask-outline" />
                  <div class="pfm-icon-preview">
                    <SvgIcon :icon="form.icon || 'mdi:account-outline'" class="pfm-icon-preview-icon" />
                  </div>
                </div>
                <span class="pfm-hint">
                  前往 <a href="https://icones.js.org" target="_blank" rel="noopener noreferrer" class="pfm-icon-link">icones.js.org</a>
                  搜索复制，格式 <code class="pfm-code">集合:图标名</code>
                </span>
              </div>
              <div class="pfm-field pfm-field--full">
                <label class="pfm-label">一句话描述</label>
                <NInput
                  v-model:value="form.description"
                  type="textarea"
                  :rows="2"
                  :maxlength="200"
                  placeholder="说明该职业的典型职责，帮助用户对号入座"
                />
              </div>
            </div>
          </section>

          <!-- 用户视角实时预览 -->
          <section class="pfm-panel pfm-panel--preview">
            <h3 class="pfm-panel-title">用户视角 · 实时预览</h3>
            <div class="pfm-preview-stage">
              <p class="pfm-preview-chrome">欢迎使用 <span>STEP 1 · 选择你的职业</span></p>
              <div class="pfm-prof-card" :class="{ 'has-content': Boolean(form.name) }">
                <span class="pfm-prof-icon">
                  <SvgIcon :icon="form.icon || 'mdi:account-outline'" />
                </span>
                <span class="pfm-prof-name">{{ form.name || '职业名称' }}</span>
                <span class="pfm-prof-desc">{{ form.description || '职业描述将展示在这里…' }}</span>
                <span class="pfm-prof-count">{{ form.recommendedActionIds.length }} 项推荐</span>
                <span class="pfm-prof-check" aria-hidden="true">
                  <SvgIcon icon="mdi:check" />
                </span>
              </div>
            </div>
            <p class="pfm-preview-hint">新用户引导弹窗第一步看到的徽章卡，随左侧输入实时更新</p>
          </section>
        </div>

        <!-- 推荐功能勾选 -->
        <section class="pfm-panel">
          <div class="pfm-panel-head">
            <h3 class="pfm-panel-title">推荐功能</h3>
            <div class="pfm-picker-tools">
              <label class="pfm-search">
                <SvgIcon icon="mdi:magnify" class="pfm-search-icon" />
                <input v-model="pickerQuery" type="text" placeholder="搜索功能…" />
              </label>
              <span class="pfm-chip pfm-chip--accent">已选 {{ form.recommendedActionIds.length }}</span>
              <button v-if="form.recommendedActionIds.length > 0" type="button" class="pfm-clear" @click="clearRec">清空</button>
            </div>
          </div>

          <p class="pfm-picker-tip">
            新用户选择该职业后，第二步会按此清单预勾选；用户可自行增删，之后这批功能出现在首屏橱窗与对话框。
            <button type="button" class="pfm-tip-link" @click="emit('gotoActions')">管理功能库 →</button>
          </p>

          <div v-if="missingRecIds.length > 0" class="pfm-missing">
            <SvgIcon icon="mdi:alert-circle-outline" class="pfm-missing-icon" />
            <span>推荐集中 {{ missingRecIds.length }} 个功能已失效（停用或删除），建议移除：</span>
            <button
              v-for="id in missingRecIds"
              :key="id"
              type="button"
              class="pfm-missing-chip"
              title="点击移除"
              @click="toggleRec(id)"
            >
              #{{ id }} ×
            </button>
          </div>

          <div v-if="pickerGroups.length > 0" class="pfm-groups">
            <section v-for="g in pickerGroups" :key="g.cat" class="pfm-group">
              <h4 class="pfm-group-title">
                {{ g.cat }}
                <span class="pfm-group-count">{{ g.actions.length }}</span>
              </h4>
              <div class="pfm-pick-grid">
                <button
                  v-for="a in g.actions"
                  :key="a.id"
                  type="button"
                  class="pfm-pick"
                  :class="{ checked: recSet.has(a.id) }"
                  @click="toggleRec(a.id)"
                >
                  <span class="pfm-pick-box" aria-hidden="true">
                    <SvgIcon v-if="recSet.has(a.id)" icon="mdi:check" />
                  </span>
                  <span class="pfm-pick-icon">
                    <SvgIcon :icon="a.icon || 'mdi:lightning-bolt'" />
                  </span>
                  <span class="pfm-pick-text">
                    <span class="pfm-pick-name">{{ a.name }}</span>
                    <span v-if="a.description" class="pfm-pick-desc">{{ a.description }}</span>
                  </span>
                </button>
              </div>
            </section>
          </div>
          <div v-else class="pfm-pick-empty">
            <p>没有匹配「{{ pickerQuery }}」的功能</p>
          </div>
        </section>

        <!-- 底部操作 -->
        <footer class="pfm-editor-foot">
          <button v-if="dirty" type="button" class="glass-btn" @click="restore">放弃修改</button>
          <span class="pfm-foot-spacer" />
          <button
            type="button"
            class="glass-btn glass-btn--primary glass-btn--lg"
            :disabled="!canSave"
            @click="saveProfession"
          >
            <svg v-if="!saving" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 8.5l3.2 3.2L13 5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <span v-else class="pfm-btn-spinner" />
            {{ saving ? '保存中…' : selectedId === 0 ? '创建职业' : '保存修改' }}
          </button>
        </footer>
      </div>
    </main>
  </div>
</template>

<style scoped>
/* ─── design tokens（与快捷功能管理页同族：蓝青玻璃拟态） ─── */
.pfm-root {
  --bg: #f5f7fb;
  --bg-deep: #eaf0f9;

  --surface: rgba(255, 255, 255, 0.42);
  --surface-strong: rgba(255, 255, 255, 0.62);
  --surface-deep: rgba(255, 255, 255, 0.78);
  --highlight: rgba(255, 255, 255, 0.95);

  --border: rgba(30, 64, 175, 0.1);
  --border-strong: rgba(30, 64, 175, 0.18);
  --border-glow: rgba(30, 64, 175, 0.25);

  --ink: #0f172a;
  --ink-soft: #334155;
  --ink-mute: #64748b;
  --ink-faint: #94a3b8;

  --c-deep: #1e3a8a;
  --c-blue: #1e40af;
  --c-blue-2: #2563eb;
  --c-sky: #0ea5e9;
  --c-cyan: #0891b2;
  --c-violet: #4f46e5;
  --c-mint: #10b981;

  --aurora: linear-gradient(110deg, var(--c-blue) 0%, var(--c-blue-2) 35%, var(--c-sky) 70%, var(--c-cyan) 100%);
  --aurora-soft: linear-gradient(110deg, rgba(30, 64, 175, 0.18) 0%, rgba(37, 99, 235, 0.16) 50%, rgba(8, 145, 178, 0.18) 100%);

  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 16px -8px rgba(30, 64, 175, 0.12);
  --shadow-md: 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px rgba(30, 64, 175, 0.18);
  --shadow-lg: 0 1px 2px rgba(15, 23, 42, 0.05), 0 24px 64px -20px rgba(30, 64, 175, 0.28);
  --shadow-glow: 0 8px 32px -10px rgba(30, 64, 175, 0.45);

  --ease: cubic-bezier(0.32, 0.72, 0, 1);

  position: relative;
  display: flex;
  height: 100%;
  background: transparent;
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, 'PingFang SC', 'Noto Sans SC', sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: var(--ink);
  overflow: hidden;
  gap: 20px;
}

/* ─── glass button（同族） ─── */
.glass-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  color: var(--ink-soft);
  cursor: pointer;
  border-radius: 11px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.005em;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
  transition: all 0.2s ease;
  white-space: nowrap;
}
.glass-btn:hover {
  color: var(--c-blue);
  border-color: var(--border-glow);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    var(--shadow-glow);
  transform: translateY(-1px);
}
.glass-btn--icon {
  width: 34px; height: 34px;
  padding: 0;
  flex-shrink: 0;
}
.glass-btn--sm {
  height: 30px;
  padding: 0 12px;
  border-radius: 10px;
  font-size: 11.5px;
}
.glass-btn--lg {
  height: 40px;
  padding: 0 22px;
  border-radius: 12px;
  font-size: 13px;
}
.glass-btn--primary {
  background: var(--aurora);
  color: #fff;
  border-color: transparent;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 4px 14px -2px rgba(30, 64, 175, 0.45);
}
.glass-btn--primary:hover {
  color: #fff;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 6px 20px -2px rgba(30, 64, 175, 0.55);
}
.glass-btn--primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}
.glass-btn--danger:hover {
  color: #ef4444;
  border-color: rgba(239, 68, 68, 0.3);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    0 8px 32px -10px rgba(239, 68, 68, 0.35);
}

.pfm-power {
  color: var(--c-blue);
  border-color: rgba(30, 64, 175, 0.22);
}
.pfm-power.is-off {
  color: var(--ink-faint);
  border-color: rgba(148, 163, 184, 0.4);
  background: rgba(148, 163, 184, 0.08);
}
.pfm-power.is-off:hover {
  color: var(--c-mint);
  border-color: rgba(16, 185, 129, 0.4);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    0 8px 32px -10px rgba(16, 185, 129, 0.35);
}

/* ─── chip ─── */
.pfm-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 9px;
  background: var(--surface-deep);
  color: var(--ink-mute);
  font-size: 10.5px;
  font-weight: 600;
  border-radius: 999px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  line-height: 1.5;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.pfm-chip--accent {
  color: #fff;
  border-color: transparent;
  background: var(--c-blue);
  font-size: 11px;
  padding: 3px 11px;
}
.pfm-chip--rec {
  color: var(--c-blue);
  background: rgba(30, 64, 175, 0.08);
  border-color: rgba(30, 64, 175, 0.16);
}
.pfm-chip--user {
  color: var(--c-cyan);
  background: rgba(8, 145, 178, 0.08);
  border-color: rgba(8, 145, 178, 0.18);
}

/* ─── orb（空态 / 占位） ─── */
.empty-orb,
.placeholder-orb {
  width: 76px; height: 76px;
  border-radius: 50%;
  background: var(--aurora);
  position: relative;
  animation: pfm-breathe 3s ease-in-out infinite;
  flex-shrink: 0;
}
.empty-orb::before,
.placeholder-orb::before {
  content: '';
  position: absolute;
  inset: -10px;
  border-radius: 50%;
  background: var(--aurora);
  filter: blur(20px);
  opacity: 0.5;
  z-index: -1;
}

@keyframes pfm-breathe {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.08); }
}

/* ─── Sidebar ─── */
.pfm-sidebar {
  position: relative;
  z-index: 2;
  width: 300px;
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 18px;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    var(--shadow-sm);
  overflow: hidden;
}

.pfm-sidebar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 16px 14px;
  border-bottom: 1px solid var(--border);
}

.pfm-head-left {
  display: flex;
  align-items: center;
  gap: 11px;
}

.brand-mark {
  position: relative;
  width: 34px; height: 34px;
  border-radius: 11px;
  background: var(--aurora);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 4px 14px -4px rgba(30, 64, 175, 0.55);
}

.bm-glyph {
  position: relative;
  z-index: 1;
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.18));
}

.bm-halo {
  position: absolute;
  inset: -2px;
  border-radius: 13px;
  background: var(--aurora);
  filter: blur(12px);
  opacity: 0.5;
  z-index: 0;
  animation: pfm-halo 3s ease-in-out infinite;
}

@keyframes pfm-halo {
  0%, 100% { opacity: 0.4; transform: scale(0.95); }
  50%      { opacity: 0.7; transform: scale(1.1); }
}

.brand-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1;
}

.brand-zh {
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.02em;
  background: var(--aurora);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.brand-en {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--ink-faint);
  text-transform: uppercase;
}

/* 统计条 */
.pfm-stats-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 11px 20px;
  border-bottom: 1px solid var(--border);
}

.pfm-stat {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.pfm-stat-val {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.pfm-stat-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ink-faint);
}

.pfm-stat-divider {
  width: 1px;
  height: 16px;
  background: var(--border);
}

.pfm-sidebar-spin {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 0;
}

.pfm-sidebar-nav {
  flex: 1;
  overflow-y: auto;
  padding: 10px 10px 14px;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}

.pfm-nav-hint {
  margin: 2px 4px 10px;
  font-size: 10.5px;
  font-weight: 500;
  color: var(--ink-faint);
  letter-spacing: 0.02em;
}

/* ─── 职业徽章卡 ─── */
.pfm-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 12px 12px 12px 8px;
  margin-bottom: 8px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 13px;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  transition: all 0.2s ease;
  position: relative;
}

.pfm-card:hover {
  background: rgba(255, 255, 255, 0.5);
}

.pfm-card.is-active {
  background: rgba(255, 255, 255, 0.82);
  border-color: var(--border-glow);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.5),
    0 4px 16px -6px rgba(30, 64, 175, 0.2);
}

.pfm-card.is-off {
  opacity: 0.62;
}
.pfm-card.is-off .pfm-card-tile {
  filter: grayscale(0.65);
}

.pfm-card.is-dragging {
  opacity: 0.4;
}
.pfm-card.is-drop-target {
  border-color: var(--c-blue-2);
  box-shadow: 0 -2px 0 0 var(--c-blue-2);
}

.pfm-drag {
  display: flex;
  align-items: center;
  padding-top: 8px;
  color: var(--ink-faint);
  opacity: 0.35;
  flex-shrink: 0;
  cursor: grab;
  transition: opacity 0.15s ease;
}
.pfm-card:hover .pfm-drag {
  opacity: 0.9;
}

.pfm-card-tile {
  width: 40px; height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--aurora-soft);
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 11px;
  flex-shrink: 0;
  transition: transform 0.2s var(--ease), background 0.2s ease;
}

.pfm-card:hover .pfm-card-tile {
  transform: scale(1.06);
}

.pfm-card.is-active .pfm-card-tile {
  background: var(--aurora);
  border-color: transparent;
  box-shadow: 0 4px 12px -4px rgba(30, 64, 175, 0.5);
}

.pfm-card-icon {
  font-size: 20px;
  color: var(--c-blue);
  transition: color 0.2s ease;
}

.pfm-card.is-active .pfm-card-icon {
  color: #fff;
}

.pfm-card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.pfm-card-name {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--ink);
  letter-spacing: -0.005em;
  display: flex;
  align-items: center;
  gap: 6px;
  line-height: 1.35;
}

.pfm-card.is-active .pfm-card-name {
  color: var(--c-blue);
}

.pfm-off-badge {
  font-size: 9.5px;
  font-weight: 700;
  color: #b45309;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.25);
  border-radius: 5px;
  padding: 0 4px;
  line-height: 1.5;
  flex-shrink: 0;
}

.pfm-card-desc {
  font-size: 11.5px;
  color: var(--ink-mute);
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.pfm-card-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 2px;
}

/* 空侧栏 */
.pfm-sidebar-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 20px;
  gap: 10px;
}

.pfm-empty-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
  letter-spacing: -0.01em;
  margin: 6px 0 0;
}

.pfm-empty-hint {
  font-size: 12px;
  color: var(--ink-mute);
  margin: 0 0 6px;
}

/* ─── Main ─── */
.pfm-main {
  position: relative;
  z-index: 1;
  flex: 1;
  overflow-y: auto;
  border-radius: 18px;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}

.pfm-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 12px;
}

.placeholder-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--ink);
  margin: 10px 0 0;
  letter-spacing: -0.01em;
}

.placeholder-hint {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-mute);
  margin: 0;
}

/* ─── Editor ─── */
.pfm-editor {
  max-width: 920px;
  margin: 0 auto;
  padding: 4px 28px 32px;
  animation: pfm-rise 0.28s var(--ease);
}

@keyframes pfm-rise {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: translateY(0); }
}

.pfm-editor-head {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 24px 26px;
  background: var(--surface-strong);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 18px;
  margin-bottom: 14px;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    var(--shadow-md);
}

.pfm-editor-tile {
  width: 56px; height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--aurora);
  border-radius: 15px;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 6px 20px -6px rgba(30, 64, 175, 0.4);
}

.pfm-editor-icon {
  font-size: 27px;
  color: #fff;
}

.pfm-editor-info {
  flex: 1;
  min-width: 0;
}

.pfm-editor-title {
  margin: 0 0 4px;
  font-size: 21px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.25;
  color: var(--ink);
  display: flex;
  align-items: center;
  gap: 10px;
}

.pfm-dirty-chip {
  font-size: 10px;
  font-weight: 700;
  color: #b45309;
  background: rgba(245, 158, 11, 0.14);
  border: 1px solid rgba(245, 158, 11, 0.3);
  border-radius: 999px;
  padding: 2px 9px;
  line-height: 1.4;
  animation: pfm-pulse 1.6s ease-in-out infinite;
}

@keyframes pfm-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.55; }
}

.pfm-editor-sub {
  margin: 0;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  color: var(--ink-faint);
  text-transform: uppercase;
}

.pfm-editor-ops {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
  align-items: center;
}

/* 切换确认条 */
.pfm-switchbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  margin-bottom: 14px;
  border-radius: 13px;
  border: 1px solid rgba(245, 158, 11, 0.32);
  background: linear-gradient(160deg, rgba(245, 158, 11, 0.12), rgba(245, 158, 11, 0.05));
  font-size: 12.5px;
  font-weight: 600;
  color: #92400e;
  animation: pfm-rise 0.22s var(--ease);
}

.pfm-switchbar-icon {
  font-size: 17px;
  flex-shrink: 0;
}

.pfm-switchbar-ops {
  margin-left: auto;
  display: flex;
  gap: 8px;
}

/* 双列：基本信息 + 预览 */
.pfm-cols {
  display: grid;
  grid-template-columns: 1.12fr 0.88fr;
  gap: 14px;
  margin-bottom: 14px;
}

/* 面板 */
.pfm-panel {
  background: var(--surface-strong);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 18px 22px 20px;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    var(--shadow-sm);
}

.pfm-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.pfm-panel-title {
  margin: 0 0 14px;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10.5px;
  font-weight: 700;
  color: var(--ink-faint);
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.pfm-panel-head .pfm-panel-title {
  margin-bottom: 0;
}

/* 表单 */
.pfm-form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px 16px;
}

.pfm-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.pfm-field--full {
  grid-column: 1 / -1;
}

.pfm-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-soft);
}

.pfm-label em {
  color: #ef4444;
  font-style: normal;
}

.pfm-hint {
  font-size: 11.5px;
  color: var(--ink-faint);
  line-height: 1.5;
}

.pfm-icon-link,
.pfm-icon-link:visited {
  color: var(--c-blue-2);
  text-decoration: underline;
  text-underline-offset: 2px;
  font-weight: 600;
  transition: color 0.15s ease;
}

.pfm-icon-link:hover {
  color: var(--c-sky);
}

.pfm-code {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10.5px;
  padding: 1px 5px;
  border-radius: 7px;
  background: rgba(30, 64, 175, 0.08);
  color: var(--c-blue);
  font-weight: 600;
}

.pfm-icon-input {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pfm-icon-preview {
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--aurora);
  border-radius: 10px;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 2px 8px -2px rgba(30, 64, 175, 0.4);
  transition: transform 0.2s var(--ease);
}

.pfm-icon-preview:hover {
  transform: scale(1.08) rotate(-3deg);
}

.pfm-icon-preview-icon {
  font-size: 21px;
  color: #fff;
}

/* ─── 实时预览舞台 ─── */
.pfm-panel--preview {
  display: flex;
  flex-direction: column;
}

.pfm-preview-stage {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 12px;
  padding: 18px 16px;
  border-radius: 13px;
  border: 1.5px dashed rgba(30, 64, 175, 0.2);
  background:
    radial-gradient(circle at 20% 0%, rgba(37, 99, 235, 0.07), transparent 55%),
    radial-gradient(circle at 90% 100%, rgba(8, 145, 178, 0.08), transparent 55%),
    rgba(255, 255, 255, 0.35);
}

.pfm-preview-chrome {
  margin: 0;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink);
}

.pfm-preview-chrome span {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.12em;
  color: var(--ink-mute);
}

/* 预览徽章卡（镜像 OnboardingModal 的 obm-prof） */
.pfm-prof-card {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  padding: 18px;
  border: 1px solid rgba(30, 64, 175, 0.16);
  border-radius: 16px;
  background: var(--surface-deep);
  box-shadow:
    inset 0 0 0 1px rgba(255, 255, 255, 0.5),
    0 10px 28px -14px rgba(30, 64, 175, 0.35);
  transition: all 0.25s var(--ease);
  opacity: 0.72;
}

.pfm-prof-card.has-content {
  opacity: 1;
  border-color: var(--c-blue-2);
  background: linear-gradient(160deg, rgba(30, 64, 175, 0.1), rgba(14, 165, 233, 0.06));
  box-shadow:
    inset 0 0 0 1px rgba(37, 99, 235, 0.4),
    0 10px 28px -14px rgba(30, 64, 175, 0.5);
}

.pfm-prof-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: var(--aurora);
  color: #fff;
  font-size: 22px;
  box-shadow: 0 4px 12px -4px rgba(30, 64, 175, 0.5);
}

.pfm-prof-name {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
}

.pfm-prof-desc {
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink-mute);
}

.pfm-prof-count {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--c-blue);
  padding: 2px 8px;
  border-radius: 99px;
  background: rgba(30, 64, 175, 0.08);
}

.pfm-prof-check {
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
}

.pfm-preview-hint {
  margin: 10px 0 0;
  font-size: 11px;
  color: var(--ink-faint);
  text-align: center;
}

/* ─── 推荐功能勾选器 ─── */
.pfm-picker-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pfm-search {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--surface-deep);
  transition: all 0.18s ease;
}

.pfm-search:focus-within {
  border-color: var(--c-blue-2);
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.14);
}

.pfm-search-icon {
  font-size: 15px;
  color: var(--ink-faint);
  flex-shrink: 0;
}

.pfm-search input {
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  color: var(--ink);
  width: 130px;
}

.pfm-search input::placeholder {
  color: var(--ink-faint);
}

.pfm-clear {
  border: none;
  background: transparent;
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-faint);
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 8px;
  transition: all 0.15s ease;
}

.pfm-clear:hover {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.08);
}

.pfm-picker-tip {
  margin: 12px 0 0;
  padding: 9px 12px;
  border-radius: 11px;
  background: rgba(30, 64, 175, 0.05);
  border: 1px solid rgba(30, 64, 175, 0.1);
  font-size: 12px;
  line-height: 1.55;
  color: var(--ink-soft);
}

.pfm-missing {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
  padding: 9px 12px;
  border-radius: 11px;
  background: rgba(245, 158, 11, 0.09);
  border: 1px solid rgba(245, 158, 11, 0.28);
  font-size: 12px;
  font-weight: 600;
  color: #92400e;
}

.pfm-missing-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.pfm-missing-chip {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10.5px;
  font-weight: 700;
  color: #b45309;
  background: rgba(245, 158, 11, 0.16);
  border: 1px solid rgba(245, 158, 11, 0.35);
  border-radius: 999px;
  padding: 2px 9px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.pfm-missing-chip:hover {
  background: rgba(239, 68, 68, 0.14);
  border-color: rgba(239, 68, 68, 0.4);
  color: #dc2626;
}

.pfm-groups {
  margin-top: 6px;
}

.pfm-group {
  margin-top: 14px;
}

.pfm-group-title {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--ink-soft);
  display: flex;
  align-items: center;
  gap: 7px;
}

.pfm-group-count {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10px;
  font-weight: 700;
  color: var(--ink-faint);
  background: rgba(30, 64, 175, 0.07);
  border-radius: 999px;
  padding: 1px 7px;
  font-variant-numeric: tabular-nums;
}

.pfm-pick-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

.pfm-pick {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 12px;
  background: var(--surface-deep);
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.16s ease;
}

.pfm-pick:hover {
  border-color: rgba(30, 64, 175, 0.28);
  transform: translateY(-1px);
}

.pfm-pick.checked {
  border-color: var(--c-blue-2);
  background: linear-gradient(160deg, rgba(30, 64, 175, 0.08), rgba(14, 165, 233, 0.04));
  box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.25);
}

.pfm-pick-box {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  margin-top: 1px;
  border: 1.5px solid rgba(30, 64, 175, 0.3);
  border-radius: 6px;
  color: #fff;
  font-size: 13px;
  flex-shrink: 0;
  transition: all 0.16s ease;
}

.pfm-pick.checked .pfm-pick-box {
  background: var(--aurora);
  border-color: transparent;
}

.pfm-pick-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: rgba(30, 64, 175, 0.08);
  color: var(--c-blue);
  font-size: 16px;
  flex-shrink: 0;
}

.pfm-pick-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.pfm-pick-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.pfm-pick-desc {
  font-size: 11px;
  line-height: 1.4;
  color: var(--ink-mute);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.pfm-pick-empty {
  margin-top: 16px;
  padding: 26px 16px;
  text-align: center;
  border: 1px dashed var(--border);
  border-radius: 12px;
  font-size: 12.5px;
  color: var(--ink-faint);
}

.pfm-pick-empty p {
  margin: 0;
}

/* 底部操作 */
.pfm-editor-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 16px;
  padding: 14px 20px;
  border-radius: 15px;
  background: var(--surface-strong);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    var(--shadow-sm);
}

.pfm-foot-spacer {
  flex: 1;
}

.pfm-btn-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.35);
  border-top-color: #fff;
  border-radius: 50%;
  animation: pfm-spin 0.7s linear infinite;
}

@keyframes pfm-spin {
  to { transform: rotate(360deg); }
}

/* ─── Responsive ─── */
@media (max-width: 1000px) {
  .pfm-cols {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 900px) {
  .pfm-root { padding: 12px 16px 16px; gap: 14px; }
  .pfm-sidebar { width: 240px; }
  .pfm-editor { padding: 4px 14px 24px; }
}

@media (max-width: 640px) {
  .pfm-root { flex-direction: column; max-width: 100%; padding: 10px 12px 12px; gap: 10px; }
  .pfm-sidebar { width: 100%; max-height: 42vh; }
  .brand-text { display: none; }
  .pfm-form-grid,
  .pfm-pick-grid { grid-template-columns: 1fr; }
}

@media (prefers-reduced-motion: reduce) {
  .bm-halo, .empty-orb, .placeholder-orb, .pfm-dirty-chip { animation: none !important; }
}

/* ─── 功能库互跳链接 ─── */
.pfm-tip-link {
  border: none;
  background: transparent;
  padding: 0;
  margin-left: 6px;
  font-family: inherit;
  font-size: inherit;
  font-weight: 700;
  color: var(--c-blue-2);
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
  transition: color 0.15s ease;
}

.pfm-tip-link:hover {
  color: var(--c-cyan);
}
</style>
