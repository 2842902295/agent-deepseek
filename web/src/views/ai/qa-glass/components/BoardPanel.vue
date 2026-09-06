<script setup lang="ts">
import {computed, ref} from 'vue';
import BoardShell, {type QaBridge} from '@/views/ai/workflow/modules/board-shell.vue';

/**
 * 伴侣面板：把流程编排 / 应用制作挂进 QA 对话页的右侧薄壳。
 * 内容整体复用画板成品组件 board-shell（与流程编排专页同一份）——板自己的顶栏
 * （标题/新建/切换/版本/分享）即面板的头部，壳不再另加头；
 * 「展开到专页」在 composer 选板弹层，收起在 composer 板 chip 的 ×。
 */
defineProps<{
  workflowKey: string;
  qaBridge?: QaBridge;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'switched', wk: string): void;
  (e: 'created', type: 'board' | 'html'): void;
  (e: 'not-found'): void;
  (e: 'preview-attachment', att: {name: string; src: string}): void;
}>();

const shellRef = ref<InstanceType<typeof BoardShell> | null>(null);

const selectedNodeIds = computed<string[]>(() => shellRef.value?.selectedNodeIds || []);

defineExpose({selectedNodeIds, flushSave: () => shellRef.value?.flushSave()});
</script>

<template>
  <aside class="board-panel">
    <BoardShell
      ref="shellRef"
      :workflow-key="workflowKey"
      :qa-bridge="qaBridge"
      :show-chat-toggle="false"
      :compact="true"
      @not-found="emit('not-found')"
      @board-switched="wk => emit('switched', wk)"
      @create-requested="t => emit('created', t)"
      @back="emit('close')"
      @preview-attachment="att => emit('preview-attachment', att)"
    />
  </aside>
</template>

<style scoped>
.board-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
  position: relative;
  z-index: 5;
  border-left: 1px solid var(--line-hair, rgba(30, 64, 175, 0.08));
  background: var(--paper, #f5f7fb);
}
</style>
