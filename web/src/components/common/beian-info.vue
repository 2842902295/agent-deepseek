<script setup lang="ts">
import { getBrandVariant } from '@/utils/brand-config';

defineOptions({ name: 'BeianInfo' });

const isGeneric = getBrandVariant() === 'generic';
</script>

<template>
  <span v-if="isGeneric" class="beian-group">
    <span class="beian-sep">·</span>
    <a
      href="https://beian.miit.gov.cn/"
      rel="noreferrer"
      target="_blank"
      class="beian-link"
    >京ICP备2026030984号</a>
    <span class="beian-sep">·</span>
    <a
      href="https://beian.mps.gov.cn/#/query/webSearch?code=11010102007757"
      rel="noreferrer"
      target="_blank"
      class="beian-link"
    >
      <img src="/beian-icon.png" alt="" class="beian-icon" />京公网安备11010102007757号
    </a>
  </span>
</template>

<style scoped>
.beian-group {
  display: inline-flex;
  align-items: center;
  gap: 0 0.4em;
}

.beian-sep {
  opacity: 0.6;
}

.beian-link {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: inherit;
  text-decoration: none;
  letter-spacing: 0.1em;
  transition: color 0.15s;
}

.beian-link:hover {
  color: var(--ink-soft, #334155);
}

.beian-icon {
  width: 12px;
  height: 12px;
  object-fit: contain;
  flex-shrink: 0;
  vertical-align: middle;
}
</style>
