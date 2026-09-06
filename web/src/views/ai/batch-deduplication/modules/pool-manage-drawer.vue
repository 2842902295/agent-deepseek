<script setup lang="tsx">
import { ref, reactive, onMounted } from 'vue';
import {
  NDrawer,
  NDrawerContent,
  NDataTable,
  NButton,
  NSpace,
  NModal,
  NForm,
  NFormItem,
  NInput,
  NSwitch,
  NTag,
  useMessage,
  useDialog,
  type DataTableColumns
} from 'naive-ui';
import {
  fetchDeduplicationPoolList,
  createDeduplicationPool,
  updateDeduplicationPool,
  deleteDeduplicationPool,
  fetchDeduplicationPoolDetail
} from '@/service/api';

const message = useMessage();
const dialog = useDialog();

// Props & Emits
const props = defineProps<{
  show: boolean;
}>();

const emit = defineEmits<{
  'update:show': [value: boolean];
}>();

// 列表数据
const loading = ref(false);
const poolList = ref<Api.AI.DeduplicationPool[]>([]);
const pagination = reactive({
  page: 1,
  pageSize: 10,
  itemCount: 0,
  showSizePicker: true,
  pageSizes: [10, 20, 50],
  onChange: (page: number) => {
    pagination.page = page;
    loadPoolList();
  },
  onUpdatePageSize: (pageSize: number) => {
    pagination.pageSize = pageSize;
    pagination.page = 1;
    loadPoolList();
  }
});

// 搜索条件
const searchPoolName = ref('');

// 编辑/新建对话框
const showEditModal = ref(false);
const editMode = ref<'create' | 'edit'>('create');
const editFormRef = ref();
const editForm = reactive<{
  id?: number;
  pool_name: string;
  description: string;
  standard_nos: string;
  is_active: boolean;
}>({
  pool_name: '',
  description: '',
  standard_nos: '',
  is_active: true
});

// 表单规则
const editFormRules = {
  pool_name: [
    { required: true, message: '请输入查重池名称', trigger: 'blur' },
    { min: 1, max: 200, message: '名称长度在 1 到 200 个字符', trigger: 'blur' }
  ],
  standard_nos: [{ required: true, message: '请输入标准编号', trigger: 'blur' }]
};

// 表格列定义
const columns: DataTableColumns<Api.AI.DeduplicationPool> = [
  {
    key: 'id',
    title: 'ID',
    width: 60,
    align: 'center'
  },
  {
    key: 'pool_name',
    title: '查重池名称',
    width: 200,
    render: row => {
      if (row.is_default) {
        return (
          <NSpace>
            <span>{row.pool_name}</span>
            <NTag type="info" size="small">
              默认
            </NTag>
          </NSpace>
        );
      }
      return row.pool_name;
    }
  },
  {
    key: 'description',
    title: '描述',
    ellipsis: {
      tooltip: true
    }
  },
  {
    key: 'standard_count',
    title: '标准数量',
    width: 100,
    align: 'center'
  },
  {
    key: 'is_active',
    title: '状态',
    width: 80,
    align: 'center',
    render: row => {
      return (
        <NTag type={row.is_active ? 'success' : 'default'} size="small">
          {row.is_active ? '启用' : '禁用'}
        </NTag>
      );
    }
  },
  {
    key: 'create_time',
    title: '创建时间',
    width: 160,
    render: row => {
      if (!row.create_time) return '-';
      return new Date(row.create_time).toLocaleString('zh-CN');
    }
  },
  {
    key: 'actions',
    title: '操作',
    width: 180,
    align: 'center',
    render: row => {
      return (
        <NSpace justify="center">
          <NButton size="small" onClick={() => handleEdit(row)}>
            编辑
          </NButton>
          <NButton
            size="small"
            type="error"
            disabled={row.is_default}
            onClick={() => handleDelete(row)}
          >
            删除
          </NButton>
        </NSpace>
      );
    }
  }
];

// 加载查重池列表
async function loadPoolList() {
  loading.value = true;
  try {
    const res = await fetchDeduplicationPoolList({
      current: pagination.page,
      size: pagination.pageSize,
      pool_name: searchPoolName.value || undefined
    });

    if (res.data) {
      poolList.value = res.data.records;
      pagination.itemCount = res.total || 0;
    }
  } catch (error: any) {
    message.error(`加载失败: ${error.message || '未知错误'}`);
  } finally {
    loading.value = false;
  }
}

// 搜索
function handleSearch() {
  pagination.page = 1;
  loadPoolList();
}

// 重置搜索
function handleResetSearch() {
  searchPoolName.value = '';
  pagination.page = 1;
  loadPoolList();
}

// 打开新建对话框
function handleCreate() {
  editMode.value = 'create';
  editForm.id = undefined;
  editForm.pool_name = '';
  editForm.description = '';
  editForm.standard_nos = '';
  editForm.is_active = true;
  showEditModal.value = true;
}

// 打开编辑对话框
async function handleEdit(row: Api.AI.DeduplicationPool) {
  editMode.value = 'edit';
  editForm.id = row.id;
  editForm.pool_name = row.pool_name;
  editForm.description = row.description || '';
  editForm.is_active = row.is_active;

  // 加载详细信息（包含标准编号列表）
  try {
    const res = await fetchDeduplicationPoolDetail(row.id);
    if (res.data && res.data.standard_nos) {
      editForm.standard_nos = res.data.standard_nos.join('\n');
    }
    showEditModal.value = true;
  } catch (error: any) {
    message.error(`加载详情失败: ${error.message || '未知错误'}`);
  }
}

// 提交表单
async function handleSubmit() {
  await editFormRef.value?.validate();

  const standardNos = editForm.standard_nos
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0);

  if (standardNos.length === 0) {
    message.warning('请输入至少一个标准编号');
    return;
  }

  try {
    if (editMode.value === 'create') {
      await createDeduplicationPool({
        pool_name: editForm.pool_name,
        description: editForm.description || undefined,
        standard_nos: standardNos
      });
      message.success('创建成功');
    } else {
      await updateDeduplicationPool(editForm.id!, {
        pool_name: editForm.pool_name,
        description: editForm.description || undefined,
        standard_nos: standardNos,
        is_active: editForm.is_active
      });
      message.success('更新成功');
    }

    showEditModal.value = false;
    loadPoolList();
  } catch (error: any) {
    message.error(`操作失败: ${error.message || '未知错误'}`);
  }
}

// 删除查重池
function handleDelete(row: Api.AI.DeduplicationPool) {
  dialog.warning({
    title: '确认删除',
    content: `确定要删除查重池"${row.pool_name}"吗？此操作不可恢复。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await deleteDeduplicationPool(row.id);
        message.success('删除成功');
        loadPoolList();
      } catch (error: any) {
        message.error(`删除失败: ${error.message || '未知错误'}`);
      }
    }
  });
}

// 组件挂载时加载列表
onMounted(() => {
  loadPoolList();
});
</script>

<template>
  <NDrawer :show="show" :width="1200" @update:show="emit('update:show', $event)">
    <NDrawerContent title="查重池管理" closable>
      <NSpace vertical :size="16">
        <!-- 搜索栏 -->
        <NSpace>
          <NInput
            v-model:value="searchPoolName"
            placeholder="搜索查重池名称"
            clearable
            style="width: 200px"
            @keyup.enter="handleSearch"
          />
          <NButton type="primary" @click="handleSearch">搜索</NButton>
          <NButton @click="handleResetSearch">重置</NButton>
          <NButton type="primary" @click="handleCreate">新建查重池</NButton>
        </NSpace>

        <!-- 表格 -->
        <NDataTable
          :columns="columns"
          :data="poolList"
          :loading="loading"
          :pagination="pagination"
          :scroll-x="1000"
        />
      </NSpace>
    </NDrawerContent>
  </NDrawer>

  <!-- 编辑/新建对话框 -->
  <NModal
    v-model:show="showEditModal"
    :title="editMode === 'create' ? '新建查重池' : '编辑查重池'"
    preset="dialog"
    style="width: 600px"
  >
    <NForm ref="editFormRef" :model="editForm" :rules="editFormRules" label-placement="left" label-width="100">
      <NFormItem label="查重池名称" path="pool_name">
        <NInput v-model:value="editForm.pool_name" placeholder="请输入查重池名称" />
      </NFormItem>

      <NFormItem label="描述" path="description">
        <NInput
          v-model:value="editForm.description"
          type="textarea"
          :rows="3"
          placeholder="请输入查重池描述（可选）"
        />
      </NFormItem>

      <NFormItem label="标准编号" path="standard_nos">
        <NInput
          v-model:value="editForm.standard_nos"
          type="textarea"
          :rows="10"
          placeholder="请输入标准编号，每行一个&#10;例如：&#10;GB/T 11313.24-2009&#10;GB/T 27001-2022"
        />
      </NFormItem>

      <NFormItem v-if="editMode === 'edit'" label="启用状态" path="is_active">
        <NSwitch v-model:value="editForm.is_active" />
      </NFormItem>
    </NForm>

    <template #action>
      <NSpace justify="end">
        <NButton @click="showEditModal = false">取消</NButton>
        <NButton type="primary" @click="handleSubmit">确定</NButton>
      </NSpace>
    </template>
  </NModal>
</template>
