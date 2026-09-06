<script setup lang="ts">
/**
 * 应用制作画布：「html 型」任务的画布渲染器。
 *
 * 与流程编排（VueFlow）并列的第二种画布：agent 在任务目录开发的多文件 HTML 应用，
 * 通过带签名 token 的托管路由（/api/v1/ai/html-app/{token}/...）以 iframe 呈现。
 *
 * - token 每次进任务签发（父组件以 :key=workflowKey 保证切换任务时整体重建）
 * - version 变化（agent 更新画面后父级轮询命中）→ 重载 iframe；src 绑定的是
 *   「已显示的版本」shownVersion 而非 prop 本身——否则 prop 一变 src 立即换、
 *   iframe 当场重载，「交互中延迟重载」形同虚设，且重载完还留着过期的刷新条；
 *   iframe 正被用户交互（焦点/悬停）时延迟重载，浮出「新版本已就绪」横条
 * - entryReady=false（尚未发布过 index.html）→ 占位提示
 */
import {onBeforeUnmount, onMounted, ref, watch} from 'vue';
import {fetchShareViewToken, fetchSignHtmlAppToken} from '@/service/api/ai';

const props = defineProps<{
  workflowKey: string;
  /** 父级轮询到的板版本号；agent publish_html_board 会 bump，作为重载触发信号 */
  version: number;
  /** 入口 index.html 是否已发布（父级 fetchWorkflow 回填） */
  entryReady: boolean;
  /** 访客分享模式（/share/:workflowKey 页）：走 share-view 端点签发 share 态 token。
   *  share token 不注入「编辑文字」脚本，其余读写能力一致；share-view 只在已发布时签发，无占位态。
   *  「仅登录用户」模式下匿名访客会收到 need-login 上报，由宿主引导登录 */
  share?: boolean;
}>();

const emit = defineEmits<{
  /** 占位态「再看看」：请父级重新拉取工作流（刷新 entryReady/version） */
  (e: 'recheck'): void;
  /** 注入脚本回报的文字编辑状态（外框顶栏「编辑文字」按钮据此换形态） */
  (e: 'edit-state', s: {editing: boolean; editableCount: number}): void;
  /** 分享态专属：看板为「仅登录用户」模式而访客未登录（后端 4000 + needLogin 标记），
   *  请宿主引导登录（带 redirect 回分享页） */
  (e: 'need-login'): void;
  /** 应用页内经 AppBridge.sendToChat 呼叫 agent：宿主把 text 作为一条用户消息触发回合，
   *  完成后调 respond(ok, reason?) 回执（页内据此提示 / 退避）。分享页无宿主监听 →
   *  respond 永不被调 → AppBridge 超时静默降级 no-channel（预期行为） */
  (e: 'app-chat', p: {text: string; respond: (ok: boolean, reason?: string) => void}): void;
}>();

defineExpose({toggleTextEdit});

type Phase = 'signing' | 'placeholder' | 'ready' | 'error';

// 托管路由前缀必须与 axios 同一真相源：dev（--mode test）是 http://localhost:9999/api/v1 直连后端（无 vite 代理），
// prod 是 /api/v1 同源走 nginx——写死同源相对路径会在 dev 打到 9527 上 404。
// dev 下 iframe 跨源加载不受限；页内 fetch 相对/绝对路径都解析到 iframe 文档自身源（= 后端），save 同样通。
const serviceBase = import.meta.env.VITE_SERVICE_BASE_URL || '/api/v1';

const phase = ref<Phase>('signing');
/** 错误态文案（分享态区分「链接无效 / 分享已关闭」与通用签发失败） */
const errorMsg = ref('');
const token = ref('');
/** 递增以 :key 强制销毁重建 iframe（比改 src 更彻底地断掉旧文档脚本/定时器） */
const reloadKey = ref(0);
/** iframe 正在显示的版本号（src 只绑它）：prop version 领先它 = 有新版本待重载。
 *  绝不把 prop 直接拼进 src——否则版本一变 iframe 立即重载，延迟重载与刷新条逻辑全部失效 */
const shownVersion = ref(props.version);
const pendingReload = ref(false);
const pointerIn = ref(false);
const iframeFocused = ref(false);
const iframeEl = ref<HTMLIFrameElement | null>(null);

let reloadTimer: ReturnType<typeof setTimeout> | null = null;

// ── token 签发 ──────────────────────────────────────────────────────────────

async function sign() {
  phase.value = 'signing';
  if (props.share) {
    // 访客分享态：share-view 仅在分享开启且已发布时签发；「仅登录用户」模式下匿名访客
    // 收到 4000 + needLogin 标记 → 上报宿主引导登录（不渲染错误态，避免与跳转闪在一起）
    const {data, error, response} = await fetchShareViewToken(props.workflowKey);
    if (error || !data) {
      if ((response?.data as any)?.data?.needLogin) {
        emit('need-login');
        return;
      }
      errorMsg.value = error?.msg || '分享链接无效或分享已关闭';
      phase.value = 'error';
      return;
    }
    token.value = data.token;
    phase.value = 'ready';
    return;
  }
  const {data, error} = await fetchSignHtmlAppToken(props.workflowKey);
  if (error || !data) {
    errorMsg.value = '签发访问凭据失败，请重试';
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

// ── version 变化 → 重载（500ms 防抖合并连续更新；交互中延迟；已显示同版本则忽略）─────

watch(
  () => props.version,
  (v, old) => {
    if (v === old) return;
    if (phase.value !== 'ready') {
      shownVersion.value = v; // iframe 尚未挂载，直接同步显示版本号，无需重载
      return;
    }
    if (v === shownVersion.value) return; // 画面已经是这个版本（重载已完成）——不再重复提示/重载
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
  shownVersion.value = props.version;
  reloadKey.value += 1;
  // iframe 重建 = 页内编辑态必然消失，主动告知父级复位按钮形态
  emit('edit-state', {editing: false, editableCount: 0});
}

// ── 文字编辑：外框顶栏按钮 → postMessage 驱动页内注入脚本（serve 时由后端注入） ──

function iframeTargetOrigin(): string {
  try {
    return new URL(iframeEl.value!.src).origin;
  } catch {
    return '*';
  }
}

function toggleTextEdit() {
  iframeEl.value?.contentWindow?.postMessage({type: 'hbte-toggle-edit'}, iframeTargetOrigin());
}

function onEditStateMessage(ev: MessageEvent) {
  const data = ev.data;
  if (!data || typeof data !== 'object' || data.type !== 'hbte-edit-state') return;
  // 只认自己 iframe 的回报，防其它 window 伪造消息改按钮状态
  if (!iframeEl.value || ev.source !== iframeEl.value.contentWindow) return;
  emit('edit-state', {editing: !!data.editing, editableCount: Number(data.editableCount) || 0});
}

// 应用页内 AppBridge.sendToChat → postMessage 呼叫 agent：转交宿主触发一个对话回合，
// 并把结果经 ack 回发页内。只认自己 iframe 的消息（与 onEditStateMessage 同款防线）。
function onAppChatMessage(ev: MessageEvent) {
  const data = ev.data;
  if (!data || typeof data !== 'object' || data.type !== 'hbte-app-chat') return;
  if (!iframeEl.value || ev.source !== iframeEl.value.contentWindow) return;
  const id = data.id;
  const text = String(data.text || '').slice(0, 2000);
  const target = iframeTargetOrigin();
  const respond = (ok: boolean, reason?: string) => {
    iframeEl.value?.contentWindow?.postMessage({type: 'hbte-app-chat-ack', id, ok, reason}, target);
  };
  if (!text.trim()) {
    respond(false, 'empty');
    return;
  }
  emit('app-chat', {text, respond});
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
  window.addEventListener('message', onEditStateMessage);
  window.addEventListener('message', onAppChatMessage);
  sign();
});

onBeforeUnmount(() => {
  window.removeEventListener('blur', onWindowBlur);
  window.removeEventListener('focus', onWindowFocus);
  window.removeEventListener('message', onEditStateMessage);
  window.removeEventListener('message', onAppChatMessage);
  if (reloadTimer) clearTimeout(reloadTimer);
});
</script>

<template>
  <div class="hb-root">
    <!-- 沙箱放开策略：allow-modals 必开（否则页内 alert/confirm 被静默拦截，写回失败提示弹不出来）；
         allowfullscreen + fullscreen 权限策略支持应用自己的全屏请求；pointer-lock 供画布游戏鼠标锁定；
         popups-to-escape-sandbox 让应用打开的外部链接不继承沙箱；microphone/camera/geolocation
         仅放开「允许询问」，浏览器仍会弹授权框。gpu 无需配置：WebGL 默认可用、WebGPU 只看安全上下文 -->
    <iframe
      v-if="phase === 'ready' && token"
      :key="reloadKey"
      ref="iframeEl"
      class="hb-frame"
      :src="`${serviceBase}/ai/html-app/${token}/index.html?v=${shownVersion}`"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads allow-modals allow-pointer-lock"
      allow="fullscreen; autoplay; clipboard-write; microphone; camera; geolocation"
      allowfullscreen
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
        <p class="hb-empty-sub">在右侧对话里描述你想要的功能，开发完成后会自动出现在这里</p>
      </template>
      <template v-else>
        <p class="hb-empty-title">暂时无法打开应用</p>
        <p class="hb-empty-sub">{{ errorMsg || '签发访问凭据失败，请重试' }}</p>
        <button class="hb-btn" @click="sign">重试</button>
      </template>
    </div>

    <!-- agent 更新了画面但用户正在页内交互 → 延迟重载，显式提示 -->
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
