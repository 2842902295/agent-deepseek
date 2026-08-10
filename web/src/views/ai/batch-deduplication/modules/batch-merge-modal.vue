<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { NButton, NCard, NDataTable, NForm, NFormItem, NInput, NModal, NSpace, NTag, useMessage } from 'naive-ui';
import { mergeBatches } from '@/service/api';

interface Props {
  show: boolean;
  selectedBatches: Api.AI.BatchHistoryRecord[];
}

const props = defineProps<Props>();
const emit = defineEmits<{
  'update:show': [value: boolean];
  success: [batchId: number];
}>();

const message = useMessage();
const submitting = ref(false);
const form = ref({
  batch_name: '',
  remark: ''
});

watch(
  () => props.show,
  v => {
    if (v) {
      form.value.batch_name = '';
      form.value.remark = '';
    }
  }
);

const poolName = computed(() => props.selectedBatches[0]?.pool_name ?? '-');

const totalStandards = computed(() =>
  props.selectedBatches.reduce((sum, b) => sum + (b.total_count || 0), 0)
);

const columns = [
  { title: '批次ID', key: 'id', width: 70 },
  {
    title: '批次名称',
    key: 'batch_name',
    ellipsis: { tooltip: true },
    render: (row: Api.AI.BatchHistoryRecord) => row.batch_name || `批次 #${row.id}`
  },
  {
    title: '创建时间',
    key: 'create_time',
    width: 150,
    render: (row: Api.AI.BatchHistoryRecord) =>
      row.create_time ? new Date(row.create_time).toLocaleString('zh-CN') : '-'
  },
  { title: '总数', key: 'total_count', width: 70, align: 'center' as const },
  {
    title: '需要关注',
    key: 'duplicate_count',
    width: 90,
    align: 'center' as const
  }
];

async function handleSubmit() {
  if (props.selectedBatches.length < 2) {
    message.warning('至少需要选择 2 个批次');
    return;
  }
  submitting.value = true;
  try {
    const { error, data } = await mergeBatches({
      source_batch_ids: props.selectedBatches.map(b => b.id),
      batch_name: form.value.batch_name || undefined,
      remark: form.value.remark || undefined
    });
    if (!error && data?.batch_id) {
      message.success(`合并成功，新批次包含 ${data.total} 个标准`);
      emit('success', data.batch_id);
      emit('update:show', false);
    }
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="false"
    preset="card"
    title="合并批次"
    style="width: 720px"
    @update:show="emit('update:show', $event)"
  >
    <NSpace vertical :size="16">
      <!-- 提示信息 -->
      <NCard :bordered="false" size="small" class="bg-gray-50 dark:bg-gray-800">
        <NSpace vertical :size="6">
          <div class="flex items-center gap-8px text-sm">
            <icon-mdi:information-outline class="text-primary" />
            <span>共选中 <b class="text-primary">{{ selectedBatches.length }}</b> 个批次，含约
              <b class="text-primary">{{ totalStandards }}</b> 个标准（合并后将按标准号去重）</span>
          </div>
          <div class="flex items-center gap-8px text-sm">
            <icon-mdi:database-outline class="text-gray-500" />
            <span>查重池：<NTag size="small" :type="poolName === '全库' ? 'info' : 'default'">{{ poolName }}</NTag></span>
          </div>
          <div class="flex items-start gap-8px text-xs text-gray-500">
            <icon-mdi:lightbulb-outline class="mt-2px" />
            <span>同一标准号在多个批次中存在时，将保留<b class="text-amber-600">最近一次更新</b>的结果；原批次不会被删除。</span>
          </div>
        </NSpace>
      </NCard>

      <!-- 选中清单 -->
      <NCard title="待合并批次" :bordered="false" size="small">
        <NDataTable
          :columns="columns"
          :data="selectedBatches"
          size="small"
          :max-height="240"
          :pagination="false"
          :row-key="(row: Api.AI.BatchHistoryRecord) => row.id"
        />
      </NCard>

      <!-- 表单 -->
      <NCard title="合并后批次信息" :bordered="false" size="small">
        <NForm label-placement="left" label-width="80" :show-feedback="false" size="small">
          <NFormItem label="批次名称">
            <NInput
              v-model:value="form.batch_name"
              placeholder="留空将自动生成（如：合并批次（共 N 个标准））"
              maxlength="200"
              show-count
              clearable
            />
          </NFormItem>
          <NFormItem label="备注" class="mt-12px">
            <NInput
              v-model:value="form.remark"
              type="textarea"
              :rows="2"
              placeholder="可选，留空将自动填入源批次 ID 列表"
              maxlength="500"
              show-count
            />
          </NFormItem>
        </NForm>
      </NCard>
    </NSpace>

    <template #footer>
      <NSpace justify="end">
        <NButton @click="emit('update:show', false)">取消</NButton>
        <NButton type="primary" :loading="submitting" @click="handleSubmit">
          确认合并
        </NButton>
      </NSpace>
    </template>
  </NModal>
</template>
