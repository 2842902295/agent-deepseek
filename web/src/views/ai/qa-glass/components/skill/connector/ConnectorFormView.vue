<script setup lang="ts">
import { computed, ref } from 'vue';
import TierMultiSelect from '../TierMultiSelect.vue';
import {
  fetchCreateAgentConnector,
  fetchUpdateAgentConnector,
  fetchTestAgentConnector,
  fetchSetAgentConnectorCredential
} from '@/service/api';
import type { AgentConnector, ConnectorTestResult, RoleTier } from '@/service/api';
import IconPicker from '../IconPicker.vue';
import { customIconHtml } from '../skill-icon';

/** 添加/编辑连接器表单：含「测试连接」（试连 MCP server 并列出工具）。
 *  数据集维度复用本组件：kind 决定新建落库类别，label 换文案 */
const props = withDefaults(
  defineProps<{
    /** null=新建；传值=编辑 */
    connector: AgentConnector | null;
    /** full=创建者/管理员编辑完整定义；credential=普通用户只配置个人凭据 */
    mode?: 'full' | 'credential';
    /** 实体类别：新建时落库（编辑不改类别） */
    kind?: 'connector' | 'dataset';
    /** 实体称谓（展示文案）：连接器（默认）/ 数据集 */
    label?: string;
    /** 管理员：编辑态多一个「可见范围」多选 */
    isAdmin?: boolean;
    /** 可见档位清单（面板统一下发） */
    tiers?: RoleTier[];
  }>(),
  { mode: 'full', kind: 'connector', label: '连接器', isAdmin: false, tiers: () => [] }
);

const credentialOnly = computed(() => props.mode === 'credential');
/** 凭据模式信息卡图标：自定义（svg/图片 data URI）优先，空则模板内回落兜底图标 */
const readonlyIconHtml = computed(() => customIconHtml(props.connector?.icon));

const emit = defineEmits<{
  saved: [connector: AgentConnector];
  /** 快捷提问被点击（父级负责补添加/启用 → 填充输入框 → 关面板；个人凭据缺失提示先配凭据） */
  tryExample: [connector: AgentConnector, question: string];
}>();

const isEdit = computed(() => props.connector != null);

const name = ref(props.connector?.name || '');
/** 图标（svg 源码 / 图片 data URI）；null=未设置，保存时按变化量落库 */
const icon = ref<string | null>(props.connector?.icon || null);
const description = ref(props.connector?.description || '');
/** 快捷提问：一行一条，详情页（本表单）渲染成可点建议气泡（仅完整模式可编辑） */
const askDraft = ref((props.connector?.exampleQuestions || []).join('\n'));
function parseAskList(): string[] {
  return askDraft.value
    .split('\n')
    .map(s => s.trim())
    .filter(Boolean);
}
const transport = ref<'sse' | 'streamable_http'>(props.connector?.transport || 'streamable_http');
const url = ref(props.connector?.url || '');
/** 编辑态：空=不改动（后端语义）；输入=替换；「清除」=传空串清除 */
const apiKey = ref('');
const clearKey = ref(false);
/** 明文查看当前填入的凭据（仅前端展示态，不影响落库） */
const showKey = ref(false);
/** 可见档位白名单仅管理员在编辑态可调（空数组=全员；档位间无包含关系，与未上架口径一致） */
const minTierCodes = ref<string[]>(props.connector?.minTierCode || []);

const saving = ref(false);
const testing = ref(false);
const testResult = ref<ConnectorTestResult | null>(null);
const testError = ref('');

function validBasic(): string {
  if (!name.value.trim()) return `请填写${props.label}名称`;
  if (name.value.trim().length > 64) return '名称不能超过 64 字';
  if (!url.value.trim()) return '请填写 MCP 服务地址';
  try {
    const u = new URL(url.value.trim());
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return '地址仅支持 http/https 协议';
  } catch {
    return '地址格式不正确';
  }
  return '';
}

/** 表单相对编辑对象是否有改动（决定测试连接走 id 还是现值） */
const dirty = computed(() => {
  const c = props.connector;
  if (!c) return true;
  return (
    name.value.trim() !== c.name ||
    (description.value.trim() || '') !== (c.description || '') ||
    transport.value !== c.transport ||
    url.value.trim() !== c.url ||
    apiKey.value !== '' ||
    clearKey.value
  );
});

async function onTest() {
  const err = validBasic();
  if (err) {
    window.$message?.error(err);
    return;
  }
  testing.value = true;
  testResult.value = null;
  testError.value = '';
  try {
    // 编辑且无改动 / 仅凭据模式未输新 key → 用库里的连接器试连（凭据按 本人>共享 解析）；否则用表单现值
    const payload =
      (isEdit.value && !dirty.value) || (credentialOnly.value && !apiKey.value.trim())
        ? { id: props.connector!.id }
        : {
            transport: transport.value,
            url: url.value.trim(),
            ...(apiKey.value ? { api_key: apiKey.value } : {})
          };
    const { data, error } = await fetchTestAgentConnector(payload);
    if (!error && data) {
      testResult.value = data;
    } else if (error) {
      // 后端业务错误原文（4000/超时等文案）优先，兜底 axios message
      const resp = (error as { response?: { data?: { msg?: string } } }).response;
      testError.value = resp?.data?.msg || error.message || '连接失败';
    }
  } finally {
    testing.value = false;
  }
}

async function onSave() {
  const err = validBasic();
  if (err) {
    window.$message?.error(err);
    return;
  }
  saving.value = true;
  try {
    if (credentialOnly.value) {
      // 仅凭据模式：只保存我的个人凭据（空=清除），定义字段不可改
      const { data: saved, error } = await fetchSetAgentConnectorCredential(props.connector!.id, apiKey.value.trim());
      if (!error && saved) {
        window.$message?.success(apiKey.value.trim() ? '凭据已保存' : '凭据已清除');
        emit('saved', saved);
      } else {
        window.$message?.error('保存失败');
      }
      return;
    }
    if (isEdit.value) {
      const data: Parameters<typeof fetchUpdateAgentConnector>[1] = {
        name: name.value.trim(),
        description: description.value.trim(),
        transport: transport.value,
        url: url.value.trim(),
        example_questions: parseAskList()
      };
      // 图标变化量：没变不传；清除了传空串；换了传新值
      if ((icon.value || null) !== (props.connector?.icon || null)) {
        data.icon = icon.value || '';
      }
      if (clearKey.value) data.api_key = '';
      else if (apiKey.value) data.api_key = apiKey.value;
      // 可见范围仅管理员可改（后端同样有 4032 守卫）；空数组=全员
      if (props.isAdmin) data.min_tier_code = minTierCodes.value;
      const { data: saved, error } = await fetchUpdateAgentConnector(props.connector!.id, data);
      if (!error && saved) {
        window.$message?.success('已保存');
        emit('saved', saved);
      } else {
        window.$message?.error('保存失败');
      }
    } else {
      const { data: saved, error } = await fetchCreateAgentConnector({
        name: name.value.trim(),
        description: description.value.trim() || undefined,
        kind: props.kind,
        transport: transport.value,
        url: url.value.trim(),
        ...(icon.value ? { icon: icon.value } : {}),
        ...(apiKey.value ? { api_key: apiKey.value } : {}),
        example_questions: parseAskList()
      });
      if (!error && saved) {
        window.$message?.success('已添加，默认未上架（仅自己可见）');
        emit('saved', saved);
      } else {
        window.$message?.error('保存失败');
      }
    }
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="cf">
    <div class="cf-scroll">
      <div class="cf-card">
        <!--
          凭据模式（普通用户配凭据）：顶部只读信息卡即可，定义字段不展示；
          完整模式（创建者/管理员）：图标+名称+描述可编辑
        -->
        <div v-if="credentialOnly" class="cf-readonly">
          <span class="cf-readonly-icon" :class="{ 'cf-readonly-icon--custom': readonlyIconHtml }" aria-hidden="true">
            <span v-if="readonlyIconHtml" v-html="readonlyIconHtml"></span>
            <!-- 兜底图标与列表卡片同套：数据集=圆柱库，连接器=插头 -->
            <svg v-else-if="kind === 'dataset'" width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
              <ellipse cx="8" cy="3.6" rx="5" ry="1.9" />
              <path d="M3 3.6v8.8c0 1.05 2.24 1.9 5 1.9s5-.85 5-1.9V3.6" />
              <path d="M3 8c0 1.05 2.24 1.9 5 1.9S13 9.05 13 8" />
            </svg>
            <svg v-else width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M6 2v3M10 2v3" />
              <path d="M4 5h8v2.5a4 4 0 0 1-4 4 4 4 0 0 1-4-4V5z" />
              <path d="M8 11.5V14" />
            </svg>
          </span>
          <div class="cf-readonly-main">
            <div class="cf-readonly-name">{{ connector?.name }}</div>
            <p v-if="connector?.description" class="cf-readonly-desc">{{ connector.description }}</p>
          </div>
        </div>
        <template v-else>
          <div class="cf-field">
            <label class="cf-label">图标</label>
            <IconPicker :icon="icon" @update:icon="v => (icon = v)" />
          </div>

          <div class="cf-field">
            <label class="cf-label">名称 <em>*</em></label>
            <input v-model="name" class="cf-input" maxlength="64" placeholder="如：百炼联网搜索" />
          </div>

          <div class="cf-field">
            <label class="cf-label">描述</label>
            <input v-model="description" class="cf-input" maxlength="200" :placeholder="`一句话说明这个${label}提供什么能力`" />
          </div>

          <div class="cf-field">
            <label class="cf-label">设置快捷提问</label>
            <textarea v-model="askDraft" class="cf-textarea" rows="3" placeholder="一行一句，最多 6 句，点一下就能问出口" />
            <p class="cf-hint">会以可点的建议气泡展示在详情页，用户点一下即自动添加该{{ label }}并把问题填入输入框。</p>
          </div>
        </template>

        <!-- 快捷提问（全体用户可见）：详情页展示全部条目，点一下即添加该{{ label }}并把问题填入输入框 -->
        <div v-if="connector && (connector.exampleQuestions || []).length" class="cf-field">
          <label class="cf-label">试试这样问</label>
          <div class="cf-asks">
            <button
              v-for="q in connector.exampleQuestions"
              :key="q"
              class="cf-ask"
              :title="`@${connector.connectorKey} ${q}`"
              @click="emit('tryExample', connector, q)"
            >
              {{ q }}
            </button>
          </div>
        </div>

        <!-- 传输方式/服务地址属技术细节：凭据模式不展示 -->
        <div v-if="!credentialOnly" class="cf-row">
          <div class="cf-field cf-field--half">
            <label class="cf-label">
              传输方式
              <span class="cf-help" tabindex="0">
                ?
                <span class="cf-help-tip">
                  <b>Streamable HTTP</b>：MCP 新规范的官方标准传输（推荐），本平台内置桥接均为该方式；<br />
                  <b>SSE</b>：MCP 旧版传输，已逐步废弃。<br />
                  两者互不兼容，选错会试连一直等到超时；不确定时先选 Streamable HTTP。
                </span>
              </span>
            </label>
            <div class="cf-seg">
              <button class="cf-seg-btn" :class="[{ 'cf-seg-btn--on': transport === 'streamable_http' }]" @click="transport = 'streamable_http'">
                Streamable HTTP
              </button>
              <button class="cf-seg-btn" :class="[{ 'cf-seg-btn--on': transport === 'sse' }]" @click="transport = 'sse'">SSE</button>
            </div>
          </div>
          <div class="cf-field cf-field--grow">
            <label class="cf-label">服务地址 <em>*</em></label>
            <input v-model="url" class="cf-input cf-input--mono" placeholder="https://example.com/mcp" />
          </div>
        </div>

        <!-- 可见范围（仅管理员编辑态）：可见档位白名单，档位间无包含关系；新建一律全员可见 -->
        <div v-if="isEdit && isAdmin" class="cf-field">
          <label class="cf-label">
            可见范围
            <span class="cf-help" tabindex="0">
              ?
              <span class="cf-help-tip">
                勾选哪些档位，就只有这些档位的用户可见（档位间无包含关系）；不勾选=全部用户可见；作者本人恒可见自己的{{ label }}。<br />
                新建{{ label }}默认全员可见，上架后才对他人产生意义。
              </span>
            </span>
          </label>
          <TierMultiSelect v-model="minTierCodes" :tiers="tiers" />
        </div>

        <div class="cf-field">
          <label class="cf-label">
            凭据
            <span v-if="connector?.hasMyKey" class="cf-label-hint">（已配置）</span>
          </label>
          <div class="cf-key-row">
            <input
              v-model="apiKey"
              class="cf-input cf-input--mono"
              :type="showKey ? 'text' : 'password'"
              autocomplete="new-password"
              :placeholder="connector?.hasMyKey ? '留空保持不变' : '可选，按 Authorization: Bearer 发送'"
            />
            <button
              v-if="apiKey !== ''"
              class="cf-key-toggle"
              :title="showKey ? '隐藏凭据' : '查看凭据'"
              @click="showKey = !showKey"
            >
              {{ showKey ? '隐藏' : '查看' }}
            </button>
            <button
              v-if="connector?.hasMyKey && !clearKey"
              class="cf-key-clear"
              :class="{ 'cf-key-clear--on': apiKey !== '' }"
              title="清除我的凭据"
              @click="apiKey = ''; clearKey = true"
            >
              清除
            </button>
            <button v-else-if="clearKey" class="cf-key-clear cf-key-clear--on" @click="clearKey = false">
              恢复保留
            </button>
          </div>
          <p v-if="credentialOnly" class="cf-hint">凭据一人一份、互不可见</p>
          <p v-else class="cf-hint">凭据只属于你自己，一人一份、互不可见。服务不需要鉴权就留空。</p>
        </div>

        <!-- 测试连接结果 -->
        <div v-if="testResult" class="cf-test cf-test--ok">
          <div class="cf-test-head">
            <span>连接成功 · {{ testResult.toolCount }} 个工具</span>
            <span v-if="testResult.keySource" class="cf-test-src">
              凭据：{{ {mine: '我的个人凭据', shared: '共享凭据', given: '本次填写', none: '无'}[testResult.keySource] }}
            </span>
          </div>
          <!-- 工具名清单（monospace 技术信息）仅完整模式展示；凭据模式只看「连接成功 · N 个工具」 -->
          <div v-if="!credentialOnly" class="cf-test-tools">
            <span v-for="t in testResult.tools" :key="t.name" class="cf-tool" :title="t.description || ''">{{ t.name }}</span>
          </div>
        </div>
        <div v-else-if="testError" class="cf-test cf-test--err">
          {{ testError }}
        </div>
      </div>
    </div>

    <div class="cf-footer">
      <button class="cf-btn" :disabled="testing || saving" @click="onTest">
        <span v-if="testing" class="cf-spin" />
        {{ testing ? '正在试连…' : '测试连接' }}
      </button>
      <span class="cf-footer-spacer" />
      <button class="cf-btn cf-btn--primary" :disabled="saving || testing" @click="onSave">
        {{ saving ? '保存中…' : credentialOnly ? '保存凭据' : isEdit ? '保存修改' : `添加${label}` }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.cf {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.cf-scroll {
  flex: 1;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，内容增减不抖 */
  padding: 22px 26px 10px;
  min-height: 0;
}
.cf-scroll::-webkit-scrollbar {
  width: 5px;
}
.cf-scroll::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 3px;
}
.cf-card {
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
.cf-field {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.cf-row {
  display: flex;
  gap: 14px;
}
.cf-field--half {
  flex-shrink: 0;
}
.cf-field--grow {
  flex: 1;
  min-width: 0;
}
.cf-label {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink-2);
}
.cf-label em {
  color: #dc2626;
  font-style: normal;
}
/* 传输方式说明：小问号 + 悬停气泡 */
.cf-help {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  margin-left: 5px;
  border: 1px solid var(--border-strong);
  border-radius: 50%;
  font-size: 10px;
  font-weight: 600;
  color: var(--ink-3);
  cursor: help;
  vertical-align: middle;
}
.cf-help-tip {
  display: none;
  position: absolute;
  left: -6px;
  top: calc(100% + 8px);
  z-index: 30;
  width: 264px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-strong);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.12);
  font-size: 12px;
  font-weight: 500;
  line-height: 1.7;
  color: var(--ink-2);
  text-align: left;
  white-space: normal;
  cursor: auto;
}
.cf-help:hover .cf-help-tip,
.cf-help:focus .cf-help-tip {
  display: block;
}
.cf-label-hint {
  font-weight: 500;
  color: var(--ink-4);
}
/* 凭据模式只读信息卡：图标 + 名称 + 描述（普通用户视角无定义字段） */
.cf-readonly {
  display: flex;
  align-items: flex-start;
  gap: 13px;
  padding-bottom: 18px;
  border-bottom: 1px dashed var(--border);
}
.cf-readonly-icon {
  flex-shrink: 0;
  display: grid;
  place-items: center;
  width: 46px;
  height: 46px;
  border-radius: 13px;
  background: var(--accent-soft);
  color: var(--accent);
  overflow: hidden;
}
/* 自定义图标：svg 按 24px 呈现；图片 data URI 铺满圆角瓦片（与列表卡片同一语言） */
.cf-readonly-icon--custom :deep(svg) {
  width: 24px;
  height: 24px;
  display: block;
}
.cf-readonly-icon--custom :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.cf-readonly-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.cf-readonly-name {
  font-size: 16px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--ink);
}
.cf-readonly-desc {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ink-3);
}
.cf-input {
  height: 38px;
  padding: 0 13px;
  font-family: inherit;
  font-size: 13px;
  color: var(--ink);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 10px;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s, background 0.15s;
}
.cf-input:focus {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
  background: #fff;
}
.cf-input--mono {
  font-family: var(--font-mono);
  font-size: 12.5px;
}
.cf-textarea {
  padding: 9px 13px;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 10px;
  outline: none;
  resize: vertical;
  min-height: 74px;
  transition: border-color 0.15s, box-shadow 0.15s, background 0.15s;
}
.cf-textarea:focus {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  box-shadow: 0 0 0 3px var(--accent-soft);
  background: #fff;
}
.cf-hint {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--ink-4);
}
/* 快捷提问气泡（详情页）：展示全部条目，允许整句换行不截断，点击即问 */
.cf-asks {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.cf-ask {
  max-width: 100%;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.5;
  text-align: left;
  padding: 5px 12px;
  border-radius: 20px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  color: var(--ink-3);
  cursor: pointer;
  transition: all 0.15s;
}
.cf-ask:hover {
  color: var(--ca);
  border-color: color-mix(in srgb, var(--ca) 38%, transparent);
  background: color-mix(in srgb, var(--ca) 8%, transparent);
}
.cf-seg {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  background: var(--fill-hover);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 3px;
  height: 38px;
}
.cf-seg-btn {
  font-family: inherit;
  font-size: 12px;
  font-weight: 700;
  color: var(--ink-3);
  background: transparent;
  border: none;
  border-radius: 8px;
  padding: 0 14px;
  height: 100%;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.cf-seg-btn--on {
  color: var(--ink);
  background: var(--surface-strong);
  box-shadow: var(--shadow-sm);
}
.cf-key-row {
  display: flex;
  align-items: center;
  gap: 9px;
}
.cf-key-row .cf-input {
  flex: 1;
}
.cf-key-clear {
  flex-shrink: 0;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.cf-key-clear:hover {
  border-color: var(--border-strong);
  color: var(--ink-2);
}
.cf-key-clear--on {
  color: #b45309;
  border-color: rgba(180, 83, 9, 0.3);
  background: rgba(180, 83, 9, 0.07);
}
.cf-key-toggle {
  flex-shrink: 0;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 13px;
  cursor: pointer;
  transition:
    border-color 0.15s,
    color 0.15s;
}
.cf-key-toggle:hover {
  border-color: var(--border-strong);
  color: var(--ink-2);
}

/* ── 测试结果 ───────────────────────────────────── */
.cf-test {
  border-radius: 10px;
  padding: 12px 14px;
  font-size: 12.5px;
  line-height: 1.6;
}
.cf-test--ok {
  background: rgba(5, 150, 105, 0.06);
  border: 1px solid rgba(5, 150, 105, 0.22);
  color: #047857;
}
.cf-test--err {
  background: rgba(220, 38, 38, 0.05);
  border: 1px solid rgba(220, 38, 38, 0.22);
  color: #b91c1c;
}
.cf-test-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  margin-bottom: 8px;
}
.cf-test-src {
  font-weight: 500;
  font-size: 11.5px;
  color: var(--ink-3, #64748b);
}
.cf-test-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.cf-tool {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-2);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 2px 9px;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 底栏 ───────────────────────────────────────── */
.cf-footer {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 26px;
  border-top: 1px solid var(--line-hair);
  background: var(--surface);
}
.cf-footer-spacer {
  flex: 1;
}
.cf-btn {
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
  padding: 8px 18px;
  cursor: pointer;
  transition: all 0.16s;
}
.cf-btn:hover:not(:disabled) {
  background: var(--fill-hover);
  border-color: var(--border-strong);
}
.cf-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.cf-btn--primary {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}
.cf-btn--primary:hover:not(:disabled) {
  background: var(--grad-brand);
  filter: brightness(0.96);
  box-shadow: var(--shadow-md);
}
.cf-spin {
  width: 12px;
  height: 12px;
  border: 2px solid color-mix(in srgb, currentColor 25%, transparent);
  border-top-color: currentColor;
  border-radius: 50%;
  animation: cf-rotate 0.8s linear infinite;
}
@keyframes cf-rotate {
  to {
    transform: rotate(360deg);
  }
}
</style>
