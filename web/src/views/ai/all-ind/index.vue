<script lang="tsx" setup>
import {computed, onMounted, reactive, ref} from 'vue';
import {NButton, NInput, NSelect, NSpace, NTag} from 'naive-ui';
import {fetchAllIndList, fetchIndTaxonomy} from '@/service/api';
import {useAppStore} from '@/store/modules/app';
import {defaultTransform, useNaivePaginatedTable} from '@/hooks/common/table';
import IndDetailDrawer from '../standard-ind/modules/ind-detail-drawer.vue';

const appStore = useAppStore();

const taxonomy = ref<Api.AI.IndTaxonomy | null>(null);

onMounted(async () => {
  const resp = await fetchIndTaxonomy();
  if (resp.data) taxonomy.value = resp.data;
});

const normClassOptions = computed(() => [
  {label: '全部类别', value: ''},
  ...(taxonomy.value?.norm_classes ?? ['基础类', '规范类', '方法类']).map(v => ({label: v, value: v})),
]);

const objectTypeOptions = computed(() => [
  {label: '全部类型', value: ''},
  ...(taxonomy.value?.object_types ?? []).map(v => ({label: v, value: v})),
]);

const categoryOptions = computed(() =>
  (taxonomy.value?.all_categories ?? []).map(v => ({label: v, value: v}))
);

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

const catColorMap = computed<Record<string, [string, string]>>(() => {
  const map: Record<string, [string, string]> = {};
  (taxonomy.value?.all_categories ?? []).forEach((cat, i) => {
    map[cat] = [PALETTE_COLOR[i % PALETTE_COLOR.length], PALETTE_BG[i % PALETTE_BG.length]];
  });
  return map;
});

const OBJ_TYPE_STYLE: Record<string, string> = {
  产品类对象: 'background:#eff6ff;color:#1e40af;border:1px solid #bfdbfe',
  服务类对象: 'background:#f0fdf4;color:#15803d;border:1px solid #86efac',
  过程类对象: 'background:#fff7ed;color:#c2410c;border:1px solid #fdba74',
};

function getCategoryStyle(cat: string) {
  const [color, bg] = catColorMap.value[cat] ?? ['#6b7280', '#f3f4f6'];
  return `font-size:10px;font-weight:600;padding:2px 6px;border-radius:3px;white-space:nowrap;background:${bg};color:${color};border:1px solid ${bg}`;
}

const searchParams = reactive({
  current: 1,
  size: 10,
  standard_no: undefined as string | undefined,
  norm_class: undefined as string | undefined,
  indicator_category: undefined as string | undefined,
  object_type: undefined as string | undefined,
  keyword: undefined as string | undefined,
  applicable_object: undefined as string | undefined,
  standard_object: undefined as string | undefined,
});

const drawerShow = ref(false);
const selectedStandardNo = ref('');
const selectedStandardName = ref('');

function openDetail(standardNo: string, standardName: string) {
  selectedStandardNo.value = standardNo;
  selectedStandardName.value = standardName;
  drawerShow.value = true;
}

const {columns, data, loading, getDataByPage, mobilePagination} = useNaivePaginatedTable({
  api: () => fetchAllIndList(searchParams),
  transform: response => {
    const d = (response as any).data;
    if (d && Array.isArray(d.list)) {
      return {data: d.list, pageNum: d.current ?? 1, pageSize: d.size ?? 10, total: d.total ?? 0};
    }
    return defaultTransform(response);
  },
  onPaginationParamsChange: params => {
    searchParams.current = params.page;
    searchParams.size = params.pageSize;
  },
  columns: () => [
    {
      key: 'index',
      title: '序号',
      width: 64,
      align: 'center' as const,
      render: (_row: any, rowIndex: number) =>
        (searchParams.current - 1) * searchParams.size + rowIndex + 1,
    },
    {
      key: 'standard_object',
      title: '标准化对象',
      width: 160,
      render: (row: Api.AI.AllIndItem) => (
        <div style="display:flex;flex-direction:column;gap:3px">
          {(row as any).standard_object
            ? <span style="font-size:13px;color:#374151">{(row as any).standard_object}</span>
            : <span style="color:#9ca3af;font-size:12px">—</span>}
          {(row as any).object_type && (
            <span
              style={`font-size:10px;font-weight:500;padding:1px 5px;border-radius:3px;white-space:nowrap;${OBJ_TYPE_STYLE[(row as any).object_type] ?? 'background:#f3f4f6;color:#6b7280;border:1px solid #e5e7eb'}`}>
              {(row as any).object_type}
            </span>
          )}
        </div>
      ),
    },
    {
      key: 'applicable_object',
      title: '应用对象',
      width: 110,
      render: (row: Api.AI.AllIndItem) =>
        (row as any).applicable_object
          ? <span style="font-size:13px;color:#374151">{(row as any).applicable_object}</span>
          : <span style="color:#9ca3af;font-size:12px">—</span>,
    },
    {
      key: 'norm_class',
      title: '规范类别',
      width: 80,
      align: 'center' as const,
      render: (row: Api.AI.AllIndItem) => {
        const style: Record<string, string> = {
          '基础类': 'background:#f3f4f6;color:#374151;border:1px solid #d1d5db',
          '规范类': 'background:#eff6ff;color:#1e40af;border:1px solid #bfdbfe',
          '方法类': 'background:#fff7ed;color:#c2410c;border:1px solid #fdba74',
        };
        const s = style[(row as any).norm_class] ?? 'background:#f3f4f6;color:#9ca3af;border:1px solid #e5e7eb';
        return (row as any).norm_class
          ? <NTag size="small" style={s}>{(row as any).norm_class}</NTag>
          : <span style="color:#9ca3af;font-size:12px">—</span>;
      },
    },
    {
      key: 'indicator_category',
      title: '分类',
      width: 90,
      align: 'center' as const,
      render: (row: Api.AI.AllIndItem) =>
        row.indicator_category
          ? <span style={getCategoryStyle(row.indicator_category)}>{row.indicator_category}</span>
          : <span style="color:#9ca3af;font-size:12px">—</span>,
    },
    {
      key: 'name',
      //title: '指标名称 / 实验名称',
      title: '指标名称',
      minWidth: 200,
      render: (row: Api.AI.AllIndItem) => {
        const name = row.indicator_type === 'static' ? row.indicator_object : row.experiment_name;
        return <div style="font-weight:600;color:#111827;font-size:13px">{name || '—'}</div>;
      },
    },
    {
      key: 'value',
      title: '规定值 / 结果',
      minWidth: 160,
      render: (row: Api.AI.AllIndItem) => {
        const val = row.indicator_type === 'static' ? row.source_value : row.source_result;
        return val
          ? <span style="font-family:'Fira Code',Consolas,monospace;font-size:12px;color:#374151">{val}</span>
          : <span style="color:#9ca3af;font-style:italic;font-size:12px">—</span>;
      },
    },
    {
      key: 'source_clause',
      title: '条款',
      width: 100,
      render: (row: Api.AI.AllIndItem) =>
        row.source_clause
          ? <span
            style="font-size:11px;color:#6b7280;background:#f3f4f6;border:1px solid #e5e7eb;border-radius:4px;padding:1px 6px;font-family:'Fira Code',Consolas,monospace">{row.source_clause}</span>
          : <span style="color:#9ca3af;font-size:12px">—</span>,
    },
    {
      key: 'standard_no',
      title: '标准编号',
      width: 180,
      render: (row: Api.AI.AllIndItem) => (
        <span
          style="font-weight:600;color:#1e40af;font-family:'Fira Code',Consolas,monospace;font-size:13px">{row.standard_no}</span>
      ),
    },
    {
      key: 'actions',
      title: '操作',
      width: 100,
      align: 'center' as const,
      render: (row: Api.AI.AllIndItem) => (
        <NButton size="small" type="primary" ghost onClick={() => openDetail(row.standard_no, row.standard_name)}>
          查看详情
        </NButton>
      ),
    },
  ],
});

function handleSearch() {
  getDataByPage(1);
}

function handleReset() {
  searchParams.standard_no = undefined;
  searchParams.norm_class = undefined;
  searchParams.indicator_category = undefined;
  searchParams.object_type = undefined;
  searchParams.keyword = undefined;
  searchParams.applicable_object = undefined;
  searchParams.standard_object = undefined;
  getDataByPage(1);
}
</script>

<template>
  <div class="min-h-620px flex-col-stretch gap-16px overflow-hidden lt-sm:overflow-auto">
    <NCard :bordered="false" class="sm:flex-1-hidden card-wrapper" size="small" title="全量指标列表">
      <template #header-extra>
        <NSpace wrap>
          <NInput
            v-model:value="searchParams.applicable_object"
            :style="{ width: '130px' }"
            clearable
            placeholder="应用对象"
            @keyup.enter="handleSearch"
          />
          <NInput
            v-model:value="searchParams.standard_object"
            :style="{ width: '130px' }"
            clearable
            placeholder="标准化对象"
            @keyup.enter="handleSearch"
          />
          <NInput
            v-model:value="searchParams.standard_no"
            :style="{ width: '150px' }"
            clearable
            placeholder="标准编号"
            @keyup.enter="handleSearch"
          />
          <NSelect
            v-model:value="searchParams.object_type"
            :options="objectTypeOptions"
            :style="{ width: '130px' }"
            clearable
            placeholder="对象类型"
          />
          <NSelect
            v-model:value="searchParams.norm_class"
            :options="normClassOptions"
            :style="{ width: '120px' }"
            clearable
            placeholder="规范类别"
          />
          <NSelect
            v-model:value="searchParams.indicator_category"
            :options="categoryOptions"
            :style="{ width: '130px' }"
            clearable
            filterable
            placeholder="指标分类"
          />
          <NInput
            v-model:value="searchParams.keyword"
            :style="{ width: '170px' }"
            clearable
            placeholder="关键字搜索"
            @keyup.enter="handleSearch"
          />
          <NButton type="primary" @click="handleSearch">搜索</NButton>
          <NButton @click="handleReset">重置</NButton>
        </NSpace>
      </template>
      <NDataTable
        :columns="columns"
        :data="data"
        :flex-height="!appStore.isMobile"
        :loading="loading"
        :pagination="mobilePagination"
        :row-key="(row: Api.AI.AllIndItem) => row.id"
        :scroll-x="1100"
        class="sm:h-full"
        remote
        size="small"
      />
    </NCard>

    <IndDetailDrawer
      v-model:show="drawerShow"
      :standard-name="selectedStandardName"
      :standard-no="selectedStandardNo"
    />
  </div>
</template>
