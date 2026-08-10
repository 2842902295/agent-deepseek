<script setup lang="ts">
import { computed, ref } from 'vue';
import { NPopover } from 'naive-ui';
import type { AgentSkill } from '@/service/api';
import SkillCard from './SkillCard.vue';

const props = defineProps<{
  skills: AgentSkill[];
  loading: boolean;
  myUserId: number | null;
  isAdmin: boolean;
  highlightKey?: string | null;
}>();

const emit = defineEmits<{
  use: [skill: AgentSkill];
  detail: [skill: AgentSkill];
  download: [skill: AgentSkill];
  remove: [skill: AgentSkill];
  toggle: [skill: AgentSkill];
  refresh: [];
  create: [];
}>();

const search = ref('');
const filterSource = ref<string>('all');
const filterVisibility = ref<string>('all');
const filterTag = ref<string>('all');

const sourceChips: Array<{ value: string; label: string; dot: string }> = [
  { value: 'official', label: '官方', dot: '#d97706' },
  { value: 'derived', label: '凝练', dot: '#0891b2' },
  { value: 'curated', label: '收录', dot: '#0ea5e9' }
];
const visChips: Array<{ value: string; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'private', label: '仅自己' },
  { value: 'role', label: '指定角色' },
  { value: 'public', label: '全员' }
];
// 「指定角色」筛选只对超管/管理员有意义
const visibleVisChips = computed(() => (props.isAdmin ? visChips : visChips.filter(c => c.value !== 'role')));

const tagOptions = computed(() => {
  const map = new Map<string, number>();
  for (const s of props.skills) for (const t of s.tags || []) map.set(t, (map.get(t) || 0) + 1);
  return Array.from(map, ([label, count]) => ({ label, count })).sort((a, b) => a.label.localeCompare(b.label));
});
const sourceCounts = computed(() => {
  const m: Record<string, number> = {};
  for (const s of props.skills) m[s.source] = (m[s.source] || 0) + 1;
  return m;
});
const visCounts = computed(() => {
  const m: Record<string, number> = {};
  for (const s of props.skills) m[s.visibility] = (m[s.visibility] || 0) + 1;
  return m;
});

function isMine(s: AgentSkill): boolean {
  return props.myUserId != null && s.userId === props.myUserId;
}

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase();
  return props.skills.filter(s => {
    const matchSearch =
      !q ||
      s.name.toLowerCase().includes(q) ||
      s.skillKey.toLowerCase().includes(q) ||
      (s.description || '').toLowerCase().includes(q);
    const matchSource = filterSource.value === 'all' || s.source === filterSource.value;
    const matchVis = filterVisibility.value === 'all' || s.visibility === filterVisibility.value;
    const matchTag = filterTag.value === 'all' || (s.tags || []).includes(filterTag.value);
    return matchSearch && matchSource && matchVis && matchTag;
  });
});

// 官方技能独立一组置顶最前；其次「我的技能」置顶，其余归入「共享技能」
const officialSkills = computed(() => filtered.value.filter(s => s.source === 'official'));
const mySkills = computed(() => filtered.value.filter(s => s.source !== 'official' && isMine(s)));
const sharedSkills = computed(() => filtered.value.filter(s => s.source !== 'official' && !isMine(s)));

const enabledCount = computed(() => props.skills.filter(s => s.isEnabled).length);

const hasFilter = computed(
  () => search.value.trim() !== '' || filterSource.value !== 'all' || filterVisibility.value !== 'all' || filterTag.value !== 'all'
);

function toggleSource(v: string) {
  filterSource.value = filterSource.value === v ? 'all' : v;
}
const tagPopShow = ref(false);
function pickTag(v: string) {
  filterTag.value = v;
  tagPopShow.value = false;
}
function clearFilters() {
  search.value = '';
  filterSource.value = 'all';
  filterVisibility.value = 'all';
  filterTag.value = 'all';
}
</script>

<template>
  <div class="sk-list">
    <!-- 工具栏 -->
    <div class="sk-toolbar">
      <div class="sk-search">
        <svg class="sk-search-icon" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor">
          <circle cx="7" cy="7" r="4.5" stroke-width="1.6" />
          <path d="M10.5 10.5L14 14" stroke-width="1.6" stroke-linecap="round" />
        </svg>
        <input v-model="search" class="sk-search-input" placeholder="搜索技能名称、key 或描述…" />
        <button v-if="search" class="sk-search-clear" @click="search = ''">
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.8" stroke-linecap="round" /></svg>
        </button>
      </div>

      <div class="sk-filters">
        <div class="sk-chip-group">
          <button
            class="sk-chip"
            :class="{ 'sk-chip--on': filterSource === 'all' }"
            @click="filterSource = 'all'"
          >
            全部来源<span class="sk-chip-count">{{ skills.length }}</span>
          </button>
          <button
            v-for="c in sourceChips"
            :key="c.value"
            class="sk-chip"
            :class="{ 'sk-chip--on': filterSource === c.value }"
            :style="{ '--dot': c.dot }"
            @click="toggleSource(c.value)"
          >
            <span class="sk-chip-dot" />{{ c.label }}<span class="sk-chip-count">{{ sourceCounts[c.value] || 0 }}</span>
          </button>
        </div>

        <span class="sk-filter-sep" />

        <div class="sk-chip-group">
          <button
            v-for="c in visibleVisChips"
            :key="c.value"
            class="sk-chip"
            :class="{ 'sk-chip--on': filterVisibility === c.value }"
            @click="filterVisibility = c.value"
          >
            {{ c.label }}<span v-if="c.value !== 'all'" class="sk-chip-count">{{ visCounts[c.value] || 0 }}</span>
          </button>
        </div>

        <NPopover
          v-if="tagOptions.length"
          v-model:show="tagPopShow"
          trigger="click"
          placement="bottom-start"
          :show-arrow="false"
          :padding="6"
          :width="200"
        >
          <template #trigger>
            <button class="sk-chip sk-chip--tag" :class="{ 'sk-chip--on': filterTag !== 'all' }">
              <span class="sk-chip-hash">#</span>{{ filterTag === 'all' ? '全部标签' : filterTag }}
              <svg
                class="sk-chip-caret"
                :class="{ 'sk-chip-caret--open': tagPopShow }"
                width="9"
                height="9"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
              >
                <path d="M4 6l4 4 4-4" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
          </template>
          <div class="sk-tag-menu">
            <button class="sk-tag-opt" :class="{ 'sk-tag-opt--on': filterTag === 'all' }" @click="pickTag('all')">
              <span>全部标签</span><span class="sk-tag-opt-count">{{ skills.length }}</span>
            </button>
            <button
              v-for="t in tagOptions"
              :key="t.label"
              class="sk-tag-opt"
              :class="{ 'sk-tag-opt--on': filterTag === t.label }"
              @click="pickTag(t.label)"
            >
              <span class="sk-tag-opt-name"># {{ t.label }}</span><span class="sk-tag-opt-count">{{ t.count }}</span>
            </button>
          </div>
        </NPopover>

        <button v-if="hasFilter" class="sk-filter-clear" @click="clearFilters">
          <svg width="9" height="9" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.8" stroke-linecap="round" /></svg>
          清除筛选
        </button>
      </div>
    </div>

    <!-- 加载骨架 -->
    <div v-if="loading && skills.length === 0" class="sk-skeleton">
      <div v-for="i in 6" :key="i" class="sk-skeleton-card" />
    </div>

    <!-- 空态 -->
    <div v-else-if="filtered.length === 0" class="sk-empty">
      <div class="sk-empty-visual">
        <span class="sk-empty-ring sk-empty-ring--a" />
        <span class="sk-empty-ring sk-empty-ring--b" />
        <span class="sk-empty-orb" />
      </div>
      <div class="sk-empty-title">{{ hasFilter ? '没有匹配的技能' : '还没有技能' }}</div>
      <div class="sk-empty-hint">
        {{ hasFilter ? '试试调整搜索或筛选条件' : '创建第一个属于自己的技能，或在对话中让 AI 帮你凝练' }}
      </div>
      <button v-if="!hasFilter" class="sk-empty-btn" @click="emit('create')">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
        创建技能
      </button>
    </div>

    <!-- 分组列表 -->
    <div v-else class="sk-groups">
      <!-- 官方技能（置顶最前） -->
      <section v-if="officialSkills.length" class="sk-group">
        <div class="sk-group-head">
          <span class="sk-group-eyebrow sk-group-eyebrow--official">官方技能</span>
          <span class="sk-group-line" />
          <span class="sk-group-count">{{ officialSkills.length }}</span>
        </div>
        <div class="sk-grid">
          <SkillCard
            v-for="s in officialSkills"
            :key="s.id"
            :skill="s"
            :is-mine="isMine(s)"
            :is-admin="isAdmin"
            :highlight="highlightKey === s.skillKey"
            @use="emit('use', $event)"
            @detail="emit('detail', $event)"
            @download="emit('download', $event)"
            @remove="emit('remove', $event)"
            @toggle="emit('toggle', $event)"
          />
        </div>
      </section>

      <!-- 我的技能 -->
      <section v-if="mySkills.length" class="sk-group">
        <div class="sk-group-head">
          <span class="sk-group-eyebrow sk-group-eyebrow--mine">我的技能</span>
          <span class="sk-group-line" />
          <span class="sk-group-count">{{ mySkills.length }}</span>
        </div>
        <div class="sk-grid">
          <SkillCard
            v-for="s in mySkills"
            :key="s.id"
            :skill="s"
            :is-mine="true"
            :is-admin="isAdmin"
            :highlight="highlightKey === s.skillKey"
            @use="emit('use', $event)"
            @detail="emit('detail', $event)"
            @download="emit('download', $event)"
            @remove="emit('remove', $event)"
            @toggle="emit('toggle', $event)"
          />
        </div>
      </section>

      <!-- 共享技能 -->
      <section v-if="sharedSkills.length" class="sk-group">
        <div class="sk-group-head">
          <span class="sk-group-eyebrow">共享技能</span>
          <span class="sk-group-line" />
          <span class="sk-group-count">{{ sharedSkills.length }}</span>
        </div>
        <div class="sk-grid">
          <SkillCard
            v-for="s in sharedSkills"
            :key="s.id"
            :skill="s"
            :is-mine="false"
            :is-admin="isAdmin"
            :highlight="highlightKey === s.skillKey"
            @use="emit('use', $event)"
            @detail="emit('detail', $event)"
            @download="emit('download', $event)"
            @remove="emit('remove', $event)"
            @toggle="emit('toggle', $event)"
          />
        </div>
      </section>
    </div>

    <!-- 底栏 -->
    <div class="sk-footer">
      <span class="sk-footer-count">{{ filtered.length }} / {{ skills.length }} 项 · {{ enabledCount }} 已启用</span>
      <button class="sk-footer-refresh" :disabled="loading" @click="emit('refresh')">
        <svg
          class="sk-refresh-icon"
          :class="{ 'sk-refresh-spin': loading }"
          width="12"
          height="12"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
        >
          <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" stroke-width="1.6" stroke-linecap="round" />
          <path d="M13.7 1.8v2.6h-2.6" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        刷新
      </button>
    </div>
  </div>
</template>

<style scoped>
.sk-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* ── 工具栏 ─────────────────────────────────────── */
.sk-toolbar {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 26px 12px;
}
.sk-search {
  display: flex;
  align-items: center;
  gap: 9px;
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 10px;
  padding: 0 13px;
  height: 38px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.sk-search:focus-within {
  border-color: rgba(37, 99, 235, 0.45);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.08);
  background: #fff;
}
.sk-search-icon {
  flex-shrink: 0;
  color: var(--ink-4, #94a3b8);
}
.sk-search-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--ink, #0f172a);
}
.sk-search-input::placeholder {
  color: var(--ink-4, #94a3b8);
}
.sk-search-clear {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 50%;
  background: rgba(148, 163, 184, 0.16);
  color: var(--ink-3, #64748b);
  cursor: pointer;
  transition: background 0.15s;
}
.sk-search-clear:hover {
  background: rgba(148, 163, 184, 0.3);
}

.sk-filters {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.sk-chip-group {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}
.sk-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-3, #64748b);
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 20px;
  padding: 4px 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.sk-chip:hover {
  border-color: rgba(30, 64, 175, 0.28);
  color: var(--ink-2, #334155);
}
.sk-chip-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--dot, #94a3b8);
  flex-shrink: 0;
}
.sk-chip--on {
  color: #fff;
  background: linear-gradient(135deg, #2563eb, #1e40af);
  border-color: transparent;
  box-shadow: 0 3px 10px -3px rgba(37, 99, 235, 0.5);
}
.sk-chip--on .sk-chip-dot {
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.35);
}
.sk-chip-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 15px;
  color: var(--ink-4, #94a3b8);
  background: rgba(30, 64, 175, 0.07);
  border-radius: 10px;
  padding: 0 6px;
}
.sk-chip--on .sk-chip-count {
  color: rgba(255, 255, 255, 0.92);
  background: rgba(255, 255, 255, 0.2);
}
.sk-chip-hash {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  color: #b45309;
}
.sk-chip--on .sk-chip-hash {
  color: rgba(255, 255, 255, 0.85);
}
.sk-chip--tag.sk-chip--on {
  background: linear-gradient(135deg, #d97706, #b45309);
  box-shadow: 0 3px 10px -3px rgba(217, 119, 6, 0.5);
}
.sk-chip-caret {
  transition: transform 0.18s;
}
.sk-chip-caret--open {
  transform: rotate(180deg);
}
.sk-filter-sep {
  width: 1px;
  height: 18px;
  background: rgba(30, 64, 175, 0.12);
}

/* ── 标签下拉面板（NPopover 内容） ────────────────── */
.sk-tag-menu {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 264px;
  overflow-y: auto;
}
.sk-tag-menu::-webkit-scrollbar {
  width: 5px;
}
.sk-tag-menu::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.14);
  border-radius: 3px;
}
.sk-tag-opt {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  text-align: left;
  color: var(--ink-2, #334155);
  background: transparent;
  border: none;
  border-radius: 7px;
  padding: 7px 10px;
  cursor: pointer;
  transition: background 0.12s;
}
.sk-tag-opt:hover {
  background: rgba(30, 64, 175, 0.06);
}
.sk-tag-opt--on {
  color: #b45309;
  background: rgba(217, 119, 6, 0.1);
}
.sk-tag-opt-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sk-tag-opt-count {
  flex-shrink: 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 16px;
  color: var(--ink-4, #94a3b8);
  background: rgba(30, 64, 175, 0.07);
  border-radius: 10px;
  padding: 0 7px;
}
.sk-tag-opt--on .sk-tag-opt-count {
  color: #b45309;
  background: rgba(217, 119, 6, 0.14);
}

/* ── 清除筛选 ─────────────────────────────────────── */
.sk-filter-clear {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: #dc2626;
  background: rgba(220, 38, 38, 0.06);
  border: 1px solid rgba(220, 38, 38, 0.18);
  border-radius: 20px;
  padding: 4px 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.sk-filter-clear:hover {
  background: rgba(220, 38, 38, 0.11);
  border-color: rgba(220, 38, 38, 0.3);
}

/* ── 分组 ───────────────────────────────────────── */
.sk-groups {
  flex: 1;
  overflow-y: auto;
  padding: 4px 26px 10px;
  min-height: 0;
}
.sk-groups::-webkit-scrollbar {
  width: 5px;
}
.sk-groups::-webkit-scrollbar-track {
  background: transparent;
}
.sk-groups::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.14);
  border-radius: 3px;
}
.sk-group {
  margin-bottom: 24px;
}
.sk-group-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 13px;
}
.sk-group-eyebrow {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--ink-3, #64748b);
}
.sk-group-eyebrow--mine {
  background: linear-gradient(110deg, #1e40af, #0891b2);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
}
/* 官方分组：琥珀渐变，与官方徽章同色系 */
.sk-group-eyebrow--official {
  background: linear-gradient(110deg, #d97706, #f59e0b);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
}
.sk-group-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(90deg, rgba(30, 64, 175, 0.14), transparent);
}
.sk-group-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ink-4, #94a3b8);
  background: rgba(30, 64, 175, 0.07);
  border-radius: 12px;
  padding: 1px 9px;
}
.sk-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(252px, 1fr));
  gap: 12px;
}

/* ── 骨架屏 ─────────────────────────────────────── */
.sk-skeleton {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(252px, 1fr));
  gap: 12px;
  padding: 4px 26px;
  align-content: start;
}
.sk-skeleton-card {
  height: 150px;
  border-radius: 14px;
  background: linear-gradient(100deg, rgba(30, 64, 175, 0.05) 40%, rgba(30, 64, 175, 0.11) 50%, rgba(30, 64, 175, 0.05) 60%);
  background-size: 200% 100%;
  animation: sk-shimmer 1.4s ease infinite;
}
@keyframes sk-shimmer {
  to {
    background-position: -200% 0;
  }
}

/* ── 空态 ───────────────────────────────────────── */
.sk-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 40px 20px;
}
.sk-empty-visual {
  position: relative;
  width: 92px;
  height: 92px;
  margin-bottom: 20px;
}
.sk-empty-ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 1.5px dashed rgba(30, 64, 175, 0.25);
}
.sk-empty-ring--b {
  inset: 16px;
  border-color: rgba(8, 145, 178, 0.3);
}
.sk-empty-orb {
  position: absolute;
  inset: 30px;
  border-radius: 50%;
  background: linear-gradient(135deg, #1e40af, #0891b2);
  opacity: 0.9;
  box-shadow: 0 6px 18px -4px rgba(30, 64, 175, 0.5);
}
.sk-empty-title {
  font-family: var(--font-display, 'Plus Jakarta Sans', sans-serif);
  font-size: 17px;
  font-weight: 700;
  color: var(--ink-2, #334155);
  margin-bottom: 7px;
}
.sk-empty-hint {
  font-size: 12.5px;
  color: var(--ink-4, #94a3b8);
  max-width: 320px;
  line-height: 1.7;
}
.sk-empty-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-top: 22px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, #2563eb, #1e40af);
  border: none;
  border-radius: 10px;
  padding: 10px 24px;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
  box-shadow: 0 4px 14px -4px rgba(37, 99, 235, 0.4);
}
.sk-empty-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 18px -4px rgba(37, 99, 235, 0.5);
}

/* ── 底栏 ───────────────────────────────────────── */
.sk-footer {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 11px 26px;
  border-top: 1px solid rgba(30, 64, 175, 0.1);
  background: rgba(255, 255, 255, 0.4);
}
.sk-footer-count {
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
  font-family: 'JetBrains Mono', monospace;
  font-variant-numeric: tabular-nums;
}
.sk-footer-refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3, #64748b);
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid rgba(30, 64, 175, 0.13);
  border-radius: 8px;
  padding: 5px 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.sk-footer-refresh:hover:not(:disabled) {
  background: #fff;
  border-color: rgba(30, 64, 175, 0.26);
  color: var(--ink-2, #334155);
}
.sk-footer-refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.sk-refresh-spin {
  animation: sk-rotate 0.7s linear infinite;
}
@keyframes sk-rotate {
  to {
    transform: rotate(360deg);
  }
}
</style>
