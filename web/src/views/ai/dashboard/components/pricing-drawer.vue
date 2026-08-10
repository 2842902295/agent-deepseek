<script setup lang="ts">
import {computed, onMounted, reactive, ref, watch} from 'vue';
import {NButton, NInput, NRadioButton, NRadioGroup, useMessage} from 'naive-ui';
import {
  fetchDashboardPricing,
  fetchDashboardPricingHistory,
  upsertDashboardPricing
} from '@/service/api';

const message = useMessage();

const props = defineProps<{ show: boolean }>();
const emit = defineEmits<(e: 'update:show', v: boolean) => void>();

const visible = computed({
  get: () => props.show,
  set: v => emit('update:show', v)
});

const keyword = ref('');
const loading = ref(false);
const items = ref<Api.AI.DashboardPricingItem[]>([]);
const creditRate = ref(0.001);

// ── Token 显示单位切换：1K / 1M ───────────────────────────────────────────────
// 仅影响展示与编辑提示，落库始终是"元 / 单个 token"
type TokenUnit = '1K' | '1M';
const TOKEN_UNIT_LS_KEY = 'pricing-drawer:token-unit';
const tokenUnit = ref<TokenUnit>(
  (typeof localStorage !== 'undefined' && (localStorage.getItem(TOKEN_UNIT_LS_KEY) as TokenUnit)) || '1K'
);
const tokenScale = computed(() => (tokenUnit.value === '1M' ? 1_000_000 : 1000));
const tokenLabel = computed(() => (tokenUnit.value === '1M' ? '1M tokens' : '1K tokens'));
watch(tokenUnit, v => {
  try {
    localStorage.setItem(TOKEN_UNIT_LS_KEY, v);
  } catch {
    /* ignore quota / privacy errors */
  }
});

async function load() {
  loading.value = true;
  try {
    const {data, error} = await fetchDashboardPricing({keyword: keyword.value || undefined});
    if (!error && data) {
      items.value = data.items || [];
      creditRate.value = data.creditRateYuan || 0.001;
    }
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  if (props.show) load();
});

watch(() => props.show, v => {
  if (v) load();
});

// ── unit 元信息：label / 单位 / 配色 ──────────────────────────────────────────
type UnitMeta = { label: string; per: string; tone: string };
// token 类型的 per 用占位 {tk}，运行时由 unitMeta() 替换成当前选中的 1K / 1M
const UNIT_META: Record<string, UnitMeta> = {
  token_in:       {label: '输入',       per: '/ {tk}',     tone: 'blue'},
  token_out:      {label: '输出',       per: '/ {tk}',     tone: 'green'},
  token_cached:   {label: '缓存',       per: '/ {tk}',     tone: 'gray'},
  video_sec_480:  {label: '480P 视频',  per: '/ 秒',       tone: 'orange'},
  video_sec_720:  {label: '720P 视频',  per: '/ 秒',       tone: 'orange'},
  video_sec_1080: {label: '1080P 视频', per: '/ 秒',       tone: 'orange'},
  mcp_call:       {label: '工具调用',   per: '/ 次',       tone: 'purple'}
};
const UNIT_ORDER = ['token_in', 'token_out', 'token_cached', 'video_sec_480', 'video_sec_720', 'video_sec_1080', 'mcp_call'];
const FALLBACK_META: UnitMeta = {label: '', per: '/ 单位', tone: 'gray'};

function unitMeta(unitType: string): UnitMeta {
  const raw = UNIT_META[unitType] || {...FALLBACK_META, label: unitType};
  // 把 token 类的 {tk} 占位替换成当前 tokenLabel
  if (raw.per.includes('{tk}')) {
    return {...raw, per: raw.per.replace('{tk}', tokenLabel.value)};
  }
  return raw;
}
// ── 按 (provider, model) 分组 ────────────────────────────────────────────────
type ModelGroup = {
  key: string;
  provider: string;
  model: string;
  units: Api.AI.DashboardPricingItem[];
  latestEffective: number;
};

const grouped = computed<ModelGroup[]>(() => {
  const map = new Map<string, ModelGroup>();
  for (const it of items.value) {
    const key = `${it.provider}__${it.model}`;
    if (!map.has(key)) {
      map.set(key, {
        key, provider: it.provider, model: it.model,
        units: [], latestEffective: 0
      });
    }
    const g = map.get(key)!;
    g.units.push(it);
    if ((it.effectiveFrom || 0) > g.latestEffective) g.latestEffective = it.effectiveFrom || 0;
  }
  // 卡内 unit 按 UNIT_ORDER 排序，未知 unit 排末尾
  for (const g of map.values()) {
    g.units.sort((a, b) => {
      const ai = UNIT_ORDER.indexOf(a.unitType);
      const bi = UNIT_ORDER.indexOf(b.unitType);
      return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
    });
  }
  // 按 provider 分组，再按 model 字母序
  return [...map.values()].sort((a, b) => {
    if (a.provider !== b.provider) return a.provider.localeCompare(b.provider);
    return a.model.localeCompare(b.model);
  });
});

// 按 provider 二次分组（生成 section 标题）
const byProvider = computed(() => {
  const out: Array<{provider: string; groups: ModelGroup[]}> = [];
  for (const g of grouped.value) {
    let last = out[out.length - 1];
    if (!last || last.provider !== g.provider) {
      last = {provider: g.provider, groups: []};
      out.push(last);
    }
    last.groups.push(g);
  }
  return out;
});

// ── 改价 / 新增 Modal ────────────────────────────────────────────────────────
const editing = reactive({
  open: false,
  provider: '',
  model: '',
  unitType: '',
  priceYuan: '',
  // 编辑态下"按当前 token 单位"展示的价格（仅 token_ 类用）
  priceDisplay: '',
  note: '',
  isNew: false,
  // 新增时是否锁定 provider/model（点 "+ 加 Unit" 时锁定）
  lockProviderModel: false
});

// 编辑态下的展示单位（1K/1M）：token_ 类用 tokenLabel；其它返回 '单位'
const editingTokenInputUnit = computed(() => {
  if (editing.unitType.startsWith('token_')) return tokenLabel.value;
  return '单位';
});
const editingIsToken = computed(() => editing.unitType.startsWith('token_'));

// priceYuan（落库的"元/单 token"） ↔ priceDisplay（"元/1K|1M"）双向同步
function syncDisplayFromYuan() {
  const num = Number(editing.priceYuan);
  if (!editing.priceYuan || Number.isNaN(num)) {
    editing.priceDisplay = editing.priceYuan;
    return;
  }
  if (editingIsToken.value) {
    editing.priceDisplay = String(num * tokenScale.value);
  } else {
    editing.priceDisplay = editing.priceYuan;
  }
}
function syncYuanFromDisplay() {
  const num = Number(editing.priceDisplay);
  if (!editing.priceDisplay || Number.isNaN(num)) {
    editing.priceYuan = editing.priceDisplay;
    return;
  }
  if (editingIsToken.value) {
    // 用足够的精度，避免 0.6 / 1000000 → 6e-7 显示
    editing.priceYuan = (num / tokenScale.value).toFixed(12).replace(/0+$/, '').replace(/\.$/, '');
  } else {
    editing.priceYuan = editing.priceDisplay;
  }
}

// 切换 1K / 1M 时，已打开的编辑框跟着重算 display
watch(tokenUnit, () => {
  if (editing.open && editingIsToken.value) syncDisplayFromYuan();
});

function openEditPrice(row: Api.AI.DashboardPricingItem) {
  editing.provider = row.provider;
  editing.model = row.model;
  editing.unitType = row.unitType;
  editing.priceYuan = String(row.priceYuan);
  editing.note = '';
  editing.isNew = false;
  editing.lockProviderModel = true;
  editing.open = true;
  syncDisplayFromYuan();
}

function openAddUnit(group: ModelGroup) {
  editing.provider = group.provider;
  editing.model = group.model;
  editing.unitType = '';
  editing.priceYuan = '';
  editing.note = '';
  editing.isNew = true;
  editing.lockProviderModel = true;
  editing.open = true;
  syncDisplayFromYuan();
}

function openAddModel() {
  editing.provider = '';
  editing.model = '';
  editing.unitType = '';
  editing.priceYuan = '';
  editing.note = '';
  editing.isNew = true;
  editing.lockProviderModel = false;
  editing.open = true;
  syncDisplayFromYuan();
}

// 新增态下用户在 unitType 框敲入 token_xxx 时，重算 display
watch(() => editing.unitType, () => {
  if (editing.open) syncDisplayFromYuan();
});

async function submitEdit() {
  if (!editing.provider || !editing.model || !editing.unitType) {
    message.warning('Provider / Model / Unit 必填');
    return;
  }
  if (!editing.priceYuan || Number.isNaN(Number(editing.priceYuan))) {
    message.warning('单价必须是数字');
    return;
  }
  const {error} = await upsertDashboardPricing({
    provider: editing.provider.trim(),
    model: editing.model.trim(),
    unitType: editing.unitType.trim(),
    priceYuan: editing.priceYuan,
    note: editing.note.trim() || undefined
  });
  if (!error) {
    message.success(editing.isNew ? '新增成功' : '改价成功（秒级生效）');
    editing.open = false;
    load();
  }
}

// ── 历史 Popover ─────────────────────────────────────────────────────────────
const historyCache = reactive<Record<string, Api.AI.DashboardPricingHistoryItem[]>>({});
const historyLoading = ref<string | null>(null);

async function ensureHistory(row: Api.AI.DashboardPricingItem) {
  const key = `${row.provider}__${row.model}__${row.unitType}`;
  if (historyCache[key]) return key;
  historyLoading.value = key;
  try {
    const {data, error} = await fetchDashboardPricingHistory({
      provider: row.provider, model: row.model, unitType: row.unitType
    });
    if (!error && data) historyCache[key] = data.items || [];
  } finally {
    historyLoading.value = null;
  }
  return key;
}

function fmtTime(ms: number | null) {
  if (!ms) return '-';
  const d = new Date(ms);
  const pad = (n: number) => (n < 10 ? `0${n}` : `${n}`);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// 价格的"人话"展示：根据当前选中的 token 单位换算
function fmtPriceFriendly(unitType: string, price: number, rate: number): string {
  if (!price) return '0';
  if (unitType.startsWith('token_')) {
    // token 按当前选中的单位（1K / 1M）展示
    const scaled = price * tokenScale.value;
    return `${scaled.toLocaleString(undefined, {maximumFractionDigits: 4})} 元/${tokenLabel.value}`;
  }
  if (unitType.startsWith('video_sec_')) {
    return `${price.toLocaleString(undefined, {maximumFractionDigits: 2})} 元/秒`;
  }
  if (unitType === 'mcp_call') {
    return `${price.toLocaleString(undefined, {maximumFractionDigits: 4})} 元/次`;
  }
  return `${price.toLocaleString(undefined, {maximumFractionDigits: 8})} 元`;
}

function fmtCreditsPerUnit(unitType: string, price: number, rate: number): string {
  if (!price || !rate) return '0';
  const credits = price / rate;
  if (unitType.startsWith('token_')) {
    // token 按当前选中的单位（1K / 1M）展示
    const scaled = credits * tokenScale.value;
    return `${scaled.toLocaleString(undefined, {maximumFractionDigits: 2})} 积分/${tokenLabel.value}`;
  }
  if (unitType.startsWith('video_sec_')) {
    return `${credits.toLocaleString(undefined, {maximumFractionDigits: 2})} 积分/秒`;
  }
  if (unitType === 'mcp_call') {
    return `${credits.toLocaleString(undefined, {maximumFractionDigits: 2})} 积分/次`;
  }
  return `${credits.toLocaleString(undefined, {maximumFractionDigits: 4})} 积分`;
}

// Modal 标题：抽成 computed 避免在模板属性里写复杂三元（vite-plugin-vue-inspector 不喜欢）
const editingTitle = computed(() => {
  if (!editing.isNew) return '改价';
  if (editing.lockProviderModel) return `为 ${editing.model} 加一个 Unit`;
  return '新增模型 / 单价';
});
</script>
<template>
  <NDrawer v-model:show="visible" :width="860" placement="right">
    <NDrawerContent title="单价管理 · Pricing" closable>
      <div class="pd-toolbar">
        <NInput
          v-model:value="keyword"
          placeholder="按 model / provider 搜索"
          size="small"
          clearable
          class="pd-search"
          @keydown.enter="load"
          @clear="load"
        />
        <NButton size="small" @click="load">刷新</NButton>
        <NButton size="small" type="primary" @click="openAddModel">+ 新增模型</NButton>
        <span class="pd-toolbar-sep" />
        <span class="pd-toolbar-label">Token 单位</span>
        <NRadioGroup v-model:value="tokenUnit" size="small">
          <NRadioButton value="1K">1K</NRadioButton>
          <NRadioButton value="1M">1M</NRadioButton>
        </NRadioGroup>
        <span class="pd-rate">1 元 = {{ Math.round(1 / creditRate).toLocaleString() }} 积分</span>
      </div>

      <NSpin :show="loading">
        <div v-if="!byProvider.length && !loading" class="pd-empty">
          暂无数据，请点击右上角「+ 新增模型」开始
        </div>

        <div v-for="section in byProvider" :key="section.provider" class="pd-section">
          <div class="pd-section-head">
            <span class="pd-section-name mono">{{ section.provider }}</span>
            <span class="pd-section-count">{{ section.groups.length }} 个模型</span>
          </div>

          <div class="pd-grid">
            <article
              v-for="g in section.groups"
              :key="g.key"
              class="pd-card"
            >
              <header class="pd-card-head">
                <div class="pd-card-titles">
                  <h3 class="pd-card-model" :title="g.model">{{ g.model }}</h3>
                  <span class="pd-card-meta">
                    {{ g.units.length }} 个计费维度
                    <template v-if="g.latestEffective">
                      · 最近改动 {{ fmtTime(g.latestEffective) }}
                    </template>
                  </span>
                </div>
                <button class="pd-add-unit" type="button" @click="openAddUnit(g)">
                  <svg viewBox="0 0 16 16" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.8">
                    <path d="M8 3v10M3 8h10" stroke-linecap="round" />
                  </svg>
                  加 Unit
                </button>
              </header>

              <div class="pd-units">
                <div
                  v-for="u in g.units"
                  :key="u.id"
                  class="pd-unit"
                  :class="`pd-unit-${unitMeta(u.unitType).tone}`"
                >
                  <div class="pd-unit-head">
                    <span class="pd-unit-label">{{ unitMeta(u.unitType).label || u.unitType }}</span>
                    <span class="pd-unit-key mono">{{ u.unitType }}</span>
                  </div>
                  <div class="pd-unit-price mono">
                    ¥{{ u.priceYuan.toLocaleString(undefined, { maximumFractionDigits: 8 }) }}
                  </div>
                  <div class="pd-unit-friendly mono">
                    {{ fmtPriceFriendly(u.unitType, u.priceYuan, creditRate) }}
                  </div>
                  <div class="pd-unit-credit mono">
                    ≈ {{ fmtCreditsPerUnit(u.unitType, u.priceYuan, creditRate) }}
                  </div>
                  <div class="pd-unit-actions">
                    <button class="pd-link" type="button" @click="openEditPrice(u)">改价</button>
                    <NPopover
                      placement="bottom"
                      trigger="click"
                      :width="320"
                      @update:show="(v: boolean) => v && ensureHistory(u)"
                    >
                      <template #trigger>
                        <button class="pd-link" type="button">历史</button>
                      </template>
                      <div class="pd-pop">
                        <div class="pd-pop-title">
                          {{ unitMeta(u.unitType).label || u.unitType }} · 历史价格
                        </div>
                        <div
                          v-if="historyLoading === `${u.provider}__${u.model}__${u.unitType}`"
                          class="pd-pop-loading"
                        >
                          加载中…
                        </div>
                        <ul
                          v-else
                          class="pd-pop-list"
                        >
                          <li
                            v-for="h in (historyCache[`${u.provider}__${u.model}__${u.unitType}`] || [])"
                            :key="h.id"
                            class="pd-pop-item"
                            :class="h.isCurrent && 'pd-pop-current'"
                          >
                            <div class="pd-pop-row">
                              <span class="mono pd-pop-price">¥{{ h.priceYuan.toLocaleString(undefined, { maximumFractionDigits: 8 }) }}</span>
                              <span v-if="h.isCurrent" class="pd-pop-tag">当前</span>
                            </div>
                            <div class="pd-pop-period mono">
                              {{ fmtTime(h.effectiveFrom) }} → {{ h.effectiveTo ? fmtTime(h.effectiveTo) : '至今' }}
                            </div>
                            <div v-if="h.note" class="pd-pop-note">{{ h.note }}</div>
                          </li>
                          <li
                            v-if="!(historyCache[`${u.provider}__${u.model}__${u.unitType}`] || []).length && historyLoading !== `${u.provider}__${u.model}__${u.unitType}`"
                            class="pd-pop-empty"
                          >
                            暂无历史
                          </li>
                        </ul>
                      </div>
                    </NPopover>
                  </div>
                </div>
              </div>
            </article>
          </div>
        </div>
      </NSpin>

      <NModal
        v-model:show="editing.open"
        preset="card"
        :title="editingTitle"
        style="width: 480px"
      >
        <div class="pd-form">
          <div class="pd-form-row">
            <div class="pd-field">
              <label>Provider</label>
              <NInput
                v-model:value="editing.provider"
                :disabled="editing.lockProviderModel"
                placeholder="dashscope / openai / ..."
              />
            </div>
            <div class="pd-field">
              <label>Model</label>
              <NInput
                v-model:value="editing.model"
                :disabled="editing.lockProviderModel"
                placeholder="qwen-max / happyhorse-1.0-t2v ..."
              />
            </div>
          </div>
          <div class="pd-field">
            <label>Unit Type</label>
            <NInput
              v-model:value="editing.unitType"
              :disabled="!editing.isNew"
              placeholder="token_in / token_out / video_sec_720 / mcp_call"
            />
            <span v-if="editing.unitType && unitMeta(editing.unitType).label" class="pd-hint">
              {{ unitMeta(editing.unitType).label }} {{ unitMeta(editing.unitType).per }}
            </span>
          </div>
          <div class="pd-field">
            <label>
              单价（元 / {{ editingTokenInputUnit }}）
              <span v-if="editingIsToken" class="pd-hint pd-hint-inline">
                落库统一为「元 / 单 token」，输入按当前选中的「{{ tokenLabel }}」单位
              </span>
            </label>
            <NInput
              v-model:value="editing.priceDisplay"
              placeholder="例如 token_in 在 1K 单位下填 0.02；1M 单位下填 20"
              @update:value="syncYuanFromDisplay"
            />
            <span v-if="editing.priceYuan && !Number.isNaN(Number(editing.priceYuan))" class="pd-hint pd-hint-credit">
              落库：¥{{ Number(editing.priceYuan).toLocaleString(undefined, { maximumFractionDigits: 12 }) }} / 单位
              · ≈ {{ fmtCreditsPerUnit(editing.unitType, Number(editing.priceYuan), creditRate) }}
            </span>
          </div>
          <div class="pd-field">
            <label>备注（可选）</label>
            <NInput v-model:value="editing.note" placeholder="例如：2026-06 上游调价" />
          </div>
        </div>
        <template #footer>
          <div class="pd-form-footer">
            <NButton size="small" @click="editing.open = false">取消</NButton>
            <NButton size="small" type="primary" @click="submitEdit">确定</NButton>
          </div>
        </template>
      </NModal>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>

/* ── 设计 token（与 dashboard 主页对齐） ─────────────────────────────────── */
.pd-toolbar, .pd-section, .pd-form, .pd-pop {
  --ink: #0f172a;
  --ink-soft: #334155;
  --ink-mute: #64748b;
  --ink-faint: #94a3b8;
  --line: rgba(30, 64, 175, 0.10);
  --line-strong: rgba(30, 64, 175, 0.18);
  --surface: rgba(255, 255, 255, 0.62);
  --surface-deep: rgba(255, 255, 255, 0.92);
  --gold: #b08900;
  --c-blue: #1e40af;
  --c-cyan: #0891b2;
  --c-green: #047857;
  --c-orange: #b45309;
  --c-purple: #5b21b6;
  --c-gray: #64748b;
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
  color: var(--ink);
}
.mono { font-family: 'JetBrains Mono', monospace; }

/* ── Toolbar ───────────────────────────────────────────────────────────── */
.pd-toolbar {
  display: flex; align-items: center; gap: 10px;
  padding: 4px 4px 18px;
}
.pd-search { max-width: 280px; }
.pd-toolbar-sep {
  width: 1px; height: 20px;
  background: var(--line-strong);
  margin: 0 4px;
}
.pd-toolbar-label {
  font-size: 11px; font-weight: 600;
  color: var(--ink-mute);
  letter-spacing: 0.04em;
}
.pd-rate {
  margin-left: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px; font-weight: 700;
  color: var(--gold);
  padding: 5px 12px;
  border-radius: 8px;
  background: linear-gradient(135deg, rgba(245,158,11,0.12), rgba(176,137,0,0.06));
  border: 1px solid rgba(245,158,11,0.22);
}

/* ── Empty ─────────────────────────────────────────────────────────────── */
.pd-empty {
  text-align: center; color: var(--ink-faint);
  padding: 64px 0; font-size: 13px;
}

/* ── Provider section ─────────────────────────────────────────────────── */
.pd-section { margin-bottom: 28px; }
.pd-section-head {
  display: flex; align-items: baseline; gap: 10px;
  padding: 0 4px 10px;
  border-bottom: 1px dashed var(--line);
  margin-bottom: 14px;
}
.pd-section-name {
  font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--c-blue); text-transform: uppercase;
}
.pd-section-count {
  font-size: 11px; color: var(--ink-faint);
}

/* ── Model card 网格 ──────────────────────────────────────────────────── */
.pd-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 14px;
}
@media (min-width: 720px) {
  .pd-grid { grid-template-columns: repeat(2, 1fr); }
}

.pd-card {
  background: var(--surface);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 14px 14px 12px;
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.6),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 6px 20px -10px rgba(30, 64, 175, 0.12);
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}
.pd-card:hover {
  transform: translateY(-1px);
  border-color: var(--line-strong);
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.6),
    0 1px 2px rgba(15, 23, 42, 0.05),
    0 12px 28px -12px rgba(30, 64, 175, 0.20);
}

.pd-card-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.pd-card-titles { min-width: 0; flex: 1; }
.pd-card-model {
  font-family: 'JetBrains Mono', monospace;
  font-size: 14px; font-weight: 700;
  color: var(--ink);
  margin: 0 0 3px;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pd-card-meta {
  font-size: 10px;
  color: var(--ink-faint);
}
.pd-add-unit {
  display: inline-flex; align-items: center; gap: 4px;
  flex-shrink: 0;
  height: 24px; padding: 0 9px;
  border: 1px dashed var(--line-strong);
  background: transparent;
  border-radius: 999px;
  font-family: inherit;
  font-size: 11px; font-weight: 600;
  color: var(--ink-mute);
  cursor: pointer;
  transition: all 0.15s ease;
}
.pd-add-unit:hover {
  color: var(--c-blue);
  border-color: var(--c-blue);
  border-style: solid;
  background: rgba(30, 64, 175, 0.06);
}

/* ── Unit grid（卡片内部） ──────────────────────────────────────────────── */
.pd-units {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 8px;
}
.pd-unit {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px 11px;
  background: var(--surface-deep);
  display: flex;
  flex-direction: column;
  gap: 2px;
  position: relative;
  transition: border-color 0.15s ease, transform 0.15s ease;
}
.pd-unit:hover { border-color: var(--line-strong); }
.pd-unit-head {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 6px;
  margin-bottom: 2px;
}
.pd-unit-label {
  font-size: 12px; font-weight: 600; color: var(--ink);
}
.pd-unit-key {
  font-size: 9px; color: var(--ink-faint);
  letter-spacing: 0.02em;
}
.pd-unit-price {
  font-size: 13px; font-weight: 700;
  color: var(--ink);
  letter-spacing: -0.01em;
}
.pd-unit-friendly {
  font-size: 10px;
  color: var(--ink-soft);
  font-weight: 600;
}
.pd-unit-credit {
  font-size: 10px;
  color: var(--gold);
  font-weight: 600;
  margin-bottom: 4px;
}
.pd-unit-actions {
  display: flex; gap: 10px;
  margin-top: 4px;
  padding-top: 6px;
  border-top: 1px dashed var(--line);
}

/* unit 配色：左侧 3px 竖条 + 价格颜色暗示资源类型 */
.pd-unit::before {
  content: '';
  position: absolute;
  left: 0; top: 10px; bottom: 10px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: currentColor;
  opacity: 0.7;
}
.pd-unit-blue   { color: var(--c-blue); }
.pd-unit-green  { color: var(--c-green); }
.pd-unit-gray   { color: var(--c-gray); }
.pd-unit-orange { color: var(--c-orange); }
.pd-unit-purple { color: var(--c-purple); }
.pd-unit-blue   .pd-unit-label,
.pd-unit-green  .pd-unit-label,
.pd-unit-gray   .pd-unit-label,
.pd-unit-orange .pd-unit-label,
.pd-unit-purple .pd-unit-label { color: currentColor; }
.pd-unit-blue   .pd-unit-price,
.pd-unit-green  .pd-unit-price,
.pd-unit-gray   .pd-unit-price,
.pd-unit-orange .pd-unit-price,
.pd-unit-purple .pd-unit-price { color: var(--ink); }

/* 文字按钮 */
.pd-link {
  border: 0; background: transparent; padding: 0;
  font-family: inherit;
  font-size: 11px; font-weight: 600;
  color: var(--ink-mute);
  cursor: pointer;
  transition: color 0.15s ease;
}
.pd-link:hover { color: var(--c-blue); }

/* ── 历史 Popover ─────────────────────────────────────────────────────── */
.pd-pop { padding: 4px; }
.pd-pop-title {
  font-size: 11px; font-weight: 700;
  color: var(--ink); margin-bottom: 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--line);
}
.pd-pop-loading, .pd-pop-empty {
  text-align: center; color: var(--ink-faint);
  font-size: 11px; padding: 12px 0;
}
.pd-pop-list {
  list-style: none; padding: 0; margin: 0;
  display: flex; flex-direction: column; gap: 6px;
  max-height: 280px; overflow-y: auto;
}
.pd-pop-item {
  padding: 6px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
}
.pd-pop-current {
  border-color: rgba(16, 185, 129, 0.4);
  background: rgba(16, 185, 129, 0.05);
}
.pd-pop-row {
  display: flex; align-items: center; justify-content: space-between;
}
.pd-pop-price {
  font-size: 12px; font-weight: 700; color: var(--ink);
}
.pd-pop-tag {
  font-size: 9px; font-weight: 700;
  color: #047857;
  background: rgba(16,185,129,0.15);
  padding: 1px 6px; border-radius: 999px;
}
.pd-pop-period {
  font-size: 10px; color: var(--ink-mute);
  margin-top: 2px;
}
.pd-pop-note {
  font-size: 11px; color: var(--ink-faint);
  font-style: italic;
  margin-top: 2px;
}

/* ── Form Modal ───────────────────────────────────────────────────────── */
.pd-form { display: flex; flex-direction: column; gap: 12px; }
.pd-form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.pd-field { display: flex; flex-direction: column; gap: 4px; }
.pd-field label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px; font-weight: 700; letter-spacing: 0.04em;
  color: var(--ink-mute); text-transform: uppercase;
}
.pd-hint {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px; color: var(--ink-mute); margin-top: 2px;
}
.pd-hint-inline {
  font-family: inherit;
  font-size: 11px; font-weight: 400;
  color: var(--ink-faint);
  margin-left: 8px;
}
.pd-hint-credit { color: var(--gold); font-weight: 600; }
.pd-form-footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
