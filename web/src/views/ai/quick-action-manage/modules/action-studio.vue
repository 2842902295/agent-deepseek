<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, computed, nextTick, watch } from 'vue';
import { NButton, NModal, NInput, NInputNumber, NSelect, NSpin, NPopconfirm, NSpace, NUpload, NImage, NImageGroup, type UploadFileInfo, useMessage } from 'naive-ui';
import type { UploadCustomRequestOptions } from 'naive-ui';
import { getServiceBaseURL } from '@/utils/service';
import { ensureFreshAccessToken } from '@/service/request/shared';
import SvgIcon from '@/components/custom/svg-icon.vue';
import {
  type QuickAction,
  type QuickActionCategory,
  type QuickActionExample,
  type QuickActionGroup,
  type AgentSession,
  fetchManageQuickActions,
  fetchCreateQuickAction,
  fetchUpdateQuickAction,
  fetchDeleteQuickAction,
  fetchCreateQuickActionExampleFromSession,
  fetchUpdateQuickActionExample,
  fetchDeleteQuickActionExample,
  fetchCreateQuickActionCategory,
  fetchUpdateQuickActionCategory,
  fetchDeleteQuickActionCategory,
  fetchSortQuickActionCategories,
  fetchSortQuickActions,
  fetchAgentSessions,
  type Profession,
  fetchProfessions
} from '@/service/api';

const emit = defineEmits<{
  /** 跳转到职业体系并选中指定职业 */
  openProfession: [id: number];
  /** 仅跳转到职业体系工作区 */
  gotoProfessions: [];
}>();

const message = useMessage();
const loading = ref(false);
const actions = ref<QuickAction[]>([]);
const categories = ref<QuickActionCategory[]>([]);
const groups = ref<QuickActionGroup[]>([]);
const sessions = ref<AgentSession[]>([]);
const selectedAction = ref<QuickAction | null>(null);
const detailKey = ref(0);

const actionFormVisible = ref(false);
const actionForm = ref({
  id: 0,
  name: '',
  skillKey: '',
  icon: '',
  description: '',
  categoryIds: [] as number[],
  visibility: 'public' as 'public' | 'role' | 'private'
});
const actionFormMode = ref<'create' | 'edit'>('create');

const exampleFormVisible = ref(false);
const exampleForm = ref({
  id: 0,
  actionId: 0,
  sessionKey: '',
  title: '',
  description: '',
  previewImages: [] as string[],
  sortOrder: 0
});
const exampleFormMode = ref<'create' | 'edit'>('create');
const uploadingImage = ref(false);

/* ── 多图管理（支持拖拽排序） ── */
interface ImageItem {
  id: string;
  path: string;
  url: string;
}
const imageList = ref<ImageItem[]>([]);
const dragItemIndex = ref<number | null>(null);
const dragOverIndex = ref<number | null>(null);

function genImgId() {
  return Math.random().toString(36).slice(2, 10);
}

function onDragStart(index: number) {
  dragItemIndex.value = index;
}

function onDragOver(index: number, e: DragEvent) {
  e.preventDefault();
  dragOverIndex.value = index;
}

function onDragEnd() {
  if (dragItemIndex.value !== null && dragOverIndex.value !== null && dragItemIndex.value !== dragOverIndex.value) {
    const items = [...imageList.value];
    const [moved] = items.splice(dragItemIndex.value, 1);
    items.splice(dragOverIndex.value, 0, moved);
    imageList.value = items;
  }
  dragItemIndex.value = null;
  dragOverIndex.value = null;
}

function removeImage(index: number) {
  imageList.value.splice(index, 1);
}

/* 程序化触发 NImageGroup 预览 */
const previewTriggerRef = ref<InstanceType<typeof NImage> | null>(null);
const previewIndex = ref(0);

function triggerPreview(index: number) {
  previewIndex.value = index;
  nextTick(() => {
    (previewTriggerRef.value as any)?.$el?.querySelector('img')?.click();
  });
}

/* ── 左侧两级树：类型章节（拖拽排序）→ 功能（组内拖拽排序）+ 未分组 ── */
interface ChapterNode {
  kind: 'chapter';
  chIdx: number; // 章节序号（仅章节节点间排序用）
  cat: QuickActionCategory;
  actions: QuickAction[];
}
interface UngroupedNode {
  kind: 'ungrouped';
  actions: QuickAction[];
}
type TreeNode = ChapterNode | UngroupedNode;

/** 与用户页橱窗一致的渲染结构：章节顺序 = 类型 sort_order，组内 = 类型内排序 */
const tree = computed<TreeNode[]>(() => {
  const byId = new Map(actions.value.map(a => [a.id, a]));
  const groupByCat = new Map(groups.value.map(g => [g.id, g]));
  const nodes: TreeNode[] = [];
  const grouped = new Set<number>();
  let chIdx = 0;
  for (const cat of categories.value) {
    const members = (groupByCat.get(cat.id)?.actionIds ?? [])
      .map(id => byId.get(id))
      .filter((a): a is QuickAction => Boolean(a));
    members.forEach(a => grouped.add(a.id));
    nodes.push({ kind: 'chapter', chIdx: chIdx++, cat, actions: members });
  }
  const rest = actions.value.filter(a => !grouped.has(a.id));
  if (rest.length > 0 || categories.value.length === 0) {
    nodes.push({ kind: 'ungrouped', actions: rest });
  }
  return nodes;
});

const catNameMap = computed(() => new Map(categories.value.map(c => [c.id, c.name])));

/* ── 拖拽状态：章节 / 组内功能，两套互斥 ── */
const dragChapter = ref<number | null>(null);
const dragOverChapter = ref<number | null>(null);
const dragAction = ref<{ catId: number | null; index: number } | null>(null);
const dragOverAction = ref<{ catId: number | null; index: number } | null>(null);

function scopeOf(node: TreeNode): number | null {
  return node.kind === 'chapter' ? node.cat.id : null;
}

function chapterCls(chIdx: number) {
  return {
    'is-dragging': dragChapter.value === chIdx,
    'is-drop-target': dragOverChapter.value === chIdx && dragChapter.value !== null && dragChapter.value !== chIdx
  };
}

function actionCls(node: TreeNode, index: number) {
  const scope = scopeOf(node);
  const from = dragAction.value;
  const over = dragOverAction.value;
  return {
    'is-dragging': from?.catId === scope && from?.index === index,
    'is-drop-target': over?.catId === scope && over?.index === index && !(from?.catId === scope && from?.index === index)
  };
}

function onChapterDragStart(chIdx: number) {
  dragChapter.value = chIdx;
}

function onChapterDragOver(chIdx: number, e: DragEvent) {
  if (dragChapter.value === null) return;
  e.preventDefault();
  dragOverChapter.value = chIdx;
}

async function onChapterDragEnd() {
  const from = dragChapter.value;
  const to = dragOverChapter.value;
  dragChapter.value = null;
  dragOverChapter.value = null;
  if (from === null || to === null || from === to) return;
  const ids = categories.value.map(c => c.id);
  const [moved] = ids.splice(from, 1);
  ids.splice(to, 0, moved);
  const { error } = await fetchSortQuickActionCategories(ids);
  if (!error) {
    message.success('章节顺序已保存');
    await loadActions();
  }
}

function onActionDragStart(node: TreeNode, index: number) {
  dragAction.value = { catId: scopeOf(node), index };
}

function onActionDragOver(node: TreeNode, index: number, e: DragEvent) {
  const from = dragAction.value;
  if (!from || from.catId !== scopeOf(node)) return; // 只允许同组内排序（换组请在功能编辑里改类型）
  e.preventDefault();
  dragOverAction.value = { catId: scopeOf(node), index };
}

async function onActionDragEnd(node: TreeNode) {
  const from = dragAction.value;
  const to = dragOverAction.value;
  dragAction.value = null;
  dragOverAction.value = null;
  if (!from || !to || from.catId !== to.catId || from.index === to.index) return;
  const ids = node.actions.map(a => a.id);
  const [moved] = ids.splice(from.index, 1);
  ids.splice(to.index, 0, moved);
  const { error } = await fetchSortQuickActions({ categoryId: from.catId, actionIds: ids });
  if (!error) {
    message.success('排序已保存');
    await loadActions();
  }
}

/* ── 章节折叠：功能多时折起章节，拖拽更清爽 ── */
const collapsedCats = ref<Set<number>>(new Set());

function isCatCollapsed(catId: number) {
  return collapsedCats.value.has(catId);
}

function toggleCatCollapse(catId: number) {
  const next = new Set(collapsedCats.value);
  if (next.has(catId)) {
    next.delete(catId);
  } else {
    next.add(catId);
  }
  collapsedCats.value = next;
}

/* ── 类型管理（内联新增 / 双击重命名 / 启停 / 删除） ── */
const newCatVisible = ref(false);
const newCatName = ref('');

async function addCategory() {
  const name = newCatName.value.trim();
  if (!name) return;
  const { error } = await fetchCreateQuickActionCategory(name);
  if (!error) {
    message.success(`类型「${name}」已创建`);
    newCatName.value = '';
    newCatVisible.value = false;
    await loadActions();
  }
}

const renamingCatId = ref<number | null>(null);
const renameCatName = ref('');

function startRenameCat(cat: QuickActionCategory) {
  renamingCatId.value = cat.id;
  renameCatName.value = cat.name;
}

async function commitRenameCat() {
  const id = renamingCatId.value;
  renamingCatId.value = null;
  if (id === null) return;
  const name = renameCatName.value.trim();
  const cur = categories.value.find(c => c.id === id);
  if (!name || !cur || cur.name === name) return;
  const { error } = await fetchUpdateQuickActionCategory(id, { name });
  if (!error) {
    message.success('已重命名');
    await loadActions();
  }
}

async function toggleCatEnabled(cat: QuickActionCategory) {
  const { error } = await fetchUpdateQuickActionCategory(cat.id, { isEnabled: cat.isEnabled === 0 ? 1 : 0 });
  if (!error) {
    message.success(cat.isEnabled === 0 ? `类型「${cat.name}」已启用` : `类型「${cat.name}」已停用`);
    await loadActions();
  }
}

async function deleteCategory(cat: QuickActionCategory) {
  const { error } = await fetchDeleteQuickActionCategory(cat.id);
  if (!error) {
    message.success(`类型「${cat.name}」已删除，其中功能回到未分组`);
    await loadActions();
  }
}

/* ── 功能 CRUD ── */
function selectAction(action: QuickAction) {
  selectedAction.value = action;
  detailKey.value++;
}

async function loadActions() {
  loading.value = true;
  try {
    const { data, error } = await fetchManageQuickActions();
    if (!error && data) {
      actions.value = data.actions;
      categories.value = data.categories;
      groups.value = data.groups;
      if (selectedAction.value) {
        // 重新指向新数据中的对应项，保持详情页同步
        const updated = data.actions.find(a => a.id === selectedAction.value!.id);
        selectedAction.value = updated ?? null;
      } else if (data.actions.length > 0) {
        selectedAction.value = data.actions[0];
      }
    }
  } finally {
    loading.value = false;
  }
}

async function loadSessions() {
  const { data, error } = await fetchAgentSessions(1000);
  if (!error && data) sessions.value = data;
}

/* ── 职业互通：哪些职业把当前功能列为推荐（供详情页展示，点击直达职业体系） ── */
const professions = ref<Profession[]>([]);

async function loadProfessions() {
  const { data, error } = await fetchProfessions();
  if (!error && data) professions.value = data;
}

const refProfessions = computed(() =>
  selectedAction.value ? professions.value.filter(p => p.recommendedActionIds.includes(selectedAction.value!.id)) : []
);

onMounted(() => {
  loadActions();
  loadSessions();
  loadProfessions();
});

function openActionForm(mode: 'create' | 'edit', action?: QuickAction) {
  actionFormMode.value = mode;
  if (mode === 'edit' && action) {
    actionForm.value = {
      id: action.id,
      name: action.name,
      skillKey: action.skillKey || '',
      icon: action.icon || '',
      description: action.description || '',
      categoryIds: [...(action.categoryIds || [])],
      visibility: 'public'
    };
  } else {
    actionForm.value = { id: 0, name: '', skillKey: '', icon: '', description: '', categoryIds: [], visibility: 'public' };
  }
  actionFormVisible.value = true;
}

async function saveAction() {
  loading.value = true;
  try {
    if (actionFormMode.value === 'create') {
      const { error } = await fetchCreateQuickAction({
        name: actionForm.value.name,
        skillKey: actionForm.value.skillKey,
        icon: actionForm.value.icon,
        description: actionForm.value.description,
        categoryIds: actionForm.value.categoryIds,
        visibility: actionForm.value.visibility
      });
      if (!error) { message.success('创建成功'); actionFormVisible.value = false; await loadActions(); }
    } else {
      const { error } = await fetchUpdateQuickAction(actionForm.value.id, {
        name: actionForm.value.name,
        skillKey: actionForm.value.skillKey,
        icon: actionForm.value.icon,
        description: actionForm.value.description,
        categoryIds: actionForm.value.categoryIds
      });
      if (!error) { message.success('更新成功'); actionFormVisible.value = false; await loadActions(); }
    }
  } finally {
    loading.value = false;
  }
}

async function deleteAction(action: QuickAction) {
  loading.value = true;
  try {
    const { error } = await fetchDeleteQuickAction(action.id);
    if (!error) {
      message.success('已删除');
      if (selectedAction.value?.id === action.id) {
        selectedAction.value = null;
      }
      await loadActions();
    }
  } finally {
    loading.value = false;
  }
}

async function toggleActionEnabled(on: boolean) {
  if (!selectedAction.value) return;
  const { error } = await fetchUpdateQuickAction(selectedAction.value.id, { isEnabled: on ? 1 : 0 });
  if (!error) {
    message.success(on ? '已启用' : '已停用');
    await loadActions();
  }
}

/* ── 案例 CRUD ── */
function openExampleForm(action: QuickAction, mode: 'create' | 'edit' = 'create', example?: QuickActionExample) {
  exampleFormMode.value = mode;
  if (mode === 'edit' && example) {
    const images = example.previewImages?.length
      ? example.previewImages
      : example.previewImage ? [example.previewImage] : [];
    exampleForm.value = {
      id: example.id,
      actionId: example.actionId,
      sessionKey: '',
      title: example.title,
      description: example.description || '',
      previewImages: [...images],
      sortOrder: example.sortOrder
    };
    imageList.value = images.map(p => ({
      id: genImgId(),
      path: p,
      url: getImageUrl(p)
    }));
  } else {
    exampleForm.value = {
      id: 0,
      actionId: action.id,
      sessionKey: '',
      title: '',
      description: '',
      previewImages: [],
      sortOrder: action.examples.length
    };
    imageList.value = [];
  }
  exampleFormVisible.value = true;
}

async function saveExample() {
  loading.value = true;
  try {
    const previewImages = imageList.value.map(img => img.path);
    if (exampleFormMode.value === 'edit') {
      const { error } = await fetchUpdateQuickActionExample(exampleForm.value.id, {
        title: exampleForm.value.title,
        description: exampleForm.value.description,
        previewImages,
        sortOrder: exampleForm.value.sortOrder
      });
      if (!error) { message.success('更新成功'); exampleFormVisible.value = false; await loadActions(); }
    } else {
      if (!exampleForm.value.sessionKey) { message.warning('请选择会话'); return; }
      const { error } = await fetchCreateQuickActionExampleFromSession(exampleForm.value.actionId, {
        sessionKey: exampleForm.value.sessionKey,
        title: exampleForm.value.title,
        description: exampleForm.value.description,
        previewImages,
        sortOrder: exampleForm.value.sortOrder
      });
      if (!error) { message.success('案例已添加'); exampleFormVisible.value = false; await loadActions(); }
    }
  } finally {
    loading.value = false;
  }
}

async function deleteExample(example: QuickActionExample) {
  loading.value = true;
  try {
    const { error } = await fetchDeleteQuickActionExample(example.id);
    if (!error) { message.success('已删除'); await loadActions(); }
  } finally {
    loading.value = false;
  }
}

function fmtDate(ts: number): string {
  const d = new Date(ts);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${mm}-${dd} ${hh}:${mi}`;
}

const sessionOptions = computed(() =>
  [...sessions.value]
    .sort((a, b) => b.createdAt - a.createdAt)
    .map(s => ({
      label: `${fmtDate(s.createdAt)}  ${s.title || '无标题'}（${s.messageCount} 条消息）`,
      value: s.sessionKey
    }))
);

const totalExamples = computed(() => actions.value.reduce((sum, a) => sum + a.examples.length, 0));

/* 类型候选项：来自类型表（左侧栏管理），停用的类型带标记但仍可勾选 */
const categoryOptions = computed(() =>
  categories.value.map(c => ({
    label: c.isEnabled === 0 ? `${c.name}（已停用）` : c.name,
    value: c.id
  }))
);

/* 拼接后端 origin，确保图片请求打到后端而非前端端口 */
function getImageUrl(path?: string): string {
  if (!path) return '';
  const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
  const { baseURL } = getServiceBaseURL(import.meta.env, isHttpProxy);
  // baseURL 形如 "http://localhost:9999/api/v1" 或 "/api/v1" 或 "/proxy-default"
  // 提取 origin 部分（scheme + host + port），生产环境同源则返回空串
  const origin = /^https?:\/\/[^/]+/.exec(baseURL)?.[0] ?? '';
  return origin + path;
}

/* 图片上传（多图） */
async function handleImageUpload(options: UploadCustomRequestOptions) {
  const { file, onFinish, onError } = options;
  uploadingImage.value = true;
  try {
    const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
    const { baseURL } = getServiceBaseURL(import.meta.env, isHttpProxy);
    const token = await ensureFreshAccessToken();

    const formData = new FormData();
    formData.append('file', file.file as File);

    const response = await fetch(`${baseURL}/ai/upload/quick-action-image`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`
      },
      body: formData
    });

    if (!response.ok) {
      throw new Error(`上传失败: ${response.status}`);
    }

    const result = await response.json();
    if (result.code === '0000' && result.data) {
      const path = result.data.path;
      imageList.value.push({
        id: genImgId(),
        path,
        url: getImageUrl(path)
      });
      message.success('图片上传成功');
      onFinish();
    } else {
      throw new Error(result.msg || '上传失败');
    }
  } catch (error: any) {
    message.error(error.message || '图片上传失败');
    onError();
  } finally {
    uploadingImage.value = false;
  }
}

function beforeImageUpload(options: { file: UploadFileInfo }) {
  const file = options.file.file as File;
  const isImage = file.type.startsWith('image/');
  if (!isImage) {
    message.error('只能上传图片文件');
    return false;
  }
  const isLt100M = file.size / 1024 / 1024 < 100;
  if (!isLt100M) {
    message.error('图片大小不能超过 100MB');
    return false;
  }
  return true;
}

/* ── 剪贴板粘贴上传（粘贴卡片模式） ── */
const pasteZoneRef = ref<HTMLElement | null>(null);
const pasteReady = ref(false);

function activatePasteZone() {
  pasteReady.value = true;
  nextTick(() => pasteZoneRef.value?.focus());
}

function deactivatePasteZone() {
  pasteReady.value = false;
}

/* 提取上传逻辑 */
async function uploadImageBlob(blob: File): Promise<boolean> {
  if (blob.size / 1024 / 1024 >= 100) {
    message.error('粘贴的图片超过 100MB');
    return false;
  }
  uploadingImage.value = true;
  try {
    const isHttpProxy = import.meta.env.DEV && import.meta.env.VITE_HTTP_PROXY === 'Y';
    const { baseURL } = getServiceBaseURL(import.meta.env, isHttpProxy);
    const token = await ensureFreshAccessToken();
    const formData = new FormData();
    formData.append('file', blob);
    const response = await fetch(`${baseURL}/ai/upload/quick-action-image`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: formData
    });
    if (!response.ok) throw new Error(`上传失败: ${response.status}`);
    const result = await response.json();
    if (result.code === '0000' && result.data) {
      imageList.value.push({ id: genImgId(), path: result.data.path, url: getImageUrl(result.data.path) });
      message.success('图片已粘贴');
      return true;
    }
    throw new Error(result.msg || '上传失败');
  } catch (err: any) {
    message.error(err.message || '粘贴图片上传失败');
    return false;
  } finally {
    uploadingImage.value = false;
  }
}

/* 从 ClipboardEvent 中提取图片并上传，返回是否处理了图片 */
async function handleImagePaste(e: ClipboardEvent): Promise<boolean> {
  const items = e.clipboardData?.items;
  if (!items) return false;
  const imageItems: DataTransferItem[] = [];
  for (let i = 0; i < items.length; i++) {
    if (items[i].type.startsWith('image/')) imageItems.push(items[i]);
  }
  if (imageItems.length === 0) return false;
  e.preventDefault();
  let anySuccess = false;
  for (const item of imageItems) {
    const blob = item.getAsFile();
    if (blob && await uploadImageBlob(blob)) anySuccess = true;
  }
  return anySuccess;
}

/* 粘贴区焦点触发 */
function handlePasteZonePaste(e: ClipboardEvent) {
  e.stopPropagation(); // 防止事件冒泡到 document 导致全局监听重复触发
  handleImagePaste(e).then(ok => { if (ok) pasteReady.value = false; });
}

/* 全局 Ctrl+V（弹窗打开时生效，不干扰输入框） */
function handleGlobalPaste(e: ClipboardEvent) {
  if (pasteReady.value) return; // 粘贴区已激活，由粘贴区处理器接管
  const target = e.target as HTMLElement;
  if (target?.tagName === 'INPUT' || target?.tagName === 'TEXTAREA' || target?.isContentEditable) return;
  handleImagePaste(e);
}

watch(
  () => exampleFormVisible.value,
  (visible) => {
    if (visible) {
      document.addEventListener('paste', handleGlobalPaste);
    } else {
      document.removeEventListener('paste', handleGlobalPaste);
      pasteReady.value = false;
    }
  }
);

onBeforeUnmount(() => {
  document.removeEventListener('paste', handleGlobalPaste);
});

</script>

<template>
  <div class="qam-root">
    <!-- ========== 左侧栏 ========== -->
    <aside class="qam-sidebar">
      <header class="qam-sidebar-head">
        <div class="qam-head-left">
          <div class="brand-mark">
            <span class="bm-glyph">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </span>
            <span class="bm-halo" />
          </div>
          <div class="brand-text">
            <span class="brand-zh">功能库</span>
            <span class="brand-en">ACTION LIBRARY</span>
          </div>
        </div>
        <div class="qam-head-ops">
          <button class="glass-btn glass-btn--icon" title="新建类型（橱窗章节）" @click="newCatVisible = !newCatVisible">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
              <path d="M2.5 4.5h11M2.5 8h7M2.5 11.5h9" stroke-linecap="round" />
              <path d="M12.5 9.5v4M10.5 11.5h4" stroke-linecap="round" />
            </svg>
          </button>
          <button class="glass-btn glass-btn--icon" title="新建快捷功能" @click="openActionForm('create')">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M8 3v10M3 8h10" stroke-linecap="round" />
            </svg>
          </button>
        </div>
      </header>

      <!-- 统计摘要 -->
      <div v-if="actions.length > 0 || categories.length > 0" class="qam-stats-bar">
        <div class="qam-stat">
          <span class="qam-stat-val">{{ actions.length }}</span>
          <span class="qam-stat-label">功能</span>
        </div>
        <div class="qam-stat-divider" />
        <div class="qam-stat">
          <span class="qam-stat-val">{{ categories.length }}</span>
          <span class="qam-stat-label">类型</span>
        </div>
        <div class="qam-stat-divider" />
        <div class="qam-stat">
          <span class="qam-stat-val">{{ totalExamples }}</span>
          <span class="qam-stat-label">案例</span>
        </div>
      </div>

      <!-- 内联新建类型 -->
      <div v-if="newCatVisible" class="qam-new-cat">
        <NInput
          v-model:value="newCatName"
          size="small"
          autofocus
          placeholder="新类型名，如：写标准"
          @keyup.enter="addCategory"
          @blur="newCatVisible = false"
        />
      </div>

      <NSpin v-if="loading && actions.length === 0 && categories.length === 0" size="medium" class="qam-sidebar-spin" />

      <!-- 两级树：类型章节 + 组内功能 + 未分组（所见即用户页橱窗） -->
      <nav v-else-if="actions.length > 0 || categories.length > 0" class="qam-sidebar-nav">
        <template v-for="node in tree" :key="node.kind === 'chapter' ? `c-${node.cat.id}` : 'ungrouped'">
          <!-- ── 类型章节 ── -->
          <section v-if="node.kind === 'chapter'" class="qam-chapter" :class="[chapterCls(node.chIdx), { 'is-collapsed': isCatCollapsed(node.cat.id) }]">
            <div
              class="qam-chapter-head"
              :class="{ 'is-off': node.cat.isEnabled === 0 }"
              draggable="true"
              title="拖拽调整章节顺序"
              @dragstart="onChapterDragStart(node.chIdx)"
              @dragover="onChapterDragOver(node.chIdx, $event)"
              @dragend="onChapterDragEnd"
            >
              <span class="qam-drag-handle" aria-hidden="true">
                <svg width="9" height="13" viewBox="0 0 10 14" fill="currentColor">
                  <circle cx="3" cy="2" r="1.2" /><circle cx="7" cy="2" r="1.2" />
                  <circle cx="3" cy="7" r="1.2" /><circle cx="7" cy="7" r="1.2" />
                  <circle cx="3" cy="12" r="1.2" /><circle cx="7" cy="12" r="1.2" />
                </svg>
              </span>
              <NInput
                v-if="renamingCatId === node.cat.id"
                v-model:value="renameCatName"
                size="tiny"
                autofocus
                class="qam-cat-rename"
                @keyup.enter="commitRenameCat"
                @blur="commitRenameCat"
                @click.stop
              />
              <span
                v-else
                class="qam-chapter-name"
                title="双击重命名"
                @dblclick.stop="startRenameCat(node.cat)"
              >{{ node.cat.name }}</span>
              <span class="qam-chapter-count">{{ node.actions.length }}</span>
              <span class="qam-chapter-ops" @click.stop>
                <button
                  class="qam-cat-op"
                  :class="{ 'is-on': node.cat.isEnabled !== 0 }"
                  :title="node.cat.isEnabled === 0 ? '启用该类型（用户页可见）' : '停用该类型（用户页隐藏）'"
                  @click="toggleCatEnabled(node.cat)"
                >
                  <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
                    <circle cx="8" cy="8" r="5.5" />
                    <circle v-if="node.cat.isEnabled !== 0" cx="8" cy="8" r="2.4" fill="currentColor" stroke="none" />
                  </svg>
                </button>
                <NPopconfirm @positive-click="deleteCategory(node.cat)">
                  <template #trigger>
                    <button class="qam-cat-op qam-cat-op--danger" title="删除类型（功能保留，回到未分组）">
                      <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">
                        <path d="M3 4h10M5.5 4V3a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1M6 7v5M10 7v5M4 4l.5 9a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1l.5-9" stroke-linecap="round" stroke-linejoin="round" />
                      </svg>
                    </button>
                  </template>
                  删除类型「{{ node.cat.name }}」？其中的功能会保留并回到未分组。
                </NPopconfirm>
              </span>
              <button
                class="qam-collapse-btn"
                :title="isCatCollapsed(node.cat.id) ? `展开「${node.cat.name}」` : `折叠「${node.cat.name}」`"
                @click.stop="toggleCatCollapse(node.cat.id)"
              >
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
                  <path d="M4 6l4 4 4-4" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </button>
            </div>
            <div class="qam-chapter-fold">
              <div class="qam-chapter-fold-inner">
                <div class="qam-chapter-body">
                  <button
                    v-for="(action, ai) in node.actions"
                    :key="action.id"
                    class="qam-nav-item qam-nav-item--sub"
                    :class="[actionCls(node, ai), { 'is-active': selectedAction?.id === action.id, 'is-off': action.isEnabled === 0 }]"
                    draggable="true"
                    @dragstart="onActionDragStart(node, ai)"
                    @dragover="onActionDragOver(node, ai, $event)"
                    @dragend="onActionDragEnd(node)"
                    @click="selectAction(action)"
                  >
                    <div class="qam-nav-tile">
                      <SvgIcon :icon="action.icon || 'mdi:lightning-bolt'" class="qam-nav-icon" />
                    </div>
                    <div class="qam-nav-info">
                      <div class="qam-nav-name">
                        {{ action.name }}
                        <span v-if="action.isEnabled === 0" class="qam-nav-off">停</span>
                      </div>
                      <div class="qam-nav-meta">
                        {{ action.examples.length }} 个案例
                        <span v-if="action.skillKey" class="qam-nav-skill">{{ action.skillKey }}</span>
                      </div>
                    </div>
                    <svg class="qam-nav-arrow" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                      <path d="M6 4l4 4-4 4" stroke-linecap="round" stroke-linejoin="round" />
                    </svg>
                  </button>
                  <div v-if="node.actions.length === 0" class="qam-chapter-empty">
                    暂无功能 —— 在功能编辑里勾选此类型
                  </div>
                </div>
              </div>
            </div>
          </section>

          <!-- ── 未分组 ── -->
          <section v-else class="qam-chapter qam-chapter--plain">
            <div class="qam-chapter-head qam-chapter-head--plain">
              <span class="qam-chapter-name">未分组</span>
              <span class="qam-chapter-count">{{ node.actions.length }}</span>
            </div>
            <div class="qam-chapter-body">
              <button
                v-for="(action, ai) in node.actions"
                :key="action.id"
                class="qam-nav-item qam-nav-item--sub"
                :class="[actionCls(node, ai), { 'is-active': selectedAction?.id === action.id, 'is-off': action.isEnabled === 0 }]"
                draggable="true"
                @dragstart="onActionDragStart(node, ai)"
                @dragover="onActionDragOver(node, ai, $event)"
                @dragend="onActionDragEnd(node)"
                @click="selectAction(action)"
              >
                <div class="qam-nav-tile">
                  <SvgIcon :icon="action.icon || 'mdi:lightning-bolt'" class="qam-nav-icon" />
                </div>
                <div class="qam-nav-info">
                  <div class="qam-nav-name">
                    {{ action.name }}
                    <span v-if="action.isEnabled === 0" class="qam-nav-off">停</span>
                  </div>
                  <div class="qam-nav-meta">
                    {{ action.examples.length }} 个案例
                    <span v-if="action.skillKey" class="qam-nav-skill">{{ action.skillKey }}</span>
                  </div>
                </div>
                <svg class="qam-nav-arrow" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                  <path d="M6 4l4 4-4 4" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </button>
            </div>
          </section>
        </template>
      </nav>

      <div v-else class="qam-sidebar-empty">
        <div class="empty-orb" />
        <p class="qam-empty-title">还没有快捷功能</p>
        <button class="glass-btn glass-btn--primary" @click="openActionForm('create')">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M8 3v10M3 8h10" stroke-linecap="round" />
          </svg>
          <span>创建第一个</span>
        </button>
      </div>
    </aside>

    <!-- ========== 主内容区 ========== -->
    <main class="qam-main">
      <div v-if="!selectedAction" class="qam-placeholder">
        <div class="placeholder-orb" />
        <p class="placeholder-title">从左侧选择功能以查看详情</p>
        <p class="placeholder-hint">拖拽左侧树可调整章节与组内顺序 · 章节右侧箭头可折叠</p>
      </div>

      <Transition name="qam-fade" mode="out-in">
        <div v-if="selectedAction" :key="detailKey" class="qam-detail">
          <!-- 详情头部 -->
          <div class="qam-detail-head" @click="openActionForm('edit', selectedAction!)">
            <div class="qam-detail-tile">
              <SvgIcon :icon="selectedAction.icon || 'mdi:lightning-bolt'" class="qam-detail-icon" />
            </div>
            <div class="qam-detail-info">
              <h1 class="qam-detail-title">{{ selectedAction.name }}</h1>
              <p v-if="selectedAction.description" class="qam-detail-desc">{{ selectedAction.description }}</p>
              <div class="qam-detail-meta">
                <span v-if="selectedAction.skillKey" class="chip chip--accent">{{ selectedAction.skillKey }}</span>
                <span v-for="cid in (selectedAction.categoryIds || [])" :key="cid" class="chip chip--cat">
                  {{ catNameMap.get(cid) || `#${cid}` }}
                </span>
                <span v-if="selectedAction.categoryIds?.length === 0" class="chip">未分组</span>
                <span class="chip">{{ selectedAction.examples.length }} 个案例</span>
                <span
                  v-for="prof in refProfessions"
                  :key="`prof-${prof.id}`"
                  class="chip chip--prof"
                  :title="`职业「${prof.name}」向新用户推荐此功能 · 点击进入职业体系`"
                  @click.stop="emit('openProfession', prof.id)"
                >
                  <SvgIcon :icon="prof.icon || 'mdi:account-outline'" class="chip-prof-icon" />
                  {{ prof.name }}
                </span>
                <button
                  v-if="refProfessions.length === 0"
                  type="button"
                  class="chip chip--prof-add"
                  title="前往职业体系，把此功能设为某职业的推荐"
                  @click.stop="emit('gotoProfessions')"
                >
                  + 职业推荐
                </button>
              </div>
            </div>
            <div class="qam-detail-ops" @click.stop>
              <button
                class="glass-btn glass-btn--icon qam-power"
                :class="{ 'is-off': selectedAction.isEnabled === 0 }"
                :title="selectedAction.isEnabled === 0 ? '已停用 · 点击启用（用户页恢复展示）' : '停用该功能（用户页隐藏，数据保留）'"
                @click="toggleActionEnabled(selectedAction.isEnabled === 0)"
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">
                  <path d="M8 2v5.5" stroke-linecap="round" />
                  <path d="M4.4 4.3a5.5 5.5 0 1 0 7.2 0" stroke-linecap="round" />
                </svg>
              </button>
              <NPopconfirm @positive-click="deleteAction(selectedAction!)">
                <template #trigger>
                  <button class="glass-btn glass-btn--icon glass-btn--danger" title="删除功能">
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                      <path d="M3 4h10M5.5 4V3a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1M6 7v5M10 7v5M4 4l.5 9a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1l.5-9" stroke-linecap="round" stroke-linejoin="round" />
                    </svg>
                  </button>
                </template>
                确定删除「{{ selectedAction.name }}」？关联案例也会被删除。
              </NPopconfirm>
            </div>
          </div>

          <!-- 案例列表 -->
          <section class="qam-examples">
            <div class="qam-examples-head">
              <h3 class="qam-examples-title">案例</h3>
              <button class="glass-btn glass-btn--sm" @click="openExampleForm(selectedAction!)">
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M8 3v10M3 8h10" stroke-linecap="round" />
                </svg>
                <span>添加案例</span>
              </button>
            </div>

            <div v-if="selectedAction.examples.length === 0" class="qam-examples-empty">
              <div class="empty-orb empty-orb--sm" />
              <p class="empty-title">暂无案例</p>
              <p class="empty-hint">从已有会话中挑选优质对话，添加为参考案例</p>
            </div>

            <div v-else class="qam-examples-list">
              <article
                v-for="ex in selectedAction.examples"
                :key="ex.id"
                class="qam-ex-card"
                @click="openExampleForm(selectedAction!, 'edit', ex)"
              >
                <div v-if="ex.previewImages?.length || ex.previewImage" class="qam-ex-preview">
                  <NImageGroup :preview-src="(ex.previewImages || [ex.previewImage!]).map(p => getImageUrl(p))">
                    <NImage
                      :src="getImageUrl(ex.previewImages?.[0] || ex.previewImage)"
                      width="126"
                      height="94"
                      object-fit="cover"
                      lazy
                      @click.stop
                    />
                  </NImageGroup>
                  <span v-if="(ex.previewImages?.length || 0) > 1" class="qam-ex-img-count">
                    {{ ex.previewImages!.length }} 图
                  </span>
                </div>
                <div class="qam-ex-body">
                  <div class="qam-ex-top">
                    <h4 class="qam-ex-title">{{ ex.title }}</h4>
                    <NPopconfirm @positive-click="deleteExample(ex)">
                      <template #trigger>
                        <button class="glass-btn glass-btn--icon glass-btn--xs glass-btn--danger" title="删除" @click.stop>
                          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                            <path d="M3 4h10M5.5 4V3a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1M6 7v5M10 7v5M4 4l.5 9a1 1 0 0 0 1 1h5a1 1 0 0 0 1-1l.5-9" stroke-linecap="round" stroke-linejoin="round" />
                          </svg>
                        </button>
                      </template>
                      确定删除案例「{{ ex.title }}」？
                    </NPopconfirm>
                  </div>
                  <p v-if="ex.description" class="qam-ex-desc">{{ ex.description }}</p>
                  <div class="qam-ex-footer">
                    <span class="qam-ex-count">
                      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                        <path d="M2 4h12M2 8h8M2 12h10" stroke-linecap="round" />
                      </svg>
                      {{ ex.conversationData.length }} 条对话
                    </span>
                    <span v-if="ex.previewImages?.length || ex.previewImage" class="chip chip--accent chip--sm">
                      <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5">
                        <rect x="2" y="2" width="12" height="12" rx="2" />
                        <circle cx="6" cy="6" r="1.5" />
                        <path d="M14 11l-3.5-3.5L4 14" stroke-linecap="round" stroke-linejoin="round" />
                      </svg>
                      {{ (ex.previewImages?.length || 0) > 1 ? `${ex.previewImages!.length} 张图片` : '预览图' }}
                    </span>
                  </div>
                </div>
              </article>
            </div>
          </section>
        </div>
      </Transition>
    </main>

    <!-- ========== 新建/编辑功能弹窗 ========== -->
    <NModal
      v-model:show="actionFormVisible"
      preset="card"
      :title="actionFormMode === 'create' ? '新建快捷功能' : '编辑快捷功能'"
      style="max-width: 480px"
      :mask-closable="true"
    >
      <NSpace vertical :size="16">
        <div class="qam-field">
          <label class="qam-label">名称 <em>*</em></label>
          <NInput v-model:value="actionForm.name" placeholder="例：流程图生成" />
        </div>
        <div class="qam-field">
          <label class="qam-label">所属类型</label>
          <NSelect
            v-model:value="actionForm.categoryIds"
            multiple
            clearable
            :options="categoryOptions"
            placeholder="选择展示类型（可多选）"
          />
          <span class="qam-hint">类型即用户页橱窗的章节；新增 / 重命名 / 排序类型请用左侧栏（拖章节标题排序，双击重命名）。未选类型的功能归入「未分组」</span>
        </div>
        <div class="qam-field">
          <label class="qam-label">图标</label>
          <div class="qam-icon-input">
            <NInput v-model:value="actionForm.icon" placeholder="iconify 图标名，例：mdi:chart-bar" />
            <div v-if="actionForm.icon" class="qam-icon-preview">
              <SvgIcon :icon="actionForm.icon" class="qam-icon-preview-icon" />
            </div>
          </div>
          <span class="qam-hint">
            前往 <a href="https://icones.js.org" target="_blank" rel="noopener noreferrer" class="qam-icon-link">icones.js.org</a>
            搜索图标，点击复制后粘贴到上方输入框。格式为 <code class="qam-code">集合:图标名</code>，例如 <code class="qam-code">mdi:chart-bar</code>、<code class="qam-code">carbon:machine</code>、<code class="qam-code">lucide:bot</code>
          </span>
        </div>
        <div class="qam-field">
          <label class="qam-label">关联技能 Key</label>
          <NInput v-model:value="actionForm.skillKey" placeholder="例：流程图生成" />
        </div>
        <div class="qam-field">
          <label class="qam-label">描述</label>
          <NInput v-model:value="actionForm.description" type="textarea" :rows="3" placeholder="简要说明功能用途" />
        </div>
      </NSpace>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="actionFormVisible = false">取消</NButton>
          <NButton type="primary" :disabled="!actionForm.name || loading" :loading="loading" @click="saveAction">
            保存
          </NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- ========== 添加案例弹窗 ========== -->
    <NModal
      v-model:show="exampleFormVisible"
      preset="card"
      :title="exampleFormMode === 'create' ? '添加案例' : '编辑案例'"
      style="max-width: 480px"
      :mask-closable="true"
    >
      <NSpace vertical :size="16">
        <div v-if="exampleFormMode === 'create'" class="qam-field">
          <label class="qam-label">选择会话 <em>*</em></label>
          <NSelect
            v-model:value="exampleForm.sessionKey"
            :options="sessionOptions"
            placeholder="请选择会话"
            filterable
            clearable
          />
          <span class="qam-hint">支持搜索，按时间倒序排列</span>
        </div>
        <div class="qam-field">
          <label class="qam-label">标题</label>
          <NInput v-model:value="exampleForm.title" :placeholder="exampleFormMode === 'create' ? '留空则使用会话标题' : '案例标题'" />
        </div>
        <div class="qam-field">
          <label class="qam-label">描述</label>
          <NInput v-model:value="exampleForm.description" type="textarea" :rows="3" placeholder="简要说明使用场景" />
        </div>
        <div class="qam-field">
          <label class="qam-label">预览图片</label>

          <!-- ===== 空态：大尺寸主上传区域 ===== -->
          <div v-if="imageList.length === 0" class="qam-img-hero">
            <NUpload
              :show-file-list="false"
              :custom-request="handleImageUpload"
              class="qam-img-hero-upload"
              @before-upload="beforeImageUpload"
            >
              <div class="qam-img-hero-trigger" :class="{ 'is-uploading': uploadingImage }">
                <svg v-if="!uploadingImage" class="qam-hero-icon" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="17 8 12 3 7 8" />
                  <line x1="12" y1="3" x2="12" y2="15" />
                </svg>
                <span v-else class="qam-img-spinner qam-hero-spinner" />
                <span class="qam-hero-text">{{ uploadingImage ? '上传中…' : '点击上传图片' }}</span>
                <span class="qam-hero-sub">JPG / PNG / GIF，单张不超过 100MB</span>
              </div>
            </NUpload>
            <div class="qam-img-hero-divider">
              <span class="qam-hero-line" />
              <span class="qam-hero-or">或</span>
              <span class="qam-hero-line" />
            </div>
            <div
              ref="pasteZoneRef"
              class="qam-img-hero-paste"
              :class="{ 'is-active': pasteReady }"
              tabindex="0"
              @click.stop="activatePasteZone"
              @paste="handlePasteZonePaste"
              @blur="deactivatePasteZone"
            >
              <template v-if="!pasteReady">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="8" y="2" width="8" height="4" rx="1" />
                  <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
                </svg>
                <span>从剪贴板粘贴</span>
                <kbd>Ctrl+V</kbd>
              </template>
              <template v-else>
                <span class="qam-paste-pulse" />
                <span>等待粘贴…</span>
                <kbd>Ctrl+V</kbd>
              </template>
            </div>
          </div>

          <!-- ===== 有图片态：缩略图网格 + 紧凑添加按钮 ===== -->
          <div v-else class="qam-img-grid">
            <NImageGroup>
              <!-- 隐藏触发器：zoom 按钮通过它打开预览 -->
              <NImage
                ref="previewTriggerRef"
                :src="imageList[previewIndex]?.url || ''"
                width="0"
                height="0"
                class="qam-preview-trigger"
              />
              <div
                v-for="(img, idx) in imageList"
                :key="img.id"
                class="qam-img-card"
                :class="{
                  'is-dragging': dragItemIndex === idx,
                  'is-drop-target': dragOverIndex === idx
                }"
                draggable="true"
                @dragstart="onDragStart(idx)"
                @dragover="onDragOver(idx, $event)"
                @dragend="onDragEnd"
                @dragleave="dragOverIndex = null"
              >
                <NImage
                  :src="img.url"
                  width="86"
                  height="86"
                  object-fit="cover"
                  preview-disabled
                  lazy
                  class="qam-img-nimage"
                />
                <div class="qam-img-overlay">
                  <div class="qam-img-bar">
                    <span class="qam-img-index">{{ idx + 1 }}</span>
                    <div class="qam-img-actions">
                      <button class="qam-img-action-btn" title="放大查看" @click.stop="triggerPreview(idx)">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                          <circle cx="11" cy="11" r="7" /><path d="M21 21l-4.35-4.35" />
                          <path d="M11 8v6M8 11h6" />
                        </svg>
                      </button>
                      <button class="qam-img-action-btn qam-img-action-btn--danger" title="移除" @click.stop="removeImage(idx)">
                        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
                          <path d="M4 4l8 8M12 4l-8 8" stroke-linecap="round" />
                        </svg>
                      </button>
                    </div>
                  </div>
                </div>
                <div class="qam-img-drag-handle" title="拖拽排序">
                  <svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor" opacity="0.5">
                    <circle cx="3" cy="2" r="1.2" /><circle cx="7" cy="2" r="1.2" />
                    <circle cx="3" cy="7" r="1.2" /><circle cx="7" cy="7" r="1.2" />
                    <circle cx="3" cy="12" r="1.2" /><circle cx="7" cy="12" r="1.2" />
                  </svg>
                </div>
              </div>
              <!-- 紧凑添加按钮（上传 + 粘贴合一） -->
              <div class="qam-img-add-wrap">
                <NUpload
                  :show-file-list="false"
                  :custom-request="handleImageUpload"
                  @before-upload="beforeImageUpload"
                >
                  <div class="qam-img-add" :class="{ 'is-uploading': uploadingImage, 'is-paste-mode': pasteReady }">
                    <template v-if="uploadingImage">
                      <span class="qam-img-spinner" />
                    </template>
                    <template v-else-if="pasteReady">
                      <span class="qam-paste-pulse" />
                      <span class="qam-add-paste-hint">Ctrl+V</span>
                    </template>
                    <template v-else>
                      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M12 5v14M5 12h14" stroke-linecap="round" />
                      </svg>
                    </template>
                  </div>
                </NUpload>
                <button
                  ref="pasteZoneRef"
                  class="qam-paste-btn"
                  :class="{ 'is-active': pasteReady }"
                  tabindex="0"
                  title="点击进入粘贴模式"
                  @click.stop="activatePasteZone"
                  @paste="handlePasteZonePaste"
                  @blur="deactivatePasteZone"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="8" y="2" width="8" height="4" rx="1" />
                    <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
                  </svg>
                </button>
              </div>
            </NImageGroup>
          </div>

          <span class="qam-hint">支持多张图片，拖拽可调整顺序（最大 100MB/张，JPG/PNG/GIF）</span>
        </div>
        <div v-if="exampleFormMode === 'edit'" class="qam-field">
          <label class="qam-label">排序</label>
          <NInputNumber v-model:value="exampleForm.sortOrder" :min="0" class="w-full" />
        </div>
      </NSpace>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="exampleFormVisible = false">取消</NButton>
          <NButton
            type="primary"
            :disabled="(exampleFormMode === 'create' && !exampleForm.sessionKey) || loading"
            :loading="loading"
            @click="saveExample"
          >
            {{ exampleFormMode === 'create' ? '添加' : '保存' }}
          </NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
/* ─── design tokens（蓝青色系 · 玻璃拟态） ─── */
.qam-root {
  --bg: #f5f7fb;
  --bg-deep: #eaf0f9;

  /* 玻璃质感 */
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

/* ─── glass button 系统 ─── */
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
.glass-btn--xs {
  width: 26px; height: 26px;
  border-radius: 8px;
}
.glass-btn--sm {
  height: 32px;
  padding: 0 12px;
  border-radius: 10px;
  font-size: 11.5px;
}
.glass-btn--primary {
  background: var(--aurora);
  color: #fff;
  border-color: transparent;
  padding: 7px 16px;
  height: 36px;
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
.glass-btn--danger:hover {
  color: #ef4444;
  border-color: rgba(239, 68, 68, 0.3);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    0 8px 32px -10px rgba(239, 68, 68, 0.35);
}

/* 详情头的功能启停按钮：与玻璃按钮同族，停用态灰显、hover 给启用暗示（薄荷绿） */
.qam-power {
  color: var(--c-blue);
  border-color: rgba(30, 64, 175, 0.22);
}
.qam-power.is-off {
  color: var(--ink-faint);
  border-color: rgba(148, 163, 184, 0.4);
  background: rgba(148, 163, 184, 0.08);
}
.qam-power.is-off:hover {
  color: var(--c-mint);
  border-color: rgba(16, 185, 129, 0.4);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    0 8px 32px -10px rgba(16, 185, 129, 0.35);
}

/* ─── chip / badge ─── */
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  background: var(--surface-deep);
  color: var(--ink-mute);
  font-size: 11.5px;
  font-weight: 600;
  border-radius: 999px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  letter-spacing: 0.01em;
  line-height: 1.5;
}
.chip--accent {
  color: #fff;
  border-color: transparent;
  background: var(--c-blue);
}
.chip--cat {
  color: var(--c-blue);
  background: rgba(30, 64, 175, 0.08);
  border-color: rgba(30, 64, 175, 0.16);
}
.chip--sm {
  font-size: 10.5px;
  padding: 2px 8px;
}

/* ─── shared orb (empty / placeholder) ─── */
.empty-orb {
  width: 80px; height: 80px;
  border-radius: 50%;
  background: var(--aurora);
  position: relative;
  animation: orb-breathe 3s ease-in-out infinite;
  flex-shrink: 0;
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
.empty-orb--sm {
  width: 56px; height: 56px;
  margin-bottom: 8px;
}

@keyframes orb-breathe {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.08); }
}

/* ─── Sidebar ─── */
.qam-sidebar {
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

.qam-sidebar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 16px 14px;
  border-bottom: 1px solid var(--border);
}

.qam-head-left {
  display: flex;
  align-items: center;
  gap: 11px;
}

.qam-head-ops {
  display: flex;
  align-items: center;
  gap: 6px;
}

/* brand mark — 与 nian 一致 */
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
  animation: halo-breathe 3s ease-in-out infinite;
}

@keyframes halo-breathe {
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

/* ─── Stats Bar ─── */
.qam-stats-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 11px 20px;
  border-bottom: 1px solid var(--border);
}

.qam-stat {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.qam-stat-val {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}

.qam-stat-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ink-faint);
}

.qam-stat-divider {
  width: 1px;
  height: 16px;
  background: var(--border);
}

/* ─── 内联新建类型 ─── */
.qam-new-cat {
  padding: 10px 14px 2px;
}

/* ─── Loading ─── */
.qam-sidebar-spin {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 0;
}

/* ─── Navigation（两级树） ─── */
.qam-sidebar-nav {
  flex: 1;
  overflow-y: auto;
  padding: 10px 10px 14px;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}

/* 章节（类型） */
.qam-chapter {
  margin-bottom: 12px;
  border-radius: 12px;
  transition: box-shadow 0.15s ease;
}
.qam-chapter:last-child {
  margin-bottom: 0;
}
.qam-chapter.is-drop-target {
  box-shadow: 0 -2px 0 0 var(--c-blue-2);
}
.qam-chapter.is-dragging {
  opacity: 0.45;
}

.qam-chapter-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 8px 7px 6px;
  border-radius: 10px;
  cursor: grab;
  user-select: none;
  transition: background 0.15s ease;
}
.qam-chapter-head:hover {
  background: rgba(255, 255, 255, 0.55);
}
.qam-chapter-head:active {
  cursor: grabbing;
}
.qam-chapter-head--plain {
  cursor: default;
}
.qam-chapter-head--plain:hover {
  background: transparent;
}

.qam-drag-handle {
  display: flex;
  align-items: center;
  color: var(--ink-faint);
  opacity: 0.5;
  flex-shrink: 0;
  transition: opacity 0.15s ease;
}
.qam-chapter-head:hover .qam-drag-handle {
  opacity: 1;
}

.qam-chapter-name {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--ink-mute);
  text-transform: uppercase;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.qam-chapter-head.is-off .qam-chapter-name {
  color: var(--ink-faint);
  text-decoration: line-through;
  text-decoration-color: rgba(148, 163, 184, 0.6);
}

.qam-cat-rename {
  flex: 1;
  min-width: 0;
}

.qam-chapter-count {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10px;
  font-weight: 700;
  color: var(--ink-faint);
  background: rgba(30, 64, 175, 0.07);
  border-radius: 999px;
  padding: 1px 7px;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.qam-chapter-ops {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  opacity: 0;
  transition: opacity 0.15s ease;
  flex-shrink: 0;
}
.qam-chapter-head:hover .qam-chapter-ops {
  opacity: 1;
}

.qam-collapse-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px; height: 20px;
  border: none;
  background: transparent;
  border-radius: 6px;
  color: var(--ink-faint);
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s ease, color 0.15s ease;
}
.qam-collapse-btn:hover {
  background: rgba(30, 64, 175, 0.1);
  color: var(--c-blue);
}
.qam-collapse-btn svg {
  transition: transform 0.28s var(--ease);
}
.qam-chapter.is-collapsed .qam-collapse-btn svg {
  transform: rotate(-90deg);
}

.qam-chapter-fold {
  display: grid;
  grid-template-rows: 1fr;
  transition: grid-template-rows 0.3s var(--ease), opacity 0.22s ease;
}
.qam-chapter.is-collapsed .qam-chapter-fold {
  grid-template-rows: 0fr;
  opacity: 0;
}
.qam-chapter-fold-inner {
  overflow: hidden;
  min-height: 0;
}

.qam-cat-op {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px; height: 22px;
  border: none;
  background: transparent;
  border-radius: 7px;
  color: var(--ink-faint);
  cursor: pointer;
  transition: all 0.15s ease;
}
.qam-cat-op:hover {
  background: rgba(30, 64, 175, 0.1);
  color: var(--c-blue);
}
.qam-cat-op.is-on {
  color: var(--c-mint);
}
.qam-cat-op--danger:hover {
  background: rgba(239, 68, 68, 0.1);
  color: #ef4444;
}

.qam-chapter-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 2px 0 2px 10px;
  border-left: 1px dashed rgba(30, 64, 175, 0.18);
  margin-left: 10px;
}
.qam-chapter--plain .qam-chapter-body {
  border-left-color: rgba(148, 163, 184, 0.3);
}

.qam-chapter-empty {
  font-size: 11px;
  color: var(--ink-faint);
  padding: 8px 12px;
  border: 1px dashed var(--border);
  border-radius: 10px;
  text-align: center;
}

/* 功能行（组内缩进） */
.qam-nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 11px;
  cursor: pointer;
  text-align: left;
  transition: all 0.2s ease;
  position: relative;
  font-family: inherit;
}

.qam-nav-item:hover {
  background: rgba(255, 255, 255, 0.5);
}

.qam-nav-item.is-active {
  background: rgba(255, 255, 255, 0.82);
  border-color: var(--border-glow);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.5),
    0 4px 16px -6px rgba(30, 64, 175, 0.2);
}

.qam-nav-item.is-active .qam-nav-name {
  color: var(--c-blue);
  font-weight: 700;
}

.qam-nav-item.is-off {
  opacity: 0.55;
}
.qam-nav-item.is-off .qam-nav-tile {
  filter: grayscale(0.6);
}

/* 拖拽反馈（组内排序） */
.qam-nav-item.is-dragging {
  opacity: 0.4;
}
.qam-nav-item.is-drop-target {
  border-color: var(--c-blue-2);
  box-shadow: 0 -2px 0 0 var(--c-blue-2);
}

/* 渐变方块 */
.qam-nav-tile {
  width: 32px; height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--aurora-soft);
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 9px;
  flex-shrink: 0;
  transition: transform 0.2s var(--ease);
}

.qam-nav-item:hover .qam-nav-tile {
  transform: scale(1.06);
}

.qam-nav-icon {
  font-size: 16px;
  color: var(--c-blue);
}

.qam-nav-info {
  flex: 1;
  min-width: 0;
}

.qam-nav-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.4;
  transition: color 0.2s ease;
  letter-spacing: -0.005em;
  display: flex;
  align-items: center;
  gap: 5px;
}

.qam-nav-off {
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

.qam-nav-meta {
  font-size: 11px;
  font-weight: 500;
  color: var(--ink-faint);
  margin-top: 1px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.qam-nav-skill {
  color: var(--ink-faint);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 68px;
  opacity: 0.7;
}
.qam-nav-skill::before {
  content: '·';
  margin-right: 4px;
}

.qam-nav-arrow {
  color: var(--c-blue);
  flex-shrink: 0;
  opacity: 0;
  transform: translateX(-4px);
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.qam-nav-item:hover .qam-nav-arrow,
.qam-nav-item.is-active .qam-nav-arrow {
  opacity: 0.6;
  transform: translateX(0);
}
.qam-nav-item.is-active .qam-nav-arrow {
  opacity: 1;
}

/* ─── Empty sidebar ─── */
.qam-sidebar-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 20px;
  gap: 12px;
}

.qam-empty-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
  letter-spacing: -0.01em;
  margin: 0;
}

/* ─── Main Content ─── */
.qam-main {
  position: relative;
  z-index: 1;
  flex: 1;
  overflow-y: auto;
  border-radius: 18px;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}

/* ─── Placeholder ─── */
.qam-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 12px;
}

.placeholder-orb {
  width: 88px; height: 88px;
  border-radius: 50%;
  background: var(--aurora);
  position: relative;
  margin-bottom: 8px;
  animation: orb-breathe 3s ease-in-out infinite;
}
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

.placeholder-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--ink);
  margin: 0;
  letter-spacing: -0.01em;
}
.placeholder-hint {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-mute);
  margin: 0;
}

/* ─── Detail Transition ─── */
.qam-fade-enter-active,
.qam-fade-leave-active {
  transition: opacity 0.22s ease, transform 0.22s var(--ease);
}
.qam-fade-enter-from {
  opacity: 0;
  transform: translateY(10px);
}
.qam-fade-leave-to {
  opacity: 0;
  transform: translateY(-5px);
}

/* ─── Detail View ─── */
.qam-detail {
  max-width: 880px;
  margin: 0 auto;
  padding: 0 32px 32px;
}

/* 详情头卡片 — 玻璃感 */
.qam-detail-head {
  display: flex;
  gap: 20px;
  padding: 26px;
  background: var(--surface-strong);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 18px;
  margin-bottom: 20px;
  margin-top: 4px;
  position: relative;
  overflow: hidden;
  cursor: pointer;
  transition: all 0.22s ease;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    var(--shadow-md);
}

.qam-detail-head:hover {
  border-color: var(--border-glow);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.5),
    var(--shadow-lg);
  transform: translateY(-2px);
}

/* 图标方块 */
.qam-detail-tile {
  width: 58px; height: 58px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--aurora);
  border-radius: 14px;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 6px 20px -6px rgba(30, 64, 175, 0.4);
}

.qam-detail-icon {
  font-size: 28px;
  color: #fff;
}

.qam-detail-info {
  flex: 1;
  min-width: 0;
}

.qam-detail-title {
  font-size: 21px;
  font-weight: 700;
  color: var(--ink);
  margin: 0 0 6px 0;
  line-height: 1.3;
  letter-spacing: -0.02em;
}

.qam-detail-desc {
  font-size: 13.5px;
  color: var(--ink-soft);
  line-height: 1.6;
  margin: 0 0 12px 0;
}

.qam-detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.qam-detail-ops {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
  align-items: center;
}

/* ─── Examples Section — 玻璃卡片 ─── */
.qam-examples {
  background: var(--surface-strong);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 22px 26px 26px;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    var(--shadow-md);
}

.qam-examples-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}

.qam-examples-title {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10.5px;
  font-weight: 700;
  color: var(--ink-faint);
  margin: 0;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

/* Empty examples */
.qam-examples-empty {
  padding: 44px 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.empty-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--ink);
  letter-spacing: -0.01em;
  margin: 0;
}

.empty-hint {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-mute);
  margin: 0;
}

/* 案例卡片 */
.qam-examples-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 4px;
}

.qam-ex-card {
  display: flex;
  border: 1px solid var(--border);
  border-radius: 14px;
  overflow: hidden;
  transition: all 0.22s ease;
  background: var(--surface-deep);
  cursor: pointer;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.3);
}

.qam-ex-card:hover {
  border-color: var(--border-glow);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.5),
    var(--shadow-lg);
  transform: translateY(-2px);
}

.qam-ex-preview {
  width: 128px;
  height: 96px;
  flex-shrink: 0;
  overflow: hidden;
  background: rgba(30, 64, 175, 0.06);
  display: flex;
  align-items: center;
  justify-content: center;
}

.qam-ex-preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.3s var(--ease);
}

.qam-ex-card:hover .qam-ex-preview img {
  transform: scale(1.06);
}

.qam-ex-body {
  flex: 1;
  padding: 14px 18px;
  min-width: 0;
}

.qam-ex-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}

.qam-ex-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  margin: 0;
  line-height: 1.4;
  letter-spacing: -0.005em;
}

.qam-ex-desc {
  font-size: 13px;
  color: var(--ink-soft);
  line-height: 1.55;
  margin: 0 0 10px 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.qam-ex-footer {
  display: flex;
  align-items: center;
  gap: 10px;
}

.qam-ex-count {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  font-weight: 500;
  color: var(--ink-faint);
  font-variant-numeric: tabular-nums;
}
.qam-ex-count svg {
  opacity: 0.55;
}

/* ─── Form Fields ─── */
.qam-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.qam-icon-input {
  display: flex;
  align-items: center;
  gap: 10px;
}

.qam-icon-preview {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--aurora);
  border-radius: 10px;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 2px 8px -2px rgba(30, 64, 175, 0.4);
}

.qam-icon-preview-icon {
  font-size: 22px;
  color: #fff;
}

.qam-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-soft);
}

.qam-label em {
  color: #ef4444;
  font-style: normal;
}

.qam-hint {
  font-size: 12px;
  color: var(--ink-faint);
}

.qam-icon-link,
.qam-icon-link:visited {
  color: #2563eb;
  text-decoration: underline;
  text-underline-offset: 2px;
  font-weight: 600;
  transition: color 0.15s ease;
}

.qam-icon-link:hover {
  color: #0ea5e9;
}

.qam-code {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 11px;
  padding: 1px 5px;
  border-radius: 8px;
  background: rgba(30, 64, 175, 0.08);
  color: var(--c-blue);
  font-weight: 600;
  letter-spacing: 0.01em;
}

/* Image upload */
.qam-upload-trigger {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: var(--ink-mute);
  cursor: pointer;
  transition: color 0.2s ease;
}
.qam-upload-trigger:hover {
  color: var(--c-blue);
}
.qam-upload-text {
  font-size: 13px;
  font-weight: 600;
}

/* ─── 多图网格（拖拽排序） ─── */
.qam-img-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.qam-img-card {
  position: relative;
  width: 88px;
  height: 88px;
  border-radius: 10px;
  overflow: hidden;
  border: 1.5px solid var(--border);
  background: rgba(30, 64, 175, 0.04);
  cursor: grab;
  transition: all 0.2s ease;
  box-shadow: var(--shadow-sm);
}

.qam-img-card:hover {
  border-color: var(--border-glow);
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

.qam-img-card.is-dragging {
  opacity: 0.4;
  transform: scale(0.95);
  border-color: var(--border-glow);
}

.qam-img-card.is-drop-target {
  border-color: var(--c-blue);
  box-shadow: 0 0 0 2px rgba(30, 64, 175, 0.2);
  transform: scale(1.04);
}

.qam-img-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* NImage 在卡片内撑满 */
.qam-img-card :deep(.n-image) {
  width: 100% !important;
  height: 100% !important;
}

.qam-img-card :deep(.n-image img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  cursor: zoom-in;
  border-radius: 9px;
}

/* NImageGroup 不参与布局 */
.qam-img-grid :deep(.n-image-group) {
  display: contents;
}

/* 隐藏预览触发器 */
.qam-preview-trigger {
  position: absolute;
  width: 0 !important;
  height: 0 !important;
  overflow: hidden;
  opacity: 0;
  pointer-events: none;
}

.qam-img-overlay {
  position: absolute;
  inset: 0;
  opacity: 0;
  transition: opacity 0.18s ease;
  pointer-events: none;
  display: flex;
  align-items: flex-start;
}

.qam-img-card:hover .qam-img-overlay {
  opacity: 1;
  pointer-events: auto;
}

/* 统一顶部横栏 */
.qam-img-bar {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 4px 5px;
  background: linear-gradient(to bottom, rgba(0,0,0,0.45) 0%, rgba(0,0,0,0.15) 70%, transparent 100%);
}

.qam-img-index {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9.5px;
  font-weight: 700;
  color: rgba(255,255,255,0.9);
  line-height: 1;
}

.qam-img-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
}

.qam-img-action-btn {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: rgba(255,255,255,0.8);
  cursor: pointer;
  transition: all 0.15s ease;
  padding: 0;
}

.qam-img-action-btn:hover {
  background: rgba(255,255,255,0.2);
  color: #fff;
}

.qam-img-action-btn--danger:hover {
  background: rgba(239,68,68,0.7);
  color: #fff;
}

.qam-img-drag-handle {
  position: absolute;
  bottom: 3px;
  left: 50%;
  transform: translateX(-50%);
  color: #fff;
  opacity: 0;
  transition: opacity 0.18s ease;
  pointer-events: none;
  filter: drop-shadow(0 1px 2px rgba(0,0,0,0.5));
}

.qam-img-card:hover .qam-img-drag-handle {
  opacity: 1;
}

/* ─── 添加按钮（紧凑态，有图片时） ─── */
.qam-img-add-wrap {
  position: relative;
  width: 88px;
  height: 88px;
  flex-shrink: 0;
}

.qam-img-add {
  width: 88px;
  height: 88px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  border-radius: 10px;
  border: 1.5px solid rgba(30, 64, 175, 0.22);
  background: var(--surface);
  color: var(--ink-mute);
  cursor: pointer;
  transition: all 0.2s ease;
}

.qam-img-add:hover:not(.is-uploading) {
  border-color: var(--c-blue);
  color: var(--c-blue);
  background: rgba(30, 64, 175, 0.04);
}

.qam-img-add.is-uploading,
.qam-img-add.is-paste-mode {
  cursor: default;
}

.qam-add-paste-hint {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  font-weight: 700;
  color: var(--c-blue);
}

.qam-img-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid var(--border);
  border-top-color: var(--c-blue);
  border-radius: 50%;
  animation: qam-spin 0.7s linear infinite;
}

@keyframes qam-spin {
  to { transform: rotate(360deg); }
}

/* ─── 紧凑粘贴按钮（贴在添加按钮右下角） ─── */
.qam-paste-btn {
  position: absolute;
  right: -4px;
  bottom: -4px;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 7px;
  border: 1.5px solid var(--border);
  background: var(--surface-deep);
  color: var(--ink-faint);
  cursor: pointer;
  transition: all 0.18s ease;
  outline: none;
  padding: 0;
  z-index: 2;
  box-shadow: 0 1px 4px rgba(15,23,42,0.08);
}

.qam-paste-btn:hover {
  border-color: var(--c-sky);
  color: var(--c-sky);
  transform: scale(1.08);
}

.qam-paste-btn.is-active {
  border-color: var(--c-blue);
  color: var(--c-blue);
  background: rgba(30, 64, 175, 0.08);
  box-shadow: 0 0 0 2px rgba(30, 64, 175, 0.15);
}

/* ─── 英雄上传区域（空态） ─── */
.qam-img-hero {
  border: 1.5px solid rgba(30, 64, 175, 0.22);
  border-radius: 14px;
  overflow: hidden;
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}

.qam-img-hero :deep(.n-upload),
.qam-img-hero :deep(.n-upload-trigger) {
  width: 100%;
  border-radius: 0;
  border: none;
  outline: none;
  background: transparent;
}

.qam-img-hero-upload {
  display: block;
  width: 100%;
}

.qam-img-hero-trigger {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 28px 20px 22px;
  cursor: pointer;
  color: var(--ink-mute);
  transition: all 0.2s ease;
}

.qam-img-hero-trigger:hover {
  color: var(--c-blue);
  background: rgba(30, 64, 175, 0.03);
}

.qam-img-hero-trigger.is-uploading {
  cursor: wait;
  opacity: 0.65;
}

.qam-hero-icon {
  color: var(--ink-faint);
  transition: color 0.2s ease;
}

.qam-img-hero-trigger:hover .qam-hero-icon {
  color: var(--c-blue);
}

.qam-hero-spinner {
  width: 26px;
  height: 26px;
}

.qam-hero-text {
  font-size: 13.5px;
  font-weight: 600;
  letter-spacing: -0.005em;
}

.qam-hero-sub {
  font-size: 11.5px;
  color: var(--ink-faint);
}

/* 分隔线 + 或 */
.qam-img-hero-divider {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 24px;
}

.qam-hero-line {
  flex: 1;
  height: 1px;
  background: var(--border);
}

.qam-hero-or {
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-faint);
  letter-spacing: 0.04em;
}

/* 粘贴行 */
.qam-img-hero-paste {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px 20px;
  color: var(--ink-mute);
  cursor: pointer;
  transition: all 0.18s ease;
  outline: none;
  font-size: 12.5px;
  font-weight: 500;
  user-select: none;
  border-top: 1px solid var(--border);
  background: rgba(30, 64, 175, 0.015);
}

.qam-img-hero-paste:hover {
  color: var(--c-blue);
  background: rgba(30, 64, 175, 0.04);
}

.qam-img-hero-paste.is-active {
  color: var(--c-blue);
  background: rgba(30, 64, 175, 0.06);
}

.qam-img-hero-paste kbd {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 11px;
  font-weight: 800;
  padding: 3px 8px;
  border-radius: 5px;
  border: 1.5px solid var(--border-glow);
  background: var(--surface-strong);
  color: var(--c-blue);
  line-height: 1;
  box-shadow: 0 1px 2px rgba(30, 64, 175, 0.1);
}

/* ─── 粘贴动画 ─── */
.qam-paste-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--c-blue);
  position: relative;
  flex-shrink: 0;
}

.qam-paste-pulse::after {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  border: 2px solid var(--c-blue);
  opacity: 0;
  animation: qam-pulse-ring 1.2s ease-out infinite;
}

@keyframes qam-pulse-ring {
  0%   { transform: scale(0.6); opacity: 0.6; }
  100% { transform: scale(1.6); opacity: 0; }
}

/* 案例卡片预览图数量标记 */
.qam-ex-preview {
  position: relative;
}

.qam-ex-img-count {
  position: absolute;
  bottom: 4px;
  right: 4px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 8px;
  font-family: 'JetBrains Mono', monospace;
}

/* ─── Responsive ─── */
@media (max-width: 900px) {
  .qam-root { padding: 12px 16px 16px; gap: 14px; }
  .qam-sidebar { width: 240px; }
  .qam-detail { padding: 18px; }
  .qam-detail-head {
    flex-direction: column;
    padding: 20px;
    gap: 14px;
  }
  .qam-detail-ops { align-self: flex-end; }
  .qam-detail-tile { width: 48px; height: 48px; }
  .qam-detail-emoji { font-size: 24px; }
  .qam-examples { padding: 16px 18px 20px; }
  .qam-ex-preview { width: 100px; height: 75px; }
}

@media (max-width: 640px) {
  .qam-root { flex-direction: column; max-width: 100%; padding: 10px 12px 12px; gap: 10px; }
  .qam-sidebar { width: 100%; max-height: 45vh; }
  .brand-text { display: none; }
  .qam-ex-card { flex-direction: column; }
  .qam-ex-preview { width: 100%; height: 140px; }
}

@media (prefers-reduced-motion: reduce) {
  .bm-halo, .empty-orb, .placeholder-orb { animation: none !important; }
}
/* ─── 职业引用 chip（与职业体系工作台互通） ─── */
.chip--prof {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  color: var(--c-cyan);
  background: rgba(8, 145, 178, 0.08);
  border-color: rgba(8, 145, 178, 0.22);
  transition: all 0.16s ease;
}

.chip--prof:hover {
  color: #fff;
  background: var(--aurora);
  border-color: transparent;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px -4px rgba(8, 145, 178, 0.5);
}

.chip-prof-icon {
  font-size: 12px;
  transition: color 0.16s ease;
}

.chip--prof:hover .chip-prof-icon {
  color: #fff;
}

.chip--prof-add {
  display: inline-flex;
  align-items: center;
  font-family: inherit;
  cursor: pointer;
  color: var(--ink-faint);
  background: transparent;
  border: 1px dashed rgba(148, 163, 184, 0.55);
  transition: all 0.16s ease;
}

.chip--prof-add:hover {
  color: var(--c-blue);
  border-color: rgba(30, 64, 175, 0.4);
  background: rgba(30, 64, 175, 0.06);
}
</style>
