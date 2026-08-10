<script setup lang="tsx">
import {computed, onMounted, reactive, ref, watch} from 'vue';
import {
  NButton,
  NCard,
  NDataTable,
  NDivider,
  NEmpty,
  NInput,
  NSelect,
  NSpace,
  NSpin,
  NSwitch,
  NTag,
  NTooltip
} from 'naive-ui';
import {
  exportBatchToExcel,
  fetchAIComparisonBatch,
  fetchBatchDetail,
  fetchBatchHistory,
  fetchBatchStats,
  fetchFullTextSimilarityBatch
} from '@/service/api';
import {useNaivePaginatedTable} from '@/hooks/common/table';
import StandardDetailDrawer from './modules/standard-detail-drawer.vue';
import FullTextSimilarityDrawer from './modules/full-text-similarity-drawer.vue';
import AIComparisonDrawer from './modules/ai-comparison-drawer-v2.vue';
import StandardEvaluationDrawer from './modules/standard-evaluation-drawer.vue';
import RingChart from './modules/charts/RingChart.vue';
import TagStatsCards from './modules/charts/TagStatsCards.vue';

const batchOptions = ref<Array<{ label: string; value: number }>>([]);
const selectedBatchId = ref<number | null>(null);
const loadingBatches = ref(false);

const tagLabels: Record<string, string> = {
  同系列标准: '同系列标准',
  标准化对象一致: '标准化对象一致',
  通用专用关系: '通用专用关系',
  适用范围重叠: '适用范围相似'
};

const tagOptions = Object.keys(tagLabels).map(key => ({
  label: tagLabels[key],
  value: key
}));

const showOnlyNeedAttention = ref(false);
const showOnlyFound = ref(true);
const selectedFilterTags = ref<string[]>([]);
const filterTagsMode = ref<string>('any');
const hiddenTags = ref<string[]>([]);
const filterStandardNo = ref('');
const filterStandardName = ref('');
const mainHasResFilter = ref<string>('has_res');
const filterStdDomain = ref<string | null>(null);

const stdDomainOptions = [
  { label: '国家标准对行业标准', value: '国家标准对行业标准' },
  { label: '行业标准对国家标准', value: '行业标准对国家标准' },
];

const showStandardDetail = ref(false);
const selectedStandard = ref<any>(null);

const checkedRowKeys = ref<string[]>([]);

const showFullTextSimilarity = ref(false);
const fullTextSimilarityParams = ref({
  sourceStandardNo: '',
  targetStandardNo: ''
});

const showAIComparison = ref(false);
const aiComparisonParams = ref({
  sourceStandardNo: '',
  targetStandardNo: '',
  forceRecalculate: false
});

const showEvaluation = ref(false);
const evaluationStandardNo = ref('');

const batchRecord = ref<any>(null);

const statsData = ref<Api.AI.BatchStatsResponse['data'] | null>(null);
const loadingStats = ref(false);
// 请求序号：只接受最后一次发出的请求的响应，过期响应（如切换批次前发出的慢请求）直接丢弃
let statsSeq = 0;

async function loadBatchStats() {
  if (!selectedBatchId.value) {
    statsData.value = null;
    return;
  }
  const seq = ++statsSeq;
  const batchId = selectedBatchId.value;
  const stdDomain = filterStdDomain.value ?? undefined;
  loadingStats.value = true;
  try {
    const response = await fetchBatchStats(batchId, stdDomain);
    if (seq !== statsSeq) return;
    if (response.data) {
      statsData.value = response.data ?? null;
    }
  } catch (error) {
    if (seq !== statsSeq) return;
    console.error('加载批次统计数据失败:', error);
    statsData.value = null;
  } finally {
    if (seq === statsSeq) {
      loadingStats.value = false;
    }
  }
}

const sortField = ref<string | null>(null);
const sortOrder = ref<'ascend' | 'descend' | null>(null);
const tableSorter = ref<any>(null);

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
  main_has_res_filter: 'has_res',
  filter_std_domain: null as string | null
});

function updateSearchParams(params: Partial<typeof searchParams>) {
  Object.assign(searchParams, params);
}

const {
  loading,
  data,
  columns: tableColumns,
  getData,
  mobilePagination
} = useNaivePaginatedTable<any, any>({
  api: async () => {
    if (!selectedBatchId.value) {
      return {data: {records: [], current: 1, size: 10, total: 0}} as any;
    }
    const response = await fetchBatchDetail(
      selectedBatchId.value,
      searchParams.current || 1,
      searchParams.size || 10,
      searchParams.only_need_attention || false,
      searchParams.only_found || true,
      searchParams.filter_tags || '',
      searchParams.hidden_tags || '',
      searchParams.filter_tags_mode || 'any',
      searchParams.sort_field || null,
      searchParams.sort_order || null,
      searchParams.filter_standard_no || '',
      searchParams.filter_standard_name || '',
      '',
      searchParams.main_has_res_filter || '',
      searchParams.filter_std_domain || ''
    );
    batchRecord.value = response.data?.record || (response as any).record;
    return response;
  },
  transform: (response: any) => {
    const record = response.data?.record || response.record || {};
    const pagination = response.data?.pagination || response.pagination || {};
    const results = record.results || [];
    const recordsWithIndex = results.map((item: any, index: number) => ({
      ...item,
      index: (pagination.current - 1) * pagination.size + index + 1
    }));
    return {
      data: recordsWithIndex,
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
    {
      type: 'selection',
      fixed: 'left'
    },
    {
      key: 'index',
      title: '序号',
      width: 64,
      align: 'center'
    },
    {
      title: '标准编号',
      key: 'standard_no',
      width: 180,
      fixed: 'left',
      sorter: true
    },
    {
      title: '标准名称',
      key: 'standard_name',
      minWidth: 250,
      ellipsis: {
        tooltip: true
      },
      sorter: true
    },
    /* 需要关注列暂时隐藏
    {
      title: '需要关注',
      key: 'need_attention',
      width: 100,
      align: 'center',
      sorter: true,
      render: row => {
        if (!row.found) {
          return <NTag type="error" size="small">未找到</NTag>;
        }
        return row.need_attention
          ? <NTag type="warning" size="small">需要关注</NTag>
          : <NTag type="success" size="small">无需关注</NTag>;
      }
    },
    */
    /* 相似标准数列暂时隐藏
    {
      title: '相似标准数',
      key: 'similar_count',
      width: 110,
      align: 'center',
      sorter: true,
      render: row => {
        if (!row.found || !row.similar_standards) return '-';
        return row.similar_standards.length;
      }
    },
    */
    {
      title: '疑似交叉类型',
      key: 'tags',
      minWidth: 300,
      render: row => {
        if (!row.found || !row.similar_standards || row.similar_standards.length === 0) {
          return <span class="text-gray-400 text-xs">无相似标准</span>;
        }

        const allTags = new Set<string>();
        row.similar_standards.forEach((similar: any) => {
          const tags = similar.tags || {};
          Object.entries(tags).forEach(([key, value]) => {
            if (value && tagLabels[key]) {
              allTags.add(key);
            }
          });
        });

        if (allTags.size === 0) {
          return <span class="text-gray-400 text-xs">无匹配标签</span>;
        }

        const tagPriority: Record<string, number> = {
          标准化对象一致: 1,
          适用范围重叠: 2,
          通用专用关系: 3,
          同系列标准: 4
        };

        const getTagType = (tagKey: string) => {
          if (tagKey === '标准化对象一致' || tagKey === '适用范围重叠') return 'warning';
          if (tagKey === '通用专用关系') return 'info';
          if (tagKey === '同系列标准') return 'default';
          return 'info';
        };

        const sortedTags = Array.from(allTags).sort((a, b) => {
          return (tagPriority[a] || 999) - (tagPriority[b] || 999);
        });

        return (
          <NSpace size={[4, 4]} wrap={false} style="max-width: 100%;">
            {sortedTags.slice(0, 3).map(tag => (
              <NTag key={tag} type={getTagType(tag)} size="small" style="flex-shrink: 0;">
                {tagLabels[tag]}
              </NTag>
            ))}
            {sortedTags.length > 3 && (
              <NTooltip trigger="hover">
                {{
                  trigger: () => (
                    <NTag size="small" type="info" style="flex-shrink: 0;">
                      +{sortedTags.length - 3}
                    </NTag>
                  ),
                  default: () => (
                    <div>
                      {sortedTags.slice(3).map(tag => (
                        <div key={tag}>{tagLabels[tag]}</div>
                      ))}
                    </div>
                  )
                }}
              </NTooltip>
            )}
          </NSpace>
        );
      }
    },
    {
      title: '缓存状态',
      key: 'cache_status',
      width: 140,
      align: 'center',
      render: row => {
        if (!row.found || !row.similar_standards || row.similar_standards.length === 0) {
          return <span class="text-gray-300 text-xs">—</span>;
        }
        const attentionStandards = row.similar_standards.filter((s: any) => s['need_attention'] === true);
        if (attentionStandards.length === 0) {
          return <span class="text-gray-300 text-xs">—</span>;
        }
        const total = attentionStandards.length;
        const simCount = attentionStandards.filter((s: any) => s.has_sim_cache).length;
        const aiCount = attentionStandards.filter((s: any) => s.has_ai_comparison_cache).length;

        function cacheTagType(count: number, t: number) {
          if (count === t) return 'success' as const;
          if (count > 0) return 'warning' as const;
          return 'error' as const;
        }
        const simType = cacheTagType(simCount, total);
        const aiType = cacheTagType(aiCount, total);

        return (
          <NSpace size={4} vertical align="center">
            <NTooltip trigger="hover">
              {{
                trigger: () => (
                  <NTag type={simType} size="tiny" bordered={false}>
                    文本 {simCount}/{total}
                  </NTag>
                ),
                default: () => `全文相似度缓存：${simCount}/${total} 个相似标准已有缓存`
              }}
            </NTooltip>
            <NTooltip trigger="hover">
              {{
                trigger: () => (
                  <NTag type={aiType} size="tiny" bordered={false}>
                    AI {aiCount}/{total}
                  </NTag>
                ),
                default: () => `AI指标比对缓存：${aiCount}/${total} 个相似标准已有缓存`
              }}
            </NTooltip>
          </NSpace>
        );
      }
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      align: 'center',
      fixed: 'right',
      render: row => {
        if (!row.found) {
          return <span class="text-gray-400 text-xs">{row.error || '未找到'}</span>;
        }
        return (
          <NSpace size={6} justify="center">
            <NButton type="primary" ghost size="small" onClick={() => handleViewStandardDetail(row)}>
              查看详情
            </NButton>
            <NButton type="warning" ghost size="small" onClick={() => handleEvaluation(row)}>
              整合评估
            </NButton>
          </NSpace>
        );
      }
    }
  ]
});

async function loadBatchList() {
  loadingBatches.value = true;
  try {
    const response = await fetchBatchHistory(1, 100);
    const records = response.data?.records || [];
    batchOptions.value = records.map((record: any) => ({
      label: `${record.batch_name || '批次' + record.id} - ${new Date(record.create_time).toLocaleString('zh-CN')} (${record.total_count}个标准)`,
      value: record.id
    }));
    if (batchOptions.value.length > 0 && !selectedBatchId.value) {
      selectedBatchId.value = batchOptions.value[0].value;
    }
  } catch (error) {
    console.error('加载批次列表失败:', error);
  } finally {
    loadingBatches.value = false;
  }
}

function handleViewStandardDetail(row: any) {
  if (!row.found) return;
  selectedStandard.value = row;
  showStandardDetail.value = true;
}

function handleEvaluation(row: any) {
  evaluationStandardNo.value = row.standard_no;
  showEvaluation.value = true;
}

const exporting = ref(false);
async function handleExport() {
  if (!selectedBatchId.value) {
    window.$message?.error('请先选择批次');
    return;
  }
  exporting.value = true;
  try {
    const blob = await exportBatchToExcel(selectedBatchId.value);
    const filename = `批次查重结果_${selectedBatchId.value}_${new Date().getTime()}.xlsx`;
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
    window.$message?.success('导出成功');
  } catch (error: any) {
    window.$message?.error(`导出失败: ${error.message || '未知错误'}`);
  } finally {
    exporting.value = false;
  }
}

const executingBatch = ref(false);
async function handleBatchExecute() {
  if (!checkedRowKeys.value || checkedRowKeys.value.length === 0) {
    window.$message?.error('请先勾选要执行比对的标准');
    return;
  }
  const selectedRows = data.value.filter((item: any) =>
    checkedRowKeys.value.includes(item.standard_no) && item.found && item.similar_standards?.length > 0
  );
  if (selectedRows.length === 0) {
    window.$message?.error('勾选的标准中没有可比对的数据（需有相似标准）');
    return;
  }
  const aiItems: Api.AI.AIComparisonRequest[] = [];
  const simItems: Api.AI.FullTextSimilarityRequest[] = [];
  for (const row of selectedRows) {
    for (const similar of row.similar_standards.filter((s: any) => s['need_attention'] === true)) {
      aiItems.push({ source_standard_no: row.standard_no, target_standard_no: similar.standard_no, force_recalculate: false });
      simItems.push({ source_standard_no: row.standard_no, target_standard_no: similar.standard_no, force_recalculate: false });
    }
  }
  if (aiItems.length === 0) {
    window.$message?.error('勾选的标准中没有标记为"需要关注"的相似标准');
    return;
  }
  const total = aiItems.length;
  window.$message?.info(`开始执行比对，共 ${selectedRows.length} 个主标准，${total} 对任务...`);
  executingBatch.value = true;
  try {
    const [aiRes, simRes] = await Promise.all([
      fetchAIComparisonBatch(aiItems),
      fetchFullTextSimilarityBatch(simItems)
    ]);
    const aiSuccess = (aiRes.data || []).filter((r: any) => r?.data?.success).length;
    const simSuccess = (simRes.data || []).filter((r: any) => r?.data?.success).length;
    window.$message?.success(`比对完成：AI比对 ${aiSuccess}/${total} 成功，全文相似度 ${simSuccess}/${total} 成功`);
    getData();
  } catch (err: any) {
    window.$message?.error(`执行失败: ${err.message || '未知错误'}`);
  } finally {
    executingBatch.value = false;
  }
}

const exportingWord = ref(false);
async function handleBatchExportWord() {
  if (!selectedBatchId.value) {
    window.$message?.error('请先选择批次');
    return;
  }
  if (!checkedRowKeys.value || checkedRowKeys.value.length === 0) {
    window.$message?.error('请先勾选要导出的标准');
    return;
  }
  exportingWord.value = true;
  try {
    const { exportStandardToWord } = await import('@/service/api');
    const selectedStandards = data.value.filter((item: any) =>
      checkedRowKeys.value.includes(item.standard_no) && item.found
    );
    if (selectedStandards.length === 0) {
      window.$message?.error('勾选的标准中没有可导出的有效数据');
      return;
    }
    window.$message?.info(`开始批量导出 ${selectedStandards.length} 个标准的Word文档...`);
    let successCount = 0;
    let failCount = 0;
    for (const standard of selectedStandards) {
      try {
        const blob = await exportStandardToWord(selectedBatchId.value, standard.standard_no);
        const filename = `${standard.standard_no}_比对结果_${new Date().getTime()}.docx`;
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);
        successCount++;
        await new Promise(resolve => setTimeout(resolve, 500));
      } catch (error: any) {
        console.error(`导出标准 ${standard.standard_no} 失败:`, error);
        failCount++;
      }
    }
    if (successCount > 0) {
      window.$message?.success(`批量导出完成：成功 ${successCount} 个，失败 ${failCount} 个`);
      checkedRowKeys.value = [];
    } else {
      window.$message?.error('批量导出失败');
    }
  } catch (error: any) {
    window.$message?.error(`批量导出失败: ${error.message || '未知错误'}`);
  } finally {
    exportingWord.value = false;
  }
}

function handleSorterChange(sorter: any) {
  tableSorter.value = sorter;
  if (!sorter || typeof sorter.order === 'undefined' || sorter.order === false) {
    sortField.value = null;
    sortOrder.value = null;
  } else {
    sortField.value = sorter.columnKey;
    sortOrder.value = sorter.order;
  }
  updateSearchParams({
    sort_field: sortField.value,
    sort_order: sortOrder.value,
    current: 1
  });
  getData();
}

watch(showStandardDetail, val => {
  if (!val) getData();
});

watch(
  () => selectedBatchId.value,
  () => {
    if (selectedBatchId.value) {
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
      tableSorter.value = null;
      checkedRowKeys.value = [];
      getData();
      loadBatchStats();
    }
  }
);

watch(
  [showOnlyNeedAttention, showOnlyFound, selectedFilterTags, filterTagsMode, hiddenTags, filterStandardNo, filterStandardName, mainHasResFilter, filterStdDomain],
  () => {
    updateSearchParams({
      only_need_attention: showOnlyNeedAttention.value,
      only_found: showOnlyFound.value,
      filter_tags: selectedFilterTags.value.join(','),
      filter_tags_mode: filterTagsMode.value,
      hidden_tags: hiddenTags.value.join(','),
      filter_standard_no: filterStandardNo.value.trim(),
      filter_standard_name: filterStandardName.value.trim(),
      main_has_res_filter: mainHasResFilter.value,
      filter_std_domain: filterStdDomain.value,
      current: 1
    });
    loadBatchStats();
    getData();
  }
);

const columns = computed(() => tableColumns.value);

onMounted(() => {
  loadBatchList();
  const urlParams = new URLSearchParams(window.location.search);
  const action = urlParams.get('action');
  if (action === 'fullTextSimilarity') {
    fullTextSimilarityParams.value = {
      sourceStandardNo: urlParams.get('sourceStandardNo') || '',
      targetStandardNo: urlParams.get('targetStandardNo') || ''
    };
    showFullTextSimilarity.value = true;
  } else if (action === 'aiComparison') {
    aiComparisonParams.value = {
      sourceStandardNo: urlParams.get('sourceStandardNo') || '',
      targetStandardNo: urlParams.get('targetStandardNo') || '',
      forceRecalculate: urlParams.get('forceRecalculate') === 'true'
    };
    showAIComparison.value = true;
  }
});
</script>

<template>
  <div class="flex-col-stretch gap-16px">
    <!-- 筛选条件 -->
    <NCard v-if="selectedBatchId" :bordered="false" size="small" class="filter-card">
      <NSpace vertical :size="12">

        <!-- 主布局：左侧圆环图 + 右侧筛选器 -->
        <div class="main-layout">

          <!-- 左侧：圆环图 -->
          <div class="sidebar-chart">
            <div v-if="loadingStats" class="sidebar-loading">
              <NSpin size="small" />
            </div>
            <RingChart
              v-else-if="statsData"
              :need-attention-count="statsData.high_risk_stats.need_attention_count"
              :total-count="statsData.high_risk_stats.total_count"
              :high-risk-tag-count="statsData.high_risk_stats.high_risk_tag_count"
            />
            <div v-else class="sidebar-empty">
              <NEmpty size="small" description="暂无数据" />
            </div>
          </div>

          <!-- 右侧：筛选器 -->
          <div class="filter-content">
            <!-- 筛选器 -->
            <div class="filter-section">
          <div class="filter-header mb-3">
            <div class="filter-title">
              <svg class="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
              </svg>
              <span>筛选条件</span>
              <NTag v-if="showOnlyNeedAttention || !showOnlyFound || selectedFilterTags.length > 0 || hiddenTags.length > 0 || filterStandardNo || filterStandardName || mainHasResFilter" type="info" size="small" :bordered="false" class="ml-2">
                {{ (showOnlyNeedAttention ? 1 : 0) + (!showOnlyFound ? 1 : 0) + selectedFilterTags.length + hiddenTags.length + (filterStandardNo ? 1 : 0) + (filterStandardName ? 1 : 0) + (mainHasResFilter ? 1 : 0) }} 个条件生效
              </NTag>
            </div>
            <div class="flex items-center gap-2 flex-wrap">
              <!-- 批次选择 -->
              <div class="flex items-center gap-2">
                <span class="text-xs text-gray-500 whitespace-nowrap">批次：</span>
                <NSelect
                  v-model:value="selectedBatchId"
                  :options="batchOptions"
                  :loading="loadingBatches"
                  placeholder="选择批次"
                  filterable
                  size="tiny"
                  style="min-width: 350px; max-width: 500px"
                  @update:value="() => {}"
                />
                <NButton
                  text
                  type="primary"
                  size="tiny"
                  :loading="loadingBatches"
                  @click="loadBatchList"
                >
                  <template #icon>
                    <icon-mdi:refresh class="text-icon" />
                  </template>
                </NButton>
              </div>
              <NButton
                v-if="selectedFilterTags.length > 0 || hiddenTags.length > 0 || filterStandardNo || filterStandardName || mainHasResFilter"
                text
                type="primary"
                size="tiny"
                @click="selectedFilterTags = []; filterTagsMode = 'any'; hiddenTags = []; filterStandardNo = ''; filterStandardName = ''; mainHasResFilter = 'has_res'"
              >
                清除全部筛选
              </NButton>
              <NButton
                v-if="showOnlyNeedAttention || !showOnlyFound || selectedFilterTags.length > 0 || hiddenTags.length > 0 || filterStandardNo || filterStandardName || mainHasResFilter"
                text
                type="primary"
                size="tiny"
                @click="showOnlyNeedAttention = false; showOnlyFound = true; selectedFilterTags = []; filterTagsMode = 'any'; hiddenTags = []; filterStandardNo = ''; filterStandardName = ''; mainHasResFilter = 'has_res'"
              >
                重置全部
              </NButton>
            </div>
          </div>

          <!-- 关键词搜索 -->
          <div class="flex items-center gap-4 flex-wrap mb-3">
            <div class="flex items-center gap-2" style="min-width: 280px;">
              <svg class="w-4 h-4 text-purple-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <span class="text-sm text-gray-600 whitespace-nowrap">标准编号：</span>
              <NInput
                v-model:value="filterStandardNo"
                placeholder="输入关键词搜索"
                clearable
                size="small"
                style="flex: 1; max-width: 250px"
              />
            </div>
            <div class="flex items-center gap-2" style="min-width: 280px;">
              <svg class="w-4 h-4 text-purple-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <span class="text-sm text-gray-600 whitespace-nowrap">标准名称：</span>
              <NInput
                v-model:value="filterStandardName"
                placeholder="输入关键词搜索"
                clearable
                size="small"
                style="flex: 1; max-width: 250px"
              />
            </div>
          </div>

          <div class="flex items-center gap-6 flex-wrap">
            <!-- 快捷筛选 -->
            <div class="flex items-center gap-4">
              <div class="flex items-center gap-2">
                <NTooltip trigger="hover" placement="top" style="max-width: 400px;">
                  <template #trigger>
                    <span class="text-sm text-gray-600 whitespace-nowrap cursor-help">只显示需要关注：</span>
                  </template>
                  <div style="line-height: 1.6;">
                    同时满足以下条件的标准会被标记为"需要关注"：<br>
                    标准化对象一致 + 适用范围相似 + 非同系列标准 + 非通用专用关系
                  </div>
                </NTooltip>
                <NSwitch v-model:value="showOnlyNeedAttention" size="small" />
              </div>
              <div class="flex items-center gap-2">
                <NTooltip trigger="hover" placement="top">
                  <template #trigger>
                    <span class="text-sm text-gray-600 whitespace-nowrap cursor-help">只显示已匹配：</span>
                  </template>
                  只显示标准编号与数据库匹配成功的标准，隐藏未匹配到的标准
                </NTooltip>
                <NSwitch v-model:value="showOnlyFound" size="small" />
              </div>
            </div>
            <!-- 标签筛选 - 显示和隐藏 -->
            <div class="flex items-center gap-6 flex-wrap" style="width: 100%;">
              <!-- 显示筛选 -->
              <div class="flex items-center gap-3 flex-1" style="min-width: 280px;">
                <div class="flex items-center gap-2">
                  <svg class="w-4 h-4 text-green-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span class="text-sm text-gray-600 whitespace-nowrap">显示：</span>
                </div>
                <NSelect
                  v-model:value="selectedFilterTags"
                  multiple
                  placeholder="包含以下类型"
                  :options="tagOptions"
                  clearable
                  :max-tag-count="2"
                  size="small"
                  style="flex: 1; max-width: 350px"
                />
                <NSelect
                  v-model:value="filterTagsMode"
                  :options="[
                    { label: '包含任一', value: 'any' },
                    { label: '包含全部', value: 'all' }
                  ]"
                  size="small"
                  style="width: 100px"
                  :disabled="selectedFilterTags.length === 0"
                />
              </div>
              <!-- 隐藏筛选 -->
              <div class="flex items-center gap-3 flex-1" style="min-width: 280px;">
                <div class="flex items-center gap-2">
                  <svg class="w-4 h-4 text-red-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                  </svg>
                  <span class="text-sm text-gray-600 whitespace-nowrap">隐藏：</span>
                </div>
                <NSelect
                  v-model:value="hiddenTags"
                  multiple
                  placeholder="包含以下类型"
                  :options="tagOptions"
                  clearable
                  :max-tag-count="2"
                  size="small"
                  style="flex: 1; max-width: 350px"
                />
              </div>
              <!-- 标准原文筛选 -->
              <div class="flex items-center gap-3 flex-1" style="min-width: 280px;">
                <div class="flex items-center gap-2">
                  <NTooltip trigger="hover" placement="top" style="max-width: 400px;">
                    <template #trigger>
                      <svg class="w-4 h-4 text-blue-500 flex-shrink-0 cursor-help" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                    </template>
                    <div style="line-height: 1.6;">
                      "有原文"：主标准有原文，且相似标准中至少有一个有原文<br>
                      "无原文"：主标准没有原文，或所有相似标准都没有原文
                    </div>
                  </NTooltip>
                  <span class="text-sm text-gray-600 whitespace-nowrap">标准原文：</span>
                </div>
                <NSelect
                  v-model:value="mainHasResFilter"
                  placeholder="全部"
                  clearable
                  :options="[
                    { label: '有原文', value: 'has_res' },
                    { label: '无原文', value: 'no_res' }
                  ]"
                  size="small"
                  style="flex: 1; max-width: 180px"
                />
              </div>
              <!-- 标准层级/跨域筛选 -->
              <div class="flex items-center gap-3 flex-1" style="min-width: 280px;">
                <div class="flex items-center gap-2">
                  <NTooltip trigger="hover" placement="top" style="max-width: 400px;">
                    <template #trigger>
                      <svg class="w-4 h-4 text-orange-500 flex-shrink-0 cursor-help" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
                      </svg>
                    </template>
                    <div style="line-height: 1.6;">
                      "国家标准对行业标准"：列表只显示国家标准，详情只看与其重叠的行业标准（且需要关注）<br>
                      "行业标准对国家标准"：反之亦然
                    </div>
                  </NTooltip>
                  <span class="text-sm text-gray-600 whitespace-nowrap">标准层级：</span>
                </div>
                <NSelect
                  v-model:value="filterStdDomain"
                  placeholder="全部"
                  clearable
                  :options="stdDomainOptions"
                  size="small"
                  style="flex: 1; max-width: 220px"
                />
              </div>
            </div>
          </div>
            </div>
          </div>
        </div>

        <!-- 基本统计 -->
        <div class="filter-stats">
          <div class="stat-item">
            <span class="stat-label">总计：</span>
            <span class="stat-value">{{ batchRecord?.total_count || 0 }}</span>
            <span class="stat-unit">个标准</span>
          </div>
          <span class="stat-divider">|</span>
          <div class="stat-item">
            <span class="stat-label">当前显示：</span>
            <span class="stat-value" :class="(mobilePagination?.itemCount || 0) < (batchRecord?.total_count || 0) ? 'text-blue-500' : 'text-green-500'">
              {{ mobilePagination?.itemCount || 0 }}
            </span>
            <span class="stat-unit">个</span>
          </div>
          <template v-if="(mobilePagination?.itemCount || 0) < (batchRecord?.total_count || 0)">
            <span class="stat-divider">|</span>
            <div class="stat-item">
              <span class="stat-label">已隐藏：</span>
              <span class="stat-value text-red-500">{{ (batchRecord?.total_count || 0) - (mobilePagination?.itemCount || 0) }}</span>
              <span class="stat-unit">个</span>
            </div>
          </template>
        </div>
      </NSpace>
    </NCard>

    <!-- 分析结果 -->
    <NCard
      v-if="selectedBatchId"
      title="相似度分析结果"
      :bordered="false"
      size="small"
      class="card-wrapper"
    >
      <template #header-extra>
        <NSpace>
          <NButton
            type="success"
            size="small"
            :loading="executingBatch"
            :disabled="!batchRecord || checkedRowKeys.length === 0"
            @click="handleBatchExecute"
          >
            <template #icon>
              <icon-mdi:play-circle class="text-icon" />
            </template>
            执行比对 ({{ checkedRowKeys.length }})
          </NButton>
          <NButton
            type="primary"
            size="small"
            :loading="exporting"
            :disabled="!batchRecord || data.length === 0"
            @click="handleExport"
          >
            <template #icon>
              <icon-mdi:microsoft-excel class="text-icon" />
            </template>
            导出 Excel
          </NButton>
          <NButton
            type="info"
            size="small"
            :loading="exportingWord"
            :disabled="!batchRecord || checkedRowKeys.length === 0"
            @click="handleBatchExportWord"
          >
            <template #icon>
              <icon-mdi:file-word class="text-icon" />
            </template>
            批量导出 Word ({{ checkedRowKeys.length }})
          </NButton>
        </NSpace>
      </template>
      <NDataTable
        :columns="columns"
        :data="data"
        :scroll-x="1000"
        size="small"
        remote
        :loading="loading"
        :row-key="(row: any) => row.standard_no"
        :pagination="mobilePagination"
        v-model:checked-row-keys="checkedRowKeys"
        @update:sorter="handleSorterChange"
      />
    </NCard>

    <!-- 未选择批次提示 -->
    <NCard v-else :bordered="false" size="small" class="card-wrapper">
      <NEmpty description="请在上方选择要查看的批次" class="py-20" />
    </NCard>


    <!-- 标准详情抽屉 -->
    <StandardDetailDrawer
      v-model:show="showStandardDetail"
      :standard="selectedStandard"
      :batch-id="selectedBatchId"
      :filter-std-domain="filterStdDomain"
    />

    <!-- 全文相似度抽屉（用于新窗口打开）-->
    <FullTextSimilarityDrawer
      v-model:show="showFullTextSimilarity"
      :source-standard-no="fullTextSimilarityParams.sourceStandardNo"
      :target-standard-no="fullTextSimilarityParams.targetStandardNo"
    />

    <!-- AI比对抽屉（用于新窗口打开） -->
    <AIComparisonDrawer
      v-model:show="showAIComparison"
      :source-standard-no="aiComparisonParams.sourceStandardNo"
      :target-standard-no="aiComparisonParams.targetStandardNo"
      :force-recalculate="aiComparisonParams.forceRecalculate"
    />

    <!-- 整合评估抽屉 -->
    <StandardEvaluationDrawer
      v-model:show="showEvaluation"
      :standard-no="evaluationStandardNo"
    />
  </div>
</template>

<style scoped>
.card-wrapper {
  @apply rounded-8px shadow-sm;
}

/* 主布局：左侧图表 + 右侧筛选 */
.main-layout {
  display: flex;
  gap: 16px;
  align-items: stretch;
}

.sidebar-chart {
  width: 340px;
  flex-shrink: 0;
  border: 1px solid #e0e4ea;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.sidebar-loading,
.sidebar-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}

.filter-content {
  flex: 1;
  min-width: 0;
  border: 1px solid #e0e4ea;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
  padding: 14px 18px;
}

/* 筛选卡片 */
.filter-card {
  background: #f5f6f8 !important;
}

.filter-card :deep(.n-card-header) {
  padding: 12px 16px;
  border-bottom: 1px solid #ebebee;
  background: #f5f6f8;
}

.filter-card :deep(.n-card__content) {
  padding: 12px 16px;
  background: #f5f6f8;
}

/* 筛选区域 */
.filter-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.filter-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.filter-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 500;
  font-size: 14px;
  color: #333;
}

/* 统计信息 */
.filter-stats {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: #fff;
  border: 1px solid #e0e4ea;
  border-radius: 10px;
  font-size: 13px;
}

.stat-item {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.stat-label {
  color: #666;
  font-size: 12px;
}

.stat-value {
  font-weight: 600;
  font-size: 16px;
  color: #333;
}

.stat-unit {
  color: #999;
  font-size: 12px;
}

.stat-divider {
  color: #d9d9d9;
  font-size: 12px;
}

/* SVG图标样式 */
svg {
  flex-shrink: 0;
}
</style>
