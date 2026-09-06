<script setup lang="ts">
import { computed } from 'vue';
import { NSwitch, NPopconfirm } from 'naive-ui';
import type { AgentSkill } from '@/service/api';
import { skillIconHtml } from './skill-icon';

/** 技能面板三视图（商店模型）：store 商店 / mine 我的技能 / manage 上架管理 */
export type SkillViewMode = 'store' | 'mine' | 'manage';

const props = defineProps<{
  skill: AgentSkill;
  isMine: boolean;
  isAdmin: boolean;
  /** 所在视图：决定卡片主操作形态（添加/使用、启用禁用开关、上架下架开关） */
  mode: SkillViewMode;
  highlight?: boolean;
  /** 批量管理模式：卡片点击=切换选中（不进详情），开关与操作条禁用 */
  selectable?: boolean;
  selected?: boolean;
}>();

const emit = defineEmits<{
  use: [skill: AgentSkill];
  detail: [skill: AgentSkill];
  download: [skill: AgentSkill];
  /** store=无此动作；mine=移出我的技能；manage=彻底删除（弹层内确认） */
  remove: [skill: AgentSkill];
  /** mine=本人创建且未上架的技能：彻底删除（弹层内确认），与 manage 的删除同走后端删除接口 */
  delete: [skill: AgentSkill];
  /** store=添加；mine=启用/禁用；manage=上架/下架（由父级按视图分支） */
  toggle: [skill: AgentSkill];
  /** manage=设为/取消精选（仅管理员入口） */
  feature: [skill: AgentSkill];
  select: [skill: AgentSkill];
}>();

// 标题前图标：DB 存 svg 源码（agent 生成）或图片 data URI（原图直存），渲染与清洗见 skill-icon
const iconHtml = computed(() => skillIconHtml(props.skill.icon));

// 与后端 delete_skill 权限对齐：非内置 且（超管/管理员 或 创建者）才可删，否则不展示删除入口
const canDelete = computed(
  () => props.skill.source !== 'builtin' && (props.isAdmin || props.isMine)
);

// 上架管理视图的「在架」口径 = isEnabled（统一尺子，无 visibility 机制）。
// 开关/置灰都用这个合成态驱动，避免 private 技能开关显示「已上架」而徽章却是「未上架」的自相矛盾
const isListed = computed(() => props.skill.isEnabled);

// 我的技能视图：本人创建且未上架的技能没有「退回商店」语义，「移除」升级为「删除」——真删除，不可恢复
const hardDelete = computed(() => props.mode === 'mine' && props.isMine && !isListed.value);

// 置灰态：mine 视图=个人禁用；manage 视图=未上架（含已下架与 private 未公开）；store 视图恒为上架中技能不置灰
const isOff = computed(() => {
  if (props.mode === 'mine') return !props.skill.userEnabled;
  if (props.mode === 'manage') return !isListed.value;
  return false;
});

function onToggle() {
  emit('toggle', props.skill);
}

/** mine 视图末位操作：本人未上架技能=彻底删除；其余=移出我的技能 */
function onRemoveOrDelete() {
  if (hardDelete.value) emit('delete', props.skill);
  else emit('remove', props.skill);
}

function onCardClick() {
  if (props.selectable) emit('select', props.skill);
  else emit('detail', props.skill);
}
</script>

<template>
  <div
    class="sk-card"
    :class="{
      'sk-card--off': isOff,
      'sk-card--hl': highlight,
      'sk-card--sel': selectable && selected
    }"
    @click="onCardClick"
  >
    <!-- 批量模式选中角标 -->
    <span v-if="selectable" class="sk-check" :class="{ 'sk-check--on': selected }">
      <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M3 8.5l3.5 3.5L13 4.5" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" /></svg>
    </span>

    <!-- 标题行：图标 + 技能名（+状态徽章）+ 右侧视图控件 -->
    <div class="sk-head">
      <span class="sk-icon" v-html="iconHtml" />
      <div class="sk-name" :title="skill.skillKey">
        <span class="sk-name-text">{{ skill.name }}</span>
        <!-- 与 @ 弹层同口径：本人创建的技能打「我的」标签 -->
        <span v-if="mode === 'mine' && isMine" class="sk-seal" title="我创建的技能">我的</span>
        <span v-if="mode === 'mine' && !skill.userEnabled" class="sk-seal sk-seal--self-off" title="已禁用：完全不加载、@ 不可调用；重新启用即恢复">已禁用</span>
        <!-- 未上架/已下架不再加徽章：置灰卡片 + 右侧「未上架」开关已双重表达，徽章多余占位 -->
        <!-- 精选标记仅上架管理页展示（由管理员维护） -->
        <span v-if="mode === 'manage' && skill.isFeatured" class="sk-seal sk-seal--feat" title="精选技能：展示在商店顶部精选区">★ 精选</span>
      </div>
      <!-- 商店：未添加→「+」图标按钮（点击添加）；已添加→绿勾状态标识（商店无启用/禁用概念） -->
      <span v-if="mode === 'store'" class="sk-slot" @click.stop>
        <button v-if="!skill.isAdded" class="sk-slot-icon sk-slot-icon--add" title="添加到我的技能" @click="emit('toggle', skill)">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-linecap="round">
            <path d="M8 3v10M3 8h10" stroke-width="1.8" />
          </svg>
        </button>
        <span v-else class="sk-slot-icon sk-slot-icon--added" title="已添加到我的技能">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 8.5l3.5 3.5L13 4.5" stroke-width="2" />
          </svg>
        </span>
      </span>
      <!-- 我的技能：启用/禁用开关（禁用=完全不加载、@ 不可调用）；批量模式下隐藏，选中态由角标表达 -->
      <span v-else-if="mode === 'mine' && !selectable" class="sk-switch" :title="skill.userEnabled ? '已启用：点击禁用（完全不加载、不可 @ 调用）' : '已禁用：点击启用'" @click.stop>
        <NSwitch :value="skill.userEnabled" size="small" @update:value="onToggle" />
      </span>
      <!-- 上架管理：上架/下架用「文字 + 滑轨」按钮（白底浮起、带滑钮，明确可点；
           与「我的技能」的 NSwitch 形态不同，避免混淆）。
           开关反映在架态（isEnabled），未上架技能显示「未上架」，一键即上架 -->
      <span v-else-if="mode === 'manage' && !selectable" class="sk-slot" @click.stop>
        <button
          class="sk-shelf"
          :class="isListed ? 'sk-shelf--on' : 'sk-shelf--off'"
          :title="isListed ? '已上架：点击下架（商店撤下、所有用户不可用）' : '未上架：点击上架（上架后全员可见）'"
          @click="emit('toggle', skill)"
        >
          <span class="sk-shelf-label">{{ isListed ? '已上架' : '未上架' }}</span>
          <span class="sk-shelf-track"><span class="sk-shelf-knob" /></span>
        </button>
      </span>
    </div>

    <!-- 描述 -->
    <p class="sk-desc">{{ skill.description || '暂无描述' }}</p>

    <!-- 作者行（仅上架管理视图：管理员浏览全员技能，需要归属信息） -->
    <div v-if="mode === 'manage'" class="sk-author">
      <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor">
        <circle cx="8" cy="5.5" r="2.8" stroke-width="1.5" />
        <path d="M3 14c.8-2.5 2.6-3.7 5-3.7s4.2 1.2 5 3.7" stroke-width="1.5" stroke-linecap="round" />
      </svg>
      {{ skill.author || '官方' }}
    </div>

    <!-- hover 操作（商店卡片无此条：详情=点卡片本身；批量管理模式下隐藏） -->
    <div v-if="!selectable && mode !== 'store'" class="sk-actions">
      <!-- 我的技能：使用（仅启用时）/ 详情 / 下载（仅创建者或管理员，与后端 download_skill 守卫一致）/ 移除 -->
      <template v-if="mode === 'mine'">
        <button v-if="skill.userEnabled" class="sk-act sk-act--go" @click.stop="emit('use', skill)">使用</button>
        <button class="sk-act" @click.stop="emit('detail', skill)">详情</button>
        <button
          v-if="skill.hasFiles && (isAdmin || isMine)"
          class="sk-act"
          title="导出为 zip 技能包"
          @click.stop="emit('download', skill)"
        >下载</button>
        <NPopconfirm
          :positive-text="hardDelete ? '删除' : '移除'"
          negative-text="取消"
          @positive-click="onRemoveOrDelete"
        >
          <template #default>{{ hardDelete ? '删除后不可恢复。确定删除吗？' : '确定移出我的技能吗？可在商店重新添加' }}</template>
          <template #trigger>
            <button class="sk-act sk-act--del" @click.stop>{{ hardDelete ? '删除' : '移除' }}</button>
          </template>
        </NPopconfirm>
      </template>
      <!-- 上架管理：详情 / 精选（仅管理员）/ 删除 -->
      <template v-else>
        <button class="sk-act" @click.stop="emit('detail', skill)">详情</button>
        <button
          v-if="isAdmin"
          class="sk-act sk-act--feat"
          :title="skill.isFeatured ? '从商店顶部精选区撤下' : '展示在商店顶部精选区'"
          @click.stop="emit('feature', skill)"
        >{{ skill.isFeatured ? '取消精选' : '设为精选' }}</button>
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
      </template>
    </div>
  </div>
</template>

<style scoped>
/* --ca/--ca2 由 .sk-panel 令牌块传导（--ca: var(--accent)），ink 下自动塌成墨黑 */
.sk-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 15px 16px 12px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 14px;
  cursor: pointer;
  overflow: hidden;
  transition:
    border-color 0.2s,
    box-shadow 0.2s;
}
.sk-card:hover {
  border-color: color-mix(in srgb, var(--ca) 30%, transparent);
  box-shadow: var(--shadow-md);
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
/* 批量模式选中：蓝色描边强调 */
.sk-card--sel {
  border-color: color-mix(in srgb, var(--ca) 55%, transparent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--ca) 28%, transparent);
}
/* 批量模式选中角标 */
.sk-check {
  position: absolute;
  top: 9px;
  right: 9px;
  z-index: 2;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  border: 1.5px solid var(--border-strong);
  background: var(--surface-strong);
  color: transparent;
  transition: all 0.15s;
}
.sk-check--on {
  border-color: transparent;
  background: var(--ca);
  color: var(--on-primary);
}
@keyframes sk-pulse {
  0% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--ca) 55%, transparent);
  }
  100% {
    box-shadow: 0 0 0 14px transparent;
  }
}

/* ── 标题行：图标 + 技能名 + 右侧视图控件 ───────────── */
.sk-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
/* 图标瓦片：svg 用主题色着色（兜底星），data URI 图片铺满圆角 */
.sk-icon {
  flex-shrink: 0;
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 11px;
  background: var(--fill-hover);
  color: var(--ca);
  overflow: hidden;
}
.sk-icon :deep(svg) {
  width: 22px;
  height: 22px;
  display: block;
}
.sk-icon :deep(img) {
  /* 图片略内缩并自带小圆角，与瓦片外圆角形成套叠层次 */
  width: calc(100% - 8px);
  height: calc(100% - 8px);
  object-fit: cover;
  display: block;
  border-radius: 6px;
}
.sk-switch {
  flex-shrink: 0;
}
/* 商店视图常驻槽位：未添加=「+」/ 已添加=绿勾 */
.sk-slot {
  flex-shrink: 0;
}
.sk-slot-icon {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
}
/* 「+」添加：朴素描边圆形按钮，hover 才染主题色（不再实底强调） */
.sk-slot-icon--add {
  border: 1px solid var(--border-strong);
  color: var(--ink-3);
  background: var(--surface-strong);
  cursor: pointer;
  transition: all 0.15s;
}
.sk-slot-icon--add:hover {
  color: var(--ca);
  border-color: color-mix(in srgb, var(--ca) 40%, transparent);
  background: color-mix(in srgb, var(--ca) 8%, transparent);
}
/* 已添加 = 状态标识：浅绿底绿勾（承接旧「已添加」印章配色），不可点 */
.sk-slot-icon--added {
  color: #047857;
  background: rgba(5, 150, 105, 0.12);
}

/* 上架开关（上架管理页专用）：白底按钮 + 文字 + 迷你滑轨，明确的「可点」形态，
   与「我的技能」的 NSwitch 视觉完全不同；绿轨=已上架 / 灰轨=已下架，点按切换 */
.sk-shelf {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: inherit;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.02em;
  border-radius: 20px;
  padding: 4px 7px 4px 12px;
  cursor: pointer;
  background: #fff;
  border: 1px solid var(--border-strong);
  box-shadow: var(--shadow-sm);
  transition: all 0.15s;
}
.sk-shelf:hover {
  transform: translateY(-1px);
  border-color: color-mix(in srgb, var(--ca) 34%, transparent);
  box-shadow: var(--shadow-md);
}
.sk-shelf:active {
  transform: translateY(0);
}
.sk-shelf--on .sk-shelf-label {
  color: #047857;
}
.sk-shelf--off .sk-shelf-label {
  color: var(--ink-3);
}
/* 迷你滑轨：滑钮位置表达状态，悬停即知是开关 */
.sk-shelf-track {
  position: relative;
  width: 26px;
  height: 14px;
  border-radius: 8px;
  background: #cbd5e1;
  flex-shrink: 0;
  transition: background 0.18s;
}
.sk-shelf--on .sk-shelf-track {
  background: #059669;
}
.sk-shelf-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.3);
  transition: transform 0.18s cubic-bezier(0.22, 1, 0.36, 1);
}
.sk-shelf--on .sk-shelf-knob {
  transform: translateX(12px);
}

/* ── 技能名（标题，占满剩余宽度、超长截断） ─────────── */
.sk-name {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 14px;
  font-weight: 700;
  line-height: 1.3;
  color: var(--ink);
}
.sk-name-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
/* 印章统一「浅底+深字」，与来源徽章同格式 */
.sk-seal {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.04em;
  padding: 1px 7px;
  border-radius: 20px;
  color: var(--ca);
  background: color-mix(in srgb, var(--ca) 12%, transparent);
}
/* 个人禁用（我的技能视图）：浅灰底弱化；禁用=完全不加载、@ 不可调用 */
.sk-seal--self-off {
  color: #64748b;
  background: rgba(100, 116, 139, 0.12);
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

/* ── 精选/作者（上架管理视图专属信息） ───────────────── */
/* 精选印章：暖金色，与常规徽章区分 */
.sk-seal--feat {
  color: #b45309;
  background: rgba(217, 119, 6, 0.1);
}
/* 作者行：管理员浏览全员技能时的归属标识 */
.sk-author {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--ink-4, #94a3b8);
}
.sk-author svg {
  flex-shrink: 0;
}

/* ── hover 操作：平时不占位，悬停时自卡片底部渐隐浮起（无硬边/阴影/backdrop-filter） ── */
.sk-actions {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 24px 12px 12px;
  background: linear-gradient(to top, var(--paper) 55%, transparent);
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
  border: 1px solid var(--border);
  background: transparent;
  color: var(--ink-3, #64748b);
  cursor: pointer;
  transition: all 0.15s;
  /* 按钮文字不压缩不变形：放不下就整行换行（.sk-actions 已 flex-wrap） */
  white-space: nowrap;
  flex-shrink: 0;
}
.sk-act:hover {
  background: var(--fill-hover);
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
.sk-act--feat {
  color: #b45309;
  border-color: rgba(217, 119, 6, 0.22);
}
.sk-act--feat:hover {
  background: rgba(217, 119, 6, 0.08);
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
    border-top: 1px solid var(--line-hair);
    box-shadow: none;
    opacity: 1;
    transform: none;
    pointer-events: auto;
  }
}
</style>
