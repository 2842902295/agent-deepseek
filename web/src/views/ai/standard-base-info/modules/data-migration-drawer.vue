<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { NDrawer, NDrawerContent, NForm, NFormItem, NInput, NInputNumber, NButton, NSpace, NAlert } from 'naive-ui';
import { importDataFromMySQL } from '@/service/api';

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
const STORAGE_KEY = 'mysql_migration_config';

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
    database: ''
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

// 监听表单数据变化，自动保存
watch(
  formData,
  () => {
    saveConfig();
  },
  { deep: true }
);

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

  loading.value = true;

  try {
    const { data, error } = await importDataFromMySQL(formData.value);

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
    <NDrawerContent title="从 MySQL 迁入数据" closable>
      <NAlert type="warning" title="注意" :bordered="false" class="mb-16px">
        此操作将从 MySQL 数据库导入数据，并覆盖当前的表数据。请确保已备份重要数据！
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
      </NForm>

      <NAlert type="info" title="将导入以下表" :bordered="false" class="mb-16px">
        <ul class="pl-20px">
          <li>standard_base_info（标准基础信息）</li>
          <li>standard_jgh_pdf（标准 PDF 信息）</li>
          <li>standard_jgh_pdf_chapter（标准 PDF 章节）</li>
          <li>standard_jgh_pdf_table（PDF 表格表）</li>
          <li>standard_jgh_pdf_formula（PDF 公式表）</li>
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
