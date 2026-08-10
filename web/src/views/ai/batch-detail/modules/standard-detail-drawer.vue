<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { NDrawer, NDrawerContent, NSpace, NCard, NTag, NDivider, NSelect, NEmpty, NButton, NTooltip, NPagination, NCheckbox } from 'naive-ui';
import FullTextSimilarityDrawer from './full-text-similarity-drawer.vue';
import AIComparisonDrawer from './ai-comparison-drawer.vue';
import { fetchSimilarStandardsByNo, updateSimilarAttention, fetchFullTextSimilarity } from '@/service/api';

// Props & Emits
const props = defineProps<{
  show: boolean;
  standard: any | null;
  batchId: number | null;
  filterStdDomain?: string | null;
}>();

const emit = defineEmits<{
  'update:show': [value: boolean];
}>();

const standardData = computed(() => props.standard);

// 相似标准列表（已筛选+分页，来自后端）
const similarStandards = ref<any[]>([]);
const loadingSimilars = ref(false);
const filteredTotal = ref(0);
const allStats = ref({ total: 0, need_attention: 0, ignored: 0, same_series: 0 });

// 筛选条件
const selectedTags = ref<string[]>([]);
const hiddenTags = ref<string[]>(['同系列标准']);
const filterMode = ref<'any' | 'all'>('any');
const hasResFilter = ref<string>('has_res');
const llmScoreMin = ref<number | null>(null);
const needAttentionOnly = ref<boolean>(true);
const sortBy = ref<'llm_score' | 'tag'>('llm_score');

// 分页
const page = ref(1);
const pageSize = ref(10);

// 全文相似度自动补算状态（ftsAutoToken 用于在翻页/关闭抽屉时中止正在进行的补算）
const ftsComputing = ref<Record<string, boolean>>({});
let ftsAutoToken = 0;

async function loadSimilarStandards() {
  if (!props.standard) return;
  ftsAutoToken++; // 中止上一轮自动补算
  ftsComputing.value = {}; // 清空上一轮的「计算中」标记
  loadingSimilars.value = true;
  try {
    const params: Parameters<typeof fetchSimilarStandardsByNo>[0] = {
      page: page.value,
      page_size: pageSize.value,
      sort_by: sortBy.value,
      need_attention_only: needAttentionOnly.value,
    };
    if (props.standard.id != null) {
      params.record_id = props.standard.id;
    } else if (props.standard.standard_no) {
      params.standard_no = props.standard.standard_no;
    } else {
      return;
    }
    if (selectedTags.value.length) {
      params.filter_tags = selectedTags.value.join(',');
      params.filter_tags_mode = filterMode.value;
    }
    if (hiddenTags.value.length) {
      params.hidden_tags = hiddenTags.value.join(',');
    }
    if (hasResFilter.value) params.has_res_filter = hasResFilter.value;
    if (llmScoreMin.value !== null) params.llm_score_min = llmScoreMin.value;
    if (props.filterStdDomain) params.filter_std_domain = props.filterStdDomain;

    const res = await fetchSimilarStandardsByNo(params);
    const data = res.data!;
    similarStandards.value = data.list ?? [];
    filteredTotal.value = data.total ?? 0;
    allStats.value = data.stats ?? { total: 0, need_attention: 0, ignored: 0, same_series: 0 };
    // 列表就绪后，后台逐条补算缺失的全文相似度
    autoFillFullTextSimilarity();
  } catch {
    window.$message?.error('获取相似标准列表失败');
    similarStandards.value = [];
  } finally {
    loadingSimilars.value = false;
  }
}

// 自动补算：对当前页中缺少全文相似度的行，串行逐条调用计算接口（结果会写入缓存，下次直接命中）
async function autoFillFullTextSimilarity() {
  const token = ++ftsAutoToken;
  const sourceNo = standardData.value?.standard_no;
  if (!sourceNo) return;
  const targets = similarStandards.value.filter(s => s.full_text_similarity == null && s.standard_no);
  for (const s of targets) {
    // 翻页 / 关闭抽屉 / 新一轮加载都会使 token 失效，及时中止
    if (token !== ftsAutoToken || !props.show) return;
    const key = s.standard_no;
    ftsComputing.value[key] = true;
    try {
      const res = await fetchFullTextSimilarity({
        source_standard_no: sourceNo,
        target_standard_no: key
      });
      if (token !== ftsAutoToken) return;
      const pct = res.data?.similarity_percentage;
      if (typeof pct === 'number') {
        s.full_text_similarity = pct;
      }
    } catch {
      // 单条失败静默跳过（如缺少全文），保留「未计算」
    } finally {
      if (token === ftsAutoToken) ftsComputing.value[key] = false;
    }
  }
}

watch(
  () => props.show,
  val => {
    if (val) {
      loadSimilarStandards();
    } else {
      ftsAutoToken++; // 关闭抽屉时中止自动补算
      ftsComputing.value = {};
      similarStandards.value = [];
      filteredTotal.value = 0;
      allStats.value = { total: 0, need_attention: 0, ignored: 0, same_series: 0 };
    }
  }
);

// 筛选条件变化：重置页码并重新请求
watch([selectedTags, hiddenTags, filterMode, hasResFilter, llmScoreMin, needAttentionOnly, sortBy], () => {
  page.value = 1;
  if (props.show) loadSimilarStandards();
});

watch(page, () => {
  if (props.show) loadSimilarStandards();
});

watch(pageSize, () => {
  page.value = 1;
  if (props.show) loadSimilarStandards();
});

// 统计信息
const statistics = computed(() => ({
  total: allStats.value.total,
  sameSeries: allStats.value.same_series,
  needAttention: allStats.value.need_attention,
  ignored: allStats.value.ignored,
  filtered: filteredTotal.value,
  hidden: allStats.value.total - filteredTotal.value,
}));

// 切换相似标准的需要关注状态
const togglingMap = ref<Record<string, boolean>>({});

async function toggleAttention(similar: any) {
  if (!props.batchId || !standardData.value?.standard_no || !similar.standard_no) return;
  const key = similar.standard_no;
  togglingMap.value[key] = true;
  try {
    const newVal = !similar['need_attention'];
    await updateSimilarAttention(props.batchId, {
      standard_no: standardData.value.standard_no,
      similar_standard_no: similar.standard_no,
      need_attention: newVal
    });
    similar['need_attention'] = newVal;
    // 乐观更新本地统计，避免重新拉取
    const delta = newVal ? 1 : -1;
    allStats.value.need_attention += delta;
    allStats.value.ignored -= delta;
  } catch {
    window.$message?.error('操作失败，请重试');
  } finally {
    togglingMap.value[key] = false;
  }
}

// 全文相似度抽屉
const showFullTextSimilarity = ref(false);
const fullTextSimilarityParams = ref({
  sourceStandardNo: '',
  targetStandardNo: ''
});

// AI比对抽屉
const showAIComparison = ref(false);
const aiComparisonParams = ref({
  sourceStandardNo: '',
  targetStandardNo: '',
  forceRecalculate: false
});

// 查看全文相似度 - 在新窗口打开
function handleViewFullTextSimilarity(similar: any) {
  const params = new URLSearchParams({
    action: 'fullTextSimilarity',
    sourceStandardNo: standardData.value.standard_no,
    targetStandardNo: similar.standard_no || ''
  });
  window.open(`${window.location.pathname}?${params.toString()}`, '_blank');
}

// AI智能比对 - 在新窗口打开
function handleAIComparison(similar: any, forceRecalculate = false) {
  const params = new URLSearchParams({
    action: 'aiComparison',
    sourceStandardNo: standardData.value.standard_no,
    targetStandardNo: similar.standard_no || '',
    forceRecalculate: forceRecalculate.toString()
  });
  window.open(`${window.location.pathname}?${params.toString()}`, '_blank');
}

// 标签名称映射（用于显示）
const tagLabels: Record<string, string> = {
  同系列标准: '同系列标准',
  标准化对象一致: '标准化对象一致',
  通用专用关系: '通用专用关系',
  适用范围重叠: '适用范围相似'
};

// 标签选项（用于 select）
const tagOptions = Object.keys(tagLabels).map(key => ({
  label: tagLabels[key],
  value: key
}));

// 获取标签类型（用于颜色）
function getTagType(tagKey: string): 'error' | 'warning' | 'info' | 'success' | 'default' {
  if (tagKey === '标准化对象一致' || tagKey === '适用范围重叠') return 'warning';
  if (tagKey === '通用专用关系') return 'info';
  if (tagKey === '同系列标准') return 'default';
  return 'info';
}

</script>

<template>
  <NDrawer :show="show" :width="'100%'" @update:show="emit('update:show', $event)">
    <NDrawerContent v-if="standardData" :title="`${standardData.standard_no} ${standardData.standard_name} - 相似度分析`" closable>
      <NFlex vertical size="small">
        <!-- 基本信息 -->
        <div>
          <div class="font-bold mb-2 text-base">标准名称</div>
          <div>{{ standardData.standard_name }}</div>
        </div>

        <div v-if="standardData.use_range">
          <div class="font-bold mb-2 text-base">适用范围</div>
          <div class="text-sm" v-html="standardData.use_range"></div>
        </div>

        <NDivider />

        <!-- 筛选器 -->
        <div v-if="allStats.total > 0">
          <NCard :bordered="false" size="small" class="mb-4 filter-card">
            <template #header>
              <div class="flex items-center justify-between">
                <div class="flex items-center gap-2">
                  <span class="text-base font-semibold">筛选条件</span>
                  <NTag v-if="selectedTags.length > 0 || hiddenTags.length > 0 || hasResFilter" type="info" size="small" :bordered="false">
                    {{ selectedTags.length + hiddenTags.length + (hasResFilter ? 1 : 0) }} 个条件生效
                  </NTag>
                </div>
                <NButton
                  v-if="selectedTags.length > 0 || hiddenTags.length > 0 || hasResFilter || llmScoreMin !== null || !needAttentionOnly"
                  text
                  type="primary"
                  size="small"
                  @click="selectedTags = []; hiddenTags = []; hasResFilter = 'has_res'; llmScoreMin = null; needAttentionOnly = true"
                >
                  重置全部
                </NButton>
              </div>
            </template>

            <NSpace vertical :size="20">
              <!-- 显示筛选和隐藏筛选在同一行 -->
              <div class="filter-section">
                <div class="filter-header mb-3">
                  <div class="filter-title">
                    <svg class="w-4 h-4 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
                    </svg>
                    <span>标签筛选</span>
                  </div>
                  <NSpace :size="8">
                    <NTag
                      v-if="selectedTags.length > 0"
                      size="small"
                      :type="filterMode === 'all' ? 'info' : 'success'"
                      :bordered="false"
                      style="cursor: pointer; user-select: none"
                      @click="filterMode = filterMode === 'all' ? 'any' : 'all'"
                    >
                      <template #icon>
                        <svg v-if="filterMode === 'all'" class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        <svg v-else class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4" />
                        </svg>
                      </template>
                      {{ filterMode === 'all' ? '满足全部' : '满足任一' }}
                    </NTag>
                    <NButton
                      v-if="selectedTags.length > 0 || hiddenTags.length > 0 || hasResFilter || llmScoreMin !== null || !needAttentionOnly"
                      text
                      type="primary"
                      size="tiny"
                      @click="selectedTags = []; hiddenTags = []; hasResFilter = 'has_res'; llmScoreMin = null; needAttentionOnly = true"
                    >
                      清除全部
                    </NButton>
                  </NSpace>
                </div>
                <div class="flex items-center gap-6 flex-wrap">
                  <!-- 显示筛选 -->
                  <div class="flex items-center gap-3 flex-1" style="min-width: 280px;">
                    <div class="flex items-center gap-2">
                      <svg class="w-4 h-4 text-green-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      <span class="text-sm text-gray-600 whitespace-nowrap">显示：</span>
                    </div>
                    <NSelect
                      v-model:value="selectedTags"
                      multiple
                      placeholder="包含以下类型"
                      :options="tagOptions"
                      clearable
                      :max-tag-count="2"
                      size="small"
                      style="flex: 1; max-width: 350px"
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
                      <svg class="w-4 h-4 text-blue-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                      </svg>
                      <span class="text-sm text-gray-600 whitespace-nowrap">标准原文：</span>
                    </div>
                    <NSelect
                      v-model:value="hasResFilter"
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
                  <!-- llm_score 最低分筛选 -->
                  <div class="flex items-center gap-3 flex-1" style="min-width: 280px;">
                    <div class="flex items-center gap-2">
                      <svg class="w-4 h-4 text-purple-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                      </svg>
                      <span class="text-sm text-gray-600 whitespace-nowrap">基本信息相似度≥：</span>
                    </div>
                    <NSelect
                      v-model:value="llmScoreMin"
                      placeholder="不限"
                      clearable
                      :options="[
                        { label: '0.9 以上（高度重复）', value: 0.9 },
                        { label: '0.8 以上（强相关）', value: 0.8 },
                        { label: '0.7 以上（需要关注）', value: 0.7 },
                        { label: '0.6 以上（中等相关）', value: 0.6 }
                      ]"
                      size="small"
                      style="flex: 1; max-width: 220px"
                    />
                  </div>
                  <!-- 关注状态 + 排序 -->
                  <div class="flex items-center gap-4 flex-wrap">
                    <NCheckbox v-model:checked="needAttentionOnly" size="small">
                      仅需要关注
                    </NCheckbox>
                    <div class="flex items-center gap-2">
                      <span class="text-sm text-gray-600 whitespace-nowrap">排序：</span>
                      <NSelect
                        v-model:value="sortBy"
                        :options="[
                          { label: '按相似度评分', value: 'llm_score' },
                          { label: '按标签重要性', value: 'tag' }
                        ]"
                        size="small"
                        style="width: 140px"
                      />
                    </div>
                  </div>
                </div>
              </div>

              <NDivider style="margin: 0" />

              <!-- 统计信息 -->
              <div class="filter-stats">
                <div class="stat-item">
                  <span class="stat-label">总计：</span>
                  <span class="stat-value">{{ statistics.total }}</span>
                  <span class="stat-unit">个相似标准</span>
                </div>
                <span class="stat-divider">|</span>
                <div v-if="statistics.needAttention > 0" class="stat-item">
                  <span class="stat-label">需要关注：</span>
                  <span class="stat-value text-red-500">{{ statistics.needAttention }}</span>
                  <span class="stat-unit">个</span>
                </div>
                <span v-if="statistics.needAttention > 0" class="stat-divider">|</span>
                <div v-if="statistics.ignored > 0" class="stat-item">
                  <span class="stat-label">已忽略：</span>
                  <span class="stat-value text-gray-400">{{ statistics.ignored }}</span>
                  <span class="stat-unit">个</span>
                </div>
                <span v-if="statistics.ignored > 0" class="stat-divider">|</span>
                <div v-if="statistics.sameSeries > 0" class="stat-item">
                  <span class="stat-label">同系列：</span>
                  <span class="stat-value text-gray-400">{{ statistics.sameSeries }}</span>
                  <span class="stat-unit">个</span>
                </div>
                <span v-if="statistics.sameSeries > 0" class="stat-divider">|</span>
                <div class="stat-item">
                  <span class="stat-label">当前显示：</span>
                  <span class="stat-value" :class="statistics.hidden > 0 ? 'text-blue-500' : 'text-green-500'">
                    {{ statistics.filtered }}
                  </span>
                  <span class="stat-unit">个</span>
                </div>
                <template v-if="statistics.hidden > 0">
                  <span class="stat-divider">|</span>
                  <div class="stat-item">
                    <span class="stat-label">已隐藏：</span>
                    <span class="stat-value text-red-500">{{ statistics.hidden }}</span>
                    <span class="stat-unit">个</span>
                  </div>
                </template>
              </div>
            </NSpace>
          </NCard>
        </div>

        <!-- 相似标准列表 -->
        <div v-if="allStats.total > 0">
          <div class="font-bold mb-3 text-base">
            相似标准列表
            <span class="text-sm font-normal text-gray-500">({{ filteredTotal }} 个)</span>
          </div>

          <!-- 空状态 -->
          <NEmpty v-if="similarStandards.length === 0" description="没有符合筛选条件的标准" class="py-8" />

          <!-- 标准列表 -->
          <NSpace v-else vertical :size="12">
            <NCard v-for="(similar, idx) in similarStandards" :key="idx" size="small" embedded>
              <div class="text-sm">
                <div class="flex items-start justify-between mb-2">
                  <div class="flex-1">
                    <div class="mb-2 flex items-center gap-2">
                      <span class="font-bold">编号：</span>{{ similar.standard_no || '-' }}
                      <NTag v-if="similar['need_attention']" type="error" size="small" :bordered="false">需要关注</NTag>
                    </div>
                    <div class="mb-2">
                      <span class="font-bold">名称：</span>
                      <span>{{ similar.cname }}</span>
                      <NTooltip v-if="similar.use_range" trigger="hover" :style="{ maxWidth: '500px' }">
                        <template #trigger>
                          <NTag size="tiny" :bordered="false" class="ml-2 cursor-help use-range-tag">
                            适用范围
                          </NTag>
                        </template>
                        <div class="max-w-md">
                          <div class="font-semibold mb-1">适用范围：</div>
                          <div class="text-sm" v-html="similar.use_range"></div>
                        </div>
                      </NTooltip>
                    </div>
                    <!-- 相似度评分 -->
                    <div class="mb-2 flex items-center gap-4 text-xs text-gray-500">
                      <span>
                        基本信息综合相似度：
                        <span
                          class="font-bold text-sm"
                          :class="{
                            'text-red-500': (similar.llm_score ?? 0) >= 0.8,
                            'text-orange-500': (similar.llm_score ?? 0) >= 0.6 && (similar.llm_score ?? 0) < 0.8,
                            'text-gray-400': (similar.llm_score ?? 0) < 0.6
                          }"
                        >{{ similar.llm_score != null ? (similar.llm_score * 100).toFixed(0) + '%' : '-' }}</span>
                      </span>
                    </div>
                  </div>
                  <NSpace :size="8" align="start">
                    <NTooltip trigger="hover">
                      <template #trigger>
                        <NButton
                          :type="similar['need_attention'] === true ? 'error' : similar['need_attention'] === false ? 'default' : 'default'"
                          :ghost="similar['need_attention'] !== true"
                          :dashed="similar['need_attention'] == null"
                          size="small"
                          :loading="togglingMap[similar.standard_no]"
                          @click="toggleAttention(similar)"
                        >
                          {{ similar['need_attention'] === true ? '需要关注' : similar['need_attention'] === false ? '已忽略' : '标记关注' }}
                        </NButton>
                      </template>
                      {{ similar['need_attention'] === true ? '点击取消关注' : similar['need_attention'] === false ? '点击重新标记为关注' : '点击标记为需要关注' }}
                    </NTooltip>
                    <NButton
                      type="primary"
                      ghost
                      size="small"
                      :loading="ftsComputing[similar.standard_no]"
                      @click="handleViewFullTextSimilarity(similar)"
                    >
                      全文相似度
                      <span v-if="similar.full_text_similarity != null" class="fts-pct">
                        {{ similar.full_text_similarity.toFixed(1) }}%
                      </span>
                      <span v-else-if="ftsComputing[similar.standard_no]" class="fts-pct fts-computing">计算中</span>
                      <span v-else class="fts-pct fts-none">未计算</span>
                    </NButton>
                    <NButton
                      type="success"
                      ghost
                      size="small"
                      @click="handleAIComparison(similar, false)"
                    >
                      <template #icon>
                        <icon-mdi:robot-outline class="text-icon" />
                      </template>
                      AI比对
                    </NButton>
                  </NSpace>
                </div>

                <!-- 疑似交叉类型 -->
                <div v-if="similar.tags && similar.tags.length > 0" class="mb-2">
                  <div class="font-bold mb-1">疑似交叉类型：</div>
                  <NSpace :size="[8, 8]">
                    <NTag
                      v-for="tag in similar.tags"
                      :key="tag"
                      :type="getTagType(tag)"
                      size="small"
                    >
                      {{ tagLabels[tag] }}
                    </NTag>
                  </NSpace>
                </div>

                <!-- 关系说明 -->
                <div v-if="similar.relation_desc" class="mb-2">
                  <span class="font-bold">关系说明：</span>
                  <span class="text-gray-600">{{ similar.relation_desc }}</span>
                </div>
              </div>
            </NCard>
          </NSpace>

          <!-- 分页 -->
          <div v-if="filteredTotal > pageSize" class="mt-4 flex justify-end">
            <NPagination
              v-model:page="page"
              :page-count="Math.ceil(filteredTotal / pageSize)"
              :page-size="pageSize"
              show-size-picker
              :page-sizes="[10, 20, 30, 50]"
              @update:page-size="(size) => { pageSize = size; page = 1; }"
            />
          </div>
        </div>

        <div v-else class="text-center text-gray-400 py-8">
          未找到符合阈值的相似标准
        </div>
      </NFlex>
    </NDrawerContent>
  </NDrawer>

  <!-- 全文相似度分析抽屉 -->
  <FullTextSimilarityDrawer
    v-model:show="showFullTextSimilarity"
    :source-standard-no="fullTextSimilarityParams.sourceStandardNo"
    :target-standard-no="fullTextSimilarityParams.targetStandardNo"
  />

  <!-- AI比对抽屉 -->
  <AIComparisonDrawer
    v-model:show="showAIComparison"
    :source-standard-no="aiComparisonParams.sourceStandardNo"
    :target-standard-no="aiComparisonParams.targetStandardNo"
    :force-recalculate="aiComparisonParams.forceRecalculate"
  />
</template>

<style scoped>
/* 筛选卡片 */
.filter-card :deep(.n-card-header) {
  padding: 16px 20px;
  border-bottom: 1px solid #f0f0f0;
}

.filter-card :deep(.n-card__content) {
  padding: 20px;
}

/* 筛选区域 */
.filter-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
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

.filter-description {
  font-weight: 400;
  font-size: 12px;
  color: #999;
  margin-left: 4px;
}

.filter-content {
  padding: 12px 16px;
  background: #fafafa;
  border-radius: 6px;
  border: 1px solid #f0f0f0;
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

/* 鼠标悬浮提示样式 */
.cursor-help {
  cursor: help;
}

/* 适用范围标签样式 */
.use-range-tag {
  vertical-align: middle;
  transition: all 0.2s;
}

.use-range-tag:hover {
  opacity: 0.8;
  transform: scale(1.05);
}

/* 查看全文相似度按钮上的百分比（颜色继承按钮文字色，与"查看全文相似度"保持一致） */
.fts-pct {
  margin-left: 6px;
  font-weight: 700;
  font-family: monospace;
  letter-spacing: 0.3px;
}

.fts-none {
  color: #999;
  font-weight: 400;
}

.fts-computing {
  color: #2080f0;
  font-weight: 400;
  animation: fts-blink 1.2s ease-in-out infinite;
}

@keyframes fts-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
