<script setup lang="ts">
import {computed, h, ref, watch} from 'vue';
import type {SelectOption} from 'naive-ui';
import {NSelect, NSpin, NTag, useMessage} from 'naive-ui';
import {fetchModelConfig, fetchRoleModelConfig, fetchSetModelConfig, fetchSetRoleModelConfig} from '@/service/api';

/**
 * 模型配置抽屉（仅超管，由 ModelSwitcher 打开）——全局切换 + 按角色配置二合一。
 *
 * 上半区「全局模型」：三类模型横向 chip 流式选择，点击弹确认后切换，对所有用户立即生效。
 * 下半区「按角色配置」：每个角色对 对话 / 生图 / 生视频 各可选「跟随全局 / 指定预设块」，
 * 生图 / 生视频额外可「禁用」。生效规则：用户角色按 R_SUPER → R_ADMIN → R_USER → 自创角色
 * 固定顺序，第一个配置过的角色整行生效；都没配置落 OTHER 兜底行；再落全局。
 */

const SECTIONS = [
  {key: 'chat', title: '基础语言模型'},
  {key: 'image', title: '生图模型'},
  {key: 'video', title: '生视频模型'}
] as const;

type Category = (typeof SECTIONS)[number]['key'];

const DISABLED = 'DISABLED';
const FOLLOW = ''; // 空值 = 跟随全局

const props = defineProps<{show: boolean}>();
const emit = defineEmits<(e: 'update:show', v: boolean) => void>();

const visible = computed({
  get: () => props.show,
  set: v => emit('update:show', v)
});

const message = useMessage();

// ── 状态（全局切换与角色表共用 globalKeys：全局切换后角色表「跟随全局」标签实时联动）──
const globalLoading = ref(false);
const config = ref<Api.AI.ModelConfig | null>(null);
/** 正在切换的项，形如 `${category}:${key}`，用于禁用与转圈 */
const switching = ref('');

type RowState = Api.AI.RoleModelConfigRow & {
  editChat: string;
  editImage: string;
  editVideo: string;
  saving?: boolean;
};

const roleLoading = ref(false);
const rows = ref<RowState[]>([]);
const chatOptions = ref<{key: string; label: string}[]>([]);
const imageOptions = ref<{key: string; label: string}[]>([]);
const videoOptions = ref<{key: string; label: string}[]>([]);
const globalKeys = ref<{chat: string; image: string; video: string}>({chat: '', image: '', video: ''});

// ── 全局模型 ────────────────────────────────────────────────────────────────
async function loadGlobal() {
  globalLoading.value = true;
  const {data, error} = await fetchModelConfig();
  globalLoading.value = false;
  if (!error && data) config.value = data;
}

function currentLabel(cat: Category) {
  const c = config.value?.[cat];
  if (!c) return '-';
  return c.options.find(o => o.key === c.current)?.label || c.current;
}

function selectGlobal(sec: {key: Category; title: string}, opt: {key: string; label: string}) {
  const cat = config.value?.[sec.key];
  if (!cat || cat.current === opt.key || switching.value) return;
  window.$dialog?.warning({
    title: '切换模型',
    content: `将「${sec.title}」切换为「${opt.label}」，会立即影响所有用户当前使用的模型，确认切换？`,
    positiveText: '确认切换',
    negativeText: '取消',
    onPositiveClick: async () => {
      switching.value = `${sec.key}:${opt.key}`;
      // 失败提示由全局请求拦截器统一弹出（后端 msg），此处只处理成功
      const {error} = await fetchSetModelConfig(sec.key, opt.key);
      switching.value = '';
      if (!error && config.value) {
        config.value[sec.key].current = opt.key;
        // 同步角色表「跟随全局」的展示标签
        globalKeys.value[sec.key] = opt.key;
        window.$message?.success?.('已切换，对所有用户生效');
      }
    }
  });
}

// ── 按角色配置 ──────────────────────────────────────────────────────────────
async function loadRoles() {
  roleLoading.value = true;
  const {data, error} = await fetchRoleModelConfig();
  roleLoading.value = false;
  if (error || !data) return;
  chatOptions.value = data.chatOptions;
  imageOptions.value = data.imageOptions;
  videoOptions.value = data.videoOptions;
  globalKeys.value = data.globalKeys;
  rows.value = data.roles.map(r => ({
    ...r,
    editChat: r.chatBlockKey || FOLLOW,
    editImage: r.imageBlockKey || FOLLOW,
    editVideo: r.videoBlockKey || FOLLOW
  }));
}

function globalLabel(cat: Category) {
  const opts = cat === 'chat' ? chatOptions.value : cat === 'image' ? imageOptions.value : videoOptions.value;
  const key = globalKeys.value[cat];
  return opts.find(o => o.key === key)?.label || key;
}

function selectOptions(cat: Category) {
  const src = cat === 'chat' ? chatOptions.value : cat === 'image' ? imageOptions.value : videoOptions.value;
  const opts = src.map(o => ({label: o.label, value: o.key}));
  const follow = {label: `跟随全局（${globalLabel(cat)}）`, value: FOLLOW};
  // 生图 / 生视频多一档「禁用」；对话模型不可禁用
  return cat === 'chat' ? [follow, ...opts] : [follow, ...opts, {label: '禁用', value: DISABLED}];
}

/**
 * 「跟随全局」选项的特殊标识渲染：复用全局 chip 选中态的渐变胶囊样式，
 * 与上半区视觉语言一致。下拉菜单 teleport 到 body，故用内联样式。
 */
function makeRenderLabel(cat: Category) {
  return (option: SelectOption) => {
    if (option.value !== FOLLOW) return option.label;
    return h('div', {style: 'display:flex;align-items:center;gap:8px;min-width:0;'}, [
      h(
        'span',
        {
          style:
            'flex-shrink:0;display:inline-flex;align-items:center;padding:2px 10px;border-radius:999px;' +
            'background:linear-gradient(110deg,#1e40af 0%,#2563eb 35%,#0ea5e9 70%,#0891b2 100%);' +
            'color:#fff;font-size:11px;font-weight:600;line-height:16px;' +
            'box-shadow:0 6px 16px -6px rgba(30,64,175,0.45);'
        },
        '跟随全局'
      ),
      h(
        'span',
        {style: 'color:#94a3b8;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'},
        globalLabel(cat)
      )
    ]);
  };
}

function isDirty(row: RowState) {
  return (
    row.editChat !== (row.chatBlockKey || FOLLOW) ||
    row.editImage !== (row.imageBlockKey || FOLLOW) ||
    row.editVideo !== (row.videoBlockKey || FOLLOW)
  );
}

async function save(row: RowState) {
  row.saving = true;
  const {error} = await fetchSetRoleModelConfig({
    roleCode: row.roleCode,
    chatBlockKey: row.editChat || null,
    imageBlockKey: row.editImage || null,
    videoBlockKey: row.editVideo || null
  });
  row.saving = false;
  if (error) return; // 失败提示由全局请求拦截器统一弹出（后端 msg）
  // 保存成功后服务端已校验块有效，同步基线并清失效警告
  row.chatBlockKey = row.editChat || null;
  row.imageBlockKey = row.editImage || null;
  row.videoBlockKey = row.editVideo || null;
  row.chatBlockValid = true;
  row.imageBlockValid = true;
  row.videoBlockValid = true;
  message.success(
    row.chatBlockKey || row.imageBlockKey || row.videoBlockKey ? '已保存，对该角色用户的下一条消息生效' : '已清除配置，该角色恢复跟随全局'
  );
}

// 角色卡片的三类模型字段（类别 → 编辑缓冲字段 / 失效警告位 的映射）
const CATS = [
  {key: 'chat', label: '对话模型', edit: 'editChat', valid: 'chatBlockValid'},
  {key: 'image', label: '生图模型', edit: 'editImage', valid: 'imageBlockValid'},
  {key: 'video', label: '生视频模型', edit: 'editVideo', valid: 'videoBlockValid'}
] as const;

// 打开时两段都拉最新（关闭→打开都刷新）
watch(
  () => props.show,
  v => {
    if (v) {
      loadGlobal();
      loadRoles();
    }
  }
);
</script>

<template>
  <NDrawer v-model:show="visible" :width="920" placement="right">
    <NDrawerContent title="模型配置" closable>
      <!-- 全局模型 -->
      <section class="mcd-sec">
        <header class="mcd-sec-head">
          <div class="mcd-sec-title">全局模型</div>
          <div class="mcd-sec-sub">对所有用户生效，切换后立即改变当前使用的模型</div>
        </header>
        <NSpin :show="globalLoading">
          <div v-if="config" class="mcd-global">
            <div v-for="sec in SECTIONS" :key="sec.key" class="mcd-cat">
              <div class="mcd-cat-head">
                <span class="mcd-cat-title">{{ sec.title }}</span>
                <span class="mcd-cat-current">当前：{{ currentLabel(sec.key) }}</span>
              </div>
              <div class="mcd-chips">
                <button
                  v-for="opt in config[sec.key].options"
                  :key="opt.key"
                  class="mcd-chip"
                  :class="{
                    'is-active': config[sec.key].current === opt.key,
                    'is-busy': switching === `${sec.key}:${opt.key}`
                  }"
                  :disabled="!!switching"
                  @click="selectGlobal(sec, opt)"
                >
                  <span class="mcd-chip-label">{{ opt.label }}</span>
                  <span v-if="config[sec.key].current === opt.key" class="mcd-chip-check">✓</span>
                </button>
              </div>
            </div>
          </div>
        </NSpin>
      </section>

      <div class="mcd-divider" />

      <!-- 按角色配置 -->
      <section class="mcd-sec">
        <header class="mcd-sec-head">
          <div class="mcd-sec-title">按角色配置</div>
          <div class="mcd-sec-sub">覆盖全局配置，仅对该角色的用户生效</div>
        </header>
        <p class="mcd-desc">
          用户的角色按 超管 → 管理员 → 普通用户 → 自创角色 的固定顺序查找，第一个配置过的角色整行生效（未填的项跟随全局）；
          都没有配置时落「其他用户」行；再落全局模型。保存后对该角色用户的下一条消息生效。
        </p>
        <NSpin :show="roleLoading">
          <div v-if="rows.length" class="mrd-list">
            <article v-for="row in rows" :key="row.roleCode" class="mrd-card" :class="{'is-dirty': isDirty(row)}">
              <header class="mrd-head">
                <div class="mrd-id">
                  <span class="mrd-name">{{ row.roleName }}</span>
                  <span class="mrd-code">{{ row.roleCode }}</span>
                </div>
                <button class="mrd-save" :disabled="!isDirty(row) || row.saving" @click="save(row)">
                  {{ row.saving ? '保存中…' : '保存' }}
                </button>
              </header>
              <div class="mrd-grid">
                <div v-for="c in CATS" :key="c.key" class="mrd-field">
                  <div class="mrd-field-label">
                    {{ c.label }}
                    <NTag v-if="!row[c.valid]" type="warning" size="tiny">所选块已失效，保存后将修正</NTag>
                  </div>
                  <NSelect
                    :value="row[c.edit]"
                    :options="selectOptions(c.key)"
                    :render-label="makeRenderLabel(c.key)"
                    size="small"
                    :consistent-menu-width="false"
                    @update:value="(v: string) => (row[c.edit] = v)"
                  />
                </div>
              </div>
            </article>
          </div>
        </NSpin>
      </section>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
/* ── 区块骨架 ─────────────────────────────────────────────── */
.mcd-sec {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.mcd-sec-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.mcd-sec-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink, #0f172a);
}

.mcd-sec-sub {
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
}

.mcd-divider {
  margin: 18px 0;
  border-top: 1px dashed rgba(30, 64, 175, 0.16);
}

/* ── 全局模型：chip 流式选择 ─────────────────────────────── */
.mcd-global {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.mcd-cat-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}

.mcd-cat-title {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: var(--ink-3, #64748b);
}

.mcd-cat-current {
  font-size: 11px;
  color: #1e40af;
  font-weight: 600;
}

.mcd-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.mcd-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 13px;
  background: rgba(255, 255, 255, 0.66);
  border: 1px solid rgba(30, 64, 175, 0.14);
  border-radius: 10px;
  font-family: var(--font-body, inherit);
  font-size: 12px;
  color: #334155;
  cursor: pointer;
  transition: all 0.16s ease;
}

.mcd-chip:hover:not(:disabled):not(.is-active) {
  border-color: rgba(30, 64, 175, 0.4);
  color: #1e40af;
  background: rgba(30, 64, 175, 0.05);
  transform: translateY(-1px);
}

.mcd-chip:disabled {
  cursor: not-allowed;
}

.mcd-chip.is-busy {
  opacity: 0.6;
}

.mcd-chip.is-active {
  background: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  border-color: transparent;
  color: #fff;
  font-weight: 600;
  box-shadow: 0 6px 16px -6px rgba(30, 64, 175, 0.45);
}

.mcd-chip-check {
  font-size: 11px;
  font-weight: 700;
}

/* ── 按角色配置：角色卡片 ─────────────────────────────────── */
.mcd-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-3, #64748b);
}

.mrd-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.mrd-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 14px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.6),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 6px 20px -10px rgba(30, 64, 175, 0.12);
  transition: border-color 0.16s ease, box-shadow 0.16s ease;
}

/* 有未保存改动时描边提示 */
.mrd-card.is-dirty {
  border-color: rgba(30, 64, 175, 0.3);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.6),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 8px 24px -10px rgba(30, 64, 175, 0.22);
}

.mrd-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.mrd-id {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.mrd-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--ink, #0f172a);
}

.mrd-code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--ink-4, #94a3b8);
}

.mrd-save {
  flex-shrink: 0;
  padding: 5px 14px;
  border: 1px solid rgba(30, 64, 175, 0.22);
  border-radius: 8px;
  background: rgba(30, 64, 175, 0.06);
  font-family: var(--font-body, inherit);
  font-size: 12px;
  font-weight: 600;
  color: #1e40af;
  cursor: pointer;
  transition: all 0.15s ease;
}

.mrd-save:hover:not(:disabled) {
  background: rgba(30, 64, 175, 0.12);
  border-color: rgba(30, 64, 175, 0.4);
}

.mrd-save:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.mrd-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

@media (max-width: 700px) {
  .mrd-grid {
    grid-template-columns: 1fr;
  }
}

.mrd-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.mrd-field-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--ink-3, #64748b);
}
</style>
