<script setup lang="ts">
/**
 * HTML 看板画布：深度任务「html 型」的画布渲染器。
 *
 * 与流程板（VueFlow）并列的第二种画布：agent 在任务目录开发的多文件 HTML 应用，
 * 通过带签名 token 的托管路由（/api/v1/ai/html-app/{token}/...）以 iframe 呈现。
 *
 * - token 每次进任务签发（父组件以 :key=workflowKey 保证切换任务时整体重建）
 * - version 变化（agent publish_html_board 后父级轮询命中）→ 重载 iframe；
 *   iframe 正被用户交互（焦点/悬停）时延迟重载，浮出「新版本已就绪」横条
 * - entryReady=false（尚未发布过 index.html）→ 占位提示
 */
import {onBeforeUnmount, onMounted, ref, watch} from 'vue';
import {fetchSignHtmlAppToken} from '@/service/api/ai';

const props = defineProps<{
  workflowKey: string;
  /** 父级轮询到的板版本号；agent publish_html_board 会 bump，作为重载触发信号 */
  version: number;
  /** 入口 index.html 是否已发布（父级 fetchWorkflow 回填） */
  entryReady: boolean;
}>();

const emit = defineEmits<{
  /** 占位态「再看看」：请父级重新拉取工作流（刷新 entryReady/version） */
  (e: 'recheck'): void;
}>();

type Phase = 'signing' | 'placeholder' | 'ready' | 'error';

// 托管路由前缀必须与 axios 同一真相源：dev（--mode test）是 http://localhost:9999/api/v1 直连后端（无 vite 代理），
// prod 是 /api/v1 同源走 nginx——写死同源相对路径会在 dev 打到 9527 上 404。
// dev 下 iframe 跨源加载不受限；页内 fetch 相对/绝对路径都解析到 iframe 文档自身源（= 后端），save 同样通。
const serviceBase = import.meta.env.VITE_SERVICE_BASE_URL || '/api/v1';

const phase = ref<Phase>('signing');
const token = ref('');
/** 递增以 :key 强制销毁重建 iframe（比改 src 更彻底地断掉旧文档脚本/定时器） */
const reloadKey = ref(0);
const pendingReload = ref(false);
const pointerIn = ref(false);
const iframeFocused = ref(false);
const iframeEl = ref<HTMLIFrameElement | null>(null);

let reloadTimer: ReturnType<typeof setTimeout> | null = null;

// ── token 签发 ──────────────────────────────────────────────────────────────

async function sign() {
  phase.value = 'signing';
  const {data, error} = await fetchSignHtmlAppToken(props.workflowKey);
  if (error || !data) {
    phase.value = 'error';
    return;
  }
  token.value = data.token;
  phase.value = props.entryReady || data.entryReady ? 'ready' : 'placeholder';
}

// 占位态等到 agent 首次发布（父级轮询把 entryReady 翻 true）→ 直接进 ready
watch(
  () => props.entryReady,
  ready => {
    if (ready && phase.value === 'placeholder') phase.value = 'ready';
  }
);

// ── version 变化 → 重载（500ms 防抖合并连续 publish；交互中延迟）─────────────

watch(
  () => props.version,
  (v, old) => {
    if (v === old || phase.value !== 'ready') return;
    if (reloadTimer) clearTimeout(reloadTimer);
    reloadTimer = setTimeout(scheduleReload, 500);
  }
);

function scheduleReload() {
  if (iframeFocused.value || pointerIn.value) {
    pendingReload.value = true;
  } else {
    doReload();
  }
}

function doReload() {
  pendingReload.value = false;
  reloadKey.value += 1;
}

// 交互解除后自动补上被延迟的重载
watch([pointerIn, iframeFocused], ([inFrame, focused]) => {
  if (!inFrame && !focused && pendingReload.value) doReload();
});

// ── iframe 焦点检测：window blur 时焦点落在 iframe 上 = 用户在页内交互 ──────

function onWindowBlur() {
  iframeFocused.value = iframeEl.value !== null && document.activeElement === iframeEl.value;
}
function onWindowFocus() {
  iframeFocused.value = false;
}

onMounted(() => {
  window.addEventListener('blur', onWindowBlur);
  window.addEventListener('focus', onWindowFocus);
  sign();
});

onBeforeUnmount(() => {
  window.removeEventListener('blur', onWindowBlur);
  window.removeEventListener('focus', onWindowFocus);
  if (reloadTimer) clearTimeout(reloadTimer);
});
</script>

<template>
  <div class="hb-root">
    <iframe
      v-if="phase === 'ready' && token"
      :key="reloadKey"
      ref="iframeEl"
      class="hb-frame"
      :src="`${serviceBase}/ai/html-app/${token}/index.html?v=${version}`"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
      referrerpolicy="no-referrer"
      @mouseenter="pointerIn = true"
      @mouseleave="pointerIn = false"
    />

    <div v-else class="hb-empty">
      <template v-if="phase === 'signing'">
        <p class="hb-empty-title">正在打开应用…</p>
      </template>
      <template v-else-if="phase === 'placeholder'">
        <svg class="hb-empty-art" viewBox="0 0 168 74" fill="none">
          <rect x="8" y="8" width="152" height="58" rx="10" stroke="rgba(30,64,175,0.35)" stroke-width="1.5" />
          <rect x="8" y="8" width="152" height="16" rx="10" fill="rgba(30,64,175,0.06)" />
          <circle cx="18" cy="16" r="2.5" fill="rgba(30,64,175,0.35)" />
          <circle cx="27" cy="16" r="2.5" fill="rgba(30,64,175,0.25)" />
          <circle cx="36" cy="16" r="2.5" fill="rgba(30,64,175,0.18)" />
          <rect x="20" y="34" width="56" height="6" rx="3" fill="rgba(30,64,175,0.14)" />
          <rect x="20" y="46" width="84" height="6" rx="3" fill="rgba(30,64,175,0.09)" />
          <rect x="118" y="34" width="30" height="18" rx="6" stroke="rgba(30,64,175,0.4)" stroke-width="1.5" stroke-dasharray="4 3" />
        </svg>
        <p class="hb-empty-title">AI 正在开发这个应用</p>
        <p class="hb-empty-sub">在右侧对话里描述你想要的功能，开发完成并「发布」后会自动出现在这里</p>
        <button class="hb-btn" @click="emit('recheck')">再看看</button>
      </template>
      <template v-else>
        <p class="hb-empty-title">暂时无法打开应用</p>
        <p class="hb-empty-sub">签发访问凭据失败，请重试</p>
        <button class="hb-btn" @click="sign">重试</button>
      </template>
    </div>

    <!-- agent 发布了新版本但用户正在页内交互 → 延迟重载，显式提示 -->
    <button v-if="pendingReload" class="hb-reload-bar" @click="doReload">✦ 新版本已就绪 · 点此刷新</button>
  </div>
</template>

<style scoped>
.hb-root {
  position: relative;
  width: 100%;
  height: 100%;
}

.hb-frame {
  display: block;
  width: 100%;
  height: 100%;
  border: none;
  background: #fff;
}

.hb-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 24px;
}

.hb-empty-art {
  width: 168px;
  height: 74px;
  margin-bottom: 20px;
  opacity: 0.9;
}

.hb-empty-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink, #0f172a);
}

.hb-empty-sub {
  margin: 0 0 18px;
  max-width: 420px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-mute, #64748b);
}

.hb-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 18px;
  border: 1px solid transparent;
  background: var(--aurora, linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%));
  color: #fff;
  border-radius: 10px;
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px -12px rgba(30, 64, 175, 0.16));
  transition: filter 0.15s ease, transform 0.15s ease;
}
.hb-btn:hover {
  filter: brightness(1.06);
  transform: translateY(-1px);
}

.hb-reload-bar {
  position: absolute;
  top: 14px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  padding: 7px 16px;
  border: 1px solid rgba(30, 64, 175, 0.25);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(8px);
  color: var(--c-blue, #1e40af);
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
  box-shadow: var(--shadow-md, 0 1px 2px rgba(15, 23, 42, 0.05), 0 12px 32px -12px rgba(30, 64, 175, 0.22));
  transition: filter 0.15s ease;
}
.hb-reload-bar:hover {
  filter: brightness(1.03);
}
</style>
