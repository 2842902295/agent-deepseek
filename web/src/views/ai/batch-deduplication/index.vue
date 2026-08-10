<script setup lang="tsx">
import {computed, onMounted, onUnmounted, reactive, ref} from 'vue';
import type {DataTableColumns} from 'naive-ui';
import {NButton, NCard, NDatePicker, NInput, NPopconfirm, NProgress, NSelect, NSpace, NStatistic, NTag, NTooltip} from 'naive-ui';
import {deleteBatchRecord, fetchAllActivePools, fetchBatchHistory, fetchBatchSummary, resumeBatchRecord} from '@/service/api';
import {defaultTransform, useNaivePaginatedTable} from '@/hooks/common/table';
import BatchCheckDrawer from './modules/batch-check-drawer.vue';
import PoolManageDrawer from './modules/pool-manage-drawer.vue';
import BatchResultDrawer from './modules/batch-result-drawer.vue';
import BatchMergeModal from './modules/batch-merge-modal.vue';

const searchParams = reactive<{
  current: number;
  size: number;
  batch_name?: string;
  status?: string;
  pool_id?: number;
  start_time?: string;
  end_time?: string;
}>({
  current: 1,
  size: 10
});

// 筛选表单（独立于 searchParams，点搜索才提交）
const filterForm = reactive({
  batch_name: '',
  status: null as string | null,
  pool_id: null as number | null,
  dateRange: null as [number, number] | null
});

const poolOptions = ref<{ label: string; value: number }[]>([]);
const statusOptions = [
  {label: '已完成', value: 'completed'},
  {label: '处理中', value: 'processing'},
  {label: '失败', value: 'failed'}
];

async function loadPoolOptions() {
  const res = await fetchAllActivePools();
  const pools = (res.data as any)?.pools || [];
  poolOptions.value = [
    {label: '全库', value: -1},
    ...pools.map((p: any) => ({label: p.pool_name, value: p.id}))
  ];
}

const summary = ref<Api.AI.BatchSummary | null>(null);
const summaryLoading = ref(false);

async function loadSummary() {
  summaryLoading.value = true;
  try {
    const res = await fetchBatchSummary();
    if (res.data) summary.value = res.data;
  } finally {
    summaryLoading.value = false;
  }
}

const {
  columns,
  columnChecks,
  data,
  loading,
  getData,
  getDataByPage,
  mobilePagination
} = useNaivePaginatedTable({
  api: () => fetchBatchHistory(searchParams),
  transform: response => defaultTransform(response),
  onPaginationParamsChange: params => {
    searchParams.current = params.page ?? 1;
    searchParams.size = params.pageSize ?? 10;
  },
  columns: () => [
    {
      type: 'selection',
      disabled: (row: any) => row.status === 'processing'
    },
    {
      key: 'index',
      title: '序号',
      width: 60,
      align: 'center',
      render: (_row: any, rowIndex: number) =>
        (searchParams.current - 1) * searchParams.size + rowIndex + 1
    },
    {
      key: 'batch_name',
      title: '批次名称',
      width: 160,
      ellipsis: {tooltip: true},
      render: (row: any) => row.batch_name || `批次 #${row.id}`
    },
    {
      key: 'pool_name',
      title: '查重池',
      width: 130,
      render: (row: any) => {
        if (!row.pool_name || row.pool_name === '-') return <span class="text-gray-400">-</span>;
        return <NTag size="small" type={row.pool_name === '全库' ? 'info' : 'default'}>{row.pool_name}</NTag>;
      }
    },
    {
      key: 'create_time',
      title: '创建时间',
      width: 160,
      render: (row: any) => row.create_time ? new Date(row.create_time).toLocaleString('zh-CN') : '-'
    },
    {
      key: 'total_count',
      title: '总数',
      width: 70,
      align: 'center'
    },
    {
      key: 'success_rate',
      title: '成功率/进度',
      width: 150,
      render: (row: any) => {
        // processing 状态：显示实时进度（done+failed 占比）
        if (row.status === 'processing' && row.progress) {
          const p = row.progress;
          const finished = (p.done ?? 0) + (p.failed ?? 0);
          const total = row.total_count || (finished + (p.pending ?? 0) + (p.running ?? 0));
          const rate = total ? Math.round((finished / total) * 100) : 0;
          return (
            <div class="flex items-center gap-6px">
              <NProgress
                type="line"
                percentage={rate}
                height={6}
                showIndicator={false}
                style="flex: 1; min-width: 60px"
                status="info"
              />
              <span class="text-xs text-gray-600 whitespace-nowrap">{finished}/{total}</span>
            </div>
          );
        }
        if (!row.total_count) return '-';
        const rate = Math.round((row.success_count / row.total_count) * 100);
        return (
          <div class="flex items-center gap-6px">
            <NProgress
              type="line"
              percentage={rate}
              height={6}
              showIndicator={false}
              style="flex: 1; min-width: 60px"
              status={rate >= 90 ? 'success' : rate >= 60 ? 'warning' : 'error'}
            />
            <span class="text-xs text-gray-600 whitespace-nowrap">{rate}%</span>
          </div>
        );
      }
    },
    {
      key: 'duplicate_count',
      title: '需要关注',
      width: 90,
      align: 'center',
      render: (row: any) => {
        if (row.duplicate_count > 0) return <NTag type="warning">{row.duplicate_count}</NTag>;
        return '0';
      }
    },
    {
      key: 'status',
      title: '状态',
      width: 90,
      align: 'center',
      render: (row: any) => {
        const statusMap: Record<string, { type: 'success' | 'error' | 'warning' | 'info'; text: string }> = {
          completed: { type: 'success', text: '已完成' },
          processing: { type: 'warning', text: '处理中' },
          failed: { type: 'error', text: '失败' }
        };
        const s = statusMap[row.status] || {type: 'info', text: row.status};
        return <NTag type={s.type}>{s.text}</NTag>;
      }
    },
    {
      key: 'remark',
      title: '备注',
      minWidth: 120,
      ellipsis: {tooltip: true},
      render: (row: any) => row.remark || <span class="text-gray-400">-</span>
    },
    {
      key: 'operate',
      title: '操作',
      width: 200,
      align: 'center',
      render: (row: any) => {
        // 只在 processing 且无 running 中（即已"卡住"）时允许续跑
        const p = row.progress;
        const canResume = row.status === 'processing'
          && p
          && (p.running ?? 0) === 0
          && ((p.pending ?? 0) + (p.failed ?? 0)) > 0;
        return (
          <NSpace justify="center" size={6}>
            <NButton size="small" type="primary" ghost onClick={() => handleViewResult(row)}>
              查看结果
            </NButton>
            {canResume && (
              <NButton size="small" type="warning" ghost onClick={() => handleResume(row.id)}>
                续跑
              </NButton>
            )}
            <NPopconfirm onPositiveClick={() => handleDelete(row.id)}>
              {{
                default: () => '确认删除该批次记录？',
                trigger: () => <NButton size="small" type="error" ghost>删除</NButton>
              }}
            </NPopconfirm>
          </NSpace>
        );
      }
    }
  ]
});

const typedColumns = computed(() => columns.value as DataTableColumns<any>);

function applyFilter() {
  searchParams.current = 1;
  searchParams.batch_name = filterForm.batch_name || undefined;
  searchParams.status = filterForm.status || undefined;
  searchParams.pool_id = filterForm.pool_id ?? undefined;
  if (filterForm.dateRange) {
    searchParams.start_time = new Date(filterForm.dateRange[0]).toISOString();
    searchParams.end_time = new Date(filterForm.dateRange[1]).toISOString();
  } else {
    searchParams.start_time = undefined;
    searchParams.end_time = undefined;
  }
  getData();
}

function resetFilter() {
  filterForm.batch_name = '';
  filterForm.status = null;
  filterForm.pool_id = null;
  filterForm.dateRange = null;
  searchParams.current = 1;
  searchParams.batch_name = undefined;
  searchParams.status = undefined;
  searchParams.pool_id = undefined;
  searchParams.start_time = undefined;
  searchParams.end_time = undefined;
  getData();
}

const showBatchCheck = ref(false);
const showPoolManage = ref(false);
const showBatchResult = ref(false);
const selectedBatchId = ref<number | null>(null);
const selectedBatchName = ref<string | null>(null);

function handleAdd() {
  showBatchCheck.value = true;
}

function handleManagePool() {
  showPoolManage.value = true;
}

function handleBatchCheckSuccess() {
  getDataByPage();
  loadSummary();
}

function handleViewResult(row: any) {
  selectedBatchId.value = row.id;
  selectedBatchName.value = row.batch_name || `批次 #${row.id}`;
  showBatchResult.value = true;
}

async function handleDelete(batchId: number) {
  const { error } = await deleteBatchRecord(batchId);
  if (!error) {
    window.$message?.success('删除成功');
    await getDataByPage();
    void loadSummary();
  }
}

async function handleResume(batchId: number) {
  const { error } = await resumeBatchRecord(batchId);
  if (!error) {
    window.$message?.success('已触发续跑，正在后台执行');
    await getDataByPage();
  }
}

// ── 批次合并相关 ──
const checkedRowKeys = ref<number[]>([]);
const showMergeModal = ref(false);

// 当前选中的批次完整记录
const checkedBatches = computed<Api.AI.BatchHistoryRecord[]>(() => {
  const map = new Map<number, Api.AI.BatchHistoryRecord>();
  (data.value || []).forEach((r: any) => map.set(r.id, r));
  return checkedRowKeys.value.map(id => map.get(id)).filter(Boolean) as Api.AI.BatchHistoryRecord[];
});

// pool_id 是否一致（仅在选中 ≥2 时才有意义）
const poolConflict = computed(() => {
  if (checkedBatches.value.length < 2) return false;
  const set = new Set(checkedBatches.value.map(b => b.pool_id));
  return set.size > 1;
});

const mergeBtnTooltip = computed(() => {
  if (checkedRowKeys.value.length < 2) return '至少选择 2 个批次';
  if (poolConflict.value) return '所选批次的查重池不一致，无法合并';
  return '';
});

function handleRowKeysChange(keys: (string | number)[]) {
  checkedRowKeys.value = keys.map(k => Number(k));
}

function handleOpenMerge() {
  if (checkedRowKeys.value.length < 2) {
    window.$message?.warning('至少选择 2 个批次');
    return;
  }
  if (poolConflict.value) {
    window.$message?.warning('所选批次的查重池不一致，无法合并');
    return;
  }
  showMergeModal.value = true;
}

function handleClearSelection() {
  checkedRowKeys.value = [];
}

async function handleMergeSuccess(_newBatchId: number) {
  checkedRowKeys.value = [];
  await getDataByPage();
  void loadSummary();
}

// 列表中存在 processing 行时，自动轮询刷新进度
let pollTimer: ReturnType<typeof setInterval> | null = null;

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(() => {
    const hasProcessing = (data.value || []).some((r: any) => r.status === 'processing');
    if (hasProcessing) {
      void getDataByPage();
    }
  }, 5000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

onMounted(() => {
  loadSummary();
  loadPoolOptions();
  startPolling();
});

onUnmounted(() => {
  stopPolling();
});
</script>

<template>
  <div class="flex-col-stretch gap-16px lt-sm:overflow-auto">
    <!-- KPI 看板 -->
    <div class="grid grid-cols-4 gap-16px lt-sm:grid-cols-2">
      <NCard :bordered="false" class="card-wrapper" size="small">
        <NStatistic :loading="summaryLoading" :value="summary?.total_batches ?? '-'" label="总批次数"/>
      </NCard>
      <NCard :bordered="false" class="card-wrapper" size="small">
        <NStatistic :loading="summaryLoading" :value="summary?.recent_7d_batches ?? '-'" label="近7天新增"/>
      </NCard>
      <NCard :bordered="false" class="card-wrapper" size="small">
        <NStatistic :loading="summaryLoading" :value="summary?.total_duplicate ?? '-'" label="累计关注标准"/>
      </NCard>
      <NCard :bordered="false" class="card-wrapper" size="small">
        <NStatistic :loading="summaryLoading" :value="summary?.total_analyzed ?? '-'" label="累计分析标准"/>
      </NCard>
    </div>

    <!-- 筛选栏 -->
    <NCard :bordered="false" class="card-wrapper" size="small">
      <NSpace :size="[12, 8]" align="center" wrap>
        <NInput
          v-model:value="filterForm.batch_name"
          clearable
          placeholder="批次名称"
          style="width: 160px"
          @keyup.enter="applyFilter"
        />
        <NSelect
          v-model:value="filterForm.status"
          :options="statusOptions"
          clearable
          placeholder="状态"
          style="width: 120px"
        />
        <NSelect
          v-model:value="filterForm.pool_id"
          :options="poolOptions"
          clearable
          filterable
          placeholder="查重池"
          style="width: 160px"
        />
        <NDatePicker
          v-model:value="filterForm.dateRange"
          clearable
          placeholder="创建时间范围"
          style="width: 240px"
          type="daterange"
        />
        <NButton type="primary" @click="applyFilter">搜索</NButton>
        <NButton @click="resetFilter">重置</NButton>
      </NSpace>
    </NCard>

    <!-- 历史列表 -->
    <NCard :bordered="false" class="card-wrapper" size="small" title="批量相似度分析历史">
      <template #header-extra>
        <NSpace>
          <NButton size="small" @click="handleManagePool">管理查重池</NButton>
          <TableHeaderOperation
            v-model:columns="columnChecks"
            :loading="loading"
            add-text="新建批量分析"
            table-id="batch-deduplication"
            :show-delete="false"
            @add="handleAdd"
            @refresh="getData"
          />
        </NSpace>
      </template>

      <!-- 选中操作条（仅当有选中时显示） -->
      <Transition name="fade-slide">
        <div
          v-if="checkedRowKeys.length > 0"
          class="mb-12px flex items-center justify-between rounded-6px bg-primary/8 px-12px py-8px"
        >
          <NSpace align="center" :size="12">
            <span class="text-sm">
              已选中 <b class="text-primary">{{ checkedRowKeys.length }}</b> 个批次
            </span>
            <NTag
              v-if="poolConflict"
              size="small"
              type="warning"
              :bordered="false"
            >
              <template #icon>
                <icon-mdi:alert-circle-outline />
              </template>
              查重池不一致，无法合并
            </NTag>
          </NSpace>
          <NSpace :size="8">
            <NTooltip :disabled="!mergeBtnTooltip" placement="top">
              <template #trigger>
                <NButton
                  size="small"
                  type="primary"
                  :disabled="checkedRowKeys.length < 2 || poolConflict"
                  @click="handleOpenMerge"
                >
                  <template #icon>
                    <icon-mdi:merge />
                  </template>
                  合并选中
                </NButton>
              </template>
              {{ mergeBtnTooltip }}
            </NTooltip>
            <NButton size="small" quaternary @click="handleClearSelection">
              取消选择
            </NButton>
          </NSpace>
        </div>
      </Transition>

      <NDataTable
        :columns="typedColumns"
        :data="data"
        size="small"
        :scroll-x="1100"
        :loading="loading"
        remote
        :row-key="(row: any) => row.id"
        :checked-row-keys="checkedRowKeys"
        :pagination="mobilePagination"
        class="sm:h-full"
        @update:checked-row-keys="handleRowKeysChange"
      />
    </NCard>

    <BatchCheckDrawer v-model:show="showBatchCheck" @success="handleBatchCheckSuccess" />
    <PoolManageDrawer v-model:show="showPoolManage" />
    <BatchResultDrawer
      v-model:show="showBatchResult"
      :batch-id="selectedBatchId"
      :batch-name="selectedBatchName"
    />
    <BatchMergeModal
      v-model:show="showMergeModal"
      :selected-batches="checkedBatches"
      @success="handleMergeSuccess"
    />
  </div>
</template>

<style scoped>
.card-wrapper {
  @apply rounded-8px shadow-sm;
}

.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}
.fade-slide-enter-from,
.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
