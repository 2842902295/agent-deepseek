<script setup lang="ts">
/**
 * 节点徽标（临时协作态）：骑在卡片顶边右上角的小角标（top 负值骑边，
 * 不与卡内右上角 hover 浮现的 ⛶ 全屏按钮 / 铅笔提示抢位）。
 *
 * 三态：new = Agent 本轮新增（紫红渐变 NEW）/ human = 人工编辑过（琥珀铅笔）/ agent = Agent 编辑过（纯紫 EDIT）。
 * 数据来自板级 marks 独立通道（inject('wfMarks')，不进 node.data、不进 agent 上下文），
 * agent 每次写板全量重建 marks → 旧标自然清零，不会堆积。
 *
 * 纯展示：自身拦截 pointer/click/dblclick 不冒泡（不触发卡片落焦点 / 编辑 / 预览手势），仅保留 title 提示。
 */
import {computed} from 'vue';
import WfIcon from './wf-icon.vue';

const props = defineProps<{
  type: 'new' | 'human' | 'agent';
}>();

const tip = computed(() => (props.type === 'new' ? 'Agent 本轮新增' : props.type === 'human' ? '人工编辑过' : 'Agent 编辑过'));
</script>

<template>
  <span class="wf-node-badge" :class="`b-${type}`" :title="tip" @pointerdown.stop @click.stop @dblclick.stop>
    <template v-if="type === 'new'">
      <WfIcon name="plus" :size="8" />NEW
    </template>
    <WfIcon v-else-if="type === 'human'" name="pencil" :size="9" />
    <template v-else>
      <WfIcon name="sparkle" :size="8" />EDIT
    </template>
  </span>
</template>

<style scoped>
.wf-node-badge {
  position: absolute;
  top: -9px;
  right: 14px;
  z-index: 7; /* 高于卡内 ⛶ 全屏按钮（z6），hover 时不被盖住 */
  display: inline-flex;
  gap: 3px; /* 前缀小图标与 NEW / EDIT 字样的间距（单图标徽标不受影响） */
  align-items: center;
  justify-content: center;
  height: 16px;
  min-width: 16px;
  padding: 0 5px;
  border-radius: 8px;
  color: #fff;
  font-size: 9px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: 0.06em;
  box-shadow: 0 2px 6px -2px rgba(15, 23, 42, 0.35); /* 单层柔化阴影（nian 性能约定） */
  pointer-events: auto;
  cursor: default;
}
.wf-node-badge.b-new {
  padding: 0 6px;
  /* 紫→紫红渐变（同 --aurora 的 110deg 方向）：NEW 是第一眼信号，靠色相质感与 EDIT 的纯紫拉开辨识度 */
  background: linear-gradient(110deg, #7c3aed 0%, #a855f7 55%, #d946ef 100%);
}
.wf-node-badge.b-human {
  background: #f59e0b;
}
.wf-node-badge.b-agent {
  padding: 0 6px; /* 与 NEW 同为文字徽标，宽度节奏一致 */
  background: #7c3aed;
}
</style>
