<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { NSwitch, NTooltip, NPopconfirm } from 'naive-ui';
import {
  fetchUpdateAgentSkill,
  fetchUpdateAgentSkillVisibility,
  fetchUpdateAgentSkillTags,
  fetchSetAgentSkillSource,
  fetchAgentSkillVersions,
  fetchActivateAgentSkillVersion,
  fetchDeleteAgentSkillVersion
} from '@/service/api';
import type { AgentSkill, AgentSkillVersion, AgentVisibility } from '@/service/api';

const props = defineProps<{
  skill: AgentSkill;
  myUserId: number | null;
  isAdmin: boolean;
}>();

const emit = defineEmits<{
  changed: [skill: AgentSkill];
  use: [skill: AgentSkill];
  aiEdit: [skill: AgentSkill];
  download: [skill: AgentSkill];
  remove: [skill: AgentSkill];
}>();

const SOURCE_THEME: Record<AgentSkill['source'], { ca: string; ca2: string; label: string }> = {
  official: { ca: '#d97706', ca2: '#f59e0b', label: '官方' },
  derived: { ca: '#0891b2', ca2: '#22d3ee', label: '凝练' },
  curated: { ca: '#0ea5e9', ca2: '#38bdf8', label: '收录' },
  builtin: { ca: '#64748b', ca2: '#94a3b8', label: '内置' }
};
const theme = computed(() => SOURCE_THEME[props.skill.source]);
const heroStyle = computed(() => ({ '--ca': theme.value.ca, '--ca2': theme.value.ca2 }));
const initial = computed(() => (props.skill.name || props.skill.skillKey || '?').slice(0, 1));

const canManage = computed(
  () => props.isAdmin || (props.myUserId != null && props.skill.userId === props.myUserId)
);

// ── 编辑（仅 name / description；skill_md 即 SKILL.md 主文件，不允许手改）──
const editing = ref(false);
const saving = ref(false);
const editForm = ref({ name: '', description: '' });

function startEdit() {
  editForm.value = {
    name: props.skill.name,
    description: props.skill.description || ''
  };
  editing.value = true;
}
async function saveEdit() {
  if (!editForm.value.name.trim()) {
    window.$message?.error('名称不能为空');
    return;
  }
  saving.value = true;
  try {
    const { data, error } = await fetchUpdateAgentSkill(props.skill.id, {
      name: editForm.value.name.trim(),
      description: editForm.value.description.trim()
    });
    if (!error && data) {
      emit('changed', data);
      editing.value = false;
      window.$message?.success('已保存');
    } else {
      window.$message?.error('保存失败');
    }
  } finally {
    saving.value = false;
  }
}

// ── 启用开关 ─────────────────────────────────────────────
async function onToggle(val: boolean) {
  const { data, error } = await fetchUpdateAgentSkill(props.skill.id, { is_enabled: val });
  if (!error && data) emit('changed', data);
  else window.$message?.error('切换失败');
}

// ── 官方标记（仅超管）：key 会随来源实时联动（官方去掉用户名前缀） ──
async function toggleOfficial() {
  const next = props.skill.source === 'official' ? 'curated' : 'official';
  const { data, error } = await fetchSetAgentSkillSource(props.skill.id, next);
  if (!error && data) {
    emit('changed', data);
    window.$message?.success(next === 'official' ? '已设为官方技能' : '已取消官方标记');
  } else {
    window.$message?.error('操作失败');
  }
}

// ── 可见性 ───────────────────────────────────────────────
const visSaving = ref(false);
const roleCodesText = ref((props.skill.allowedRoleCodes || []).join(', '));
const visOptions: { label: string; value: AgentVisibility; hint: string }[] = [
  { label: '仅自己', value: 'private', hint: '只有你可见可用' },
  { label: '指定角色', value: 'role', hint: '拥有下列角色码的用户可见' },
  { label: '全员', value: 'public', hint: '所有用户可见可用' }
];
async function setVisibility(v: AgentVisibility) {
  visSaving.value = true;
  try {
    const payload: { visibility: AgentVisibility; allowed_role_codes?: string[] } = { visibility: v };
    if (v === 'role') {
      payload.allowed_role_codes = roleCodesText.value
        .split(/[,，\s]+/)
        .map(s => s.trim())
        .filter(Boolean);
    }
    const { data, error } = await fetchUpdateAgentSkillVisibility(props.skill.id, payload);
    if (!error && data) {
      emit('changed', data);
      window.$message?.success('可见性已更新');
    } else {
      window.$message?.error('更新失败');
    }
  } finally {
    visSaving.value = false;
  }
}

// ── 标签 ─────────────────────────────────────────────────
const tagInput = ref('');
async function addTag() {
  const t = tagInput.value.trim();
  if (!t) return;
  if ((props.skill.tags || []).includes(t)) {
    tagInput.value = '';
    return;
  }
  const next = [...(props.skill.tags || []), t];
  const { data, error } = await fetchUpdateAgentSkillTags(props.skill.id, next);
  if (!error && data) {
    emit('changed', data);
    tagInput.value = '';
  }
}
async function removeTag(t: string) {
  const next = (props.skill.tags || []).filter(x => x !== t);
  const { data, error } = await fetchUpdateAgentSkillTags(props.skill.id, next);
  if (!error && data) emit('changed', data);
}

// ── 版本 ─────────────────────────────────────────────────
const versions = ref<AgentSkillVersion[]>([]);
const versionsLoading = ref(false);
async function loadVersions() {
  versionsLoading.value = true;
  try {
    const { data, error } = await fetchAgentSkillVersions(props.skill.id);
    if (!error && data) versions.value = data;
  } finally {
    versionsLoading.value = false;
  }
}
async function activateVersion(v: AgentSkillVersion) {
  if (v.isActive) return;
  const { data, error } = await fetchActivateAgentSkillVersion(props.skill.id, v.version);
  if (!error && data) {
    emit('changed', data);
    await loadVersions();
    window.$message?.success(`已切换到版本 ${v.version}`);
  } else {
    window.$message?.error('切换失败');
  }
}
function removeVersion(v: AgentSkillVersion) {
  if (v.isActive) return;
  window.$dialog?.warning({
    title: '删除版本',
    content: `确定删除版本 ${v.version} 吗？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      const { error } = await fetchDeleteAgentSkillVersion(props.skill.id, v.version);
      if (!error) {
        window.$message?.success('已删除');
        await loadVersions();
      } else {
        window.$message?.error('删除失败');
      }
    }
  });
}

// ── 格式化 ───────────────────────────────────────────────
function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function fmtDate(ts: number | null): string {
  if (!ts) return '—';
  const d = new Date(ts);
  const p = (n: number) => n.toString().padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

watch(
  () => props.skill.id,
  () => {
    editing.value = false;
    roleCodesText.value = (props.skill.allowedRoleCodes || []).join(', ');
    loadVersions();
  },
  { immediate: true }
);
</script>

<template>
  <div class="sd">
    <div class="sd-scroll">
      <!-- 顶部概览 -->
      <section class="sd-hero" :style="heroStyle">
        <span class="sd-hero-ghost">@</span>
        <span class="sd-hero-avatar">{{ initial }}</span>
        <div class="sd-hero-main">
          <h2 class="sd-name">{{ skill.name }}</h2>
          <code class="sd-key">@{{ skill.skillKey }}</code>
          <div class="sd-hero-badges">
            <span class="sd-badge sd-badge-src">{{ theme.label }}</span>
            <span class="sd-badge sd-badge-vis">
              {{ skill.visibility === 'public' ? '全员' : skill.visibility === 'role' ? '指定角色' : '仅自己' }}
            </span>
            <span v-if="skill.version" class="sd-badge sd-badge-ver">v{{ skill.version }}</span>
            <span v-if="skill.hasFiles" class="sd-badge">
              <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 3h5l3 3v7a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" stroke-width="1.4" stroke-linejoin="round" /><path d="M9 3v3h3" stroke-width="1.4" stroke-linejoin="round" /></svg>
              {{ skill.fileCount }} 文件
            </span>
          </div>
          <p class="sd-desc">{{ skill.description || '暂无描述' }}</p>
        </div>
        <div class="sd-hero-side">
          <label class="sd-enable">
            <span>{{ skill.isEnabled ? '已启用' : '已停用' }}</span>
            <NSwitch :value="skill.isEnabled" :disabled="!canManage" @update:value="onToggle" />
          </label>
          <button class="sd-use" @click="emit('use', skill)">在对话中使用</button>
          <div class="sd-meta">
            <div><span>创建</span><em>{{ fmtDate(skill.createdAt) }}</em></div>
            <div><span>更新</span><em>{{ fmtDate(skill.updatedAt) }}</em></div>
          </div>
        </div>
      </section>

      <!-- SKILL.md 主文件 -->
      <section class="sd-block">
        <div class="sd-block-head">
          <span class="sd-eyebrow">SKILL.MD</span>
          <span class="sd-block-line" />
          <button v-if="canManage && !editing" class="sd-link" @click="startEdit">编辑</button>
        </div>

        <div class="sd-prompt-view">{{ skill.skillMd || '（空）' }}</div>

        <div v-if="editing" class="sd-prompt-edit">
          <label class="sd-field">
            <span>名称</span>
            <input v-model="editForm.name" class="sd-input" />
          </label>
          <label class="sd-field">
            <span>描述</span>
            <input v-model="editForm.description" class="sd-input" placeholder="一句话说明这个技能做什么" />
          </label>
          <div class="sd-edit-actions">
            <button class="sd-btn" @click="editing = false">取消</button>
            <button class="sd-btn sd-btn-primary" :disabled="saving" @click="saveEdit">
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </section>

      <!-- 标签 -->
      <section class="sd-block">
        <div class="sd-block-head">
          <span class="sd-eyebrow">TAGS</span>
          <span class="sd-block-line" />
        </div>
        <p class="sd-tag-hint">
          标签是给技能贴的分类标记：贴上后就能在技能库顶部按标签一键筛选、快速定位同类技能。技能越多越能体现价值——自己整理起来更顺手，团队成员也更容易按主题发现彼此共享的技能。建议用简短的主题词，如「去重」「报告」「写作」。
        </p>
        <div class="sd-tags">
          <span v-for="t in skill.tags || []" :key="t" class="sd-tag">
            # {{ t }}
            <button v-if="canManage" class="sd-tag-x" @click="removeTag(t)">×</button>
          </span>
          <span v-if="!(skill.tags || []).length" class="sd-tags-empty">暂无标签</span>
          <input
            v-if="canManage"
            v-model="tagInput"
            class="sd-tag-input"
            placeholder="＋ 添加标签"
            @keydown.enter.prevent="addTag"
          />
        </div>
      </section>

      <!-- 可见性 -->
      <section v-if="canManage" class="sd-block">
        <div class="sd-block-head">
          <span class="sd-eyebrow">VISIBILITY</span>
          <span class="sd-block-line" />
        </div>
        <div class="sd-vis">
          <button
            v-for="opt in visOptions"
            :key="opt.value"
            class="sd-vis-opt"
            :class="{ 'sd-vis-active': skill.visibility === opt.value }"
            :disabled="visSaving"
            @click="setVisibility(opt.value)"
          >
            <span class="sd-vis-label">{{ opt.label }}</span>
            <span class="sd-vis-hint">{{ opt.hint }}</span>
          </button>
        </div>
        <div v-if="skill.visibility === 'role'" class="sd-vis-roles">
          <input
            v-model="roleCodesText"
            class="sd-input"
            placeholder="角色码，用逗号分隔，如 R_USER, R_EDITOR"
            @keydown.enter.prevent="setVisibility('role')"
          />
          <button class="sd-btn" :disabled="visSaving" @click="setVisibility('role')">应用角色</button>
        </div>
      </section>

      <!-- 版本 -->
      <section class="sd-block">
        <div class="sd-block-head">
          <span class="sd-eyebrow">VERSIONS</span>
          <span class="sd-block-line" />
          <span v-if="versions.length" class="sd-count">{{ versions.length }}</span>
        </div>
        <div v-if="versionsLoading" class="sd-empty-sm">加载中…</div>
        <div v-else-if="!versions.length" class="sd-empty-sm">暂无版本记录（仅文件型技能有版本）</div>
        <div v-else class="sd-versions">
          <div
            v-for="v in versions"
            :key="v.version"
            class="sd-version"
            :class="{ 'sd-version-active': v.isActive }"
          >
            <div class="sd-version-main">
              <code class="sd-version-no">v{{ v.version }}</code>
              <span v-if="v.isActive" class="sd-version-tag">当前</span>
              <span class="sd-version-meta">{{ v.fileCount }} 文件 · {{ fmtSize(v.size) }}</span>
            </div>
            <div v-if="canManage" class="sd-version-actions">
              <button v-if="!v.isActive" class="sd-link" @click="activateVersion(v)">激活</button>
              <button v-if="!v.isActive" class="sd-link sd-link-danger" @click="removeVersion(v)">删除</button>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- 底部操作行 -->
    <div class="sd-foot">
      <!-- AI 编辑对所有人可见：无权限时 agent 会引导「基于此技能新建自己的技能」 -->
      <NTooltip trigger="hover" :delay="300">
        <template #trigger>
          <button class="sd-btn" @click="emit('aiEdit', skill)">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 1.5l1.6 3.9 4.2.4-3.2 2.7.9 4.1L8 10.2l-3.5 2.4.9-4.1L2.2 5.8l4.2-.4L8 1.5z" stroke-width="1.3" stroke-linejoin="round" /></svg>
            AI 编辑
          </button>
        </template>
        在对话中插入「@编辑 @key」，让 AI 帮你改写这个技能
      </NTooltip>
      <NTooltip v-if="skill.hasFiles" trigger="hover" :delay="300">
        <template #trigger>
          <button class="sd-btn" @click="emit('download', skill)">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 2v9M4.5 8L8 11.5 11.5 8" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 13.5h10" stroke-width="1.6" stroke-linecap="round" /></svg>
            下载技能包
          </button>
        </template>
        导出为 zip，可分享给他人上传使用
      </NTooltip>
      <button v-if="isAdmin && skill.source !== 'builtin'" class="sd-btn sd-btn-official" @click="toggleOfficial">
        {{ skill.source === 'official' ? '取消官方' : '设为官方' }}
      </button>
      <NPopconfirm
        v-if="canManage && skill.source !== 'builtin'"
        positive-text="删除"
        negative-text="取消"
        @positive-click="emit('remove', skill)"
      >
        <template #default>确定删除此技能吗？此操作不可恢复。</template>
        <template #trigger>
          <button class="sd-btn sd-btn-danger">删除技能</button>
        </template>
      </NPopconfirm>
    </div>
  </div>
</template>

<style scoped>
.sd {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.sd-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 0 24px 12px;
  min-height: 0;
}
.sd-scroll::-webkit-scrollbar {
  width: 5px;
}
.sd-scroll::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.12);
  border-radius: 3px;
}

/* 概览 */
.sd-hero {
  position: relative;
  display: flex;
  gap: 20px;
  padding: 6px 0 22px;
  border-bottom: 1px solid rgba(30, 64, 175, 0.08);
  margin-bottom: 20px;
  overflow: hidden;
}
.sd-hero-ghost {
  position: absolute;
  top: -30px;
  right: 150px;
  font-family: var(--font-display, 'Plus Jakarta Sans', sans-serif);
  font-size: 110px;
  font-weight: 800;
  letter-spacing: -0.04em;
  color: rgba(30, 64, 175, 0.05);
  pointer-events: none;
  user-select: none;
  line-height: 1;
}
.sd-hero-avatar {
  flex-shrink: 0;
  width: 56px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 15px;
  color: #fff;
  font-size: 24px;
  font-weight: 700;
  background: linear-gradient(135deg, var(--ca), var(--ca2));
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 6px 16px -4px color-mix(in srgb, var(--ca) 45%, transparent);
}
.sd-hero-main {
  flex: 1;
  min-width: 0;
}
.sd-name {
  font-family: var(--font-display, 'Plus Jakarta Sans', sans-serif);
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -0.02em;
  margin: 0 0 7px;
  background: linear-gradient(110deg, #1e40af 0%, #0891b2 100%);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  color: transparent;
}
.sd-key {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 600;
  color: var(--ca);
  background: color-mix(in srgb, var(--ca) 9%, transparent);
  padding: 2px 9px;
  border-radius: 5px;
}
.sd-hero-badges {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 13px 0 12px;
}
.sd-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 20px;
  background: rgba(148, 163, 184, 0.14);
  color: var(--ink-3, #64748b);
}
.sd-badge-src {
  background: color-mix(in srgb, var(--ca) 12%, transparent);
  color: var(--ca);
}
.sd-badge-vis {
  background: rgba(8, 145, 178, 0.1);
  color: #0891b2;
}
.sd-badge-ver {
  font-family: 'JetBrains Mono', monospace;
  background: rgba(37, 99, 235, 0.1);
  color: #2563eb;
}
.sd-desc {
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--ink-3, #64748b);
  margin: 0;
}
.sd-hero-side {
  width: 210px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sd-enable {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-2, #334155);
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 9px;
  padding: 9px 12px;
}
.sd-use {
  font-family: inherit;
  font-size: 13px;
  font-weight: 700;
  color: #fff;
  background: linear-gradient(135deg, #2563eb, #1e40af);
  border: none;
  border-radius: 9px;
  padding: 10px;
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
  box-shadow: 0 4px 14px -4px rgba(37, 99, 235, 0.4);
}
.sd-use:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 18px -4px rgba(37, 99, 235, 0.5);
}
.sd-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sd-meta div {
  display: flex;
  justify-content: space-between;
  font-size: 11.5px;
}
.sd-meta span {
  color: var(--ink-4, #94a3b8);
}
.sd-meta em {
  font-family: 'JetBrains Mono', monospace;
  font-style: normal;
  color: var(--ink-3, #64748b);
}

/* 通用块 */
.sd-block {
  margin-bottom: 24px;
}
.sd-block-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.sd-eyebrow {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: var(--ink-3, #64748b);
}
.sd-block-line {
  flex: 1;
  height: 1px;
  background: rgba(30, 64, 175, 0.08);
}
.sd-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
}
.sd-tag-hint {
  font-size: 12px;
  line-height: 1.75;
  color: var(--ink-4, #94a3b8);
  margin: 0 0 10px;
}
.sd-link {
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--accent, #1e40af);
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 4px;
}
.sd-link:hover {
  text-decoration: underline;
}
.sd-link-danger {
  color: #dc2626;
}

/* Prompt 查看 */
.sd-prompt-view {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--ink-2, #334155);
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 10px;
  padding: 16px;
  max-height: 340px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.sd-prompt-view::-webkit-scrollbar {
  width: 5px;
}
.sd-prompt-view::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.12);
  border-radius: 3px;
}

/* Prompt 编辑 */
.sd-prompt-edit {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sd-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.sd-field > span {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3, #64748b);
}
.sd-input {
  font-family: inherit;
  font-size: 13px;
  color: var(--ink, #0f172a);
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 8px;
  padding: 9px 12px;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.sd-input:focus {
  border-color: rgba(37, 99, 235, 0.4);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.08);
}
.sd-textarea {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ink, #0f172a);
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 8px;
  padding: 12px;
  outline: none;
  resize: vertical;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.sd-textarea:focus {
  border-color: rgba(37, 99, 235, 0.4);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.08);
}
.sd-edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* 标签 */
.sd-tags {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
  align-items: center;
}
.sd-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11.5px;
  font-weight: 600;
  color: #b45309;
  background: rgba(217, 119, 6, 0.08);
  border: 1px solid rgba(217, 119, 6, 0.22);
  padding: 3px 9px;
  border-radius: 20px;
}
.sd-tag-x {
  font-size: 13px;
  line-height: 1;
  color: inherit;
  background: none;
  border: none;
  cursor: pointer;
  opacity: 0.6;
}
.sd-tag-x:hover {
  opacity: 1;
}
.sd-tags-empty {
  font-size: 12px;
  color: var(--ink-4, #94a3b8);
}
.sd-tag-input {
  font-family: inherit;
  font-size: 12px;
  color: var(--ink, #0f172a);
  background: transparent;
  border: 1px dashed rgba(30, 64, 175, 0.2);
  border-radius: 5px;
  padding: 3px 9px;
  outline: none;
  width: 110px;
}
.sd-tag-input:focus {
  border-color: rgba(37, 99, 235, 0.4);
}

/* 可见性 */
.sd-vis {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.sd-vis-opt {
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
  font-family: inherit;
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 10px;
  padding: 12px 14px;
  cursor: pointer;
  transition: all 0.15s;
}
.sd-vis-opt:hover:not(:disabled) {
  border-color: rgba(37, 99, 235, 0.3);
}
.sd-vis-active {
  border-color: rgba(37, 99, 235, 0.5);
  background: rgba(37, 99, 235, 0.06);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.08);
}
.sd-vis-label {
  font-size: 13.5px;
  font-weight: 700;
  color: var(--ink, #0f172a);
}
.sd-vis-hint {
  font-size: 11.5px;
  color: var(--ink-4, #94a3b8);
}
.sd-vis-roles {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
.sd-vis-roles .sd-input {
  flex: 1;
}

/* 版本 */
.sd-versions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sd-version {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 9px;
  padding: 10px 14px;
}
.sd-version-active {
  border-color: rgba(5, 150, 105, 0.3);
  background: rgba(5, 150, 105, 0.04);
}
.sd-version-main {
  display: flex;
  align-items: center;
  gap: 10px;
}
.sd-version-no {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink-2, #334155);
}
.sd-version-tag {
  font-size: 10.5px;
  font-weight: 700;
  color: #059669;
  background: rgba(5, 150, 105, 0.1);
  padding: 2px 8px;
  border-radius: 20px;
}
.sd-version-meta {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11.5px;
  color: var(--ink-4, #94a3b8);
}
.sd-version-actions {
  display: flex;
  gap: 12px;
}
.sd-empty-sm {
  font-size: 12.5px;
  color: var(--ink-4, #94a3b8);
  padding: 8px 2px;
}

/* 底部操作 */
.sd-foot {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 24px;
  border-top: 1px solid rgba(30, 64, 175, 0.08);
  background: var(--surface, rgba(255, 255, 255, 0.42));
}
.sd-btn {
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 8px 16px;
  border-radius: 9px;
  border: 1px solid rgba(30, 64, 175, 0.14);
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  color: var(--ink-2, #334155);
  cursor: pointer;
  transition: all 0.15s;
}
.sd-btn:hover:not(:disabled) {
  background: rgba(30, 64, 175, 0.05);
}
.sd-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.sd-btn-primary {
  background: linear-gradient(135deg, #2563eb, #1e40af);
  border-color: transparent;
  color: #fff;
}
.sd-btn-primary:hover:not(:disabled) {
  background: linear-gradient(135deg, #1d4ed8, #1e3a8a);
}
.sd-btn-official {
  color: #b45309;
  border-color: rgba(217, 119, 6, 0.3);
  margin-left: auto;
}
.sd-btn-official:hover:not(:disabled) {
  background: rgba(217, 119, 6, 0.08);
}
.sd-btn-danger {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.16);
  margin-left: auto;
}
.sd-btn-danger:hover:not(:disabled) {
  background: rgba(220, 38, 38, 0.06);
}
/* 官方按钮已占据靠右位置时，删除按钮紧随其后 */
.sd-btn-official + .sd-btn-danger {
  margin-left: 0;
}

@media (max-width: 680px) {
  .sd-hero {
    flex-direction: column;
  }
  .sd-hero-side {
    width: 100%;
  }
  .sd-vis {
    grid-template-columns: 1fr;
  }
}
</style>
