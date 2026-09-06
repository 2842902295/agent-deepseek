<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { Ref } from 'vue';
import { NPopconfirm } from 'naive-ui';
import { useAuthStore } from '@/store/modules/auth';
import { brand } from '@/constants/brand';
import {
  fetchAgentSkills,
  fetchAgentSkillsPaged,
  fetchAgentSkillAuthorFacets,
  fetchBatchAgentSkillPrefs,
  fetchDeleteAgentSkill,
  fetchDownloadAgentSkill,
  fetchUpdateAgentSkill,
  fetchBatchManageAgentSkills,
  fetchBatchDeleteAgentSkills,
  fetchAgentSkillCategories,
  fetchAddAgentSkillCategory,
  fetchUpdateAgentSkillCategory,
  fetchDeleteAgentSkillCategory,
  fetchAgentConnectors,
  fetchAgentConnectorsPaged,
  fetchAgentConnectorAuthorFacets,
  fetchBatchAgentConnectorPrefs,
  fetchDeleteAgentConnector,
  fetchBatchManageAgentConnectors,
  fetchBatchDeleteAgentConnectors,
  fetchAgentExperts,
  fetchAgentExpertsPaged,
  fetchAgentExpertAuthorFacets,
  fetchBatchAgentExpertPrefs,
  fetchUpdateAgentExpert,
  fetchDeleteAgentExpert,
  fetchRoleTiers
} from '@/service/api';
import type {
  AgentSkill,
  AgentSkillCategory,
  AgentConnector,
  AgentExpert,
  RoleTier,
  AuthorFacet,
  ManageListQuery,
  ManageStatus,
  ManageFilterPatch
} from '@/service/api';
import SkillListView from './SkillListView.vue';
import SkillDetailView from './SkillDetailView.vue';
import SkillCreateView from './SkillCreateView.vue';
import SkillDiscoverView from './SkillDiscoverView.vue';
import SkillUploadView from './SkillUploadView.vue';
import ConnectorListView from './connector/ConnectorListView.vue';
import ConnectorFormView from './connector/ConnectorFormView.vue';
import ExpertListView from './expert/ExpertListView.vue';
import ExpertFormView from './expert/ExpertFormView.vue';
import ExpertDetailView from './expert/ExpertDetailView.vue';

/** 技能视图：商店根页 + 我的技能/上架管理像详情一样 push 成子页面 */
type ViewName = 'store' | 'mine' | 'manage' | 'detail' | 'create' | 'discover' | 'upload';
/** 连接器视图：与技能同构的三层语义（无详情页；表单即供给入口）；数据集 tab 复用同一套视图栈类型 */
type ConnView = 'store' | 'mine' | 'manage' | 'form';
/** 专家视图：与技能同构（含详情页；表单兼创建/编辑） */
type ExpertView = 'store' | 'mine' | 'manage' | 'detail' | 'form';
type TabName = 'expert' | 'skill' | 'connector' | 'dataset';

const props = withDefaults(defineProps<{ show: boolean; initialTab?: TabName }>(), { initialTab: 'skill' });
const emit = defineEmits<{
  'update:show': [value: boolean];
  use: [skillKey: string];
  /** 引导到会话框：关闭抽屉并把起手文案填入输入框 */
  fill: [text: string];
  /** 「和 TA 对话」：新建绑定该专家的会话并跳转（外层实现） */
  expertChat: [expertKey: string];
  /** 技能/连接器/专家数据发生任何变更（添加/启停/上下架/删除/上传/编辑保存）：通知外层刷新 */
  change: [];
}>();

const authStore = useAuthStore();
/** 「专家」体系称谓随品牌变体：standard=助理 / generic=专家 */
const expertLabel = brand.expertLabel;
const myUserId = computed<number | null>(() => {
  const id = authStore.userInfo?.userId;
  return id != null && id !== '' ? Number(id) : null;
});
/** 超管或管理员：技能管理权限（编辑/他人技能删除/上下架/精选/指定档位筛选） */
const isAdmin = computed(() => (authStore.userInfo?.roles || []).some(r => r === 'R_SUPER' || r === 'R_ADMIN'));

// ── 四 tab 导航：四条独立视图栈，切 tab 各自保留页面状态 ─────────────────────
const activeTab = ref<TabName>('skill');
const viewStack = ref<ViewName[]>(['store']);
const connViewStack = ref<ConnView[]>(['store']);
/** 数据集栈：与连接器同构（同一套 kind 数据源，按 kind 过滤） */
const datasetViewStack = ref<ConnView[]>(['store']);
const expertViewStack = ref<ExpertView[]>(['store']);
const currentView = computed<ViewName>(() => viewStack.value[viewStack.value.length - 1]);
const connView = computed<ConnView>(() => connViewStack.value[connViewStack.value.length - 1]);
const datasetView = computed<ConnView>(() => datasetViewStack.value[datasetViewStack.value.length - 1]);
const expertView = computed<ExpertView>(() => expertViewStack.value[expertViewStack.value.length - 1]);
/** 根视图 = 当前 tab 停在商店根页：显示关闭键 + tab 切换 + 右上角入口；子页面显示返回键 */
const isRootView = computed(() => {
  const top =
    activeTab.value === 'skill'
      ? currentView.value
      : activeTab.value === 'connector'
        ? connView.value
        : activeTab.value === 'dataset'
          ? datasetView.value
          : expertView.value;
  return top === 'store';
});

const skills = ref<AgentSkill[]>([]);
const loading = ref(false);
/** 商店搜索词：输入框在面板头部（「我的技能」按钮旁），列表视图按此过滤 */
const storeSearch = ref('');
const selectedSkill = ref<AgentSkill | null>(null);
const highlightKey = ref<string | null>(null);

const skillMeta: Record<ViewName, { title: string }> = {
  store: { title: '技能库' },
  mine: { title: '我的技能' },
  manage: { title: '上架管理' },
  detail: { title: '技能详情' },
  create: { title: '创建技能' },
  discover: { title: '发现技能' },
  upload: { title: '上传技能包' }
};
const connMeta: Record<ConnView, { title: string }> = {
  store: { title: '连接器商店' },
  mine: { title: '我的连接器' },
  manage: { title: '上架管理' },
  form: { title: '添加连接器' }
};
const datasetMeta: Record<ConnView, { title: string }> = {
  store: { title: '数据集商店' },
  mine: { title: '我的数据集' },
  manage: { title: '上架管理' },
  form: { title: '添加数据集' }
};
const connEditing = ref<AgentConnector | null>(null);
/** 表单模式：创建者/管理员编辑完整定义；其他人只配置个人凭据 */
const connFormMode = ref<'full' | 'credential'>('full');
/** 表单归属 tab（连接器/数据集共用一份表单态）：保存成功时弹对应的栈 */
const connFormOwner = ref<'connector' | 'dataset'>('connector');

const expertMeta: Record<ExpertView, { title: string }> = {
  store: { title: `${brand.expertLabel}中心` },
  mine: { title: `我的${brand.expertLabel}` },
  manage: { title: `${brand.expertLabel}管理` },
  // detail 实际由 currentMeta 短路取选中专家名，此处仅为类型完备
  detail: { title: `${brand.expertLabel}详情` },
  form: { title: `创建${brand.expertLabel}` }
};

const currentMeta = computed(() => {
  if (activeTab.value === 'connector' || activeTab.value === 'dataset') {
    const isDs = activeTab.value === 'dataset';
    const view = isDs ? datasetView.value : connView.value;
    if (view === 'form') {
      const noun = isDs ? '数据集' : '连接器';
      const t = !connEditing.value ? `添加${noun}` : connFormMode.value === 'credential' ? '配置凭据' : `编辑${noun}`;
      return { title: t };
    }
    return isDs ? datasetMeta[view] : connMeta[view];
  }
  if (activeTab.value === 'expert') {
    if (expertView.value === 'form') return { title: expertEditing.value ? `编辑${expertLabel}` : `创建${expertLabel}` };
    if (expertView.value === 'detail') return { title: selectedExpert.value?.name || `${expertLabel}详情` };
    return expertMeta[expertView.value];
  }
  return skillMeta[currentView.value];
});

// ── 技能语义过滤（单一数据源；skills 含下架行，上架管理页需要） ──────────────
const isMyCreated = (s: AgentSkill) => myUserId.value != null && s.userId === myUserId.value;
/** 商店：上架中（统一尺子，无 visibility 机制） */
const storeSkills = computed(() => skills.value.filter(s => s.isEnabled));
/** 我的技能：已添加 且（上架中 或 本人创建——自己未上架的也在这里） */
const mineSkills = computed(() =>
  skills.value.filter(s => s.isAdded && (s.isEnabled || isMyCreated(s)))
);
/** 上架管理：管理员看全部（含下架），普通作者只看自己的 */
const manageSkills = computed(() => (isAdmin.value ? skills.value : skills.value.filter(isMyCreated)));
/** 上架管理入口：仅超管/管理员（全员技能与分类配置都在这里管理；普通用户不可见） */
const canSeeManage = computed(() => isAdmin.value);

// ── 上架管理服务端分页状态中枢（管理员专属；四个 tab 各一个 pager 实例） ─────────
/** 管理页页长（与列表侧增量渲染步长一致） */
const MANAGE_PAGE = 24;

/** 管理页 pager：服务端分页 + 筛选条件 + 竞态防护。
 *  - seq 序号守卫：筛选快速连发 / 写后刷新时，乱序返回的旧响应直接丢弃
 *  - 末屏钳制：keepPage 刷新后当前页被删空（批量删除）而仍有数据 → 回退上一页
 *  - Array.isArray 降级防御：后端意外回落全量数组时仍可消费 */
function makeManagePager<T>(
  fetchFn: (query: ManageListQuery) => Promise<{ data: Api.Common.PaginatingQueryRecord<T> | null; error: unknown }>,
  withCategory = false
) {
  const records = ref([]) as Ref<T[]>;
  const total = ref(0);
  const current = ref(1);
  /** 筛选条件（由列表视图 filterChange 驱动） */
  const keyword = ref('');
  const category = ref('all');
  const status = ref<ManageStatus>('all');
  /** 作者筛选：null=全部；0=官方桶（后端 user_id=0 → user_id 为空） */
  const userId = ref<number | null>(null);
  /** 换名避免 shadow 外层技能列表 loading；对外仍以 loading 键导出 */
  const pageLoading = ref(false);
  const loadingMore = ref(false);
  /** 竞态序号：每次请求前自增，旧序号的返回一律丢弃 */
  let seq = 0;

  const hasMore = computed(() => records.value.length < total.value);

  function buildQuery(page: number): ManageListQuery {
    const q: ManageListQuery = { current: page, size: MANAGE_PAGE };
    const kw = keyword.value.trim();
    if (kw) q.keyword = kw;
    if (withCategory && category.value !== 'all') q.category = category.value;
    if (status.value !== 'all') q.status = status.value;
    if (userId.value != null) q.user_id = userId.value;
    return q;
  }

  async function fetchPage(page: number, append: boolean) {
    const mySeq = ++seq;
    if (append) loadingMore.value = true;
    else pageLoading.value = true;
    try {
      const { data, error } = await fetchFn(buildQuery(page));
      if (mySeq !== seq) return; // 已被更新的请求取代：丢弃
      const payload = data as Api.Common.PaginatingQueryRecord<T> | T[] | null;
      if (error || !payload) {
        if (!append) {
          records.value = [];
          total.value = 0;
        }
        return;
      }
      const list = Array.isArray(payload) ? payload : Array.isArray(payload.records) ? payload.records : [];
      records.value = append ? [...records.value, ...list] : list;
      total.value = Array.isArray(payload) ? list.length : (payload.total ?? list.length);
      current.value = page;
    } finally {
      if (mySeq === seq) {
        if (append) loadingMore.value = false;
        else pageLoading.value = false;
      }
    }
  }

  /** 刷新：keepPage=false 回第 1 页（进视图 / 筛选变化）；keepPage=true 停留当前页（写操作后） */
  async function reload(keepPage = false) {
    const page = keepPage ? Math.max(1, current.value) : 1;
    await fetchPage(page, false);
    // 末屏钳制：当前页被删空但仍有数据 → 回退上一页
    if (keepPage && page > 1 && records.value.length === 0 && total.value > 0) {
      await fetchPage(page - 1, false);
    }
  }

  /** 触底：加载下一页并追加 */
  async function loadMore() {
    if (!hasMore.value || pageLoading.value || loadingMore.value) return;
    await fetchPage(current.value + 1, true);
  }

  /** 只清筛选条件（重进上架管理视图时与 reset 等效，语义更明确） */
  function resetFilters() {
    keyword.value = '';
    category.value = 'all';
    status.value = 'all';
    userId.value = null;
  }

  /** 全量复位（面板关闭时；在途请求经 seq 作废） */
  function reset() {
    seq += 1;
    records.value = [];
    total.value = 0;
    current.value = 1;
    resetFilters();
    pageLoading.value = false;
    loadingMore.value = false;
  }

  return { records, total, current, keyword, category, status, userId, loading: pageLoading, loadingMore, hasMore, reload, loadMore, reset, resetFilters };
}

const skillPager = makeManagePager<AgentSkill>(q => fetchAgentSkillsPaged(q), true);
const connPager = makeManagePager<AgentConnector>(q => fetchAgentConnectorsPaged({ ...q, kind: 'connector' }));
const datasetPager = makeManagePager<AgentConnector>(q => fetchAgentConnectorsPaged({ ...q, kind: 'dataset' }));
const expertPager = makeManagePager<AgentExpert>(q => fetchAgentExpertsPaged(q));

/** 上架管理列表控件复位信号：仅「显式进入」管理页（navigate 钩子）时 +1，列表组件经 v-show 常驻，
 *  其本地筛选控件需与 pager 的 reset 对齐；返回上一页（goBack）不加——筛选/滚动/已加载页全部保留 */
const skillManageResetSeq = ref(0);
const connManageResetSeq = ref(0);
const datasetManageResetSeq = ref(0);
const expertManageResetSeq = ref(0);

/** 作者筛选下拉源（四个 tab 各自一份；进上架管理视图时与首页并行拉取） */
const skillAuthors = ref<AuthorFacet[]>([]);
const connAuthors = ref<AuthorFacet[]>([]);
const datasetAuthors = ref<AuthorFacet[]>([]);
const expertAuthors = ref<AuthorFacet[]>([]);

// 作者清单端点为管理员专属（4032 守卫），而本文件的删除/新建等回调普通用户也可触发，
// 故在拉取入口统一门控：非管理员不发请求（数据也仅上架管理视图的筛选下拉消费）
async function loadSkillAuthors() {
  if (!isAdmin.value) return;
  const { data, error } = await fetchAgentSkillAuthorFacets();
  if (!error && data) skillAuthors.value = data;
}
async function loadConnAuthors() {
  if (!isAdmin.value) return;
  const { data, error } = await fetchAgentConnectorAuthorFacets('connector');
  if (!error && data) connAuthors.value = data;
}
async function loadDatasetAuthors() {
  if (!isAdmin.value) return;
  const { data, error } = await fetchAgentConnectorAuthorFacets('dataset');
  if (!error && data) datasetAuthors.value = data;
}
async function loadExpertAuthors() {
  if (!isAdmin.value) return;
  const { data, error } = await fetchAgentExpertAuthorFacets();
  if (!error && data) expertAuthors.value = data;
}

/** 列表视图筛选变化 → 写入对应 pager → 回第 1 页重取 */
type AnyManagePager = Pick<
  ReturnType<typeof makeManagePager<AgentSkill>>,
  'keyword' | 'category' | 'status' | 'userId' | 'reload'
>;
function applyManageFilter(pager: AnyManagePager, patch: ManageFilterPatch) {
  if (patch.keyword !== undefined) pager.keyword.value = patch.keyword;
  if (patch.category !== undefined) pager.category.value = patch.category;
  if (patch.status !== undefined) pager.status.value = patch.status;
  if ('userId' in patch) pager.userId.value = patch.userId ?? null;
  void pager.reload(false);
}
function onSkillManageFilter(patch: ManageFilterPatch) {
  applyManageFilter(skillPager, patch);
}
function onConnManageFilter(patch: ManageFilterPatch) {
  applyManageFilter(connPager, patch);
}
function onDatasetManageFilter(patch: ManageFilterPatch) {
  applyManageFilter(datasetPager, patch);
}
function onExpertManageFilter(patch: ManageFilterPatch) {
  applyManageFilter(expertPager, patch);
}

/** 管理页底栏「刷新」：全量列表（头部角标统计用）与分页 pager 双刷 */
function onSkillManageRefresh() {
  loadSkills();
  void skillPager.reload(true);
}
function onConnManageRefresh() {
  loadConnectors();
  void connPager.reload(true);
  void datasetPager.reload(true);
}
function onExpertManageRefresh() {
  loadExperts();
  void expertPager.reload(true);
}

function navigate(view: ViewName) {
  viewStack.value.push(view);
  if (view === 'manage') {
    skillManageResetSeq.value += 1;
    skillPager.reset();
    void skillPager.reload(false);
    void loadSkillAuthors();
  }
}
function goBack() {
  // 列表页经 v-show 常驻：返回不销毁组件，筛选/滚动位置/已加载页原样保留，
  // 因此这里只弹栈、不复位 pager（复位只发生在 navigate 的显式进入）
  if (activeTab.value === 'skill') {
    if (viewStack.value.length > 1) viewStack.value.pop();
    else selectedSkill.value = null;
  } else if (activeTab.value === 'connector') {
    if (connViewStack.value.length > 1) connViewStack.value.pop();
  } else if (activeTab.value === 'dataset') {
    if (datasetViewStack.value.length > 1) datasetViewStack.value.pop();
  } else if (expertViewStack.value.length > 1) {
    expertViewStack.value.pop();
  }
}
function resetTo() {
  viewStack.value = ['store'];
  connViewStack.value = ['store'];
  datasetViewStack.value = ['store'];
  expertViewStack.value = ['store'];
}
/** 我的技能空态「去商店逛逛」：直接回到根视图 */
function goStore() {
  viewStack.value = ['store'];
}
function close() {
  emit('update:show', false);
}

async function loadSkills() {
  loading.value = true;
  try {
    // include_disabled=true：上架管理页需要下架行；商店/我的技能由 computed 过滤
    const { data, error } = await fetchAgentSkills(true);
    if (!error && data) skills.value = data;
  } finally {
    loading.value = false;
  }
}

// ── 分类词表（DB 动态；管理员在上架管理页维护，system-admin 子 agent 亦可写表） ──
const categories = ref<AgentSkillCategory[]>([]);
/** 下发给列表/详情视图的词表名序列 */
const categoryNames = computed(() => categories.value.map(c => c.name));
async function loadCategories() {
  const { data, error } = await fetchAgentSkillCategories();
  if (!error && data) categories.value = data;
}

// ── 可见档位（agent_role_tier；「可见范围」多选选项源，透传给详情/表单视图） ─────────────
const roleTiers = ref<RoleTier[]>([]);
async function loadTiers() {
  const { data, error } = await fetchRoleTiers();
  if (!error && data) roleTiers.value = data.tiers;
}

// ── 分类管理（仅管理员，上架管理页内） ──────────────────
const newCatName = ref('');
const editingCatId = ref<number | null>(null);
const editingCatName = ref('');

function startRenameCat(c: AgentSkillCategory) {
  editingCatId.value = c.id;
  editingCatName.value = c.name;
}
async function commitRenameCat() {
  const id = editingCatId.value;
  if (id == null) return;
  editingCatId.value = null;
  const name = editingCatName.value.trim();
  const old = categories.value.find(c => c.id === id);
  if (!name || !old || name === old.name) return;
  const { data, error } = await fetchUpdateAgentSkillCategory(id, { name });
  if (!error && data) {
    const idx = categories.value.findIndex(c => c.id === id);
    if (idx >= 0) categories.value[idx] = data;
    // 后端级联把引用技能的 category 跟着改名，刷新列表回显
    await loadSkills();
  } else {
    window.$message?.error('重命名失败');
  }
}
async function onDeleteCat(c: AgentSkillCategory) {
  const { error } = await fetchDeleteAgentSkillCategory(c.id);
  if (!error) {
    categories.value = categories.value.filter(x => x.id !== c.id);
    await loadSkills();
    window.$message?.success(`已删除「${c.name}」，其下技能归入「其他」`);
  } else {
    window.$message?.error('删除失败');
  }
}
async function onAddCat() {
  const name = newCatName.value.trim();
  if (!name) return;
  const { data, error } = await fetchAddAgentSkillCategory({ name });
  if (!error && data) {
    categories.value = [...categories.value, data];
    newCatName.value = '';
  } else {
    window.$message?.error('新增分类失败');
  }
}

// ── 技能卡片动作（三页语义不同，各自独立回调，不做分支复用） ────
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

/** 商店：添加（重新添加默认启用） */
async function onStoreAdd(skill: AgentSkill) {
  const { data, error } = await fetchBatchAgentSkillPrefs([skill.skillKey], { isAdded: true, isEnabled: true });
  if (!error && data) {
    skill.isAdded = true;
    skill.userEnabled = true;
    // 本地先行盖上添加时间：「我的技能」按 addedAt 倒序，新添加的立刻排最前（下次 loadSkills 以后端为准）
    skill.addedAt = Date.now();
    emit('change');
    window.$message?.success('已添加到我的技能');
  } else {
    window.$message?.error('添加失败');
  }
}

/** 我的技能：启用/禁用（禁用=完全不加载、@ 不可调用） */
async function onMineToggle(skill: AgentSkill) {
  const { data, error } = await fetchBatchAgentSkillPrefs([skill.skillKey], { isEnabled: !skill.userEnabled });
  if (!error && data) {
    skill.userEnabled = !skill.userEnabled;
    emit('change');
  } else {
    window.$message?.error('切换失败');
  }
}

/** 我的技能：移除（退回商店未添加态；卡片内已确认） */
async function onMineRemove(skill: AgentSkill) {
  const { data, error } = await fetchBatchAgentSkillPrefs([skill.skillKey], { isAdded: false });
  if (!error && data) {
    skill.isAdded = false;
    emit('change');
  } else {
    window.$message?.error('移除失败');
  }
}

/** 我的技能：批量启用/禁用 */
async function onBatchToggle(keys: string[], enabled: boolean) {
  if (!keys.length) return;
  const { data, error } = await fetchBatchAgentSkillPrefs(keys, { isEnabled: enabled });
  if (!error && data) {
    const updated = new Set(data.updated);
    skills.value.forEach(s => {
      if (updated.has(s.skillKey)) s.userEnabled = enabled;
    });
    emit('change');
    window.$message?.success(`${enabled ? '已启用' : '已禁用'} ${data.updated.length} 个技能`);
  } else {
    window.$message?.error('批量切换失败');
  }
}
/** 我的技能：批量移除 */
async function onBatchRemove(keys: string[]) {
  if (!keys.length) return;
  const { data, error } = await fetchBatchAgentSkillPrefs(keys, { isAdded: false });
  if (!error && data) {
    const updated = new Set(data.updated);
    skills.value.forEach(s => {
      if (updated.has(s.skillKey)) s.isAdded = false;
    });
    emit('change');
    window.$message?.success(`已移除 ${data.updated.length} 个技能`);
  } else {
    window.$message?.error('批量移除失败');
  }
}

/** 上架管理：上架/下架（全局，影响所有用户；统一尺子 is_enabled，仅管理员可操作） */
async function onManageToggle(skill: AgentSkill) {
  const next = !skill.isEnabled;
  const { data, error } = await fetchUpdateAgentSkill(skill.id, { is_enabled: next });
  if (!error && data) {
    const idx = skills.value.findIndex(s => s.id === data.id);
    if (idx >= 0) skills.value[idx] = data;
    emit('change');
    void skillPager.reload(true);
  } else {
    window.$message?.error(next ? '上架失败' : '下架失败');
  }
}

/** 上架管理：设为/取消精选（商店顶部精选区，仅管理员入口；后端对非管理员返回 4032） */
async function onManageFeature(skill: AgentSkill) {
  const { data, error } = await fetchUpdateAgentSkill(skill.id, { is_featured: !skill.isFeatured });
  if (!error && data) {
    const idx = skills.value.findIndex(s => s.id === data.id);
    if (idx >= 0) skills.value[idx] = data;
    emit('change');
    void skillPager.reload(true);
    window.$message?.success(data.isFeatured ? '已设为精选' : '已取消精选');
  } else {
    window.$message?.error('精选设置失败');
  }
}

/** 上架管理：批量上架/下架（仅管理员；后端跳过 builtin 等不可操作项） */
async function onManageBatchShelf(keys: string[], enabled: boolean) {
  if (!keys.length) return;
  const { data, error } = await fetchBatchManageAgentSkills(keys, { isEnabled: enabled });
  if (!error && data) {
    await loadSkills();
    emit('change');
    void skillPager.reload(true);
    window.$message?.success(
      `${enabled ? '已上架' : '已下架'} ${data.updated.length} 个技能${data.skipped.length ? `，跳过 ${data.skipped.length} 个` : ''}`
    );
  } else {
    window.$message?.error('批量操作失败');
  }
}

/** 上架管理：批量精选/取消精选（仅管理员） */
async function onManageBatchFeature(keys: string[], featured: boolean) {
  if (!keys.length) return;
  const { data, error } = await fetchBatchManageAgentSkills(keys, { isFeatured: featured });
  if (!error && data) {
    await loadSkills();
    emit('change');
    void skillPager.reload(true);
    window.$message?.success(
      `${featured ? '已设为精选' : '已取消精选'} ${data.updated.length} 个技能${data.skipped.length ? `，跳过 ${data.skipped.length} 个` : ''}`
    );
  } else {
    window.$message?.error('批量操作失败');
  }
}

/** 上架管理：批量删除（卡片列表已勾选，批量条内已二次确认） */
async function onManageBatchDelete(keys: string[]) {
  if (!keys.length) return;
  const { data, error } = await fetchBatchDeleteAgentSkills(keys);
  if (!error && data) {
    await loadSkills();
    emit('change');
    void skillPager.reload(true);
    loadSkillAuthors(); // 作者计数随删除变化
    window.$message?.success(
      `已删除 ${data.updated.length} 个技能${data.skipped.length ? `，跳过 ${data.skipped.length} 个（内置/无权）` : ''}`
    );
  } else {
    window.$message?.error('批量删除失败');
  }
}

/** 彻底删除（详情页 / 上架管理页共用；触发处已完成二次确认） */
async function doDelete(skill: AgentSkill) {
  const { error } = await fetchDeleteAgentSkill(skill.id);
  if (!error) {
    window.$message?.success('已删除');
    if (selectedSkill.value?.id === skill.id) goBack();
    await loadSkills();
    emit('change');
    void skillPager.reload(true);
    loadSkillAuthors(); // 作者计数随删除变化
  } else {
    window.$message?.error('删除失败');
  }
}
function onRemove(skill: AgentSkill) {
  void doDelete(skill);
}

// ── 技能子视图回调 ────────────────────────────────────────────
function onFill(text: string) {
  emit('fill', text);
  close();
}
/** 上传完成：停留上传页展示「本次上传变动」，仅刷新数据并记下高亮 key；
 *  跳转由上传页「查看我的技能」按钮触发（onUploadGotoMine） */
function onUploaded(skillKey: string) {
  highlightKey.value = skillKey;
  loadSkills();
  emit('change');
}
/** 上传页「查看我的技能」：跳到我的技能页并高亮最近上传的技能 */
function onUploadGotoMine() {
  viewStack.value = ['store', 'mine'];
  loadSkills();
}
function onDetailChanged(skill: AgentSkill) {
  const idx = skills.value.findIndex(s => s.id === skill.id);
  if (idx >= 0) skills.value[idx] = skill;
  selectedSkill.value = skill;
  emit('change');
  void skillPager.reload(true); // 详情页保存（名称/分类/可见范围等）同步管理列表
}

// ── 连接器 / 数据集（同一套 MCP 连接器数据，按 kind 分两个维度展示；商店口径与技能对齐：默认未上架） ──
const connectors = ref<AgentConnector[]>([]);
const connLoading = ref(false);
const connSearch = ref('');
const highlightConnKey = ref<string | null>(null);
const datasetSearch = ref('');
const highlightDatasetKey = ref<string | null>(null);

async function loadConnectors() {
  connLoading.value = true;
  try {
    const { data, error } = await fetchAgentConnectors(true);
    if (!error && data) connectors.value = data;
  } finally {
    connLoading.value = false;
  }
}

const isMyCreatedConn = (c: AgentConnector) => myUserId.value != null && c.userId === myUserId.value;
const isDataset = (c: AgentConnector) => c.kind === 'dataset';
/** 连接器 tab 只看 kind=connector；数据集 tab 只看 kind=dataset（两个维度互斥） */
const plainConnectors = computed(() => connectors.value.filter(c => !isDataset(c)));
const datasetConnectors = computed(() => connectors.value.filter(isDataset));
/** 商店：上架中 */
const storeConnectors = computed(() => plainConnectors.value.filter(c => c.isEnabled));
const storeDatasets = computed(() => datasetConnectors.value.filter(c => c.isEnabled));
/** 我的：已添加 且（上架中 或 本人创建） */
const mineConnectors = computed(() => plainConnectors.value.filter(c => c.isAdded && (c.isEnabled || isMyCreatedConn(c))));
const mineDatasets = computed(() => datasetConnectors.value.filter(c => c.isAdded && (c.isEnabled || isMyCreatedConn(c))));
/** 上架管理：管理员看全部，普通作者只看自己的 */
const manageConnectors = computed(() => (isAdmin.value ? plainConnectors.value : plainConnectors.value.filter(isMyCreatedConn)));
const manageDatasets = computed(() => (isAdmin.value ? datasetConnectors.value : datasetConnectors.value.filter(isMyCreatedConn)));

function connNavigate(view: ConnView) {
  connViewStack.value.push(view);
  if (view === 'manage') {
    connManageResetSeq.value += 1;
    connPager.reset();
    void connPager.reload(false);
    void loadConnAuthors();
  }
}
function datasetNavigate(view: ConnView) {
  datasetViewStack.value.push(view);
  if (view === 'manage') {
    datasetManageResetSeq.value += 1;
    datasetPager.reset();
    void datasetPager.reload(false);
    void loadDatasetAuthors();
  }
}
/** 我的空态「去逛逛」：回到对应商店根页 */
function connGoStore() {
  connViewStack.value = ['store'];
}
function datasetGoStore() {
  datasetViewStack.value = ['store'];
}

/** 商店：添加（加进我的 + 个人启用）；personal 类型直接带进凭据配置表单 */
async function onConnStoreAdd(c: AgentConnector) {
  const noun = isDataset(c) ? '数据集' : '连接器';
  const { data, error } = await fetchBatchAgentConnectorPrefs([c.connectorKey], { isAdded: true, isEnabled: true });
  if (!error && data) {
    c.isAdded = true;
    c.userEnabled = true;
    c.addedAt = Date.now();
    emit('change');
    if (c.credentialMode === 'personal') {
      window.$message?.success('已添加，请填写你的个人凭据');
      connEditing.value = c;
      connFormMode.value = 'credential';
      connFormOwner.value = isDataset(c) ? 'dataset' : 'connector';
      (isDataset(c) ? datasetNavigate : connNavigate)('form');
    } else {
      window.$message?.success(`已添加到我的${noun}`);
    }
  } else {
    window.$message?.error('添加失败');
  }
}

/** 我的：个人启用/禁用（禁用=agent 不加载其工具） */
async function onConnMineToggle(c: AgentConnector) {
  const { data, error } = await fetchBatchAgentConnectorPrefs([c.connectorKey], { isEnabled: !c.userEnabled });
  if (!error && data) {
    c.userEnabled = !c.userEnabled;
    emit('change');
  } else {
    window.$message?.error('切换失败');
  }
}

/** 我的：移除（退回商店未添加态；卡片内已确认） */
async function onConnMineRemove(c: AgentConnector) {
  const { data, error } = await fetchBatchAgentConnectorPrefs([c.connectorKey], { isAdded: false });
  if (!error && data) {
    c.isAdded = false;
    emit('change');
  } else {
    window.$message?.error('移除失败');
  }
}

/** 上架管理：上/下架在列表弹窗内完成（含「是否带凭据」选择），这里刷新并通知外层 */
function onConnShelfChanged() {
  loadConnectors();
  emit('change');
  void connPager.reload(true);
  void datasetPager.reload(true);
}

/** 上架管理：批量上架/下架（仅管理员）；同一 tab 内批量，kind 一致，文案按 tab 取 */
async function onConnManageBatchShelf(keys: string[], enabled: boolean) {
  if (!keys.length) return;
  const noun = activeTab.value === 'dataset' ? '数据集' : '连接器';
  const { data, error } = await fetchBatchManageAgentConnectors(keys, { isEnabled: enabled });
  if (!error && data) {
    await loadConnectors();
    emit('change');
    void connPager.reload(true);
    void datasetPager.reload(true);
    window.$message?.success(
      `${enabled ? '已上架' : '已下架'} ${data.updated.length} 个${noun}${data.skipped.length ? `，跳过 ${data.skipped.length} 个` : ''}`
    );
  } else {
    window.$message?.error('批量操作失败');
  }
}

/** 上架管理：批量删除（批量条内已二次确认） */
async function onConnManageBatchDelete(keys: string[]) {
  if (!keys.length) return;
  const noun = activeTab.value === 'dataset' ? '数据集' : '连接器';
  const { data, error } = await fetchBatchDeleteAgentConnectors(keys);
  if (!error && data) {
    await loadConnectors();
    emit('change');
    void connPager.reload(true);
    void datasetPager.reload(true);
    loadConnAuthors(); // 作者计数随删除变化
    loadDatasetAuthors();
    window.$message?.success(
      `已删除 ${data.updated.length} 个${noun}${data.skipped.length ? `，跳过 ${data.skipped.length} 个（无权）` : ''}`
    );
  } else {
    window.$message?.error('批量删除失败');
  }
}

/** 彻底删除（卡片内已确认；连带清除所有人的偏好行） */
async function onConnDelete(c: AgentConnector) {
  const { error } = await fetchDeleteAgentConnector(c.id);
  if (!error) {
    window.$message?.success('已删除');
    await loadConnectors();
    emit('change');
    void connPager.reload(true);
    void datasetPager.reload(true);
    loadConnAuthors(); // 作者计数随删除变化
    loadDatasetAuthors();
  } else {
    window.$message?.error('删除失败');
  }
}

/** 表单入口：添加 / 编辑（创建者/管理员）/ 配置个人凭据（其他用户）；归属当前 tab */
function onConnCreate() {
  connEditing.value = null;
  connFormMode.value = 'full';
  connFormOwner.value = activeTab.value === 'dataset' ? 'dataset' : 'connector';
  (connFormOwner.value === 'dataset' ? datasetNavigate : connNavigate)('form');
}
function onConnEdit(c: AgentConnector) {
  connEditing.value = c;
  connFormMode.value = isMyCreatedConn(c) || isAdmin.value ? 'full' : 'credential';
  connFormOwner.value = isDataset(c) ? 'dataset' : 'connector';
  (connFormOwner.value === 'dataset' ? datasetNavigate : connNavigate)('form');
}
/** 表单保存成功：弹归属栈、高亮、刷新 */
function onConnSaved(saved: AgentConnector) {
  if (connFormOwner.value === 'dataset') {
    datasetViewStack.value.pop();
    highlightDatasetKey.value = saved.connectorKey;
  } else {
    connViewStack.value.pop();
    highlightConnKey.value = saved.connectorKey;
  }
  loadConnectors();
  emit('change');
  void connPager.reload(true);
  void datasetPager.reload(true);
  loadConnAuthors(); // 新建可能引入新作者
  loadDatasetAuthors();
}

// ── 专家（会话级召唤：@专家名 驻留绑定；商店口径与技能/连接器对齐） ─────────
const experts = ref<AgentExpert[]>([]);
const expertLoading = ref(false);
const expertSearch = ref('');
const highlightExpertKey = ref<string | null>(null);
const expertEditing = ref<AgentExpert | null>(null);
const selectedExpert = ref<AgentExpert | null>(null);

async function loadExperts() {
  expertLoading.value = true;
  try {
    const { data, error } = await fetchAgentExperts(true);
    if (!error && data) experts.value = data;
  } finally {
    expertLoading.value = false;
  }
}

const isMyCreatedExpert = (e: AgentExpert) => myUserId.value != null && e.userId === myUserId.value;
/** 专家中心：上架中 */
const storeExperts = computed(() => experts.value.filter(e => e.isEnabled));
/** 我的专家：已添加 且（上架中 或 本人创建） */
const mineExperts = computed(() => experts.value.filter(e => e.isAdded && (e.isEnabled || isMyCreatedExpert(e))));
/** 专家管理：管理员看全部，普通作者只看自己的 */
const manageExperts = computed(() => (isAdmin.value ? experts.value : experts.value.filter(isMyCreatedExpert)));

function expertNavigate(view: ExpertView) {
  expertViewStack.value.push(view);
  if (view === 'manage') {
    expertManageResetSeq.value += 1;
    expertPager.reset();
    void expertPager.reload(false);
    void loadExpertAuthors();
  }
}
function expertGoStore() {
  expertViewStack.value = ['store'];
}

/** 召唤：未添加先添加，再通知外层新建专家会话并关闭面板 */
async function onExpertSummon(e: AgentExpert) {
  if (!e.isAdded) {
    const { data, error } = await fetchBatchAgentExpertPrefs([e.expertKey], { isAdded: true, isEnabled: true });
    if (!error && data) {
      e.isAdded = true;
      e.userEnabled = true;
      emit('change');
    } else {
      window.$message?.error('添加失败');
      return;
    }
  }
  emit('expertChat', e.expertKey);
  close();
}

// ── 快捷提问（详情页建议气泡）：补添加/启用 → 填充输入框 → 关面板 ─────────────
// 统一前缀约定：填充内容一律「@实体key + 问题」——技能/专家由后端 @ 解析命中（注入技能 / 驻留专家），
// 连接器/数据集无 @ 语义但保留同形前缀，提示该问题针对哪个数据源

/** 技能快捷提问：未添加/未启用先补齐，再把「@技能key + 问题」填入输入框（不发送） */
async function onSkillTryExample(skill: AgentSkill, question: string) {
  if (!skill.isAdded || !skill.userEnabled) {
    const { data, error } = await fetchBatchAgentSkillPrefs([skill.skillKey], { isAdded: true, isEnabled: true });
    if (!error && data) {
      skill.isAdded = true;
      skill.userEnabled = true;
      skill.addedAt = skill.addedAt ?? Date.now();
      emit('change');
    } else {
      window.$message?.error('添加失败');
      return;
    }
  }
  emit('fill', `@${skill.skillKey} ${question}`);
  close();
}

/** 专家快捷提问：未添加/未启用先补齐 → 召唤（绑定会话）→「@专家key + 问题」填入输入框 */
async function onExpertTryExample(e: AgentExpert, question: string) {
  if (!e.isAdded || !e.userEnabled) {
    const { data, error } = await fetchBatchAgentExpertPrefs([e.expertKey], { isAdded: true, isEnabled: true });
    if (!error && data) {
      e.isAdded = true;
      e.userEnabled = true;
      emit('change');
    } else {
      window.$message?.error('添加失败');
      return;
    }
  }
  emit('expertChat', e.expertKey);
  emit('fill', `@${e.expertKey} ${question}`);
  close();
}

/** 连接器/数据集快捷提问：个人凭据未配置先带去凭据表单；否则补添加/启用后把「@连接器key + 问题」填入输入框 */
async function onConnTryExample(c: AgentConnector, question: string) {
  if (c.credentialMode === 'personal' && !c.hasMyKey) {
    // 已在本连接器表单内（详情页例子区入口）：不重复压栈进表单，只提示先配凭据
    const owner = connFormOwner.value;
    const inThisForm =
      connEditing.value?.connectorKey === c.connectorKey &&
      (owner === 'dataset' ? datasetView.value : connView.value) === 'form';
    if (inThisForm) {
      window.$message?.info('请先填写个人凭据，保存后即可使用该问法');
      return;
    }
    // 与卡片「+」添加同路径：补添加并打开凭据表单（此时不填充，等凭据配好再用）
    await onConnStoreAdd(c);
    return;
  }
  if (!c.isAdded || !c.userEnabled) {
    const { data, error } = await fetchBatchAgentConnectorPrefs([c.connectorKey], { isAdded: true, isEnabled: true });
    if (!error && data) {
      c.isAdded = true;
      c.userEnabled = true;
      c.addedAt = c.addedAt ?? Date.now();
      emit('change');
    } else {
      window.$message?.error('添加失败');
      return;
    }
  }
  emit('fill', `@${c.connectorKey} ${question}`);
  close();
}

/** 我的专家：启用/禁用（禁用=不可召唤） */
async function onExpertMineToggle(e: AgentExpert) {
  const { data, error } = await fetchBatchAgentExpertPrefs([e.expertKey], { isEnabled: !e.userEnabled });
  if (!error && data) {
    e.userEnabled = !e.userEnabled;
    emit('change');
  } else {
    window.$message?.error('切换失败');
  }
}

/** 我的专家：移除（卡片内已确认） */
async function onExpertMineRemove(e: AgentExpert) {
  const { data, error } = await fetchBatchAgentExpertPrefs([e.expertKey], { isAdded: false });
  if (!error && data) {
    e.isAdded = false;
    emit('change');
  } else {
    window.$message?.error('移除失败');
  }
}

/** 专家管理：上/下架（卡片内操作） */
async function onExpertShelf(e: AgentExpert) {
  const next = !e.isEnabled;
  const { data, error } = await fetchUpdateAgentExpert(e.id, { isEnabled: next });
  if (!error && data) {
    const idx = experts.value.findIndex(x => x.id === data.id);
    if (idx >= 0) experts.value[idx] = data;
    emit('change');
    void expertPager.reload(true);
    window.$message?.success(next ? '已上架' : '已下架');
  } else {
    window.$message?.error(next ? '上架失败' : '下架失败');
  }
}

/** 专家管理：删除（卡片内已确认） */
async function onExpertDelete(e: AgentExpert) {
  const { error } = await fetchDeleteAgentExpert(e.id);
  if (!error) {
    window.$message?.success('已删除');
    if (selectedExpert.value?.id === e.id) {
      selectedExpert.value = null;
      if (expertView.value === 'detail') expertViewStack.value.pop();
    }
    await loadExperts();
    emit('change');
    void expertPager.reload(true);
    loadExpertAuthors(); // 作者计数随删除变化
  } else {
    window.$message?.error('删除失败');
  }
}

/** 卡片点击 → 详情页 */
function onExpertDetail(e: AgentExpert) {
  selectedExpert.value = e;
  expertNavigate('detail');
}

/** 表单入口：创建 / 编辑 */
function onExpertCreate() {
  expertEditing.value = null;
  expertNavigate('form');
}
function onExpertEdit(e: AgentExpert) {
  expertEditing.value = e;
  expertNavigate('form');
}
/** 详情页就地保存成功（可见范围等）：同步列表与选中对象，不回退视图栈 */
function onExpertChanged(saved: AgentExpert) {
  const idx = experts.value.findIndex(x => x.id === saved.id);
  if (idx >= 0) experts.value[idx] = saved;
  if (selectedExpert.value?.id === saved.id) selectedExpert.value = saved;
}
/** 表单保存成功：回退一层、高亮、刷新、同步详情页选中对象 */
function onExpertSaved(saved: AgentExpert) {
  expertViewStack.value.pop();
  highlightExpertKey.value = saved.expertKey;
  const idx = experts.value.findIndex(x => x.id === saved.id);
  if (idx >= 0) experts.value[idx] = saved;
  if (selectedExpert.value?.id === saved.id) selectedExpert.value = saved;
  loadExperts();
  emit('change');
  void expertPager.reload(true);
  loadExpertAuthors(); // 新建可能引入新作者
}

watch(
  () => props.show,
  open => {
    if (open) {
      activeTab.value = props.initialTab;
      resetTo();
      selectedSkill.value = null;
      highlightKey.value = null;
      storeSearch.value = '';
      connEditing.value = null;
      highlightConnKey.value = null;
      connFormMode.value = 'full';
      connFormOwner.value = 'connector';
      connSearch.value = '';
      datasetSearch.value = '';
      highlightDatasetKey.value = null;
      expertEditing.value = null;
      highlightExpertKey.value = null;
      expertSearch.value = '';
      selectedExpert.value = null;
      // 管理页分页状态全量复位（在途请求经 seq 作废）
      skillPager.reset();
      connPager.reset();
      datasetPager.reset();
      expertPager.reset();
      skillAuthors.value = [];
      connAuthors.value = [];
      datasetAuthors.value = [];
      expertAuthors.value = [];
      loadSkills();
      loadCategories();
      loadTiers();
      loadConnectors();
      loadExperts();
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
        <!-- 头部：根视图=关闭键 + 双 tab 切换 + 右侧入口；子页面=返回键 + 实色标题 -->
        <header class="sk-head">
          <button v-if="!isRootView" class="sk-back" @click="goBack">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M10 3L5 8l5 5" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" /></svg>
            返回
          </button>
          <button v-else class="sk-close" title="关闭" @click="close">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.7" stroke-linecap="round" /></svg>
          </button>

          <!-- 根视图：专家 / 技能库 / 连接器 / 数据集 四 tab（四条独立导航栈，切换互不清空） -->
          <div v-if="isRootView" class="sk-tabs">
            <button class="sk-tab" :class="[{ 'sk-tab--on': activeTab === 'expert' }]" @click="activeTab = 'expert'">{{ expertLabel }}</button>
            <button class="sk-tab" :class="[{ 'sk-tab--on': activeTab === 'skill' }]" @click="activeTab = 'skill'">技能</button>
            <button class="sk-tab" :class="[{ 'sk-tab--on': activeTab === 'connector' }]" @click="activeTab = 'connector'">连接器</button>
            <button class="sk-tab" :class="[{ 'sk-tab--on': activeTab === 'dataset' }]" @click="activeTab = 'dataset'">数据集</button>
          </div>
          <h2 v-else class="sk-head-title">{{ currentMeta.title }}</h2>

          <!-- 技能侧右上角：搜索 + 我的技能 + 上架管理 -->
          <div v-if="isRootView && activeTab === 'skill'" class="sk-head-actions">
            <div class="sk-head-search">
              <svg class="sk-head-search-icon" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor">
                <circle cx="7" cy="7" r="4.5" stroke-width="1.6" />
                <path d="M10.5 10.5L14 14" stroke-width="1.6" stroke-linecap="round" />
              </svg>
              <input v-model="storeSearch" placeholder="搜索技能…" />
              <button v-if="storeSearch" class="sk-head-search-clear" @click="storeSearch = ''">
                <svg width="9" height="9" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.8" stroke-linecap="round" /></svg>
              </button>
            </div>
            <button class="sk-head-btn sk-head-btn--primary" @click="navigate('mine')">
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 1.8l1.9 3.8 4.2.6-3 3 .7 4.2L8 11.4l-3.8 2 .7-4.2-3-3 4.2-.6L8 1.8z" stroke-width="1.4" stroke-linejoin="round" /></svg>
              我的技能
              <span class="sk-head-btn-count">{{ mineSkills.length }}</span>
            </button>
            <button
              v-if="canSeeManage"
              class="sk-head-btn"
              title="管理技能的上架/下架"
              @click="navigate('manage')"
            >
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M2.5 13.5v-4M6 13.5v-7M9.5 13.5V8.5M13 13.5v-11" stroke-width="1.7" stroke-linecap="round" /></svg>
              上架管理
              <span class="sk-head-btn-count">{{ manageSkills.length }}</span>
            </button>
          </div>

          <!-- 专家侧右上角：搜索 + 我的专家 + 专家管理 -->
          <div v-if="isRootView && activeTab === 'expert'" class="sk-head-actions">
            <div class="sk-head-search">
              <svg class="sk-head-search-icon" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor">
                <circle cx="7" cy="7" r="4.5" stroke-width="1.6" />
                <path d="M10.5 10.5L14 14" stroke-width="1.6" stroke-linecap="round" />
              </svg>
              <input v-model="expertSearch" :placeholder="`搜索${expertLabel}…`" />
              <button v-if="expertSearch" class="sk-head-search-clear" @click="expertSearch = ''">
                <svg width="9" height="9" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.8" stroke-linecap="round" /></svg>
              </button>
            </div>
            <button class="sk-head-btn sk-head-btn--primary" @click="expertNavigate('mine')">
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 1.8l1.9 3.8 4.2.6-3 3 .7 4.2L8 11.4l-3.8 2 .7-4.2-3-3 4.2-.6L8 1.8z" stroke-width="1.4" stroke-linejoin="round" /></svg>
              我的{{ expertLabel }}
              <span class="sk-head-btn-count">{{ mineExperts.length }}</span>
            </button>
            <button
              v-if="canSeeManage"
              class="sk-head-btn"
              :title="`管理${expertLabel}的上架/下架`"
              @click="expertNavigate('manage')"
            >
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M2.5 13.5v-4M6 13.5v-7M9.5 13.5V8.5M13 13.5v-11" stroke-width="1.7" stroke-linecap="round" /></svg>
              {{ expertLabel }}管理
              <span class="sk-head-btn-count">{{ manageExperts.length }}</span>
            </button>
          </div>

          <!-- 连接器侧右上角：搜索 + 我的连接器 + 上架管理 -->
          <div v-if="isRootView && activeTab === 'connector'" class="sk-head-actions">
            <div class="sk-head-search">
              <svg class="sk-head-search-icon" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor">
                <circle cx="7" cy="7" r="4.5" stroke-width="1.6" />
                <path d="M10.5 10.5L14 14" stroke-width="1.6" stroke-linecap="round" />
              </svg>
              <input v-model="connSearch" placeholder="搜索连接器…" />
              <button v-if="connSearch" class="sk-head-search-clear" @click="connSearch = ''">
                <svg width="9" height="9" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.8" stroke-linecap="round" /></svg>
              </button>
            </div>
            <button class="sk-head-btn sk-head-btn--primary" @click="connNavigate('mine')">
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2v3M10 2v3" stroke-width="1.5" /><path d="M4 5h8v2.5a4 4 0 0 1-8 0V5z" stroke-width="1.4" /><path d="M8 11.5V14" stroke-width="1.5" /></svg>
              我的连接器
              <span class="sk-head-btn-count">{{ mineConnectors.length }}</span>
            </button>
            <button
              v-if="canSeeManage"
              class="sk-head-btn"
              title="管理连接器的上架/下架"
              @click="connNavigate('manage')"
            >
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M2.5 13.5v-4M6 13.5v-7M9.5 13.5V8.5M13 13.5v-11" stroke-width="1.7" stroke-linecap="round" /></svg>
              上架管理
              <span class="sk-head-btn-count">{{ manageConnectors.length }}</span>
            </button>
          </div>

          <!-- 数据集侧右上角：搜索 + 我的数据集 + 上架管理 -->
          <div v-if="isRootView && activeTab === 'dataset'" class="sk-head-actions">
            <div class="sk-head-search">
              <svg class="sk-head-search-icon" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor">
                <circle cx="7" cy="7" r="4.5" stroke-width="1.6" />
                <path d="M10.5 10.5L14 14" stroke-width="1.6" stroke-linecap="round" />
              </svg>
              <input v-model="datasetSearch" placeholder="搜索数据集…" />
              <button v-if="datasetSearch" class="sk-head-search-clear" @click="datasetSearch = ''">
                <svg width="9" height="9" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 4l8 8M12 4l-8 8" stroke-width="1.8" stroke-linecap="round" /></svg>
              </button>
            </div>
            <button class="sk-head-btn sk-head-btn--primary" @click="datasetNavigate('mine')">
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="8" cy="4" rx="5.5" ry="2.1" stroke-width="1.4" /><path d="M2.5 4v8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1V4" stroke-width="1.4" /><path d="M2.5 8c0 1.2 2.5 2.1 5.5 2.1s5.5-.9 5.5-2.1" stroke-width="1.4" /></svg>
              我的数据集
              <span class="sk-head-btn-count">{{ mineDatasets.length }}</span>
            </button>
            <button
              v-if="canSeeManage"
              class="sk-head-btn"
              title="管理数据集的上架/下架"
              @click="datasetNavigate('manage')"
            >
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M2.5 13.5v-4M6 13.5v-7M9.5 13.5V8.5M13 13.5v-11" stroke-width="1.7" stroke-linecap="round" /></svg>
              上架管理
              <span class="sk-head-btn-count">{{ manageDatasets.length }}</span>
            </button>
          </div>
        </header>

        <!--
          视图主体：四个 tab 各自的面板保持挂载（v-show），切换不丢状态；
          列表页（商店/我的/上架管理）同样 v-show 常驻，进详情再返回时筛选/滚动位置原样保留
        -->
        <div class="sk-body">
          <!-- ── 专家 ── -->
          <div v-show="activeTab === 'expert'" class="sk-tabpane">
            <ExpertListView
              v-show="expertView === 'store'"
              :experts="storeExperts"
              :loading="expertLoading"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              mode="store"
              :highlight-key="highlightExpertKey"
              :search="expertSearch"
              @summon="onExpertSummon"
              @detail="onExpertDetail"
              @refresh="loadExperts"
            />
            <ExpertListView
              v-show="expertView === 'mine'"
              :experts="mineExperts"
              :loading="expertLoading"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              mode="mine"
              :highlight-key="highlightExpertKey"
              :search="expertSearch"
              @summon="onExpertSummon"
              @detail="onExpertDetail"
              @toggle="onExpertMineToggle"
              @remove="onExpertMineRemove"
              @edit="onExpertEdit"
              @create="onExpertCreate"
              @go-store="expertGoStore"
              @refresh="loadExperts"
            />
            <ExpertListView
              v-show="expertView === 'manage'"
              :experts="expertPager.records.value"
              :loading="expertPager.loading.value"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              mode="manage"
              server-paged
              :paged-total="expertPager.total.value"
              :loading-more="expertPager.loadingMore.value"
              :author-options="expertAuthors"
              :reset-seq="expertManageResetSeq"
              :highlight-key="highlightExpertKey"
              :search="expertSearch"
              @detail="onExpertDetail"
              @shelf="onExpertShelf"
              @edit="onExpertEdit"
              @delete="onExpertDelete"
              @create="onExpertCreate"
              @filter-change="onExpertManageFilter"
              @load-more="expertPager.loadMore()"
              @refresh="onExpertManageRefresh"
            />
            <ExpertDetailView
              v-if="expertView === 'detail' && selectedExpert"
              :expert="selectedExpert"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              :skills="skills"
              :connectors="connectors"
              :tiers="roleTiers"
              @summon="onExpertSummon"
              @try-example="onExpertTryExample"
              @edit="onExpertEdit"
              @delete="onExpertDelete"
              @changed="onExpertChanged"
            />
            <ExpertFormView
              v-if="expertView === 'form'"
              :expert="expertEditing"
              :skills="skills"
              :connectors="connectors"
              :is-admin="isAdmin"
              :tiers="roleTiers"
              @saved="onExpertSaved"
            />
          </div>

          <!-- ── 技能库 ── -->
          <div v-show="activeTab === 'skill'" class="sk-tabpane">
            <SkillListView
              v-show="currentView === 'store'"
              :skills="storeSkills"
              :loading="loading"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              mode="store"
              :highlight-key="highlightKey"
              :search="storeSearch"
              :categories="categoryNames"
              @use="onUse"
              @detail="onDetail"
              @download="onDownload"
              @toggle="onStoreAdd"
              @feature="onManageFeature"
              @refresh="loadSkills"
            />
            <SkillListView
              v-show="currentView === 'mine'"
              :skills="mineSkills"
              :loading="loading"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              mode="mine"
              :highlight-key="highlightKey"
              :categories="categoryNames"
              @use="onUse"
              @detail="onDetail"
              @download="onDownload"
              @remove="onMineRemove"
              @delete="onRemove"
              @toggle="onMineToggle"
              @batch-toggle="onBatchToggle"
              @batch-remove="onBatchRemove"
              @refresh="loadSkills"
              @create="navigate('create')"
              @discover="navigate('discover')"
              @upload="navigate('upload')"
              @go-store="goStore"
            />
            <!--
              上架管理：分类管理条（词表存 DB，驱动商店导航）+ 技能列表。
              v-show 常驻：从详情返回时筛选/滚动/已加载页原样保留（配合 goBack 不复位 pager）
            -->
            <div v-show="currentView === 'manage'" class="sk-pane">
              <div class="sk-cat-manage">
                <span class="sk-cat-manage-label">分类管理</span>
                <template v-for="c in categories" :key="c.id">
                  <input
                    v-if="editingCatId === c.id"
                    v-model="editingCatName"
                    class="sk-cat-edit"
                    @keydown.enter.prevent="commitRenameCat"
                    @keydown.esc="editingCatId = null"
                    @blur="commitRenameCat"
                  />
                  <span v-else class="sk-cat-chip" title="点击重命名" @click="startRenameCat(c)">
                    {{ c.name }}
                    <NPopconfirm
                      positive-text="删除"
                      negative-text="取消"
                      @positive-click="onDeleteCat(c)"
                    >
                      <template #default>删除分类「{{ c.name }}」后，其下技能将归入「其他」。确定删除吗？</template>
                      <template #trigger>
                        <button class="sk-cat-x" title="删除该分类" @click.stop>×</button>
                      </template>
                    </NPopconfirm>
                  </span>
                </template>
                <input
                  v-model="newCatName"
                  class="sk-cat-add"
                  placeholder="＋ 新分类，回车添加"
                  @keydown.enter.prevent="onAddCat"
                />
              </div>
              <SkillListView
                :skills="skillPager.records.value"
                :loading="skillPager.loading.value"
                :my-user-id="myUserId"
                :is-admin="isAdmin"
                mode="manage"
                server-paged
                :paged-total="skillPager.total.value"
                :loading-more="skillPager.loadingMore.value"
                :author-options="skillAuthors"
                :reset-seq="skillManageResetSeq"
                :highlight-key="highlightKey"
                :categories="categoryNames"
                @use="onUse"
                @detail="onDetail"
                @download="onDownload"
                @remove="onRemove"
                @toggle="onManageToggle"
                @feature="onManageFeature"
                @batch-shelf="onManageBatchShelf"
                @batch-feature="onManageBatchFeature"
                @batch-delete="onManageBatchDelete"
                @filter-change="onSkillManageFilter"
                @load-more="skillPager.loadMore()"
                @refresh="onSkillManageRefresh"
              />
            </div>
            <SkillDetailView
              v-if="currentView === 'detail' && selectedSkill"
              :skill="selectedSkill"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              :categories="categoryNames"
              :tiers="roleTiers"
              @changed="onDetailChanged"
              @use="onUse"
              @try-example="onSkillTryExample"
              @ai-edit="onAiEdit"
              @download="onDownload"
              @remove="onRemove"
            />
            <SkillCreateView v-if="currentView === 'create'" @fill="onFill" />
            <SkillDiscoverView v-if="currentView === 'discover'" @fill="onFill" />
            <SkillUploadView v-if="currentView === 'upload'" @uploaded="onUploaded" @goto-mine="onUploadGotoMine" />
          </div>

          <!-- ── 连接器 ── -->
          <div v-show="activeTab === 'connector'" class="sk-tabpane">
            <ConnectorListView
              v-show="connView === 'store'"
              :connectors="storeConnectors"
              :loading="connLoading"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              mode="store"
              :highlight-key="highlightConnKey"
              :search="connSearch"
              @add="onConnStoreAdd"
              @edit="onConnEdit"
              @refresh="loadConnectors"
            />
            <ConnectorListView
              v-show="connView === 'mine'"
              :connectors="mineConnectors"
              :loading="connLoading"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              mode="mine"
              :highlight-key="highlightConnKey"
              @toggle="onConnMineToggle"
              @remove="onConnMineRemove"
              @edit="onConnEdit"
              @create="onConnCreate"
              @go-store="connGoStore"
              @refresh="loadConnectors"
            />
            <ConnectorListView
              v-show="connView === 'manage'"
              :connectors="connPager.records.value"
              :loading="connPager.loading.value"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              mode="manage"
              server-paged
              :paged-total="connPager.total.value"
              :loading-more="connPager.loadingMore.value"
              :author-options="connAuthors"
              :reset-seq="connManageResetSeq"
              :highlight-key="highlightConnKey"
              @changed="onConnShelfChanged"
              @edit="onConnEdit"
              @delete="onConnDelete"
              @batch-shelf="onConnManageBatchShelf"
              @batch-delete="onConnManageBatchDelete"
              @filter-change="onConnManageFilter"
              @load-more="connPager.loadMore()"
              @refresh="onConnManageRefresh"
            />
            <ConnectorFormView
              v-if="connView === 'form'"
              :connector="connEditing"
              :mode="connFormMode"
              :is-admin="isAdmin"
              :tiers="roleTiers"
              @saved="onConnSaved"
              @try-example="onConnTryExample"
            />
          </div>

          <!-- ── 数据集（kind=dataset 的 MCP 连接器，视图复用连接器组件） ── -->
          <div v-show="activeTab === 'dataset'" class="sk-tabpane">
            <ConnectorListView
              v-show="datasetView === 'store'"
              :connectors="storeDatasets"
              :loading="connLoading"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              mode="store"
              label="数据集"
              intro="添加 MCP 数据集后，agent 对话中会自动加载它的数据访问工具"
              :highlight-key="highlightDatasetKey"
              :search="datasetSearch"
              @add="onConnStoreAdd"
              @edit="onConnEdit"
              @refresh="loadConnectors"
            />
            <ConnectorListView
              v-show="datasetView === 'mine'"
              :connectors="mineDatasets"
              :loading="connLoading"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              mode="mine"
              label="数据集"
              :highlight-key="highlightDatasetKey"
              @toggle="onConnMineToggle"
              @remove="onConnMineRemove"
              @edit="onConnEdit"
              @create="onConnCreate"
              @go-store="datasetGoStore"
              @refresh="loadConnectors"
            />
            <ConnectorListView
              v-show="datasetView === 'manage'"
              :connectors="datasetPager.records.value"
              :loading="datasetPager.loading.value"
              :my-user-id="myUserId"
              :is-admin="isAdmin"
              mode="manage"
              server-paged
              :paged-total="datasetPager.total.value"
              :loading-more="datasetPager.loadingMore.value"
              :author-options="datasetAuthors"
              :reset-seq="datasetManageResetSeq"
              label="数据集"
              :highlight-key="highlightDatasetKey"
              @changed="onConnShelfChanged"
              @edit="onConnEdit"
              @delete="onConnDelete"
              @batch-shelf="onConnManageBatchShelf"
              @batch-delete="onConnManageBatchDelete"
              @filter-change="onDatasetManageFilter"
              @load-more="datasetPager.loadMore()"
              @refresh="onConnManageRefresh"
            />
            <ConnectorFormView
              v-if="datasetView === 'form'"
              :connector="connEditing"
              :mode="connFormMode"
              kind="dataset"
              label="数据集"
              :is-admin="isAdmin"
              :tiers="roleTiers"
              @saved="onConnSaved"
              @try-example="onConnTryExample"
            />
          </div>
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
  /* ─── 双主题令牌（同 SessionSearchModal .ssm-card 模式：teleport 弹层脱离 .qa-shell
     作用域，在此复刻一套设计变量；值与 index.vue .qa-shell 两块保持同步） ─── */
  --paper: #f5f7fb;
  --paper-deep: #eaf0f9;
  --surface: rgba(255, 255, 255, 0.42);
  --surface-strong: rgba(255, 255, 255, 0.62);
  --ink: #0f172a;
  --ink-2: #334155;
  --ink-3: #64748b;
  --ink-4: #94a3b8;
  --rule: rgba(30, 64, 175, 0.1);
  --border: rgba(30, 64, 175, 0.1);
  --border-strong: rgba(30, 64, 175, 0.18);
  --line-hair: rgba(30, 64, 175, 0.1);
  --accent: #1e40af;
  --accent-soft: rgba(30, 64, 175, 0.08);
  --accent-deep: #1e3a8a;
  --c-cyan: #0891b2;
  --fill-hover: rgba(30, 64, 175, 0.06);
  --fill-2: rgba(30, 64, 175, 0.08);
  --card-bg: rgba(255, 255, 255, 0.62);
  --card-border: transparent;
  --card-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
  --grad-brand: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  --on-primary: #ffffff;
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.04), 0 4px 16px -8px rgba(30, 64, 175, 0.09);
  --shadow-md: 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px rgba(30, 64, 175, 0.13);
  --font-body: -apple-system, 'PingFang SC', 'HarmonyOS Sans SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
  /* 传导令牌：「我的」强调/批量选中/hover 描边全走 color-mix(var(--ca) N%)，
     ink 覆盖 --accent 后自动传导为墨黑，零额外规则 */
  --ca: var(--accent);
  --ca2: var(--c-cyan);

  position: fixed;
  top: 0;
  right: 0;
  z-index: 1501;
  width: min(1120px, 96vw);
  height: 100dvh;
  display: flex;
  flex-direction: column;
  background: linear-gradient(var(--paper), var(--paper-deep));
  border-left: 1px solid var(--border);
  box-shadow:
    -24px 0 64px -24px rgba(15, 23, 42, 0.24),
    inset 1px 0 0 rgba(255, 255, 255, 0.95);
  font-family: var(--font-body);
  color: var(--ink);
}

/* ink（Kimi 墨简）：令牌塌成墨灰阶 + 容器实色去玻璃感 */
:root[data-qa-theme='ink'] .sk-panel {
  --paper: #fbfbfc;
  --paper-deep: #f1f1f3;
  --surface: #ffffff;
  --surface-strong: #ffffff;
  --ink: #17181a;
  --ink-2: #494a50;
  --ink-3: #7e7f86;
  --ink-4: #b9bac0;
  --rule: rgba(31, 32, 36, 0.07);
  --border: rgba(31, 32, 36, 0.08);
  --border-strong: rgba(31, 32, 36, 0.15);
  --line-hair: rgba(31, 32, 36, 0.07);
  --accent: #17181a;
  --accent-soft: rgba(23, 24, 26, 0.05);
  --accent-deep: #000000;
  --c-cyan: #494a50;
  --fill-hover: rgba(23, 24, 26, 0.045);
  --fill-2: #efeff1;
  --card-bg: #ffffff;
  --card-border: rgba(31, 32, 36, 0.07);
  --card-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  --grad-brand: #131316;
  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.04);
  --shadow-md: 0 1px 2px rgba(0, 0, 0, 0.03), 0 10px 30px -14px rgba(0, 0, 0, 0.1);

  background: var(--paper);
  border-left-color: var(--border);
  box-shadow: -16px 0 48px -28px rgba(0, 0, 0, 0.16);
}
:root[data-qa-theme='ink'] .sk-mask {
  background: rgba(23, 24, 26, 0.22);
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

/* ── 头部（一行式：返回/关闭 + tab 或标题 + 右侧入口） ── */
.sk-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 26px;
  border-bottom: 1px solid var(--line-hair);
  background: var(--surface);
}
.sk-head-title {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-family: var(--font-body);
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.01em;
  line-height: 1.2;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 根视图双 tab：技能库 / 连接器（segmented，沿用头部按钮视觉语言） */
.sk-tabs {
  display: flex;
  align-items: center;
  gap: 3px;
  width: fit-content;
  background: var(--fill-hover);
  border: 1px solid var(--border);
  border-radius: 11px;
  padding: 3px;
}
.sk-tab {
  font-family: inherit;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--ink-3);
  background: transparent;
  border: none;
  border-radius: 8px;
  padding: 6px 20px;
  cursor: pointer;
  transition: all 0.16s;
  white-space: nowrap;
}
.sk-tab:hover {
  color: var(--ink-2);
}
.sk-tab--on {
  color: var(--ink);
  background: var(--surface-strong);
  box-shadow: var(--shadow-sm);
}
.sk-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-3);
  background: var(--fill-hover);
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 3px 10px 3px 7px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.sk-back:hover {
  background: var(--fill-2);
  color: var(--ink-2);
}
.sk-close {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ink-4);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 9px;
  cursor: pointer;
  transition: all 0.15s;
}
.sk-close:hover {
  background: var(--fill-hover);
  color: var(--ink);
  border-color: var(--border-strong);
}

/* ── 右上角独立入口（我的技能 / 上架管理） ───────────── */
.sk-head-actions {
  flex-shrink: 0;
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
}
/* 头部搜索框：与列表页 sk-search 同款语言，尺寸随头部收窄 */
.sk-head-search {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 232px;
  height: 33px;
  padding: 0 10px;
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 9px;
  transition: border-color 0.15s, box-shadow 0.15s, background 0.15s;
}
.sk-head-search:focus-within {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
  background: #fff;
}
.sk-head-search-icon {
  flex-shrink: 0;
  color: var(--ink-4);
}
.sk-head-search input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-family: inherit;
  font-size: 12.5px;
  color: var(--ink);
}
.sk-head-search input::placeholder {
  color: var(--ink-4);
}
.sk-head-search-clear {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 17px;
  height: 17px;
  border: none;
  border-radius: 50%;
  background: rgba(148, 163, 184, 0.16);
  color: var(--ink-3);
  cursor: pointer;
  transition: background 0.15s;
}
.sk-head-search-clear:hover {
  background: rgba(148, 163, 184, 0.3);
}
.sk-head-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 8px 15px;
  cursor: pointer;
  transition: all 0.16s;
}
.sk-head-btn:hover {
  background: var(--fill-hover);
  border-color: var(--border-strong);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}
.sk-head-btn--primary {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}
.sk-head-btn--primary:hover {
  /* hover 态必须重申实底与字色：.sk-head-btn:hover 的优先级（类+伪类）高于
     .sk-head-btn--primary（单类），不重申会被近透明的 fill-hover 盖掉渐变底，只剩白字 */
  background: var(--grad-brand);
  color: var(--on-primary);
  filter: brightness(0.96);
  border-color: transparent;
  box-shadow: var(--shadow-md);
}
.sk-head-btn-count {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 15px;
  color: var(--ink-4);
  background: var(--fill-hover);
  border-radius: 10px;
  padding: 0 6px;
}
.sk-head-btn--primary .sk-head-btn-count {
  color: rgba(255, 255, 255, 0.92);
  background: rgba(255, 255, 255, 0.2);
}

.sk-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
/* tab 面板容器：两个面板保持挂载（v-show 切换），各自独立滚动 */
.sk-tabpane {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
/* 上架管理页包裹层（分类管理条 + 列表）：v-show 不能用于 template，用常驻容器代替，自带纵向 flex 布局 */
.sk-pane {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* ── 分类管理条（上架管理页顶部，管理员维护 DB 词表） ── */
.sk-cat-manage {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  padding: 12px 26px 10px;
  border-bottom: 1px solid var(--line-hair);
  background: var(--surface);
}
.sk-cat-manage-label {
  font-size: 12px;
  font-weight: 700;
  color: var(--ink-3);
  margin-right: 3px;
}
.sk-cat-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 4px 6px 4px 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.sk-cat-chip:hover {
  border-color: var(--border-strong);
  background: var(--fill-hover);
}
.sk-cat-x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--ink-4);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  transition: all 0.12s;
}
.sk-cat-x:hover {
  background: rgba(220, 38, 38, 0.1);
  color: #dc2626;
}
.sk-cat-edit,
.sk-cat-add {
  font-family: inherit;
  font-size: 12px;
  color: var(--ink);
  background: var(--surface-strong);
  border: 1px solid var(--border-strong);
  border-radius: 20px;
  padding: 4px 12px;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.sk-cat-edit {
  width: 96px;
}
.sk-cat-add {
  width: 160px;
  margin-left: auto;
}
.sk-cat-edit:focus,
.sk-cat-add:focus {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
</style>
