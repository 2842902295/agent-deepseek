<script setup lang="ts">
import {computed} from 'vue';
import type {PendingItem} from '../composables/useNian';

const props = defineProps<{item: PendingItem}>();

const preview = computed(() => {
  const t = props.item.text.trim();
  return t.length > 120 ? t.slice(0, 120) + '…' : t;
});

const elapsed = computed(() => {
  const s = Math.floor((Date.now() - props.item.submittedAt) / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m`;
});
</script>

<template>
  <article class="pending-card">
    <div class="pc-shimmer" aria-hidden="true" />
    <div class="pc-meta">
      <span class="pc-pill">
        <span class="pc-pulse" />
        <span class="pc-label">PROCESSING</span>
      </span>
      <span class="pc-elapsed">{{ elapsed }}</span>
    </div>
    <p class="pc-preview">{{ preview }}</p>
    <div class="pc-bars" aria-hidden="true">
      <span class="pc-bar pc-bar-1" />
      <span class="pc-bar pc-bar-2" />
      <span class="pc-bar pc-bar-3" />
    </div>
  </article>
</template>

<style scoped>
.pending-card {
  position: relative;
  padding: 14px 16px;
  background: rgba(255, 255, 255, 0.38);
  backdrop-filter: blur(40px) saturate(160%);
  -webkit-backdrop-filter: blur(40px) saturate(160%);
  border: 1px dashed rgba(100, 116, 139, 0.32);
  border-radius: 16px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  color: #334155;
  overflow: hidden;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.9),
    0 4px 16px -8px rgba(100, 116, 139, 0.16);
}

/* 顶部流动高光 */
.pc-shimmer {
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.28) 50%,
    transparent 100%
  );
  background-size: 200% 100%;
  animation: shimmer 2.2s ease-in-out infinite;
  pointer-events: none;
  border-radius: inherit;
}
@keyframes shimmer {
  0%   { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

.pc-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.pc-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px 2px 6px;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid rgba(100, 116, 139, 0.2);
  border-radius: 999px;
}

.pc-pulse {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #64748b;
  animation: pulse-dot 1.4s ease-in-out infinite;
}
@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%       { opacity: 0.4; transform: scale(0.7); }
}

.pc-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: #64748b;
}

.pc-elapsed {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  color: #94a3b8;
  margin-left: auto;
}

.pc-preview {
  margin: 0 0 14px;
  font-size: 13px;
  font-weight: 500;
  color: #475569;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 底部骨架线 */
.pc-bars {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.pc-bar {
  display: block;
  height: 6px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.22);
  animation: bar-pulse 1.8s ease-in-out infinite;
}
.pc-bar-1 { width: 80%; animation-delay: 0s; }
.pc-bar-2 { width: 55%; animation-delay: 0.2s; }
.pc-bar-3 { width: 68%; animation-delay: 0.4s; }

@keyframes bar-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.4; }
}
</style>
