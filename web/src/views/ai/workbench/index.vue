<script setup lang="ts">
import {computed, nextTick, onMounted, onUnmounted, reactive, ref} from 'vue';
import {useRouter} from 'vue-router';
import {useAuthStore} from '@/store/modules/auth';
import {brand} from '@/constants/brand';
import {
  type AgentSkill,
  type AgentSkillVersion,
  type AgentVisibility,
  type DiscoveredSkillCandidate,
  fetchActivateAgentSkillVersion,
  fetchAgentSkillVersions,
  fetchAgentSkills,
  fetchDeleteAgentSkill,
  fetchDeleteAgentSkillVersion,
  fetchDiscoverSkillStream,
  fetchDownloadAgentSkill,
  fetchInstallAgentSkill,
  fetchUpdateAgentSkill,
  fetchUpdateAgentSkillVisibility,
  fetchUpdateAgentSkillTags,
  fetchUploadAgentSkill
} from '@/service/api';

type SourceFilter = 'all' | 'builtin' | 'official' | 'derived' | 'curated';
type VisibilityFilter = 'all' | 'mine' | 'shared';

const router = useRouter();
const authStore = useAuthStore();
const myUserId = computed<number | null>(() => {
  const id = authStore.userInfo?.userId;
  return id != null && id !== '' ? Number(id) : null;
});
/** 超管或管理员：技能管理权限 */
const isAdmin = computed(() => (authStore.userInfo?.roles || []).some(r => r === 'R_SUPER' || r === 'R_ADMIN'));
const searchText = ref('');
const filterSource = ref<SourceFilter>('all');
const filterVisibility = ref<VisibilityFilter>('all');
const filterTag = ref<string>('');
const tagFilterOpen = ref(false);
const includeDisabled = ref(false);

function closeTagFilter() {
  tagFilterOpen.value = false;
}

onMounted(async () => {
  document.addEventListener('click', closeTagFilter);
  await reload();
});

onUnmounted(() => {
  document.removeEventListener('click', closeTagFilter);
});

const skills = ref<AgentSkill[]>([]);
const loading = ref(false);

const generalEntry = {
  id: 'qa',
  name: brand.assistantName,
  description: brand.assistantDesc,
  icon: '§'
};

const sourceLabel: Record<AgentSkill['source'], string> = {
  builtin: '内置',
  official: '官方',
  derived: '凝练',
  curated: '收录'
};

function pickIcon(sk: AgentSkill): string {
  const map: Record<string, string> = {
    标准去重: '⇌',
    全文相似度: '≈',
    AI比对: '⇄',
    指标提取: '∑',
    标准评估: '★',
    对象关系: '⋈'
  };
  return map[sk.skillKey] || sk.name.slice(0, 1);
}

async function reload() {
  loading.value = true;
  try {
    const {data, error} = await fetchAgentSkills(includeDisabled.value);
    if (!error && data) skills.value = data;
  } finally {
    loading.value = false;
  }
}

const filteredSkills = computed(() => {
  // 官方技能优先排在最前（sort 稳定，其余保持后端顺序）
  return skills.value
    .filter(s => {
      const matchSearch =
        !searchText.value ||
        s.name.includes(searchText.value) ||
        s.skillKey.includes(searchText.value) ||
        (s.description ?? '').includes(searchText.value);
      const matchSource = filterSource.value === 'all' || s.source === filterSource.value;
      const matchVis =
        filterVisibility.value === 'all'
        || (filterVisibility.value === 'mine' && s.visibility === 'private')
        || (filterVisibility.value === 'shared' && s.visibility !== 'private');
      const matchTag = !filterTag.value || (s.tags || []).includes(filterTag.value);
      return matchSearch && matchSource && matchVis && matchTag;
    })
    .sort((a, b) => Number(b.source === 'official') - Number(a.source === 'official'));
});

const allTags = computed(() => {
  const set = new Set<string>();
  for (const s of skills.value) for (const t of s.tags || []) set.add(t);
  for (const t of localExtraTags.value) set.add(t);
  return Array.from(set).sort();
});

// 本地草稿标签池：在"标签管理"里新建的标签先存内存，等被某张卡片选中时才真正持久化
const localExtraTags = ref<string[]>([]);

const counts = computed(() => ({
  total: skills.value.length,
  builtin: skills.value.filter(s => s.source === 'builtin').length,
  official: skills.value.filter(s => s.source === 'official').length,
  derived: skills.value.filter(s => s.source === 'derived').length,
  curated: skills.value.filter(s => s.source === 'curated').length
}));

function goToQA() {
  router.push({name: 'ai_qa-glass', query: {t: Date.now()}});
}

function goToKnowledge() {
  router.push({path: '/ai/nian', query: {t: Date.now()}});
}

function runSkill(skill: AgentSkill) {
  router.push({name: 'ai_qa-glass', query: {skill: skill.skillKey, t: Date.now()}});
}

function runAiEdit(skill: AgentSkill, e: Event) {
  e.stopPropagation();
  // 跳到对话页，预填 "@编辑 + 目标 @skill_key"，让用户直接接着说要怎么改
  router.push({
    name: 'ai_qa-glass',
    query: {
      skill: '编辑',
      target: skill.skillKey,
      t: Date.now(),
    },
  });
}

async function toggleEnabled(skill: AgentSkill, e: Event) {
  e.stopPropagation();
  const {data, error} = await fetchUpdateAgentSkill(skill.id, {is_enabled: !skill.isEnabled});
  if (!error && data) Object.assign(skill, data);
}

async function removeSkill(skill: AgentSkill, e: Event) {
  e.stopPropagation();
  if (!requestConfirm(`del-skill-${skill.id}`)) return;
  const {error} = await fetchDeleteAgentSkill(skill.id);
  if (!error) skills.value = skills.value.filter(s => s.id !== skill.id);
}

// ───── 能力编辑 ─────────────────────────────────────────────────────────────
const editOpen = ref(false);
const editing = ref<AgentSkill | null>(null);
const editForm = ref({name: '', description: ''});
const editSaving = ref(false);
const editError = ref('');

const viewOpen = ref(false);
const viewing = ref<AgentSkill | null>(null);

function openView(skill: AgentSkill, e: Event) {
  e.stopPropagation();
  viewing.value = skill;
  viewOpen.value = true;
}

function closeView() {
  viewOpen.value = false;
  viewing.value = null;
}

function runFromView() {
  if (!viewing.value) return;
  const sk = viewing.value;
  closeView();
  runSkill(sk);
}

function openEdit(skill: AgentSkill, e: Event) {
  e.stopPropagation();
  editing.value = skill;
  editForm.value = {
    name: skill.name,
    description: skill.description || ''
  };
  editError.value = '';
  editOpen.value = true;
}

function closeEdit() {
  editOpen.value = false;
  editing.value = null;
}

async function saveEdit() {
  if (!editing.value || editSaving.value) return;
  editSaving.value = true;
  editError.value = '';
  // skill_md 即 SKILL.md 主文件，不允许在此手改（与技能库详情页一致）
  const payload = {
    name: editForm.value.name,
    description: editForm.value.description
  };
  try {
    const {data, error, msg} = await fetchUpdateAgentSkill(editing.value.id, payload);
    if (error || !data) {
      editError.value = msg || '保存失败';
      return;
    }
    Object.assign(editing.value, data);
    window.$message?.success?.('已保存');
    closeEdit();
  } finally {
    editSaving.value = false;
  }
}

// ── 卡片背景大字符视差 ───────────────────────────────────────────────
function onCardMouseMove(e: MouseEvent) {
  const card = (e.currentTarget as HTMLElement);
  const bgText = card.querySelector('.wb-card-bg-text') as HTMLElement | null;
  if (!bgText) return;
  const rect = card.getBoundingClientRect();
  const x = ((e.clientX - rect.left) / rect.width - 0.5) * 12;
  const y = ((e.clientY - rect.top) / rect.height - 0.5) * 8;
  bgText.style.transform = `translate(${x}px, ${y}px)`;
}

function onCardMouseLeave(e: MouseEvent) {
  const card = (e.currentTarget as HTMLElement);
  const bgText = card.querySelector('.wb-card-bg-text') as HTMLElement | null;
  if (!bgText) return;
  bgText.style.transform = '';
}

// ───── 技能操作 ─────────────────────────────────────────────────────────────

// ───── 发现技能 ────────────────────────────────────────────────────────────
const discoverOpen = ref(false);
const discoverQuery = ref('');
const discovering = ref(false);
const discoverError = ref('');
const candidates = ref<DiscoveredSkillCandidate[]>([]);
const installingUrl = ref<string | null>(null);

function openDiscover() {
  discoverOpen.value = true;
  discoverQuery.value = '';
  candidates.value = [];
  discoverError.value = '';
}

async function runDiscover() {
  if (!discoverQuery.value.trim() || discovering.value) return;
  discovering.value = true;
  discoverError.value = '';
  candidates.value = [];
  try {
    await fetchDiscoverSkillStream(discoverQuery.value.trim(), ev => {
      if (ev.type === 'candidates') candidates.value = ev.items;
      else if (ev.type === 'error') discoverError.value = ev.message;
    });
  } catch (err: any) {
    discoverError.value = err?.message || '请求失败';
  } finally {
    discovering.value = false;
  }
}

async function installCandidate(c: DiscoveredSkillCandidate) {
  if (installingUrl.value) return;
  installingUrl.value = c.source_url;
  try {
    const {data, error, msg} = await fetchInstallAgentSkill({
      source_url: c.source_url,
      is_public: true
    });
    if (error || !data) {
      window.$message?.error?.(msg || '安装失败');
      return;
    }
    window.$message?.success?.(`已安装：${data.skill?.skillKey || c.name}`);
    await reload();
  } finally {
    installingUrl.value = null;
  }
}

// ── 上传技能 ─────────────────────────────────────────────────────────────
const uploadPkgInputEl = ref<HTMLInputElement | null>(null);
const uploadingPkg = ref(false);
const uploadModalOpen = ref(false);
const uploadDragActive = ref(false);
const uploadProgress = ref(0);

function triggerUploadPkg() {
  if (uploadingPkg.value) return;
  uploadDragActive.value = false;
  uploadProgress.value = 0;
  uploadModalOpen.value = true;
}

function closeUploadModal() {
  if (uploadingPkg.value) return;
  uploadModalOpen.value = false;
  uploadDragActive.value = false;
}

function pickUploadFile() {
  if (uploadingPkg.value) return;
  uploadPkgInputEl.value?.click();
}

function onUploadDragEnter(e: DragEvent) {
  e.preventDefault();
  if (uploadingPkg.value) return;
  uploadDragActive.value = true;
}

function onUploadDragOver(e: DragEvent) {
  e.preventDefault();
  if (uploadingPkg.value) return;
  uploadDragActive.value = true;
}

function onUploadDragLeave(e: DragEvent) {
  e.preventDefault();
  uploadDragActive.value = false;
}

function onUploadDrop(e: DragEvent) {
  e.preventDefault();
  uploadDragActive.value = false;
  if (uploadingPkg.value) return;
  const file = e.dataTransfer?.files?.[0];
  if (file) void doUploadFile(file);
}

async function handleUploadPkgSelect(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  await doUploadFile(file);
}

async function doUploadFile(file: File) {
  if (!file.name.toLowerCase().endsWith('.zip')) {
    window.$message?.error?.('只接受 .zip 文件');
    return;
  }
  if (file.size > 50 * 1024 * 1024) {
    window.$message?.error?.('文件大小不能超过 50MB');
    return;
  }
  uploadingPkg.value = true;
  uploadProgress.value = 0;
  try {
    const result = await fetchUploadAgentSkill(
      file,
      {isPublic: true},
      (p) => { uploadProgress.value = p; }
    );
    window.$message?.success?.(`已上传：${result.skill?.skillKey || file.name}`);
    uploadModalOpen.value = false;
    await reload();
    if (versionsOpen.value && versionsSkill.value?.skillKey === result.skill?.skillKey) {
      const {data, error} = await fetchAgentSkillVersions(versionsSkill.value.id);
      if (!error && data) versionsList.value = data;
    }
  } catch (err: any) {
    window.$message?.error?.(err?.message || '上传失败');
  } finally {
    uploadingPkg.value = false;
  }
}

// ── 下载技能 ─────────────────────────────────────────────────────────────
async function downloadSkill(s: AgentSkill, e: Event) {
  e.stopPropagation();
  try {
    await fetchDownloadAgentSkill(s.id, `${s.skillKey}-${s.version || 'latest'}.zip`);
  } catch (err: any) {
    window.$message?.error?.(err?.message || '下载失败');
  }
}

// ── 版本管理抽屉 ───────────────────────────────────────────────────────────
const versionsOpen = ref(false);
const versionsSkill = ref<AgentSkill | null>(null);
const versionsList = ref<AgentSkillVersion[]>([]);
const versionsLoading = ref(false);

async function openVersions(s: AgentSkill, e: Event) {
  e.stopPropagation();
  versionsSkill.value = s;
  versionsOpen.value = true;
  versionsLoading.value = true;
  try {
    const {data, error} = await fetchAgentSkillVersions(s.id);
    if (!error && data) versionsList.value = data;
  } finally {
    versionsLoading.value = false;
  }
}

function closeVersions() {
  versionsOpen.value = false;
  versionsSkill.value = null;
  versionsList.value = [];
}

async function activateVersion(v: AgentSkillVersion) {
  if (!versionsSkill.value || v.isActive) return;
  if (!requestConfirm(`activate-ver-${versionsSkill.value.id}-${v.version}`)) return;
  const {data, error, msg} = await fetchActivateAgentSkillVersion(versionsSkill.value.id, v.version);
  if (error || !data) {
    window.$message?.error?.(msg || '切换失败');
    return;
  }
  window.$message?.success?.(`已切换到 v${v.version}`);
  // 刷新版本列表 + 主列表
  if (versionsSkill.value) {
    const {data: vlist} = await fetchAgentSkillVersions(versionsSkill.value.id);
    if (vlist) versionsList.value = vlist;
  }
  await reload();
}

async function removeVersion(v: AgentSkillVersion) {
  if (!versionsSkill.value || v.isActive) return;
  if (!requestConfirm(`del-ver-${versionsSkill.value.id}-${v.version}`)) return;
  const {error, msg} = await fetchDeleteAgentSkillVersion(versionsSkill.value.id, v.version);
  if (error) {
    window.$message?.error?.(msg || '删除失败');
    return;
  }
  window.$message?.success?.(`已删除 v${v.version}`);
  versionsList.value = versionsList.value.filter(x => x.version !== v.version);
}

function fmtSize(bytes: number | null): string {
  if (!bytes) return '--';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fmtDate(ts: number | null): string {
  if (!ts) return '--';
  const d = new Date(ts);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ── 可见性管理 ─────────────────────────────────────────────────────────────
const visOpen = ref(false);
const visTarget = ref<AgentSkill | null>(null);
const visForm = ref<{ visibility: AgentVisibility; rolesText: string }>({
  visibility: 'private',
  rolesText: '',
});
const visSaving = ref(false);

const visIcon = (v: AgentVisibility) => visLabel(v);

const visLabel = (v: AgentVisibility) =>
  v === 'public' ? '全员' : v === 'role' ? '指定角色' : '仅自己';

function openVisibility(skill: AgentSkill, e: Event) {
  e.stopPropagation();
  visTarget.value = skill;
  visForm.value = {
    visibility: skill.visibility || 'private',
    rolesText: (skill.allowedRoleCodes || []).join(', '),
  };
  visOpen.value = true;
}

function closeVisibility() {
  visOpen.value = false;
  visTarget.value = null;
}

async function saveVisibility() {
  if (!visTarget.value || visSaving.value) return;
  visSaving.value = true;
  try {
    const roleCodes =
      visForm.value.visibility === 'role'
        ? visForm.value.rolesText.split(',').map(x => x.trim()).filter(Boolean)
        : undefined;
    if (visForm.value.visibility === 'role' && (!roleCodes || roleCodes.length === 0)) {
      window.$message?.error?.('请填写至少一个角色 code');
      return;
    }
    const {data, error, msg} = await fetchUpdateAgentSkillVisibility(visTarget.value.id, {
      visibility: visForm.value.visibility,
      allowed_role_codes: roleCodes,
    });
    if (error || !data) {
      window.$message?.error?.(msg || '保存失败');
      return;
    }
    Object.assign(visTarget.value, data);
    window.$message?.success?.('已保存');
    closeVisibility();
  } finally {
    visSaving.value = false;
  }
}

// ── 标签 inline 编辑 ─────────────────────────────────────────────────────
type TagOwner = AgentSkill;

// 当前正在 inline 编辑的卡片 id，同一时刻只能编辑一个
const editingTagOwnerKey = ref<string>('');
const editingTagOwner = ref<TagOwner | null>(null);
const tagInlineInput = ref('');
const popPos = ref<{ top: number; left: number; width: number }>({top: 0, left: 0, width: 200});

function ownerKey(o: TagOwner): string {
  return `skill-${o.id}`;
}

function updatePopPos(inputEl: HTMLInputElement) {
  const r = inputEl.getBoundingClientRect();
  popPos.value = {
    top: r.bottom + 4,
    left: r.left,
    width: Math.max(r.width, 220),
  };
}

function startTagInline(o: TagOwner, e?: Event) {
  if (e) e.stopPropagation();
  editingTagOwnerKey.value = ownerKey(o);
  editingTagOwner.value = o;
  tagInlineInput.value = '';
  // 下一帧 focus + 算位置
  nextTick(() => {
    const el = document.getElementById(`tag-input-${ownerKey(o)}`) as HTMLInputElement | null;
    el?.focus();
    if (el) updatePopPos(el);
  });
}

function cancelTagInline() {
  editingTagOwnerKey.value = '';
  editingTagOwner.value = null;
  tagInlineInput.value = '';
}

async function commitTagInline(o: TagOwner) {
  const q = tagInlineInput.value.trim();
  if (!q) return;
  const suggestions = tagSuggestions(o);
  if (suggestions.length === 0) return;
  // 优先精确匹配，否则取第一条候选
  const exact = suggestions.find(t => t.toLowerCase() === q.toLowerCase());
  const pick = exact || suggestions[0];
  await pickSuggestion(o, pick);
}

async function removeTagFromCard(o: TagOwner, t: string, e: Event) {
  e.stopPropagation();
  const next = (o.tags || []).filter(x => x !== t);
  await saveTagsFor(o, next);
}

async function saveTagsFor(o: TagOwner, tags: string[]) {
  const {data, error, msg} = await fetchUpdateAgentSkillTags(o.id, tags);
  if (error || !data) {
    window.$message?.error?.(msg || '保存标签失败');
    return;
  }
  Object.assign(o, data);
}

// 与后端 _can_manage 对齐：非内置 且（超管/管理员 或 创建者）才可编辑/删除/改可见性
function canManage(o: TagOwner): boolean {
  return o.source !== 'builtin' && (isAdmin.value || (myUserId.value != null && o.userId === myUserId.value));
}

function tagSuggestions(o: TagOwner): string[] {
  // 候选 = 所有已用过标签 - 当前卡片已有 - 不匹配输入前缀的
  const existing = new Set(o.tags || []);
  const q = tagInlineInput.value.trim().toLowerCase();
  return allTags.value
    .filter(t => !existing.has(t))
    .filter(t => !q || t.toLowerCase().includes(q))
    .slice(0, 20);
}

async function pickSuggestion(o: TagOwner, t: string) {
  const next = [...(o.tags || [])];
  if (!next.includes(t)) next.push(t);
  await saveTagsFor(o, next);
  // 标签持久化后，从本地草稿池里移除
  localExtraTags.value = localExtraTags.value.filter(x => x !== t);
  // 选完保持编辑态，方便继续加
  tagInlineInput.value = '';
  nextTick(() => {
    const el = document.getElementById(`tag-input-${ownerKey(o)}`) as HTMLInputElement | null;
    el?.focus();
  });
}

// 输入框失焦时延迟关闭，避免点击候选项还没触发就被关
function onTagInputBlur() {
  setTimeout(() => {
    cancelTagInline();
  }, 150);
}

function onTagInputChange(e: Event) {
  const el = e.target as HTMLInputElement;
  if (el) updatePopPos(el);
}

// ── 标签管理弹窗（统一编辑/删除标签）──────────────────────────────────────
const tagMgrOpen = ref(false);
const tagMgrEditKey = ref('');
const tagMgrEditValue = ref('');
const tagMgrNewInput = ref('');

function openTagMgr() {
  tagMgrOpen.value = true;
  tagMgrEditKey.value = '';
  tagMgrEditValue.value = '';
  tagMgrNewInput.value = '';
}

function closeTagMgr() {
  tagMgrOpen.value = false;
  tagMgrEditKey.value = '';
  tagMgrEditValue.value = '';
  tagMgrNewInput.value = '';
}

function createTagInMgr() {
  const raw = tagMgrNewInput.value.trim();
  if (!raw) return;
  const parts = raw.split(/[,，\s]+/).map(x => x.trim().slice(0, 32)).filter(Boolean);
  for (const p of parts) {
    if (!allTags.value.includes(p)) {
      localExtraTags.value.push(p);
    }
  }
  tagMgrNewInput.value = '';
}

function tagUsageCount(tag: string): number {
  return skills.value.filter(s => (s.tags || []).includes(tag)).length;
}

function startRenameInMgr(tag: string) {
  tagMgrEditKey.value = tag;
  tagMgrEditValue.value = tag;
  nextTick(() => {
    const el = document.getElementById(`tag-mgr-input-${tag}`) as HTMLInputElement | null;
    el?.focus();
    el?.select();
  });
}

function cancelRenameInMgr() {
  tagMgrEditKey.value = '';
  tagMgrEditValue.value = '';
}

async function commitRenameInMgr() {
  const oldTag = tagMgrEditKey.value;
  const newTag = tagMgrEditValue.value.trim().slice(0, 32);
  cancelRenameInMgr();
  if (!newTag || newTag === oldTag) return;
  // 本地草稿池里也要替换
  if (localExtraTags.value.includes(oldTag)) {
    localExtraTags.value = localExtraTags.value.map(t => t === oldTag ? newTag : t);
  }
  for (const s of skills.value) {
    if ((s.tags || []).includes(oldTag)) {
      const next = (s.tags || []).map(t => t === oldTag ? newTag : t);
      await fetchUpdateAgentSkillTags(s.id, next);
      s.tags = next;
    }
  }
  window.$message?.success?.(`已将 #${oldTag} 重命名为 #${newTag}`);
}

async function deleteTagInMgr(tag: string) {
  if (!requestConfirm(`del-tag-${tag}`)) return;
  // 本地草稿池移除
  localExtraTags.value = localExtraTags.value.filter(t => t !== tag);
  for (const s of skills.value) {
    if ((s.tags || []).includes(tag)) {
      const next = (s.tags || []).filter(t => t !== tag);
      await fetchUpdateAgentSkillTags(s.id, next);
      s.tags = next;
    }
  }
  // 如果当前筛选的就是被删掉的标签，清掉筛选
  if (filterTag.value === tag) filterTag.value = '';
  window.$message?.success?.(`已删除标签 #${tag}`);
}

// ── Inline 二次确认 ──────────────────────────────────────────────────
const confirmingKeys = reactive<Record<string, boolean>>({});
const confirmTimers: Record<string, ReturnType<typeof setTimeout>> = {};

function needConfirm(key: string): boolean {
  return !!confirmingKeys[key];
}

function requestConfirm(key: string): boolean {
  if (confirmingKeys[key]) {
    // 第二次点击 = 确认
    clearTimeout(confirmTimers[key]);
    delete confirmTimers[key];
    confirmingKeys[key] = false;
    return true;
  }
  // 第一次点击 = 进入确认态
  confirmingKeys[key] = true;
  confirmTimers[key] = setTimeout(() => {
    confirmingKeys[key] = false;
    delete confirmTimers[key];
  }, 2500);
  return false;
}
</script>

<template>
  <div class="wb-root">
    <div class="wb-shell">
      <div class="wb-grain" aria-hidden="true"/>

    <!-- ─── Header（全局壳：brand + 知识库常驻入口） ─────────────────── -->
    <header class="wb-header">
      <div class="wb-header-inner">
        <div class="wb-brand">
          <span class="wb-brand-mark">⌘</span>
          <div class="wb-brand-text">
            <div class="wb-brand-title">{{ brand.workbenchTitle }}</div>
            <div class="wb-brand-sub">{{ brand.workbenchSub }}</div>
          </div>
        </div>

        <button
          class="wb-nian-entry"
          type="button"
          title="打开知识库 · 你的私人沉淀空间"
          @click="goToKnowledge"
        >
          <span class="wb-ne-mark">库</span>
          <span class="wb-ne-text">知识库</span>
        </button>
      </div>
    </header>

    <!-- ─── Main（toolbar + grid，工作流自洽） ───────────────────── -->
    <main class="wb-main">
      <!-- toolbar：search/filter + 操作按钮 -->
      <div class="wb-toolbar">
        <div class="wb-toolbar-inner">
          <div class="wb-header-controls">
            <div class="wb-search-wrap">
              <span class="wb-search-icon">⌕</span>
              <input v-model="searchText" class="wb-search"
                     placeholder="搜索技能…" type="text"/>
            </div>
            <div class="wb-filter-tabs" :data-active="filterVisibility">
              <span class="wb-filter-thumb" aria-hidden="true"/>
              <button :class="{ active: filterVisibility === 'all' }" class="wb-filter-tab"
                      @click="filterVisibility = 'all'">全部
              </button>
              <button :class="{ active: filterVisibility === 'mine' }" class="wb-filter-tab"
                      @click="filterVisibility = 'mine'">仅我
              </button>
              <button :class="{ active: filterVisibility === 'shared' }" class="wb-filter-tab"
                      @click="filterVisibility = 'shared'">共享
              </button>
            </div>
            <div v-if="allTags.length" class="wb-tag-filter" @click.stop>
              <button class="wb-tag-filter-trigger" :class="{ active: !!filterTag }" @click="tagFilterOpen = !tagFilterOpen">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/>
                  <line x1="7" y1="7" x2="7.01" y2="7"/>
                </svg>
                <span>{{ filterTag ? `#${filterTag}` : '标签' }}</span>
                <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="6 9 12 15 18 9"/>
                </svg>
              </button>
              <Transition name="wb-dropdown">
              <div v-if="tagFilterOpen" class="wb-tag-filter-drop">
                <button class="wb-tag-filter-item" :class="{ active: !filterTag }"
                        @click="filterTag = ''; tagFilterOpen = false;">全部标签</button>
                <button v-for="t in allTags" :key="t"
                        class="wb-tag-filter-item" :class="{ active: filterTag === t }"
                        @click="filterTag = t; tagFilterOpen = false;">#{{ t }}</button>
              </div>
              </Transition>
            </div>
            <button class="wb-discover-btn" @click="triggerUploadPkg">
              <span class="ae-icon">↥</span>
              <span>上传</span>
            </button>
            <button class="wb-discover-btn" @click="openDiscover">
              <span class="ae-icon">⌕</span>
              <span>发现</span>
            </button>
          </div>
        </div>
      </div>

      <Transition name="wb-grid-swap" mode="out-in" appear>
        <div class="wb-grid">
          <!-- 通用问答固定卡片 -->
          <div class="wb-card wb-card-general" @click="goToQA"
               @mousemove="onCardMouseMove" @mouseleave="onCardMouseLeave">
            <div class="wb-card-bg-text">QA</div>
            <div class="wb-card-icon">{{ generalEntry.icon }}</div>
            <div class="wb-card-body">
              <div class="wb-card-name">{{ generalEntry.name }}</div>
              <div class="wb-card-desc">{{ generalEntry.description }}</div>
            </div>
            <div class="wb-card-footer">
              <span class="wb-badge wb-badge-general">{{ brand.assistantBadge }}</span>
              <span class="wb-card-arrow">→</span>
            </div>
          </div>

          <!-- 技能卡片 -->
          <div
            v-for="skill in filteredSkills"
            :key="'cap-' + skill.id"
            :class="{ 'wb-card-inactive': !skill.isEnabled }"
            class="wb-card wb-card-skill"
            @click="runSkill(skill)"
            @mousemove="onCardMouseMove"
            @mouseleave="onCardMouseLeave"
          >
            <div class="wb-card-bg-text">{{ pickIcon(skill) }}</div>
            <div class="wb-card-icon">{{ pickIcon(skill) }}</div>

            <!-- 右上角浮层：操作按钮 -->
            <div class="wb-card-actions" @click.stop>
              <button v-if="skill.source === 'builtin'" class="wb-act-btn"
                      @click="openView(skill, $event)">详情</button>
              <template v-if="skill.source !== 'builtin'">
                <button v-if="canManage(skill)" class="wb-act-btn" @click="openEdit(skill, $event)">编辑</button>
                <button class="wb-act-btn" @click="runAiEdit(skill, $event)">AI编辑</button>
                <button v-if="skill.hasFiles" class="wb-act-btn" @click="openVersions(skill, $event)">版本</button>
                <button v-if="skill.hasFiles" class="wb-act-btn" @click="downloadSkill(skill, $event)">下载</button>
                <button v-if="canManage(skill)" class="wb-act-btn" @click="openVisibility(skill, $event)">范围</button>
                <button v-if="canManage(skill)" class="wb-act-btn wb-act-btn-danger"
                        :class="{ 'is-confirming': needConfirm(`del-skill-${skill.id}`) }"
                        @click="removeSkill(skill, $event)">{{ needConfirm(`del-skill-${skill.id}`) ? '再点确认' : '删除' }}</button>
              </template>
            </div>

            <div class="wb-card-body">
              <div class="wb-card-name">
                <span class="wb-card-at">@{{ skill.skillKey }}</span>
                <span class="wb-card-separator">·</span>
                <span>{{ skill.name }}</span>
                <span v-if="skill.version" class="wb-card-ver">v{{ skill.version }}</span>
              </div>
              <div class="wb-card-desc">{{ skill.description || '(未填写描述)' }}</div>
            </div>
            <div class="wb-card-footer">
              <div class="wb-card-tagbar">
                <span v-if="skill.source !== 'builtin'" :class="`wb-vis-chip wb-vis-${skill.visibility}`"
                      :title="`可见范围：${visLabel(skill.visibility)}`">
                  {{ visLabel(skill.visibility) }}
                </span>
                <span v-if="skill.hasFiles" class="wb-vis-chip wb-vis-files" title="含文件">
                  {{ skill.fileCount }} 文件
                </span>
                <span v-for="t in (skill.tags || [])" :key="t"
                      class="wb-tag-chip" :class="{ active: filterTag === t }"
                      :title="filterTag === t ? '点击取消筛选' : `点击只看 #${t}`"
                      @click.stop="filterTag = filterTag === t ? '' : t">
                  #{{ t }}
                  <button v-if="canManage(skill)"
                          class="wb-tag-chip-x" title="移除此标签"
                          @click.stop="removeTagFromCard(skill, t, $event)">×</button>
                </span>

                <!-- inline 输入框（编辑态） -->
                <template v-if="canManage(skill)">
                  <span v-if="editingTagOwnerKey === ownerKey(skill)"
                        class="wb-tag-input-wrap" @click.stop>
                    <input
                      :id="`tag-input-${ownerKey(skill)}`"
                      v-model="tagInlineInput"
                      class="wb-tag-input-inline"
                      placeholder="搜索标签…"
                      @keydown.enter.prevent="commitTagInline(skill)"
                      @keydown.esc.prevent="cancelTagInline"
                      @blur="onTagInputBlur"
                      @input="onTagInputChange"
                    />
                  </span>
                  <template v-else>
                    <button v-if="(skill.tags || []).length === 0"
                            class="wb-tag-add-empty"
                            @click.stop="startTagInline(skill, $event)">+ 添加标签</button>
                    <button v-else class="wb-tag-add-plus"
                            title="添加标签"
                            @click.stop="startTagInline(skill, $event)">+</button>
                  </template>
                </template>
              </div>
            </div>
          </div>

          <div v-if="!loading && filteredSkills.length === 0" class="wb-empty">
            <span class="wb-empty-icon">∅</span>
            <span>{{ searchText ? '未找到匹配的技能' : brand.emptyCapability }}</span>
          </div>
      </div>
      </Transition>
    </main>

    <!-- ─── Footer ─────────────────────────────────────────────────── -->
    <footer class="wb-footer">
      <span>{{ brand.footerCapability(counts.total) }}</span>
    </footer>

    <!-- ─── Discover 抽屉 ────────────────────────────────────────────── -->
    <Transition name="wb-drawer">
    <div v-if="discoverOpen" class="wb-drawer-mask" @click.self="discoverOpen = false">
      <div class="wb-drawer">
        <header class="wb-drawer-head">
          <span class="wb-drawer-tag">DISCOVER · SKILLS</span>
          <span class="wb-drawer-line"/>
          <button class="wb-drawer-close" @click="discoverOpen = false">×</button>
        </header>
        <div class="wb-drawer-body">
          <label class="wb-drawer-label">描述你想要的技能</label>
          <textarea
            v-model="discoverQuery"
            class="wb-drawer-input"
            placeholder="例：帮我找一个能自动从标准草案里抽取测试方法并生成检测表的 skill…"
            rows="3"
            @keydown.enter.meta.prevent="runDiscover"
          />
          <div class="wb-drawer-actions">
            <button :disabled="!discoverQuery.trim() || discovering" class="wb-drawer-run" @click="runDiscover">
              {{ discovering ? '发现中…' : '开始发现' }}
            </button>
          </div>
          <div v-if="discoverError" class="wb-drawer-error">{{ discoverError }}</div>

          <div v-if="candidates.length" class="wb-cand-list">
            <div class="wb-cand-label">候选 · {{ candidates.length }}</div>
            <div v-for="c in candidates" :key="c.source_url" class="wb-cand-item">
              <div class="wb-cand-name">{{ c.name }}</div>
              <div v-if="c.description" class="wb-cand-desc">{{ c.description }}</div>
              <div class="wb-cand-url">{{ c.source_url }}</div>
              <button
                class="wb-cand-install"
                :disabled="installingUrl === c.source_url"
                @click="installCandidate(c)"
              >{{ installingUrl === c.source_url ? '安装中…' : '安装' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
    </Transition>

    <!-- ─── 能力详情抽屉（只读） ───────────────────────────────────── -->
    <Transition name="wb-drawer">
    <div v-if="viewOpen && viewing" class="wb-drawer-mask" @click.self="closeView">
      <div class="wb-drawer">
        <header class="wb-drawer-head">
          <span class="wb-drawer-tag">VIEW · CAPABILITY</span>
          <span class="wb-drawer-line"/>
          <button class="wb-drawer-close" @click="closeView">×</button>
        </header>
        <div class="wb-drawer-body">
          <div class="wb-drawer-keyrow">
            <span class="wb-drawer-keytag">@{{ viewing.skillKey }}</span>
            <span :class="`wb-badge-${viewing.source}`" class="wb-badge">{{ sourceLabel[viewing.source] }}</span>
            <span v-if="!viewing.isEnabled" class="wb-drawer-readonly">· 已停用</span>
          </div>

          <label class="wb-drawer-label">显示名</label>
          <div class="wb-drawer-readonly-box">{{ viewing.name }}</div>

          <label class="wb-drawer-label">简短描述</label>
          <div class="wb-drawer-readonly-box">{{ viewing.description || '(未填写描述)' }}</div>

          <label class="wb-drawer-label">SKILL.md</label>
          <pre class="wb-drawer-readonly-box wb-drawer-prompt-view">{{ viewing.skillMd }}</pre>

          <div class="wb-drawer-actions">
            <button class="wb-drawer-cancel" @click="closeView">关闭</button>
            <button class="wb-drawer-run" @click="runFromView">运行此能力</button>
          </div>
        </div>
      </div>
    </div>
    </Transition>

    <!-- ─── 能力编辑抽屉 ─────────────────────────────────────────────── -->
    <Transition name="wb-drawer">
    <div v-if="editOpen && editing" class="wb-drawer-mask" @click.self="closeEdit">
      <div class="wb-drawer">
        <header class="wb-drawer-head">
          <span class="wb-drawer-tag">EDIT · CAPABILITY</span>
          <span class="wb-drawer-line"/>
          <button class="wb-drawer-close" @click="closeEdit">×</button>
        </header>
        <div class="wb-drawer-body">
          <div class="wb-drawer-keyrow">
            <span class="wb-drawer-keytag">@{{ editing.skillKey }}</span>
          </div>

          <label class="wb-drawer-label">显示名</label>
          <input
            v-model="editForm.name"
            class="wb-drawer-input wb-drawer-input-line"
            type="text"
          />

          <label class="wb-drawer-label">简短描述</label>
          <input
            v-model="editForm.description"
            class="wb-drawer-input wb-drawer-input-line"
            type="text"
            placeholder="一句话说明这个能力做什么"
          />

          <div class="wb-drawer-actions">
            <button class="wb-drawer-cancel" @click="closeEdit">取消</button>
            <button :disabled="editSaving" class="wb-drawer-run" @click="saveEdit">
              {{ editSaving ? '保存中…' : '保存' }}
            </button>
          </div>
          <div v-if="editError" class="wb-drawer-error">{{ editError }}</div>
        </div>
      </div>
    </div>
    </Transition>

    <!-- ─── 版本管理抽屉 ─────────────────────────────────────────────── -->
    <Transition name="wb-drawer">
    <div v-if="versionsOpen && versionsSkill" class="wb-drawer-mask" @click.self="closeVersions">
      <div class="wb-drawer">
        <header class="wb-drawer-head">
          <span class="wb-drawer-tag">VERSIONS</span>
          <span class="wb-drawer-line"/>
          <button class="wb-drawer-close" @click="closeVersions">×</button>
        </header>
        <div class="wb-drawer-body">
          <div class="wb-drawer-keyrow">
            <span class="wb-drawer-keytag">{{ versionsSkill.skillKey }}</span>
            <span class="wb-drawer-readonly">当前 v{{ versionsSkill.version || '--' }}</span>
          </div>

          <div class="wb-version-actions">
            <button class="wb-mini-btn" @click="triggerUploadPkg">上传新版本（zip）</button>
          </div>

          <div v-if="versionsLoading" class="wb-drawer-readonly">加载中…</div>
          <ul v-else-if="versionsList.length" class="wb-version-list">
            <li v-for="v in versionsList" :key="v.version" class="wb-version-item" :class="{ 'is-active': v.isActive }">
              <div class="wb-version-row">
                <span class="wb-version-num">v{{ v.version }}</span>
                <span v-if="v.isActive" class="wb-version-active-badge">当前</span>
                <span class="wb-version-size">{{ v.fileCount }} 文件 · {{ fmtSize(v.size) }}</span>
              </div>
              <div class="wb-version-ops">
                <button v-if="!v.isActive" class="wb-mini-btn"
                        :class="{ 'is-confirming': needConfirm(`activate-ver-${versionsSkill.id}-${v.version}`) }"
                        @click="activateVersion(v)">{{ needConfirm(`activate-ver-${versionsSkill.id}-${v.version}`) ? '再点确认' : '激活' }}</button>
                <button v-if="!v.isActive" class="wb-mini-btn wb-mini-btn-danger"
                        :class="{ 'is-confirming': needConfirm(`del-ver-${versionsSkill.id}-${v.version}`) }"
                        @click="removeVersion(v)">{{ needConfirm(`del-ver-${versionsSkill.id}-${v.version}`) ? '再点确认' : '删除' }}</button>
              </div>
            </li>
          </ul>
          <div v-else class="wb-drawer-readonly">暂无版本记录</div>
        </div>
      </div>
    </div>
    </Transition>

    <!-- ─── 上传技能包弹窗 ─────────────────────────────────────────── -->
    <Transition name="wb-drawer">
    <div v-if="uploadModalOpen" class="wb-drawer-mask" @click.self="closeUploadModal">
      <div class="wb-drawer wb-drawer-narrow">
        <header class="wb-drawer-head">
          <span class="wb-drawer-tag">UPLOAD · SKILL</span>
          <span class="wb-drawer-line"/>
          <button class="wb-drawer-close" :disabled="uploadingPkg" @click="closeUploadModal">×</button>
        </header>
        <div class="wb-drawer-body">
          <div class="wb-upload-title">上传技能</div>
          <div class="wb-upload-subtitle">上传前请仔细阅读以下注意事项</div>

          <ul class="wb-upload-tips">
            <li><span class="wb-upload-tips-dot">1</span><span>仅支持 <b>.zip</b> 格式，单个文件大小不超过 <b>50MB</b>。</span></li>
            <li><span class="wb-upload-tips-dot">2</span><span>压缩包根目录需包含 <b>SKILL.md</b> 描述文件，遵循平台技能包规范。</span></li>
            <li><span class="wb-upload-tips-dot">3</span><span>技能包内请勿包含恶意代码、敏感数据或与业务无关的大文件。</span></li>
            <li><span class="wb-upload-tips-dot">4</span><span>若 <b>skillKey</b> 已存在，将自动作为新版本追加；不存在则创建新技能。</span></li>
          </ul>

          <div
            class="wb-upload-drop"
            :class="{ 'is-active': uploadDragActive, 'is-busy': uploadingPkg }"
            @click="pickUploadFile"
            @dragenter="onUploadDragEnter"
            @dragover="onUploadDragOver"
            @dragleave="onUploadDragLeave"
            @drop="onUploadDrop"
          >
            <div v-if="!uploadingPkg" class="wb-upload-drop-inner">
              <div class="wb-upload-drop-icon">↥</div>
              <div class="wb-upload-drop-main">
                {{ uploadDragActive ? '松开以上传' : '将 .zip 文件拖拽到此处' }}
              </div>
              <div class="wb-upload-drop-sub">或 <span class="wb-upload-drop-link">点击选择文件</span></div>
            </div>
            <div v-else class="wb-upload-drop-inner wb-upload-drop-busy">
              <div class="wb-upload-progress">
                <div class="wb-upload-progress-bar" :style="{ width: uploadProgress + '%' }"/>
              </div>
              <div class="wb-upload-drop-main">上传中… {{ uploadProgress }}%</div>
              <div class="wb-upload-drop-sub">请勿关闭弹窗</div>
            </div>
          </div>

          <input ref="uploadPkgInputEl" type="file" accept=".zip" hidden @change="handleUploadPkgSelect"/>

          <div class="wb-drawer-actions">
            <button class="wb-drawer-cancel" :disabled="uploadingPkg" @click="closeUploadModal">
              {{ uploadingPkg ? '上传中…' : '取消' }}
            </button>
            <button class="wb-drawer-run" :disabled="uploadingPkg" @click="pickUploadFile">选择文件</button>
          </div>
        </div>
      </div>
    </div>
    </Transition>

    <!-- ─── 可见性设置弹窗 ────────────────────────────────────────── -->
    <Transition name="wb-drawer">
    <div v-if="visOpen && visTarget" class="wb-drawer-mask" @click.self="closeVisibility">
      <div class="wb-drawer wb-drawer-narrow">
        <header class="wb-drawer-head">
          <span class="wb-drawer-tag">VISIBILITY</span>
          <span class="wb-drawer-line"/>
          <button class="wb-drawer-close" @click="closeVisibility">×</button>
        </header>
        <div class="wb-drawer-body">
          <div class="wb-drawer-keyrow">
            <span class="wb-drawer-keytag">
              {{ visTarget.type === 'skill' ? '@' + visTarget.data.skillKey : visTarget.data.skillKey }}
            </span>
            <span class="wb-drawer-readonly">{{ visTarget.data.name }}</span>
          </div>

          <label class="wb-drawer-label">可见范围</label>
          <div class="wb-vis-radio-group">
            <label class="wb-vis-radio">
              <input v-model="visForm.visibility" type="radio" value="private"/>
              <span class="wb-vis-radio-text">仅自己</span>
              <span class="wb-vis-radio-hint">仅创建者本人可见，默认</span>
            </label>
            <label class="wb-vis-radio">
              <input v-model="visForm.visibility" type="radio" value="role"/>
              <span class="wb-vis-radio-text">指定角色</span>
              <span class="wb-vis-radio-hint">下方填角色 code，命中即可见</span>
            </label>
            <label class="wb-vis-radio">
              <input v-model="visForm.visibility" type="radio" value="public"/>
              <span class="wb-vis-radio-text">全员</span>
              <span class="wb-vis-radio-hint">所有登录用户可见</span>
            </label>
          </div>

          <template v-if="visForm.visibility === 'role'">
            <label class="wb-drawer-label">允许的角色 code（多个用逗号分隔）</label>
            <input
              v-model="visForm.rolesText"
              class="wb-drawer-input wb-drawer-input-line"
              placeholder="例：R_ADMIN, R_STANDARD_EDITOR"
            />
          </template>

          <div class="wb-drawer-actions">
            <button class="wb-drawer-cancel" @click="closeVisibility">取消</button>
            <button :disabled="visSaving" class="wb-drawer-run" @click="saveVisibility">
              {{ visSaving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </div>
    </Transition>

    <!-- ─── 标签：直接 inline 编辑，无弹窗 ─────────────────────── -->

    <!-- ─── 标签管理弹窗 ─────────────────────────────────────────── -->
    <Transition name="wb-drawer">
    <div v-if="tagMgrOpen" class="wb-drawer-mask" @click.self="closeTagMgr">
      <div class="wb-drawer wb-drawer-narrow">
        <header class="wb-drawer-head">
          <span class="wb-drawer-tag">TAG · MANAGE</span>
          <span class="wb-drawer-line"/>
          <button class="wb-drawer-close" @click="closeTagMgr">×</button>
        </header>
        <div class="wb-drawer-body">
          <label class="wb-drawer-label">新建标签</label>
          <div class="wb-tag-new-row">
            <input
              v-model="tagMgrNewInput"
              class="wb-drawer-input wb-drawer-input-line wb-tag-new-input"
              placeholder="输入标签名，回车或逗号分隔批量创建"
              maxlength="32"
              @keydown.enter.prevent="createTagInMgr"
            />
            <button class="wb-tag-new-btn" :disabled="!tagMgrNewInput.trim()" @click="createTagInMgr">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 5v14M5 12h14"/>
              </svg>
              <span>新建</span>
            </button>
          </div>
          <div class="wb-tag-new-hint">新建的标签需要在卡片上「+ 添加标签」中选中后才会持久化</div>

          <label class="wb-drawer-label" style="margin-top: 8px;">全部标签 · {{ allTags.length }}</label>
          <div v-if="allTags.length === 0" class="wb-tag-mgr-empty">
            <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" opacity="0.4">
              <path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z"/>
              <line x1="7" y1="7" x2="7.01" y2="7"/>
            </svg>
            <span>暂无标签</span>
          </div>
          <ul v-else class="wb-tag-mgr-list">
            <li v-for="tag in allTags" :key="tag" class="wb-tag-mgr-item"
                :class="{ 'is-draft': localExtraTags.includes(tag) }">
              <template v-if="tagMgrEditKey === tag">
                <span class="wb-tag-mgr-hash">#</span>
                <input
                  :id="`tag-mgr-input-${tag}`"
                  v-model="tagMgrEditValue"
                  class="wb-tag-mgr-input"
                  maxlength="32"
                  @keydown.enter.prevent="commitRenameInMgr"
                  @keydown.esc.prevent="cancelRenameInMgr"
                />
                <button class="wb-icon-btn wb-icon-btn-primary" title="保存" @click="commitRenameInMgr">
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="20 6 9 17 4 12"/>
                  </svg>
                </button>
                <button class="wb-icon-btn" title="取消" @click="cancelRenameInMgr">
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="6" x2="6" y2="18"/>
                    <line x1="6" y1="6" x2="18" y2="18"/>
                  </svg>
                </button>
              </template>
              <template v-else>
                <span class="wb-tag-mgr-chip"
                      :class="{ active: filterTag === tag }"
                      :title="filterTag === tag ? '点击取消筛选' : `点击只看 #${tag}`"
                      @click="filterTag = filterTag === tag ? '' : tag">
                  <span class="wb-tag-mgr-hash">#</span>{{ tag }}
                </span>
                <span class="wb-tag-mgr-count">
                  <template v-if="localExtraTags.includes(tag) && tagUsageCount(tag) === 0">草稿</template>
                  <template v-else>{{ tagUsageCount(tag) }} 处使用</template>
                </span>
                <button class="wb-icon-btn" title="重命名" @click="startRenameInMgr(tag)">
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                  </svg>
                </button>
                <button class="wb-icon-btn wb-icon-btn-danger"
                        :class="{ 'is-confirming': needConfirm(`del-tag-${tag}`) }"
                        :title="needConfirm(`del-tag-${tag}`) ? '再点确认' : '删除'"
                        @click="deleteTagInMgr(tag)">
                  <svg v-if="!needConfirm(`del-tag-${tag}`)" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="3 6 5 6 21 6"/>
                    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                    <path d="M10 11v6M14 11v6"/>
                    <path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/>
                  </svg>
                  <svg v-else viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="9"/>
                    <line x1="12" y1="8" x2="12" y2="12"/>
                    <line x1="12" y1="16" x2="12.01" y2="16"/>
                  </svg>
                </button>
              </template>
            </li>
          </ul>
        </div>
      </div>
    </div>
    </Transition>
  </div>

  <!-- 标签快选浮层（Teleport 到 body，避免被卡片 overflow:hidden 裁掉） -->
  <Teleport to="body">
    <div v-if="editingTagOwner"
         class="wb-tag-suggest-pop"
         :style="{ position: 'fixed', top: popPos.top + 'px', left: popPos.left + 'px', width: popPos.width + 'px' }"
         @mousedown.prevent>
      <div class="wb-tag-suggest-title">选择标签</div>
      <template v-if="tagSuggestions(editingTagOwner).length">
        <div class="wb-tag-suggest-chips">
          <button v-for="t in tagSuggestions(editingTagOwner)" :key="t"
                  class="wb-tag-suggest-chip"
                  @click="pickSuggestion(editingTagOwner!, t)">#{{ t }}</button>
        </div>
      </template>
      <div v-else class="wb-tag-suggest-empty">
        <span>无匹配标签</span>
        <span class="wb-tag-suggest-empty-hint">在「标签管理」里新建</span>
      </div>
      <button class="wb-tag-suggest-mgr" @click="cancelTagInline(); openTagMgr();">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 5v14M5 12h14"/>
        </svg>
        <span>新建 / 管理标签</span>
      </button>
    </div>
  </Teleport>
  </div>
</template>

<style scoped>

.wb-root {
  height: 100%;
  width: 100%;
  display: flex;
  flex-direction: column;
}

.wb-shell {
  --paper: #fbfafc;
  --paper-deep: #f1f4fa;
  --paper-soft: #ffffff;
  --ink: #0f172a;
  --ink-2: #334155;
  --ink-3: #64748b;
  --ink-4: #94a3b8;
  --rule: #e2e8f0;
  --accent: #1e40af;
  --accent-soft: #eef2ff;
  --accent-deep: #1e3a8a;
  --gold: #0891b2;
  --green: #059669;
  --red: #dc2626;

  --font-display: 'Fraunces', 'Source Han Serif SC', 'Noto Serif SC', Georgia, serif;
  --font-body: 'Manrope', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  --font-mono: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;

  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--font-body);
  overflow: hidden;
}

.wb-grain {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
}

.wb-grain::before,
.wb-grain::after {
  content: '';
  position: absolute;
  border-radius: 50%;
  filter: blur(100px);
}

.wb-grain::before {
  width: 680px;
  height: 680px;
  bottom: -220px;
  left: -180px;
  background: radial-gradient(circle, rgba(30, 64, 175, 0.32) 0%, transparent 65%);
  animation: wb-aurora-1 28s ease-in-out infinite;
}

.wb-grain::after {
  width: 600px;
  height: 600px;
  top: -180px;
  right: -140px;
  background: radial-gradient(circle, rgba(8, 145, 178, 0.3) 0%, transparent 65%);
  animation: wb-aurora-2 32s ease-in-out infinite;
}

@keyframes wb-aurora-1 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50%      { transform: translate(60px, -40px) scale(1.08); }
}

@keyframes wb-aurora-2 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50%      { transform: translate(-40px, 50px) scale(1.06); }
}

@media (prefers-reduced-motion: reduce) {
  .wb-grain::before,
  .wb-grain::after { animation: none !important; }
}

.wb-shell::after {
  content: '';
  position: absolute;
  width: 460px;
  height: 460px;
  top: 38%;
  right: 18%;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(99, 102, 241, 0.22) 0%, transparent 65%);
  filter: blur(110px);
  pointer-events: none;
  z-index: 0;
  animation: wb-aurora-3 30s ease-in-out infinite;
}

@keyframes wb-aurora-3 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50%      { transform: translate(-50px, 30px) scale(0.96); }
}

/* ─── HEADER ──────────────────────────────────────────────────────── */
.wb-header {
  position: relative;
  z-index: 2;
  border-bottom: 1px solid var(--rule);
  background: var(--paper);
  flex-shrink: 0;
}

.wb-header-inner {
  max-width: 1280px;
  margin: 0 auto;
  padding: 20px 40px;
  display: flex;
  align-items: center;
  gap: 24px;
}

.wb-brand {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
}

.wb-brand-mark {
  font-family: var(--font-display);
  font-size: 36px;
  line-height: 1;
  font-weight: 300;
  color: var(--accent);
  font-style: italic;
}

.wb-brand-title {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 18px;
  color: var(--ink);
  letter-spacing: -0.01em;
  line-height: 1.2;
}

.wb-brand-sub {
  font-family: var(--font-mono);
  font-size: 8px;
  font-weight: 500;
  color: var(--ink-3);
  letter-spacing: 0.22em;
  margin-top: 3px;
}

.wb-header-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
  flex-shrink: 0;
  flex-wrap: wrap;
}

/* toolbar 里这一组居右靠齐，但不再吃 header 的轴 */
.wb-toolbar-inner .wb-header-controls {
  margin-left: auto;
}

.wb-search-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.wb-search {
  padding: 8px 14px 8px 34px;
  border: 1px solid var(--rule);
  background: var(--paper-soft);
  font-family: var(--font-body);
  font-size: 13px;
  color: var(--ink);
  border-radius: 1px;
  outline: none;
  width: 180px;
  transition: all 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}

.wb-search:focus {
  border-color: var(--accent);
  background: #fff;
  box-shadow: 0 1px 0 var(--accent);
  width: 240px;
}

.wb-search-icon {
  position: absolute;
  left: 12px;
  font-size: 16px;
  color: var(--ink-4);
  pointer-events: none;
  font-family: var(--font-display);
  transition: color 0.28s;
}

.wb-search:focus ~ .wb-search-icon,
.wb-search-wrap:focus-within .wb-search-icon {
  color: var(--accent);
}

.wb-search::placeholder {
  color: var(--ink-4);
  font-style: italic;
  font-family: var(--font-display);
}

.wb-filter-tabs {
  position: relative;
  display: flex;
  border: 1px solid var(--rule);
  border-radius: 1px;
  overflow: hidden;
  isolation: isolate;
}

.wb-filter-thumb {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 33.333%;
  background: var(--ink);
  z-index: 0;
  transform: translateX(0);
  transition: transform 0.38s cubic-bezier(0.7, 0, 0.2, 1);
  will-change: transform;
}

.wb-filter-tabs[data-active='mine'] .wb-filter-thumb {
  transform: translateX(100%);
}

.wb-filter-tabs[data-active='shared'] .wb-filter-thumb {
  transform: translateX(200%);
}

.wb-filter-tab {
  position: relative;
  z-index: 1;
  padding: 7px 16px;
  background: transparent;
  border: none;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.12em;
  color: var(--ink-3);
  cursor: pointer;
  transition: color 0.28s cubic-bezier(0.7, 0, 0.2, 1);
  text-transform: uppercase;
}

.wb-filter-tab:hover:not(.active) {
  color: var(--ink);
}

.wb-filter-tab.active {
  color: var(--paper);
}

/* ─── MAIN ────────────────────────────────────────────────────────── */
.wb-main {
  flex: 1;
  overflow-y: auto;
  position: relative;
  z-index: 1;
}

.wb-main::-webkit-scrollbar {
  width: 6px;
}

.wb-main::-webkit-scrollbar-track {
  background: transparent;
}

.wb-main::-webkit-scrollbar-thumb {
  background: var(--rule);
  border-radius: 3px;
}

.wb-grid {
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 40px 48px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 1px;
}

/* ─── 知识库常驻入口（header 右侧） ───────────────────────────────── */
.wb-nian-entry {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  padding: 7px 13px 7px 9px;
  margin-left: auto;
  border: 1px solid rgba(30, 64, 175, 0.18);
  border-radius: 10px;
  background:
    linear-gradient(110deg, rgba(30, 64, 175, 0.06) 0%, rgba(37, 99, 235, 0.05) 35%, rgba(14, 165, 233, 0.05) 70%, rgba(8, 145, 178, 0.06) 100%),
    var(--paper-soft, #fafaf7);
  color: #1e40af;
  cursor: pointer;
  font-family: inherit;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.85),
    0 1px 2px rgba(30, 64, 175, 0.05);
  transition: border-color 0.2s ease, background 0.2s ease, box-shadow 0.22s ease, transform 0.18s ease;
}

.wb-nian-entry:hover {
  border-color: rgba(30, 64, 175, 0.36);
  background:
    linear-gradient(110deg, rgba(30, 64, 175, 0.12) 0%, rgba(37, 99, 235, 0.10) 35%, rgba(14, 165, 233, 0.10) 70%, rgba(8, 145, 178, 0.12) 100%),
    var(--paper-soft, #fafaf7);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.92),
    0 4px 14px -4px rgba(30, 64, 175, 0.28);
  transform: translateY(-1px);
}

.wb-ne-mark {
  width: 22px;
  height: 22px;
  border-radius: 7px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  color: #fff;
  font-family: var(--font-display, sans-serif);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: -0.02em;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 2px 6px -1px rgba(30, 64, 175, 0.4);
  flex-shrink: 0;
}

.wb-ne-text {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.16em;
  color: #1e3a8a;
}

.wb-ne-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 16px;
  padding: 0 5px;
  background: linear-gradient(110deg, #1e40af 0%, #0891b2 100%);
  color: #fff;
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
  border-radius: 999px;
  box-shadow: 0 1px 4px -1px rgba(30, 64, 175, 0.4);
}

/* ─── Toolbar（grid 的控制器，与 grid 边框接续） ────────────────── */
.wb-toolbar {
  max-width: 1280px;
  margin: 28px auto 0;
  padding: 0 40px;
}

.wb-toolbar-inner {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 12px 14px;
  background: var(--paper-soft, #fafaf7);
  border: 1px solid var(--rule);
  border-bottom: none;
}


/* ─── CARD ────────────────────────────────────────────────────────── */
.wb-card {
  position: relative;
  background: var(--paper-soft);
  padding: 32px 28px 24px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow: hidden;
  transition: background 0.18s;
  min-height: 200px;
  border-top: 1px solid var(--rule);
}

.wb-card::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--ink);
  transform: translateY(100%);
  transition: transform 0.38s cubic-bezier(0.22, 1, 0.36, 1);
  z-index: 0;
}

.wb-card:hover::before {
  transform: translateY(0);
}

.wb-card:hover {
  color: var(--paper);
}

.wb-card > * {
  position: relative;
  z-index: 1;
}

.wb-card-bg-text {
  position: absolute;
  bottom: -10px;
  right: 16px;
  font-family: var(--font-display);
  font-size: 96px;
  font-weight: 300;
  font-style: italic;
  color: var(--ink);
  opacity: 0.04;
  line-height: 1;
  pointer-events: none;
  z-index: 0;
  transition: opacity 0.2s, transform 0.4s cubic-bezier(0.22, 1, 0.36, 1);
  user-select: none;
  will-change: transform;
}

.wb-card:hover .wb-card-bg-text {
  opacity: 0.06;
  color: var(--paper);
}

.wb-card-icon {
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 300;
  font-style: italic;
  color: var(--accent);
  line-height: 1;
  transition: color 0.18s;
}

.wb-card:hover .wb-card-icon {
  color: var(--paper);
}

.wb-card-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.wb-card-name {
  font-family: var(--font-display);
  font-size: 20px;
  font-weight: 400;
  letter-spacing: -0.01em;
  line-height: 1.3;
  color: var(--ink);
  transition: color 0.18s;
}

.wb-card:hover .wb-card-name {
  color: var(--paper);
}

.wb-card-desc {
  font-size: 13px;
  line-height: 1.65;
  color: var(--ink-3);
  transition: color 0.18s;
}

.wb-card:hover .wb-card-desc {
  color: rgba(255, 255, 255, 0.65);
}

.wb-card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--rule);
  transition: border-color 0.18s;
}

.wb-card:hover .wb-card-footer {
  border-top-color: rgba(255, 255, 255, 0.15);
}

.wb-badge {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  padding: 3px 8px;
  border-radius: 1px;
  transition: all 0.18s;
}

.wb-badge-general {
  background: var(--accent-soft);
  color: var(--accent);
}

.wb-card:hover .wb-badge-general {
  background: rgba(255, 255, 255, 0.15);
  color: var(--paper);
}

.wb-badge-curated {
  background: var(--paper-deep);
  color: var(--ink-2);
}

.wb-card:hover .wb-badge-curated {
  background: rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.8);
}

.wb-badge-builtin {
  background: var(--paper-deep);
  color: var(--ink-2);
}

.wb-card:hover .wb-badge-builtin {
  background: rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.8);
}

.wb-badge-derived {
  background: var(--accent-soft);
  color: var(--accent);
}

.wb-card:hover .wb-badge-derived {
  background: rgba(255, 255, 255, 0.18);
  color: rgba(255, 255, 255, 0.9);
}

.wb-badge-scheduled {
  background: rgba(8, 145, 178, 0.08);
  color: var(--gold);
}

.wb-card:hover .wb-badge-scheduled {
  background: rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.8);
}

.wb-card-arrow {
  font-family: var(--font-display);
  font-size: 20px;
  color: var(--ink-4);
  transition: transform 0.25s, color 0.18s;
}

.wb-card:hover .wb-card-arrow {
  transform: translateX(6px);
  color: var(--paper);
}

.wb-card-meta {
  display: flex;
  align-items: center;
  gap: 6px;
}

.wb-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.wb-status-success {
  background: var(--green);
}

.wb-status-failed {
  background: var(--red);
}

.wb-status-running {
  background: var(--gold);
  animation: pulse-dot 1.2s ease-in-out infinite;
}

.wb-status-idle {
  background: var(--ink-4);
}

.wb-card-time {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-4);
  letter-spacing: 0.06em;
  transition: color 0.18s;
}

.wb-card:hover .wb-card-time {
  color: rgba(255, 255, 255, 0.5);
}

.wb-card:hover .wb-status-dot {
  opacity: 0.8;
}

.wb-mini-btn {
  background: transparent;
  border: 1px solid var(--rule);
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--ink-2);
  padding: 3px 9px;
  cursor: pointer;
  border-radius: 1px;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}

.wb-mini-btn:hover {
  border-color: var(--ink-2);
  color: var(--accent);
}

.wb-mini-btn-danger:hover {
  border-color: var(--red);
  color: var(--red);
}

.wb-card:hover .wb-mini-btn {
  border-color: rgba(255, 255, 255, 0.3);
  color: rgba(255, 255, 255, 0.75);
}

.wb-card:hover .wb-mini-btn:hover {
  border-color: #fff;
  color: #fff;
  background: rgba(255, 255, 255, 0.08);
}

.wb-card-at {
  font-family: var(--font-mono);
  font-size: 12.5px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--accent-deep);
  margin-right: 6px;
}

.wb-card:hover .wb-card-at {
  color: var(--gold);
}

.wb-card-separator {
  color: var(--ink-4);
  margin: 0 4px;
  font-weight: 300;
}

.wb-disabled-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  color: var(--ink-3);
  cursor: pointer;
  user-select: none;
}

.wb-disabled-toggle input {
  cursor: pointer;
  accent-color: var(--accent);
}

/* ─── TABS / 发现 / 抽屉 ─────────────────────────────────────────── */
.wb-tab-switch {
  position: relative;
  display: inline-flex;
  border: 1px solid var(--rule);
  background: var(--paper-soft);
  border-radius: 2px;
  flex-shrink: 0;
  overflow: hidden;
  isolation: isolate;
}

.wb-tab-thumb {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 50%;
  background: var(--ink);
  z-index: 0;
  transform: translateX(0);
  transition: transform 0.42s cubic-bezier(0.7, 0, 0.2, 1);
  will-change: transform;
}

.wb-tab-switch[data-active='pkg'] .wb-tab-thumb {
  transform: translateX(100%);
}

.wb-tab {
  position: relative;
  z-index: 1;
  padding: 8px 26px;
  background: transparent;
  border: none;
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.22em;
  font-weight: 700;
  color: var(--ink-3);
  cursor: pointer;
  text-transform: uppercase;
  transition: color 0.32s cubic-bezier(0.7, 0, 0.2, 1);
  min-width: 110px;
}

.wb-tab:hover:not(.active) {
  color: var(--ink);
}

.wb-tab.active {
  color: var(--paper);
}

.wb-discover-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  background: var(--accent);
  color: var(--paper);
  border: 1px solid var(--accent);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.18em;
  font-weight: 700;
  text-transform: uppercase;
  cursor: pointer;
  border-radius: 1px;
  transition: opacity 0.15s;
}

.wb-discover-btn:hover {
  opacity: 0.85;
}

.wb-card-uses {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-3);
  letter-spacing: 0.04em;
}

.wb-uses-pkg {
  padding: 1px 6px;
  background: var(--paper-deep);
  color: var(--ink-2);
  border-radius: 1px;
}

.wb-card:hover .wb-uses-pkg {
  background: rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.85);
}

.wb-card-pkg {
  border-left: 3px solid var(--gold);
}

.wb-badge-official {
  background: rgba(217, 119, 6, 0.12);
  color: var(--gold);
}

.wb-card:hover .wb-badge-official {
  background: rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.85);
}

.wb-drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.32);
  backdrop-filter: blur(2px);
  z-index: 200;
  display: flex;
  align-items: stretch;
  justify-content: flex-end;
}

/* 抽屉动画：mask 淡入、drawer 从右滑入 */
.wb-drawer-enter-active,
.wb-drawer-leave-active {
  transition: background-color 0.32s ease, backdrop-filter 0.32s ease;
}

.wb-drawer-enter-active .wb-drawer,
.wb-drawer-leave-active .wb-drawer {
  transition: transform 0.46s cubic-bezier(0.22, 1, 0.36, 1),
              box-shadow 0.46s ease,
              opacity 0.3s ease;
  will-change: transform;
}

.wb-drawer-enter-from,
.wb-drawer-leave-to {
  background-color: rgba(15, 23, 42, 0);
  backdrop-filter: blur(0);
}

.wb-drawer-enter-from .wb-drawer,
.wb-drawer-leave-to .wb-drawer {
  transform: translateX(100%);
  box-shadow: -2px 0 0 transparent, -16px 0 40px -10px rgba(15, 23, 42, 0);
  opacity: 0.85;
}

.wb-drawer {
  width: min(560px, 90vw);
  height: 100%;
  background: var(--paper-soft);
  border-left: 1px solid var(--ink);
  box-shadow: -2px 0 0 var(--accent), -16px 0 40px -10px rgba(15, 23, 42, 0.25);
  display: flex;
  flex-direction: column;
}

.wb-drawer-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--rule);
  background: var(--paper-deep);
}

.wb-drawer-tag {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.32em;
  color: var(--accent);
}

.wb-drawer-line {
  flex: 1;
  height: 1px;
  background: linear-gradient(to right, var(--accent), transparent);
  opacity: 0.4;
}

.wb-drawer-close {
  background: none;
  border: none;
  font-size: 22px;
  line-height: 1;
  color: var(--ink-3);
  cursor: pointer;
}

.wb-drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px 22px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.wb-drawer-label {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.22em;
  color: var(--ink-3);
  text-transform: uppercase;
}

.wb-drawer-input {
  border: 1px solid var(--rule);
  background: var(--paper);
  padding: 10px 14px;
  font-family: var(--font-body);
  font-size: 14px;
  line-height: 1.6;
  color: var(--ink);
  resize: vertical;
  border-radius: 1px;
  outline: none;
}

.wb-drawer-input:focus {
  border-color: var(--ink);
  box-shadow: 0 1px 0 var(--accent);
}

.wb-drawer-input-line {
  resize: none;
}

.wb-drawer-input:disabled {
  opacity: 0.6;
  background: var(--paper-deep);
  cursor: not-allowed;
}

.wb-drawer-prompt {
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.7;
  min-height: 220px;
}

.wb-drawer-keyrow {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 4px;
}

.wb-drawer-keytag {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--accent-deep);
  background: var(--accent-soft);
  padding: 2px 10px;
  border-radius: 1px;
}

.wb-drawer-readonly {
  font-family: var(--font-display);
  font-style: italic;
  font-size: 12.5px;
  color: var(--ink-3);
}

.wb-drawer-readonly-box {
  border: 1px solid var(--rule);
  background: var(--paper-deep);
  padding: 10px 14px;
  font-family: var(--font-body);
  font-size: 14px;
  line-height: 1.6;
  color: var(--ink);
  border-radius: 1px;
  white-space: pre-wrap;
  word-break: break-word;
}

.wb-drawer-prompt-view {
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.7;
  max-height: 360px;
  overflow-y: auto;
  margin: 0;
}

.wb-drawer-deps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.wb-drawer-cancel {
  background: transparent;
  border: 1px solid var(--rule);
  color: var(--ink-2);
  padding: 8px 18px;
  font-family: var(--font-body);
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.06em;
  cursor: pointer;
  border-radius: 1px;
  text-transform: uppercase;
  margin-right: 10px;
}

.wb-drawer-cancel:hover {
  border-color: var(--ink);
  color: var(--ink);
}

.wb-drawer-actions {
  display: flex;
  justify-content: flex-end;
}

.wb-drawer-run {
  background: var(--ink);
  color: var(--paper);
  border: none;
  padding: 8px 18px;
  font-family: var(--font-body);
  font-size: 12.5px;
  font-weight: 600;
  letter-spacing: 0.06em;
  cursor: pointer;
  border-radius: 1px;
  text-transform: uppercase;
}

.wb-drawer-run:disabled {
  background: var(--rule);
  color: var(--ink-4);
  cursor: not-allowed;
}

.wb-drawer-error {
  font-family: var(--font-display);
  font-style: italic;
  color: #b91c1c;
  background: #fef3f2;
  padding: 8px 12px;
  border-left: 2px solid #b91c1c;
  font-size: 13px;
}

.wb-cand-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.wb-cand-label {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.22em;
  color: var(--ink-3);
  text-transform: uppercase;
}

.wb-cand-item {
  border: 1px solid var(--rule);
  border-left: 2px solid var(--accent);
  padding: 12px 14px;
  background: var(--paper);
  border-radius: 1px;
}

.wb-cand-name {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 500;
  color: var(--ink);
  margin-bottom: 4px;
}

.wb-cand-desc {
  font-family: var(--font-body);
  font-size: 13px;
  color: var(--ink-2);
  line-height: 1.6;
  margin-bottom: 6px;
}

.wb-cand-url {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  word-break: break-all;
  margin-bottom: 10px;
}

.wb-cand-install {
  background: transparent;
  color: var(--accent);
  border: 1px solid var(--accent);
  padding: 4px 14px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.18em;
  font-weight: 700;
  text-transform: uppercase;
  cursor: pointer;
  border-radius: 1px;
}

.wb-cand-install:hover:not(:disabled) {
  background: var(--accent);
  color: var(--paper);
}

.wb-cand-install:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 通用问答卡片特殊样式 */
.wb-card-general {
  border-left: 3px solid var(--accent);
}

.wb-card-general::before {
  background: var(--accent-deep);
}

/* 停用卡片 */
.wb-card-inactive {
  opacity: 0.45;
  pointer-events: none;
}

/* 空状态 */
.wb-empty {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 80px 40px;
  background: var(--paper-soft);
  color: var(--ink-3);
  font-family: var(--font-display);
  font-style: italic;
  font-size: 15px;
}

.wb-empty-icon {
  font-size: 40px;
  font-style: normal;
  opacity: 0.3;
}

/* ─── FOOTER ──────────────────────────────────────────────────────── */
.wb-footer {
  position: relative;
  z-index: 2;
  border-top: 1px dashed var(--rule);
  padding: 12px 40px;
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--ink-4);
  letter-spacing: 0.18em;
  text-align: center;
  text-transform: uppercase;
  background: var(--paper);
  flex-shrink: 0;
}

/* ─── ANIMATIONS ─────────────────────────────────────────────────── */
@keyframes pulse-dot {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}

/* tab 切换：grid 整体淡入+轻微滑动 */
.wb-grid-swap-enter-active {
  transition: opacity 0.36s cubic-bezier(0.22, 1, 0.36, 1), transform 0.42s cubic-bezier(0.22, 1, 0.36, 1);
}

.wb-grid-swap-leave-active {
  transition: opacity 0.18s ease-out, transform 0.22s ease-out;
}

.wb-grid-swap-enter-from {
  opacity: 0;
  transform: translateY(14px);
}

.wb-grid-swap-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* 卡片渐次入场：grid 出现时，每张卡片接力浮现 */
.wb-grid-swap-enter-active .wb-card,
.wb-grid-swap-enter-active .wb-empty {
  animation: wb-card-in 0.5s cubic-bezier(0.22, 1, 0.36, 1) backwards;
}

.wb-grid-swap-enter-active .wb-card:nth-child(1)  { animation-delay: 0.02s; }
.wb-grid-swap-enter-active .wb-card:nth-child(2)  { animation-delay: 0.06s; }
.wb-grid-swap-enter-active .wb-card:nth-child(3)  { animation-delay: 0.10s; }
.wb-grid-swap-enter-active .wb-card:nth-child(4)  { animation-delay: 0.14s; }
.wb-grid-swap-enter-active .wb-card:nth-child(5)  { animation-delay: 0.18s; }
.wb-grid-swap-enter-active .wb-card:nth-child(6)  { animation-delay: 0.22s; }
.wb-grid-swap-enter-active .wb-card:nth-child(7)  { animation-delay: 0.26s; }
.wb-grid-swap-enter-active .wb-card:nth-child(8)  { animation-delay: 0.30s; }
.wb-grid-swap-enter-active .wb-card:nth-child(n+9) { animation-delay: 0.32s; }

@keyframes wb-card-in {
  from {
    opacity: 0;
    transform: translateY(10px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* ─── RESPONSIVE ─────────────────────────────────────────────────── */
@media (max-width: 768px) {
  .wb-header-inner {
    padding: 16px 20px;
    flex-wrap: wrap;
    gap: 16px;
  }

  .wb-toolbar {
    margin-top: 18px;
    padding: 0 20px;
  }

  .wb-toolbar-inner {
    flex-wrap: wrap;
  }

  .wb-toolbar-inner .wb-header-controls {
    width: 100%;
    flex-wrap: wrap;
  }

  .wb-search {
    width: 100%;
  }

  .wb-grid {
    padding: 0 20px 28px;
  }

  .wb-grid {
    grid-template-columns: 1fr;
  }

  .wb-nian-entry .wb-ne-text {
    display: none;
  }

  .wb-nian-entry {
    padding: 7px 10px 7px 7px;
  }
}

/* ── 版本管理抽屉 ─────────────────────────────────────────── */
.wb-version-actions {
  display: flex;
  gap: 8px;
  padding: 4px 0 8px;
  border-bottom: 1px dashed var(--rule);
  margin-bottom: 8px;
}

.wb-version-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.wb-version-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  background: var(--paper-soft);
  border: 1px solid var(--rule);
  border-radius: 2px;
  font-size: 12px;
}

.wb-version-item.is-active {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.wb-version-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
  flex-wrap: wrap;
}

.wb-version-num {
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--accent-deep);
}

.wb-version-active-badge {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.12em;
  padding: 1px 6px;
  background: var(--accent);
  color: var(--paper);
  border-radius: 1px;
}

.wb-version-source {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-3);
  text-transform: uppercase;
}

.wb-version-size,
.wb-version-time {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--ink-4);
}

.wb-version-ops {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

/* ── 可见性角标 + 弹窗 ─────────────────────────────────── */
.wb-card-badges {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.wb-vis-chip {
  font-family: var(--font-mono);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 2px 7px;
  border-radius: 2px;
  white-space: nowrap;
}

.wb-vis-private {
  background: var(--paper-deep);
  color: var(--ink-3);
}

.wb-vis-role {
  background: rgba(8, 145, 178, 0.12);
  color: var(--gold);
}

.wb-vis-public {
  background: var(--accent-soft);
  color: var(--accent);
}

.wb-card:hover .wb-vis-chip {
  background: rgba(255, 255, 255, 0.14);
  color: rgba(255, 255, 255, 0.88);
}

.wb-drawer-narrow {
  width: min(420px, 90vw);
}

/* ── 上传技能包弹窗 ───────────────────────────────────────── */
.wb-upload-title {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: 0.02em;
  margin-top: 4px;
}

.wb-upload-subtitle {
  font-size: 12px;
  color: var(--ink-3);
  margin: 4px 0 14px;
}

.wb-upload-tips {
  list-style: none;
  margin: 0 0 14px;
  padding: 12px 14px;
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: 2px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.wb-upload-tips li {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--ink-2);
}

.wb-upload-tips li b {
  color: var(--ink);
  font-weight: 600;
}

.wb-upload-tips-dot {
  flex: 0 0 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-soft);
  border: 1px solid var(--rule);
  border-radius: 2px;
  margin-top: 1px;
}

.wb-upload-drop {
  position: relative;
  margin-bottom: 14px;
  padding: 28px 16px;
  background: var(--paper);
  border: 1px dashed var(--ink-3);
  border-radius: 2px;
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease;
  text-align: center;
}

.wb-upload-drop:hover:not(.is-busy) {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.wb-upload-drop.is-active {
  border-color: var(--accent);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px var(--accent);
}

.wb-upload-drop.is-busy {
  cursor: progress;
  border-style: solid;
  border-color: var(--rule);
}

.wb-upload-drop-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  pointer-events: none;
}

.wb-upload-drop-icon {
  font-size: 28px;
  line-height: 1;
  color: var(--accent);
  margin-bottom: 2px;
}

.wb-upload-drop-main {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
}

.wb-upload-drop-sub {
  font-size: 12px;
  color: var(--ink-3);
}

.wb-upload-drop-link {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.wb-upload-drop-busy {
  width: 100%;
}

.wb-upload-progress {
  width: 100%;
  height: 4px;
  background: var(--paper-deep);
  border: 1px solid var(--rule);
  border-radius: 2px;
  overflow: hidden;
  margin-bottom: 8px;
}

.wb-upload-progress-bar {
  height: 100%;
  background: var(--accent);
  transition: width 0.18s ease;
}

.wb-vis-radio-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 0 8px;
}

.wb-vis-radio {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  background: var(--paper);
  border: 1px solid var(--rule);
  border-radius: 2px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.wb-vis-radio:hover {
  border-color: var(--ink-3);
  background: var(--paper-deep);
}

.wb-vis-radio:has(input:checked) {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.wb-vis-radio input {
  margin: 0;
  accent-color: var(--accent);
}

.wb-vis-radio-text {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.wb-vis-radio-hint {
  flex: 1;
  font-size: 11px;
  color: var(--ink-3);
}

/* ── 用户自定义标签 ─────────────────────────────────────── */
.wb-tag-chip {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  padding: 2px 6px;
  background: var(--paper-deep);
  color: var(--ink-2);
  border-radius: 2px;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s, color 0.15s;
}

.wb-tag-chip:hover {
  background: var(--accent-soft);
  color: var(--accent);
}

.wb-tag-chip.active {
  background: var(--accent);
  color: var(--paper);
}

.wb-card:hover .wb-tag-chip {
  background: rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.85);
}

.wb-tag-chip-editable {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 4px 4px 8px;
  font-size: 11.5px;
}

.wb-tag-chip-remove {
  background: none;
  border: none;
  color: inherit;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
}

.wb-tag-chip-remove:hover {
  color: var(--red);
}

.wb-tag-chip-suggest {
  opacity: 0.65;
}

.wb-tag-chip-suggest:hover {
  opacity: 1;
}

.wb-tag-edit-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
  padding: 8px 10px;
  background: var(--paper);
  border: 1px solid var(--rule);
  border-radius: 2px;
  min-height: 38px;
}

.wb-tag-input {
  flex: 1;
  min-width: 120px;
  border: none;
  outline: none;
  background: transparent;
  font-family: var(--font-body);
  font-size: 13px;
  color: var(--ink);
}

.wb-tag-suggest {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
  padding-top: 4px;
}

.wb-tag-suggest-label {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-3);
  letter-spacing: 0.08em;
}

/* 顶部 tag 筛选：自定义下拉，和其它按钮保持同一视觉语言 */
.wb-tag-filter {
  position: relative;
}

.wb-tag-filter-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border: 1px solid var(--rule);
  background: var(--paper-soft);
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ink-3);
  border-radius: 2px;
  cursor: pointer;
  transition: border-color 0.18s, color 0.18s, background 0.18s;
}

.wb-tag-filter-trigger:hover {
  border-color: var(--ink-3);
  color: var(--ink);
}

.wb-tag-filter-trigger.active {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}

.wb-tag-filter-trigger svg:last-child {
  opacity: 0.5;
  transition: transform 0.22s ease, opacity 0.18s;
}

.wb-tag-filter-trigger:hover svg:last-child {
  opacity: 1;
}

.wb-tag-filter-drop {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  min-width: 180px;
  max-width: 260px;
  max-height: 320px;
  overflow-y: auto;
  padding: 6px;
  background: #ffffff;
  border: 1px solid var(--rule);
  border-top: 2px solid var(--accent);
  box-shadow: 0 8px 24px -6px rgba(15, 23, 42, 0.18);
  border-radius: 3px;
  z-index: 50;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.wb-tag-filter-item {
  text-align: left;
  padding: 6px 10px;
  background: transparent;
  border: none;
  border-radius: 2px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-2);
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.wb-tag-filter-item:hover {
  background: var(--paper-deep);
  color: var(--ink);
}

.wb-tag-filter-item.active {
  background: var(--accent);
  color: #ffffff;
}

/* 下拉动画 */
.wb-dropdown-enter-active,
.wb-dropdown-leave-active {
  transition: opacity 0.18s ease, transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
  transform-origin: top right;
}

.wb-dropdown-enter-from,
.wb-dropdown-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.96);
}

/* ── 卡片右上角操作浮层 ───────────────────────────────────── */
.wb-card-actions {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 3;
  display: inline-flex;
  gap: 2px;
  padding: 4px;
  background: rgba(255, 255, 255, 0.97);
  border: 1px solid var(--rule);
  border-radius: 3px;
  box-shadow: 0 4px 12px -4px rgba(15, 23, 42, 0.14);
  opacity: 0;
  transform: translateY(-4px);
  pointer-events: none;
  transition: opacity 0.18s, transform 0.18s;
  /* 不受卡片 hover 的 color: var(--paper) 继承影响 */
  color: var(--ink-2);
}

.wb-card:hover .wb-card-actions {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
  color: var(--ink-2);
}

.wb-icon-btn {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: 2px;
  cursor: pointer;
  color: var(--ink-2);
  font-size: 14px;
  line-height: 1;
  transition: background 0.12s, color 0.12s;
}

.wb-icon-btn:hover {
  background: var(--paper-deep);
  color: var(--accent);
}

.wb-icon-btn-danger:hover {
  background: #fef2f2;
  color: var(--red);
}

/* ── 紧凑文字操作按钮（卡片右上角浮层用）── */
.wb-act-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 4px 8px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--ink-2);
  border-radius: 2px;
  line-height: 1;
  transition: background 0.12s, color 0.12s;
}

.wb-act-btn:hover {
  background: var(--accent-soft);
  color: var(--accent);
}

.wb-act-btn-danger:hover {
  background: #fef2f2;
  color: var(--red);
}

/* Inline 二次确认态：红色实色高亮 + 脉冲 */
.wb-act-btn.is-confirming,
.wb-mini-btn.is-confirming {
  background: var(--red);
  color: #fff;
  border-color: var(--red);
  animation: wb-confirm-pulse 1.2s ease-in-out infinite;
}

.wb-act-btn.is-confirming:hover,
.wb-mini-btn.is-confirming:hover {
  background: #b91c1c;
  color: #fff;
  border-color: #b91c1c;
}

.wb-icon-btn.is-confirming {
  background: var(--red);
  color: #fff;
  border-color: var(--red);
  animation: wb-confirm-pulse 1.2s ease-in-out infinite;
}

.wb-icon-btn.is-confirming:hover {
  background: #b91c1c;
  color: #fff;
  border-color: #b91c1c;
}

@keyframes wb-confirm-pulse {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.35);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(220, 38, 38, 0);
  }
}

/* ── 标签快选浮层（Teleport 到 body，脱离 .wb-shell，必须用硬编码颜色） ── */
.wb-tag-suggest-pop {
  z-index: 9999;
  min-width: 220px;
  max-width: 320px;
  max-height: 280px;
  overflow-y: auto;
  padding: 8px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-top: 2px solid #1e40af;
  box-shadow: 0 8px 24px -6px rgba(15, 23, 42, 0.18);
  border-radius: 3px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-family: 'Manrope', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  color: #0f172a;
}

.wb-tag-suggest-title {
  font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.2em;
  color: #94a3b8;
  text-transform: uppercase;
  padding: 2px 2px 6px;
  border-bottom: 1px dashed #e2e8f0;
}

.wb-tag-suggest-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 2px 0;
}

.wb-tag-suggest-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 16px 8px;
  font-family: 'Fraunces', 'Source Han Serif SC', 'Noto Serif SC', Georgia, serif;
  font-style: italic;
  font-size: 12.5px;
  color: #64748b;
}

.wb-tag-suggest-empty-hint {
  font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
  font-style: normal;
  font-size: 10px;
  color: #94a3b8;
  letter-spacing: 0.06em;
}

.wb-tag-suggest-chip {
  background: #eef2f7;
  border: 1px solid transparent;
  color: #334155;
  font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 2px;
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.wb-tag-suggest-chip:hover {
  background: #1e40af;
  color: #f5f6f8;
  border-color: #1e40af;
}

.wb-tag-suggest-mgr {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  margin-top: 2px;
  padding: 7px 8px;
  background: transparent;
  border: none;
  border-top: 1px dashed #e2e8f0;
  font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #64748b;
  cursor: pointer;
  border-radius: 0;
  transition: color 0.12s, background 0.12s;
}

.wb-tag-suggest-mgr:hover {
  background: #eef2f7;
  color: #1e40af;
}

.wb-tag-input-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}

/* ── tag bar（卡片底部）───────────────────────────────────── */
.wb-card-tagbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-height: 22px;
  width: 100%;
}

/* 增强：tag chip 内置 × 删除按钮 */
.wb-tag-chip {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding-right: 6px;
}

.wb-tag-chip-x {
  width: 14px;
  height: 14px;
  display: none;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  color: inherit;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  margin-left: 2px;
  border-radius: 2px;
  opacity: 0.6;
}

.wb-tag-chip:hover .wb-tag-chip-x {
  display: inline-flex;
}

.wb-tag-chip-x:hover {
  opacity: 1;
  background: rgba(220, 38, 38, 0.12);
  color: var(--red);
}

/* "+ 添加标签" 完整按钮（无标签时） */
.wb-tag-add-empty {
  background: transparent;
  border: 1px dashed var(--rule);
  color: var(--ink-3);
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 2px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}

.wb-tag-add-empty:hover {
  border-color: var(--accent);
  color: var(--accent);
  border-style: solid;
}

.wb-card:hover .wb-tag-add-empty {
  border-color: rgba(255, 255, 255, 0.3);
  color: rgba(255, 255, 255, 0.65);
}

.wb-card:hover .wb-tag-add-empty:hover {
  border-color: rgba(255, 255, 255, 0.7);
  color: #fff;
}

/* 末尾 "+" 小按钮（有标签时） */
.wb-tag-add-plus {
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px dashed var(--rule);
  color: var(--ink-3);
  font-size: 14px;
  line-height: 1;
  font-weight: 500;
  padding: 0;
  border-radius: 2px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, border-color 0.15s, color 0.15s;
}

.wb-card:hover .wb-tag-add-plus {
  opacity: 1;
}

.wb-tag-add-plus:hover {
  border-color: var(--accent);
  color: var(--accent);
  border-style: solid;
  opacity: 1;
}

.wb-card:hover .wb-tag-add-plus {
  border-color: rgba(255, 255, 255, 0.3);
  color: rgba(255, 255, 255, 0.7);
}

.wb-card:hover .wb-tag-add-plus:hover {
  border-color: #fff;
  color: #fff;
}

/* inline tag 输入框 */
.wb-tag-input-inline {
  border: 1px solid var(--accent);
  background: var(--paper-soft);
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--ink);
  padding: 2px 6px;
  border-radius: 2px;
  outline: none;
  width: 110px;
}

.wb-tag-input-inline::placeholder {
  color: var(--ink-4);
}

/* ── 标签管理弹窗 ─────────────────────────────────────── */
.wb-tag-new-row {
  display: flex;
  gap: 8px;
  align-items: stretch;
}

.wb-tag-new-input {
  flex: 1;
}

.wb-tag-new-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 14px;
  background: var(--ink);
  color: var(--paper);
  border: 1px solid var(--ink);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  cursor: pointer;
  border-radius: 1px;
  transition: background 0.15s, opacity 0.15s;
  white-space: nowrap;
}

.wb-tag-new-btn:hover:not(:disabled) {
  background: var(--accent);
  border-color: var(--accent);
}

.wb-tag-new-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.wb-tag-new-hint {
  font-family: var(--font-display);
  font-style: italic;
  font-size: 11.5px;
  color: var(--ink-4);
  line-height: 1.5;
  padding: 0 2px;
}

.wb-tag-mgr-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 40px 20px;
  color: var(--ink-3);
  font-family: var(--font-display);
  font-style: italic;
  font-size: 13px;
}

.wb-tag-mgr-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.wb-tag-mgr-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--paper-soft);
  border: 1px solid var(--rule);
  border-radius: 2px;
  transition: border-color 0.15s, background 0.15s;
}

.wb-tag-mgr-item:hover {
  border-color: var(--ink-3);
  background: var(--paper-deep);
}

.wb-tag-mgr-item.is-draft {
  border-style: dashed;
  background: repeating-linear-gradient(
    -45deg,
    var(--paper-soft),
    var(--paper-soft) 6px,
    var(--paper-deep) 6px,
    var(--paper-deep) 7px
  );
}

.wb-tag-mgr-hash {
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--accent);
  opacity: 0.7;
}

.wb-tag-mgr-chip {
  flex: 1;
  display: inline-flex;
  align-items: center;
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  cursor: pointer;
  min-width: 0;
  word-break: break-all;
  padding: 2px 0;
}

.wb-tag-mgr-chip:hover {
  color: var(--accent);
}

.wb-tag-mgr-chip:hover .wb-tag-mgr-hash {
  opacity: 1;
}

.wb-tag-mgr-chip.active {
  color: var(--accent);
}

.wb-tag-mgr-chip.active .wb-tag-mgr-hash {
  opacity: 1;
}

.wb-tag-mgr-count {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--ink-4);
  white-space: nowrap;
  padding: 0 2px;
}

.wb-tag-mgr-input {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--accent);
  background: var(--paper-soft);
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  padding: 4px 8px;
  border-radius: 2px;
  outline: none;
}

/* 图标按钮：紧凑、低视觉负担 */
.wb-icon-btn {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 2px;
  cursor: pointer;
  color: var(--ink-3);
  padding: 0;
  flex-shrink: 0;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
}

.wb-icon-btn:hover {
  background: var(--paper-soft);
  border-color: var(--ink-3);
  color: var(--accent);
}

.wb-icon-btn-primary {
  color: var(--accent);
}

.wb-icon-btn-primary:hover {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}

.wb-icon-btn-danger:hover {
  background: #fef2f2;
  border-color: var(--red);
  color: var(--red);
}
</style>
