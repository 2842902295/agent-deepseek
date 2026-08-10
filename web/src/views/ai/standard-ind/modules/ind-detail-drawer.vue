<script lang="ts" setup>
import {computed, onMounted, ref, watch} from 'vue';
import {NButton, NDrawer, NDrawerContent, NEmpty, NPopover, NSpin} from 'naive-ui';
import {fetchIndTaxonomy, fetchStandardIndDetail, fetchStandardObjIndicators, fetchStandardTests} from '@/service/api';

// ── 分类体系枚举（从后端 mind_map parser 获取）──────────────────────────────────
const taxonomy = ref<Api.AI.IndTaxonomy | null>(null);
onMounted(async () => {
  const resp = await fetchIndTaxonomy();
  if (resp.data) taxonomy.value = resp.data;
});

const PALETTE_COLOR = [
  '#16a34a', '#1e40af', '#7c3aed', '#0d9488', '#c2410c', '#059669',
  '#6b7280', '#475569', '#64748b', '#4b5563', '#374151', '#1f2937',
  '#1d4ed8', '#4338ca', '#15803d', '#0891b2', '#b45309', '#b91c1c',
  '#be123c', '#6d28d9', '#047857', '#be185d', '#0f766e', '#92400e',
];
const PALETTE_BG = [
  '#f0fdf4', '#eff6ff', '#f5f3ff', '#f0fdfa', '#fff7ed', '#ecfdf5',
  '#f9fafb', '#f8fafc', '#f8fafc', '#f9fafb', '#f3f4f6', '#f3f4f6',
  '#eff6ff', '#eef2ff', '#f0fdf4', '#ecfeff', '#fffbeb', '#fff1f2',
  '#fff1f2', '#f5f3ff', '#ecfdf5', '#fdf2f8', '#f0fdfa', '#fffbeb',
];

// [bg, color, border]
const catColorMap = computed<Record<string, [string, string, string]>>(() => {
  const map: Record<string, [string, string, string]> = {};
  (taxonomy.value?.all_categories ?? []).forEach((cat, i) => {
    const color = PALETTE_COLOR[i % PALETTE_COLOR.length];
    const bg = PALETTE_BG[i % PALETTE_BG.length];
    map[cat] = [bg, color, bg];
  });
  return map;
});

const normClassOptions = computed(() => [
  {value: '', label: '全部'},
  ...(taxonomy.value?.norm_classes ?? []).map(v => ({value: v, label: v})),
]);

const props = defineProps<{
  show: boolean;
  standardNo: string;
  standardName?: string;
  runId?: string;
  standardObject?: string;
}>();

const emit = defineEmits<{
  'update:show': [value: boolean];
}>();

const loading = ref(false);
const detail = ref<Api.AI.StandardIndDetailResponse | null>(null);
const tests = ref<Api.AI.StandardTestItem[]>([]);
const standardStructureType = ref('');
const activeTab = ref<'indicators' | 'tests'>('indicators');
const error = ref<string | null>(null);

async function loadData() {
  loading.value = true;
  error.value = null;
  detail.value = null;
  tests.value = [];
  standardStructureType.value = '';
  activeTab.value = 'indicators';

  try {
    if (props.standardObject) {
      // 标准化对象模式：一次请求拿全部
      const resp = await fetchStandardObjIndicators(props.standardObject);
      if (resp.data) {
        detail.value = resp.data;
        tests.value = resp.data.tests || [];
        standardStructureType.value = resp.data.standard_structure_type || '';
      } else {
        error.value = (resp as any).msg || '加载失败';
      }
    } else {
      if (!props.standardNo) return;
      const [indResp, testResp] = await Promise.all([
        fetchStandardIndDetail(props.standardNo, props.runId),
        fetchStandardTests(props.standardNo, props.runId),
      ]);
      if (indResp.data) {
        detail.value = indResp.data;
        standardStructureType.value = indResp.data.standard_structure_type || '';
      } else {
        error.value = (indResp as any).msg || '加载失败';
      }
      if (testResp.data) {
        tests.value = testResp.data.tests || [];
        if (!standardStructureType.value && testResp.data.tests?.length) {
          standardStructureType.value = 'has_test_only';
        }
      }
    }
    if (indicators.value.length > 0) {
      activeTab.value = 'indicators';
    } else if (tests.value.length > 0) {
      activeTab.value = 'tests';
    } else {
      activeTab.value = 'indicators';
    }
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
      detail.value = null;
      tests.value = [];
      standardStructureType.value = '';
      error.value = null;
      filterObjectType.value = '';
      filterNormClass.value = '';
      filterCategory.value = '';
      selectedTreeNode.value = 'root';
    }
  },
);

function handleUpdateShow(value: boolean) {
  emit('update:show', value);
}

const indicators = computed(() => detail.value?.indicators ?? []);

const objectType = computed(() => indicators.value.find(i => i.object_type)?.object_type ?? '');

const applicableObjects = computed(() => {
  const seen = new Set<string>();
  return indicators.value
    .map(i => i.applicable_object)
    .filter(v => v && !seen.has(v) && seen.add(v));
});

// 筛选
const filterNormClass = ref('');
const filterCategory = ref('');
const filterObjectType = ref('');
const searchKeyword = ref('');

const filteredList = computed(() => {
  let list = indicators.value;
  if (filterObjectType.value) list = list.filter(i => i.object_type === filterObjectType.value);
  if (filterNormClass.value) list = list.filter(i => i.norm_class === filterNormClass.value);
  if (filterCategory.value) list = list.filter(i => i.indicator_category === filterCategory.value);
  if (searchKeyword.value.trim()) {
    const kw = searchKeyword.value.trim().toLowerCase();
    list = list.filter(
      i =>
        (i.indicator_object || '').toLowerCase().includes(kw) ||
        (i.standard_object || '').toLowerCase().includes(kw) ||
        (i.applicable_object || '').toLowerCase().includes(kw) ||
        (i.indicator_category || '').toLowerCase().includes(kw) ||
        (i.source_value || '').toLowerCase().includes(kw) ||
        (i.source_clause || '').toLowerCase().includes(kw),
    );
  }
  return list;
});

const stats = computed(() => {
  const all = indicators.value;
  const total = all.length;
  const normClassCounts: Record<string, number> = {};
  all.forEach(i => {
    if (i.norm_class) normClassCounts[i.norm_class] = (normClassCounts[i.norm_class] ?? 0) + 1;
  });
  return {total, normClassCounts, testCount: tests.value.length};
});

// 试验筛选
const filterTestCategory = ref('');
const searchTestKeyword = ref('');
const filteredTests = computed(() => {
  let list = tests.value;
  if (filterObjectType.value) list = list.filter(t => t.object_type === filterObjectType.value);
  if (filterNormClass.value) {
    const ncCats = visibleTree.value
      .find(ot => ot.filterOt === filterObjectType.value)?.children
      ?.find(nc => nc.filterNc === filterNormClass.value)?.children?.map(c => c.label) ?? [];
    if (ncCats.length) list = list.filter(t => ncCats.includes(t.indicator_category));
  }
  if (filterCategory.value) list = list.filter(t => t.indicator_category === filterCategory.value);
  if (filterTestCategory.value) list = list.filter(t => t.indicator_category === filterTestCategory.value);
  if (searchTestKeyword.value.trim()) {
    const kw = searchTestKeyword.value.trim().toLowerCase();
    list = list.filter(
      t =>
        t.test_name.toLowerCase().includes(kw) ||
        (t.conditions || '').toLowerCase().includes(kw) ||
        (t.procedure || '').toLowerCase().includes(kw) ||
        (t.standard_object || '').toLowerCase().includes(kw),
    );
  }
  return list;
});

function getCategoryStyle(cat: string, active = false): Record<string, string> {
  const [bg, color] = catColorMap.value[cat] ?? ['#f3f4f6', '#6b7280'];
  const border = bg;
  return active
    ? {background: bg, color, border: `1px solid ${border}`, fontWeight: '600'}
    : {background: bg, color, border: `1px solid ${border}`};
}

// 三层嵌套分布：对象类型 → norm_class → indicator_category
const distributionTree = computed(() => {
  // { objType: { normClass: { total, cats: { cat: count } } } }
  const tree: Record<string, {
    total: number;
    normClasses: Record<string, { total: number; cats: Record<string, number> }>
  }> = {};
  indicators.value.forEach(i => {
    const ot = i.standard_object || '未分类';
    const nc = i.norm_class || '未分类';
    const cat = i.indicator_category || '';
    if (!tree[ot]) tree[ot] = {total: 0, normClasses: {}};
    tree[ot].total++;
    if (!tree[ot].normClasses[nc]) tree[ot].normClasses[nc] = {total: 0, cats: {}};
    tree[ot].normClasses[nc].total++;
    if (cat) tree[ot].normClasses[nc].cats[cat] = (tree[ot].normClasses[nc].cats[cat] ?? 0) + 1;
  });

  const normClassOrder = taxonomy.value?.norm_classes ?? [];

  return Object.entries(tree)
    .sort((a, b) => b[1].total - a[1].total)
    .map(([ot, v]) => {
      const ncEntries = Object.entries(v.normClasses)
        .sort((a, b) => {
          const ai = normClassOrder.indexOf(a[0]);
          const bi = normClassOrder.indexOf(b[0]);
          if (ai === -1 && bi === -1) return b[1].total - a[1].total;
          if (ai === -1) return 1;
          if (bi === -1) return -1;
          return ai - bi;
        })
        .map(([nc, nv]) => ({
          normClass: nc,
          total: nv.total,
          cats: Object.entries(nv.cats).sort((a, b) => b[1] - a[1]).map(([cat, count]) => ({cat, count})),
        }));
      return {objectType: ot, total: v.total, normClasses: ncEntries};
    });
});

const NC_COLORS = ['#1e40af', '#0d9488', '#7c3aed', '#c2410c', '#16a34a', '#b45309'];
const NC_STROKES = ['#3b82f6', '#14b8a6', '#8b5cf6', '#f97316', '#22c55e', '#f59e0b'];
const NC_BGS = ['#eff6ff', '#f0fdfa', '#f5f3ff', '#fff7ed', '#f0fdf4', '#fffbeb'];

function normClassCircleStyle(nc: string | undefined): Record<string, string> {
  const idx = (taxonomy.value?.norm_classes ?? []).indexOf(nc ?? '');
  const i = idx >= 0 ? idx : 0;
  return {
    background: NC_BGS[i % NC_BGS.length],
    color: NC_COLORS[i % NC_COLORS.length],
    border: `1px solid ${NC_BGS[i % NC_BGS.length]}`
  };
}

// 环形图数据
const hoveredNcIndex = ref<number | null>(null);
const selectedObjectType = ref('');

watch(distributionTree, tree => {
  if (!tree.find(g => g.objectType === selectedObjectType.value)) {
    selectedObjectType.value = tree[0]?.objectType ?? '';
    hoveredNcIndex.value = null;
  }
});

const activeObjectGroup = computed(() =>
  distributionTree.value.find(g => g.objectType === selectedObjectType.value)
  ?? distributionTree.value[0]
  ?? null
);

const donutSegments = computed(() => {
  const group = activeObjectGroup.value;
  if (!group || !group.total) return [];
  const total = group.total;
  const ncList = taxonomy.value?.norm_classes ?? [];
  const entries = [...group.normClasses]
    .sort((a, b) => {
      const ai = ncList.indexOf(a.normClass), bi = ncList.indexOf(b.normClass);
      if (ai === -1 && bi === -1) return b.total - a.total;
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    })
    .map(nc => [nc.normClass, nc.total] as [string, number]);

  const R = 54, r = 34, cx = 70, cy = 70;
  const GAP = 0.03;
  let angle = -Math.PI / 2;
  return entries.map(([nc, count], i) => {
    const sweep = (count / total) * (Math.PI * 2) - GAP;
    const a1 = angle + GAP / 2, a2 = angle + GAP / 2 + sweep;
    const x1 = cx + R * Math.cos(a1), y1 = cy + R * Math.sin(a1);
    const x2 = cx + R * Math.cos(a2), y2 = cy + R * Math.sin(a2);
    const ix1 = cx + r * Math.cos(a2), iy1 = cy + r * Math.sin(a2);
    const ix2 = cx + r * Math.cos(a1), iy2 = cy + r * Math.sin(a1);
    const large = sweep > Math.PI ? 1 : 0;
    const d = `M ${x1} ${y1} A ${R} ${R} 0 ${large} 1 ${x2} ${y2} L ${ix1} ${iy1} A ${r} ${r} 0 ${large} 0 ${ix2} ${iy2} Z`;
    const ncIdx = ncList.indexOf(nc);
    const colorIdx = ncIdx >= 0 ? ncIdx : i;
    angle += sweep + GAP;
    return {
      nc,
      count,
      d,
      color: NC_STROKES[colorIdx % NC_STROKES.length],
      bg: NC_BGS[colorIdx % NC_BGS.length],
      pct: Math.round(count / total * 100),
      idx: i
    };
  });
});

// ── 分类体系树 ──────────────────────────────────────────────────────────
const TREE_TEMPLATE: Record<string, Record<string, string[]>> = {
  '产品类对象': {
    '基本要素': ['术语', '分类、编码', '图形、符号', '其他'],
    '规范类要素': ['标识', '外在特性', '感官', '性能', '功能', '物质含量', '其他'],
    '其他要素': ['包装', '运输', '贮存', '其他'],
  },
  '服务类对象': {
    '基础要素': ['术语', '分类', '标识与符号'],
    '服务提供类要素': ['提供者', '人员', '环境', '设施设备', '用品', '合同', '安全与应急', '提供过程', '结果'],
    '服务评价类要素': ['顾客满意度', '分类等级', '质量评价'],
  },
};

interface VisibleTreeNode {
  key: string;
  label: string;
  count: number;
  level: 'objectType' | 'normClass' | 'category';
  filterOt: string;
  filterNc: string;
  filterCat: string;
  children?: VisibleTreeNode[];
}

const visibleTree = computed<VisibleTreeNode[]>(() => {
  const all = indicators.value;
  if (!all.length) return [];
  const objectTypes = [...new Set(all.map(i => i.object_type).filter(Boolean))];
  return objectTypes.map(ot => {
    const otList = all.filter(i => i.object_type === ot);
    const tmpl = TREE_TEMPLATE[ot] || {};
    const normClasses = [...new Set(otList.map(i => i.norm_class).filter(Boolean))];
    const orderedNc = Object.keys(tmpl).filter(nc => normClasses.includes(nc));
    const extraNc = normClasses.filter(nc => !Object.keys(tmpl).includes(nc));
    const allNc = [...orderedNc, ...extraNc];
    const children = allNc.map(nc => {
      const ncList = otList.filter(i => i.norm_class === nc);
      const catTmpl = tmpl[nc] || [];
      const cats = [...new Set(ncList.map(i => i.indicator_category).filter(Boolean))];
      const orderedCats = catTmpl.filter(c => cats.includes(c));
      const extraCats = cats.filter(c => !catTmpl.includes(c));
      const allCats = [...orderedCats, ...extraCats];
      const catChildren = allCats.map(cat => ({
        key: `${ot}|${nc}|${cat}`,
        label: cat,
        count: ncList.filter(i => i.indicator_category === cat).length,
        level: 'category' as const,
        filterOt: ot,
        filterNc: nc,
        filterCat: cat,
      }));
      return {
        key: `${ot}|${nc}`,
        label: nc,
        count: ncList.length,
        level: 'normClass' as const,
        filterOt: ot,
        filterNc: nc,
        filterCat: '',
        children: catChildren,
      };
    });
    return {
      key: ot,
      label: ot,
      count: otList.length,
      level: 'objectType' as const,
      filterOt: ot,
      filterNc: '',
      filterCat: '',
      children,
    };
  });
});

const selectedTreeNode = ref('root');
const expandedTreeNodes = ref(new Set(['root']));

watch(visibleTree, tree => {
  const expanded = new Set(['root']);
  tree.forEach(ot => {
    expanded.add(ot.key);
    ot.children?.forEach(nc => expanded.add(nc.key));
  });
  expandedTreeNodes.value = expanded;
  selectedTreeNode.value = 'root';
});

function handleTreeRootClick() {
  selectedTreeNode.value = 'root';
  filterObjectType.value = '';
  filterNormClass.value = '';
  filterCategory.value = '';
}

function handleTreeNodeClick(node: VisibleTreeNode) {
  selectedTreeNode.value = node.key;
  filterObjectType.value = node.filterOt;
  filterNormClass.value = node.filterNc;
  filterCategory.value = node.filterCat;
}

function toggleTreeExpand(key: string) {
  const s = new Set(expandedTreeNodes.value);
  if (s.has(key)) s.delete(key); else s.add(key);
  expandedTreeNodes.value = s;
}
</script>

<template>
  <NDrawer :show="show" width="100%" @update:show="handleUpdateShow">
    <NDrawerContent
      :title="props.standardObject ? `标准化对象详情 — ${props.standardObject}` : `标准指标详情 — ${props.standardNo}`"
      closable>
      <NSpin :show="loading">
        <NEmpty v-if="error && !loading" :description="error">
          <template #extra>
            <NButton size="small" @click="loadData">重试</NButton>
          </template>
        </NEmpty>

        <div v-else-if="detail" class="ind-page">

	          <!-- 上半区：树形图 + 信息 -->
	          <div class="ind-upper">
	            <!-- 左：分类体系树 -->
	            <div v-if="visibleTree.length" class="ind-tree-panel">
	              <div class="ind-tree-header">分类体系</div>
	              <div class="ind-tree-body">
	                <div
	                  class="tree-node tree-node--root"
	                  :class="{ 'tree-node--selected': selectedTreeNode === 'root' }"
	                  @click="handleTreeRootClick"
	                >
	                  <span
	                    class="tree-node__arrow"
	                    :class="{ 'tree-node__arrow--expanded': expandedTreeNodes.has('root') }"
	                    @click.stop="toggleTreeExpand('root')"
	                  >▶</span>
	                  标准化对象
	                  <span class="tree-node__count">{{ stats.total }}</span>
	                </div>
	                <template v-for="otNode in visibleTree" :key="otNode.key">
	                  <div
	                    v-show="expandedTreeNodes.has('root')"
	                    class="tree-node tree-node--ot"
	                    :class="{ 'tree-node--selected': selectedTreeNode === otNode.key }"
	                    @click="handleTreeNodeClick(otNode)"
	                  >
	                    <span
	                      class="tree-node__arrow"
	                      :class="{ 'tree-node__arrow--expanded': expandedTreeNodes.has(otNode.key) }"
	                      @click.stop="toggleTreeExpand(otNode.key)"
	                    >▶</span>
	                    <span class="tree-node__dot" :style="{ background: otNode.label === '产品类对象' ? '#1e40af' : '#0d9488' }"></span>
	                    {{ otNode.label }}
	                    <span class="tree-node__count">{{ otNode.count }}</span>
	                  </div>
	                  <template v-for="ncNode in otNode.children" :key="ncNode.key">
	                    <div
	                      v-show="expandedTreeNodes.has('root') && expandedTreeNodes.has(otNode.key)"
                      class="tree-node tree-node--nc"
                      :class="{ 'tree-node--selected': selectedTreeNode === ncNode.key }"
                      @click="handleTreeNodeClick(ncNode)"
                    >
                      <span
                        v-if="ncNode.children?.length"
                        class="tree-node__arrow"
                        :class="{ 'tree-node__arrow--expanded': expandedTreeNodes.has(ncNode.key) }"
                        @click.stop="toggleTreeExpand(ncNode.key)"
                      >▶</span>
                      <span v-else class="tree-node__leaf-dot"></span>
                      {{ ncNode.label }}
                      <span class="tree-node__count">{{ ncNode.count }}</span>
                    </div>
                    <template v-for="catNode in (ncNode.children || [])" :key="catNode.key">
                      <div
                        v-show="expandedTreeNodes.has('root') && expandedTreeNodes.has(otNode.key) && expandedTreeNodes.has(ncNode.key)"
                        class="tree-node tree-node--cat"
                        :class="{ 'tree-node--selected': selectedTreeNode === catNode.key }"
                        @click="handleTreeNodeClick(catNode)"
                      >
	                        <span class="tree-node__leaf-dot"></span>
	                        {{ catNode.label }}
	                        <span class="tree-node__count">{{ catNode.count }}</span>
	                      </div>
	                    </template>
	                  </template>
	                </template>
	              </div>
	            </div>

	            <!-- 右：横幅 + 总览 -->
	            <div class="ind-upper-right">
	              <!-- ① 标准信息横幅 -->
          <div class="ind-banner">
            <div class="ind-banner__left">
              <div class="ind-banner__no">
                {{ props.standardObject || props.standardNo }}
                <span v-if="!props.standardObject && objectType" class="ind-obj-type-badge">{{ objectType }}</span>
                <span v-if="applicableObjects.length" style="font-weight:400;color:#6b7280;font-size:13px">
                  （{{ applicableObjects.join(' / ') }}）
                </span>
              </div>
              <div v-if="!props.standardObject && props.standardName" class="ind-banner__name">{{
                  props.standardName
                }}
              </div>
            </div>
            <div class="ind-banner__stats">
              <div class="ind-stat">
                <span class="ind-stat__val ind-stat__val--total">{{ stats.total }}</span>
                <span class="ind-stat__lbl">指标数</span>
              </div>
              <template v-for="(nc, idx) in (taxonomy?.norm_classes ?? [])" :key="nc">
                <div class="ind-stat__sep"></div>
                <div class="ind-stat">
                  <span :style="{ color: ['#374151','#1e40af','#c2410c','#0d9488','#7c3aed','#16a34a'][idx % 6] }"
                        class="ind-stat__val">
                    {{ stats.normClassCounts[nc] ?? 0 }}
                  </span>
                  <span class="ind-stat__lbl">{{ nc }}</span>
                </div>
              </template>
              <template v-if="stats.testCount">
                <div class="ind-stat__sep"></div>
                <div class="ind-stat">
                  <span class="ind-stat__val" style="color:#0d9488">{{ stats.testCount }}</span>
                  <span class="ind-stat__lbl">试验数</span>
                </div>
              </template>
            </div>
          </div>

          <!-- ② 总览面板 -->
          <div v-if="indicators.length" class="ov-panel">
            <!-- 对象类型 tabs -->
            <div class="ov-tabs">
              <button
                v-for="group in distributionTree"
                :key="group.objectType"
                :class="{ 'ov-tab--active': selectedObjectType === group.objectType }"
                class="ov-tab"
                @click="selectedObjectType = group.objectType; hoveredNcIndex = null"
              >
                {{ group.objectType }}
                <span class="ov-tab__count">{{ group.total }}</span>
              </button>
              <div v-if="stats.testCount" class="ov-test-badge">
                <span class="ov-test-badge__num">{{ stats.testCount }}</span>
                <span class="ov-test-badge__lbl">项试验</span>
              </div>
            </div>

            <div class="ov-layout">
              <!-- 左：环形图 -->
              <div class="ov-donut-wrap">
                <svg class="ov-donut-svg" viewBox="0 0 140 140">
                  <path
                    v-for="seg in donutSegments"
                    :key="seg.nc"
                    :d="seg.d"
                    :fill="seg.color"
                    :opacity="hoveredNcIndex === null || hoveredNcIndex === seg.idx ? 1 : 0.25"
                    class="ov-donut-seg"
                    @mouseenter="hoveredNcIndex = seg.idx"
                    @mouseleave="hoveredNcIndex = null"
                  />
                  <text class="ov-donut-num" dominant-baseline="middle" text-anchor="middle" x="70" y="64">
                    {{
                      hoveredNcIndex !== null ? donutSegments[hoveredNcIndex]?.count : (activeObjectGroup?.total ?? stats.total)
                    }}
                  </text>
                  <text class="ov-donut-lbl" dominant-baseline="middle" text-anchor="middle" x="70" y="82">
                    {{ hoveredNcIndex !== null ? donutSegments[hoveredNcIndex]?.nc : '项指标' }}
                  </text>
                </svg>
              </div>

              <!-- 右：要素组图例 + chips -->
              <div class="ov-legend">
                <div
                  v-for="seg in donutSegments"
                  :key="seg.nc"
                  :class="{ 'ov-legend-row--dim': hoveredNcIndex !== null && hoveredNcIndex !== seg.idx }"
                  class="ov-legend-row"
                  @mouseenter="hoveredNcIndex = seg.idx"
                  @mouseleave="hoveredNcIndex = null"
                >
                  <div class="ov-legend-hd">
                    <span :style="{ background: seg.color }" class="ov-legend-dot"></span>
                    <span class="ov-legend-nc">{{ seg.nc }}</span>
                    <span :style="{ color: seg.color }" class="ov-legend-count">{{ seg.count }}</span>
                    <span class="ov-legend-pct">{{ seg.pct }}%</span>
                    <div class="ov-legend-bar-wrap">
                      <div :style="{ width: seg.pct + '%', background: seg.color }" class="ov-legend-bar"></div>
                    </div>
                  </div>
                  <div class="ov-legend-cats">
                    <button
                      v-for="c in (activeObjectGroup?.normClasses.find(n => n.normClass === seg.nc)?.cats ?? [])"
                      :key="c.cat"
                      :class="{ 'ov-cat-chip--active': filterCategory === c.cat }"
                      :style="getCategoryStyle(c.cat, filterCategory === c.cat)"
                      class="ov-cat-chip"
                      @click="filterCategory = filterCategory === c.cat ? '' : c.cat"
                    >
                      <span class="ov-cat-chip__name">{{ c.cat }}</span>
                      <span :style="{ color: seg.color }" class="ov-cat-chip__n">{{ c.count }}</span>
                    </button>
                  </div>
                </div>
                <button v-if="filterCategory" class="ov-clear-btn" @click="filterCategory = ''">
                  清除筛选 ×
                </button>
              </div>
	            </div>
	          </div>
	            </div>
	          </div>

	          <!-- ③ Tab 切换：全量指标 / 全量试验 -->
          <div v-if="indicators.length || tests.length" class="ind-card">
            <div class="ind-card__hd">
              <div class="ind-tabs">
                <button
                  :class="['ind-tab', { 'ind-tab--active': activeTab === 'indicators' }]"
                  @click="activeTab = 'indicators'"
                >
                  全量指标明细
                  <span class="ind-tab__count">{{ indicators.length }}</span>
                </button>
                <button
                  v-if="tests.length"
                  :class="['ind-tab', { 'ind-tab--active': activeTab === 'tests' }]"
                  @click="activeTab = 'tests'"
                >
                  全量试验明细
                  <span class="ind-tab__count">{{ tests.length }}</span>
                </button>
              </div>
            </div>

            <!-- 指标 Tab -->
            <template v-if="activeTab === 'indicators'">
              <!-- 筛选栏 -->
              <div class="ind-filters">
                <div class="ind-search">
                  <svg class="ind-search__icon" fill="none" viewBox="0 0 16 16">
                    <circle cx="6.5" cy="6.5" r="4.5" stroke="currentColor" stroke-width="1.5"/>
                    <path d="m10 10 3 3" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"/>
                  </svg>
                  <input
                    v-model="searchKeyword"
                    class="ind-search__input"
                    placeholder="搜索指标名称或值…"
                    type="text"
                  />
                  <button v-if="searchKeyword" class="ind-search__clear" @click="searchKeyword = ''">
                    <svg fill="none" viewBox="0 0 16 16">
                      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"/>
                    </svg>
                  </button>
                </div>
              </div>

              <!-- 指标表格 -->
              <div class="tbl-scroll">
                <table class="ind-tbl">
                  <thead>
                  <tr>
                    <th class="th-name">指标名称</th>
                    <th class="th-val">规定值</th>
                    <th v-if="props.standardObject" class="th-stdno">来源标准</th>
                    <th class="th-obj">标准化对象</th>
                    <th class="th-clause">条款</th>
                  </tr>
                  </thead>
                  <tbody v-if="filteredList.length">
                  <tr v-for="item in filteredList" :key="item.id" class="ind-tbl__row">
                    <!-- 名称列 -->
                    <td>
                      <div class="td-name">
                        <span class="td-name__main">
                          <span
                            class="ind-type-circle"
                            :style="normClassCircleStyle(item.norm_class)"
                          >{{ item.norm_class ? item.norm_class.slice(0, 2) : '—' }}</span>
                          {{ item.indicator_object || '—' }}
                        </span>
                        <span v-if="item.standard_object" class="td-name__sub">
                          {{ item.standard_object }}{{ item.applicable_object ? `（${item.applicable_object}）` : '' }}
                        </span>
                        <!-- 关联试验 Popover -->
                        <NPopover
                          v-if="item.linked_tests && item.linked_tests.length"
                          :width="420"
                          placement="right"
                          scrollable
                          trigger="click"
                        >
                          <template #trigger>
                            <button class="linked-test-btn">
                              <svg class="linked-test-btn__icon" fill="none" viewBox="0 0 16 16">
                                <path d="M2 4h12M2 8h8M2 12h10" stroke="currentColor" stroke-linecap="round"
                                      stroke-width="1.5"/>
                              </svg>
                              关联试验
                              <span class="linked-test-btn__count">{{ item.linked_tests.length }}</span>
                            </button>
                          </template>
                          <div class="test-popover">
                            <div
                              v-for="t in item.linked_tests"
                              :key="t.id"
                              class="test-popover__item"
                            >
                              <div class="test-popover__name">{{ t.test_name }}</div>
                              <div v-if="t.method_desc" class="test-popover__row">
                                <span class="test-popover__label">试验方法</span>
                                <span class="test-popover__val">{{ t.method_desc }}</span>
                              </div>
                              <div v-if="t.conditions" class="test-popover__row">
                                <span class="test-popover__label">试验条件</span>
                                <span class="test-popover__val">{{ t.conditions }}</span>
                              </div>
                              <div v-if="t.preparation" class="test-popover__row">
                                <span class="test-popover__label">试验准备</span>
                                <span class="test-popover__val">{{ t.preparation }}</span>
                              </div>
                              <div v-if="t.procedure" class="test-popover__row">
                                <span class="test-popover__label">试验过程</span>
                                <span class="test-popover__val">{{ t.procedure }}</span>
                              </div>
                              <div v-if="t.acceptance" class="test-popover__row">
                                <span class="test-popover__label">试验要求</span>
                                <span class="test-popover__val test-popover__val--accept">{{ t.acceptance }}</span>
                              </div>
                              <div v-if="t.report_items" class="test-popover__row">
                                <span class="test-popover__label">报告</span>
                                <span class="test-popover__val">{{ t.report_items }}</span>
                              </div>
                              <div v-if="t.source_clause" class="test-popover__clause">条款 {{ t.source_clause }}</div>
                            </div>
                          </div>
                        </NPopover>
                      </div>
                    </td>

                    <!-- 规定值列 -->
                    <td class="td-stack">
                      <span v-if="item.source_value" class="val-mono">{{ item.source_value }}</span>
                      <span v-else class="val-absent">—</span>
                    </td>

                    <!-- 来源标准列（仅 obj 模式） -->
                    <td v-if="props.standardObject" class="td-stdno">
                      <span v-if="item.standard_no" class="val-stdno">{{ item.standard_no }}</span>
                      <span v-else class="val-absent">—</span>
                    </td>

                    <!-- 标准化对象列 -->
                    <td class="td-obj">
                      <span v-if="item.standard_object">{{ item.standard_object }}</span>
                      <span v-else class="val-absent">—</span>
                    </td>

                    <!-- 条款列 -->
                    <td class="td-clause">
                      <span v-if="item.source_clause" class="val-tag">{{ item.source_clause }}</span>
                      <span v-else class="val-absent">—</span>
                    </td>
                  </tr>
                  </tbody>
                  <tbody v-else>
                  <tr>
                    <td class="td-empty" colspan="4">
                      {{ searchKeyword || filterNormClass || filterCategory ? '无匹配指标' : '暂无指标数据' }}
                    </td>
                  </tr>
                  </tbody>
                </table>
              </div>
            </template>

            <!-- 试验 Tab -->
            <template v-else-if="activeTab === 'tests'">
              <div class="ind-filters">
                <div class="ind-search">
                  <svg class="ind-search__icon" fill="none" viewBox="0 0 16 16">
                    <circle cx="6.5" cy="6.5" r="4.5" stroke="currentColor" stroke-width="1.5"/>
                    <path d="m10 10 3 3" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"/>
                  </svg>
                  <input
                    v-model="searchTestKeyword"
                    class="ind-search__input"
                    placeholder="搜索试验名称或内容…"
                    type="text"
                  />
                  <button v-if="searchTestKeyword" class="ind-search__clear" @click="searchTestKeyword = ''">
                    <svg fill="none" viewBox="0 0 16 16">
                      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-linecap="round" stroke-width="1.5"/>
                    </svg>
                  </button>
                </div>
              </div>
              <div class="tbl-scroll">
                <table class="ind-tbl">
                  <thead>
                  <tr>
                    <th style="width:160px">试验名称</th>
                    <th style="width:160px">试验方法</th>
                    <th style="width:160px">试验条件</th>
                    <th style="width:180px">试验准备</th>
                    <th style="width:220px">试验过程</th>
                    <th style="width:140px">试验要求</th>
                    <th style="width:140px">报告</th>
                    <th style="width:90px">条款</th>
                  </tr>
                  </thead>
                  <tbody v-if="filteredTests.length">
                  <tr v-for="t in filteredTests" :key="t.id" class="ind-tbl__row">
                    <td>
                      <div class="td-name">
                        <span class="td-name__main" style="padding-left:0;font-weight:600">{{ t.test_name }}</span>
                        <span v-if="t.standard_object" class="td-name__sub">
                          {{ t.standard_object }}{{ t.applicable_object ? `（${t.applicable_object}）` : '' }}
                        </span>
                        <div v-if="t.linked_indicators && t.linked_indicators.length" class="linked-ind-tags">
                          <span
                            v-for="li in t.linked_indicators"
                            :key="li.id"
                            class="linked-ind-tag"
                          >{{ li.indicator_object || '—' }}</span>
                        </div>
                      </div>
                    </td>
                    <td class="td-stack"><span v-if="t.method_desc" class="val-text">{{ t.method_desc }}</span><span
                      v-else class="val-absent">—</span></td>
                    <td class="td-stack"><span v-if="t.conditions" class="val-text">{{ t.conditions }}</span><span
                      v-else class="val-absent">—</span></td>
                    <td class="td-stack"><span v-if="t.preparation" class="val-text">{{ t.preparation }}</span><span
                      v-else class="val-absent">—</span></td>
                    <td class="td-stack"><span v-if="t.procedure" class="val-text">{{ t.procedure }}</span><span v-else
                                                                                                                 class="val-absent">—</span>
                    </td>
                    <td class="td-stack"><span v-if="t.acceptance" class="val-result">{{ t.acceptance }}</span><span
                      v-else class="val-absent">—</span></td>
                    <td class="td-stack"><span v-if="t.report_items" class="val-text">{{ t.report_items }}</span><span
                      v-else class="val-absent">—</span></td>
                    <td class="td-clause">
                      <span v-if="t.source_clause" class="val-tag">{{ t.source_clause }}</span>
                      <span v-else class="val-absent">—</span>
                    </td>
                  </tr>
                  </tbody>
                  <tbody v-else>
                  <tr>
                    <td class="td-empty" colspan="6">{{ searchTestKeyword ? '无匹配试验' : '暂无试验数据' }}</td>
                  </tr>
                  </tbody>
                </table>
              </div>
            </template>
          </div>
        </div>

        <NEmpty v-else-if="!loading" description="暂无数据"/>
      </NSpin>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>

.ind-page {
  --src: #1e40af;
  --src-2: #3b82f6;
  --src-bg: #eff6ff;
  --src-border: #bfdbfe;
  --src-text: #1e3a8a;
  --tgt: #0d9488;
  --srconly: #7c3aed;
  --srconly-bg: #f5f3ff;
  --srconly-border: #c4b5fd;
  --ink: #111827;
  --ink-2: #374151;
  --ink-3: #6b7280;
  --ink-4: #9ca3af;
  --border: #e5e7eb;
  --bg: #f8fafc;
  --white: #ffffff;
  --font-sans: 'Fira Sans', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  --font-mono: 'Fira Code', 'Consolas', monospace;

  font-family: var(--font-sans);
  display: flex;
  flex-direction: column;
  gap: 16px;
  animation: ind-in 0.25s ease both;
}

/* ── UPPER SECTION (Tree + Info) ── */
.ind-upper {
  display: flex;
  gap: 16px;
  align-items: stretch;
}

.ind-tree-panel {
  width: 260px;
  min-width: 260px;
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
}

.ind-tree-header {
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 700;
  color: var(--src-text);
  background: var(--bg);
  border-bottom: 1px solid var(--border);
  letter-spacing: 0.02em;
}

.ind-tree-body {
  padding: 8px 0;
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}

.tree-node {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 12px;
  font-size: 13px;
  color: var(--ink-2);
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
  user-select: none;
  white-space: nowrap;
}

.tree-node:hover {
  background: var(--bg);
}

.tree-node--selected {
  background: var(--src-bg) !important;
  color: var(--src-text);
  font-weight: 600;
}

.tree-node--root {
  font-size: 14px;
  font-weight: 700;
  color: var(--ink);
  padding: 6px 12px;
}

.tree-node--ot {
  padding-left: 20px;
  font-weight: 600;
}

.tree-node--nc {
  padding-left: 36px;
  font-size: 12px;
  font-weight: 500;
}

.tree-node--cat {
  padding-left: 52px;
  font-size: 12px;
  font-weight: 400;
  color: var(--ink-3);
}

.tree-node--cat.tree-node--selected {
  color: var(--src-text);
}

.tree-node__arrow {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  font-size: 8px;
  color: var(--ink-4);
  transition: transform 0.2s ease;
  flex-shrink: 0;
}

.tree-node__arrow--expanded {
  transform: rotate(90deg);
}

.tree-node__dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  flex-shrink: 0;
}

.tree-node__leaf-dot {
  display: inline-block;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--ink-4);
  flex-shrink: 0;
  margin: 0 5px;
}

.tree-node__count {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 600;
  padding: 0 5px;
  border-radius: 8px;
  background: #f3f4f6;
  color: var(--ink-4);
  margin-left: auto;
  flex-shrink: 0;
}

.tree-node--selected .tree-node__count {
  background: var(--src-border);
  color: var(--src-text);
}

.ind-upper-right {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

@keyframes ind-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* ── BANNER ── */
.ind-banner {
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

.ind-banner__no {
  font-size: 18px;
  font-weight: 700;
  color: var(--src-text);
  letter-spacing: -0.01em;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.ind-obj-type-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 20px;
  background: #f0fdf4;
  color: #15803d;
  border: 1px solid #86efac;
  letter-spacing: 0;
  white-space: nowrap;
}

.ind-banner__name {
  font-size: 12px;
  color: var(--ink-4);
  margin-top: 3px;
}

.ind-banner__stats {
  display: flex;
  align-items: center;
  gap: 16px;
}

.ind-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.ind-stat__val {
  font-family: var(--font-mono);
  font-size: 28px;
  font-weight: 600;
  line-height: 1;
}

.ind-stat__val--total {
  color: var(--ink-2);
}

.ind-stat__val--basic {
  color: #6b7280;
}

.ind-stat__val--norm {
  color: var(--src);
}

.ind-stat__val--method {
  color: #c2410c;
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

.ov-metric--basic .ov-metric__val {
  color: #6b7280;
}

.ov-metric--norm .ov-metric__val {
  color: var(--src-2);
}

.ov-metric--method .ov-metric__val {
  color: #f97316;
}

.ind-type-circle--basic {
  background: #f3f4f6;
  color: #374151;
  border: 1px solid #d1d5db;
}

.ind-type-circle--norm {
  background: var(--src-bg);
  color: var(--src-text);
  border: 1px solid var(--src-border);
}

.ind-type-circle--method {
  background: #fff7ed;
  color: #c2410c;
  border: 1px solid #fdba74;
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

.ind-stat__lbl {
  font-size: 11px;
  color: var(--ink-4);
  white-space: nowrap;
}

.ind-stat__sep {
  width: 1px;
  height: 36px;
  background: var(--border);
}

/* ── OVERVIEW PANEL ── */
.ov-panel {
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}

/* 对象类型 tabs */
.ov-tabs {
  display: flex;
  align-items: center;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  border-radius: 12px 12px 0 0;
}

.ov-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-3);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
  white-space: nowrap;
  margin-bottom: -1px;
  font-family: var(--font-sans);
}

.ov-tab:hover {
  color: var(--ink-2);
}

.ov-tab--active {
  color: var(--src);
  border-bottom-color: var(--src);
  font-weight: 600;
}

.ov-tab__count {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 8px;
  background: #f3f4f6;
  color: var(--ink-4);
}

.ov-tab--active .ov-tab__count {
  background: var(--src-bg);
  color: var(--src);
}

.ov-tabs .ov-test-badge {
  margin-left: auto;
}

.ov-layout {
  display: flex;
  gap: 0;
  align-items: stretch;
  min-height: 160px;
}

/* 左：环形图区 */
.ov-donut-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 20px 16px 20px 24px;
  flex-shrink: 0;
}

.ov-donut-svg {
  width: 140px;
  height: 140px;
  overflow: visible;
}

.ov-donut-seg {
  cursor: pointer;
  transition: opacity 0.2s ease;
}

.ov-donut-num {
  font-family: var(--font-mono);
  font-size: 22px;
  font-weight: 700;
  fill: var(--ink-2);
  transition: all 0.15s;
}

.ov-donut-lbl {
  font-family: var(--font-sans);
  font-size: 10px;
  fill: var(--ink-4);
  transition: all 0.15s;
}

.ov-test-badge {
  display: flex;
  align-items: baseline;
  gap: 3px;
  padding: 3px 10px;
  background: #f0fdfa;
  border: 1px solid #99f6e4;
  border-radius: 20px;
}

.ov-test-badge__num {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
  color: #0d9488;
}

.ov-test-badge__lbl {
  font-size: 11px;
  color: #0d9488;
  opacity: 0.8;
}

/* 右：图例 + chips */
.ov-legend {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 10px;
  padding: 16px 24px 16px 16px;
  border-left: 1px solid var(--border);
}

.ov-legend-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: opacity 0.2s ease;
}

.ov-legend-row--dim {
  opacity: 0.35;
}

.ov-legend-hd {
  display: flex;
  align-items: center;
  gap: 7px;
}

.ov-legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  flex-shrink: 0;
}

.ov-legend-nc {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-2);
  white-space: nowrap;
}

.ov-legend-count {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
}

.ov-legend-pct {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-4);
  min-width: 32px;
}

.ov-legend-bar-wrap {
  flex: 1;
  max-width: 500px;
  height: 3px;
  background: #f1f5f9;
  border-radius: 2px;
  overflow: hidden;
}

.ov-legend-bar {
  height: 100%;
  border-radius: 2px;
  opacity: 0.5;
  transition: width 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}

.ov-legend-cats {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding-left: 15px;
}

/* indicator_category chip */
.ov-cat-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 500;
  font-family: var(--font-sans);
  padding: 3px 9px;
  border-radius: 5px;
  cursor: pointer;
  transition: transform 0.12s ease, box-shadow 0.12s ease;
  white-space: nowrap;
  animation: chip-in 0.2s ease both;
}

@keyframes chip-in {
  from {
    opacity: 0;
    transform: scale(0.92);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.ov-cat-chip:hover {
  transform: translateY(-1px);
  box-shadow: 0 3px 8px rgba(0, 0, 0, 0.1);
}

.ov-cat-chip--active {
  box-shadow: 0 0 0 2px currentColor !important;
  font-weight: 700;
}

.ov-cat-chip__name {
  line-height: 1;
}

.ov-cat-chip__n {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
}

.ov-clear-btn {
  align-self: flex-start;
  font-size: 11px;
  color: var(--ink-4);
  background: none;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 2px 8px;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
  margin-top: 2px;
}

.ov-clear-btn:hover {
  color: var(--ink-2);
  border-color: #9ca3af;
}

/* ── 旧样式保留 ── */
.ov-bar-seg--basic {
  background: #9ca3af;
}

.ov-bar-seg--norm {
  background: var(--src-2);
}

.ov-bar-seg--method {
  background: #f97316;
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

.ov-obj-group__total {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-4);
}

/* ── CARD ── */
.ind-card {
  background: var(--white);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow: hidden;
}

.ind-card__hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}

/* ── TABS ── */
.ind-tabs {
  display: flex;
  gap: 0;
}

.ind-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 12px 16px;
  font-size: 13px;
  font-weight: 500;
  color: var(--ink-3);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
  white-space: nowrap;
  margin-bottom: -1px;
}

.ind-tab:hover {
  color: var(--ink-2);
}

.ind-tab--active {
  color: var(--src);
  border-bottom-color: var(--src);
  font-weight: 600;
}

.ind-tab__count {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 8px;
  background: #f3f4f6;
  color: var(--ink-4);
}

.ind-tab--active .ind-tab__count {
  background: var(--src-bg);
  color: var(--src);
}

/* ── 关联试验按钮 ── */
.linked-test-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  margin-top: 4px;
  padding: 2px 7px;
  font-size: 11px;
  font-weight: 500;
  color: #0d9488;
  background: #f0fdfa;
  border: 1px solid #99f6e4;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s;
  font-family: var(--font-sans);
}

.linked-test-btn:hover {
  background: #ccfbf1;
}

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
  background: #0d9488;
  color: #fff;
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  line-height: 1;
}

/* ── 试验 Popover 内容 ── */
.test-popover {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 4px 0;
  max-height: 480px;
  overflow-y: auto;
}

.test-popover__item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.test-popover__name {
  font-size: 13px;
  font-weight: 700;
  color: var(--ink);
  padding-bottom: 4px;
  border-bottom: 1px solid var(--border);
}

.test-popover__row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.test-popover__label {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  color: var(--ink-4);
  width: 52px;
  padding-top: 1px;
}

.test-popover__val {
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.6;
  word-break: break-all;
}

.test-popover__val--accept {
  color: #0d9488;
  font-weight: 500;
}

.test-popover__clause {
  font-size: 11px;
  color: var(--ink-4);
  font-family: var(--font-mono);
}

/* ── 试验 Tab 关联指标标签 ── */
.linked-ind-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}

.linked-ind-tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  background: #eff6ff;
  color: #1e40af;
  border: 1px solid #bfdbfe;
  white-space: nowrap;
}

.val-text {
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.6;
  word-break: break-all;
}

/* ── FILTER BAR ── */
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

.ind-fchip--all.ind-fchip--active {
  background: var(--ink);
  border-color: var(--ink);
  color: var(--white);
}

.ind-fchip--all.ind-fchip--active .ind-fchip__count {
  background: rgba(255, 255, 255, 0.2);
  color: var(--white);
}

/* ── TABLE ── */
.tbl-scroll {
  overflow-x: auto;
}

.ind-tbl {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 13px;
  font-family: var(--font-sans);
}

.ind-tbl thead th {
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

.th-name {
  width: 220px;
}

.th-val {
  width: 260px;
}

.th-stdno {
  width: 130px;
}

.th-obj {
  width: 160px;
}

.th-clause {
  width: 90px;
}

.td-stdno {
  font-family: var(--font-mono);
  font-size: 12px;
  color: #1e40af;
}

.val-stdno {
  font-family: var(--font-mono);
  font-size: 11px;
  color: #1e40af;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 3px;
  padding: 1px 5px;
  white-space: nowrap;
}

.ind-tbl tbody td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
  color: var(--ink-2);
  font-size: 13px;
  overflow: hidden;
  word-break: break-all;
}

.ind-tbl__row:last-child td {
  border-bottom: none;
}

.ind-tbl__row:nth-child(even) td {
  background: var(--bg);
}

.ind-tbl__row:hover td {
  background: #eef5ff;
  transition: background 0.15s;
}

/* 名称列 */
.td-name {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.td-name__main {
  position: relative;
  padding-left: 28px;
  font-weight: 600;
  color: var(--ink);
  font-size: 13px;
  display: block;
}

.td-name__sub {
  font-size: 11px;
  color: var(--ink-4);
  display: block;
  margin-top: 2px;
}

/* 类型标签（规范类别） */
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

/* 分类标签 */
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

/* 值列 */
.td-stack {
  min-width: 200px;
}

.val-mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

.val-tag {
  display: inline-block;
  font-size: 11px;
  color: var(--ink-3);
  background: #f3f4f6;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 6px;
  vertical-align: middle;
  white-space: normal;
  word-break: break-all;
  font-family: var(--font-mono);
}

.val-absent {
  color: var(--ink-4);
  font-style: italic;
  font-size: 12px;
}

.val-result {
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-2);
}

.val-test-flow {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  margin-top: 5px;
}

.val-test-flow__icon {
  flex-shrink: 0;
  width: 12px;
  height: 12px;
  margin-top: 1px;
  color: var(--ink-4);
}

.val-test-flow__text {
  font-size: 11px;
  color: var(--ink-4);
  line-height: 1.5;
  word-break: break-all;
}

.td-obj {
  font-size: 12px;
  color: var(--ink-3);
}

.td-clause {
  font-size: 12px;
  color: var(--ink-4);
}

.td-empty {
  padding: 32px !important;
  text-align: center;
  font-size: 13px;
  color: var(--ink-4);
  background: var(--white) !important;
}
</style>
