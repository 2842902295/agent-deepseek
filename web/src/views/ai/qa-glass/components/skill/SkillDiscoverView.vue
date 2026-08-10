<script setup lang="ts">
const emit = defineEmits<{
  fill: [text: string];
}>();

function goDiscover() {
  emit('fill', '帮我寻找一个技能：');
}
</script>

<template>
  <div class="sdv">
    <div class="sdv-scroll">
      <!-- 发现提示（参照定时任务抽屉）：引导用户到会话框中描述需求，由 Agent 寻找并安装 -->
      <div class="sdv-hint">
        <div class="sdv-hint-text">
          回到会话框，直接描述你想要的能力，Agent 会帮你从技能市场寻找现成的技能包并安装。如：
          <span class="sdv-hint-ex">「帮我找一个能把会议记录整理成待办清单的技能」</span>
          <span class="sdv-hint-ex">「有没有能自动生成周报的技能？帮我装上」</span>
        </div>
        <button class="sdv-hint-btn" @click="goDiscover">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 1.5l1.6 3.9 4.2.4-3.2 2.7.9 4.1L8 10.2l-3.5 2.4.9-4.1L2.2 5.8l4.2-.4L8 1.5z" stroke-width="1.3" stroke-linejoin="round" /></svg>
          到会话中寻找
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sdv {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.sdv-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 16px 24px 12px;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.sdv-scroll::-webkit-scrollbar {
  width: 5px;
}
.sdv-scroll::-webkit-scrollbar-thumb {
  background: rgba(30, 64, 175, 0.12);
  border-radius: 3px;
}

/* ─── 发现提示（对齐 TaskDrawer 的 td-hint，紫色系）────────────── */
.sdv-hint {
  padding: 14px 16px;
  background: rgba(124, 58, 237, 0.04);
  border: 1px solid rgba(124, 58, 237, 0.1);
  border-radius: 10px;
}
.sdv-hint-text {
  font-size: 13px;
  line-height: 1.8;
  color: var(--ink-3, #64748b);
}
.sdv-hint-ex {
  display: block;
  width: fit-content;
  max-width: 100%;
  margin-top: 6px;
  font-size: 12.5px;
  line-height: 1.5;
  color: #7c3aed;
  background: rgba(124, 58, 237, 0.06);
  padding: 4px 10px;
  border-radius: 6px;
}
.sdv-hint-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 14px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  padding: 9px 18px;
  border-radius: 9px;
  border: transparent;
  background: linear-gradient(135deg, #7c3aed, #6d28d9);
  color: #fff;
  cursor: pointer;
  box-shadow: 0 4px 14px -4px rgba(124, 58, 237, 0.4);
  transition: all 0.15s;
}
.sdv-hint-btn:hover {
  background: linear-gradient(135deg, #6d28d9, #5b21b6);
  transform: translateY(-1px);
  box-shadow: 0 6px 18px -4px rgba(124, 58, 237, 0.5);
}
</style>
