<script setup lang="ts">
import { ref, computed } from 'vue';
import { NDrawer, NDrawerContent, NForm, NFormItem, NInput, NInputNumber, NButton, NSpace, NAlert } from 'naive-ui';
import { importStructuredDataFromMySQL } from '@/service/api';

interface Props {
  show: boolean;
}

interface Emits {
  (e: 'update:show', value: boolean): void;
  (e: 'success'): void;
}

const props = defineProps<Props>();
const emit = defineEmits<Emits>();

const visible = computed({
  get() {
    return props.show;
  },
  set(value) {
    emit('update:show', value);
  }
});

// 从 localStorage 读取上次的配置
const STORAGE_KEY = 'mysql_structured_migration_config';

function loadConfig() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      return JSON.parse(saved);
    }
  } catch (error) {
    console.error('读取配置失败:', error);
  }
  return {
    host: 'localhost',
    port: 3306,
    user: 'root',
    password: '',
    database: '',
    standardNos: ''
  };
}

// 表单数据
const formData = ref(loadConfig());

// 保存配置到 localStorage
function saveConfig() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(formData.value));
  } catch (error) {
    console.error('保存配置失败:', error);
  }
}

// 加载状态
const loading = ref(false);

// 提交表单
async function handleSubmit() {
  if (!formData.value.database) {
    window.$message?.error('请输入数据库名称');
    return;
  }

  if (!formData.value.password) {
    window.$message?.error('请输入密码');
    return;
  }

  if (!formData.value.standardNos.trim()) {
    window.$message?.error('请输入标准号');
    return;
  }

  // 保存配置
  saveConfig();

  loading.value = true;

  try {
    // 解析标准号列表（按换行分隔）
    const standardNosList = formData.value.standardNos
      .split('\n')
      .map((line: string) => line.trim())
      .filter((line: string) => line.length > 0);

    if (standardNosList.length === 0) {
      window.$message?.error('请输入至少一个标准号');
      return;
    }

    const { data, error } = await importStructuredDataFromMySQL({
      host: formData.value.host,
      port: formData.value.port,
      user: formData.value.user,
      password: formData.value.password,
      database: formData.value.database,
      standard_nos: standardNosList
    });

    if (error) {
      window.$message?.error(error.msg || '数据导入失败');
      return;
    }

    window.$message?.success(data?.msg || '数据导入成功');
    visible.value = false;
    emit('success');
  } catch (error: any) {
    window.$message?.error(error.message || '数据导入失败');
  } finally {
    loading.value = false;
  }
}

// 关闭抽屉
function handleClose() {
  visible.value = false;
}
</script>

<template>
  <NDrawer v-model:show="visible" :width="500" placement="right">
    <NDrawerContent title="结构化数据迁入" closable>
      <NAlert type="info" title="说明" :bordered="false" class="mb-16px">
        此功能将从 MySQL 的 standard_ai_doc 表中读取指定标准号的结构化数据（json_data 字段），并解析导入到本地数据库。
      </NAlert>

      <NForm :model="formData" label-placement="left" label-width="100">
        <NFormItem label="主机地址" required>
          <NInput v-model:value="formData.host" placeholder="例如: localhost 或 192.168.1.100" />
        </NFormItem>

        <NFormItem label="端口" required>
          <NInputNumber v-model:value="formData.port" :min="1" :max="65535" style="width: 100%" />
        </NFormItem>

        <NFormItem label="用户名" required>
          <NInput v-model:value="formData.user" placeholder="MySQL 用户名" />
        </NFormItem>

        <NFormItem label="密码" required>
          <NInput
            v-model:value="formData.password"
            type="password"
            show-password-on="click"
            placeholder="MySQL 密码"
          />
        </NFormItem>

        <NFormItem label="数据库名称" required>
          <NInput v-model:value="formData.database" placeholder="要导入的数据库名称" />
        </NFormItem>

        <NFormItem label="标准号列表" required>
          <NInput
            v-model:value="formData.standardNos"
            type="textarea"
            :rows="8"
            placeholder="请输入标准号，每行一个&#10;例如：&#10;FZ/T 70020-2025&#10;GB/T 1234-2023"
          />
        </NFormItem>
      </NForm>

      <NAlert type="warning" title="注意" :bordered="false" class="mb-16px">
        <ul class="pl-20px">
          <li>如果标准号已存在，将更新其数据</li>
          <li>章节数据将被完全替换</li>
        </ul>
      </NAlert>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="handleClose">取消</NButton>
          <NButton type="primary" :loading="loading" @click="handleSubmit">开始导入</NButton>
        </NSpace>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.mb-16px {
  margin-bottom: 16px;
}

.pl-20px {
  padding-left: 20px;
}
</style>
