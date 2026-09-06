<script setup lang="ts">
import {computed, ref, watch} from 'vue';
import {NButton, NDrawer, NDrawerContent, NEmpty, NPopover, NSpace, NTag, NTabs, NTabPane, useMessage} from 'naive-ui';
import {marked} from 'marked';
import {fetchAIComparison, fetchAIComparisonDetail, fetchStandardBatchVerify} from '@/service/api';
import StdDetailDrawer from '../../standard-base-info/modules/std-detail-drawer.vue';

const message = useMessage();

const props = defineProps<{
  show: boolean;
  sourceStandardNo: string;
  targetStandardNo: string;
  forceRecalculate?: boolean;
}>();

const emit = defineEmits<{
  'update:show': [value: boolean];
}>();

const loading = ref(false);
const data = ref<Api.AI.AIComparisonResponse | null>(null);
const error = ref<string | null>(null);

async function loadData() {
  if (!props.sourceStandardNo || !props.targetStandardNo) return;
  loading.value = true;
  error.value = null;
  data.value = null;
  try {
    if (!props.forceRecalculate) {
      const cacheResp = await fetchAIComparisonDetail({
        source_standard_no: props.sourceStandardNo,
        target_standard_no: props.targetStandardNo,
      });
      if (cacheResp.data?.matched_html) {
        data.value = {
          success: true,
          from_cache: true,
          source_standard_name: '',
          target_standard_name: '',
          ...cacheResp.data,
          source_standard_no: cacheResp.data.source_standard_no || props.sourceStandardNo,
          target_standard_no: cacheResp.data.target_standard_no || props.targetStandardNo,
        } as any;
        message.success('已从缓存加载');
        return;
      }
    }
    const resp = await fetchAIComparison({
      source_standard_no: props.sourceStandardNo,
      target_standard_no: props.targetStandardNo,
      force_recalculate: props.forceRecalculate || false,
    });
    if (!resp.data) {
      error.value = (resp as any).msg || '比对失败';
      return;
    }
    data.value = resp.data;
    message.success(resp.data.from_cache ? '已从缓存加载' : '比对完成');
  } catch (err: any) {
    error.value = err.message || '未知错误';
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.show,
  newVal => {
    if (newVal) loadData();
    else {
      data.value = null;
      error.value = null;
    }
  },
);

// ─── 标准号点击查看原文 ───────────────────────────────────────────────
// banner 上的源/目标标准号：用 standard_no 批量换取数据库 id，命中才允许点击打开原文抽屉
const stdIdMap = ref<Record<string, string>>({});
const showStdDetail = ref(false);
const selectedStdId = ref('');
const selectedChapterNo = ref('');
const stdDetailTab = ref<'detail' | 'html' | 'pdf'>('detail');

async function resolveStdIds() {
  const nos = [data.value?.source_standard_no, data.value?.target_standard_no].filter(Boolean) as string[];
  const unique = [...new Set(nos)];
  if (!unique.length) return;
  try {
    const {data: res, error: err} = await fetchStandardBatchVerify(unique);
    if (err || !res) return;
    const map: Record<string, string> = {};
    for (const no of unique) {
      const e = res[no];
      if (e?.exists && e.id) map[no] = e.id;
    }
    stdIdMap.value = map;
  } catch {
    // 查询失败：标准号保持不可点击，不阻断比对主流程
  }
}

function openStdDetail(stdNo?: string) {
  if (!stdNo) return;
  const id = stdIdMap.value[stdNo];
  if (!id) {
    message.info(`标准 ${stdNo} 原文暂不可用`);
    return;
  }
  selectedStdId.value = id;
  // banner 标准号：打开基础信息 tab，清掉可能残留的章节跳转
  selectedChapterNo.value = '';
  stdDetailTab.value = 'detail';
  showStdDetail.value = true;
}

// 指标章节号：打开 HTML 正文 tab 并定位到该章节
function openChapter(stdNo?: string, clause?: string) {
  if (!stdNo || !clause) return;
  const id = stdIdMap.value[stdNo];
  if (!id) {
    message.info(`标准 ${stdNo} 原文暂不可用`);
    return;
  }
  selectedStdId.value = id;
  selectedChapterNo.value = clause;
  stdDetailTab.value = 'html';
  showStdDetail.value = true;
}

// 比对数据就绪后解析 id；数据清空（关闭抽屉）时一并重置
watch(data, (v) => {
  stdIdMap.value = {};
  if (v) resolveStdIds();
});

function handleUpdateShow(value: boolean) {
  emit('update:show', value);
}

const allIndicators = computed(() => data.value?.all_indicators ?? []);

// 综合评价：后端以 markdown（**加粗**）标注重点关注指标，这里渲染为 HTML
const assessmentHtml = computed(() => {
  const text = data.value?.overall_assessment || '';
  if (!text) return '';
  return marked.parse(text, {breaks: true}) as string;
});

// 全量指标筛选状态
const filterType = ref<'' | 'matched' | 'source_only' | 'target_only'>('matched');
const filterNormClass = ref('');
const filterChangeType = ref<'' | 'consistent' | 'changed'>('');
const filterCategory = ref('');
const searchKeyword = ref('');

const filterOptions = [
  { value: '', label: '全部' },
  {value: 'matched', label: '同义指标'},
  {value: 'source_only', label: '源标准独有指标'},
  {value: 'target_only', label: '目标标准独有指标'},
];

const normClassOptions = [
  {value: '基础类', label: '基础类'},
  {value: '规范类', label: '规范类'},
  {value: '方法类', label: '方法类'},
];

const changeTypeOptions = [
  { value: '', label: '全部变更' },
  { value: 'consistent', label: '一致' },
  { value: 'changed', label: '其他' },
];

function getFilterCount(type: string) {
  const list = allIndicators.value;
  if (!type) return list.length;
  return list.filter(i => i.comparison_type === type).length;
}

function getNormClassCount(type: string) {
  const list = allIndicators.value;
  if (!type) return list.length;
  return list.filter(i => (i as any).norm_class === type).length;
}

function getChangeTypeCount(type: string) {
  const list = allIndicators.value.filter(i => i.comparison_type === 'matched');
  if (!type) return list.length;
  if (type === 'consistent') {
    return list.filter(i => (i.change_analysis || '').includes('一致')).length;
  }
  return list.filter(i => !(i.change_analysis || '').includes('一致')).length;
}

const filteredAllList = computed(() => {
  let list = allIndicators.value;
  if (filterType.value) list = list.filter(i => i.comparison_type === filterType.value);
  if (filterNormClass.value) list = list.filter(i => (i as any).norm_class === filterNormClass.value);
  if (filterCategory.value) list = list.filter(i => i.indicator_category === filterCategory.value);
  if (filterChangeType.value) {
    if (filterChangeType.value === 'consistent') {
      list = list.filter(i => (i.change_analysis || '').includes('一致'));
    } else {
      list = list.filter(i => !(i.change_analysis || '').includes('一致'));
    }
  }
  if (searchKeyword.value.trim()) {
    const kw = searchKeyword.value.trim().toLowerCase();
    list = list.filter(
      i =>
        (i.source_indicator_object || '').toLowerCase().includes(kw) ||
        (i.target_indicator_object || '').toLowerCase().includes(kw) ||
        (i.source_experiment_name || '').toLowerCase().includes(kw) ||
        (i.target_experiment_name || '').toLowerCase().includes(kw) ||
        (i.standard_object || '').toLowerCase().includes(kw) ||
        (i.applicable_object || '').toLowerCase().includes(kw) ||
        (i.indicator_category || '').toLowerCase().includes(kw) ||
        (i.source_value || '').toLowerCase().includes(kw) ||
        (i.target_value || '').toLowerCase().includes(kw) ||
        (i.source_result || '').toLowerCase().includes(kw) ||
        (i.target_result || '').toLowerCase().includes(kw) ||
        (i.change_analysis || '').toLowerCase().includes(kw),
    );
  }
  return list;
});

function setFilterType(v: string) {
  filterType.value = (filterType.value === v ? '' : v) as typeof filterType.value;
}

function setFilterChangeType(v: string) {
  filterChangeType.value = (filterChangeType.value === v ? '' : v) as typeof filterChangeType.value;
}

function getItemNormClass(item: Record<string, any>): string {
  return item.norm_class || '';
}

function normClassKey(nc: string) {
  if (nc === '规范类要素') return 'spec';
  if (nc === '其他要素') return 'other';
  if (nc === '基本要素') return 'elem';
  if (nc === '基础要素') return 'base';
  if (nc === '服务提供类要素') return 'svc-src';
  if (nc === '服务评价类要素') return 'svc-eval';
  return 'spec';
}

function normClassLabel(nc: string) {
  const map: Record<string, string> = {
    '规范类要素': '规范',
    '其他要素': '其他',
    '基本要素': '基本',
    '基础要素': '基础',
    '服务提供类要素': '服务提供',
    '服务评价类要素': '服务评价',
  };
  return map[nc] ?? nc.slice(0, 2);
}

function isChanged(item: Api.AI.FullIndicator) {
  if (item.comparison_type !== 'matched') return false;
  return item.change_analysis !== '一致';
}

const stats = computed(() => {
  if (data.value?.stats) return data.value.stats;
  const all = allIndicators.value;
  return {
    matched: all.filter(i => i.comparison_type === 'matched').length,
    source_only: all.filter(i => i.comparison_type === 'source_only').length,
    target_only: all.filter(i => i.comparison_type === 'target_only').length,
  };
});

function typeLabel(ct: string) {
  if (ct === 'matched') return '同义指标';
  if (ct === 'source_only') return '源标准独有指标';
  return '目标标准独有指标';
}

// 变更分析：提取冒号前的专业词汇作为徽标（收严/放宽/一致/条件删减…），冒号后为说明正文；
// 无冒号的短文本（如「一致」）整体作徽标，冒号前过长（AI 未按格式输出）则回退纯文本
function splitChangeAnalysis(text: string): {tag: string; body: string; cls: string} | null {
  const m = text.match(/^([^：:]{1,12})[：:]([\s\S]*)$/);
  if (m) {
    const tag = m[1].trim();
    if (tag) return {tag, body: m[2].trim(), cls: changeTagClass(tag)};
  }
  const plain = text.trim();
  if (plain.length <= 8) return {tag: plain, body: '', cls: changeTagClass(plain)};
  return null;
}

function changeTagClass(tag: string): string {
  if (/一致|相同|无变化/.test(tag)) return 'chg-tag--same';
  if (/收严|增加|细化|扩大|提高|加严/.test(tag)) return 'chg-tag--tighter';
  if (/放宽|删减|概化|缩小|删除|降低|取消/.test(tag)) return 'chg-tag--looser';
  return 'chg-tag--other';
}

const activeMainTab = ref<'indicators' | 'tests'>('indicators');

const elementStatsBar = computed(() => {
  const es = data.value?.element_stats;
  if (!es) return null;
  const nc = es.by_norm_class || {};
  return {
    total: es.total,
    source_test_count: es.source_test_count,
    target_test_count: es.target_test_count,
    items: [
      { label: '基本要素', value: nc['基本要素'] ?? 0 },
      { label: '规范类要素', value: nc['规范类要素'] ?? 0 },
      { label: '其他要素', value: nc['其他要素'] ?? 0 },
      { label: '基础要素', value: nc['基础要素'] ?? 0 },
      { label: '服务提供类要素', value: nc['服务提供类要素'] ?? 0 },
      { label: '服务评价类要素', value: nc['服务评价类要素'] ?? 0 },
    ],
  };
});

const allTests = computed(() => {
  const src = (data.value?.source_tests ?? []).map(t => ({
    ...t, _std: data.value?.source_standard_no ?? '', _std_name: data.value?.source_standard_name ?? '',
  }));
  const tgt = (data.value?.target_tests ?? []).map(t => ({
    ...t, _std: data.value?.target_standard_no ?? '', _std_name: data.value?.target_standard_name ?? '',
  }));
  return [...src, ...tgt];
});

const applicableSubject = computed(() =>
  allIndicators.value.find(i => i.applicable_object)?.applicable_object ?? ''
);

const categoryStats = computed(() => {
  const catMap: Record<string, number> = {};
  allIndicators.value.forEach(i => {
    if (i.indicator_category) catMap[i.indicator_category] = (catMap[i.indicator_category] || 0) + 1;
  });
  const total = allIndicators.value.length;
  return Object.entries(catMap)
    .sort((a, b) => b[1] - a[1])
    .map(([cat, count]) => ({cat, count, pct: total ? Math.round((count / total) * 100) : 0}));
});

const perStdStats = computed(() => {
  if (!data.value || !allIndicators.value.length) return null;
  const all = allIndicators.value;
  const normClassOrder = ['基本要素', '规范类要素', '其他要素', '基础要素', '服务提供类要素', '服务评价类要素'];

  const srcInds = all.filter(i => i.comparison_type !== 'target_only');
  const tgtInds = all.filter(i => i.comparison_type !== 'source_only');

  function buildNcItems(inds: typeof all) {
    const map: Record<string, number> = {};
    inds.forEach(i => {
      const nc = (i as any).norm_class as string;
      if (nc) map[nc] = (map[nc] || 0) + 1;
    });
    return normClassOrder.map(k => ({ label: k, value: map[k] ?? 0 }));
  }

  const es = (data.value as any).element_stats as { source_test_count?: number; target_test_count?: number } | undefined;
  const srcTestCnt = es?.source_test_count ?? (data.value.source_tests ?? []).length;
  const tgtTestCnt = es?.target_test_count ?? (data.value.target_tests ?? []).length;

  return {
    source: { total: srcInds.length, testCount: srcTestCnt, ncItems: buildNcItems(srcInds) },
    target: { total: tgtInds.length, testCount: tgtTestCnt, ncItems: buildNcItems(tgtInds) },
  };
});

const CAT_COLOR: Record<string, [string, string, string]> = {
  规格: ['#eff6ff', '#1e40af', '#bfdbfe'],
  性能: ['#f0fdf4', '#16a34a', '#86efac'],
  检测: ['#f5f3ff', '#7c3aed', '#c4b5fd'],
  材料: ['#fff7ed', '#c2410c', '#fdba74'],
  外观: ['#f0fdfa', '#0d9488', '#99f6e4'],
  安全: ['#fff1f2', '#be123c', '#fda4af'],
  配套: ['#eef2ff', '#4338ca', '#a5b4fc'],
  包装运输: ['#f9fafb', '#4b5563', '#d1d5db'],
};

function getCategoryStyle(cat: string, active = false): Record<string, string> {
  const [bg, color, border] = CAT_COLOR[cat] ?? ['#f3f4f6', '#6b7280', '#e5e7eb'];
  return active
    ? {background: bg, color, border: `1px solid ${border}`, fontWeight: '600'}
    : {background: bg, color, border: `1px solid ${border}`};
}

const indicatorStats = computed(() => {
  const all = allIndicators.value;
  const total = all.length;
  const matched = all.filter(i => i.comparison_type === 'matched').length;
  const sourceOnly = all.filter(i => i.comparison_type === 'source_only').length;
  const targetOnly = all.filter(i => i.comparison_type === 'target_only').length;
  const basicCount = all.filter(i => (i as any).norm_class === '基础类').length;
  const normCount = all.filter(i => (i as any).norm_class === '规范类').length;
  const methodCount = all.filter(i => (i as any).norm_class === '方法类').length;
  const matchedList = all.filter(i => i.comparison_type === 'matched');
  const consistent = matchedList.filter(i => (i.change_analysis || '').includes('一致')).length;
  const changed = matchedList.length - consistent;
  return {
    total, matched, sourceOnly, targetOnly,
    basicCount, normCount, methodCount,
    consistent, changed,
    matchedPct: total ? Math.round((matched / total) * 100) : 0,
    sourceOnlyPct: total ? Math.round((sourceOnly / total) * 100) : 0,
    targetOnlyPct: total ? Math.round((targetOnly / total) * 100) : 0,
    consistentPct: matched ? Math.round((consistent / matched) * 100) : 0,
    changedPct: matched ? Math.round((changed / matched) * 100) : 0,
  };
});

</script>

<template>
  <NDrawer :show="show" width="100%" @update:show="handleUpdateShow">
    <NDrawerContent title="AI 智能比对分析">
      <template #header-extra>
        <NSpace v-if="data" align="center" :size="8">
          <NTag v-if="data.relationship" type="info" size="small">{{ data.relationship }}</NTag>
          <NTag v-if="data.from_cache" size="small">缓存</NTag>
          <span v-if="data.calculation_time" class="hdr-time">{{ data.calculation_time?.toFixed(1) }}s</span>
        </NSpace>
      </template>

      <div class="cmp-content-area">
        <div v-if="loading" class="ai-loading-overlay">
          <div class="ai-loading-box">
            <div class="ai-loading-text">AI 智能对比分析中</div>
            <div class="ai-progress-track">
              <div class="ai-progress-bar"></div>
            </div>
            <div class="ai-loading-hint">正在提取并比对标准技术指标，请稍候…</div>
          </div>
        </div>
        <NEmpty v-else-if="error" :description="error">
          <template #extra>
            <NButton size="small" @click="loadData">重试</NButton>
          </template>
        </NEmpty>

        <div v-else-if="data" class="cmp-page">

          <!-- ① 标准横幅 -->
          <div class="cmp-banner">
            <div class="cmp-banner__standards">
              <div class="cmp-std cmp-std--src">
                <span class="cmp-std__badge cmp-std__badge--src">源标准</span>
                <div>
                  <div
                    class="cmp-std__no"
                    :class="{ 'cmp-std__no--link': !!stdIdMap[data.source_standard_no] }"
                    :title="stdIdMap[data.source_standard_no] ? '点击查看标准原文' : ''"
                    @click="openStdDetail(data.source_standard_no)"
                  >
                    {{ data.source_standard_no }}
                  </div>
                  <div v-if="data.source_standard_name" class="cmp-std__name">{{ data.source_standard_name }}</div>
                </div>
              </div>
              <div class="cmp-banner__arrow">
                <svg width="28" height="12" viewBox="0 0 28 12" fill="none">
                  <path d="M0 6h24M19 1l6 5-6 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </div>
              <div class="cmp-std cmp-std--tgt">
                <span class="cmp-std__badge cmp-std__badge--tgt">目标标准</span>
                <div>
                  <div
                    class="cmp-std__no"
                    :class="{ 'cmp-std__no--link': !!stdIdMap[data.target_standard_no] }"
                    :title="stdIdMap[data.target_standard_no] ? '点击查看标准原文' : ''"
                    @click="openStdDetail(data.target_standard_no)"
                  >
                    {{ data.target_standard_no }}
                  </div>
                  <div v-if="data.target_standard_name" class="cmp-std__name">{{ data.target_standard_name }}</div>
                </div>
              </div>
            </div>
            <div class="cmp-banner__stats">
              <div class="cmp-stat">
                <span class="cmp-stat__val">{{ indicatorStats.total }}</span>
                <span class="cmp-stat__lbl">总指标数</span>
              </div>
              <div class="cmp-stat__sep"></div>
              <div class="cmp-stat">
                <span class="cmp-stat__val cmp-stat__val--matched">{{ stats.matched }}</span>
                <span class="cmp-stat__lbl">共同指标</span>
              </div>
              <div class="cmp-stat__sep"></div>
              <div class="cmp-stat">
                <span class="cmp-stat__val cmp-stat__val--src">{{ stats.source_only }}</span>
                <span class="cmp-stat__lbl">源标准独有指标</span>
              </div>
              <div class="cmp-stat__sep"></div>
              <div class="cmp-stat">
                <span class="cmp-stat__val cmp-stat__val--tgt">{{ stats.target_only }}</span>
                <span class="cmp-stat__lbl">目标标准独有指标</span>
              </div>
            </div>
            <!-- 概览图表行 -->
            <div v-if="allIndicators.length" class="cmp-banner__ov">
              <div class="cmp-banner__ov-body">
                <div class="ov-section">
                  <div class="ov-section__label">匹配分布</div>
                  <div class="ov-bar-track">
                    <div :style="{ flex: indicatorStats.matched || 0.01 }" :title="`同义指标 ${indicatorStats.matched}`" class="ov-bar-seg ov-bar-seg--matched"></div>
                    <div :style="{ flex: indicatorStats.sourceOnly || 0.01 }" :title="`源标准独有指标 ${indicatorStats.sourceOnly}`" class="ov-bar-seg ov-bar-seg--src"></div>
                    <div :style="{ flex: indicatorStats.targetOnly || 0.01 }" :title="`目标标准独有指标 ${indicatorStats.targetOnly}`" class="ov-bar-seg ov-bar-seg--tgt"></div>
                  </div>
                  <div class="ov-metrics">
                    <div class="ov-metric ov-metric--matched">
                      <span class="ov-metric__val">{{ indicatorStats.matched }}</span>
                      <span class="ov-metric__sub">同义指标 {{ indicatorStats.matchedPct }}%</span>
                    </div>
                    <div class="ov-metric ov-metric--src">
                      <span class="ov-metric__val">{{ indicatorStats.sourceOnly }}</span>
                      <span class="ov-metric__sub">源标准独有指标 {{ indicatorStats.sourceOnlyPct }}%</span>
                    </div>
                    <div class="ov-metric ov-metric--tgt">
                      <span class="ov-metric__val">{{ indicatorStats.targetOnly }}</span>
                      <span class="ov-metric__sub">目标标准独有指标 {{ indicatorStats.targetOnlyPct }}%</span>
                    </div>
                  </div>
                </div>
                <!-- 同义指标变更已隐藏，匹配分布占据整行
                <div class="ov-divider"></div>
                <div class="ov-section">
                  <div class="ov-section__label">同义指标变更 <span class="ov-section__hint">（{{ indicatorStats.matched }} 项同义指标中）</span></div>
                  <div class="ov-bar-track">
                    <div :style="{ flex: indicatorStats.consistent || 0.01 }" :title="`一致 ${indicatorStats.consistent}`" class="ov-bar-seg ov-bar-seg--consistent"></div>
                    <div :style="{ flex: indicatorStats.changed || 0.01 }" :title="`有变更 ${indicatorStats.changed}`" class="ov-bar-seg ov-bar-seg--changed"></div>
                  </div>
                  <div class="ov-metrics">
                    <div class="ov-metric ov-metric--consistent">
                      <span class="ov-metric__val">{{ indicatorStats.consistent }}</span>
                      <span class="ov-metric__sub">一致 {{ indicatorStats.consistentPct }}%</span>
                    </div>
                    <div class="ov-metric ov-metric--changed">
                      <span class="ov-metric__val">{{ indicatorStats.changed }}</span>
                      <span class="ov-metric__sub">有变更 {{ indicatorStats.changedPct }}%</span>
                    </div>
                  </div>
                </div>
                -->
              </div>
              <div v-if="categoryStats.length" class="ov-cat-row">
                <span class="ov-section__label">分类分布</span>
                <div class="ov-cat-chips">
                  <button
                    v-for="item in categoryStats"
                    :key="item.cat"
                    :class="{ 'ov-cat-chip--active': filterCategory === item.cat }"
                    :style="getCategoryStyle(item.cat, filterCategory === item.cat)"
                    class="ov-cat-chip"
                    @click="filterCategory = filterCategory === item.cat ? '' : item.cat"
                  >
                    {{ item.cat }}
                    <span class="ov-cat-chip__count">{{ item.count }}</span>
                    <span class="ov-cat-chip__pct">{{ item.pct }}%</span>
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- ② 各标准要素分布（整行已隐藏，如需恢复把 v-if 改回 perStdStats） -->
          <div v-if="false" class="std-dist-card" style="order: 2">
            <!-- 源标准列 -->
            <div class="std-dist-col std-dist-col--src">
              <div class="std-dist-col__hd">
                <span class="std-dist-badge std-dist-badge--src">源标准</span>
                <span class="std-dist-no">{{ data.source_standard_no }}</span>
              </div>
              <div class="std-dist-col__body">
                <div class="std-dist-row">
                  <div class="std-dist-stat">
                    <span class="std-dist-stat__val">{{ perStdStats.source.total }}</span>
                    <span class="std-dist-stat__lbl">指标数</span>
                  </div>
                  <div v-for="item in perStdStats.source.ncItems" :key="item.label" class="std-dist-stat">
                    <span class="std-dist-stat__val std-dist-stat__val--src">{{ item.value }}</span>
                    <span class="std-dist-stat__lbl">{{ item.label }}</span>
                  </div>
                  <div v-if="perStdStats.source.testCount > 0" class="std-dist-stat">
                    <span class="std-dist-stat__val std-dist-stat__val--test">{{ perStdStats.source.testCount }}</span>
                    <span class="std-dist-stat__lbl">试验数</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="std-dist-sep"></div>

            <!-- 目标标准列 -->
            <div class="std-dist-col std-dist-col--tgt">
              <div class="std-dist-col__hd">
                <span class="std-dist-badge std-dist-badge--tgt">目标标准</span>
                <span class="std-dist-no">{{ data.target_standard_no }}</span>
              </div>
              <div class="std-dist-col__body">
                <div class="std-dist-row">
                  <div class="std-dist-stat">
                    <span class="std-dist-stat__val">{{ perStdStats.target.total }}</span>
                    <span class="std-dist-stat__lbl">指标数</span>
                  </div>
                  <div v-for="item in perStdStats.target.ncItems" :key="item.label" class="std-dist-stat">
                    <span class="std-dist-stat__val std-dist-stat__val--tgt">{{ item.value }}</span>
                    <span class="std-dist-stat__lbl">{{ item.label }}</span>
                  </div>
                  <div v-if="perStdStats.target.testCount > 0" class="std-dist-stat">
                    <span class="std-dist-stat__val std-dist-stat__val--test">{{ perStdStats.target.testCount }}</span>
                    <span class="std-dist-stat__lbl">试验数</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- ③ 综合评价 -->
          <div v-if="data.overall_assessment" class="cmp-assessment" style="order: 3">
            <div class="cmp-assessment__label">综合评价</div>
            <div class="cmp-assessment__text" v-html="assessmentHtml"></div>
          </div>

          <!-- ③ 整体情况统计（已合并至顶部 banner） -->
          <div v-if="false" class="ov-panel">
            <div class="ov-panel__hd">
              <span class="ov-panel__title">整体情况</span>
            </div>
            <div class="ov-panel__body">
              <!-- 总指标数 -->
              <!-- <div class="ov-total">
                <span class="ov-total__val">{{ indicatorStats.total }}</span>
                <span class="ov-total__lbl">总指标数</span>
              </div>
              <div class="ov-divider"></div> -->
              <!-- 匹配分布 -->
              <div class="ov-section">
                <div class="ov-section__label">匹配分布</div>
                <div class="ov-bar-track">
                  <div :style="{ flex: indicatorStats.matched || 0.01 }" :title="`同义指标 ${indicatorStats.matched}`"
                       class="ov-bar-seg ov-bar-seg--matched"></div>
                  <div :style="{ flex: indicatorStats.sourceOnly || 0.01 }"
                       :title="`源标准独有指标 ${indicatorStats.sourceOnly}`"
                       class="ov-bar-seg ov-bar-seg--src"></div>
                  <div :style="{ flex: indicatorStats.targetOnly || 0.01 }"
                       :title="`目标标准独有指标 ${indicatorStats.targetOnly}`"
                       class="ov-bar-seg ov-bar-seg--tgt"></div>
                </div>
                <div class="ov-metrics">
                  <div class="ov-metric ov-metric--matched">
                    <span class="ov-metric__val">{{ indicatorStats.matched }}</span>
                    <span class="ov-metric__sub">同义指标 {{ indicatorStats.matchedPct }}%</span>
                  </div>
                  <div class="ov-metric ov-metric--src">
                    <span class="ov-metric__val">{{ indicatorStats.sourceOnly }}</span>
                    <span class="ov-metric__sub">源标准独有指标 {{ indicatorStats.sourceOnlyPct }}%</span>
                  </div>
                  <div class="ov-metric ov-metric--tgt">
                    <span class="ov-metric__val">{{ indicatorStats.targetOnly }}</span>
                    <span class="ov-metric__sub">目标标准独有指标 {{ indicatorStats.targetOnlyPct }}%</span>
                  </div>
                </div>
              </div>

              <!-- <div class="ov-divider"></div> -->

              <!-- 规范类别（已隐藏）
              <div class="ov-section">
                <div class="ov-section__label">规范类别</div>
                <div class="ov-bar-track">
                  <div :style="{ flex: indicatorStats.basicCount || 0.01 }"
                       :title="`基础类 ${indicatorStats.basicCount}`"
                       class="ov-bar-seg ov-bar-seg--basic"></div>
                  <div :style="{ flex: indicatorStats.normCount || 0.01 }" :title="`规范类 ${indicatorStats.normCount}`"
                       class="ov-bar-seg ov-bar-seg--norm"></div>
                  <div :style="{ flex: indicatorStats.methodCount || 0.01 }"
                       :title="`方法类 ${indicatorStats.methodCount}`"
                       class="ov-bar-seg ov-bar-seg--method"></div>
                </div>
                <div class="ov-metrics">
                  <div class="ov-metric ov-metric--basic">
                    <span class="ov-metric__val">{{ indicatorStats.basicCount }}</span>
                    <span class="ov-metric__sub">基础类</span>
                  </div>
                  <div class="ov-metric ov-metric--norm">
                    <span class="ov-metric__val">{{ indicatorStats.normCount }}</span>
                    <span class="ov-metric__sub">规范类</span>
                  </div>
                  <div class="ov-metric ov-metric--method">
                    <span class="ov-metric__val">{{ indicatorStats.methodCount }}</span>
                    <span class="ov-metric__sub">方法类</span>
                  </div>
                </div>
              </div>
              -->

              <div class="ov-divider"></div>

              <!-- 同义指标变更 -->
              <div class="ov-section">
                <div class="ov-section__label">同义指标变更 <span class="ov-section__hint">（{{ indicatorStats.matched }} 项同义指标中）</span>
                </div>
                <div class="ov-bar-track">
                  <div :style="{ flex: indicatorStats.consistent || 0.01 }" :title="`一致 ${indicatorStats.consistent}`"
                       class="ov-bar-seg ov-bar-seg--consistent"></div>
                  <div :style="{ flex: indicatorStats.changed || 0.01 }" :title="`有变更 ${indicatorStats.changed}`"
                       class="ov-bar-seg ov-bar-seg--changed"></div>
                </div>
                <div class="ov-metrics">
                  <div class="ov-metric ov-metric--consistent">
                    <span class="ov-metric__val">{{ indicatorStats.consistent }}</span>
                    <span class="ov-metric__sub">一致 {{ indicatorStats.consistentPct }}%</span>
                  </div>
                  <div class="ov-metric ov-metric--changed">
                    <span class="ov-metric__val">{{ indicatorStats.changed }}</span>
                    <span class="ov-metric__sub">有变更 {{ indicatorStats.changedPct }}%</span>
                  </div>
                </div>
              </div>
            </div>

            <!-- 分类分布行 -->
            <div v-if="categoryStats.length" class="ov-cat-row">
              <span class="ov-section__label">分类分布</span>
              <div class="ov-cat-chips">
                <button
                  v-for="item in categoryStats"
                  :key="item.cat"
                  :class="{ 'ov-cat-chip--active': filterCategory === item.cat }"
                  :style="getCategoryStyle(item.cat, filterCategory === item.cat)"
                  class="ov-cat-chip"
                  @click="filterCategory = filterCategory === item.cat ? '' : item.cat"
                >
                  {{ item.cat }}
                  <span class="ov-cat-chip__count">{{ item.count }}</span>
                  <span class="ov-cat-chip__pct">{{ item.pct }}%</span>
                </button>
              </div>
            </div>
          </div>

          <!-- ⑤ 明细标签页 -->
          <div v-if="allIndicators.length || allTests.length" class="cmp-card" style="order: 4">
            <!-- Tab 栏 -->
            <div class="main-tab-bar">
              <button
                class="main-tab"
                :class="{ 'main-tab--active': activeMainTab === 'indicators' }"
                @click="activeMainTab = 'indicators'"
              >
                全量指标明细
                <span class="main-tab__count">{{ allIndicators.length }}</span>
              </button>
              <button
                class="main-tab"
                :class="{ 'main-tab--active': activeMainTab === 'tests' }"
                @click="activeMainTab = 'tests'"
              >
                全量试验明细
                <span class="main-tab__count">{{ allTests.length }}</span>
              </button>
            </div>

            <!-- Tab 1: 全量指标明细 -->
            <template v-if="activeMainTab === 'indicators'">
              <!-- 筛选栏 -->
              <div class="ind-filters">
                <div class="ind-search">
                  <svg class="ind-search__icon" viewBox="0 0 16 16" fill="none">
                    <circle cx="6.5" cy="6.5" r="4.5" stroke="currentColor" stroke-width="1.5" />
                    <path d="m10 10 3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
                  </svg>
                  <input
                    v-model="searchKeyword"
                    type="text"
                    placeholder="搜索指标名称或值…"
                    class="ind-search__input"
                  />
                  <button v-if="searchKeyword" class="ind-search__clear" @click="searchKeyword = ''">
                    <svg viewBox="0 0 16 16" fill="none">
                      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
                    </svg>
                  </button>
                </div>
                <div class="ind-fchips">
                  <button
                    v-for="opt in filterOptions"
                    :key="opt.value"
                    class="ind-fchip"
                    :class="[`ind-fchip--${opt.value || 'all'}`, { 'ind-fchip--active': filterType === opt.value }]"
                    @click="setFilterType(opt.value)"
                  >
                    {{ opt.label }}
                    <span class="ind-fchip__count">{{ getFilterCount(opt.value) }}</span>
                  </button>
                </div>
              </div>

              <!-- 统一表格 -->
              <div class="tbl-scroll">
                <table class="cmp-tbl">
                  <thead>
                    <tr>
                      <th class="th-obj">标准化对象</th>
                      <th class="th-name"><div class="th-inner"><span>指标名称</span></div></th>
                      <th class="th-src-wide">{{ data.source_standard_no }} 指标</th>
                      <th class="th-tgt-wide">{{ data.target_standard_no }} 指标</th>
                      <th class="th-change">
                        <div class="th-inner">
                          <span>变更分析</span>
                          <div class="th-filters">
                            <button
                              v-for="opt in changeTypeOptions.slice(1)"
                              :key="opt.value"
                              class="th-fchip"
                              :class="[`th-fchip--${opt.value}`, { 'th-fchip--active': filterChangeType === opt.value }]"
                              @click="setFilterChangeType(opt.value)"
                            >{{ opt.label }}</button>
                          </div>
                        </div>
                      </th>
                      <th class="th-status">状态</th>
                      <!-- <th class="th-action">试验</th> -->
                    </tr>
                  </thead>
                  <tbody v-if="filteredAllList.length">
                    <tr
                      v-for="(item, idx) in filteredAllList"
                      :key="idx"
                      class="cmp-tbl__row"
                      :class="{ 'row--changed': isChanged(item) }"
                    >
                      <td class="td-obj">
                        <span v-if="item.standard_object" class="td-obj__chip" :title="item.applicable_object ? `${item.standard_object}（适用 ${item.applicable_object}）` : item.standard_object">
                          {{ item.standard_object }}
                          <span v-if="item.applicable_object" class="td-obj__sub">（适用 {{ item.applicable_object }}）</span>
                        </span>
                        <span v-else class="val-absent">—</span>
                      </td>
                      <td>
                        <div class="td-name">
                          <span class="td-name__main" :class="{ 'td-name__main--wide-badge': ['svc-src', 'svc-eval'].includes(normClassKey(getItemNormClass(item))) }">
                            <span v-if="getItemNormClass(item)" class="ind-type-circle" :class="`ind-type-circle--${normClassKey(getItemNormClass(item))}`">{{ normClassLabel(getItemNormClass(item)) }}</span>
                            {{ item.source_indicator_object || item.source_experiment_name || item.target_indicator_object || item.target_experiment_name || '—' }}
                            <span v-if="item.indicator_category" :style="getCategoryStyle(item.indicator_category)" class="ind-cat-tag">{{ item.indicator_category }}</span>
                          </span>
                        </div>
                      </td>
                      <td :class="item.comparison_type === 'target_only' ? 'td--absent' : ''" class="td-stack">
                        <template v-if="item.comparison_type !== 'target_only'">
                          <template v-if="item.source_indicator_type === 'static'">
                            <span v-if="item.source_value" class="val-mono">{{ item.source_value }}</span>
                            <span v-if="item.source_clause" class="val-tag val-tag--link" title="点击查看原文对应章节" @click.stop="openChapter(data.source_standard_no, item.source_clause)">{{ item.source_clause }}</span>
                            <span v-if="!item.source_value" class="val-absent">—</span>
                          </template>
                          <template v-else>
                            <div v-if="item.source_input_params" class="srow"><span class="skey">条件</span><span class="sval">{{ item.source_input_params }}</span></div>
                            <div v-if="item.source_process_logic" class="srow"><span class="skey">步骤</span><span class="sval">{{ item.source_process_logic }}</span></div>
                            <div v-if="item.source_result" class="srow"><span class="skey">结果</span><span class="sval"><span class="val-mono">{{ item.source_result }}</span><span v-if="item.source_clause" class="val-tag val-tag--link" title="点击查看原文对应章节" @click.stop="openChapter(data.source_standard_no, item.source_clause)">{{ item.source_clause }}</span></span></div>
                            <span v-if="!item.source_input_params && !item.source_process_logic && !item.source_result" class="val-absent">—</span>
                          </template>
                          <NPopover
                            v-if="item.source_tests?.length"
                            :width="420"
                            placement="right"
                            scrollable
                            trigger="click"
                          >
                            <template #trigger>
                              <button class="linked-test-btn linked-test-btn--src" @click.stop>
                                <svg class="linked-test-btn__icon" fill="none" viewBox="0 0 16 16">
                                  <path d="M2 4h12M2 8h8M2 12h10" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"/>
                                </svg>
                                关联试验
                                <span class="linked-test-btn__count">{{ item.source_tests.length }}</span>
                              </button>
                            </template>
                            <div class="test-popover">
                              <div class="tm-section">
                                <div class="tm-section__hd tm-section__hd--src">
                                  {{ data?.source_standard_no }}
                                  <span class="tm-count">{{ item.source_tests.length }}</span>
                                </div>
                                <div class="tm-cards">
                                  <div v-for="(t, i) in item.source_tests" :key="i" class="tm-card">
                                    <div class="tm-card__name">{{ t.test_name || '—' }}<span v-if="t.source_clause" class="val-tag val-tag--link" title="点击查看原文对应章节" @click.stop="openChapter(data.source_standard_no, t.source_clause)">{{ t.source_clause }}</span></div>
                                    <div v-if="t.method_desc" class="tm-row"><span class="tm-key">方法</span><span class="tm-val">{{ t.method_desc }}</span></div>
                                    <div v-if="t.conditions" class="tm-row"><span class="tm-key">条件</span><span class="tm-val">{{ t.conditions }}</span></div>
                                    <div v-if="t.preparation" class="tm-row"><span class="tm-key">准备</span><span class="tm-val">{{ t.preparation }}</span></div>
                                    <div v-if="t.procedure" class="tm-row"><span class="tm-key">步骤</span><span class="tm-val">{{ t.procedure }}</span></div>
                                    <div v-if="t.acceptance" class="tm-row"><span class="tm-key">要求</span><span class="tm-val">{{ t.acceptance }}</span></div>
                                    <div v-if="t.report_items" class="tm-row"><span class="tm-key">报告</span><span class="tm-val">{{ t.report_items }}</span></div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </NPopover>
                        </template>
                        <span v-else class="val-absent">—</span>
                      </td>
                      <td :class="[item.comparison_type === 'source_only' ? 'td--absent' : '', isChanged(item) ? 'td--changed' : '']" class="td-stack">
                        <template v-if="item.comparison_type !== 'source_only'">
                          <template v-if="item.target_indicator_type === 'static'">
                            <span v-if="item.target_value" class="val-mono">{{ item.target_value }}</span>
                            <span v-if="item.target_clause" class="val-tag val-tag--link" title="点击查看原文对应章节" @click.stop="openChapter(data.target_standard_no, item.target_clause)">{{ item.target_clause }}</span>
                            <span v-if="!item.target_value" class="val-absent">—</span>
                          </template>
                          <template v-else>
                            <div v-if="item.target_input_params" class="srow"><span class="skey">条件</span><span class="sval">{{ item.target_input_params }}</span></div>
                            <div v-if="item.target_process_logic" class="srow"><span class="skey">步骤</span><span class="sval">{{ item.target_process_logic }}</span></div>
                            <div v-if="item.target_result" class="srow"><span class="skey">结果</span><span class="sval"><span class="val-mono">{{ item.target_result }}</span><span v-if="item.target_clause" class="val-tag val-tag--link" title="点击查看原文对应章节" @click.stop="openChapter(data.target_standard_no, item.target_clause)">{{ item.target_clause }}</span></span></div>
                            <span v-if="!item.target_input_params && !item.target_process_logic && !item.target_result" class="val-absent">—</span>
                          </template>
                          <NPopover
                            v-if="item.target_tests?.length"
                            :width="420"
                            placement="right"
                            scrollable
                            trigger="click"
                          >
                            <template #trigger>
                              <button class="linked-test-btn linked-test-btn--tgt" @click.stop>
                                <svg class="linked-test-btn__icon" fill="none" viewBox="0 0 16 16">
                                  <path d="M2 4h12M2 8h8M2 12h10" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"/>
                                </svg>
                                关联试验
                                <span class="linked-test-btn__count">{{ item.target_tests.length }}</span>
                              </button>
                            </template>
                            <div class="test-popover">
                              <div class="tm-section">
                                <div class="tm-section__hd tm-section__hd--tgt">
                                  {{ data?.target_standard_no }}
                                  <span class="tm-count">{{ item.target_tests.length }}</span>
                                </div>
                                <div class="tm-cards">
                                  <div v-for="(t, i) in item.target_tests" :key="i" class="tm-card">
                                    <div class="tm-card__name">{{ t.test_name || '—' }}<span v-if="t.source_clause" class="val-tag val-tag--link" title="点击查看原文对应章节" @click.stop="openChapter(data.target_standard_no, t.source_clause)">{{ t.source_clause }}</span></div>
                                    <div v-if="t.method_desc" class="tm-row"><span class="tm-key">方法</span><span class="tm-val">{{ t.method_desc }}</span></div>
                                    <div v-if="t.conditions" class="tm-row"><span class="tm-key">条件</span><span class="tm-val">{{ t.conditions }}</span></div>
                                    <div v-if="t.preparation" class="tm-row"><span class="tm-key">准备</span><span class="tm-val">{{ t.preparation }}</span></div>
                                    <div v-if="t.procedure" class="tm-row"><span class="tm-key">步骤</span><span class="tm-val">{{ t.procedure }}</span></div>
                                    <div v-if="t.acceptance" class="tm-row"><span class="tm-key">要求</span><span class="tm-val">{{ t.acceptance }}</span></div>
                                    <div v-if="t.report_items" class="tm-row"><span class="tm-key">报告</span><span class="tm-val">{{ t.report_items }}</span></div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </NPopover>
                        </template>
                        <span v-else class="val-absent">—</span>
                      </td>
                      <td class="td-change">
                        <template v-if="item.change_analysis">
                          <span v-if="splitChangeAnalysis(item.change_analysis)" class="td-change__wrap">
                            <span class="chg-tag" :class="splitChangeAnalysis(item.change_analysis)?.cls">{{ splitChangeAnalysis(item.change_analysis)?.tag }}</span>
                            <span v-if="splitChangeAnalysis(item.change_analysis)?.body" class="td-change__body">{{ splitChangeAnalysis(item.change_analysis)?.body }}</span>
                          </span>
                          <span v-else>{{ item.change_analysis }}</span>
                        </template>
                        <span v-else class="val-absent">—</span>
                      </td>
                      <td class="td-status">
                        <span class="cmp-tag" :class="`cmp-tag--${item.comparison_type}`">{{ typeLabel(item.comparison_type) }}</span>
                      </td>
                      <!-- <td class="td-action">
                        <button
                          v-if="item.source_tests?.length || item.target_tests?.length"
                          class="test-view-btn"
                          @click="openTestModal(item)"
                        >试验</button>
                        <span v-else class="val-absent">—</span>
                      </td> -->
                    </tr>
                  </tbody>
                  <tbody v-else>
                    <tr><td colspan="5" class="td-empty">{{ searchKeyword || filterType || filterNormClass || filterChangeType ? '无匹配指标' : '暂无指标数据' }}</td></tr>
                  </tbody>
                </table>
              </div>
            </template>

            <!-- Tab 2: 全量试验明细 -->
            <template v-if="activeMainTab === 'tests'">
              <div class="tbl-scroll">
                <table class="cmp-tbl test-tbl">
                  <thead>
                    <tr>
                      <th class="th-test-std">所属标准</th>
                      <th class="th-test-name">试验名称</th>
                      <th class="th-test-wide">试验方法</th>
                      <th class="th-test-wide">试验条件</th>
                      <th class="th-test-wide">试验准备</th>
                      <th class="th-test-wide">试验过程</th>
                      <th class="th-test-wide">试验要求</th>
                      <th class="th-test-wide">报告</th>
                      <th class="th-test-clause">条款</th>
                    </tr>
                  </thead>
                  <tbody v-if="allTests.length">
                    <tr v-for="(t, idx) in allTests" :key="idx" class="cmp-tbl__row">
                      <td><span class="val-tag test-std-tag">{{ t._std }}</span></td>
                      <td><span class="test-name-cell">{{ t.test_name || '—' }}</span></td>
                      <td><span class="test-text-cell">{{ t.method_desc || '—' }}</span></td>
                      <td><span class="test-text-cell">{{ t.conditions || '—' }}</span></td>
                      <td><span class="test-text-cell">{{ t.preparation || '—' }}</span></td>
                      <td><span class="test-text-cell">{{ t.procedure || '—' }}</span></td>
                      <td><span class="test-text-cell">{{ t.acceptance || '—' }}</span></td>
                      <td><span class="test-text-cell">{{ t.report_items || '—' }}</span></td>
                      <td><span class="val-tag" :class="{ 'val-tag--link': !!t.source_clause }" :title="t.source_clause ? '点击查看原文对应章节' : ''" @click.stop="t.source_clause && openChapter(t._std, t.source_clause)">{{ t.source_clause || '—' }}</span></td>
                    </tr>
                  </tbody>
                  <tbody v-else>
                    <tr><td colspan="9" class="td-empty">暂无试验明细数据</td></tr>
                  </tbody>
                </table>
              </div>
            </template>
          </div>
        </div>

        <NEmpty v-else description="暂无数据" />
      </div>
    </NDrawerContent>
  </NDrawer>

  <!-- 标准原文抽屉：点击 banner 标准号 / 指标章节号打开 -->
  <StdDetailDrawer
    v-model:show="showStdDetail"
    :standard-id="selectedStdId"
    :initial-tab="stdDetailTab"
    :chapter-no="selectedChapterNo"
  />
</template>

<style scoped>
/* ══════════════════════════════════════════
   FONTS  (Fira Code + Fira Sans — Dashboard Data pairing)
══════════════════════════════════════════ */

/* ══════════════════════════════════════════
   DESIGN TOKENS
══════════════════════════════════════════ */
.cmp-page {
  /* Source = Blue */
  --src:        #1e40af;
  --src-2:      #3b82f6;
  --src-bg:     #eff6ff;
  --src-border: #bfdbfe;
  --src-text:   #1e3a8a;
  /* Target = Teal */
  --tgt:        #0d9488;
  --tgt-2:      #14b8a6;
  --tgt-bg:     #f0fdfa;
  --tgt-border: #99f6e4;
  --tgt-text:   #0f766e;
  /* States */
  --matched:        #16a34a;
  --matched-bg:     #f0fdf4;
  --matched-border: #86efac;
  --srconly:        #7c3aed;
  --srconly-bg:     #f5f3ff;
  --srconly-border: #c4b5fd;
  --tgtonly:        #c2410c;
  --tgtonly-bg:     #fff7ed;
  --tgtonly-border: #fdba74;
  --changed: #8b1212;
  --changed-bg:     #fffbeb;
  --changed-border: #fde68a;
  /* Neutral */
  --ink:    #111827;
  --ink-2:  #374151;
  --ink-3:  #6b7280;
  --ink-4:  #9ca3af;
  --border: #e5e7eb;
  --bg:     #f8fafc;
  --white:  #ffffff;
  /* Type */
  --font-sans: 'Fira Sans', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  --font-mono: 'Fira Code', 'Consolas', monospace;
  --tbl-row-h: 40px;

  font-family: var(--font-sans);
  display: flex;
  flex-direction: column;
  gap: 16px;
  animation: cmp-in 0.25s ease both;
}

@keyframes cmp-in {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ── header time ── */
.hdr-time {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-4);
}

/* ══════════════════════════════════════════
   BANNER
══════════════════════════════════════════ */
.cmp-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 16px 24px;
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 10px;
  flex-wrap: wrap;
}

.cmp-banner__standards {
  display: flex;
  align-items: center;
  gap: 16px;
}

.cmp-std {
  display: flex;
  align-items: center;
  gap: 10px;
}

.cmp-std__badge {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  padding: 3px 7px;
  border-radius: 4px;
  flex-shrink: 0;
}
.cmp-std__badge--src { background: var(--src-bg); color: var(--src); border: 1px solid var(--src-border); }
.cmp-std__badge--tgt { background: var(--tgt-bg); color: var(--tgt); border: 1px solid var(--tgt-border); }

.cmp-std__no {
  font-size: 16px;
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: -0.01em;
}
.cmp-std--src .cmp-std__no { color: var(--src-text); }
.cmp-std--tgt .cmp-std__no { color: var(--tgt-text); }

/* 标准号可点击查看原文：仅当解析到数据库 id 时挂此类，保留源/目标原有配色 */
.cmp-std__no--link {
  display: inline-block;
  cursor: pointer;
  padding: 0 3px;
  margin: 0 -3px;
  border-radius: 3px;
  text-decoration: underline;
  text-decoration-style: dashed;
  text-decoration-thickness: 1px;
  text-underline-offset: 3px;
  transition: background 0.15s ease, text-decoration-style 0.15s ease;
}
.cmp-std__no--link:hover {
  text-decoration-style: solid;
  background: rgba(37, 99, 235, 0.1);
}

/* 指标章节号可点击：跳转到原文对应章节 */
.val-tag--link {
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
}
.val-tag--link:hover {
  color: #2563eb;
  background: #eff6ff;
  box-shadow: inset 0 0 0 1px #bfdbfe;
}

.cmp-std__name {
  font-size: 11px;
  color: var(--ink-4);
  margin-top: 1px;
}

.cmp-banner__arrow {
  color: var(--ink-4);
  flex-shrink: 0;
}

.cmp-banner__ov {
  width: 100%;
  border-top: 1px solid var(--border);
  padding-top: 14px;
  margin-top: 4px;
}

.cmp-banner__ov-body {
  display: flex;
  align-items: stretch;
  gap: 0;
}

.cmp-banner__stats {
  display: flex;
  align-items: center;
  gap: 16px;
}

.cmp-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.cmp-stat__val {
  font-family: var(--font-mono);
  font-size: 28px;
  font-weight: 600;
  line-height: 1;
}
.cmp-stat__val--matched { color: var(--matched); }
.cmp-stat__val--src     { color: var(--src); }
.cmp-stat__val--tgt     { color: var(--tgt); }

.cmp-stat__lbl {
  font-size: 11px;
  color: var(--ink-4);
  white-space: nowrap;
}

.cmp-stat__sep {
  width: 1px;
  height: 36px;
  background: var(--border);
}

/* ══════════════════════════════════════════
   ASSESSMENT
══════════════════════════════════════════ */
.cmp-assessment {
  display: flex;
  gap: 14px;
  padding: 14px 18px;
  background: var(--white);
  border: 1px solid var(--border);
  border-left: 3px solid var(--src-2);
  border-radius: 0 8px 8px 0;
}

.cmp-assessment__label {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--ink-4);
  white-space: nowrap;
  padding-top: 3px;
  flex-shrink: 0;
}

.cmp-assessment__text {
  margin: 0;
  font-size: 14px;
  line-height: 1.8;
  color: var(--ink-2);
}

/* marked 渲染产生的段落：去除默认外边距，段间留小间距 */
.cmp-assessment__text p {
  margin: 0;
}

.cmp-assessment__text p + p {
  margin-top: 8px;
}

/* 重点关注指标加粗高亮 */
.cmp-assessment__text strong {
  color: var(--ink);
  font-weight: 700;
}

/* ══════════════════════════════════════════
   CARD
══════════════════════════════════════════ */
.cmp-card {
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.cmp-card__hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  gap: 12px;
  flex-wrap: wrap;
}

.cmp-card__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: 0.01em;
}

.cmp-card__body {
  padding: 16px 20px;
}

.cmp-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.cmp-chip {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  padding: 3px 10px;
  border-radius: 20px;
  border: 1px solid;
  white-space: nowrap;
}
.cmp-chip--matched { background: var(--matched-bg); color: var(--matched); border-color: var(--matched-border); }
.cmp-chip--src     { background: var(--src-bg);     color: var(--src);     border-color: var(--src-border); }
.cmp-chip--tgt     { background: var(--tgt-bg);     color: var(--tgt);     border-color: var(--tgt-border); }

.cmp-empty {
  padding: 32px;
  text-align: center;
  font-size: 13px;
  color: var(--ink-4);
}

.td-empty {
  padding: 32px !important;
  text-align: center;
  font-size: 13px;
  color: var(--ink-4);
  background: var(--white) !important;
}

/* ══════════════════════════════════════════
   GROUP HEADER
══════════════════════════════════════════ */
.cmp-group-hd {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 20px;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
}

.cmp-group-hd--mt {
  border-top: 2px solid var(--border);
}

.cmp-group-hd__name {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ink-3);
}

.cmp-group-hd__count {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 10px;
  background: #e5e7eb;
  color: var(--ink-3);
  font-weight: 500;
}

.cmp-group-hd__legend {
  margin-left: auto;
  display: flex;
  gap: 14px;
  font-size: 11px;
  font-weight: 500;
}
.legend-src { color: var(--src); }
.legend-tgt { color: var(--tgt); }

/* ══════════════════════════════════════════
   TABLE
══════════════════════════════════════════ */
.tbl-scroll {
  overflow-x: auto;
}

.cmp-tbl {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 13px;
  font-family: var(--font-sans);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}

/* ── thead ── */
.cmp-tbl thead th {
  padding: 9px 14px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-align: left;
  white-space: nowrap;
  border-bottom: 2px solid var(--border);
  background: var(--bg);
  color: var(--ink-3);
}

.th-name { width: 180px; }
.cmp-tbl thead th.th-obj { width: 100px; }

.cmp-tbl thead th.th-src,
.cmp-tbl thead th.th-src-sub,
.cmp-tbl thead th.th-src-wide {
  color: var(--src-text);
}
.cmp-tbl thead th.th-src      { width: 130px; }
.cmp-tbl thead th.th-src-sub  { width: 90px; }
.cmp-tbl thead th.th-src-wide { width: 240px; }

.cmp-tbl thead th.th-tgt,
.cmp-tbl thead th.th-tgt-sub,
.cmp-tbl thead th.th-tgt-wide {
  color: var(--tgt-text);
}
.cmp-tbl thead th.th-tgt      { width: 130px; }
.cmp-tbl thead th.th-tgt-sub  { width: 90px; }
.cmp-tbl thead th.th-tgt-wide { width: 240px; }

.th-change { width: 200px; color: var(--changed); }
.th-status { width: 80px; white-space: nowrap; }

/* ── 表头内联筛选 ── */
.th-inner {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.th-filters {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.th-fchip {
  display: inline-flex;
  align-items: center;
  font-size: 10px;
  font-weight: 500;
  font-family: var(--font-sans);
  padding: 2px 7px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--white);
  color: var(--ink-4);
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
  line-height: 1.4;
}

.th-fchip:hover {
  border-color: #9ca3af;
  color: var(--ink-2);
}

.th-fchip--基础类.th-fchip--active {
  background: #f3f4f6;
  border-color: #9ca3af;
  color: #374151;
}

.th-fchip--规范类.th-fchip--active {
  background: var(--src-bg);
  border-color: var(--src-border);
  color: var(--src-text);
}

.th-fchip--方法类.th-fchip--active {
  background: #fff7ed;
  border-color: #fdba74;
  color: #c2410c;
}

/* th-fchip count badge */
.th-fchip__count {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 0 4px;
  border-radius: 8px;
  background: #f3f4f6;
  color: var(--ink-4);
  margin-left: 3px;
}

.th-fchip--consistent.th-fchip--active {
  background: var(--matched-bg);
  border-color: var(--matched-border);
  color: var(--matched);
}

.th-fchip--changed.th-fchip--active {
  background: var(--changed-bg);
  border-color: var(--changed-border);
  color: var(--changed);
}

.td-clause {
  font-size: 12px;
  color: var(--ink-4);
}

/* Absent cell */
.td--absent {
  background: var(--bg) !important;
}

/* Changed cell — highlight target value */
.td--changed .val-mono {
  color: var(--changed);
  font-weight: 600;
}

/* ── Cell content ── */
.td-name {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  height: 100%;
}

/* For table cells, display block */
.cmp-tbl__row td.td-name {
  vertical-align: middle;
  line-height: 1;
}

.td-name__main {
  position: relative;
  padding-left: 28px; /* 配合 ind-type-circle 标签，隐藏标签时注释此行 */
  font-weight: 600;
  color: var(--ink);
  font-size: 13px;
  display: block;
}

/* 标准化对象列 — 灰色圆角徽章，与之前的引用式样式呼应 */
.td-obj {
  font-size: 11px;
  color: var(--ink-3);
  line-height: 1.5;
  word-break: break-all;
}

.td-obj__chip {
  display: inline-block;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 11px;
  color: var(--ink-3);
  line-height: 1.5;
  max-width: 100%;
  word-break: break-all;
}

.td-obj__sub {
  font-size: 10px;
  color: var(--ink-4);
  display: block;
  margin-top: 2px;
}

.val-mono {
  font-family: var(--font-mono);
  font-size: 12px;
}
.val-clause {
  font-size: 12px;
  color: var(--ink-3);
}
.val-tag {
  display: inline-block;
  margin-left: 6px;
  font-size: 11px;
  color: var(--ink-3);
  background: #f3f4f6;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 6px;
  vertical-align: middle;
  white-space: nowrap;
  font-family: var(--font-mono);
}
.val-absent {
  color: var(--ink-4);
  font-style: italic;
  font-size: 12px;
}

.td-change {
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.55;
  max-width: 220px;
}

/* 变更类型徽标：语义配色（一致=绿 / 收严=红 / 放宽=琥珀 / 其他=紫） */
.td-change__wrap {
  display: block;
}

.chg-tag {
  display: inline-block;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid;
  margin-right: 6px;
  vertical-align: 1px;
  white-space: nowrap;
  letter-spacing: 0.02em;
}

.chg-tag--same   { background: var(--matched-bg); color: var(--matched); border-color: var(--matched-border); }
.chg-tag--tighter { background: #fff1f2; color: #be123c; border-color: #fda4af; }
.chg-tag--looser  { background: var(--changed-bg); color: #b45309; border-color: var(--changed-border); }
.chg-tag--other   { background: var(--srconly-bg); color: var(--srconly); border-color: var(--srconly-border); }

.td-change__body {
  font-size: 12px;
  color: var(--ink-3);
  line-height: 1.55;
}

.td-status {
  white-space: nowrap;
}

/* ── Status tag ── */
.cmp-tag {
  display: inline-block;
  font-family: var(--font-sans);
  font-size: 11px;
  font-weight: 600;
  padding: 3px 9px;
  border-radius: 20px;
  border: 1px solid;
  white-space: nowrap;
  letter-spacing: 0.01em;
}
.cmp-tag--matched    { background: var(--matched-bg);  color: var(--matched);  border-color: var(--matched-border); }
.cmp-tag--source_only { background: var(--srconly-bg); color: var(--srconly);  border-color: var(--srconly-border); }
.cmp-tag--target_only { background: var(--tgtonly-bg); color: var(--tgtonly);  border-color: var(--tgtonly-border); }

/* ── Dynamic stacked rows ── */
.td-stack {
  min-width: 200px;
}
.srow {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 5px;
  line-height: 1.5;
}
.srow:last-child { margin-bottom: 0; }

.skey {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  color: var(--ink-4);
  white-space: nowrap;
  flex-shrink: 0;
  letter-spacing: 0.04em;
  padding-top: 1px;
  min-width: 24px;
}
.sval {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--ink-2);
  flex: 1;
}

/* ══════════════════════════════════════════
   LLM HTML ZONE
══════════════════════════════════════════ */
.html-zone {
  font-family: var(--font-sans);
  font-size: 13px;
  line-height: 1.75;
  color: var(--ink-2);
}

.html-zone :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0 0 16px;
  font-size: 13px;
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
}

.html-zone :deep(thead th) {
  padding: 9px 14px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--ink-3);
  background: var(--bg);
  border-bottom: 2px solid var(--border);
  text-align: left;
}

.html-zone :deep(tbody td) {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
  line-height: 1.6;
}

.html-zone :deep(tbody tr:last-child td) {
  border-bottom: none;
}

.html-zone :deep(tbody tr:nth-child(even) td) {
  background: var(--bg);
}

.html-zone :deep(tbody tr:hover td) {
  background: #e8f3ff;
  transition: background 0.15s;
}

.html-zone :deep(h1),
.html-zone :deep(h2),
.html-zone :deep(h3) {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  margin: 0 0 10px;
}
.html-zone :deep(p) {
  margin: 0 0 10px;
}
.html-zone :deep(details) {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 8px;
  cursor: pointer;
}
.html-zone :deep(summary) {
  font-weight: 600;
  list-style: none;
  user-select: none;
}

/* ══════════════════════════════════════════
   TABLE ROWS
══════════════════════════════════════════ */
.cmp-tbl tbody td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
  color: var(--ink-2);
  font-size: 13px;
  overflow: hidden;
  word-break: break-all;
}

.cmp-tbl__row:last-child td {
  border-bottom: none;
}

.cmp-tbl__row:nth-child(even) td {
  background: var(--bg);
}

.cmp-tbl__row:hover td {
  background: #eef5ff;
  transition: background 0.15s;
}

.cmp-tbl__row.row--changed td:first-child {
  border-left: 3px solid var(--changed);
  padding-left: 11px;
}

/* ══════════════════════════════════════════
   FULL INDICATOR — TYPE BADGE (左上角标)
══════════════════════════════════════════ */
.ind-type-circle {
  position: absolute;
  top: 1px;
  left: 0;
  padding: 0 4px;
  height: 15px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 9px;
  font-weight: 700;
  line-height: 1;
  flex-shrink: 0;
  white-space: nowrap;
}

.ind-type-circle--spec {
  background: var(--src-bg);
  color: var(--src-text);
  border: 1px solid var(--src-border);
}

.ind-type-circle--other {
  background: #f3f4f6;
  color: #4b5563;
  border: 1px solid #d1d5db;
}

.ind-type-circle--elem {
  background: #f8fafc;
  color: #64748b;
  border: 1px solid #e2e8f0;
}

.ind-type-circle--base {
  background: #f5f3ff;
  color: #7c3aed;
  border: 1px solid #c4b5fd;
}

.ind-type-circle--svc-src {
  background: #f0fdfa;
  color: #0d9488;
  border: 1px solid #99f6e4;
}

.ind-type-circle--svc-eval {
  background: #fff7ed;
  color: #c2410c;
  border: 1px solid #fdba74;
}

.td-name__main--wide-badge {
  padding-left: 42px;
}

.ind-type-circle--svc-src,
.ind-type-circle--svc-eval {
  font-size: 8px;
  padding: 0 3px;
}

/* ══════════════════════════════════════════
   FULL INDICATOR — FILTER BAR
══════════════════════════════════════════ */
.ind-filters {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: var(--white);
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}

.ind-search {
  position: relative;
  display: flex;
  align-items: center;
  min-width: 200px;
  flex: 1;
  max-width: 320px;
}

.ind-search__icon {
  position: absolute;
  left: 9px;
  width: 14px;
  height: 14px;
  color: var(--ink-4);
  pointer-events: none;
  flex-shrink: 0;
}

.ind-search__input {
  width: 100%;
  padding: 6px 30px 6px 30px;
  font-size: 12px;
  font-family: var(--font-sans);
  color: var(--ink);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.ind-search__input::placeholder {
  color: var(--ink-4);
}

.ind-search__input:focus {
  border-color: var(--src-2);
  box-shadow: 0 0 0 3px #dbeafe;
  background: var(--white);
}

.ind-search__clear {
  position: absolute;
  right: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--ink-4);
  padding: 0;
}

.ind-search__clear svg {
  width: 12px;
  height: 12px;
}

.ind-search__clear:hover {
  color: var(--ink-2);
}

.ind-fchips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.ind-fchip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 500;
  padding: 4px 10px;
  border-radius: 20px;
  border: 1px solid var(--border);
  background: var(--white);
  color: var(--ink-3);
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.ind-fchip:hover {
  border-color: #9ca3af;
  color: var(--ink-2);
}

.ind-fchip__count {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 0 5px;
  border-radius: 8px;
  background: #f3f4f6;
  color: var(--ink-4);
}


/* Active states */
.ind-fchip--all.ind-fchip--active,
.ind-fchip--alltype.ind-fchip--active,
.ind-fchip--allchange.ind-fchip--active {
  background: var(--ink);
  border-color: var(--ink);
  color: var(--white);
}

.ind-fchip--all.ind-fchip--active .ind-fchip__count,
.ind-fchip--alltype.ind-fchip--active .ind-fchip__count,
.ind-fchip--allchange.ind-fchip--active .ind-fchip__count {
  background: rgba(255, 255, 255, 0.2);
  color: var(--white);
}

.ind-fchip--matched.ind-fchip--active {
  background: var(--matched-bg);
  border-color: var(--matched-border);
  color: var(--matched);
}

.ind-fchip--matched.ind-fchip--active .ind-fchip__count {
  background: var(--matched-border);
  color: var(--matched);
}

.ind-fchip--source_only.ind-fchip--active {
  background: var(--srconly-bg);
  border-color: var(--srconly-border);
  color: var(--srconly);
}

.ind-fchip--source_only.ind-fchip--active .ind-fchip__count {
  background: var(--srconly-border);
  color: var(--srconly);
}

.ind-fchip--target_only.ind-fchip--active {
  background: var(--tgtonly-bg);
  border-color: var(--tgtonly-border);
  color: var(--tgtonly);
}

.ind-fchip--target_only.ind-fchip--active .ind-fchip__count {
  background: var(--tgtonly-border);
  color: var(--tgtonly);
}

.ind-fchip--基础类.ind-fchip--active {
  background: #f3f4f6;
  border-color: #9ca3af;
  color: #374151;
}

.ind-fchip--基础类.ind-fchip--active .ind-fchip__count {
  background: #d1d5db;
  color: #374151;
}

.ind-fchip--规范类.ind-fchip--active {
  background: var(--src-bg);
  border-color: var(--src-border);
  color: var(--src-text);
}

.ind-fchip--规范类.ind-fchip--active .ind-fchip__count {
  background: var(--src-border);
  color: var(--src-text);
}

.ind-fchip--方法类.ind-fchip--active {
  background: #fff7ed;
  border-color: #fdba74;
  color: #c2410c;
}

.ind-fchip--方法类.ind-fchip--active .ind-fchip__count {
  background: #fdba74;
  color: #c2410c;
}

.ind-fchip--consistent.ind-fchip--active {
  background: var(--matched-bg);
  border-color: var(--matched-border);
  color: var(--matched);
}

.ind-fchip--consistent.ind-fchip--active .ind-fchip__count {
  background: var(--matched-border);
  color: var(--matched);
}

.ind-fchip--changed.ind-fchip--active {
  background: var(--changed-bg);
  border-color: var(--changed-border);
  color: var(--changed);
}

.ind-fchip--changed.ind-fchip--active .ind-fchip__count {
  background: var(--changed-border);
  color: var(--changed);
}

/* ══════════════════════════════════════════
   OVERVIEW PANEL
══════════════════════════════════════════ */
.ov-panel {
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.ov-panel__hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}

.ov-panel__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: 0.01em;
}

.ov-panel__total {
  font-size: 12px;
  color: var(--ink-4);
}

.ov-panel__total b {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
  color: var(--ink-2);
}

.ov-panel__body {
  display: flex;
  align-items: stretch;
  gap: 0;
  padding: 16px 20px;
}

.ov-divider {
  width: 1px;
  background: var(--border);
  margin: 0 20px;
  flex-shrink: 0;
}

.ov-total {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 64px;
  flex-shrink: 0;
}

.ov-total__val {
  font-family: var(--font-mono);
  font-size: 36px;
  font-weight: 600;
  line-height: 1;
  color: var(--ink-2);
}

.ov-total__lbl {
  font-size: 11px;
  color: var(--ink-4);
  white-space: nowrap;
}

.ov-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.ov-section__label {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ink-4);
}

.ov-section__hint {
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
  color: var(--ink-4);
}

.ov-bar-track {
  display: flex;
  height: 8px;
  border-radius: 4px;
  overflow: hidden;
  gap: 2px;
}

.ov-bar-seg {
  border-radius: 4px;
  min-width: 4px;
  transition: opacity 0.15s;
}

.ov-bar-seg:hover {
  opacity: 0.75;
}

.ov-bar-seg--matched {
  background: var(--matched);
}

.ov-bar-seg--src {
  background: var(--src-2);
}

.ov-bar-seg--tgt {
  background: var(--tgt-2);
}

.ov-bar-seg--basic {
  background: #9ca3af;
}

.ov-bar-seg--norm {
  background: var(--src-2);
}

.ov-bar-seg--method {
  background: #f97316;
}

.ov-bar-seg--consistent {
  background: var(--matched);
}

.ov-bar-seg--changed {
  background: #f59e0b;
}

.ov-metrics {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.ov-metric {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.ov-metric__val {
  font-family: var(--font-mono);
  font-size: 22px;
  font-weight: 600;
  line-height: 1;
}

.ov-metric__sub {
  font-size: 11px;
  color: var(--ink-4);
  white-space: nowrap;
}

.ov-metric--matched .ov-metric__val {
  color: var(--matched);
}

.ov-metric--src .ov-metric__val {
  color: var(--src);
}

.ov-metric--tgt .ov-metric__val {
  color: var(--tgt);
}

.ov-metric--basic .ov-metric__val {
  color: #6b7280;
}

.ov-metric--norm .ov-metric__val {
  color: var(--src-2);
}

.ov-metric--method .ov-metric__val {
  color: #f97316;
}

.ov-metric--consistent .ov-metric__val {
  color: var(--matched);
}

.ov-metric--changed .ov-metric__val {
  color: #d97706;
}

/* ── 分类分布行 ── */
.ov-cat-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px 14px;
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
}

.ov-cat-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.ov-cat-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 500;
  font-family: var(--font-sans);
  padding: 3px 9px;
  border-radius: 20px;
  cursor: pointer;
  transition: opacity 0.15s, box-shadow 0.15s;
  white-space: nowrap;
}

.ov-cat-chip:hover {
  opacity: 0.8;
}

.ov-cat-chip--active {
  box-shadow: 0 0 0 2px currentColor;
}

.ov-cat-chip__count {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
}

.ov-cat-chip__pct {
  font-size: 10px;
  opacity: 0.7;
}

/* ── 表格名称列：分类标签 ── */
.ind-cat-tag {
  display: inline-block;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: 3px;
  vertical-align: middle;
  margin-left: 4px;
  white-space: nowrap;
  letter-spacing: 0.02em;
}

/* ══════════════════════════════════════════
   AI LOADING OVERLAY
══════════════════════════════════════════ */
.ai-loading-overlay {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  padding: 60px 20px;
}

.ai-loading-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  width: 320px;
}

.ai-loading-text {
  font-size: 17px;
  font-weight: 600;
  color: #1e40af;
  letter-spacing: 0.06em;
  animation: ai-text-pulse 2s ease-in-out infinite;
}

@keyframes ai-text-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.45; }
}

.ai-progress-track {
  width: 100%;
  height: 4px;
  background: #dbeafe;
  border-radius: 2px;
  overflow: hidden;
  position: relative;
}

.ai-progress-bar {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  width: 45%;
  background: linear-gradient(90deg, #93c5fd, #3b82f6, #1d4ed8);
  border-radius: 2px;
  animation: ai-bar-slide 1.6s cubic-bezier(0.4, 0, 0.2, 1) infinite;
}

@keyframes ai-bar-slide {
  0%   { left: -45%; }
  100% { left: 100%; }
}

.ai-loading-hint {
  font-size: 12px;
  color: #9ca3af;
  text-align: center;
  letter-spacing: 0.01em;
}

/* ══════════════════════════════════════════
   PER-STANDARD ELEMENT DISTRIBUTION CARD
══════════════════════════════════════════ */
.std-dist-card {
  display: flex;
  align-items: stretch;
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.std-dist-sep {
  width: 1px;
  background: var(--border);
  flex-shrink: 0;
}

.std-dist-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.std-dist-col__hd {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  border-bottom: 1px solid var(--border);
}

.std-dist-col--src .std-dist-col__hd { background: var(--src-bg); }
.std-dist-col--tgt .std-dist-col__hd { background: var(--tgt-bg); }

.std-dist-col__body {
  flex: 1;
}

.std-dist-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  padding: 4px 0;
}

.std-dist-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 10px 16px;
}

.std-dist-stat__val {
  font-family: var(--font-mono);
  font-size: 22px;
  font-weight: 700;
  line-height: 1;
  color: var(--ink-2);
}

.std-dist-stat__val--src  { color: var(--src); }
.std-dist-stat__val--tgt  { color: var(--tgt); }
.std-dist-stat__val--test { color: var(--tgt); }

.std-dist-stat__lbl {
  font-size: 11px;
  color: var(--ink-4);
  white-space: nowrap;
}

.std-dist-stat + .std-dist-stat {
  border-left: 1px solid var(--border);
}

.std-dist-badge {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 2px 7px;
  border-radius: 4px;
  flex-shrink: 0;
}

.std-dist-badge--src {
  background: var(--src-bg);
  color: var(--src);
  border: 1px solid var(--src-border);
}

.std-dist-badge--tgt {
  background: var(--tgt-bg);
  color: var(--tgt);
  border: 1px solid var(--tgt-border);
}

.std-dist-no {
  font-size: 12px;
  color: var(--ink-2);
  font-family: var(--font-mono);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}


/* ══════════════════════════════════════════
   MAIN TAB BAR
══════════════════════════════════════════ */
.main-tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border);
  padding: 0 20px;
  background: var(--bg);
}

.main-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 11px 16px;
  font-size: 13px;
  font-weight: 500;
  font-family: var(--font-sans);
  color: var(--ink-3);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
  white-space: nowrap;
  margin-bottom: -1px;
}

.main-tab:hover {
  color: var(--ink-2);
}

.main-tab--active {
  color: var(--src);
  border-bottom-color: var(--src);
  font-weight: 600;
}

.main-tab__count {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 10px;
  background: #e5e7eb;
  color: var(--ink-3);
  font-weight: 500;
}

.main-tab--active .main-tab__count {
  background: var(--src-bg);
  color: var(--src);
}

/* ══════════════════════════════════════════
   TEST TABLE
══════════════════════════════════════════ */
.test-tbl .th-test-std   { width: 110px; }
.test-tbl .th-test-name  { width: 130px; font-weight: 700; }
.test-tbl .th-test-wide  { min-width: 160px; }
.test-tbl .th-test-clause { width: 70px; }

.test-std-tag {
  font-size: 10px;
  padding: 2px 5px;
  white-space: normal;
  word-break: break-all;
  display: inline-block;
}

.test-name-cell {
  font-weight: 600;
  color: var(--ink);
  font-size: 13px;
}

.test-text-cell {
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.6;
  display: block;
  white-space: pre-wrap;
  max-height: 120px;
  overflow-y: auto;
}

/* ══════════════════════════════════════════
   关联试验按钮（Popover 触发）
══════════════════════════════════════════ */
.linked-test-btn {
  display: flex;
  width: fit-content;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s, transform 0.15s;
  font-family: var(--font-sans);
  white-space: nowrap;
}
.linked-test-btn:hover { transform: translateY(-1px); }

/* 原指标列 = 蓝（--src），目标指标列 = 青（--tgt） */
.linked-test-btn--src { color: var(--src); background: var(--src-bg); border-color: var(--src-border); }
.linked-test-btn--src:hover { background: #dbeafe; }
.linked-test-btn--tgt { color: var(--tgt); background: var(--tgt-bg); border-color: var(--tgt-border); }
.linked-test-btn--tgt:hover { background: #ccfbf1; }

.linked-test-btn__icon {
  width: 11px;
  height: 11px;
  flex-shrink: 0;
}

.linked-test-btn__count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  color: #fff;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
}
.linked-test-btn--src .linked-test-btn__count { background: var(--src); }
.linked-test-btn--tgt .linked-test-btn__count { background: var(--tgt); }

.test-popover {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 4px 0;
  max-height: 480px;
  overflow-y: auto;
}

/* ══════════════════════════════════════════
   试验明细（Popover 内容）
══════════════════════════════════════════ */
.tm-body {
  display: flex;
  flex-direction: column;
  gap: 0;
  font-family: var(--font-sans, sans-serif);
}

.tm-divider {
  height: 1px;
  background: #e5e7eb;
  margin: 16px 0;
}

.tm-section { display: flex; flex-direction: column; gap: 10px; }

.tm-section__hd {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding-bottom: 6px;
  border-bottom: 2px solid;
}
.tm-section__hd--src { color: #1e40af; border-color: #bfdbfe; }
.tm-section__hd--tgt { color: #0d9488; border-color: #99f6e4; }

.tm-count {
  font-family: 'Fira Code', monospace;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 10px;
  background: #f3f4f6;
  color: #6b7280;
  font-weight: 500;
}

.tm-cards { display: flex; flex-direction: column; gap: 10px; }

.tm-card {
  padding: 12px 14px;
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tm-card__name {
  font-size: 13px;
  font-weight: 700;
  color: #111827;
  display: flex;
  align-items: center;
  gap: 6px;
}

.tm-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 12px;
  line-height: 1.6;
}

.tm-key {
  flex-shrink: 0;
  min-width: 28px;
  font-size: 10px;
  font-weight: 700;
  color: #9ca3af;
  letter-spacing: 0.04em;
  padding-top: 2px;
  text-align: right;
}

.tm-val {
  color: #374151;
  flex: 1;
  white-space: pre-wrap;
  word-break: break-all;
}

.tm-empty {
  font-size: 12px;
  color: #9ca3af;
  font-style: italic;
  padding: 12px 0;
}
</style>
