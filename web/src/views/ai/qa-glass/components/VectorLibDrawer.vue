<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue';
import {
  NDrawer,
  NDrawerContent,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSelect,
  useDialog,
  useMessage,
  type FormInst,
  type FormRules
} from 'naive-ui';
import { useAuthStore } from '@/store/modules/auth';
import {
  fetchVectorLibAddItems,
  fetchVectorLibBuild,
  fetchVectorLibCancel,
  fetchVectorLibCreate,
  fetchVectorLibDelete,
  fetchVectorLibDeleteItems,
  fetchVectorLibItems,
  fetchVectorLibList,
  fetchVectorLibProgress,
  fetchVectorLibSearch,
  fetchVectorLibUpload
} from '@/service/api';

/**
 * 向量库管理抽屉（仅管理员可见：入口由 VectorLibEntry 按 R_SUPER/R_ADMIN 门控）。
 *
 * 单一列表，系统库与用户库合并展示（系统库按 libraryKey standard_* 识别，
 * 他人用户库带拥有者徽标）；来源标签统一：系统同步 / 数据表同步均显示「数据表同步」。
 * 权限统一口径（2026-08-27）：管理面仅管理员可达，面内所有库（含系统库）
 * 全体管理员同权——建库 / 条目 / 上传 / 构建 / 删除均可操作；
 * 系统库删除后由后端启动种子自动恢复为空库。
 *
 * 表格为自绘实现（不用 NDataTable）：勾选 / 展开全文 / 关键词过滤 / 自定义分页。
 * 状态语义（后端派生）：building 构建中（进度条）/ error 异常 / stale 陈旧
 * （构建快照 ≠ 当前激活块，需显式重建）/ ready 就绪 / empty 空库。
 */

const props = defineProps<{ show: boolean }>();
const emit = defineEmits<(e: 'update:show', v: boolean) => void>();

const visible = computed({
  get: () => props.show,
  set: v => emit('update:show', v)
});

const message = useMessage();
const dialog = useDialog();
const authStore = useAuthStore();
const myName = computed(() => authStore.userInfo?.userName || '');

// ── 库列表 ──────────────────────────────────────────────────────────────────
const listLoading = ref(false);
const libs = ref<Api.AI.VectorLibRecord[]>([]);
const activeBlock = ref<string | null>(null);
const activeBlockLabel = ref<string | null>(null);
const activeDim = ref<number | null>(null);

// ── 条目面板状态（每库单展开；声明在 loadList 之前，列表刷新会联动） ──────────
const itemsLibKey = ref('');
const itemsLoading = ref(false);
const items = ref<Api.AI.VectorLibItemRecord[]>([]);
const checkedKeys = ref<string[]>([]);
/** 本次展开期间是否出现过过滤键：决定该列是否显示（手动/文件库通常恒为空） */
const sawRefKey = ref(false);
/** 当前展开全文的条目（itemKey） */
const expandedKey = ref('');
const keyword = ref('');
const page = ref(1);
const pageSize = ref(10);
const itemCount = ref(0);
let kwTimer: ReturnType<typeof setTimeout> | null = null;

const itemsLib = computed(() => libs.value.find(l => l.libraryKey === itemsLibKey.value) || null);
const pageCount = computed(() => Math.max(1, Math.ceil(itemCount.value / pageSize.value)));

async function loadItems() {
  if (!itemsLibKey.value) return;
  itemsLoading.value = true;
  const { data, error } = await fetchVectorLibItems(
    itemsLibKey.value,
    page.value,
    pageSize.value,
    keyword.value.trim() || undefined
  );
  itemsLoading.value = false;
  if (error || !data) return;
  items.value = data.records;
  itemCount.value = data.total;
  // 本次展开会话里一旦出现过过滤键，该列就保持显示（避免翻页时列闪烁）
  if (data.records.some(r => !!r.refKey)) sawRefKey.value = true;
}

async function loadList() {
  listLoading.value = true;
  const { data, error } = await fetchVectorLibList();
  listLoading.value = false;
  if (error || !data) return;
  libs.value = data.records;
  activeBlock.value = data.activeEmbedBlock;
  activeBlockLabel.value = data.activeEmbedBlockLabel;
  activeDim.value = data.activeEmbedDim;
  prunePendingUploads();
  // 展开中的条目面板同步刷新（构建 / 上传摄入会改条目数）
  if (itemsLibKey.value) await loadItems();
}

const STATE_META: Record<Api.AI.VectorLibState, { label: string }> = {
  building: { label: '构建中' },
  error: { label: '异常' },
  stale: { label: '陈旧' },
  ready: { label: '就绪' },
  empty: { label: '空库' }
};

const PHASE_LABEL: Record<string, string> = {
  preparing: '准备中',
  diffing: '比对源表变更',
  embedding: '嵌入中',
  deleting: '清理失效条目',
  merging: '收尾'
};

const SOURCE_LABEL: Record<string, string> = {
  manual: '手动条目',
  file: '文件上传',
  // system_sync 与 table_sync 同为数据表同步来源（前者仅指两个系统语义库的固定源表），展示上一律不区分
  table_sync: '数据表同步',
  system_sync: '数据表同步'
};

// ── 图标（内联 SVG 字符串，v-html 注入；均为静态受信内容） ─────────────────
const ICONS: Record<string, string> = {
  ready: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>',
  building:
    '<svg class="vlc-spin" viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.2-8.56"/></svg>',
  stale:
    '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.46 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
  error:
    '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>',
  empty: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M8 12h8"/></svg>',
  search:
    '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>',
  db: '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>',
  close: '<svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>'
};

// ── 展示辅助 ────────────────────────────────────────────────────────────────
function fmtNum(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  return String(n);
}

/** 相对时间（悬停可看完整时间） */
function relTime(s: string | null): string {
  if (!s) return '—';
  const t = new Date(s.replace(' ', 'T')).getTime();
  if (Number.isNaN(t)) return s;
  const m = Math.floor((Date.now() - t) / 60000);
  if (m < 1) return '刚刚';
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d} 天前`;
  return s.slice(5, 16);
}

/** 超管视角下他人库才显示拥有者标记（本人库不标） */
function showOwner(lib: Api.AI.VectorLibRecord) {
  return lib.scope === 'user' && !!lib.ownerName && lib.ownerName !== myName.value;
}

function isSyncSource(lib: Api.AI.VectorLibRecord) {
  return lib.sourceType === 'table_sync' || lib.sourceType === 'system_sync';
}

/** 构建按钮文案：陈旧=重建 / 同步源=立即同步 / 其余=构建（isStale 覆盖 error 态下的陈旧库） */
function buildLabel(lib: Api.AI.VectorLibRecord) {
  if (lib.isStale || lib.state === 'stale') return '重建';
  if (isSyncSource(lib)) return '立即同步';
  return '构建';
}

function scorePct(score: number) {
  return `${Math.max(0, Math.min(100, Math.round(score * 100)))}%`;
}

// ── 轮询：有构建在跑或上传摄入待完成时每 3s 刷一次 ─────────────────────────
/** 上传是后台摄入（不走构建占用表），只能靠条目数变化判完成；记录基线 + 90s 兜底 */
const pendingUploads = ref<Record<string, { base: number; at: number }>>({});
const progressMap = ref<Record<string, Api.AI.VectorLibProgress | null>>({});
let pollTimer: ReturnType<typeof setTimeout> | null = null;

function needPoll() {
  return libs.value.some(l => l.isBuilding) || Object.keys(pendingUploads.value).length > 0;
}

function clearPoll() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

/** 拉构建中库的实时进度（重建整个 map，避免动态 delete） */
async function refreshProgress() {
  const next: Record<string, Api.AI.VectorLibProgress | null> = {};
  await Promise.all(
    libs.value
      .filter(l => l.isBuilding)
      .map(async l => {
        const { data } = await fetchVectorLibProgress(l.libraryKey);
        if (data?.progress) next[l.libraryKey] = data.progress;
      })
  );
  progressMap.value = next;
}

function progressOf(lib: Api.AI.VectorLibRecord) {
  return progressMap.value[lib.libraryKey] || null;
}

function barStyle(lib: Api.AI.VectorLibRecord): string {
  const p = progressOf(lib);
  if (!p?.total) return '';
  return `width:${Math.min(100, Math.round((p.processed / p.total) * 100))}%`;
}

function progressTxt(lib: Api.AI.VectorLibRecord): string {
  const p = progressOf(lib);
  if (!p) return '';
  return p.total ? `${fmtNum(p.processed)} / ${fmtNum(p.total)}` : `已处理 ${fmtNum(p.processed)} 条`;
}

function schedulePoll() {
  clearPoll();
  if (!props.show || !needPoll()) return;
  pollTimer = setTimeout(async () => {
    await loadList();
    await refreshProgress();
    schedulePoll();
  }, 3000);
}

function prunePendingUploads() {
  const kept: Record<string, { base: number; at: number }> = {};
  for (const key of Object.keys(pendingUploads.value)) {
    const p = pendingUploads.value[key];
    const lib = libs.value.find(l => l.libraryKey === key);
    // 库还在 + 条目数未变 + 未超 90s 兜底 → 继续观察；否则移出（摄入已落库或放弃等待）
    if (lib && lib.itemCount === p.base && Date.now() - p.at <= 90000) {
      kept[key] = p;
    }
  }
  pendingUploads.value = kept;
}

// ── 条目面板（每库单展开） ──────────────────────────────────────────────────
function toggleItems(lib: Api.AI.VectorLibRecord) {
  if (itemsLibKey.value === lib.libraryKey) {
    itemsLibKey.value = '';
    return;
  }
  itemsLibKey.value = lib.libraryKey;
  checkedKeys.value = [];
  sawRefKey.value = false;
  expandedKey.value = '';
  keyword.value = '';
  page.value = 1;
  loadItems();
}

/** 自绘表格列宽（勾选 / 展开 / 编号 / 内容 / [过滤键] / 更新时间 / [操作]） */
const vtGrid = computed(() => {
  const cols: string[] = [];
  if (itemsLib.value?.canWrite) cols.push('26px');
  cols.push('26px', '180px', 'minmax(0,1fr)');
  if (sawRefKey.value) cols.push('120px');
  cols.push('104px');
  if (itemsLib.value?.canWrite) cols.push('52px');
  return cols.join(' ');
});

function toggleExpand(key: string) {
  expandedKey.value = expandedKey.value === key ? '' : key;
}

function toggleCheck(key: string) {
  const i = checkedKeys.value.indexOf(key);
  if (i >= 0) checkedKeys.value.splice(i, 1);
  else checkedKeys.value.push(key);
}

const allPageChecked = computed(
  () => items.value.length > 0 && items.value.every(r => checkedKeys.value.includes(r.itemKey))
);
const somePageChecked = computed(
  () => !allPageChecked.value && items.value.some(r => checkedKeys.value.includes(r.itemKey))
);

function toggleCheckAll() {
  const keysOnPage = items.value.map(r => r.itemKey);
  if (allPageChecked.value) {
    const drop = new Set(keysOnPage);
    checkedKeys.value = checkedKeys.value.filter(k => !drop.has(k));
  } else {
    checkedKeys.value = [...new Set([...checkedKeys.value, ...keysOnPage])];
  }
}

function onKeywordInputEvent(e: Event) {
  onKeywordInput((e.target as HTMLInputElement).value);
}

function onKeywordInput(v: string) {
  keyword.value = v;
  if (kwTimer !== null) clearTimeout(kwTimer);
  kwTimer = setTimeout(() => {
    page.value = 1;
    loadItems();
  }, 400);
}

function searchNow() {
  if (kwTimer !== null) clearTimeout(kwTimer);
  page.value = 1;
  loadItems();
}

function clearKeyword() {
  keyword.value = '';
  searchNow();
}

function setPage(p: number) {
  page.value = Math.max(1, Math.min(pageCount.value, p));
  loadItems();
}

function setPageSize(s: number) {
  pageSize.value = s;
  page.value = 1;
  loadItems();
}

// ── 删条目 ──────────────────────────────────────────────────────────────────
const deletingItems = ref(false);

async function deleteItems(keys: string[]) {
  if (!itemsLibKey.value || !keys.length) return;
  const doDelete = async () => {
    deletingItems.value = true;
    const { data, error } = await fetchVectorLibDeleteItems(itemsLibKey.value, keys);
    deletingItems.value = false;
    if (error) return; // 失败提示由全局拦截器弹出
    message.success(`已删除 ${data?.deleted ?? 0} 条`);
    const drop = new Set(keys);
    checkedKeys.value = checkedKeys.value.filter(k => !drop.has(k));
    if (expandedKey.value && drop.has(expandedKey.value)) expandedKey.value = '';
    await Promise.all([loadItems(), loadList()]);
  };
  if (keys.length === 1) {
    await doDelete();
    return;
  }
  dialog.warning({
    title: '批量删除条目',
    content: `确认删除选中的 ${keys.length} 个条目？删除后不可恢复。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: doDelete
  });
}

// ── 测试检索（建完库 / 重建后验证召回效果） ─────────────────────────────────
const searchFor = ref('');
const searchQuery = ref('');
const searching = ref(false);
const searchHits = ref<Api.AI.VectorLibSearchHit[] | null>(null);

function toggleSearch(lib: Api.AI.VectorLibRecord) {
  if (searchFor.value === lib.libraryKey) {
    searchFor.value = '';
    return;
  }
  searchFor.value = lib.libraryKey;
  searchQuery.value = '';
  searchHits.value = null;
}

async function runSearch(lib: Api.AI.VectorLibRecord) {
  const q = searchQuery.value.trim();
  if (!q || searching.value) return;
  searching.value = true;
  const { data, error } = await fetchVectorLibSearch(lib.libraryKey, q, 5);
  searching.value = false;
  if (error) {
    searchHits.value = null; // 失败原因（如库陈旧）由全局拦截器弹出
    return;
  }
  searchHits.value = data?.records ?? [];
}

// ── 建库 ────────────────────────────────────────────────────────────────────
const showCreate = ref(false);
const creating = ref(false);
const createFormRef = ref<FormInst | null>(null);
const createForm = reactive({
  name: '',
  description: '',
  sourceType: 'manual' as 'manual' | 'file' | 'table_sync',
  table: '',
  keyColumn: '',
  contentColumns: '',
  refColumn: ''
});

const sourceOptions = computed(() => [
  { label: '手动条目（页面添加 / agent 写入）', value: 'manual' },
  { label: '文件上传（xlsx / csv / txt / md）', value: 'file' },
  { label: '数据表同步（自动增量，源表限 standard_ 前缀）', value: 'table_sync' }
]);

/** table_sync 条件必填校验（切回其他来源时字段隐藏，校验自动失效） */
function syncFieldValidator(requiredMsg: string, extra?: (v: string) => string | null) {
  return (_rule: unknown, value: string) => {
    if (createForm.sourceType !== 'table_sync') return true;
    const v = (value || '').trim();
    if (!v) return new Error(requiredMsg);
    if (extra) {
      const err = extra(v);
      if (err) return new Error(err);
    }
    return true;
  };
}

const createRules: FormRules = {
  name: { required: true, message: '请输入库名称', trigger: ['blur', 'input'] },
  table: {
    validator: syncFieldValidator('请输入源表名', v =>
      /^standard_[a-z0-9_]+$/i.test(v) ? null : '源表必须以 standard_ 开头（仅字母数字下划线）'
    ),
    trigger: ['blur', 'input']
  },
  keyColumn: { validator: syncFieldValidator('请输入唯一键列'), trigger: ['blur', 'input'] },
  contentColumns: {
    validator: syncFieldValidator('请输入参与嵌入的内容列', v =>
      v.split(/[,，]/).some(s => s.trim()) ? null : '请输入参与嵌入的内容列'
    ),
    trigger: ['blur', 'input']
  }
};

function openCreate() {
  createForm.name = '';
  createForm.description = '';
  createForm.sourceType = 'manual';
  createForm.table = '';
  createForm.keyColumn = '';
  createForm.contentColumns = '';
  createForm.refColumn = '';
  showCreate.value = true;
}

async function submitCreate() {
  try {
    await createFormRef.value?.validate();
  } catch {
    return; // 行内校验提示已显示
  }
  let sourceConfig: Record<string, unknown> | null = null;
  if (createForm.sourceType === 'table_sync') {
    const cols = createForm.contentColumns
      .split(/[,，]/)
      .map(s => s.trim())
      .filter(Boolean);
    sourceConfig = {
      table: createForm.table.trim(),
      key_column: createForm.keyColumn.trim(),
      content_columns: cols,
      ...(createForm.refColumn.trim() ? { ref_column: createForm.refColumn.trim() } : {})
    };
  }
  creating.value = true;
  const { data, error } = await fetchVectorLibCreate({
    name: createForm.name.trim(),
    description: createForm.description.trim() || null,
    sourceType: createForm.sourceType,
    sourceConfig
  });
  creating.value = false;
  if (error) return; // 失败提示由全局拦截器弹出
  showCreate.value = false;
  message.success(`已创建：${data?.libraryKey || ''}`);
  await loadList();
}

// ── 手动加条目（每行一条） ──────────────────────────────────────────────────
const showAddItems = ref(false);
const addItemsText = ref('');
const addingItems = ref(false);

async function submitAddItems() {
  const lines = addItemsText.value
    .split('\n')
    .map(s => s.trim())
    .filter(Boolean);
  if (!lines.length) {
    message.warning('内容为空');
    return;
  }
  if (lines.length > 500) {
    message.error('单次最多添加 500 条');
    return;
  }
  addingItems.value = true;
  const { data, error } = await fetchVectorLibAddItems(itemsLibKey.value, lines.map(content => ({ content })));
  addingItems.value = false;
  if (error) return;
  message.success(`已写入：新增 ${data?.added ?? 0} / 重嵌 ${data?.reembedded ?? 0} / 未变 ${data?.unchanged ?? 0}`);
  showAddItems.value = false;
  addItemsText.value = '';
  await Promise.all([loadItems(), loadList()]);
}

// ── 文件上传 ────────────────────────────────────────────────────────────────
const uploadInput = ref<HTMLInputElement>();
const uploadTarget = ref('');
const uploading = ref(false);

function pickUpload(lib: Api.AI.VectorLibRecord) {
  uploadTarget.value = lib.libraryKey;
  uploadInput.value?.click();
}

async function onUploadFile(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file || !uploadTarget.value) return;
  uploading.value = true;
  try {
    await fetchVectorLibUpload(uploadTarget.value, file);
    message.success('文件已接收，正在后台解析并摄入（条目数变化后自动刷新）');
    const lib = libs.value.find(l => l.libraryKey === uploadTarget.value);
    pendingUploads.value[uploadTarget.value] = { base: lib?.itemCount ?? -1, at: Date.now() };
    schedulePoll();
  } catch (err) {
    message.error((err as Error).message || '上传失败');
  }
  uploading.value = false;
}

// ── 构建 / 取消 / 删库 ──────────────────────────────────────────────────────
const busyKey = ref('');

function confirmBuild(lib: Api.AI.VectorLibRecord) {
  const stale = lib.isStale || lib.state === 'stale';
  const sync = isSyncSource(lib);
  const title = stale ? '重建向量库' : sync ? '立即同步' : '构建向量库';
  const content = stale
    ? '该库已陈旧（向量模型已切换）：重建将清空既有向量并全量重新嵌入，嵌入调用量 = 条目数，会产生可观费用。确认重建？'
    : sync
      ? '将按数据源执行一次增量同步：仅内容变化的行重新嵌入，源里消失的行删除。'
      : '将对库内全部条目执行一次嵌入核对（内容不变不重嵌）。';
  dialog.warning({
    title,
    content,
    positiveText: '确认',
    negativeText: '取消',
    onPositiveClick: async () => {
      busyKey.value = lib.libraryKey;
      const { error } = await fetchVectorLibBuild(lib.libraryKey);
      busyKey.value = '';
      if (error) return;
      message.success('构建任务已启动');
      await loadList();
      await refreshProgress();
      schedulePoll();
    }
  });
}

async function cancelBuild(lib: Api.AI.VectorLibRecord) {
  busyKey.value = lib.libraryKey;
  const { error } = await fetchVectorLibCancel(lib.libraryKey);
  busyKey.value = '';
  if (error) return;
  message.success('已发送取消信号，将在当前嵌入批完成后停止');
  await loadList();
  schedulePoll();
}

function confirmDeleteLib(lib: Api.AI.VectorLibRecord) {
  dialog.error({
    title: '删除向量库',
    content: `确认删除「${lib.name}」？库内全部 ${lib.itemCount} 个条目将被物理清除，此操作不可恢复。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      busyKey.value = lib.libraryKey;
      const { error } = await fetchVectorLibDelete(lib.libraryKey);
      busyKey.value = '';
      if (error) return;
      message.success('已删除');
      if (itemsLibKey.value === lib.libraryKey) itemsLibKey.value = '';
      if (searchFor.value === lib.libraryKey) searchFor.value = '';
      await loadList();
    }
  });
}

// ── 生命周期 ────────────────────────────────────────────────────────────────
watch(
  () => props.show,
  async v => {
    if (v) {
      await loadList();
      await refreshProgress();
      schedulePoll();
    } else {
      clearPoll();
    }
  }
);

onUnmounted(() => {
  clearPoll();
  if (kwTimer !== null) clearTimeout(kwTimer);
});
</script>

<template>
  <NDrawer v-model:show="visible" :width="960" placement="right">
    <NDrawerContent title="向量库" :native-scrollbar="false" closable>
      <!-- 当前激活向量模型（陈旧判定的参照系） -->
      <div class="vlc-ref">
        <div class="vlc-ref-line">
          <span class="vlc-ref-k">当前向量模型</span>
          <span class="vlc-ref-v" :title="activeBlock || ''">{{ activeBlockLabel || activeBlock || '未配置' }}</span>
          <span v-if="activeDim" class="vlc-ref-dim">{{ activeDim }} 维</span>
        </div>
        <div class="vlc-ref-tip">库的构建快照与之不等即「陈旧」，需显式重建后才能语义搜索</div>
      </div>

      <div class="vlc-listhead">
        <div class="vlc-listhead-sub"></div>
        <button class="vlc-btn is-primary" @click="openCreate">＋ 新建向量库</button>
      </div>

      <div v-if="!libs.length && !listLoading" class="vlc-empty">
        <span class="vlc-empty-icon" v-html="ICONS.db" />
        <span>暂无向量库，点「新建向量库」创建；也可以直接对 agent 说「帮我建一个 xx 向量库」</span>
      </div>

      <div class="vlc-list">
        <article v-for="lib in libs" :key="lib.libraryKey" class="vlc-card" :class="`is-${lib.state}`">
          <div class="vlc-top">
            <span class="vlc-medal" :class="`is-${lib.state}`" :title="`状态：${STATE_META[lib.state].label}`" v-html="ICONS[lib.state]" />
            <div class="vlc-id">
              <span class="vlc-name">{{ lib.name }}</span>
              <span class="vlc-key">{{ lib.libraryKey }}</span>
              <span v-if="lib.sourceTable" class="vlc-key vlc-srctable" :title="`同步源表：${lib.sourceTable}`">
                ⇠ {{ lib.sourceTable }}
              </span>
              <span class="vlc-chip" :class="`is-${lib.state}`">{{ STATE_META[lib.state].label }}</span>
              <span class="vlc-chip is-plain">{{ SOURCE_LABEL[lib.sourceType] || lib.sourceType }}</span>
              <span v-if="showOwner(lib)" class="vlc-chip is-owner" :title="`拥有者：${lib.ownerName}`">{{ lib.ownerName }}</span>
              <span v-if="lib.failedCount > 0" class="vlc-chip is-failed" title="嵌入失败待重试的条目数；再次构建会自动续跑">{{ lib.failedCount }} 条嵌入失败</span>
            </div>
            <div class="vlc-actions">
              <button class="vlc-btn" :class="{ on: itemsLibKey === lib.libraryKey }" @click="toggleItems(lib)">
                {{ itemsLibKey === lib.libraryKey ? '收起条目' : '条目' }}
              </button>
              <button class="vlc-btn" :class="{ on: searchFor === lib.libraryKey }" @click="toggleSearch(lib)">检索验证</button>
              <button
                v-if="lib.canWrite && !isSyncSource(lib)"
                class="vlc-btn"
                :disabled="uploading"
                @click="pickUpload(lib)"
              >
                上传
              </button>
              <button
                v-if="lib.canWrite && !lib.isBuilding && (isSyncSource(lib) || lib.itemCount > 0)"
                class="vlc-btn is-primary"
                :disabled="busyKey === lib.libraryKey"
                @click="confirmBuild(lib)"
              >
                <span v-if="busyKey === lib.libraryKey" class="vlc-btn-spin" />{{ buildLabel(lib) }}
              </button>
              <button
                v-if="lib.canWrite && lib.scope !== 'system'"
                class="vlc-btn is-danger"
                :disabled="lib.isBuilding || busyKey === lib.libraryKey"
                @click="confirmDeleteLib(lib)"
              >
                删除库
              </button>
            </div>
          </div>

          <p v-if="lib.description" class="vlc-desc">{{ lib.description }}</p>

          <!-- 统计块 -->
          <div class="vlc-stats">
            <div class="vlc-stat">
              <div class="vlc-stat-v" :title="String(lib.itemCount)">{{ fmtNum(lib.itemCount) }}</div>
              <div class="vlc-stat-k">条目</div>
            </div>
            <div class="vlc-stat">
              <div class="vlc-stat-v" :title="lib.embedBlock || ''">{{ lib.embedBlockLabel || lib.embedBlock || '未构建' }}</div>
              <div class="vlc-stat-k">向量模型{{ lib.embedDim ? ` · ${lib.embedDim} 维` : '' }}</div>
            </div>
            <div class="vlc-stat">
              <div class="vlc-stat-v" :title="lib.lastBuiltAt || ''">{{ relTime(lib.lastBuiltAt) }}</div>
              <div class="vlc-stat-k">最近构建</div>
            </div>
          </div>

          <!-- 构建进度（进度条 + 阶段 + 取消） -->
          <div v-if="lib.isBuilding" class="vlc-progress">
            <div class="vlc-progress-track">
              <div class="vlc-progress-bar" :class="{ ind: !progressOf(lib)?.total }" :style="barStyle(lib)" />
            </div>
            <span class="vlc-progress-txt">
              {{ PHASE_LABEL[progressOf(lib)?.phase || ''] || '构建中' }}
              <template v-if="progressTxt(lib)">· {{ progressTxt(lib) }}</template>
            </span>
            <button class="vlc-btn is-warn" :disabled="busyKey === lib.libraryKey" @click="cancelBuild(lib)">取消</button>
          </div>

          <div v-if="lib.state === 'error' && lib.lastError" class="vlc-banner is-error">{{ lib.lastError }}</div>
          <div v-else-if="lib.lastError && lib.state !== 'building'" class="vlc-banner is-warn">{{ lib.lastError }}</div>
          <div v-if="lib.isStale || lib.state === 'stale'" class="vlc-banner is-stale">
            向量模型已切换，库内向量已失效：语义搜索将拒绝返回结果。请点击「重建」全量重嵌恢复。
          </div>

          <!-- 测试检索面板 -->
          <div v-if="searchFor === lib.libraryKey" class="vlc-ts">
            <div class="vlc-ts-bar">
              <input
                v-model="searchQuery"
                class="vlc-ts-input"
                placeholder="输入查询文本验证召回效果，回车检索"
                @keyup.enter="runSearch(lib)"
              />
              <button class="vlc-btn is-primary" :disabled="searching || !searchQuery.trim()" @click="runSearch(lib)">
                <span v-if="searching" class="vlc-btn-spin" />{{ searching ? '检索中' : '检索' }}
              </button>
            </div>
            <div v-if="searchHits" class="vlc-ts-results">
              <div v-if="!searchHits.length" class="vlc-ts-empty">无召回 —— 库可能为空，或查询与条目内容不相关</div>
              <div v-for="hit in searchHits" :key="hit.itemKey" class="vlc-ts-hit">
                <div class="vlc-ts-score" :title="`相似度 ${hit.score}`">
                  <span class="vlc-ts-scorebar" :style="{ width: scorePct(hit.score) }" />
                  <span class="vlc-ts-scoretxt">{{ scorePct(hit.score) }}</span>
                </div>
                <div class="vlc-ts-body">
                  <div class="vlc-ts-content">{{ hit.contentPreview || '—' }}</div>
                  <div class="vlc-ts-key mono">
                    {{ hit.itemKey }}<template v-if="hit.refKey"> · {{ hit.refKey }}</template>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 条目面板（自绘表格） -->
          <div v-if="itemsLibKey === lib.libraryKey" class="vt">
            <div class="vt-bar">
              <div class="vt-searchbox">
                <span class="vt-search-icon" v-html="ICONS.search" />
                <input
                  class="vt-search-input"
                  :value="keyword"
                  placeholder="搜索条目内容 / 编号…"
                  @input="onKeywordInputEvent"
                  @keyup.enter="searchNow"
                />
                <button v-if="keyword" class="vt-search-clear" @click="clearKeyword" v-html="ICONS.close" />
              </div>
              <template v-if="lib.canWrite">
                <span class="vt-check" @click="toggleCheckAll">
                  <span class="vt-checkbox" :class="{ on: allPageChecked, half: somePageChecked }" />
                  <span>全选本页</span>
                </span>
                <button class="vlc-btn is-primary" @click="showAddItems = true">添加条目</button>
                <button class="vlc-btn is-danger" :disabled="!checkedKeys.length || deletingItems" @click="deleteItems(checkedKeys)">
                  <span v-if="deletingItems" class="vlc-btn-spin" />删除选中（{{ checkedKeys.length }}）
                </button>
              </template>
              <span class="vt-total">共 {{ itemCount }} 条</span>
            </div>

            <div class="vt-head" :style="{ gridTemplateColumns: vtGrid }">
              <span v-if="lib.canWrite" />
              <span />
              <span class="vt-th" title="条目在库内的唯一标识，删除条目以此为凭">编号</span>
              <span class="vt-th" title="参与向量嵌入的文本，点击行首箭头展开全文">内容</span>
              <span v-if="sawRefKey" class="vt-th" title="语义搜索时做等值过滤的值（如限定某个标准号）">过滤键</span>
              <span>更新时间</span>
              <span v-if="lib.canWrite" />
            </div>

            <div v-if="itemsLoading && !items.length" class="vt-skeleton">
              <div v-for="i in 3" :key="i" class="vt-skel-row" />
            </div>
            <div v-else-if="!items.length" class="vt-empty">{{ keyword ? '没有匹配的条目' : '暂无条目' }}</div>

            <template v-else>
              <div v-for="row in items" :key="row.itemKey" class="vt-item">
                <div class="vt-row" :style="{ gridTemplateColumns: vtGrid }" :class="{ expanded: expandedKey === row.itemKey }">
                  <span v-if="lib.canWrite" class="vt-cell-check" @click.stop="toggleCheck(row.itemKey)">
                    <span class="vt-checkbox" :class="{ on: checkedKeys.includes(row.itemKey) }" />
                  </span>
                  <span class="vt-cell-exp" @click.stop="toggleExpand(row.itemKey)">
                    <span class="vt-chevron" :class="{ open: expandedKey === row.itemKey }" v-html="ICONS.chevron" />
                  </span>
                  <span class="vt-cell mono" :title="row.itemKey">{{ row.itemKey }}</span>
                  <span class="vt-cell vt-cell-content" :title="row.content.slice(0, 500)">{{ row.content }}</span>
                  <span v-if="sawRefKey" class="vt-cell" :title="row.refKey || ''">{{ row.refKey || '—' }}</span>
                  <span class="vt-cell vt-cell-time" :title="row.updatedAt || ''">{{ relTime(row.updatedAt) }}</span>
                  <span v-if="lib.canWrite" class="vt-cell-act">
                    <button class="vlc-btn is-danger is-mini" @click.stop="deleteItems([row.itemKey])">删除</button>
                  </span>
                </div>
                <div v-if="expandedKey === row.itemKey" class="vt-expand">
                  <div class="vt-expand-kv mono">
                    <span>编号 {{ row.itemKey }}</span>
                    <span v-if="row.refKey">过滤键 {{ row.refKey }}</span>
                    <span v-if="row.updatedAt">更新 {{ row.updatedAt }}</span>
                  </div>
                  <div class="vt-expand-content">{{ row.content }}</div>
                  <div v-if="row.payload" class="vt-expand-payload mono">payload：{{ JSON.stringify(row.payload) }}</div>
                </div>
              </div>
            </template>

            <!-- 自定义分页 -->
            <div class="vt-pager">
              <div class="vt-sizes">
                <button v-for="s in [10, 20, 50]" :key="s" class="vt-size" :class="{ on: pageSize === s }" @click="setPageSize(s)">
                  {{ s }}
                </button>
                <span class="vt-size-k">条/页</span>
              </div>
              <span class="vt-page-info">第 {{ page }} / {{ pageCount }} 页</span>
              <button class="vt-nav" :disabled="page <= 1" @click="setPage(page - 1)">‹ 上一页</button>
              <button class="vt-nav" :disabled="page >= pageCount" @click="setPage(page + 1)">下一页 ›</button>
            </div>
          </div>
        </article>
      </div>
    </NDrawerContent>
  </NDrawer>

  <!-- 建库弹窗（行内校验） -->
  <NModal v-model:show="showCreate" preset="card" title="新建向量库" style="width: 560px" :mask-closable="false">
    <NForm ref="createFormRef" :model="createForm" :rules="createRules" label-placement="left" label-width="88">
      <NFormItem label="库名称" path="name">
        <NInput v-model:value="createForm.name" placeholder="如：项目资料库" maxlength="128" />
      </NFormItem>
      <NFormItem label="用途说明">
        <NInput
          v-model:value="createForm.description"
          type="textarea"
          placeholder="选填，帮助 agent 判断何时搜索此库"
          maxlength="512"
          :rows="2"
        />
      </NFormItem>
      <NFormItem label="内容来源">
        <NSelect v-model:value="createForm.sourceType" :options="sourceOptions" />
      </NFormItem>
      <template v-if="createForm.sourceType === 'table_sync'">
        <NFormItem label="源表" path="table">
          <NInput v-model:value="createForm.table" placeholder="standard_xxx（仅标准库表）" />
        </NFormItem>
        <NFormItem label="唯一键列" path="keyColumn">
          <NInput v-model:value="createForm.keyColumn" placeholder="如 id / standard_no" />
        </NFormItem>
        <NFormItem label="内容列" path="contentColumns">
          <NInput v-model:value="createForm.contentColumns" placeholder="参与嵌入的列，逗号分隔，如 cname,use_range" />
        </NFormItem>
        <NFormItem label="过滤键列">
          <NInput v-model:value="createForm.refColumn" placeholder="选填，写入 refKey 供等值过滤，如 standard_no" />
        </NFormItem>
        <p class="vlc-form-tip">数据表同步为自动增量：构建 / 每次同步只重嵌内容变化的行；源表消失的行自动删除。</p>
      </template>
    </NForm>
    <template #footer>
      <div class="vlc-modal-footer">
        <button class="vlc-btn" @click="showCreate = false">取消</button>
        <button class="vlc-btn is-primary" :disabled="creating" @click="submitCreate">
          <span v-if="creating" class="vlc-btn-spin" />创建
        </button>
      </div>
    </template>
  </NModal>

  <!-- 添加条目弹窗（每行一条） -->
  <NModal v-model:show="showAddItems" preset="card" title="添加条目" style="width: 560px" :mask-closable="false">
    <p class="vlc-form-tip">每行一个条目（非空行即独立嵌入）；单次最多 500 条。内容不变的条目不会重复嵌入。</p>
    <NInput
      v-model:value="addItemsText"
      type="textarea"
      :rows="8"
      placeholder="每行一条内容，如：&#10;GB/T 1.1 规定了标准化文件的起草规则&#10;……"
    />
    <template #footer>
      <div class="vlc-modal-footer">
        <button class="vlc-btn" @click="showAddItems = false">取消</button>
        <button class="vlc-btn is-primary" :disabled="addingItems" @click="submitAddItems">
          <span v-if="addingItems" class="vlc-btn-spin" />写入
        </button>
      </div>
    </template>
  </NModal>

  <!-- 文件选择器（隐藏） -->
  <input ref="uploadInput" type="file" accept=".xlsx,.xlsm,.csv,.txt,.md" style="display: none" @change="onUploadFile" />
</template>

<style scoped>
/* ── 顶部参照系 ─────────────────────────────────────────────── */
.vlc-ref {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 16px;
  padding: 10px 14px;
  border-radius: 12px;
  background: linear-gradient(120deg, rgba(30, 64, 175, 0.07), rgba(30, 64, 175, 0.02));
  border: 1px solid rgba(30, 64, 175, 0.12);
}

.vlc-ref-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.vlc-ref-k {
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
}

.vlc-ref-v {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 700;
  color: #1e40af;
}

.vlc-ref-dim {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 999px;
  background: rgba(30, 64, 175, 0.1);
  color: #1e40af;
}

.vlc-ref-tip {
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
  text-align: right;
}

/* ── 区块骨架 ───────────────────────────────────────────────── */
/* ── 列表头（单一列表：系统库 + 用户库合并，徽标区分归属） ─────── */
.vlc-listhead {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.vlc-listhead-sub {
  flex: 1;
  font-size: 11px;
  line-height: 1.5;
  color: var(--ink-4, #94a3b8);
}

/* ── 按钮体系（自绘，替代 NButton） ─────────────────────────── */
.vlc-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 11px;
  border-radius: 8px;
  border: 1px solid rgba(100, 116, 139, 0.28);
  background: rgba(255, 255, 255, 0.7);
  color: var(--ink-3, #475569);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.5;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.18s ease;
}

.vlc-btn:hover:not(:disabled) {
  border-color: rgba(30, 64, 175, 0.4);
  color: #1e40af;
  background: rgba(255, 255, 255, 0.95);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px -4px rgba(30, 64, 175, 0.25);
}

.vlc-btn.on {
  border-color: rgba(30, 64, 175, 0.45);
  color: #1e40af;
  background: rgba(30, 64, 175, 0.08);
}

.vlc-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.vlc-btn.is-primary {
  border-color: rgba(30, 64, 175, 0.35);
  background: rgba(30, 64, 175, 0.09);
  color: #1e40af;
}

.vlc-btn.is-primary:hover:not(:disabled) {
  background: rgba(30, 64, 175, 0.16);
}

.vlc-btn.is-danger {
  border-color: rgba(208, 48, 80, 0.3);
  background: rgba(208, 48, 80, 0.05);
  color: #d03050;
}

.vlc-btn.is-danger:hover:not(:disabled) {
  border-color: rgba(208, 48, 80, 0.5);
  color: #d03050;
  background: rgba(208, 48, 80, 0.1);
  box-shadow: 0 4px 12px -4px rgba(208, 48, 80, 0.25);
}

.vlc-btn.is-warn {
  border-color: rgba(217, 119, 6, 0.35);
  background: rgba(217, 119, 6, 0.07);
  color: #b45309;
}

.vlc-btn.is-mini {
  padding: 2px 8px;
  font-size: 10px;
  border-radius: 6px;
}

.vlc-btn-spin {
  width: 10px;
  height: 10px;
  flex-shrink: 0;
  border: 1.5px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: vlc-rotate 0.7s linear infinite;
}

@keyframes vlc-rotate {
  to {
    transform: rotate(360deg);
  }
}

.vlc-spin {
  animation: vlc-rotate 0.9s linear infinite;
}

/* ── 库卡片 ─────────────────────────────────────────────────── */
.vlc-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.vlc-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 14px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.6),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 6px 20px -10px rgba(30, 64, 175, 0.12);
  transition: box-shadow 0.2s ease;
}

.vlc-card:hover {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.6),
    0 2px 4px rgba(15, 23, 42, 0.05),
    0 12px 28px -12px rgba(30, 64, 175, 0.2);
}

.vlc-card.is-stale {
  border-color: rgba(217, 119, 6, 0.3);
}

.vlc-card.is-error {
  border-color: rgba(208, 48, 80, 0.25);
}

.vlc-top {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.vlc-medal {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  border-radius: 10px;
}

.vlc-medal.is-ready {
  background: rgba(22, 163, 74, 0.1);
  color: #16a34a;
}

.vlc-medal.is-building {
  background: rgba(30, 64, 175, 0.1);
  color: #1e40af;
}

.vlc-medal.is-stale {
  background: rgba(217, 119, 6, 0.12);
  color: #b45309;
}

.vlc-medal.is-error {
  background: rgba(208, 48, 80, 0.1);
  color: #d03050;
}

.vlc-medal.is-empty {
  background: rgba(100, 116, 139, 0.1);
  color: #64748b;
}

.vlc-id {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: 1 1 auto;
  min-width: 0;
  flex-wrap: wrap;
}

.vlc-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--ink, #0f172a);
}

.vlc-key {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--ink-4, #94a3b8);
}

/* 同步源表名（表来源库：数据表同步 / 系统语义库的固定源表） */
.vlc-key.vlc-srctable {
  color: #1e40af;
  opacity: 0.75;
}

.vlc-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-left: auto;
}

/* ── 状态 / 来源 / 归属 徽标 ───────────────────────────────── */
.vlc-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 600;
  line-height: 1.7;
}

.vlc-chip.is-ready {
  background: rgba(22, 163, 74, 0.1);
  color: #15803d;
}

.vlc-chip.is-building {
  background: rgba(30, 64, 175, 0.1);
  color: #1e40af;
}

.vlc-chip.is-stale {
  background: rgba(217, 119, 6, 0.12);
  color: #b45309;
}

.vlc-chip.is-error {
  background: rgba(208, 48, 80, 0.1);
  color: #d03050;
}

.vlc-chip.is-empty {
  background: rgba(100, 116, 139, 0.12);
  color: #64748b;
}

.vlc-chip.is-plain {
  background: rgba(100, 116, 139, 0.08);
  color: var(--ink-3, #64748b);
  font-weight: 500;
}

.vlc-chip.is-owner {
  background: rgba(124, 58, 237, 0.08);
  color: #6d28d9;
  font-weight: 500;
}

.vlc-chip.is-failed {
  background: rgba(217, 119, 6, 0.12);
  color: #b45309;
}

.vlc-desc {
  margin: -2px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ink-3, #64748b);
}

/* ── 统计块 ─────────────────────────────────────────────────── */
.vlc-stats {
  display: flex;
  align-items: stretch;
  gap: 0;
  padding: 8px 2px 2px;
}

.vlc-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 16px;
  min-width: 0;
}

.vlc-stat:first-child {
  padding-left: 0;
}

.vlc-stat + .vlc-stat {
  border-left: 1px dashed rgba(30, 64, 175, 0.14);
}

.vlc-stat-v {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink, #0f172a);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}

.vlc-stat-k {
  font-size: 10px;
  color: var(--ink-4, #94a3b8);
  white-space: nowrap;
}

/* ── 构建进度 ───────────────────────────────────────────────── */
.vlc-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(30, 64, 175, 0.05);
  border: 1px solid rgba(30, 64, 175, 0.1);
}

.vlc-progress-track {
  flex: 1;
  height: 6px;
  border-radius: 999px;
  background: rgba(30, 64, 175, 0.12);
  overflow: hidden;
}

.vlc-progress-bar {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #3b82f6, #1e40af);
  transition: width 0.5s ease;
}

.vlc-progress-bar.ind {
  width: 35%;
  animation: vlc-slide 1.3s ease-in-out infinite;
}

@keyframes vlc-slide {
  0% {
    transform: translateX(-110%);
  }
  100% {
    transform: translateX(320%);
  }
}

.vlc-progress-txt {
  font-size: 11px;
  color: #1e40af;
  white-space: nowrap;
}

/* ── 提示横幅 ───────────────────────────────────────────────── */
.vlc-banner {
  padding: 7px 11px;
  border-radius: 9px;
  font-size: 11px;
  line-height: 1.6;
  word-break: break-all;
}

.vlc-banner.is-error {
  background: rgba(208, 48, 80, 0.06);
  border: 1px solid rgba(208, 48, 80, 0.15);
  color: #d03050;
}

/* 部分失败警示（库整体可用，但有失败条目/历史错误摘要，不隐藏） */
.vlc-banner.is-warn {
  background: rgba(234, 179, 8, 0.08);
  border: 1px solid rgba(234, 179, 8, 0.25);
  color: #a16207;
}

.vlc-banner.is-stale {
  background: rgba(217, 119, 6, 0.07);
  border: 1px solid rgba(217, 119, 6, 0.18);
  color: #b45309;
}

/* ── 测试检索面板 ───────────────────────────────────────────── */
.vlc-ts {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 11px;
  background: rgba(30, 64, 175, 0.04);
  border: 1px dashed rgba(30, 64, 175, 0.18);
}

.vlc-ts-bar {
  display: flex;
  gap: 8px;
}

.vlc-ts-input {
  flex: 1;
  min-width: 0;
  padding: 5px 11px;
  border-radius: 9px;
  border: 1px solid rgba(30, 64, 175, 0.2);
  background: rgba(255, 255, 255, 0.85);
  font-size: 12px;
  color: var(--ink, #0f172a);
  outline: none;
  transition: border-color 0.15s ease;
}

.vlc-ts-input:focus {
  border-color: #1e40af;
  box-shadow: 0 0 0 2px rgba(30, 64, 175, 0.12);
}

.vlc-ts-results {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.vlc-ts-empty {
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
  padding: 4px 0;
}

.vlc-ts-hit {
  display: flex;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.75);
  border: 1px solid rgba(30, 64, 175, 0.08);
}

.vlc-ts-score {
  position: relative;
  width: 54px;
  height: 40px;
  flex-shrink: 0;
  border-radius: 8px;
  background: rgba(30, 64, 175, 0.07);
  overflow: hidden;
  display: grid;
  place-items: center;
}

.vlc-ts-scorebar {
  position: absolute;
  left: 0;
  bottom: 0;
  width: 100%;
  height: 3px;
  background: linear-gradient(90deg, #3b82f6, #1e40af);
}

.vlc-ts-scoretxt {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 700;
  color: #1e40af;
}

.vlc-ts-body {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.vlc-ts-content {
  font-size: 12px;
  line-height: 1.55;
  color: var(--ink, #0f172a);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.vlc-ts-key {
  font-size: 10px;
  color: var(--ink-4, #94a3b8);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── 自绘条目表格 ───────────────────────────────────────────── */
.vt {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px dashed rgba(30, 64, 175, 0.14);
}

.vt-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.vt-searchbox {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 220px;
  max-width: 340px;
  padding: 4px 10px;
  border-radius: 9px;
  border: 1px solid rgba(30, 64, 175, 0.18);
  background: rgba(255, 255, 255, 0.8);
  transition: border-color 0.15s ease;
}

.vt-searchbox:focus-within {
  border-color: #1e40af;
  box-shadow: 0 0 0 2px rgba(30, 64, 175, 0.1);
}

.vt-search-icon {
  display: inline-flex;
  color: var(--ink-4, #94a3b8);
  flex-shrink: 0;
}

.vt-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 11.5px;
  color: var(--ink, #0f172a);
}

.vt-search-input::placeholder {
  color: var(--ink-4, #94a3b8);
}

.vt-search-clear {
  display: grid;
  place-items: center;
  width: 16px;
  height: 16px;
  border: none;
  border-radius: 50%;
  background: rgba(100, 116, 139, 0.15);
  color: var(--ink-3, #64748b);
  cursor: pointer;
  flex-shrink: 0;
  padding: 0;
}

.vt-search-clear:hover {
  background: rgba(208, 48, 80, 0.15);
  color: #d03050;
}

.vt-check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--ink-3, #64748b);
  cursor: pointer;
  user-select: none;
}

.vt-total {
  margin-left: auto;
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
}

/* 自绘勾选框 */
.vt-checkbox {
  position: relative;
  display: inline-block;
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  border: 1.5px solid rgba(100, 116, 139, 0.45);
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.9);
  transition: all 0.15s ease;
  cursor: pointer;
}

.vt-checkbox.on {
  border-color: #1e40af;
  background: #1e40af;
}

.vt-checkbox.on::after {
  content: '';
  position: absolute;
  left: 3.5px;
  top: 0.5px;
  width: 4px;
  height: 8px;
  border: solid #fff;
  border-width: 0 1.5px 1.5px 0;
  transform: rotate(45deg);
}

.vt-checkbox.half {
  border-color: #1e40af;
}

.vt-checkbox.half::after {
  content: '';
  position: absolute;
  left: 2px;
  top: 5px;
  width: 8px;
  height: 1.5px;
  background: #1e40af;
  border-radius: 1px;
}

.vt-cell-check {
  display: grid;
  place-items: center;
  cursor: pointer;
}

/* 表头 */
.vt-head {
  display: grid;
  align-items: center;
  gap: 8px;
  padding: 4px 6px 6px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.14);
  font-size: 10.5px;
  font-weight: 600;
  color: var(--ink-4, #94a3b8);
}

.vt-th {
  cursor: help;
}

/* 行 */
.vt-row {
  display: grid;
  align-items: center;
  gap: 8px;
  padding: 7px 6px;
  border-bottom: 1px solid rgba(15, 23, 42, 0.05);
  transition: background 0.12s ease;
}

.vt-row:hover {
  background: rgba(30, 64, 175, 0.035);
}

.vt-row.expanded {
  background: rgba(30, 64, 175, 0.045);
  border-bottom-color: transparent;
}

.vt-cell {
  font-size: 11.5px;
  color: var(--ink-2, #334155);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.vt-cell-content {
  cursor: default;
}

.vt-cell-time {
  color: var(--ink-4, #94a3b8);
  font-size: 10.5px;
}

.vt-cell-exp {
  display: grid;
  place-items: center;
  cursor: pointer;
  color: var(--ink-4, #94a3b8);
}

.vt-cell-exp:hover {
  color: #1e40af;
}

.vt-chevron {
  display: inline-flex;
  transition: transform 0.18s ease;
}

.vt-chevron.open {
  transform: rotate(180deg);
}

.vt-cell-act {
  display: grid;
  place-items: center;
}

.mono {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
}

/* 展开详情 */
.vt-expand {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin: 0 6px 8px;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(30, 64, 175, 0.04);
  border: 1px dashed rgba(30, 64, 175, 0.16);
}

.vt-expand-kv {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 10px;
  color: var(--ink-4, #94a3b8);
}

.vt-expand-content {
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink, #0f172a);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 300px;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，长文本滚动条出现不抖动 */
}

.vt-expand-payload {
  font-size: 10.5px;
  line-height: 1.6;
  color: var(--ink-3, #64748b);
  word-break: break-all;
}

/* 加载骨架 */
.vt-skeleton {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 8px 6px;
}

.vt-skel-row {
  height: 26px;
  border-radius: 8px;
  background: linear-gradient(90deg, rgba(30, 64, 175, 0.06) 25%, rgba(30, 64, 175, 0.12) 50%, rgba(30, 64, 175, 0.06) 75%);
  background-size: 200% 100%;
  animation: vlc-shimmer 1.4s ease infinite;
}

@keyframes vlc-shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

.vt-empty {
  padding: 22px 0;
  text-align: center;
  font-size: 12px;
  color: var(--ink-4, #94a3b8);
}

/* 分页 */
.vt-pager {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding-top: 2px;
}

.vt-sizes {
  display: flex;
  align-items: center;
  gap: 3px;
}

.vt-size {
  min-width: 26px;
  padding: 2px 6px;
  border-radius: 7px;
  border: 1px solid transparent;
  background: transparent;
  font-size: 11px;
  color: var(--ink-3, #64748b);
  cursor: pointer;
  transition: all 0.15s ease;
}

.vt-size:hover {
  background: rgba(30, 64, 175, 0.06);
}

.vt-size.on {
  border-color: rgba(30, 64, 175, 0.35);
  background: rgba(30, 64, 175, 0.08);
  color: #1e40af;
  font-weight: 700;
}

.vt-size-k {
  font-size: 10px;
  color: var(--ink-4, #94a3b8);
  margin-left: 2px;
}

.vt-page-info {
  font-size: 11px;
  color: var(--ink-3, #64748b);
}

.vt-nav {
  padding: 3px 10px;
  border-radius: 7px;
  border: 1px solid rgba(100, 116, 139, 0.25);
  background: rgba(255, 255, 255, 0.75);
  font-size: 11px;
  color: var(--ink-3, #64748b);
  cursor: pointer;
  transition: all 0.15s ease;
}

.vt-nav:hover:not(:disabled) {
  border-color: rgba(30, 64, 175, 0.4);
  color: #1e40af;
}

.vt-nav:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* ── 空状态 ─────────────────────────────────────────────────── */
.vlc-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 26px 0;
  text-align: center;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-4, #94a3b8);
  border: 1px dashed rgba(30, 64, 175, 0.14);
  border-radius: 12px;
}

.vlc-empty-icon {
  color: rgba(30, 64, 175, 0.28);
  display: inline-flex;
}

/* ── 弹窗 ───────────────────────────────────────────────────── */
.vlc-form-tip {
  margin: 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--ink-4, #94a3b8);
}

.vlc-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* ── 响应式 ─────────────────────────────────────────────────── */
@media (max-width: 720px) {
  .vlc-stats {
    flex-wrap: wrap;
    gap: 8px 0;
  }
  .vlc-ref {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }
  .vlc-ref-tip {
    text-align: left;
  }
}
</style>
