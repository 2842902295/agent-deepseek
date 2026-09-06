<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { NSwitch, NTooltip, NPopconfirm } from 'naive-ui';
import {
  fetchUpdateAgentSkill,
  fetchAgentSkillVersions,
  fetchActivateAgentSkillVersion,
  fetchDeleteAgentSkillVersion,
  fetchBatchAgentSkillPrefs,
  fetchSkillExamples,
  fetchUpdateSkillExample,
  fetchDeleteSkillExample
} from '@/service/api';
import type { AgentSkill, AgentSkillVersion, SkillExample, RoleTier } from '@/service/api';
import SkillExampleModal from './SkillExampleModal.vue';
import IconPicker from './IconPicker.vue';
import TierMultiSelect from './TierMultiSelect.vue';
import { skillIconHtml } from './skill-icon';
import { OTHER_CATEGORY, displayCategory } from './skill-categories';

/** 旧案例体系入口开关：暂时隐藏（留给后续「探索」栏目），翻回 true 即恢复 */
const SHOW_CASES = false;

const props = defineProps<{
  skill: AgentSkill;
  myUserId: number | null;
  isAdmin: boolean;
  /** 分类词表（DB 动态，面板统一下发） */
  categories: string[];
  /** 可见档位清单（面板统一下发；管理员设「可见范围」用） */
  tiers: RoleTier[];
}>();

const emit = defineEmits<{
  changed: [skill: AgentSkill];
  use: [skill: AgentSkill];
  /** 详情页快捷提问被点击（父级负责补添加/启用 → 填充输入框 → 关面板） */
  tryExample: [skill: AgentSkill, question: string];
  aiEdit: [skill: AgentSkill];
  download: [skill: AgentSkill];
  remove: [skill: AgentSkill];
}>();

// 来源徽章降饱和色（浅底+深字，单色即可）；「官方」分类已废除。
// 徽章仅在未上架技能展示：上架技能不再区分收录/凝练
const SOURCE_THEME: Record<AgentSkill['source'], { ca: string; label: string }> = {
  derived: { ca: '#0e7490', label: '凝练' },
  curated: { ca: '#0369a1', label: '收录' },
  builtin: { ca: '#475569', label: '内置' }
};
const theme = computed(() => SOURCE_THEME[props.skill.source]);
const heroStyle = computed(() => ({ '--ca': theme.value.ca }));
const initial = computed(() => (props.skill.name || props.skill.skillKey || '?').slice(0, 1));
// 头像瓦片：有图标（agent 生成的 svg / 原图 data URI）优先显示，否则回落名称首字
const hasIcon = computed(() => !!(props.skill.icon || '').trim());
const iconHtml = computed(() => skillIconHtml(props.skill.icon));

const canManage = computed(
  () => props.isAdmin || (props.myUserId != null && props.skill.userId === props.myUserId)
);

// ── 图标更换（仅可管理者：上传图片 / 粘贴 SVG / 清除，存法同 agent_skill.icon）──
const iconEditOpen = ref(false);
async function onIconChange(v: string | null) {
  const { data, error } = await fetchUpdateAgentSkill(props.skill.id, { icon: v || '' });
  if (!error && data) {
    emit('changed', data);
  } else {
    window.$message?.error('图标更新失败');
  }
}

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

// ── 启用开关（两层） ─────────────────────────────────────
/** 个人启用/禁用：人人可操作，只影响自己；禁用=完全不加载、@ 不可调用 */
async function onToggleUserPref(val: boolean) {
  const { data, error } = await fetchBatchAgentSkillPrefs([props.skill.skillKey], {isEnabled: val});
  if (!error && data) {
    emit('changed', { ...props.skill, userEnabled: val });
  } else {
    window.$message?.error('切换失败');
  }
}
/** 商店在架态：isEnabled（统一尺子，无 visibility 机制） */
const isListed = computed(() => props.skill.isEnabled);

/** 商店上架/下架：仅管理员可操作，影响所有用户 */
async function onToggleGlobal(val: boolean) {
  const { data, error } = await fetchUpdateAgentSkill(props.skill.id, { is_enabled: val });
  if (!error && data) emit('changed', data);
  else window.$message?.error('切换失败');
}

// ── 可见范围（仅管理员）：可见档位白名单，空数组=全员；档位间无包含关系，勾选哪些档位就只有哪些档位的用户可见 ──
const tierCodes = computed(() => props.skill.minTierCode || []);
async function onSetTierCodes(codes: string[]) {
  const cur = [...(props.skill.minTierCode || [])].sort();
  const next = [...codes].sort();
  if (cur.length === next.length && cur.every((c, i) => c === next[i])) return;
  const { data, error } = await fetchUpdateAgentSkill(props.skill.id, { min_tier_code: codes });
  if (!error && data) emit('changed', data);
  else window.$message?.error('可见范围设置失败');
}

// ── 分类（取代旧标签；词表 DB 动态，由面板下发） ──
/** 可选项 = DB 词表 + 恒在的「其他」兜底桶（选它=清除分类） */
const categoryOptions = computed(() =>
  props.categories.includes(OTHER_CATEGORY) ? props.categories : [...props.categories, OTHER_CATEGORY]
);
async function setCategory(c: string) {
  if (displayCategory(props.skill.category, props.categories) === c) return;
  const { data, error } = await fetchUpdateAgentSkill(props.skill.id, { category: c === OTHER_CATEGORY ? '' : c });
  if (!error && data) emit('changed', data);
  else window.$message?.error('分类设置失败');
}

// ── 快捷提问（详情页渲染成可点建议气泡，点一下即添加技能并把「@技能key + 问题」填入输入框） ──
const askDraft = ref('');
const askSaving = ref(false);
function syncAskDraft() {
  askDraft.value = (props.skill.exampleQuestions || []).join('\n');
}
syncAskDraft();
async function saveAsk() {
  askSaving.value = true;
  try {
    const list = askDraft.value
      .split('\n')
      .map(s => s.trim())
      .filter(Boolean);
    const { data, error } = await fetchUpdateAgentSkill(props.skill.id, { example_questions: list });
    if (!error && data) {
      emit('changed', data);
      // 回写归一化后的结果（去重/截断/最多6条），所见即所存
      askDraft.value = (data.exampleQuestions || []).join('\n');
      window.$message?.success('已保存');
    } else {
      window.$message?.error('保存失败');
    }
  } finally {
    askSaving.value = false;
  }
}

// ── 案例（仅管理员：从会话提取挂到本技能，橱窗展示、用户可 fork 试用） ──
const examples = ref<SkillExample[]>([]);
const examplesLoading = ref(false);
/** 提取弹窗 */
const exampleModalShow = ref(false);

async function loadExamples() {
  if (!props.isAdmin) return;
  examplesLoading.value = true;
  try {
    const { data, error } = await fetchSkillExamples(props.skill.id);
    if (!error && data) examples.value = data;
  } finally {
    examplesLoading.value = false;
  }
}

function onExampleAdded(ex: SkillExample) {
  examples.value = [...examples.value, ex];
}

async function toggleExampleEnabled(ex: SkillExample) {
  const next = ex.isEnabled === 0 ? 1 : 0;
  const { data, error } = await fetchUpdateSkillExample(ex.id, { isEnabled: next });
  if (!error && data) await loadExamples();
  else window.$message?.error('切换失败');
}

async function removeExample(ex: SkillExample) {
  const { error } = await fetchDeleteSkillExample(ex.id);
  if (!error) {
    examples.value = examples.value.filter(x => x.id !== ex.id);
    window.$message?.success('已删除');
  } else {
    window.$message?.error('删除失败');
  }
}

onMounted(() => {
  if (props.isAdmin && SHOW_CASES) loadExamples();
});
watch(
  () => props.skill.id,
  () => {
    if (props.isAdmin && SHOW_CASES) loadExamples();
  }
);

// ── 版本 ─────────────────────────────────────────────────
const versions = ref<AgentSkillVersion[]>([]);
const versionsLoading = ref(false);
async function loadVersions() {
  // 版本接口仅创建者/管理员可调（后端 4032 守卫），无权时不拉取
  if (!canManage.value) return;
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
    syncAskDraft();
    loadVersions();
  },
  { immediate: true }
);
</script>

<template>
  <div class="sd">
    <div class="sd-scroll">
      <div class="sd-card" :style="heroStyle">
        <!-- 身份区：图标 + 名称 + 徽标（管理员多版本/文件数徽标） -->
        <div class="sd-id">
          <span v-if="hasIcon" class="sd-avatar sd-avatar--icon" v-html="iconHtml"></span>
          <span v-else class="sd-avatar">{{ initial }}</span>
          <div class="sd-id-main">
            <h2 class="sd-name">{{ skill.name }}</h2>
            <div class="sd-badges">
              <!-- 来源徽章仅未上架技能展示；上架后不再区分收录/凝练（「官方」分类已废除） -->
              <span v-if="!skill.isEnabled" class="sd-badge sd-badge-src">{{ theme.label }}</span>
              <span class="sd-badge">{{ displayCategory(skill.category, categories) }}</span>
              <span v-if="canManage && skill.version" class="sd-badge sd-badge-ver">v{{ skill.version }}</span>
              <span v-if="canManage && skill.hasFiles" class="sd-badge">{{ skill.fileCount }} 文件</span>
            </div>
          </div>
        </div>

        <p class="sd-desc">{{ skill.description || '暂无描述' }}</p>

        <!-- 快捷提问（全体用户可见）：详情页展示全部条目，点一下即添加技能并把「@技能key + 问题」填入输入框 -->
        <section v-if="(skill.exampleQuestions || []).length" class="sd-sec">
          <div class="sd-sec-head">
            <span class="sd-sec-title">试试这样问</span>
            <span class="sd-sec-line" />
          </div>
          <div class="sd-asks">
            <button
              v-for="q in skill.exampleQuestions"
              :key="q"
              class="sd-ask"
              :title="`@${skill.skillKey} ${q}`"
              @click="emit('tryExample', skill, q)"
            >
              {{ q }}
            </button>
          </div>
        </section>

        <!-- 使用设置：个人开关（已添加才显示）/ 上架开关（管理员）/ 更换图标（可管理者） -->
        <div class="sd-settings">
          <label
            v-if="skill.isAdded"
            class="sd-enable"
            title="只影响你自己：禁用=完全剔除，不再加载、不可 @ 调用；重新启用即恢复"
          >
            <span>{{ skill.userEnabled ? '已启用' : '已禁用' }}</span>
            <NSwitch :value="skill.userEnabled" @update:value="onToggleUserPref" />
          </label>
          <label v-if="isAdmin" class="sd-enable sd-enable--global" title="商店上架状态，影响所有用户：上架后全员可见，仅管理员可操作">
            <span>{{ isListed ? '已上架' : '未上架' }}</span>
            <NSwitch :value="isListed" @update:value="onToggleGlobal" />
          </label>
          <div
            v-if="isAdmin && skill.source !== 'builtin'"
            class="sd-tier"
            title="可见范围：勾选哪些档位就只有哪些档位的用户可见（档位间无包含关系）；不勾选=全部用户；作者本人恒可见自己的技能（内置技能恒全员可见，不受档位限制）"
          >
            <span>可见范围</span>
            <TierMultiSelect :model-value="tierCodes" :tiers="tiers" @update:model-value="onSetTierCodes" />
          </div>
          <button v-if="canManage" class="sd-minor" @click="iconEditOpen = !iconEditOpen">
            {{ iconEditOpen ? '收起图标设置' : '更换图标' }}
          </button>
        </div>
        <div v-if="canManage && iconEditOpen" class="sd-icon-edit">
          <IconPicker :icon="skill.icon || null" @update:icon="onIconChange" />
        </div>

        <!-- 管理信息（仅可管理者）：key / 作者 / 时间戳 -->
        <div v-if="canManage" class="sd-meta">
          <div class="sd-meta-row"><span>标识</span><code>@{{ skill.skillKey }}</code></div>
          <div class="sd-meta-row"><span>作者</span><em>{{ skill.userId == null ? '官方' : (skill.author || '未命名') }}</em></div>
          <div class="sd-meta-row"><span>创建</span><em>{{ fmtDate(skill.createdAt) }}</em></div>
          <div class="sd-meta-row"><span>更新</span><em>{{ fmtDate(skill.updatedAt) }}</em></div>
        </div>

        <!-- 技能文档（仅可管理者）：给 agent 的提示词原文 -->
        <section v-if="canManage" class="sd-sec">
          <div class="sd-sec-head">
            <span class="sd-sec-title">技能文档</span>
            <span class="sd-sec-line" />
            <button v-if="!editing" class="sd-link" @click="startEdit">编辑</button>
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

        <!-- 分类切换（仅可管理者；普通用户的分类徽标已在身份区） -->
        <section v-if="canManage" class="sd-sec">
          <div class="sd-sec-head">
            <span class="sd-sec-title">分类</span>
            <span class="sd-sec-line" />
          </div>
          <p class="sd-tag-hint">
            分类决定这个技能在技能库顶部归属哪个导航 tab，方便团队成员按主题发现同类技能。点击下方分类直接切换。
          </p>
          <div class="sd-tags">
            <button
              v-for="c in categoryOptions"
              :key="c"
              class="sd-cat"
              :class="{ 'sd-cat--on': displayCategory(skill.category, categories) === c }"
              @click="setCategory(c)"
            >{{ c }}</button>
          </div>
        </section>

        <!-- 快捷提问编辑（仅可管理者）：保存后以可点建议气泡形式展示在上方「试试这样问」分节 -->
        <section v-if="canManage" class="sd-sec">
          <div class="sd-sec-head">
            <span class="sd-sec-title">设置快捷提问</span>
            <span class="sd-sec-line" />
          </div>
          <p class="sd-tag-hint">写几句话，会以建议气泡的形式出现在技能详情页上，用户点一下就能问出口。一行一句，最多 6 句。</p>
          <textarea v-model="askDraft" class="sd-input sd-ask-edit" rows="4" placeholder="一行一句，用户点一下就能问出口" />
          <div class="sd-edit-actions">
            <button class="sd-btn sd-btn-primary" :disabled="askSaving" @click="saveAsk">
              {{ askSaving ? '保存中…' : '保存' }}
            </button>
          </div>
        </section>

        <!-- 案例（仅管理员：从会话提取挂到本技能；橱窗按技能展示，用户可 fork 试用） -->
        <section v-if="isAdmin && SHOW_CASES" class="sd-sec">
          <div class="sd-sec-head">
            <span class="sd-sec-title">案例管理</span>
            <span class="sd-sec-line" />
            <span v-if="examples.length" class="sd-count">{{ examples.length }}</span>
          </div>
          <div v-if="examplesLoading" class="sd-empty-sm">加载中…</div>
          <div v-else-if="!examples.length" class="sd-empty-sm">暂无案例——从下方挑一段精彩任务提取一条</div>
          <div v-else class="sd-examples">
            <div v-for="ex in examples" :key="ex.id" class="sd-example" :class="{ 'is-off': ex.isEnabled === 0 }">
              <img v-if="ex.previewImage" class="sd-example-thumb" :src="ex.previewImage" alt="" />
              <div class="sd-example-main">
                <span class="sd-example-title">
                  {{ ex.title }}
                  <em v-if="ex.isEnabled === 0">已停用</em>
                </span>
                <span v-if="ex.description" class="sd-example-desc">{{ ex.description }}</span>
              </div>
              <div class="sd-example-ops">
                <button
                  class="sd-btn"
                  :title="ex.isEnabled === 0 ? '启用：恢复橱窗展示' : '停用：橱窗不再展示（数据保留）'"
                  @click="toggleExampleEnabled(ex)"
                >{{ ex.isEnabled === 0 ? '启用' : '停用' }}</button>
                <NPopconfirm positive-text="删除" negative-text="取消" @positive-click="removeExample(ex)">
                  <template #default>确定删除案例「{{ ex.title }}」？此操作不可恢复。</template>
                  <template #trigger>
                    <button class="sd-btn sd-btn-danger">删除</button>
                  </template>
                </NPopconfirm>
              </div>
            </div>
          </div>

          <!-- 从会话提取（弹窗：会话搜索选择 + 消息预览 + 预览图上传） -->
          <div class="sd-example-add">
            <button class="sd-btn sd-btn-primary" @click="exampleModalShow = true">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
              从任务提取案例
            </button>
          </div>
        </section>

        <!-- 案例提取弹窗（teleport 到 body） -->
        <SkillExampleModal v-if="isAdmin && SHOW_CASES" v-model:show="exampleModalShow" :skill="skill" @added="onExampleAdded" />

        <!-- 版本记录（仅可管理者） -->
        <section v-if="canManage" class="sd-sec">
          <div class="sd-sec-head">
            <span class="sd-sec-title">版本记录</span>
            <span class="sd-sec-line" />
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
              <div class="sd-version-actions">
                <button v-if="!v.isActive" class="sd-link" @click="activateVersion(v)">激活</button>
                <button v-if="!v.isActive" class="sd-link sd-link-danger" @click="removeVersion(v)">删除</button>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>

    <!-- 底部操作行：连接器页同款（次要操作居左，主操作居右） -->
    <div class="sd-foot">
      <!-- AI 编辑仅可管理者（创建者/管理员）可见：后端 skill_save 同样拒绝编辑他人技能 -->
      <NTooltip v-if="canManage" trigger="hover" :delay="300">
        <template #trigger>
          <button class="sd-btn" @click="emit('aiEdit', skill)">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 1.5l1.6 3.9 4.2.4-3.2 2.7.9 4.1L8 10.2l-3.5 2.4.9-4.1L2.2 5.8l4.2-.4L8 1.5z" stroke-width="1.3" stroke-linejoin="round" /></svg>
            AI 编辑
          </button>
        </template>
        在任务中插入「@编辑 @key」，让 AI 帮你改写这个技能
      </NTooltip>
      <!-- 技能包下载仅可管理者可用：普通用户不应拿到技能文件本体 -->
      <NTooltip v-if="canManage && skill.hasFiles" trigger="hover" :delay="300">
        <template #trigger>
          <button class="sd-btn" @click="emit('download', skill)">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 2v9M4.5 8L8 11.5 11.5 8" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /><path d="M3 13.5h10" stroke-width="1.6" stroke-linecap="round" /></svg>
            下载技能包
          </button>
        </template>
        导出为 zip，可分享给他人上传使用
      </NTooltip>
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
      <span class="sd-foot-spacer" />
      <button
        class="sd-btn sd-btn-primary"
        :disabled="!skill.isEnabled || !skill.isAdded || !skill.userEnabled"
        :title="!skill.isEnabled ? '技能未上架，暂不可使用' : !skill.isAdded ? '请先在商店添加到我的技能' : !skill.userEnabled ? '技能已被禁用，请先启用' : '插入 @调用 到输入框'"
        @click="emit('use', skill)"
      >
        在任务中使用
      </button>
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
  scrollbar-gutter: stable; /* 预留滚动条槽位，内容增减不抖 */
  padding: 22px 26px 10px;
  min-height: 0;
}
.sd-scroll::-webkit-scrollbar {
  width: 5px;
}
.sd-scroll::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 3px;
}

/* 居中卡片：与连接器详情页（.cf-card）同一设计语言 */
.sd-card {
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 18px;
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 14px;
  box-shadow: var(--card-shadow);
  padding: 24px 26px;
}

/* ── 身份区 ─────────────────────────────────────── */
.sd-id {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}
/* 头像瓦片：来源色浅底深字（与来源徽章同格式，去渐变） */
.sd-avatar {
  flex-shrink: 0;
  width: 52px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 14px;
  color: var(--ca);
  font-size: 22px;
  font-weight: 700;
  background: color-mix(in srgb, var(--ca) 12%, transparent);
  overflow: hidden;
}
/* 图标形态：svg 按 28px 呈现；图片 data URI 铺满圆角瓦片 */
.sd-avatar--icon :deep(svg) {
  width: 28px;
  height: 28px;
  display: block;
}
.sd-avatar--icon :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.sd-id-main {
  flex: 1;
  min-width: 0;
}
.sd-name {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: -0.01em;
  margin: 0 0 8px;
  color: var(--ink);
}
.sd-badges {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
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
.sd-badge-ver {
  font-family: 'JetBrains Mono', monospace;
  background: var(--accent-soft);
  color: var(--accent);
}
.sd-desc {
  font-size: 13px;
  line-height: 1.75;
  color: var(--ink-2, #334155);
  margin: 0;
  /* description 支持多行块标量（SKILL.md 的 description: |），保留换行与段落 */
  white-space: pre-line;
}

/* ── 使用设置 ───────────────────────────────────── */
.sd-settings {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.sd-enable {
  flex: 1;
  min-width: 170px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-2, #334155);
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 9px 12px;
}
/* 全局开关：琥珀描边区分于个人开关（影响所有用户，权重更高） */
.sd-enable--global {
  border-color: rgba(217, 119, 6, 0.28);
  background: rgba(217, 119, 6, 0.05);
}
/* 可见范围（仅管理员）：与全局开关同权重语义（琥珀描边），内嵌手写多选 chips（空选=全员） */
.sd-tier {
  flex: 1;
  min-width: 210px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink-2, #334155);
  background: rgba(217, 119, 6, 0.05);
  border: 1px solid rgba(217, 119, 6, 0.28);
  border-radius: 10px;
  padding: 5px 6px 5px 12px;
}
.sd-tier > span {
  flex-shrink: 0;
}
.sd-minor {
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-3);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 8px 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.sd-minor:hover {
  background: var(--fill-hover);
  border-color: var(--border-strong);
  color: var(--ink-2);
}
.sd-icon-edit {
  padding: 12px;
  border-radius: 10px;
  background: var(--surface);
  border: 1px solid var(--border);
}

/* ── 管理信息（仅可管理者） ─────────────────────── */
.sd-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 16px;
  border-top: 1px dashed var(--border);
}
.sd-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
}
.sd-meta-row span {
  color: var(--ink-4, #94a3b8);
}
.sd-meta-row em {
  font-style: normal;
  color: var(--ink-2, #334155);
}
.sd-meta-row code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ca);
  background: color-mix(in srgb, var(--ca) 9%, transparent);
  padding: 1px 8px;
  border-radius: 5px;
}

/* ── 卡内分节 ───────────────────────────────────── */
.sd-sec {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 16px;
  border-top: 1px dashed var(--border);
}
.sd-sec-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.sd-sec-title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink-2);
}
.sd-sec-line {
  flex: 1;
  height: 1px;
  background: var(--line-hair);
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
  margin: 0;
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
.sd-empty-sm {
  font-size: 12.5px;
  color: var(--ink-4, #94a3b8);
  padding: 2px;
}

/* 技能文档查看 */
.sd-prompt-view {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--ink-2, #334155);
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  max-height: 340px;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，内容增减不抖 */
  white-space: pre-wrap;
  word-break: break-word;
}
.sd-prompt-view::-webkit-scrollbar {
  width: 5px;
}
.sd-prompt-view::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 3px;
}

/* 名称/描述编辑 */
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
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 9px 12px;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.sd-input:focus {
  border-color: color-mix(in srgb, var(--accent) 40%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.sd-ask-edit {
  width: 100%;
  min-height: 96px;
  line-height: 1.7;
  resize: vertical;
  margin: 8px 0;
}

/* 快捷提问气泡（详情页）：展示全部条目，允许整句换行不截断，点击即问 */
.sd-asks {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.sd-ask {
  max-width: 100%;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.5;
  text-align: left;
  padding: 5px 12px;
  border-radius: 20px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  color: var(--ink-3, #64748b);
  cursor: pointer;
  transition: all 0.15s;
}
.sd-ask:hover {
  color: var(--ca);
  border-color: color-mix(in srgb, var(--ca) 38%, transparent);
  background: color-mix(in srgb, var(--ca) 8%, transparent);
}
.sd-edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* 分类切换 */
.sd-tags {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
  align-items: center;
}
/* 选中态主题色实底，与商店导航 chip 同一语言 */
.sd-cat {
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 650;
  color: var(--ink-3, #64748b);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 3px 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.sd-cat:hover {
  border-color: var(--border-strong);
  color: var(--ink-2, #334155);
}
.sd-cat--on {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}

/* ── 案例管理（管理员） ── */
.sd-examples {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sd-example {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  transition: border-color 0.15s;
}
.sd-example:hover {
  border-color: var(--border-strong);
}
.sd-example.is-off {
  opacity: 0.62;
}
.sd-example-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.sd-example-title {
  font-size: 13px;
  font-weight: 650;
  color: var(--ink, #0f172a);
  display: flex;
  align-items: center;
  gap: 6px;
}
.sd-example-title em {
  font-style: normal;
  font-size: 9.5px;
  font-weight: 700;
  color: #64748b;
  background: rgba(100, 116, 139, 0.12);
  border-radius: 5px;
  padding: 0 5px;
  line-height: 1.6;
}
.sd-example-desc {
  font-size: 11.5px;
  color: var(--ink-3, #64748b);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sd-example-ops {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
.sd-example-thumb {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--border);
  flex-shrink: 0;
}
.sd-example-add {
  padding-top: 12px;
  border-top: 1px dashed var(--border);
  display: flex;
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
  border: 1px solid var(--border);
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

/* ── 底部操作行（连接器页同款） ─────────────────── */
.sd-foot {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 26px;
  border-top: 1px solid var(--line-hair);
  background: var(--surface, rgba(255, 255, 255, 0.42));
}
.sd-foot-spacer {
  flex: 1;
}
.sd-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  padding: 8px 16px;
  border-radius: 9px;
  border: 1px solid var(--border-strong);
  background: var(--surface-strong, rgba(255, 255, 255, 0.62));
  color: var(--ink-2, #334155);
  cursor: pointer;
  transition: all 0.15s;
}
.sd-btn:hover:not(:disabled) {
  background: var(--fill-hover);
}
.sd-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.sd-btn-primary {
  background: var(--grad-brand);
  border-color: transparent;
  color: var(--on-primary);
  box-shadow: var(--shadow-sm);
}
.sd-btn-primary:hover:not(:disabled) {
  filter: brightness(0.96);
  box-shadow: var(--shadow-md);
}
.sd-btn-danger {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.16);
}
.sd-btn-danger:hover:not(:disabled) {
  background: rgba(220, 38, 38, 0.06);
}
</style>
