<script setup lang="ts">
import {computed, onBeforeUnmount, onMounted, ref} from 'vue';

defineOptions({
  name: 'FpsMeter'
});

/** 采样窗口：每 500ms 结算一次帧数 */
const SAMPLE_MS = 500;
/** 趋势线保留的历史样本数 */
const HISTORY = 40;

const fps = ref(0);
const history = ref<number[]>([]);
/** 观测到的最高帧率 ≈ 显示器刷新率，作为健康度基准（兼容 60/120/165Hz） */
const peak = ref(60);

let raf = 0;
let timer: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  let frames = 0;
  let last = performance.now();
  const loop = () => {
    frames += 1;
    raf = requestAnimationFrame(loop);
  };
  raf = requestAnimationFrame(loop);
  timer = setInterval(() => {
    const now = performance.now();
    const current = Math.round((frames * 1000) / (now - last));
    frames = 0;
    last = now;
    fps.value = current;
    peak.value = Math.max(peak.value, current);
    history.value = [...history.value.slice(-(HISTORY - 1)), current];
  }, SAMPLE_MS);
});

onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
  if (timer) clearInterval(timer);
});

type Health = 'good' | 'warn' | 'bad';
const health = computed<Health>(() => {
  if (fps.value >= peak.value * 0.9) return 'good';
  if (fps.value >= peak.value * 0.55) return 'warn';
  return 'bad';
});

/** SVG 折线点串：按 peak 归一化，贴顶为满帧 */
const sparkPoints = computed(() =>
  history.value.map((v, i) => `${(i * 80) / Math.max(HISTORY - 1, 1)},${18 - Math.min(v / peak.value, 1) * 17}`).join(' ')
);
</script>

<template>
  <div class="fps-meter" :class="`fps-${health}`" aria-hidden="true">
    <svg class="fps-spark" viewBox="0 0 80 18" preserveAspectRatio="none">
      <polyline :points="sparkPoints" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" />
    </svg>
    <span class="fps-num">{{ fps || '--' }}</span>
    <span class="fps-unit">FPS</span>
  </div>
</template>

<style scoped>
.fps-meter {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 9999;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 30px;
  padding: 0 10px;
  border-radius: 9px;
  background: rgba(15, 23, 42, 0.82);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.08),
    0 4px 16px -4px rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(8px);
  font-family: 'JetBrains Mono', monospace;
  pointer-events: none;
  user-select: none;
  animation: fps-in 0.3s ease;
}

.fps-good { color: #4ade80; }
.fps-warn { color: #fbbf24; }
.fps-bad  { color: #f87171; }

.fps-spark {
  width: 64px;
  height: 18px;
  opacity: 0.9;
}

.fps-num {
  min-width: 2ch;
  text-align: right;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.fps-unit {
  font-size: 9px;
  font-weight: 600;
  opacity: 0.55;
  letter-spacing: 0.08em;
}

@keyframes fps-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
}
</style>
