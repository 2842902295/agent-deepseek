<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { NPopconfirm, NSelect, NSwitch } from 'naive-ui';
import SvgIcon from '@/components/custom/svg-icon.vue';
import { brand } from '@/constants/brand';
import type { AgentExpert, AuthorFacet, ManageFilterPatch, ManageStatus } from '@/service/api';

/** 「专家」体系称谓随品牌变体：standard=助理 / generic=专家 */
const expertLabel = brand.expertLabel;

/** 专家列表视图：三种模式复用同一组件（商店/我的/管理），视觉语言与 ConnectorListView 同源 */
const props = defineProps<{
  /** 已由 SkillPanel 按页面做过语义过滤的数组 */
  experts: AgentExpert[];
  loading: boolean;
  myUserId: number | null;
  isAdmin: boolean;
  mode: 'store' | 'mine' | 'manage';
  highlightKey?: string | null;
  /** 商店视图专用：搜索词由 SkillPanel 头部输入框驱动（其余视图用内部搜索框） */
  search?: string;
  /** 服务端分页模式（仅上架管理）：筛选/排序由服务端完成，本地直通消费已加载数据 */
  serverPaged?: boolean;
  /** serverPaged：服务端命中总数 */
  pagedTotal?: number;
  /** serverPaged：正在加载下一页 */
  loadingMore?: boolean;
  /** serverPaged：作者筛选下拉源 */
  authorOptions?: AuthorFacet[];
  /** serverPaged 专用：父级复位信号（显式重进管理页时 +1），本地筛选控件随之复位、与 pager 的重置口径对齐；
   *  返回上一页不会变它——筛选/滚动/已加载页全部原样保留 */
  resetSeq?: number;
}>();

const emit = defineEmits<{
  /** 召唤：未添加则先添加，并新建绑定该专家的会话（卡片头部「召唤」钮） */
  summon: [expert: AgentExpert];
  /** 点卡片进详情页 */
  detail: [expert: AgentExpert];
  /** 我的：个人启用/禁用（禁用=不可召唤） */
  toggle: [expert: AgentExpert];
  /** 我的：移除出我的专家 */
  remove: [expert: AgentExpert];
  /** 管理：上/下架 */
  shelf: [expert: AgentExpert];
  /** 编辑（本人/管理员） */
  edit: [expert: AgentExpert];
  /** 删除（卡片内已确认） */
  delete: [expert: AgentExpert];
  /** 供给动作（仅我的专家工具栏）：创建专家 */
  create: [];
  /** 我的专家空态：跳回专家中心 */
  goStore: [];
  refresh: [];
  /** serverPaged：筛选条件变化（搜索词已防抖），父级据此回第 1 页重取 */
  filterChange: [patch: ManageFilterPatch];
  /** serverPaged：触底加载更多 */
  loadMore: [];
}>();

const innerSearch = ref('');
const effectiveSearch = computed(() => (props.mode === 'store' ? props.search || '' : innerSearch.value));

/** serverPaged 专用：上架状态筛选（无精选维度）；作者筛选。与连接器侧同口径 */
const filterStatus = ref<ManageStatus>('all');
/** null=全部；0=官方桶（与后端 user_id=0 → user_id 为空闭环） */
const filterAuthor = ref<number | null>(null);
/** 滚动容器（筛选发射回顶 + 触底监听；声明前置供 emitFilter/onScrollArea 引用） */
const scrollRef = ref<HTMLElement | null>(null);

function emitFilter() {
  emit('filterChange', {
    keyword: effectiveSearch.value.trim(),
    status: filterStatus.value,
    userId: filterAuthor.value
  });
  scrollRef.value?.scrollTo({ top: 0 });
}

/** 状态 chips：点选生效，再点已选状态回到「全部」 */
const statusChips: { value: ManageStatus; label: string }[] = [
  { value: 'enabled', label: '在架' },
  { value: 'disabled', label: '未上架' }
];
function toggleStatus(v: ManageStatus) {
  filterStatus.value = filterStatus.value === v ? 'all' : v;
  emitFilter();
}
const authorSelectOptions = computed(() =>
  (props.authorOptions || []).map(a => ({ label: `${a.author} · ${a.count}`, value: a.userId ?? 0 }))
);
function onAuthorChange(v: number | null) {
  filterAuthor.value = v ?? null;
  emitFilter();
}

// serverPaged：搜索词 300ms 防抖后发射（先例 SessionSearchModal）；状态/作者点选直发
let searchTimer: ReturnType<typeof setTimeout> | null = null;
/** resetSeq 复位清空搜索词时，吞掉一次 watcher 触发，避免多发一次 filterChange */
let suppressSearchWatch = false;
watch(innerSearch, () => {
  if (!props.serverPaged) return;
  if (suppressSearchWatch) {
    suppressSearchWatch = false;
    return;
  }
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => emitFilter(), 300);
});

function matchesSearch(e: AgentExpert): boolean {
  const q = effectiveSearch.value.trim().toLowerCase();
  return (
    !q ||
    e.name.toLowerCase().includes(q) ||
    e.expertKey.toLowerCase().includes(q) ||
    (e.description || '').toLowerCase().includes(q) ||
    (e.category || '').toLowerCase().includes(q)
  );
}

function isMine(e: AgentExpert): boolean {
  return props.myUserId != null && e.userId === props.myUserId;
}

const filtered = computed(() => {
  // serverPaged：服务端已筛已排（-is_enabled, sort_order, id），本地直通
  if (props.serverPaged) return props.experts;
  const list = props.experts.filter(matchesSearch);
  if (props.mode === 'mine') list.sort((a, b) => (b.addedAt ?? b.createdAt ?? 0) - (a.addedAt ?? a.createdAt ?? 0));
  if (props.mode === 'manage') list.sort((a, b) => Number(b.isEnabled) - Number(a.isEnabled));
  return list;
});

// ── 滚动加载（仅 serverPaged；商店/我的维持一次全渲染） ──
const hasMore = computed(() => !!props.serverPaged && filtered.value.length < (props.pagedTotal ?? 0));
function onScrollArea() {
  const el = scrollRef.value;
  if (!el || !hasMore.value || props.loadingMore) return;
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 160) emit('loadMore');
}

const enabledCount = computed(() => props.experts.filter(e => e.userEnabled).length);
const shelfCount = computed(() => props.experts.filter(e => e.isEnabled).length);
const hasFilter = computed(
  () =>
    effectiveSearch.value.trim() !== '' ||
    (props.serverPaged && (filterStatus.value !== 'all' || filterAuthor.value != null))
);

/** 父级复位（显式重进管理页 = resetSeq+1）：本地筛选控件同步回 pager 的重置口径。
 *  返回上一页不会触发——列表组件经 v-show 常驻，筛选/滚动/已加载页原样保留。复位只清本地、不 emit（pager 由父级自行重置） */
watch(
  () => props.resetSeq,
  () => {
    if (innerSearch.value !== '') {
      suppressSearchWatch = true;
      innerSearch.value = '';
    }
    if (searchTimer) {
      clearTimeout(searchTimer);
      searchTimer = null;
    }
    filterStatus.value = 'all';
    filterAuthor.value = null;
    scrollRef.value?.scrollTo({ top: 0 });
  }
);
</script>

<template>
  <div class="ex-list">
    <!-- 工具栏（我的 / 管理）：商店搜索在面板头部 -->
    <div v-if="mode !== 'store'" class="ex-toolbar">
      <div class="ex-search">
        <svg class="ex-search-icon" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor">
          <circle cx="7" cy="7" r="4.5" stroke-width="1.6" />
          <path d="M10.5 10.5L14 14" stroke-width="1.6" stroke-linecap="round" />
        </svg>
        <input v-model="innerSearch" class="ex-search-input" :placeholder="`搜索${expertLabel}名称或分组…`" />
        <button v-if="innerSearch" class="ex-search-clear" @click="innerSearch = ''">
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.8" stroke-linecap="round" /></svg>
        </button>
      </div>
      <div v-if="mode === 'mine'" class="ex-supply">
        <button class="ex-supply-btn ex-supply-btn--primary" @click="emit('create')">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
          创建{{ expertLabel }}
        </button>
      </div>
    </div>
    <!-- serverPaged（上架管理）：状态筛选 + 作者筛选（搜索词在上方输入框，防抖发射） -->
    <div v-if="serverPaged" class="ex-filterrow">
      <div class="ex-chip-group">
        <button
          v-for="st in statusChips"
          :key="st.value"
          class="ex-chip"
          :class="{ 'ex-chip--on': filterStatus === st.value }"
          title="再点一次取消该状态筛选"
          @click="toggleStatus(st.value)"
        >
          {{ st.label }}
        </button>
      </div>
      <NSelect
        :value="filterAuthor"
        class="ex-author-select"
        size="small"
        :options="authorSelectOptions"
        filterable
        clearable
        placeholder="全部作者"
        @update:value="onAuthorChange"
      />
    </div>

    <!-- 加载态 -->
    <div v-if="loading && experts.length === 0" class="ex-loading">
      <span class="ex-loading-spin" />
      <span class="ex-loading-text">正在加载{{ expertLabel }}…</span>
    </div>

    <!-- 空态 -->
    <div v-else-if="filtered.length === 0" class="ex-empty">
      <div class="ex-empty-title">
        {{ hasFilter ? `没有匹配的${expertLabel}` : mode === 'mine' ? `还没添加${expertLabel}` : mode === 'manage' ? `还没有${expertLabel}` : `${expertLabel}中心里还没有${expertLabel}` }}
      </div>
      <div class="ex-empty-hint">
        {{
          hasFilter
            ? '试试调整搜索条件'
            : mode === 'mine'
              ? `去${expertLabel}中心逛逛，把需要的${expertLabel}添加进来，@TA 即可召唤`
              : `创建${expertLabel}后，用户在任务中 @${expertLabel}名 即可召唤（人设 + 绑定技能随任务生效）`
        }}
      </div>
      <template v-if="!hasFilter && mode === 'mine'">
        <button class="ex-empty-btn" @click="emit('goStore')">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M2 6h12M3.5 6l1-3h9l1 3M4 6v7h8V6" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
          去逛逛
        </button>
        <button class="ex-empty-btn ex-empty-btn--ghost" @click="emit('create')">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
          创建{{ expertLabel }}
        </button>
      </template>
      <button v-else-if="!hasFilter && mode !== 'store'" class="ex-empty-btn" @click="emit('create')">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
        创建专家
      </button>
    </div>

    <!-- 卡片网格（serverPaged：触底向服务端要下一页） -->
    <div v-else ref="scrollRef" class="ex-scroll" @scroll="onScrollArea">
      <div class="ex-grid">
        <div
          v-for="e in filtered"
          :key="e.id"
          class="ex-card"
          :class="{ 'ex-card--hl': highlightKey === e.expertKey }"
          @click="emit('detail', e)"
        >
          <div class="ex-card-head">
            <span class="ex-icon" aria-hidden="true">
              <SvgIcon :icon="e.icon || 'mdi:account-tie-outline'" />
            </span>
            <span class="ex-name" :title="e.name">{{ e.name }}</span>
            <!-- 召唤位：点击即添加（若未添加）并发起专家会话 -->
            <button v-if="mode !== 'manage'" class="ex-call" @click.stop="emit('summon', e)">召唤</button>
            <span v-if="isMine(e)" class="ex-badge ex-badge--mine">我的</span>
            <span v-if="mode === 'mine' && !e.userEnabled" class="ex-badge ex-badge--off">已禁用</span>
            <span v-if="mode === 'manage' && !e.isEnabled" class="ex-badge ex-badge--off">未上架</span>
          </div>
          <div class="ex-meta">
            <span v-if="e.category">{{ e.category }}</span>
            <span v-if="e.skillKeys.length">{{ e.skillKeys.length }} 项技能</span>
            <span v-if="e.connectorKeys.length">{{ e.connectorKeys.length }} 个连接器</span>
            <!-- 上架管理：全用户的条目混排，作者归属是必要信息 -->
            <span v-if="mode === 'manage'">{{ e.author || '官方' }}</span>
            <span v-else-if="e.author && !isMine(e)">{{ e.author }}</span>
          </div>
          <p class="ex-desc">{{ e.description || '暂无简介' }}</p>

          <div v-if="mode !== 'store'" class="ex-actions">
            <!-- 我的：启停 + 编辑 + 移除 -->
            <template v-if="mode === 'mine'">
              <label class="ex-switch" :title="e.userEnabled ? '已启用：可被召唤' : '已禁用：不可召唤'" @click.stop>
                <NSwitch :value="e.userEnabled" size="small" @update:value="emit('toggle', e)" />
                <span>{{ e.userEnabled ? '已启用' : '已禁用' }}</span>
              </label>
              <button v-if="isMine(e) || isAdmin" class="ex-btn" @click.stop="emit('edit', e)">编辑</button>
              <button class="ex-btn ex-btn--danger" @click.stop="emit('remove', e)">移除</button>
            </template>
            <!-- 管理：上架开关 + 编辑 + 删除 -->
            <template v-else>
              <label class="ex-switch" :title="e.isEnabled ? '已上架：全员商店可见' : '未上架：仅创建者可见'" @click.stop>
                <NSwitch :value="e.isEnabled" size="small" :disabled="!isAdmin" @update:value="emit('shelf', e)" />
                <span>{{ e.isEnabled ? '已上架' : '未上架' }}</span>
              </label>
              <button class="ex-btn" @click.stop="emit('edit', e)">编辑</button>
              <NPopconfirm positive-text="删除" negative-text="取消" @positive-click="emit('delete', e)">
                <template #default>删除{{ expertLabel }}「{{ e.name }}」后，引用 TA 的任务将降级为通用任务，确定吗？</template>
                <template #trigger>
                  <button class="ex-btn ex-btn--danger" @click.stop>删除</button>
                </template>
              </NPopconfirm>
            </template>
          </div>
        </div>
      </div>
      <!-- 滚动加载提示（仅 serverPaged） -->
      <div v-if="hasMore || (serverPaged && loadingMore)" class="ex-load-more">
        {{ serverPaged && loadingMore ? '正在加载…' : '下滑加载更多…' }}
      </div>
    </div>

    <!-- 底栏 -->
    <div class="ex-footer">
      <span v-if="mode === 'store'" class="ex-footer-count">{{ filtered.length }} / {{ experts.length }} 位{{ expertLabel }}</span>
      <span v-else-if="mode === 'mine'" class="ex-footer-count">已添加 {{ experts.length }} · 启用 {{ enabledCount }}</span>
      <!-- serverPaged：上/下架分列计数依赖全量行，分页下改为「总数 · 已加载」 -->
      <span v-else-if="serverPaged" class="ex-footer-count">共 {{ pagedTotal ?? 0 }} · 已加载 {{ experts.length }}</span>
      <span v-else class="ex-footer-count">共 {{ experts.length }} · 上架 {{ shelfCount }} · 未上架 {{ experts.length - shelfCount }}</span>
      <button class="ex-footer-refresh" :disabled="loading" @click="emit('refresh')">
        <svg class="ex-refresh-icon" :class="{ 'ex-refresh-spin': loading }" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor">
          <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" stroke-width="1.6" stroke-linecap="round" />
          <path d="M13.7 1.8v2.6h-2.6" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        刷新
      </button>
    </div>
  </div>
</template>

<style scoped>
.ex-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* ── 工具栏 ─────────────────────────────────────── */
.ex-toolbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 26px 12px;
}
.ex-search {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 9px;
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0 13px;
  height: 38px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.ex-search:focus-within {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
  background: #fff;
}
.ex-search-icon {
  flex-shrink: 0;
  color: var(--ink-4);
}
.ex-search-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--ink);
}
.ex-search-input::placeholder {
  color: var(--ink-4);
}
.ex-search-clear {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 50%;
  background: rgba(148, 163, 184, 0.16);
  color: var(--ink-3);
  cursor: pointer;
  transition: background 0.15s;
}
.ex-search-clear:hover {
  background: rgba(148, 163, 184, 0.3);
}
.ex-supply {
  display: flex;
  align-items: center;
  gap: 9px;
}
.ex-supply-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  padding: 6px 13px;
  border-radius: 9px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  color: var(--ink-2);
  cursor: pointer;
  transition: all 0.16s;
  white-space: nowrap;
}
.ex-supply-btn:hover {
  background: var(--fill-hover);
  border-color: var(--border-strong);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}
.ex-supply-btn--primary {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}
.ex-supply-btn--primary:hover {
  background: var(--grad-brand);
  filter: brightness(0.96);
  border-color: transparent;
  box-shadow: var(--shadow-md);
}

/* ── 筛选行（仅上架管理） ─────────────────────────── */
.ex-filterrow {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 0 26px 12px;
}
.ex-chip-group {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}
.ex-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-3);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 4px 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.ex-chip:hover {
  border-color: var(--border-strong);
  color: var(--ink-2);
}
.ex-chip--on {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}
.ex-author-select {
  width: 172px;
  flex-shrink: 0;
}
.ex-load-more {
  text-align: center;
  font-size: 11px;
  color: var(--ink-4);
  padding: 12px 0 4px;
}

/* ── 加载 / 空态 ─────────────────────────────────── */
.ex-loading {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
}
.ex-loading-spin {
  width: 24px;
  height: 24px;
  border: 2.5px solid color-mix(in srgb, var(--ca) 16%, transparent);
  border-top-color: var(--ca);
  border-radius: 50%;
  animation: ex-rotate 0.8s linear infinite;
}
@keyframes ex-rotate {
  to { transform: rotate(360deg); }
}
.ex-loading-text {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-4);
}
.ex-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 40px 20px;
}
.ex-empty-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--ink-2);
  margin-bottom: 7px;
}
.ex-empty-hint {
  font-size: 12.5px;
  color: var(--ink-4);
  max-width: 360px;
  line-height: 1.7;
}
.ex-empty-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-top: 22px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  color: var(--on-primary);
  background: var(--grad-brand);
  border: none;
  border-radius: 10px;
  padding: 10px 24px;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
  box-shadow: var(--shadow-sm);
}
.ex-empty-btn:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}
.ex-empty-btn--ghost {
  color: var(--ink-2);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  box-shadow: none;
}
.ex-empty-btn--ghost:hover {
  background: #fff;
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}

/* ── 卡片网格 ───────────────────────────────────── */
.ex-scroll {
  flex: 1;
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding: 16px 26px 10px;
  min-height: 0;
}
.ex-scroll::-webkit-scrollbar {
  width: 5px;
}
.ex-scroll::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 3px;
}
.ex-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(252px, 1fr));
  gap: 12px;
}
.ex-card {
  display: flex;
  flex-direction: column;
  gap: 7px;
  cursor: pointer;
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 13px;
  padding: 14px 15px 12px;
  box-shadow: var(--card-shadow);
  transition: transform 0.16s, box-shadow 0.16s, border-color 0.16s;
}
.ex-card:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
  border-color: var(--border-strong);
}
.ex-card--hl {
  border-color: color-mix(in srgb, var(--ca) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.ex-card-head {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.ex-icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  color: var(--accent);
  background: var(--accent-soft);
}
.ex-icon :deep(svg) {
  width: 16px;
  height: 16px;
}
.ex-name {
  flex: 1;
  min-width: 0;
  font-size: 13.5px;
  font-weight: 700;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ex-badge {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 700;
  color: var(--ink-3);
  background: var(--fill-hover);
  border-radius: 10px;
  padding: 2px 7px;
  white-space: nowrap;
}
.ex-badge--mine {
  color: var(--accent);
  background: var(--accent-soft);
}
.ex-badge--off {
  color: #b45309;
  background: rgba(180, 83, 9, 0.09);
}
/* 召唤钮：卡片头部原「官方」徽标位，点击=添加（若未添加）+ 发起专家会话 */
.ex-call {
  flex-shrink: 0;
  font-family: inherit;
  font-size: 10.5px;
  font-weight: 700;
  color: var(--on-primary);
  background: var(--grad-brand);
  border: none;
  border-radius: 10px;
  padding: 3px 10px;
  cursor: pointer;
  transition: filter 0.15s, box-shadow 0.15s;
  white-space: nowrap;
}
.ex-call:hover {
  filter: brightness(0.96);
  box-shadow: var(--shadow-sm);
}
.ex-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  font-size: 11px;
  color: var(--ink-4);
}
.ex-desc {
  margin: 0;
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  min-height: 38px;
}
.ex-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: auto;
  padding-top: 4px;
}
.ex-btn {
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-2);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.ex-btn:hover {
  background: var(--fill-hover);
  border-color: var(--border-strong);
}
.ex-btn--primary {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
}
.ex-btn--primary:hover {
  filter: brightness(0.96);
  box-shadow: var(--shadow-sm);
}
.ex-btn--danger {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.2);
  background: rgba(220, 38, 38, 0.04);
}
.ex-btn--danger:hover {
  background: rgba(220, 38, 38, 0.1);
  border-color: rgba(220, 38, 38, 0.34);
}
.ex-added {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-4);
}
.ex-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-right: auto;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-3);
  cursor: pointer;
}

/* ── 底栏 ───────────────────────────────────────── */
.ex-footer {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 11px 26px;
  border-top: 1px solid var(--line-hair);
  background: var(--surface);
}
.ex-footer-count {
  font-size: 11px;
  color: var(--ink-4);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.ex-footer-refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 5px 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.ex-footer-refresh:hover:not(:disabled) {
  background: #fff;
  border-color: var(--border-strong);
  color: var(--ink-2);
}
.ex-footer-refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.ex-refresh-spin {
  animation: ex-rotate 0.7s linear infinite;
}
</style>
