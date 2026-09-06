<script setup lang="ts">
/**
 * 节点全屏预览弹窗（参照附件卡 ⛶ 预览，推广到所有卡型）。
 *
 * ⚠️ 为什么必须是独立子组件：vue-flow 的状态靠 provide/inject 绑定——useVueFlow 会向下 provide 自己创建的状态，
 * 子树里的 VueFlow 组件注入最近的一份。若把弹窗的 useVueFlow 与主画布的放在同一个父组件（index.vue）里，
 * 两次 provide 互相覆盖，主画布与弹窗会绑到同一个状态上——弹窗播种单卡的那一刻主画布也被替换成一张卡。
 * 因此弹窗流程的状态在本组件子树内创建并 provide，与主画布天然隔离（关闭弹窗组件卸载，状态随之销毁）。
 *
 * 弹窗里挂的是该卡的真实节点组件（nodeTypes 由父页传入）：不可拖动 / 连线，但就地编辑完整可用
 * （wfNodeApi 的 inject 沿组件树来自 index.vue，编辑直写主画布节点并自动落库）。
 */
import {computed, nextTick, provide, watch} from 'vue';
import {VueFlow, useVueFlow} from '@vue-flow/core';
import WfIcon from './wf-icon.vue';

// 子树内广播「预览弹窗环境」：WfNode 据此把单击改为直接进编辑、屏蔽双击再开预览（防套娃）
provide('wfPreview', true);

const props = defineProps<{
  /** 要预览的节点（主画布 nodes 里的响应式对象） */
  node: any;
  /** 弹窗头部的可读标签（nodeLabel 派生） */
  label: string;
  /** 节点类型注册表（与主画布同一份） */
  nodeTypes: Record<string, any>;
}>();
const emit = defineEmits<{(e: 'close'): void}>();

// 弹窗专属流程状态：本组件子树内创建并 provide，下方 VueFlow 注入它；与主画布状态零交集
const flow = useVueFlow('wf-node-preview');
const EMPTY_EDGES: any[] = [];

const flowNodes = computed(() => {
  const n = props.node;
  if (!n) return [];
  // 只给干净字段：主流程节点对象可能带 vue-flow 内部态（dimensions / internals 等），带进来会污染弹窗的测量；
  // data 共享引用——主画布上编辑后 computed 自动重算，弹窗同步刷新
  return [{id: String(n.id), type: String(n.type || 'textNode'), position: {x: 0, y: 0}, data: n.data, selected: false}];
});

/** 打开后等弹窗里的卡被 ResizeObserver 量完再 fitView——单卡居中适配，卡片是弹窗的内容主体 */
function fitNode(tries = 8) {
  const n = flow.findNode(String(props.node?.id ?? ''));
  if (n?.dimensions?.width && n?.dimensions?.height) {
    flow.fitView({padding: 0.3, duration: 160});
    return;
  }
  if (tries > 0) setTimeout(() => fitNode(tries - 1), 70);
}
watch(
  () => props.node?.id,
  () => nextTick(() => fitNode()),
  {immediate: true}
);
</script>

<template>
  <div class="wf-nprev-overlay" @click.self="emit('close')">
    <div class="wf-nprev-modal">
      <div class="wf-nprev-head">
        <span class="wf-nprev-title" :title="label">{{ label }}</span>
        <span class="wf-nprev-badge">可编辑</span>
        <button class="wf-nprev-close" title="关闭预览 (Esc)" @click="emit('close')"><WfIcon name="x" :size="14" /></button>
      </div>
      <div class="wf-nprev-body">
        <!--
          elements-selectable 必须开着：vue-flow 只在 selectable / draggable / 有点击监听时才给节点指针事件，
          全关的话节点 pointer-events:none，双击编辑根本收不到事件；自定义节点类型无选中样式，视觉无影响。
          delete-key-code=null：防止选中卡片后 Backspace 把弹窗里唯一的卡误删。
        -->
        <VueFlow
          :nodes="flowNodes"
          :edges="EMPTY_EDGES"
          :node-types="nodeTypes"
          :nodes-draggable="false"
          :nodes-connectable="false"
          elements-selectable
          :delete-key-code="null"
          :zoom-on-scroll="false"
          :zoom-on-pinch="false"
          :pan-on-drag="false"
          :fit-view-on-init="false"
          :default-viewport="{x: 60, y: 40, zoom: 1}"
          :min-zoom="0.35"
          :max-zoom="1.6"
          class="wf-nprev-flow"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ── 节点全屏预览弹窗：参照附件预览的弹窗语言（深色蒙层 + 居中玻璃窗口）。
   内容是该卡的真实节点组件（独立小 VueFlow 放大渲染），预览状态下保持可编辑 ── */
.wf-nprev-overlay {
  position: fixed;
  inset: 0;
  z-index: 320;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.55);
  animation: wf-nprev-fade 0.16s ease-out;
}
@keyframes wf-nprev-fade {
  from { opacity: 0; }
}
.wf-nprev-modal {
  display: flex;
  flex-direction: column;
  width: min(780px, calc(100vw - 64px));
  height: min(560px, calc(100vh - 96px));
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid rgba(255, 255, 255, 0.6);
  border-radius: 16px;
  box-shadow: 0 32px 80px -24px rgba(15, 23, 42, 0.5);
  overflow: hidden;
  animation: wf-nprev-in 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes wf-nprev-in {
  from { opacity: 0; transform: scale(0.97); }
}
.wf-nprev-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px 9px 18px;
  background: rgba(255, 255, 255, 0.78);
  border-bottom: 1px solid var(--border, rgba(30, 64, 175, 0.1));
  flex-shrink: 0;
}
.wf-nprev-title {
  flex: 1;
  min-width: 0;
  font-size: 13.5px;
  font-weight: 700;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wf-nprev-badge {
  flex-shrink: 0;
  font-size: 10.5px;
  font-weight: 600;
  line-height: 1;
  padding: 3px 7px;
  border-radius: 6px;
  background: rgba(37, 99, 235, 0.1);
  border: 1px solid rgba(37, 99, 235, 0.18);
  color: #2563eb;
}
.wf-nprev-close {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 1px solid rgba(30, 64, 175, 0.18);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.66);
  color: #64748b;
  cursor: pointer;
  transition: all 0.18s;
}
.wf-nprev-close:hover {
  color: #1e40af;
  border-color: rgba(30, 64, 175, 0.28);
  box-shadow: 0 4px 12px -4px rgba(30, 64, 175, 0.3);
}
.wf-nprev-body {
  flex: 1;
  min-height: 0;
  position: relative;
  /* 纯净底色不打点阵：卡片是弹窗的内容主体，不是嵌进来的另一块画布 */
  background: #f5f7fb;
}
.wf-nprev-flow {
  width: 100%;
  height: 100%;
}
/* 弹窗里的放大卡不再显示预览入口（防套娃）；「＋」连线入口在预览里也无意义，一并隐藏 */
.wf-nprev-body :deep(.wf-card-fs),
.wf-nprev-body :deep(.wf-add) {
  display: none;
}
</style>
