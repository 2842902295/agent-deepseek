<script setup lang="ts">
import { ref } from 'vue';
import ModelConfigDrawer from './ModelConfigDrawer.vue';

/**
 * 模型配置入口（仅 R_SUPER 可见，由 QATopBar 引入）。
 * 点击打开统一的模型配置抽屉：上半区全局模型切换（对所有用户生效），
 * 下半区按角色配置（覆盖全局，仅对该角色用户生效）。
 */
const drawerShow = ref(false);
</script>

<template>
  <div class="model-switcher">
    <button class="ms-trigger" title="模型配置：全局切换 / 按角色配置" @click="drawerShow = true">
      <span class="ms-icon">⬡</span>
      <span class="ms-text">模型</span>
    </button>

    <ModelConfigDrawer v-model:show="drawerShow" />
  </div>
</template>

<style scoped>
.model-switcher {
  position: relative;
  display: inline-flex;
}

/* ─── 触发按钮（对齐顶栏玻璃按钮） ─────────────────────────── */
.ms-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  margin-left: 4px;
  background: rgba(255, 255, 255, 0.62);
  border: 1px solid rgba(30, 64, 175, 0.18);
  color: #1e40af;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.005em;
  cursor: pointer;
  border-radius: 9px;
  transition: all 0.2s ease;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4);
}
.ms-trigger:hover {
  border-color: rgba(30, 64, 175, 0.35);
  background: rgba(255, 255, 255, 0.78);
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    0 8px 24px -6px rgba(30, 64, 175, 0.28);
}

.ms-icon {
  font-size: 13px;
  line-height: 1;
}

/* ─── 响应式：手机端隐藏文字，仅留图标 ───────────────────────── */
@media (max-width: 960px) {
  .ms-trigger {
    border-radius: 999px;
    padding: 6px 9px;
    margin-left: 0;
  }
  .ms-text {
    display: none;
  }
}
</style>
