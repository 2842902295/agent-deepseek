<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { NPopconfirm, NSelect, NSwitch } from 'naive-ui';
import type { AgentConnector, AuthorFacet, ManageFilterPatch, ManageStatus } from '@/service/api';
import { fetchUpdateAgentConnector } from '@/service/api';
import { customIconHtml } from '../skill-icon';

/** 连接器列表视图：三种模式复用同一组件（商店/我的/上架管理），视觉语言与 SkillListView 同源。
 *  数据集维度复用本组件：label/intro 换文案，数据按 kind 在 SkillPanel 侧过滤 */
const props = defineProps<{
  /** 已由 SkillPanel 按页面做过语义过滤的数组 */
  connectors: AgentConnector[];
  loading: boolean;
  myUserId: number | null;
  isAdmin: boolean;
  mode: 'store' | 'mine' | 'manage';
  highlightKey?: string | null;
  /** 商店视图专用：搜索词由 SkillPanel 头部输入框驱动（其余视图用内部搜索框） */
  search?: string;
  /** 实体称谓：连接器（默认）/ 数据集 */
  label?: string;
  /** 空态引导文案（store/manage）：默认连接器口径 */
  intro?: string;
  /** 服务端分页模式（仅上架管理）：筛选/排序由服务端完成，本地直通消费已加载数据 */
  serverPaged?: boolean;
  /** serverPaged：服务端命中总数 */
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
  /** 商店：添加到我的连接器 */
  add: [connector: AgentConnector];
  /** 我的：个人启用/禁用 */
  toggle: [connector: AgentConnector];
  /** 我的：移除出我的连接器 */
  remove: [connector: AgentConnector];
  /** 上架管理：上/下架完成（弹窗内已调接口，通知面板刷新） */
  changed: [];
  /** 上架管理：批量上架/下架（管理员） */
  batchShelf: [keys: string[], enabled: boolean];
  /** 上架管理：批量删除（批量条内已二次确认） */
  batchDelete: [keys: string[]];
  /** 编辑（本人/管理员） */
  edit: [connector: AgentConnector];
  /** 删除（卡片内已确认） */
  delete: [connector: AgentConnector];
  /** 供给动作（仅我的连接器工具栏）：添加连接器 */
  create: [];
  /** 我的连接器空态：跳回商店 */
  goStore: [];
  refresh: [];
  /** serverPaged：筛选条件变化（搜索词已防抖），父级据此回第 1 页重取 */
  filterChange: [patch: ManageFilterPatch];
  /** serverPaged：触底加载更多 */
  loadMore: [];
}>();

const innerSearch = ref('');
const effectiveSearch = computed(() => (props.mode === 'store' ? props.search || '' : innerSearch.value));

/** serverPaged 专用：上架状态筛选（无精选维度）；作者筛选。与技能侧同口径 */
const filterStatus = ref<ManageStatus>('all');
/** null=全部；0=官方桶（与后端 user_id=0 → user_id 为空闭环） */
const filterAuthor = ref<number | null>(null);
/** 滚动容器（筛选发射回顶 + 触底监听；声明前置供 emitFilter/onScrollArea 引用） */
const scrollRef = ref<HTMLElement | null>(null);

function emitFilter() {
  emit('filterChange', {
    keyword: innerSearch.value.trim(),
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

/** 实体称谓（连接器/数据集）与空态引导文案 */
const label = computed(() => props.label || '连接器');
const intro = computed(() => props.intro || `添加 MCP 服务${label.value}后，agent 对话中会自动加载它的工具`);

function matchesSearch(c: AgentConnector): boolean {
  const q = effectiveSearch.value.trim().toLowerCase();
  return (
    !q ||
    c.name.toLowerCase().includes(q) ||
    c.url.toLowerCase().includes(q) ||
    (c.description || '').toLowerCase().includes(q)
  );
}

function isMine(c: AgentConnector): boolean {
  return props.myUserId != null && c.userId === props.myUserId;
}

/** 图标：svg 源码 / 图片 data URI（同技能），无图标回落插头图形 */
function iconHtmlOf(c: AgentConnector): string {
  return customIconHtml(c.icon);
}

/** host 展示（不裸露完整 URL，避免 query 里带敏感参数） */
function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

const filtered = computed(() => {
  // serverPaged：服务端已筛已排（-is_enabled, id），本地直通
  if (props.serverPaged) return props.connectors;
  const list = props.connectors.filter(matchesSearch);
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

const enabledCount = computed(() => props.connectors.filter(c => c.userEnabled).length);
const shelfCount = computed(() => props.connectors.filter(c => c.isEnabled).length);
const hasFilter = computed(
  () =>
    effectiveSearch.value.trim() !== '' ||
    (props.serverPaged && (filterStatus.value !== 'all' || filterAuthor.value != null))
);

// ── 上架/下架确认（上架可选凭据处理：保留共享/各自填写/带上我的凭据） ─────────
const shelfTarget = ref<AgentConnector | null>(null);
/** 「带上我的凭据」可用性：操作者已为该连接器填过个人凭据 */
const hasMyShelfCred = computed(() => !!shelfTarget.value?.hasMyKey);
type ShelfCredChoice = 'keep' | 'none' | 'mine';
const shelfCredChoice = ref<ShelfCredChoice>('none');
const shelfSaving = ref(false);
const unshelfTarget = ref<AgentConnector | null>(null);

/** 开关拦截：上架走确认弹窗；下架走下架确认 */
function onShelfSwitch(c: AgentConnector, val: boolean) {
  if (val) {
    // 已有共享凭据（编辑里配置过）默认保留；否则默认「各自填写」
    shelfCredChoice.value = c.hasSharedKey ? 'keep' : 'none';
    shelfTarget.value = c;
  } else {
    unshelfTarget.value = c;
  }
}

async function confirmShelf() {
  const c = shelfTarget.value;
  if (!c || shelfSaving.value) return;
  shelfSaving.value = true;
  try {
    const { data, error } = await fetchUpdateAgentConnector(c.id, {
      is_enabled: true,
      with_credential: shelfCredChoice.value
    });
    if (!error && data) {
      shelfTarget.value = null;
      const tip =
        shelfCredChoice.value === 'mine'
          ? '已上架，凭据将共享给所有用户'
          : shelfCredChoice.value === 'keep'
            ? '已上架，保留共享凭据'
            : '已上架，用户各自填写凭据';
      window.$message?.success(tip);
      emit('changed');
    } else {
      window.$message?.error('上架失败');
    }
  } finally {
    shelfSaving.value = false;
  }
}

async function confirmUnshelf() {
  const c = unshelfTarget.value;
  if (!c) return;
  const { data, error } = await fetchUpdateAgentConnector(c.id, { is_enabled: false });
  if (!error && data) {
    unshelfTarget.value = null;
    window.$message?.success('已下架');
    emit('changed');
  } else {
    window.$message?.error('下架失败');
  }
}

// ── 批量管理（上架管理页：批量上架/下架/删除） ──────────────────
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
function toggleSelect(c: AgentConnector) {
  const next = new Set(selectedKeys.value);
  if (next.has(c.connectorKey)) next.delete(c.connectorKey);
  else next.add(c.connectorKey);
  selectedKeys.value = next;
}
/** 点卡片 = 进详情。连接器/数据集无独立详情页，表单即详情：
 *  创建者/管理员进完整编辑，其余用户进个人凭据设置（见 SkillPanel::onConnEdit 的模式判定） */
function onCardClick(c: AgentConnector) {
  if (batchMode.value) {
    toggleSelect(c);
    return;
  }
  emit('edit', c);
}
function toggleSelectFilteredAll() {
  const keys = filtered.value.map(c => c.connectorKey);
  const allSelected = keys.length > 0 && keys.every(k => selectedKeys.value.has(k));
  const next = new Set(selectedKeys.value);
  if (allSelected) keys.forEach(k => next.delete(k));
  else keys.forEach(k => next.add(k));
  selectedKeys.value = next;
}
const filteredAllSelected = computed(() => {
  const keys = filtered.value.map(c => c.connectorKey);
  return keys.length > 0 && keys.every(k => selectedKeys.value.has(k));
});
function applyBatchShelf(enabled: boolean) {
  const keys = Array.from(selectedKeys.value);
  if (!keys.length) return;
  emit('batchShelf', keys, enabled);
  selectedKeys.value = new Set();
}
function applyBatchDelete() {
  const keys = Array.from(selectedKeys.value);
  if (!keys.length) return;
  emit('batchDelete', keys);
  selectedKeys.value = new Set();
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
    filterStatus.value = 'all';
    filterAuthor.value = null;
    exitBatchMode();
    scrollRef.value?.scrollTo({ top: 0 });
  }
);
</script>

<template>
  <div class="cn-list">
    <!-- 工具栏（我的 / 上架管理）：商店搜索在面板头部 -->
    <div v-if="mode !== 'store'" class="cn-toolbar">
      <div class="cn-search">
        <svg class="cn-search-icon" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor">
          <circle cx="7" cy="7" r="4.5" stroke-width="1.6" />
          <path d="M10.5 10.5L14 14" stroke-width="1.6" stroke-linecap="round" />
        </svg>
        <input v-model="innerSearch" class="cn-search-input" :placeholder="`搜索${label}名称或地址…`" />
        <button v-if="innerSearch" class="cn-search-clear" @click="innerSearch = ''">
          <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.8" stroke-linecap="round" /></svg>
        </button>
      </div>
      <div v-if="mode === 'mine'" class="cn-supply">
        <button class="cn-supply-btn cn-supply-btn--primary" @click="emit('create')">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
          添加{{ label }}
        </button>
      </div>
      <!-- 上架管理：批量管理入口（批量上架/下架/删除） -->
      <button
        v-if="mode === 'manage'"
        class="cn-supply-btn"
        :class="{ 'cn-supply-btn--on': batchMode }"
        :title="batchMode ? '退出批量管理' : `批量上架/下架/删除${label}（影响全员）`"
        @click="batchMode ? exitBatchMode() : enterBatchMode()"
      >
        批量管理
      </button>
    </div>
    <!-- serverPaged（上架管理）：状态筛选 + 作者筛选（搜索词在上方输入框，防抖发射） -->
    <div v-if="serverPaged" class="cn-filterrow">
      <div class="cn-chip-group">
        <button
          v-for="st in statusChips"
          :key="st.value"
          class="cn-chip"
          :class="{ 'cn-chip--on': filterStatus === st.value }"
          title="再点一次取消该状态筛选"
          @click="toggleStatus(st.value)"
        >
          {{ st.label }}
        </button>
      </div>
      <NSelect
        :value="filterAuthor"
        class="cn-author-select"
        size="small"
        :options="authorSelectOptions"
        filterable
        clearable
        placeholder="全部作者"
        @update:value="onAuthorChange"
      />
    </div>

    <!-- 加载态 -->
    <div v-if="loading && connectors.length === 0" class="cn-loading">
      <span class="cn-loading-spin" />
      <span class="cn-loading-text">正在加载{{ label }}…</span>
    </div>

    <!-- 空态 -->
    <div v-else-if="filtered.length === 0" class="cn-empty">
      <div class="cn-empty-title">
        {{ hasFilter ? `没有匹配的${label}` : mode === 'mine' ? `还没添加${label}` : mode === 'manage' ? `还没有${label}` : `商店里还没有${label}` }}
      </div>
      <div class="cn-empty-hint">
        {{
          hasFilter
            ? '试试调整搜索条件'
            : mode === 'mine'
              ? `去${label}商店逛逛，把需要的 MCP 服务添加进来`
              : intro
        }}
      </div>
      <template v-if="!hasFilter && mode === 'mine'">
        <button class="cn-empty-btn" @click="emit('goStore')">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M2 6h12M3.5 6l1-3h9l1 3M4 6v7h8V6" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
          去逛逛
        </button>
        <button class="cn-empty-btn cn-empty-btn--ghost" @click="emit('create')">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
          添加{{ label }}
        </button>
      </template>
      <button v-else-if="!hasFilter && mode !== 'store'" class="cn-empty-btn" @click="emit('create')">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
        添加{{ label }}
      </button>
    </div>

    <!-- 卡片网格（serverPaged：触底向服务端要下一页） -->
    <div v-else ref="scrollRef" class="cn-scroll" @scroll="onScrollArea">
      <div class="cn-grid">
        <div
          v-for="c in filtered"
          :key="c.id"
          class="cn-card"
          :class="{ 'cn-card--hl': highlightKey === c.connectorKey, 'cn-card--sel': batchMode && selectedKeys.has(c.connectorKey) }"
          @click="onCardClick(c)"
        >
          <div class="cn-card-head">
            <span
              v-if="mode === 'manage' && batchMode"
              class="cn-check"
              :class="{ 'cn-check--on': selectedKeys.has(c.connectorKey) }"
              @click.stop="toggleSelect(c)"
            >
              <svg v-if="selectedKeys.has(c.connectorKey)" width="9" height="9" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M3 8.5l3.2 3L13 4.5" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" /></svg>
            </span>
            <span class="cn-icon" :class="{ 'cn-icon--custom': iconHtmlOf(c) }" aria-hidden="true">
              <span v-if="iconHtmlOf(c)" v-html="iconHtmlOf(c)"></span>
              <svg v-else width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M6 2v3M10 2v3" />
                <path d="M4 5h8v2.5a4 4 0 0 1-4 4 4 4 0 0 1-4-4V5z" />
                <path d="M8 11.5V14" />
              </svg>
            </span>
            <span class="cn-name" :title="c.name">{{ c.name }}</span>
            <span v-if="isMine(c)" class="cn-badge cn-badge--mine">我的</span>
            <!-- 个人凭据状态仅「我的」页提示（本人可操作的状态）；凭据类型标签不上卡片 -->
            <template v-if="mode === 'mine' && c.credentialMode === 'personal'">
              <span v-if="c.hasMyKey" class="cn-badge cn-badge--key" title="已填写我的个人凭据">我的凭据</span>
              <span v-else class="cn-badge cn-badge--nokey" title="还没填写个人凭据：点「配置凭据」填写后才能使用">待配置凭据</span>
            </template>
            <!-- 商店视图常驻槽位：未添加=「+」/ 已添加=绿勾（与技能卡片同源） -->
            <span v-if="mode === 'store'" class="cn-slot" @click.stop>
              <button v-if="!c.isAdded" class="cn-slot-icon cn-slot-icon--add" :title="`添加到我的${label}`" @click="emit('add', c)">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-linecap="round"><path d="M8 3v10M3 8h10" stroke-width="1.8" /></svg>
              </button>
              <span v-else class="cn-slot-icon cn-slot-icon--added" :title="`已添加到我的${label}`">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.5 3.5L13 4.5" stroke-width="2" /></svg>
              </span>
            </span>
          </div>
          <div class="cn-host" :title="c.url">
            {{ hostOf(c.url) }}
            <!-- 上架管理：全用户的条目混排，作者归属是必要信息 -->
            <template v-if="mode === 'manage'"> · {{ c.author || '官方' }}</template>
          </div>
          <p class="cn-desc">{{ c.description || '暂无描述' }}</p>

          <!-- 商店模式的「添加」已移到卡片头部右上角（+ 槽位），底部无操作 -->
          <div v-if="mode !== 'store'" class="cn-actions">
            <!-- 我的：启停 + 移除 + 编辑 -->
            <template v-if="mode === 'mine'">
              <label class="cn-switch" :title="c.userEnabled ? `已启用：agent 会加载该${label}的工具` : '已禁用：agent 不加载'" @click.stop>
                <NSwitch :value="c.userEnabled" size="small" @update:value="emit('toggle', c)" />
                <span>{{ c.userEnabled ? '已启用' : '已禁用' }}</span>
              </label>
              <button v-if="isMine(c) || isAdmin" class="cn-btn" @click.stop="emit('edit', c)">编辑</button>
              <button v-else class="cn-btn" @click.stop="emit('edit', c)">配置凭据</button>
              <button class="cn-btn cn-btn--danger" @click.stop="emit('remove', c)">移除</button>
            </template>
            <!-- 上架管理：上架开关 + 编辑 + 删除（批量模式下收起，用底栏批量条操作） -->
            <template v-else-if="!batchMode">
              <label class="cn-switch" :title="c.isEnabled ? '已上架：全员商店可见' : '未上架：仅创建者可见'" @click.stop>
                <NSwitch :value="c.isEnabled" size="small" :disabled="!isAdmin" @update:value="v => onShelfSwitch(c, v)" />
                <span>{{ c.isEnabled ? '已上架' : '未上架' }}</span>
              </label>
              <button class="cn-btn" @click.stop="emit('edit', c)">编辑</button>
              <NPopconfirm positive-text="删除" negative-text="取消" @positive-click="emit('delete', c)">
                <template #default>删除{{ label }}「{{ c.name }}」后所有人的添加记录一并清除，确定吗？</template>
                <template #trigger>
                  <button class="cn-btn cn-btn--danger" @click.stop>删除</button>
                </template>
              </NPopconfirm>
            </template>
          </div>
        </div>
      </div>
      <!-- 滚动加载提示（仅 serverPaged） -->
      <div v-if="hasMore || (serverPaged && loadingMore)" class="cn-load-more">
        {{ serverPaged && loadingMore ? '正在加载…' : '下滑加载更多…' }}
      </div>
    </div>

    <!-- 底栏：批量模式下变为批量操作条 -->
    <div class="cn-footer" :class="{ 'cn-footer--batch': batchMode }">
      <template v-if="batchMode">
        <div class="cn-batch-left">
          <button class="cn-batch-btn" @click="toggleSelectFilteredAll">
            {{ filteredAllSelected ? '取消全选' : serverPaged ? '全选已加载' : '全选当前' }}
          </button>
          <span class="cn-batch-count">已选 {{ selectedKeys.size }} 项</span>
        </div>
        <div class="cn-batch-right">
          <button class="cn-batch-btn cn-batch-btn--on" :disabled="!selectedKeys.size" @click="applyBatchShelf(true)">批量上架</button>
          <button class="cn-batch-btn cn-batch-btn--off" :disabled="!selectedKeys.size" @click="applyBatchShelf(false)">批量下架</button>
          <NPopconfirm
            positive-text="删除"
            negative-text="取消"
            :disabled="!selectedKeys.size"
            @positive-click="applyBatchDelete"
          >
            <template #default>确定彻底删除选中的 {{ selectedKeys.size }} 个{{ label }}吗？所有人的添加记录一并清除。</template>
            <template #trigger>
              <button class="cn-batch-btn cn-batch-btn--off" :disabled="!selectedKeys.size">批量删除</button>
            </template>
          </NPopconfirm>
          <button class="cn-batch-btn" @click="exitBatchMode">退出</button>
        </div>
      </template>
      <template v-else>
        <span v-if="mode === 'store'" class="cn-footer-count">{{ filtered.length }} / {{ connectors.length }} 个{{ label }}</span>
        <span v-else-if="mode === 'mine'" class="cn-footer-count">已添加 {{ connectors.length }} · 启用 {{ enabledCount }}</span>
        <!-- serverPaged：上/下架分列计数依赖全量行，分页下改为「总数 · 已加载」 -->
        <span v-else-if="serverPaged" class="cn-footer-count">共 {{ pagedTotal ?? 0 }} · 已加载 {{ connectors.length }}</span>
        <span v-else class="cn-footer-count">共 {{ connectors.length }} · 上架 {{ shelfCount }} · 未上架 {{ connectors.length - shelfCount }}</span>
        <button class="cn-footer-refresh" :disabled="loading" @click="emit('refresh')">
          <svg class="cn-refresh-icon" :class="{ 'cn-refresh-spin': loading }" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor">
            <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" stroke-width="1.6" stroke-linecap="round" />
            <path d="M13.7 1.8v2.6h-2.6" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          刷新
        </button>
      </template>
    </div>
    <!-- ─── 上架确认：是否带上我的凭据（默认不带） ─── -->
    <div v-if="shelfTarget" class="cn-dlg-mask" @click.self="shelfTarget = null">
      <div class="cn-dlg">
        <div class="cn-dlg-title">上架「{{ shelfTarget.name }}」</div>
        <p class="cn-dlg-hint">上架到商店后，其他用户即可添加使用。凭据怎么处理？</p>
        <label v-if="shelfTarget.hasSharedKey" class="cn-dlg-opt">
          <input v-model="shelfCredChoice" type="radio" value="keep" name="shelf-cred" />
          <span class="cn-dlg-opt-main">
            <b>保留共享凭据</b>
            <small>沿用编辑里配置的共享凭据，所有用户共用。</small>
          </span>
        </label>
        <label class="cn-dlg-opt">
          <input v-model="shelfCredChoice" type="radio" value="none" name="shelf-cred" />
          <span class="cn-dlg-opt-main">
            <b>用户各自填写凭据{{ shelfTarget.hasSharedKey ? '' : '（默认）' }}</b>
            <small>每个用户填自己的 api_key，互不可见。适合个人账号类的 MCP 服务。{{ shelfTarget.hasSharedKey ? '将清除现有共享凭据。' : '' }}</small>
          </span>
        </label>
        <label class="cn-dlg-opt" :class="{ 'cn-dlg-opt--off': !hasMyShelfCred }">
          <input v-model="shelfCredChoice" type="radio" value="mine" name="shelf-cred" :disabled="!hasMyShelfCred" />
          <span class="cn-dlg-opt-main">
            <b>带上我的凭据</b>
            <small>{{ hasMyShelfCred ? '把你填的凭据共享给所有用户（请确认该凭据允许共用）。适合平台统一账号。' : `你还没为该${label}填写凭据，先在「编辑」里填好再选这项。` }}</small>
          </span>
        </label>
        <div class="cn-dlg-actions">
          <button class="cn-btn" @click="shelfTarget = null">取消</button>
          <button class="cn-btn cn-btn--primary" :disabled="shelfSaving || (shelfCredChoice === 'mine' && !hasMyShelfCred)" @click="confirmShelf">
            {{ shelfSaving ? '上架中…' : '确认上架' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ─── 下架确认 ─── -->
    <div v-if="unshelfTarget" class="cn-dlg-mask" @click.self="unshelfTarget = null">
      <div class="cn-dlg">
        <div class="cn-dlg-title">下架「{{ unshelfTarget.name }}」</div>
        <p class="cn-dlg-hint">
          下架后商店不再展示该{{ label }}。
          <template v-if="unshelfTarget.credentialMode === 'shared'">上架时带上的共享凭据将一并移除（用户自己填的凭据不受影响）。</template>
        </p>
        <div class="cn-dlg-actions">
          <button class="cn-btn" @click="unshelfTarget = null">取消</button>
          <button class="cn-btn cn-btn--danger" @click="confirmUnshelf">确认下架</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cn-list {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* ── 工具栏 ─────────────────────────────────────── */
.cn-toolbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 26px 12px;
}
.cn-search {
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
.cn-search:focus-within {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
  background: #fff;
}
.cn-search-icon {
  flex-shrink: 0;
  color: var(--ink-4);
}
.cn-search-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
  font-size: 13px;
  color: var(--ink);
}
.cn-search-input::placeholder {
  color: var(--ink-4);
}
.cn-search-clear {
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
.cn-search-clear:hover {
  background: rgba(148, 163, 184, 0.3);
}
.cn-supply {
  display: flex;
  align-items: center;
  gap: 9px;
}
.cn-supply-btn {
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
.cn-supply-btn:hover {
  background: var(--fill-hover);
  border-color: var(--border-strong);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}
.cn-supply-btn--primary {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}
.cn-supply-btn--primary:hover {
  background: var(--grad-brand);
  filter: brightness(0.96);
  border-color: transparent;
  box-shadow: var(--shadow-md);
}
/* 批量管理激活态（上架管理页） */
.cn-supply-btn--on {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}
.cn-supply-btn--on:hover {
  background: var(--grad-brand);
  filter: brightness(0.96);
  border-color: transparent;
  box-shadow: var(--shadow-md);
}

/* ── serverPaged 筛选行（状态 chips + 作者下拉；视觉与技能侧 sk-chip 同源） ── */
.cn-filterrow {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 0 26px 12px;
}
.cn-chip-group {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}
.cn-chip {
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
.cn-chip:hover {
  border-color: var(--border-strong);
  color: var(--ink-2);
}
.cn-chip--on {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}
.cn-author-select {
  width: 172px;
  flex-shrink: 0;
}
.cn-load-more {
  text-align: center;
  font-size: 11px;
  color: var(--ink-4);
  padding: 12px 0 4px;
}

/* ── 加载 / 空态 ─────────────────────────────────── */
.cn-loading {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
}
.cn-loading-spin {
  width: 24px;
  height: 24px;
  border: 2.5px solid color-mix(in srgb, var(--ca) 16%, transparent);
  border-top-color: var(--ca);
  border-radius: 50%;
  animation: cn-rotate 0.8s linear infinite;
}
.cn-loading-text {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-4);
}
.cn-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 40px 20px;
}
.cn-empty-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--ink-2);
  margin-bottom: 7px;
}
.cn-empty-hint {
  font-size: 12.5px;
  color: var(--ink-4);
  max-width: 360px;
  line-height: 1.7;
}
.cn-empty-btn {
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
.cn-empty-btn:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}
.cn-empty-btn--ghost {
  color: var(--ink-2);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  box-shadow: none;
}
.cn-empty-btn--ghost:hover {
  background: #fff;
  border-color: var(--border-strong);
  box-shadow: var(--shadow-sm);
}

/* ── 卡片网格 ───────────────────────────────────── */
.cn-scroll {
  flex: 1;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，内容增减不抖 */
  padding: 16px 26px 10px;
  min-height: 0;
}
.cn-scroll::-webkit-scrollbar {
  width: 5px;
}
.cn-scroll::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 3px;
}
.cn-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(252px, 1fr));
  gap: 12px;
}
.cn-card {
  display: flex;
  flex-direction: column;
  gap: 7px;
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 13px;
  padding: 14px 15px 12px;
  box-shadow: var(--card-shadow);
  cursor: pointer; /* 整卡可点进详情（表单即详情） */
  transition: transform 0.16s, box-shadow 0.16s, border-color 0.16s;
}
.cn-card:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
  border-color: var(--border-strong);
}
.cn-card--hl {
  border-color: color-mix(in srgb, var(--ca) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
/* 批量模式选中态 */
.cn-card--sel {
  border-color: color-mix(in srgb, var(--ca) 55%, transparent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--ca) 18%, transparent);
}
.cn-card-head {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
/* 批量选择勾选框（标题行内联，居首） */
.cn-check {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  border: 1.5px solid var(--border-strong);
  background: var(--surface-strong);
  color: transparent;
  cursor: pointer;
  transition: all 0.15s;
}
.cn-check:hover {
  border-color: color-mix(in srgb, var(--ca) 45%, transparent);
}
.cn-check--on {
  border-color: transparent;
  background: var(--ca);
  color: var(--on-primary);
}
.cn-icon {
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
.cn-icon--custom :deep(svg) {
  width: 18px;
  height: 18px;
}
.cn-icon--custom :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 8px;
}
.cn-name {
  flex: 1;
  min-width: 0;
  font-size: 13.5px;
  font-weight: 700;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cn-badge {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 700;
  color: var(--ink-3);
  background: var(--fill-hover);
  border-radius: 10px;
  padding: 2px 7px;
  white-space: nowrap;
}
.cn-badge--mine {
  color: var(--accent);
  background: var(--accent-soft);
}
.cn-badge--off {
  color: #b45309;
  background: rgba(180, 83, 9, 0.09);
}
.cn-badge--key {
  display: inline-flex;
  align-items: center;
  padding: 3px 6px;
  color: var(--ink-4);
}
.cn-badge--nokey {
  color: #b45309;
  background: rgba(180, 83, 9, 0.09);
}
.cn-host {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cn-desc {
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
.cn-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: auto;
  padding-top: 4px;
}
.cn-btn {
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
.cn-btn:hover {
  background: var(--fill-hover);
  border-color: var(--border-strong);
}
.cn-btn--primary {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
}
.cn-btn--primary:hover {
  filter: brightness(0.96);
  box-shadow: var(--shadow-sm);
}
.cn-btn--danger {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.2);
  background: rgba(220, 38, 38, 0.04);
}
.cn-btn--danger:hover {
  background: rgba(220, 38, 38, 0.1);
  border-color: rgba(220, 38, 38, 0.34);
}
/* 商店视图常驻槽位：未添加=「+」/ 已添加=绿勾（与技能卡片 sk-slot 同源） */
.cn-slot {
  flex-shrink: 0;
}
.cn-slot-icon {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
}
/* 「+」添加：朴素描边圆形按钮，hover 才染主题色（不再实底强调） */
.cn-slot-icon--add {
  border: 1px solid var(--border-strong);
  color: var(--ink-3);
  background: var(--surface-strong);
  cursor: pointer;
  transition: all 0.15s;
}
.cn-slot-icon--add:hover {
  color: var(--ca);
  border-color: color-mix(in srgb, var(--ca) 40%, transparent);
  background: color-mix(in srgb, var(--ca) 8%, transparent);
}
/* 已添加 = 状态标识：浅绿底绿勾，不可点 */
.cn-slot-icon--added {
  color: #047857;
  background: rgba(5, 150, 105, 0.12);
}
.cn-switch {
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
.cn-footer {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 11px 26px;
  border-top: 1px solid var(--line-hair);
  background: var(--surface);
}
.cn-footer-count {
  font-size: 11px;
  color: var(--ink-4);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
.cn-footer-refresh {
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
.cn-footer-refresh:hover:not(:disabled) {
  background: #fff;
  border-color: var(--border-strong);
  color: var(--ink-2);
}
.cn-footer-refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.cn-refresh-spin {
  animation: cn-rotate 0.7s linear infinite;
}

/* ── 上架/下架确认弹窗 ───────────────────────────── */
.cn-dlg-mask {
  position: fixed;
  inset: 0;
  z-index: 1700;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.28);
  backdrop-filter: blur(2px);
}
.cn-dlg {
  width: min(460px, calc(100vw - 48px));
  background: var(--paper, #f5f7fb);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: var(--shadow-md);
  padding: 20px 22px 18px;
}
.cn-dlg-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
  margin-bottom: 8px;
}
.cn-dlg-hint {
  margin: 0 0 12px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ink-3);
}
.cn-dlg-opt {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-strong);
  padding: 10px 12px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}
.cn-dlg-opt:hover {
  border-color: var(--border-strong);
}
.cn-dlg-opt input {
  margin-top: 3px;
  accent-color: var(--accent, #1e40af);
}
.cn-dlg-opt--off {
  opacity: 0.6;
  cursor: not-allowed;
}
.cn-dlg-opt-main {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.cn-dlg-opt-main b {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink-2);
}
.cn-dlg-opt-main small {
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--ink-4);
}
.cn-dlg-actions {
  display: flex;
  justify-content: flex-end;
  gap: 9px;
  margin-top: 12px;
}
@keyframes cn-rotate {
  to {
    transform: rotate(360deg);
  }
}

/* ── 批量操作条（复用底栏位置，视觉与技能侧 sk-batch 同源） ── */
.cn-footer--batch {
  justify-content: space-between;
  background: var(--fill-2);
}
.cn-batch-left,
.cn-batch-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cn-batch-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11.5px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ink-2);
}
.cn-batch-btn {
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
.cn-batch-btn:hover:not(:disabled) {
  background: #fff;
  border-color: var(--border-strong);
  color: var(--ink-2);
}
.cn-batch-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.cn-batch-btn--on {
  color: #059669;
  border-color: rgba(5, 150, 105, 0.28);
  background: rgba(5, 150, 105, 0.07);
}
.cn-batch-btn--on:hover:not(:disabled) {
  background: rgba(5, 150, 105, 0.14);
  border-color: rgba(5, 150, 105, 0.42);
  color: #047857;
}
.cn-batch-btn--off {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.24);
  background: rgba(220, 38, 38, 0.05);
}
.cn-batch-btn--off:hover:not(:disabled) {
  background: rgba(220, 38, 38, 0.1);
  border-color: rgba(220, 38, 38, 0.38);
  color: #b91c1c;
}
</style>
