<script setup lang="ts">
import { ref } from 'vue';
import SvgIcon from '@/components/custom/svg-icon.vue';
import { brand } from '@/constants/brand';

/** 「专家」体系称谓随品牌变体：standard=助理 / generic=专家 */
const expertLabel = brand.expertLabel;

/**
 * 输入框专家位（composer-actions 内）：
 * - 有驻留专家：展示图标 + 专家全名；叉在图标位（悬停盖住图标），仅点叉才移除回到通用会话（整块不可点）
 * - 无专家：「专家」按钮，弹层列出已添加的专家供召唤（绑定当前会话）
 */
defineProps<{
  /** 当前会话驻留专家；null=通用会话 */
  expert: { key: string; name: string; icon?: string | null } | null;
  /** 已添加且启用的专家（「专家」按钮弹层候选） */
  candidates: Array<{ key: string; name: string; icon?: string | null }>;
}>();

const emit = defineEmits<{
  /** 叉掉专家：回到通用会话 */
  remove: [];
  /** 从候选召唤一位专家（绑定当前会话） */
  summon: [expertKey: string];
  /** 召唤更多：打开助理商店（技能面板专家页） */
  openShop: [];
}>();

const open = ref(false);
</script>

<template>
  <!-- 有驻留专家：图标 + 全名常驻展示；叉在图标位，悬停时盖住图标，仅点叉才移除（整块不可点，避免误删） -->
  <span v-if="expert" class="exbar" :title="`当前任务${expertLabel}：${expert.name}`">
    <button class="exbar-slot" :title="`移除${expertLabel}，回到通用任务`" @click="emit('remove')">
      <span class="exbar-icon">
        <SvgIcon :icon="expert.icon || 'mdi:account-tie-outline'" />
      </span>
      <span class="exbar-x" aria-hidden="true">
        <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor">
          <path d="M4 4l8 8M12 4l-8 8" stroke-width="1.8" stroke-linecap="round" />
        </svg>
      </span>
    </button>
    <span class="exbar-name">{{ expert.name }}</span>
  </span>

  <!-- 无专家：召唤入口 -->
  <div v-else class="exbar-wrap">
    <button class="exbar-btn" :title="`召唤一位${expertLabel}为本任务服务`" @click="open = !open">
      <SvgIcon icon="mdi:account-tie-outline" />
      {{ expertLabel }}
    </button>
    <template v-if="open">
      <div class="exbar-backdrop" @click="open = false" />
      <div class="exbar-pop">
        <div class="exbar-pop-head">
          <span class="exbar-pop-title">召唤{{ expertLabel }}</span>
          <span class="exbar-pop-hint">绑定当前任务</span>
        </div>
        <div class="exbar-pop-list">
          <button
            v-for="c in candidates"
            :key="c.key"
            class="exbar-item"
            @click="
              open = false;
              emit('summon', c.key);
            "
          >
            <span class="exbar-item-icon">
              <SvgIcon :icon="c.icon || 'mdi:account-tie-outline'" />
            </span>
            <span class="exbar-item-name" :title="c.name">{{ c.name }}</span>
          </button>
          <div v-if="!candidates.length" class="exbar-empty">还没有添加{{ expertLabel }}，点击下方按钮去{{ expertLabel }}商店添加</div>
        </div>
        <div class="exbar-pop-foot">
          <button
            class="exbar-more"
            :title="`打开${expertLabel}商店，添加更多${expertLabel}`"
            @click="
              open = false;
              emit('openShop');
            "
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 8v8M8 12h8" />
            </svg>
            召唤更多{{ expertLabel }}
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 有专家：图标 + 全名常驻展示（整块不可点）；叉在图标位，悬停盖住图标，仅点叉移除 */
.exbar {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border-radius: 20px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent, #1e40af);
  max-width: 220px;
}
/* 图标位：常驻图标 + 悬停盖上的叉，同位叠放 */
.exbar-slot {
  position: relative;
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.exbar-icon {
  display: inline-flex;
  align-items: center;
  color: var(--accent, #1e40af);
  transition: opacity 0.12s;
}
.exbar-icon :deep(svg) {
  width: 13px;
  height: 13px;
}
.exbar-x {
  position: absolute;
  inset: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--surface-1, rgba(241, 245, 249, 1));
  color: var(--ink-3);
  opacity: 0;
  transition: opacity 0.15s, color 0.12s;
}
.exbar:hover .exbar-x,
.exbar-slot:focus-visible .exbar-x {
  opacity: 1;
}
.exbar-slot:hover .exbar-x {
  color: #dc2626;
}
/* 触屏没有 hover：保持弱可见，保证可点 */
@media (hover: none) {
  .exbar-x {
    opacity: 0.55;
  }
}
.exbar-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 无专家：召唤按钮 + 弹层 */
.exbar-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.exbar-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: none;
  background: transparent;
  padding: 3px 8px;
  cursor: pointer;
  border-radius: 20px;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3);
  transition: background 0.15s, color 0.15s;
}
.exbar-btn:hover {
  background: var(--fill-hover);
  color: var(--ink);
}
.exbar-btn :deep(svg) {
  width: 13px;
  height: 13px;
}
.exbar-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1499;
}
.exbar-pop {
  position: absolute;
  bottom: calc(100% + 10px);
  left: 0;
  z-index: 1500;
  width: 240px;
  max-height: 320px;
  display: flex;
  flex-direction: column;
  background: var(--surface-strong);
  border: 1px solid var(--border-strong);
  border-radius: 12px;
  box-shadow: var(--shadow-md);
  backdrop-filter: blur(14px);
  overflow: hidden;
}
.exbar-pop-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 10px 13px 8px;
  border-bottom: 1px solid var(--line-hair);
}
.exbar-pop-title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink);
}
.exbar-pop-hint {
  font-size: 10.5px;
  color: var(--ink-4);
}
.exbar-pop-list {
  flex: 1;
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding: 4px 6px;
}
.exbar-item {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 7px 8px;
  border: none;
  border-radius: 9px;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.12s;
}
.exbar-item:hover {
  background: var(--fill-hover);
}
.exbar-item-icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 7px;
  background: var(--fill-hover);
  color: var(--accent, #1e40af);
}
.exbar-item-icon :deep(svg) {
  width: 13px;
  height: 13px;
}
.exbar-item-name {
  flex: 1;
  min-width: 0;
  text-align: left;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.exbar-empty {
  padding: 14px 10px;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--ink-4);
  text-align: center;
}
/* 弹层底部：召唤更多助理（打开助理商店） */
.exbar-pop-foot {
  border-top: 1px solid var(--line-hair);
  padding: 6px;
}
.exbar-more {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  width: 100%;
  padding: 7px 8px;
  border: none;
  border-radius: 9px;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-3);
  transition: background 0.12s, color 0.12s;
}
.exbar-more:hover {
  background: var(--fill-hover);
  color: var(--accent, #1e40af);
}
</style>
