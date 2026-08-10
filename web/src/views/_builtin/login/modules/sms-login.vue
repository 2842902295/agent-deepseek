<script setup lang="ts">
import { inject, reactive, ref } from 'vue';
import { useAuthStore } from '@/store/modules/auth';
import { useNaiveForm } from '@/hooks/common/form';
import { fetchSendSmsCode, fetchSmsLogin } from '@/service/api';
import { localStg } from '@/utils/storage';
import { SWITCH_LOGIN_MODULE } from '../shared';

defineOptions({ name: 'SmsLogin' });

const authStore = useAuthStore();
const { formRef, validate } = useNaiveForm();
const switchModule = inject(SWITCH_LOGIN_MODULE, () => {});

interface FormModel {
  userPhone: string;
  code: string;
}

const model: FormModel = reactive({ userPhone: '', code: '' });

const rules = {
  userPhone: [
    { required: true, message: '请输入手机号' },
    { pattern: /^1[3-9]\d{9}$/, message: '手机号格式不正确', trigger: ['blur', 'input'] }
  ],
  code: [
    { required: true, message: '请输入验证码' },
    { pattern: /^\d{6}$/, message: '请输入 6 位数字验证码', trigger: ['blur', 'input'] }
  ]
};

// 倒计时
const countdown = ref(0);
const sendLoading = ref(false);
let timer: ReturnType<typeof setInterval> | null = null;

function startCountdown() {
  countdown.value = 60;
  timer = setInterval(() => {
    countdown.value -= 1;
    if (countdown.value <= 0) {
      clearInterval(timer!);
      timer = null;
    }
  }, 1000);
}

async function handleSendCode() {
  if (!/^1[3-9]\d{9}$/.test(model.userPhone)) {
    window.$message?.warning('请先输入正确的手机号');
    return;
  }
  sendLoading.value = true;
  const { error } = await fetchSendSmsCode(model.userPhone);
  sendLoading.value = false;
  if (!error) {
    window.$message?.success('验证码已发送');
    startCountdown();
  }
}

const submitLoading = ref(false);

async function handleSubmit() {
  await validate();
  submitLoading.value = true;

  const { data, error } = await fetchSmsLogin(model.userPhone, model.code);
  submitLoading.value = false;

  if (error) return;

  if (data?.isNewUser && !data.token) {
    // 后端告知：新用户，需要补充昵称；把手机号+验证码传递给下一步
    localStg.set('smsLoginPhone', model.userPhone);
    localStg.set('smsLoginCode', model.code);
    switchModule('complete-profile');
    return;
  }

  if (data?.token) {
    await authStore.loginByToken({ token: data.token, refreshToken: data.refreshToken! });
  }
}
</script>

<template>
  <NForm ref="formRef" :model="model" :rules="rules" size="large" :show-label="false" @keyup.enter="handleSubmit">
    <NFormItem path="userPhone">
      <NInput v-model:value="model.userPhone" placeholder="请输入手机号" maxlength="11" />
    </NFormItem>
    <NFormItem path="code">
      <div class="flex w-full gap-12px">
        <NInput v-model:value="model.code" placeholder="请输入验证码" maxlength="6" class="flex-1" />
        <NButton
          :disabled="countdown > 0 || sendLoading"
          :loading="sendLoading"
          class="sms-code-btn"
          style="min-width: 110px"
          @click="handleSendCode"
        >
          {{ countdown > 0 ? `${countdown}s 后重发` : '获取验证码' }}
        </NButton>
      </div>
    </NFormItem>
    <NButton :loading="submitLoading || authStore.loginLoading" block round size="large" type="primary" @click="handleSubmit">
      登录
    </NButton>
  </NForm>
</template>

<style scoped>
.sms-code-btn {
  --n-color: rgba(255, 255, 255, 0.55) !important;
  --n-color-hover: rgba(255, 255, 255, 0.75) !important;
  --n-color-pressed: rgba(255, 255, 255, 0.45) !important;
  --n-color-disabled: rgba(255, 255, 255, 0.35) !important;
  --n-text-color: #334155 !important;
  --n-text-color-hover: #1e40af !important;
  --n-text-color-pressed: #1e3a8a !important;
  --n-text-color-disabled: #94a3b8 !important;
  --n-border: 1px solid rgba(30, 64, 175, 0.12) !important;
  --n-border-hover: 1px solid rgba(30, 64, 175, 0.28) !important;
  --n-border-pressed: 1px solid rgba(30, 64, 175, 0.18) !important;
  --n-border-disabled: 1px solid rgba(30, 64, 175, 0.06) !important;
  --n-border-radius: 10px !important;
  --n-ripple-color: rgba(30, 64, 175, 0.15) !important;
  --n-loading-color: #2563eb !important;
  backdrop-filter: blur(12px);
  height: 40px;
  font-weight: 600;
}
</style>
