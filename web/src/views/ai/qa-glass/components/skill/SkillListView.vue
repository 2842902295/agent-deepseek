<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { NPopconfirm, NSelect } from 'naive-ui';
import type { AgentSkill, AuthorFacet, ManageFilterPatch, ManageStatus } from '@/service/api';
import SkillCard from './SkillCard.vue';
import type { SkillViewMode } from './SkillCard.vue';
import { OTHER_CATEGORY, displayCategory } from './skill-categories';

const props = defineProps<{
  /** 已由 SkillPanel 按页面做过语义过滤的数组（商店=上架中、我的=已添加、上架管理=可管理全集） */
  skills: AgentSkill[];
  loading: boolean;
  myUserId: number | null;
  isAdmin: boolean;
  /** 所在页面：决定工具栏、分组、卡片形态与底栏文案；三个页面各自独立实例，状态互不相干 */
  mode: SkillViewMode;
  highlightKey?: string | null;
  /** 商店视图专用：搜索词由 SkillPanel 头部输入框驱动（其余视图用内部搜索框） */
  search?: string;
  /** 分类词表（DB 动态，面板统一下发） */
  categories: string[];
  /** 服务端分页模式（仅上架管理）：筛选/排序由服务端完成，本地直通消费已加载数据 */
  serverPaged?: boolean;
  /** serverPaged：服务端命中总数（底栏统计 + 「全部」分类计数） */
  pagedTotal?: number;
  /** serverPaged：正在加载下一页 */
  loadingMore?: boolean;
  /** serverPaged：作者筛选下拉源 */
  authorOptions?: AuthorFacet[];
  /** serverPaged 专用：父级复位信号（显式重进上架管理时 +1），本地筛选控件随之复位、与 pager 的重置口径对齐；
   *  返回上一页不会变它——筛选/滚动/已加载页全部原样保留 */
  resetSeq?: number;
}>();

const emit = defineEmits<{
  use: [skill: AgentSkill];
  detail: [skill: AgentSkill];
  download: [skill: AgentSkill];
  remove: [skill: AgentSkill];
  /** 我的技能：本人创建的未上架技能彻底删除（卡片内已确认） */
  delete: [skill: AgentSkill];
  toggle: [skill: AgentSkill];
  /** 上架管理：设为/取消精选（仅管理员） */
  feature: [skill: AgentSkill];
  /** 批量启用/禁用（个人偏好） */
  batchToggle: [keys: string[], enabled: boolean];
  /** 批量移除出我的技能 */
  batchRemove: [keys: string[]];
  /** 上架管理：批量上架/下架（全局） */
  batchShelf: [keys: string[], enabled: boolean];
  /** 上架管理：批量精选/取消精选 */
  batchFeature: [keys: string[], featured: boolean];
  /** 上架管理：批量删除（卡片列表已勾选，端点级二次确认在批量条内） */
  batchDelete: [keys: string[]];
  refresh: [];
  /** 供给动作（仅我的技能工具栏）：创建/发现/上传 */
  create: [];
  discover: [];
  upload: [];
  /** 我的技能空态：跳回商店 */
  goStore: [];
  /** serverPaged：筛选条件变化（搜索词已防抖），父级据此回第 1 页重取 */
  filterChange: [patch: ManageFilterPatch];
  /** serverPaged：触底加载更多 */
  loadMore: [];
}>();

// 搜索：商店视图用面板头部传入的 search，我的技能/上架管理用各自内部搜索框
const innerSearch = ref('');
const effectiveSearch = computed(() => (props.mode === 'store' ? props.search || '' : innerSearch.value));

const filterCategory = ref<string>('all');
/** serverPaged 专用：上架状态筛选（本地模式无此维度） */
const filterStatus = ref<ManageStatus>('all');
/** serverPaged 专用：作者筛选。null=全部；0=官方桶（与后端 user_id=0 → user_id 为空闭环） */
const filterAuthor = ref<number | null>(null);
/** 滚动容器（筛选发射时回顶用；声明前置供 emitFilter 引用） */
const groupsRef = ref<HTMLElement | null>(null);

/** serverPaged：把当前筛选状态整体上报（父级回第 1 页重取）；分类/状态/作者点选直发，搜索词在 watcher 里防抖 */
function emitFilter() {
  emit('filterChange', {
    keyword: innerSearch.value.trim(),
    category: filterCategory.value,
    status: filterStatus.value,
    userId: filterAuthor.value
  });
  groupsRef.value?.scrollTo({ top: 0 });
}

/** 搜索命中判断（精选区与列表共用；分类筛选不进来——精选不受分类影响） */
function matchesSearch(s: AgentSkill): boolean {
  const q = effectiveSearch.value.trim().toLowerCase();
  return (
    !q ||
    s.name.toLowerCase().includes(q) ||
    s.skillKey.toLowerCase().includes(q) ||
    (s.description || '').toLowerCase().includes(q)
  );
}

// 分类导航：本地模式只展示当前数据集里有条目的分类；serverPaged 展示词表全量（计数由服务端决定，本地不显示）
const catCounts = computed(() => {
  const m: Record<string, number> = {};
  for (const s of props.skills) {
    const c = displayCategory(s.category, props.categories);
    m[c] = (m[c] || 0) + 1;
  }
  return m;
});
const categoryChips = computed(() => {
  const list = props.categories.includes(OTHER_CATEGORY) ? props.categories : [...props.categories, OTHER_CATEGORY];
  if (props.serverPaged) return list.map(c => ({ value: c, label: c, count: null as number | null }));
  return list
    .filter(c => (catCounts.value[c] || 0) > 0)
    .map(c => ({ value: c, label: c, count: catCounts.value[c] as number | null }));
});

function isMine(s: AgentSkill): boolean {
  return props.myUserId != null && s.userId === props.myUserId;
}

const filtered = computed(() => {
  // serverPaged：服务端已筛已排（-is_enabled, id），本地直通
  if (props.serverPaged) return props.skills;
  const list = props.skills.filter(s => {
    const matchCat = filterCategory.value === 'all' || displayCategory(s.category, props.categories) === filterCategory.value;
    return matchesSearch(s) && matchCat;
  });
  // 我的技能视图：新创建/新添加的排前面（addedAt=个人添加时间；本人创建无偏好行时后端回落创建时间）
  if (props.mode === 'mine') list.sort((a, b) => (b.addedAt ?? b.createdAt ?? 0) - (a.addedAt ?? a.createdAt ?? 0));
  // 上架管理视图：未上架（已下架/private 未公开）的排后面，方便聚焦在架技能（稳定排序保留组内原序）
  if (props.mode === 'manage')
    list.sort(
      (a, b) =>
        Number(b.isEnabled) - Number(a.isEnabled)
    );
  return list;
});

/** serverPaged 状态筛选 chips：点选生效，再点已选状态回到「全部」（避免与分类导航的「全部」并排混淆） */
const statusChips: { value: ManageStatus; label: string }[] = [
  { value: 'enabled', label: '在架' },
  { value: 'disabled', label: '未上架' },
  { value: 'featured', label: '精选' }
];
function toggleStatus(v: ManageStatus) {
  filterStatus.value = filterStatus.value === v ? 'all' : v;
  emitFilter();
}

/** 作者下拉：label 带计数；value = userId ?? 0（null=官方，清空=全部） */
const authorSelectOptions = computed(() =>
  (props.authorOptions || []).map(a => ({ label: `${a.author} · ${a.count}`, value: a.userId ?? 0 }))
);
function onAuthorChange(v: number | null) {
  filterAuthor.value = v ?? null;
  emitFilter();
}

// ── 精选区（仅商店）：管理员维护的 isFeatured 技能，一屏若干枚，「换一换」轮换展示。
// 只跟搜索走，不受分类筛选影响（数据源用 skills 而非 filtered） ──
const FEAT_PAGE = 4;
const featOffset = ref(0);
const featured = computed(() => props.skills.filter(s => s.isFeatured && matchesSearch(s)));
const featuredPage = computed(() => {
  const list = featured.value;
  if (!list.length) return [];
  if (list.length <= FEAT_PAGE) return list;
  const off = featOffset.value % list.length;
  return [...list.slice(off, off + FEAT_PAGE), ...list.slice(0, Math.max(0, off + FEAT_PAGE - list.length))];
});
function swapFeatured() {
  featOffset.value = (featOffset.value + FEAT_PAGE) % Math.max(1, featured.value.length);
}

// ── 滚动加载：本地模式=卡片按需增量渲染；serverPaged=触底向服务端要下一页 ──
const PAGE = 24;
const visibleCount = ref(PAGE);
const visibleList = computed(() => (props.serverPaged ? filtered.value : filtered.value.slice(0, visibleCount.value)));
const hasMore = computed(() =>
  props.serverPaged ? filtered.value.length < (props.pagedTotal ?? 0) : filtered.value.length > visibleCount.value
);
function onGroupsScroll() {
  const el = groupsRef.value;
  if (!el || !hasMore.value) return;
  if (el.scrollTop + el.clientHeight < el.scrollHeight - 160) return;
  if (props.serverPaged) {
    if (!props.loadingMore) emit('loadMore');
  } else {
    visibleCount.value += PAGE;
  }
}
// 搜索/筛选变化 → 回到第一页（精选轮换位置一并复位）；serverPaged 的滚动回顶由筛选发射点自行处理，追加数据不回顶
watch([effectiveSearch, filterCategory, () => props.skills], () => {
  if (props.serverPaged) return;
  visibleCount.value = PAGE;
  featOffset.value = 0;
  groupsRef.value?.scrollTo({ top: 0 });
});
// serverPaged：搜索词 300ms 防抖后发射（先例 SessionSearchModal）；分类/状态/作者点选在各自回调里直发
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

// 底栏统计（按视图取数）
const enabledCount = computed(() => props.skills.filter(s => s.userEnabled).length);
const addedCount = computed(() => props.skills.filter(s => s.isAdded).length);
// 上架口径与卡片开关一致：isEnabled（统一尺子）
const onShelfCount = computed(() => props.skills.filter(s => s.isEnabled).length);

// ── 批量管理（我的技能：启用/禁用/移除） ──────────────────
const batchMode = ref(false);
const selectedKeys = ref<Set<string>>(new Set());

function enterBatchMode() {
  batchMode.value = true;
  selectedKeys.value = new Set();
}
function exitBatchMode() {
  batchMode.value = false;
  selectedKeys.value = new Set();
}
function toggleSelect(skill: AgentSkill) {
  const next = new Set(selectedKeys.value);
  if (next.has(skill.skillKey)) next.delete(skill.skillKey);
  else next.add(skill.skillKey);
  selectedKeys.value = next;
}
/** 全选 / 反选当前筛选结果 */
function toggleSelectFilteredAll() {
  const keys = filtered.value.map(s => s.skillKey);
  const allSelected = keys.length > 0 && keys.every(k => selectedKeys.value.has(k));
  const next = new Set(selectedKeys.value);
  if (allSelected) keys.forEach(k => next.delete(k));
  else keys.forEach(k => next.add(k));
  selectedKeys.value = next;
}
const filteredAllSelected = computed(() => {
  const keys = filtered.value.map(s => s.skillKey);
  return keys.length > 0 && keys.every(k => selectedKeys.value.has(k));
});
function applyBatch(enabled: boolean) {
  const keys = Array.from(selectedKeys.value);
  if (!keys.length) return;
  emit('batchToggle', keys, enabled);
  selectedKeys.value = new Set();
}
function applyBatchRemove() {
  const keys = Array.from(selectedKeys.value);
  if (!keys.length) return;
  emit('batchRemove', keys);
  selectedKeys.value = new Set();
}
// ── 上架管理批量动作（mode === 'manage'） ──
function applyBatchShelf(enabled: boolean) {
  const keys = Array.from(selectedKeys.value);
  if (!keys.length) return;
  emit('batchShelf', keys, enabled);
  selectedKeys.value = new Set();
}
function applyBatchFeature(featured: boolean) {
  const keys = Array.from(selectedKeys.value);
  if (!keys.length) return;
  emit('batchFeature', keys, featured);
  selectedKeys.value = new Set();
}
function applyBatchDelete() {
  const keys = Array.from(selectedKeys.value);
  if (!keys.length) return;
  emit('batchDelete', keys);
  selectedKeys.value = new Set();
}

const hasFilter = computed(
  () =>
    innerSearch.value.trim() !== '' ||
    filterCategory.value !== 'all' ||
    (props.serverPaged && (filterStatus.value !== 'all' || filterAuthor.value != null))
);

function toggleCategory(v: string) {
  filterCategory.value = filterCategory.value === v ? 'all' : v;
  if (props.serverPaged) emitFilter();
}

/** 父级复位（显式重进上架管理 = resetSeq+1）：本地筛选控件同步回 pager 的重置口径。
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
    filterCategory.value = 'all';
    filterStatus.value = 'all';
    filterAuthor.value = null;
    visibleCount.value = PAGE;
    featOffset.value = 0;
    exitBatchMode();
    groupsRef.value?.scrollTo({ top: 0 });
  }
);
</script>

<template>
  <div class="sk-list">
    <!-- 工具栏（我的技能 / 上架管理）：商店视图的搜索在面板头部、分类导航在精选区下方，这里不渲染 -->
    <div v-if="mode !== 'store'" class="sk-toolbar">
      <div class="sk-search">
        <svg class="sk-search-icon" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor">
          <circle cx="7" cy="7" r="4.5" stroke-width="1.6" />
          <path d="M10.5 10.5L14 14" stroke-width="1.6" stroke-linecap="round" />
        </svg>
        <input v-model="innerSearch" class="sk-search-input" placeholder="搜索技能名称、key 或描述…" />
        <button v-if="innerSearch" class="sk-search-clear" @click="innerSearch = ''">
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.8" stroke-linecap="round" /></svg>
        </button>
      </div>

      <div class="sk-filters">
        <!-- 分类导航：全部 + 有条目的分类（词表顺序）；再点已选分类回到全部。
             serverPaged：词表全量展示、不显示本地计数（「全部」计数=服务端命中总数） -->
        <div class="sk-chip-group">
          <button
            class="sk-chip"
            :class="{ 'sk-chip--on': filterCategory === 'all' }"
            @click="toggleCategory('all')"
          >
            全部<span class="sk-chip-count">{{ serverPaged ? (pagedTotal ?? 0) : skills.length }}</span>
          </button>
          <button
            v-for="c in categoryChips"
            :key="c.value"
            class="sk-chip"
            :class="{ 'sk-chip--on': filterCategory === c.value }"
            @click="toggleCategory(c.value)"
          >
            {{ c.label }}<span v-if="c.count != null" class="sk-chip-count">{{ c.count }}</span>
          </button>
        </div>

        <!-- serverPaged（上架管理）：状态筛选 + 作者筛选。状态 chips 点选生效、再点取消（避免与分类「全部」并排混淆） -->
        <template v-if="serverPaged">
          <span class="sk-filter-sep" />
          <div class="sk-chip-group">
            <button
              v-for="st in statusChips"
              :key="st.value"
              class="sk-chip"
              :class="{ 'sk-chip--on': filterStatus === st.value }"
              title="再点一次取消该状态筛选"
              @click="toggleStatus(st.value)"
            >
              {{ st.label }}
            </button>
          </div>
          <NSelect
            :value="filterAuthor"
            class="sk-author-select"
            size="small"
            :options="authorSelectOptions"
            filterable
            clearable
            placeholder="全部作者"
            @update:value="onAuthorChange"
          />
        </template>

        <!-- 批量管理：「我的技能」（启用/禁用/移除）与「上架管理」（上下架/精选/删除）各自提供 -->
        <template v-if="mode === 'mine' || mode === 'manage'">
          <span class="sk-filter-sep" />
          <button
            class="sk-chip"
            :class="{ 'sk-chip--on': batchMode }"
            :title="batchMode ? '退出批量管理' : mode === 'mine' ? '批量启用/禁用/移除技能（只影响自己）' : '批量上架/下架/精选/删除技能（影响全员）'"
            @click="batchMode ? exitBatchMode() : enterBatchMode()"
          >
            批量管理
          </button>
        </template>

        <!-- 供给动作（仅我的技能：创建/发现/上传；原统计条上的三按钮收敛到此） -->
        <div v-if="mode === 'mine'" class="sk-supply">
          <button class="sk-supply-btn sk-supply-btn--primary" @click="emit('create')">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
            创建技能
          </button>
          <button class="sk-supply-btn" @click="emit('discover')">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 1.5l1.6 3.9 4.2.4-3.2 2.7.9 4.1L8 10.2l-3.5 2.4.9-4.1L2.2 5.8l4.2-.4L8 1.5z" stroke-width="1.3" stroke-linejoin="round" /></svg>
            发现技能
          </button>
          <button class="sk-supply-btn" @click="emit('upload')">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 11V3M5 6l3-3 3 3" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 12.5h10" stroke-width="1.7" stroke-linecap="round" /></svg>
            上传技能包
          </button>
        </div>
      </div>
    </div>

    <!-- 加载态：不做仿布局骨架（真实布局含精选区/分类导航，仿不像反而有落差），
         只给一个不冒充内容的居中小指示器 -->
    <div v-if="loading && skills.length === 0" class="sk-loading">
      <span class="sk-loading-spin" />
      <span class="sk-loading-text">正在加载技能…</span>
    </div>

    <!-- 空态（文案与按钮按页面分化：创建入口只属于「我的技能」；商店空态在列表区内联） -->
    <div v-else-if="filtered.length === 0 && mode !== 'store'" class="sk-empty">
      <div class="sk-empty-title">
        {{ hasFilter ? '没有匹配的技能' : mode === 'mine' ? '还没添加技能' : '还没有技能' }}
      </div>
      <div class="sk-empty-hint">
        {{
          hasFilter
            ? '试试调整搜索或筛选条件'
            : mode === 'mine'
              ? '去技能库逛逛，把需要的技能添加进来'
              : mode === 'manage'
                ? '创建第一个属于自己的技能，或在对话中让 AI 帮你凝练'
                : '在右上角「我的技能」里创建/上传技能，或在对话中让 AI 帮你凝练'
        }}
      </div>
      <template v-if="!hasFilter && mode === 'mine'">
        <button class="sk-empty-btn" @click="emit('goStore')">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M2 6h12M3.5 6l1-3h9l1 3M4 6v7h8V6" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
          去逛逛
        </button>
        <button class="sk-empty-btn sk-empty-btn--ghost" @click="emit('create')">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
          创建技能
        </button>
      </template>
      <button v-else-if="!hasFilter && mode === 'manage'" class="sk-empty-btn" @click="emit('create')">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
        创建技能
      </button>
    </div>

    <!-- 列表主体：商店=精选区+分类平铺，我的技能/上架管理平铺；均滚动加载 -->
    <div v-else ref="groupsRef" class="sk-groups" @scroll="onGroupsScroll">
      <template v-if="mode === 'store'">
        <!-- 精选区：管理员维护（is_featured），超过一页时「换一换」轮换展示 -->
        <section v-if="featured.length" class="sk-group">
          <div class="sk-group-head">
            <span class="sk-group-eyebrow">精选技能</span>
            <span class="sk-group-line" />
            <button v-if="featured.length > FEAT_PAGE" class="sk-swap" title="换一批精选技能" @click="swapFeatured">
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor">
                <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" stroke-width="1.6" stroke-linecap="round" />
                <path d="M13.7 1.8v2.6h-2.6" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
              换一换
            </button>
          </div>
          <div class="sk-grid">
            <SkillCard
              v-for="s in featuredPage"
              :key="s.id"
              :skill="s"
              :is-mine="isMine(s)"
              :is-admin="isAdmin"
              mode="store"
              :highlight="highlightKey === s.skillKey"
              @use="emit('use', $event)"
              @detail="emit('detail', $event)"
              @download="emit('download', $event)"
              @remove="emit('remove', $event)"
              @toggle="emit('toggle', $event)"
              @feature="emit('feature', $event)"
            />
          </div>
        </section>

        <!-- 分类导航：位于精选区下方；只筛下面的卡片列表，不影响精选区 -->
        <div class="sk-cats">
          <button
            class="sk-chip"
            :class="{ 'sk-chip--on': filterCategory === 'all' }"
            @click="toggleCategory('all')"
          >
            全部<span class="sk-chip-count">{{ serverPaged ? (pagedTotal ?? 0) : skills.length }}</span>
          </button>
          <button
            v-for="c in categoryChips"
            :key="c.value"
            class="sk-chip"
            :class="{ 'sk-chip--on': filterCategory === c.value }"
            @click="toggleCategory(c.value)"
          >
            {{ c.label }}<span v-if="c.count != null" class="sk-chip-count">{{ c.count }}</span>
          </button>
        </div>

        <!-- 当前分类下的卡片（含精选技能，滚动加载） -->
        <div class="sk-grid">
          <SkillCard
            v-for="s in visibleList"
            :key="s.id"
            :skill="s"
            :is-mine="isMine(s)"
            :is-admin="isAdmin"
            mode="store"
            :highlight="highlightKey === s.skillKey"
            @use="emit('use', $event)"
            @detail="emit('detail', $event)"
            @download="emit('download', $event)"
            @remove="emit('remove', $event)"
            @toggle="emit('toggle', $event)"
            @feature="emit('feature', $event)"
          />
        </div>
        <!-- 分类内空态（精选区不受影响，仍照常展示） -->
        <div v-if="!visibleList.length" class="sk-cat-empty">
          {{ skills.length ? '该分类下暂无技能' : '还没有技能，去「我的技能」里创建/上传第一个吧' }}
        </div>
      </template>

      <!-- 我的技能 / 上架管理：平铺网格（滚动加载） -->
      <div v-else class="sk-grid">
        <SkillCard
          v-for="s in visibleList"
          :key="s.id"
          :skill="s"
          :is-mine="isMine(s)"
          :is-admin="isAdmin"
          :mode="mode"
          :highlight="highlightKey === s.skillKey"
          :selectable="(mode === 'mine' || mode === 'manage') && batchMode"
          :selected="selectedKeys.has(s.skillKey)"
          @use="emit('use', $event)"
          @detail="emit('detail', $event)"
          @download="emit('download', $event)"
          @remove="emit('remove', $event)"
          @delete="emit('delete', $event)"
          @toggle="emit('toggle', $event)"
          @feature="emit('feature', $event)"
          @select="toggleSelect"
        />
      </div>

      <!-- 滚动加载提示 -->
      <div v-if="hasMore || (serverPaged && loadingMore)" class="sk-load-more">
        {{ serverPaged && loadingMore ? '正在加载…' : '下滑加载更多…' }}
      </div>
    </div>

    <!-- 底栏：批量模式下变为批量操作条 -->
    <div class="sk-footer" :class="{ 'sk-footer--batch': batchMode }">
      <template v-if="batchMode">
        <div class="sk-batch-left">
          <button class="sk-batch-btn" @click="toggleSelectFilteredAll">
            {{ filteredAllSelected ? '取消全选' : serverPaged ? '全选已加载' : '全选当前' }}
          </button>
          <span class="sk-batch-count">已选 {{ selectedKeys.size }} 项</span>
        </div>
        <div v-if="mode === 'manage'" class="sk-batch-right">
          <button class="sk-batch-btn sk-batch-btn--on" :disabled="!selectedKeys.size" @click="applyBatchShelf(true)">批量上架</button>
          <button class="sk-batch-btn sk-batch-btn--off" :disabled="!selectedKeys.size" @click="applyBatchShelf(false)">批量下架</button>
          <button class="sk-batch-btn" :disabled="!selectedKeys.size" @click="applyBatchFeature(true)">批量精选</button>
          <button class="sk-batch-btn" :disabled="!selectedKeys.size" @click="applyBatchFeature(false)">取消精选</button>
          <NPopconfirm
            positive-text="删除"
            negative-text="取消"
            :disabled="!selectedKeys.size"
            @positive-click="applyBatchDelete"
          >
            <template #default>确定彻底删除选中的 {{ selectedKeys.size }} 个技能吗？删除后不可恢复。</template>
            <template #trigger>
              <button class="sk-batch-btn sk-batch-btn--off" :disabled="!selectedKeys.size">批量删除</button>
            </template>
          </NPopconfirm>
          <button class="sk-batch-btn" @click="exitBatchMode">退出</button>
        </div>
        <div v-else class="sk-batch-right">
          <button class="sk-batch-btn sk-batch-btn--on" :disabled="!selectedKeys.size" @click="applyBatch(true)">批量启用</button>
          <button class="sk-batch-btn sk-batch-btn--off" :disabled="!selectedKeys.size" @click="applyBatch(false)">批量禁用</button>
          <button class="sk-batch-btn" :disabled="!selectedKeys.size" @click="applyBatchRemove">批量移除</button>
          <button class="sk-batch-btn" @click="exitBatchMode">退出</button>
        </div>
      </template>
      <template v-else>
        <span v-if="mode === 'store'" class="sk-footer-count">{{ filtered.length }} / {{ skills.length }} 个技能 · {{ addedCount }} 已添加</span>
        <span v-else-if="mode === 'mine'" class="sk-footer-count">已添加 {{ skills.length }} · 启用 {{ enabledCount }}</span>
        <!-- serverPaged：上/下架分列计数依赖全量行，分页下改为「总数 · 已加载」 -->
        <span v-else-if="serverPaged" class="sk-footer-count">共 {{ pagedTotal ?? 0 }} · 已加载 {{ skills.length }}</span>
        <span v-else class="sk-footer-count">共 {{ skills.length }} · 上架 {{ onShelfCount }} · 未上架 {{ skills.length - onShelfCount }}</span>
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
      </template>
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
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0 13px;
  height: 38px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.sk-search:focus-within {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
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
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 4px 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.sk-chip:hover {
  border-color: var(--border-strong);
  color: var(--ink-2, #334155);
}
.sk-chip--on {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}
.sk-chip-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 15px;
  color: var(--ink-4, #94a3b8);
  background: var(--fill-hover);
  border-radius: 10px;
  padding: 0 6px;
}
.sk-chip--on .sk-chip-count {
  color: rgba(255, 255, 255, 0.92);
  background: rgba(255, 255, 255, 0.2);
}
.sk-filter-sep {
  width: 1px;
  height: 18px;
  background: var(--border);
}
/* 作者筛选下拉（仅上架管理 / serverPaged） */
.sk-author-select {
  width: 172px;
  flex-shrink: 0;
}

/* ── 供给动作（仅我的技能工具栏右侧：创建/发现/上传） ── */
.sk-supply {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 9px;
}
.sk-supply-btn {
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
  color: var(--ink-2, #334155);
  cursor: pointer;
  transition: all 0.16s;
  white-space: nowrap;
}
.sk-supply-btn:hover {
  background: var(--fill-hover);
  border-color: var(--border-strong);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}
.sk-supply-btn--primary {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}
.sk-supply-btn--primary:hover {
  background: var(--grad-brand);
  filter: brightness(0.96);
  border-color: transparent;
  box-shadow: var(--shadow-md);
}

/* ── 分组 ───────────────────────────────────────── */
.sk-groups {
  flex: 1;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，内容增减不抖 */
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
  background: var(--border-strong);
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
/* 分组标题：对齐侧栏 session-group-label 的平淡语言（无渐变/无强调） */
.sk-group-eyebrow {
  font-size: 12px;
  font-weight: 400;
  letter-spacing: normal;
  color: var(--ink-4);
}
.sk-group-line {
  flex: 1;
  height: 1px;
  background: var(--line-hair);
}
.sk-group-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ink-4, #94a3b8);
  background: var(--fill-hover);
  border-radius: 12px;
  padding: 1px 9px;
}
/* 分类导航行（商店：精选区之下，滚动区内） */
.sk-cats {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  margin: 4px 0 13px;
}
/* 分类内空态提示 */
.sk-cat-empty {
  text-align: center;
  font-size: 12.5px;
  color: var(--ink-4, #94a3b8);
  padding: 34px 0 20px;
}
/* 换一换（精选区分组头右侧）：轮换展示一批精选技能 */
.sk-swap {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-3, #64748b);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 3px 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.sk-swap:hover {
  color: var(--ca);
  border-color: color-mix(in srgb, var(--ca) 34%, transparent);
  background: color-mix(in srgb, var(--ca) 7%, transparent);
}
/* 滚动加载提示 */
.sk-load-more {
  text-align: center;
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
  padding: 12px 0 4px;
}
.sk-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(252px, 1fr));
  gap: 12px;
}

/* ── 加载态：居中小指示器（不做仿布局骨架） ── */
.sk-loading {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
}
.sk-loading-spin {
  width: 24px;
  height: 24px;
  border: 2.5px solid color-mix(in srgb, var(--ca) 16%, transparent);
  border-top-color: var(--ca);
  border-radius: 50%;
  animation: sk-rotate 0.8s linear infinite;
}
.sk-loading-text {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-4, #94a3b8);
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
.sk-empty-title {
  font-family: var(--font-body);
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
  color: var(--on-primary);
  background: var(--grad-brand);
  border: none;
  border-radius: 10px;
  padding: 10px 24px;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
  box-shadow: var(--shadow-sm);
}
.sk-empty-btn:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}
/* 次按钮：白底描边，弱化于主按钮 */
.sk-empty-btn--ghost {
  color: var(--ink-2, #334155);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  box-shadow: none;
}
.sk-empty-btn--ghost:hover {
  background: #fff;
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}

/* ── 底栏 ───────────────────────────────────────── */
.sk-footer {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 11px 26px;
  border-top: 1px solid var(--line-hair);
  background: var(--surface);
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
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 5px 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.sk-footer-refresh:hover:not(:disabled) {
  background: #fff;
  border-color: var(--border-strong);
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

/* ── 批量操作条（复用底栏位置） ───────────────────── */
.sk-footer--batch {
  justify-content: space-between;
  background: var(--fill-2);
}
.sk-batch-left,
.sk-batch-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sk-batch-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11.5px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ink-2, #334155);
}
.sk-batch-btn {
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3, #64748b);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 5px 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.sk-batch-btn:hover:not(:disabled) {
  background: #fff;
  border-color: var(--border-strong);
  color: var(--ink-2, #334155);
}
.sk-batch-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.sk-batch-btn--on {
  color: #059669;
  border-color: rgba(5, 150, 105, 0.28);
  background: rgba(5, 150, 105, 0.07);
}
.sk-batch-btn--on:hover:not(:disabled) {
  background: rgba(5, 150, 105, 0.14);
  border-color: rgba(5, 150, 105, 0.42);
  color: #047857;
}
.sk-batch-btn--off {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.24);
  background: rgba(220, 38, 38, 0.05);
}
.sk-batch-btn--off:hover:not(:disabled) {
  background: rgba(220, 38, 38, 0.1);
  border-color: rgba(220, 38, 38, 0.38);
  color: #b91c1c;
}
</style>
