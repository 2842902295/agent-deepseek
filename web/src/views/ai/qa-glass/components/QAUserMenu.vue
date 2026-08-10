<script setup lang="ts">
import { computed, ref } from 'vue';
import { useAuthStore } from '@/store/modules/auth';
import { useRouterPush } from '@/hooks/common/router';
import { $t } from '@/locales';

defineOptions({
  name: 'QAUserMenu'
});

defineProps<{
  /** 「今日简报」功能开关（默认关闭，由 index.vue 持久化到 localStorage） */
  briefEnabled: boolean;
}>();

const emit = defineEmits<{
  openProfile: [];
  'update:briefEnabled': [value: boolean];
}>();

const authStore = useAuthStore();
const { toLogin } = useRouterPush();

const popoverShow = ref(false);

const displayName = computed(() => authStore.userInfo.nickName || authStore.userInfo.userName);
const avatarChar = computed(() => (displayName.value || 'U').charAt(0).toUpperCase());

function openProfile() {
  popoverShow.value = false;
  emit('openProfile');
}

function toggleBrief(value: boolean) {
  emit('update:briefEnabled', value);
}

function logout() {
  popoverShow.value = false;
  window.$dialog?.info({
    title: $t('common.tip'),
    content: $t('common.logoutConfirm'),
    positiveText: $t('common.confirm'),
    negativeText: $t('common.cancel'),
    onPositiveClick: () => {
      authStore.resetStore();
    }
  });
}
</script>

<template>
  <NButton v-if="!authStore.isLogin" quaternary @click="toLogin()">
    {{ $t('page.login.common.loginOrRegister') }}
  </NButton>
  <NPopover
    v-else
    v-model:show="popoverShow"
    trigger="click"
    placement="top-start"
    to="body"
    raw
    :show-arrow="false"
    :duration="150"
  >
    <!-- 外层 div 保证 trigger 为单一元素节点（ButtonIcon 内嵌 NTooltip，插槽多节点会让 VBinder 定位失败） -->
    <template #trigger>
      <div>
        <ButtonIcon>
          <SvgIcon icon="ph:user-circle" class="text-icon-large" />
          <span class="text-16px font-medium">{{ displayName }}</span>
        </ButtonIcon>
      </div>
    </template>

    <div class="qa-user-card">
      <div class="qa-user-glow" aria-hidden="true" />

      <!-- 用户信息头 -->
      <div class="qa-user-head">
        <span class="qa-user-avatar">{{ avatarChar }}</span>
        <div class="qa-user-meta">
          <div class="qa-user-name">{{ displayName }}</div>
          <div class="qa-user-sub">@{{ authStore.userInfo.userName }}</div>
        </div>
      </div>

      <div class="qa-user-divider" />

      <button class="qa-user-row" type="button" @click="openProfile">
        <SvgIcon icon="ph:pencil-simple-line" class="qa-user-ico" />
        <span>编辑个人资料</span>
      </button>

      <div class="qa-user-row qa-user-row--switch">
        <span class="qa-user-row-label">
          <SvgIcon icon="ph:newspaper-clipping" class="qa-user-ico" />
          <span>启用今日简报</span>
        </span>
        <NSwitch size="small" :value="briefEnabled" @update:value="toggleBrief" />
      </div>

      <div class="qa-user-divider" />

      <button class="qa-user-row qa-user-row--danger" type="button" @click="logout">
        <SvgIcon icon="ph:sign-out" class="qa-user-ico" />
        <span>{{ $t('common.logout') }}</span>
      </button>
    </div>
  </NPopover>
</template>

<style scoped>
/* 弹层 teleport 到 body，脱离 .qa-shell 作用域，复刻其玻璃设计变量（同 ProfileModal / SessionSearchModal） */
.qa-user-card {
  --surface: rgba(255, 255, 255, 0.62);
  --ink: #0f172a;
  --ink-2: #334155;
  --ink-3: #64748b;
  --accent: #1e40af;
  --accent-soft: rgba(30, 64, 175, 0.08);
  --aurora: linear-gradient(110deg, #1e40af 0%, #2563eb 35%, #0ea5e9 70%, #0891b2 100%);
  --font-display: 'Plus Jakarta Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;

  position: relative;
  width: 216px;
  overflow: hidden;
  border-radius: 16px;
  background: var(--surface);
  backdrop-filter: blur(40px) saturate(200%);
  -webkit-backdrop-filter: blur(40px) saturate(200%);
  font-family: var(--font-display);
  color: var(--ink);
  padding: 8px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.95),
    inset 0 0 0 1px rgba(255, 255, 255, 0.4),
    0 1px 2px rgba(15, 23, 42, 0.06),
    0 24px 64px -20px rgba(30, 64, 175, 0.32);
}

/* 顶部极光氛围光 */
.qa-user-glow {
  position: absolute;
  top: -46px;
  left: 50%;
  width: 200px;
  height: 90px;
  transform: translateX(-50%);
  background: var(--aurora);
  opacity: 0.14;
  filter: blur(34px);
  pointer-events: none;
}

/* ─── 用户信息头 ─── */
.qa-user-head {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 8px 10px;
}

.qa-user-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 11px;
  background: var(--aurora);
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  flex-shrink: 0;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 4px 14px -4px rgba(30, 64, 175, 0.55);
}

.qa-user-meta {
  flex: 1;
  min-width: 0;
}

.qa-user-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.qa-user-sub {
  margin-top: 2px;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.02em;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ─── 菜单行 ─── */
.qa-user-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  padding: 9px 10px;
  border: none;
  border-radius: 10px;
  background: transparent;
  font-family: var(--font-display);
  font-size: 13px;
  color: var(--ink-2);
  cursor: pointer;
  transition: background 0.18s ease, color 0.18s ease;
  text-align: left;
}

.qa-user-row:hover {
  background: var(--accent-soft);
  color: var(--accent);
}

.qa-user-row--switch {
  justify-content: space-between;
  cursor: default;
}

.qa-user-row--switch:hover {
  background: transparent;
  color: var(--ink-2);
}

.qa-user-row-label {
  display: flex;
  align-items: center;
  gap: 9px;
}

.qa-user-ico {
  font-size: 15px;
  color: var(--ink-3);
  flex-shrink: 0;
  transition: color 0.18s ease;
}

.qa-user-row:hover .qa-user-ico {
  color: var(--accent);
}

.qa-user-row--danger:hover {
  background: rgba(220, 38, 38, 0.07);
  color: #dc2626;
}

.qa-user-row--danger:hover .qa-user-ico {
  color: #dc2626;
}

.qa-user-divider {
  height: 1px;
  margin: 4px 10px;
  background: rgba(30, 64, 175, 0.08);
}
</style>
