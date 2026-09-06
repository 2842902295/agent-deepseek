<script setup lang="ts">
import { computed, inject, reactive, ref } from 'vue';
import { useFormRules, useNaiveForm } from '@/hooks/common/form';
import { fetchRegister } from '@/service/api';
import { useAuthStore } from '@/store/modules/auth';
import { SWITCH_LOGIN_MODULE } from '../shared';

defineOptions({
  name: 'Register'
});

const { formRef, validate } = useNaiveForm();
const switchModule = inject(SWITCH_LOGIN_MODULE, () => {});

interface FormModel {
  nickName: string;
  userPhone: string;
  password: string;
  confirmPassword: string;
}

const model: FormModel = reactive({
  nickName: '',
  userPhone: '',
  password: '',
  confirmPassword: ''
});

const rules = computed<Record<keyof FormModel, App.Global.FormRule[]>>(() => {
  const { patternRules, createConfirmPwdRule } = useFormRules();

  return {
    nickName: [{ required: true, message: '请输入名字' }],
    userPhone: [{ required: true, message: '请输入手机号' }, patternRules.phone],
    password: [{ required: true, message: '请输入密码' }, patternRules.pwd],
    confirmPassword: createConfirmPwdRule(model.password)
  };
});

const submitting = ref(false);

async function handleSubmit() {
  await validate();
  submitting.value = true;
  const { data, error } = await fetchRegister(model.userPhone, model.password, model.nickName);
  submitting.value = false;
  if (error) return;

  // 注册成功后自动登录
  const authStore = useAuthStore();
  await authStore.loginByToken(data);
  window.$message?.success('注册成功');
}
</script>

<template>
  <NForm ref="formRef" :model="model" :rules="rules" size="large" :show-label="false" @keyup.enter="handleSubmit">
    <NFormItem path="nickName">
      <NInput v-model:value="model.nickName" placeholder="请输入名字" />
    </NFormItem>
    <NFormItem path="userPhone">
      <NInput v-model:value="model.userPhone" placeholder="请输入手机号" />
    </NFormItem>
    <NFormItem path="password">
      <NInput
        v-model:value="model.password"
        type="password"
        show-password-on="click"
        placeholder="请输入密码"
      />
    </NFormItem>
    <NFormItem path="confirmPassword">
      <NInput
        v-model:value="model.confirmPassword"
        type="password"
        show-password-on="click"
        placeholder="请再次输入密码"
      />
    </NFormItem>
    <NSpace vertical :size="12" class="w-full">
      <NButton type="primary" size="large" round block :loading="submitting" @click="handleSubmit">
        注册
      </NButton>
      <NButton
        class="back-btn"
        size="large"
        round
        block
        :disabled="submitting"
        @click="switchModule('pwd-login')"
      >
        返回登录
      </NButton>
    </NSpace>
  </NForm>
</template>

<style scoped>
.back-btn :deep(.n-button__content) {
  color: #334155;
}

.back-btn {
  background: rgba(255, 255, 255, 0.5) !important;
  border: 1px solid rgba(30, 64, 175, 0.12) !important;
  transition: all 0.2s ease;
}

.back-btn:not(:disabled):hover {
  background: rgba(255, 255, 255, 0.7) !important;
  border-color: rgba(30, 64, 175, 0.25) !important;
}

.back-btn:not(:disabled):hover :deep(.n-button__content) {
  color: #1e40af;
}
</style>
