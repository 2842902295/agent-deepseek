<script setup lang="ts">
import { computed, provide, ref, watch } from 'vue';
import type { Component } from 'vue';
import { useAppStore } from '@/store/modules/app';
import { useThemeStore } from '@/store/modules/theme';
import { getBrandVariant } from '@/utils/brand-config';
import PwdLogin from './modules/pwd-login.vue';
import CodeLogin from './modules/code-login.vue';
import Register from './modules/register.vue';
import ResetPwd from './modules/reset-pwd.vue';
import BindWechat from './modules/bind-wechat.vue';
import SmsLogin from './modules/sms-login.vue';
import CompleteProfile from './modules/complete-profile.vue';
import { SWITCH_LOGIN_MODULE } from './shared';

interface Props {
  /** The login module from route param (used as initial value only) */
  module?: UnionKey.LoginModule;
}

const props = defineProps<Props>();

const appStore = useAppStore();
const themeStore = useThemeStore();

interface LoginModule {
  label: string;
  component: Component;
}

const moduleMap: Record<UnionKey.LoginModule, LoginModule> = {
  'pwd-login': { label: '密码登录', component: PwdLogin },
  'code-login': { label: '验证码登录', component: CodeLogin },
  register: { label: '注册账号', component: Register },
  'reset-pwd': { label: '重置密码', component: ResetPwd },
  'bind-wechat': { label: '绑定微信', component: BindWechat },
  'sms-login': { label: '手机号登录', component: SmsLogin },
  'complete-profile': { label: '完善信息', component: CompleteProfile }
};

// generic 模式默认走短信登录
const isGeneric = getBrandVariant() === 'generic';
const defaultModule: UnionKey.LoginModule = isGeneric ? 'sms-login' : 'pwd-login';

const activeKey = ref<UnionKey.LoginModule>(props.module || defaultModule);
watch(
  () => props.module,
  v => {
    if (v) activeKey.value = v;
  }
);

provide(SWITCH_LOGIN_MODULE, (key: UnionKey.LoginModule) => {
  activeKey.value = key;
});

const activeModule = computed(() => moduleMap[activeKey.value]);

// ── 品牌文案（VITE_BRAND=standard|generic）───────────────────────────────
const BRAND_COPY = {
  standard: {
    title: 'CESI 标准AI智能平台',
    subtitle: '标准AI智能平台',
    footer: 'CESI · 标准AI智能平台',
    glyph: '标'
  },
  generic: {
    title: 'agent-deepseek',
    subtitle: 'AI-Agent',
    footer: 'AI-Agent',
    glyph: 'AI'
  }
} as const;

const brandKey = getBrandVariant() as keyof typeof BRAND_COPY;
const brand = BRAND_COPY[brandKey] ?? BRAND_COPY.standard;

// ── Logo 五连击：2 秒内点 5 次解锁 dev 面板 ───────────────────────────────
const SECRET_NEEDED = 5;
const SECRET_WINDOW = 2000;
const tapTimestamps = ref<number[]>([]);
const unlocked = ref(false);

function handleLogoTap() {
  const now = Date.now();
  tapTimestamps.value = [...tapTimestamps.value, now].filter(t => now - t <= SECRET_WINDOW);
  if (tapTimestamps.value.length >= SECRET_NEEDED && !unlocked.value) {
    unlocked.value = true;
    tapTimestamps.value = [];
    window.dispatchEvent(new CustomEvent('login-dev-unlock', { detail: { unlocked: true } }));
    window.$message?.info('Developer mode');
  }
}
</script>

<template>
  <div class="login-page">
    <!-- aurora 背景（深蓝 + cyan 色系） -->
    <div class="aurora" aria-hidden="true">
      <div class="aurora-orb aurora-1" />
      <div class="aurora-orb aurora-2" />
      <div class="aurora-orb aurora-3" />
      <div class="aurora-orb aurora-4" />
      <div class="aurora-grain" />
    </div>

    <!-- 顶部右侧操作 -->
    <div class="top-actions">
      <ThemeSchemaSwitch
        :theme-schema="themeStore.themeScheme"
        :show-tooltip="false"
        class="top-icon"
        @switch="themeStore.toggleThemeScheme"
      />
      <LangSwitch
        v-if="themeStore.header.multilingual.visible"
        :lang="appStore.locale"
        :lang-options="appStore.localeOptions"
        :show-tooltip="false"
        class="top-icon"
        @change-lang="appStore.changeLocale"
      />
    </div>

    <!-- 居中卡片 -->
    <div class="card-wrap">
      <div class="glass-card">
        <header class="card-header">
          <div
            class="brand-mark"
            :class="{ 'brand-unlocked': unlocked }"
            @click="handleLogoTap"
          >
            <span class="bm-glyph">{{ brand.glyph }}</span>
            <span class="bm-halo" />
            <span v-if="unlocked" class="dev-dot" />
          </div>
          <div class="brand-text">
            <h2 class="brand-title">{{ brand.title }}</h2>
            <span class="module-tag">{{ activeModule.label }}</span>
          </div>
        </header>

        <main class="card-body">
          <Transition :name="themeStore.page.animateMode" mode="out-in" appear>
            <component :is="activeModule.component" />
          </Transition>
        </main>
      </div>
    </div>

    <!-- 底部版权 -->
    <div class="footer">
      <span>© {{ new Date().getFullYear() }} {{ brand.footer }}</span>
      <BeianInfo />
    </div>
  </div>
</template>

<style scoped>
/* ─── design tokens ─── */
.login-page {
  --bg: #f0f4fa;
  --bg-warm: #faf5f0;

  --surface: rgba(255, 255, 255, 0.42);
  --surface-strong: rgba(255, 255, 255, 0.62);
  --surface-deep: rgba(255, 255, 255, 0.78);
  --highlight: rgba(255, 255, 255, 0.95);

  --border: rgba(30, 64, 175, 0.1);
  --border-strong: rgba(30, 64, 175, 0.18);
  --border-glow: rgba(30, 64, 175, 0.25);

  --ink: #0f172a;
  --ink-soft: #334155;
  --ink-mute: #64748b;
  --ink-faint: #94a3b8;

  --c-deep: #1e3a8a;
  --c-blue: #1e40af;
  --c-blue-2: #2563eb;
  --c-sky: #0ea5e9;
  --c-cyan: #0891b2;
  --c-violet: #4f46e5;
  --c-mint: #10b981;

  --aurora: linear-gradient(110deg, var(--c-blue) 0%, var(--c-blue-2) 35%, var(--c-sky) 70%, var(--c-cyan) 100%);

  --shadow-glow: 0 8px 32px -10px rgba(30, 64, 175, 0.45);

  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
  color: var(--ink);
  background: linear-gradient(160deg, var(--bg) 0%, #eef2f9 35%, var(--bg-warm) 65%, #f0eef5 100%);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* ─── aurora background ─── */
.aurora {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  overflow: hidden;
}

/* 中心柔光：让卡片和背景产生视觉联系 */
.aurora::before {
  content: '';
  position: absolute;
  width: 700px;
  height: 700px;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: radial-gradient(circle, rgba(37, 99, 235, 0.06) 0%, rgba(14, 165, 233, 0.03) 40%, transparent 70%);
  filter: blur(60px);
  z-index: 1;
}

.aurora-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
  will-change: transform;
}

.aurora-1 {
  width: 680px;
  height: 680px;
  top: -200px;
  right: -160px;
  background: radial-gradient(circle, var(--c-blue) 0%, transparent 65%);
  opacity: 0.3;
  animation: aurora-drift-1 26s ease-in-out infinite;
}

.aurora-2 {
  width: 720px;
  height: 720px;
  bottom: -260px;
  left: -200px;
  background: radial-gradient(circle, var(--c-cyan) 0%, transparent 65%);
  opacity: 0.28;
  animation: aurora-drift-2 32s ease-in-out infinite;
}

.aurora-3 {
  width: 480px;
  height: 480px;
  top: 36%;
  right: 14%;
  background: radial-gradient(circle, var(--c-sky) 0%, transparent 65%);
  opacity: 0.25;
  animation: aurora-drift-3 28s ease-in-out infinite;
}

.aurora-4 {
  width: 420px;
  height: 420px;
  top: 18%;
  left: -60px;
  background: radial-gradient(circle, var(--c-violet) 0%, transparent 65%);
  opacity: 0.18;
  animation: aurora-drift-1 30s ease-in-out infinite reverse;
}

.aurora-grain {
  position: absolute;
  inset: 0;
  background-image:
    radial-gradient(circle at 1px 1px, rgba(15, 23, 42, 0.045) 1px, transparent 0);
  background-size: 3px 3px;
  opacity: 0.4;
  mix-blend-mode: multiply;
}

@keyframes aurora-drift-1 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50%      { transform: translate(60px, 40px) scale(1.08); }
}

@keyframes aurora-drift-2 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50%      { transform: translate(-40px, -60px) scale(1.05); }
}

@keyframes aurora-drift-3 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33%      { transform: translate(-50px, 30px) scale(0.96); }
  66%      { transform: translate(40px, -25px) scale(1.04); }
}

@media (prefers-reduced-motion: reduce) {
  .aurora-orb { animation: none !important; }
  .bm-halo { animation: none !important; }
  .glass-card { animation: none !important; }
}

/* ─── top-right actions ─── */
.top-actions {
  position: absolute;
  right: 20px;
  top: 20px;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px;
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 4px 16px -6px rgba(30, 64, 175, 0.14);
}

.top-icon {
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  color: var(--ink-soft);
  transition: all 0.2s ease;
}

.top-icon:hover {
  color: var(--c-blue);
  background: rgba(30, 64, 175, 0.06);
}

/* ─── card wrapper ─── */
.card-wrap {
  position: relative;
  z-index: 4;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}

/* ─── glass card ─── */
.glass-card {
  width: 420px;
  padding: 36px 32px 32px;
  border-radius: 24px;
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  border: 1px solid var(--border);
  box-shadow:
    inset 0 1px 0 var(--highlight),
    inset 0 0 0 1px rgba(255, 255, 255, 0.5),
    0 1px 2px rgba(15, 23, 42, 0.04),
    0 8px 24px -8px rgba(30, 64, 175, 0.18),
    0 24px 48px -16px rgba(30, 64, 175, 0.12);
  animation: card-entrance 0.6s cubic-bezier(0.16, 1, 0.3, 1) both;
}

@keyframes card-entrance {
  from {
    opacity: 0;
    transform: translateY(20px) scale(0.97);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@media (max-width: 480px) {
  .glass-card {
    width: 100%;
    max-width: 360px;
    padding: 28px 24px 24px;
    border-radius: 20px;
  }
}

/* ─── card header ─── */
.card-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

/* ─── brand mark ─── */
.brand-mark {
  position: relative;
  width: 52px;
  height: 52px;
  border-radius: 16px;
  background: var(--aurora);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  user-select: none;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 4px 14px -4px rgba(30, 64, 175, 0.55);
  transition: box-shadow 0.3s ease;
}

.brand-mark:hover {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 6px 20px -4px rgba(30, 64, 175, 0.65);
}

.bm-glyph {
  position: relative;
  z-index: 1;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 22px;
  font-weight: 700;
  color: #fff;
  letter-spacing: -0.02em;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.18);
}

.bm-halo {
  position: absolute;
  inset: -4px;
  border-radius: 20px;
  background: var(--aurora);
  filter: blur(16px);
  opacity: 0.45;
  z-index: 0;
  animation: halo-breathe 3s ease-in-out infinite;
}

@keyframes halo-breathe {
  0%, 100% { opacity: 0.35; transform: scale(0.95); }
  50%      { opacity: 0.65; transform: scale(1.12); }
}

.brand-unlocked {
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 0 24px rgba(30, 64, 175, 0.5),
    0 0 48px rgba(37, 99, 235, 0.3) !important;
}

.dev-dot {
  position: absolute;
  top: -2px;
  right: -2px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--c-mint);
  border: 2px solid #fff;
  z-index: 2;
  box-shadow: 0 0 8px rgba(16, 185, 129, 0.6);
  animation: dev-pulse 1.5s ease-in-out infinite;
}

@keyframes dev-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.5; transform: scale(0.85); }
}

/* ─── brand text ─── */
.brand-text {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

.brand-title {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.01em;
  background: var(--aurora);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  line-height: 1.2;
}

.module-tag {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--ink-mute);
  text-transform: uppercase;
  padding: 2px 10px;
  background: rgba(30, 64, 175, 0.06);
  border: 1px solid rgba(30, 64, 175, 0.08);
  border-radius: 999px;
}

/* ─── card body ─── */
.card-body {
  padding-top: 28px;
}

/* ─── footer ─── */
.footer {
  position: absolute;
  bottom: 16px;
  left: 0;
  right: 0;
  z-index: 4;
  text-align: center;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.04em;
  color: var(--ink-faint);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0 0.4em;
}

/* ─── Naive UI overrides: light glass inputs ─── */
.glass-card :deep(.n-input) {
  --n-color: rgba(255, 255, 255, 0.62) !important;
  --n-color-focus: rgba(255, 255, 255, 0.78) !important;
  --n-color-disabled: rgba(255, 255, 255, 0.35) !important;
  --n-text-color: #0f172a !important;
  --n-text-color-disabled: #94a3b8 !important;
  --n-placeholder-color: #94a3b8 !important;
  --n-placeholder-color-disabled: #cbd5e1 !important;
  --n-border: 1px solid rgba(30, 64, 175, 0.1) !important;
  --n-border-hover: 1px solid rgba(30, 64, 175, 0.22) !important;
  --n-border-focus: 1px solid rgba(30, 64, 175, 0.28) !important;
  --n-border-disabled: 1px solid rgba(30, 64, 175, 0.06) !important;
  --n-box-shadow-focus: 0 0 0 3px rgba(30, 64, 175, 0.07) !important;
  --n-caret-color: #2563eb !important;
  --n-loading-color: #2563eb !important;
  --n-icon-color: #64748b !important;
  --n-icon-color-hover: #1e40af !important;
  --n-icon-color-pressed: #1e3a8a !important;
  --n-icon-color-disabled: #cbd5e1 !important;
  --n-suffix-text-color: #334155 !important;
  --n-color-focus-error: rgba(255, 255, 255, 0.78) !important;
  --n-border-error: 1px solid rgba(239, 68, 68, 0.35) !important;
  --n-border-hover-error: 1px solid rgba(239, 68, 68, 0.55) !important;
  --n-border-focus-error: 1px solid rgba(239, 68, 68, 0.65) !important;
  --n-box-shadow-focus-error: 0 0 0 3px rgba(239, 68, 68, 0.1) !important;
  --n-caret-color-error: #ef4444 !important;
  --n-loading-color-error: #ef4444 !important;
  border-radius: 10px;
}

/* 浏览器自动填充覆盖 */
.glass-card :deep(.n-input input:-webkit-autofill),
.glass-card :deep(.n-input input:-webkit-autofill:hover),
.glass-card :deep(.n-input input:-webkit-autofill:focus) {
  -webkit-text-fill-color: #0f172a;
  -webkit-box-shadow: 0 0 0 1000px rgba(255, 255, 255, 0.7) inset;
  caret-color: #2563eb;
  transition: background-color 9999s ease-in-out 0s;
}

/* checkbox */
.glass-card :deep(.n-checkbox .n-checkbox__label) {
  color: var(--ink-mute);
}

/* ─── Naive UI button overrides: aurora primary ─── */
.glass-card :deep(.n-button--primary-type) {
  background: var(--aurora) !important;
  border: none !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.4),
    0 4px 14px -2px rgba(30, 64, 175, 0.45) !important;
  transition: all 0.2s ease !important;
}

.glass-card :deep(.n-button--primary-type:hover:not(:disabled)) {
  transform: translateY(-1px);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 6px 20px -2px rgba(30, 64, 175, 0.55) !important;
}

.glass-card :deep(.n-button--primary-type:active:not(:disabled)) {
  transform: translateY(0);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.3),
    0 2px 8px -2px rgba(30, 64, 175, 0.4) !important;
}

.glass-card :deep(.n-button--primary-type .n-button__content) {
  color: #fff !important;
  font-weight: 600 !important;
}

/* secondary / text buttons: blue accent */
.glass-card :deep(.n-button--secondary-type) {
  border-color: rgba(30, 64, 175, 0.18) !important;
  background: rgba(255, 255, 255, 0.5) !important;
}

.glass-card :deep(.n-button--secondary-type .n-button__content) {
  color: var(--c-blue) !important;
}

.glass-card :deep(.n-button--secondary-type:hover:not(:disabled)) {
  border-color: rgba(30, 64, 175, 0.32) !important;
  background: rgba(255, 255, 255, 0.7) !important;
}

.glass-card :deep(.n-button--default-type:not(.n-button--ghost-type):not(.n-button--dashed-type)) {
  background: rgba(255, 255, 255, 0.5) !important;
  border-color: rgba(30, 64, 175, 0.12) !important;
}

.glass-card :deep(.n-button--default-type:not(.n-button--ghost-type):not(.n-button--dashed-type) .n-button__content) {
  color: var(--ink-soft) !important;
}

.glass-card :deep(.n-button--default-type:not(.n-button--ghost-type):not(.n-button--dashed-type):hover:not(:disabled)) {
  background: rgba(255, 255, 255, 0.7) !important;
  border-color: rgba(30, 64, 175, 0.22) !important;
}

.glass-card :deep(.n-button--default-type:not(.n-button--ghost-type):not(.n-button--dashed-type):hover:not(:disabled) .n-button__content) {
  color: var(--c-blue) !important;
}

/* text button */
.glass-card :deep(.n-button--text-type .n-button__content) {
  color: var(--c-blue) !important;
  font-weight: 600 !important;
}

.glass-card :deep(.n-button--text-type:hover:not(:disabled) .n-button__content) {
  color: var(--c-blue-2) !important;
}

/* form item spacing — 用 padding-bottom 控制间距，feedback 固定高度防抖动 */
.glass-card :deep(.n-form-item) {
  margin-bottom: 0;
  padding-bottom: 6px;
}

.glass-card :deep(.n-form-item-feedback-wrapper) {
  min-height: 18px !important;
  padding-top: 2px !important;
  font-size: 12px !important;
}

/* n-space vertical gap override for consistency */
.glass-card :deep(.n-space) {
  gap: 10px !important;
}
</style>
