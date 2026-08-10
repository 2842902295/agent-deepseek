<script setup lang="ts">
import {ref, watch, h, computed, reactive} from 'vue';
import { NDrawer, NDrawerContent, NSpace, NCard, NStatistic, NSpin, NEmpty, NDataTable, NTag, NSelect, NButton, NInput, NTabs, NTabPane, NAlert } from 'naive-ui';
import type { DataTableColumns } from 'naive-ui';
import { fetchAIComparison } from '@/service/api';
import { useMessage } from 'naive-ui';

const message = useMessage();

// Props & Emits
const props = defineProps<{
  show: boolean;
  sourceStandardNo: string;  // 源标准编号
  targetStandardNo: string;  // 目标标准编号
  forceRecalculate?: boolean; // 是否强制重新计算
}>();

const emit = defineEmits<{
  'update:show': [value: boolean];
}>();

const loading = ref(false);
const comparisonData = ref<Api.AI.AIComparisonResponse | null>(null);
const fromCache = ref(false);
const error = ref<string | null>(null); // 错误信息

// 筛选条件
const selectedCategories = ref<string[]>([]); // 指标大类筛选
const searchKeyword = ref(''); // 指标名称搜索
const selectedChangeType = ref<string>(''); // 变化类型筛选（单选）

// 章节视图筛选
const chapterViewFilter = ref<string>('all'); // all | matched | unique
const activeChapterId = ref<string>(''); // 当前激活的章节ID

// 监听打开事件，自动加载数据
async function loadComparisonData() {
  if (!props.sourceStandardNo || !props.targetStandardNo) {
    console.warn('缺少必要参数', {
      sourceStandardNo: props.sourceStandardNo,
      targetStandardNo: props.targetStandardNo
    });
    return;
  }

  loading.value = true;
  fromCache.value = false;
  error.value = null; // 清除之前的错误

  try {
    console.log('开始AI比对计算', {
      sourceStandardNo: props.sourceStandardNo,
      targetStandardNo: props.targetStandardNo,
      forceRecalculate: props.forceRecalculate
    });

    if (props.forceRecalculate) {
      message.info('正在重新计算，请稍候...');
    } else {
      message.info('正在加载比对结果，请稍候...');
    }

    const response = await fetchAIComparison({
      source_standard_no: props.sourceStandardNo,
      target_standard_no: props.targetStandardNo,
      force_recalculate: props.forceRecalculate || false
    });

    console.log('AI比对响应', response);

    // 检查响应数据是否为空（接口虽然成功，但data为null表示有错误）
    if (!response.data) {
      error.value = (response as any).msg || '数据加载失败';
      comparisonData.value = null;
      loading.value = false;
      return;
    }

    comparisonData.value = response.data;
    fromCache.value = response.data?.from_cache || false;

    // 对指标按大类排序（相同大类的指标挨在一起）
    if (comparisonData.value) {
      if (comparisonData.value.matched_indicators) {
        comparisonData.value.matched_indicators.sort((a, b) =>
          a.category.localeCompare(b.category, 'zh-CN')
        );
      }
      if (comparisonData.value.source_unique_indicators) {
        comparisonData.value.source_unique_indicators.sort((a, b) =>
          a.category.localeCompare(b.category, 'zh-CN')
        );
      }
      if (comparisonData.value.target_unique_indicators) {
        comparisonData.value.target_unique_indicators.sort((a, b) =>
          a.category.localeCompare(b.category, 'zh-CN')
        );
      }
    }

    if (props.forceRecalculate) {
      message.success('重新比对完成');
    } else if (response.data?.from_cache) {
      message.success('已从缓存加载比对结果');
    } else {
      message.success('比对完成');
    }
  } catch (err: any) {
    console.error('加载AI比对失败', err);
    // 保存错误信息，不关闭抽屉
    error.value = err.message || '未知错误';
    comparisonData.value = null;
  } finally {
    loading.value = false;
  }
}

// 监听 show 属性变化
watch(() => props.show, (newVal) => {
  if (newVal) {
    loadComparisonData();
  } else {
    comparisonData.value = null;
    selectedCategories.value = [];
    searchKeyword.value = '';
    selectedChangeType.value = '';
    error.value = null; // 清除错误
  }
});

// 监听打开状态（用户手动关闭）
function handleUpdateShow(value: boolean) {
  emit('update:show', value);
}

// 获取所有唯一的指标大类（作为 select 选项）
const categoryOptions = computed(() => {
  if (!comparisonData.value) return [];

  const categoriesSet = new Set<string>();

  // 从对应指标中提取
  comparisonData.value.matched_indicators?.forEach(ind => {
    if (ind.category) categoriesSet.add(ind.category);
  });

  // 从源独有指标中提取
  comparisonData.value.source_unique_indicators?.forEach(ind => {
    if (ind.category) categoriesSet.add(ind.category);
  });

  // 从目标独有指标中提取
  comparisonData.value.target_unique_indicators?.forEach(ind => {
    if (ind.category) categoriesSet.add(ind.category);
  });

  // 转为数组并排序，然后转为 select 选项格式
  return Array.from(categoriesSet)
    .sort((a, b) => a.localeCompare(b, 'zh-CN'))
    .map(category => ({
      label: category,
      value: category
    }));
});

// 变化类型选项
const changeTypeOptions = [
  { label: '一致', value: '一致' },
  { label: '有变化', value: '有变化' }
];

// placeholder文本
const categoryPlaceholder = computed(() => {
  return `不选则显示全部 ${categoryOptions.value.length} 个大类`;
});

// 计算数据的rowSpan（用于合并单元格）
function calculateRowSpan<T extends { category: string }>(data: T[]) {
  const result: Array<T & { categoryRowSpan: number }> = [];
  const categoryCount = new Map<string, number>();

  // 统计每个大类的数量
  data.forEach(item => {
    categoryCount.set(item.category, (categoryCount.get(item.category) || 0) + 1);
  });

  const categoryFirstIndex = new Map<string, number>();

  data.forEach((item, index) => {
    const category = item.category;

    if (!categoryFirstIndex.has(category)) {
      // 第一次出现，设置rowSpan为该大类的总数
      categoryFirstIndex.set(category, index);
      result.push({
        ...item,
        categoryRowSpan: categoryCount.get(category) || 1
      });
    } else {
      // 后续出现，rowSpan设为1（会被自动隐藏）
      result.push({
        ...item,
        categoryRowSpan: 1
      });
    }
  });

  return result;
}

// 过滤后的对应指标（带rowSpan）
const filteredMatchedIndicators = computed(() => {
  if (!comparisonData.value?.matched_indicators) return [];

  let result = comparisonData.value.matched_indicators;

  // 1. 按指标大类筛选
  if (selectedCategories.value.length > 0) {
    result = result.filter(ind => selectedCategories.value.includes(ind.category));
  }

  // 2. 按关键词搜索
  if (searchKeyword.value.trim()) {
    const keyword = searchKeyword.value.trim().toLowerCase();
    result = result.filter(ind =>
      ind.name.toLowerCase().includes(keyword) ||
      ind.category.toLowerCase().includes(keyword) ||
      (ind.source_requirement || '').toLowerCase().includes(keyword) ||
      (ind.target_requirement || '').toLowerCase().includes(keyword)
    );
  }

  // 3. 按变化类型筛选（单选）
  if (selectedChangeType.value) {
    result = result.filter(ind => {
      const analysis = ind.change_analysis || '';
      const isConsistent = analysis === '一致' || analysis.includes('一致');

      if (selectedChangeType.value === '一致') return isConsistent;
      if (selectedChangeType.value === '有变化') return !isConsistent;
      return true;
    });
  }

  // 计算rowSpan
  return calculateRowSpan(result);
});

// 过滤后的源独有指标（带rowSpan）
const filteredSourceUniqueIndicators = computed(() => {
  if (!comparisonData.value?.source_unique_indicators) return [];

  let result = comparisonData.value.source_unique_indicators;

  // 1. 按指标大类筛选
  if (selectedCategories.value.length > 0) {
    result = result.filter(ind => selectedCategories.value.includes(ind.category));
  }

  // 2. 按关键词搜索
  if (searchKeyword.value.trim()) {
    const keyword = searchKeyword.value.trim().toLowerCase();
    result = result.filter(ind =>
      ind.name.toLowerCase().includes(keyword) ||
      ind.category.toLowerCase().includes(keyword) ||
      (ind.requirement || '').toLowerCase().includes(keyword)
    );
  }

  // 计算rowSpan
  return calculateRowSpan(result);
});

// 过滤后的目标独有指标（带rowSpan）
const filteredTargetUniqueIndicators = computed(() => {
  if (!comparisonData.value?.target_unique_indicators) return [];

  let result = comparisonData.value.target_unique_indicators;

  // 1. 按指标大类筛选
  if (selectedCategories.value.length > 0) {
    result = result.filter(ind => selectedCategories.value.includes(ind.category));
  }

  // 2. 按关键词搜索
  if (searchKeyword.value.trim()) {
    const keyword = searchKeyword.value.trim().toLowerCase();
    result = result.filter(ind =>
      ind.name.toLowerCase().includes(keyword) ||
      ind.category.toLowerCase().includes(keyword) ||
      (ind.requirement || '').toLowerCase().includes(keyword)
    );
  }

  // 计算rowSpan
  return calculateRowSpan(result);
});

// 清空所有筛选
function clearAllFilters() {
  selectedCategories.value = [];
  searchKeyword.value = '';
  selectedChangeType.value = '';
}

// 对应指标分页配置
const MatchedPagination = reactive({
  page: 1,
  pageSize: 10,
  showSizePicker: true,
  pageSizes: [10, 20, 50, 100],
  onChange: (page: number) => {
    MatchedPagination.page = page;
  },
  onUpdatePageSize: (pageSize: number) => {
    MatchedPagination.pageSize = pageSize;
    MatchedPagination.page = 1;
  }
});

// 源标准独有指标分页配置
const SourceUniquePagination = reactive({
  page: 1,
  pageSize: 10,
  showSizePicker: true,
  pageSizes: [10, 20, 50, 100],
  onChange: (page: number) => {
    SourceUniquePagination.page = page;
  },
  onUpdatePageSize: (pageSize: number) => {
    SourceUniquePagination.pageSize = pageSize;
    SourceUniquePagination.page = 1;
  }
});

// 目标标准独有指标分页配置
const TargetUniquePagination = reactive({
  page: 1,
  pageSize: 10,
  showSizePicker: true,
  pageSizes: [10, 20, 50, 100],
  onChange: (page: number) => {
    TargetUniquePagination.page = page;
  },
  onUpdatePageSize: (pageSize: number) => {
    TargetUniquePagination.pageSize = pageSize;
    TargetUniquePagination.page = 1;
  }
});

// 章节对比视图 - 数据结构定义
interface ChapterIndicator {
  type: 'matched' | 'source_unique';
  name: string;
  category: string;
  sourceRequirement: string;
  sourceChapter: string;
  targetRequirement?: string;
  targetChapter?: string;
  changeAnalysis?: string;
  isConsistent?: boolean;
}

interface ChapterGroup {
  chapterName: string;
  chapterNo: string;
  indicators: ChapterIndicator[];
  matchedCount: number;
  changedCount: number;
  uniqueCount: number;
}

// 按章节分组的指标数据
const chapterGroups = computed(() => {
  if (!comparisonData.value) return [];

  const groups = new Map<string, ChapterGroup>();

  // 1. 处理对应的指标
  filteredMatchedIndicators.value.forEach(ind => {
    const chapterKey = ind.source_chapter || '未分类章节';

    if (!groups.has(chapterKey)) {
      groups.set(chapterKey, {
        chapterName: chapterKey,
        chapterNo: chapterKey,
        indicators: [],
        matchedCount: 0,
        changedCount: 0,
        uniqueCount: 0
      });
    }

    const group = groups.get(chapterKey)!;
    const isConsistent = ind.change_analysis === '一致' || ind.change_analysis?.includes('一致');

    group.indicators.push({
      type: 'matched',
      name: ind.name,
      category: ind.category,
      sourceRequirement: ind.source_requirement,
      sourceChapter: ind.source_chapter || '',
      targetRequirement: ind.target_requirement,
      targetChapter: ind.target_chapter || '',
      changeAnalysis: ind.change_analysis,
      isConsistent
    });

    group.matchedCount++;
    if (!isConsistent) {
      group.changedCount++;
    }
  });

  // 2. 处理源独有指标
  filteredSourceUniqueIndicators.value.forEach(ind => {
    const chapterKey = ind.chapter_info || '未分类章节';

    if (!groups.has(chapterKey)) {
      groups.set(chapterKey, {
        chapterName: chapterKey,
        chapterNo: chapterKey,
        indicators: [],
        matchedCount: 0,
        changedCount: 0,
        uniqueCount: 0
      });
    }

    const group = groups.get(chapterKey)!;
    group.indicators.push({
      type: 'source_unique',
      name: ind.name,
      category: ind.category,
      sourceRequirement: ind.requirement,
      sourceChapter: ind.chapter_info || ''
    });

    group.uniqueCount++;
  });

  // 3. 转换为数组并排序
  return Array.from(groups.values()).sort((a, b) => {
    // 按章节号排序
    return a.chapterNo.localeCompare(b.chapterNo, 'zh-CN', { numeric: true });
  });
});

// 目标标准独有指标（按章节分组）
const targetUniqueGroups = computed(() => {
  if (!comparisonData.value) return [];

  const groups = new Map<string, ChapterGroup>();

  filteredTargetUniqueIndicators.value.forEach(ind => {
    const chapterKey = ind.chapter_info || '未分类章节';

    if (!groups.has(chapterKey)) {
      groups.set(chapterKey, {
        chapterName: chapterKey,
        chapterNo: chapterKey,
        indicators: [],
        matchedCount: 0,
        changedCount: 0,
        uniqueCount: 0
      });
    }

    const group = groups.get(chapterKey)!;
    group.indicators.push({
      type: 'source_unique',
      name: ind.name,
      category: ind.category,
      sourceRequirement: ind.requirement,
      sourceChapter: ind.chapter_info || ''
    });

    group.uniqueCount++;
  });

  return Array.from(groups.values()).sort((a, b) => {
    return a.chapterNo.localeCompare(b.chapterNo, 'zh-CN', { numeric: true });
  });
});

// 根据章节视图筛选过滤的章节组
const filteredChapterGroups = computed(() => {
  if (chapterViewFilter.value === 'all') {
    return chapterGroups.value;
  }

  if (chapterViewFilter.value === 'matched') {
    // 只显示有对应指标的章节
    return chapterGroups.value
      .map(group => ({
        ...group,
        indicators: group.indicators.filter(ind => ind.type === 'matched')
      }))
      .filter(group => group.indicators.length > 0);
  }

  if (chapterViewFilter.value === 'unique') {
    // 只显示有独有指标的章节
    return chapterGroups.value
      .map(group => ({
        ...group,
        indicators: group.indicators.filter(ind => ind.type === 'source_unique')
      }))
      .filter(group => group.indicators.length > 0);
  }

  return chapterGroups.value;
});

// 滚动到指定章节
function scrollToChapter(chapterId: string) {
  activeChapterId.value = chapterId;
  const element = document.getElementById(chapterId);
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// 对应指标表格列定义（使用计算属性，从API返回的数据中获取标准编号）
const matchedColumns = computed<DataTableColumns<any>>(() => [
  {
    title: '指标大类',
    key: 'category',
    width: 120,
    ellipsis: { tooltip: true },
    rowSpan: (rowData) => rowData.categoryRowSpan || 1
  },
  {
    title: '指标名称',
    key: 'name',
    width: 150,
    ellipsis: { tooltip: true }
  },
  {
    title: `${comparisonData.value?.source_standard_no || props.sourceStandardNo || '源标准'} 要求`,
    key: 'source_requirement',
    ellipsis: { tooltip: true },
    render: (row) => row.source_requirement || '-'
  },
  {
    title: `${comparisonData.value?.target_standard_no || props.targetStandardNo || '目标标准'} 要求`,
    key: 'target_requirement',
    ellipsis: { tooltip: true },
    render: (row) => row.target_requirement || '-'
  },
  {
    title: '差异分析',
    key: 'change_analysis',
    width: 200,
    ellipsis: { tooltip: true },
    render: (row) => {
      const analysis = row.change_analysis || '';
      if (analysis === '一致' || analysis.includes('一致')) {
        return h(NTag, { type: 'success', size: 'small' }, { default: () => analysis });
      }
      return h(NTag, { type: 'warning', size: 'small' }, { default: () => analysis });
    }
  }
]);

// 独有指标表格列定义
const uniqueColumns: DataTableColumns<any> = [
  {
    title: '指标大类',
    key: 'category',
    width: 120,
    ellipsis: { tooltip: true },
    rowSpan: (rowData) => rowData.categoryRowSpan || 1
  },
  {
    title: '指标名称',
    key: 'name',
    width: 150,
    ellipsis: { tooltip: true }
  },
  {
    title: '指标要求',
    key: 'requirement',
    ellipsis: { tooltip: true }
  },
  {
    title: '所在章节',
    key: 'chapter_info',
    width: 180,
    ellipsis: { tooltip: true },
    render: (row) => row.chapter_info || '-'
  }
];
</script>

<template>
  <NDrawer :show="show" width="100%" @update:show="handleUpdateShow">
    <NDrawerContent :title="`AI智能比对分析`" >
      <NSpin :show="loading">
        <!-- 错误提示 -->
        <div v-if="error && !loading" class="error-container">
          <NEmpty description="">
            <template #icon>
              <svg class="error-icon w-16 h-16 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </template>
            <template #extra>
              <div class="error-content">
                <div class="error-title">无法进行AI比对分析</div>
                <div class="error-message">{{ error }}</div>
                <NAlert type="info" :bordered="false" class="mt-16px">
                  <template #header>
                    <div class="flex items-center gap-2">
                      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      <span>可能的原因</span>
                    </div>
                  </template>
                  <ul class="error-reasons">
                    <li>标准在数据库中只有题录信息，缺少全文内容</li>
                    <li>标准文件尚未上传或处理</li>
                    <li>标准编号错误或不存在</li>
                    <li>标准格式不支持或解析失败</li>
                  </ul>
                </NAlert>
                <div class="error-info mt-16px">
                  <div class="info-item">
                    <span class="info-label">源标准：</span>
                    <span class="info-value">{{ sourceStandardNo }}</span>
                  </div>
                  <div class="info-item">
                    <span class="info-label">目标标准：</span>
                    <span class="info-value">{{ targetStandardNo }}</span>
                  </div>
                </div>
              </div>
            </template>
          </NEmpty>
        </div>

        <!-- 正常数据显示 -->
        <div v-else-if="comparisonData" class="flex flex-col gap-16px">
          <!-- 概览信息 -->
          <NCard title="比对概览" :bordered="false" size="small">
            <NSpace vertical :size="16">
              <!-- 标准信息 -->
              <div class="flex items-center justify-between">
                <div class="flex-1 standard-info-source">
                  <div class="text-sm text-gray-600 mb-1">源标准</div>
                  <div class="font-medium">{{ comparisonData.source_standard_no }}</div>
                  <div class="text-sm text-gray-500">{{ comparisonData.source_standard_name }}</div>
                </div>
                <div class="px-16px">
                  <svg class="w-6 h-6 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" />
                  </svg>
                </div>
                <div class="flex-1 standard-info-target">
                  <div class="text-sm text-gray-600 mb-1">目标标准</div>
                  <div class="font-medium">{{ comparisonData.target_standard_no }}</div>
                  <div class="text-sm text-gray-500">{{ comparisonData.target_standard_name }}</div>
                </div>
              </div>

              <!-- 统计指标 -->
              <div class="grid grid-cols-4 gap-16px">
                <NCard embedded>
                  <NStatistic label="对应指标数" :value="comparisonData.matched_indicators.length" />
                </NCard>

                <NCard embedded>
                  <NStatistic label="源标准独有" :value="comparisonData.source_unique_indicators.length" />
                </NCard>

                <NCard embedded>
                  <NStatistic label="目标标准独有" :value="comparisonData.target_unique_indicators.length" />
                </NCard>

                <NCard embedded>
                  <NStatistic label="分析耗时" :value="comparisonData.calculation_time.toFixed(2)" suffix="秒">
                    <template #prefix>
                      <icon-mdi:clock-outline class="text-icon" />
                    </template>
                  </NStatistic>
                </NCard>
              </div>
            </NSpace>
          </NCard>

          <!-- 筛选器 -->
          <NCard v-if="categoryOptions.length > 0" title="筛选条件" :bordered="false" size="small" class="filter-card">
            <template #header-extra>
              <NButton
                v-if="selectedCategories.length > 0 || searchKeyword || selectedChangeType"
                text
                type="primary"
                size="small"
                @click="clearAllFilters"
              >
                清除全部
              </NButton>
            </template>
            <div class="flex items-center gap-5 flex-wrap">
              <!-- 指标大类 -->
              <div class="flex items-center gap-2 flex-1" style="min-width: 280px;">
                <div class="flex items-center gap-2">
                  <svg class="w-4 h-4 text-blue-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" />
                  </svg>
                  <span class="text-sm text-gray-600 whitespace-nowrap">大类：</span>
                </div>
                <NSelect
                  v-model:value="selectedCategories"
                  multiple
                  :placeholder="categoryPlaceholder"
                  :options="categoryOptions"
                  clearable
                  :max-tag-count="2"
                  size="small"
                  style="flex: 1; max-width: 400px"
                />
              </div>

              <!-- 关键词搜索 -->
              <div class="flex items-center gap-2 flex-1" style="min-width: 240px;">
                <div class="flex items-center gap-2">
                  <svg class="w-4 h-4 text-green-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                  <span class="text-sm text-gray-600 whitespace-nowrap">关键词：</span>
                </div>
                <NInput
                  v-model:value="searchKeyword"
                  placeholder="搜索指标名称、要求等"
                  clearable
                  size="small"
                  style="flex: 1; max-width: 300px"
                />
              </div>

              <!-- 变化类型（单选） -->
              <div class="flex items-center gap-2" style="min-width: 200px;">
                <div class="flex items-center gap-2">
                  <svg class="w-4 h-4 text-orange-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" />
                  </svg>
                  <span class="text-sm text-gray-600 whitespace-nowrap">差异分析：</span>
                </div>
                <NSelect
                  v-model:value="selectedChangeType"
                  placeholder="全部"
                  :options="changeTypeOptions"
                  clearable
                  size="small"
                  style="max-width: 180px"
                />
              </div>
            </div>

            <!-- 统计信息 -->
            <div v-if="selectedCategories.length > 0 || searchKeyword || selectedChangeType" class="filter-stats mt-3">
              <div class="stat-item">
                <span class="stat-label">对应：</span>
                <span class="stat-value">{{ filteredMatchedIndicators.length }}</span>
                <span class="stat-unit">/{{ comparisonData.matched_indicators.length }}</span>
              </div>
              <span class="stat-divider">|</span>
              <div class="stat-item">
                <span class="stat-label">源独有：</span>
                <span class="stat-value">{{ filteredSourceUniqueIndicators.length }}</span>
                <span class="stat-unit">/{{ comparisonData.source_unique_indicators.length }}</span>
              </div>
              <span class="stat-divider">|</span>
              <div class="stat-item">
                <span class="stat-label">目标独有：</span>
                <span class="stat-value">{{ filteredTargetUniqueIndicators.length }}</span>
                <span class="stat-unit">/{{ comparisonData.target_unique_indicators.length }}</span>
              </div>
            </div>
          </NCard>

          <!-- 指标对比结果 - Tab 形式 -->
          <NCard :bordered="false" size="small">
            <NTabs type="line" animated>
              <!-- 对应指标对比 -->
              <NTabPane :name="'matched'" :tab="`对应指标 (${filteredMatchedIndicators.length})`">
                <div class="tab-content">
                  <NDataTable
                    v-if="filteredMatchedIndicators.length > 0"
                    :columns="matchedColumns"
                    :data="filteredMatchedIndicators"
                    :pagination="MatchedPagination"
                    :scroll-x="1200"
                    :single-line="false"
                  />
                  <NEmpty v-else description="未找到对应的指标" class="empty-placeholder" />
                </div>
              </NTabPane>

              <!-- 源标准独有指标 -->
              <NTabPane :name="'source'" :tab="`${comparisonData.source_standard_no} 独有 (${filteredSourceUniqueIndicators.length})`">
                <div class="tab-content">
                  <NDataTable
                    v-if="filteredSourceUniqueIndicators.length > 0"
                    :columns="uniqueColumns"
                    :data="filteredSourceUniqueIndicators"
                    :pagination="SourceUniquePagination"
                    :single-line="false"
                  />
                  <NEmpty v-else description="无独有指标" class="empty-placeholder" />
                </div>
              </NTabPane>

              <!-- 目标标准独有指标 -->
              <NTabPane :name="'target'" :tab="`${comparisonData.target_standard_no} 独有 (${filteredTargetUniqueIndicators.length})`">
                <div class="tab-content">
                  <NDataTable
                    v-if="filteredTargetUniqueIndicators.length > 0"
                    :columns="uniqueColumns"
                    :data="filteredTargetUniqueIndicators"
                    :pagination="TargetUniquePagination"
                    :single-line="false"
                  />
                  <NEmpty v-else description="无独有指标" class="empty-placeholder" />
                </div>
              </NTabPane>

              <!-- 章节对比视图 -->
              <NTabPane :name="'chapter'" :tab="`章节对比视图`">
                <div class="tab-content chapter-view">
                  <div v-if="chapterGroups.length > 0" class="chapter-layout">
                    <!-- 左侧导航栏 -->
                    <div class="chapter-sidebar">
                      <!-- 筛选器 -->
                      <div class="sidebar-filter">
                        <div class="filter-title">筛选指标</div>
                        <div class="filter-options">
                          <div
                            class="filter-option"
                            :class="{ active: chapterViewFilter === 'all' }"
                            @click="chapterViewFilter = 'all'"
                          >
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
                            </svg>
                            <span>全部</span>
                            <span class="count">{{ chapterGroups.length }}</span>
                          </div>
                          <div
                            class="filter-option"
                            :class="{ active: chapterViewFilter === 'matched' }"
                            @click="chapterViewFilter = 'matched'"
                          >
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            <span>对应</span>
                            <span class="count">{{ chapterGroups.filter(g => g.matchedCount > 0).length }}</span>
                          </div>
                          <div
                            class="filter-option"
                            :class="{ active: chapterViewFilter === 'unique' }"
                            @click="chapterViewFilter = 'unique'"
                          >
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
                            </svg>
                            <span>独有</span>
                            <span class="count">{{ chapterGroups.filter(g => g.uniqueCount > 0).length }}</span>
                          </div>
                        </div>
                      </div>

                      <!-- 章节导航 -->
                      <div class="sidebar-nav">
                        <div class="nav-title">章节导航</div>
                        <div class="nav-list">
                          <div
                            v-for="(group, idx) in filteredChapterGroups"
                            :key="idx"
                            class="nav-item"
                            :class="{ active: activeChapterId === `chapter-${idx}` }"
                            @click="scrollToChapter(`chapter-${idx}`)"
                          >
                            <div class="nav-item-name">{{ group.chapterName }}</div>
                            <div class="nav-item-stats">
                              <span v-if="group.matchedCount > 0" class="nav-stat matched">{{ group.matchedCount }}</span>
                              <span v-if="group.uniqueCount > 0" class="nav-stat unique">{{ group.uniqueCount }}</span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>

                    <!-- 右侧内容区 -->
                    <div class="chapter-content">
                      <div class="chapter-container">
                        <!-- 源标准章节 -->
                        <div class="chapter-section">
                          <div class="section-header">
                            <h3 class="section-title">
                              <svg class="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                              </svg>
                              {{ comparisonData.source_standard_no }} - 按章节组织
                            </h3>
                            <span class="section-subtitle">共 {{ filteredChapterGroups.length }} 个章节</span>
                          </div>

                          <!-- 章节卡片列表 -->
                          <div class="chapters-grid">
                            <div
                              v-for="(group, idx) in filteredChapterGroups"
                              :key="idx"
                              :id="`chapter-${idx}`"
                              class="chapter-card"
                            >
                              <!-- 章节头部 -->
                              <div class="chapter-card-header">
                                <div class="chapter-info">
                                  <svg class="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                                  </svg>
                                  <span class="chapter-name">{{ group.chapterName }}</span>
                                </div>
                                <div class="chapter-stats">
                                  <span v-if="group.matchedCount > 0" class="stat-badge stat-matched">
                                    对应 {{ group.matchedCount }}
                                  </span>
                                  <span v-if="group.changedCount > 0" class="stat-badge stat-changed">
                                    变化 {{ group.changedCount }}
                                  </span>
                                  <span v-if="group.uniqueCount > 0" class="stat-badge stat-unique">
                                    独有 {{ group.uniqueCount }}
                                  </span>
                                </div>
                              </div>

                              <!-- 指标列表 -->
                              <div class="indicators-list">
                                <div v-for="(indicator, iIdx) in group.indicators" :key="iIdx" class="indicator-item">
                                  <!-- 指标头部（横向布局）-->
                                  <div class="indicator-header">
                                    <!-- 状态图标 + 名称 + 大类 -->
                                    <div class="indicator-title-row">
                                      <svg v-if="indicator.type === 'matched' && indicator.isConsistent" class="status-icon icon-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                                      </svg>
                                      <svg v-else-if="indicator.type === 'matched' && !indicator.isConsistent" class="status-icon icon-warning" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                                      </svg>
                                      <svg v-else class="status-icon icon-unique" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                                      </svg>
                                      <span class="indicator-name">{{ indicator.name }}</span>
                                      <NTag size="small" type="info" :bordered="false">{{ indicator.category }}</NTag>
                                    </div>
                                    <!-- 变化分析或独有标识 -->
                                    <div v-if="indicator.type === 'matched' && indicator.changeAnalysis" class="indicator-status-tag">
                                      <NTag size="small" :type="indicator.isConsistent ? 'success' : 'warning'" :bordered="false">
                                        {{ indicator.changeAnalysis }}
                                      </NTag>
                                    </div>
                                    <div v-else-if="indicator.type === 'source_unique'" class="indicator-status-tag">
                                      <span class="unique-label">{{ comparisonData.target_standard_no }} 无对应</span>
                                    </div>
                                  </div>

                                  <!-- 指标对比内容 -->
                                  <div class="indicator-comparison">
                                    <!-- 源标准要求 -->
                                    <div class="comparison-item source-item">
                                      <div class="comparison-label">源标准</div>
                                      <div class="comparison-content">{{ indicator.sourceRequirement }}</div>
                                      <div v-if="indicator.sourceChapter" class="comparison-chapter">{{ indicator.sourceChapter }}</div>
                                    </div>

                                    <!-- 目标标准要求（仅对应时显示） -->
                                    <div v-if="indicator.type === 'matched'" class="comparison-item target-item">
                                      <div class="comparison-label">目标标准</div>
                                      <div class="comparison-content">{{ indicator.targetRequirement || '-' }}</div>
                                      <div v-if="indicator.targetChapter" class="comparison-chapter">{{ indicator.targetChapter }}</div>
                                    </div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>

                        <!-- 目标标准独有章节 -->
                        <div v-if="targetUniqueGroups.length > 0 && chapterViewFilter !== 'matched'" class="chapter-section target-unique-section">
                          <div class="section-header">
                            <h3 class="section-title">
                              <svg class="w-5 h-5 text-purple-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                              </svg>
                              {{ comparisonData.target_standard_no }} - 独有指标
                            </h3>
                            <span class="section-subtitle">{{ comparisonData.source_standard_no }} 无对应指标</span>
                          </div>

                          <!-- 章节卡片列表 -->
                          <div class="chapters-grid">
                            <div v-for="(group, idx) in targetUniqueGroups" :key="`target-${idx}`" class="chapter-card target-card">
                              <!-- 章节头部 -->
                              <div class="chapter-card-header">
                                <div class="chapter-info">
                                  <svg class="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                                  </svg>
                                  <span class="chapter-name">{{ group.chapterName }}</span>
                                </div>
                                <div class="chapter-stats">
                                  <span class="stat-badge stat-unique">独有 {{ group.indicators.length }}</span>
                                </div>
                              </div>

                              <!-- 指标列表 -->
                              <div class="indicators-list">
                                <div v-for="(indicator, iIdx) in group.indicators" :key="iIdx" class="indicator-item">
                                  <div class="indicator-header">
                                    <div class="indicator-title-row">
                                      <svg class="status-icon icon-target-unique" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
                                      </svg>
                                      <span class="indicator-name">{{ indicator.name }}</span>
                                      <NTag size="small" type="info" :bordered="false">{{ indicator.category }}</NTag>
                                    </div>
                                  </div>

                                  <div class="indicator-comparison">
                                    <div class="comparison-item target-unique-item">
                                      <div class="comparison-label">要求</div>
                                      <div class="comparison-content">{{ indicator.sourceRequirement }}</div>
                                      <div v-if="indicator.sourceChapter" class="comparison-chapter">{{ indicator.sourceChapter }}</div>
                                    </div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                  <NEmpty v-else description="暂无章节数据" class="empty-placeholder" />
                </div>
              </NTabPane>
            </NTabs>
          </NCard>
        </div>

        <NEmpty v-else description="暂无数据" class="py-32px" />
      </NSpin>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
/* 标准信息样式 */
.standard-info-source {
  padding: 12px;
  background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%);
  border-radius: 8px;
  border: 2px solid #18a058;
}

.standard-info-target {
  padding: 12px;
  background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
  border-radius: 8px;
  border: 2px solid #2080f0;
}

/* 错误容器 */
.error-container {
  padding: 40px 20px;
  min-height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.error-icon {
  max-width: 40px;
  max-height: 40px;
  display: block;
}

.error-content {
  max-width: 600px;
  text-align: center;
}

.error-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
  margin-bottom: 8px;
}

.error-message {
  font-size: 14px;
  color: #d03050;
  padding: 8px 16px;
  background: #fef0f0;
  border-radius: 4px;
  margin-bottom: 8px;
  word-break: break-word;
}

.error-reasons {
  list-style: disc;
  text-align: left;
  padding-left: 24px;
  margin: 8px 0 0 0;
  line-height: 1.8;
}

.error-reasons li {
  color: #666;
  font-size: 13px;
}

.error-info {
  background: #f5f5f5;
  padding: 12px 16px;
  border-radius: 6px;
  text-align: left;
}

.info-item {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.info-item:last-child {
  margin-bottom: 0;
}

.info-label {
  font-size: 13px;
  color: #666;
  font-weight: 500;
}

.info-value {
  font-size: 13px;
  color: #333;
  font-family: monospace;
}

/* 筛选卡片 */
.filter-card :deep(.n-card-header) {
  padding: 16px 20px;
  border-bottom: 1px solid #f0f0f0;
}

.filter-card :deep(.n-card__content) {
  padding: 20px;
}

/* 统计信息 */
.filter-stats {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: linear-gradient(135deg, #f5f7fa 0%, #f0f2f5 100%);
  border-radius: 6px;
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

/* Tab样式优化 */
:deep(.n-tabs .n-tabs-nav) {
  padding: 0 20px;
}

:deep(.n-tabs .n-tab-pane) {
  padding-top: 16px;
}

/* Tab内容区域 - 固定最小高度避免抖动 */
.tab-content {
  min-height: 500px;
  display: flex;
  flex-direction: column;
}

/* 空状态占位 - 保持与有数据时相同的高度 */
.empty-placeholder {
  min-height: 500px;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ========== 章节对比视图样式 ========== */
.chapter-view {
  height: 600px;
  display: flex;
  flex-direction: column;
}

/* 章节布局（左侧导航 + 右侧内容） */
.chapter-layout {
  display: flex;
  gap: 16px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* 左侧边栏 */
.chapter-sidebar {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow: hidden;
}

/* 筛选器 */
.sidebar-filter {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px;
  flex-shrink: 0;
}

.filter-title {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  margin-bottom: 10px;
  padding: 0 4px;
}

.filter-options {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.filter-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
  font-size: 13px;
  color: #6b7280;
  user-select: none;
}

.filter-option:hover {
  background: #f3f4f6;
  color: #374151;
}

.filter-option.active {
  background: #eff6ff;
  color: #2563eb;
  font-weight: 500;
}

.filter-option svg {
  flex-shrink: 0;
}

.filter-option .count {
  margin-left: auto;
  font-size: 12px;
  padding: 2px 6px;
  background: #e5e7eb;
  border-radius: 10px;
  font-weight: 500;
}

.filter-option.active .count {
  background: #bfdbfe;
  color: #1e40af;
}

/* 导航区域 */
.sidebar-nav {
  flex: 1;
  min-height: 0;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.nav-title {
  font-size: 13px;
  font-weight: 600;
  color: #333;
  padding: 12px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
  flex-shrink: 0;
}

.nav-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  min-height: 0;
}

.nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
  margin-bottom: 4px;
  user-select: none;
}

.nav-item:hover {
  background: #f3f4f6;
}

.nav-item.active {
  background: #eff6ff;
  border-left: 3px solid #3b82f6;
  padding-left: 7px;
}

.nav-item-name {
  font-size: 12px;
  color: #374151;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.4;
}

.nav-item.active .nav-item-name {
  color: #1e40af;
  font-weight: 500;
}

.nav-item-stats {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.nav-stat {
  font-size: 11px;
  padding: 2px 5px;
  border-radius: 8px;
  font-weight: 500;
}

.nav-stat.matched {
  background: #d1fae5;
  color: #065f46;
}

.nav-stat.unique {
  background: #dbeafe;
  color: #1e40af;
}

/* 右侧内容区 */
.chapter-content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  min-width: 0;
  padding-right: 4px;
}

.chapter-container {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding-bottom: 16px;
}

/* 章节区域 */
.chapter-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.target-unique-section {
  margin-top: 32px;
  padding-top: 32px;
  border-top: 2px dashed #e0e0e0;
}

/* 区域标题 */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
  border-radius: 8px;
  border-left: 4px solid #3b82f6;
}

.target-unique-section .section-header {
  border-left-color: #a855f7;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #333;
}

.section-subtitle {
  font-size: 13px;
  color: #666;
}

/* 章节网格 */
.chapters-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 章节卡片 */
.chapter-card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
  transition: all 0.2s;
}

.chapter-card:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  border-color: #3b82f6;
}

.target-card:hover {
  border-color: #a855f7;
}

/* 章节卡片头部 */
.chapter-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: #f9fafb;
  border-bottom: 1px solid #e5e7eb;
}

.chapter-info {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.chapter-name {
  font-size: 14px;
  font-weight: 500;
  color: #333;
}

/* 章节统计徽章 */
.chapter-stats {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.stat-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.stat-matched {
  background: #d1fae5;
  color: #065f46;
}

.stat-changed {
  background: #fed7aa;
  color: #92400e;
}

.stat-unique {
  background: #dbeafe;
  color: #1e40af;
}

/* 指标列表 */
.indicators-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
}

/* 指标项 */
.indicator-item {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  background: #fafafa;
  border-radius: 6px;
  border: 1px solid #e5e7eb;
  transition: all 0.2s;
}

.indicator-item:hover {
  background: #f5f5f5;
  border-color: #d1d5db;
}

/* 指标头部 */
.indicator-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

/* 指标标题行（图标 + 名称 + 大类） */
.indicator-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 200px;
}

/* 状态图标 */
.status-icon {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.icon-success {
  color: #10b981;
}

.icon-warning {
  color: #f59e0b;
}

.icon-unique {
  color: #9ca3af;
}

.icon-target-unique {
  color: #a855f7;
}

.indicator-name {
  font-size: 14px;
  font-weight: 600;
  color: #1f2937;
}

/* 指标状态标签 */
.indicator-status-tag {
  display: flex;
  align-items: center;
}

.unique-label {
  font-size: 12px;
  color: #6b7280;
  padding: 2px 8px;
  background: #f3f4f6;
  border-radius: 4px;
}

/* 指标对比内容 */
.indicator-comparison {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 10px;
  margin-top: 4px;
}

/* 对比项 */
.comparison-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.6;
}

.comparison-label {
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 2px;
}

.comparison-content {
  color: #374151;
}

.comparison-chapter {
  display: inline-flex;
  align-items: center;
  padding: 2px 6px;
  background: rgba(255, 255, 255, 0.6);
  border-radius: 3px;
  font-size: 11px;
  font-weight: 500;
  margin-top: 4px;
  align-self: flex-start;
}

/* 源标准样式 */
.source-item {
  background: #eff6ff;
  border-left: 3px solid #3b82f6;
}

.source-item .comparison-label {
  color: #1e40af;
}

.source-item .comparison-chapter {
  color: #1e40af;
}

/* 目标标准样式 */
.target-item {
  background: #fef3c7;
  border-left: 3px solid #f59e0b;
}

.target-item .comparison-label {
  color: #92400e;
}

.target-item .comparison-chapter {
  color: #92400e;
}

/* 目标独有样式 */
.target-unique-item {
  background: #f3e8ff;
  border-left: 3px solid #a855f7;
}

.target-unique-item .comparison-label {
  color: #6b21a8;
}

.target-unique-item .comparison-chapter {
  color: #6b21a8;
}
</style>
