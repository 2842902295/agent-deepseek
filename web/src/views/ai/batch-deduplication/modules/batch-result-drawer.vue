<script lang="tsx" setup>
import {computed, reactive, ref, watch} from 'vue';
import {
  NButton,
  NCard,
  NDataTable,
  NDivider,
  NDrawer,
  NDrawerContent,
  NInput,
  NSelect,
  NSpace,
  NSpin,
  NStatistic,
  NSwitch,
  NTag,
  NTooltip
} from 'naive-ui';
import {
  exportBatchToExcel,
  exportStandardToWord,
  fetchAIComparisonBatch,
  fetchBatchDetail,
  fetchBatchStats,
  fetchFullTextSimilarityBatch
} from '@/service/api';
import {useNaivePaginatedTable} from '@/hooks/common/table';
import StandardDetailDrawer from '../../batch-detail/modules/standard-detail-drawer.vue';
import StandardEvaluationDrawer from '../../batch-detail/modules/standard-evaluation-drawer.vue';

const props = defineProps<{
  show: boolean;
  batchId: number | null;
  batchName?: string | null;
}>();

const emit = defineEmits<{
  'update:show': [value: boolean];
}>();

const tagLabels: Record<string, string> = {
  同系列标准: '同系列标准',
  标准化对象一致: '标准化对象一致',
  通用专用关系: '通用专用关系',
  适用范围重叠: '适用范围相似'
};

const tagOptions = Object.keys(tagLabels).map(key => ({label: tagLabels[key], value: key}));

const showOnlyNeedAttention = ref(false);
const showOnlyFound = ref(true);
const selectedFilterTags = ref<string[]>([]);
const filterTagsMode = ref<string>('any');
const hiddenTags = ref<string[]>([]);
const filterStandardNo = ref('');
const filterStandardName = ref('');
const mainHasResFilter = ref<string>('has_res');

const checkedRowKeys = ref<string[]>([]);
const batchRecord = ref<any>(null);
const statsData = ref<Api.AI.BatchStatsResponse['data'] | null>(null);
const loadingStats = ref(false);

const showStandardDetail = ref(false);
const selectedStandard = ref<any>(null);
const showEvaluation = ref(false);
const evaluationStandardNo = ref('');

const sortField = ref<string | null>(null);
const sortOrder = ref<'ascend' | 'descend' | null>(null);

const searchParams = reactive({
  current: 1,
  size: 10,
  only_need_attention: false,
  only_found: true,
  filter_tags: '',
  hidden_tags: '',
  filter_tags_mode: 'any',
  sort_field: null as string | null,
  sort_order: null as string | null,
  filter_standard_no: '',
  filter_standard_name: '',
  main_has_res_filter: 'has_res'
});

const {loading, data, columns: tableColumns, getData, mobilePagination} = useNaivePaginatedTable<any, any>({
  api: async () => {
    if (!props.batchId) return {data: {records: [], current: 1, size: 10, total: 0}} as any;
    const response = await fetchBatchDetail(
      props.batchId,
      searchParams.current,
      searchParams.size,
      searchParams.only_need_attention,
      searchParams.only_found,
      searchParams.filter_tags,
      searchParams.hidden_tags,
      searchParams.filter_tags_mode,
      searchParams.sort_field,
      searchParams.sort_order,
      searchParams.filter_standard_no,
      searchParams.filter_standard_name,
      '',
      searchParams.main_has_res_filter
    );
    batchRecord.value = response.data?.record || (response as any).record;
    return response;
  },
  transform: (response: any) => {
    const record = response.data?.record || response.record || {};
    const pagination = response.data?.pagination || response.pagination || {};
    const results = record.results || [];
    return {
      data: results.map((item: any, index: number) => ({
        ...item,
        index: (pagination.current - 1) * pagination.size + index + 1
      })),
      pageNum: pagination.current || 1,
      pageSize: pagination.size || 10,
      total: pagination.total || 0
    };
  },
  showTotal: true,
  onPaginationParamsChange: params => {
    searchParams.current = params.page ?? 1;
    searchParams.size = params.pageSize ?? 10;
  },
  immediate: false,
  columns: () => [
    {type: 'selection', fixed: 'left'},
    {key: 'index', title: '序号', width: 64, align: 'center'},
    {title: '标准编号', key: 'standard_no', width: 180, fixed: 'left', sorter: true},
    {title: '标准名称', key: 'standard_name', minWidth: 250, ellipsis: {tooltip: true}, sorter: true},
    {
      title: '需要关注', key: 'need_attention', width: 100, align: 'center', sorter: true,
      render: (row: any) => {
        if (!row.found) return <NTag type="error" size="small">未找到</NTag>;
        return row.need_attention
          ? <NTag type="warning" size="small">需要关注</NTag>
          : <NTag type="success" size="small">无需关注</NTag>;
      }
    },
    {
      title: '相似标准数', key: 'similar_count', width: 110, align: 'center', sorter: true,
      render: (row: any) => (!row.found || !row.similar_standards) ? '-' : row.similar_standards.length
    },
    {
      title: '疑似交叉类型', key: 'tags', minWidth: 280,
      render: (row: any) => {
        if (!row.found || !row.similar_standards?.length) return <span class="text-gray-400 text-xs">无相似标准</span>;
        const allTags = new Set<string>();
        row.similar_standards.forEach((s: any) => {
          Object.entries(s.tags || {}).forEach(([k, v]) => {
            if (v && tagLabels[k]) allTags.add(k);
          });
        });
        if (!allTags.size) return <span class="text-gray-400 text-xs">无匹配标签</span>;
        const tagPriority: Record<string, number> = {
          标准化对象一致: 1,
          适用范围重叠: 2,
          通用专用关系: 3,
          同系列标准: 4
        };
        const getTagType = (k: string) => k === '标准化对象一致' || k === '适用范围重叠' ? 'warning' : k === '通用专用关系' ? 'info' : 'default';
        const sorted = Array.from(allTags).sort((a, b) => (tagPriority[a] || 999) - (tagPriority[b] || 999));
        return (
          <NSpace size={[4, 4]} wrap={false}>
            {sorted.slice(0, 3).map(tag => <NTag key={tag} type={getTagType(tag) as any}
                                                 size="small">{tagLabels[tag]}</NTag>)}
            {sorted.length > 3 && (
              <NTooltip trigger="hover">
                {{
                  trigger: () => <NTag size="small" type="info">+{sorted.length - 3}</NTag>,
                  default: () => sorted.slice(3).map(t => <div key={t}>{tagLabels[t]}</div>)
                }}
              </NTooltip>
            )}
          </NSpace>
        );
      }
    },
    {
      title: '操作', key: 'actions', width: 180, align: 'center', fixed: 'right',
      render: (row: any) => {
        if (!row.found) return <span class="text-gray-400 text-xs">{row.error || '未找到'}</span>;
        return (
          <NSpace size={6} justify="center">
            <NButton type="primary" ghost size="small" onClick={() => {
              selectedStandard.value = row;
              showStandardDetail.value = true;
            }}>查看详情</NButton>
            <NButton type="warning" ghost size="small" onClick={() => {
              evaluationStandardNo.value = row.standard_no;
              showEvaluation.value = true;
            }}>整合评估</NButton>
          </NSpace>
        );
      }
    }
  ]
});

const columns = computed(() => tableColumns.value);

async function loadStats() {
  if (!props.batchId) return;
  loadingStats.value = true;
  try {
    const res = await fetchBatchStats(props.batchId);
    statsData.value = res.data ?? null;
  } finally {
    loadingStats.value = false;
  }
}

function resetFilters() {
  showOnlyNeedAttention.value = false;
  showOnlyFound.value = true;
  selectedFilterTags.value = [];
  filterTagsMode.value = 'any';
  hiddenTags.value = [];
  filterStandardNo.value = '';
  filterStandardName.value = '';
  mainHasResFilter.value = 'has_res';
  sortField.value = null;
  sortOrder.value = null;
  checkedRowKeys.value = [];
}

watch(
  () => props.show,
  val => {
    if (val && props.batchId) {
      resetFilters();
      Object.assign(searchParams, {
        current: 1,
        size: 10,
        only_need_attention: false,
        only_found: true,
        filter_tags: '',
        hidden_tags: '',
        filter_tags_mode: 'any',
        sort_field: null,
        sort_order: null,
        filter_standard_no: '',
        filter_standard_name: '',
        main_has_res_filter: 'has_res'
      });
      getData();
      loadStats();
    }
  }
);

watch(
  [showOnlyNeedAttention, showOnlyFound, selectedFilterTags, filterTagsMode, hiddenTags, filterStandardNo, filterStandardName, mainHasResFilter],
  () => {
    Object.assign(searchParams, {
      only_need_attention: showOnlyNeedAttention.value,
      only_found: showOnlyFound.value,
      filter_tags: selectedFilterTags.value.join(','),
      filter_tags_mode: filterTagsMode.value,
      hidden_tags: hiddenTags.value.join(','),
      filter_standard_no: filterStandardNo.value.trim(),
      filter_standard_name: filterStandardName.value.trim(),
      main_has_res_filter: mainHasResFilter.value,
      current: 1
    });
    getData();
  }
);

watch(showStandardDetail, val => {
  if (!val) getData();
});

function handleSorterChange(sorter: any) {
  if (!sorter || sorter.order === false) {
    sortField.value = null;
    sortOrder.value = null;
  } else {
    sortField.value = sorter.columnKey;
    sortOrder.value = sorter.order;
  }
  Object.assign(searchParams, {sort_field: sortField.value, sort_order: sortOrder.value, current: 1});
  getData();
}

const exporting = ref(false);

async function handleExport() {
  if (!props.batchId) return;
  exporting.value = true;
  try {
    const blob = await exportBatchToExcel(props.batchId);
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `批次查重结果_${props.batchId}_${Date.now()}.xlsx`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
    window.$message?.success('导出成功');
  } catch (e: any) {
    window.$message?.error(`导出失败: ${e.message}`);
  } finally {
    exporting.value = false;
  }
}

const executingBatch = ref(false);

async function handleBatchExecute() {
  const selectedRows = data.value.filter((item: any) => checkedRowKeys.value.includes(item.standard_no) && item.found && item.similar_standards?.length > 0);
  if (!selectedRows.length) {
    window.$message?.error('勾选的标准中没有可比对的数据');
    return;
  }
  const aiItems: Api.AI.AIComparisonRequest[] = [];
  const simItems: Api.AI.FullTextSimilarityRequest[] = [];
  for (const row of selectedRows) {
    for (const s of row.similar_standards.filter((s: any) => s.need_attention === true)) {
      aiItems.push({source_standard_no: row.standard_no, target_standard_no: s.standard_no, force_recalculate: false});
      simItems.push({source_standard_no: row.standard_no, target_standard_no: s.standard_no, force_recalculate: false});
    }
  }
  if (!aiItems.length) {
    window.$message?.error('没有标记为"需要关注"的相似标准');
    return;
  }
  executingBatch.value = true;
  window.$message?.info(`开始执行比对，共 ${aiItems.length} 对任务...`);
  try {
    const [aiRes, simRes] = await Promise.all([fetchAIComparisonBatch(aiItems), fetchFullTextSimilarityBatch(simItems)]);
    const aiOk = (aiRes.data || []).filter((r: any) => r?.data?.success).length;
    const simOk = (simRes.data || []).filter((r: any) => r?.data?.success).length;
    window.$message?.success(`比对完成：AI ${aiOk}/${aiItems.length}，全文相似度 ${simOk}/${simItems.length}`);
    getData();
  } catch (e: any) {
    window.$message?.error(`执行失败: ${e.message}`);
  } finally {
    executingBatch.value = false;
  }
}

const exportingWord = ref(false);

async function handleBatchExportWord() {
  if (!props.batchId || !checkedRowKeys.value.length) {
    window.$message?.error('请先勾选要导出的标准');
    return;
  }
  const selectedStandards = data.value.filter((item: any) => checkedRowKeys.value.includes(item.standard_no) && item.found);
  if (!selectedStandards.length) {
    window.$message?.error('勾选的标准中没有可导出的有效数据');
    return;
  }
  exportingWord.value = true;
  window.$message?.info(`开始批量导出 ${selectedStandards.length} 个标准...`);
  let ok = 0;
  let fail = 0;
  for (const std of selectedStandards) {
    try {
      const blob = await exportStandardToWord(props.batchId, std.standard_no);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${std.standard_no}_比对结果_${Date.now()}.docx`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
      ok++;
      await new Promise(r => setTimeout(r, 500));
    } catch {
      fail++;
    }
  }
  window.$message?.[ok > 0 ? 'success' : 'error'](`批量导出完成：成功 ${ok} 个，失败 ${fail} 个`);
  if (ok > 0) checkedRowKeys.value = [];
  exportingWord.value = false;
}
</script>

<template>
  <NDrawer :show="show" :width="1300" @update:show="emit('update:show', $event)">
    <NDrawerContent :native-scrollbar="false" :title="batchName ? `批次结果：${batchName}` : '批次结果'" closable>
      <NSpin :show="loading && !data.length">
        <NSpace :size="16" vertical>
          <!-- KPI 摘要 -->
          <div class="grid grid-cols-4 gap-12px">
            <NCard :bordered="false" class="stat-card" size="small">
              <NStatistic :value="batchRecord?.total_count ?? 0" label="总计" suffix="个"/>
            </NCard>
            <NCard :bordered="false" class="stat-card" size="small">
              <NStatistic :value="batchRecord?.success_count ?? 0" label="成功" suffix="个"/>
            </NCard>
            <NCard :bordered="false" class="stat-card" size="small">
              <NStatistic :value="batchRecord?.failed_count ?? 0" label="失败" suffix="个"/>
            </NCard>
            <NCard :bordered="false" class="stat-card" size="small">
              <NStatistic :value="batchRecord?.duplicate_count ?? 0" label="需要关注" suffix="个"/>
            </NCard>
          </div>

          <NDivider style="margin: 0"/>

          <!-- 筛选区 -->
          <NSpace :size="10" vertical>
            <div class="flex items-center gap-4 flex-wrap">
              <div class="flex items-center gap-2">
                <span class="text-sm text-gray-600">标准编号：</span>
                <NInput v-model:value="filterStandardNo" clearable placeholder="关键词搜索" size="small"
                        style="width: 200px"/>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-sm text-gray-600">标准名称：</span>
                <NInput v-model:value="filterStandardName" clearable placeholder="关键词搜索" size="small"
                        style="width: 200px"/>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-sm text-gray-600">只显示需要关注：</span>
                <NSwitch v-model:value="showOnlyNeedAttention" size="small"/>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-sm text-gray-600">只显示已匹配：</span>
                <NSwitch v-model:value="showOnlyFound" size="small"/>
              </div>
            </div>
            <div class="flex items-center gap-4 flex-wrap">
              <div class="flex items-center gap-2">
                <span class="text-sm text-gray-600">显示标签：</span>
                <NSelect v-model:value="selectedFilterTags" :max-tag-count="2" :options="tagOptions" clearable multiple
                         placeholder="包含以下类型" size="small" style="width: 280px"/>
                <NSelect v-model:value="filterTagsMode"
                         :disabled="!selectedFilterTags.length"
                         :options="[{ label: '包含任一', value: 'any' }, { label: '包含全部', value: 'all' }]" size="small" style="width: 100px"/>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-sm text-gray-600">隐藏标签：</span>
                <NSelect v-model:value="hiddenTags" :max-tag-count="2" :options="tagOptions" clearable multiple
                         placeholder="包含以下类型" size="small" style="width: 280px"/>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-sm text-gray-600">标准原文：</span>
                <NSelect v-model:value="mainHasResFilter" :options="[{ label: '有原文', value: 'has_res' }, { label: '无原文', value: 'no_res' }]"
                         clearable
                         placeholder="全部" size="small" style="width: 120px"/>
              </div>
            </div>
          </NSpace>

          <!-- 操作栏 -->
          <NSpace justify="end">
            <NButton :disabled="!checkedRowKeys.length" :loading="executingBatch" size="small" type="success"
                     @click="handleBatchExecute">
              执行比对 ({{ checkedRowKeys.length }})
            </NButton>
            <NButton :disabled="!data.length" :loading="exporting" size="small" type="primary" @click="handleExport">
              导出 Excel
            </NButton>
            <NButton :disabled="!checkedRowKeys.length" :loading="exportingWord" size="small" type="info"
                     @click="handleBatchExportWord">
              批量导出 Word ({{ checkedRowKeys.length }})
            </NButton>
          </NSpace>

          <!-- 结果表格 -->
          <NDataTable
            v-model:checked-row-keys="checkedRowKeys"
            :columns="columns"
            :data="data"
            :loading="loading"
            :pagination="mobilePagination"
            :row-key="(row: any) => row.standard_no"
            :scroll-x="1100"
            remote
            size="small"
            @update:sorter="handleSorterChange"
          />
        </NSpace>
      </NSpin>
    </NDrawerContent>
  </NDrawer>

  <StandardDetailDrawer v-model:show="showStandardDetail" :batch-id="batchId" :standard="selectedStandard"/>
  <StandardEvaluationDrawer v-model:show="showEvaluation" :standard-no="evaluationStandardNo"/>
</template>

<style scoped>
.stat-card {
  @apply rounded-8px bg-gray-50;
}
</style>
