<script setup lang="ts">
import {computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch} from 'vue';
import {useRoute, useRouter} from 'vue-router';
import {useAuthStore} from '@/store/modules/auth';
import {fetchKbEntry, fetchKbSearch, fetchMyCreditBalance, type KBEntry, type NianFeedItem} from '@/service/api';
import {useNian} from './composables/useNian';
import FeedCard from './components/feed-card.vue';
import DetailModal from './components/detail-modal.vue';
import PendingCard from './components/pending-card.vue';

const QAGlass = defineAsyncComponent(() => import('@/views/ai/qa-glass/index.vue'));

const router = useRouter();
const route = useRoute();
const authStore = useAuthStore();

const isSuper = computed(() => authStore.userInfo.roles.includes('R_SUPER'));

function goToWorkbench() {
  router.push({name: 'ai_home'});
}

function goToQA() {
  router.push({name: 'ai_qa-glass', query: {t: Date.now()}});
}

const qaDrawerOpen = ref(false);
const qaPrefill = ref('');
const qaDrawerKey = ref(0);

// 抽屉打开时锁定主页面滚动
watch(qaDrawerOpen, (open) => {
  document.body.style.overflow = open ? 'hidden' : '';
});

function openQADrawer(title: string) {
  qaPrefill.value = title;
  qaDrawerKey.value++;
  qaDrawerOpen.value = true;
}

function onDetailQA(title: string) {
  if (qaDrawerOpen.value) {
    qaDrawerOpen.value = false;
  } else {
    qaPrefill.value = title;
    qaDrawerKey.value++;
    qaDrawerOpen.value = true;
  }
}

function goToDashboard() {
  router.push({name: 'ai_dashboard'});
}

const {
  items,
  loading,
  loadingMore,
  hasMore,
  total,
  reloadFeed,
  loadMore,
  loadTagsStats,
  loadTagsClustered,
  openInbox,
  deleteEntry,
  trackOpen,
  entryById,
  pendingItems,
} = useNian();

const detailShow = ref(false);
const detailId = ref<string | null>(null);
const externalDetailItem = ref<NianFeedItem | null>(null);
const detailItem = computed<NianFeedItem | null>(() => {
  if (!detailId.value) return null;
  const fromList = entryById(detailId.value) as NianFeedItem | undefined;
  if (fromList) return fromList;
  return externalDetailItem.value;
});

async function openDetail(id: string) {
  detailId.value = id;
  detailShow.value = true;
  if (!entryById(id)) {
    // 来自搜索的条目可能不在当前 feed，先把详情拉回来
    const {data} = await fetchKbEntry(id);
    if (data) {
      externalDetailItem.value = data as NianFeedItem;
    } else {
      detailShow.value = false;
      detailId.value = null;
      return;
    }
  } else {
    externalDetailItem.value = null;
  }
  trackOpen(id);
}

watch(
  () => route.query.detail,
  (v) => {
    if (typeof v === 'string' && v) {
      openDetail(v);
    }
  },
  {immediate: false}
);

const today = new Date();
const dateStr = computed(() => {
  const yy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const dd = String(today.getDate()).padStart(2, '0');
  const wk = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][today.getDay()];
  return `${yy}.${mm}.${dd} · ${wk}`;
});

const columnCount = ref(3);

function updateColumnCount() {
  const w = window.innerWidth;
  columnCount.value = w <= 560 ? 1 : w <= 920 ? 2 : 3;
}

// ── 积分余额（个人） ──────────────────────────────────────────
const creditBalance = ref<Api.AI.MyCreditBalance | null>(null);

async function loadCreditBalance() {
  const {data, error} = await fetchMyCreditBalance();
  if (!error && data) creditBalance.value = data;
}

function fmtCreditShort(v: number) {
  if (v < 0) return '∞';
  if (v >= 10000) return `${(v / 10000).toFixed(1)}万`;
  return v.toLocaleString(undefined, {maximumFractionDigits: 0});
}

const creditPct = computed(() => {
  if (!creditBalance.value || creditBalance.value.isUnlimited) return 100;
  const q = creditBalance.value.quota;
  if (q <= 0) return 0;
  return Math.max(0, Math.min(100, (creditBalance.value.remaining / q) * 100));
});

const creditColor = computed(() => {
  const p = creditPct.value;
  if (p <= 10) return '#ef4444';
  if (p <= 30) return '#f59e0b';
  return '#10b981';
});

// ── 搜索 ─────────────────────────────────────────────────────────
const searchText = ref('');
const searchInputRef = ref<HTMLInputElement | null>(null);
const searchResults = ref<NianFeedItem[]>([]);
const searchLoading = ref(false);
let searchDebounce: ReturnType<typeof setTimeout> | null = null;
let searchSeq = 0;

// ── 筛选 ──────────────────────────────────────────────────────────
type FilterType = 'all' | 'knowledge' | 'idea' | 'todo';
type FilterTodo = 'all' | 'pending' | 'done';
type SortBy = 'updatedAt' | 'createdAt' | 'title';
type ViewMode = 'masonry' | 'list';
const filterType = ref<FilterType>((localStorage.getItem('kb_filterType') as FilterType) || 'all');
const filterTodo = ref<FilterTodo>('all');
const filterTag = ref<string>('');
const sortBy = ref<SortBy>((localStorage.getItem('kb_sortBy') as SortBy) || 'updatedAt');
const viewMode = ref<ViewMode>((localStorage.getItem('kb_viewMode') as ViewMode) || 'masonry');

/** 持久化用户偏好 */
watch(filterType, (v) => localStorage.setItem('kb_filterType', v));
watch(sortBy, (v) => localStorage.setItem('kb_sortBy', v));
watch(viewMode, (v) => localStorage.setItem('kb_viewMode', v));

const searchActive = computed(() => searchText.value.trim().length > 0);

/** 服务端标签聚类 */
interface TagClusterLocal {
  canonical: string;
  count: number;
  members: string[];
  memberCounts?: Record<string, number>;
  size: number;
}
const tagClusters = ref<TagClusterLocal[]>([]);
const tagSearch = ref('');
const tagsExpanded = ref(false);
const TAGS_VISIBLE_LIMIT = 20;

async function refreshTagClusters() {
  const et = filterType.value === 'all' ? undefined : filterType.value;
  tagClusters.value = await loadTagsClustered(et);
}

/** 搜索过滤后的聚类列表 */
const filteredClusters = computed(() => {
  const q = tagSearch.value.trim().toLowerCase();
  if (!q) return tagClusters.value;
  return tagClusters.value.filter(c =>
    c.canonical.toLowerCase().includes(q) ||
    c.members.some(m => m.toLowerCase().includes(q))
  );
});

/** 实际展示的聚类（未展开时截断到前 N 个） */
const visibleClusters = computed(() =>
  tagsExpanded.value ? filteredClusters.value : filteredClusters.value.slice(0, TAGS_VISIBLE_LIMIT)
);
const hiddenTagCount = computed(() => Math.max(0, filteredClusters.value.length - TAGS_VISIBLE_LIMIT));

/** 类型筛选变化时重新加载标签聚类 */
watch(filterType, () => {
  filterTag.value = '';
  tagSearch.value = '';
  tagsExpanded.value = false;
  refreshTagClusters();
});

/** 选中某聚类时，filterTag 设为 canonical（匹配所有 members） */
function selectCluster(canonical: string) {
  filterTag.value = filterTag.value === canonical ? '' : canonical;
}

/** 选中某个具体成员标签时，filterTag 设为该标签名 */
function selectMemberTag(member: string) {
  filterTag.value = filterTag.value === member ? '' : member;
}

/** 获取当前选中的标签 */
const activeClusterMembers = computed(() => {
  if (!filterTag.value) return [];
  // 精确匹配：只筛选点击的那个具体标签
  return [filterTag.value];
});

const displayItems = computed<NianFeedItem[]>(() => {
  let base = searchActive.value ? searchResults.value : items.value;
  if (filterType.value !== 'all') {
    base = base.filter(x => x.entryType === filterType.value);
  }
  if (filterType.value === 'todo' && filterTodo.value !== 'all') {
    base = base.filter(x => x.todoStatus === filterTodo.value);
  }
  if (filterTag.value) {
    const members = activeClusterMembers.value;
    base = base.filter(x => (x.tags || []).some(t => members.includes(t)));
  }
  // 排序
  const sorted = [...base];
  if (sortBy.value === 'updatedAt') {
    sorted.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  } else if (sortBy.value === 'createdAt') {
    sorted.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
  } else {
    sorted.sort((a, b) => (a.title || '').localeCompare(b.title || '', 'zh'));
  }
  return sorted;
});

/** 快捷统计 */
const statsBreakdown = computed(() => {
  const all = searchActive.value ? searchResults.value : items.value;
  const counts = {knowledge: 0, idea: 0, todo: 0};
  for (const item of all) {
    if (item.entryType in counts) counts[item.entryType as keyof typeof counts]++;
  }
  return counts;
});

// ── 无限滚动 ──────────────────────────────────────────────────────
/** 页面自身就是滚动容器（.nian-page overflow-y: scroll），不依赖 window 滚动 */
const selfRef = ref<HTMLElement | null>(null);

function onScrollNearBottom() {
  const el = selfRef.value;
  if (!el) return;
  const threshold = 300;
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - threshold) {
    loadMore();
  }
}

const columns = computed<NianFeedItem[][]>(() => {
  const cols: NianFeedItem[][] = Array.from({length: columnCount.value}, () => []);
  displayItems.value.forEach((it, i) => {
    cols[i % columnCount.value].push(it);
  });
  return cols;
});

async function runSearch(q: string) {
  const seq = ++searchSeq;
  searchLoading.value = true;
  try {
    const {data, error} = await fetchKbSearch(q, 30);
    if (seq !== searchSeq) return; // 旧请求丢弃
    if (!error && data) {
      searchResults.value = (data as KBEntry[])
        .filter((x) => !x.isArchived)
        .map((x) => x as NianFeedItem);
    } else {
      searchResults.value = [];
    }
  } finally {
    if (seq === searchSeq) searchLoading.value = false;
  }
}

watch(searchText, (v) => {
  const q = v.trim();
  if (searchDebounce) clearTimeout(searchDebounce);
  if (!q) {
    searchResults.value = [];
    searchLoading.value = false;
    searchSeq++; // 让 in-flight 失效
    return;
  }
  searchDebounce = setTimeout(() => runSearch(q), 220);
});

function closeSearch() {
  searchText.value = '';
  searchResults.value = [];
  searchLoading.value = false;
  searchSeq++;
  if (searchDebounce) {
    clearTimeout(searchDebounce);
    searchDebounce = null;
  }
}

function onSearchKey(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.preventDefault();
    closeSearch();
  }
}

onMounted(async () => {
  await reloadFeed();
  refreshTagClusters();
  updateColumnCount();
  loadCreditBalance();
  window.addEventListener('resize', updateColumnCount);
  window.addEventListener('scroll', onScrollNearBottom, {passive: true});
  // 来自全局搜索的跳转：?detail=<id>
  const did = route.query.detail;
  if (typeof did === 'string' && did) {
    openDetail(did);
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateColumnCount);
  window.removeEventListener('scroll', onScrollNearBottom);
});
</script>

<template>
  <div ref="selfRef" class="nian-page">
    <!-- aurora 背景（深蓝 + cyan） -->
    <div class="aurora" aria-hidden="true">
      <div class="aurora-orb aurora-1" />
      <div class="aurora-orb aurora-2" />
      <div class="aurora-orb aurora-3" />
      <div class="aurora-orb aurora-4" />
      <div class="aurora-grain" />
    </div>

    <header class="topbar">
      <div class="topbar-inner">
        <div class="brand">
          <div class="brand-mark">
            <span class="bm-glyph">库</span>
            <span class="bm-halo" />
          </div>
          <div class="brand-text">
            <span class="brand-zh">知识库</span>
            <span class="brand-en">{{ dateStr }}</span>
          </div>
          <div class="agent-badge">
            <span class="ab-pulse" />
            <span class="ab-text">AGENT · 待命</span>
          </div>
        </div>
        <div class="topbar-actions">
          <button
            v-if="isSuper"
            class="action-nav action-nav-admin"
            title="使用看板（仅管理员可见）"
            @click="goToDashboard"
          >
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" width="14" height="14">
              <path d="M3 3h6v8H3zM11 3h6v5h-6zM11 10h6v7h-6zM3 13h6v4H3z" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <span class="action-nav-text">看板</span>
          </button>
          <button
            class="action-nav"
            title="打开对话"
            @click="goToQA"
          >
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14">
              <path d="M12 5l-5 5 5 5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <span class="action-nav-text">对话</span>
          </button>
          <button
            class="action-nav"
            title="返回首页"
            @click="goToWorkbench"
          >
            <span class="action-nav-text">首页</span>
          </button>
          <!-- 内联搜索：图标按钮原地展开为输入框 -->
          <!-- 积分余额（非 unlimited 时显示） -->
          <div
            v-if="creditBalance && !creditBalance.isUnlimited"
            class="credit-badge"
            :style="{borderColor: creditColor + '44', color: creditColor}"
            :title="`已用 ${fmtCreditShort(creditBalance.used)} / 配额 ${fmtCreditShort(creditBalance.quota)} 积分`"
          >
            <div class="cb-bar-wrap">
              <div class="cb-bar-fill" :style="{width: creditPct + '%', background: creditColor}" />
            </div>
            <span class="cb-text">{{ fmtCreditShort(creditBalance.remaining) }}</span>
            <span class="cb-label">余额</span>
          </div>
          <button class="action action-primary" title="Cmd/Ctrl + K" @click="openInbox()">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
              <path d="M10 4v12M4 10h12" stroke-linecap="round" />
            </svg>
            <span class="action-text">万用收件箱</span>
            <kbd class="action-kbd">⌘K</kbd>
          </button>
        </div>
      </div>
    </header>

    <main class="main">
      <!-- 搜索 + 筛选卡片 -->
      <div class="toolbar-card">
        <!-- 第一行：搜索 + 统计 + 视图/排序 -->
        <div class="tb-row-main">
          <div class="tb-search">
            <svg class="tb-search-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14">
              <circle cx="9" cy="9" r="5.5" />
              <path d="M13 13l4 4" stroke-linecap="round" />
            </svg>
            <input
              ref="searchInputRef"
              v-model="searchText"
              class="tb-search-input"
              type="text"
              placeholder="搜索知识库…"
              @keydown="onSearchKey"
            />
            <span v-if="searchLoading" class="tb-search-spin" />
            <button v-if="searchText" class="tb-search-clear" @click="closeSearch">
              <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="2" width="10" height="10">
                <path d="M3 3l8 8M11 3l-8 8" stroke-linecap="round" />
              </svg>
            </button>
          </div>

          <!-- 快捷统计 -->
          <div class="tb-stats">
            <span class="tb-stat"><span class="tb-stat-n">{{ statsBreakdown.knowledge }}</span>知识</span>
            <span class="tb-stat"><span class="tb-stat-n">{{ statsBreakdown.idea }}</span>灵感</span>
            <span class="tb-stat"><span class="tb-stat-n">{{ statsBreakdown.todo }}</span>待办</span>
          </div>

          <!-- 排序 + 视图切换 -->
          <div class="tb-actions">
            <select v-model="sortBy" class="tb-sort-select">
              <option value="updatedAt">最近更新</option>
              <option value="createdAt">最近创建</option>
              <option value="title">按标题</option>
            </select>
            <div class="tb-view-toggle">
              <button :class="['view-btn', viewMode === 'masonry' && 'view-active']" title="瀑布流" @click="viewMode = 'masonry'">
                <svg viewBox="0 0 16 16" fill="currentColor" width="13" height="13">
                  <rect x="1" y="1" width="5" height="6" rx="1" /><rect x="1" y="9" width="5" height="6" rx="1" />
                  <rect x="8" y="1" width="7" height="4" rx="1" /><rect x="8" y="7" width="7" height="8" rx="1" />
                </svg>
              </button>
              <button :class="['view-btn', viewMode === 'list' && 'view-active']" title="列表" @click="viewMode = 'list'">
                <svg viewBox="0 0 16 16" fill="currentColor" width="13" height="13">
                  <rect x="1" y="2" width="14" height="2.5" rx="1" /><rect x="1" y="6.75" width="14" height="2.5" rx="1" /><rect x="1" y="11.5" width="14" height="2.5" rx="1" />
                </svg>
              </button>
            </div>
          </div>
        </div>

        <!-- 第二行：类型分段控件 -->
        <div class="tb-row-filter">
          <div class="tb-seg">
            <button :class="['seg-btn', filterType === 'all' && 'seg-active']" @click="filterType = 'all'">全部</button>
            <button :class="['seg-btn', 'seg-knowledge', filterType === 'knowledge' && 'seg-active']" @click="filterType = filterType === 'knowledge' ? 'all' : 'knowledge'">知识</button>
            <button :class="['seg-btn', 'seg-idea', filterType === 'idea' && 'seg-active']" @click="filterType = filterType === 'idea' ? 'all' : 'idea'">灵感</button>
            <button :class="['seg-btn', 'seg-todo', filterType === 'todo' && 'seg-active']" @click="filterType = filterType === 'todo' ? 'all' : 'todo'">待办</button>
          </div>

          <!-- 待办子筛选 -->
          <div v-if="filterType === 'todo'" class="tb-sub-seg">
            <button :class="['sub-btn', filterTodo === 'all' && 'sub-active']" @click="filterTodo = 'all'">全部</button>
            <button :class="['sub-btn', filterTodo === 'pending' && 'sub-active']" @click="filterTodo = filterTodo === 'pending' ? 'all' : 'pending'">进行中</button>
            <button :class="['sub-btn', filterTodo === 'done' && 'sub-active']" @click="filterTodo = filterTodo === 'done' ? 'all' : 'done'">已完成</button>
          </div>

          <span v-if="searchActive || filterType !== 'all' || filterTag" class="tb-count">{{ displayItems.length }} 条结果</span>
        </div>

        <!-- 标签聚类区 -->
        <div v-if="tagClusters.length" class="tb-tags-section">
          <div class="tb-tags-header">
            <input
              v-model="tagSearch"
              class="tb-tags-search"
              type="text"
              placeholder="搜索标签…"
            />
            <span class="tb-tags-total">{{ tagClusters.length }} 个标签组</span>
          </div>
          <div class="tb-tags">
            <button
              :class="['tag-pill', !filterTag && 'tag-active']"
              @click="filterTag = ''"
            >全部</button>
            <template v-for="cluster in visibleClusters" :key="cluster.canonical">
              <!-- 归并组：多个同义标签一起展示 -->
              <div v-if="cluster.size > 1" class="tag-group">
                <button
                  v-for="member in cluster.members"
                  :key="member"
                  :class="['tag-pill', filterTag === member && 'tag-active', member === cluster.canonical && 'tag-group-canonical']"
                  @click="selectMemberTag(member)"
                >
                  <span class="tag-hash">#</span>{{ member }}
                  <span class="tag-n">{{ cluster.memberCounts?.[member] ?? cluster.count }}</span>
                </button>
              </div>
              <!-- 单独标签 -->
              <button
                v-else
                :class="['tag-pill', filterTag === cluster.canonical && 'tag-active']"
                @click="selectMemberTag(cluster.canonical)"
              >
                <span class="tag-hash">#</span>{{ cluster.canonical }}
                <span class="tag-n">{{ cluster.count }}</span>
              </button>
            </template>
            <button
              v-if="!tagsExpanded && hiddenTagCount > 0"
              class="tag-pill tag-expand-btn"
              @click="tagsExpanded = true"
            >+{{ hiddenTagCount }}</button>
            <button
              v-else-if="tagsExpanded && filteredClusters.length > TAGS_VISIBLE_LIMIT"
              class="tag-pill tag-expand-btn"
              @click="tagsExpanded = false"
            >收起</button>
          </div>
        </div>
      </div>

      <div v-if="!searchActive && loading && !items.length" class="state-loading">
        <span class="dot dot-1" />
        <span class="dot dot-2" />
        <span class="dot dot-3" />
        <span class="state-text">载入中</span>
      </div>

      <div v-else-if="!searchActive && !items.length && !pendingItems.length" class="state-empty">
        <div class="empty-orb" />
        <div class="empty-title">知识库里还没有内容</div>
        <div class="empty-hint">按 ⌘K 打开万用收件箱，把第一条丢进来</div>
      </div>

      <div v-else-if="searchActive && !searchLoading && !displayItems.length" class="state-empty">
        <div class="empty-title">没找到相关条目</div>
        <div class="empty-hint">换个关键词或语义描述试试</div>
      </div>

      <!-- 整理中占位卡片（agent 处理期间显示，与瀑布流并存） -->
      <TransitionGroup v-if="pendingItems.length" tag="div" name="pending-fade" class="pending-row">
        <PendingCard
          v-for="p in pendingItems"
          :key="p.id"
          :item="p"
        />
      </TransitionGroup>

      <!-- 瀑布流视图 -->
      <div v-if="viewMode === 'masonry'" class="masonry">
        <div
          v-for="(col, ci) in columns"
          :key="ci"
          class="masonry-col"
        >
          <FeedCard
            v-for="it in col"
            :key="it.id"
            :item="it"
            @delete="deleteEntry"
            @open="openDetail"
          />
        </div>
      </div>

      <!-- 列表视图 -->
      <div v-else class="list-view">
        <div
          v-for="it in displayItems"
          :key="it.id"
          class="list-item"
          @click="openDetail(it.id)"
        >
          <span :class="['list-type-dot', `list-dot-${it.entryType}`]" />
          <span class="list-title">{{ it.title || '（无题）' }}</span>
          <span v-if="it.summary" class="list-summary">{{ it.summary }}</span>
          <span v-if="it.tags.length" class="list-tags">
            <span v-for="t in it.tags.slice(0, 3)" :key="t" class="list-tag">#{{ t }}</span>
          </span>
          <span class="list-time">{{ it.updatedAt ? new Date(it.updatedAt).toLocaleDateString('zh-CN', {month: 'short', day: 'numeric'}) : '' }}</span>
        </div>
      </div>

      <!-- 无限滚动加载指示器 -->
      <div v-if="loadingMore" class="load-more">
        <span class="dot dot-1" />
        <span class="dot dot-2" />
        <span class="dot dot-3" />
        <span class="load-more-text">加载更多…</span>
      </div>
      <div v-else-if="!hasMore && items.length > 30" class="load-more load-more-end">
        <span class="load-more-text">已加载全部 {{ total }} 条</span>
      </div>
    </main>

    <DetailModal
      v-model:show="detailShow"
      :item="detailItem"
      :drawer-open="qaDrawerOpen"
      @delete="deleteEntry"
      @open-related="openDetail"
      @qa="onDetailQA"
    />

    <!-- QA 问答抽屉 -->
    <Teleport to="body">
      <Transition name="qa-drawer-fade">
        <div v-if="qaDrawerOpen" class="qa-drawer-root">
          <Transition name="qa-drawer-slide">
            <div v-if="qaDrawerOpen" class="qa-drawer">
              <button class="qa-drawer-close" @click="qaDrawerOpen = false" title="关闭">
                <svg viewBox="0 0 14 14" fill="none" stroke="currentColor" stroke-width="2" width="22" height="22">
                  <path d="M3 3l8 8M11 3l-8 8" stroke-linecap="round" />
                </svg>
              </button>
              <Suspense>
                <QAGlass :key="qaDrawerKey" :prefill="qaPrefill" :embedded="true" />
                <template #fallback>
                  <div class="qa-drawer-loading">
                    <span class="dot dot-1" /><span class="dot dot-2" /><span class="dot dot-3" />
                  </div>
                </template>
              </Suspense>
            </div>
          </Transition>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>

/* ─── design tokens (QA 蓝色系) ─── */
.nian-page {
  --bg: #f5f7fb;
  --bg-deep: #eaf0f9;

  /* 玻璃质感（透明度更低，对比更强，看得见） */
  --surface: rgba(255, 255, 255, 0.42);
  --surface-strong: rgba(255, 255, 255, 0.62);
  --surface-deep: rgba(255, 255, 255, 0.78);
  --highlight: rgba(255, 255, 255, 0.95);
  --inner-shadow: rgba(30, 64, 175, 0.04);

  --border: rgba(30, 64, 175, 0.1);
  --border-strong: rgba(30, 64, 175, 0.18);
  --border-glow: rgba(30, 64, 175, 0.25);

  --ink: #0f172a;
  --ink-soft: #334155;
  --ink-mute: #64748b;
  --ink-faint: #94a3b8;

  /* 蓝青色板 */
  --c-deep: #1e3a8a;
  --c-blue: #1e40af;
  --c-blue-2: #2563eb;
  --c-sky: #0ea5e9;
  --c-cyan: #0891b2;
  --c-teal: #0e7490;
  --c-violet: #4f46e5;
  --c-mint: #10b981;

  --aurora: linear-gradient(110deg, var(--c-blue) 0%, var(--c-blue-2) 35%, var(--c-sky) 70%, var(--c-cyan) 100%);
  --aurora-soft: linear-gradient(110deg, rgba(30, 64, 175, 0.18) 0%, rgba(37, 99, 235, 0.16) 50%, rgba(8, 145, 178, 0.18) 100%);

  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 16px -8px rgba(30, 64, 175, 0.12);
  --shadow-md: 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px rgba(30, 64, 175, 0.18);
  --shadow-lg: 0 1px 2px rgba(15, 23, 42, 0.05), 0 24px 64px -20px rgba(30, 64, 175, 0.28);
  --shadow-glow: 0 8px 32px -10px rgba(30, 64, 175, 0.45);

  /* 自滚动容器：页面内部滚动、不产生文档级滚动条。
     overflow-y: scroll 恒定预留滚动条槽位（轨道透明），
     筛选导致内容高度临界变化时，滚动条出现/消失不再挤压内容宽度 */
  height: 100%;
  position: relative;
  overflow-x: hidden;
  overflow-y: scroll;
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
  color: var(--ink);
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* ─── aurora background ───
   性能约定：光球故意保持静态。blur(120px) 的大色块一旦动起来，
   所有压在它上面的半透明层都要每帧重合成，滚动帧率直接腰斩；
   静态模糊层只光栅化一次，近乎零成本 */
.aurora {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
}

.aurora-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
}

.aurora-1 {
  width: 680px;
  height: 680px;
  top: -200px;
  right: -160px;
  background: radial-gradient(circle, var(--c-blue) 0%, transparent 65%);
  opacity: 0.35;
}

.aurora-2 {
  width: 720px;
  height: 720px;
  bottom: -260px;
  left: -200px;
  background: radial-gradient(circle, var(--c-cyan) 0%, transparent 65%);
  opacity: 0.32;
}

.aurora-3 {
  width: 480px;
  height: 480px;
  top: 36%;
  right: 14%;
  background: radial-gradient(circle, var(--c-sky) 0%, transparent 65%);
  opacity: 0.3;
}

.aurora-4 {
  width: 420px;
  height: 420px;
  top: 18%;
  left: -60px;
  background: radial-gradient(circle, var(--c-violet) 0%, transparent 65%);
  opacity: 0.22;
}

.aurora-grain {
  position: absolute;
  inset: 0;
  background-image:
    radial-gradient(circle at 1px 1px, rgba(15, 23, 42, 0.05) 1px, transparent 0);
  background-size: 3px 3px;
  opacity: 0.4;
  mix-blend-mode: multiply;
}

/* ─── topbar：玻璃感重一些 ─── */
.topbar {
  position: sticky;
  top: 16px;
  z-index: 30;
  margin: 16px auto 0;
  max-width: 1180px;
  padding: 0 24px;
}

.topbar-inner {
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 9px 11px 9px 13px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 8px 24px -8px rgba(30, 64, 175, 0.18);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  position: relative;
  width: 34px;
  height: 34px;
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
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 16px;
  font-weight: 700;
  color: #fff;
  letter-spacing: -0.02em;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.18);
}

.bm-halo {
  position: absolute;
  inset: -2px;
  border-radius: 13px;
  background: var(--aurora);
  filter: blur(12px);
  opacity: 0.5;
  z-index: 0;
  animation: halo-breathe 3s ease-in-out infinite;
}

@keyframes halo-breathe {
  0%, 100% { opacity: 0.4; transform: scale(0.95); }
  50%      { opacity: 0.7; transform: scale(1.1); }
}

.brand-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  line-height: 1;
}

.brand-zh {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.02em;
  background: var(--aurora);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.brand-en {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--ink-mute);
}

.agent-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 4px;
  padding: 4px 10px 4px 8px;
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--ink-soft);
  letter-spacing: 0.02em;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
  transition: all 0.24s ease;
}

.ab-pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--c-mint);
  box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.5), 0 0 8px rgba(16, 185, 129, 0.5);
  animation: ab-pulse 2s infinite;
}

@keyframes ab-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.5), 0 0 8px rgba(16, 185, 129, 0.5); }
  70%  { box-shadow: 0 0 0 7px rgba(16, 185, 129, 0), 0 0 8px rgba(16, 185, 129, 0.5); }
  100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0), 0 0 8px rgba(16, 185, 129, 0.5); }
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-nav {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  color: var(--ink-soft);
  cursor: pointer;
  border-radius: 11px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.005em;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
  transition: all 0.2s ease;
}

.action-nav:hover {
  color: var(--c-blue);
  border-color: var(--border-glow);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    var(--shadow-glow);
  transform: translateY(-1px);
}

.action-nav-text {
  letter-spacing: 0.005em;
}

.action-nav-admin {
  color: #c9a043;
  border-color: rgba(201, 160, 67, 0.3);
  background: linear-gradient(180deg, rgba(201, 160, 67, 0.08), rgba(201, 160, 67, 0.02));
}

.action-nav-admin:hover {
  color: #d4af52;
  border-color: rgba(201, 160, 67, 0.55);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    0 0 0 3px rgba(201, 160, 67, 0.12);
}

.action-quiet {
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  color: var(--ink-soft);
  cursor: pointer;
  border-radius: 11px;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
  transition: all 0.2s ease;
}

.action-quiet:hover:not(:disabled) {
  color: var(--c-blue);
  border-color: var(--border-glow);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    var(--shadow-glow);
  transform: translateY(-1px);
}

.action-quiet:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.action-quiet:disabled svg {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.action {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 13px 7px 11px;
  border: 1px solid transparent;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink);
  cursor: pointer;
  border-radius: 11px;
  transition: all 0.2s ease;
  letter-spacing: 0.005em;
}

.action-primary {
  background: var(--aurora);
  color: #fff;
  position: relative;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 4px 14px -2px rgba(30, 64, 175, 0.45);
}

.action-primary::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 11px;
  background: var(--aurora);
  filter: blur(14px);
  opacity: 0.45;
  z-index: -1;
  transition: opacity 0.3s;
}

.action-primary:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 6px 20px -2px rgba(30, 64, 175, 0.55);
}

.action-primary:hover:not(:disabled)::before {
  opacity: 0.7;
}

.action-text {
  letter-spacing: 0.005em;
}

.action-kbd {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9.5px;
  font-weight: 600;
  letter-spacing: 0.02em;
  margin-left: 4px;
  padding: 2px 6px;
  background: rgba(255, 255, 255, 0.2);
  border: 1px solid rgba(255, 255, 255, 0.32);
  border-radius: 5px;
}

/* ─── 搜索 + 筛选卡片 ─── */
.toolbar-card {
  margin-bottom: 20px;
  padding: 14px 16px 10px;
  background: var(--surface-strong);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 2px 12px -6px rgba(30, 64, 175, 0.1);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tb-row-main {
  display: flex;
  align-items: center;
  gap: 10px 12px;
  flex-wrap: wrap;
}
.tb-row-filter {
  display: flex;
  align-items: center;
  gap: 8px 12px;
  flex-wrap: wrap;
}

/* 搜索框 */
.tb-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1 1 260px;
  min-width: 200px;
  height: 36px;
  padding: 0 12px;
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 10px;
  transition: border-color 0.18s, box-shadow 0.18s;
}
.tb-search:focus-within {
  border-color: var(--c-blue);
  box-shadow: 0 0 0 3px rgba(30, 64, 175, 0.08);
}
.tb-search-icon {
  color: var(--c-blue);
  flex-shrink: 0;
  opacity: 0.55;
}
.tb-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  outline: none;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}
.tb-search-input::placeholder {
  color: var(--ink-faint);
  font-weight: 500;
}
.tb-search-spin {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 1.5px solid rgba(30, 64, 175, 0.18);
  border-top-color: var(--c-blue);
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}
.tb-search-clear {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: var(--ink-mute);
  cursor: pointer;
  border-radius: 5px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.tb-search-clear:hover {
  background: rgba(30, 64, 175, 0.08);
  color: var(--c-blue);
}

/* 类型分段控件 */
.tb-seg {
  display: inline-flex;
  align-items: center;
  padding: 2px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  gap: 1px;
}
.seg-btn {
  padding: 5px 14px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-mute);
  background: transparent;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.seg-btn:hover {
  color: var(--ink-soft);
  background: rgba(255, 255, 255, 0.6);
}
.seg-btn.seg-active {
  color: #fff;
  background: var(--c-blue);
  box-shadow: 0 1px 4px rgba(30, 64, 175, 0.25);
}
.seg-idea.seg-active { background: #7c3aed; box-shadow: 0 1px 4px rgba(124, 58, 237, 0.25); }
.seg-todo.seg-active  { background: #d97706; box-shadow: 0 1px 4px rgba(217, 119, 6, 0.25); }

/* 待办子筛选 */
.tb-sub-seg {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding-left: 4px;
  border-left: 1px solid var(--border);
}
.sub-btn {
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-mute);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
}
.sub-btn:hover { color: var(--c-blue); }
.sub-btn.sub-active {
  color: var(--c-blue);
  background: rgba(30, 64, 175, 0.08);
  border-color: rgba(30, 64, 175, 0.15);
}

/* 标签聚类区 */
.tb-tags-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tb-tags-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tb-tags-search {
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 11px;
  font-weight: 500;
  color: var(--ink);
  outline: none;
  width: 160px;
  transition: border-color 0.15s, width 0.2s;
}
.tb-tags-search:focus {
  border-color: var(--c-blue);
  width: 220px;
}
.tb-tags-search::placeholder {
  color: var(--ink-faint);
}
.tb-tags-total {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--ink-faint);
  white-space: nowrap;
}
.tb-tags {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
  padding: 2px 0;
}
.tag-pill {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px 10px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 11px;
  font-weight: 500;
  color: var(--ink-mute);
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
  flex-shrink: 0;
}
.tag-pill:hover {
  border-color: rgba(30, 64, 175, 0.25);
  color: var(--c-blue);
}
.tag-pill.tag-active {
  color: #fff;
  background: var(--c-blue);
  border-color: var(--c-blue);
}
.tag-pill.tag-active .tag-n,
.tag-pill.tag-active .tag-hash {
  color: rgba(255, 255, 255, 0.7);
}
.tag-hash {
  color: var(--ink-faint);
  font-weight: 400;
  margin-right: 1px;
}
.tag-n {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  font-weight: 600;
  color: var(--ink-faint);
  margin-left: 3px;
}

/* 标签归并组：同义标签归为一组，各自可见 */
.tag-group {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 6px;
  border-radius: 14px;
  background: rgba(30, 64, 175, 0.03);
  border: 1px solid rgba(30, 64, 175, 0.08);
  flex-shrink: 0;
}
.tag-group .tag-pill {
  padding: 2px 7px;
  font-size: 10.5px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 8px;
}
.tag-group .tag-pill:hover {
  background: rgba(30, 64, 175, 0.06);
  border-color: rgba(30, 64, 175, 0.12);
}
.tag-group .tag-pill.tag-group-canonical {
  font-weight: 600;
}
.tag-group .tag-pill.tag-active {
  background: var(--c-blue);
  border-color: var(--c-blue);
  color: #fff;
}
.tag-expand-btn {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  color: var(--c-blue);
  border-color: rgba(30, 64, 175, 0.2);
  background: rgba(30, 64, 175, 0.04);
}
.tag-expand-btn:hover {
  background: rgba(30, 64, 175, 0.1);
}

/* 结果计数 */
.tb-count {
  margin-left: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-faint);
  white-space: nowrap;
}

/* 快捷统计 */
.tb-stats {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.tb-stat {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 11px;
  font-weight: 500;
  color: var(--ink-mute);
  white-space: nowrap;
}
.tb-stat-n {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 700;
  color: var(--ink-soft);
}

/* 排序 + 视图切换 */
.tb-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  flex-shrink: 0;
}
.tb-sort-select {
  height: 30px;
  padding: 0 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-mute);
  cursor: pointer;
  outline: none;
  transition: border-color 0.15s;
  appearance: none;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg width='8' height='5' viewBox='0 0 8 5' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1l3 3 3-3' stroke='%2394a3b8' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 8px center;
  padding-right: 22px;
}
.tb-sort-select:hover { border-color: var(--border-strong); }
.tb-sort-select:focus { border-color: var(--c-blue); }
.tb-view-toggle {
  display: inline-flex;
  align-items: center;
  padding: 2px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  gap: 1px;
}
.view-btn {
  width: 28px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--ink-faint);
  cursor: pointer;
  transition: all 0.15s;
}
.view-btn:hover { color: var(--ink-mute); }
.view-btn.view-active {
  color: var(--c-blue);
  background: rgba(30, 64, 175, 0.08);
}

/* ─── 列表视图 ─── */
.list-view {
  display: flex;
  flex-direction: column;
  padding: 4px;
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 0 4px 12px rgba(0, 0, 0, 0.03);
}
.list-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  cursor: pointer;
  transition: background 0.12s;
  border-bottom: 1px solid rgba(0, 0, 0, 0.04);
}
.list-item:last-child { border-bottom: none; }
.list-item:hover { background: rgba(30, 64, 175, 0.03); }
.list-type-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--c-blue);
}
.list-dot-knowledge { background: #1e40af; }
.list-dot-idea { background: #7c3aed; }
.list-dot-todo { background: #d97706; }
.list-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 280px;
  flex-shrink: 0;
}
.list-summary {
  font-size: 12px;
  color: var(--ink-mute);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}
.list-tags {
  display: inline-flex;
  gap: 4px;
  flex-shrink: 0;
}
.list-tag {
  font-size: 10px;
  font-weight: 500;
  color: var(--c-blue);
  opacity: 0.6;
}
.list-time {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--ink-faint);
  flex-shrink: 0;
  margin-left: auto;
}

/* ─── 无限滚动加载指示器 ─── */
.load-more {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 24px 0 40px;
}
.load-more-end {
  opacity: 0.5;
}
.load-more-text {
  font-size: 12px;
  color: var(--ink-faint);
}

/* ─── main ─── */
.main {
  position: relative;
  z-index: 1;
  max-width: 1180px;
  margin: 0 auto;
  padding: 28px 24px 100px;
}

/* ─── 瀑布流：JS round-robin 分列，flex 横向排列 ─── */
/* ── 整理中占位卡片行 ── */
.pending-row {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-bottom: 14px;
}
.pending-row > * {
  flex: 0 0 calc(33.333% - 10px);
  min-width: 220px;
  max-width: 360px;
}
.pending-fade-enter-active {
  transition: opacity 0.3s ease, transform 0.3s cubic-bezier(0.32, 0.72, 0, 1);
}
.pending-fade-leave-active {
  transition: opacity 0.4s ease, transform 0.4s ease;
}
.pending-fade-enter-from {
  opacity: 0;
  transform: translateY(-8px) scale(0.97);
}
.pending-fade-leave-to {
  opacity: 0;
  transform: scale(0.95);
}

.masonry {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.masonry-col {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* ─── loading / empty ─── */
.state-loading,
.state-empty {
  padding: 80px 0;
  text-align: center;
}

.state-loading {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 8px;
}

.state-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.16em;
  color: var(--ink-mute);
  margin-left: 12px;
  text-transform: uppercase;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  animation: nian-bounce 1.2s ease-in-out infinite;
}

.dot-1 { background: var(--c-blue); box-shadow: 0 0 12px rgba(30, 64, 175, 0.5); }
.dot-2 { background: var(--c-sky); animation-delay: 0.18s; box-shadow: 0 0 12px rgba(14, 165, 233, 0.5); }
.dot-3 { background: var(--c-cyan); animation-delay: 0.36s; box-shadow: 0 0 12px rgba(8, 145, 178, 0.5); }

@keyframes nian-bounce {
  0%, 80%, 100% { transform: translateY(0) scale(0.8); opacity: 0.5; }
  40%           { transform: translateY(-8px) scale(1); opacity: 1; }
}

.empty-orb {
  width: 88px;
  height: 88px;
  border-radius: 50%;
  margin: 0 auto 20px;
  background: var(--aurora);
  position: relative;
  animation: empty-breathe 3s ease-in-out infinite;
}

.empty-orb::before {
  content: '';
  position: absolute;
  inset: -10px;
  border-radius: 50%;
  background: var(--aurora);
  filter: blur(20px);
  opacity: 0.5;
  z-index: -1;
}

@keyframes empty-breathe {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.08); }
}

.empty-title {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 19px;
  font-weight: 700;
  color: var(--ink);
  margin-bottom: 6px;
  letter-spacing: -0.01em;
}

.empty-hint {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-mute);
}

/* ─── 移动端：顶栏压缩，隐藏次要元素 ─── */
@media (max-width: 720px) {
  .topbar {
    margin-top: 10px;
    padding: 0 12px;
  }
  .topbar-inner {
    padding: 7px 8px 7px 9px;
    gap: 8px;
  }
  .brand { gap: 8px; }
  .brand-text { display: none; }
  .agent-badge { display: none; }
  .topbar-actions { gap: 6px; }
  .action-nav {
    height: 32px;
    padding: 0 9px;
    gap: 0;
  }
  .action-nav-text { display: none; }
  .action-quiet {
    width: 32px;
    height: 32px;
  }
  .search-field {
    width: 56vw;
    height: 32px;
  }
  @keyframes sf-grow {
    from { width: 32px; opacity: 0.6; }
    to   { width: 56vw; opacity: 1; }
  }
  .action {
    padding: 6px 10px;
  }
  .action-text { display: none; }
  .action-kbd { display: none; }
  .main {
    padding: 18px 12px 80px;
  }
}

@media (max-width: 380px) {
  .brand-mark { width: 30px; height: 30px; border-radius: 9px; }
  .bm-glyph { font-size: 14px; }
}

/* ─── 积分余额 badge ─── */
.credit-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid;
  background: var(--surface-strong);
  border-radius: 10px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  box-shadow: inset 0 1px 0 var(--highlight);
}
.cb-bar-wrap {
  width: 28px; height: 3px;
  border-radius: 2px;
  background: rgba(0,0,0,0.08);
  overflow: hidden;
  flex-shrink: 0;
}
.cb-bar-fill { height: 100%; border-radius: 2px; transition: width 0.4s ease; }
.cb-text {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 12px;
}
.cb-label { opacity: 0.65; font-size: 10px; }

@media (max-width: 720px) {
  .cb-label { display: none; }
  .cb-bar-wrap { display: none; }
}
</style>

<style>
/* QA 抽屉（Teleport 到 body，不能 scoped） */
.qa-drawer-root {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  z-index: 3000;
  pointer-events: none;
}
.qa-drawer {
  position: relative;
  width: min(860px, 80vw);
  height: 100vh;
  background: #f5f7fb;
  box-shadow: -8px 0 40px rgba(0, 0, 0, 0.12);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  pointer-events: auto;
}
.qa-drawer .qa-shell {
  height: 100%;
  border-radius: 0;
}
.qa-drawer-close {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 10;
  border: none;
  background: transparent;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #94a3b8;
  transition: color 0.15s;
}
.qa-drawer-close:hover {
  color: #475569;
}
.qa-drawer-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 6px;
}
.qa-drawer-loading .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #94a3b8;
  animation: nian-dot-pulse 1.2s ease-in-out infinite;
}
.qa-drawer-loading .dot:nth-child(2) { animation-delay: 0.15s; }
.qa-drawer-loading .dot:nth-child(3) { animation-delay: 0.3s; }
/* transitions */
.qa-drawer-fade-enter-active { transition: opacity 0.2s ease; }
.qa-drawer-fade-leave-active { transition: opacity 0.25s ease; }
.qa-drawer-fade-enter-from,
.qa-drawer-fade-leave-to { opacity: 0; }
.qa-drawer-slide-enter-active { transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1); }
.qa-drawer-slide-leave-active { transition: transform 0.25s ease-in; }
.qa-drawer-slide-enter-from { transform: translateX(100%); }
.qa-drawer-slide-leave-to { transform: translateX(100%); }
</style>
