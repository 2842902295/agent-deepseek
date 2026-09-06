<script setup lang="ts">
import { computed } from 'vue';
import { NPopconfirm } from 'naive-ui';
import SvgIcon from '@/components/custom/svg-icon.vue';
import { brand } from '@/constants/brand';
import { fetchUpdateAgentExpert } from '@/service/api';
import type { AgentConnector, AgentExpert, AgentSkill, RoleTier } from '@/service/api';
import TierMultiSelect from '../TierMultiSelect.vue';

/** 专家详情页（连接器页同款设计语言）：居中卡片 + 底部操作栏；召唤为主操作 */
const props = defineProps<{
  expert: AgentExpert;
  myUserId: number | null;
  isAdmin: boolean;
  /** 全量技能（解析绑定 skillKeys 的展示名） */
  skills: AgentSkill[];
  /** 全量连接器（解析绑定 connectorKeys 的展示名） */
  connectors: AgentConnector[];
  /** 可见档位清单（面板统一下发；管理员设「可见范围」用） */
  tiers: RoleTier[];
}>();

const emit = defineEmits<{
  /** 召唤：添加（若未添加）并新建绑定会话 */
  summon: [expert: AgentExpert];
  /** 详情页快捷提问被点击（父级负责补添加/启用 → 召唤 → 填充输入框 → 关面板） */
  tryExample: [expert: AgentExpert, question: string];
  /** 编辑（本人/管理员） */
  edit: [expert: AgentExpert];
  /** 删除（已确认；本人/管理员） */
  delete: [expert: AgentExpert];
  /** 详情页就地保存成功（可见范围等），父级同步列表与选中对象 */
  changed: [expert: AgentExpert];
}>();

/** 「专家」体系称谓随品牌变体：standard=助理 / generic=专家 */
const expertLabel = brand.expertLabel;

const isMine = computed(() => props.myUserId != null && props.expert.userId === props.myUserId);
const canManage = computed(() => props.isAdmin || isMine.value);

// ── 可见范围（仅管理员）：可见档位白名单，空数组=全员；档位间无包含关系，勾选哪些档位就只有哪些档位的用户可见 ──
const tierCodes = computed(() => props.expert.minTierCode || []);
async function onSetTierCodes(codes: string[]) {
  const cur = [...(props.expert.minTierCode || [])].sort();
  const next = [...codes].sort();
  if (cur.length === next.length && cur.every((c, i) => c === next[i])) return;
  const { data, error } = await fetchUpdateAgentExpert(props.expert.id, { minTierCode: codes });
  if (!error && data) emit('changed', data);
  else window.$message?.error('可见范围设置失败');
}

const boundSkills = computed(() => {
  const map = new Map(props.skills.map(s => [s.skillKey, s]));
  return props.expert.skillKeys.map(k => map.get(k)).filter((s): s is AgentSkill => Boolean(s));
});
const boundConnectors = computed(() => {
  const map = new Map(props.connectors.map(c => [c.connectorKey, c]));
  return props.expert.connectorKeys.map(k => map.get(k)).filter((c): c is AgentConnector => Boolean(c));
});

const createdAtStr = computed(() =>
  props.expert.createdAt ? new Date(props.expert.createdAt).toLocaleDateString('zh-CN') : ''
);
</script>

<template>
  <div class="exd">
    <div class="exd-scroll">
      <div class="exd-card">
        <!-- 身份区：图标 + 名称 + 徽标 + 召唤引导 -->
        <div class="exd-id">
          <span class="exd-avatar">
            <SvgIcon :icon="expert.icon || 'mdi:account-tie-outline'" />
          </span>
          <div class="exd-id-main">
            <h2 class="exd-name">
              {{ expert.name }}
              <span v-if="isMine" class="exd-badge exd-badge--mine">我的</span>
              <span v-if="!expert.userEnabled" class="exd-badge exd-badge--off">已禁用</span>
              <span v-if="!expert.isEnabled && (isAdmin || isMine)" class="exd-badge exd-badge--off">未上架</span>
            </h2>
            <div class="exd-sub">
              <span v-if="expert.category">{{ expert.category }}</span>
              <!-- 作者/创建日期对普通用户无意义，仅可管理者展示 -->
              <span v-if="canManage && expert.author">{{ expert.author }} 创建</span>
              <span v-if="canManage && createdAtStr">{{ createdAtStr }}</span>
              <span>@{{ expert.name }} 即可召唤</span>
            </div>
          </div>
        </div>

        <p v-if="expert.description" class="exd-desc">{{ expert.description }}</p>

        <!-- 快捷提问（全体用户可见）：详情页展示全部条目，点一下即召唤该{{ expertLabel }}并把问题填入输入框 -->
        <section v-if="(expert.exampleQuestions || []).length" class="exd-sec">
          <h4 class="exd-sec-title">试试这样问</h4>
          <div class="exd-asks">
            <button
              v-for="q in expert.exampleQuestions"
              :key="q"
              class="exd-ask"
              :title="`@${expert.expertKey} ${q}`"
              @click="emit('tryExample', expert, q)"
            >
              {{ q }}
            </button>
          </div>
        </section>

        <!-- 可见范围（仅管理员）：档位白名单，勾选哪些档位就只有哪些档位的用户可见；不勾选=全部用户 -->
        <div
          v-if="isAdmin"
          class="exd-tier"
          title="可见范围：勾选哪些档位就只有哪些档位的用户可见（档位间无包含关系）；不勾选=全部用户；创建者本人恒可见自己的专家"
        >
          <span>可见范围</span>
          <TierMultiSelect :model-value="tierCodes" :tiers="tiers" @update:model-value="onSetTierCodes" />
        </div>

        <!-- 欢迎语 -->
        <section v-if="expert.welcomeMessage" class="exd-sec">
          <h4 class="exd-sec-title">欢迎语</h4>
          <p class="exd-welcome">{{ expert.welcomeMessage }}</p>
        </section>

        <!-- 绑定技能 -->
        <section class="exd-sec">
          <h4 class="exd-sec-title">绑定技能<span class="exd-sec-n">{{ boundSkills.length }}</span></h4>
          <div v-if="boundSkills.length" class="exd-chips">
            <span v-for="s in boundSkills" :key="s.skillKey" class="exd-chip" :title="s.description || ''">
              <SvgIcon icon="mdi:lightning-bolt-outline" />
              {{ s.name }}
              <!-- @key 由专家自动加载绑定，普通用户无需感知 -->
              <em v-if="canManage">@{{ s.skillKey }}</em>
            </span>
          </div>
          <p v-else class="exd-none">未绑定技能</p>
        </section>

        <!-- 绑定连接器 -->
        <section v-if="connectors.length" class="exd-sec">
          <h4 class="exd-sec-title">绑定连接器<span class="exd-sec-n">{{ boundConnectors.length }}</span></h4>
          <div v-if="boundConnectors.length" class="exd-chips">
            <span v-for="c in boundConnectors" :key="c.connectorKey" class="exd-chip" :title="c.description || ''">
              <SvgIcon icon="mdi:connection" />
              {{ c.name }}
            </span>
          </div>
          <p v-else class="exd-none">未绑定连接器</p>
        </section>

        <!-- 人设与方法论：给 agent 的提示词全文，属技术细节，仅可管理者展示 -->
        <section v-if="canManage" class="exd-sec">
          <h4 class="exd-sec-title">人设与方法论</h4>
          <pre class="exd-instructions">{{ expert.instructions || '（未填写）' }}</pre>
        </section>
      </div>
    </div>

    <!-- 底部操作行：连接器页同款（次要操作居左，主操作居右） -->
    <div class="exd-foot">
      <button v-if="canManage" class="exd-btn" @click="emit('edit', expert)">编辑</button>
      <NPopconfirm
        v-if="canManage"
        positive-text="删除"
        negative-text="取消"
        @positive-click="emit('delete', expert)"
      >
        <template #default>删除{{ expertLabel }}「{{ expert.name }}」后，引用 TA 的任务将降级为通用任务，确定吗？</template>
        <template #trigger>
          <button class="exd-btn exd-btn--danger">删除</button>
        </template>
      </NPopconfirm>
      <span class="exd-foot-spacer" />
      <button class="exd-btn exd-btn--primary" @click="emit('summon', expert)">召唤</button>
    </div>
  </div>
</template>

<style scoped>
.exd {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.exd-scroll {
  flex: 1;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，内容增减不抖 */
  padding: 22px 26px 10px;
  min-height: 0;
}
.exd-scroll::-webkit-scrollbar {
  width: 5px;
}
.exd-scroll::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 3px;
}

/* 居中卡片：与连接器详情页（.cf-card）同一设计语言 */
.exd-card {
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
.exd-id {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}
.exd-avatar {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: var(--accent-soft);
  color: var(--accent);
}
.exd-avatar :deep(svg) {
  width: 28px;
  height: 28px;
}
.exd-id-main {
  flex: 1;
  min-width: 0;
}
.exd-name {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  margin: 0 0 6px;
  font-size: 18px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--ink);
}
.exd-badge {
  font-size: 10px;
  font-weight: 700;
  color: var(--ink-3);
  background: var(--fill-hover);
  border-radius: 10px;
  padding: 2px 7px;
  white-space: nowrap;
}
.exd-badge--mine {
  color: var(--accent);
  background: var(--accent-soft);
}
.exd-badge--off {
  color: #b45309;
  background: rgba(180, 83, 9, 0.09);
}
.exd-sub {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 11.5px;
  color: var(--ink-4);
}
.exd-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.75;
  color: var(--ink-2);
}
/* 可见范围（仅管理员）：琥珀描边表示全局影响（与技能详情页 .sd-tier 同款） */
.exd-tier {
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
.exd-tier > span {
  flex-shrink: 0;
}

/* ── 卡内分节 ───────────────────────────────────── */
.exd-sec {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding-top: 16px;
  border-top: 1px dashed var(--border);
}
.exd-sec-title {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0;
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink-2);
}
.exd-sec-n {
  font-size: 10.5px;
  font-weight: 700;
  color: var(--ink-4);
  background: var(--fill-hover);
  border-radius: 9px;
  padding: 1px 7px;
}
.exd-instructions {
  margin: 0;
  padding: 13px 15px;
  border-radius: 11px;
  background: var(--surface);
  border: 1px solid var(--border);
  font-family: inherit;
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--ink-2);
  white-space: pre-wrap;
  word-break: break-word;
}
.exd-welcome {
  margin: 0;
  padding: 11px 15px;
  border-radius: 11px;
  background: var(--accent-soft);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ink-2);
}
.exd-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.exd-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border-radius: 9px;
  background: var(--surface-strong);
  border: 1px solid var(--border);
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
}
.exd-chip :deep(svg) {
  width: 13px;
  height: 13px;
  color: var(--accent);
}
.exd-chip em {
  font-style: normal;
  font-size: 10.5px;
  font-weight: 500;
  color: var(--ink-4);
}
.exd-none {
  margin: 0;
  font-size: 12px;
  color: var(--ink-4);
}

/* 快捷提问气泡（详情页）：展示全部条目，允许整句换行不截断，点击即问 */
.exd-asks {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.exd-ask {
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
.exd-ask:hover {
  color: var(--ca);
  border-color: color-mix(in srgb, var(--ca) 38%, transparent);
  background: color-mix(in srgb, var(--ca) 8%, transparent);
}

/* ── 底部操作行（连接器页同款） ─────────────────── */
.exd-foot {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 26px;
  border-top: 1px solid var(--line-hair);
  background: var(--surface);
}
.exd-foot-spacer {
  flex: 1;
}
.exd-btn {
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
  background: var(--surface-strong);
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 8px 18px;
  cursor: pointer;
  transition: all 0.15s;
}
.exd-btn:hover {
  background: var(--fill-hover);
  border-color: var(--border-strong);
}
.exd-btn--primary {
  color: var(--on-primary);
  background: var(--grad-brand);
  border-color: transparent;
  box-shadow: var(--shadow-sm);
}
.exd-btn--primary:hover {
  filter: brightness(0.96);
  box-shadow: var(--shadow-md);
}
.exd-btn--danger {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.2);
  background: rgba(220, 38, 38, 0.04);
}
.exd-btn--danger:hover {
  background: rgba(220, 38, 38, 0.1);
  border-color: rgba(220, 38, 38, 0.34);
}
</style>
