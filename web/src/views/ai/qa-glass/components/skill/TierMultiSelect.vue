<script setup lang="ts">
import { computed } from 'vue';
import SvgIcon from '@/components/custom/svg-icon.vue';
import type { RoleTier } from '@/service/api';

/** 可见范围多选（手写组件，不依赖 naive-ui）
 *  - modelValue = 已勾选的档位 code 数组；空数组 = 全部用户可见
 *  - 档位之间无包含关系：勾选哪些档位，就只有这些档位的用户可见；作者本人恒可见 */
const props = defineProps<{
  modelValue: string[];
  /** 档位清单（面板统一下发；tierCode='all' 普通用户档不渲染，空选即全员） */
  tiers: RoleTier[];
}>();

const emit = defineEmits<{
  'update:modelValue': [codes: string[]];
}>();

const options = computed(() => props.tiers.filter(t => t.tierCode !== 'all'));

function toggle(code: string) {
  const next = props.modelValue.includes(code) ? props.modelValue.filter(c => c !== code) : [...props.modelValue, code];
  emit('update:modelValue', next);
}
</script>

<template>
  <div class="tms" role="group" aria-label="可见范围">
    <button
      v-for="t in options"
      :key="t.tierCode"
      type="button"
      class="tms-chip"
      :class="{ 'tms-chip--on': modelValue.includes(t.tierCode) }"
      :title="`勾选后仅「${t.tierName}」可见`"
      @click="toggle(t.tierCode)"
    >
      <span class="tms-check"><SvgIcon icon="mdi:check" /></span>
      {{ t.tierName }}
    </button>
    <span class="tms-hint" :class="{ 'tms-hint--dim': modelValue.length > 0 }">不勾选 = 全部用户可见</span>
  </div>
</template>

<style scoped>
.tms {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.tms-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--border-strong);
  background: var(--surface-strong);
  color: var(--ink-2);
  font-size: 12.5px;
  line-height: 1.4;
  cursor: pointer;
  transition:
    border-color 0.15s,
    background 0.15s,
    color 0.15s;
}
.tms-chip:hover {
  border-color: var(--accent);
  background: var(--fill-hover);
}
.tms-check {
  display: grid;
  place-items: center;
  width: 14px;
  height: 14px;
  border-radius: 4px;
  border: 1px solid var(--border-strong);
  color: transparent;
  flex-shrink: 0;
}
.tms-check :deep(svg) {
  width: 10px;
  height: 10px;
}
.tms-chip--on {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
.tms-chip--on .tms-check {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}
.tms-hint {
  font-size: 11.5px;
  color: var(--ink-4);
  white-space: nowrap;
}
.tms-hint--dim {
  opacity: 0.6;
}
</style>
