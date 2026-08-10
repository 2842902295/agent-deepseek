<script lang="tsx" setup>
import {computed, onMounted, reactive, ref} from 'vue';
import {NButton, NInput, NSelect, NSpace, NTag} from 'naive-ui';
import {fetchIndTaxonomy, fetchStandardObjList} from '@/service/api';
import {useAppStore} from '@/store/modules/app';
import {defaultTransform, useNaivePaginatedTable} from '@/hooks/common/table';
import IndDetailDrawer from '../standard-ind/modules/ind-detail-drawer.vue';

const appStore = useAppStore();

const searchParams = reactive({
  current: 1,
  size: 10,
  keyword: undefined as string | undefined,
  norm_class: undefined as string | undefined,
  indicator_category: undefined as string | undefined,
});

const taxonomy = ref<Api.AI.IndTaxonomy | null>(null);
onMounted(async () => {
  const resp = await fetchIndTaxonomy();
  if (resp.data) taxonomy.value = resp.data;
});

const normClassOptions = computed(() => [
  {value: '', label: '全部规范类别'},
  ...(taxonomy.value?.norm_classes ?? []).map(v => ({value: v, label: v})),
]);

const categoryOptions = computed(() => [
  {value: '', label: '全部指标分类'},
  ...(taxonomy.value?.all_categories ?? []).map(v => ({value: v, label: v})),
]);

const drawerShow = ref(false);
const selectedObject = ref('');

function openDetail(standardObject: string) {
  selectedObject.value = standardObject;
  drawerShow.value = true;
}

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
    map[cat] = [PALETTE_BG[i % PALETTE_BG.length], PALETTE_COLOR[i % PALETTE_COLOR.length]];
  });
  return map;
});

function getCatStyle(cat: string): string {
  const [bg, color] = catColorMap.value[cat] ?? ['#f3f4f6', '#6b7280'];
  return `font-size:10px;font-weight:600;padding:2px 6px;border-radius:3px;white-space:nowrap;background:${bg};color:${color};border:1px solid ${bg}`;
}

const {columns, data, loading, getDataByPage, mobilePagination} = useNaivePaginatedTable({
  api: () => fetchStandardObjList(searchParams),
  transform: (response: any) => {
    const d = response?.data;
    if (d && Array.isArray(d.list)) {
      return {data: d.list, pageNum: d.current ?? 1, pageSize: d.size ?? 10, total: d.total ?? 0};
    }
    return defaultTransform(response);
  },
  onPaginationParamsChange: params => {
    searchParams.current = params.page!;
    searchParams.size = params.pageSize!;
  },
  columns: () => [
    {
      key: 'index',
      title: '序号',
      width: 64,
      align: 'center' as const,
      render: (_row: Api.AI.StandardObjRecord, rowIndex: number) =>
        (searchParams.current - 1) * searchParams.size + rowIndex + 1,
    },
    {
      key: 'standard_object',
      title: '标准化对象',
      minWidth: 160,
      render: (row: Api.AI.StandardObjRecord) => {
        const appObjs = (row.applicable_object || '').split('/').map(s => s.trim()).filter(Boolean);
        return (
          <span style="font-weight:600;color:#111827;font-size:14px">
            {row.standard_object}
            {appObjs.length > 0 && (
              <span style="font-weight:400;color:#6b7280;font-size:13px">
                （{appObjs.join(' / ')}）
              </span>
            )}
          </span>
        );
      },
    },
    {
      key: 'standard_count',
      title: '涉及标准数',
      width: 100,
      align: 'center' as const,
      render: (row: Api.AI.StandardObjRecord) => (
        <div style="display:flex;flex-direction:column;align-items:center;gap:2px">
          <span style="font-family:'Fira Code',Consolas,monospace;font-size:18px;font-weight:700;color:#1e40af">
            {row.standard_count}
          </span>
          <span style="font-size:10px;color:#9ca3af">个标准</span>
        </div>
      ),
    },
    {
      key: 'total_count',
      title: '指标总数',
      width: 90,
      align: 'center' as const,
      render: (row: Api.AI.StandardObjRecord) => (
        <span style="font-family:'Fira Code',Consolas,monospace;font-size:15px;font-weight:600;color:#374151">
          {row.total_count}
        </span>
      ),
    },
    {
      key: 'norm_class',
      title: '规范类别',
      width: 220,
      align: 'center' as const,
      render: (row: Api.AI.StandardObjRecord) => {
        const entries = Object.entries(row.norm_class_counts ?? {});
        if (!entries.length) return <span style="color:#9ca3af;font-size:12px">—</span>;
        return (
          <NSpace size={4} justify="center" wrap>
            {entries.map(([nc, cnt]) => (
              <NTag key={nc} size="small" style="background:#f3f4f6;color:#374151;border:1px solid #d1d5db">
                {nc} {cnt}
              </NTag>
            ))}
          </NSpace>
        );
      },
    },
    {
      key: 'categories',
      title: '指标分类',
      minWidth: 100,
      render: (row: Api.AI.StandardObjRecord) => {
        if (!row.categories || row.categories.length === 0) {
          return <span style="color:#9ca3af;font-size:12px">—</span>;
        }
        return (
          <NSpace size={4} wrap>
            {row.categories.map(cat => (
              <span key={cat} style={getCatStyle(cat)}>{cat}</span>
            ))}
          </NSpace>
        );
      },
    },
    {
      key: 'standard_nos',
      title: '来源标准',
      minWidth: 200,
      render: (row: Api.AI.StandardObjRecord) => {
        if (!row.standard_nos || row.standard_nos.length === 0) {
          return <span style="color:#9ca3af;font-size:12px">—</span>;
        }
        return (
          <NSpace size={4} wrap>
            {row.standard_nos.map(no => (
              <span
                key={no}
                style="font-family:'Fira Code',Consolas,monospace;font-size:11px;color:#1e40af;background:#eff6ff;border:1px solid #bfdbfe;border-radius:3px;padding:1px 5px;white-space:nowrap"
              >{no}</span>
            ))}
          </NSpace>
        );
      },
    },
    {
      key: 'actions',
      title: '操作',
      width: 100,
      align: 'center' as const,
      render: (row: Api.AI.StandardObjRecord) => (
        <NButton size="small" type="primary" ghost onClick={() => openDetail(row.standard_object)}>
          查看指标
        </NButton>
      ),
    },
  ],
});

const hasSearch = computed(() => !!(searchParams.keyword || searchParams.norm_class || searchParams.indicator_category));

function handleSearch() {
  getDataByPage(1);
}

function handleReset() {
  searchParams.keyword = undefined;
  searchParams.norm_class = undefined;
  searchParams.indicator_category = undefined;
  getDataByPage(1);
}
</script>

<template>
  <div class="min-h-620px flex-col-stretch gap-16px overflow-hidden lt-sm:overflow-auto">
    <NCard :bordered="false" class="sm:flex-1-hidden card-wrapper" size="small" title="标准化对象">
      <template #header-extra>
        <NSpace>
          <NInput
            v-model:value="searchParams.keyword"
            :style="{ width: '220px' }"
            clearable
            placeholder="标准化对象 / 适用对象"
            @keyup.enter="handleSearch"
          />
          <NSelect
            v-model:value="searchParams.norm_class"
            :options="normClassOptions"
            :style="{ width: '150px' }"
            clearable
            placeholder="规范类别"
          />
          <NSelect
            v-model:value="searchParams.indicator_category"
            :options="categoryOptions"
            :style="{ width: '130px' }"
            clearable
            placeholder="指标分类"
          />
          <NButton type="primary" @click="handleSearch">搜索</NButton>
          <NButton v-if="hasSearch" @click="handleReset">重置</NButton>
        </NSpace>
      </template>
      <NDataTable
        :columns="columns"
        :data="data"
        :flex-height="!appStore.isMobile"
        :loading="loading"
        :pagination="mobilePagination"
        :row-key="(row) => `${row.standard_object}__${row.applicable_object}`"
        :scroll-x="1000"
        class="sm:h-full"
        remote
        size="small"
      />
    </NCard>

    <IndDetailDrawer
      v-model:show="drawerShow"
      :standard-object="selectedObject"
      standard-no=""
    />
  </div>
</template>
