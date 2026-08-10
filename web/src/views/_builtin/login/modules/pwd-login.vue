<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useAuthStore } from '@/store/modules/auth';
import { useFormRules, useNaiveForm } from '@/hooks/common/form';
import { SWITCH_LOGIN_MODULE } from '../shared';

defineOptions({
  name: 'PwdLogin'
});

const authStore = useAuthStore();
const route = useRoute();
const router = useRouter();
const { formRef, validate } = useNaiveForm();

const switchModule = inject(SWITCH_LOGIN_MODULE, () => {});

interface FormModel {
  userName: string;
  password: string;
}

const model: FormModel = reactive({
  userName: '',
  password: ''
});

const rules = computed<Record<keyof FormModel, App.Global.FormRule[]>>(() => {
  const { patternRules } = useFormRules();

  return {
    userName: [{ required: true, message: '请输入手机号' }, patternRules.phone],
    // 登录不做强度校验：历史/短信注册/管理员设置的密码可能不满足强度规则，登录应允许任意已存在密码
    password: [{ required: true, message: '请输入密码' }]
  };
});

async function handleSubmit() {
  await validate();
  await authStore.login(model.userName, model.password);
}

type AccountKey = 'super' | 'admin' | 'user';

interface Account {
  key: AccountKey;
  label: string;
  userName: string;
  password: string;
}

const accounts: Account[] = [
  { key: 'super', label: '超级管理员', userName: 'Super', password: 'Naohao:172' },
  { key: 'admin', label: '管理员', userName: 'Admin', password: 'Naohao:172' },
  { key: 'user', label: '普通用户', userName: 'User', password: 'Naohao:172' }
];

async function handleAccountLogin(account: Account) {
  showDevPanel.value = false;
  await authStore.login(account.userName, account.password);
}

// ── Logo 五连击解锁开发者面板（事件总线由 index.vue 派发）─────────────────
const showDevPanel = ref(false);
function handleLogoSecret(e: Event) {
  showDevPanel.value = (e as CustomEvent).detail?.unlocked ?? false;
}
onMounted(() => window.addEventListener('login-dev-unlock', handleLogoSecret));
onUnmounted(() => window.removeEventListener('login-dev-unlock', handleLogoSecret));

// 自动登录（开发便捷）：.env 中设置 VITE_AUTO_LOGIN=Super/Admin/User
onMounted(async () => {
  const auto = String(import.meta.env.VITE_AUTO_LOGIN || '').trim();
  if (!auto) return;
  if (authStore.isLogin) return;
  const target = accounts.find(
    a => a.userName.toLowerCase() === auto.toLowerCase() || a.key === auto.toLowerCase()
  );
  if (!target) return;
  const redirect = route.query?.redirect as string | undefined;
  await handleAccountLogin(target);
  if (redirect) {
    await router.push(redirect);
  }
});
</script>

<template>
  <NForm ref="formRef" :model="model" :rules="rules" size="large" :show-label="false" @keyup.enter="handleSubmit">
    <NFormItem path="userName">
      <NInput v-model:value="model.userName" placeholder="请输入手机号" />
    </NFormItem>
    <NFormItem path="password">
      <NInput
        v-model:value="model.password"
        type="password"
        show-password-on="click"
        placeholder="请输入密码"
      />
    </NFormItem>
    <NSpace vertical :size="12">
      <div class="flex-y-center justify-between">
        <NCheckbox>记住我</NCheckbox>
      </div>
      <NButton :loading="authStore.loginLoading" block round size="large" type="primary" @click="handleSubmit">
        登录
      </NButton>
      <div class="switch-row">
        <span class="switch-hint">还没有账号？</span>
        <button class="switch-link" type="button" @click="switchModule('register')">注册账号</button>
      </div>
      <Transition name="fade-slide">
        <div v-if="showDevPanel" class="dev-panel rd-10px px-12px py-10px">
          <div class="flex-y-center justify-between mb-8px">
            <span class="dev-label">DEV · QUICK LOGIN</span>
            <NButton text size="tiny" @click="showDevPanel = false">×</NButton>
          </div>
          <div class="flex gap-8px">
            <NButton
              v-for="item in accounts"
              :key="item.key"
              size="small"
              secondary
              type="primary"
              class="flex-1"
              @click="handleAccountLogin(item)"
            >
              {{ item.label }}
            </NButton>
          </div>
        </div>
      </Transition>
    </NSpace>
  </NForm>
</template>

<style scoped>
.switch-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  font-size: 13.5px;
}

.switch-hint {
  color: #64748b;
}

.switch-link {
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  color: #2563eb;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 13.5px;
  font-weight: 600;
  letter-spacing: 0.005em;
  position: relative;
  transition: color 0.2s ease;
}

.switch-link::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 1px;
  background: currentColor;
  opacity: 0.3;
  transition: opacity 0.2s ease;
}

.switch-link:hover {
  color: #1e40af;
}

.switch-link:hover::after {
  opacity: 0.6;
}

.dev-panel {
  background: rgba(255, 255, 255, 0.5);
  border: 1px dashed rgba(30, 64, 175, 0.18);
  backdrop-filter: blur(12px);
}

.dev-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: #64748b;
}

.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.25s ease;
}

.fade-slide-enter-from,
.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}
</style>
