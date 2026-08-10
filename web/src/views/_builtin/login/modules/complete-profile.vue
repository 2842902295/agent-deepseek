<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { useAuthStore } from '@/store/modules/auth';
import { useNaiveForm } from '@/hooks/common/form';
import { fetchSmsLogin } from '@/service/api';
import { localStg } from '@/utils/storage';

defineOptions({ name: 'CompleteProfile' });

const authStore = useAuthStore();
const { formRef, validate } = useNaiveForm();

interface FormModel {
  nickName: string;
}

const model: FormModel = reactive({ nickName: '' });

const rules = {
  nickName: [
    { required: true, message: '请输入昵称' },
    { min: 1, max: 50, message: '昵称长度 1-50 字', trigger: ['blur', 'input'] }
  ]
};

const phone = ref('');
const code = ref('');

onMounted(() => {
  phone.value = localStg.get('smsLoginPhone') ?? '';
  code.value = localStg.get('smsLoginCode') ?? '';
});

const submitLoading = ref(false);

async function handleSubmit() {
  await validate();
  if (!phone.value || !code.value) {
    window.$message?.error('登录信息已过期，请重新获取验证码');
    return;
  }
  submitLoading.value = true;
  const { data, error } = await fetchSmsLogin(phone.value, code.value, model.nickName);
  submitLoading.value = false;

  if (error) return;

  localStg.remove('smsLoginPhone');
  localStg.remove('smsLoginCode');

  if (data?.token) {
    await authStore.loginByToken({ token: data.token, refreshToken: data.refreshToken! });
  }
}
</script>

<template>
  <div class="flex-col-center gap-16px pb-4px">
    <p class="text-14px text-#64748b text-center">验证成功！请设置一个昵称</p>
  </div>
  <NForm ref="formRef" :model="model" :rules="rules" size="large" :show-label="false" @keyup.enter="handleSubmit">
    <NFormItem path="nickName">
      <NInput v-model:value="model.nickName" placeholder="请输入昵称" maxlength="50" />
    </NFormItem>
    <NButton :loading="submitLoading || authStore.loginLoading" block round size="large" type="primary" @click="handleSubmit">
      完成
    </NButton>
  </NForm>
</template>
