<script setup lang="ts">
import {computed, h, ref, watch} from 'vue';
import type {SelectOption} from 'naive-ui';
import {NInput, NSelect, NSpin, NTag, NTooltip, useMessage} from 'naive-ui';
import {
  fetchChatModeConfig,
  fetchCreateChatModeConfig,
  fetchDeleteChatModeConfig,
  fetchModelConfig,
  fetchRoleModelConfig,
  fetchSetChatModeConfig,
  fetchSetModelConfig,
  fetchSetRoleModelConfig
} from '@/service/api';

/**
 * 模型配置抽屉（仅超管，由 ModelSwitcher 打开）——分页签三区：
 *
 * 「对话」：对话模式动态卡片（DB 行 = 真相源，可增删改：名称/说明行内编辑、
 *   指派模型点选即存、↑↓ 排序、删除；balanced 为系统兜底不可删）+
 *   「默认对话模型（兜底）」chip 行（原全局 chat 切换，agent_model_config chat 维度退居兜底）。
 *   未指派模型的模式走兜底链：按角色配置 → 默认对话模型。
 * 「生成与向量」：生图 / 生视频 / 向量三类横向 chip 流式选择，点击弹确认后切换。
 *   向量模型（embed）切换有加重确认：切换只改变新请求使用的向量空间，既有向量库 / 知识库
 *   会标记为「陈旧」（需显式重建），绝不自动重嵌。
 * 「角色差异」：每个角色对 生图 / 生视频 各可选「跟随全局 / 指定预设块 / 禁用」，
 *   点选即存（弹确认）。对话模型由「对话」页签的模式统一管理，角色卡不含任务模型列
 *   （保存即清除存量角色 chat 配置）。生效规则：用户角色按 R_SUPER → R_ADMIN → R_USER →
 *   自创角色固定顺序，第一个配置过的角色整行生效；都没配置落 OTHER 兜底行；再落全局。
 *   （向量模型全局统一，不参与按角色配置。）
 *
 * 统一交互语法：点选即存 + 确认弹窗（destructive / 影响面大的操作全部先弹确认）。
 */

const SECTIONS = [
  {key: 'image', title: '生图模型'},
  {key: 'video', title: '生视频模型'},
  {key: 'embed', title: '向量模型'}
] as const;

type Category = 'chat' | 'image' | 'video' | 'embed';
/** 参与「跟随全局」标签联动的类别（embed 全局统一，不进 globalKeys） */
type GlobalCat = 'chat' | 'image' | 'video';

/** 思考强度档位中文名（wire 值 → 展示；与后端 chat_mode.LEVEL_LABELS 一致） */
const LEVEL_LABELS: Record<string, string> = {
  none: '关闭',
  minimal: '极轻',
  low: '轻度',
  medium: '中等',
  high: '高',
  max: '极致'
};

const DISABLED = 'DISABLED';
const FOLLOW = ''; // 空值 = 跟随全局 / 跟随默认

const props = defineProps<{show: boolean}>();
const emit = defineEmits<(e: 'update:show', v: boolean) => void>();

const visible = computed({
  get: () => props.show,
  set: v => emit('update:show', v)
});

const message = useMessage();

const activeTab = ref<'chat' | 'gen' | 'role'>('chat');

// ── 状态（全局切换与角色表共用 globalKeys：全局切换后「跟随全局」标签实时联动）──
const globalLoading = ref(false);
const config = ref<Api.AI.ModelConfig | null>(null);
/** 正在切换的项，形如 `${category}:${key}`，用于禁用与转圈 */
const switching = ref('');

type RowState = Api.AI.RoleModelConfigRow & {
  editImage: string;
  editVideo: string;
  saving?: boolean;
};

const roleLoading = ref(false);
const rows = ref<RowState[]>([]);
const imageOptions = ref<{key: string; label: string}[]>([]);
const videoOptions = ref<{key: string; label: string}[]>([]);
const globalKeys = ref<{chat: string; image: string; video: string}>({chat: '', image: '', video: ''});

// ── 对话模式配置（动态卡片：增删改 + 排序，DB 行 = 真相源） ──────────────────
const chatModeLoading = ref(false);
const chatModeRows = ref<Api.AI.ChatModeConfigRow[]>([]);
const chatModeOptions = ref<{key: string; label: string; levels?: string[]}[]>([]);
/** 正在保存的模式 key（禁用该行控件） */
const modeSaving = ref('');
/** 排序提交中（↑↓ 两次 PUT 期间禁用全部排序钮） */
const reordering = ref(false);
/** 行内文本编辑态（名称 / 说明共用一个：同卡同时只编辑一项） */
const textEdit = ref<{key: string; field: 'label' | 'note'; value: string} | null>(null);
/** 新增模式卡表单 */
const newMode = ref({open: false, label: '', key: ''});
const creating = ref(false);

const modeSelectOptions = computed(() => {
  const follow = {label: `跟随默认（${globalLabel('chat')}）`, value: FOLLOW};
  return [
    follow,
    ...chatModeOptions.value.map(o => ({
      // 档位徽标：该模型支持的思考强度档数（0 档 = 不展示强度滑块）
      label: o.levels?.length ? `${o.label} · ${o.levels.length}档强度` : o.label,
      value: o.key
    }))
  ];
});

/** 某模式有效块（未指派 = 全局兜底块）的档位白名单（wire 值序 = 滑块序） */
function blockLevelsOf(blockKey: string | null): string[] {
  const k = blockKey || globalKeys.value.chat;
  return chatModeOptions.value.find(o => o.key === k)?.levels || [];
}

async function loadChatModeConfig() {
  chatModeLoading.value = true;
  const {data, error} = await fetchChatModeConfig();
  chatModeLoading.value = false;
  if (error || !data) return;
  chatModeRows.value = data.modes;
  chatModeOptions.value = data.chatOptions;
  if (data.globalChatKey) globalKeys.value.chat = data.globalChatKey;
}

function selectModeBlock(m: Api.AI.ChatModeConfigRow, v: string) {
  const cur = m.chatBlockKey || FOLLOW;
  if (cur === v || modeSaving.value) return;
  const label = v ? chatModeOptions.value.find(o => o.key === v)?.label || v : `跟随默认（${globalLabel('chat')}）`;
  window.$dialog?.warning({
    title: '调整对话模式',
    content: `将「${m.label}」模式指派为「${label}」，会影响所有使用该模式的用户（下一条消息将开启新的运行时会话并自动续接历史），确认调整？`,
    positiveText: '确认调整',
    negativeText: '取消',
    onPositiveClick: async () => {
      modeSaving.value = m.key;
      const {error} = await fetchSetChatModeConfig({mode: m.key, chatBlockKey: v || null});
      modeSaving.value = '';
      if (!error) {
        m.chatBlockKey = v || null;
        m.blockValid = true;
        message.success('已保存，对该模式用户的下一条消息生效');
      }
    }
  });
}

function startTextEdit(m: Api.AI.ChatModeConfigRow, field: 'label' | 'note') {
  if (modeSaving.value) return;
  textEdit.value = {key: m.key, field, value: field === 'label' ? m.label : m.note};
}

/** 行内文本编辑提交（Enter / blur）：值未变或名称为空则静默放弃 */
async function commitTextEdit(m: Api.AI.ChatModeConfigRow) {
  const ed = textEdit.value;
  textEdit.value = null;
  if (!ed || ed.key !== m.key || modeSaving.value) return;
  const val = ed.value.trim();
  if (ed.field === 'label' && (!val || val === m.label)) return;
  if (ed.field === 'note' && val === m.note) return;
  modeSaving.value = m.key;
  const payload: Api.AI.ChatModeConfigUpdate = ed.field === 'label' ? {mode: m.key, label: val} : {mode: m.key, note: val};
  const {error} = await fetchSetChatModeConfig(payload);
  modeSaving.value = '';
  if (error) return;
  if (ed.field === 'label') m.label = val;
  else m.note = val;
  message.success('已保存');
}

/** ↑↓ 排序：与邻行交换 sortOrder（两次 PUT；任一失败整表重拉对齐） */
async function moveMode(i: number, dir: -1 | 1) {
  const j = i + dir;
  if (j < 0 || j >= chatModeRows.value.length || reordering.value || modeSaving.value) return;
  const a = chatModeRows.value[i];
  const b = chatModeRows.value[j];
  reordering.value = true;
  const {error: e1} = await fetchSetChatModeConfig({mode: a.key, sortOrder: j});
  const {error: e2} = await fetchSetChatModeConfig({mode: b.key, sortOrder: i});
  reordering.value = false;
  if (e1 || e2) {
    await loadChatModeConfig();
    return;
  }
  a.sortOrder = j;
  b.sortOrder = i;
  const arr = chatModeRows.value;
  [arr[i], arr[j]] = [arr[j], arr[i]];
}

function removeMode(m: Api.AI.ChatModeConfigRow) {
  if (modeSaving.value) return;
  window.$dialog?.warning({
    title: '删除对话模式',
    content: `删除「${m.label}」后，使用该模式的用户将自动回落默认模式，确认删除？`,
    positiveText: '确认删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      modeSaving.value = m.key;
      const {error} = await fetchDeleteChatModeConfig(m.key);
      modeSaving.value = '';
      if (error) return;
      chatModeRows.value = chatModeRows.value.filter(r => r.key !== m.key);
      message.success('已删除，使用该模式的用户自动回落默认模式');
    }
  });
}

async function submitNewMode() {
  const label = newMode.value.label.trim();
  if (!label || creating.value) return;
  creating.value = true;
  const {error} = await fetchCreateChatModeConfig({label, key: newMode.value.key.trim() || undefined});
  creating.value = false;
  if (error) return;
  newMode.value = {open: false, label: '', key: ''};
  message.success(`已新增模式「${label}」，用户弹层下一条消息即见`);
  await loadChatModeConfig();
}

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
  // 向量模型切换加重确认：既有向量空间不会自动重建；
  // chat 是兜底语义：只影响未指派模式块的用户与系统路径
  const content =
    sec.key === 'embed'
      ? `将「向量模型」切换为「${opt.label}」。切换后所有向量库与知识库的既有向量将标记为「陈旧」，语义搜索 / 查重在重建完成前不可用，且重建会产生大量嵌入调用，确认切换？`
      : sec.key === 'chat'
        ? `将「默认对话模型（兜底）」切换为「${opt.label}」。仅影响对话模式未指派模型的用户与系统路径（每日简报、定时任务等）；已在「对话」页签指派了模型的模式不受影响，确认切换？`
        : `将「${sec.title}」切换为「${opt.label}」，会立即影响所有用户当前使用的模型，确认切换？`;
  window.$dialog?.warning({
    title: '切换模型',
    content,
    positiveText: '确认切换',
    negativeText: '取消',
    onPositiveClick: async () => {
      switching.value = `${sec.key}:${opt.key}`;
      // 失败提示由全局请求拦截器统一弹出（后端 msg），此处只处理成功
      const {error} = await fetchSetModelConfig(sec.key, opt.key);
      switching.value = '';
      if (!error && config.value) {
        config.value[sec.key].current = opt.key;
        // 同步「跟随全局」的展示标签（向量模型不参与，无需同步）
        const k = sec.key;
        if (k !== 'embed') globalKeys.value[k] = opt.key;
        window.$message?.success?.('已切换，对所有用户生效');
      }
    }
  });
}

// ── 按角色配置（点选即存） ──────────────────────────────────────────────────
async function loadRoles() {
  roleLoading.value = true;
  const {data, error} = await fetchRoleModelConfig();
  roleLoading.value = false;
  if (error || !data) return;
  imageOptions.value = data.imageOptions;
  videoOptions.value = data.videoOptions;
  globalKeys.value = data.globalKeys;
  rows.value = data.roles.map(r => ({
    ...r,
    editImage: r.imageBlockKey || FOLLOW,
    editVideo: r.videoBlockKey || FOLLOW
  }));
}

function globalLabel(cat: GlobalCat) {
  const opts =
    cat === 'chat'
      ? chatModeOptions.value.length
        ? chatModeOptions.value
        : (config.value?.chat.options ?? [])
      : cat === 'image'
        ? imageOptions.value
        : videoOptions.value;
  const key = globalKeys.value[cat];
  return opts.find(o => o.key === key)?.label || key || '-';
}

function selectOptions(cat: GlobalCat) {
  const src = cat === 'image' ? imageOptions.value : videoOptions.value;
  const opts = src.map(o => ({label: o.label, value: o.key}));
  const follow = {label: `跟随全局（${globalLabel(cat)}）`, value: FOLLOW};
  // 角色卡只剩生图 / 生视频，均有「禁用」档
  return [follow, ...opts, {label: '禁用', value: DISABLED}];
}

/**
 * 「跟随全局」选项的特殊标识渲染：复用全局 chip 选中态的渐变胶囊样式，
 * 与 chip 流视觉语言一致。下拉菜单 teleport 到 body，故用内联样式。
 */
function makeRenderLabel(cat: GlobalCat) {
  return (option: SelectOption) => {
    if (option.value !== FOLLOW) return option.label as string;
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

/** 角色卡单个下拉点选即存：弹确认 → PUT 该行（chatBlockKey 恒传 null 清存量任务模型配置） */
function selectRoleBlock(row: RowState, cat: 'image' | 'video', v: string) {
  const cur = cat === 'image' ? row.editImage : row.editVideo;
  if (cur === v || row.saving) return;
  const opts = cat === 'image' ? imageOptions.value : videoOptions.value;
  const desc =
    v === DISABLED ? '禁用（该角色不可用）' : v ? opts.find(o => o.key === v)?.label || v : `跟随全局（${globalLabel(cat)}）`;
  window.$dialog?.warning({
    title: '调整角色模型配置',
    content: `将「${row.roleName}」的${cat === 'image' ? '生图' : '生视频'}模型调整为「${desc}」，对该角色用户的下一条消息生效，确认调整？`,
    positiveText: '确认调整',
    negativeText: '取消',
    onPositiveClick: async () => {
      row.saving = true;
      const nextImage = cat === 'image' ? v : row.editImage;
      const nextVideo = cat === 'video' ? v : row.editVideo;
      const {error} = await fetchSetRoleModelConfig({
        roleCode: row.roleCode,
        chatBlockKey: null,
        imageBlockKey: nextImage || null,
        videoBlockKey: nextVideo || null
      });
      row.saving = false;
      if (error) return; // 失败提示由全局请求拦截器统一弹出（后端 msg）
      // 服务端已校验块有效：同步基线、清失效警告，受控下拉落到新值
      if (cat === 'image') row.editImage = v;
      else row.editVideo = v;
      row.chatBlockKey = null;
      row.imageBlockKey = nextImage || null;
      row.videoBlockKey = nextVideo || null;
      row.chatBlockValid = true;
      row.imageBlockValid = true;
      row.videoBlockValid = true;
      message.success(nextImage || nextVideo ? '已保存，对该角色用户的下一条消息生效' : '已清除配置，该角色恢复跟随全局');
    }
  });
}

// 角色卡片的两类模型字段（对话模型已移交「对话」页签的模式管理）
const CATS = [
  {key: 'image', label: '生图模型', edit: 'editImage', valid: 'imageBlockValid'},
  {key: 'video', label: '生视频模型', edit: 'editVideo', valid: 'videoBlockValid'}
] as const;

// 打开时三段都拉最新（关闭→打开都刷新）
watch(
  () => props.show,
  v => {
    if (v) {
      loadChatModeConfig();
      loadGlobal();
      loadRoles();
    }
  }
);
</script>

<template>
  <NDrawer v-model:show="visible" :width="920" placement="right">
    <NDrawerContent title="模型配置" :native-scrollbar="false" closable>
      <NTabs v-model:value="activeTab" type="segment" size="small" class="mcd-tabs">
        <!-- ═══ 对话：模式动态卡片 + 默认对话模型（兜底） ═══ -->
        <NTabPane name="chat" tab="对话">
          <div class="mcd-tab">
            <section class="mcd-sec">
              <header class="mcd-sec-head">
                <div class="mcd-sec-title">对话模式</div>
                <div class="mcd-sec-sub">用户在输入框处自选模式，此处管理模式清单与各模式指派的对话模型</div>
                <NTooltip trigger="hover" class="mcd-tip">
                  <template #trigger>
                    <span class="mcd-info">
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <circle cx="12" cy="12" r="9" />
                        <path d="M12 11v5.2" />
                        <path d="M12 7.6v.5" />
                      </svg>
                    </span>
                  </template>
                  <div style="max-width: 340px; line-height: 1.7">
                    未指派模型的模式走兜底链：按角色配置 → 默认对话模型。名称 / 说明点击即可行内编辑；
                    调整指派或增删模式后，受影响用户的下一条消息生效（将开启新的运行时会话并自动续接历史）。
                    「均衡」是系统兜底模式，不可删除。
                  </div>
                </NTooltip>
              </header>
              <NSpin :show="chatModeLoading">
                <div class="mcd-grid">
                  <div v-for="(m, i) in chatModeRows" :key="m.key" class="mcd-card" :class="{'is-busy': modeSaving === m.key}">
                    <div class="mcd-card-head">
                      <NInput
                        v-if="textEdit?.key === m.key && textEdit.field === 'label'"
                        :value="textEdit?.value ?? ''"
                        size="tiny"
                        :disabled="modeSaving === m.key"
                        @update:value="(v: string) => textEdit && (textEdit.value = v)"
                        @keyup.enter="commitTextEdit(m)"
                        @keyup.esc="textEdit = null"
                        @blur="commitTextEdit(m)"
                      />
                      <template v-else>
                        <span class="mcd-name">{{ m.label }}</span>
                        <button class="mcd-icon-btn" title="重命名" :disabled="modeSaving === m.key" @click="startTextEdit(m, 'label')">
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                            <path d="M16.8 3.6a2.1 2.1 0 0 1 3 3L7.6 18.8 3.2 20.4l1.6-4.4z" />
                            <path d="M14.6 5.8l3 3" />
                          </svg>
                        </button>
                      </template>
                      <div class="mcd-card-ops">
                        <button class="mcd-icon-btn" title="上移" :disabled="i === 0 || reordering || !!modeSaving" @click="moveMode(i, -1)">
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                            <path d="M12 19V5.5" />
                            <path d="M5.8 11.7L12 5.5l6.2 6.2" />
                          </svg>
                        </button>
                        <button
                          class="mcd-icon-btn"
                          title="下移"
                          :disabled="i === chatModeRows.length - 1 || reordering || !!modeSaving"
                          @click="moveMode(i, 1)"
                        >
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                            <path d="M12 5v13.5" />
                            <path d="M5.8 12.3L12 18.5l6.2-6.2" />
                          </svg>
                        </button>
                        <button
                          v-if="chatModeRows.length > 1"
                          class="mcd-icon-btn is-danger"
                          title="删除模式"
                          :disabled="!!modeSaving"
                          @click="removeMode(m)"
                        >
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                            <path d="M4.5 7h15" />
                            <path d="M9.5 7V4.8h5V7" />
                            <path d="M6.8 7l1 12.4h8.4L17.2 7" />
                            <path d="M10.3 10.4v5.8" />
                            <path d="M13.7 10.4v5.8" />
                          </svg>
                        </button>
                      </div>
                    </div>
                    <NTag v-if="!m.blockValid" type="warning" size="tiny" class="mcd-card-tag">所选块已失效，重新选择后将修正</NTag>
                    <NSelect
                      :value="m.chatBlockKey || FOLLOW"
                      :options="modeSelectOptions"
                      size="small"
                      :consistent-menu-width="false"
                      :disabled="modeSaving === m.key"
                      @update:value="(v: string) => selectModeBlock(m, v)"
                    />
                    <!-- 说明（行内编辑）：composer 弹层小字 -->
                    <div class="mcd-note-line">
                      <NInput
                        v-if="textEdit?.key === m.key && textEdit.field === 'note'"
                        :value="textEdit?.value ?? ''"
                        size="tiny"
                        placeholder="一句话说明该模式适用场景"
                        :disabled="modeSaving === m.key"
                        @update:value="(v: string) => textEdit && (textEdit.value = v)"
                        @keyup.enter="commitTextEdit(m)"
                        @keyup.esc="textEdit = null"
                        @blur="commitTextEdit(m)"
                      />
                      <span v-else class="mcd-note" title="点击编辑说明" @click="startTextEdit(m, 'note')">
                        {{ m.note || '点击添加一句话说明' }}
                      </span>
                    </div>
                    <!-- 思考强度档位（只读展示，来自模型块 reasoning_levels 白名单） -->
                    <div class="mcd-lv">
                      <template v-if="blockLevelsOf(m.chatBlockKey).length">
                        <span class="mcd-lv-cap">强度档位</span>
                        <span v-for="lv in blockLevelsOf(m.chatBlockKey)" :key="lv" class="mcd-lv-dot">{{ LEVEL_LABELS[lv] || lv }}</span>
                      </template>
                      <span v-else class="mcd-lv-cap">该模型不支持强度调节</span>
                    </div>
                  </div>

                  <!-- 新增模式：虚线卡 → 行内表单 -->
                  <button v-if="!newMode.open" class="mcd-card mcd-card--add" :disabled="creating" @click="newMode.open = true">
                    <span class="mcd-add-plus">
                      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true">
                        <path d="M12 5v14" />
                        <path d="M5 12h14" />
                      </svg>
                    </span>
                    <span class="mcd-add-text">新增模式</span>
                  </button>
                  <div v-else class="mcd-card mcd-card--form">
                    <NInput v-model:value="newMode.label" size="small" placeholder="模式名称（必填），如 极速" :disabled="creating" />
                    <NInput
                      v-model:value="newMode.key"
                      size="small"
                      placeholder="key 可省略（自动生成；小写字母开头 2~16 位）"
                      :disabled="creating"
                      @keyup.enter="submitNewMode"
                    />
                    <div class="mcd-form-ops">
                      <button class="mcd-btn" :disabled="creating || !newMode.label.trim()" @click="submitNewMode">
                        {{ creating ? '创建中…' : '创建' }}
                      </button>
                      <button class="mcd-btn is-ghost" :disabled="creating" @click="newMode = {open: false, label: '', key: ''}">取消</button>
                    </div>
                  </div>
                </div>
              </NSpin>
            </section>

            <div class="mcd-divider" />

            <!-- 默认对话模型（兜底）：原全局 chat 切换的紧凑一行 -->
            <section class="mcd-sec">
              <div v-if="config" class="mcd-cat">
                <div class="mcd-cat-head">
                  <span class="mcd-cat-title">默认对话模型（兜底）</span>
                  <span class="mcd-cat-current">当前：{{ currentLabel('chat') }}</span>
                </div>
                <p class="mcd-desc">仅影响未指派模型的模式与系统路径（每日简报、定时任务等）。</p>
                <div class="mcd-chips">
                  <button
                    v-for="opt in config.chat.options"
                    :key="opt.key"
                    class="mcd-chip"
                    :class="{
                      'is-active': config.chat.current === opt.key,
                      'is-busy': switching === `chat:${opt.key}`
                    }"
                    :disabled="!!switching"
                    @click="selectGlobal({key: 'chat', title: '默认对话模型'}, opt)"
                  >
                    <span class="mcd-chip-label">{{ opt.label }}</span>
                    <span v-if="config.chat.current === opt.key" class="mcd-chip-check">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <path d="M4.5 12.5l5 5 10-11" />
                      </svg>
                    </span>
                  </button>
                </div>
              </div>
            </section>
          </div>
        </NTabPane>

        <!-- ═══ 生成与向量：全局 chip 流 ═══ -->
        <NTabPane name="gen" tab="生成与向量">
          <div class="mcd-tab">
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
                    <p v-if="sec.key === 'embed'" class="mcd-embed-warn">
                      切换只改变新请求使用的向量空间：既有向量库 / 知识库不会自动重建，将标记为「陈旧」，需显式重建后才能使用语义搜索与查重。
                    </p>
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
                        <span v-if="config[sec.key].current === opt.key" class="mcd-chip-check">
                          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                            <path d="M4.5 12.5l5 5 10-11" />
                          </svg>
                        </span>
                      </button>
                    </div>
                  </div>
                </div>
              </NSpin>
            </section>
          </div>
        </NTabPane>

        <!-- ═══ 角色差异：点选即存 ═══ -->
        <NTabPane name="role" tab="角色差异">
          <div class="mcd-tab">
            <section class="mcd-sec">
              <header class="mcd-sec-head">
                <div class="mcd-sec-title">按角色配置</div>
                <div class="mcd-sec-sub">覆盖全局配置，仅对该角色的用户生效</div>
                <NTooltip trigger="hover" class="mcd-tip">
                  <template #trigger>
                    <span class="mcd-info">
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <circle cx="12" cy="12" r="9" />
                        <path d="M12 11v5.2" />
                        <path d="M12 7.6v.5" />
                      </svg>
                    </span>
                  </template>
                  <div style="max-width: 340px; line-height: 1.7">
                    用户的角色按 超管 → 管理员 → 普通用户 → 自创角色 的固定顺序查找，第一个配置过的角色整行生效
                    （未填的项跟随全局）；都没有配置时落「其他用户」行；再落全局模型。点选即存，对该角色用户的
                    下一条消息生效。对话模型由「对话」页签统一管理，此处调整会清除该角色存量的任务模型配置。
                  </div>
                </NTooltip>
              </header>
              <NSpin :show="roleLoading">
                <div v-if="rows.length" class="mrd-list">
                  <article v-for="row in rows" :key="row.roleCode" class="mcd-card">
                    <header class="mrd-head">
                      <div class="mrd-id">
                        <span class="mcd-name">{{ row.roleName }}</span>
                        <span class="mrd-code">{{ row.roleCode }}</span>
                      </div>
                      <span v-if="row.saving" class="mrd-saving">保存中…</span>
                    </header>
                    <div class="mrd-grid">
                      <div v-for="c in CATS" :key="c.key" class="mrd-field">
                        <div class="mrd-field-label">
                          {{ c.label }}
                          <NTag v-if="!row[c.valid]" type="warning" size="tiny">所选块已失效，重新选择后将修正</NTag>
                        </div>
                        <NSelect
                          :value="row[c.edit]"
                          :options="selectOptions(c.key)"
                          :render-label="makeRenderLabel(c.key)"
                          size="small"
                          :consistent-menu-width="false"
                          :disabled="row.saving"
                          @update:value="(v: string) => selectRoleBlock(row, c.key, v)"
                        />
                      </div>
                    </div>
                  </article>
                </div>
              </NSpin>
            </section>
          </div>
        </NTabPane>
      </NTabs>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.mcd-tabs {
  scrollbar-gutter: stable;
}

.mcd-tab {
  display: flex;
  flex-direction: column;
  padding-top: 14px;
}

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

.mcd-info {
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  color: var(--ink-4, #94a3b8);
  cursor: help;
}

.mcd-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-3, #64748b);
}

.mcd-divider {
  margin: 18px 0;
  border-top: 1px dashed rgba(30, 64, 175, 0.16);
}

/* ── 玻璃卡片（模式卡 / 角色卡通用） ──────────────────────── */
.mcd-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.1);
  border-radius: 14px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.6),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 6px 20px -10px rgba(30, 64, 175, 0.12);
}

.mcd-card.is-busy {
  opacity: 0.6;
  pointer-events: none;
}

.mcd-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--ink, #0f172a);
}

/* ── 对话模式：动态卡片 grid ─────────────────────────────── */
.mcd-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px;
  align-items: start;
}

.mcd-card-head {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.mcd-card-head :deep(.n-input) {
  flex: 1;
  min-width: 0;
}

.mcd-card-ops {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  flex-shrink: 0;
}

.mcd-card-tag {
  align-self: flex-start;
}

.mcd-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  font-size: 12px;
  line-height: 1;
  color: var(--ink-4, #94a3b8);
  cursor: pointer;
  transition: background 0.14s ease, color 0.14s ease;
}

.mcd-icon-btn:hover:not(:disabled) {
  background: rgba(30, 64, 175, 0.08);
  color: #1e40af;
}

.mcd-icon-btn.is-danger:hover:not(:disabled) {
  background: rgba(220, 38, 38, 0.08);
  color: #dc2626;
}

.mcd-icon-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.mcd-note-line {
  min-height: 22px;
  display: flex;
  align-items: center;
}

.mcd-note {
  font-size: 11px;
  line-height: 1.5;
  color: var(--ink-4, #94a3b8);
  cursor: text;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mcd-note:hover {
  color: var(--ink-3, #64748b);
}

/* 思考强度档位点阵（只读展示） */
.mcd-lv {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}

.mcd-lv-cap {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.04em;
  color: var(--ink-4, #94a3b8);
  margin-right: 2px;
}

.mcd-lv-dot {
  display: inline-flex;
  align-items: center;
  padding: 1px 7px;
  border-radius: 999px;
  background: rgba(30, 64, 175, 0.07);
  border: 1px solid rgba(30, 64, 175, 0.14);
  font-size: 10px;
  color: #1e40af;
}

/* 新增模式：虚线卡 + 行内表单 */
.mcd-card--add {
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: 120px;
  background: transparent;
  border: 1px dashed rgba(30, 64, 175, 0.28);
  box-shadow: none;
  color: #1e40af;
  cursor: pointer;
  font-family: var(--font-body, inherit);
  transition: background 0.14s ease, border-color 0.14s ease;
}

.mcd-card--add:hover:not(:disabled) {
  background: rgba(30, 64, 175, 0.04);
  border-color: rgba(30, 64, 175, 0.5);
}

.mcd-card--add:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.mcd-add-plus {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}

.mcd-add-text {
  font-size: 12px;
  font-weight: 600;
}

.mcd-card--form {
  gap: 8px;
  border-style: dashed;
  border-color: rgba(30, 64, 175, 0.35);
}

.mcd-form-ops {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.mcd-btn {
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

.mcd-btn:hover:not(:disabled) {
  background: rgba(30, 64, 175, 0.12);
  border-color: rgba(30, 64, 175, 0.4);
}

.mcd-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.mcd-btn.is-ghost {
  background: transparent;
  border-color: rgba(100, 116, 139, 0.25);
  color: var(--ink-3, #64748b);
}

/* ── chip 流式选择（生成与向量 / 兜底对话模型） ──────────── */
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

.mcd-embed-warn {
  margin: 0 0 6px;
  font-size: 11px;
  line-height: 1.6;
  color: #b45309;
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
  display: inline-flex;
  align-items: center;
  font-weight: 700;
}

/* ── 角色差异：角色卡片 ─────────────────────────────────── */
.mrd-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
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

.mrd-code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--ink-4, #94a3b8);
}

.mrd-saving {
  flex-shrink: 0;
  font-size: 11px;
  color: #1e40af;
}

.mrd-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
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
