<script setup lang="ts">
const emit = defineEmits<{
  fill: [text: string];
}>();

function goCreate() {
  emit('fill', '帮我创建一个技能：');
}
</script>

<template>
  <div class="sc">
    <div class="sc-scroll">
      <!-- 创建提示（参照定时任务抽屉）：引导用户到会话框中创建 -->
      <div class="sc-hint">
        <div class="sc-hint-text">
          回到任务框，直接告诉 Agent 你想创建的技能，它会帮你生成并保存，之后可以用
          <code>@key</code>
          主动调用。如：
          <span class="sc-hint-ex">「帮我创建一个周报生成技能：每周五汇总本周产出，输出标准格式周报」</span>
          <span class="sc-hint-ex">「创建一个会议纪要技能：把会议记录整理成结论 + 待办清单」</span>
        </div>
        <button class="sc-hint-btn" @click="goCreate">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor"><path d="M8 3v10M3 8h10" stroke-width="1.9" stroke-linecap="round" /></svg>
          到任务中创建
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sc {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.sc-scroll {
  flex: 1;
  overflow-y: auto;
  scrollbar-gutter: stable; /* 预留滚动条槽位，内容增减不抖 */
  padding: 16px 24px 12px;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.sc-scroll::-webkit-scrollbar {
  width: 5px;
}
.sc-scroll::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 3px;
}

/* ─── 创建提示（对齐 TaskDrawer 的 td-hint；与发现页同一套令牌语言） ── */
.sc-hint {
  padding: 14px 16px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 10px;
}
.sc-hint-text {
  font-size: 13px;
  line-height: 1.8;
  color: var(--ink-3, #64748b);
}
.sc-hint-text code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: var(--accent, #1e40af);
  background: var(--accent-soft);
  padding: 1px 5px;
  border-radius: 4px;
}
.sc-hint-ex {
  display: block;
  width: fit-content;
  max-width: 100%;
  margin-top: 6px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--accent);
  background: var(--accent-soft);
  padding: 4px 10px;
  border-radius: 6px;
}
.sc-hint-btn {
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
  background: var(--grad-brand);
  color: var(--on-primary);
  cursor: pointer;
  box-shadow: var(--shadow-sm);
  transition: all 0.15s;
}
.sc-hint-btn:hover {
  filter: brightness(0.96);
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}
</style>
