<script setup lang="ts">
import { computed, ref } from 'vue';
import SvgIcon from '@/components/custom/svg-icon.vue';
import TierMultiSelect from '../TierMultiSelect.vue';
import { brand } from '@/constants/brand';
import { fetchCreateAgentExpert, fetchUpdateAgentExpert } from '@/service/api';
import type { AgentConnector, AgentExpert, AgentSkill, RoleTier } from '@/service/api';

/** 「专家」体系称谓随品牌变体：standard=助理 / generic=专家 */
const expertLabel = brand.expertLabel;

/** 专家创建/编辑表单（含引导预览：按 OnboardingModal 卡片样式实时回显） */
const props = defineProps<{
  expert: AgentExpert | null;
  /** 可绑定的技能候选（商店可见全集） */
  skills: AgentSkill[];
  /** 可绑定的连接器候选 */
  connectors: AgentConnector[];
  isAdmin: boolean;
  /** 可见档位清单（面板统一下发；管理员编辑态设「可见范围」用） */
  tiers: RoleTier[];
}>();

const emit = defineEmits<{
  saved: [expert: AgentExpert];
}>();

const isEdit = computed(() => props.expert != null);
const saving = ref(false);

const name = ref(props.expert?.name || '');
const icon = ref(props.expert?.icon || '');
const category = ref(props.expert?.category || '');
const description = ref(props.expert?.description || '');
const instructions = ref(props.expert?.instructions || '');
const welcomeMessage = ref(props.expert?.welcomeMessage || '');
/** 快捷提问：一行一条，卡片上渲染成可点建议气泡 */
const askDraft = ref((props.expert?.exampleQuestions || []).join('\n'));
const skillKeys = ref<Set<string>>(new Set(props.expert?.skillKeys || []));
const connectorKeys = ref<Set<string>>(new Set(props.expert?.connectorKeys || []));
/** 上架状态仅管理员在编辑时可调（新建一律未上架，与连接器一致） */
const isEnabled = ref<boolean>(props.expert?.isEnabled || false);
/** 可见档位白名单仅管理员在编辑时可调（空数组=全员；档位间无包含关系，与上架口径一致） */
const minTierCodes = ref<string[]>(props.expert?.minTierCode || []);

const skillKw = ref('');
const skillOptions = computed(() => {
  const kw = skillKw.value.trim().toLowerCase();
  const rows = props.skills.filter(s => !kw || s.name.toLowerCase().includes(kw) || s.skillKey.toLowerCase().includes(kw));
  return rows.slice(0, 100);
});

function toggleSkill(key: string) {
  const next = new Set(skillKeys.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  skillKeys.value = next;
}
function toggleConnector(key: string) {
  const next = new Set(connectorKeys.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  connectorKeys.value = next;
}

async function save() {
  const n = name.value.trim();
  if (!n) {
    window.$message?.warning(`请填写${expertLabel}名称`);
    return;
  }
  if (saving.value) return;
  saving.value = true;
  const payload = {
    name: n,
    icon: icon.value.trim() || null,
    category: category.value.trim() || null,
    description: description.value.trim() || null,
    instructions: instructions.value.trim() || null,
    welcomeMessage: welcomeMessage.value.trim() || null,
    skillKeys: [...skillKeys.value],
    connectorKeys: [...connectorKeys.value],
    exampleQuestions: askDraft.value
      .split('\n')
      .map(s => s.trim())
      .filter(Boolean)
  };
  try {
    if (isEdit.value && props.expert) {
      const data: Record<string, unknown> = { ...payload };
      if (props.isAdmin) {
        data.isEnabled = isEnabled.value;
        data.minTierCode = minTierCodes.value;
      }
      const { data: saved, error } = await fetchUpdateAgentExpert(props.expert.id, data as any);
      if (error || !saved) {
        window.$message?.error(error?.msg || '保存失败');
        return;
      }
      window.$message?.success(`${expertLabel}已保存`);
      emit('saved', saved);
    } else {
      const { data: saved, error } = await fetchCreateAgentExpert(payload);
      if (error || !saved) {
        window.$message?.error(error?.msg || '创建失败');
        return;
      }
      window.$message?.success(`${expertLabel}已创建（默认未上架）`);
      emit('saved', saved);
    }
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="exf">
    <div class="exf-main">
      <div class="exf-row">
        <label class="exf-label">{{ expertLabel }}名称 <span class="exf-req">*</span></label>
        <input v-model="name" class="exf-input" maxlength="32" placeholder="如：标准编制专家" />
      </div>

      <div class="exf-cols">
        <div class="exf-row exf-grow">
          <label class="exf-label">图标（iconify 图标名）</label>
          <div class="exf-icon-row">
            <input v-model="icon" class="exf-input" placeholder="mdi:account-tie-outline" />
            <span class="exf-icon-preview">
              <SvgIcon :icon="icon.trim() || 'mdi:account-tie-outline'" />
            </span>
          </div>
        </div>
        <div class="exf-row">
          <label class="exf-label">分组分类</label>
          <input v-model="category" class="exf-input" maxlength="32" placeholder="如：标准工程" />
        </div>
      </div>

      <div class="exf-row">
        <label class="exf-label">一句话简介（卡片展示，≤200 字）</label>
        <input v-model="description" class="exf-input" maxlength="200" placeholder="这位专家擅长什么、能帮用户做什么" />
      </div>

      <div class="exf-row">
        <label class="exf-label">人设与方法论（注入系统提示词，决定{{ expertLabel }}的行为风格）</label>
        <textarea
          v-model="instructions"
          class="exf-textarea"
          rows="7"
          placeholder="角色定位 / 工作方法 / 交付风格。如：你是一位资深的标准审查专家，审查前先明确依据条款……"
        />
      </div>

      <div class="exf-row">
        <label class="exf-label">欢迎语（绑定任务首屏展示，不进提示词）</label>
        <textarea v-model="welcomeMessage" class="exf-textarea" rows="2" placeholder="你好，我是××专家。把××交给我……" />
      </div>

      <div class="exf-row">
        <label class="exf-label">试试这样问（卡片上展示成可点的建议气泡，用户点一下就能问出口）</label>
        <textarea
          v-model="askDraft"
          class="exf-textarea"
          rows="3"
          placeholder="一行一句，最多 6 句。如：帮我核对这份标准是否现行有效"
        />
      </div>

      <div class="exf-row">
        <label class="exf-label">绑定技能（{{ skillKeys.size }}）——{{ expertLabel }}任务内运行时生效</label>
        <input v-model="skillKw" class="exf-input exf-input--sm" placeholder="搜索技能…" />
        <div class="exf-checks">
          <label v-for="s in skillOptions" :key="s.skillKey" class="exf-check">
            <input type="checkbox" :checked="skillKeys.has(s.skillKey)" @change="toggleSkill(s.skillKey)" />
            <span class="exf-check-name">{{ s.name }}</span>
            <span class="exf-check-key">@{{ s.skillKey }}</span>
          </label>
          <div v-if="!skillOptions.length" class="exf-check-empty">无匹配技能</div>
        </div>
      </div>

      <div v-if="connectors.length" class="exf-row">
        <label class="exf-label">绑定连接器（{{ connectorKeys.size }}）——{{ expertLabel }}任务内运行时生效</label>
        <div class="exf-checks exf-checks--min">
          <label v-for="c in connectors" :key="c.connectorKey" class="exf-check">
            <input type="checkbox" :checked="connectorKeys.has(c.connectorKey)" @change="toggleConnector(c.connectorKey)" />
            <span class="exf-check-name">{{ c.name }}</span>
          </label>
        </div>
      </div>

      <div v-if="isEdit && isAdmin" class="exf-row exf-row--inline exf-admin-row">
        <label class="exf-check">
          <input v-model="isEnabled" type="checkbox" />
          <span class="exf-check-name">商店上架（全员可见）</span>
        </label>
        <div
          class="exf-tier"
          title="可见范围：勾选哪些档位就只有哪些档位的用户可见（档位间无包含关系）；不勾选=全部用户；作者本人恒可见自己的专家"
        >
          <span class="exf-tier-label">可见范围</span>
          <TierMultiSelect v-model="minTierCodes" :tiers="tiers" />
        </div>
      </div>
    </div>

    <!-- 引导预览：按 OnboardingModal 专家卡片样式实时回显 -->
    <aside class="exf-preview">
      <div class="exf-preview-title">引导预览</div>
      <div class="ob-card-mock">
        <span class="ob-avatar">
          <SvgIcon :icon="icon.trim() || 'mdi:account-tie-outline'" />
        </span>
        <div class="ob-id">
          <div class="ob-name">{{ name.trim() || `${expertLabel}名称` }}</div>
          <div class="ob-desc">{{ description.trim() || '一句话简介会显示在这里' }}</div>
          <div class="ob-meta">
            <span v-if="skillKeys.size"><SvgIcon icon="mdi:lightning-bolt-outline" />{{ skillKeys.size }} 项技能</span>
            <span v-if="connectorKeys.size"><SvgIcon icon="mdi:connection" />{{ connectorKeys.size }} 个连接器</span>
            <span v-if="category.trim()">{{ category.trim() }}</span>
          </div>
        </div>
        <span :class="['ob-check', { 'ob-check--on': !!name.trim() }]">
          <SvgIcon icon="mdi:check" />
        </span>
      </div>
      <p class="exf-preview-tip">新手引导第一步将展示该卡片；选中后其绑定技能在第二步预勾选。</p>

      <button class="exf-save" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : isEdit ? '保存修改' : `创建${expertLabel}` }}
      </button>
    </aside>
  </div>
</template>

<style scoped>
.exf {
  display: flex;
  gap: 18px;
  height: 100%;
  overflow: hidden;
  padding: 14px 16px 24px;
}
.exf-main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  scrollbar-gutter: stable;
  display: flex;
  flex-direction: column;
  gap: 13px;
  padding-right: 4px;
}
.exf-cols {
  display: flex;
  gap: 12px;
}
.exf-grow {
  flex: 1;
}
.exf-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.exf-row--inline {
  flex-direction: row;
  align-items: center;
}
.exf-admin-row {
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}
/* 可见范围多选（仅管理员编辑态；手写组件，空选=全员） */
.exf-tier {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.exf-tier-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-3);
  white-space: nowrap;
}
.exf-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
}
.exf-req {
  color: #dc2626;
}
.exf-input {
  padding: 8px 11px;
  border-radius: 9px;
  border: 1px solid var(--border-strong);
  background: var(--surface-strong);
  color: var(--ink);
  font-size: 13px;
  outline: none;
}
.exf-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-soft);
}
.exf-input--sm {
  padding: 6px 10px;
  font-size: 12.5px;
}
.exf-icon-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.exf-icon-preview {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: var(--accent-soft);
  color: var(--accent);
}
.exf-icon-preview :deep(svg) {
  width: 18px;
  height: 18px;
}
.exf-textarea {
  padding: 9px 11px;
  border-radius: 9px;
  border: 1px solid var(--border-strong);
  background: var(--surface-strong);
  color: var(--ink);
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
  outline: none;
  font-family: inherit;
}
.exf-textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px var(--accent-soft);
}
.exf-checks {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 190px;
  overflow-y: auto;
  scrollbar-gutter: stable;
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 6px;
  background: var(--surface);
}
.exf-checks--min {
  max-height: 130px;
}
.exf-check {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 7px;
  border-radius: 7px;
  cursor: pointer;
  font-size: 12.5px;
  color: var(--ink-2);
}
.exf-check:hover {
  background: var(--fill-hover);
}
.exf-check input {
  accent-color: var(--accent);
}
.exf-check-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.exf-check-key {
  margin-left: auto;
  font-size: 11px;
  color: var(--ink-4);
}
.exf-check-empty {
  padding: 10px;
  text-align: center;
  font-size: 12px;
  color: var(--ink-4);
}
.exf-preview {
  flex: 0 0 268px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow-y: auto;
  scrollbar-gutter: stable;
}
.exf-preview-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
}
/* 引导卡片 mock（与 OnboardingModal 专家卡同构） */
.ob-card-mock {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 13px;
  border-radius: 12px;
  border: 1px solid var(--border-strong);
  background: var(--surface-strong);
}
.ob-avatar {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: var(--accent-soft);
  color: var(--accent);
}
.ob-avatar :deep(svg) {
  width: 22px;
  height: 22px;
}
.ob-id {
  flex: 1;
  min-width: 0;
}
.ob-name {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ink);
}
.ob-desc {
  margin-top: 3px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink-3);
}
.ob-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
  font-size: 11px;
  color: var(--ink-4);
}
.ob-meta span {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.ob-meta :deep(svg) {
  width: 11px;
  height: 11px;
}
.ob-check {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 1.5px solid var(--border-strong);
  color: transparent;
}
.ob-check :deep(svg) {
  width: 12px;
  height: 12px;
}
.ob-check--on {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}
.exf-preview-tip {
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--ink-4);
}
.exf-save {
  margin-top: auto;
  padding: 10px 0;
  border-radius: 10px;
  border: none;
  background: var(--grad-brand);
  color: var(--on-primary);
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
}
.exf-save:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
