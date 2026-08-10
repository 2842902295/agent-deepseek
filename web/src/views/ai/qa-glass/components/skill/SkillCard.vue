<script setup lang="ts">
import { computed } from 'vue';
import { NSwitch, NTooltip, NPopconfirm } from 'naive-ui';
import type { AgentSkill } from '@/service/api';

const props = defineProps<{
  skill: AgentSkill;
  isMine: boolean;
  isAdmin: boolean;
  highlight?: boolean;
}>();

const emit = defineEmits<{
  use: [skill: AgentSkill];
  detail: [skill: AgentSkill];
  download: [skill: AgentSkill];
  remove: [skill: AgentSkill];
  toggle: [skill: AgentSkill];
}>();

// 来源徽章按来源着色（与筛选 chips / 详情页 SOURCE_THEME 同一套色）：
// 官方琥珀实底最强调，其余浅色底保持克制；「我的」仍单独强调
const SOURCE_META: Record<AgentSkill['source'], { label: string; ca: string; ca2: string; tip: string }> = {
  official: { label: '官方', ca: '#d97706', ca2: '#f59e0b', tip: '超管指定的官方技能' },
  derived: { label: '凝练', ca: '#0891b2', ca2: '#22d3ee', tip: '对话中由 AI 凝练入库' },
  curated: { label: '收录', ca: '#0ea5e9', ca2: '#38bdf8', tip: '技能包上传 / 发现安装收录入库' },
  builtin: { label: '内置', ca: '#64748b', ca2: '#94a3b8', tip: '系统自带的初始技能' }
};

const srcMeta = computed(() => SOURCE_META[props.skill.source]);

const visLabel = computed(() =>
  props.skill.visibility === 'public' ? '全员' : props.skill.visibility === 'role' ? '指定角色' : '仅自己'
);

// 与后端 delete_skill 权限对齐：非内置 且（超管/管理员 或 创建者）才可删，否则不展示删除入口
const canDelete = computed(
  () => props.skill.source !== 'builtin' && (props.isAdmin || props.isMine)
);

function onToggle() {
  emit('toggle', props.skill);
}
</script>

<template>
  <div
    class="sk-card"
    :class="{
      'sk-card--mine': isMine,
      'sk-card--off': !skill.isEnabled,
      'sk-card--hl': highlight
    }"
    :style="{ '--src': srcMeta.ca, '--src2': srcMeta.ca2 }"
    @click="emit('detail', skill)"
  >
    <!-- 顶部来源缎带 -->
    <span class="sk-ribbon" />

    <!-- 调用指令：技能本体标识，区别于功能的中文入口名 -->
    <div class="sk-invoke">
      <code class="sk-invoke-key" :title="`@${skill.skillKey}`">
        <span class="sk-invoke-tick">›</span>@{{ skill.skillKey }}
      </code>
      <span class="sk-switch" @click.stop>
        <NSwitch :value="skill.isEnabled" size="small" @update:value="onToggle" />
      </span>
    </div>

    <!-- 中文名（副标题） -->
    <div class="sk-name">
      {{ skill.name }}
      <span v-if="isMine" class="sk-seal">我的</span>
    </div>

    <!-- 描述 -->
    <p class="sk-desc">{{ skill.description || '暂无描述' }}</p>

    <!-- 实体证据：来源 / 版本 / 文件 / 可见性 -->
    <div class="sk-proof">
      <NTooltip trigger="hover" :delay="350">
        <template #trigger>
          <span class="sk-proof-src" :class="{ 'sk-proof-src--official': skill.source === 'official' }">{{ srcMeta.label }}</span>
        </template>
        {{ srcMeta.tip }}
      </NTooltip>
      <span v-if="skill.version" class="sk-proof-item sk-proof-ver">v{{ skill.version }}</span>
      <span v-if="skill.hasFiles" class="sk-proof-item">
        <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M4 3h5l3 3v7a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" stroke-width="1.4" stroke-linejoin="round" /><path d="M9 3v3h3" stroke-width="1.4" stroke-linejoin="round" /></svg>
        {{ skill.fileCount }}
      </span>
      <span class="sk-proof-item sk-proof-vis">{{ visLabel }}</span>
    </div>

    <div v-if="skill.tags && skill.tags.length" class="sk-tags">
      <span v-for="t in skill.tags.slice(0, 3)" :key="t" class="sk-tag">#{{ t }}</span>
      <span v-if="skill.tags.length > 3" class="sk-tag sk-tag-more">+{{ skill.tags.length - 3 }}</span>
    </div>

    <!-- hover 操作 -->
    <div class="sk-actions">
      <button class="sk-act sk-act--go" @click.stop="emit('use', skill)">使用</button>
      <button class="sk-act" @click.stop="emit('detail', skill)">详情</button>
      <NTooltip v-if="skill.hasFiles" trigger="hover" :delay="400">
        <template #trigger>
          <button class="sk-act" @click.stop="emit('download', skill)">下载</button>
        </template>
        导出为 zip 技能包
      </NTooltip>
      <NPopconfirm
        v-if="canDelete"
        positive-text="删除"
        negative-text="取消"
        @positive-click="emit('remove', skill)"
      >
        <template #default>确定删除此技能吗？此操作不可恢复。</template>
        <template #trigger>
          <button class="sk-act sk-act--del" @click.stop>删除</button>
        </template>
      </NPopconfirm>
    </div>
  </div>
</template>

<style scoped>
.sk-card {
  --ca: #1e40af;
  --ca2: #0891b2;
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 15px 16px 12px;
  background: linear-gradient(160deg, rgba(255, 255, 255, 0.9), rgba(255, 255, 255, 0.66));
  border: 1px solid rgba(30, 64, 175, 0.12);
  border-radius: 14px;
  cursor: pointer;
  overflow: hidden;
  transition:
    transform 0.2s cubic-bezier(0.22, 1, 0.36, 1),
    border-color 0.2s,
    box-shadow 0.2s;
}
/* 顶部缎带：来源渐变色，静态不滚动（性能约定） */
.sk-ribbon {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--src), var(--src2));
  opacity: 0.5;
  transition: opacity 0.2s, height 0.2s;
}
.sk-card:hover {
  transform: translateY(-3px);
  border-color: color-mix(in srgb, var(--ca) 34%, transparent);
  box-shadow: 0 14px 30px -12px color-mix(in srgb, var(--ca) 32%, transparent);
}
.sk-card:hover .sk-ribbon {
  opacity: 1;
  height: 4px;
}

/* 「我的」技能：蓝色描边 + 极淡蓝底 + 加粗缎带；指令块实心蓝底白字，一眼可辨 */
.sk-card--mine {
  border-color: color-mix(in srgb, var(--ca) 42%, transparent);
  background: linear-gradient(160deg, color-mix(in srgb, var(--ca) 9%, #fff) 0%, rgba(255, 255, 255, 0.72) 62%);
}
.sk-card--mine .sk-ribbon {
  opacity: 1;
  height: 4px;
}
.sk-card--mine .sk-invoke-key {
  color: #fff;
  background: linear-gradient(135deg, var(--ca), var(--ca2));
  border-color: transparent;
  box-shadow: 0 4px 12px -4px color-mix(in srgb, var(--ca) 55%, transparent);
}
.sk-card--mine .sk-invoke-tick {
  color: rgba(255, 255, 255, 0.85);
}

.sk-card--off {
  opacity: 0.55;
}
.sk-card--off:hover {
  opacity: 0.82;
}
.sk-card--hl {
  animation: sk-pulse 1.8s ease-out;
}
@keyframes sk-pulse {
  0% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--ca) 55%, transparent);
  }
  100% {
    box-shadow: 0 0 0 14px transparent;
  }
}

/* ── 调用指令行：技能主角 ─────────────────────────── */
.sk-invoke {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.sk-invoke-key {
  font-family: 'JetBrains Mono', monospace;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: var(--ca);
  background: color-mix(in srgb, var(--ca) 9%, transparent);
  border: 1px solid color-mix(in srgb, var(--ca) 22%, transparent);
  border-radius: 8px;
  padding: 5px 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.sk-invoke-tick {
  color: var(--ca2);
  margin-right: 3px;
  font-weight: 800;
}
.sk-switch {
  flex-shrink: 0;
}

/* ── 中文名（副标题） ─────────────────────────────── */
.sk-name {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13.5px;
  font-weight: 650;
  line-height: 1.3;
  color: var(--ink-2, #334155);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sk-seal {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.04em;
  padding: 1px 7px;
  border-radius: 20px;
  color: #fff;
  background: linear-gradient(135deg, var(--ca), var(--ca2));
}

/* ── 描述 ─────────────────────────────────────────── */
.sk-desc {
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ink-3, #64748b);
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 40px;
}

/* ── 实体证据行 ───────────────────────────────────── */
.sk-proof {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.sk-proof-src {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.03em;
  padding: 2px 8px;
  border-radius: 20px;
  color: var(--src);
  background: color-mix(in srgb, var(--src) 12%, transparent);
}
/* 官方：琥珀实底胶囊（与「我的」印章同款处理），来源徽章中最强强调 */
.sk-proof-src--official {
  color: #fff;
  background: linear-gradient(135deg, var(--src), var(--src2));
  box-shadow: 0 2px 8px -2px color-mix(in srgb, var(--src) 55%, transparent);
}
.sk-proof-item {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--ink-3, #64748b);
  background: rgba(30, 64, 175, 0.06);
  padding: 2px 7px;
  border-radius: 5px;
}
.sk-proof-ver {
  color: var(--ca);
  font-weight: 700;
  background: color-mix(in srgb, var(--ca) 10%, transparent);
}
.sk-proof-vis {
  margin-left: auto;
}

/* ── 标签 ─────────────────────────────────────────── */
.sk-tags {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
}
.sk-tag {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  font-weight: 700;
  color: #b45309;
  background: rgba(217, 119, 6, 0.09);
  border: 1px solid rgba(217, 119, 6, 0.24);
  padding: 2px 8px;
  border-radius: 20px;
}
.sk-tag-more {
  color: var(--ink-4, #94a3b8);
  background: transparent;
  border-color: rgba(148, 163, 184, 0.3);
  font-weight: 600;
}

/* ── hover 操作：平时不占位，悬停时自卡片底部渐隐浮起（无硬边/阴影/backdrop-filter） ── */
.sk-actions {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  gap: 6px;
  padding: 24px 12px 12px;
  background: linear-gradient(to top, rgba(255, 255, 255, 0.98) 52%, rgba(255, 255, 255, 0));
  opacity: 0;
  transform: translateY(8px);
  pointer-events: none;
  transition: opacity 0.18s ease, transform 0.18s ease;
}
.sk-card:hover .sk-actions {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}
.sk-act {
  font-family: inherit;
  font-size: 12px;
  font-weight: 500;
  padding: 4px 12px;
  border-radius: 7px;
  border: 1px solid rgba(30, 64, 175, 0.14);
  background: transparent;
  color: var(--ink-3, #64748b);
  cursor: pointer;
  transition: all 0.15s;
}
.sk-act:hover {
  background: rgba(30, 64, 175, 0.06);
  color: var(--ink-2, #334155);
}
.sk-act--go {
  background: color-mix(in srgb, var(--ca) 10%, transparent);
  color: var(--ca);
  border-color: color-mix(in srgb, var(--ca) 22%, transparent);
  font-weight: 600;
}
.sk-act--go:hover {
  background: color-mix(in srgb, var(--ca) 17%, transparent);
}
.sk-act--del {
  color: #dc2626;
  border-color: rgba(220, 38, 38, 0.15);
  margin-left: auto;
}
.sk-act--del:hover {
  background: rgba(220, 38, 38, 0.07);
}

/* 触屏：无 hover，操作条回归常规布局常显 */
@media (hover: none) {
  .sk-actions {
    position: static;
    margin-top: 2px;
    padding: 11px 0 0;
    background: transparent;
    border-top: 1px solid rgba(30, 64, 175, 0.07);
    box-shadow: none;
    opacity: 1;
    transform: none;
    pointer-events: auto;
  }
}
</style>
